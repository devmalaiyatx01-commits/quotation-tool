"""Understanding a request in a person's own words, and answering it from the catalogue's real values.

Why this exists
---------------
Ask matched words: "a duragreen product having nearly 1400Lm with cutout less than 100mm" put
"100 Fixed" first, because "100mm" and "100" look alike to a word search - and its cut-out is 100 mm,
which is not less than 100 mm. No record held a lumen figure either, so the model was left to make
one up. A better prompt cannot fix that: the model was never given the facts, and comparing numbers
is not what a word search does.

So a request is turned into constraints ("make Duragreen, lumens 1,190-1,610, cut-out under 100 mm"),
and this code - not a model - checks every product against them. Every value in an answer is a value
the product holds. The model has only one job here, and only when the rules could not read the
wording: to propose constraints, whose numbers must be in the person's own sentence.

The reading is always shown to the person, so a wrong reading is seen rather than trusted, and a
product that does not state a value is "not stated", never a pass. The wording a lighting designer
uses is data, in the tables below: to teach it a new phrase, add a row and a question to
docs/quality/ask_questions.json, then run scripts/maintenance/ask_eval.py.
"""
import difflib
import json
import re
import threading
import time

from sqlalchemy.orm import load_only, selectinload

from . import llm_extract, match_index, models, npd
from .typology_match import typology_key

# --- what the words mean ---------------------------------------------------------------------------
# Each row is (pattern, meaning), tried in order and removed from the sentence once read, so
# "warm white" is not also read as a white finish.

# A trade colour name is a band, not one number: a product offering any part of it is offered.
CCT_WORDS = [
    (r"(?:extra|very|ultra)[ -]warm(?: white)?", (1800, 2400), "extra warm"),
    (r"warm(?:er)? white|warm light|warm", (2700, 3000), "warm white"),
    (r"neutral white|natural white|neutral", (3500, 4000), "neutral white"),
    (r"cool white|cold white|cool light|cool", (4000, 5000), "cool white"),
    (r"day ?light", (5000, 6500), "daylight"),
]
TUNABLE_WORDS = r"tunable(?: white)?|tuneable(?: white)?|dim to warm|dim-to-warm|dynamic white|colou?r changing cct"

# Beam bands as designers say them. A zoom range overlapping the band counts.
BEAM_WORDS = [
    (r"very narrow(?: spot)?|super narrow|pencil beam", (0, 12), "very narrow beam"),
    (r"narrow (?:spot|beam|flood|optic|angle)|spot beam|tight beam", (0, 20), "narrow beam"),
    (r"medium (?:flood|beam|optic|angle|spread)", (20, 40), "medium beam"),
    (r"wide (?:flood|beam|optic|angle|spread)|flood beam|broad beam", (40, 180), "wide beam"),
]

# A place implies a minimum protection. What the reading says lets a person disagree.
PLACE_WORDS = [
    (r"under ?water|swimming pool|pool|fountain|submerged", "68", "underwater"),
    (r"outdoors?|exteriors?|external(?!\s+(?:driver|power|supply|psu))|fa[cç]ade|garden|landscape|terrace|balcony|rain", "65", "outdoors"),
    (r"shower|wet area|wet room|wet", "65", "wet area"),
    (r"bath ?rooms?|wash ?rooms?|toilets?|powder room|damp|humid|kitchen|spa", "44", "damp area"),
]

QUALITY_WORDS = [
    (r"(?:excellent|high|good|great|best|accurate) (?:cri|colou?r rendering|colou?r rendition)|high ra", "cri", 90, "CRI 90 or better"),
    (r"(?:low|no|minimal|less|reduced|anti)[ -]?glare|glare[ -]?(?:free|less)|(?:won't|doesn't|does not|will not|not) glare|comfortable light|visual comfort", "ugr", 19, "UGR 19 or lower"),
]

CONTROL_WORDS = [
    (r"\bdali(?:[ -]?2)?\b", ["DALI"], "DALI"),
    (r"\b(?:0|1)\s*[-/]\s*10\s*v?\b|\bzero to ten\b", ["0-10V"], "0-10V"),
    (r"\b(?:triac|phase[ -]?cut|mains dim(?:ming|mable)?|leading edge|trailing edge)\b", ["Phase Cut"], "phase-cut"),
    (r"\bzigbee\b", ["Zigbee"], "Zigbee"),
    (r"\bcasambi\b", ["Casambi"], "Casambi"),
    (r"\bdmx\b", ["DMX"], "DMX"),
    (r"\b(?:wireless|smart|bluetooth|app[ -]controlled)\b", ["Zigbee", "Casambi"], "wireless"),
    (r"\b(?:non[ -]?dim(?:mable|ming)?|(?:not|no|without)[ -]dim(?:mable|ming)?|on[ /-]?off|switched)\b", ["On/Off"], "on/off"),
    (r"\bdimm?(?:able|ing)\b", ["DALI", "0-10V", "Phase Cut", "Zigbee", "Casambi", "DMX", "Push"], "dimmable"),
]

# Longest phrases first. The right side is the typology key(s) of app/typology_match.py, so this
# holds no name the catalogue does not use.
TYPOLOGY_WORDS = [
    (r"track ?lights?|track ?spots?|luminaires? for tracks?|spots? for (?:a )?tracks?", ["track light", "track spot"], "track light"),
    (r"track(?: rail)?s?\b(?= only| system| profile)|track rails?|magnetic tracks?", ["track"], "track"),
    (r"wall ?wash(?:er|ers|ing)?|wall ?graz(?:er|ers|ing)|grazers?", ["wall grazer", "linear grazing", "flexible led lens grazer"], "wall washer"),
    (r"step ?lights?|stair ?lights?|foot ?lights?|path ?lights?|skirting lights?|stair lighting", ["step light"], "step light"),
    (r"down ?lights?|recessed spots?|ceiling spots?|can lights?|cans\b", ["downlight"], "downlight"),
    (r"spot ?lights?|spots\b", ["spotlight"], "spotlight"),
    (r"pendants?|hanging lights?|suspended lights?|suspension|chandeliers?", ["pendant"], "pendant"),
    (r"wall ?lights?|wall ?lamps?|sconces?|wall[ -]?mount(?:ed)?(?: lights?)?", ["wall light", "wall mount"], "wall light"),
    (r"floor ?lamps?|standing lamps?", ["floor lamp"], "floor lamp"),
    (r"table ?lamps?|desk ?lamps?", ["table lamp"], "table lamp"),
    (r"(?:led )?strips?(?: lights?)?|led tapes?|tape lights?|cove lights?|flexible (?:led|neon)|neon flex", ["strip light", "flexible neon led band", "linear cove"], "strip light"),
    (r"linear (?:lights?|profiles?|systems?)|profile lights?|aluminium profiles?|linear\b", ["linear", "linear system", "linear cove"], "linear light"),
    (r"bollards?", ["bollard"], "bollard"),
    (r"in-?ground(?: lights?)?|buried lights?|floor recessed|up ?lights?", ["inground", "floor recessed", "uplight"], "in-ground or uplight"),
    (r"flood ?lights?|projectors?", ["projector", "base projector"], "projector"),
    (r"garden spikes?|spike lights?", ["garden spike"], "garden spike"),
    (r"under ?water (?:lights?|fixtures?)|pool lights?", ["underwater fixture"], "underwater fixture"),
    (r"pole[ -]?(?:mounted|top)|post[ -]?top|street lights?|area lights?", ["pole mounted", "area and post top"], "pole-mounted"),
]

# What a fitting physically is. Each is looked for in what the product itself says about it.
FEATURE_WORDS = [
    (r"semi[ -]?recessed", r"semi[ -]?recess", "semi-recessed"),
    (r"recessed|flush(?:ed)?(?: mounted?)?|in the ceiling|false ceiling", r"recess", "recessed"),
    (r"surface(?:[ -]mounted?)?", r"surface", "surface-mounted"),
    (r"trim[ -]?less|no trim|without (?:a )?trim", r"trim[ -]?less", "trimless"),
    (r"with (?:a )?trim|mini[ -]?trim|trimmed", r"(?<![a-z])trim(?![a-z ]*less)", "with trim"),
    (r"adjustable|tiltable|tilting|aimable|rotatable|gimbal", r"adjust|tilt", "adjustable"),
    (r"zoom(?:able)?|variable beam", r"zoom", "zoomable"),
    (r"pin ?hole", r"pin ?hole", "pinhole"),
    (r"fixed", r"fixed", "fixed"),
]
SHAPE_WORDS = [(r"dual square|twin square", "Dual Square"), (r"square", "Square"), (r"round|circular", "Round")]
FINISH_WORDS = ("black", "white", "brass", "gold", "bronze", "copper", "chrome", "nickel", "champagne", "silver", "grey", "gray")

ORDER_WORDS = [
    (r"cheapest|lowest price|least expensive|most affordable|lowest cost", "price", "cheapest first"),
    (r"(?:most expensive|highest price|costliest)", "-price", "dearest first"),
    (r"brightest|most lumens?|highest (?:lumens?|output)|maximum (?:lumens?|output)", "-lumens", "brightest first"),
    (r"smallest (?:cut[ -]?out|hole|aperture|opening)|tiniest", "cutout", "smallest cut-out first"),
    (r"lowest (?:wattage|power)|least (?:power|wattage)|most efficient", "wattage", "lowest wattage first"),
]

_BOQ_WORK = re.compile(
    r"\b(?:(?:which|what|how many|list|show|any|all)\s+(?:of\s+)?(?:the\s+|our\s+)?(?:boqs?|projects?|quotations?|clients?)|our\s+boqs?"
    r"|boqs?\s+(?:named|called|for|of|with|has|have|had|used|using)|(?:in|on|from)\s+(?:the\s+|this\s+|that\s+|my\s+)?(?:[\w-]+\s+){0,3}boq"
    r"|have\s+we\s+(?:used|quoted)|did\s+we\s+(?:use|quote)|last\s+(?:time|quoted)|quoted|total\s+(?:of|for)\s+(?:the\s+|this\s+|that\s+|my\s+|our\s+)?(?:[\w-]+\s+){0,3}(?:boq|project|quotation))\b", re.I)
