"""The quick BOQ's two documents as .pdf - the print-ready sibling of consolidated_export.py's
.xlsx, the same relationship quotation_pdf_export.py has to quotation_export.py.

Same table, same grouping, same figures (the amounts come from quotation_export._line_totals,
as every other document in this app does, so no two formats can ever disagree on a price), laid
out for somebody to open and read rather than to keep editing.

Landscape A4: the consolidated table is eighteen columns with the money on, and two of them are
pictures. Portrait has nowhere near the width.

The one thing worth knowing about the layout: a reference code spans its whole group of rows and
a product spans its own accessory rows, so the table is built as a flat list of rows with SPAN
commands recorded as it goes - the same shape the .xlsx writes with merged cells.
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak,
)

from reportlab.lib.styles import ParagraphStyle

from .database import IMAGES_DIR
from .pdf_export import (
    fit_header, _p, _scaled_image, _product_image_flowable, _notes_flowables,
    STYLE_BODY, STYLE_BODY_LEFT, STYLE_LABEL, STYLE_VALUE, BORDER, GREY,
)
from .quotation_pdf_export import _cover_flowables, _money, _currency_prefix
from .quotation_export import _line_totals, _safe_float, line_discount, discount_text
from .consolidated_export import (
    CONSOLIDATED_LABELS, MONEY_KEYS, CLOSING_NOTE, TAX_NOTE,
    _visible_keys, quick_products, group_by_ref_code, _totals_of, discount_plan,
)

PAGE_SIZE = landscape(A4)
MARGIN = 12 * mm
# A whisker under the true width: a table sized to exactly the frame gets squeezed by
# reportlab, and a column a fraction narrower than the fitter measured breaks its heading in
# half. The slack costs nothing visible.
USABLE_MM = (PAGE_SIZE[0] - 2 * MARGIN) / mm * 0.985

# Read as millimetres at the page's full width, and scaled to fill whatever is actually
# available. Each one is what its own content needs at CELL_PT: a money cell holds
# "Rs. N" on one line, CONTROL holds "DALI DIM", and the description takes what is
# left. Sized this way because the first cut was sized by eye and broke words in half -
# "CONTR OL", "Litolu x" - which makes a correct document look unfinished.
COLUMN_WEIGHTS = {
    "ref_code": 8, "area": 20, "ref_images": 17, "make": 13, "image": 17, "description": 45,
    "control": 13, "wattage": 12, "led_color": 9, "finish": 10, "beam": 13, "qty": 7,
    "unit": 7, "price": 18, "discount": 9, "amount": 21, "acc_net_price": 16, "acc_amount": 16,
    "remarks": 14,
}
# How big a picture may be drawn in its column, in mm - capped so a tall picture cannot push one
# row down a whole page on its own.
PICTURE_MAX_MM = 26

# Eighteen columns across a landscape page leaves each one narrow, so this table sets its own
# slightly smaller cell type rather than the shared one - a point smaller is the difference
# between "DALI DIM" on one line and "DALI" over "DIM".
CELL_PT = 6
STYLE_CELL = ParagraphStyle("consCell", parent=STYLE_BODY, fontSize=CELL_PT, leading=CELL_PT + 1.4)
STYLE_CELL_LEFT = ParagraphStyle("consCellLeft", parent=STYLE_BODY_LEFT, fontSize=CELL_PT,
                                 leading=CELL_PT + 1.4)


def _column_widths(keys):
    total = sum(COLUMN_WEIGHTS[k] for k in keys)
    return [COLUMN_WEIGHTS[k] / total * USABLE_MM * mm for k in keys]


def _ref_picture_flowables(item, images_by_id, width_mm):
    """This product's reference pictures, stacked in their cell and aspect-fitted - the same
    containment rule the .xlsx applies, done by the table cell rather than by hand."""
    out = []
    for image_id in (item.ref_image_ids or []):
        im = images_by_id.get(image_id)
        if not im:
            continue
        path = IMAGES_DIR / im.filename
        if not path.exists():
            continue
        drawn = _scaled_image({"path": str(path)}, min(PICTURE_MAX_MM, max(6, width_mm - 2)))
        if drawn:
            out.append(drawn)
    if not out:
        return ""
    if len(out) == 1:
        return out[0]
    stacked = []
    for n, picture in enumerate(out):
        if n:
            stacked.append(Spacer(1, 1.5 * mm))
        stacked.append(picture)
    return stacked


def _summary_flowables(boq, totals, currency, plan=None):
    prefix = _currency_prefix(currency)
    flow = [Paragraph("Summary", STYLE_LABEL), Spacer(1, 4 * mm)]
    rows = [
        [_p("Scope", STYLE_BODY), _p("Price", STYLE_BODY), _p("Amount Of Accessories", STYLE_BODY)],
        [_p((boq.area or "").strip() or boq.project_name, STYLE_BODY_LEFT),
         _p(_money(totals[0], prefix), STYLE_BODY), _p(_money(totals[1], prefix), STYLE_BODY)],
        [_p("Grand Total", STYLE_BODY),
         _p(_money((totals[0] or 0) + (totals[1] or 0), prefix), STYLE_BODY), _p("", STYLE_BODY)],
    ]
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#FFF6CC")),
        ("SPAN", (1, 2), (2, 2)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    # The rate the products are worked out from, stated once as the .xlsx states it under its
    # table - only on a quotation that shows its discount (2026-10-01; the PDF never said it).
    if plan is not None and plan.shown:
        rows.append([_p(plan.cell_label(), STYLE_BODY), _p(discount_text(plan.rate), STYLE_BODY),
                     _p("", STYLE_BODY)])
        style.append(("SPAN", (1, 3), (2, 3)))
    table = Table(rows, colWidths=[80 * mm, 55 * mm, 55 * mm])
    table.setStyle(TableStyle(style))
    flow.append(table)
    flow.append(Spacer(1, 6 * mm))
    flow.append(Paragraph("GST as per actual<br/>Freight as per actual", STYLE_VALUE))
    flow.append(PageBreak())
    return flow


def _table_flowables(boq, keys, with_money, plan=None):
    widths = _column_widths(keys)
    widths_mm = [w / mm for w in widths]
    index_of = {key: i for i, key in enumerate(keys)}
    areas_by_id = {a.id: a for a in boq.areas}
    images_by_id = {im.id: im for im in boq.ref_images}
    prefix = _currency_prefix(boq.currency)
    discount = boq.discount_percent or 0.0
    plan = plan or discount_plan(boq)

    data = [[fit_header(CONSOLIDATED_LABELS[k], widths_mm[i], base_size=CELL_PT) for i, k in enumerate(keys)]]
    spans, row_index = [], 1

    for code, supplies in group_by_ref_code(quick_products(boq)):
        group_start = row_index
        for product, accessories in supplies:
            product_start = row_index
            for n, item in enumerate([product] + list(accessories)):
                is_product = n == 0
                unit_price, net_unit, amount = _line_totals(item, discount)
                area_text = "<br/>".join(
                    areas_by_id[a].name for a in (product.area_ids or []) if a in areas_by_id)
                cells = []
                for key in keys:
                    if key == "ref_code":
                        cells.append(_p(code, STYLE_CELL) if row_index == group_start else "")
                    elif key == "area":
                        cells.append(_p(area_text, STYLE_CELL) if is_product else "")
                    elif key == "ref_images":
                        cells.append(_ref_picture_flowables(
                            product, images_by_id, widths_mm[index_of["ref_images"]])
                            if is_product else "")
                    elif key == "image":
                        cells.append(_product_image_flowable(
                            product, widths_mm[index_of["image"]]) if is_product else "")
                    elif key == "make":
                        cells.append(_p(item.make or "", STYLE_CELL) if is_product else "")
                    elif key == "description":
                        cells.append(_p((item.description or "").replace("\n", "<br/>"),
                                        STYLE_CELL_LEFT))
                    elif key == "remarks":
                        cells.append(_p((item.remarks or "").replace("\n", "<br/>"), STYLE_CELL_LEFT))
                    elif key in ("price", "amount"):
                        show = (not item.is_accessory) and unit_price
                        # Quoting final prices only, the price printed is the net one.
                        price = unit_price if plan.shown else net_unit
                        value = price if key == "price" else amount
                        cells.append(_p(_money(value, prefix), STYLE_CELL) if show else "")
                    elif key == "discount":
                        cells.append(_p(discount_text(line_discount(item, discount)), STYLE_CELL))
                    elif key in ("acc_net_price", "acc_amount"):
                        show = item.is_accessory and unit_price
                        value = net_unit if key == "acc_net_price" else amount
                        cells.append(_p(_money(value, prefix), STYLE_CELL) if show else "")
                    else:
                        cells.append(_p(getattr(item, {
                            "control": "control", "wattage": "wattage", "led_color": "led_color",
                            "finish": "finish", "beam": "beam", "qty": "qty", "unit": "unit",
                        }[key]) or "", STYLE_CELL))
                data.append(cells)
                row_index += 1
            product_end = row_index - 1
            if product_end > product_start:
                for key in ("area", "ref_images", "make", "image"):
                    if key in index_of:
                        spans.append(("SPAN", (index_of[key], product_start),
                                      (index_of[key], product_end)))
        if row_index - 1 > group_start:
            spans.append(("SPAN", (index_of["ref_code"], group_start),
                          (index_of["ref_code"], row_index - 1)))

    totals = _totals_of(boq) if with_money else (0.0, 0.0)
    if with_money:
        foot = [""] * len(keys)
        foot[index_of["description"]] = _p(CLOSING_NOTE, STYLE_CELL_LEFT)
        foot[index_of["amount"]] = _p(_money(totals[0], prefix), STYLE_CELL)
        foot[index_of["acc_net_price"]] = _p(TAX_NOTE.replace("\n", "<br/>"), STYLE_CELL_LEFT)
        foot[index_of["acc_amount"]] = _p(_money(totals[1], prefix), STYLE_CELL)
        data.append(foot)
        spans.append(("SPAN", (index_of["description"], row_index),
                      (index_of["amount"] - 1, row_index)))
        spans.append(("BACKGROUND", (index_of["amount"], row_index),
                      (index_of["amount"], row_index), colors.HexColor("#FFF6CC")))
        spans.append(("BACKGROUND", (index_of["acc_amount"], row_index),
                      (index_of["acc_amount"], row_index), colors.HexColor("#FFF6CC")))

    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        # 3pt a side, the same as every other table here - and the same total fit_header
        # measures against (pdf_export.CELL_PADDING_PT). Left at reportlab's own default of 6
        # a side, every heading had 6pt less room than the fitter thought and wrapped in the
        # middle of a word: "CONTR OL", "Q TY".
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ] + spans))
    return [table]


def _build(boq, out_path: Path, with_money: bool) -> Path:
    plan = discount_plan(boq)
    keys = _visible_keys(with_money, plan)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=PAGE_SIZE, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="%s - %s" % (boq.project_name or "BOQ",
                           "Quotation" if with_money else "BOQ"),
    )
    flow = list(_cover_flowables(boq))
    if with_money:
        flow += _summary_flowables(boq, _totals_of(boq), boq.currency, plan)
    flow += _table_flowables(boq, keys, with_money, plan)
    flow.append(PageBreak())
    flow += _notes_flowables(boq)
    doc.build(flow)
    return out_path


def export_consolidated_quotation_pdf(boq, out_path: Path) -> Path:
    return _build(boq, out_path, with_money=True)


def export_consolidated_boq_pdf(boq, out_path: Path) -> Path:
    return _build(boq, out_path, with_money=False)
