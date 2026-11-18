"""Generates a clean, client-presentable BOQ .pdf - same underlying data as
app/excel_export.py's .xlsx (cover info, per-floor/per-room item tables with
product photos, the standard terms/NOTE page), but laid out as a print-ready
document rather than a working spreadsheet: no cell-editing affordances, no
spreadsheet gridlines/banner colors, just a plain table a client can open and
read without Excel. Shares COMPANY_LINES/DEFAULT_NOTES_TEXT with excel_export.py so
the two formats can never drift apart on the actual company/terms text.
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase.pdfmetrics import stringWidth
from xml.sax.saxutils import escape
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak, HRFlowable,
)

from .excel_export import (
    line_diagram_for,
    COMPANY_LINES, DEFAULT_NOTES_TEXT, BOLD_SPAN_RE, COLUMN_KEYS, LOGO_PATH,
    item_ref_pictures, boq_has_ref_pictures,
    _item_blocks, _format_display_date, _autofill_sno,
)

PAGE_SIZE = A4
MARGIN = 18 * mm

COLUMN_LABELS = {
    "s_no": "S.No", "image": "Image", "ref_images": "Ref. Images", "dimensions": "Dimension",
    # "Ref. No", matching the .xlsx: at this column width "Reference No." wraps to
    # "Referenc / e No.", and the same BOQ should not name a column two different ways in
    # its two formats.
    "reference_number": "Ref. No", "description": "Description",
    "finish": "Finish", "wattage": "Wattage", "beam": "Beam", "control": "Control", "make": "Make",
    "qty": "Qty", "unit": "Unit", "remarks": "Remarks",
}
# A4 width (210mm) minus left+right margins (18mm each) - the true physical usable width, kept
# as its own constant rather than derived from COL_WIDTHS_MM_BY_KEY's sum. It used to be
# derived (sum(COL_WIDTHS_MM_BY_KEY.values())), which only worked because the original 11
# widths were hand-calibrated to sum to exactly this number - a fragile coincidence that broke
# the instant a 12th column (reference_number) was added, since _scaled_widths_mm scales
# *visible* columns back up to fill this constant whenever some are hidden: if the constant
# itself drifts from the real page width, every hidden-column export silently overflows the
# page margin instead of fitting it.
USABLE_WIDTH_MM = 174
# {key: width_mm} - relative weights, not final widths (_scaled_widths_mm always scales
# whatever's visible to fill USABLE_WIDTH_MM exactly, so these don't need to sum to anything
# in particular). Each width comfortably fits its own header label at STYLE_HEADER_CELL's
# font/size/padding, plus (where there's room) the single longest real word commonly seen in
# that column's actual data (e.g. "TEXTURED" from finish "TEXTURED GREY", "InHouse",
# "ON-OFF") - a single word forced to break mid-character ("BLAC"/"K") reads as broken;
# two words wrapping onto two lines ("TEXTURED"/"GREY") reads as a normal compact table
# cell, so only the former is worth sizing against.
#
# reference_number (2026-08-19, was 12) and wattage (was 12) were too narrow for their OWN
# header labels - "Reference No." and "Wattage" were breaking mid-word ("Refere"/"nce No.",
# "Wattag"/"e") on every single export, verified with reportlab.pdfmetrics.stringWidth
# against STYLE_HEADER_CELL (Helvetica-Bold 7pt) plus this table's real 3pt+3pt cell
# padding, not eyeballed. description gave up the room (was 48, plenty of real slack -
# still by far the widest column). Note: a handful of real DATA values (e.g. finish
# "TEXTURED", beam "ASYMMETRIC", make "Duragreen") are ALSO long enough to risk the same
# mid-word break in their own columns - a real, separate finding, left alone here since
# the header-breaking-on-every-export case is unambiguous and this one is a rarer,
# content-dependent edge case that deserves its own look rather than a rushed page-width
# renegotiation across every column at once.
# Keyed explicitly rather than zip()ped against COLUMN_KEYS: a zip silently truncates to the
# shorter side, so adding a column to excel_export.COLUMN_KEYS used to drop the width of
# whichever column happened to be last - no error, just a column with no width in a document
# already sent to a client. The assertion makes that a startup failure instead.
COL_WIDTHS_MM_BY_KEY = {
    "s_no": 9, "image": 20, "ref_images": 20, "dimensions": 18, "reference_number": 16, "description": 41,
    "finish": 18, "wattage": 13, "beam": 11, "control": 14, "make": 11, "qty": 7,
    "unit": 9, "remarks": 15,
}
assert set(COL_WIDTHS_MM_BY_KEY) == set(COLUMN_KEYS), (
    "every export column needs a PDF width: missing %s" % (set(COLUMN_KEYS) - set(COL_WIDTHS_MM_BY_KEY))
)

IMAGE_MAX_MM = 16  # longest side a product photo is scaled to fit within its cell
DIAGRAM_MAX_MM = 17  # the Dimension column's line drawing - its printed measurements have to
                     # stay readable, and it sits in a slightly wider column than the photo

GREY = colors.HexColor("#e8e8e8")
LIGHT_GREY = colors.HexColor("#f4f4f4")
BORDER = colors.HexColor("#bbbbbb")

STYLE_COMPANY = ParagraphStyle("company", fontName="Helvetica-Bold", fontSize=13, alignment=TA_CENTER, leading=16)
STYLE_COMPANY_LINE = ParagraphStyle("company_line", fontName="Helvetica", fontSize=9.5, alignment=TA_CENTER, leading=13)
STYLE_LABEL = ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=10, leading=13)
STYLE_VALUE = ParagraphStyle("value", fontName="Helvetica", fontSize=10, leading=13)
STYLE_FLOOR = ParagraphStyle("floor", fontName="Helvetica-Bold", fontSize=12, spaceBefore=4, spaceAfter=6)
STYLE_HEADER_CELL = ParagraphStyle("header_cell", fontName="Helvetica-Bold", fontSize=7, alignment=TA_CENTER, leading=9)

# How far a column heading may shrink to stay on one line. Below MIN it stops shrinking and is
# allowed to wrap after all - an unreadable heading is worse than a wrapped one, and hitting
# the floor means the label itself is too long for its column and wants shortening at source.
HEADER_MIN_FONT_PT = 4.6
# A column may be shrunk toward its content, but not so far that its own heading has to drop
# below this to fit - a 4.6pt heading is a last resort for a label that is genuinely too long,
# not something to inflict on a column just because this project left it blank.
HEADING_READABLE_PT = 5.8
CELL_PADDING_PT = 6  # LEFTPADDING + RIGHTPADDING, as set on every table below


def widest_token_pt(text, font="Helvetica", size=7.5):
    """Width of the longest run of non-space characters in `text`.

    This, not the width of the whole string, is what a column has to be able to hold: reportlab
    breaks a line at spaces, so a phrase spreads over two lines quite happily, but a token wider
    than the column is broken through the middle - "123,456.00" becoming "123,456.0 / 0", which
    on a priced document is not merely untidy."""
    return max((stringWidth(t, font, size) for t in str(text or "").split()), default=0.0)


def fit_columns_mm(keys, raw_mm, needed_pt, usable_mm, flex_keys,
                   heading_pt=None, min_flex_mm=20.0):
    """Column widths: the designed proportions, adjusted for what this document actually prints.

    The designed widths deliberately sum to more than the page (204mm of BOQ columns for a
    174mm page) and are scaled to fit - that scaling IS the layout, and it is the starting
    point here.

    From there, two adjustments:

    Columns in `needed_pt` that cannot hold their widest unbreakable token grow to fit it. A
    number split across lines ("123,456.0 / 0") is misread, not merely untidy.

    That width is paid for by columns with slack - one whose content is narrower than its
    designed width, which on a real BOQ is most of them: REF NO., Model No., Dimension and
    Remarks are blank on most rows and still claim their full width. A column is never taken
    below what its own heading needs (`heading_pt`), and the prose columns in `flex_keys` are
    only drawn on after that slack is exhausted, never below min_flex_mm.

    Anything still spare afterwards goes to the prose columns, which are the ones that can
    always use more."""
    scale = usable_mm / sum(raw_mm[k] for k in keys)
    base = {k: raw_mm[k] * scale for k in keys}
    heading_pt = heading_pt or {}

    def floor_mm(k):
        """The narrowest this column may be: enough for its content and its heading."""
        content = needed_pt.get(k, 0.0)
        head = heading_pt.get(k, 0.0)
        return (max(content, head) + CELL_PADDING_PT + 1) / mm

    want = dict(base)
    deficit = 0.0
    for k in keys:
        if k in flex_keys:
            continue
        need = floor_mm(k)
        if need > want[k]:
            deficit += need - want[k]
            want[k] = need

    if deficit <= 0:
        return [want[k] for k in keys]

    # 1) slack in the columns that are wider than their contents need
    donors = [(k, want[k] - floor_mm(k)) for k in keys
              if k not in flex_keys and want[k] - floor_mm(k) > 0]
    slack = sum(d for _, d in donors)
    if slack > 0:
        take = min(deficit, slack)
        for k, d in donors:
            want[k] -= take * (d / slack)
        deficit -= take

    # 2) then, and only then, the prose columns
    if deficit > 0:
        flex = [k for k in keys if k in flex_keys]
        spare = sum(max(0.0, want[k] - min_flex_mm) for k in flex)
        if spare > 0:
            take = min(deficit, spare)
            for k in flex:
                give = max(0.0, want[k] - min_flex_mm)
                want[k] -= take * (give / spare)
            deficit -= take

    # Whatever the table did not need goes back to the prose columns.
    left = usable_mm - sum(want.values())
    flex = [k for k in keys if k in flex_keys]
    if left > 0 and flex:
        for k in flex:
            want[k] += left / len(flex)
    return [want[k] for k in keys]

    flex = [k for k in keys if k in flex_keys]
    spare = sum(max(0.0, want[k] - min_flex_mm) for k in flex)
    wanted = sum(shortfall.values())
    payable = min(wanted, spare)
    if payable <= 0:
        return [want[k] for k in keys]

    for k, short in shortfall.items():
        want[k] += short * (payable / wanted)      # pro-rata when there is not enough to go round
    for k in flex:
        give = max(0.0, want[k] - min_flex_mm)
        if spare > 0:
            want[k] -= payable * (give / spare)
    return [want[k] for k in keys]


def fit_header(text, col_width_mm, base_size=7, min_size=HEADER_MIN_FONT_PT, bold=True):
    """A heading Paragraph sized to sit on one line in a column of this width.

    reportlab wraps a Paragraph at the cell edge, which is how "DIMENSION" became
    "DIMENSIO / N". Measuring the string against the real (post-scaling) column width and
    stepping the font down instead keeps every heading on one line at the largest size that
    fits."""
    text = str(text or "")
    font = "Helvetica-Bold" if bold else "Helvetica"
    # 0.97 of the true space: reportlab wraps when the text is wider than the cell, and a
    # heading sized to exactly the available width sits on that boundary - "Wattage" measured
    # 25.46pt into 25.46pt and wrapped anyway. The margin costs a fraction of a point and
    # removes the tie.
    avail = (col_width_mm * mm - CELL_PADDING_PT) * 0.97
    size = base_size
    if avail > 0 and text:
        needed = stringWidth(text, font, base_size)
        if needed > avail:
            size = max(min_size, base_size * avail / needed)
    style = ParagraphStyle(
        f"hdr_{size:.2f}", fontName=font, fontSize=size,
        alignment=TA_CENTER, leading=size * 1.15,
    )
    return Paragraph(escape(text), style)
STYLE_ROOM_CELL = ParagraphStyle("room_cell", fontName="Helvetica-Bold", fontSize=9.5, alignment=TA_LEFT, leading=12)
STYLE_ACCESSORIES_CELL = ParagraphStyle("accessories_cell", fontName="Helvetica-Bold", fontSize=8.5, alignment=TA_CENTER, leading=10)
STYLE_REMARK_CELL = ParagraphStyle("remark_cell", fontName="Helvetica-Oblique", fontSize=8.5, alignment=TA_LEFT, leading=11)
STYLE_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=8, alignment=TA_CENTER, leading=10)
STYLE_BODY_LEFT = ParagraphStyle("body_left", fontName="Helvetica", fontSize=8, alignment=TA_LEFT, leading=10)
STYLE_NOTE_BODY = ParagraphStyle("note_body", fontName="Helvetica", fontSize=9, leading=12)


def _p(text, style):
    return Paragraph((text or "").replace("\n", "<br/>"), style)


def _visible_keys(hidden_columns, boq=None):
    hidden = set(hidden_columns or [])
    if not boq_has_ref_pictures(boq):
        hidden.add("ref_images")
    return [k for k in COLUMN_KEYS if k not in hidden]


def _scaled_widths_mm(keys):
    """Widths for just the visible columns, scaled up so they still sum to the full
    usable page width - hiding a column frees up its space for the others rather than
    leaving a blank margin."""
    raw = [COL_WIDTHS_MM_BY_KEY[k] for k in keys]
    scale = USABLE_WIDTH_MM / sum(raw) if raw else 1
    return [w * scale for w in raw]


# {key: item -> cell content} - "image" is handled separately (a flowable, not text) and
# intentionally absent here; "s_no" reads item_code the same way excel_export.py's VALUE_BY_KEY
# does, for the same field, just formatted for a reportlab Paragraph instead of a cell value.
VALUE_BY_KEY = {
    "s_no": lambda item: _p(item.item_code, STYLE_BODY),
    # Blank when the line drawing is going here instead - see _line_diagram_flowable.
    "dimensions": lambda item: _p("" if line_diagram_for(item) else item.dimensions, STYLE_BODY),
    "reference_number": lambda item: _p(item.reference_number, STYLE_BODY),
    "description": lambda item: _p(item.description, STYLE_BODY_LEFT),
    "finish": lambda item: _p(item.finish, STYLE_BODY),
    "wattage": lambda item: _p(item.wattage, STYLE_BODY),
    "beam": lambda item: _p(item.beam, STYLE_BODY),
    "control": lambda item: _p(item.control, STYLE_BODY),
    "make": lambda item: _p(item.make, STYLE_BODY),
    "qty": lambda item: _p(item.qty, STYLE_BODY),
    "unit": lambda item: _p(item.unit, STYLE_BODY),
    "remarks": lambda item: _p(item.remarks, STYLE_BODY_LEFT),
}


def _line_diagram_flowable(item, col_width_mm=None):
    """The manufacturer's dimensioned line drawing, for the Dimension cell."""
    drawing = line_diagram_for(item)
    if not drawing:
        return ""
    return _scaled_image(drawing, _cell_image_mm(col_width_mm, DIAGRAM_MAX_MM))


