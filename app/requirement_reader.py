"""Reads a client's lighting requirement document into one entry per requirement, whatever its
layout, and says plainly what it left out and why.

Why this exists
---------------
The Match tab used to flatten a PDF into one long string and then guess where each fixture
began ("Supply of", or blank lines). That works on one studio's house style and nowhere else.
Measured on real client documents in September 2026:

    A client schedule with no ruled lines (DOC-20260817-WA0077): plain text extraction reads it
    COLUMN BY COLUMN - every code, then every description, then every wattage - so no text-based
    splitter can ever pair a code with its wattage again.
    A client schedule ending in "PLEASE NOTE: *Finishes may change..." (Vikram Aggarwal BOQs):
    the notes came out as requirements.

A PDF is not a string, it is words at positions on a page, and a requirement schedule is a
table even when nothing draws its lines. So this reads positions:

  1. TABLE PATH. Find a header row by what its cells SAY ("Description", "Power", "CCT",
     "Ref. Code"...), not by ruled lines, which half the real documents do not have in any
     form PyMuPDF can see. The header's cells give the columns. Rows are cut at the vertical
     gaps between blocks of text, one row per entry in the key column (reference code, serial
     number or quantity - whichever gives the cleanest rows on this document, measured, not
     assumed). Each cell's value is read from its own column, so "14w" under "LED Power" is the
     wattage no matter how the description is worded.
  2. TEXT PATH, for pages with no recognisable header (prose, numbered lists, specsheets).
     Several ways of splitting are tried - reference labels, "Supply of", numbering, blank lines,
     one per page - and each is SCORED on the result: how many blocks describe exactly one
     product, how many merge two, how much product detail ended up in no block at all. The best
     one wins. No single guessed convention decides.

Nothing is dropped silently
---------------------------
A row or block becomes a requirement only if it carries product evidence: a fixture word
(downlight, profile, bollard...) or at least two specification values (7W, 3000K, IP65,
24 deg...). Everything else - notes, terms, totals, addresses, section headings - goes into
`ignored` WITH its reason, and the Match tab shows that list. A requirement cannot vanish
without the user being able to see where it went.

No model is involved. The same PDF always reads the same way, it costs nothing, it works with
no network and no quota, and every decision can be traced to a rule written here.
"""
import re
import statistics

# --- evidence: does this text describe a light fixture? -------------------------------------

# Words that name a luminaire or a part of one. Grounded in the typologies this library already
# classifies (app/typology_classifier.py) plus the wording real client schedules use.
_FIXTURE_WORD_RE = re.compile(
    r"\b(down\s?light(?:er)?s?|spot\s?light(?:s)?|spots?|luminaires?|fixtures?|light\s?fittings?|"
    r"fittings?|track(?:\s?lights?)?|strips?|profiles?|linear|bollards?|up\s?light(?:er)?s?|"
    r"grazers?|wall\s?washers?|washers?|pendants?|chandeliers?|panels?|lamps?|cove|flood\s?lights?|"
    r"projectors?|step\s?lights?|wall\s?lights?|sconces?|in\s?ground|inground|burial|recessed|"
    r"surface\s?mounted|suspended|magnetic|neon|flex|spike\s?lights?|lanterns?|batten|high\s?bay|"
    r"street\s?light|post\s?top|underwater|led)\b",
    re.IGNORECASE,
)

# Each specification kind counts once however often it appears - "3000K / 4000K" is one kind.
_SPEC_PATTERNS = {
    "wattage": re.compile(r"\b\d+(?:\.\d+)?\s*(?:w|watts?)(?:\b|/)", re.IGNORECASE),
    "cct": re.compile(r"\b\d{4}\s*k\b", re.IGNORECASE),
    "beam_angle": re.compile(r"\b\d{1,3}\s*(?:°|deg(?:ree)?s?\.?\b)", re.IGNORECASE),
    "ip_rating": re.compile(r"\bIP\s?[-:]?\s?\d{2}\b", re.IGNORECASE),
    "cri": re.compile(r"\b(?:CRI|Ra)\s*[:>=≥]*\s*\d{2}", re.IGNORECASE),
    "lumen_output": re.compile(r"\b\d+(?:\.\d+)?\s*lm\b", re.IGNORECASE),
    "control": re.compile(r"\b(dali|dimmable|dimming|phase\s?cut|triac|0-10\s?v|1-10\s?v|dmx|casambi|zigbee|on\s?/\s?off)\b", re.IGNORECASE),
    "voltage": re.compile(r"\b\d{2,3}\s*v\s*(?:dc|ac)\b", re.IGNORECASE),
    "ugr": re.compile(r"\bUGR\b", re.IGNORECASE),
}

# Text that opens like this is about the document, not a product - even when it mentions one
# ("Note: all fixtures to be 3000K" names a CCT and a fixture, and is still not a requirement).
# A leading "*" is how schedules bullet their footnotes ("*Cove lights to be measured on site").
_NON_PRODUCT_START_RE = re.compile(
    # Words need a boundary after them ("note" but not "notebook"); labels need their colon, and a
    # colon has no word boundary after it, so the two are matched separately.
    r"^\s*\*|^\W*(?:(?:please\s+note|note|notes|n\.?\s?b|terms|conditions|payment|delivery|validity|"
    r"quotes?\s+validity|price\s+(?:basis|validity|inclusions|exclusions)|gst|tax|freight|despatch|"
    r"dispatch|sales\s+returns?|grand\s+total|sub\s?-?total|total|summary|kind\s+attention|dear|"
    r"regards|thanks?|concern\s+person|revision|disclaimer)\b"
    r"|(?:from|to|date|project|client|architect|warranty|tel|fax|e-?mail|address|phone|mob(?:ile)?)\.?\s*:)",
    re.IGNORECASE,
)


def spec_kinds(text: str) -> set:
    return {kind for kind, pattern in _SPEC_PATTERNS.items() if pattern.search(text or "")}


def has_fixture_word(text: str) -> bool:
    return bool(_FIXTURE_WORD_RE.search(text or ""))


def looks_non_product(text: str) -> bool:
    return bool(_NON_PRODUCT_START_RE.match(text or ""))


