"""The quick BOQ's own two documents - built to the studio's consolidated sample
(SampleInputs/Consolidted Quotation Sample.xlsx).

A quick BOQ says what goes into the project and where, without dividing quantities between
areas: one row per product, its total quantity, the areas it goes into, and the reference code
the drawing uses. So the table here is flat - no floor banners, no room banners - and reads
across instead: REF. CODE, Area, REF. IMAGES, MAKE, IMAGE, DESCRIPTION, the specification
columns, Qty, UNIT, then the money.

Two documents come out of it, the same table either way:

    export_consolidated_boq        - no money columns at all. Cover, the table, NOTE.
    export_consolidated_quotation  - with them, plus the Summary sheet. Cover, Summary,
                                     the table, NOTE.

Everything shared with the other exports is imported, never re-written here: the Cover sheet,
the NOTE page, the letterhead, the fonts and borders, the picture placement, and the formula
cache that lets a total read correctly in a previewer that does not calculate. What is genuinely
different is the shape of the table, which is this module.

Two things the layout does that the detailed exports do not:

  * A reference code groups several product rows. In the sample, DL1 covers the same fixture
    supplied by three makes across different areas, its code cell merged down the whole group -
    so the code is written once and each row under it is one supply.
  * Accessories and drivers are priced in their own pair of columns (ACCESSORIES NET PRICE /
    AMOUNT OF ACCESSORIES), under the product they hang from, whose area, pictures, make and
    photo merge down over them.
"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU, points_to_pixels

from .database import IMAGES_DIR
from .excel_export import (
    BORDER_ALL, CENTER, CENTER_WRAP, LEFT_WRAP, FONT_BODY, FONT_QTY, FONT_HEADER_COMPACT,
    FONT_COMPANY, FONT_COMPANY_BOLD, COMPANY_LINES, _place_diagram,
    IMAGE_CELL_PADDING_PX, PX_TO_PT, _col_width_to_px, write_addressees,
    _write_cover_sheet, _write_note_sheet, _ref_pictures, _place_pictures,
)
from .excel_fit import fit_heading_row, fit_rows
from .quotation_export import (
    FormulaCache, _embed_cached_values, _line_totals, _number_text, _qty_number,
    _safe_float, _write_discount_cell, DISCOUNT_NAME, LINE_MONEY_FORMAT, currency_code,
    DiscountPlan, line_discount, follows_standard, discount_format, fit_figures,
)

# The sample's own column order, read off the file rather than arranged by eye. DISC. % is
# the one addition, printed only when products are quoted at different discounts.
CONSOLIDATED_KEYS = [
    "ref_code", "area", "ref_images", "make", "image", "description", "control", "wattage",
    "led_color", "finish", "beam", "qty", "unit",
    "price", "discount", "amount", "acc_net_price", "acc_amount", "remarks",
]
# The money columns - the headings that carry the currency.
MONEY_KEYS = ("price", "amount", "acc_net_price", "acc_amount")
# Everything about pricing, which is the whole difference between the two documents.
PRICING_KEYS = MONEY_KEYS + ("discount",)

CONSOLIDATED_LABELS = {
    "ref_code": "REF. CODE", "area": "AREA", "ref_images": "REF. IMAGES", "make": "MAKE",
    "image": "IMAGE", "description": "DESCRIPTION", "control": "CONTROL", "wattage": "WATTAGE",
    "led_color": "CCT", "finish": "COLOUR FINISH", "beam": "BEAM ANGLE", "qty": "QTY",
    "unit": "UNIT", "price": "PRICE", "discount": "DISC. %", "amount": "AMOUNT",
    "acc_net_price": "ACCESSORIES NET PRICE", "acc_amount": "AMOUNT OF ACCESSORIES",
    "remarks": "REMARK",
}
CONSOLIDATED_WIDTHS = {
    "ref_code": 12.5, "area": 26.0, "ref_images": 18.0, "make": 14.0, "image": 18.0,
    "description": 46.0, "control": 13.0, "wattage": 11.0, "led_color": 10.0, "finish": 13.0,
    "beam": 12.0, "qty": 8.0, "unit": 8.0, "price": 12.0, "discount": 9.0, "amount": 14.0,
    "acc_net_price": 14.0, "acc_amount": 15.0, "remarks": 20.0,
}

# Which columns belong to the product and merge down over its accessory sub-rows, and which
# belong to the reference code and merge down over the whole group. Taken from the sample: its
# REF. CODE cell spans every row of DL1, while Area/REF. IMAGES/MAKE/IMAGE span one supply.
PRODUCT_SPAN_KEYS = ("area", "ref_images", "make", "image")

HEADER_FILL = PatternFill("solid", fgColor="D9D9D9")
TOTAL_FILL = PatternFill("solid", fgColor="FFFF00")
FIRST_TABLE_ROW = 13              # rows 1-11 are the letterhead, row 12 the column headings

CLOSING_NOTE = ("Kindly note that any changes in beam angle, finish, or product specification "
                "may lead to a change in price.")
TAX_NOTE = "GST as per actual\nFreight as per actual"


def _visible_keys(with_money, plan=None):
    """The table's columns: none of the pricing ones on the working BOQ, and on the quotation
    only the discount column its DiscountPlan prints."""
    if not with_money:
        return [k for k in CONSOLIDATED_KEYS if k not in PRICING_KEYS]
    return plan.keys(CONSOLIDATED_KEYS) if plan is not None else list(CONSOLIDATED_KEYS)


def discount_plan(boq):
    """The quick BOQ's DiscountPlan, over the rows the consolidated table prints."""
    return DiscountPlan(boq, (i for p, accs in quick_products(boq) for i in [p] + list(accs)))


