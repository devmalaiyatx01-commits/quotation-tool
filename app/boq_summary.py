"""Two "no floors, no rooms" exports - Export BOQ (Summary) and Export Quotation (Summary),
both in .xlsx and .pdf - for the case the studio actually gives a client/architect: the
same physical product used on the ground floor and the first floor should show up as ONE
line with its total quantity, not the same spec repeated once per room it was added in.

Reuses every column definition, style, and (critically) the intricate image-embedding math
from excel_export.py/pdf_export.py/quotation_export.py/quotation_pdf_export.py rather than
reimplementing any of it - those four modules were refactored alongside this one to each
expose a reusable "write these _item_blocks() as table rows starting here" function
(_write_item_block_rows, _append_item_block_rows, _write_quotation_item_block_rows,
_append_quotation_rows) that used to be inline in their own per-room loops. This module's
only genuinely new logic is the grouping in `summarized_items()` below.
"""
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak, Spacer

from .excel_export import (
    BORDER_ALL, CENTER_WRAP, FONT_HEADER,
    _visible_columns, _apply_col_widths, _write_header_block, _write_columns_row,
    _write_item_block_rows, _write_cover_sheet, _write_note_sheet,
)
from .excel_fit import fit_heading_row, fit_rows
from .pdf_export import (
    PAGE_SIZE as BOQ_PDF_PAGE_SIZE, MARGIN as BOQ_PDF_MARGIN,
    COLUMN_LABELS as PDF_COLUMN_LABELS, STYLE_HEADER_CELL, STYLE_FLOOR,
    GREY, BORDER, _visible_keys, _scaled_widths_mm, _append_item_block_rows,
    fit_header, fit_columns_mm, widest_token_pt, HEADING_READABLE_PT,
    _cover_flowables as _boq_cover_flowables, _notes_flowables,
)
from .quotation_export import (
    QUOTATION_COLUMN_KEYS, QUOTATION_COLUMN_LABELS, QUOTATION_COL_WIDTHS, GRAND_TOTAL_FILL,
    SUMMARY_HEADER_FILL, CENTER, FONT_BODY, total_row_columns,
    currency_code, fit_figures, _write_quotation_heading_row,
    _currency_format, _totals_for_items, _write_quotation_header_block, _write_quotation_item_block_rows,
    FormulaCache, _embed_cached_values, _write_discount_cell, _sheet_ref, _number_text,
    visible_quotation_keys, DiscountPlan, line_discount, discount_text, summary_headings,
)
from .quotation_pdf_export import (
    PAGE_SIZE as QUOTATION_PDF_PAGE_SIZE, MARGIN as QUOTATION_PDF_MARGIN,
    RAW_COL_WIDTHS_MM, USABLE_WIDTH_MM as QUOTATION_USABLE_WIDTH_MM, STYLE_QUOTE_HEADER_CELL, STYLE_LABEL,
    _money, _cover_flowables as _quotation_cover_flowables, _append_quotation_rows,
    pdf_quotation_heading, _widest_values_pt, FLEX_KEYS as QUOTATION_FLEX_KEYS,
)

# Every quotation column except AREA - a per-room location that stops meaning anything once
# rows from different rooms/floors are merged into a single deduplicated line.
# "area" goes because a summary exists to collapse the rooms away; "ref_images" goes with it,
# for the same reason one step further on - a summarised row stands for the same product in
# several rooms at once, so a picture chosen against one of those placements would be shown
# speaking for all of them.
SUMMARY_QUOTATION_COLUMN_KEYS = [k for k in QUOTATION_COLUMN_KEYS
                                 if k not in ("area", "ref_images")]


