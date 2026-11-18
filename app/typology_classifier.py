"""Keyword-based typology classification.

`typology` (the business classification config rules are scoped to - see
config_rules.py) can't be reliably read straight off a PDF the way `category`
(the literal product-type text in its title block) can - it's a curatorial
decision. But most new specsheets are more of the *same* typologies already in
the library (another downlight, another strip), not a genuinely new one - so
requiring a manual classification click every single time is unnecessary
friction for the common case. This module guesses from `category` text using
a keyword table, and only that: if nothing matches, it returns "" rather than
inventing a new classification name, since an incorrect auto-classification
would silently apply the wrong typology's rules to a real BOQ item - worse
than asking a human once.

Pure function, no DB access - same "engine" pattern as config_rules.py and
pdf_parser.py (see TUTORIAL.md section 6.3).
"""

# Ordered most-specific-first: "track" must be checked before the generic "spot"
# match, since "Track Spot" contains both. Extend this list (and re-run it against
# already-unclassified specsheets) whenever a genuinely new typology gets added.
#
# "uplight" and "spot" (2026-08-10) were added after checking a real reference -
# ERCO's Handbook of Lighting Design (SampleInputs/Lighting Encyclopedia.pdf,
# section 2.7 "Luminaires") - not guessed: 2.7.1.2 Uplights confirms "uplight" as
# the standard term for a fixture that emits light upwards, mounted on/in the
# floor or wall (an inground fixture is just one mounting method of an uplight,
# not a separate luminaire type); 2.7.2.1 Spotlights confirms "spot"/"spotlight"
# as the standard term for accent fixtures with a defined beam angle. Widened
# from the literal word "spotlight" to the substring "spot" specifically because
# real specsheets in this library (Aura, Riva) say "...LED Spot..." in their own
# category text, never spelling out "spotlight" in full - "track" staying first
# in this list is what keeps that broadening safe ("Track Spot" still matches
# "track" first, same as before).
#
# "step light" has no equivalent in that 1992 reference (a modern fixture
# category it predates) - kept anyway because it's the exact, unambiguous term a
# real specsheet's own category text already uses (RASO-1: "Wall Recessed Step
# Light"), not an invented one.
#
# "louvre"/"louver" (2026-08-18) - added after re-checking the same reference while
# investigating a wrong Match-tool suggestion: 2.7.1.3 "Louvred luminaires" confirms
# this as its own real, distinct stationary-luminaire category (linear light sources
# with anti-glare louvre/reflector/prismatic-diffuser attachments), not something
# invented for this app. Zero specsheets currently in the library use either spelling
# in their name (checked before adding, same as every other entry here) - kept anyway
# as a genuine, zero-risk gap-fill for whenever one does, rather than added reactively
# after the fact.
#
# Also re-checked while investigating the same bug and deliberately NOT added: "zoom"/
# "zoomable" - the reference does describe variable-beam-angle-via-zoom-lens as a
# spotlight trait (2.7.2.1), but real specsheets in this library use "Zoom" across
# genuine Downlights, Track Lights, AND Spotlights depending on the rest of the name
# (e.g. "78 Bella Zoom Trimless Short Snoot" is a recessed downlight with a zoom
# feature, not a spotlight) - a blanket "zoom" keyword would misclassify the first two
# to fix the third, which already classifies correctly today because it says
# "Spotlight" outright. Keyword-based classification only works when the word itself
# is unambiguous; "zoom" isn't, in this library's actual data.
#
# "underwater"/"under water"/"submersible" (2026-08-19) - unlike every entry above,
# checked against the reference and NOT found there: read the full 289-page document
# end to end looking for "underwater"/"submersible"/"pool"/"fountain" and found no
# dedicated pool/fountain/submersible-fixture section - section 2.7's taxonomy is a
# general architectural interior/exterior scheme (this 1992 text predates aquatic/
# landscape lighting as its own discipline the way it predates step lights, per that
# entry above). Added anyway on the SAME basis "step light" was: real specsheets in
# this library say it themselves, unambiguously, in their own category text (Aura-Nano/
# Aura-Midi: "Underwater Recessed LED Spot"; Riva-Midi: "Underwater Surface LED Spot";
# TIDE 1617 SV: "Underwater Flexible Homogeneous LED Band") - these were previously
# swept into Spotlight/Flexible Neon purely because "spot"/"band" matched first and
# nothing here recognized "underwater" as its own category. Ordered FIRST, ahead of
# every other keyword: a product whose own category text says "underwater" should
# never lose to a shape/form keyword ("spot", "band") also present in that same text.
TYPOLOGY_KEYWORDS = [
    (("underwater", "under water", "submersible"), "Underwater Fixture"),
    (("track",), "Track Light"),
    (("grazer", "wall wash", "washer"), "Wall Grazer / Wall Washer"),
    (("strip",), "Flexible Strip Light"),
    (("band", "neon"), "Flexible Neon / LED Band"),
    (("louvre", "louver"), "Louvred Luminaire"),
    (("downlight",), "Downlight"),
    (("uplight",), "Uplight"),
    (("step light",), "Step Light"),
    (("spot",), "Spotlight"),
]


def classify_typology(category: str) -> str:
    """Best-effort typology from a specsheet's parsed `category` text.
    Returns "" (leave for manual classification) if nothing matches confidently."""
    text = (category or "").lower()
    for keywords, typology in TYPOLOGY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return typology
    return ""