def product_evidence(text: str, extra_kinds=(), strict=False) -> str:
    """-> "" when the text describes a product, otherwise the reason it does not.

    `strict` is for loose text outside any table. A table row has already shown it is a row of a
    fixture schedule, so a fixture word is enough there ("Outdoor Decorative LED Installation - as
    per image" is a real line item). Loose text has shown nothing, and a footnote like "Optics of
    the fixtures may change" names a fixture too - so it must also state a specification.

    The reason is shown to the user next to the ignored text, so it is written for them.
    """
    text = " ".join((text or "").split())
    if not text:
        return "empty"
    if looks_non_product(text):
        return "a note or terms, not a product"
    kinds = spec_kinds(text) | set(extra_kinds)
    if len(kinds) >= 2 or (has_fixture_word(text) and (kinds or not strict)):
        return ""
    if strict and has_fixture_word(text):
        return "mentions a fixture but states no specification"
    if kinds:
        return "only one specification value and no fixture description"
    return "no product details"


# --- table headers ----------------------------------------------------------------------------

# First pattern to match the WHOLE header cell wins, so the specific ones come first: "Ref. Brand"
# is a brand and "Ref. Product Image" is a picture, although both start like "Ref. Code".
_HEADER_KINDS = [
    ("image", r"(ref\.?\s*)?(product\s*|reference\s*|fixture\s*)?(image|photo|picture|pic)s?"),
    ("price", r"(unit\s*|list\s*|discounted\s*|net\s*|total\s*|accessories\s*net\s*)?(price|rate|amount|cost)"
              r"(\s*of\s*accessories)?(\s*\(?(inr|rs\.?|₹|usd|eur)\)?)?"),
    ("brand", r"(ref\.?\s*|preferred\s*|approved\s*)?(brands?|makes?|manufacturers?|vendors?|suppliers?)"),
    ("model", r"(model|product\s*code|cat(alogue)?\.?|article)\s*(no\.?|number|code|#)?"),
    ("sno", r"(s|sr|sl)\.?\s*no\.?|serial(\s*no\.?)?|#|no\.?"),
    ("wattage", r"(led\s*|lamp\s*|system\s*|total\s*)?(power|wattage|watts?|load)(\s*\(?w\)?)?(\s*consumption)?"),
    ("cct", r"cct|c\.?\s*temp\.?|colou?r\s*temp(\.|erature)?(\s*\(?k\)?)?|(led|light)\s*colou?r|kelvin"),
    ("beam_angle", r"optics?|beam(\s*angle)?|(light\s*)?distribution|lens"),
    ("cri", r"cri|ra"),
    ("ip_rating", r"ip(\s*rating)?|ingress\s*protection|protection"),
    ("control", r"controls?(\s*gears?)?(\s*type)?|dimming(\s*type)?|drivers?|gears?|protocol"),
    ("cutout", r"cut\s*-?\s*outs?(\s*(size|dia\.?|diameter|dimensions?))?"),
    ("dimensions", r"dimensions?|size|dia(meter)?\.?|length"),
    ("finish", r"finish(es)?|body\s*colou?r|colou?r"),
    ("mounting", r"mounting(\s*(frame|type))?|installation|frame"),
    ("lumen_output", r"(delivered\s*|source\s*)?(lumens?|lm|flux)(\s*output)?"),
    ("ugr", r"ugr"),
    ("qty", r"qty\.?|quantity|nos\.?|no\.?\s*of\s*(units|fixtures|nos\.?)|count"),
    ("unit", r"units?|uom"),
    ("area", r"areas?|locations?|rooms?|zones?|spaces?|floors?"),
    ("remarks", r"remarks?|notes?|comments?"),
    ("led", r"led|source|chip"),
    ("ref", r"(ref(erence)?\.?|fixture|luminaire|light(ing)?|item|type|code|tag)"
            r"(\s*(code|no\.?|number|type|tag|id|ref\.?))?|tag|symbol|legend|key|code\s*no\.?"),
    ("description", r"((fixture|luminaire|item|product|technical)\s*)?(description|specifications?|details|particulars)"
                    r"|items?|products?|(fixture|luminaire)\s*type"),
]
_HEADER_KIND_RES = [(kind, re.compile(rf"(?:{pattern})")) for kind, pattern in _HEADER_KINDS]

# Columns whose text is product specification. Remarks, quantity, prices and so on are shown to
# the user but never searched for fields: "24 Deg.=8" in a remarks cell is a quantity note.
SPEC_COLUMNS = ("description", "wattage", "cct", "beam_angle", "cri", "ip_rating", "control",
                "cutout", "dimensions", "finish", "mounting", "lumen_output", "ugr", "led")
# Columns that hold one value per row. Two separate blocks of text in one of these inside a
# single row means two rows were read as one.
_SINGLE_VALUE_COLUMNS = ("ref", "sno", "qty", "wattage", "cct", "beam_angle", "cri", "ip_rating")
# The key column candidates, most trustworthy first.
_ANCHOR_PRIORITY = ("ref", "sno", "qty", "model", "wattage", "cct", "description")


def classify_header(text: str) -> str:
    norm = " ".join((text or "").lower().replace("_", " ").split()).strip(" :*-")
    if not norm or len(norm) > 40:
        return ""
    # Tried again with the spaces removed, for a heading wrapped mid-word in a narrow column:
    # our own BOQ export prints "Reference No." as "Referen" over "ce No.".
    for candidate in (norm, norm.replace(" ", "")):
        for kind, pattern in _HEADER_KIND_RES:
            if pattern.fullmatch(candidate):
                return kind
    return ""


