"""Generates the studio's Quotation .xlsx - a different document from the working BOQ
export (app/excel_export.py): same Cover sheet and terms/NOTE page, but a Summary sheet
with per-floor subtotals up front, and item tables with pricing (Price/Discounted Price/
Amount, plus a separate Accessories Net Price/Amount of Accessories pair for accessory
rows) instead of the BOQ's plain product-tracking columns. Built directly against a real
sample (SampleInputs/SampleQuotation/23.03.2026 Studio-Sample Project.pdf) - see TUTORIAL.md
for the column-by-column comparison against that PDF and the simplifications made.

Reuses excel_export.py's Cover/NOTE sheet writers and shared constants (COMPANY_LINES,
LOGO_PATH, fonts, borders, _item_blocks) rather than duplicating them, so the letterhead/
logo/terms text can never drift between the two export formats - the same reasoning
pdf_export.py already applies to excel_export.py.

Discounts (reworked 2026-09-29). The quotation's standard discount (BOQ.discount_percent) goes
on PRODUCT lines only; accessories, drivers and power supplies are quoted at the price entered.
Any single line can carry a discount of its own instead (BOQItem.discount_percent), 0 included.
line_discount() below is the one place that rule is written down, and every priced export -
this one, the PDF, the summaries, the quick BOQ's consolidated pair - asks it. The sample PDF
this was built from discounted some accessory rows and not others; a per-line discount is how
such a quotation is now made on purpose, rather than guessed at by a rule.

Whether the client sees the discount at all is decided once per document (DiscountPlan), by the
quotation's "Show the discount on the quotation" setting and nothing else (2026-10-01): ticked,
every line carries its DISC. % and discounted price - 0% included - and the document states
its standard discount; unticked, final prices only, with no trace of a discount anywhere.
"""
import math
import re
import zipfile
from types import SimpleNamespace
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils import get_column_letter, quote_sheetname
from openpyxl.comments import Comment
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.utils.units import pixels_to_EMU, points_to_pixels

from .excel_export import (
    line_diagram_for, _place_diagram, _place_pictures, item_ref_pictures, boq_has_ref_pictures, FONT_HEADER_COMPACT,
    COMPANY_LINES, BORDER_ALL, CENTER_WRAP, LEFT_WRAP, CENTER,
    FONT_COMPANY_BOLD, FONT_COMPANY, FONT_HEADER, FONT_BODY, FONT_QTY,
    FLOOR_FILL, ROOM_FILL, REMARK_FILL, FONT_FLOOR, FONT_ROOM, FONT_REMARK,
    IMAGE_TARGET_WIDTH_PX, IMAGE_CELL_PADDING_PX, PX_TO_PT,
    _item_blocks, _col_width_to_px, write_addressees, write_banner_row,
    _write_cover_sheet, _write_note_sheet, _autofill_sno,
)
from .excel_fit import fit_heading_row, fit_number_columns, fit_rows

QUOTATION_COLUMN_KEYS = [
    "s_no", "ref_no", "make", "image", "ref_images", "dimensions", "model_no", "description", "led_color",
    "wattage", "finish", "beam", "control", "area", "qty", "unit",
    "price", "discount", "discounted_price", "amount", "acc_net_price", "acc_amount",
]
QUOTATION_COLUMN_LABELS = {
    "s_no": "S. No.", "ref_no": "REF NO.", "make": "MAKE", "image": "IMAGE",
    "ref_images": "REF. IMAGES", "dimensions": "DIMENSION",
    "model_no": "Model No.", "description": "DESCRIPTION", "led_color": "LED COLOR",
    "wattage": "WATTAGE", "finish": "FINISH", "beam": "BEAM ANGLE", "control": "CONTROL",
    "area": "AREA", "qty": "QTY", "unit": "UNIT", "price": "Price", "discount": "Disc. %",
    "discounted_price": "Discounted Price", "amount": "Amount",
    "acc_net_price": "Accessories Net Price", "acc_amount": "Amount of Accessories",
}
QUOTATION_COL_WIDTHS = {
    "s_no": 6.5, "ref_no": 10, "make": 10, "image": 15, "ref_images": 17, "dimensions": 16, "model_no": 12,
    "description": 32,
    "led_color": 9, "wattage": 8, "finish": 10, "beam": 9, "control": 11, "area": 14,
    "qty": 6, "unit": 7, "price": 11, "discount": 8, "discounted_price": 12, "amount": 12,
    "acc_net_price": 12, "acc_amount": 13,
}
# Columns whose value only appears on a block's first physical row (a product's own
# accessory sub-rows show blank here) - matches the sample PDF's own visual pattern,
# achieved by simply not writing a value on later rows rather than real cell merging,
# since those fields (item_code/make/model_no/a photo) are already blank on genuine
# accessory items in this app's data most of the time anyway.
# "dimensions" joins these: like the photo, a product's measurements belong on the product's
# own row, not repeated down each of its accessory sub-rows.
#
# "s_no" is deliberately NOT here (2026-09-04). It was, which meant a driver or power supply
# hanging under a product reached the document with an empty S.No - nothing to refer to that
# line by when ordering it. Every row now carries its own number; the fields that stay are the
# ones that would otherwise repeat the parent product's identity down its own sub-rows.
BLOCK_FIRST_ROW_ONLY_KEYS = {"ref_no", "make", "model_no", "image", "ref_images", "dimensions"}

