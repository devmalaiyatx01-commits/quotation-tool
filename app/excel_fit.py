"""Sizing an exported sheet's rows and columns to what they hold, so it opens tidy - no heading
cut off, no description clipped at the bottom, no total showing "#####" - and nobody has to drag
a column or a row before sending it (2026-10-12).

openpyxl writes widths and heights but cannot measure text, and Excel does not refit a row whose
height has been set - which every row here has, because the pictures need it. So the text is
measured here, with the font metrics reportlab ships (Arial's widths are Helvetica's, by design),
and each family's ratio to them taken from Excel itself (see `_FAMILIES`).

Everything here only ever makes a row taller or a column wider than the writer asked for. The
writers' own sizes are the floor, so a picture that has stretched a row keeps its room.
"""
import math

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter
from reportlab.pdfbase.pdfmetrics import stringWidth

# Excel counts a column's width in characters of the workbook's default font, Calibri 11, whose
# digits are 7 px wide; this is Excel's own formula for turning that into pixels.
MAX_DIGIT_PX = 7
# What a cell keeps clear of its borders, each side, before text starts.
CELL_PADDING_PX = 3
PX_TO_PT = 0.75

# Each family Excel draws, as a reportlab standard font and how much wider it sets than that font
# at the same size, from Excel's own AutoFit of 121 strings in every font the exports use
# (2026-10-12): Calibri a little narrower than Helvetica, Century Gothic about 15% wider, Cambria
# about 28% wider than Times. Arial is the one measured closely, because it is the one that wraps
# - the table's text, headings and figures. The others set short labels, banners and the
# letterhead, and their ratios already hold the screen rounding below, so they measure a little
# wide; in those places a little wide costs nothing.
_FAMILIES = {
    "arial": ("Helvetica", 1.0),
    "calibri": ("Helvetica", 0.95),
    "century gothic": ("Helvetica", 1.15),
    "cambria": ("Times-Roman", 1.28),
}
# A family not listed is measured as a wide sans serif: too wide costs a little white space, too
# narrow costs the end of somebody's text.
_UNKNOWN_FAMILY = ("Helvetica", 1.15)

# Excel draws text a little wider than its metrics at small sizes - by 1 + k / size - and how much
# that matters depends on what is being decided, so there are two values, both measured against
# Excel (2026-10-12):
# - WRAP_K, where Excel wraps text, which decides how tall a row must be. At 1 + 0.5 / size, 180
#   real descriptions wrapped at the exports' three description widths all came out at the
#   number of lines Excel used, or one more - never one fewer. At 1.0 three came out a line short.
# - HEADING_K, whether a heading's line fits its column without Excel breaking a word in half.
#   Kept at Excel's own AutoFit of the headings, which runs wider: a heading that misses by a
#   pixel loses half a word to the next line, where a row a line too tall only shows white space.
WRAP_K = 0.5
HEADING_K = 1.4


def _screen_rounding(size, k):
    return 1.0 + k / size


# How tall Excel makes one line of each family, as a multiple of the font size - rounded up to
# whole pixels, as Excel does (Arial 9 gives 12 pt lines, Arial 13 16.5 pt, Calibri 11 15 pt).
_LINE_HEIGHT = {"arial": 1.33, "calibri": 1.364, "century gothic": 1.3, "cambria": 1.29}
# The breathing room a row keeps above and below its text. 6 pt is what the item rows always had.
ROW_PADDING_PT = 6
# Kept clear at the end of a heading's line, so a heading that only just fits is not one Excel
# draws a pixel or two wider and wraps again. With the cell's own padding that is 9 px in all,
# which covered the widest gap between Excel's AutoFit and the measurement for every heading the
# exports print (8.5 px, on short ones such as "Price").
HEADING_MARGIN_PT = 1.5
# See figure_px.
FIGURE_PADDING_PX = 12
# The shortest line a heading may be broken into.
MIN_HEADING_LINE_CHARS = 3


def _family(font):
    return ((font.name if font is not None else None) or "Calibri").strip().lower()


def _size(font):
    return float((font.sz if font is not None else None) or 11)