def _cell_image_mm(col_width_mm, cap_mm):
    """The largest square box an image may occupy in a column of this width.

    The cap alone is not enough: _scaled_widths_mm rescales every column to fill the page, so a
    column's real width is not the raw number in the width table - in the Quotation the
    Dimension column lands at 14.1mm, and a drawing drawn to a fixed 17mm ran straight over its
    neighbours. Whichever is smaller wins."""
    if not col_width_mm:
        return cap_mm
    return max(4, min(cap_mm, col_width_mm - CELL_PADDING_PT / mm))


def _scaled_image(diagram, max_mm):
    """Aspect-fit one diagram dict into a max_mm box, never upscaling past native - the same
    rule excel_export._place_diagram applies, just centred by the table cell instead of by
    manual EMU offsets."""
    path = diagram.get("path")
    if not path or not Path(path).exists():
        return ""
    native_w, native_h = diagram.get("width"), diagram.get("height")
    try:
        if not native_w or not native_h:
            img = Image(path)
            native_w, native_h = img.imageWidth, img.imageHeight
        max_pt = max_mm * mm
        scale = min(max_pt / native_w, max_pt / native_h, 1.0)
        return Image(path, width=native_w * scale, height=native_h * scale)
    except Exception:
        return ""


def _product_image_flowable(item, col_width_mm=None):
    """Same source photo the .xlsx embeds (specsheet's first diagram), aspect-fit into its own
    cell without upscaling past its native resolution - mirrors the rule in excel_export.py,
    just centred by the table cell instead of manual EMU math."""
    diagrams = item.specsheet.diagrams if item.specsheet else None
    if not diagrams:
        return ""
    return _scaled_image(diagrams[0], _cell_image_mm(col_width_mm, IMAGE_MAX_MM))