GRAND_TOTAL_FILL = PatternFill("solid", fgColor="FFFF00")
SUMMARY_HEADER_FILL = PatternFill("solid", fgColor="D9D9D9")

# The two columns a "Total" row under a table of item rows can meaningfully add up. The
# unit-price columns (Price, Discounted Price, Accessories Net Price) are deliberately left
# out: a column of per-unit rates has no total, and printing one invites it to be read as
# money owed.
TOTAL_ROW_KEYS = ("amount", "acc_amount")


def total_row_columns(keys, product_total, accessory_total):
    """(index -> value) for a totals row, keyed by position in `keys`.

    Either column can be hidden, so this is driven by what is actually on the table rather
    than by fixed positions. Shared by the per-floor tables of the full Quotation export and
    by the flat table of the summarised one."""
    totals = {"amount": product_total, "acc_amount": accessory_total}
    return {keys.index(k): totals[k] for k in TOTAL_ROW_KEYS if k in keys}


def visible_quotation_keys(hidden_columns, keys=None, boq=None, plan=None):
    """The quotation columns left after hiding some - the counterpart of
    excel_export._visible_columns, and the reason a quotation can now drop its photos or its
    AREA column the way the detailed BOQ always could.

    REF. IMAGES is dropped as well for a BOQ that has no reference pictures on it, so a
    quotation only grows the column once there is something to put in it - and, given the
    document's DiscountPlan, the discount columns it does not print."""
    hidden = set(hidden_columns or [])
    if not boq_has_ref_pictures(boq):
        hidden.add("ref_images")
    out = [k for k in (keys or QUOTATION_COLUMN_KEYS) if k not in hidden]
    return plan.keys(out) if plan is not None else out


def _safe_float(value, default=0.0) -> float:
    """qty is free text (BOQItem.qty is a String column) - defensively parse rather than
    let one unparseable value crash the whole export; an item that can't be priced
    contributes 0 to totals instead."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def line_discount(item, standard_percent):
    """The discount, in percent, one line is quoted at - the only place the rule lives.

    A line's own discount wins, 0 included. Otherwise a product gets the quotation's standard
    discount and an accessory, driver or power supply gets none: they are quoted at the price
    entered for them (decided 2026-09-29)."""
    own = getattr(item, "discount_percent", None)
    if own is not None:
        return float(own)
    if item.is_accessory:
        return 0.0
    return float(standard_percent or 0.0)


def follows_standard(item):
    """Whether a line's discount is the quotation's standard one, so a formula for it should
    read the Discount cell rather than a rate of its own."""
    return getattr(item, "discount_percent", None) is None and not item.is_accessory


def _line_totals(item, discount_percent):
    """(unit_price, discounted_unit_price, amount) for one item, at the discount
    line_discount() gives it. `discount_percent` is the quotation's standard discount."""
    qty = _safe_float(item.qty, default=0.0)
    unit_price = item.price or 0.0
    discounted_unit = unit_price * (1 - line_discount(item, discount_percent) / 100.0)
    amount = discounted_unit * qty
    return unit_price, discounted_unit, amount


class DiscountPlan:
    """How one document presents its discount - decided once, from all of its lines, so every
    sheet, page and total in it agrees.

    `shown`     - the discount appears at all, and the quotation's "Show the discount on the
                  quotation" setting is the only thing that decides it (2026-10-01, the owner's
                  call). Ticked, every line carries its DISC. % and its discounted price and the
                  document states its standard discount - at 0% as well, so a quotation's layout
                  never depends on what its rates happen to be. It used to need a discounted
                  product too; a quotation set to show its discount then printed none, and that
                  read as a fault. Unticked, the client sees final prices only.
    `own_rates` - some product carries a discount of its own, so the Discount cell is the
                  standard the other products follow, and says so in its label.
    `rate`      - what the Discount cell holds: the standard discount. A product on the standard
                  reads the cell; a line with its own rate holds that rate in its DISC. % cell.

    Hidden, the client sees final prices only: each line's net price where its price would be,
    and nothing about how it was reached - no column, row, rate, formula or note."""

    def __init__(self, boq, items):
        items = list(items)
        self.standard = float(boq.discount_percent or 0.0)
        self.shown = getattr(boq, "show_discount", True) is not False
        self.own_rates = any(not follows_standard(i) for i in items if not i.is_accessory)
        self.rate = self.standard

    @classmethod
    def for_boq(cls, boq):
        return cls(boq, (i for f in boq.floors for r in f.rooms for i in r.items))

    def keys(self, keys):
        """`keys` without the discount columns this document does not print: both when the
        discount is hidden, neither when it is shown."""
        if self.shown:
            return list(keys)
        return [k for k in keys if k not in ("discounted_price", "discount")]

    def cell_label(self):
        """The Discount cell's label: "Standard discount" once some product has its own."""
        return "Standard discount" if self.own_rates else "Discount"


def discount_text(rate):
    """A rate as printed: "15%", "12.5%", "0%" - never "15.00%". Only ever printed on a
    quotation that shows its discount, where a line at no discount says 0% rather than leaving
    its cell blank: a blank in a column of rates reads as a figure that was left out."""
    return "%g%%" % round(rate or 0.0, 2)


