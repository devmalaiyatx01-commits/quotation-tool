"""The catalogue, reduced to what the Match tab compares: numbers and ranges per product.

Why this exists
---------------
The Match tab lets a person move a tolerance slider ("wattage within 20%") and see every
requirement's candidates change at once. That only works if matching runs in the browser, and
matching can only run in the browser if the catalogue's values are already numbers. They are
not. The same fact is written a dozen ways across four manufacturers:

    CCT      "2700K / 3000K"   "2700K-6000K"   "Dynamic White (2700K to 6500K)"   "22K to 65K"
    beam     "15°/24°/36°"     "Narrow Flood 24°"   "Zoomable 20°-50°"   "Elliptical Narrow Spot"
    lumens   "700 lm / 1000 lm"   "11 000lm"   "1400 lm/m"   "85 -510lm"
    CRI      "CRI92 / CRI98"   "> 90 (R9 > 90)"   "CRI 90+ (White Light Only)"
    IP       "IP20 internal compartment IP54 optical housing"   "Front IP44, optional IP65"

So the server reads them once, here, and hands the page intervals: every stated value becomes
[low, high] - a single value is [v, v], a tunable or zoom range is [low, high]. The page then
only ever asks "how far is this interval from the requirement", which is the same question for
a fixed 3000K product and a 2700-6500K tunable one.

What is deliberately NOT done
-----------------------------
A value with no number in it ("Elliptical Narrow Spot", "Regular Output", "Remote driver") says
nothing measurable, so it contributes nothing - the product is "not stated" for that field, and
the page shows that plainly instead of guessing. Per-metre values (strips: "5 W/M", "1400 lm/m")
are kept apart from per-unit ones and never compared with them: 5 W/m and a 5 W downlight are
not the same five watts.

Lumenpulse writes CCT in hundreds ("30K" is 3000K, "22K to 65K" is 2200-6500K). That shorthand
is read only for two-digit numbers between 18 and 65 followed by K, which is exactly the range
colour temperatures occupy in it and no other reading of "30K" exists in this catalogue.
"""
import re
from functools import lru_cache

from app.shape_classifier import classify_specsheet_shape
from app.typology_match import typology_key

# --- small helpers ---------------------------------------------------------------------------

_DASH = r"\s*(?:-|–|—|to)\s*"


def _num(text):
    """'11 000' -> 11000.0, '1,500' -> 1500.0, '3.9' -> 3.9, '4,5' -> 4.5."""
    t = text.strip()
    if re.fullmatch(r"\d{1,3}(?:[ ,]\d{3})+", t):
        return float(re.sub(r"[ ,]", "", t))
    return float(t.replace(",", "."))


def _merge(intervals):
    """Sorted, de-duplicated intervals, rounded so the page gets short numbers. Tuples, because
    the readers below are cached and hand the same result to every product with the same text -
    nothing may change it in place."""
    return tuple(sorted({(round(lo, 2), round(hi, 2)) for lo, hi in intervals}))


def _texts(fields, options, *names):
    """Every raw string the product states for these field names: the split-out options list
    and the combined field text. Both, because each is sometimes the only one with the number -
    a strip's wattage options can come out as ["5 W", "M"] while the field says "5 W/M"."""
    out = []
    for name in names:
        value = (fields or {}).get(name)
        if value:
            out.append(str(value))
        for opt in (options or {}).get(name) or []:
            if opt:
                out.append(str(opt))
    return tuple(out)


# --- per-field readers -----------------------------------------------------------------------

_WATT_RANGE_RE = re.compile(r"(\d+(?:[.,]\d+)?)" + _DASH + r"(\d+(?:[.,]\d+)?)\s*W(?![a-z])(\s*/\s*(?:m|mtrs?|mts?|rmt?|rmtrs?|meters?|metres?)\b)?", re.I)
_WATT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*W(?![a-z])(\s*/\s*(?:m|mtrs?|mts?|rmt?|rmtrs?|meters?|metres?)\b)?", re.I)


@lru_cache(maxsize=4096)
def read_wattage(texts):
    """(per-unit intervals, per-metre intervals)."""
    unit, metre = [], []
    for text in texts:
        for m in _WATT_RANGE_RE.finditer(text):
            (metre if m.group(3) else unit).append((_num(m.group(1)), _num(m.group(2))))
        rest = _WATT_RANGE_RE.sub(" ", text)
        for m in _WATT_RE.finditer(rest):
            v = _num(m.group(1))
            if v > 0:
                (metre if m.group(2) else unit).append((v, v))
    return _merge(unit), _merge(metre)