def quick_products(boq):
    """Every product row of a quick BOQ, with the accessories hanging off each one.

    A quick BOQ keeps all its rows in one area behind the scenes (see BOQ.mode), so this reads
    them straight through in the order they were entered rather than walking floors and rooms.
    Returns [(product, [accessories])].
    """
    items = [i for f in boq.floors for r in f.rooms for i in r.items]
    items.sort(key=lambda i: (i.room_id, i.sort_order))
    children = {}
    for i in items:
        if i.parent_item_id:
            children.setdefault(i.parent_item_id, []).append(i)
    out = []
    for i in items:
        if i.parent_item_id:
            continue
        out.append((i, children.get(i.id, [])))
    return out


def group_by_ref_code(products):
    """The product rows gathered under their reference code, in the order the codes first
    appear. A row with no code stands on its own, so it is never silently merged into the
    group above it - two unrelated unnamed rows are not the same fixture."""
    groups, index = [], {}
    for product, accessories in products:
        code = (product.reference_number or "").strip()
        if code and code in index:
            groups[index[code]][1].append((product, accessories))
            continue
        if code:
            index[code] = len(groups)
        groups.append((code, [(product, accessories)]))
    return groups


def _area_names(item, areas_by_id):
    """The areas this product goes into, as the sample writes them: one line each, in the
    order they were marked."""
    names = [areas_by_id[a].name for a in (item.area_ids or []) if a in areas_by_id]
    return "\n".join(names)


def _write_letterhead(ws, boq, priced):
    """The same From/To block the Quotation's own sheets carry, plus the project's own line -
    a consolidated quotation is one sheet, so the project it is for belongs on it. The column
    widths must be set first (_write_heading_row): the names go in the first column clear of
    "Kind Attention:"."""
    ws["A1"] = "From:"
    ws["A1"].font = FONT_COMPANY_BOLD
    ws["A2"] = COMPANY_LINES[0]
    ws["A2"].font = FONT_COMPANY_BOLD
    for i, line in enumerate(COMPANY_LINES[1:], start=3):
        ws.cell(row=i, column=1, value=line).font = FONT_COMPANY
    write_addressees(ws, boq.client, boq.kind_attention)
    ws["A10"] = (("Following your kind request, please find our best quotation for the required "
                  "products:") if priced else
                 "Please find the consolidated list of products for the required areas:")
    ws["A10"].font = FONT_COMPANY
    for r in range(1, 11):
        ws.row_dimensions[r].height = 17.4


def _write_heading_row(ws, keys, currency):
    row = FIRST_TABLE_ROW - 1
    for col, key in enumerate(keys, start=1):
        label = CONSOLIDATED_LABELS[key]
        if key in MONEY_KEYS:
            label = "%s (%s)" % (label, currency_code(currency))
        c = ws.cell(row=row, column=col, value=label)
        c.font = FONT_HEADER_COMPACT
        c.fill = HEADER_FILL
        c.border = BORDER_ALL
        c.alignment = CENTER_WRAP
        ws.column_dimensions[get_column_letter(col)].width = CONSOLIDATED_WIDTHS[key]
    fit_heading_row(ws, row, minimum=21)
    # Everything above the table is the letterhead, so the headings are what stays in view.
    ws.freeze_panes = ws.cell(row=FIRST_TABLE_ROW, column=1)


def _merge_down(ws, col, start_row, end_row):
    if end_row > start_row:
        ws.merge_cells(start_row=start_row, start_column=col, end_row=end_row, end_column=col)


