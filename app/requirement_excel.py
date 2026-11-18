"""The Match tab's spreadsheet: a template to fill, and the reader for a filled one.

Why a spreadsheet
-----------------
Client requirement documents arrive in every layout there is, some of them scanned. Reading them
is the hard, error-prone part, and it is exactly what a person with a general-purpose assistant
on their own machine can do well - into a fixed set of columns. Once it is columns, nothing here
has to guess where a fixture starts or which number is its wattage: each value sits under a
heading that says what it is. So the template fixes the headings, and the "How to fill" sheet
carries a prompt that asks for exactly them.

The template is the contract, not a straitjacket: a sheet made some other way is still read if
its headings are recognisable ("Power", "Colour temperature", "Location"...), using the same
header vocabulary the PDF reader uses (app/requirement_reader.classify_header).

Nothing is dropped silently
---------------------------
A row that states no product - a note, a total, a blank line with a stray value - is returned in
`ignored` with its reason, and a column that means nothing to the matcher is listed in
`ignored_columns`. The Match tab shows both, as it does for a PDF.

Values are returned as written. The page parses them (one parser for spreadsheets, PDFs, pasted
text and a person's own edits), so "12 W/m" keeps its per-metre meaning all the way to matching.
"""
import csv
import io
import re
import zipfile

from app.requirement_reader import classify_header, is_accessory, is_driver, looks_non_product
from app.shape_classifier import classify_shape
from app.typology_classifier import classify_typology
from app.typology_match import typology_key

# (key, heading as the template prints it, column width). The order is the template's order.
COLUMNS = [
    ("ref", "Ref", 10),
    ("description", "Description", 48),
    ("typology", "Typology", 22),
    ("shape", "Shape", 11),
    ("area", "Area", 20),
    ("qty", "Qty", 7),
    ("unit", "Unit", 7),
    ("wattage", "Wattage (W)", 12),
    ("lumen_output", "Lumen output (lm)", 15),
    ("cct", "CCT (K)", 10),
    ("beam_angle", "Beam angle (°)", 13),
    ("cri", "CRI", 7),
    ("ip_rating", "IP rating", 10),
    ("ugr", "UGR", 7),
    ("control", "Control", 14),
    ("finish", "Finish", 16),
    ("mounting", "Mounting", 14),
    ("source", "Source", 12),
    ("remarks", "Remarks", 30),
]
SPEC_KEYS = ("wattage", "lumen_output", "cct", "beam_angle", "cri", "ip_rating", "ugr", "control",
             "finish", "mounting")

# Exact headings first - the template's own, and the plain alternatives an assistant or a person
# is likely to write. Compared after folding case, punctuation and any "(unit)" suffix away.
_ALIASES = {
    "ref": ["ref", "reference", "ref code", "ref no", "reference code", "tag", "fixture ref",
            "fixture code", "item code", "code", "symbol"],
    "description": ["description", "fixture description", "details", "specification",
                    "specifications", "item description", "product description"],
    "typology": ["typology", "luminaire typology", "product typology", "category",
                 "luminaire category", "product category"],
    "shape": ["shape"],
    "area": ["area", "areas", "location", "locations", "room", "rooms", "zone", "space"],
    "qty": ["qty", "quantity", "nos", "count", "no of units"],
    "unit": ["unit", "uom", "units"],
    "wattage": ["wattage", "power", "watt", "watts", "led power", "system power", "wattage w"],
    "lumen_output": ["lumen output", "lumens", "lumen", "flux", "luminous flux", "delivered lumens",
                     "lumen output lm"],
    "cct": ["cct", "colour temperature", "color temperature", "cct k"],
    "beam_angle": ["beam angle", "beam", "optics", "optic", "beam angle deg"],
    "cri": ["cri", "ra"],
    "ip_rating": ["ip rating", "ip", "ingress protection"],
    "ugr": ["ugr"],
    "control": ["control", "controls", "dimming", "driver", "protocol"],
    "finish": ["finish", "colour", "color", "body colour", "body color"],
    "mounting": ["mounting", "installation", "mounting type"],
    "source": ["source", "page", "source page", "reference page"],
    "remarks": ["remarks", "remark", "notes", "comments"],
}
_ALIAS_TO_KEY = {alias: key for key, aliases in _ALIASES.items() for alias in aliases}