# "11 000lm" and "1,500 lm" as well as "800lm"; the thousands form is tried first so that
# "11 000lm" is not read as "000lm".
_LM_NUMBER = r"(\d{1,3}(?:[ ,]\d{3})+|\d+(?:\.\d+)?)"
_LM_RANGE_RE = re.compile(_LM_NUMBER + _DASH + _LM_NUMBER + r"\s*lm\b(\s*/\s*(?:m|mtrs?|mts?|rmt?|rmtrs?|meters?|metres?)\b)?", re.I)
_LM_RE = re.compile(_LM_NUMBER + r"\s*lm\b(\s*/\s*(?:m|mtrs?|mts?|rmt?|rmtrs?|meters?|metres?)\b)?", re.I)


@lru_cache(maxsize=4096)
def read_lumens(texts):
    """(per-unit intervals, per-metre intervals)."""
    unit, metre = [], []
    for text in texts:
        for m in _LM_RANGE_RE.finditer(text):
            (metre if m.group(3) else unit).append((_num(m.group(1)), _num(m.group(2))))
        rest = _LM_RANGE_RE.sub(" ", text)
        for m in _LM_RE.finditer(rest):
            v = _num(m.group(1))
            if v > 0:
                (metre if m.group(2) else unit).append((v, v))
    return _merge(unit), _merge(metre)


_CCT_RANGE_RE = re.compile(r"(\d{4})\s*K?" + _DASH + r"(\d{4})\s*K", re.I)
_CCT_SHORT_RANGE_RE = re.compile(r"\b(\d{2})\s*K" + _DASH + r"(\d{2})\s*K\b", re.I)
_CCT_RE = re.compile(r"\b(\d{4})\s*K\b", re.I)
_CCT_SHORT_RE = re.compile(r"\b(\d{2})\s*K\b", re.I)


def _short_cct(n):
    v = int(n)
    return v * 100 if 18 <= v <= 65 else None


@lru_cache(maxsize=4096)
def read_cct(texts):
    out = []
    for text in texts:
        for m in _CCT_RANGE_RE.finditer(text):
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            out.append((lo, hi))
        rest = _CCT_RANGE_RE.sub(" ", text)
        for m in _CCT_SHORT_RANGE_RE.finditer(rest):
            lo, hi = _short_cct(m.group(1)), _short_cct(m.group(2))
            if lo and hi:
                out.append(tuple(sorted((lo, hi))))
        rest = _CCT_SHORT_RANGE_RE.sub(" ", rest)
        for m in _CCT_RE.finditer(rest):
            v = int(m.group(1))
            if 1000 <= v <= 10000:
                out.append((v, v))
        rest = _CCT_RE.sub(" ", rest)
        for m in _CCT_SHORT_RE.finditer(rest):
            v = _short_cct(m.group(1))
            if v:
                out.append((v, v))
    return _merge(out)


_DEG = r"\s*(?:°|˚|º|deg(?:rees?)?\b)"
_BEAM_RANGE_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)" + r"(?:" + _DEG + r")?" + _DASH + r"(\d{1,3}(?:\.\d+)?)" + _DEG, re.I)
_BEAM_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)" + _DEG, re.I)


@lru_cache(maxsize=4096)
def read_beam(texts):
    out = []
    for text in texts:
        for m in _BEAM_RANGE_RE.finditer(text):
            lo, hi = sorted((float(m.group(1)), float(m.group(2))))
            if 0 < lo and hi <= 180:
                out.append((lo, hi))
        rest = _BEAM_RANGE_RE.sub(" ", text)
        for m in _BEAM_RE.finditer(rest):
            v = float(m.group(1))
            if 0 < v <= 180:
                out.append((v, v))
    return _merge(out)


@lru_cache(maxsize=4096)
def read_cutout(texts):
    """A cut-out as written ("68mm", "36mm / 40mm", "80*80mm", "105 mm x 55 mm") -> the hole needed
    for each size offered: its largest dimension, since a fitting's long side has to go through."""
    out = []
    scale = {"cm": 10.0, "inch": 25.4, "inches": 25.4, '"': 25.4}
    for text in texts:
        # "W:34mm * H:>43mm" names a body's sides, not the hole: not stated, rather than a guess.
        if re.search(r"[a-z]\s*:", text, re.I):
            continue
        for part in re.split(r"/|\bor\b", text):
            numbers = [float(n) * scale.get(unit.lower(), 1.0)
                       for n, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(mm|cm|inch(?:es)?|\")?", part)]
            if numbers and max(numbers) >= 5:
                out.append((max(numbers), max(numbers)))
    return _merge(out)


