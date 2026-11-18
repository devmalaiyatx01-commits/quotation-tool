"""Given an architect's free-text fixture-requirement paragraph (the kind of prose
spec block seen in a real Studio proposal BOQ - "Power consumption: 2W; ... LED
colour: 3000K; Optics: 17 degrees; ... Ingress protection: IP54; ... Control
system:DALI; ... Dimensions: 36mm Dia. X 20mm H ..."), pull out the same canonical
fields `app/pdf_parser.py` already uses (see its CANONICAL_FIELDS), then score every
specsheet in the library against those fields to suggest the closest match.

Deliberately NOT fuzzy/ML matching - see the dedup-fingerprint precedent in
app/main.py (_dedup_fingerprints): a fuzzy threshold silently corrupted real data
the one time this project tried that shortcut. Every point awarded here traces back
to one named field comparison, shown in the returned breakdown, so a wrong
suggestion is easy to see through rather than a black box to trust or distrust.

A field is only ever scored when BOTH the requirement text and the candidate
specsheet actually have a value for it - a field neither/either side is missing is
excluded from the score entirely (never guessed, never penalized as a mismatch),
matching this project's existing typology/environment classifier philosophy.
"""
import re

# --- free-text extraction ---------------------------------------------------

_WATTAGE_LABEL_RE = re.compile(r"power\s+consumption\s*:\s*([\d.]+)\s*W", re.IGNORECASE)
_WATTAGE_FALLBACK_RE = re.compile(r"([\d.]+)\s*W\b", re.IGNORECASE)
_CCT_RE = re.compile(r"(\d{4})\s*K\b", re.IGNORECASE)
_BEAM_LABEL_RE = re.compile(r"Optics\s*:\s*(\d+)\s*(?:°|degrees?)", re.IGNORECASE)
# "degree(s)" or "deg", or a ° that is not followed by C - a bare ° also shows up in temperatures
# ("Ta 25°C"), which isn't a beam angle and would otherwise be the first ° in the paragraph.
# "deg" and "24°" added 2026-09-17: client schedules write "24 Deg." and "10°", not "24 degrees".
_BEAM_FALLBACK_RE = re.compile(r"(\d+)\s*(?:degrees?\b|deg\b|°(?!\s*[CF]\b))", re.IGNORECASE)
# "IP-20" and "IP: 65" as well as "IP20" - our own quotation descriptions write all three.
_IP_RE = re.compile(r"\bIP\s?[-:]?\s?(\d{2})\b", re.IGNORECASE)
# "CRI > 80", "CRI>90" and "Ra90" as well as "CRI: 90".
_CRI_RE = re.compile(r"(?:CRI|\bRa)(?:\s+Colour\s+Rendering\s+Index)?\s*[:>=≥]*\s*(\d{2,3})", re.IGNORECASE)
_UGR_RE = re.compile(r"UGR\s*[<>=]*\s*(\d+)", re.IGNORECASE)
_WARRANTY_RE = re.compile(r"warranty\s*:\s*(\d+)\s*years?", re.IGNORECASE)
_CONTROL_LABEL_RE = re.compile(r"control\s+system\s*:\s*([A-Za-z0-9/\-\s]+?)\s*[;.\n]", re.IGNORECASE)
# stop at the first comma OR semicolon, not just semicolon - some paragraphs (e.g. LED strip
# specs) use commas as the primary field separator with only occasional semicolons, and
# stopping at semicolon only let a value run on and swallow several unrelated fields after it
_FINISH_RE = re.compile(r"Finishe?s?\s*:\s*([^,;]+)", re.IGNORECASE)
_MATERIAL_RE = re.compile(r"Body\s+material\s*:\s*([^,;]+)", re.IGNORECASE)
_MOUNTING_RE = re.compile(r"Mounting\s*:\s*([^,;]+)", re.IGNORECASE)
_CUTOUT_RE = re.compile(r"Cutout\s+dimensions?\s*:?\s*([^,;]+)", re.IGNORECASE)
_DIMENSIONS_RE = re.compile(r"(?<!Cutout )Dimensions?\s*:\s*([^,;]+)", re.IGNORECASE)
_ACCESSORIES_RE = re.compile(r"Required\s+Accesor?ies\s*:\s*([^.]+)\.", re.IGNORECASE)
# "Delivered lumen output" preferred over "Source lumen output" when a paragraph states
# both (real requirement text draws this exact distinction - delivered is what actually
# reaches the space, source is before fixture/optic losses) - \s+ between "lumen" and
# "output" (not a literal space) because the real sample PDF wraps the label mid-phrase
# across a line break ("Delivered lumen \noutput: 165 lm") after fitz extraction
_LUMEN_DELIVERED_RE = re.compile(r"Delivered\s+lumen\s+output\s*:?\s*([\d.]+)\s*lm", re.IGNORECASE)
_LUMEN_SOURCE_RE = re.compile(r"Source\s+lumen\s+output\s*:?\s*([\d.]+)\s*lm", re.IGNORECASE)

