"""Generates a BOQ .xlsx matching the layout and cell-level styling of the
studio's sample template (SampleInputs/SampleBOQ): same header block, column
header row, floor-title and room-label banners, product/accessory rows with
S.No+Image cells merged across an item's accessory sub-rows, borders on every
table cell, and embedded, aspect-correct product photos - plus a Cover sheet
and the fixed terms/NOTE sheet.

Fonts, fills and column widths below were read directly off the sample
workbook's cell styles (openpyxl `cell.font` / `cell.fill` / `cell.column_dimensions`)
rather than guessed, including resolving the floor-title banner's theme color
(accent6, tint 0.6) from the workbook's theme XML.
"""
import datetime
import math
import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU, points_to_pixels
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

from .database import IMAGES_DIR
from . import accessories
from .excel_fit import first_column_clear_of, fit_heading_row, fit_rows

COMPANY_LINES = [
    "YOUR COMPANY NAME",
    "D-14/9 1st Floor, Okhla Industrial Area,",
    "Phase-1, New Delhi-110020.",
    "Tel.: 0091-11-41406716 Fax: 0091-11-4140671.",
]

# Extracted directly from the sample template's own Cover sheet (SampleInputs/SampleBOQ) -
# the real logo, not a redrawn approximation. Used on the Cover sheet only (see
# _write_cover_sheet); the per-floor header block and NOTE sheet keep the plain-text
# COMPANY_LINES letterhead, matching the sample template's own more compact treatment there.
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.png"

# Every export column, keyed - lets a caller hide any subset (e.g. "image", so client-facing
# exports don't have to include product photos) without hand-editing cell coordinates: every
# writer below looks up "which column number is X right now" from a dict built from whichever
# keys are actually visible, rather than a fixed literal column index.
# "reference_number" deliberately sits right after "image", not right after "s_no" - the
# accessory-block merge logic below (and pdf_export.py's own) merges the *entire* column
# range between "s_no" and "image" for an ACCESSORIES label, so those two have to stay
# adjacent or that merge silently swallows whatever column ends up sitting between them.
# "dimensions" sits immediately right of "image", matching the studio's own format. It is
# after "image" rather than between "s_no" and "image" for the merge reason above.
# "ref_images" sits right after the product photo: the two picture columns belong beside each
# other, and it is outside the s_no..image range the ACCESSORIES label merges across (see the
# note above), so it cannot be swallowed by that merge. A BOQ with no reference pictures on it
# never shows this column at all - see visible_boq_keys - so it costs nothing to anyone who
# does not use them.
COLUMN_KEYS = ["s_no", "image", "ref_images", "dimensions", "reference_number", "description", "finish", "wattage", "beam", "control", "make", "qty", "unit", "remarks"]
COLUMN_LABELS = {
    "s_no": "S. No", "image": "Image", "ref_images": "Ref. Images", "dimensions": "Dimension",
    # "Ref. No", not "Reference No." - the header row reads on one line, and the sample
    # format abbreviates it too.
    "reference_number": "Ref. No", "description": "Description*",
    "finish": "Finish", "wattage": "Wattage", "beam": "Beam", "control": "Control", "make": "Make",
    "qty": "Qty", "unit": "Unit", "remarks": "Remarks",
}
COL_WIDTHS_BY_KEY = {
    "s_no": 12.5, "image": 28.5, "ref_images": 24.0, "dimensions": 22.6, "reference_number": 14.6, "description": 49.4,
    "finish": 14.6, "wattage": 14.3,
    "beam": 12.1, "control": 15.1, "make": 14.9, "qty": 9.9, "unit": 8.4, "remarks": 16.3,
}

# colors lifted from the sample workbook's cell styles
HEADER_FILL = PatternFill("solid", fgColor="C0C0C0")     # indexed color 22 on the column-header row
FLOOR_FILL = PatternFill("solid", fgColor="FCD5B5")      # theme accent6 @ tint 0.6 on the floor-title row
ROOM_FILL = PatternFill("solid", fgColor="FFFF00")       # plain yellow on the room/area-label row
REMARK_FILL = PatternFill("solid", fgColor="F2F2F2")     # light grey for the whole-area remark row

THIN = Side(style="thin")
BORDER_ALL = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

FONT_COMPANY_BOLD = Font(name="Cambria", size=14, bold=True)
FONT_COMPANY = Font(name="Cambria", size=14, bold=False)
# Headings and totals are Arial (2026-10-12). The sample template set them in Swis721 Ex BT, a
# Bitstream font that neither the studio's computers nor its clients' have, so Excel was drawing
# a stand-in anyway - and a sheet can only be sized to its text when the text's font is known.
FONT_HEADER = Font(name="Arial", size=13, bold=True)
FONT_FLOOR = Font(name="Century Gothic", size=12, bold=True)
FONT_ROOM = Font(name="Century Gothic", size=12, bold=True)
FONT_CODE = Font(name="Century Gothic", size=12, bold=False)
FONT_BODY = Font(name="Arial", size=9, bold=False)
FONT_QTY = Font(name="Arial", size=10, bold=False)
FONT_REMARK = Font(name="Arial", size=9, italic=True, bold=False)
FONT_ACCESSORIES = Font(name="Century Gothic", size=11, bold=True)