# "Used in" is past work only about a product named: "to be used in a bathroom" is about a place.
_USED = re.compile(r"\bused\s+(?:in|on|before|by|for)\b", re.I)
_COMPARE = re.compile(r"\b(?:compare|comparison|difference between|versus|vs\.?)\b", re.I)
_NOT_LUMINAIRE = re.compile(r"\b(?:drivers?|power suppl(?:y|ies)|transformers?|accessor(?:y|ies)|louvres?|louvers?|lens(?:es)?|honeycomb)\b", re.I)
_SEARCHING = re.compile(r"\b(?:find|need|needs|want|suggest|recommend|looking|something|anything|which|any|show|what (?:can|would|should))\b", re.I)
_EXPLAINING = re.compile(r"\s*(?:(?:what (?:is|are)|what's)\s+(?:an?\s+|the\s+)?[\w-]+(?:\s+[\w-]+){0,2}\s*\??\s*$"
                         r"|how (?:do|does|to|is|are|can|should)\b|why\b|when\b|who\b|define\b|tell me about\b)", re.I)
_WHAT_ARE = re.compile(r"\s*what are\b", re.I)
_HOW_TO_FIND = re.compile(r"\s*how (?:can|do|should) (?:i|we)\s+(?:find|get|choose|pick|search)\b", re.I)
_EXPLAIN_ANYWHERE = re.compile(r"\b(?:explain|meaning of|what does .{1,60}\bmean)\b", re.I)
_COUNT = re.compile(r"\bhow many\b|\bcount of\b|\bnumber of\b", re.I)

# Words that carry no requirement. What is left after these and everything above is reported as
# not understood, rather than being quietly ignored.
_FILLER = set("""a about all also am an and any anything are around as at available be been best better but by can could do does
    fitting fittings fixture fixtures for from get give good got has have having hello help hi how i if in is it item items its i'm
    what's that's it's there's i'd i'll i've we're we've we'd let's here's where's who's how's
    work works won't isn't doesn't don't can't too much many nos pcs pieces units goes go sits sit throw throws fit fits fitted stair stairs staircase corridors hole holes size ones just kindly lamp lamps led light lighting lights like list look looking luminaire luminaires make me model models more my
    need needed needs nice of offer offered on one ones only option options or our out over please product products provide
    range recommend recommendation required requirement requires room rooms say see should show similar so some something spec
    specification specs suggest suitable supply that the their them then there these they thing things this those to too type
    types us use used using very want wanted we what when where which who will with within would you your
    project client site space area floor ceiling wall hotel office home residence villa lobby corridor living bedroom restaurant
    retail shop store gallery museum hall reception meeting""".split())

# Bounded on purpose: a number nobody means (400 nines) stays unread rather than overflowing later.
# Ordinary words that happen to start a product's name. They say something, but not which product.
_PLAIN_WORDS = {"low", "high", "tall", "small", "big", "bright", "large", "medium", "long", "short"}

_NUM = r"(\d{1,3}(?:,\d{3}){1,3}(?:\.\d{1,3})?|\d{1,9}(?:\.\d{1,3})?)"
_APPROX = r"nearly|almost|about|around|approximately|approx\.?|roughly|close to|near|circa|~|something like|in the region of|throws|gives|delivering"
_MAXW = r"no bigger than|no larger than|no greater than|not above|not higher than|no higher than|not greater than|not bigger than|not larger than|less than|lesser than|smaller than|fewer than|under|below|beneath|not more than|no more than|not exceeding|not over|at most|up to|upto|maximum of|maximum|max\.?|<=|≤|<"
_MINW = r"no smaller than|not below|not under|not lower than|no lower than|not smaller than|more than|greater than|bigger than|larger than|higher than|above|over|exceeding|at least|not less than|no less than|minimum of|minimum|min\.?|>=|≥|>"
_OP = r"(?<![a-z])(" + _APPROX + "|" + _MAXW + "|" + _MINW + r")"
_STRICT = re.compile(r"^(?:less than|lesser than|smaller than|fewer than|under|below|beneath|<|more than|greater than|bigger than|larger than|higher than|above|over|exceeding|>)$")
_AFTER = r"or (?:less|lower|below|under|smaller)|and (?:below|under|less)|max(?:imum)?|at most|or (?:more|higher|above|better|greater|bigger)|and (?:above|over|up|more)|plus|\+|min(?:imum)?|at least|ish"
# At most three linking words, each one consuming something. Never a repeat inside a repeat: that
# form once let a 62-character question ("lumens is is is ...") stall a whole server worker.
_LINK = r"\s*(?:(?:(?:is|are|of|should be|must be|needs? to be|has to be|at|having|with|size|dia(?:meter)?|dimension|value|rating|output|power)\b|[:=])\s*){0,3}"
_LABELS = {
    "lumens": r"lumens?|lumen output|light output|output|lm|brightness",
    "wattage": r"wattage|watts?|power|load",
    "cutout": r"cut[ -]?outs?|cut[ -]?out size|ceiling (?:cut|hole|opening)|holes?(?: size)?|gaps?|openings?|apertures?|recess size",
    "cct": r"cct|colou?r temperature|colou?r temp|kelvin",
    "beam": r"beam(?: angle)?|optics?|angle",
}
_UNITS = {
    "lumens": r"lumens?|lms?",
    "wattage": r"watts?|w",
    "cct": r"kelvin|k",
    "beam": r"°|˚|º|degrees?|deg",
    "mm": r"mm|millimet(?:er|re)s?",
    "cm": r"cm|centimet(?:er|re)s?",
    "inch": r"inch(?:es)?|\"",
}
# How far "about" and an unqualified value reach. Matches the Match tab's own default tolerances.
APPROX_SHARE = {"lumens": 0.15, "wattage": 0.15, "cutout": 0.1, "cct": 0.0, "beam": 0.2, "size": 0.1, "price": 0.15}
PLAIN_SHARE = {"lumens": 0.15, "wattage": 0.0, "cutout": 0.05, "cct": 0.0, "beam": 0.1, "size": 0.05, "price": 0.0}
UNIT_WORD = {"lumens": " lm", "wattage": "W", "cutout": " mm", "cct": "K", "beam": "°", "size": " mm"}
LABEL_WORD = {"lumens": "Lumens", "wattage": "Wattage", "cutout": "Cut-out", "cct": "CCT", "beam": "Beam", "size": "Size", "price": "an NPD price",
              "ip": "IP", "cri": "CRI", "ugr": "UGR", "control": "control", "tunable": "CCT", "finish": "finish"}


def _number(text):
    return float(str(text).replace(",", ""))


def _fmt(n):
    n = round(float(n), 1)
    if n == int(n):
        n = int(n)
    return "{:,}".format(n)


def _say(kind, lo, hi):
    """A range in the words a person would check it by."""
    unit = UNIT_WORD.get(kind, "")
    pre = "₹" if kind == "price" else ""
    if lo is None and hi is None:
        return ""
    if lo is None:
        return "up to %s%s%s" % (pre, _fmt(hi), unit)
    if hi is None:
        return "%s%s%s or more" % (pre, _fmt(lo), unit)
    if lo == hi:
        return ("%s%s%s" % (pre, _fmt(lo), unit)).replace(",", "" if kind == "cct" else ",")
    return ("%s%s to %s%s" % (pre, _fmt(lo), _fmt(hi), unit)).replace(",", "" if kind == "cct" else ",")


def _bound(kind, value, op, after):
    """{lo, hi, target, says} for a value and how it was qualified. Strictly less than 100 is not 100."""
    op = (op or "").strip().lower().rstrip(".")
    after = (after or "").strip().lower()
    below = bool(op and re.fullmatch(_MAXW, op)) or bool(re.search(r"less|lower|below|under|smaller|^max|at most", after))
    above = bool(op and re.fullmatch(_MINW, op)) or bool(re.search(r"more|higher|above|better|greater|bigger|over|up$|plus|\+|^min|at least", after))
    strict = bool(op and _STRICT.match(op))
    if below:
        return {"lo": None, "hi": value - 0.01 if strict else value, "target": None, "strict": strict,
                "says": ("under %s%s" % (_fmt(value), UNIT_WORD.get(kind, ""))) if strict else _say(kind, None, value)}
    if above:
        return {"lo": value + 0.01 if strict else value, "hi": None, "target": None, "strict": strict,
                "says": ("over %s%s" % (_fmt(value), UNIT_WORD.get(kind, ""))) if strict else _say(kind, value, None)}
    approx = bool(op) or after == "ish"
    share = (APPROX_SHARE if approx else PLAIN_SHARE).get(kind, 0)
    lo, hi = value * (1 - share), value * (1 + share)
    says = ("about %s%s (%s)" % (_fmt(value), UNIT_WORD.get(kind, ""), _say(kind, round(lo), round(hi)))) if share else _say(kind, value, value)
    return {"lo": lo, "hi": hi, "target": value, "strict": False, "says": says.replace(",", "") if kind == "cct" else says}


def _blank(text, match):
    return text[:match.start()] + " " * (match.end() - match.start()) + text[match.end():]


def _fold(text):
    text = str(text or "").lower().replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _store(found, kind, value, raw=""):
    """The first value of a kind stands. Any later one is reported, never silently lost."""
    if kind in found:
        found.setdefault("left", []).append((raw or value.get("says", "")).strip())
    else:
        found[kind] = value


def _read_ranges(text, found):
    """"between 1000 and 1500 lm", "1000-1500lm", "12 to 15 W", "2.7-3k", "8-10 cm"."""
    for kind, unit, scale in (("lumens", _UNITS["lumens"], 1), ("wattage", _UNITS["wattage"], 1), ("cct", _UNITS["cct"], 1), ("beam", _UNITS["beam"], 1),
                              ("mm", _UNITS["mm"], 1), ("mm", _UNITS["cm"], 10)):
        pattern = re.compile(r"(?:between\s+(?<![\w.])" + _NUM + r"\s*(?:" + unit + r")?\s*(?:-|to|and)\s*" + _NUM
                             + r"|(?<![\w.])" + _NUM + r"\s*(?:" + unit + r")?\s*(?:-|to)\s*" + _NUM + r")\s*(?:" + unit + r")(?![a-z/])", re.I)
        while True:
            m = pattern.search(text)
            if not m:
                break
            lo, hi = sorted((_number(m.group(1) or m.group(3)) * scale, _number(m.group(2) or m.group(4)) * scale))
            target = "cutout" if kind == "mm" and _near_label(text, m, "cutout") else ("size" if kind == "mm" else kind)
            if kind == "cct":
                lo, hi = _cct_value(lo), _cct_value(hi)
            _store(found, target, {"lo": lo, "hi": hi, "target": None, "says": _say(target, lo, hi), "span": True}, m.group(0))
            text = _blank(text, m)
    return text


def _cct_value(value):
    """CCT in its shorthands: 2.7K and 3K are thousands, Lumenpulse's 30K is hundreds."""
    if value < 10:
        return value * 1000
    if 18 <= value <= 65:
        return value * 100
    return value