# checked in order; first hit wins - matches the "Control system:" label text seen
# in real Studio requirement docs (DALI / 0-10V / Phase Cut / On-Off / Zigbee / Casambi / DMX)
_CONTROL_KEYWORDS = [
    ("DALI", "DALI"),
    ("PHASE CUT", "Phase Cut"),
    ("0/1-10V", "0-10V"), ("0-10V", "0-10V"), ("1-10V", "0-10V"),
    ("ZIGBEE", "Zigbee"),
    ("CASAMBI", "Casambi"),
    ("DMX", "DMX"),
    ("ON/OFF", "On/Off"), ("ON / OFF", "On/Off"), ("NON DIMMING", "Non Dimming"), ("NON-DIMMABLE", "Non Dimming"),
]


def _first_match(pattern, text):
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def _extract_control(text):
    label = _first_match(_CONTROL_LABEL_RE, text)
    haystack = (label or text).upper()
    for keyword, canonical in _CONTROL_KEYWORDS:
        if keyword in haystack:
            return canonical
    return None


def extract_requirement_fields(text: str) -> dict:
    """Pulls canonical fields (same names as pdf_parser.CANONICAL_FIELDS' values) out
    of one fixture's free-text requirement paragraph. Only returns keys it actually
    found a value for - absent fields are left out, never guessed."""
    text = text or ""
    fields = {}

    wattage = _first_match(_WATTAGE_LABEL_RE, text) or _first_match(_WATTAGE_FALLBACK_RE, text)
    if wattage:
        fields["wattage"] = f"{wattage}W"

    cct = _first_match(_CCT_RE, text)
    if cct:
        fields["cct"] = f"{cct}K"

    beam = _first_match(_BEAM_LABEL_RE, text) or _first_match(_BEAM_FALLBACK_RE, text)
    if beam:
        fields["beam_angle"] = beam

    ip = _first_match(_IP_RE, text)
    if ip:
        fields["ip_rating"] = ip

    cri = _first_match(_CRI_RE, text)
    if cri:
        fields["cri"] = cri

    ugr = _first_match(_UGR_RE, text)
    if ugr:
        fields["ugr"] = ugr

    warranty = _first_match(_WARRANTY_RE, text)
    if warranty:
        fields["warranty"] = f"{warranty} years"

    control = _extract_control(text)
    if control:
        fields["control"] = control

    finish = _first_match(_FINISH_RE, text)
    if finish:
        fields["finish"] = finish

    material = _first_match(_MATERIAL_RE, text)
    if material:
        fields["material"] = material

    mounting = _first_match(_MOUNTING_RE, text)
    if mounting:
        fields["mounting"] = mounting

    cutout = _first_match(_CUTOUT_RE, text)
    if cutout:
        fields["cutout_dimensions"] = cutout

    dimensions = _first_match(_DIMENSIONS_RE, text)
    if dimensions:
        fields["luminaire_length"] = dimensions

    accessories = _first_match(_ACCESSORIES_RE, text)
    if accessories:
        fields["accessories"] = accessories

    lumen = _first_match(_LUMEN_DELIVERED_RE, text) or _first_match(_LUMEN_SOURCE_RE, text)
    if lumen:
        fields["lumen_output"] = f"{lumen}lm"

    return fields


_SUPPLY_OF_RE = re.compile(r"\bSupply of\b")


