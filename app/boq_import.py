"""Reading a BOQ or a quotation back in from a spreadsheet (branch Inverse, 2026-10-28).

Why
---
A BOQ leaves the app as an .xlsx and often comes back changed: quantities corrected in Excel, rows
added after a site visit, an area renamed, a description reworded. Until now the only way back in
was to type it all again. This reads such a file into what the app is made of - floors, areas and
lines - links every line it can to the product it names in NPD, and keeps every other line as a
typed row with its details in place, so the BOQ can be carried on in the app as if it had been made
there.

What it reads
-------------
The app's own BOQ and quotation layouts, and layouts close to them:

- Headings are matched by meaning, not spelling: "S. No", "Sr. No" and "Sl. No" are all the line's
  number; "Qty" and "Quantity", "Make" and "Brand", "Price" and "Rate" are the same column. A column
  can be missing, moved or extra.
- Floors and areas come from the banner rows the app writes (a floor title over area titles - by
  their colours when the file still has them, otherwise by their order: a banner directly over
  another banner is a floor, the one over the lines is an area), or from an AREA column, or failing
  both from the sheet's own name. A workbook with one sheet per floor reads as one floor per sheet.
- An "ACCESSORIES" block marks its lines as accessories, and a driver is recognised by its words.
- A quotation's prices and discounts are kept: a line on the standard discount follows it, a line
  with a rate of its own keeps that rate.
- Cover-sheet details (project, code, area, client, date) and the NOTE page's wording come back too.

Nothing is dropped silently: a row that is a total, a repeated heading, a stray note - or a sheet
that is a summary of the others - is returned with its reason, for the page to show.

Matching to NPD
---------------
Only approved products of this tool are candidates, as in every other picker. In order:
1. the line's name as the app writes it - "<product name>_<category>" on the first line - or a
   product's own name at the start of the first line, or the manufacturer's code on the line;
2. otherwise a name that is nearly the same (a typo, a different dash), marked "check" so a person
   confirms it;
3. otherwise no product: the line becomes a typed row, with any near names offered as choices.
A brand on the line that is not the product's own also asks for a check. Accessory and driver lines
are not matched - they are not NPD products.

This module only reads. Nothing is stored here; app/main.py's import endpoint builds the BOQ, in one
transaction, from what the person confirmed.
"""
import datetime
import difflib
import io
import re
import unicodedata
import zipfile
from collections import Counter

from app.requirement_excel import SheetError
from app.requirement_reader import is_driver as text_names_driver
from app import match_index

MAX_BYTES = 20 * 1024 * 1024           # an exported BOQ carries its product pictures
# The live server has about a gigabyte, shared by everyone using the app, so a file is held to what a
# BOQ really is: pictures barely compress, so an exported BOQ unpacks to little more than its size.
MAX_UNPACKED_BYTES = 80 * 1024 * 1024
MAX_SHEET_XML_BYTES = 30 * 1024 * 1024
MAX_COLS = 60                          # a BOQ has under 25 columns; a sheet may claim 16,384
MAX_ROWS_PER_SHEET = 4000
MAX_LINES = 3000
HEADER_SCAN_ROWS = 40

# Each line field and the headings that mean it, compared after folding case, punctuation and any
# "(...)" away - "S. No" -> "s no", "Disc. %" -> "disc", "Description*" -> "description".
COLUMN_ALIASES = {
    "item_code": ["s no", "sno", "sr no", "srno", "sl no", "slno", "serial no", "serial number",
                  "item no", "line no", "no", "s n"],
    "image": ["image", "images", "picture", "photo", "product image", "product photo"],
    "ref_images": ["ref images", "reference images", "ref image", "ref pictures"],
    "dimensions": ["dimension", "dimensions", "size", "cutout", "cut out"],
    "reference_number": ["ref no", "ref", "reference", "reference no", "ref code", "reference number",
                         "ref number", "tag", "type", "fixture ref", "fixture type", "code"],
    "description": ["description", "item description", "product description", "specification",
                    "specifications", "details", "particulars", "product", "item"],
    "model_no": ["model no", "model", "model number", "article no", "article code", "part no",
                 "sku", "product code", "order code", "catalogue no", "cat no"],
    "make": ["make", "brand", "manufacturer"],
    "finish": ["finish", "colour", "color", "body colour", "body color", "body finish"],
    "wattage": ["wattage", "watt", "watts", "power", "wattage w", "w"],
    "beam": ["beam", "beam angle", "optic", "optics"],
    "control": ["control", "controls", "dimming", "driver", "protocol"],
    "led_color": ["led color", "led colour", "cct", "colour temperature", "color temperature",
                  "colour temp", "color temp"],
    "qty": ["qty", "quantity", "nos", "no of units", "count", "qnty"],
    "unit": ["unit", "uom", "units"],
    "remarks": ["remarks", "remark", "notes", "comments", "note"],
    "area": ["area", "location", "room", "zone", "space", "area room"],
    "floor": ["floor", "level", "floor level", "storey"],
    "price": ["price", "rate", "unit price", "unit rate", "list price", "mrp", "basic price",
              "price per unit", "rate per unit"],
    "discount": ["disc", "disc %", "discount", "discount %", "disc percent"],
    "discounted_price": ["discounted price", "net price", "net rate", "offer price"],
    "amount": ["amount", "total", "total amount", "value", "total price"],
    "acc_net_price": ["accessories net price", "accessory price", "accessories price"],
    "acc_amount": ["amount of accessories", "accessories amount"],
}
_ALIAS_TO_KEY = {alias: key for key, aliases in COLUMN_ALIASES.items() for alias in aliases}
TEXT_FIELDS = ("item_code", "reference_number", "model_no", "description", "dimensions", "finish",
               "wattage", "beam", "control", "led_color", "make", "qty", "unit", "remarks")