def _near_label(text, match, label):
    """Whether a label word sits next to this number: "cut-out 75 mm", "75 mm cut-out"."""
    before = text[max(0, match.start() - 40):match.start()]
    after = text[match.end():match.end() + 25]
    pattern = _LABELS[label]
    return bool(re.search(r"(?:" + pattern + r")\b[^,;.]*$", before) or re.match(r"\s*(?:" + pattern + r")\b", after))


def _read_units(text, found):
    """A number with its unit, and whatever qualifies it."""
    # Per metre first: 5 W/m and a 5 W fitting are not the same five watts.
    metre = r"\s*(?:/|per)\s*(?:m|mtrs?|mts?|rmt?|meters?|metres?|running met(?:er|re)s?)\b"
    for kind, unit in (("lumens", _UNITS["lumens"]), ("wattage", _UNITS["wattage"])):
        p = re.compile(r"(?:" + _OP + r"\s*)?(?<![\w.])" + _NUM + r"\s*(?:" + unit + r")" + metre, re.I)
        m = p.search(text)
        if m:
            _store(found, kind, _bound(kind, _number(m.group(2)), m.group(1), ""))
            found[kind]["per_metre"] = True
            found[kind]["says"] += " per metre"
            text = _blank(text, m)
    # A kilo-shorthand on CCT ("2.7K", "3K") is a colour temperature, and so is "30K" (Lumenpulse's).
    def number_with(kind, unit):
        return re.compile(r"(?:" + _OP + _LINK + r")?(?<![\w.,])" + _NUM + r"\s*(?:" + unit + r")(?![a-z0-9/])(?:\s*(" + _AFTER + r")(?![a-z]))?", re.I)
    for kind, unit in (("lumens", _UNITS["lumens"]), ("wattage", _UNITS["wattage"]), ("cct", _UNITS["cct"]), ("beam", _UNITS["beam"])):
        pattern = number_with(kind, unit)
        for m in list(pattern.finditer(text)):
            value = _number(m.group(2))
            if kind == "cct":
                # "under 5k" is far more likely a budget than a colour: left to be reported.
                if value < 100 and m.group(1):
                    continue
                value = _cct_value(value)
                if not 1000 <= value <= 10000:
                    continue
            if kind == "beam" and not 0 < value <= 180:
                continue
            if kind in found:
                found.setdefault("left", []).append(m.group(0).strip().replace(" ", ""))
            else:
                found[kind] = _bound(kind, value, m.group(1), m.group(3))
            text = _blank(text, m)
    for unit_name, scale in (("mm", 1.0), ("cm", 10.0), ("inch", 25.4)):
        for m in list(number_with("mm", _UNITS[unit_name]).finditer(text)):
            value = _number(m.group(2)) * scale
            if value <= 0:
                found.setdefault("left", []).append(m.group(0).strip())
                text = _blank(text, m)
                continue
            kind = "cutout" if _near_label(text, m, "cutout") else "size"
            # A nominal "3 inch downlight" is a trade size, not a measurement.
            reading = _bound(kind, value, m.group(1) or ("about" if unit_name == "inch" else ""), m.group(3))
            # Only the words in this number's own part of the sentence: in "cut-out 75mm, height 90mm"
            # the height is about the 90.
            nearby = re.split(r"[,;]", text[max(0, m.start() - 16):m.start()])[-1] + " " + re.split(r"[,;]", text[m.end():m.end() + 12])[0]
            if re.search(r"\b(?:length|long|height|tall|depth|deep|wide|width)\b", nearby):
                found.setdefault("left", []).append(m.group(0).strip())
            else:
                _store(found, kind, reading, m.group(0))
            text = _blank(text, m)
    return text


def _read_labelled(text, found):
    """A label and a bare number: "cutout less than 100", "lumens above 1000", "beam 24"."""
    limits = {"lumens": (20, 200000), "wattage": (0.5, 2000), "cutout": (5, 1000), "cct": (1000, 10000), "beam": (1, 180)}
    for kind, label in _LABELS.items():
        pattern = re.compile(r"\b(?:" + label + r")\b" + _LINK + r"(?:" + _OP + r"\s*)?" + _NUM + r"(?![\w%])(?:\s*(" + _AFTER + r")(?![a-z]))?", re.I)
        m = pattern.search(text)
        if m and limits[kind][0] <= _number(m.group(2)) <= limits[kind][1]:
            if kind in found:
                found.setdefault("left", []).append(m.group(0).strip())
            else:
                found[kind] = _bound(kind, _number(m.group(2)), m.group(1), m.group(3))
            text = _blank(text, m)
    return text


def _read_grades(text, found):
    """IP, CRI, UGR and price - each read by its own label, not by a number's size alone."""
    m = re.search(r"\b(?:" + _MINW + r")?\s*\bip\s*[-:]?\s*(\d)(\d|x)\b(?:\s*(?:or (?:better|higher|above|more)|and above|\+))?", text)
    if m:
        found["ip"] = {"digits": m.group(1) + (m.group(2) if m.group(2) != "x" else "0"), "says": "IP%s%s or better" % (m.group(1), m.group(2).upper())}
        text = _blank(text, m)
    m = re.search(r"\b(?:cri|ra)\s*(?:of|is|:|=|above|over|at least|minimum|min\.?|>=?|≥)?\s*(\d{2,3})\s*\+?(?:\s*(?:or (?:better|higher|above|more)|and above))?", text) \
        or re.search(r"(?<![\w.])(\d{2,3})\s*\+?\s*(?:cri|ra)\b", text)
    if m and 50 <= int(m.group(1)) <= 100:
        found["cri"] = {"n": int(m.group(1)), "says": "CRI %s or better" % m.group(1)}
        text = _blank(text, m)
    m = re.search(r"\bugr\s*(?:of|is|:|=|below|under|less than|at most|max(?:imum)?\.?|<=?|≤)?\s*(\d{1,2})(?:\s*(?:or (?:less|lower|below)|and below))?", text)
    if m and 5 <= int(m.group(1)) <= 34:
        found["ugr"] = {"n": int(m.group(1)), "says": "UGR %s or lower" % m.group(1)}
        text = _blank(text, m)
    # Rupees only as a word of their own: "washers 12W" ends in "rs", and "12W" is not ₹N.
    sign = r"(?:₹|(?<![a-z])(?:rs\.?|inr|rupees?)(?![a-z]))"
    money = sign + r"\s*" + _NUM + r"(k|l|lakh)?(?![a-z0-9])|(?<![\w.])" + _NUM + r"(k|l|lakh)?\s*" + sign
    budget = re.compile(r"(?<![a-z])budget(?:\s+of)?\s*(?:" + sign + r"\s*)?" + _NUM + r"(k|l|lakh)?(?![a-z0-9])")
    m = budget.search(text)
    if m:
        value = _number(m.group(1)) * {"k": 1000, "l": 100000, "lakh": 100000}.get((m.group(2) or "").lower(), 1)
        found["price"] = _bound("price", value, "up to", "")
        text = _blank(text, m)
    m = re.search(r"(?:" + _OP + r"\s*(?:of\s*)?)?(?:" + money + r")(?:\s*(" + _AFTER + r")(?![a-z]))?", text)
    if m:
        value = _number(m.group(2) or m.group(4))
        scale = (m.group(3) or m.group(5) or "").lower()
        value *= 1000 if scale == "k" else (100000 if scale in ("l", "lakh") else 1)
        op = m.group(1) or ""
        _store(found, "price", _bound("price", value, op, m.group(6)))
        text = _blank(text, m)
    return text


def _read_band(text, found, kind, table):
    """A trade name for a range. It is always taken out, even when a number already said it: left in,
    "warm white 3000K" read its "white" as a finish. If the two disagree, that is reported."""
    for pattern, band, label in table:
        m = re.search(r"\b(?:" + pattern + r")\b", text)
        if not m:
            continue
        if kind not in found:
            found[kind] = {"lo": band[0], "hi": band[1], "target": None, "says": "%s (%s)" % (label, _say(kind, band[0], band[1]))}
        elif _distance(found[kind], (band,)) > 0:
            found.setdefault("left", []).append(m.group(0))
        return _blank(text, m)
    return text


def _read_words(text, found):
    """The designer's vocabulary. Each table is read once, and what it reads is taken away."""
    for pattern, key, label in ORDER_WORDS:
        m = re.search(r"\b(?:" + pattern + r")\b", text)
        if m:
            found["order"] = {"key": key, "says": label}
            text = _blank(text, m)
            break
    if _COUNT.search(text):
        found["count"] = True
        text = _COUNT.sub(lambda m: " " * len(m.group(0)), text)
    m = re.search(r"\b(?:" + TUNABLE_WORDS + r")\b", text)
    if m:
        found["tunable"] = {"says": "tunable white"}
        text = _blank(text, m)
    text = _read_band(text, found, "beam", BEAM_WORDS)
    text = _read_band(text, found, "cct", CCT_WORDS)
    for pattern, digits, label in PLACE_WORDS:
        m = re.search(r"\b(?:" + pattern + r")\b", text)
        if m:
            if "ip" not in found:
                found["ip"] = {"digits": digits, "says": "IP%s or better (%s)" % (digits, label)}
            text = _blank(text, m)
            break
    for pattern, key, value, label in QUALITY_WORDS:
        m = re.search(r"\b(?:" + pattern + r")\b", text)
        if m and key not in found:
            found[key] = {"n": value, "says": label}
            text = _blank(text, m)
    for pattern, wanted, label in CONTROL_WORDS:
        m = re.search(pattern, text)
        if m and "control" not in found:
            found["control"] = {"any": wanted, "says": label}
            text = _blank(text, m)
    for pattern, keys, label in TYPOLOGY_WORDS:
        m = re.search(r"\b(?:" + pattern + r")(?![a-z])", text)
        if m and "typology" not in found:
            found["typology"] = {"keys": keys, "says": label, "at": m.start()}
            text = _blank(text, m)
            how_many = re.search(r"(?<![\w.])\d{1,4}\s*$", text[:m.start()])
            if how_many:
                text = _blank(text, how_many)
    features = []
    for pattern, look_for, label in FEATURE_WORDS:
        m = re.search(r"\b(?:" + pattern + r")\b", text)
        if m:
            features.append({"look_for": look_for, "says": label})
            text = _blank(text, m)
    if features:
        found["features"] = features
    for pattern, shape in SHAPE_WORDS:
        m = re.search(r"\b(?:" + pattern + r")\b", text)
        if m:
            found["shape"] = {"value": shape, "says": shape.lower() + " shape"}
            text = _blank(text, m)
            break
    finishes = [w for w in FINISH_WORDS if re.search(r"\b%s\b" % w, text)]
    if finishes:
        found["finish"] = {"words": finishes, "says": " or ".join(finishes) + " finish"}
        for w in finishes:
            text = re.sub(r"\b%s\b" % w, " " * len(w), text)
        text = re.sub(r"\bfinish(?:ed)?\b", " ", text)
    return text