# Drivers and small parts are ordered alongside a luminaire, but they are not what the client is
# asking to be matched - the library search leaves drivers out entirely. Our own quotation export
# lists "Dali Dimmable Driver for above fixture", "Gel connector", "End caps" as rows of their own.
_ACCESSORY_RE = re.compile(
    r"\b(drivers?|power\s?suppl(?:y|ies)|psus?|smps|transformers?|connectors?|end\s?caps?|feeders?|"
    r"(?:suspension\s)?cables?|housings?|adapt(?:o|e)rs?|junction\s?box(?:es)?|mounting\s?kits?|"
    r"brackets?|clips?)\b",
    re.IGNORECASE,
)
_LUMINAIRE_RE = re.compile(
    r"\b(down\s?light(?:er)?s?|spot(?:\s?light)?s?|luminaires?|track(?:\s?lights?)?|strips?|profiles?|"
    r"bollards?|up\s?-?\s?light(?:er)?s?|grazers?|wall\s?washers?|pendants?|chandeliers?|panels?|"
    r"lamps?|flood\s?lights?|projectors?|neon|cove|step\s?lights?|wall\s?lights?|sconces?|linear\s+led)\b",
    re.IGNORECASE,
)


ACCESSORY_REASON = "a driver or accessory, not a luminaire"

# A driver is the one accessory the app can offer by itself: the driver_prices table holds every
# valid type/control/IP/wattage combination and what each costs (see DriverRules.md), so a
# requirement line that asks for one can be answered from there. Every other accessory - a
# connector, an end cap, a bracket - only exists as a part of some fixture, and is added under
# the fixture it belongs to instead.
_DRIVER_WORDS = r"drivers?|power\s?suppl(?:y|ies)|psus?|smps|transformers?|ballasts?"
_DRIVER_RE = re.compile(r"\b(%s)\b" % _DRIVER_WORDS, re.IGNORECASE)

# A driver named as a property of something else: "ATRIA 18W ... ( On/Off Driver Integrated )" is
# a downlight, and "Track spot with remote driver" is a spot. Both were being read as drivers,
# which is how a luminaire would have ended up quoted as a power supply.
_DRIVER_AS_FEATURE_RE = re.compile(
    r"(?:with|w/|c/w|incl(?:uding|\.)?|complete\s+with|and)\s+(?:\w+[\s,]+){0,3}(?:%s)\b"
    r"|\b(?:%s)\s+(?:is\s+)?(?:integrated|included|inbuilt|built[-\s]?in|on[-\s]?board)\b"
    r"|\b(?:integrated|inbuilt|built[-\s]?in|on[-\s]?board|internal)\s+(?:%s)\b"
    % (_DRIVER_WORDS, _DRIVER_WORDS, _DRIVER_WORDS),
    re.IGNORECASE,
)


def _named_as_itself(pattern, text: str):
    """The first match of `pattern` that is the thing being listed, not a feature of it.

    "Driver Integrated" inside a downlight's description names a property; the same word at the
    head of "Dali Constant Current Driver, 10.5W" names the line item.
    """
    skip = [m.span() for m in _DRIVER_AS_FEATURE_RE.finditer(text)]
    for m in pattern.finditer(text):
        if any(start <= m.start() and m.end() <= end for start, end in skip):
            continue
        return m
    return None


def _luminaire_owns(match, text: str) -> bool:
    """Whether a luminaire word before `match` means the line is that luminaire.

    Only when it is far enough back to be the head of the phrase rather than a word modifying
    what follows: "Track spot with remote driver" is a spot, but "Track Power Supply" is a power
    supply for a track, and "48V Track Rail Connector" is a connector.
    """
    luminaire = _LUMINAIRE_RE.search(text)
    if not luminaire or luminaire.start() > match.start():
        return False
    between = text[luminaire.end():match.start()]
    return len(between.split()) > 2


def is_driver(text: str) -> bool:
    """A driver or power supply rather than a luminaire or any other part."""
    text = text or ""
    driver = _named_as_itself(_DRIVER_RE, text)
    return bool(driver) and not _luminaire_owns(driver, text)


def is_accessory(text: str) -> bool:
    """A driver or part rather than a luminaire.

    Order matters because both appear together: "Driver for magnetic track" is a driver, while
    "Magnetic track spot with remote driver" is a spot. So an accessory word only counts when it
    names the line item itself (see _named_as_itself) and no luminaire word owns the phrase
    (see _luminaire_owns).
    """
    text = text or ""
    if re.search(r"\bfor\s+(the\s+)?above\b", text, re.IGNORECASE):
        return True
    accessory = _named_as_itself(_ACCESSORY_RE, text)
    return bool(accessory) and not _luminaire_owns(accessory, text)


# --- page geometry ----------------------------------------------------------------------------

class _Line:
    __slots__ = ("x0", "y0", "x1", "y1", "text", "size")

    def __init__(self, x0, y0, x1, y1, text, size):
        self.x0, self.y0, self.x1, self.y1, self.text, self.size = x0, y0, x1, y1, text, size

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2

    @property
    def h(self):
        return self.y1 - self.y0


def page_lines(page, textpage=None):
    """Text lines on one page, turned so the page's main text runs left to right.

    A schedule exported sideways onto a portrait page has all its text rotated; reading its
    boxes unrotated would put every column on top of every other. Text running a different way
    from the majority (a margin note printed up the side of the table) belongs to no row and is
    returned separately.
    """
    raw = []
    kwargs = {"textpage": textpage} if textpage is not None else {}
    for block in page.get_text("dict", **kwargs).get("blocks", []):
        for line in block.get("lines", []):
            text = " ".join("".join(span["text"] for span in line["spans"]).split())
            if not text:
                continue
            size = max((span["size"] for span in line["spans"]), default=0)
            direction = (round(line["dir"][0]), round(line["dir"][1]))
            raw.append((direction, line["bbox"], text, size))
    if not raw:
        return [], []
    counts = {}
    for direction, _, text, _ in raw:
        counts[direction] = counts.get(direction, 0) + len(text)
    main = max(counts, key=counts.get)
    dx, dy = main
    lines, stray = [], []
    for direction, (x0, y0, x1, y1), text, size in raw:
        # Rotate the box so `main` becomes left-to-right: new_x = x*dx + y*dy, new_y = y*dx - x*dy.
        corners = [(x * dx + y * dy, y * dx - x * dy) for x in (x0, x1) for y in (y0, y1)]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        item = _Line(min(xs), min(ys), max(xs), max(ys), text, size)
        (lines if direction == main else stray).append(item)
    lines.sort(key=lambda l: (round(l.y0, 1), l.x0))
    return lines, stray