def _write_table(ws, boq, keys, cache, with_money, plan=None):
    """The rows themselves. Returns (row after the last one, the amount cells to total)."""
    col_of = {key: get_column_letter(i) for i, key in enumerate(keys, start=1)}
    index_of = {key: i for i, key in enumerate(keys, start=1)}
    areas_by_id = {a.id: a for a in boq.areas}
    images_by_id = {im.id: im for im in boq.ref_images}
    discount = boq.discount_percent or 0.0
    plan = plan or discount_plan(boq)

    row = FIRST_TABLE_ROW
    amount_cells, acc_amount_cells = [], []
    for code, supplies in group_by_ref_code(quick_products(boq)):
        group_start = row
        for product, accessories in supplies:
            product_start = row
            for n, item in enumerate([product] + list(accessories)):
                is_product = n == 0
                unit_price, discounted_unit, amount = _line_totals(item, discount)
                qty_num = _qty_number(item.qty)
                values = {
                    "ref_code": code if row == group_start else None,
                    "area": _area_names(product, areas_by_id) if is_product else None,
                    "make": (item.make or "") if is_product else None,
                    "description": item.description or "",
                    "control": item.control or "",
                    "wattage": item.wattage or "",
                    "led_color": item.led_color or "",
                    "finish": item.finish or "",
                    "beam": item.beam or "",
                    "qty": qty_num if qty_num is not None else (item.qty or ""),
                    "unit": item.unit or "",
                    "remarks": item.remarks or "",
                }

                formulas = {}
                if with_money:
                    qty_ref = (("%s%d" % (col_of["qty"], row)) if qty_num is not None
                               else _number_text(qty_num or 0))
                    rate = line_discount(item, discount)
                    # The rate this line's formulas read - see the same choice in
                    # quotation_export._write_quotation_item_block_rows.
                    if "discount" in col_of:
                        # Every line states its rate, 0% included (the column is only on a
                        # quotation that shows its discount).
                        rate_ref = "%s%d" % (col_of["discount"], row)
                        if not item.is_accessory and follows_standard(item):
                            formulas["discount"] = (DISCOUNT_NAME, rate / 100.0,
                                                    discount_format(rate))
                        else:
                            values["discount"] = rate / 100.0
                    elif not item.is_accessory and follows_standard(item):
                        rate_ref = DISCOUNT_NAME
                    else:
                        rate_ref = _number_text(rate / 100.0)
                    # An accessory is priced in its own pair of columns, a product in the
                    # other pair - the sample keeps the two apart all the way to the totals.
                    if item.is_accessory:
                        if unit_price:
                            if plan.shown and ("discount" in col_of or rate):
                                # A formula over the line's rate, so editing its DISC. % cell
                                # re-prices it - see quotation_export for the same choice.
                                net = "%s*(1-%s)" % (_number_text(unit_price), rate_ref)
                                formulas["acc_net_price"] = (net, discounted_unit)
                            else:
                                # Final prices only: its price, as a number.
                                values["acc_net_price"] = discounted_unit
                            net_ref = "%s%d" % (col_of["acc_net_price"], row)
                            formulas["acc_amount"] = ("%s*%s" % (net_ref, qty_ref), amount)
                            acc_amount_cells.append("%s%d" % (col_of["acc_amount"], row))
                    elif plan.shown:
                        values["price"] = unit_price or None
                        if unit_price:
                            price_ref = "%s%d" % (col_of["price"], row)
                            formulas["amount"] = ("%s*%s*(1-%s)" % (price_ref, qty_ref,
                                                                    rate_ref), amount)
                            amount_cells.append("%s%d" % (col_of["amount"], row))
                    else:
                        # Final prices only: the net price, as a plain number, where the price
                        # would be - see quotation_export for why never a formula here.
                        values["price"] = discounted_unit or None
                        if unit_price:
                            price_ref = "%s%d" % (col_of["price"], row)
                            formulas["amount"] = ("%s*%s" % (price_ref, qty_ref), amount)
                            amount_cells.append("%s%d" % (col_of["amount"], row))

                for col, key in enumerate(keys, start=1):
                    if key in ("image", "ref_images"):
                        ws.cell(row=row, column=col).border = BORDER_ALL
                        continue
                    if key in formulas:
                        formula, computed, *fmt = formulas[key]
                        c = cache.put(ws, row, col, formula, computed,
                                      fmt[0] if fmt else LINE_MONEY_FORMAT)
                        c.border = BORDER_ALL
                        c.font = FONT_QTY
                        c.alignment = CENTER_WRAP
                        continue
                    value = values.get(key)
                    c = ws.cell(row=row, column=col, value=value)
                    c.border = BORDER_ALL
                    if value is None:
                        continue
                    if key in MONEY_KEYS:
                        c.number_format = LINE_MONEY_FORMAT
                        c.font = FONT_QTY
                        c.alignment = CENTER_WRAP
                    elif key == "discount":
                        c.number_format = discount_format(value * 100)
                        c.font = FONT_QTY
                        c.alignment = CENTER_WRAP
                    elif key in ("description", "remarks"):
                        c.font = FONT_BODY
                        c.alignment = LEFT_WRAP
                    else:
                        c.font = FONT_QTY if key == "qty" else FONT_BODY
                        c.alignment = CENTER_WRAP
                fit_rows(ws, row)
                row += 1

            product_end = row - 1
            # The product's own cells stretch down over its accessories, so an accessory
            # never reads as belonging to a different area or make than what it hangs from.
            for key in PRODUCT_SPAN_KEYS:
                if key in index_of:
                    _merge_down(ws, index_of[key], product_start, product_end)
            # Now merged, a product's list of areas is measured against all its rows together -
            # and before the pictures, which size themselves to the rows they are given.
            fit_rows(ws, product_start, product_end)
            if product.specsheet is not None and product.specsheet.diagrams:
                _place_diagram(ws, product.specsheet.diagrams[0], index_of["image"],
                               CONSOLIDATED_WIDTHS["image"], product_start, product_end,
                               product_end - product_start + 1)
            _place_pictures(ws, _ref_pictures(product, images_by_id), index_of["ref_images"],
                            CONSOLIDATED_WIDTHS["ref_images"], product_start, product_end)
        _merge_down(ws, index_of["ref_code"], group_start, row - 1)
    return row, amount_cells, acc_amount_cells