@dataclass
class SummaryItem:
    """A stand-in for BOQItem carrying the same attribute names the shared writers above
    already know how to read (VALUE_BY_KEY, _line_totals, the image lookup via
    `.specsheet.diagrams`, etc.) - so summarized_items()'s merged rows can be fed straight
    into every one of those reused functions with no further translation. `remarks` is
    deliberately not a field here at all (see summarized_items())."""
    item_code: str
    reference_number: str
    model_no: str
    description: str
    finish: str
    wattage: str
    beam: str
    control: str
    led_color: str
    make: str
    # Both carried through from the row the group was built from: identical-spec rows are what
    # a group IS (see _dedup_key), so they share a product and therefore its measurements and
    # its line drawing. Unlike remarks, which is a per-location note with nothing correct to
    # show once locations are merged, these two say the same thing on every row in the group.
    dimensions: str
    show_line_diagram: bool
    dimension_image: str
    qty: str
    unit: str
    price: float
    is_accessory: bool
    specsheet: object
    # The line's own discount, None where it follows the quotation's rule - carried so the
    # summary prices a merged line exactly as the rows it stands for were priced.
    discount_percent: object = None
    # Carried for an accessory line's own picture (excel_export.line_diagram_for reads it from
    # here), which identical-spec lines share like their measurements above.
    config: object = None


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedup_key(item, parent_key=None, standard_discount=0.0):
    """Two rows count as "the exact same product" only if every descriptive field, the
    unit, and the price all match, in addition to specsheet_id - deliberately strict (case-
    /whitespace-insensitive, but nothing fuzzier than that) rather than a fuzzy match, the
    same "never guess, only merge what's actually identical" discipline as
    typology_classifier.py. Price is part of the key on purpose: two rows with an otherwise
    identical spec but a different entered price are NOT silently merged into one (which
    price would even show?) - they stay as two separate summary lines instead.

    `parent_key` is what an accessory hangs from, and it is what stops an accessory line
    being merged across the products it belongs to. An accessory row carries no specsheet_id
    of its own - it is a free-text sub-row - so a "Honeycomb" under OZONE Dual Square and a
    "Honeycomb" under OZONE Single were identical in every field this looks at and collapsed
    into one line with their quantities added together. They are different parts, ordered
    separately, and a summary that adds them up cannot be ordered from.

    It is the parent's KEY, not its row id: the same accessory on the same product in two
    different rooms still merges, which is the entire point of a summary. Only the product
    identity separates them, never the location.

    The discount a line is quoted at is part of it for the same reason price is: two rows at
    different discounts are two prices, and one merged line could only show one of them.
    """
    def norm(s):
        return (s or "").strip().lower()
    return (
        item.specsheet_id,
        norm(item.reference_number), norm(item.model_no), norm(item.description),
        norm(item.finish), norm(item.wattage), norm(item.beam), norm(item.control),
        norm(item.led_color), norm(item.make), norm(item.unit),
        round(item.price or 0.0, 2), bool(item.is_accessory),
        round(line_discount(item, standard_discount), 4),
        parent_key,
        # A row showing a chosen picture in its Dimension column - a particular profile - is not
        # the same line as one showing a different picture, or the plain line drawing. Only a
        # shown, chosen picture counts, so rows that group together today still do.
        norm(getattr(item, "dimension_image", "")) if getattr(item, "show_line_diagram", False) else "",
    )


def _format_qty_total(total: float) -> str:
    if total == int(total):
        return str(int(total))
    return f"{total:g}"


