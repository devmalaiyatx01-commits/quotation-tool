"""Generates the Quotation .pdf - the print-ready sibling of app/quotation_export.py's
.xlsx, the same relationship app/pdf_export.py already has to app/excel_export.py's plain
BOQ export. Same Cover/Summary/item-table/terms content and pricing logic as the .xlsx
(shares _line_totals/_floor_totals from quotation_export.py so the two formats can never
disagree on an amount), laid out for a client to open and read directly.

Landscape A4, not portrait: the Quotation's item table has 19 columns (vs. the plain BOQ
PDF's 11) - REF NO./Model No./LED COLOR/AREA plus five pricing columns on top of the usual
set. Portrait's ~174mm usable width was already tight for 11 columns (see pdf_export.py's
own column-width comment); 19 columns need the ~267mm landscape gives instead. Cover/
Summary/Notes pages render fine with the extra width too - just more whitespace, not a
layout problem - so the whole document uses one consistent pagesize rather than mixing
orientations mid-document (SimpleDocTemplate is simplest with a single pagesize throughout).
"""
from pathlib import Path
from types import SimpleNamespace

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak, HRFlowable,
)

from .excel_export import (
    COMPANY_LINES, LOGO_PATH, _item_blocks, _format_display_date, _autofill_sno,
    line_diagram_for,
)
from .pdf_export import (
    _line_diagram_flowable, _ref_pictures_flowable, fit_header, fit_columns_mm, widest_token_pt, NO_BREAK_KEYS,
    HEADING_READABLE_PT,
    GREY, LIGHT_GREY, BORDER, STYLE_COMPANY, STYLE_COMPANY_LINE, STYLE_LABEL, STYLE_VALUE,
    STYLE_FLOOR, STYLE_BODY, STYLE_BODY_LEFT, STYLE_ROOM_CELL, STYLE_REMARK_CELL,
    _p, _product_image_flowable, _notes_flowables,
)
from .quotation_export import (
    QUOTATION_COLUMN_KEYS, QUOTATION_COLUMN_LABELS, BLOCK_FIRST_ROW_ONLY_KEYS,
    visible_quotation_keys,
    money_heading, currency_code,
    CURRENCY_PREFIXES, DEFAULT_CURRENCY, _line_totals, _floor_totals, total_row_columns,
    DiscountPlan, line_discount, discount_text, summary_headings,
)

PAGE_SIZE = landscape(A4)
MARGIN = 15 * mm

# Relative weights, not final widths - scaled to fill the usable page width the same way
# pdf_export.py's own _scaled_widths_mm does, so the exact numbers here only matter
# relative to each other (description widest, s_no/qty narrowest).
#
# wattage (2026-08-19, was 11) and acc_net_price (was 16) were too narrow for their own
# ALL-CAPS header labels - "WATTAGE" and "Accessories Net Price" were breaking mid-word
# on every export, verified with reportlab.pdfmetrics.stringWidth against
# STYLE_QUOTE_HEADER_CELL (Helvetica-Bold 6.5pt) plus this table's real cell padding, same
# class of bug as pdf_export.py's own reference_number/wattage fix from the same day.
# description gave up the room (was 40, plenty of real slack - still by far the widest
# column at ~32mm final).
# The two accessory money columns are wider than the rest of the price group (21/19 against
# 15-16) for two reasons that push the same way: their headings are the longest in the table,
# and they hold the largest numbers - "1,234,567.00" needs about 45pt, which a 15mm column does
# not have. The 4mm comes off description, which wraps freely and had the most slack.
RAW_COL_WIDTHS_MM = {
    "s_no": 8, "ref_no": 14, "make": 14, "image": 20, "ref_images": 20, "model_no": 16, "description": 28,
    "led_color": 12, "wattage": 15, "finish": 13, "beam": 11, "control": 15, "area": 16,
    "dimensions": 16,
    "qty": 8, "unit": 10, "price": 15, "discount": 10, "discounted_price": 17, "amount": 16,
    "acc_net_price": 21, "acc_amount": 19,
}
USABLE_WIDTH_MM = PAGE_SIZE[0] / mm - 2 * (MARGIN / mm)
IMAGE_MAX_MM = 14