def discount_format(rate):
    """The Excel number format for a rate held as a fraction - whole percents without
    decimals, so the column reads "15%" rather than "15.00%"."""
    rate = round(float(rate or 0), 6)       # 0.15 * 100 is 15.000000000000002
    return "0%" if rate == int(rate) else "0.0#%"


def _totals_for_items(items, discount_percent):
    """(product_subtotal, accessory_subtotal) for a flat list of items - the actual per-item
    summation `_floor_totals` below applies per-room; extracted so a flat, no-floor-or-room
    item list (boq_summary.py's deduplicated Quotation Summary) can total itself the same
    way without duplicating this loop."""
    product_total = 0.0
    accessory_total = 0.0
    for item in items:
        _, _, amount = _line_totals(item, discount_percent)
        if item.is_accessory:
            accessory_total += amount
        else:
            product_total += amount
    return product_total, accessory_total


def _floor_totals(floor, discount_percent):
    """(product_subtotal, accessory_subtotal) for one floor - drives both the Summary
    sheet's per-floor rows and its Grand Total."""
    product_total = 0.0
    accessory_total = 0.0
    for room in floor.rooms:
        p, a = _totals_for_items(room.items, discount_percent)
        product_total += p
        accessory_total += a
    return product_total, accessory_total


# Deliberately plain ASCII prefixes, not currency symbol glyphs (₹, €, £...) - openpyxl
# number formats and reportlab's base-14 PDF fonts (Helvetica) can't reliably render
# those in every viewer/font environment, and a currency symbol failing to render shows
# up as an opaque "tofu" box instead of the amount, which is worse than a plain-text
# prefix. "$" is the one exception: it's plain ASCII (not a special Unicode codepoint)
# and universally recognized, so there's no reason to spell it out as "USD" instead.
CURRENCY_PREFIXES = {"INR": "Rs.", "USD": "$", "EUR": "EUR", "GBP": "GBP"}
DEFAULT_CURRENCY = "INR"


def _currency_format(currency: str) -> str:
    """A plain thousands-separated number. The currency is named once in the column heading
    (see money_heading) rather than on every amount - a quotation is in one currency
    throughout, and "Rs." repeated down five money columns of a fifty-line quotation is 250
    repetitions of a fact that does not change. It crowds the cell, and on a narrow column it
    is the part that survives while the digits wrap."""
    return "#,##0.00"


# ---------------------------------------------------------------- live formulas
#
# Every figure the app works out - a discounted price, a line amount, a sheet total, a floor's
# subtotal, the grand total - is written as a formula over the cells it comes from, not as the
# number the app got. A quotation is edited after it leaves the app: a price is bargained down,
# a driver is swapped for a cheaper one, the discount moves a point. With plain numbers every
# one of those edits left the totals underneath it wrong, and a total that silently disagrees
# with the lines above it is the worst mistake a quotation can carry.
#
# The discount is one cell, named "Discount", on the Summary sheet. Every discounted price in
# the workbook reads it, so changing the discount once re-prices the whole quotation.
#
# Only arithmetic and SUM are used, so the workbook calculates in Excel 2007 and everything
# since, as well as LibreOffice and Google Sheets.

DISCOUNT_NAME = "Discount"

# Money on an item line. The empty third section shows a zero as a blank cell: most lines are
# not priced yet (80 of 88 in the live data when this was written), and a formula over an
# empty price evaluates to 0 - without this, every unpriced line would read "0.00" down four
# columns, which a client reads as "free". Totals keep the plain format, where a zero is a
# real answer worth printing.
LINE_MONEY_FORMAT = "#,##0.00;-#,##0.00;"


def _number_text(value):
    """A number as it should appear inside a formula - no exponent, no trailing zeros."""
    value = float(value or 0)
    if value == int(value):
        return str(int(value))
    return format(value, ".10f").rstrip("0").rstrip(".")


def _qty_number(raw):
    """The quantity as a number when it is one, else None. qty is free text in the database;
    a number is written as a number so a formula can multiply by it, and anything else is
    left as the text it is (and priced at zero, exactly as _safe_float always has)."""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return int(value) if value == int(value) else value


class FormulaCache:
    """The value the app itself computed for each formula cell, so it can be stored alongside.

    openpyxl writes a formula with an empty cached result. Excel recalculates when it opens the
    file, so it never notices - but a viewer that does not calculate (a phone's attachment
    preview, the Windows preview pane) shows the cached result, and would show every total in
    the quotation as blank. Recording what the app computed and embedding it after saving
    (_embed_cached_values) means the file reads correctly everywhere, and still recalculates
    live the moment anyone edits it in Excel.
    """

    def __init__(self):
        self.values = {}

    def put(self, ws, row, col, formula, value, number_format=None):
        cell = ws.cell(row=row, column=col, value="=" + formula)
        if number_format:
            cell.number_format = number_format
        self.values[(ws.title, cell.coordinate)] = float(value or 0)
        return cell