# The PDF reader's header kinds that mean the same as one of ours. Its other kinds (image, price,
# brand, model, serial number, cut-out, dimensions...) are not something the matcher compares.
_READER_KIND_TO_KEY = {
    "ref": "ref", "description": "description", "wattage": "wattage", "cct": "cct",
    "beam_angle": "beam_angle", "cri": "cri", "ip_rating": "ip_rating", "control": "control",
    "finish": "finish", "mounting": "mounting", "lumen_output": "lumen_output", "ugr": "ugr",
    "qty": "qty", "unit": "unit", "area": "area", "remarks": "remarks",
}

# The two Typology values that are not catalogue typologies. A line that is not a luminaire is
# still a line to be quoted, so it needs somewhere to go in this column - without them, the only
# way to describe a driver was to leave it out.
EXTRA_TYPOLOGIES = ("Driver", "Accessory")

MAX_BYTES = 5 * 1024 * 1024
MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_ROWS = 2000
HEADER_SCAN_ROWS = 25


class SheetError(ValueError):
    """A spreadsheet that cannot be read, with a sentence the page can show as it is."""


def _fold(text):
    text = re.sub(r"\(.*?\)", " ", str(text or "").lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def column_key(heading) -> str:
    """Which requirement field a heading names, or "" for none."""
    folded = _fold(heading)
    if not folded:
        return ""
    if folded in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[folded]
    return _READER_KIND_TO_KEY.get(classify_header(str(heading)), "")


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())


# --- reading ---------------------------------------------------------------------------------