# Every value cell in the quotation table renders at this size (STYLE_PRICE_CELL,
# STYLE_QUOTE_BODY, STYLE_QUOTE_BODY_LEFT all use it), so measuring against it measures
# what will actually be drawn.
BODY_FONT_PT = 7.5
STYLE_PRICE_CELL = ParagraphStyle("price_cell", fontName="Helvetica", fontSize=7.5, alignment=TA_CENTER, leading=9)
STYLE_QUOTE_HEADER_CELL = ParagraphStyle("quote_header_cell", fontName="Helvetica-Bold", fontSize=6.5, alignment=TA_CENTER, leading=8)
STYLE_CURRENCY_NOTE = ParagraphStyle("currency_note", fontName="Helvetica", fontSize=7.5, alignment=TA_LEFT, leading=10, spaceAfter=3)
STYLE_QUOTE_BODY = ParagraphStyle("quote_body", fontName="Helvetica", fontSize=7.5, alignment=TA_CENTER, leading=9)
STYLE_QUOTE_BODY_LEFT = ParagraphStyle("quote_body_left", fontName="Helvetica", fontSize=7.5, alignment=TA_LEFT, leading=9)
# Bold enough to read as a total, but set at the body size: the amount columns are narrow, and
# the 10pt STYLE_LABEL wraps a figure like "12,345.00" onto two lines.
STYLE_TOTAL_CELL = ParagraphStyle("total_cell", fontName="Helvetica-Bold", fontSize=7.5, alignment=TA_CENTER, leading=9)
STYLE_TOTAL_CELL_LEFT = ParagraphStyle("total_cell_left", fontName="Helvetica-Bold", fontSize=7.5, alignment=TA_LEFT, leading=9)


# Prose: allowed - expected - to wrap, and therefore the columns that give up width so the
# value columns do not have to.
FLEX_KEYS = {"description"}


def _scaled_col_widths_mm(keys=None, boq=None, plan=None):
    """Column widths for one quotation table.

    Without `boq` this is the old behaviour: designed widths scaled to fill the page. With it,
    every column that must not wrap is first widened to hold the widest value this particular
    document prints - see pdf_export.fit_columns_mm."""
    keys = keys or QUOTATION_COLUMN_KEYS
    if boq is None:
        raw = [RAW_COL_WIDTHS_MM[k] for k in keys]
        scale = USABLE_WIDTH_MM / sum(raw)
        return [w * scale for w in raw]
    heads = {k: widest_token_pt(pdf_quotation_heading(k), "Helvetica-Bold", HEADING_READABLE_PT)
             for k in keys}
    return fit_columns_mm(keys, RAW_COL_WIDTHS_MM, _widest_values_pt(boq, keys, plan=plan),
                          USABLE_WIDTH_MM, FLEX_KEYS, heading_pt=heads)


def _price_texts(item, standard, plan):
    """What each money column - and DISC. % - prints for one line, by the document's
    DiscountPlan. Drawn by _append_quotation_rows and measured by _widest_values_pt, so the
    width a column is given is the width of what is actually printed in it.

    Quoting final prices only, the net price is printed as the Price: the client sees what they
    pay, and nothing about a discount."""
    unit_price, net_unit, amount = _line_totals(item, standard)
    rate = line_discount(item, standard)
    # Every line states its rate on a quotation that shows its discount, 0% included.
    rate_text = discount_text(rate) if plan.shown else ""
    if item.is_accessory:
        return {"discount": rate_text, "acc_net_price": _money(net_unit),
                "acc_amount": _money(amount)}
    if plan.shown:
        return {"price": _money(unit_price), "discount": rate_text,
                "discounted_price": _money(net_unit), "amount": _money(amount)}
    return {"price": _money(net_unit), "amount": _money(amount)}