def split_requirement_blocks(text: str) -> list[str]:
    """Splits a pasted multi-fixture document into per-fixture blocks - blank lines
    are the primary contract (still correct for hand-pasted paragraphs that don't
    follow any particular convention), but real architect requirement exports (this
    document family, confirmed against a real sample) have no blank-line separation
    at all even after clean extraction, with every fixture paragraph instead
    starting with the literal words "Supply of" - falls back to splitting on that
    boundary specifically when blank-line splitting clearly didn't work (only one
    block came out, and it contains more than one "Supply of" itself)."""
    text = text or ""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]

    supply_of_count = len(_SUPPLY_OF_RE.findall(text))
    # blank-line splitting only actually separated fixtures if it produced at least
    # as many blocks as there are "Supply of" occurrences - fewer means at least one
    # block still has more than one fixture crammed into it (e.g. blank lines only
    # existed *between pages*, not between fixtures, on the real sample document)
    if supply_of_count > 1 and len(blocks) < supply_of_count:
        # lookahead split - "Supply of" starts the next block rather than being
        # consumed - and drop whatever precedes the first real occurrence (a table
        # header/legend with no fixture content of its own, e.g. this document's
        # own "Ref. Model No. Description* Control ..." column-header row)
        pieces = re.split(r"(?=\bSupply of\b)", text)
        blocks = [b.strip() for b in pieces if b.strip() and _SUPPLY_OF_RE.search(b)]

    return blocks


def block_needs_manual_split(block: str) -> bool:
    """Format-independent sanity check, not another guessed splitting convention -
    split_requirement_blocks only knows two real conventions (blank lines, "Supply
    of"), so a document that uses neither (a genuinely different export format) ends
    up as one oversized block with no warning that anything went wrong. Rather than
    inventing a third guessed boundary pattern with no real sample to verify it
    against (this project's standing rule against unverified fuzzy shortcuts - see
    this module's own top-of-file docstring), this instead detects the *symptom* any
    convention-mismatch produces regardless of format: a single block genuinely
    describing one fixture only ever states "Supply of" or its own labeled "Power
    consumption: Xw" once - two or more occurrences of either inside one block is
    strong, format-agnostic evidence multiple fixtures got merged, whatever the
    document's own convention actually is.

    Deliberately checks _WATTAGE_LABEL_RE only, NOT _WATTAGE_FALLBACK_RE (the bare
    "Xw" pattern) - verified against the real 52-fixture sample PDF: every genuine
    single-fixture block has exactly one _WATTAGE_LABEL_RE match but commonly 5-7
    _WATTAGE_FALLBACK_RE matches, because unrelated reference-table cells (driver
    wattage, other rows' bleed-in text - the same PDF-table-extraction artifact
    documented on classify_typology's call site in app/main.py) also happen to
    contain bare "Nw" text with no label attached. Using the fallback pattern here
    flagged all 52/52 real blocks as suspect - useless noise, not a real signal.

    Surfaced to the user as a warning to manually add a blank line and re-run, rather
    than silently scoring a merged block as if it were one product."""
    return len(_SUPPLY_OF_RE.findall(block)) > 1 or len(_WATTAGE_LABEL_RE.findall(block)) > 1


# --- scoring -----------------------------------------------------------------

# higher weight = more of a hard constraint (protection/control rating a fixture
# either does or doesn't meet) vs. a softer preference (warranty length)
FIELD_WEIGHTS = {
    "ip_rating": 25,
    "control": 25,
    "beam_angle": 20,
    "wattage": 15,
    "lumen_output": 15,
    "cct": 15,
    "cri": 15,
    "ugr": 10,
    "finish": 5,
    "warranty": 5,
}

def _weights_for_order(field_order):
    """User-ranked priority (Match tab review step) replaces the fixed
    FIELD_WEIGHTS table with weights derived purely from rank position - first
    choice is worth the most, weight steps down by 5 per rank, floored at 5 so
    even a low-ranked field still contributes something rather than being
    silently zeroed out. Only fields the user actually ranked get a weight;
    anything extracted but left out of field_order is excluded from scoring
    entirely, same treatment score_fields already gives an unweighted field."""
    return {field: max(5, 30 - 5 * i) for i, field in enumerate(field_order)}


_NUMERIC_RE = re.compile(r"[\d.]+")


def _numeric(value):
    if value is None:
        return None
    m = _NUMERIC_RE.search(str(value))
    return float(m.group(0)) if m else None