CENTER_WRAP = Alignment(horizontal="center", vertical="center", wrap_text=True)
# The table's column headings. They wrap onto up to three lines (excel_fit.fit_heading_row)
# rather than widen the table: "Amount of Accessories (INR)" on one line would need a column
# twice the width of the figures under it.
FONT_HEADER_COMPACT = Font(name="Arial", size=9.5, bold=True)
LEFT_WRAP = Alignment(horizontal="left", vertical="center", wrap_text=True)
CENTER = Alignment(horizontal="center", vertical="center")

# The label under which a manufacturer's dimensioned line drawing is stored in
# Specsheet.diagrams. Today that is InHouse and only InHouse: all 140 of its specsheets carry a
# diagram with this label and no other make in the library has one, so the label alone is
# enough to identify a drawing - there is no make check here, and a second brand that starts
# publishing them would be picked up without any change.
LINE_DIAGRAM_LABEL = "Additional Diagram 2"


def line_diagram_for(item):
    """The picture to put in this row's Dimension cell, or None.

    None unless the row was explicitly marked for it (BOQItem.show_line_diagram): a drawing is
    a deliberate inclusion per row, not something every InHouse line silently acquires.

    Which picture is the row's choice (BOQItem.dimension_image) - a strip light's profile, say,
    extracted from its specsheet. Blank means the manufacturer's line drawing, as it always did,
    and so does a choice the specsheet no longer has: a re-extracted sheet can renumber its
    profiles, and a row should then show the drawing rather than nothing. Every export - BOQ and
    quotation, Excel and PDF, summarised or not - asks this one function, so they all agree."""
    if not getattr(item, "show_line_diagram", False):
        return None
    if getattr(item, "is_accessory", False):
        # An accessory line prints the picture its accessory had when the line was added
        # (models.AccessoryDetail) and nothing else - never a specsheet drawing, which is why
        # accessory lines that predate accessory pictures print exactly what they always did.
        name = accessories.line_picture(item)
        path = IMAGES_DIR / name if name else None
        return {"path": str(path), "label": "Accessory picture"} if path and path.exists() else None
    sheet = getattr(item, "specsheet", None)
    if sheet is None:
        return None
    diagrams = [d for d in (sheet.diagrams or []) if d.get("path")]
    wanted = str(getattr(item, "dimension_image", "") or "").strip().lower()
    if wanted:
        for d in diagrams:
            if str(d.get("label", "")).strip().lower() == wanted:
                return d
    for d in diagrams:
        if str(d.get("label", "")).strip().lower() == LINE_DIAGRAM_LABEL.lower():
            return d
    return None


# {key: (font, alignment)} for every text column - "image" is deliberately absent (it's a
# floating drawing object, never cell text)
STYLE_BY_KEY = {
    "s_no": (FONT_CODE, CENTER_WRAP), "reference_number": (FONT_CODE, CENTER_WRAP),
    "dimensions": (FONT_BODY, CENTER_WRAP),
    "description": (FONT_BODY, LEFT_WRAP),
    "finish": (FONT_BODY, CENTER_WRAP), "wattage": (FONT_BODY, CENTER_WRAP),
    "beam": (FONT_BODY, CENTER_WRAP), "control": (FONT_BODY, CENTER_WRAP),
    "make": (FONT_BODY, CENTER_WRAP), "qty": (FONT_QTY, CENTER), "unit": (FONT_QTY, CENTER),
    "remarks": (FONT_BODY, LEFT_WRAP),
}
# {key: item -> cell value} - same omission of "image"
VALUE_BY_KEY = {
    "s_no": lambda item: item.item_code or "",
    # Blank when a line drawing is going into this cell instead - text under a drawing is
    # the drawing's own dimensions repeated, badly.
    "dimensions": lambda item: "" if line_diagram_for(item) else (item.dimensions or ""),
    "reference_number": lambda item: item.reference_number or "",
    "description": lambda item: item.description or "",
    "finish": lambda item: item.finish or "",
    "wattage": lambda item: item.wattage or "",
    "beam": lambda item: item.beam or "",
    "control": lambda item: item.control or "",
    "make": lambda item: item.make or "",
    "qty": lambda item: item.qty or "",
    "unit": lambda item: item.unit or "",
    "remarks": lambda item: item.remarks or "",
}