def _write_total_row(ws, keys, row, amount_cells, acc_amount_cells, cache, totals):
    """The foot of the table: the two money totals, the tax note between them, and the
    sentence about specification changes - as the sample closes its own sheet."""
    index_of = {key: i for i, key in enumerate(keys, start=1)}
    note_from = index_of["description"]
    note_to = index_of.get("amount", index_of["unit"]) - 1
    if note_to > note_from:
        ws.merge_cells(start_row=row, start_column=note_from, end_row=row, end_column=note_to)
    note = ws.cell(row=row, column=note_from, value=CLOSING_NOTE)
    note.font = FONT_BODY
    note.alignment = LEFT_WRAP

    for key, cells, total in (("amount", amount_cells, totals[0]),
                              ("acc_amount", acc_amount_cells, totals[1])):
        if key not in index_of:
            continue
        col = index_of[key]
        formula = ("SUM(%s)" % ",".join(cells)) if cells else "0"
        c = cache.put(ws, row, col, formula, total, LINE_MONEY_FORMAT)
        c.font = Font(name="Arial", size=10, bold=True)
        c.fill = TOTAL_FILL
        c.border = BORDER_ALL
        c.alignment = CENTER_WRAP
    if "acc_net_price" in index_of:
        c = ws.cell(row=row, column=index_of["acc_net_price"], value=TAX_NOTE)
        c.font = FONT_BODY
        c.alignment = LEFT_WRAP
        c.border = BORDER_ALL
    ws.row_dimensions[row].height = 30
    fit_rows(ws, row)
    return row + 1