def _score_field(field, required, candidate):
    """Returns a 0..1 fraction of the field's weight earned, or None if the two
    values aren't comparable (candidate has no usable value for this field)."""
    if field == "ip_rating":
        req_n, cand_n = _numeric(required), _numeric(candidate)
        if req_n is None or cand_n is None:
            return None
        if cand_n == req_n:
            return 1.0
        return 0.7 if cand_n > req_n else 0.0  # over-spec still usable, under-spec isn't

    if field == "control":
        if not candidate:
            return None
        return 1.0 if str(required).upper() in str(candidate).upper() else 0.0

    if field == "beam_angle":
        req_n, cand_n = _numeric(required), _numeric(candidate)
        if req_n is None or cand_n is None:
            return None
        diff = abs(req_n - cand_n)
        return max(0.0, 1.0 - diff / 30.0)

    if field == "wattage":
        req_n, cand_n = _numeric(required), _numeric(candidate)
        if req_n is None or cand_n is None or req_n == 0:
            return None
        diff_ratio = abs(req_n - cand_n) / req_n
        return max(0.0, 1.0 - diff_ratio / 0.5)

    if field == "lumen_output":
        req_n, cand_n = _numeric(required), _numeric(candidate)
        if req_n is None or cand_n is None or req_n == 0:
            return None
        diff_ratio = abs(req_n - cand_n) / req_n
        return max(0.0, 1.0 - diff_ratio / 0.5)

    if field == "cct":
        req_n, cand_n = _numeric(required), _numeric(candidate)
        if req_n is None or cand_n is None:
            return None
        diff = abs(req_n - cand_n)
        if diff == 0:
            return 1.0
        return 0.5 if diff <= 500 else 0.0

    if field == "cri":
        req_n, cand_n = _numeric(required), _numeric(candidate)
        if req_n is None or cand_n is None or req_n == 0:
            return None
        if cand_n >= req_n:
            return 1.0
        return max(0.0, cand_n / req_n)

    if field == "ugr":
        req_n, cand_n = _numeric(required), _numeric(candidate)
        if req_n is None or cand_n is None:
            return None
        if cand_n <= req_n:
            return 1.0
        return max(0.0, 1.0 - (cand_n - req_n) / req_n)

    if field == "warranty":
        req_n, cand_n = _numeric(required), _numeric(candidate)
        if req_n is None or cand_n is None or req_n == 0:
            return None
        if cand_n >= req_n:
            return 1.0
        return max(0.0, cand_n / req_n)

    if field == "finish":
        if not candidate:
            return None
        req_s, cand_s = str(required).strip().lower(), str(candidate).strip().lower()
        if req_s == cand_s:
            return 1.0
        return 0.5 if req_s in cand_s or cand_s in req_s else 0.0

    return None


def _best_fraction(field, required, candidate_fields, candidate_options):
    """A specsheet's `fields[field]` is often a single representative value even
    when the real product offers several (e.g. cct: "2700K / 3000K / 3500K /
    4000K / 2700K-6000K" - `_numeric()` would only ever see the first, 2700,
    wrongly scoring a product that genuinely supports the required 3000K as if
    it didn't). `options[field]` holds the real split-out list for exactly the
    fields where that matters (see pdf_parser.VARIANT_FIELDS /
    import_duragreen_downlights.py) - scored against every option, best one
    wins, since the product can genuinely be ordered/configured with whichever
    option actually matches. Falls back to the single `fields[field]` value for
    everything else (ip_rating, cri, ugr, warranty - specsheets in this library
    never list more than one real value for those).

    Returns (fraction, matched_value) - matched_value is whichever specific
    option actually produced that fraction, NOT just the first-listed one, so a
    caller displaying "this product: X" never shows an X that doesn't itself
    justify a reported match (found 2026-08-19: a breakdown line was reporting
    "required 24W, this product 15W, match: true" - true because 24W WAS one of
    the product's real options, just not the one being displayed)."""
    options = (candidate_options or {}).get(field)
    if options:
        best, best_value = None, None
        for opt in options:
            fraction = _score_field(field, required, opt)
            if fraction is not None and (best is None or fraction > best):
                best, best_value = fraction, opt
        return best, best_value
    candidate = (candidate_fields or {}).get(field)
    return _score_field(field, required, candidate), candidate


def score_fields(requirement_fields: dict, candidate_fields: dict, candidate_options: dict = None, field_order: list = None) -> dict:
    """Pure-dict scorer (no DB dependency, easy to unit test) - compares an
    extracted requirement to one candidate's `fields` (+ optional `options`)
    dicts. Returns {"score": 0-100, "breakdown": [{"field","weight",
    "requirement","candidate","match","points"}, ...]}.

    `field_order` (optional) is a user-chosen per-fixture priority list (Match
    tab review step) - when given, weights come from rank position instead of
    the fixed FIELD_WEIGHTS table (see _weights_for_order), so "this product
    cares about lumen_output before wattage" is expressible per fixture rather
    than being one global setting."""
    weights = _weights_for_order(field_order) if field_order else FIELD_WEIGHTS
    breakdown = []
    earned = 0.0
    applicable = 0.0
    for field, required in requirement_fields.items():
        weight = weights.get(field)
        if weight is None:
            continue  # extracted but not one of the fields we score on (e.g. accessories, mounting)
        fraction, candidate = _best_fraction(field, required, candidate_fields, candidate_options)
        if fraction is None:
            breakdown.append({"field": field, "weight": weight, "requirement": required, "candidate": candidate, "match": None, "points": 0})
            continue
        points = fraction * weight
        earned += points
        applicable += weight
        breakdown.append({"field": field, "weight": weight, "requirement": required, "candidate": candidate, "match": fraction >= 0.999, "points": round(points, 1)})

    score = round(100 * earned / applicable) if applicable > 0 else 0
    return {"score": score, "breakdown": breakdown}