# A reference picture is given a little more room than a product photo: it is usually a
# detail or a site shot rather than a catalogue cut-out, and is being read for what it shows.
REF_PICTURE_MAX_MM = 24


def _ref_pictures_flowable(item, col_width_mm=None):
    """This row's reference pictures, stacked in their cell and aspect-fitted to it - the same
    containment rule the .xlsx applies, done by the table cell instead of by hand."""
    pictures = item_ref_pictures(item)
    if not pictures:
        return ""
    width = _cell_image_mm(col_width_mm, REF_PICTURE_MAX_MM)
    drawn = [img for img in (_scaled_image(p, width) for p in pictures) if img]
    if not drawn:
        return ""
    if len(drawn) == 1:
        return drawn[0]
    stacked = []
    for n, picture in enumerate(drawn):
        if n:
            stacked.append(Spacer(1, 1.5 * mm))
        stacked.append(picture)
    return stacked


def _cover_flowables(boq):
    flow = []
    if LOGO_PATH.exists():
        # Same source image as the .xlsx Cover sheet (extracted from the sample template
        # itself) - scaled to a fixed width for the PDF's own page proportions and centered.
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
    # No rule under the letterhead. The sample template this cover was modelled on drew one,
    # but the logo carries its own visual weight and a second horizontal line directly beneath
    # it reads as a stray mark rather than structure - especially now that the rule which had
    # been baked into the top of logo.png is gone too, which used to bracket the logo between
    # two lines. Whitespace does the separating.
    flow.append(Spacer(1, 12 * mm))

    # The date stands alone, bold, with no "DATE :" label - matches the .xlsx Cover sheet;
    # every other field below it does use the "LABEL :" pattern.
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
        colWidths=[35 * mm, 120 * mm],
    )
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    flow.append(meta_table)
    flow.append(Spacer(1, 10 * mm))
    flow.append(Paragraph("Please find below our quotation for the required products:", STYLE_VALUE))
    flow.append(PageBreak())
    return flow