def _embed_cached_values(path, cache):
    """Writes each formula's computed value into the saved file (see FormulaCache)."""
    if not cache or not cache.values:
        return
    path = Path(path)
    with zipfile.ZipFile(path) as zin:
        entries = {name: zin.read(name) for name in zin.namelist()}

    # sheet title -> its XML part, read from the workbook's own index rather than assumed from
    # sheet order: the Summary sheet is moved to the front after it is written.
    workbook_xml = entries["xl/workbook.xml"].decode("utf-8")
    rels_xml = entries["xl/_rels/workbook.xml.rels"].decode("utf-8")
    targets = dict(re.findall(r'<Relationship[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"', rels_xml))
    targets.update({rid: t for t, rid in re.findall(
        r'<Relationship[^>]*Target="([^"]+)"[^>]*Id="([^"]+)"', rels_xml)})
    part_of = {}
    for name, rid in re.findall(r'<sheet[^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', workbook_xml):
        target = targets.get(rid, "")
        part = target.lstrip("/") if target.startswith("/xl/") else "xl/" + target
        part_of[_xml_unescape(name)] = part

    by_part = {}
    for (title, coord), value in cache.values.items():
        part = part_of.get(title)
        if part:
            by_part.setdefault(part, {})[coord] = value

    for part, values in by_part.items():
        xml = entries[part].decode("utf-8")

        def fill(match):
            coord = match.group(2)
            if coord not in values:
                return match.group(0)
            return match.group(1) + "<v>" + repr(float(values[coord])) + "</v>"

        xml = re.sub(r'(<c r="([A-Z]+[0-9]+)"[^>]*><f>[^<]*</f>)<v\s*/?>(?:</v>)?', fill, xml)
        entries[part] = xml.encode("utf-8")

    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)
    tmp.replace(path)


def _xml_unescape(text):
    return (text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
                .replace("&apos;", "'").replace("&amp;", "&"))


def _sheet_ref(ws, coordinate):
    """A reference to a cell on another sheet, quoted so a floor called "Owner's Suite" works."""
    return quote_sheetname(ws.title) + "!" + coordinate


def _write_discount_cell(wb, ws, row, label_col, value_col, discount_percent, merge_to=None,
                         label_text="Discount"):
    """The one editable Discount cell, named so every sheet's formulas can read it. Written
    only when the document shows its discount (DiscountPlan.shown) - a hidden discount has no
    cell, and no formula reads one.

    Held as a fraction and formatted as a percentage, which is how Excel expects a rate: the
    cell reads "10.00%", and typing 15 into it gives 15%."""
    if merge_to and merge_to > label_col:
        ws.merge_cells(start_row=row, start_column=label_col, end_row=row, end_column=merge_to)
    label = ws.cell(row=row, column=label_col, value=label_text)
    label.font = FONT_HEADER
    label.alignment = CENTER_WRAP
    cell = ws.cell(row=row, column=value_col, value=(discount_percent or 0.0) / 100.0)
    cell.number_format = "0.00%"
    cell.font = Font(name="Calibri", size=11, bold=True)
    cell.alignment = CENTER_WRAP
    # A note rather than printed text: this is the client's document, and an instruction on
    # the page would be read by the client. Notes show on hover and do not print.
    cell.comment = Comment(
        "Every product line quoted at this discount is worked out from this rate. Change it "
        "here and those lines, every sheet total and the grand total update together. Lines "
        "with a discount of their own, and accessories and drivers, do not follow it.",
        "Quotation Tool")
    wb.defined_names[DISCOUNT_NAME] = DefinedName(
        DISCOUNT_NAME, attr_text=_sheet_ref(ws, "$%s$%d" % (get_column_letter(value_col), row)))
    return cell


# Which columns hold money, and so which headings carry the currency code.
MONEY_COLUMNS = ("price", "discounted_price", "amount", "acc_net_price", "acc_amount")


def currency_code(currency: str) -> str:
    """The code to print in a heading - INR, USD - not the symbol. A heading is read once and
    deliberately, and "INR" is unambiguous where "Rs." is shared by several currencies."""
    return (currency or DEFAULT_CURRENCY).strip().upper() or DEFAULT_CURRENCY


def money_heading(key: str, currency: str) -> str:
    """The column heading, with the currency appended when the column holds money."""
    label = QUOTATION_COLUMN_LABELS[key]
    return f"{label} ({currency_code(currency)})" if key in MONEY_COLUMNS else label


def summary_headings(plan, currency):
    """The Summary's two column headings. "Discounted Price" only on a quotation that shows its
    discount - on one quoting final prices, it would announce the very thing it is hiding."""
    code = currency_code(currency)
    if plan.shown:
        return f"Discounted Price ({code})", f"Accessories Net Price ({code})"
    return f"Amount ({code})", f"Amount of Accessories ({code})"