def summarized_items(boq):
    """Flattens every floor/room's items into one deduplicated list: identical-spec rows
    (see `_dedup_key`) collapse into a single SummaryItem whose qty is the sum of every
    matched row's qty, in first-seen order across the BOQ's floors/rooms. `remarks` is
    dropped entirely rather than merged or guessed at - it's inherently a per-location note
    ("near main door") that doesn't mean anything once rows from different rooms/floors are
    combined into one line, so there's nothing correct to show there.

    qty is free text (BOQItem.qty is a String column) - an unparseable value contributes 0
    to its group's total, the same defensive-default convention quotation_export.py's own
    _line_totals already uses for pricing, rather than this being new/different behavior."""
    # One pass to index the rows by id, so an accessory can be told which product it hangs
    # from without a query per row - the BOQ is already fully loaded here, and this keeps the
    # whole grouping linear in the number of items.
    by_id = {}
    for floor in boq.floors:
        for room in floor.rooms:
            for item in room.items:
                by_id[item.id] = item

    parent_keys = {}
    standard = boq.discount_percent or 0.0

    def key_for(item, depth=0):
        parent = by_id.get(item.parent_item_id) if item.parent_item_id else None
        # depth guards against a cycle in parent_item_id, which the schema permits even
        # though nothing creates one; without it a bad row would recurse forever.
        if parent is None or depth > 4:
            return _dedup_key(item, standard_discount=standard)
        if parent.id not in parent_keys:
            parent_keys[parent.id] = key_for(parent, depth + 1)
        return _dedup_key(item, parent_keys[parent.id], standard_discount=standard)

    groups = {}
    order = []
    for floor in boq.floors:
        for room in floor.rooms:
            for item in room.items:
                key = key_for(item)
                if key not in groups:
                    groups[key] = {"qty_total": 0.0, "source": item}
                    order.append(key)
                groups[key]["qty_total"] += _safe_float(item.qty)

    result = []
    for idx, key in enumerate(order, start=1):
        g = groups[key]
        item = g["source"]
        result.append(SummaryItem(
            item_code=str(idx),
            reference_number=item.reference_number or "",
            model_no=item.model_no or "",
            description=item.description or "",
            finish=item.finish or "",
            wattage=item.wattage or "",
            beam=item.beam or "",
            control=item.control or "",
            led_color=item.led_color or "",
            make=item.make or "",
            dimensions=getattr(item, "dimensions", "") or "",
            show_line_diagram=bool(getattr(item, "show_line_diagram", False)),
            dimension_image=getattr(item, "dimension_image", "") or "",
            qty=_format_qty_total(g["qty_total"]),
            unit=item.unit or "",
            price=item.price or 0.0,
            is_accessory=item.is_accessory,
            specsheet=item.specsheet,
            discount_percent=getattr(item, "discount_percent", None),
            config=dict(getattr(item, "config", None) or {}),
        ))
    return result


# ---------------- BOQ Summary (.xlsx / .pdf) - plain product list, no pricing ----------------

def export_boq_summary(boq, out_path: Path, hidden_columns=None) -> Path:
    """Cover -> one flat "BOQ Summary" item sheet (every distinct product spec once, total
    qty across the whole project, no Remarks column) -> NOTE page."""
    # "remarks" is always out of a summary (a per-room note means nothing once rooms are
    # collapsed away); anything else the user chose to drop joins it.
    key_to_col = _visible_columns({"remarks", "ref_images"} | set(hidden_columns or []))
    wb = Workbook()
    wb.remove(wb.active)
    _write_cover_sheet(wb, boq)
    ws = wb.create_sheet("BOQ Summary")
    _apply_col_widths(ws, key_to_col)
    _write_header_block(ws, boq.client, boq.kind_attention)
    row = 12
    row = _write_columns_row(ws, row, key_to_col)
    _write_item_block_rows(ws, summarized_items(boq), key_to_col, row)
    _write_note_sheet(wb, boq.client, boq.kind_attention, boq.notes_text)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def export_boq_summary_pdf(boq, out_path: Path, hidden_columns=None) -> Path:
    visible_keys = _visible_keys({"remarks"} | set(hidden_columns or []))
    image_idx = visible_keys.index("image") if "image" in visible_keys else None
    s_no_idx = visible_keys.index("s_no") if "s_no" in visible_keys else None

    widths_mm = dict(zip(visible_keys, _scaled_widths_mm(visible_keys)))
    table_data = [[fit_header(PDF_COLUMN_LABELS[k], widths_mm[k]) for k in visible_keys]]
    span_commands = []
    _append_item_block_rows(table_data, span_commands, summarized_items(boq), visible_keys, 0, s_no_idx, image_idx, widths_mm)

    col_widths = [widths_mm[k] * mm for k in visible_keys]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ] + span_commands))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=BOQ_PDF_PAGE_SIZE,
        leftMargin=BOQ_PDF_MARGIN, rightMargin=BOQ_PDF_MARGIN, topMargin=BOQ_PDF_MARGIN, bottomMargin=BOQ_PDF_MARGIN,
        title=f"BOQ Summary - {boq.project_name}",
    )
    story = _boq_cover_flowables(boq)
    story.append(Paragraph("Product Summary", STYLE_FLOOR))
    story.append(table)
    story.append(PageBreak())
    story.extend(_notes_flowables(boq))
    doc.build(story)
    return out_path