def _merge_intervals(spans, tolerance):
    """[(y0, y1)] -> merged, sorted. Two spans closer than `tolerance` are one."""
    merged = []
    for y0, y1 in sorted(spans):
        if merged and y0 <= merged[-1][1] + tolerance:
            merged[-1][1] = max(merged[-1][1], y1)
        else:
            merged.append([y0, y1])
    return merged


def _join(lines):
    """Text of a cell: its lines top to bottom, left to right within a visual line."""
    ordered = sorted(lines, key=lambda l: (round(l.cy / 3), l.x0))
    return " ".join(l.text for l in ordered).strip()


# --- the table path ---------------------------------------------------------------------------

class _Header:
    def __init__(self, cells, y0, y1):
        self.cells = cells          # [(kind, x0, x1, text)] left to right
        self.y0, self.y1 = y0, y1
        centres = [(c[1] + c[2]) / 2 for c in cells]
        self.bounds = [(centres[i] + centres[i + 1]) / 2 for i in range(len(centres) - 1)]
        self.centres = centres

    def bind(self, body_lines):
        """Decides each body line's column from where the data sits, not only from the header.

        Header text is centred over its column but data often is not: a left-aligned description
        starts well left of its centred "Description" heading, closer to the neighbouring column's
        heading. So the body is first cut into vertical strips at the empty gaps between text,
        and a strip lying under exactly one heading belongs to that column. Only a strip lying
        under several headings (text crowded edge to edge) falls back to the nearest heading.
        """
        self._assigned = {}
        strips = _merge_intervals([(l.x0, l.x1) for l in body_lines], 1.0)
        for x0, x1 in strips:
            under = [i for i, cell in enumerate(self.cells) if cell[1] < x1 and cell[2] > x0]
            if len(under) != 1:
                continue
            for line in body_lines:
                if x0 - 0.01 <= line.x0 and line.x1 <= x1 + 0.01:
                    self._assigned[id(line)] = under[0]

    def column_of(self, line):
        assigned = getattr(self, "_assigned", {}).get(id(line))
        if assigned is not None:
            return assigned
        for i, bound in enumerate(self.bounds):
            if line.cx < bound:
                return i
        return len(self.cells) - 1

    def spans_columns(self, line):
        """True for a line wider than any cell: a note or heading running across the table."""
        covered = sum(1 for c in self.centres if line.x0 < c < line.x1)
        return covered >= 3

    def kinds(self):
        return {c[0] for c in self.cells if c[0]}


def find_headers(lines):
    """Every header row on a page, top to bottom.

    A header row is found by what its cells say. It must name a description column plus at least
    two other things a schedule records, and most of its cells must be recognisable - a line of
    prose that happens to contain "power" and "finish" is not a header.
    """
    headers = []
    used = set()
    heights = [l.h for l in lines] or [8]
    for i, start in enumerate(lines):
        if i in used or len(start.text) > 40:
            continue
        # The header band: lines at this height, plus lines directly below in the same font size
        # (two-line headings like "LED / POWER"). A section heading or first data row sits
        # further down or in another size, so it is not swallowed into the header.
        band = [j for j, l in enumerate(lines) if abs(l.y0 - start.y0) <= max(2.0, 0.3 * start.h)
                and abs(l.size - start.size) <= 0.6]
        bottom = max(lines[j].y1 for j in band)
        grew = True
        while grew:
            grew = False
            for j, l in enumerate(lines):
                if j in band or abs(l.size - start.size) > 0.6:
                    continue
                if bottom - 0.5 < l.y0 <= bottom + 0.6 * start.h or (start.y0 <= l.y0 < bottom):
                    band.append(j)
                    bottom = max(bottom, l.y1)
                    grew = True
        band_lines = [lines[j] for j in band]
        if len(band_lines) < 3 or any(len(l.text) > 40 for l in band_lines):
            continue
        # Lines that overlap horizontally belong to the same header cell.
        cells = []
        for l in sorted(band_lines, key=lambda l: l.x0):
            for cell in cells:
                if l.x0 < cell["x1"] - 1 and l.x1 > cell["x0"] + 1:
                    cell["lines"].append(l)
                    cell["x0"], cell["x1"] = min(cell["x0"], l.x0), max(cell["x1"], l.x1)
                    break
            else:
                cells.append({"x0": l.x0, "x1": l.x1, "lines": [l]})
        typed = []
        for cell in sorted(cells, key=lambda c: c["x0"]):
            text = " ".join(x.text for x in sorted(cell["lines"], key=lambda x: (x.y0, x.x0)))
            typed.append((classify_header(text), cell["x0"], cell["x1"], text))
        kinds = {t[0] for t in typed if t[0]}
        recognised = sum(1 for t in typed if t[0])
        if ("description" in kinds and len(kinds - {"description"}) >= 2
                and len(typed) >= 3 and recognised >= 0.6 * len(typed)):
            headers.append(_Header(typed, min(l.y0 for l in band_lines), bottom))
            used.update(band)
    headers.sort(key=lambda h: h.y0)
    return headers


def _row_cells(header, row_lines):
    """{column index: [lines]} for one row."""
    cells = {}
    for line in row_lines:
        cells.setdefault(header.column_of(line), []).append(line)
    return cells


def _groups(lines, tolerance):
    """Separate blocks of text in one column, by vertical gap."""
    return _merge_intervals([(l.y0, l.y1) for l in lines], tolerance)


def _rows_for_anchor(header, lines, bands, tolerance, anchor_index):
    """Cuts the table into rows keyed on one column. -> (rows, orphans), each a list of line lists."""
    anchor_lines = [l for l in lines if header.column_of(l) == anchor_index]
    anchors = _groups(anchor_lines, tolerance)
    rows, orphans = [], []
    for band_y0, band_y1 in bands:
        inside = [a for a in anchors if band_y0 - 0.5 <= (a[0] + a[1]) / 2 <= band_y1 + 0.5]
        band_lines = [l for l in lines if band_y0 - 0.5 <= l.cy <= band_y1 + 0.5]
        if not inside:
            orphans.append(band_lines)
            continue
        # Several key entries in one unbroken block of text: rows packed with no gap. Cut
        # halfway between consecutive entries.
        cuts = [(inside[k][1] + inside[k + 1][0]) / 2 for k in range(len(inside) - 1)]
        edges = [float("-inf")] + cuts + [float("inf")]
        for k in range(len(inside)):
            rows.append([l for l in band_lines if edges[k] <= l.cy < edges[k + 1]])
    return rows, orphans