def _widest_values_pt(boq, keys, items=None, plan=None):
    """The widest string each column will actually print, in points at the body font size.

    Measured from the same values _append_quotation_rows writes, so the two cannot disagree -
    a measurement that is not of the real cell text is worse than none."""
    plan = plan or DiscountPlan.for_boq(boq)
    widest = {k: 0.0 for k in keys}

    def note(key, text):
        # The widest TOKEN, not the widest string: a phrase may wrap at its spaces, a number or
        # a code may not be broken through the middle (see widest_token_pt). Only the columns
        # read as a unit are measured - NO_BREAK_KEYS - because insisting on it everywhere needs
        # more width than the page has.
        if key in widest and key in NO_BREAK_KEYS and text:
            widest[key] = max(widest[key], widest_token_pt(text, size=BODY_FONT_PT))

    rows = items if items is not None else [
        (item, room.name) for floor in boq.floors for room in floor.rooms for item in room.items
    ]
    for entry in rows:
        item, area = entry if isinstance(entry, tuple) else (entry, "")
        note("s_no", item.item_code)
        note("ref_no", item.reference_number)
        note("make", item.make)
        note("model_no", item.model_no)
        note("led_color", item.led_color)
        note("wattage", item.wattage)
        note("finish", item.finish)
        note("beam", item.beam)
        note("control", item.control)
        note("area", area)
        note("qty", item.qty)
        note("unit", item.unit)
        if not line_diagram_for(item):
            note("dimensions", item.dimensions)
        for key, text in _price_texts(item, boq.discount_percent, plan).items():
            note(key, text)
    return widest


# The Quotation fits 20 columns across 267mm, which is ~13mm - about 37pt - per column.
# "Accessories Net Price (INR)" needs 86pt: a single line only at 2.8pt, which is not a font
# size. So the currency is stated once above the table (see _quotation_floor_flowables) rather
# than five times inside headings too narrow to hold it, and the headings themselves use the
# abbreviations a BOQ normally uses. The .xlsx keeps the fuller "(INR)" headings - its columns
# are resizable and already fit them on one line.
PDF_HEADING_OVERRIDES = {
    "s_no": "S.No",     # the .xlsx's "S. No." needs 4.7pt in a 7mm column; the BOQ PDF already
                        # writes it this way
    "led_color": "CCT",
    "beam": "BEAM",
    "price": "PRICE",
    "discount": "DISC. %",
    "discounted_price": "DIS. PRICE",
    "amount": "AMOUNT",
    "acc_net_price": "ACC. NET PRICE",
    "acc_amount": "ACC. AMOUNT",
}


def pdf_quotation_heading(key):
    return PDF_HEADING_OVERRIDES.get(key, QUOTATION_COLUMN_LABELS[key])


def _money(value, prefix=""):
    """A plain amount. The currency is named once in the column heading rather than on every
    figure - see quotation_export._currency_format for the reasoning. `prefix` is retained so
    the few places that genuinely want it inline (a single grand total standing on its own,
    with no heading above it to carry the code) can still pass one."""
    if value is None:
        return ""
    return f"{prefix} {value:,.2f}".strip() if prefix else f"{value:,.2f}"


def _currency_prefix(currency: str) -> str:
    return CURRENCY_PREFIXES.get(currency, CURRENCY_PREFIXES[DEFAULT_CURRENCY])