def text_pt(text, font, k=HEADING_K):
    """How wide `text` sets in `font`, in points, as Excel draws it - see WRAP_K and HEADING_K for
    `k`; 0 gives the font's plain metric width (see figure_px)."""
    if not text:
        return 0.0
    base, ratio = _FAMILIES.get(_family(font), _UNKNOWN_FAMILY)
    bold = bool(font is not None and font.b)
    face = {"Helvetica": "Helvetica-Bold", "Times-Roman": "Times-Bold"}[base] if bold else base
    width = 0.0
    for ch in str(text):
        try:
            width += stringWidth(ch, face, _size(font))
        except Exception:
            # A character the standard fonts do not carry (an em dash, a rupee sign): counted as
            # a wide one rather than skipped.
            width += 0.6 * _size(font)
    return width * ratio * _screen_rounding(_size(font), k)


def figure_px(text, font):
    """The narrowest column, in pixels, that shows the figure `text` rather than "#####".

    Measured separately from words: Excel sets digits on whole pixels at their metric width, so
    the screen rounding that words need would make every money column a character too wide.
    Plain metrics plus 12 px matched or just exceeded Excel's own AutoFit for every amount,
    total and percentage the exports print, in Arial 10 and 13 and Calibri 11 and 12."""
    return text_pt(text, font, k=0) / PX_TO_PT + FIGURE_PADDING_PX


def line_pt(font):
    """The height of one line of text in `font`, as Excel lays it out, in points."""
    px = _size(font) * _LINE_HEIGHT.get(_family(font), 1.33) / PX_TO_PT
    return math.ceil(px) * PX_TO_PT


def col_px(width_chars):
    """A column width in Excel's character units, as pixels on screen."""
    return int(((256 * width_chars + int(128 / MAX_DIGIT_PX)) / 256) * MAX_DIGIT_PX)


def chars_for_px(px):
    """The narrowest column width, in Excel's character units, that is at least `px` wide."""
    width = max(0.0, (px - 5) / MAX_DIGIT_PX)
    width = math.ceil(width * 10) / 10
    while col_px(width) < px:
        width += 0.1
    return round(width, 1)


def text_room_pt(width_px):
    """The width a cell of `width_px` pixels gives its text, in points."""
    return max(1.0, (width_px - 2 * CELL_PADDING_PX - 1) * PX_TO_PT)


# A column no writer sized: Excel's default, 8.43 characters as Excel shows it, stored as 9.140625.
DEFAULT_COLUMN_WIDTH = 9.140625


def column_px(ws, col):
    width = ws.column_dimensions[get_column_letter(col)].width
    return col_px(width if width else DEFAULT_COLUMN_WIDTH)


def wrapped_lines(text, font, room_pt):
    """How many lines Excel needs for `text` wrapped in `room_pt` points: it breaks at spaces and
    at the line breaks written into the text, and splits a word too long for a line by itself."""
    if text is None or text == "":
        return 1
    space = text_pt(" ", font, k=WRAP_K)
    total = 0
    for paragraph in str(text).split("\n"):
        lines, used = 1, 0.0
        # Spaces at the end of a line take no room - Excel lets them hang past the edge - so a
        # description typed with a space before its line break does not gain a phantom line.
        for word in paragraph.rstrip().split(" "):
            if not word:
                used += space if used else 0.0
                continue
            width = text_pt(word, font, k=WRAP_K)
            if used and used + space + width <= room_pt:
                used += space + width
                continue
            if used:
                lines += 1
            if width <= room_pt:
                used = width
                continue
            # Longer than a whole line: Excel carries the rest of the word on to the next ones.
            used = 0.0
            for ch in word:
                w = text_pt(ch, font, k=WRAP_K)
                if used + w > room_pt and used:
                    lines += 1
                    used = 0.0
                used += w
        total += lines
    return total


def _merged_spans(ws):
    """{(row, col) of a merged range's top-left: (last row, last col)}, and every other cell a
    merged range covers - those carry no text of their own."""
    spans, covered = {}, set()
    for rng in ws.merged_cells.ranges:
        spans[(rng.min_row, rng.min_col)] = (rng.max_row, rng.max_col)
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                if (r, c) != (rng.min_row, rng.min_col):
                    covered.add((r, c))
    return spans, covered