def _row_quality(header, row_lines, tolerance):
    """-> (is_product, is_merged). Merged = a one-value column holds two separate entries."""
    cells = _row_cells(header, row_lines)
    merged = False
    for index, cell_lines in cells.items():
        if header.cells[index][0] in _SINGLE_VALUE_COLUMNS and len(_groups(cell_lines, tolerance)) > 1:
            merged = True
    spec_text = " ".join(_join(v) for k, v in cells.items() if header.cells[k][0] in SPEC_COLUMNS)
    column_kinds = {header.cells[k][0] for k, v in cells.items()
                    if header.cells[k][0] in _SPEC_PATTERNS and _join(v).strip(" _-–")}
    is_product = product_evidence(spec_text, column_kinds) == ""
    return is_product, merged


def read_table(header, lines):
    """-> (rows, orphans, anchor_kind) using whichever key column cuts this table most cleanly.

    Measured per table rather than fixed, because documents disagree: a client schedule gives
    every row a reference code, while our own quotation export merges one serial number across a
    fixture AND its driver sub-row, where only the quantity column has one entry per real row.
    """
    heights = sorted(l.h for l in lines) or [8.0]
    tolerance = max(1.0, 0.35 * statistics.median(heights))
    bands = _merge_intervals([(l.y0, l.y1) for l in lines], tolerance)
    kinds = [c[0] for c in header.cells]
    best = None
    for priority, kind in enumerate(_ANCHOR_PRIORITY):
        if kind not in kinds:
            continue
        index = kinds.index(kind)
        rows, orphans = _rows_for_anchor(header, lines, bands, tolerance, index)
        good = merged = 0
        for row in rows:
            is_product, is_merged = _row_quality(header, row, tolerance)
            good += 1 if is_product and not is_merged else 0
            merged += 1 if is_merged else 0
        lost = sum(1 for o in orphans if _row_quality(header, o, tolerance)[0])
        score = good - 2 * merged - 2 * lost
        if best is None or score > best[0]:
            best = (score, rows, orphans, kind)
    if best is None:
        return [], [lines], ""
    return best[1], best[2], best[3]


# --- turning cells into fields ----------------------------------------------------------------

_NUMBER = r"(\d+(?:\.\d+)?)"


def _clean_cell(text):
    text = " ".join((text or "").split())
    return "" if text.strip(" _-–—/.") == "" else text


def fields_from_cells(cells: dict) -> dict:
    """Canonical fields (the same names app/requirement_matcher.py scores) from a row's columns.

    Only what a cell plainly states. "Flood" in an optics column is not a number of degrees, so
    no beam angle is recorded rather than a guessed one.
    """
    fields = {}

    def first(pattern, text):
        m = re.search(pattern, text or "", re.IGNORECASE)
        return m.group(1) if m else None

    wattage = cells.get("wattage", "")
    n = first(_NUMBER + r"\s*w", wattage) or (first(r"^\s*" + _NUMBER + r"\s*$", wattage))
    if n:
        fields["wattage"] = f"{n}W"
    cct = cells.get("cct", "")
    n = first(r"(\d{4})\s*k?", cct)
    if n:
        fields["cct"] = f"{n}K"
    beam = cells.get("beam_angle", "")
    # A range stays a range ("10-55 Deg." is an adjustable optic), never one end of it.
    m = re.search(r"(\d{1,3})\s*(?:°|deg\.?)?\s*(?:-|–|to)\s*(\d{1,3})\s*(?:°|deg|degrees?)", beam, re.IGNORECASE)
    n = f"{m.group(1)}-{m.group(2)}" if m else first(r"(\d{1,3})\s*(?:°|deg|degrees?|$)", beam)
    if n:
        fields["beam_angle"] = n
    n = first(r"(\d{2,3})", cells.get("cri", ""))
    if n:
        fields["cri"] = n
    n = first(r"(\d{2})", cells.get("ip_rating", ""))
    if n:
        fields["ip_rating"] = n
    n = first(r"(\d{1,2})", cells.get("ugr", ""))
    if n:
        fields["ugr"] = n
    n = first(_NUMBER + r"\s*(?:lm)?", cells.get("lumen_output", ""))
    if n:
        fields["lumen_output"] = f"{n}lm"
    control = cells.get("control", "")
    if control:
        from app.requirement_matcher import _extract_control
        canonical = _extract_control(control + ";")
        if canonical:
            fields["control"] = canonical
    for kind, field in (("finish", "finish"), ("mounting", "mounting"),
                        ("cutout", "cutout_dimensions"), ("dimensions", "luminaire_length")):
        value = _clean_cell(cells.get(kind, ""))
        if value:
            fields[field] = value
    return fields


def _requirement_from_row(header, row_lines, area):
    from app.requirement_matcher import extract_requirement_fields
    by_index = _row_cells(header, row_lines)
    cells, labelled = {}, []
    for index in sorted(by_index):
        kind, _, _, header_text = header.cells[index]
        value = _clean_cell(_join(by_index[index]))
        if not value:
            continue
        if kind and kind not in cells:
            cells[kind] = value
        elif kind:
            cells[kind] = f"{cells[kind]} {value}"
        if kind not in ("image", "price", "description"):
            labelled.append(f"{header_text}: {value}")
    description = cells.get("description", "")
    fields = fields_from_cells(cells)
    # The description itself often states more than the columns do ("Color temp: 3000K, IP-20,
    # CRI 90" inside a description cell). Columns win; the description only fills gaps.
    spec_text = "; ".join(cells[k] for k in SPEC_COLUMNS if k in cells)
    for key, value in extract_requirement_fields(spec_text).items():
        fields.setdefault(key, value)
    # Description first, then ";" - typology and shape are guessed from the text before the
    # first ";" (see requirement_match in app/main.py), which must be the product description.
    block = "; ".join([description] + labelled) if description else "; ".join(labelled)
    return {
        "block": block,
        "fields": fields,
        "description": description,
        "label": cells.get("ref") or cells.get("sno") or "",
        "area": cells.get("area") or area or "",
        "quantity": cells.get("qty", ""),
        "brand": cells.get("brand", ""),
        "model": cells.get("model", ""),
    }