# The NOTE page's terms + concluding signature - editable per-BOQ (BOQ.notes_text; blank
# means "use this default text" - see _write_note_sheet/_notes_flowables), unlike the fixed
# company letterhead (COMPANY_LINES), which is never part of what a user edits here. Plain
# text with two conventions, kept deliberately minimal:
#   - a blank line is a blank row/paragraph break (matches the sample template's own spacing
#     between sections - what you see in the edit box is what spacing you get, no separate
#     heading/section metadata needed)
#   - **bold** marks a bold span, whole-line or inline (e.g. only "M/S YOUR COMPANY
#     NAME" mid-sentence) - the one formatting control actually asked for
DEFAULT_NOTES_TEXT = """**TERMS OF SALE:**

**PRICE BASIS:**
Price in INR EX-Delhi Godown

**PRICE VALIDITY:**
Validity is 10 Days here after.

**PRICE INCLUSIONS:**
Luminaire Only

**PRICE EXCLUSIONS:**
Installation cost is Not Included.

**TAX:**
GST AS APPLICABLE.

**DELIVERY LEAD TIMES:**
6-8 Weeks from receipt of PO & Advance
DELIVERY LINE MAY VARY WITH BIS REGULATIONS

**DESPATCH:**
Freight in Buyer's Scope.
Freight as per actual

**PAYMENT TERMS:**
100% Advance Payment

**QUOTES VALIDITY:**
7 days only
Any deviation in exchange rate would lead to deviation in price

**WARRANTY:**
24 Months on luminaries only for manufacturing defects, Excluding Faulty/Incorrect Installation, Excluding Voltage & Frequency Fluctuations.
NO WARRANTY / GUARANTEE ON LAMPS (CFL/MH/Halogen).
IP68 water proof connectors to be used for exterior grade/wet area installations, warranty is null and void in case connectors are not used.

**SALES RETURNS:**
Order once placed cannot be cancelled unless agreed specifically in writing, in advance by us.
Material once delivered cannot be returned unless agreed specifically in writing, in advance by us.
Any Problems, defects, shortages, breakages, damages to the material MUST be informed in writing at the time of delivery at site, after which no claim to this effect will be valid on us.
In case the delivery material is found badly stored / overstacked / dumped haphazardly on site, any claims regarding breakages / damages will be treated as invalid / null and void.

**MATERIAL UNLOADING AT SITE IS IN BUYER'S SCOPE**

In eventuality of an order, you are requested to place it on **M/S YOUR COMPANY NAME**

**Trust that you will find the above offer comprehensive and looking forward to meeting you at the earliest towards closure of the above requirement.**

Thanks & Regards,
**YOUR COMPANY NAME**"""

BOLD_SPAN_RE = re.compile(r"\*\*(.+?)\*\*")


def _rich_text_for_line(line: str, font_name: str, size: float):
    """Turns "...text with **bold** in it..." into an openpyxl CellRichText mixing plain
    string segments (inherit the cell's own base font, set separately) and TextBlocks with
    an explicit bold InlineFont for each **...** span - true inline bold within one cell,
    not just a whole-line bold/not-bold choice. Returns a plain str unchanged when there's
    no bold marker at all, since a CellRichText of one plain segment is needless indirection."""
    parts = BOLD_SPAN_RE.split(line)
    if len(parts) == 1:
        return line
    segments = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:  # odd-indexed parts are the captured **bold** groups
            segments.append(TextBlock(InlineFont(rFont=font_name, sz=size, b=True), part))
        else:
            segments.append(part)
    return CellRichText(*segments) if segments else line

IMAGE_TARGET_WIDTH_PX = 100
IMAGE_CELL_PADDING_PX = 4  # margin so a centered image never touches the cell border
PX_TO_PT = 0.75
# Rows are sized to their text by excel_fit, which measures each cell at its own column's width.
# They were sized by a fixed 53 characters per line of description - right for the BOQ's
# Description column, and about 19 characters too many for the quotation's narrower one.


def _col_width_to_px(char_width, mdw=7):
    """Excel's own column-width-in-characters -> pixels formula (for the default
    Calibri 11 font, max digit width MDW=7)."""
    return int(((256 * char_width + int(128 / mdw)) / 256) * mdw)


LEFT = Alignment(horizontal="left", vertical="center")


def write_addressees(ws, client_name, kind_attention, row=7):
    """The "To:" and "Kind Attention:" lines of a letterhead, each name beside its label.

    The names go in the first column clear of "Kind Attention:" (2026-10-12). They were in column
    B, and column A is a narrow S. No. column, so the label ran under the name and printed as
    "Kind" - or, centred, lost its first letters off the edge of the sheet. Both names share one
    column, so they line up. The column widths must be set before this is called.

    Kind Attention is genuinely optional - the sample template's own saved BOQ left it blank - so
    an empty one simply prints its label alone."""
    value_col = first_column_clear_of(ws, "Kind Attention:", FONT_COMPANY_BOLD)
    for r, label, value in ((row, "To:", client_name), (row + 1, "Kind Attention:", kind_attention)):
        c = ws.cell(row=r, column=1, value=label)
        c.font = FONT_COMPANY_BOLD
        c.alignment = LEFT
        c = ws.cell(row=r, column=value_col, value=value or "")
        c.font = FONT_COMPANY_BOLD
        c.alignment = LEFT