def _append_item_block_rows(table_data, span_commands, items, visible_keys, row_idx, s_no_idx, image_idx, widths_mm=None):
    """Appends `_item_blocks(items)` to `table_data`/`span_commands` (mutated in place)
    starting right after `row_idx` - the S.No/Image merge-across-accessory-sub-rows logic
    and embedded product photo placement that used to live inline in `_floor_flowables`'s
    per-room loop, extracted so it's reusable by a flat, no-floor-or-room item table too
    (boq_summary.py's BOQ Summary PDF). Returns the new row_idx."""
    for block in _item_blocks(items):
        start_row = row_idx + 1
        is_accessory_block = block[0].is_accessory
        for item in block:
            row_idx += 1
            # An accessory line's Dimension cell holds its own picture when it has one (see
            # excel_export.line_diagram_for), and is blank otherwise, as it always was.
            table_data.append([
                ("" if (k in ("image", "ref_images") and is_accessory_block) else
                 _line_diagram_flowable(item, (widths_mm or {}).get(k))
                 if (k == "dimensions" and is_accessory_block) else
                 _product_image_flowable(item, (widths_mm or {}).get(k)) if k == "image" else
                 _ref_pictures_flowable(item, (widths_mm or {}).get(k)) if k == "ref_images" else
                 (_line_diagram_flowable(item, (widths_mm or {}).get(k)) or VALUE_BY_KEY[k](item))
                 if k == "dimensions" else
                 VALUE_BY_KEY[k](item))
                for k in visible_keys
            ])
        end_row = row_idx
        if is_accessory_block and image_idx is not None:
            # An accessories section (including driver rows added via "+ Add Driver" -
            # they're accessory rows too) gets a plain "ACCESSORIES" label, never a product
            # photo, even if a driver row does have a specsheet/diagram linked - matching the
            # studio's own sample template. In the Image column, not S.No: S.No now carries
            # each row's own number, and Image is empty on an accessory row anyway.
            # Sized to the Image column rather than fixed at 8.5pt: it is one 11-character
            # word, so a column too narrow for it splits it as "ACCESSO / RIES" - the same
            # mid-word break fit_header exists to prevent on the headings.
            table_data[start_row][image_idx] = fit_header(
                "ACCESSORIES", (widths_mm or {}).get("image", 20), base_size=8.5)
        # Image merges down the block - one photograph for the product and its accessories -
        # but S.No does not, because every row has a number of its own to show.
        if end_row > start_row and image_idx is not None:
            span_commands.append(("SPAN", (image_idx, start_row), (image_idx, end_row)))
    return row_idx