def _lumen_output_options(specsheet):
    """Resolves the real lumen-output value(s) this specsheet can offer, so
    "lumen_output" can be scored the same way any other multi-option field is
    (see _best_fraction). Two real sources, in preference order:

    1. `fields.lumen_package` - InHouse specsheets publish this directly, one
       real value per wattage option (e.g. "1950 lm/ 2880lm"), same "/"-split
       convention every other options field in this library already uses.
    2. `params["... SPEC"]["Typical Source Efficacy (lm/W)"]` (or wherever an
       "efficacy" key lives in a scraped params table) x each real wattage
       option - DuraGreen never publishes a raw lumen figure, but does publish
       efficacy (checked: absent from `fields` on 300 sampled rows, present as
       a real published number in `params`), so this is a legitimate computed
       estimate rather than a guess. Tagged "(estimated)" so a breakdown never
       presents a computed number as if the manufacturer stated it directly.

    Returns a list of value strings (never both sources mixed), or None if
    neither is available - excluded from scoring entirely, same as any other
    field a candidate has no value for."""
    fields = specsheet.fields or {}
    lumen_package = fields.get("lumen_package")
    if lumen_package:
        values = [v.strip() for v in str(lumen_package).split("/") if v.strip()]
        if values:
            return values

    params = specsheet.params or {}
    efficacy = None
    for section in params.values():
        if not isinstance(section, dict):
            continue
        for key, val in section.items():
            if "efficacy" in key.lower():
                efficacy = _numeric(val)
                break
        if efficacy is not None:
            break

    wattages = (specsheet.options or {}).get("wattage")
    if efficacy and wattages:
        computed = []
        for w in wattages:
            w_n = _numeric(w)
            if w_n is not None:
                computed.append(f"{round(efficacy * w_n)}lm (estimated)")
        if computed:
            return computed

    return None


def score_specsheet(requirement_fields: dict, specsheet, field_order: list = None) -> dict:
    """Thin wrapper around score_fields() for a real Specsheet ORM row - attaches
    identity so API responses don't need a second lookup."""
    candidate_options = dict(specsheet.options or {})
    lumen_options = _lumen_output_options(specsheet)
    if lumen_options:
        candidate_options["lumen_output"] = lumen_options
    result = score_fields(requirement_fields, specsheet.fields or {}, candidate_options, field_order)
    result["specsheet_id"] = specsheet.id
    result["product_name"] = specsheet.product_name
    result["series"] = specsheet.series
    result["family"] = specsheet.family
    result["variant"] = specsheet.variant
    result["make"] = specsheet.make
    result["image_path"] = specsheet.image_path
    return result


def rank_specsheets(requirement_fields: dict, specsheets, limit: int = 5, field_order: list = None,
                    typology: str = "") -> list[dict]:
    """Scores every candidate and returns the top `limit`, highest score first,
    ties broken by product_name for a stable order.

    `typology`, when given, is a HARD filter rather than another weighted field. Every other
    field is a matter of degree - a 7W requirement against an 8W product is a near miss worth
    showing. Typology is not: a wall lamp offered for a downlight requirement is not a close
    match, it is the wrong product, and no amount of agreement on wattage and beam angle makes
    it right. It is compared through app/typology_match.py rather than as a string, because
    this library holds both "Downlight" and "Downlights" as separate typologies and matching
    literally would discard the larger group.

    If the filter would leave nothing at all, the unfiltered ranking is returned instead. An
    empty result teaches the user nothing; a ranked list they can see is the wrong typology
    tells them the requirement's own typology was misread.
    """
    from app.typology_match import same_typology
    candidates = list(specsheets)
    if typology:
        kept = [s for s in candidates if same_typology(typology, getattr(s, "typology", ""))]
        if kept:
            candidates = kept
    scored = [score_specsheet(requirement_fields, s, field_order) for s in candidates]
    scored.sort(key=lambda r: (-r["score"], r["product_name"] or ""))
    return scored[:limit]