def _write_header_block(ws, client_name, kind_attention=""):
    # Left-aligned: column A is narrow, and a line centred in it spills out of both sides - the
    # left half off the edge of the sheet.
    ws["A1"] = "From:"
    ws["A1"].font = FONT_COMPANY_BOLD
    ws["A1"].alignment = LEFT
    ws["A2"] = COMPANY_LINES[0]
    ws["A2"].font = FONT_COMPANY_BOLD
    ws["A2"].alignment = LEFT
    for i, line in enumerate(COMPANY_LINES[1:], start=3):
        c = ws.cell(row=i, column=1, value=line)
        c.font = FONT_COMPANY
        c.alignment = LEFT
    write_addressees(ws, client_name, kind_attention)
    ws["A10"] = "Following your kind request, please find our best quotation for the required products:"
    ws["A10"].font = FONT_COMPANY
    ws["A10"].alignment = LEFT
    for r in range(1, 11):
        ws.row_dimensions[r].height = 17.4


def boq_has_ref_pictures(boq):
    """Whether anything in this BOQ has a reference picture on it.

    The Ref. Images column is only drawn for a BOQ that uses them. Every BOQ made before this
    existed, and every one whose author simply does not want pictures, exports exactly as it
    did - rather than gaining an empty column they then have to go and hide.
    """
    if boq is None:
        return False
    if not getattr(boq, "ref_images", None):
        return False
    return any((i.ref_image_ids or [])
               for f in boq.floors for r in f.rooms for i in r.items)


def visible_boq_keys(hidden_columns, boq=None):
    """The BOQ export's columns, in order, after hiding."""
    hidden = set(hidden_columns or [])
    if not boq_has_ref_pictures(boq):
        hidden.add("ref_images")
    return [k for k in COLUMN_KEYS if k not in hidden]


def _visible_columns(hidden_columns, boq=None):
    """Ordered (key, col_number) pairs for whichever columns weren't hidden - the single
    source of truth every writer below uses instead of a fixed literal column index, so
    hiding any column (e.g. "image" for a client who doesn't need product photos) just
    means it's absent from this list rather than needing its own removal logic per writer."""
    return {k: i for i, k in enumerate(visible_boq_keys(hidden_columns, boq), start=1)}


def _write_columns_row(ws, row, key_to_col):
    """The column headings. Each is broken onto as few lines as fit its column, at the break
    that leaves the lines most even, and the row is as tall as the longest (excel_fit). They
    were kept to one line, which cut off every heading longer than its column once the money
    headings gained their "(INR)". The column widths must be set before this is called."""
    for key, col_idx in key_to_col.items():
        c = ws.cell(row=row, column=col_idx, value=COLUMN_LABELS[key])
        c.font = FONT_HEADER_COMPACT
        c.fill = HEADER_FILL
        c.alignment = CENTER_WRAP
        c.border = BORDER_ALL
    fit_heading_row(ws, row, minimum=21)
    return row + 1


def _apply_col_widths(ws, key_to_col):
    for key, col_idx in key_to_col.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = COL_WIDTHS_BY_KEY[key]


def _autofill_sno(boq):
    """Fills in a plain running number (1, 2, 3, ...) for any product row that has no S.No
    of its own typed in yet - a single count across the whole BOQ in the order rows are
    already displayed (floor by floor, room by room), advancing once per row including
    accessory, driver and power-supply ones.

    Every row is numbered, accessories included (2026-09-04). A position was always reserved
    for them - which is why products ran 1, 2, 7, 10, 13 rather than 1, 2, 3, 4, 5 - but the
    number was not written, so a driver or power supply reached the document with an empty
    S.No and nothing to refer to it by when ordering. Writing the reserved number changes
    nothing that already had one; it only fills the blanks.

    Shared by every *detailed* export (excel_export/pdf_export/quotation_export/
    quotation_pdf_export - called once at the top of each) - the Summary exports don't need
    this, they already synthesize their own running item_code for their aggregated rows
    (see boq_summary.py's summarized_items()).

    Deliberately in-memory only, on the already-loaded ORM objects, for the lifetime of
    building this one export file - never committed to the database. A manually-typed S.No
    is never touched (only genuinely blank/whitespace-only ones are filled), and since
    nothing is persisted, the numbers simply reflect the BOQ's current item order fresh
    every time it's exported, the same way this app treats every other export-time
    computation (Excel/PDF export doesn't otherwise change what's stored)."""
    counter = 0
    for floor in boq.floors:
        for room in floor.rooms:
            for item in room.items:
                counter += 1
                if not (item.item_code or "").strip():
                    item.item_code = str(counter)