# Anything that wants a number right after it, so that number is neither a name nor how many.
_LIMIT_BEFORE = re.compile(r"(?:" + _OP + r"|\b(?:" + _LABELS["cutout"] + r"|size|dia(?:meter)?|cri|ra|ugr|ip|beam(?: angle)?|angle|optics?"
                           r"|cct|kelvin|wattage|watts?|power|lumens?|lm|output|brightness))\s*(?:of\s+|is\s+|:\s*|=\s*)?$", re.I)
# "100 Adjustable" is a product, and "100 adjustable downlights" is a hundred of something.
_PLURAL_KIND_AHEAD = re.compile(r"\s+[a-z-]*(?:lights|lamps|pendants|washers|grazers|bollards|sconces|spots|strips|projectors|fittings|fixtures|luminaires)\b")
_ROUGHLY_BEFORE = re.compile(r"(?<![a-z])(?:nearly|almost|about|around|approximately|approx\.?|roughly|circa|~)\s*$")
_KIND_THEN_PART = re.compile(r"\b(?:" + "|".join(p for p, _k, _l in TYPOLOGY_WORDS) + r")\s+(?:drivers?|power suppl|transformers?|accessor)", re.I)
_NEGATION = re.compile(r"(?<![a-z])(not|non|except|excluding|without|other than|apart from|but not|no)[ -]+(?:(?:a|an|the)\s+)?([a-z0-9&+-]+)")
# Negative words that already mean something in the tables above.
_COMPARISONS = {"more", "less", "over", "under", "exceeding", "above", "below", "higher", "lower", "greater", "bigger", "larger", "smaller"}
_NEGATION_KNOWN = re.compile(r"^(?:more|less|over|under|exceeding|above|below|higher|lower|greater|bigger|larger|smaller|trim|trimmed|dim|dimmable|dimming|glare|minimal|too)$")


def _read_negation(text, found, catalogue):
    """"not duragreen" and "except NYX Midi" exclude what they name. Any other "not X" cannot be
    applied, and must never be read as X: "non-dali" once read as DALI. So it is taken out and
    reported as not understood. This runs before names are read, or "except NYX Midi" means NYX Midi."""
    for m in list(_NEGATION.finditer(text)):
        word = m.group(2)
        if _NEGATION_KNOWN.match(word):
            # A comparison must be one the tables hold, or dropping the "no" reverses it: "no bigger
            # than 100 mm" once read as over 100 mm. Any other is reported, never guessed at.
            last_word = m.end(1) - len(m.group(1).split()[-1])
            if word not in _COMPARISONS or re.match(r"(?:" + _MAXW + "|" + _MINW + r")(?![a-z])", text[last_word:]):
                continue
            found.setdefault("left", []).append(m.group(0))
            text = _blank(text, m)
            continue
        named = catalogue["name_pattern"].match(text, m.start(2)) if catalogue["name_pattern"] else None
        if named:
            key = " ".join(re.findall(r"\w+", named.group(0)))
            found.setdefault("not_ids", []).extend(catalogue["name_ids"].get(key, []))
            found.setdefault("not_names", []).append(named.group(0).strip())
            text = text[:m.start()] + " " * (named.end() - m.start()) + text[named.end():]
            continue
        make = next((name for name, pattern in catalogue["makes"] if re.fullmatch(pattern, word)), None)
        if make:
            found.setdefault("not_make", []).append(make)
        elif len(word) >= 3 and not word.isdigit() and word in catalogue["series"]:
            found.setdefault("not_ids", []).extend(catalogue["series"][word])
            found.setdefault("not_names", []).append(word)
        else:
            joined = "-" if text[m.start():m.end()].startswith(m.group(1) + "-") else " "
            found.setdefault("left", []).append(m.group(1) + joined + word)
        text = _blank(text, m)
    return text


def read_request(question, catalogue) -> dict:
    """The constraints a request states. Reads and decides nothing about any product."""
    found = {}
    text = " " + _fold(question) + " "
    # "1.4k lm" is 1,400 lm; read here so nothing later takes the k for a colour temperature.
    text = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)\s*k\s*(lm|lumens?)\b", lambda m: "%g %s" % (float(m.group(1)) * 1000, m.group(2)), text)
    # The way people say it with no unit at all.
    text = re.sub(r"(?<![\w.])(\d{2,6})\s+of\s+light\b(?!\s+(?:fittings?|fixtures?|points?))", r"\1 lm", text)
    text = re.sub(r"(?<![\w.])(\d{2,3})\s+(cut[ -]?out|hole|gap|opening|aperture)\b", r"\1 mm \2", text)
    # Whole product names first: "75 ZOOM" is a product, not 75 of something. But "under 100 fixed"
    # is a limit and a word, and reading it as the product "100 Fixed" was the very first bug here.
    text = _read_negation(text, found, catalogue)
    named = []
    for at in list(catalogue["name_pattern"].finditer(text)) if catalogue["name_pattern"] else []:
        key = " ".join(re.findall(r"\w+", at.group(0)))
        if key[:1].isdigit() and (_LIMIT_BEFORE.search(text[max(0, at.start() - 40):at.start()]) or _PLURAL_KIND_AHEAD.match(text, at.end())):
            continue
        named.extend(catalogue["name_ids"].get(key, []))
        found.setdefault("named", []).append(at.group(0).strip())
        text = _blank(text, at)
    if named:
        found["named_ids"] = sorted(set(named))
    for make, pattern in catalogue["makes"]:
        at = re.search(pattern, text)
        if at:
            found.setdefault("make", {"values": [], "says": ""})["values"].append(make)
            text = _blank(text, at)
    if "make" in found:
        found["make"]["says"] = " or ".join(found["make"]["values"])
    text = _read_grades(text, found)
    text = _read_ranges(text, found)
    text = _read_units(text, found)
    text = _read_labelled(text, found)
    text = _read_words(text, found)
    text = re.sub(r"(?<![\w.])\d{1,4}\s*(?:nos?\.?|pcs?\.?|pieces?|units?)\b", " ", text)
    for label in _LABELS.values():
        text = re.sub(r"\b(?:" + label + r")\b", " ", text)
    series, left = [], list(found.pop("left", []))
    for word in re.findall(r"[a-z0-9][a-z0-9'+.-]*", text):
        word = word.rstrip(".-")
        # A driver is understood, and answered elsewhere: it is not a word to report as unread.
        if not word or word in _FILLER or word in ("mm", "cm", "lm", "w", "k") or _ASKS_FOR_DRIVER.fullmatch(word):
            continue
        ids = catalogue["series"].get(word)
        if ids and len(word) >= 3 and not word.isdigit() and word not in _PLAIN_WORDS and not _NOT_LUMINAIRE.fullmatch(word) and len(ids) <= catalogue["rare"]:
            series.append({"word": word, "ids": ids})
        else:
            left.append(word)
    if series:
        found["series"] = series
    found["left"] = [word[:40] for word in left]
    found["rest"] = text
    return found


# interpret() holds these to numbers in the sentence, so they are the person's own, not the model's guess.
WRITTEN_BY_THE_PERSON = ("lumens", "wattage", "cutout", "size")
CONSTRAINT_KEYS = ("lumens", "wattage", "cutout", "size", "cct", "beam", "ip", "cri", "ugr", "control", "features",
                   "tunable", "shape", "finish", "price")


def goes_to_records(question, reading) -> bool:
    """Questions a search of fittings cannot answer. A mention is not enough: "12W downlight with a
    DALI driver" asks for a downlight, "which driver for a 12W downlight" asks for a driver - whichever
    comes first is what is being asked for."""
    if reading.get("not_search") or _BOQ_WORK.search(question) or _COMPARE.search(question):
        return True
    if _USED.search(question) and ("named_ids" in reading or "series" in reading):
        return True
    # "What is a bollard?" asks what one is. "What are the 12W downlights?", "what is the cheapest ..."
    # and "how can I find a 12W downlight?" ask for products, and go to code, which cannot invent one.
    asks_for_products = (reading.get("order") or reading.get("count") or "make" in reading
                         or (_WHAT_ARE.match(question) and "typology" in reading)
                         or (_HOW_TO_FIND.match(question) and any(k in reading for k in CONSTRAINT_KEYS + ("typology",))))
    if (_EXPLAINING.match(question) or _EXPLAIN_ANYWHERE.search(question)) and not asks_for_products:
        return True
    folded = _fold(question)
    other = _NOT_LUMINAIRE.search(folded)
    if other:
        if _KIND_THEN_PART.search(folded):       # "downlight driver" is a driver
            return True
        kind = reading.get("typology")
        return kind is None or other.start() < kind["at"]
    return False


def wants_catalogue(question, reading) -> bool:
    """Whether this asks to find products by what they are, which code answers - or asks something
    else (what a BOQ holds, a comparison, one product's details), which goes to the records."""
    if goes_to_records(question, reading):
        return False
    if any(k in reading for k in CONSTRAINT_KEYS):
        return True
    # A brand and a kind of fitting, or either one with "how many" or "cheapest": a search.
    has_kind = "typology" in reading
    if "named_ids" in reading or "series" in reading:
        return False
    if has_kind and ("make" in reading or "not_make" in reading):
        return True
    if has_kind:
        return True
    return bool((has_kind or "make" in reading) and (reading.get("count") or reading.get("order")
                                                     or re.search(r"\b(?:show|list|find|which|what|any|have|need|want|suggest|recommend|all)\b", question, re.I)))


# --- the catalogue, as values ---------------------------------------------------------------------

INDEX_SECONDS = 120
_CACHE, _CACHE_LOCK = {}, threading.Lock()


def _printed_lumens(params):
    """A figure the vendor printed ("lm system": "180"), which beats an estimate from efficacy."""
    for section in (params or {}).values():
        if isinstance(section, dict):
            for key, value in section.items():
                if re.fullmatch(r"(?i)lm system", str(key).strip()):
                    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(value))]
                    if numbers:
                        return tuple(sorted(set(numbers)))
    return ()


def _price_from(sheet):
    """What this product costs at its cheapest in NPD, or None for no price (npd.lowest_price)."""
    return npd.lowest_price(sheet)


def _names_a_kind(name):
    """A product called "Step Light" must not swallow a request for any step light."""
    return any(re.fullmatch(pattern, name) for pattern, _keys, _label in TYPOLOGY_WORDS)