# --- the text path ----------------------------------------------------------------------------

# A reference label opening a line: "L1", "L-01", "LT 3", "RB2", "ED-75", "Type A", "Fixture 4".
_LABEL_START_RE = re.compile(
    # The word and its code must be separated, or "REFLECTOR FINISH" reads as the code "LECTOR".
    r"^\s*\(?((?:type|fixture|luminaire|ref\.?)(?:\s*[:\-]\s*|\s+)[A-Z0-9][A-Z0-9\-./]{0,6}\b"
    r"|[A-Z]{1,4}\s?-?\s?\d{1,3}[A-Z]?)\)?\s*(?:[:.\-–)|]\s*|\s+(?=[A-Z]))",
    re.IGNORECASE,
)
_TERMS_HEADING_RE = re.compile(
    r"^\W*(terms\s*(?:&|and)\s*conditions|terms\s+of\s+(?:sale|supply|payment|delivery)|general\s+terms|"
    r"commercial\s+terms|t\s*&\s*c)\b",
    re.IGNORECASE,
)
# Capital "Supply of" anywhere (the Studio export writes every requirement this way, with no line
# break between them), or a note opening after the end of a sentence.
_MIDLINE_START_RE = re.compile(
    r"(?<=\S)[ \t]+(?=Supply\s+of\b)|(?<=[.;])[ \t]+(?=(?i:please\s+note|general\s+notes?|notes?)\b)"
)
# Lines that end a requirement block and start a note. Deliberately narrower than
# _NON_PRODUCT_START_RE: "Warranty: 5 years" on its own line is part of a fixture's specification.
_NOTE_LINE_RE = re.compile(
    r"^\W*(?:(?:please\s+note|general\s+notes?|n\.\s?b|kind\s+attention|regards|thanks?)\b|notes?\s*[:\-])",
    re.IGNORECASE,
)
_SUPPLY_START_RE = re.compile(r"^\s*(supply(?:,\s*installation)?(?:\s+and\s+\w+)?\s+of)\b", re.IGNORECASE)
_NUMBER_START_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+\S")


def _starts(lines, pattern):
    return [i for i, line in enumerate(lines) if pattern.match(line)]


def _cut(lines, starts):
    """Blocks from start indices. Whatever precedes the first start is returned as a preamble."""
    if not starts:
        return [], "\n".join(lines)
    preamble = "\n".join(lines[:starts[0]])
    edges = starts + [len(lines)]
    blocks = ["\n".join(lines[edges[k]:edges[k + 1]]) for k in range(len(starts))]
    return blocks, preamble


def _label_starts(lines):
    """Lines opening with a reference label - only if labels are how this document is organised.

    A label shape has to repeat to count ("L1", "L2", "L3" all look like LETTERS+DIGITS), which
    keeps a stray "IP65" or "A4" at the start of one line from being read as a new fixture.
    """
    shapes = {}
    for i, line in enumerate(lines):
        m = _LABEL_START_RE.match(line)
        if not m:
            continue
        token = m.group(1).upper()
        if re.fullmatch(r"(IP|CRI|RA|UGR|IK)\s?-?\s?\d+[A-Z]?", token):
            continue      # a specification at the start of a wrapped line
        if re.fullmatch(r"(L|W|H|D|DIA)\s?-?\s?\d+", token) and re.match(
                r"\s*(?:mm|cm|m|x|\d)", line[m.end(1):].lstrip(" :=-")):
            continue      # a dimension ("L 600 mm", "H-800"), not the label "L1: downlight"
        shape = re.sub(r"\d+", "9", re.sub(r"[A-Z]+", "A", re.sub(r"\s", "", token)))
        shapes.setdefault(shape, []).append(i)
    if not shapes:
        return []
    shape, found = max(shapes.items(), key=lambda kv: len(kv[1]))
    return found if len(found) >= 2 else []


def _number_starts(lines):
    """Numbered lines, kept only while the numbers count upward (1, 2, 3...) - a quantity or a
    dimension at the start of a line does not continue the sequence."""
    found, expected = [], None
    for i, line in enumerate(lines):
        m = _NUMBER_START_RE.match(line)
        if not m:
            continue
        n = int(m.group(1))
        if expected is None or n == expected:
            found.append(i)
            expected = n + 1
    return found if len(found) >= 2 else []


def _paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _block_merges_several(block):
    """Does one block contain the openings of two or more fixtures, by any convention?"""
    lines = [l for l in block.split("\n") if l.strip()]
    return (len(_label_starts(lines)) >= 2 or len(_starts(lines, _SUPPLY_START_RE)) >= 2
            or len(_number_starts(lines)) >= 2)


def _score_split(blocks):
    good = merged = lost = 0
    for block in blocks:
        reason = product_evidence(block, strict=True)
        if not reason:
            if _block_merges_several(block):
                merged += 1
            else:
                good += 1
        elif spec_kinds(block):
            lost += 1          # product detail that ended up in no requirement
    return good - 2 * merged - lost