def _cells_by_row(ws, first_row, last_row):
    """{row: [its cells, left to right]} for the cells already written in those rows. Read from
    the sheet's own store: walking a row through ws[row] would create an empty cell in every
    column it passes, and those would be written into the file."""
    rows = {}
    for (r, c), cell in ws._cells.items():
        if first_row <= r <= last_row:
            rows.setdefault(r, []).append((c, cell))
    return {r: [cell for _, cell in sorted(cells, key=lambda x: x[0])] for r, cells in rows.items()}


def _shown_text(cell):
    """The text a cell shows, for measuring - None for a number or a formula, which are sized as
    figures (fit_number_columns), never wrapped."""
    value = cell.value
    if value is None or isinstance(value, (int, float)):
        return None
    value = str(value)
    if value.startswith("="):
        return None
    return value


def _text_height_pt(cell, width_px):
    text = _shown_text(cell)
    if text is None:
        return 0.0
    wraps = bool(cell.alignment is not None and cell.alignment.wrap_text)
    lines = wrapped_lines(text, cell.font, text_room_pt(width_px)) if wraps else 1
    return lines * line_pt(cell.font) + ROW_PADDING_PT


def fit_rows(ws, first_row, last_row=None, minimum=20):
    """Makes each row from `first_row` to `last_row` tall enough for the text in it - every
    column, each wrapped at its own width - and a cell merged down several rows tall enough
    across them. Never lowers a row: a height already set (a picture's, a writer's) is the floor.
    """
    last_row = last_row or first_row
    spans, covered = _merged_spans(ws)
    cells = _cells_by_row(ws, first_row, last_row)
    widths = {}

    def span_px(c1, c2):
        return sum(widths.setdefault(c, column_px(ws, c)) for c in range(c1, c2 + 1))

    tall = []          # (first row, last row, height wanted) for cells merged down
    for r in range(first_row, last_row + 1):
        need = minimum
        for cell in cells.get(r, ()):
            c = cell.column
            if (r, c) in covered:
                continue
            end_r, end_c = spans.get((r, c), (r, c))
            height = _text_height_pt(cell, span_px(c, end_c))
            if end_r > r:
                tall.append((r, end_r, height))
            else:
                need = max(need, height)
        current = ws.row_dimensions[r].height
        ws.row_dimensions[r].height = max(need, current or 0)
    for start, end, height in tall:
        have = sum(ws.row_dimensions[r].height or 20 for r in range(start, end + 1))
        if have < height:
            extra = (height - have) / (end - start + 1)
            for r in range(start, end + 1):
                ws.row_dimensions[r].height = (ws.row_dimensions[r].height or 20) + extra


def _splits(words, lines):
    """Every way to break `words` into `lines` consecutive groups."""
    if lines == 1:
        yield [words]
        return
    for i in range(1, len(words) - lines + 2):
        for rest in _splits(words[i:], lines - 1):
            yield [words[:i]] + rest


def split_heading(text, font, room_pt, max_lines=3):
    """`text` broken onto as few lines as fit `room_pt`, each break placed where it leaves the
    widest line narrowest - "Amount of Accessories (INR)" becomes "Amount of / Accessories
    (INR)" rather than three ragged words. Returns (the heading with its line breaks, the width
    in points its widest line needs). When even `max_lines` lines are too wide, the narrowest
    `max_lines` arrangement is returned, and the caller widens the column to it."""
    words = str(text).split()
    if not words:
        return "", 0.0
    best = None
    for lines in range(1, min(max_lines, len(words)) + 1):
        # A line of one or two characters - "Disc. / %", "S. / No." - reads as a fragment, not a
        # heading; such a heading stays on one line and its column widens a little instead.
        options = []
        for groups in _splits(words, lines):
            if lines > 1 and any(len(" ".join(g)) < MIN_HEADING_LINE_CHARS for g in groups):
                continue
            widths = [text_pt(" ".join(g), font) for g in groups]
            widest = max(widths)
            # Between breaks equally wide, the one whose lines are most even: "Accessories / Net
            # Price / (INR)" rather than "Accessories / Net / Price (INR)".
            ragged = sum((widest - w) ** 2 for w in widths)
            options.append((round(widest, 1), ragged, groups))
        if not options:
            continue
        widest, _, groups = min(options, key=lambda o: (o[0], o[1]))
        option = (widest, groups)
        if best is None or option[0] < best[0]:
            best = option
        if option[0] <= room_pt:
            best = option
            break
    widest, groups = best
    return "\n".join(" ".join(g) for g in groups), widest