# Prose columns: they wrap by design, and they are what gives up width when a value column
# needs more.
FLEX_KEYS = {"description", "remarks"}

# The columns whose contents are read as a single unit, and so must never be broken across
# lines: money, quantities, and the codes that identify a fitting. Everything else holds
# descriptive phrases that wrap at their spaces perfectly readably - and guaranteeing one line
# for those too would demand more width than A4 has, which is how Description got squeezed to
# nothing on the first attempt at this.
NO_BREAK_KEYS = {
    "s_no", "ref_no", "reference_number", "model_no", "qty", "unit", "wattage",
    "price", "discount", "discounted_price", "amount", "acc_net_price", "acc_amount",
    # A manufacturer name is a name: "InHouse" broken as "Litolu / x" is simply wrong, and it
    # appears on every row.
    "make",
}

# Every value cell in the BOQ table draws at this size (STYLE_BODY / STYLE_BODY_LEFT).
BODY_FONT_PT = 7


def boq_widths_mm(visible_keys, boq=None, items=None):
    """Column widths for the BOQ table, wide enough that no cell is split mid-word.

    Falls back to the plain scaled widths when there is nothing to measure, so a caller that
    has no BOQ to hand still gets a sane table."""
    if boq is None and items is None:
        return _scaled_widths_mm(visible_keys)
    rows = items if items is not None else [
        i for f in boq.floors for r in f.rooms for i in r.items
    ]
    widest = {k: 0.0 for k in visible_keys}
    for item in rows:
        for k in visible_keys:
            if k not in NO_BREAK_KEYS:
                continue
            getter = VALUE_BY_KEY.get(k)
            if not getter:
                continue
            para = getter(item)
            widest[k] = max(widest[k], widest_token_pt(getattr(para, "text", para),
                                                       size=BODY_FONT_PT))
    # "ACCESSORIES" is deliberately not measured here: it is written into the S.No cell only
    # after S.No and Image have been merged into one, so it has both columns' width to sit in.
    heads = {k: widest_token_pt(COLUMN_LABELS[k], "Helvetica-Bold", HEADING_READABLE_PT)
             for k in visible_keys}
    return fit_columns_mm(visible_keys, COL_WIDTHS_MM_BY_KEY, widest, USABLE_WIDTH_MM,
                          FLEX_KEYS, heading_pt=heads)