_R9_RE = re.compile(r"R9\s*[>≥=]*\s*\d+", re.I)


@lru_cache(maxsize=4096)
def read_cri(texts):
    """Every CRI value offered. "R9 > 90" is a different index and is removed first."""
    out = set()
    for text in texts:
        for m in re.finditer(r"(?<![\d.])(\d{2,3})(?![\d.])", _R9_RE.sub(" ", text)):
            v = int(m.group(1))
            if 50 <= v <= 100:
                out.add(v)
    return tuple(sorted(out))


@lru_cache(maxsize=4096)
def read_ugr(texts):
    out = set()
    for text in texts:
        for m in re.finditer(r"(?<![\d.])(\d{1,2})(?![\d.])", text):
            v = int(m.group(1))
            if 5 <= v <= 34:
                out.add(v)
    return tuple(sorted(out))


_IP_RE = re.compile(r"IP\s?[-:]?\s?(\d)(\d)\b", re.I)


@lru_cache(maxsize=4096)
def read_ip(texts):
    """Every IP rating the product can be had in, as "65" strings. A product stating two (an
    IP20 driver compartment and an IP54 optic, or "IP44, optional IP65") offers each."""
    out = set()
    for text in texts:
        for m in _IP_RE.finditer(text):
            out.add(m.group(1) + m.group(2))
    return tuple(sorted(out))


# One protocol per keyword, checked in this order. "Mains Dim" is phase-cut dimming by another
# name; "Remote driver" names no protocol at all and so reads as nothing.
CONTROL_KEYWORDS = [
    ("DALI", "DALI"),
    ("0/1-10", "0-10V"), ("0-10", "0-10V"), ("1-10", "0-10V"),
    ("PHASE", "Phase Cut"), ("TRIAC", "Phase Cut"), ("MAINS DIM", "Phase Cut"),
    ("LEADING EDGE", "Phase Cut"), ("TRAILING EDGE", "Phase Cut"),
    ("ZIGBEE", "Zigbee"),
    ("CASAMBI", "Casambi"),
    ("DMX", "DMX"),
    ("PUSH", "Push"),
    ("ON/OF", "On/Off"), ("ON-OFF", "On/Off"), ("ON–OFF", "On/Off"), ("ON / OFF", "On/Off"),
    ("NON DIM", "On/Off"), ("NON-DIM", "On/Off"), ("NO DIMMING", "On/Off"),
]


@lru_cache(maxsize=4096)
def read_control(texts):
    out = set()
    for text in texts:
        upper = text.upper()
        for keyword, canonical in CONTROL_KEYWORDS:
            if keyword in upper:
                out.add(canonical)
    return tuple(sorted(out))


# --- the index -------------------------------------------------------------------------------

def _estimated_lumens(fields, options, params):
    """Lumens computed from a published efficacy x each wattage option, for makes that publish
    efficacy but no lumen figure - the same estimate the old matcher made, marked as one."""
    from types import SimpleNamespace
    from app.requirement_matcher import _lumen_output_options
    values = _lumen_output_options(SimpleNamespace(fields=fields, options=options, params=params)) or []
    if not any("estimated" in v for v in values):
        return ()
    return read_lumens(tuple(values))[0]


def _per_metre_honest(reader, fields, options, *names):
    """(per unit, per metre), without the per-unit values a split options list invents.

    A strip's field says "14.4W/M"; its options list, split on "/", says "14.4W" and "M". Read
    together, that is a per-metre 14.4W and a per-unit 14.4W the product never offered - which
    would let a strip match a 14W downlight requirement. When the field's own text gives only
    per-metre values, per-unit readings that only the options produce are those split pieces."""
    unit, metre = reader(_texts(fields, options, *names))
    field_unit, field_metre = reader(_texts(fields, {}, *names))
    if field_metre and not field_unit:
        unit = ()
    return unit, metre