def read_text(pages):
    """-> (requirements, ignored, method) for text with no recognisable table header.

    `pages` is a list of (page_number, text). Each splitting method is tried on the whole text
    and scored; see _score_split. The order below only breaks ties.
    """
    all_lines, page_of_line, page_texts, ignored = [], [], [], []
    for number, text in pages:
        # A requirement or a note that starts mid-line - paragraphs a PDF export ran together -
        # is given its own line first, so the splitters below, which look at line starts, see it.
        text = re.sub(r"\bSupply[ \t]*\n[ \t]*of\b", "Supply of", text or "")   # wrapped mid-phrase
        text = _MIDLINE_START_RE.sub("\n", text)
        page_lines_ = text.split("\n")
        # Terms of sale run to the end of their page and are never a requirement, however many
        # product words they use ("IP68 connectors to be used for exterior installations").
        for i, line in enumerate(page_lines_):
            if _TERMS_HEADING_RE.match(line):
                ignored.append({"text": _squash("\n".join(page_lines_[i:])),
                                "reason": "terms and conditions", "page": number})
                page_lines_ = page_lines_[:i]
                break
        all_lines.extend(page_lines_)
        page_of_line.extend([number] * len(page_lines_))
        page_texts.append((number, "\n".join(page_lines_)))
    full_text = "\n".join(all_lines)
    if not full_text.strip():
        return [], ignored, "none"

    cursor = [0]

    def page_of(block):
        """The page a block starts on, found by its opening words. Blocks arrive in document order,
        so the search resumes just past the previous find - six specsheets that all open "Semi
        Recessed LED Downlight" are on six pages, not all on the first."""
        opening = block.strip()[:60]
        at = full_text.find(opening, cursor[0]) if opening else -1
        if at == -1:
            return None
        cursor[0] = at + 1
        line_index = full_text.count("\n", 0, at)
        return page_of_line[line_index] if line_index < len(page_of_line) else None

    candidates = []
    for name, starts in (("reference labels", _label_starts(all_lines)),
                         ("'Supply of' openings", _starts(all_lines, _SUPPLY_START_RE)),
                         ("numbered items", _number_starts(all_lines))):
        if starts:
            blocks, preamble = _cut(all_lines, starts)
            candidates.append((name, blocks, [preamble] if preamble.strip() else []))
    paragraphs = _paragraphs(full_text)
    if len(paragraphs) > 1:
        candidates.append(("paragraphs", paragraphs, []))
    non_empty_pages = [t for _, t in page_texts if t.strip()]
    if len(non_empty_pages) > 1:
        candidates.append(("one per page", non_empty_pages, []))
    candidates.append(("whole text", [full_text], []))

    best = max(enumerate(candidates), key=lambda ic: (_score_split(ic[1][1]), -ic[0]))[1]
    name, blocks, extra = best
    # A note written between two fixtures belongs to neither. Cut from the note to the end of the
    # block - never from the first line, which is the fixture itself.
    # Pages are looked up in document order - preamble, then each block - before anything is cut.
    for text in extra:
        ignored.append({"text": _squash(text), "reason": "text before the first requirement",
                        "page": page_of(text)})
    trimmed = []
    for block in blocks:
        page = page_of(block)
        lines = block.split("\n")
        cut = next((i for i, l in enumerate(lines) if i > 0 and _NOTE_LINE_RE.match(l)), None)
        if cut is not None:
            ignored.append({"text": _squash("\n".join(lines[cut:])), "reason": "a note or terms, not a product",
                            "page": page})
            block = "\n".join(lines[:cut])
        trimmed.append((block, page))
    requirements = []
    for block, page in trimmed:
        reason = product_evidence(block, strict=True)
        if not reason and is_accessory(block.strip()[:80]):
            reason = ACCESSORY_REASON
        if reason:
            if block.strip():
                ignored.append({"text": _squash(block), "reason": reason, "page": page})
            continue
        label_match = _LABEL_START_RE.match(block)
        label = label_match.group(1) if label_match and name == "reference labels" else ""
        requirements.append({"block": _squash(block), "fields": None, "description": "", "label": label,
                             "area": "", "quantity": "", "brand": "", "model": "", "page": page})
    return requirements, ignored, name


def _squash(text):
    """One requirement as one paragraph - line breaks inside a PDF cell are layout, not meaning."""
    return " ".join((text or "").split())


# --- the whole document -----------------------------------------------------------------------