def _floor_flowables(floor, visible_keys, boq=None):
    flow = [Paragraph((floor.name or "Floor").upper(), STYLE_FLOOR)]
    n_cols = len(visible_keys)
    image_idx = visible_keys.index("image") if "image" in visible_keys else None
    s_no_idx = visible_keys.index("s_no") if "s_no" in visible_keys else None

    widths_mm = dict(zip(visible_keys, boq_widths_mm(visible_keys, boq)))
    table_data = [[fit_header(COLUMN_LABELS[k], widths_mm[k]) for k in visible_keys]]
    span_commands = []  # ("SPAN", (c1,r1), (c2,r2)) for room-name/remark banners and merged S.No/Image blocks
    shaded_rows = []     # row indices to shade like a banner (room name, remark)
    row_idx = 0  # header is row 0

    for room in floor.rooms:
        row_idx += 1
        table_data.append([_p(room.name, STYLE_ROOM_CELL)] + [""] * (n_cols - 1))
        span_commands.append(("SPAN", (0, row_idx), (n_cols - 1, row_idx)))
        shaded_rows.append(row_idx)

        row_idx = _append_item_block_rows(table_data, span_commands, room.items, visible_keys, row_idx, s_no_idx, image_idx, widths_mm)

        if room.remark:
            row_idx += 1
            table_data.append([_p(f"Remark: {room.remark}", STYLE_REMARK_CELL)] + [""] * (n_cols - 1))
            span_commands.append(("SPAN", (0, row_idx), (n_cols - 1, row_idx)))
            shaded_rows.append(row_idx)

    col_widths = [widths_mm[k] * mm for k in visible_keys]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), GREY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ] + span_commands + [("BACKGROUND", (0, r), (-1, r), LIGHT_GREY) for r in shaded_rows]
    table.setStyle(TableStyle(style))
    flow.append(table)
    return flow