def _write_summary_sheet(wb, boq, floor_totals_by_id, floor_refs=None, cache=None, plan=None):
    """The Summary page. With `floor_refs` - each floor's total cells, as written on its own
    sheet - every figure here is a formula over those cells, so a price changed on a floor
    sheet reaches this page and its Grand Total without anyone re-adding anything."""
    floor_refs = floor_refs or {}
    cache = cache or FormulaCache()
    plan = plan or DiscountPlan.for_boq(boq)
    currency_format = _currency_format(boq.currency)
    ws = wb.create_sheet("Summary")
    # Set first, so the headings and figures below are fitted to them.
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 20
    ws.merge_cells("A1:D1")
    c = ws["A1"]
    c.value = "Summary"
    c.font = Font(name="Calibri", size=14, bold=True)
    c.alignment = CENTER
    for col in range(1, 5):
        ws.cell(row=1, column=col).border = BORDER_ALL
    ws.row_dimensions[1].height = 26

    header_row = 2
    code = currency_code(boq.currency)
    products_heading, accessories_heading = summary_headings(plan, boq.currency)
    ws.cell(row=header_row, column=3, value=products_heading).font = FONT_HEADER
    ws.cell(row=header_row, column=4, value=accessories_heading).font = FONT_HEADER
    for col in range(1, 5):
        cell = ws.cell(row=header_row, column=col)
        cell.fill = SUMMARY_HEADER_FILL
        cell.border = BORDER_ALL
        cell.alignment = CENTER_WRAP
    # Two lines at most: a four-column page has the width to spare, and a third line of
    # 13 pt heading would tower over the figures.
    fit_heading_row(ws, header_row, max_lines=2, minimum=22)

    start_row = header_row + 1
    row = start_row
    grand_total = 0.0
    for floor in boq.floors:
        product_total, accessory_total = floor_totals_by_id[floor.id]
        grand_total += product_total + accessory_total
        ws.cell(row=row, column=2, value=floor.name).font = FONT_BODY
        refs = floor_refs.get(floor.id) or {}
        for col, key, value in ((3, "amount", product_total), (4, "acc_amount", accessory_total)):
            # A floor whose table has no such total (the column hidden, or the floor empty)
            # has nothing to point at, so its figure is written as the number it is.
            if refs.get(key):
                cell = cache.put(ws, row, col, refs[key], value, currency_format)
            else:
                cell = ws.cell(row=row, column=col, value=value)
                cell.number_format = currency_format
            cell.font = FONT_BODY
        for col in range(1, 5):
            ws.cell(row=row, column=col).border = BORDER_ALL
            ws.cell(row=row, column=col).alignment = CENTER_WRAP
        row += 1
    end_row = row - 1

    if boq.floors:
        if end_row > start_row:
            ws.merge_cells(start_row=start_row, start_column=1, end_row=end_row, end_column=1)
        area_cell = ws.cell(row=start_row, column=1, value=boq.area or "")
        area_cell.font = FONT_HEADER
        area_cell.alignment = CENTER_WRAP

    total_row = row
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=2)
    label_cell = ws.cell(row=total_row, column=1, value=f"Grand Total ({code})")
    label_cell.font = FONT_HEADER
    label_cell.alignment = CENTER_WRAP
    ws.merge_cells(start_row=total_row, start_column=3, end_row=total_row, end_column=4)
    if boq.floors:
        total_cell = cache.put(ws, total_row, 3, "SUM(C%d:D%d)" % (start_row, end_row),
                               grand_total, currency_format)
    else:
        total_cell = ws.cell(row=total_row, column=3, value=grand_total)
        total_cell.number_format = currency_format
    total_cell.font = Font(name="Calibri", size=12, bold=True)
    total_cell.alignment = CENTER_WRAP
    for col in range(1, 5):
        ws.cell(row=total_row, column=col).border = BORDER_ALL
        ws.cell(row=total_row, column=col).fill = GRAND_TOTAL_FILL

    # The rate the product lines are worked out from - only on a quotation that shows it.
    if plan.shown:
        discount_row = total_row + 1
        _write_discount_cell(wb, ws, discount_row, 1, 3, plan.rate, merge_to=2,
                             label_text=plan.cell_label())
        ws.merge_cells(start_row=discount_row, start_column=3, end_row=discount_row, end_column=4)
        for col in range(1, 5):
            ws.cell(row=discount_row, column=col).border = BORDER_ALL

    fit_rows(ws, start_row, ws.max_row)
    return ws


def _write_quotation_header_block(ws, client_name, kind_attention):
    """The letterhead. The column widths must be set first: the names go in the first column
    clear of "Kind Attention:" (excel_export.write_addressees)."""
    ws["A1"] = "From:"
    ws["A1"].font = FONT_COMPANY_BOLD
    ws["A2"] = COMPANY_LINES[0]
    ws["A2"].font = FONT_COMPANY_BOLD
    for i, line in enumerate(COMPANY_LINES[1:], start=3):
        ws.cell(row=i, column=1, value=line).font = FONT_COMPANY
    write_addressees(ws, client_name, kind_attention)
    ws["A10"] = "Following your kind request, please find our best quotation for the required products:"
    ws["A10"].font = FONT_COMPANY
    for r in range(1, 11):
        ws.row_dimensions[r].height = 17.4