def product_entry(sheet, params=None) -> dict:
    """One product as the Match tab sees it. Keys are short because 1,200 of these are sent in
    one response; a field the product does not state is simply absent.

    `params` is passed in rather than read off `sheet`: it is the one bulky column, needed only
    for the lumen estimate, and build_index fetches it for just the rows that need one."""
    fields, options = sheet.fields or {}, sheet.options or {}
    entry = {
        "id": sheet.id,
        "t": typology_key(sheet.typology or ""),
        "shape": classify_specsheet_shape(sheet),
        "make": sheet.make or "",
        "name": sheet.product_name or "",
    }
    w, wm = _per_metre_honest(read_wattage, fields, options, "wattage")
    lm, lmm = _per_metre_honest(read_lumens, fields, options, "lumen_output", "lumen_package")
    estimated = False
    if not lm and not lmm and params:
        lm = _estimated_lumens(fields, options, params)
        estimated = bool(lm)
    values = {
        "w": w, "wm": wm, "lm": lm, "lmm": lmm,
        "k": read_cct(_texts(fields, options, "cct")),
        "b": read_beam(_texts(fields, options, "beam_angle")),
        "cri": read_cri(_texts(fields, options, "cri")),
        "ugr": read_ugr(_texts(fields, options, "ugr")),
        "ip": read_ip(_texts(fields, options, "ip_rating")),
        "ctl": read_control(_texts(fields, options, "control")),
        "cut": read_cutout(_texts(fields, {}, "cutout_dimensions")),
    }
    entry.update({k: v for k, v in values.items() if v})
    if estimated:
        entry["lme"] = 1
    return entry


def build_index(db, discipline: str) -> dict:
    """Every product the Match tab may suggest, and the typologies they fall into.

    The same products the Library shows: this tool's own catalogue, approved, drivers left out.
    Two queries, never one per row. `params` (scraped spec tables, the bulky column) is read only
    for products that state no lumen figure of their own, since the estimate is all it is for."""
    from sqlalchemy.orm import load_only
    from app import models
    S = models.Specsheet
    sheets = (db.query(S)
              .options(load_only(S.id, S.typology, S.make, S.product_name, S.series, S.family,
                                 S.fields, S.options))
              .filter(S.typology != "Driver")
              .filter(S.discipline == discipline)
              .filter(S.approval_status == "approved")
              .order_by(S.id)
              .all())
    needs_estimate = [s.id for s in sheets
                      if not any(read_lumens(_texts(s.fields, s.options, "lumen_output", "lumen_package")))
                      and (s.options or {}).get("wattage")]
    params = dict(db.query(S.id, S.params).filter(S.id.in_(needs_estimate)).all()) if needs_estimate else {}
    return {"products": [product_entry(s, params.get(s.id)) for s in sheets],
            "typologies": typology_choices(sheets)}


# --- choosing a product's own option for a requirement --------------------------------------

def _gap(want, interval):
    """How far an offered interval is from the wanted one, relative to the want. 0 = inside."""
    lo, hi = want
    olo, ohi = interval
    if ohi < lo:
        return (lo - ohi) / lo if lo else float("inf")
    if olo > hi:
        return (olo - hi) / hi if hi else float("inf")
    return 0.0


def _fit(want, interval):
    """How well an offered interval serves the wanted one - lower is better, compared in order:
    distance from it; then how much of a wanted RANGE it covers, so "2700-6500K" asked for gets
    the tunable "TW (2700K-6500K)" and not a fixed 2700K that merely sits inside the range; then
    narrowness, so a single 3000K asked for gets the fixed 3000K rather than a tunable option
    that also reaches it. It decides the option ordered, and on NPD products the price with it."""
    lo, hi = want
    olo, ohi = interval
    covered = 1.0 if hi == lo else max(0.0, min(hi, ohi) - max(lo, olo)) / (hi - lo)
    return (_gap(want, interval), -covered, ohi - olo)


def _option_texts(fields, options, field):
    """The product's own orderable values for a field, as the Configurator offers them."""
    listed = [str(o) for o in (options or {}).get(field) or [] if o]
    if listed:
        return listed
    value = (fields or {}).get(field)
    return [str(value)] if value else []