def read_document(doc, textpages=None):
    """-> {"requirements": [...], "ignored": [...], "checks": {...}} for an open PyMuPDF document.

    `textpages` optionally maps page index -> an OCR textpage, for scanned pages.
    """
    import fitz
    requirements, ignored, notes = [], [], []
    loose_pages = []          # (page number, text) of pages with no table header
    methods = set()
    last_header = None
    empty_pages = []
    for index, page in enumerate(doc):
        number = index + 1
        lines, stray = page_lines(page, (textpages or {}).get(index))
        # The page is read; MuPDF's cache of its decoded pictures (no limit set) is let go, or a
        # big PDF of drawings holds them all at once - see MATCH_DOCUMENT_MAX_BYTES in app/main.py.
        fitz.TOOLS.store_shrink(100)
        if not lines:
            empty_pages.append(number)
            continue
        headers = find_headers(lines)
        # A table that runs onto a page without repeating its header keeps the last columns - but
        # only when this page's text actually sits in them, checked the same way any table is.
        continued = False
        if not headers and last_header is not None:
            headers = [_Header(last_header.cells, float("-inf"), float("-inf"))]
            continued = True
        if not headers:
            loose_pages.append((number, "\n".join(l.text for l in lines)))
            last_header = None
            continue

        page_requirements, page_ignored = [], []
        before = [l for l in lines if l.y1 <= headers[0].y0 + 0.5] if not continued else []
        for k, header in enumerate(headers):
            bottom = headers[k + 1].y0 if k + 1 < len(headers) else float("inf")
            body = [l for l in lines if header.y1 - 0.5 < l.y0 and l.y1 <= bottom + 0.5]
            spanning = [l for l in body if header.spans_columns(l)]
            cellular = [l for l in body if not header.spans_columns(l)]
            header.bind(cellular)
            rows, orphans, anchor = read_table(header, cellular)
            if anchor:
                methods.add(f"table rows keyed on the '{_header_text(header, anchor)}' column")
            # Rows and loose bands in page order, so a section heading applies to what follows it.
            events = [(min(l.y0 for l in r), "row", r) for r in rows if r]
            events += [(min(l.y0 for l in o), "orphan", o) for o in orphans if o]
            events += [(l.y0, "span", [l]) for l in spanning]
            area = ""
            # Once the document says "NOTE:" inside the table, loose text below it is notes - even
            # a numbered note that names a fixture ("3. Accessories for all linear fixtures..."). A
            # real row after it means the notes were a mid-table remark and have ended.
            in_notes = False
            for _, kind, group in sorted(events, key=lambda e: e[0]):
                if kind == "row":
                    # Column order, not top-to-bottom: a tall remarks cell ("*Exact length as per
                    # site") can start higher on the page than the code and description do, and
                    # must not decide what the row opens with.
                    cells = _row_cells(header, group)
                    text = _squash(" ".join(_join(cells[i]) for i in sorted(cells)))
                else:
                    text = _squash(_join(group))
                if looks_non_product(text):
                    in_notes = True
                if kind == "row":
                    if in_notes and (looks_non_product(text) or len(_row_cells(header, group)) <= 2):
                        # A real schedule row fills several columns; a note written into the
                        # first cell or two of the table's last rows does not.
                        page_ignored.append({"text": text, "reason": "part of the document's notes",
                                             "page": number})
                        continue
                    in_notes = False
                    entry = _requirement_from_row(header, group, area)
                    is_product, is_merged = _row_quality(header, group, 1.0)
                    if not is_product:
                        page_ignored.append({"text": text, "reason": product_evidence(text) or
                                             "a table row with no product details", "page": number})
                        continue
                    if is_accessory(entry["description"] or entry["model"]):
                        page_ignored.append({"text": text, "reason": ACCESSORY_REASON, "page": number})
                        continue
                    entry["page"] = number
                    if is_merged:
                        entry["warning"] = "this row may hold more than one product"
                    page_requirements.append(entry)
                    continue
                reason = product_evidence(text)
                if in_notes:
                    page_ignored.append({"text": text, "reason": "part of the document's notes",
                                         "page": number})
                elif kind == "orphan" and not reason and is_accessory(text[:80]):
                    page_ignored.append({"text": text, "reason": ACCESSORY_REASON, "page": number})
                elif kind == "orphan" and not reason:
                    # Product detail in the table with nothing in the key column: a real row whose
                    # reference cell was left blank. Kept, and flagged, rather than lost.
                    entry = _requirement_from_row(header, group, area)
                    entry["page"] = number
                    entry["warning"] = "no reference in this row - check it is a separate product"
                    page_requirements.append(entry)
                elif len(text.split()) <= 8 and len(group) <= 2 and not spec_kinds(text) \
                        and not looks_non_product(text) and not re.match(r"^\W*\d+\s*[.)]", text):
                    area = text          # a section heading, e.g. "Entertainment and Party Area"
                else:
                    page_ignored.append({"text": text, "reason": reason or "not a table row",
                                         "page": number})

        if continued and not page_requirements:
            # The previous page's columns do not fit this page after all; read it as text.
            loose_pages.append((number, "\n".join(l.text for l in lines)))
            last_header = None
            continue
        if before:
            page_ignored.insert(0, {"text": _squash(_join(before)), "reason": "document heading above the table",
                                    "page": number})
        for l in stray:
            page_ignored.append({"text": l.text, "reason": "text printed sideways, outside the rows",
                                 "page": number})
        requirements.extend(page_requirements)
        ignored.extend(page_ignored)
        last_header = headers[-1]

    if loose_pages:
        text_requirements, text_ignored, method = read_text(loose_pages)
        if method != "none":
            methods.add(f"text split by {method}")
        requirements.extend(text_requirements)
        ignored.extend(text_ignored)

    for i, requirement in enumerate(requirements):
        requirement["index"] = i + 1
    return {
        "requirements": requirements,
        "ignored": [item for item in ignored if item.get("text")],
        "checks": _checks(ignored, requirements, empty_pages),
        "method": "; ".join(sorted(methods)) or "none",
    }


def _header_text(header, kind):
    for cell in header.cells:
        if cell[0] == kind:
            return cell[3]
    return kind


# A reference code as schedules write them: letters then digits, optionally hyphenated.
_CODE_RE = re.compile(r"\b[A-Z]{1,4}\d?-?\d{1,3}[A-Z]?\b")


_NOT_SEARCHED_FOR_CODES = {"document heading above the table", "text before the first requirement",
                           "terms and conditions"}


def _checks(ignored, requirements, empty_pages):
    """Cross-checks on the count, reported rather than acted on.

    The count comes from the document's structure; these look for evidence it is wrong. The one
    that matters most: a reference code in the ignored text that looks like the codes the
    requirements carry is either a note mentioning it, or a product the reading missed - either
    way a person should look. Letterheads and terms are not searched: "D-14/9 Okhla" is an
    address, not a fixture.
    """
    warnings = []
    labels = [r.get("label", "") for r in requirements if r.get("label")]
    if labels:
        shapes = {re.sub(r"\d+", "9", re.sub(r"[A-Z]+", "A", l.upper().replace(" ", ""))) for l in labels}
        found = set()
        text = "\n".join(i["text"] for i in ignored
                         if i.get("reason") not in _NOT_SEARCHED_FOR_CODES and not looks_non_product(i["text"]))
        for m in _CODE_RE.finditer(text):
            token = m.group(0)
            shape = re.sub(r"\d+", "9", re.sub(r"[A-Z]+", "A", token))
            if shape in shapes and not re.match(r"^(IP|CRI|UGR|RA|LL|PG|DC|AC)", token):
                found.add(token)
        missing = sorted(found - {l.upper().replace(" ", "") for l in labels})
        if missing and len(missing) <= 20:
            warnings.append("These codes appear in the document but not as a requirement: "
                            + ", ".join(missing) + ". Check whether any of them is a product.")
    repeats = {}
    for label in labels:
        repeats[label] = repeats.get(label, 0) + 1
    repeated = {k: v for k, v in repeats.items() if v > 1}
    if repeated:
        warnings.append("Listed more than once (usually once per area): "
                        + ", ".join(f"{k} x{v}" for k, v in sorted(repeated.items())) + ".")
    flagged = [r["index"] for r in requirements if r.get("warning")]
    if flagged:
        warnings.append("Check requirement(s) " + ", ".join(map(str, flagged)) + " - see the note on each.")
    if empty_pages:
        warnings.append("Page(s) " + ", ".join(map(str, empty_pages)) + " have no readable text"
                        " (a drawing or a scan) and were not read.")
    return {
        "requirement_count": len(requirements),
        "distinct_labels": len(set(labels)),
        "unlabelled": sum(1 for r in requirements if not r.get("label")),
        "warnings": warnings,
    }