def _cover_flowables(boq):
    """Same content/order as pdf_export.py's own _cover_flowables - not imported directly
    since that one ends with a portrait-sized meta table; rebuilt here so column widths
    can use this document's own (wider, landscape) usable width instead."""
    flow = []
    if LOGO_PATH.exists():
        probe = Image(str(LOGO_PATH))
        target_w = 100 * mm
        target_h = target_w * (probe.imageHeight / probe.imageWidth)
        logo = Image(str(LOGO_PATH), width=target_w, height=target_h)
        logo.hAlign = "CENTER"
        flow.append(logo)
    else:
        flow.append(Paragraph(COMPANY_LINES[0], STYLE_COMPANY))
        for line in COMPANY_LINES[1:]:
            flow.append(Paragraph(line, STYLE_COMPANY_LINE))
    # See pdf_export._cover_flowables: the letterhead separates itself by whitespace, with
    # no rule under it.
    flow.append(Spacer(1, 12 * mm))
    flow.append(Paragraph(_format_display_date(boq.boq_date), STYLE_LABEL))
    flow.append(Spacer(1, 4 * mm))
    rows = [
        ("PROJECT", boq.project_name or ""),
        ("PROJECT CODE", boq.project_code or ""),
        ("AREA", boq.area or ""),
        ("CLIENT", boq.client or ""),
    ]
    meta_table = Table(
        [[_p(f"{label} :", STYLE_LABEL), _p(value, STYLE_VALUE)] for label, value in rows],
        colWidths=[35 * mm, 150 * mm],
    )
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    flow.append(meta_table)
    flow.append(Spacer(1, 10 * mm))
    flow.append(Paragraph("Please find below our quotation for the required products:", STYLE_VALUE))
    flow.append(PageBreak())
    return flow