FIELD_LIMITS = {"description": 4000, "remarks": 1000}

# The fills the app gives its banners (app/excel_export.py: FLOOR_FILL, ROOM_FILL, REMARK_FILL).
FLOOR_FILLS = {"FCD5B5"}
AREA_FILLS = {"FFFF00"}

_TOTAL_WORDS = re.compile(r"^(sub ?total|total|grand total|net total|net amount|amount|gst|igst|cgst|"
                          r"sgst|tax|taxes|freight|packing|round(ed)? off|discount|less discount)\b")
_COVER_LABELS = {"project": "project_name", "project name": "project_name",
                 "project code": "project_code", "area": "area", "scope": "area", "area scope": "area",
                 "client": "client", "client name": "client", "kind attention": "kind_attention",
                 "kind attn": "kind_attention", "attention": "kind_attention"}


def _fold(text) -> str:
    text = re.sub(r"\(.*?\)", " ", str(text or "").lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def column_key(heading) -> str:
    """Which line field a heading names, or "" for none."""
    folded = _fold(heading)
    if folded in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[folded]
    # "Qty." / "QTY (Nos)" / "Price (INR)" fold to the alias already; a heading with a trailing
    # word ("Price per unit (INR)") is tried without its last word.
    words = folded.split()
    if len(words) > 1 and " ".join(words[:-1]) in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[" ".join(words[:-1])]
    return ""


def _line_text(value) -> str:
    """One-line text: numbers without a stray ".0", whitespace collapsed."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.strftime("%d-%m-%Y")
    return " ".join(str(value).split())


def _long_text(value) -> str:
    """Text that keeps its line breaks - a BOQ description is several lines on purpose."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    # Exactly as typed - a space at the end of a line included - with only Windows line endings
    # made plain and blank lines at either end dropped: the description comes back as it went out.
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip("\n")


_MONEY_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _number(value):
    """A number from a cell: 1250, "1,250.00", "Rs. N", "₹ N/-". None when there is none."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _MONEY_RE.search(str(value))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _fill_rgb(cell) -> str:
    try:
        rgb = cell.fill.fgColor.rgb
    except Exception:
        return ""
    return rgb[-6:].upper() if isinstance(rgb, str) and len(rgb) >= 6 else ""


def _is_bold(cell) -> bool:
    try:
        return bool(cell.font and cell.font.b)
    except Exception:
        return False


# --- the file ---------------------------------------------------------------------------------

def _open(filename: str, data: bytes, values: bool = True):
    name = (filename or "").lower()
    if len(data) > MAX_BYTES:
        raise SheetError("That file is over %d MB. An exported BOQ, pictures and all, is normally a few "
                         "MB - check it is the right file." % (MAX_BYTES // (1024 * 1024)))
    if name.endswith(".xls"):
        raise SheetError("This is the old .xls format. Open it in Excel, save it as .xlsx, and import that.")
    if name.endswith(".pdf"):
        raise SheetError("A PDF cannot be read back into a BOQ - import the Excel (.xlsx) version of it.")
    if not name.endswith((".xlsx", ".xlsm")):
        raise SheetError("Import an Excel file (.xlsx) - a BOQ or a quotation exported from here, or one "
                         "laid out like it.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            unpacked = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        raise SheetError("This file could not be opened as a spreadsheet (it is not a valid .xlsx).")
    # 20 MB of zip can claim to unpack to gigabytes; the archive's directory says so before anything
    # is unpacked, so such a file is refused unopened.
    if unpacked > MAX_UNPACKED_BYTES:
        raise SheetError("This spreadsheet unpacks to over %d MB, far more than a BOQ needs. Check it is "
                         "the right file." % (MAX_UNPACKED_BYTES // (1024 * 1024)))
    from openpyxl import load_workbook
    try:
        # read_only: pictures are not loaded, so a BOQ full of product photos costs little memory.
        # data_only: a formula's last computed value - what the person saw in Excel.
        return load_workbook(io.BytesIO(data), read_only=True, data_only=values)
    except Exception as exc:
        raise SheetError("This file could not be opened as a spreadsheet (%s)." % type(exc).__name__)


def _merged_ranges(data: bytes) -> dict:
    """{sheet title: [(min_row, min_col, max_row, max_col), ...]} - read from the sheets' XML, since a
    read-only workbook does not load merges. They say where an ACCESSORIES block ends and which rows
    are banners drawn across the whole table."""
    from openpyxl.utils.cell import range_boundaries
    out = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            book = archive.read("xl/workbook.xml").decode("utf-8", "replace")
            rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
            targets = {}
            for m in re.finditer(r"<Relationship\b[^>]*>", rels):
                tag = m.group(0)
                rid = re.search(r'\bId="([^"]+)"', tag)
                target = re.search(r'\bTarget="([^"]+)"', tag)
                if rid and target:
                    targets[rid.group(1)] = target.group(1)
            for m in re.finditer(r"<sheet\b[^>]*>", book):
                tag = m.group(0)
                title = re.search(r'\bname="([^"]*)"', tag)
                rid = re.search(r'\br:id="([^"]+)"', tag) or re.search(r'\bid="([^"]+)"', tag)
                if not (title and rid and rid.group(1) in targets):
                    continue
                path = targets[rid.group(1)].lstrip("/")
                path = path if path.startswith("xl/") else "xl/" + path
                try:
                    # A sheet's XML is read whole to find its merges, so a huge one is not read at
                    # all: the server has about a gigabyte, shared with everyone using the app.
                    if archive.getinfo(path).file_size > MAX_SHEET_XML_BYTES:
                        continue
                    xml = archive.read(path).decode("utf-8", "replace")
                except KeyError:
                    continue
                name = (title.group(1).replace("&amp;", "&").replace("&apos;", "'")
                        .replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))
                ranges = []
                for ref in re.findall(r'<mergeCell\b[^>]*\bref="([A-Z]+\d+:[A-Z]+\d+)"', xml):
                    min_col, min_row, max_col, max_row = range_boundaries(ref)
                    ranges.append((min_row, min_col, max_row, max_col))
                out[name] = ranges
    except Exception:
        return {}
    return out


def _find_header(rows):
    """(row index, {column index: key}) for the row naming the most line fields, with a description
    among them - a title block can name one field by coincidence, a table header names several."""
    best = None
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        mapping, seen = {}, set()
        for col, cell in enumerate(row):
            key = column_key(_line_text(cell.value) if cell is not None else "")
            if key and key not in seen:
                mapping[col] = key
                seen.add(key)
        if "description" in seen and len(mapping) >= 2 and (best is None or len(mapping) > len(best[1])):
            best = (index, mapping)
    return best


def _date_from(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.strftime("%Y-%m-%d")
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", _line_text(value), flags=re.I).replace(",", " ")
    text = " ".join(text.split())
    for fmt in ("%d %B %Y", "%d %b %Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%B %d %Y", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _read_cover(rows, project):
    """The cover's "LABEL : value" pairs, and its date (a line of its own above them)."""
    for row in rows[:60]:
        cells = [c for c in row if c is not None and _line_text(c.value)]
        if not cells:
            continue
        first = _line_text(cells[0].value)
        key = _COVER_LABELS.get(_fold(first.rstrip(":")))
        if key:
            values = [_line_text(c.value) for c in cells[1:] if _line_text(c.value).strip(" :")]
            if values and not project.get(key):
                project[key] = values[0].lstrip(": ").strip()
        elif not project.get("boq_date") and len(cells) == 1:
            date = _date_from(cells[0].value)
            if date:
                project["boq_date"] = date


def _read_notes(rows, project=None):
    """The NOTE page's wording, in the app's own convention: a line per row, a blank row a blank
    line, a bold line wrapped in **...**. The page's letterhead ("From:" and the company's address,
    "To:" and "Kind Attention:") is not wording - the app writes it on every NOTE page itself, so
    kept as text it would print twice. The addressee and the person named fill the project's own
    details when the cover did not give them."""
    from app.excel_export import COMPANY_LINES
    letterhead = {_fold(line) for line in COMPANY_LINES}
    project = project if project is not None else {}
    lines = []
    for row in rows[:400]:
        cells = [c for c in row if c is not None and _line_text(c.value)]
        if not cells:
            lines.append("")
            continue
        first = _fold(_line_text(cells[0].value))
        if first.startswith(("from", "to", "kind attention", "kind attn")) and len(first.split()) <= 3:
            value = " ".join(_line_text(c.value) for c in cells[1:]).strip(" -:")
            key = "client" if first.startswith("to") else "kind_attention" if first.startswith("kind") else ""
            if key and value and not project.get(key):
                project[key] = value
            continue
        # The letterhead comes before the wording; the same company name later is its signature.
        if not any(lines) and any(_fold(_line_text(c.value)) in letterhead for c in cells):
            continue
        text = " ".join(_long_text(c.value) for c in cells)
        lines.append("**%s**" % text if all(_is_bold(c) for c in cells) else text)
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def _strip_bold(text):
    return " ".join(text.replace("**", "").split()).lower()


def read_workbook(filename: str, data: bytes) -> dict:
    """Everything in the file the app can use, and everything it left out with the reason."""
    # _open first: its checks (type, size, how big the file unpacks to) must come before anything
    # unpacks a part of it, merges included.
    book = _open(filename, data)
    merges = _merged_ranges(data)
    # The same file again, for its formulas. A workbook the app wrote and nobody has re-saved in
    # Excel has no computed value for a formula, and a quotation keeps meaning in them: a line on
    # the standard discount reads "=Discount", an accessory's price sits inside "=50*(1-Q14)".
    formulas = _open(filename, data, values=False)
    result = {"project": {}, "floors": [], "skipped": [], "sheets": [], "discount_percent": None,
              "notes_text": None, "columns": {}}
    try:
        _read_standard_discount(book, result)
        item_sheets = []
        for ws in book.worksheets:
            rows = [list(row) for row in ws.iter_rows(max_row=MAX_ROWS_PER_SHEET, max_col=MAX_COLS)]
            header = _find_header(rows)
            title = ws.title
            if header:
                rows_f = [list(row) for row in formulas[title].iter_rows(max_row=MAX_ROWS_PER_SHEET, max_col=MAX_COLS)]
                item_sheets.append((ws, rows, rows_f, header))
                continue
            folded = _fold(title)
            body = " ".join(_fold(_line_text(c.value)) for r in rows[:80] for c in r if c is not None)
            if folded in ("note", "notes", "terms", "terms and conditions") or "terms of sale" in body:
                result["notes_text"] = _read_notes(rows, result["project"])
                result["sheets"].append({"name": title, "use": "The NOTE page's wording"})
            elif any(_COVER_LABELS.get(_fold(_line_text(r[0].value).rstrip(":"))) for r in rows[:60] if r and r[0] is not None):
                _read_cover(rows, result["project"])
                result["sheets"].append({"name": title, "use": "Project details"})
            else:
                result["sheets"].append({"name": title, "use": "",
                                         "why": "No table of lines here (no Description column)."})
        # A workbook with sheets of lines AND a sheet called a summary: the summary repeats the
        # others' lines, totalled - reading it too would count every product twice.
        if len(item_sheets) > 1:
            kept = [s for s in item_sheets if "summary" not in _fold(s[0].title)]
            if kept:
                for s in item_sheets:
                    if s not in kept:
                        result["sheets"].append({"name": s[0].title, "use": "",
                                                 "why": "A summary of the other sheets - its lines are read there."})
                item_sheets = kept
        for ws, rows, rows_f, header in item_sheets:
            _read_item_sheet(ws.title, rows, rows_f, header, merges.get(ws.title, []), result)
    finally:
        book.close()
        formulas.close()
    if result["notes_text"] is not None:
        from app.excel_export import DEFAULT_NOTES_TEXT
        if _strip_bold(result["notes_text"]) == _strip_bold(DEFAULT_NOTES_TEXT) or not result["notes_text"].strip():
            result["notes_text"] = None          # the standard wording: nothing of its own to keep
    if result.get("discount_percent") is not None and not 0 <= result["discount_percent"] <= 100:
        result["skipped"].append({"sheet": "", "row": None, "text": str(result["discount_percent"]),
                                  "why": "The file's Discount cell is not a percentage - left out."})
        result["discount_percent"] = None
    _settle_discounts(result)
    _settle_currency(result)
    _blank_running_numbers(result)
    lines = sum(len(a["rows"]) for f in result["floors"] for a in f["areas"])
    if not lines:
        raise SheetError("No BOQ lines were found in this file. It needs a table with a Description "
                         "column (and ideally Qty) - like a BOQ or quotation exported from here.")
    return result


def _read_standard_discount(book, result):
    """The quotation's standard discount: the cell the app names "Discount", held as a fraction."""
    try:
        defined = book.defined_names.get("Discount")
        if defined is None:
            return
        for sheet_title, coord in defined.destinations:
            number = _number(book[sheet_title][coord.replace("$", "")].value)
            if number is not None:
                result["discount_percent"] = round(number * 100, 4) if number <= 1 else number
                return
    except Exception:
        return


# "=1250" - a number typed as a formula; "=50*(1-Q14)" - the app's accessory price at its discount.
_FORMULA_PRICE = re.compile(r"^=\s*(-?\d+(?:\.\d+)?)\s*(?:\*\s*\(\s*1\s*-\s*\$?[A-Z]{1,3}\$?\d+\s*\))?\s*$")


def _formula_number(text):
    m = _FORMULA_PRICE.match(text or "") if isinstance(text, str) else None
    return float(m.group(1)) if m else None


class _Cell:
    """One cell's value as the person saw it, with its formula and style - None-safe."""

    def __init__(self, value_cell, formula_cell):
        self.cell = value_cell
        self.value = value_cell.value if value_cell is not None else None
        raw = formula_cell.value if formula_cell is not None else None
        self.formula = raw if isinstance(raw, str) and raw.startswith("=") else ""
        self.number_format = getattr(value_cell, "number_format", "") or ""

    @property
    def filled(self):
        """Something is in it: a value, or a formula (which has no value until Excel computes it)."""
        return self.value not in (None, "") or bool(self.formula)

    def number(self):
        if self.formula:
            constant = _formula_number(self.formula)
            if constant is not None:
                return constant        # the price inside the formula, before any discount
        return _number(self.value)


def _read_item_sheet(title, rows, rows_f, header, merges, result):
    header_index, mapping = header
    width = max((len(r) for r in rows), default=0)
    for col, key in mapping.items():
        result["columns"].setdefault(key, title)
    used_keys = set(mapping.values())
    head = rows[header_index]
    ignored = [_line_text(head[c].value) for c in range(len(head))
               if c not in mapping and head[c] is not None and _line_text(head[c].value)]
    result["sheets"].append({"name": title, "use": "Lines", "ignored_columns": ignored})
    col_of = {key: col for col, key in mapping.items()}
    image_col = col_of.get("image")
    for col, key in mapping.items():
        if key in MONEY_KEYS:
            code = _currency_in(_line_text(head[col].value) if head[col] is not None else "")
            if code:
                result.setdefault("currencies", []).append(code)

    # Which rows a merge covers: a banner is drawn across the table; an ACCESSORIES label is
    # merged down its block.
    wide_rows, accessory_rows = set(), set()
    for min_row, min_col, max_row, max_col in merges:
        if min_row == max_row and max_col - min_col + 1 >= max(3, (width or 1) // 2):
            wide_rows.add(min_row)
        if image_col is not None and min_col == max_col == image_col + 1 and max_row > min_row:
            first = rows[min_row - 1][image_col] if min_row - 1 < len(rows) and image_col < len(rows[min_row - 1]) else None
            if first is not None and _fold(_line_text(first.value)) == "accessories":
                accessory_rows.update(range(min_row, max_row + 1))

    floors = result["floors"]
    floor = area = None
    pending = []                 # banners without the app's colours, seen since the last line
    accessory_mode = False       # under an "ACCESSORIES" banner of a hand-made sheet
    column_areas = {}            # an AREA column's areas, one per name, per floor

    def get_floor(name):
        for f in floors:
            if f["name"].strip().casefold() == name.strip().casefold():
                return f
        f = {"name": name.strip(), "areas": []}
        floors.append(f)
        return f

    def new_area(f, name):
        # Two areas may share a name ("Toilet" on two floors, or twice on one): a banner is an area
        # of its own, whatever it is called.
        a = {"name": name.strip(), "remark": "", "rows": []}
        f["areas"].append(a)
        return a

    def floor_name_for(text):
        # The app writes a floor's banner in capitals and names the sheet after the floor; the
        # sheet's own name keeps the floor's real capitalisation.
        return title if text.strip().casefold() == title.strip().casefold() else text

    for index in range(header_index + 1, len(rows)):
        row = rows[index]
        row_f = rows_f[index] if index < len(rows_f) else []
        excel_row = index + 1
        cells = {key: _Cell(row[col] if col < len(row) else None, row_f[col] if col < len(row_f) else None)
                 for col, key in mapping.items()}
        texts = [_line_text(c.value) for c in row if c is not None and _line_text(c.value)]
        first_cell = row[0] if row else None
        fill = _fill_rgb(first_cell) if first_cell is not None else ""
        kind = "floor" if fill in FLOOR_FILLS else "area" if fill in AREA_FILLS else ""
        if not texts and not kind:
            continue
        if texts and sum(1 for c in row if c is not None and column_key(_line_text(c.value))) >= max(3, len(mapping) - 2):
            result["skipped"].append({"sheet": title, "row": excel_row, "text": " | ".join(texts)[:120],
                                      "why": "The column headings again."})
            continue
        description = _long_text(cells["description"].value) if "description" in cells else ""
        qty_text = _line_text(cells["qty"].value) if "qty" in cells else ""
        distinct = list(dict.fromkeys(texts))
        is_banner = kind or ((excel_row in wide_rows or len(distinct) == 1) and len(distinct) <= 1
                             and not (qty_text and description))
        if is_banner:
            # A banner's name exactly as typed, double spaces and all - only the ends trimmed.
            text = next((" ".join(str(c.value).splitlines()).strip() for c in row
                         if c is not None and _line_text(c.value)), "")
            folded = _fold(text)
            if folded.startswith("remark") and text:
                remark = re.sub(r"^\s*remarks?\s*[:\-]?\s*", "", text, flags=re.I)
                if area is not None:
                    area["remark"] = (area["remark"] + "\n" + remark).strip() if area["remark"] else remark
                else:
                    result["skipped"].append({"sheet": title, "row": excel_row, "text": text[:120],
                                              "why": "A remark before any area - nowhere to put it."})
                continue
            if folded in ("accessories", "accessory", "accessories drivers"):
                accessory_mode = True
                continue
            if text and _TOTAL_WORDS.match(folded):
                result["skipped"].append({"sheet": title, "row": excel_row, "text": text[:120],
                                          "why": "A total, not a line."})
                continue
            accessory_mode = False
            if kind == "floor":
                floor, area, pending = get_floor(floor_name_for(text)), None, []
            elif kind == "area":
                floor = floor or get_floor(title)
                area, pending = new_area(floor, text), []
            elif text:
                pending.append(text)
            continue

        # A line, or a row that only looks like one.
        if not description and not qty_text:
            result["skipped"].append({"sheet": title, "row": excel_row, "text": " | ".join(texts)[:120],
                                      "why": "No description and no quantity - not a line."})
            continue
        if description and _TOTAL_WORDS.match(_fold(description.split("\n")[0])) and not qty_text:
            result["skipped"].append({"sheet": title, "row": excel_row, "text": description[:120],
                                      "why": "A total, not a line."})
            continue
        if sum(len(a["rows"]) for f in floors for a in f["areas"]) >= MAX_LINES:
            result["skipped"].append({"sheet": title, "row": excel_row, "text": description[:120],
                                      "why": "Over %d lines - the rest were not read." % MAX_LINES})
            break

        # Banners without the app's colours, settled by their order: one over another is a floor
        # over an area; one alone over lines is an area of the current floor.
        if pending:
            if len(pending) >= 2:
                floor = get_floor(floor_name_for(pending[0]))
            floor = floor or get_floor(title)
            area = new_area(floor, pending[-1])
            pending = []
        floor_text = _line_text(cells["floor"].value) if "floor" in used_keys else ""
        if floor_text and (floor is None or floor["name"].casefold() != floor_text.casefold()):
            floor, area = get_floor(floor_text), None       # a FLOOR column names each line's floor
        floor = floor or get_floor(title)
        raw_area = cells["area"].value if "area" in used_keys else None
        # exactly as typed (only the ends trimmed), like a banner's name
        area_text = (" ".join(raw_area.splitlines()).strip() if isinstance(raw_area, str)
                     else _line_text(raw_area))
        if area_text:
            key = (id(floor), area_text.casefold())
            if key not in column_areas:
                column_areas[key] = new_area(floor, area_text)
            area = column_areas[key]
        if area is None:
            area = new_area(floor, "General")

        line = {"sheet": title, "row": excel_row}
        for key in TEXT_FIELDS:
            if key == "description":
                line[key] = description[:FIELD_LIMITS["description"]]
            else:
                line[key] = _line_text(cells[key].value)[:FIELD_LIMITS.get(key, 200)] if key in cells else ""
        line["unit"] = line["unit"] or "Nos."
        # What a person should look at before creating, each said in a sentence (the page shows them,
        # and a line with one counts as "to check").
        line["notes"] = []
        if not line["qty"]:
            line["qty"] = "1"
            line["notes"].append("The file's quantity was blank - 1 is assumed.")
        # The app keeps a line's CCT in its own field and prints it as "Color Temp:3000K" in the
        # description; a file without an LED colour column still says it there.
        if not line["led_color"]:
            line["led_color"] = _cct_from(description)
        price = cells["price"].number() if "price" in cells else None
        acc_price = cells["acc_net_price"].number() if "acc_net_price" in cells else None
        # A quotation puts an accessory's money in its own two columns and a product's in the others.
        acc_money = any(cells[k].filled for k in ("acc_net_price", "acc_amount") if k in cells)
        product_money = any(cells[k].filled for k in ("discounted_price", "amount") if k in cells)
        in_acc_block = (excel_row in accessory_rows or accessory_mode
                        or ("image" in cells and _fold(_line_text(cells["image"].value)) == "accessories")
                        or (acc_money and not product_money))
        line["is_driver"] = bool(text_names_driver(description.split("\n")[0])) if description else False
        line["is_accessory"] = bool(in_acc_block or line["is_driver"])
        line["price"] = price if price is not None else acc_price
        rate = _percent(cells.get("discount"))
        if rate is not None and not 0 <= rate <= 100:
            line["notes"].append("The discount column says %s, which is not a percentage - left out." % _line_text(rate))
            rate = None
        line["discount"] = rate
        # A net price typed over the app's formula (a price agreed with the client), or a file whose
        # only money column is a net one. Settled once the standard discount is known.
        net_cell = cells.get("discounted_price")
        line["net_price"] = (_number(net_cell.value)
                             if net_cell is not None and net_cell.value is not None and not net_cell.formula else None)
        area["rows"].append(line)
    for text in pending:
        result["skipped"].append({"sheet": title, "row": None, "text": text[:120],
                                  "why": "A heading with no lines under it."})


def _percent(cell):
    """A discount as a percentage, or None for "the standard discount". The app writes a line on the
    standard rate as the formula "=Discount", and 10% as 0.1 with a percent format; a person may
    type 10 or "10%"."""
    if cell is None or not cell.filled:
        return None
    if cell.formula and "discount" in cell.formula.lower():
        return None
    number = cell.number()
    if number is None:
        return None
    if "%" in cell.number_format or (0 < number < 1 and "%" not in str(cell.value)):
        number = number * 100
    return round(number, 4)


_CCT_LINE = re.compile(r"colou?r\s*temp(?:erature)?\s*[:\-]?\s*([0-9][0-9 ,]{2,5}\s*K)\b", re.I)


def _cct_from(description: str) -> str:
    m = _CCT_LINE.search(description or "")
    if m:
        return m.group(1).replace(" ", "").replace(",", "").upper()
    # No "Color Temp:" line: one colour temperature written anywhere ("..., 3000K") is it.
    found = match_index.read_cct((description or "",))
    return "%dK" % found[0][0] if len(found) == 1 and found[0][0] == found[0][1] else ""


def _settle_discounts(result):
    """Which lines keep a discount of their own. A quotation prints every product line's rate,
    standard ones included; only a rate different from the standard is the line's own. The
    standard is the file's Discount cell, or else the rate most product lines share."""
    product_rates = [r["discount"] for f in result["floors"] for a in f["areas"] for r in a["rows"]
                     if not r["is_accessory"] and r["discount"] is not None]
    standard = result["discount_percent"]
    if standard is None and product_rates:
        standard = Counter(product_rates).most_common(1)[0][0]
        result["discount_percent"] = standard
    for f in result["floors"]:
        for a in f["areas"]:
            for r in a["rows"]:
                rate = r.pop("discount")
                if rate is None:
                    r["discount_percent"] = None
                elif r["is_accessory"]:
                    # Accessories are quoted at their price unless given a rate of their own.
                    r["discount_percent"] = rate if rate else None
                else:
                    r["discount_percent"] = None if standard is not None and abs(rate - standard) < 1e-6 else rate
                net = r.pop("net_price")
                if net is None:
                    continue
                if r["price"] is None:
                    r["price"] = net
                    r["notes"].append("Priced from the file's net price column (there is no list price).")
                    continue
                if r["price"] <= 0:
                    continue
                applied = r["discount_percent"] if r["discount_percent"] is not None else (
                    0 if r["is_accessory"] else (standard or 0))
                if abs(r["price"] * (1 - applied / 100.0) - net) > 0.01:
                    own = round((1 - net / r["price"]) * 100, 4)
                    if 0 <= own <= 100:
                        r["discount_percent"] = own
                        r["notes"].append("The file's net price (%s) is not the price less its discount - kept "
                                          "as a discount of this line's own, %s%%." % (_line_text(net), _line_text(own)))
                    else:
                        r["notes"].append("The file's net price (%s) is above the list price - check this line's "
                                          "price." % _line_text(net))


MONEY_KEYS = ("price", "discounted_price", "amount", "acc_net_price", "acc_amount")
# The app names a quotation's currency once, in each money heading - "Price (USD)". A person may
# write "Rate (Rs.)" or "Amount in ₹" instead.
_CURRENCY_WORDS = (("INR", r"\binr\b|\brs\b\.?|₹|\brupees?\b"), ("USD", r"\busd\b|\$|\bdollars?\b"),
                   ("EUR", r"\beur\b|€|\beuros?\b"), ("GBP", r"\bgbp\b|£|\bpounds?\b"))


def _currency_in(heading: str) -> str:
    text = (heading or "").lower()
    for code, pattern in _CURRENCY_WORDS:
        if re.search(pattern, text):
            return code
    return ""


def _settle_currency(result):
    """The BOQ's currency, when the file's money headings agree on one. A quotation in dollars
    brought back as rupees would quote the same numbers in the wrong money."""
    codes = sorted(set(result.pop("currencies", [])))
    result["currency"] = codes[0] if len(codes) == 1 else None
    if len(codes) > 1:
        result["skipped"].append({"sheet": "", "row": None, "text": ", ".join(codes),
                                  "why": "The money columns name different currencies - the BOQ is "
                                         "created in INR; set its currency in the BOQ."})


def _blank_running_numbers(result):
    """An S.No that is just the line's running position (1, 2, 3 ... across the whole file) is the
    number the app's export fills in for a line with none of its own; it comes back blank, as it was,
    so the app keeps numbering the line where it stands. A number a person typed out of that order
    is kept."""
    position = 0
    for f in result["floors"]:
        for a in f["areas"]:
            for r in a["rows"]:
                position += 1
                if r["item_code"] == str(position):
                    r["item_code"] = ""


# --- matching to NPD ----------------------------------------------------------------------------

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")


def norm(text) -> str:
    """Folded for comparing names: case, accents, dashes and punctuation do not count."""
    text = unicodedata.normalize("NFKD", str(text or "")).translate(_DASHES)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


def _name_part(description: str):
    """(the product's name as the line gives it, the category after it) - the app writes a line's
    first row as "<product name>_<category>"."""
    first = (description or "").split("\n")[0].strip()
    if "_" in first:
        name, _, category = first.partition("_")
        return name.strip(), category.strip()
    return first, ""


_CODE_LINE = re.compile(r"(?:product|order|model)\s*code\s*[:\-]\s*([A-Za-z0-9][A-Za-z0-9 ./+\-]{3,})", re.I)


class Catalogue:
    """The approved products of one tool, indexed for matching lines to them."""

    def __init__(self, sheets):
        self.sheets = {s.id: s for s in sheets}
        self.by_name, self.by_code, self.by_token = {}, {}, {}
        self.longest_name = 1
        for s in sheets:
            name = norm(s.product_name)
            if name:
                self.by_name.setdefault(name, []).append(s)
                self.longest_name = max(self.longest_name, len(name.split()))
                for token in set(name.split()):
                    if len(token) >= 3:
                        self.by_token.setdefault(token, []).append(s)
            code = norm((s.fields or {}).get("product_code"))
            if len(code.replace(" ", "")) >= 5:
                self.by_code.setdefault(code, []).append(s)

    @staticmethod
    def summary(s, why=""):
        return {"id": s.id, "name": s.product_name or "", "make": s.make or "",
                "category": s.category or "", "typology": s.typology or "", "why": why}

    def _fit(self, s, line):
        """How many of the line's values the product offers - a tie-break among same-named products."""
        texts = norm(" ".join(str(v) for vals in (s.options or {}).values() for v in (vals or [])) + " " +
                     " ".join(str(v) for v in (s.fields or {}).values() if isinstance(v, (str, int, float))))
        score = 0
        for key in ("finish", "wattage", "beam", "led_color", "control"):
            value = norm(line.get(key))
            if value and value in texts:
                score += 1
        return score

    def _pick(self, found, line, how):
        """One product from several candidates, or a check when the file cannot settle it."""
        make = norm(line.get("make"))
        category = norm(_name_part(line.get("description", ""))[1])
        pool = list(dict.fromkeys(found))
        if make:
            same = [s for s in pool if norm(s.make) == make or (norm(s.make) and norm(s.make) in make)]
            if same:
                pool = same
        if category and len(pool) > 1:
            same = [s for s in pool if norm(s.category) == category]
            if same:
                pool = same
        pool.sort(key=lambda s: (-self._fit(s, line), -s.id))
        best = pool[0]
        others = [self.summary(s) for s in pool[1:6]]
        status, why = "matched", how
        if len(pool) > 1 and self._fit(pool[0], line) == self._fit(pool[1], line):
            status = "check"
            why = "%d products in NPD are called this - the closest is chosen; check it is the right one." % len(pool)
        if make and norm(best.make) and norm(best.make) != make and norm(best.make) not in make:
            status = "check"
            why = "The line says make %s; this product is %s." % (line.get("make"), best.make)
        return {"status": status, "product": self.summary(best, why), "alternatives": others}

    def match(self, line) -> dict:
        if line.get("is_accessory"):
            return {"status": "none", "product": None, "alternatives": [],
                    "why": "Comes in as an accessory row, as written."}
        description = line.get("description") or ""
        name, _category = _name_part(description)
        key = norm(name)
        if key in self.by_name:
            return self._pick(self.by_name[key], line, "Same name as in NPD.")
        # A product's own name at the start of the first line ("NYX -Midi Recessed Downlight ...").
        words = norm(description.split("\n")[0]).split()
        for size in range(min(len(words), self.longest_name), 0, -1):
            prefix = " ".join(words[:size])
            if len(prefix) >= 4 and prefix in self.by_name:
                return self._pick(self.by_name[prefix], line, "The line starts with this NPD name.")
        # The manufacturer's code, in a Model column or on a "Product Code:" line.
        codes = [line.get("model_no") or ""] + _CODE_LINE.findall(description)
        for code in codes:
            c = norm(code)
            if c in self.by_code:
                return self._pick(self.by_code[c], line, "The line carries this product's manufacturer's code.")
        # A nearly identical name: a typo, a missing hyphen.
        candidates = {}
        for token in set(key.split()):
            for s in self.by_token.get(token, []):
                candidates[s.id] = s
        scored = []
        for s in candidates.values():
            ratio = difflib.SequenceMatcher(None, key, norm(s.product_name)).ratio()
            if ratio >= 0.6:
                scored.append((ratio, s))
        scored.sort(key=lambda x: (-x[0], -x[1].id))
        if scored and scored[0][0] >= 0.92:
            picked = self._pick([s for r, s in scored if r >= scored[0][0] - 1e-9], line, "")
            picked["status"] = "check"
            picked["product"]["why"] = "A nearly identical name (%s) - check it is the right product." % scored[0][1].product_name
            return picked
        return {"status": "none", "product": None,
                "alternatives": [self.summary(s, "A similar name.") for _r, s in scored[:5]],
                "why": "No NPD product has this name - it comes in unlinked, with the file's text."}


def wants_for(line) -> dict:
    """The line's values in the form match_index.choose_options reads, for pricing it from NPD."""
    wants = {}
    unit, metre = match_index.read_wattage((line.get("wattage") or "",))
    if unit or metre:
        lo, hi = (unit or metre)[0]
        wants["wattage"] = {"lo": lo, "hi": hi, "per_metre": not unit}
    cct = match_index.read_cct((line.get("led_color") or "",))
    if cct:
        wants["cct"] = {"lo": cct[0][0], "hi": cct[0][1]}
    beam = match_index.read_beam((line.get("beam") or "",))
    if beam:
        wants["beam_angle"] = {"lo": beam[0][0], "hi": beam[0][1]}
    control = match_index.read_control((line.get("control") or "",))
    if control:
        wants["control"] = sorted(control)[0]
    # The description carries what has no column of its own - "IP20", "CRI > 90" - and NPD prices
    # some products by them (a strip's IP rating is its adder; left out, it was priced at 0).
    description = line.get("description") or ""
    ip = match_index.read_ip((description,))
    if len(ip) == 1:
        wants["ip_rating"] = "".join(str(x) for x in ip[0])
    cri = match_index.read_cri((description,))
    if len(cri) == 1:
        wants["cri"] = cri[0]
    return wants
