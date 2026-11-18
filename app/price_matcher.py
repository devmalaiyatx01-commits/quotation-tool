"""Suggests which specsheet an unmapped PriceEntry most likely belongs to, by reading
real price-sheet and specsheet fields with RANGE/SET semantics instead of string equality.

Why this exists (2026-08-20): price sheets and specsheets are both highly unstructured -
the same fact gets written "1-3W" in one and "1W / 3W" in another, "2700K, 3000K, 4000K,
6000K" in one and "2700K/3000K/ 4000K" in another. An exact-string comparison (what the
Configurator's own priceEntryMatches does, correctly, for an ALREADY-mapped entry whose
config was set by a human) is useless for discovery: it reported zero matches across the
entire Spotlight family, and worse, it made "this specsheet lists fewer CCT options than
the price row allows" look like a disqualifying mismatch when it's actually a full match -
the price row's list is the set of values ALLOWED at that price, so a product offering a
subset of them is covered, not excluded.

The separator characters carry the meaning:
  "-"  between numbers  -> an inclusive RANGE   ("1-3W" = anything 1W..3W)
  "/" or ","            -> a SET of alternatives ("2700K/3000K/4000K")
  "*" or "x" between numbers -> one asymmetric optic token ("13*55" is a single beam
                               spec, NOT the numbers 13 and 55)

This module only ever SUGGESTS, with a score and a human-readable reason per field. It
never writes specsheet_id or a price - a human still confirms every mapping through the
List Pricing tab, and the Locked/Keep Open status records how confident that human was.
That's the same "never auto-match money" rule the EMBER/NYX backfills established; this
just replaces hand-reading hundreds of rows with a ranked shortlist to confirm.
"""
import re

# One asymmetric-optic token ("13*55", "10x60", "15 °+25°") is a single beam
# specification, not two independent numbers - splitting it would invent beam angles the
# product doesn't actually offer.
_ASYM_RE = re.compile(r"\d\s*[*x×+]\s*\d")
_RANGE_RE = re.compile(r"\d\s*-\s*\d")
_NUM_RE = re.compile(r"\d+\.?\d*")


def parse_spec_values(text):
    """Parses one messy spec cell into (discrete_values, ranges).

    >>> parse_spec_values("1-3W")                      # a range
    (set(), [(1.0, 3.0)])
    >>> parse_spec_values("2700K, 3000K, 4000K")       # a set
    ({2700.0, 3000.0, 4000.0}, [])
    >>> parse_spec_values("10°/15°/13*55°")            # set + one asymmetric token
    ({10.0, 15.0, '13*55'}, [])
    """
    if not text:
        return set(), []
    text = str(text).replace("\n", " ").strip()
    values, ranges = set(), []
    for part in re.split(r"[/,]| or ", text):
        part = part.strip()
        if not part:
            continue
        if _ASYM_RE.search(part):
            values.add(re.sub(r"[\s°]+", "", part.lower()))
            continue
        nums = _NUM_RE.findall(part)
        if _RANGE_RE.search(part) and len(nums) >= 2:
            ranges.append((float(nums[0]), float(nums[1])))
        else:
            values.update(float(n) for n in nums)
    return values, ranges


def covers(price_text, sheet_text):
    """Does the PRICE ROW's allowed envelope cover everything the SPECSHEET product
    offers? Direction matters and is the whole point: a price row listing four CCTs
    covers a product offering three of them (that product can be ordered at this price);
    a product offering a beam angle the price row never lists is NOT covered (that
    configuration isn't priced by this row).

    Returns (covered: bool, detail: str) - detail names exactly which values fell
    outside, so a suggestion is always explainable rather than a bare score."""
    p_vals, p_ranges = parse_spec_values(price_text)
    s_vals, s_ranges = parse_spec_values(sheet_text)
    if not s_vals and not s_ranges:
        return True, "specsheet states nothing here"
    if not p_vals and not p_ranges:
        return True, "price row states nothing here"

    def in_price(x):
        if x in p_vals:
            return True
        if isinstance(x, float):
            return any(lo <= x <= hi for lo, hi in p_ranges)
        return False

    # A specsheet range is covered when both endpoints are (its interior follows).
    endpoints = set(s_vals)
    for lo, hi in s_ranges:
        endpoints.update([lo, hi])
    missing = {x for x in endpoints if not in_price(x)}
    if missing:
        return False, f"not priced by this row: {', '.join(str(m) for m in sorted(missing, key=str))}"
    return True, "fully covered"