def _xml_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _notes_flowables(boq):
    flow = [Paragraph("TERMS & CONDITIONS", STYLE_FLOOR)]
    # Matches the .xlsx NOTE sheet's own To:/Kind Attention: header - Kind Attention is
    # genuinely optional (the sample template's own saved BOQ left it blank too), so an
    # empty value just renders an empty line rather than needing to be hidden conditionally.
    flow.append(Paragraph(f"To : {boq.client or ''}", STYLE_LABEL))
    flow.append(Paragraph(f"Kind Attention : {boq.kind_attention or ''}", STYLE_LABEL))
    flow.append(Spacer(1, 4 * mm))
    # Same blank-line-as-paragraph-break, **bold**-as-inline-bold convention as
    # excel_export.py's _write_note_sheet/_rich_text_for_line - a blank line becomes a
    # Spacer instead of an empty Paragraph, and **...** spans become reportlab <b> tags
    # (only after XML-escaping the raw line, so a stray & or < in edited text can't
    # break the Paragraph's markup parsing).
    for line in (boq.notes_text or DEFAULT_NOTES_TEXT).split("\n"):
        line = line.rstrip()
        if not line:
            flow.append(Spacer(1, 2.5 * mm))
            continue
        html = BOLD_SPAN_RE.sub(r"<b>\1</b>", _xml_escape(line))
        flow.append(Paragraph(html, STYLE_NOTE_BODY))
    return flow


def export_boq_pdf(boq, out_path: Path, hidden_columns=None) -> Path:
    """hidden_columns: optional iterable of excel_export.COLUMN_KEYS entries to leave out of
    every floor's table - e.g. {"image"} so a client-facing export doesn't include product
    photos. Same key vocabulary as export_boq() so a caller can hide the same columns in both
    formats with one list."""
    _autofill_sno(boq)
    visible_keys = _visible_keys(hidden_columns, boq)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=PAGE_SIZE,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"BOQ - {boq.project_name}",
    )
    story = _cover_flowables(boq)
    for i, floor in enumerate(boq.floors):
        story.extend(_floor_flowables(floor, visible_keys, boq))
        if i < len(boq.floors) - 1:
            story.append(PageBreak())
    if boq.floors:
        story.append(PageBreak())
    story.extend(_notes_flowables(boq))
    doc.build(story)
    return out_path