# ---------------- Quotation Summary (.xlsx / .pdf) - priced, no pricing ----------------

# Bold enough to read as a total, but set at the item rows' own price-cell size: the amount
# columns are narrow, and STYLE_LABEL's 10pt wraps "12,345.00" onto two lines.
STYLE_TOTAL_CELL = ParagraphStyle(
    "summary_total_cell", fontName="Helvetica-Bold", fontSize=7.5,
    alignment=TA_CENTER, leading=9,
)
# The Grand Total keeps STYLE_LABEL's 10pt, and is given as many of the last columns as it needs
# instead (see export_quotation_summary_pdf), so it ends at the table's right edge.
STYLE_GRAND_TOTAL_AMOUNT = ParagraphStyle(
    "summary_grand_total_amount", parent=STYLE_LABEL, alignment=TA_RIGHT,
)


def _summary_widths_mm(boq, keys, items, plan, product_total, accessory_total):
    """The summarised table's column widths, fitted the way the detailed quotation's are (see
    pdf_export.fit_columns_mm): the designed widths scaled to the page, then any column too
    narrow for what it prints widened at the expense of columns with room to spare. A table
    whose values all fit keeps exactly the scaled widths.

    Measured on the summarised lines - one line adds up a product's quantity across every room,
    so its amount runs wider than any single room's - and on the Total row's bold figures.
    Scaled only, six-figure amounts wrapped ("120,750.0 / 0"), and more often once DISC. % took
    its share of the page (2026-10-01)."""
    needed = _widest_values_pt(boq, keys, items=items, plan=plan)
    for idx, value in total_row_columns(keys, product_total, accessory_total).items():
        needed[keys[idx]] = max(needed.get(keys[idx], 0.0), widest_token_pt(
            _money(value), STYLE_TOTAL_CELL.fontName, STYLE_TOTAL_CELL.fontSize))
    heads = {k: widest_token_pt(pdf_quotation_heading(k), "Helvetica-Bold", HEADING_READABLE_PT)
             for k in keys}
    return fit_columns_mm(keys, RAW_COL_WIDTHS_MM, needed, QUOTATION_USABLE_WIDTH_MM,
                          QUOTATION_FLEX_KEYS, heading_pt=heads)