def _same_notation(a, b):
    """Exact same raw notation, whitespace/case aside - a strong identity signal beyond
    mere numeric compatibility ("1-3W" written identically in both documents is more
    telling than "1W / 3W" merely falling inside 1..3)."""
    norm = lambda s: re.sub(r"\s+", "", str(s or "")).upper()
    return bool(norm(a)) and norm(a) == norm(b)


# Weights reflect how strongly each field identifies a specific product. Wattage is the
# primary key on every real price sheet inspected (it's what the price actually varies
# by); IP separates product generations; CCT/beam are corroborating, and are the fields
# most often stale or abbreviated between a price sheet and a datasheet.
FIELD_WEIGHTS = {"wattage": 40, "ip_rating": 25, "cct": 20, "beam_angle": 15}


def score_candidate(price_config, sheet_fields):
    """Scores one (price row -> specsheet) pairing. Returns
    {"score": 0-100, "breakdown": [{field, covered, detail, exact_notation}], "blocked": bool}.

    `blocked` means wattage isn't covered at all - a hard disqualifier rather than a low
    score, since a price for a 15W fixture simply is not the price of a 3W one however
    well everything else lines up."""
    breakdown, earned, possible = [], 0.0, 0.0
    for field, weight in FIELD_WEIGHTS.items():
        p_text, s_text = price_config.get(field), sheet_fields.get(field)
        # Both sides' raw text always travels with the result, including for a field only
        # one document states - a mapping is a judgement a human has to be able to audit,
        # and "the price sheet says nothing about IP here" is itself a thing they need to
        # see, not something to silently omit from the comparison.
        if not p_text or not s_text:
            breakdown.append({"field": field, "covered": None, "weight": weight,
                              "price_value": p_text or "", "sheet_value": s_text or "",
                              "exact_notation": False,
                              "detail": "only the price sheet states this - nothing to compare against"
                              if p_text else "only the datasheet states this - the price row does not constrain it"})
            continue
        ok, detail = covers(p_text, s_text)
        exact = _same_notation(p_text, s_text)
        possible += weight
        if ok:
            # exact notation earns full weight; merely-compatible earns most of it
            earned += weight if exact else weight * 0.85
        breakdown.append({"field": field, "covered": ok, "detail": detail,
                          "exact_notation": exact, "weight": weight,
                          "price_value": p_text, "sheet_value": s_text})
    blocked = any(b["field"] == "wattage" and b["covered"] is False for b in breakdown)
    score = round(100 * earned / possible) if possible else 0
    return {"score": score, "breakdown": breakdown, "blocked": blocked}


def suggest_specsheets(price_entry, specsheets, limit=5):
    """Ranks candidate specsheets for one unmapped PriceEntry. Only same-typology
    candidates should be passed in by the caller (the standing rule from the Match tool:
    typology is never crossed). Blocked candidates are dropped entirely, not ranked low."""
    # source_specs, NOT config - config is the minimal exact-match key the Configurator
    # uses at add-time, deliberately stripped of the multi-value cells (see
    # PriceEntry.source_specs in app/models.py); those stripped cells are precisely what
    # discovery needs to read here.
    price_specs = price_entry.source_specs or price_entry.config or {}
    results = []
    for s in specsheets:
        fields = s.fields or {}
        r = score_candidate(price_specs, fields)
        if r["blocked"] or r["score"] == 0:
            continue
        # Numbers alone cannot tell products apart - a 24W power supply, a 24W panel and a
        # 24W downlight all agree on wattage and IP. The description is the only field
        # carrying product identity, so it gates the suggestion rather than nudging it:
        # a component row is never offered a luminaire, and a row sharing no distinctive
        # wording with the datasheet is dropped even on a perfect numeric score.
        frac, shared = description_agreement(price_entry.source_label, s)
        if describes_component(price_entry.source_label) and not describes_component(
                f"{s.category} {s.product_name}"):
            continue
        if not shared:
            continue
        r["breakdown"].append({
            "field": "description", "covered": len(shared) >= 2, "exact_notation": False, "weight": 0,
            "price_value": re.sub(r"\s+", " ", str(price_entry.source_label or "")).strip(),
            "sheet_value": " - ".join(filter(None, [s.category, s.product_name])),
            "detail": f"shared wording: {', '.join(shared)}" if shared else "no shared wording",
        })
        results.append({
            "description_match": round(frac, 2), "shared_terms": shared,
            "specsheet_id": s.id, "product_name": s.product_name, "series": s.series,
            "family": s.family, "variant": s.variant, "typology": s.typology,
            "score": r["score"], "breakdown": r["breakdown"],
        })
    results.sort(key=lambda r: (-r["score"], r["product_name"] or ""))
    return results[:limit]