def _write_summary_sheet(wb, boq, table_sheet, total_refs, cache, totals):
    """Scope, its two totals, and the grand total - the sample's own Summary page.

    One scope for now: a quick BOQ quotes one scope (its Area field, "Interior" in the
    sample). The figures are formulas pointing at the table's own totals, so editing a price
    on the table updates this page too.
    """
    ws = wb.create_sheet("Summary")
    # Set first, so the headings and figures below are fitted to them.
    for col, width in (("A", 21.7), ("B", 12.0), ("C", 29.1), ("D", 29.1)):
        ws.column_dimensions[col].width = width
    ws.merge_cells("A1:D1")
    title = ws["A1"]
    title.value = "Summary"
    title.font = Font(name="Calibri", size=14, bold=True)
    title.alignment = CENTER_WRAP

    for col, label in ((1, "Scope"), (3, "Price"), (4, "Amount Of Accessories")):
        c = ws.cell(row=2, column=col, value=label)
        c.font = FONT_HEADER_COMPACT
        c.fill = HEADER_FILL
        c.border = BORDER_ALL
        c.alignment = CENTER_WRAP
    fit_heading_row(ws, 2, max_lines=2)

    scope = ws.cell(row=3, column=1, value=(boq.area or "").strip() or boq.project_name)
    scope.font = FONT_BODY
    scope.alignment = LEFT_WRAP
    scope.border = BORDER_ALL
    ws.cell(row=3, column=2, value="-").alignment = CENTER_WRAP
    quoted = "'%s'!" % table_sheet.title if " " in table_sheet.title else "%s!" % table_sheet.title
    for col, ref, value in ((3, total_refs[0], totals[0]), (4, total_refs[1], totals[1])):
        if not ref:
            continue
        c = cache.put(ws, 3, col, "%s%s" % (quoted, ref), value, LINE_MONEY_FORMAT)
        c.font = FONT_QTY
        c.border = BORDER_ALL
        c.alignment = CENTER_WRAP

    grand = ws.cell(row=4, column=2, value="Grand Total")
    grand.font = Font(name="Calibri", size=11, bold=True)
    # One line, never wrapped: it all but fills its column, and a label this short reads worse
    # broken in two than running a few pixels into the empty cell beside it.
    grand.alignment = CENTER
    ws.merge_cells(start_row=4, start_column=3, end_row=4, end_column=4)
    c = cache.put(ws, 4, 3, "C3+D3", (totals[0] or 0) + (totals[1] or 0), LINE_MONEY_FORMAT)
    c.font = Font(name="Calibri", size=11, bold=True)
    c.fill = TOTAL_FILL
    c.border = BORDER_ALL
    c.alignment = CENTER_WRAP
    # A merged range draws each cell's own border, so the second cell needs one too, or the box
    # stops halfway along.
    ws.cell(row=4, column=4).border = BORDER_ALL

    for n, line in enumerate(("GST as per actual", "Freight as per actual"), start=6):
        ws.merge_cells(start_row=n, start_column=3, end_row=n, end_column=4)
        c = ws.cell(row=n, column=3, value=line)
        c.font = FONT_BODY
        c.alignment = CENTER_WRAP

    # The scope's name and the Grand Total row, which wrap or run larger than a default row.
    fit_rows(ws, 3, 4)
    return ws


def _totals_of(boq):
    """What the app itself works out for the two money columns - written alongside each
    formula so the file reads right in a previewer that does not calculate."""
    discount = boq.discount_percent or 0.0
    products = accessories = 0.0
    for product, accs in quick_products(boq):
        for item in [product] + list(accs):
            _, _, amount = _line_totals(item, discount)
            if item.is_accessory:
                accessories += _safe_float(amount)
            else:
                products += _safe_float(amount)
    return products, accessories


def _build(boq, out_path: Path, with_money: bool) -> Path:
    plan = discount_plan(boq)
    keys = _visible_keys(with_money, plan)
    wb = Workbook()
    wb.remove(wb.active)
    _write_cover_sheet(wb, boq)

    cache = FormulaCache()
    ws = wb.create_sheet("Q1" if with_money else "BOQ")
    # The headings first: they set the column widths the letterhead lines its names up against.
    _write_heading_row(ws, keys, boq.currency)
    _write_letterhead(ws, boq, with_money)
    totals = _totals_of(boq) if with_money else (0.0, 0.0)
    row, amount_cells, acc_cells = _write_table(ws, boq, keys, cache, with_money, plan)

    total_refs = (None, None)
    if with_money:
        index_of = {key: i for i, key in enumerate(keys, start=1)}
        total_row = row
        _write_total_row(ws, keys, total_row, amount_cells, acc_cells, cache, totals)
        total_refs = ("%s%d" % (get_column_letter(index_of["amount"]), total_row),
                      "%s%d" % (get_column_letter(index_of["acc_amount"]), total_row))
        # The one editable Discount cell the product lines are worked out from, below the table
        # where it cannot be mistaken for part of it - only on a quotation that shows it.
        if plan.shown:
            _write_discount_cell(wb, ws, total_row + 2, index_of["description"],
                                 index_of["qty"], plan.rate, label_text=plan.cell_label())
            fit_rows(ws, total_row + 2)
        summary = _write_summary_sheet(wb, boq, ws, total_refs, cache, totals)
        wb.move_sheet(summary, offset=1 - wb.sheetnames.index(summary.title))

    _write_note_sheet(wb, boq.client, boq.kind_attention, boq.notes_text)
    fit_figures(wb, cache)
    ws.print_area = "A1:%s%d" % (get_column_letter(len(keys)), row)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    _embed_cached_values(out_path, cache)
    return out_path


def export_consolidated_quotation(boq, out_path: Path) -> Path:
    """Cover, Summary, the priced table, NOTE."""
    return _build(boq, out_path, with_money=True)


def export_consolidated_boq(boq, out_path: Path) -> Path:
    """The same table without a single money column, for handing round internally."""
    return _build(boq, out_path, with_money=False)
