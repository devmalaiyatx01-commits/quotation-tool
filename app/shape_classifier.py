"""Keyword-based fixture-shape classification (Round / Square / Dual Square).

Two products can share a typology (both "Downlight") while being physically
incompatible - a round downlight cannot be substituted with a square or
dual-square one, or vice versa. The requirement Match tool's own typology gate
(see requirement_matcher.py / the standing rule in app/main.py's
requirement_match()) doesn't catch this at all: "OZONE Dual Square -Mini" and
"OZONE -Mini" are both typology "Downlight" - nothing about typology
distinguishes their physical shape. Found from a real reported case: a
requirement explicitly stating "Ceiling Recessed Round Shaped..." was ranked
a 100% match against OZONE Dual Square -Mini, purely because Wattage/CCT/IP
Rating/Control happened to line up - shape was never checked at all. This is
a second, equally hard gate for exactly that, same "non-negotiable, not just
scored" treatment typology already gets.

Checked against the real library before writing this (706 specsheets, every
brand): "dual square"/"twin square" appears in 17 specsheets (a real,
unambiguous term this catalog already uses - OZONE Dual Square, ACME's Twin
Square), bare "square" (not dual/twin) in 22 more, "round" explicitly in 32.
The remaining ~630 state no shape keyword at all - this catalog's own naming
convention leaves a plain round fixture unlabeled (e.g. "OZONE -Mini" vs
"OZONE Square -Mini" vs "OZONE Dual Square -Mini" - only the non-round
variants say so in their own series/product name). That "no keyword = Round"
default only ever applies on the SPECSHEET side (see classify_specsheet_shape)
- a requirement paragraph that doesn't mention shape at all means "no stated
preference", not "round", so it is never defaulted the same way (same "leave
blank rather than guess" rule classify_typology already follows).

Deliberately NOT added: "oval" (6 real specsheets use it, but for the pinhole/
aperture cutout shape within an otherwise-round fixture body - e.g. "58 Oval
Hole Adjustable" under the "MOOD ROUND" series - a different attribute from
the fixture's own overall shape, not a competing top-level category);
"rectangular"/"rectangle"/"circular" (zero real specsheets use either,
nothing to ground them against - same discipline as typology_classifier.py
declining to add "zoom" as a spotlight signal with no unambiguous real usage).
"""

# Ordered most-specific-first: "dual square"/"twin square" must be checked
# before the generic "square" match, since both contain it (same convention
# as typology_classifier.py's "track" before "spot").
SHAPE_KEYWORDS = [
    (("dual square", "twin square"), "Dual Square"),
    (("square",), "Square"),
    (("round",), "Round"),
]


def classify_shape(text: str) -> str:
    """Best-effort shape from free text. Returns "" if nothing matches - no
    default applied here; see classify_specsheet_shape for the specsheet-side
    "no keyword found = Round" convention this catalog's own naming uses."""
    text = (text or "").lower()
    for keywords, shape in SHAPE_KEYWORDS:
        if any(kw in text for kw in keywords):
            return shape
    return ""


def classify_specsheet_shape(specsheet) -> str:
    """A specsheet's real shape, applying this catalog's own unstated-default
    convention: no shape keyword in the product's own identity text means a
    plain round fixture (checked directly against the real library - see this
    module's docstring), never left blank. Scans product_name/series/family,
    not `category` alone (classify_typology's field) - OZONE, OZONE Square,
    and OZONE Dual Square all share identical category text ("Recessed LED
    Downlight - Adjustable - With Trim"); only their series/product name
    actually differ."""
    text = " ".join([specsheet.product_name or "", specsheet.series or "", specsheet.family or ""])
    return classify_shape(text) or "Round"