def _summary_flowables(boq, floor_totals_by_id, plan=None):
    plan = plan or DiscountPlan.for_boq(boq)
    flow = [Paragraph("Summary", STYLE_FLOOR), Spacer(1, 3 * mm)]
    code = currency_code(boq.currency)
    header = ["", *summary_headings(plan, boq.currency)]
    data = [header]
    grand_total = 0.0
    for floor in boq.floors:
        product_total, accessory_total = floor_totals_by_id[floor.id]
        grand_total += product_total + accessory_total
        data.append([_p(floor.name, STYLE_BODY_LEFT), _p(_money(product_total), STYLE_BODY), _p(_money(accessory_total), STYLE_BODY)])
    # The total goes in the LAST column, not the second. The SPAN below merges columns 0-1,
    # and reportlab draws only a span's top-left cell - a total placed in column 1 is inside
    # the merged region and is silently dropped, leaving the row's visible box empty.
    data.append([_p("Grand Total (" + code + ")", STYLE_LABEL), "",
                 _p(_money(grand_total), STYLE_LABEL)])
    grand_idx = len(data) - 1
    # The rate, stated once, as the .xlsx Summary states it - only where the discount is shown.
    if plan.shown:
        data.append([_p(plan.cell_label(), STYLE_LABEL), "",
                     _p(discount_text(plan.rate), STYLE_LABEL)])

    col_widths = [60 * mm, 60 * mm, 60 * mm]
    table = Table(data, colWidths=col_widths)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("SPAN", (0, grand_idx), (1, grand_idx)),
        ("BACKGROUND", (0, grand_idx), (-1, grand_idx), colors.HexColor("#fff4b8")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if plan.shown:
        style.append(("SPAN", (0, grand_idx + 1), (1, grand_idx + 1)))
    table.setStyle(TableStyle(style))
    flow.append(table)
    if boq.area:
        flow.append(Spacer(1, 4 * mm))
        flow.append(Paragraph(boq.area, STYLE_VALUE))
    flow.append(PageBreak())
    return flow


def _append_quotation_rows(table_data, items, keys, discount_percent, currency, area_value=None, widths_mm=None, plan=None):
    """Appends `_item_blocks(items)` to `table_data` (mutated in place) - the per-item row-
    building logic extracted out of `_quotation_floor_flowables`'s per-room loop so it's
    reusable by a flat, no-floor-or-room item table too (boq_summary.py's deduplicated
    Quotation Summary PDF). `area_value` fills the AREA column (if `keys` includes it) for
    every row appended here - a room name for a per-floor table, left None for the Summary
    table where per-room area no longer applies (and "area" is typically left out of `keys`
    entirely there). `plan` is the document's DiscountPlan (see _price_texts)."""
    if plan is None:
        plan = DiscountPlan(SimpleNamespace(discount_percent=discount_percent), items)
    for block in _item_blocks(items):
        is_accessory_block = block[0].is_accessory
        for item_idx, item in enumerate(block):
            prices = _price_texts(item, discount_percent, plan)
            is_first_row = item_idx == 0
            row = []
            for key in keys:
                # An accessory line's Dimension cell is its own (its picture, below), so that one
                # is not left to the block's first row.
                if (key in BLOCK_FIRST_ROW_ONLY_KEYS and not is_first_row
                        and not (key == "dimensions" and is_accessory_block)):
                    row.append("")
                    continue
                if key == "image":
                    row.append(_product_image_flowable(item, (widths_mm or {}).get(key))
                               if (is_first_row and not is_accessory_block) else "")
                elif key == "ref_images":
                    # The project's own pictures for this product - on the product's row, not
                    # repeated down the accessories hanging under it.
                    row.append(_ref_pictures_flowable(item, (widths_mm or {}).get(key))
                               if is_first_row else "")
                elif key == "dimensions":
                    # The manufacturer's line drawing when the row asks for it, otherwise
                    # whatever measurements were typed in. An accessory line shows its own
                    # picture when it has one (excel_export.line_diagram_for), and is blank
                    # otherwise, as it always was.
                    if is_accessory_block:
                        row.append(_line_diagram_flowable(item, (widths_mm or {}).get(key)))
                    elif is_first_row:
                        row.append(_line_diagram_flowable(item, (widths_mm or {}).get(key))
                                   or _p(item.dimensions, STYLE_QUOTE_BODY))
                    else:
                        row.append("")
                elif key == "s_no":
                    # Same project-specific code as the plain BOQ export's own "S. No"
                    # column - REF NO. below is the separate field for that concept.
                    row.append(_p(item.item_code, STYLE_QUOTE_BODY))
                elif key == "ref_no":
                    row.append(_p(item.reference_number, STYLE_QUOTE_BODY))
                elif key == "model_no":
                    row.append(_p(item.model_no, STYLE_QUOTE_BODY))
                elif key == "description":
                    row.append(_p(item.description, STYLE_QUOTE_BODY_LEFT))
                elif key == "led_color":
                    row.append(_p(item.led_color, STYLE_QUOTE_BODY))
                elif key == "wattage":
                    row.append(_p(item.wattage, STYLE_QUOTE_BODY))
                elif key == "finish":
                    row.append(_p(item.finish, STYLE_QUOTE_BODY))
                elif key == "beam":
                    row.append(_p(item.beam, STYLE_QUOTE_BODY))
                elif key == "control":
                    row.append(_p(item.control, STYLE_QUOTE_BODY))
                elif key == "make":
                    row.append(_p(item.make, STYLE_QUOTE_BODY))
                elif key == "area":
                    row.append(_p(area_value or "", STYLE_QUOTE_BODY))
                elif key == "qty":
                    row.append(_p(item.qty, STYLE_QUOTE_BODY))
                elif key == "unit":
                    row.append(_p(item.unit, STYLE_QUOTE_BODY))
                elif key in ("price", "discount", "discounted_price", "amount",
                             "acc_net_price", "acc_amount"):
                    row.append(_p(prices.get(key, ""), STYLE_PRICE_CELL))
                else:
                    row.append("")
            table_data.append(row)


def _quotation_floor_flowables(floor, discount_percent, currency, keys=None, boq=None, area_banners=False, plan=None):
    """Flat table (no per-room banner rows) with AREA as its own column - matches the
    sample Quotation PDF's own structure, the same reasoning quotation_export.py's
    _write_quotation_floor_sheet already documents for the .xlsx version.

    `area_banners`: each area as a full-width banner row with its items beneath it and its
    remark after them - drawn exactly as the detailed BOQ PDF draws its areas
    (pdf_export._floor_flowables) - for the quotation without an AREA column."""
    keys = keys or QUOTATION_COLUMN_KEYS
    widths_mm = dict(zip(keys, _scaled_col_widths_mm(keys, boq, plan)))
    flow = [
        Paragraph((floor.name or "Floor").upper(), STYLE_FLOOR),
        # The currency, once, where five headings used to repeat it.
        Paragraph(f"All amounts in {currency_code(currency)}", STYLE_CURRENCY_NOTE),
    ]
    table_data = [[fit_header(pdf_quotation_heading(k), widths_mm[k], base_size=6.5)
                   for k in keys]]

    extra_styles = []
    n_cols = len(keys)

    def banner(paragraph):
        idx = len(table_data)
        table_data.append([paragraph] + [""] * (n_cols - 1))
        extra_styles.append(("SPAN", (0, idx), (n_cols - 1, idx)))
        extra_styles.append(("BACKGROUND", (0, idx), (-1, idx), LIGHT_GREY))

    for room in floor.rooms:
        if area_banners:
            banner(_p(room.name, STYLE_ROOM_CELL))
        _append_quotation_rows(table_data, room.items, keys, discount_percent, currency,
                               area_value=None if area_banners else room.name, widths_mm=widths_mm,
                               plan=plan)
        if area_banners and room.remark:
            banner(_p(f"Remark: {room.remark}", STYLE_REMARK_CELL))

    # This floor's own totals, directly under its last row, so the figures are where a reader
    # of the table finds them and not only on the Summary page at the front. Asked of the items
    # rather than of the rows: banner rows are rows, and a floor of empty areas has no total.
    product_total, accessory_total = _floor_totals(floor, discount_percent)
    total_cols = total_row_columns(keys, product_total, accessory_total)
    if total_cols and any(room.items for room in floor.rooms):
        first = min(total_cols)
        row_cells = [""] * len(keys)
        for idx, value in total_cols.items():
            row_cells[idx] = _p(_money(value), STYLE_TOTAL_CELL)
        totals_idx = len(table_data)
        if first > 0:
            # The label must sit in the span's top-left cell - reportlab draws only that one
            # and silently drops anything else inside the merged region.
            row_cells[0] = _p(f"Total ({currency_code(currency)})", STYLE_TOTAL_CELL_LEFT)
            extra_styles.append(("SPAN", (0, totals_idx), (first - 1, totals_idx)))
        extra_styles.append(
            ("BACKGROUND", (0, totals_idx), (-1, totals_idx), colors.HexColor("#e6e6e6")))
        table_data.append(row_cells)

    col_widths = [widths_mm[k] * mm for k in keys]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ] + extra_styles))
    flow.append(table)
    return flow


def export_quotation_pdf(boq, out_path: Path, hidden_columns=None, area_banners=False) -> Path:
    """`area_banners`: the "detailed, area as a subheader" quotation - see
    _quotation_floor_flowables. Same pages and figures; areas as banners, no AREA column."""
    plan = DiscountPlan.for_boq(boq)
    keys = visible_quotation_keys(hidden_columns, boq=boq, plan=plan)
    if area_banners:
        keys = [k for k in keys if k != "area"]
    _autofill_sno(boq)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=PAGE_SIZE,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"Quotation - {boq.project_name}",
    )
    floor_totals_by_id = {floor.id: _floor_totals(floor, boq.discount_percent) for floor in boq.floors}

    story = _cover_flowables(boq)
    story.extend(_summary_flowables(boq, floor_totals_by_id, plan))
    for i, floor in enumerate(boq.floors):
        story.extend(_quotation_floor_flowables(floor, boq.discount_percent, boq.currency, keys, boq,
                                                area_banners=area_banners, plan=plan))
        if i < len(boq.floors) - 1:
            story.append(PageBreak())
    if boq.floors:
        story.append(PageBreak())
    story.extend(_notes_flowables(boq))
    doc.build(story)
    return out_path