def choose_options(sheet, wants: dict) -> dict:
    """For a product the person picked, the product's OWN option string nearest each requirement
    value - "Narrow Flood 24°", not a tidied "24°" - since that exact text is what the
    Configurator stores, what the description prints, and what Exenia's product code is composed
    from. A field is left out when the product states nothing for it or the requirement asked
    nothing of it; nothing is invented to fill it.

    `wants` holds the requirement as the page parsed it: {"wattage": {"lo", "hi", "per_metre"},
    "cct"/"beam_angle"/"lumen_output": {"lo", "hi"}, "cri": n, "ugr": n, "ip_rating": "65",
    "control": "DALI"}."""
    fields, options = sheet.fields or {}, sheet.options or {}
    chosen = {}
    interval_readers = {
        "wattage": lambda o, pm: read_wattage((o,))[1 if pm else 0],
        "lumen_output": lambda o, pm: read_lumens((o,))[1 if pm else 0],
        "cct": lambda o, pm: read_cct((o,)),
        "beam_angle": lambda o, pm: read_beam((o,)),
    }
    for field, reader in interval_readers.items():
        want = wants.get(field)
        if not isinstance(want, dict) or want.get("lo") is None:
            continue
        # An open-ended limit ("at least 1000 lm") has no top: the nearest to its floor is chosen.
        top = want.get("hi")
        target = (float(want["lo"]), float(want["lo"] if top is None else top))
        best = None
        # The field's whole text last, for when no split-out option carries the value: a strip's
        # "12 W/M" can be split into options "12 W" and "M", and only the whole says per metre.
        # Options come first and win a tie, so a single orderable value is preferred whenever
        # one fits.
        texts = _option_texts(fields, options, field)
        whole = (fields or {}).get(field)
        if whole and str(whole) not in texts:
            # Its pieces first ("9.6W/M, 12W/M" -> "9.6W/M", "12W/M"), split only where a list
            # is separated - never on the "/" inside "W/M" - then the whole as the last resort.
            pieces = [x for x in re.split(r"\s*[,;]\s*|\s+/\s+", str(whole)) if x.strip()]
            texts = texts + [x for x in pieces if x not in texts] + [str(whole)]
        for opt in texts:
            intervals = reader(opt, bool(want.get("per_metre")))
            if intervals:
                rank = min(_fit(target, iv) for iv in intervals)
                if best is None or rank < best[0]:
                    best = (rank, opt)
        if best:
            chosen[field] = best[1]

    def pick_number(field, reader, want, better):
        """The option nearest `want` on the right side of it, else the nearest at all."""
        scored = []
        for opt in _option_texts(fields, options, field):
            for v in reader((opt,)):
                scored.append((v, opt))
        if not scored:
            return
        good = [x for x in scored if better(x[0], want)]
        pool = good or scored
        chosen[field] = min(pool, key=lambda x: abs(x[0] - want))[1]

    if wants.get("cri") is not None:
        pick_number("cri", read_cri, float(wants["cri"]), lambda v, w: v >= w)
    if wants.get("ugr") is not None:
        pick_number("ugr", read_ugr, float(wants["ugr"]), lambda v, w: v <= w)

    ip = str(wants.get("ip_rating") or "")
    if len(ip) == 2 and ip.isdigit():
        meeting = []
        for opt in _option_texts(fields, options, "ip_rating"):
            for rating in read_ip((opt,)):
                if rating[0] >= ip[0] and rating[1] >= ip[1]:
                    meeting.append((rating, opt))
        if meeting:
            chosen["ip_rating"] = min(meeting)[1]

    control = wants.get("control")
    if control:
        for opt in _option_texts(fields, options, "control"):
            if control in read_control((opt,)):
                chosen["control"] = opt
                break
    return chosen


def catalogue_typologies(db, discipline: str) -> list:
    """typology_choices() for the same products build_index() offers, reading only the one
    column it needs - for the template's dropdown and an import's typology check."""
    from types import SimpleNamespace
    from app import models
    S = models.Specsheet
    rows = (db.query(S.typology).filter(S.typology != "Driver").filter(S.discipline == discipline)
            .filter(S.approval_status == "approved").all())
    return typology_choices(SimpleNamespace(typology=t) for (t,) in rows)


def typology_choices(sheets) -> list:
    """One entry per typology as the matcher sees it, named the way most of its products are.

    The library holds "Downlights" (301) and "Downlight" (114) as separate names for one thing,
    and the matcher already treats them as one (app/typology_match.py) - offering both in a
    dropdown would invite a person to think they differ."""
    names = {}
    for s in sheets:
        key = typology_key(s.typology or "")
        if key:
            names.setdefault(key, {}).setdefault(s.typology, 0)
            names[key][s.typology] += 1
    out = []
    for key, by_name in names.items():
        name = max(by_name.items(), key=lambda kv: (kv[1], kv[0]))[0]
        out.append({"key": key, "name": name, "count": sum(by_name.values())})
    return sorted(out, key=lambda t: t["name"].lower())