def _write_summary_totals_sheet(wb, boq, product_total, accessory_total, refs=None, cache=None, plan=None):
    """The Summary page, in the shape the full Quotation export's own Summary sheet uses.

    That export breaks the figures down per floor; a summary has deliberately thrown floors
    away, so the breakdown here is the one distinction the numbers still carry - products
    against accessories - followed by the same Grand Total row."""
    refs = refs or {}
    cache = cache or FormulaCache()
    plan = plan or DiscountPlan.for_boq(boq)
    currency_format = _currency_format(boq.currency)
    code = currency_code(boq.currency)
    ws = wb.create_sheet("Summary")
    # Set first, so the headings and figures below are fitted to them.
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 26

    ws.merge_cells("A1:C1")
    title = ws["A1"]
    title.value = "Summary"
    title.font = Font(name="Calibri", size=14, bold=True)
    title.alignment = CENTER
    for col in range(1, 4):
        ws.cell(row=1, column=col).border = BORDER_ALL
    ws.row_dimensions[1].height = 26

    products_heading, accessories_heading = summary_headings(plan, boq.currency)
    ws.cell(row=2, column=2, value=products_heading).font = FONT_HEADER
    ws.cell(row=2, column=3, value=accessories_heading).font = FONT_HEADER
    for col in range(1, 4):
        cell = ws.cell(row=2, column=col)
        cell.fill = SUMMARY_HEADER_FILL
        cell.border = BORDER_ALL
        cell.alignment = CENTER_WRAP
    # Two lines at most - see quotation_export._write_summary_sheet.
    fit_heading_row(ws, 2, max_lines=2, minimum=22)

    ws.cell(row=3, column=1, value=boq.area or "Product Summary").font = FONT_HEADER
    # Pointing at the item table's own totals, so a price edited there arrives here.
    for col, key, value in ((2, "amount", product_total), (3, "acc_amount", accessory_total)):
        if refs.get(key):
            cell = cache.put(ws, 3, col, refs[key], value, currency_format)
        else:
            cell = ws.cell(row=3, column=col, value=value)
            cell.number_format = currency_format
        cell.font = FONT_BODY
    for col in range(1, 4):
        ws.cell(row=3, column=col).border = BORDER_ALL
        ws.cell(row=3, column=col).alignment = CENTER_WRAP

    ws.cell(row=4, column=1, value=f"Grand Total ({code})").font = FONT_HEADER
    ws.cell(row=4, column=1).alignment = CENTER_WRAP
    ws.merge_cells(start_row=4, start_column=2, end_row=4, end_column=3)
    grand = cache.put(ws, 4, 2, "B3+C3", product_total + accessory_total, currency_format)
    grand.font = Font(name="Calibri", size=12, bold=True)
    grand.alignment = CENTER_WRAP
    for col in range(1, 4):
        ws.cell(row=4, column=col).border = BORDER_ALL
        ws.cell(row=4, column=col).fill = GRAND_TOTAL_FILL

    # The rate the product lines are worked out from - only on a quotation that shows it.
    if plan.shown:
        _write_discount_cell(wb, ws, 5, 1, 2, plan.rate, label_text=plan.cell_label())
        ws.merge_cells(start_row=5, start_column=2, end_row=5, end_column=3)
        for col in range(1, 4):
            ws.cell(row=5, column=col).border = BORDER_ALL

    fit_rows(ws, 3, ws.max_row)
    return ws