def _item_blocks(items):
    """Groups a room's items into merge-cell blocks: every product gets its own block,
    and consecutive accessory/driver rows (Accessory Row, Add Driver - anything with
    is_accessory=True) form their own separate block(s), never attached to whatever
    product happens to precede them. Matches the studio's actual sample template: an
    accessories section gets one merged "ACCESSORIES" label of its own (see
    _write_floor_sheet), not the preceding product's photo stretched down over rows that
    aren't that product. A driver added right after other accessories still lands in the
    same accessories block (same merged label) since both are consecutive is_accessory
    rows - only a non-accessory product forces a new block."""
    blocks = []
    for item in items:
        if blocks and blocks[-1][0].is_accessory and item.is_accessory:
            blocks[-1].append(item)
        else:
            blocks.append([item])
    return blocks


def _write_item_block_rows(ws, items, key_to_col, start_row):
    """Writes `_item_blocks(items)` as table rows starting at `start_row` - the S.No/Image
    merge-across-accessory-sub-rows logic and embedded product-photo placement that used to
    live inline in `_write_floor_sheet`'s per-room loop, extracted so the same intricate
    image-anchoring math (comments below) exists in exactly one place and is reusable by
    anything that needs a flat, no-floor-or-room item table too (boq_summary.py's BOQ
    Summary sheet). Returns the row index directly after the last row written."""
    num_cols = len(key_to_col)
    row = start_row
    for block in _item_blocks(items):
        start_row = row
        for item in block:
            for key, col in key_to_col.items():
                # Both picture columns hold a picture, not text - they are filled in below.
                if key in ("image", "ref_images"):
                    continue
                c = ws.cell(row=row, column=col, value=VALUE_BY_KEY[key](item))
                font, alignment = STYLE_BY_KEY[key]
                c.font = font
                c.alignment = alignment
            for col in range(1, num_cols + 1):
                ws.cell(row=row, column=col).border = BORDER_ALL

            # Tall enough for every cell's text at its own column's width - a long Finish or
            # Remarks clips as surely as a long description. Before the pictures, which grow
            # the rows further where they need to.
            fit_rows(ws, row)
            row += 1
        end_row = row - 1

        is_accessory_block = block[0].is_accessory
        image_col_for_block = key_to_col.get("image")

        # S.No is never merged: every row carries its own number now, including each accessory
        # under a product, so there is nothing to merge it into. Image still merges down the
        # block, because that genuinely is one photograph for the product and its accessories.
        if image_col_for_block is not None:
            if len(block) > 1:
                ws.merge_cells(start_row=start_row, start_column=image_col_for_block,
                               end_row=end_row, end_column=image_col_for_block)
            for r in range(start_row, end_row + 1):
                ws.cell(row=r, column=image_col_for_block).border = BORDER_ALL

        if is_accessory_block and image_col_for_block is not None:
            # An accessories section (including driver rows added via "+ Add Driver" -
            # they're accessory rows too) gets a plain "ACCESSORIES" label, never a product
            # photo, even if a driver row does have a specsheet/diagram linked - matching the
            # studio's own sample template. It sits in Image rather than S.No because S.No now
            # holds each row's number; Image is empty on an accessory row in any case, and is
            # wide enough for an 11-character word that would not fit a column sized for short
            # codes like "CRA2.2".
            c = ws.cell(row=start_row, column=image_col_for_block, value="ACCESSORIES")
            c.font = FONT_ACCESSORIES
            c.alignment = CENTER_WRAP

        first_item = block[0]
        is_product_block = not is_accessory_block and first_item.specsheet is not None

        # The product photo, and - for a row marked for it - the manufacturer's dimensioned
        # line drawing in the Dimension cell beside it. Both go through the same placement,
        # and the photo goes first: _place_diagram grows the block to fit what it is given,
        # so the drawing sizes itself against rows the photo has already stretched rather
        # than the two fighting over the height.
        image_col = key_to_col.get("image")
        diagrams = first_item.specsheet.diagrams if (image_col and is_product_block) else None
        if diagrams:
            _place_diagram(ws, diagrams[0], image_col, COL_WIDTHS_BY_KEY["image"],
                           start_row, end_row, len(block))

        # The reference pictures for this product, in their own column, merged down the block
        # exactly as the photo is: they describe the product, not each accessory under it.
        pics_col = key_to_col.get("ref_images")
        if pics_col is not None:
            if len(block) > 1:
                ws.merge_cells(start_row=start_row, start_column=pics_col,
                               end_row=end_row, end_column=pics_col)
            for r in range(start_row, end_row + 1):
                ws.cell(row=r, column=pics_col).border = BORDER_ALL
            pictures = item_ref_pictures(first_item)
            if pictures:
                _place_pictures(ws, pictures, pics_col, COL_WIDTHS_BY_KEY["ref_images"],
                                start_row, end_row)

        dim_col = key_to_col.get("dimensions")
        if dim_col and is_product_block:
            drawing = line_diagram_for(first_item)
            if drawing:
                _place_diagram(ws, drawing, dim_col, COL_WIDTHS_BY_KEY["dimensions"],
                               start_row, end_row, len(block))
        if dim_col and is_accessory_block:
            # Each accessory line's own picture, in its own Dimension cell: the Image column of
            # an accessory block is the one merged ACCESSORIES label, so this is the line's cell.
            for offset, line in enumerate(block):
                drawing = line_diagram_for(line)
                if drawing:
                    r = start_row + offset
                    _place_diagram(ws, drawing, dim_col, COL_WIDTHS_BY_KEY["dimensions"], r, r, 1)
    return row