def fit_heading_row(ws, row, max_lines=3, minimum=20):
    """A table's heading row: each heading broken onto up to `max_lines` lines to fit its
    column, the column widened only where a heading cannot fit even then, and the row made tall
    enough for the longest. Merged headings are left to their own width."""
    spans, covered = _merged_spans(ws)
    for cell in _cells_by_row(ws, row, row).get(row, ()):
        c = cell.column
        if (row, c) in covered or (row, c) in spans or not isinstance(cell.value, str):
            continue
        # A few points short of the full width: the calibration's longest bold headings drew up
        # to 3 px wider in Excel than measured, and a heading that just misses wraps a word more.
        room = text_room_pt(column_px(ws, c)) - HEADING_MARGIN_PT
        label, widest = split_heading(cell.value, cell.font, room, max_lines)
        if widest > room:
            need_px = (widest + HEADING_MARGIN_PT) / PX_TO_PT + 2 * CELL_PADDING_PX + 1
            ws.column_dimensions[get_column_letter(c)].width = chars_for_px(math.ceil(need_px))
            # Widened for its longest word, the column may now take the heading in fewer lines.
            room = text_room_pt(column_px(ws, c)) - HEADING_MARGIN_PT
            label, widest = split_heading(cell.value, cell.font, room, max_lines)
        cell.value = label
        old = cell.alignment or Alignment()
        cell.alignment = Alignment(horizontal=old.horizontal or "center",
                                   vertical=old.vertical or "center", wrap_text=True)
    fit_rows(ws, row, row, minimum=minimum)


def _shown_number(value, number_format):
    """A number as Excel shows it under `number_format`, grouped the Indian way - the widest
    grouping, and the one the studio's own copies of Excel use."""
    fmt = number_format or "General"
    if "%" in fmt:
        decimals = 0 if fmt.startswith("0%") else 2
        return "%.*f%%" % (decimals, value * 100)
    if "0.00" in fmt:
        whole, frac = ("%.2f" % abs(value)).split(".")
        return ("-" if value < 0 else "") + _indian_grouping(whole) + "." + frac
    if "#,##0" in fmt:
        return ("-" if value < 0 else "") + _indian_grouping("%d" % round(abs(value)))
    return ("%f" % value).rstrip("0").rstrip(".")


def _indian_grouping(digits):
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


def fit_number_columns(ws, cached=None):
    """Widens any column whose figures would not fit it - Excel shows "#####" for a number too
    wide for its cell, rather than wrapping it - including a total set in a larger font than
    the rows above it. `cached` gives the value of each formula cell, {(sheet title,
    coordinate): value}, as the writers record them (quotation_export.FormulaCache). A figure
    in a merged range needs the range's width, and the range's last column takes the extra."""
    cached = cached or {}
    spans, covered = _merged_spans(ws)
    for (r, c), cell in sorted(ws._cells.items()):
        value = cell.value
        if isinstance(value, str) and value.startswith("="):
            value = cached.get((ws.title, cell.coordinate))
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if (r, c) in covered:
            continue
        end_c = spans.get((r, c), (r, c))[1]
        need_px = math.ceil(figure_px(_shown_number(value, cell.number_format), cell.font))
        have_px = sum(column_px(ws, col) for col in range(c, end_c + 1))
        if have_px < need_px:
            last = get_column_letter(end_c)
            ws.column_dimensions[last].width = chars_for_px(column_px(ws, end_c) + need_px - have_px)


def first_column_clear_of(ws, text, font, gap_px=8):
    """The first column that starts clear of `text` written from column A - where a value goes
    beside a label such as "Kind Attention:" without cutting it off."""
    need = text_pt(text, font) / PX_TO_PT + CELL_PADDING_PX + gap_px
    left, col = 0, 1
    while left < need:
        left += column_px(ws, col)
        col += 1
    return max(col, 2)