def export_quotation_summary(boq, out_path: Path, hidden_columns=None) -> Path:
    """Cover -> one flat "Quotation Summary" item sheet (every distinct product spec once,
    total qty, Price/Discounted Price/Amount or Accessories Net Price/Amount of Accessories
    per row, no AREA column, one Grand Total row at the bottom instead of the full
    Quotation export's separate per-floor Summary page) -> NOTE page."""
    items = summarized_items(boq)
    plan = DiscountPlan(boq, items)
    keys = visible_quotation_keys(hidden_columns, SUMMARY_QUOTATION_COLUMN_KEYS, plan=plan)
    currency_format = _currency_format(boq.currency)
    product_total, accessory_total = _totals_for_items(items, boq.discount_percent)
    wb = Workbook()
    wb.remove(wb.active)
    _write_cover_sheet(wb, boq)
    cache = FormulaCache()
    # The item table is written before the Summary page - the Summary's figures point at this
    # table's totals, so those cells must exist first - and the Summary is moved back to second
    # place once written. See export_quotation for the same order.
    ws = wb.create_sheet("Quotation Summary")
    for col, key in enumerate(keys, start=1):
        ws.column_dimensions[get_column_letter(col)].width = QUOTATION_COL_WIDTHS[key]
    _write_quotation_header_block(ws, boq.client, boq.kind_attention)

    row = 12
    _write_quotation_heading_row(ws, row, keys, boq.currency)
    row += 1

    first_item_row = row
    row = _write_quotation_item_block_rows(ws, items, keys, currency_format, boq.discount_percent,
                                           row, cache=cache, plan=plan)
    last_item_row = row - 1

    num_cols = len(keys)
    total_refs = {}          # "amount" / "acc_amount" -> the cell holding that column's total

    # A "Total" row directly under the last item, so the two amount columns are added up where
    # someone reading the table finds them - not only on the Summary page at the front.
    total_cols = total_row_columns(keys, product_total, accessory_total)
    if total_cols:
        first = min(total_cols)
        if first > 0:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=first)
            label = ws.cell(row=row, column=1, value="Total")
            label.font = FONT_HEADER
            label.alignment = CENTER_WRAP
        for idx, value in total_cols.items():
            letter = get_column_letter(idx + 1)
            if last_item_row >= first_item_row:
                cell = cache.put(ws, row, idx + 1,
                                 "SUM(%s%d:%s%d)" % (letter, first_item_row, letter, last_item_row),
                                 value, currency_format)
                total_refs[keys[idx]] = "%s%d" % (letter, row)
            else:
                cell = ws.cell(row=row, column=idx + 1, value=value)
                cell.number_format = currency_format
            cell.font = FONT_HEADER
            cell.alignment = CENTER_WRAP
        for col in range(1, num_cols + 1):
            ws.cell(row=row, column=col).border = BORDER_ALL
            ws.cell(row=row, column=col).fill = SUMMARY_HEADER_FILL
        row += 1

    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=num_cols - 1)
    label_cell = ws.cell(row=row, column=1, value=f"Grand Total ({currency_code(boq.currency)})")
    label_cell.font = Font(name="Calibri", size=12, bold=True)
    label_cell.alignment = CENTER_WRAP
    # The two totals above, added. A total whose column is hidden has no cell to point at, so
    # its figure is written into the formula instead - it must still be counted. Referencing
    # only the totals that happened to be visible dropped the products entirely when Amount
    # was hidden, leaving a Grand Total equal to the accessories alone.
    parts = []
    for key, value in (("amount", product_total), ("acc_amount", accessory_total)):
        if total_refs.get(key):
            parts.append(total_refs[key])
        elif value:
            parts.append(_number_text(value))
    if any(total_refs.get(k) for k in ("amount", "acc_amount")):
        total_cell = cache.put(ws, row, num_cols, "+".join(parts) or "0",
                               product_total + accessory_total, currency_format)
    else:
        total_cell = ws.cell(row=row, column=num_cols, value=product_total + accessory_total)
        total_cell.number_format = currency_format
    total_cell.font = Font(name="Calibri", size=12, bold=True)
    total_cell.alignment = CENTER_WRAP
    for col in range(1, num_cols + 1):
        ws.cell(row=row, column=col).border = BORDER_ALL
        ws.cell(row=row, column=col).fill = GRAND_TOTAL_FILL
    # The Total and Grand Total rows, in their larger fonts.
    fit_rows(ws, last_item_row + 1, row, minimum=24)

    summary = _write_summary_totals_sheet(
        wb, boq, product_total, accessory_total,
        refs={k: _sheet_ref(ws, v) for k, v in total_refs.items()}, cache=cache, plan=plan)
    wb.move_sheet(summary, offset=1 - wb.sheetnames.index(summary.title))

    _write_note_sheet(wb, boq.client, boq.kind_attention, boq.notes_text)
    fit_figures(wb, cache)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    _embed_cached_values(out_path, cache)
    return out_path