def _ref_pictures(item, images_by_id):
    out = []
    for image_id in (item.ref_image_ids or []):
        im = images_by_id.get(image_id)
        if im:
            out.append({"path": str(IMAGES_DIR / im.filename), "label": im.label or ""})
    return out


def item_ref_pictures(item):
    """The reference pictures attached to one row, ready to place.

    Looked up through the BOQ the row belongs to rather than handed down from the caller: the
    item tables are written by several modules (floors, the BOQ Summary, the quotation), and
    an item already knows its own room, floor and BOQ, so none of them needs to learn about
    pictures to pass them along.
    """
    if not (item.ref_image_ids or []):
        return []
    boq = getattr(getattr(getattr(item, "room", None), "floor", None), "boq", None)
    if boq is None:
        return []
    return _ref_pictures(item, {im.id: im for im in boq.ref_images})


def _place_pictures(ws, pictures, col, width_chars, start_row, end_row):
    """Every reference picture for one product, stacked down its merged cell.

    A single picture is left to _place_diagram, which centres it and grows the rows to fit.
    Several are stacked one above another here instead: the block is usually one or two rows
    tall, so slicing the rows between them would leave a picture nothing to sit in, and
    handing them all to _place_diagram would draw them on top of one another. The rows are
    grown once to hold the whole stack, then each picture is anchored at its own offset down
    the block - two pictures read as two pictures, whatever the row happens to be.
    Whatever the count, nothing is allowed outside the cell: the rows are first grown to hold
    the stack at a readable size, and then the whole stack is measured against what the cell
    actually ended up being and scaled down if it still does not fit. A picture that spills
    over the border onto the row beneath is worse than a smaller picture.
    """
    pictures = [p for p in pictures if p.get("path") and Path(p["path"]).exists()]
    if not pictures:
        return
    try:
        cell_w_px = _col_width_to_px(width_chars)
        avail_w = max(1.0, cell_w_px - 2 * IMAGE_CELL_PADDING_PX)
        gap_px = IMAGE_CELL_PADDING_PX
        rows = max(1, end_row - start_row + 1)

        # 1) each picture at the width of the cell, never blown up past its own resolution
        drawn = []
        for picture in pictures:
            img = XLImage(picture["path"])
            native_w = picture.get("width") or img.width
            native_h = picture.get("height") or img.height
            scale = min(avail_w / native_w, 1.0)
            drawn.append([img, native_w * scale, native_h * scale])

        # 2) grow the rows to hold that stack, if they are shorter than it
        wanted_px = sum(h for _, _, h in drawn) + gap_px * (len(drawn) + 1)
        have_pt = sum(ws.row_dimensions[r].height or 20 for r in range(start_row, end_row + 1))
        if have_pt < wanted_px * PX_TO_PT:
            extra = (wanted_px * PX_TO_PT - have_pt) / rows
            for r in range(start_row, end_row + 1):
                ws.row_dimensions[r].height = (ws.row_dimensions[r].height or 20) + extra

        # 3) measure the cell as it now is, and shrink the stack if it is still too tall - the
        #    rows may have been fixed taller elsewhere, or a row cap may have limited the growth
        cell_h_px = sum(points_to_pixels(ws.row_dimensions[r].height or 20)
                        for r in range(start_row, end_row + 1))
        avail_h = max(1.0, cell_h_px - 2 * gap_px)
        stack_h = sum(h for _, _, h in drawn) + gap_px * (len(drawn) - 1)
        if stack_h > avail_h:
            shrink = avail_h / stack_h
            for item in drawn:
                item[1] *= shrink
                item[2] *= shrink
            stack_h = avail_h

        # 4) centred as a group, each picture centred across the cell's width
        offset_y = max(gap_px, (cell_h_px - stack_h) / 2)
        for img, w, h in drawn:
            marker = AnchorMarker(col=col - 1, colOff=pixels_to_EMU(max(0.0, (cell_w_px - w) / 2)),
                                  row=start_row - 1, rowOff=pixels_to_EMU(offset_y))
            img.anchor = OneCellAnchor(
                _from=marker, ext=XDRPositiveSize2D(cx=pixels_to_EMU(w), cy=pixels_to_EMU(h)))
            ws.add_image(img)
            offset_y += h + gap_px
    except Exception:
        # Same rule as _place_diagram: a picture that cannot be read costs the document that
        # picture, never the document.
        pass