def _texts_of(value):
    return (str(value),) if value else ()


def build_catalogue(db, discipline) -> dict:
    """Every approved luminaire of this tool as comparable values - the Match tab's own reading of
    them (app/match_index.py), so the two tabs read a product's values alike. (Ask also uses a printed "lm system" figure, on 8 products; Match does not yet.)"""
    S = models.Specsheet
    sheets = (db.query(S)
              .options(load_only(S.id, S.typology, S.make, S.product_name, S.series, S.family, S.category, S.fields, S.options,
                                 S.base_price, S.price_adders, S.origin),
                       selectinload(S.price_entries))
              .filter(S.typology != "Driver", S.discipline == discipline, S.approval_status == "approved")
              .order_by(S.id).all())
    # `params` is the bulky column, and the live server is small: read only for products stating no
    # lumen figure of their own, as match_index.build_index does.
    need = [s.id for s in sheets if (s.options or {}).get("wattage")
            and not any(match_index.read_lumens(match_index._texts(s.fields, s.options, "lumen_output", "lumen_package")))]
    params = dict(db.query(S.id, S.params).filter(S.id.in_(need)).all()) if need else {}
    products, series, names = [], {}, {}
    for s in sheets:
        fields, options = s.fields or {}, s.options or {}
        entry = match_index.product_entry(s, params.get(s.id))
        printed = _printed_lumens(params.get(s.id))
        if printed and (entry.get("lme") or not entry.get("lm")):
            entry["lm"] = tuple((v, v) for v in printed)
            entry.pop("lme", None)
        finish_values = [str(v) for key, vals in options.items() if "finish" in key for v in (vals or [])]
        if fields.get("finish"):
            finish_values.append(str(fields["finish"]))
        words = " ".join(str(v) for v in [s.product_name, s.typology, s.category, fields.get("mounting"), fields.get("mounting_style"),
                                          fields.get("luminaire_type"), fields.get("adjustability"), fields.get("trim_details"),
                                          fields.get("optic_type"), " ".join(str(o) for o in options.get("mounting") or [])] if v)
        entry.update({"size": match_index.read_cutout(_texts_of(fields.get("size"))),
                      "finish": _fold(" ".join(finish_values)), "words": _fold(words), "price": _price_from(s),
                      "typology": s.typology or ""})
        products.append(entry)
        name = _fold(s.product_name)
        if name:
            names.setdefault(name, []).append(s.id)
            first = re.findall(r"[a-z0-9][a-z0-9'+]*", name)[:1]
            if first:
                series.setdefault(first[0], []).append(s.id)
    make_names = sorted({s.make for s in sheets if s.make}, key=lambda m: -len(m))
    # Spellings people type - "dura green", "lumen pulse" - so a space or hyphen may fall anywhere.
    makes = [(m, r"\b" + r"[\s-]?".join(re.escape(ch) for ch in _fold(m).replace(" ", "")) + r"\b") for m in make_names]
    # Longest first, and only names a person could not have meant as plain words.
    name_ids = {}
    for name, ids in names.items():
        words = re.findall(r"\w+", name)
        if len(name) >= 4 and len(words) >= 2 and not _names_a_kind(name):
            name_ids.setdefault(" ".join(words), []).extend(ids)
    # One pattern, longest first, with any spacing or hyphen between words: "NYX Midi" is NYX -Midi.
    longest = sorted(name_ids, key=len, reverse=True)
    name_pattern = re.compile(r"(?<![\w-])(?:" + "|".join(r"[\s-]*".join(re.escape(w) for w in n.split()) for n in longest) + r")(?![\w-])") if longest else None
    return {"products": products, "series": series, "name_pattern": name_pattern, "name_ids": name_ids, "makes": makes,
            "by_id": {p["id"]: p for p in products}, "rare": max(6, len(products) // 25),
            "kinds": sorted({p["t"] for p in products if p["t"]})}


def catalogue_for(db, discipline) -> dict:
    with _CACHE_LOCK:
        built = _CACHE.get(discipline)
        if built and time.time() - built[0] < INDEX_SECONDS:
            return built[1]
        catalogue = build_catalogue(db, discipline)
        _CACHE[discipline] = (time.time(), catalogue)
        return catalogue


# --- checking a product ---------------------------------------------------------------------------

def _distance(want, intervals):
    """How far the nearest offered value is from what was asked, or None for nothing stated."""
    best = None
    lo = want.get("lo") if want.get("lo") is not None else float("-inf")
    hi = want.get("hi") if want.get("hi") is not None else float("inf")
    for a, b in intervals:
        gap = 0.0 if (b >= lo and a <= hi) else (lo - b if b < lo else a - hi)
        best = gap if best is None else min(best, gap)
    return best


def _values_for(kind, want, product):
    if kind == "lumens":
        return product.get("lmm" if want.get("per_metre") else "lm") or ()
    if kind == "wattage":
        return product.get("wm" if want.get("per_metre") else "w") or ()
    if kind == "cct":
        return product.get("k") or ()
    if kind == "beam":
        return product.get("b") or ()
    if kind == "cutout":
        return product.get("cut") or ()
    if kind == "size":
        return product.get("cut") or product.get("size") or ()
    if kind == "price":
        return ((product["price"], product["price"]),) if product.get("price") else ()
    return ()


def check(reading, product) -> dict:
    """{"fails": [...], "unstated": [...]}: which constraints this product misses, and which it says
    nothing about. A product that states nothing has not met anything."""
    fails, unstated = [], []
    for kind in ("lumens", "wattage", "cutout", "size", "cct", "beam", "price"):
        want = reading.get(kind)
        if not want:
            continue
        values = _values_for(kind, want, product)
        if not values:
            unstated.append(kind)
        elif _distance(want, values) > 0:
            fails.append(kind)
    if "ip" in reading:
        ratings = product.get("ip") or ()
        need = reading["ip"]["digits"]
        if not ratings:
            unstated.append("ip")
        elif not any(r[0] >= need[0] and r[1] >= need[1] for r in ratings):
            fails.append("ip")
    if "cri" in reading:
        if not product.get("cri"):
            unstated.append("cri")
        elif max(product["cri"]) < reading["cri"]["n"]:
            fails.append("cri")
    if "ugr" in reading:
        if not product.get("ugr"):
            unstated.append("ugr")
        elif min(product["ugr"]) > reading["ugr"]["n"]:
            fails.append("ugr")
    if "control" in reading:
        if not product.get("ctl"):
            unstated.append("control")
        elif not set(product["ctl"]) & set(reading["control"]["any"]):
            fails.append("control")
    if "tunable" in reading:
        if not product.get("k"):
            unstated.append("tunable")
        elif not any(hi - lo >= 500 for lo, hi in product["k"]):
            fails.append("tunable")
    if "finish" in reading:
        if not product.get("finish"):
            unstated.append("finish")
        elif not any(re.search(r"\b%s\b" % w, product["finish"]) for w in reading["finish"]["words"]):
            fails.append("finish")
    for feature in reading.get("features") or []:
        if not re.search(feature["look_for"], product.get("words") or ""):
            fails.append("features")
    if "shape" in reading and product.get("shape") != reading["shape"]["value"]:
        fails.append("shape")
    return {"fails": fails, "unstated": unstated}


def _in_scope(reading, product):
    """What is never relaxed: the brand and kind of fitting, and a product named."""
    if "make" in reading and product.get("make") not in reading["make"]["values"]:
        return False
    if product.get("make") in (reading.get("not_make") or ()) or product["id"] in (reading.get("not_ids") or ()):
        return False
    if "typology" in reading and product.get("t") not in reading["typology"]["keys"]:
        return False
    if "named_ids" in reading and product["id"] not in reading["named_ids"]:
        return False
    for item in reading.get("series") or []:
        if product["id"] not in item["ids"]:
            return False
    return True


def _closeness(reading, product):
    """Nearest to what was asked comes first: the target for "about 1,400", else the tightest fit."""
    total = 0.0
    for kind in ("lumens", "wattage", "cutout", "size", "beam", "price"):
        want = reading.get(kind)
        if want and want.get("target"):
            values = _values_for(kind, want, product)
            if values:
                total += min(min(abs(want["target"] - a), abs(want["target"] - b)) for a, b in values) / want["target"]
    return total


def _sort_key(reading, product):
    order = (reading.get("order") or {}).get("key", "")
    name = (product.get("name") or "").lower()
    if order:
        field = order.lstrip("-")
        values = {"price": [(product["price"], product["price"])] if product.get("price") else [],
                  "lumens": product.get("lm") or product.get("lmm") or [],
                  "cutout": product.get("cut") or [],
                  "wattage": product.get("w") or []}[field]
        if values:
            number = max(v[1] for v in values) if order.startswith("-") else min(v[0] for v in values)
            return (0, -number if order.startswith("-") else number, name)
        return (1, 0, name)
    return (0, _closeness(reading, product), name)


def _nearest_lumens(want, product):
    """The lumen figure to show, and the wattage that gives it when the two lists pair up."""
    lm = product.get("lmm" if (want or {}).get("per_metre") else "lm") or ()
    if not lm:
        return None, None
    target = (want or {}).get("target")
    if target is None and want:
        inside = [iv for iv in lm if _distance(want, (iv,)) == 0]
        chosen = (inside or list(lm))[-1]
    elif target is not None:
        chosen = min(lm, key=lambda iv: abs(iv[0] - target))
    else:
        chosen = lm[-1]
    watts = product.get("w") or ()
    paired = watts[list(lm).index(chosen)][0] if len(watts) == len(lm) else None
    return chosen, paired


def row_for(reading, product) -> dict:
    """One product in an answer, with the values that were asked about and where each comes from."""
    facts = []
    lm, paired = _nearest_lumens(reading.get("lumens"), product)
    shows_lumens = bool(lm and ("lumens" in reading or (reading.get("order") or {}).get("key", "").endswith("lumens")))
    if shows_lumens:
        text = "%s lm%s" % (_fmt(lm[0]) if lm[0] == lm[1] else "%s-%s" % (_fmt(lm[0]), _fmt(lm[1])), "/m" if (reading.get("lumens") or {}).get("per_metre") else "")
        facts.append(text + (" (est.)" if product.get("lme") else "") + (" at %gW" % paired if paired else ""))
    elif product.get("w"):
        facts.append(" / ".join("%gW" % a if a == b else "%g-%gW" % (a, b) for a, b in product["w"][:6]))
    if product.get("cut"):
        facts.append("cut-out " + " / ".join("%g mm" % v[0] for v in product["cut"]))
    elif "size" in reading and product.get("size"):
        facts.append("size " + " / ".join("%g mm" % v[0] for v in product["size"]))
    if "cct" in reading or "tunable" in reading:
        if product.get("k"):
            facts.append(" / ".join("%gK" % a if a == b else "%g-%gK" % (a, b) for a, b in product["k"][:6]))
    if "beam" in reading and product.get("b"):
        facts.append(" / ".join("%g°" % a if a == b else "%g-%g°" % (a, b) for a, b in product["b"][:6]))
    if "ip" in reading and product.get("ip"):
        facts.append(" / ".join("IP" + r for r in product["ip"]))
    if "cri" in reading and product.get("cri"):
        facts.append("CRI %d" % max(product["cri"]))
    if "ugr" in reading and product.get("ugr"):
        facts.append("UGR %d" % min(product["ugr"]))
    if "control" in reading and product.get("ctl"):
        facts.append(", ".join(product["ctl"]))
    if product.get("price"):
        facts.append("from ₹" + _fmt(product["price"]))
    return {"id": product["id"], "name": " ".join(str(product.get("name") or "").split()), "make": product.get("make") or "",
            "typology": product.get("typology") or "", "facts": facts, "estimated": bool(product.get("lme") and shows_lumens)}


def run(reading, catalogue, limit=25) -> dict:
    """Every product in scope, sorted into those meeting everything, those that cannot be fully
    checked because they do not state a value, and those that nearly do."""
    meets, partial, near = [], [], []
    for product in catalogue["products"]:
        if not _in_scope(reading, product):
            continue
        result = check(reading, product)
        if result["fails"]:
            near.append((product, result))
        elif result["unstated"]:
            partial.append((product, result))
        else:
            meets.append(product)
    meets.sort(key=lambda p: _sort_key(reading, p))
    partial.sort(key=lambda pr: (len(pr[1]["unstated"]), _sort_key(reading, pr[0])))
    # Nearest misses: one thing wrong, and by how much.
    def miss(pair):
        product, result = pair
        cost = 0.0
        for kind in result["fails"]:
            want = reading.get(kind) or {}
            values = _values_for(kind, want, product)
            if values:
                base = want.get("target") or want.get("hi") or want.get("lo") or 1
                cost += _distance(want, values) / abs(base)
            else:
                cost += 1.0
        return (len(result["fails"]) + len(result["unstated"]), cost, (product.get("name") or "").lower())
    near.sort(key=miss)
    return {"meets": meets, "partial": partial, "near": near[:8], "total": len(meets),
            "rows": [row_for(reading, p) for p in meets[:limit]],
            "partial_rows": [dict(row_for(reading, p), unstated=[LABEL_WORD.get(k, k) for k in r["unstated"]]) for p, r in partial[:5]],
            "near_rows": [dict(row_for(reading, p), misses=[LABEL_WORD.get(k, k) for k in r["fails"]],
                               unstated=[LABEL_WORD.get(k, k) for k in r["unstated"]]) for p, r in near[:5]]}


def describe(reading) -> list:
    """The reading in words, in the order a person would say it. Shown with every answer."""
    out = ["not " + m for m in (reading.get("not_make") or []) + (reading.get("not_names") or [])]
    for key in ("make", "typology", "lumens", "wattage", "cutout", "size", "cct", "tunable", "beam", "ip", "cri", "ugr", "control", "shape", "finish", "price"):
        part = reading.get(key)
        if part:
            prefix = {"lumens": "Lumens ", "wattage": "Wattage ", "cutout": "Cut-out ", "size": "Size ", "cct": "CCT ", "beam": "Beam ", "price": "NPD price "}
            says = part["says"]
            out.append(says if key in ("make", "typology") or not prefix.get(key) or says[:1].isalpha() and key in ("cct", "beam") else prefix[key] + says)
    out.extend(f["says"] for f in reading.get("features") or [])
    out.extend(reading.get("named") or [])
    out.extend('name has "%s"' % s["word"] for s in reading.get("series") or [])
    if reading.get("order"):
        out.append(reading["order"]["says"])
    return out


def _plain_list(rows, show):
    return "\n".join("• %s (%s)%s" % (r["name"], r["make"], (": " + ", ".join(r["facts"])) if r["facts"] else "") for r in rows[:show])


def answer(reading, catalogue) -> dict:
    """The whole reply, written from the values. Nothing here comes from a model."""
    found = run(reading, catalogue)
    dropped = []
    inferred = [k for k in reading.get("model_keys") or [] if k not in WRITTEN_BY_THE_PERSON]
    if not found["total"] and inferred:
        dropped = describe({k: reading[k] for k in inferred if k in reading})
        reading = {k: v for k, v in reading.items() if k not in inferred}
        found = run(reading, catalogue)
    wants = describe(reading)
    what = "; ".join(wants)
    # What the model inferred is marked, so it never looks like the person's own words.
    theirs = set(describe({k: reading[k] for k in reading.get("model_keys") or [] if k in reading}))
    shown_reading = [w + " (AI's reading)" if w in theirs else w for w in wants]
    notes = []
    total = found["total"]
    if not wants:
        # Only the model's reading was searchable, and it found nothing. Listing all 1,229 products
        # under "what you asked" would be the worst possible answer.
        found = dict(found, rows=[], near_rows=[], partial=[], partial_rows=[], total=0)
        total = 0
        text = "I could not work out a product search from this. Name a make, a kind of fitting or a value, such as 12W 3000K IP65."
    elif reading.get("count") and total:
        text = "%d product%s in the catalogue meet this: %s." % (total, "" if total == 1 else "s", what)
        if found["rows"]:
            text += "\n\nFor example:\n" + _plain_list(found["rows"], 8)
    elif total:
        text = "%d product%s meet%s this: %s.%s" % (total, "" if total == 1 else "s", "s" if total == 1 else "", what,
                                                    " Closest first." if not reading.get("order") and any((reading.get(k) or {}).get("target") for k in ("lumens", "wattage", "cutout", "size")) else "")
        text += "\n\n" + _plain_list(found["rows"], 12)
        if total > 12:
            text += "\n…and %d more." % (total - 12)
    else:
        text = "No product in the catalogue meets all of this: %s." % what
        if found["near_rows"]:
            text += "\n\nThe nearest, and what each misses:\n" + "\n".join(
                "• %s (%s): misses %s%s%s" % (r["name"], r["make"], ", ".join(dict.fromkeys(r["misses"])),
                                              "; does not state %s" % ", ".join(dict.fromkeys(r["unstated"])) if r["unstated"] else "",
                                              (" - " + ", ".join(r["facts"])) if r["facts"] else "")
                for r in found["near_rows"])
    if found["partial"]:
        names = ", ".join(sorted({u for r in found["partial_rows"] for u in r["unstated"]}))
        notes.append("%d more could not be checked, because they do not state %s - not counted above." % (len(found["partial"]), names or "a value"))
    if any(r["estimated"] for r in found["rows"][:12] + found["near_rows"]):
        notes.append("(est.) is the published source efficacy x wattage: this make prints no lumen figure, and a fitting delivers less "
                     "than its source. Confirm on the spec sheet before specifying on it.")
    if "price" in reading and sum(k in reading for k in ("wattage", "lumens", "cct", "beam", "control", "ip", "cri", "ugr")):
        notes.append("Prices are each product's lowest in NPD. The options you asked for can cost more, so check the price before quoting it.")
    several = sum(1 for p in found["meets"] if len(p.get("cut") or ()) > 1) if found.get("meets") else 0
    if several and "cutout" in reading and sum(k in reading for k in ("lumens", "wattage")):
        notes.append("%d of these come in several cut-outs. Check that the size meeting the cut-out also gives the output you need." % several)
    if reading.get("left"):
        notes.append("Not understood, so not applied: \"%s\". Check the results still fit." % " ".join(dict.fromkeys(reading["left"])))
    if dropped:
        notes.append("The AI model also read this as: %s. Nothing met that, so it was left out and only your own words were applied." % "; ".join(dropped))
    if wants and not total and not found["near_rows"] and not found["partial"]:
        notes.append("The catalogue has nothing of this make and kind at all.")
    if reading.get("by_model") and reading.get("model_keys"):
        notes.append("The AI model read this wording; the products and their values are still the catalogue's. Check the reading above.")
    shown = found["rows"][:12] if total else found["near_rows"]
    return {"answer": text, "reading": shown_reading, "rows": found["rows"], "count": total, "notes": notes, "asked_model": bool(reading.get("model_called")),
            "model": llm_extract.LAST_MODEL if reading.get("model_called") else "", "from_catalogue": True, "records_given": 0,
            "sources": [{"id": "P%d" % r["id"], "kind": "product", "ref": r["id"], "title": "%s (%s)" % (r["name"], r["make"])} for r in shown]}


# --- when the rules could not read it -------------------------------------------------------------

INTERPRET_TOKENS = 400
INTERPRET_PROMPT = (
    "You turn a lighting designer's request into search constraints for a luminaire catalogue. You do not "
    "answer it. Return ONLY a JSON object with any of these keys, and only what the request states or "
    "clearly implies:\n"
    '{"make": "", "typology": "", "lumens": {"op": "about|under|max|over|min", "value": 0}, "wattage": {"op": "", "value": 0}, '
    '"cutout_mm": {"op": "", "value": 0}, "cct_low": 0, "cct_high": 0, "beam_low": 0, "beam_high": 0, "ip_min": "65", '
    '"cri_min": 0, "ugr_max": 0, "control": "", "not_a_product_search": false}\n'
    "\n"
    "Rules:\n"
    "1. lumens, wattage and cutout_mm values must be numbers written in the request. Never work one out.\n"
    "2. make must be one of MAKES, typology one of KINDS, control one of DALI, 0-10V, Phase Cut, Zigbee, Casambi, DMX, "
    "On/Off. Leave out what does not fit.\n"
    "3. Meanings: warm white 2700-3000K; neutral 3500-4000K; cool white 4000-5000K; daylight 5000-6500K; narrow beam up to "
    "20 degrees; wide beam 40 and over; bathroom or damp IP44; outdoors or wet IP65; underwater IP68; low glare UGR 19; high "
    "colour rendering CRI 90. 'a small hole' or a hole size is cutout_mm only when a number is written.\n"
    "4. If it asks about a BOQ, a project, a client, or one named product's details, return {\"not_a_product_search\": true}.\n"
    "\n"
    'Example: "need something for a bathroom ceiling, warm, not more than 15 watts, duragreen" -> '
    '{"make": "Duragreen", "typology": "downlight", "wattage": {"op": "max", "value": 15}, "cct_low": 2700, "cct_high": 3000, "ip_min": "44"}\n'
    'Example: "how much did we charge the Shah family for NYX?" -> {"not_a_product_search": true}\n'
    "Return the JSON object and nothing else."
)
_INTERPRET_OPS = {"about": "about", "under": "under", "max": "up to", "over": "over", "min": "at least"}


def interpret(question, catalogue) -> dict:
    """Constraints the model proposes for wording the rules could not read. Held to what the person
    wrote: a lumen, wattage or cut-out number must be one the rules left unread in the sentence, so
    "3000K" cannot come back as 3000 lm; a CCT, beam, IP, CRI or UGR must be written there or be one of
    the trade meanings in the tables above; a make must be spelled nearly as in the sentence. A kind of
    fitting may be inferred ("in the ceiling"), because it is marked as the model's in the reading, and
    dropped if it empties the search. A physical feature may not: the rules read those from the words,
    and "adjustable" from "gentle on the eyes" narrowed a search on something nobody said. Returns {} when it fails or adds nothing. One request."""
    user = "MAKES: %s\nKINDS: %s\n\nREQUEST: %s" % (", ".join(m for m, _p in catalogue["makes"]), ", ".join(catalogue["kinds"]), question)
    content = llm_extract.chat(INTERPRET_PROMPT, user, INTERPRET_TOKENS, 45)
    data = llm_extract._parse_json_object(content) if content else None
    if not isinstance(data, dict):
        return {}
    if data.get("not_a_product_search"):
        return {"not_search": True}
    unread = read_request(question, catalogue).get("rest") or ""
    written = {_number(n) for n in re.findall(_NUM, unread)}
    out = {}
    make = str(data.get("make") or "").strip().lower()
    words = re.findall(r"[a-z0-9]+", _fold(question))
    for name, _pattern in catalogue["makes"]:
        if name.lower() == make and difflib.get_close_matches(make.replace(" ", ""), words, n=1, cutoff=0.75):
            out["make"] = {"values": [name], "says": name}
    kind = typology_key(str(data.get("typology") or ""))
    if kind in catalogue["kinds"]:
        out["typology"] = {"keys": [kind], "says": kind}
    for source, target in (("lumens", "lumens"), ("wattage", "wattage"), ("cutout_mm", "cutout")):
        part = data.get(source)
        if isinstance(part, dict):
            try:
                value = float(part.get("value"))
            except (TypeError, ValueError):
                continue
            if value in written and value > 0:
                out[target] = _bound(target, value, _INTERPRET_OPS.get(str(part.get("op") or "").lower(), ""), "")
    for key, low, high, lo_limit, hi_limit in (("cct", "cct_low", "cct_high", 1000, 10000), ("beam", "beam_low", "beam_high", 1, 180)):
        try:
            a, b = float(data.get(low) or 0), float(data.get(high) or 0)
        except (TypeError, ValueError):
            continue
        table = CCT_WORDS if key == "cct" else BEAM_WORDS
        known = (a, b) in [tuple(band) for _p, band, _l in table] or (a in written and b in written)
        if a and b and lo_limit <= a <= b <= hi_limit and known:
            out[key] = {"lo": a, "hi": b, "target": None, "says": _say(key, a, b)}
    ip_min = str(data.get("ip_min") or "")
    if ip_min in {digits for _p, digits, _l in PLACE_WORDS} or (re.fullmatch(r"\d\d", ip_min) and float(ip_min) in written):
        out["ip"] = {"digits": ip_min, "says": "IP%s or better" % ip_min}
    for key, source, low, high, words in (("cri", "cri_min", 50, 100, "CRI %d or better"), ("ugr", "ugr_max", 5, 34, "UGR %d or lower")):
        try:
            n = int(data.get(source) or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        meant = {value for _p, k, value, _l in QUALITY_WORDS if k == key}
        if low <= n <= high and (n in meant or n in written):
            out[key] = {"n": n, "says": words % n}
    for pattern, wanted, label in CONTROL_WORDS:
        if str(data.get("control") or "") in wanted and len(wanted) == 1:
            out["control"] = {"any": wanted, "says": label}
    return out


def could_use_model(question, reading) -> bool:
    """Whether the model could help read this: words the rules did not understand, in something that
    is not about a BOQ, a comparison or one named product, and that looks like a search - or a question
    for the records would pay for a second request. Reading it costs one request."""
    if goes_to_records(question, reading) or "named_ids" in reading or "series" in reading or not reading.get("left"):
        return False
    return any(k in reading for k in CONSTRAINT_KEYS + ("typology", "make", "not_make")) or bool(_SEARCHING.search(question))


def merge_reading(reading, proposed) -> dict:
    """What the rules read stands; the model only fills what they left. It marks the reading, so the
    page says a request was spent, whatever came back. Words the rules could not read stay reported:
    nothing says the model's reading accounts for them."""
    merged = dict(reading, model_called=True)
    if proposed.get("not_search"):
        merged["not_search"] = True
        return merged
    added = {k: v for k, v in proposed.items() if k not in reading}
    if added:
        merged.update(added)
        merged["by_model"] = True
        merged["model_keys"] = list(added)
    return merged


# --- the Match tab: a sentence or two, as requirements ---------------------------------------------

_NOT_HOW_MANY = (r"(?!\s*(?:(?:w|k|v|lm|mm|cm|watts?|wattage|lumens?|kelvin|volts?|amps?|inch(?:es)?|deg|degrees?|°|x\s*\d|of light|cut[ -]?out|hole|gap|opening|aperture|beam|optic)(?![a-z])"
                 r"|(?:cri|ra|ugr|ip)(?![a-z0-9])(?!\s*\d)))")
_CLAUSES = re.compile(r"(?:\n+|;\s*|,\s+(?=\d{1,4}" + _NOT_HOW_MANY + r"\s+[a-z0-9])|(?<=[a-z0-9°\)])(?<!max)(?<!min)(?<!approx)(?<!qty)(?<!nos)(?<!\bno)\.\s+|\s+(?:and|plus|also|along with)\s+(?=(?:\d{1,4}" + _NOT_HOW_MANY + r"\s+|a\s+few\b)))", re.I)
_QTY = re.compile(r"^\s*(?:qty\.?\s*:?\s*)?(\d{1,4})" + _NOT_HOW_MANY + r"\s*(?:nos?\.?|pcs?\.?|pieces?|units?|numbers?|x\b)?\s+(?=[a-z0-9])|\b(?:qty\.?\s*:?\s*|(?<!\d\s)x\s*)(\d{1,4})\b" + _NOT_HOW_MANY + r"|\b(\d{1,4})\s*(?:nos?\.?|pcs?\.?|pieces?|units?)\b", re.I)
_AREA = re.compile(r"^\s*(?:in\s+(?:the\s+)?|for\s+(?:the\s+)?)?([a-z0-9][\w &'/-]{2,50}?)\s*(?:needs?|requires?|:|-\s)(?:\s+|$)", re.I)
# The whole phrase, not its first word: "Control room", "Client lounge" and "Make-up room" are places.
_NOT_AN_AREA = re.compile(r"(?:we|i|they|you|he|she|it|client|customer|team|please|this|that|project|site|note|notes|remarks?|attention|kindly|fyi|ps"
                          r"|ceiling|false ceiling|walls?|floor|ground|background|foreground|cove|market|budget|price"
                          r"|quantity|qty|finish|colou?r|control|dimming|make|brand|mounting|ip|cri|ra|ugr|size|dia(?:meter)?|"
                          + "|".join(_LABELS.values()) + r")", re.I)
_PERSON = re.compile(r"(?:mr|mrs|ms|dr|sir|madam|shri|smt)\b", re.I)
# "4 pendants in the lobby" names its own place, which ends the area carried from the row above.
_ASKS_FOR_DRIVER = re.compile(r"\b(?:drivers?|power suppl(?:y|ies)|transformers?)\b", re.I)
_PLACE_AT_START = re.compile(r"^\s*(?:then\s+)?(?:in|for|at)\s+the\s+([a-z][\w &'/-]{2,40}?)(?:\s*,\s*|\s+(?=\d))", re.I)
_PLACE_AT_END = re.compile(r"\b(?:in|for|at)\s+the\s+([a-z][\w &'/-]{2,40}?)\s*[.!]?$", re.I)
_QTY_BEFORE_KIND = re.compile(r"(?<![\w.])(\d{1,4})" + _NOT_HOW_MANY + r"\s+(?=(?:[a-z-]+\s+){0,3}(?:" + "|".join(p for p, _k, _l in TYPOLOGY_WORDS) + r")(?![a-z]))", re.I)
# A paste longer than this is a document, however plainly it is written.
SENTENCES_CHARS = 2000
SENTENCES_MAX = 40
_MATCH_FIELD_TOO_LONG = 400
# A plain "75 mm" or "3 inch" on these is a hole. On a linear light it is a length, which Match cannot check.
HOLE_KINDS = ("downlight", "spotlight", "track light", "step light")


def looks_like_sentences(blocks, method, text="") -> bool:
    """Whether what was typed is a person's sentences rather than a schedule: the document reader
    found no structure, or only short paragraphs. A real requirement runs to several hundred
    characters, labels and all, and is read as it always has been."""
    if len(str(text)) > SENTENCES_CHARS:
        return False
    if method in ("none", "whole text"):
        return True
    return method == "paragraphs" and all(len(b) < _MATCH_FIELD_TOO_LONG for b in blocks)


def _match_value(kind, want):
    """A constraint in the words the Match tab reads, which keeps "at most" apart from "about". Always
    the same range Ask searches, with no tolerance added on top, so the two tabs give the same products."""
    unit = {"lumens": "lm", "wattage": "W", "cutout": "mm", "cct": "K", "beam": "°"}[kind]
    suffix = "/m" if want.get("per_metre") else ""
    want = dict(want)
    if not want.get("lo"):
        want["lo"] = None
    if kind == "beam" and (want.get("hi") or 0) >= 180:
        want["hi"] = None
    if want.get("lo") is not None and want.get("lo") == want.get("hi"):
        return "%g%s%s" % (want["lo"], unit, suffix)
    if want.get("lo") is not None and want.get("hi") is not None:
        return "%g-%g%s%s" % (round(want["lo"], 2), round(want["hi"], 2), unit, suffix)
    if want.get("lo") is None:
        return ("<%g%s%s" % (round(want["hi"] + 0.01, 2), unit, suffix)) if want.get("strict") else "≤%g%s%s" % (want["hi"], unit, suffix)
    return (">%g%s%s" % (round(want["lo"] - 0.01, 2), unit, suffix)) if want.get("strict") else "≥%g%s%s" % (want["lo"], unit, suffix)


def match_fields(reading) -> dict:
    """The Match tab's fields for this reading - only what its rows can check."""
    fields = {}
    if reading.get("lumens"):
        fields["lumen_output"] = _match_value("lumens", reading["lumens"])
    if reading.get("wattage"):
        fields["wattage"] = _match_value("wattage", reading["wattage"])
    if reading.get("cct"):
        fields["cct"] = _match_value("cct", reading["cct"])
    if reading.get("beam"):
        fields["beam_angle"] = _match_value("beam", reading["beam"])
    if reading.get("cutout"):
        fields["cutout"] = _match_value("cutout", reading["cutout"])
    elif reading.get("size") and set((reading.get("typology") or {}).get("keys") or ()) & set(HOLE_KINDS):
        fields["cutout"] = _match_value("cutout", reading["size"])
    if reading.get("ip"):
        fields["ip_rating"] = "IP" + reading["ip"]["digits"]
    if reading.get("cri"):
        fields["cri"] = str(reading["cri"]["n"])
    if reading.get("ugr"):
        fields["ugr"] = str(reading["ugr"]["n"])
    wanted = (reading.get("control") or {}).get("any") or []
    if len(wanted) == 1:
        fields["control"] = wanted[0]
    return fields


def requirements_from_text(text, catalogue, use_model=False) -> tuple:
    """-> (blocks for the Match tab, ignored). Each sentence or clause naming a fitting is one row,
    with its own area and quantity, and what it did not say is left blank. A clause with no requirement
    in it is reported as left out, as the document reader does."""
    blocks, ignored, area, heading = [], [], "", ""
    # Runs of spaces go first, so no pattern below can spend its time on them.
    clauses = [" ".join(c.split()) for c in _CLAUSES.split(re.sub(r"[ \t]+", " ", str(text or "")))]
    clauses = _join_continuations([c for c in clauses if c], catalogue)
    for n, clause in enumerate(clauses):
        if n >= SENTENCES_MAX:
            # Nothing vanishes: a quotation answering fewer lines than were asked for is worse than a
            # wrong one, so what was not read is listed.
            ignored.append({"text": clause, "reason": "over the %d rows read from one paste - paste the rest separately" % SENTENCES_MAX, "page": None})
            continue
        body, own_area, heading = clause, heading, ""
        at = _AREA.match(body)
        first = _PLACE_AT_START.match(body)
        place = _PLACE_AT_END.search(body)
        if at and _is_area(at.group(1), catalogue):
            own_area = area = at.group(1).strip()
            body = body[at.end():]
            if not body:
                heading = area
                continue
        elif first and _is_area(first.group(1), catalogue):
            own_area = area = _room_name(first.group(1))
            body = body[first.end():]
        elif place and _is_area(place.group(1), catalogue):
            own_area = area = _room_name(place.group(1))
            body = body[:place.start()].strip()
            if not body:                # "In the kitchen, 6 downlights": a comma before a count split it
                heading = area
                continue
        quantity, body = _take_quantity(body, catalogue)
        reading = read_request(body, catalogue)
        block = _row_for(clause, area, quantity, reading, catalogue)
        # "Bathroom: 6 downlights" asks what "6 downlights for a bathroom" asks, and Ask reads both
        # alike. Only from the row's own words: carried onward, one room's protection would narrow the
        # products for the next, and would turn "Please send by Friday" into a row.
        protection = read_request(own_area, catalogue).get("ip") if own_area else None
        if block and protection and "ip" not in reading:
            reading["ip"] = protection
            block = _row_for(clause, area, quantity, reading, catalogue)
        if block:
            blocks.append(block)
        else:
            ignored.append({"text": clause, "reason": "no product asked for in this sentence", "page": None})
    # One request for the whole paste, never one per sentence: the model is for wording the rules
    # could read nothing from, and this is the case the Match route's own comments warn about.
    if use_model and not blocks and str(text or "").strip():
        wording = " ".join(str(text).split())[:500]
        reading = read_request(wording, catalogue)
        reading = merge_reading(reading, interpret(wording, catalogue))
        block = _row_for(wording, "", "", reading, catalogue)
        if block:
            return [block], []
    return blocks, ignored


def _room_name(words):
    words = words.strip()
    return words[:1].upper() + words[1:]


def _take_quantity(body, catalogue):
    """(how many, the rest). A number that opens a product's name ("75 ZOOM") or follows a label that
    wants one ("CRI 90") is not how many: 90 of something on a BOQ is an easy mistake to miss."""
    times = re.match(r"\s*(\d{1,4})\s*[x×]\s*", body)
    if times and catalogue["name_pattern"] and catalogue["name_pattern"].match(_fold(body[times.end():])):
        return times.group(1), body[times.end():]
    for pattern in (_QTY, _QTY_BEFORE_KIND):
        # Every candidate, not only the first: in "75 ZOOM 10 nos" the first is the name, and a stated
        # quantity left behind would quietly become 1 on the BOQ.
        for q in pattern.finditer(body):
            group = next(i for i in range(1, q.re.groups + 1) if q.group(i))
            at = q.start(group)
            folded = _fold(body[at:])
            name = catalogue["name_pattern"].match(folded) if catalogue["name_pattern"] else None
            if name and not _PLURAL_KIND_AHEAD.match(folded, name.end()):
                continue
            before = _fold(body[max(0, at - 40):at])
            # "about 10 downlights" is a rough count; "CRI 90" and "under 100" are values.
            if (_LIMIT_BEFORE.search(before) and not _ROUGHLY_BEFORE.search(before)) or re.search(r"\d\s*[x×*]$", before):
                continue
            return q.group(group), (body[:q.start()] + " " + body[q.end():]).strip()
    return "", body


def _join_continuations(clauses, catalogue):
    """A fixture is often written over several lines: "Recessed downlight 12W 3000K", then "Cut-out 75
    mm, IP44". A line naming no fitting, make or quantity, and repeating nothing the line above said,
    belongs to that line. "12W 3000K" then "15W 4000K" repeats, so those are two rows."""
    out, said, after_heading = [], set(), False
    for clause in clauses:
        reading = read_request(clause, catalogue)
        keys = {k for k in CONSTRAINT_KEYS if k in reading}
        at = _AREA.match(clause)
        is_area = bool(at and _is_area(at.group(1), catalogue))
        place = _PLACE_AT_START.match(clause) or _PLACE_AT_END.search(clause)
        names_something = (any(k in reading for k in ("typology", "named_ids", "series", "make"))
                           or is_area or (place and _is_area(place.group(1), catalogue)) or _take_quantity(clause, catalogue)[0])
        # Words it could not read may name a different fitting: "DALI driver 20W" under a row of
        # downlights once made them 20W DALI downlights. Those stand as their own row.
        unread = reading.get("left") or _NOT_LUMINAIRE.search(_fold(clause))
        if out and keys and not names_something and not unread and not after_heading and not keys & said:
            out[-1] += ", " + clause
            said |= keys
        else:
            out.append(clause)
            said = keys
        after_heading = is_area and not clause[at.end():].strip()
    return out


def _is_area(words, catalogue) -> bool:
    """"Ground floor lobby" is an area. "Pendant", "12W" and "We" are not."""
    words = re.sub(r"^(?:the|our|my)\s+", "", words.strip(), flags=re.I)
    if _NOT_AN_AREA.fullmatch(words) or _PERSON.match(words):
        return False
    reading = read_request(words, catalogue)
    # A room can name its own protection ("Bathroom", "Terrace"), and is still a room.
    keys = [k for k in CONSTRAINT_KEYS if k in reading and not (k == "ip" and not re.search(r"\d", words))]
    return not (keys or any(k in reading for k in ("typology", "make", "named_ids", "series")))


def _row_for(clause, area, quantity, reading, catalogue):
    """One Match row for what this clause asks, or None when it asks for no product."""
    fields = match_fields(reading)
    only = set(reading.get("named_ids") or []) or None
    for item in reading.get("series") or []:
        only = set(item["ids"]) if only is None else only & set(item["ids"])
    # A quantity says a product is wanted, even one the reader does not know ("2 mirror lights").
    if not fields and "typology" not in reading and not only and "make" not in reading and not quantity:
        return None
    kinds = (reading.get("typology") or {}).get("keys") or []
    named = [catalogue["by_id"][i] for i in sorted(only or ()) if i in catalogue["by_id"]]
    if not kinds and named:
        kinds = sorted({p["t"] for p in named if p["t"]})
    makes = (reading.get("make") or {}).get("values") or []
    unchecked = [f["says"] for f in reading.get("features") or []]
    unchecked += [reading[k]["says"] for k in ("finish", "tunable", "price", "order") if k in reading]
    if reading.get("size") and "cutout" not in fields:
        unchecked.append(reading["size"]["says"])
    if len((reading.get("control") or {}).get("any") or []) > 1:
        unchecked.append(reading["control"]["says"])
    if len(makes) > 1:
        unchecked.append(reading["make"]["says"])
    excluded = ["not " + m for m in (reading.get("not_make") or []) + (reading.get("not_names") or [])]
    by_model = describe({k: reading[k] for k in reading.get("model_keys") or [] if k in reading})
    notes = []
    if unchecked:
        notes.append("Not checked in Match: %s." % ", ".join(unchecked))
    if excluded:
        notes.append("Left out as you asked: %s." % ", ".join(excluded))
    if by_model:
        notes.append("Read by the AI model, so check it: %s." % "; ".join(by_model))
    unread = list(dict.fromkeys(reading.get("left") or []))
    if unread:
        notes.append("Not understood: %s." % " ".join(unread))
    return {"block": clause, "fields": fields, "area": area, "quantity": quantity, "label": "", "page": "",
            "warning": " ".join(notes), "make": makes[0] if len(makes) == 1 else "",
            "typology_key": kinds[0] if kinds else "", "kinds": kinds, "any_typology": not kinds,
            "shape": (reading.get("shape") or {}).get("value", ""), "only_ids": sorted(only or ()), "sentence": True,
            "kind": "driver" if not kinds and not only and _ASKS_FOR_DRIVER.search(clause) else "fixture",
            # Anything it could not check, or a guess at the wording, is for a person to pick on.
            "no_auto": bool(unchecked or by_model), "not_ids": sorted(set(reading.get("not_ids") or [])),
            "not_make": reading.get("not_make") or []}


def as_json(reading) -> str:
    """A reading for logs and the evaluation script."""
    return json.dumps({k: v for k, v in reading.items() if k != "named_ids"}, ensure_ascii=False, sort_keys=True)