def export_quotation_summary_pdf(boq, out_path: Path, hidden_columns=None) -> Path:
    items = summarized_items(boq)
    plan = DiscountPlan(boq, items)
    keys = visible_quotation_keys(hidden_columns, SUMMARY_QUOTATION_COLUMN_KEYS, plan=plan)
    product_total, accessory_total = _totals_for_items(items, boq.discount_percent)

    widths_mm = dict(zip(keys, _summary_widths_mm(boq, keys, items, plan,
                                                   product_total, accessory_total)))
    table_data = [[fit_header(pdf_quotation_heading(k), widths_mm[k], base_size=6.5) for k in keys]]
    _append_quotation_rows(table_data, items, keys, boq.discount_percent, boq.currency,
                           widths_mm=widths_mm, plan=plan)

    n_cols = len(keys)
    extra_styles = []

    # A "Total" row directly under the last item, adding up the two amount columns where a
    # reader of the table finds them rather than only on the Summary page at the front.
    total_cols = total_row_columns(keys, product_total, accessory_total)
    if total_cols:
        first = min(total_cols)
        row_cells = [""] * n_cols
        for idx, value in total_cols.items():
            row_cells[idx] = Paragraph(_money(value), STYLE_TOTAL_CELL)
        totals_idx = len(table_data)
        if first > 0:
            # As with Grand Total below, the label must sit in the span's top-left cell -
            # reportlab draws only that one and silently drops anything else in the region.
            row_cells[0] = Paragraph("Total", STYLE_LABEL)
            extra_styles.append(("SPAN", (0, totals_idx), (first - 1, totals_idx)))
        extra_styles.append(
            ("BACKGROUND", (0, totals_idx), (-1, totals_idx), colors.HexColor("#e6e6e6")))
        table_data.append(row_cells)

    col_widths = [widths_mm[k] * mm for k in keys]

    # The amount takes as many of the last columns as it needs. Alone in the last one, a narrow
    # money column, a 10pt total such as "98,765.00" wrapped onto two lines ("98,765.0 / 0")
    # once DISC. % took its share of the page (2026-10-01). 4pt is the cell's padding.
    grand_text = _money(product_total + accessory_total)
    need = stringWidth(grand_text, STYLE_LABEL.fontName, STYLE_LABEL.fontSize) + 4 + 1
    amount_at = n_cols - 1
    while amount_at > 1 and sum(col_widths[amount_at:]) < need:
        amount_at -= 1
    if amount_at < n_cols - 1:
        extra_styles.append(("SPAN", (amount_at, len(table_data)), (n_cols - 1, len(table_data))))

    total_row = [""] * n_cols
    total_row[0] = Paragraph(f"Grand Total ({currency_code(boq.currency)})", STYLE_LABEL)
    total_row[amount_at] = Paragraph(grand_text, STYLE_GRAND_TOTAL_AMOUNT)
    table_data.append(total_row)

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    n_rows = len(table_data)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("SPAN", (0, n_rows - 1), (amount_at - 1, n_rows - 1)),
        ("BACKGROUND", (0, n_rows - 1), (-1, n_rows - 1), colors.HexColor("#fff4b8")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ] + extra_styles))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=QUOTATION_PDF_PAGE_SIZE,
        leftMargin=QUOTATION_PDF_MARGIN, rightMargin=QUOTATION_PDF_MARGIN,
        topMargin=QUOTATION_PDF_MARGIN, bottomMargin=QUOTATION_PDF_MARGIN,
        title=f"Quotation Summary - {boq.project_name}",
    )
    story = _quotation_cover_flowables(boq)
    story.extend(_summary_totals_flowables(boq, product_total, accessory_total, plan))
    story.append(Paragraph("Product Summary", STYLE_FLOOR))
    story.append(table)
    story.append(PageBreak())
    story.extend(_notes_flowables(boq))
    doc.build(story)
    return out_path


def _summary_totals_flowables(boq, product_total, accessory_total, plan=None):
    """The Summary page, mirroring the full Quotation PDF's own - see
    _write_summary_totals_sheet for why products/accessories replace its per-floor rows."""
    plan = plan or DiscountPlan.for_boq(boq)
    code = currency_code(boq.currency)
    flow = [Paragraph("Summary", STYLE_FLOOR), Spacer(1, 3 * mm)]
    data = [
        ["", *summary_headings(plan, boq.currency)],
        [Paragraph(boq.area or "Product Summary", STYLE_LABEL),
         Paragraph(_money(product_total), STYLE_LABEL),
         Paragraph(_money(accessory_total), STYLE_LABEL)],
        # The figure goes in the LAST column: the SPAN below merges columns 0-1 and reportlab
        # draws only a span's top-left cell, so a total placed in column 1 vanishes.
        [Paragraph(f"Grand Total ({code})", STYLE_LABEL), "",
         Paragraph(_money(product_total + accessory_total), STYLE_LABEL)],
    ]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("SPAN", (0, 2), (1, 2)),
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#fff4b8")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    # The rate, stated once, as the .xlsx Summary states it - only where the discount is shown.
    if plan.shown:
        data.append([Paragraph(plan.cell_label(), STYLE_LABEL), "",
                     Paragraph(discount_text(plan.rate), STYLE_LABEL)])
        style.append(("SPAN", (0, 3), (1, 3)))
    table = Table(data, colWidths=[60 * mm, 60 * mm, 60 * mm])
    table.setStyle(TableStyle(style))
    flow.append(table)
    flow.append(PageBreak())
    return flow