def _place_diagram(ws, diagram, col, col_width_chars, start_row, end_row, block_len):
    """Centre one diagram inside the cell block (`col` x rows start_row..end_row), growing
    those rows if they are too short for it.

    Silent on failure by design: a missing or unreadable image file costs the export one
    picture, and refusing to produce the BOQ over it would be the worse outcome."""
    path = diagram.get("path")
    if not path or not Path(path).exists():
        return
    try:
        img = XLImage(path)
        native_w = diagram.get("width") or img.width
        native_h = diagram.get("height") or img.height
        aspect = (native_h / native_w) if native_w else 1

        # 1) ensure the row block is at least tall enough for a normal thumbnail,
        #    same baseline size regardless of how short the description happens to be
        target_h = IMAGE_TARGET_WIDTH_PX * aspect
        needed_pt = target_h * PX_TO_PT + IMAGE_CELL_PADDING_PX * 2 * PX_TO_PT
        current_total = sum(ws.row_dimensions[r].height or 20 for r in range(start_row, end_row + 1))
        if current_total < needed_pt:
            extra = (needed_pt - current_total) / block_len
            for r in range(start_row, end_row + 1):
                ws.row_dimensions[r].height = (ws.row_dimensions[r].height or 20) + extra

        # 2) fit the image within the FINAL cell bounds, never upscaling past its own
        #    native resolution (so it's never blown up bigger/blurrier than the source),
        #    then center it - the cell is this column's fixed width x this block's row heights
        cell_w_px = _col_width_to_px(col_width_chars)
        cell_h_px = sum(points_to_pixels(ws.row_dimensions[r].height or 20) for r in range(start_row, end_row + 1))
        avail_w = cell_w_px - 2 * IMAGE_CELL_PADDING_PX
        avail_h = cell_h_px - 2 * IMAGE_CELL_PADDING_PX
        scale = min(avail_w / native_w, avail_h / native_h, 1.0)
        final_w, final_h = native_w * scale, native_h * scale

        offset_x = (cell_w_px - final_w) / 2
        offset_y = (cell_h_px - final_h) / 2

        marker = AnchorMarker(col=col - 1, colOff=pixels_to_EMU(offset_x),
                              row=start_row - 1, rowOff=pixels_to_EMU(offset_y))
        img.anchor = OneCellAnchor(_from=marker,
                                   ext=XDRPositiveSize2D(cx=pixels_to_EMU(final_w), cy=pixels_to_EMU(final_h)))
        ws.add_image(img)
    except Exception:
        pass


# Banner rows span the whole table. A name written into the first cell alone wraps inside that
# one narrow column - "BASEMENT FLOOR" came out as two clipped lines in the BOQ's S. No column,
# and a letter or two per line in the quotation's. Left-aligned and indented one step, so it
# reads as a heading over the rows beneath it rather than a title floating mid-page.
BANNER_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1)


def write_banner_row(ws, row, n_cols, text, font, fill, height):
    """One full-width heading row - a floor, an area, or an area's remark - at least `height`
    tall, and taller if its text wraps."""
    if n_cols > 1:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=n_cols)
    c = ws.cell(row=row, column=1, value=text)
    c.font = font
    c.alignment = BANNER_ALIGN
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.border = BORDER_ALL
        cell.fill = fill
    ws.row_dimensions[row].height = height
    fit_rows(ws, row)


def _write_floor_sheet(wb, floor, client_name, key_to_col, kind_attention=""):
    sheet_name = (floor.name or "Floor")[:31]
    ws = wb.create_sheet(sheet_name)
    _apply_col_widths(ws, key_to_col)
    _write_header_block(ws, client_name, kind_attention)
    num_cols = len(key_to_col)

    row = 12
    row = _write_columns_row(ws, row, key_to_col)

    write_banner_row(ws, row, num_cols, (floor.name or "").upper(), FONT_FLOOR, FLOOR_FILL, 24)
    row += 1

    for room in floor.rooms:
        write_banner_row(ws, row, num_cols, room.name or "", FONT_ROOM, ROOM_FILL, 24)
        row += 1

        row = _write_item_block_rows(ws, room.items, key_to_col, row)

        if room.remark:
            write_banner_row(ws, row, num_cols, f"Remark: {room.remark}", FONT_REMARK,
                             REMARK_FILL, 20)
            row += 1
    return ws