def _write_quotation_item_block_rows(ws, items, keys, currency_format, discount_percent, start_row, area_value=None, cache=None, plan=None):
    """Writes `_item_blocks(items)` as this sheet's rows (Price/Discounted Price/Amount or
    Accessories Net Price/Amount of Accessories per row, block-first-row-only S.No/REF NO./
    MAKE/Model No./photo, embedded product photos) - the per-item table body extracted out
    of `_write_quotation_floor_sheet`'s per-room loop so it's reusable by a flat, no-floor-
    or-room item table too (boq_summary.py's deduplicated Quotation Summary sheet).
    `area_value` fills the AREA column (if `keys` includes it) for every row written here -
    a room name for a per-floor sheet, left None (blank, and typically just left out of
    `keys` entirely) for the Summary sheet where per-room area no longer applies. Returns
    the row index directly after the last row written.

    `plan` is the document's DiscountPlan: whether the discount is shown, and whether each
    line carries its own rate. Without one, the discount is shown the way it always was."""
    if plan is None:
        plan = DiscountPlan(SimpleNamespace(discount_percent=discount_percent), items)
    # Derived from `keys`, not from QUOTATION_COLUMN_KEYS: with a column hidden, a fixed index
    # into the full list points at the wrong column, and an image would be anchored over
    # whatever now occupies that position.
    image_col = keys.index("image") + 1 if "image" in keys else None
    pics_col = keys.index("ref_images") + 1 if "ref_images" in keys else None
    dim_col = keys.index("dimensions") + 1 if "dimensions" in keys else None
    # column letter of every column actually on this sheet - a formula may only point at a
    # cell that exists, so a hidden column is replaced by its value written into the formula
    col_of = {key: get_column_letter(i) for i, key in enumerate(keys, start=1)}

    row = start_row
    for block in _item_blocks(items):
        is_accessory_block = block[0].is_accessory
        block_start_row = row
        for item_idx, item in enumerate(block):
            unit_price, discounted_unit, amount = _line_totals(item, discount_percent)
            is_first_row = item_idx == 0
            values = {
                # S.No is the same project-specific code as the plain BOQ export's own
                # "S. No" column (item.item_code) - REF NO. is the separate field for
                # this - previously item_code was (wrongly) reused for REF NO. too,
                # which meant a value typed into the item table's "S.No" field silently
                # ended up under a different column than the one labeled "S.No" here.
                "s_no": item.item_code or "",
                # Blank when the line drawing goes in this cell instead.
                "dimensions": "" if line_diagram_for(item) else (item.dimensions or ""),
                "ref_no": item.reference_number or "",
                "make": item.make or "",
                "model_no": item.model_no or "",
                "description": item.description or "",
                "led_color": item.led_color or "",
                "wattage": item.wattage or "",
                "finish": item.finish or "",
                "beam": item.beam or "",
                "control": item.control or "",
                "area": area_value or "",
                # a number when it is one, so the Amount beside it can multiply by it
                "qty": (_qty_number(item.qty) if _qty_number(item.qty) is not None
                        else (item.qty or "")),
                "unit": item.unit or "",
            }
            if not is_first_row:
                # Only a block's first row carries S.No/REF NO/MAKE/Model No. (and the
                # product photo, handled separately below) - matches the sample PDF's
                # own visual pattern for a product's accessory sub-rows.
                for key in BLOCK_FIRST_ROW_ONLY_KEYS:
                    values.pop(key, None)
            # The figures worked out on this line, as formulas over the cells they come from.
            # A reference where that cell is on the sheet; its value, written in, where the
            # column has been hidden - so hiding a column never leaves a formula pointing at
            # the wrong thing.
            def here(key):
                return "%s%d" % (col_of[key], row)

            qty_num = _qty_number(item.qty)
            qty_ref = (here("qty") if "qty" in col_of and qty_num is not None
                       else _number_text(qty_num or 0))
            rate = line_discount(item, discount_percent)
            own_rate = _number_text(rate / 100.0)
            # The rate this line's formulas read: its own DISC. % cell where that column is
            # printed; otherwise the Discount cell for a product on the standard rate, or the
            # line's own rate written in.
            if "discount" in col_of:
                rate_ref = here("discount")
            elif not is_accessory_block and follows_standard(item):
                rate_ref = DISCOUNT_NAME
            else:
                rate_ref = own_rate
            formulas = {}
            if plan.shown and "discount" in col_of:
                # Every line states its rate, 0% included. A product on the standard rate points
                # at the Discount cell, so changing that one cell still re-prices every such
                # line; any other line holds its own rate.
                if not is_accessory_block and follows_standard(item):
                    formulas["discount"] = (DISCOUNT_NAME, rate / 100.0, discount_format(rate))
                else:
                    values["discount"] = rate / 100.0
            if is_accessory_block:
                # An accessory has no list-price column of its own - the sheet shows only its
                # net price. Where the quotation shows its discount and there is a rate to read
                # (the line's DISC. % cell, or a rate of its own), the net price is a formula
                # over it, so editing that rate re-prices the line. Otherwise - final prices
                # only, or the DISC. % column hidden at 0% - it is written as the number it is,
                # so typing over it is the natural edit. An unpriced accessory is left blank, so
                # typing its price in is what is asked.
                if unit_price and plan.shown and ("discount" in col_of or rate):
                    net_expr = "%s*(1-%s)" % (_number_text(unit_price), rate_ref)
                    formulas["acc_net_price"] = (net_expr, discounted_unit)
                    net_ref = here("acc_net_price") if "acc_net_price" in col_of else "(%s)" % net_expr
                elif unit_price:
                    values["acc_net_price"] = discounted_unit
                    net_ref = (here("acc_net_price") if "acc_net_price" in col_of
                               else _number_text(discounted_unit))
                else:
                    net_ref = here("acc_net_price") if "acc_net_price" in col_of else "0"
                formulas["acc_amount"] = ("%s*%s" % (net_ref, qty_ref), amount)
            elif plan.shown:
                values["price"] = unit_price or None
                price_ref = here("price") if "price" in col_of else _number_text(unit_price)
                disc_expr = "%s*(1-%s)" % (price_ref, rate_ref)
                formulas["discounted_price"] = (disc_expr, discounted_unit)
                disc_ref = (here("discounted_price") if "discounted_price" in col_of
                            else "(%s)" % disc_expr)
                formulas["amount"] = ("%s*%s" % (disc_ref, qty_ref), amount)
            else:
                # Final prices only: the net price stands where the price would, as a plain
                # number. A formula such as "=1000*(1-0.1)" would show the client the list price
                # and the discount to anyone who clicked the cell.
                values["price"] = discounted_unit or None
                price_ref = here("price") if "price" in col_of else _number_text(discounted_unit)
                formulas["amount"] = ("%s*%s" % (price_ref, qty_ref), amount)

            for col, key in enumerate(keys, start=1):
                if key == "image":
                    continue
                if key in formulas:
                    formula, computed, *fmt = formulas[key]
                    fmt = fmt[0] if fmt else LINE_MONEY_FORMAT
                    if cache is not None:
                        c = cache.put(ws, row, col, formula, computed, fmt)
                    else:
                        c = ws.cell(row=row, column=col, value="=" + formula)
                        c.number_format = fmt
                    c.border = BORDER_ALL
                    c.font = FONT_QTY
                    c.alignment = CENTER_WRAP
                    continue
                val = values.get(key)
                if val is None:
                    c = ws.cell(row=row, column=col)
                    c.border = BORDER_ALL
                    continue
                c = ws.cell(row=row, column=col, value=val)
                c.border = BORDER_ALL
                if key in ("price", "discounted_price", "amount", "acc_net_price", "acc_amount"):
                    c.number_format = LINE_MONEY_FORMAT
                    c.font = FONT_QTY
                elif key == "discount":
                    c.number_format = discount_format(val * 100)
                    c.font = FONT_QTY
                elif key == "description":
                    c.font = FONT_BODY
                    c.alignment = LEFT_WRAP
                    continue
                else:
                    c.font = FONT_QTY if key in ("s_no", "qty") else FONT_BODY
                c.alignment = CENTER_WRAP
            if image_col:
                ws.cell(row=row, column=image_col).border = BORDER_ALL

            # Every cell's text at its own column's width. This was the description alone, at
            # the BOQ's line length - 53 characters, where the quotation's narrower column takes
            # about 34 - so most long descriptions lost their last lines.
            fit_rows(ws, row)
            row += 1
        block_end_row = row - 1

        first_item = block[0]
        is_product_block = not is_accessory_block and first_item.specsheet is not None

        # Photo first, then the line drawing: _place_diagram grows the block to fit whatever it
        # is given, so the drawing measures itself against rows the photo has already stretched.
        if image_col and is_product_block and first_item.specsheet.diagrams:
            _place_diagram(ws, first_item.specsheet.diagrams[0], image_col,
                           QUOTATION_COL_WIDTHS["image"], block_start_row, block_end_row, len(block))
        # The project's own reference pictures for this product, beside its photo.
        if pics_col:
            pictures = item_ref_pictures(first_item)
            if pictures:
                _place_pictures(ws, pictures, pics_col, QUOTATION_COL_WIDTHS["ref_images"],
                                block_start_row, block_end_row)
        if dim_col and is_product_block:
            drawing = line_diagram_for(first_item)
            if drawing:
                _place_diagram(ws, drawing, dim_col, QUOTATION_COL_WIDTHS["dimensions"],
                               block_start_row, block_end_row, len(block))
        if dim_col and is_accessory_block:
            # Each accessory line's own picture, in its own Dimension cell - as in the BOQ
            # (excel_export._write_item_block_rows).
            for offset, line in enumerate(block):
                drawing = line_diagram_for(line)
                if drawing:
                    r = block_start_row + offset
                    _place_diagram(ws, drawing, dim_col, QUOTATION_COL_WIDTHS["dimensions"], r, r, 1)
    return row