# Words that describe the KIND of thing rather than which thing it is - a price sheet
# writes "Round Hole Accessory" where the datasheet writes "ROUND HOLE APARTURE" (sic),
# and neither extra word distinguishes it from any other accessory in the list.
_ACCESSORY_NOISE = ("ACCESSORY", "ACCESSORIES", "APARTURE", "APERTURE", "LOUVER", "LOUVRE")


def _accessory_key(name: str) -> str:
    """Normalizes an accessory name for comparison: uppercase, noise words dropped, all
    non-alphanumerics removed - so "Round Hole Accessory", "ROUND HOLE APARTURE" and
    "round-hole" all reduce to "ROUNDHOLE", and "snoot"/"SNOOT" collapse together."""
    text = re.sub(r"[^A-Za-z0-9 ]+", " ", str(name or "")).upper()
    words = [w for w in text.split() if w and w not in _ACCESSORY_NOISE]
    return "".join(words)


def match_accessory_name(price_name: str, accessory_types: list) -> str | None:
    """Maps an accessory as a PRICE SHEET names it onto the exact string the SPECSHEET's
    own accessories field uses - the two documents rarely agree ("Spike" vs "GROUNDSPIKE",
    "Base" vs "SURFACE MOUNTBASE", "Round Hole Accessory" vs "ROUND HOLE APARTURE").

    This matters because an accessory price is matched by exact string equality against
    the option the picker actually offers (resolveAccessoryPrice in static/app.js), so an
    unnormalized name means the price silently never applies - the same failure mode
    already hit twice on the main product config.

    Returns the specsheet's own spelling, or None when the answer isn't unambiguous.
    Ambiguity is always resolved by returning None (leave it for a human) rather than
    picking a plausible-looking candidate - a wrong accessory price is a real quoting
    error, and this whole subsystem's rule is that nothing about money is ever guessed."""
    target = _accessory_key(price_name)
    if not target:
        return None
    keyed = [(t, _accessory_key(t)) for t in (accessory_types or [])]

    exact = [t for t, k in keyed if k == target]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    # "SPIKE" inside "GROUNDSPIKE", "BASE" inside "SURFACEMOUNTBASE" - a real naming
    # difference between the two documents, not a coincidence, but only trusted when
    # exactly one candidate contains it (or is contained by it).
    contained = [t for t, k in keyed if k and (target in k or k in target)]
    return contained[0] if len(contained) == 1 else None


# Words too generic to identify a product - shared by nearly every lighting description,
# so counting them as agreement would make everything look like a match.
_DESC_STOPWORDS = {
    "led", "light", "lights", "lighting", "luminaire", "fixture", "the", "for", "and",
    "with", "a", "of", "in", "or", "mm", "w", "v", "dc", "ac", "applications", "application",
    "type", "series", "new", "available", "only", "shape", "round",
}

# A price row describing a COMPONENT must never be priced as a luminaire: a "Power Supply
# -24V (24W)" scored 100% against a 24W downlight on wattage+IP alone, because those two
# genuinely do share a wattage - the description is the only thing that says one is a
# driver and the other is the fixture it drives.
_COMPONENT_MARKERS = (
    "power supply", "driver", "connector", "jointer", "suspension", "spike", "snoot",
    "stopper", "tree belt", "microlouver", "honeycomb", "wall wash polarizing",
)


def _desc_tokens(text):
    words = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split()
    return {w for w in words if w not in _DESC_STOPWORDS and len(w) > 2}


def describes_component(text) -> bool:
    """True when a price row is describing a component/accessory rather than a luminaire."""
    low = re.sub(r"\s+", " ", str(text or "").lower())
    return any(m in low for m in _COMPONENT_MARKERS)


def description_agreement(price_label, specsheet):
    """How well a price row's own description agrees with what the specsheet says it is.

    Necessary because the numeric fields alone cannot tell products apart: a 24W power
    supply, a 24W recessed panel and a 24W downlight all match on wattage and IP. The
    description is the only field that carries product IDENTITY, so this gates every
    suggestion rather than merely nudging its score.

    Returns (fraction_of_price_words_found: float, shared_terms: list)."""
    p_tokens = _desc_tokens(price_label)
    if not p_tokens:
        return 0.0, []
    s_tokens = _desc_tokens(" ".join(filter(None, [
        specsheet.category, specsheet.product_name, specsheet.series, specsheet.family,
        specsheet.variant, (specsheet.fields or {}).get("mounting"),
    ])))
    shared = p_tokens & s_tokens
    return len(shared) / len(p_tokens), sorted(shared)