def _format_display_date(raw: str) -> str:
    """"2026-03-31" -> "March 31, 2026" - matches the sample template's Cover sheet date
    styling. Falls back to the raw string as-is if it isn't a plain ISO date (e.g. already
    free text), rather than raising on a value the rest of the app treats as freeform."""
    try:
        d = datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return raw or ""
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _write_cover_sheet(wb, boq):
    ws = wb.create_sheet("Cover")

    # Logo, matching the sample template's own Cover sheet exactly (extracted from its
    # embedded image rather than redrawn) - native 1338x353px, scaled to a fixed on-page
    # width and anchored a couple rows down/right from the corner, same as the sample.
    if LOGO_PATH.exists():
        img = XLImage(str(LOGO_PATH))
        native_w, native_h = img.width, img.height
        target_w = 480
        target_h = target_w * (native_h / native_w)
        marker = AnchorMarker(col=1, colOff=pixels_to_EMU(4), row=0, rowOff=pixels_to_EMU(4))
        img.anchor = OneCellAnchor(_from=marker, ext=XDRPositiveSize2D(cx=pixels_to_EMU(target_w), cy=pixels_to_EMU(target_h)))
        ws.add_image(img)
        # (target_h + a small margin), converted from px to the points row heights are set
        # in, split across rows of a fixed 20pt height each
        logo_rows_needed = math.ceil((target_h + 8) * PX_TO_PT / 20)
    else:
        logo_rows_needed = 0
    for r in range(1, max(logo_rows_needed, 1) + 1):
        ws.row_dimensions[r].height = 20

    # No rule under the letterhead - the same treatment as the PDF cover. The logo separates
    # itself; a line immediately beneath it (with the one that used to be baked into the top
    # of logo.png) bracketed the letterhead in a way that read as a printing artefact. The
    # row is kept as blank spacing so the layout below it does not shift up.
    spacer_row = logo_rows_needed + 1
    ws.row_dimensions[spacer_row].height = 6

    row = spacer_row + 2
    # The date stands alone, bold, with no "DATE :" label - matches the sample; every other
    # field below it does use the "LABEL :" pattern.
    ws.cell(row=row, column=1, value=_format_display_date(boq.boq_date)).font = FONT_COMPANY_BOLD
    row += 1
    labels = [
        ("PROJECT", boq.project_name or ""),
        ("PROJECT CODE", boq.project_code or ""),
        ("AREA", boq.area or ""),
        ("CLIENT", boq.client or ""),
    ]
    for label, value in labels:
        ws.cell(row=row, column=1, value=label).font = FONT_COMPANY_BOLD
        ws.cell(row=row, column=3, value=" :").font = FONT_COMPANY
        ws.cell(row=row, column=4, value=value).font = FONT_COMPANY
        row += 1
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["D"].width = 40
    return ws


def _write_note_sheet(wb, client_name, kind_attention="", notes_text=None):
    ws = wb.create_sheet("NOTE")
    ws["B2"] = "From: - "
    ws["B2"].font = FONT_COMPANY_BOLD
    for i, line in enumerate(COMPANY_LINES):
        c = ws.cell(row=2 + i, column=3, value=line)
        c.font = FONT_COMPANY_BOLD if i == 0 else FONT_COMPANY
    ws["B7"] = "To: - "
    ws["B7"].font = FONT_COMPANY_BOLD
    ws["C7"] = client_name or ""
    ws["C7"].font = FONT_COMPANY_BOLD
    # Matches the sample template's NOTE sheet exactly (its own "Kind Attention:" row sits at
    # B8/C8, directly under "To:") - genuinely optional, same as the floor-header block above.
    ws["B8"] = "Kind Attention: - "
    ws["B8"].font = FONT_COMPANY_BOLD
    ws["C8"] = kind_attention or ""
    ws["C8"].font = FONT_COMPANY_BOLD

    note_font_name, note_size = "Calibri", 14
    row = 10
    for line in (notes_text or DEFAULT_NOTES_TEXT).split("\n"):
        line = line.rstrip()
        if line:
            c = ws.cell(row=row, column=2, value=_rich_text_for_line(line, note_font_name, note_size))
            c.font = Font(name=note_font_name, size=note_size, bold=False)
        row += 1
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 90
    return ws


def export_boq(boq, out_path: Path, hidden_columns=None) -> Path:
    """hidden_columns: optional iterable of COLUMN_KEYS entries to leave out of every floor
    sheet - e.g. {"image"} so a client-facing export doesn't include product photos. Applies
    uniformly across every floor/room; the Cover and NOTE sheets don't have a column layout
    tied to product data, so they're unaffected."""
    _autofill_sno(boq)
    key_to_col = _visible_columns(hidden_columns, boq)
    wb = Workbook()
    wb.remove(wb.active)
    _write_cover_sheet(wb, boq)
    for floor in boq.floors:
        _write_floor_sheet(wb, floor, boq.client, key_to_col, boq.kind_attention)
    _write_note_sheet(wb, boq.client, boq.kind_attention, boq.notes_text)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