def _write_quotation_heading_row(ws, row, keys, currency):
    """The item table's column headings, each broken onto as few lines as fit its column -
    "Amount of / Accessories (INR)" - with the row as tall as the longest (excel_fit). Shared by
    the floor sheets and the Quotation Summary. The column widths must be set first."""
    for col, key in enumerate(keys, start=1):
        c = ws.cell(row=row, column=col, value=money_heading(key, currency))
        c.font = FONT_HEADER_COMPACT
        c.fill = PatternFill("solid", fgColor="C0C0C0")
        c.alignment = CENTER_WRAP
        c.border = BORDER_ALL
    fit_heading_row(ws, row, minimum=21)


def fit_figures(wb, cache=None):
    """Widens, on every sheet, any column whose figures would otherwise show as "#####" - a
    floor's total, set in the 13 pt heading font, was doing exactly that on the larger
    projects. Run last, once every figure is written."""
    for ws in wb.worksheets:
        fit_number_columns(ws, cache.values if cache is not None else None)


def _write_quotation_floor_sheet(wb, floor, client_name, kind_attention, discount_percent, currency, keys=None, cache=None, area_banners=False, plan=None):
    """One item table per floor/section.

    The standard layout is one continuous table with AREA as its own column - the sample
    Quotation PDF's structure. With `area_banners`, the same table is laid out the way the
    detailed BOQ is instead: the floor's name as a banner under the column headings, then each
    area as a banner of its own with its items beneath it, and the area's remark under those.
    The caller leaves "area" out of `keys` for that layout, since the banner says it once for
    every row beneath it. Everything else - the item rows, every formula, the totals, the
    Summary sheet they feed - is this same code, so the two quotations cannot disagree."""
    keys = keys or QUOTATION_COLUMN_KEYS
    currency_format = _currency_format(currency)
    sheet_name = (floor.name or "Floor")[:31]
    ws = wb.create_sheet(sheet_name)
    for col, key in enumerate(keys, start=1):
        ws.column_dimensions[get_column_letter(col)].width = QUOTATION_COL_WIDTHS[key]
    _write_quotation_header_block(ws, client_name, kind_attention)

    row = 12
    _write_quotation_heading_row(ws, row, keys, currency)
    row += 1

    n_cols = len(keys)
    if area_banners:
        write_banner_row(ws, row, n_cols, (floor.name or "").upper(), FONT_FLOOR, FLOOR_FILL, 24)
        row += 1

    # The totals below SUM from here to the last row written. Banner rows inside that range
    # hold nothing in the money columns (a merged range keeps its text in the first cell only),
    # so they add nothing - one formula serves both layouts.
    first_item_row = row
    for room in floor.rooms:
        if area_banners:
            write_banner_row(ws, row, n_cols, room.name or "", FONT_ROOM, ROOM_FILL, 24)
            row += 1
        row = _write_quotation_item_block_rows(ws, room.items, keys, currency_format,
                                               discount_percent, row,
                                               area_value=None if area_banners else room.name,
                                               cache=cache, plan=plan)
        if area_banners and room.remark:
            write_banner_row(ws, row, n_cols, f"Remark: {room.remark}", FONT_REMARK,
                             REMARK_FILL, 20)
            row += 1

    # This floor's own totals, directly under its last row, so the figures are where a reader
    # of the sheet finds them and not only on the Summary sheet at the front. Asked of the
    # items, not of the rows written: in the banner layout a floor whose areas are all empty
    # still writes their banners, and a Total of nothing under them would say "0.00" where the
    # standard layout rightly says nothing.
    has_items = any(room.items for room in floor.rooms)
    product_total, accessory_total = _floor_totals(floor, discount_percent)
    total_cols = total_row_columns(keys, product_total, accessory_total)
    total_refs = {}           # "amount" / "acc_amount" -> where this sheet's total of it is
    if total_cols and has_items and row > first_item_row:
        first = min(total_cols)
        if first > 0:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=first)
            label = ws.cell(row=row, column=1, value=f"Total ({currency_code(currency)})")
            label.font = FONT_HEADER
            label.alignment = CENTER_WRAP
        for idx, value in total_cols.items():
            letter = get_column_letter(idx + 1)
            formula = "SUM(%s%d:%s%d)" % (letter, first_item_row, letter, row - 1)
            if cache is not None:
                cell = cache.put(ws, row, idx + 1, formula, value, currency_format)
            else:
                cell = ws.cell(row=row, column=idx + 1, value="=" + formula)
                cell.number_format = currency_format
            cell.font = FONT_HEADER
            cell.alignment = CENTER_WRAP
            total_refs[keys[idx]] = _sheet_ref(ws, "%s%d" % (letter, row))
        for col in range(1, len(keys) + 1):
            ws.cell(row=row, column=col).border = BORDER_ALL
            ws.cell(row=row, column=col).fill = SUMMARY_HEADER_FILL
        # The 13 pt total needs more than Excel's default row.
        fit_rows(ws, row, minimum=24)
    return ws, total_refs