def _rows_from_upload(filename: str, data: bytes):
    """(sheet name, list of rows as lists of text) from an .xlsx/.xlsm or .csv upload."""
    if len(data) > MAX_BYTES:
        raise SheetError("That file is over 5 MB - a requirement sheet is normally a few hundred KB. "
                         "Check it is the right file.")
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return "CSV", [[_cell_text(c) for c in row] for row in csv.reader(io.StringIO(text))][:MAX_ROWS + HEADER_SCAN_ROWS]
    if name.endswith(".xls"):
        raise SheetError("This is the old .xls format. Open it in Excel and save it as .xlsx, then "
                         "import that.")
    if not name.endswith((".xlsx", ".xlsm")):
        raise SheetError("Import an Excel file (.xlsx) or a .csv - ideally the template from "
                         "\"Download template\".")
    from openpyxl import load_workbook
    # An .xlsx is a zip, and 5 MB of zip can claim to unpack to gigabytes - the classic
    # decompression bomb. The archive's own directory says how big its parts are before any of
    # them is unpacked, so a sheet that would not fit in reason is refused unopened.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            unpacked = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        raise SheetError("This file could not be opened as a spreadsheet (it is not a valid .xlsx).")
    if unpacked > MAX_UNPACKED_BYTES:
        raise SheetError("This spreadsheet unpacks to over %d MB, far more than a requirement sheet "
                         "needs. Check it is the right file." % (MAX_UNPACKED_BYTES // (1024 * 1024)))
    try:
        # data_only: a formula cell's last computed value, which is what the person sees.
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise SheetError("This file could not be opened as a spreadsheet (%s)." % type(exc).__name__)
    try:
        # The template's own sheet if it is there, otherwise whichever sheet has the best header.
        sheets = [book["Requirements"]] if "Requirements" in book.sheetnames else list(book.worksheets)
        best = None
        for ws in sheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append([_cell_text(c) for c in row])
                if len(rows) >= MAX_ROWS + HEADER_SCAN_ROWS:
                    break
            header = _find_header(rows)
            if header and (best is None or len(header[1]) > len(best[2][1])):
                best = (ws.title, rows, header)
            if header and ws.title == "Requirements":
                break
        if best is None:
            return (sheets[0].title if sheets else ""), []
        return best[0], best[1]
    finally:
        book.close()


def _find_header(rows):
    """(row index, {column index: key}) for the row naming the most requirement fields, or None.
    Two recognised headings at least - one could be a coincidence in a title block."""
    best = None
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        mapping, seen = {}, set()
        for col, text in enumerate(row):
            key = column_key(text)
            if key and key not in seen:
                mapping[col] = key
                seen.add(key)
        if len(mapping) >= 2 and (best is None or len(mapping) > len(best[1])):
            best = (index, mapping)
    return best


def _resolve_typology(written: str, description: str, known: dict):
    """(typology key, how it was decided). `known` maps key -> display name for this catalogue.

    given   - the sheet named a typology this catalogue has
    unknown - the sheet named one it does not have (shown to the person, never matched)
    guessed - the sheet left it blank and the description names one
    missing - neither
    """
    if written:
        key = typology_key(written)
        return (key, "given") if key in known else ("", "unknown")
    guess = typology_key(classify_typology(description.split(";", 1)[0]))
    if guess and guess in known:
        return guess, "guessed"
    return "", "missing"


# "Driver" and "Accessory" are offered in the template's Typology dropdown alongside the
# catalogue's own typologies, because they are what a person (or their assistant) reaches for
# when a line is not a luminaire. Anything else typed there is a typology to resolve.
_ACCESSORY_TYPOLOGY = re.compile(r"^\s*(accessor(?:y|ies)|part|component|hardware)\s*$", re.IGNORECASE)


def _classify_kind(typed: str, description: str) -> str:
    """"fixture", "driver" or "accessory" - what kind of line item this row is.

    What the Typology column says wins, because it is the one field a person filled in on
    purpose; only when it is empty is the description read.
    """
    if typed:
        if is_driver(typed):
            return "driver"
        if _ACCESSORY_TYPOLOGY.match(typed):
            return "accessory"
        return "fixture"
    if is_driver(description):
        return "driver"
    if is_accessory(description):
        return "accessory"
    return "fixture"


# A schedule that numbers its items 1, 2, 3... is the common case, and a gap in that run is the
# one reliable sign that something was dropped on the way in - an assistant asked to convert a
# PDF leaves out the rows it decided were not fixtures, and nothing else would ever say so.
def _ref_gaps(requirements: list) -> dict:
    numbers = []
    for r in requirements:
        text = str(r.get("ref") or "").strip()
        if text.isdigit():
            numbers.append(int(text))
    # Only when the refs really are a numbered run: a handful of numeric refs among codes like
    # "DL-02" says nothing, and neither does a run of two.
    if len(numbers) < 3 or len(numbers) < len(requirements) * 0.8:
        return {}
    lo, hi = min(numbers), max(numbers)
    missing = sorted(set(range(lo, hi + 1)) - set(numbers))
    if not missing:
        return {}
    return {"from": lo, "to": hi, "missing": missing, "read": len(numbers)}


def read_requirements(filename: str, data: bytes, typologies: list) -> dict:
    """Every requirement in an uploaded sheet, plus what was left out and why.

    `typologies` is match_index.typology_choices() for this catalogue - the only typologies a
    requirement can be matched against."""
    sheet, rows = _rows_from_upload(filename, data)
    header = _find_header(rows)
    if header is None:
        raise SheetError("No header row found. The sheet needs headings such as Description, "
                         "Typology, Wattage (W) and CCT (K) - use \"Download template\" for the "
                         "exact set.")
    header_index, mapping = header
    header_row = rows[header_index]
    ignored_columns = [text for col, text in enumerate(header_row) if text and col not in mapping]
    known = {t["key"]: t["name"] for t in typologies}

    requirements, ignored = [], []
    for offset, row in enumerate(rows[header_index + 1:header_index + 1 + MAX_ROWS]):
        cells = {key: (row[col] if col < len(row) else "") for col, key in mapping.items()}
        if not any(cells.values()):
            continue
        sheet_row = header_index + 2 + offset            # 1-based, as the spreadsheet numbers it
        description = cells.get("description", "")
        specs = {k: cells.get(k, "") for k in SPEC_KEYS if cells.get(k, "")}
        if not description and not cells.get("typology") and not specs:
            ignored.append({"row": sheet_row, "text": " | ".join(v for v in row if v)[:240],
                            "reason": "no description, typology or specification"})
            continue
        if not specs and not cells.get("typology") and looks_non_product(description):
            ignored.append({"row": sheet_row, "text": description[:240],
                            "reason": "a note about the document, not a product"})
            continue
        # Every line item in the client's schedule is kept, whatever it is. A quotation cannot
        # offer fewer lines than were asked for, so a driver, a power supply, a connector or an
        # end cap is a row like any other - it is simply answered differently: a driver from the
        # driver catalogue, an accessory as a line of its own carrying the sheet's own words.
        # Only a row that is not a line item at all (a note, a total, an empty row) is left out.
        typed = cells.get("typology", "")
        kind = _classify_kind(typed, description)
        if kind != "fixture":
            key, how, shape = "", kind, ""
        else:
            key, how = _resolve_typology(typed, description, known)
            shape = classify_shape(cells.get("shape", "")) or classify_shape(description.split(";", 1)[0])
        requirements.append({
            "row": sheet_row,
            "kind": kind,
            "ref": cells.get("ref", ""),
            "description": description,
            "typology": key,
            "typology_written": cells.get("typology", ""),
            "typology_status": how,
            "shape": shape,
            "shape_status": ("given" if cells.get("shape") else "guessed") if shape else "",
            "area": cells.get("area", ""),
            "qty": cells.get("qty", ""),
            "unit": cells.get("unit", ""),
            "fields": specs,
            "source": cells.get("source", ""),
            "remarks": cells.get("remarks", ""),
        })
    gaps = _ref_gaps(requirements)
    return {"sheet": sheet, "header_row": header_index + 1, "ref_gaps": gaps,
            "columns": [header_row[col] for col in sorted(mapping)],
            "ignored_columns": ignored_columns, "requirements": requirements, "ignored": ignored}


def has_pictures(filename: str, data: bytes) -> bool:
    """Whether an .xlsx carries pictures at all - read from the archive's own list of parts,
    without opening the workbook."""
    if not (filename or "").lower().endswith((".xlsx", ".xlsm")):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return any(n.startswith("xl/media/") for n in archive.namelist())
    except zipfile.BadZipFile:
        return False


def sheet_pictures(data: bytes, sheet_title: str) -> dict:
    """{sheet row (1-based): (bytes, format)} for pictures placed in the sheet - the leftmost one
    when a row has several. A picture is taken to belong to the row its top-left corner is in,
    which is where Excel anchors a picture dropped into a cell. One floating free of any cell
    has no row, and is left out.

    The full workbook has to be loaded for this (read-only mode does not read drawings), so it is
    only called when has_pictures() says there is something to find."""
    from openpyxl import load_workbook
    book = load_workbook(io.BytesIO(data), data_only=True)
    ws = book[sheet_title] if sheet_title in book.sheetnames else book.worksheets[0]
    found = {}
    for image in getattr(ws, "_images", []):
        start = getattr(image.anchor, "_from", None)
        if start is None:
            continue
        try:
            blob = image._data()
        except Exception:
            continue
        row = start.row + 1
        if row not in found or start.col < found[row][2]:
            found[row] = (blob, getattr(image, "format", "") or "", start.col)
    return {row: (blob, fmt) for row, (blob, fmt, _col) in found.items()}


# --- the template ----------------------------------------------------------------------------

def llm_prompt(typology_names: list) -> str:
    """What to paste into an assistant along with the client's document.

    Rule 1 is the one that matters and the reason the rest are numbered after it. An assistant
    asked to convert a lighting schedule will quietly drop the lines it judges not to be
    fixtures - on one real 26-item schedule it returned 15 rows, leaving out nine drivers and
    two connectors without saying so. A quotation cannot offer fewer lines than were asked for,
    so the count is stated as the job, the classification is made a column rather than a filter,
    and the assistant is asked to count both ends and show its working.
    """
    headings = ", ".join(heading for _key, heading, _w in COLUMNS)
    return (
        "Convert the attached lighting requirement document into a table with exactly these "
        "columns, in this order: " + headings + ".\n\n"
        "Rules:\n"
        "1. EVERY LINE ITEM GETS A ROW, AND THE ROW COUNT MUST MATCH. Drivers, power supplies, "
        "connectors, profiles, end caps, track rails and brackets are line items and each gets "
        "its own row, exactly like a luminaire. Do not leave anything out because it is not a "
        "fixture - that decision is not yours to make, it is a column (see rule 3). Do not merge "
        "two items into one row, and do not split one item across rows.\n"
        "2. Count before you answer. Count the line items in the document, count the rows in your "
        "table, and state both numbers under the table like this: \"Document: 26 line items. "
        "Table: 26 rows.\" If they differ, fix the table, not the count.\n"
        "3. Typology is one of: " + ", ".join(typology_names) + ", or Driver for a driver or power "
        "supply, or Accessory for a connector, end cap, profile, bracket or any other part. "
        "If you cannot tell what a line is, LEAVE TYPOLOGY EMPTY AND KEEP THE ROW. A row with an "
        "empty Typology is fine - the person classifies it in the app in one click. A missing row "
        "is not fine, because nobody can see what is not there.\n"
        "4. Never guess a value. If the document does not state one, leave that cell empty. An "
        "empty cell is correct; an invented value is wrong; a missing row is worse than either.\n"
        "5. Numbers only in Wattage (W), Lumen output (lm), CCT (K), Beam angle (°), CRI and UGR "
        "- for example 12, not 12W. Write a range as 10-55. For strips, write per-metre values as "
        "12/m. For a driver, its own wattage goes in Wattage (W).\n"
        "6. IP rating as IP65. Control as one of DALI, 0-10V, Phase Cut, On/Off, DMX, Casambi, "
        "Zigbee.\n"
        "7. Ref is the document's own code or number for the item, copied exactly as printed "
        "(1, 2, 3... or L1, DL-02) - each row's picture is found in the document by it. Never "
        "renumber and never close a gap: if the document numbers its items 1 to 26, the Ref "
        "column must run 1 to 26 with none missing. Area is where it goes. Qty is the quantity "
        "as a number.\n"
        "8. Source is the number of the page the row came from, as a number only (e.g. 3).\n"
        "9. Leave out only what is not a line item at all: notes, terms, totals, headings and "
        "page furniture.\n"
        "10. Do not put pictures in the table - text only.\n\n"
        "Return the table so it can be pasted into Excel starting at cell A1, header row "
        "included, then the two counts from rule 2."
    )


def build_template(typologies: list) -> bytes:
    """The workbook behind "Download template"."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    names = [t["name"] for t in typologies]
    book = Workbook()
    ws = book.active
    ws.title = "Requirements"
    head_fill = PatternFill("solid", fgColor="4A3220")
    for col, (_key, heading, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=heading)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22

    lists = book.create_sheet("Lists")
    lists["A1"] = "Typology"
    options = list(EXTRA_TYPOLOGIES) + names
    for i, name in enumerate(options, start=2):
        lists.cell(row=i, column=1, value=name)
    lists.sheet_state = "hidden"
    col_of = {key: get_column_letter(i) for i, (key, _h, _w) in enumerate(COLUMNS, start=1)}
    typology_dv = DataValidation(type="list", formula1="=Lists!$A$2:$A$%d" % (len(options) + 1),
                                 allow_blank=True, showErrorMessage=False)
    typology_dv.promptTitle = "Typology"
    typology_dv.prompt = ("Pick from the list. Driver and Accessory are at the top - use them for a "
                          "line that is not a luminaire, so it is still quoted. Leave it empty if you "
                          "cannot tell; never delete the row.")
    typology_dv.showInputMessage = True
    shape_dv = DataValidation(type="list", formula1='"Round,Square,Dual Square"', allow_blank=True)
    ws.add_data_validation(typology_dv)
    ws.add_data_validation(shape_dv)
    typology_dv.add("%s2:%s%d" % (col_of["typology"], col_of["typology"], MAX_ROWS + 1))
    shape_dv.add("%s2:%s%d" % (col_of["shape"], col_of["shape"], MAX_ROWS + 1))

    how = book.create_sheet("How to fill", 0)
    how.column_dimensions["A"].width = 110
    lines = [
        ("Requirement template - Quotation Tool, Match tab", Font(bold=True, size=14)),
        ("", None),
        ("One row per line item on the Requirements sheet - the same number of rows as the client's "
         "document has items. Leave a cell empty when the document does not say: an empty cell is "
         "never held against a product, a wrong value is, and a missing row is worse than both.", None),
        ("Typology decides which products are offered at all, so pick it from the dropdown.", None),
("EVERY line item in the client's document gets a row here - a driver, a power supply, a "
         "connector, a profile, an end cap, a track rail, all of them. A quotation cannot offer fewer "
         "lines than were asked for. Pick Driver or Accessory at the top of the Typology dropdown for "
         "those; a Driver row is answered from the app's own driver catalogue, an Accessory row goes "
         "in as its own line carrying this sheet's words.", None),
        ("If you cannot tell what a line is, leave Typology empty and keep the row - one click "
         "classifies it in the app. Deleting the row is the one thing that cannot be put right, "
         "because nobody can see what is not there.", None),
        ("Keep the document's own numbering in Ref, gaps and all. If the document numbers its items "
         "1 to 26, this sheet should show Ref 1 to 26 - the app checks for a break in that run on "
         "import and tells you which numbers are missing.", None),
        ("Numbers only in Wattage, Lumen output, CCT, Beam angle, CRI and UGR (12, not 12W). "
         "Ranges as 10-55. Per-metre strip values as 12/m.", None),
        ("Ref and Source matter: attach the client's PDF when you import this sheet, and each row's "
         "picture is taken from its Source page, found by its Ref - so no pictures need to go in "
         "this sheet. (A picture pasted into a row of this sheet is used too.)", None),
        ("", None),
        ("Converting a PDF with an AI assistant", Font(bold=True, size=12)),
        ("Attach the client's document to your assistant and paste the prompt below. Then paste "
         "its table into the Requirements sheet at A1 (replacing the header row) and check a few "
         "rows against the document before importing - an assistant can misread a value and still "
         "produce a tidy table.", None),
        ("Check your company's policy first: this sends the client's document to that assistant.",
         Font(italic=True, color="A33B2C")),
        ("On a free plan, a long document can use up the allowance part-way. Ask for a few pages at a "
         "time (\"pages 1-3 only\"), paste each part under the last, and keep one header row. If a "
         "reply stops mid-table, ask it to continue from the last Ref. Check each part's two counts "
         "before you paste it: that is the whole point of asking for them.", None),
        ("", None),
        (llm_prompt(names), None),
    ]
    for row, (text, font) in enumerate(lines, start=1):
        cell = how.cell(row=row, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if font:
            cell.font = font
    book.active = book.sheetnames.index("Requirements")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