def export_quotation(boq, out_path: Path, hidden_columns=None, area_banners=False) -> Path:
    """Cover -> Summary -> one item sheet per floor -> terms/NOTE page, matching the
    sample Quotation PDF's own page order (SampleInputs/SampleQuotation). Cover and NOTE
    are the exact same writers excel_export.py's BOQ export uses - see that module for
    why (shared letterhead/logo/terms text, can't drift between formats).

    `area_banners`: the "detailed, area as a subheader" quotation - each floor sheet laid out
    the way the detailed BOQ is (see _write_quotation_floor_sheet), with no AREA column."""
    plan = DiscountPlan.for_boq(boq)
    keys = visible_quotation_keys(hidden_columns, boq=boq, plan=plan)
    if area_banners:
        keys = [k for k in keys if k != "area"]
    _autofill_sno(boq)
    wb = Workbook()
    wb.remove(wb.active)
    _write_cover_sheet(wb, boq)
    cache = FormulaCache()

    # The floor sheets are written before the Summary, because the Summary's figures are
    # formulas pointing at each floor's total and those cells have to exist to be named. The
    # Summary is then moved back to second place, so the document still reads Cover, Summary,
    # floors, terms - the sample Quotation's own page order.
    floor_totals_by_id = {floor.id: _floor_totals(floor, boq.discount_percent) for floor in boq.floors}
    floor_refs = {}
    for floor in boq.floors:
        _, refs = _write_quotation_floor_sheet(wb, floor, boq.client, boq.kind_attention,
                                               boq.discount_percent, boq.currency, keys, cache,
                                               area_banners=area_banners, plan=plan)
        floor_refs[floor.id] = refs

    summary = _write_summary_sheet(wb, boq, floor_totals_by_id, floor_refs, cache, plan=plan)
    wb.move_sheet(summary, offset=1 - wb.sheetnames.index(summary.title))

    _write_note_sheet(wb, boq.client, boq.kind_attention, boq.notes_text)
    fit_figures(wb, cache)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    _embed_cached_values(out_path, cache)
    return out_path
