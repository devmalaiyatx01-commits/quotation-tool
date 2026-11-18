"""Keyword-based Interior/Exterior classification.

Companion to typology_classifier.py, same "leave blank rather than guess" rule, but
with one real difference: this isn't a single-value classification. Some products are
legitimately rated/sold for both contexts (a strip light or track fixture might show up
in the studio's own reference library filed under both an Interior and an Exterior
folder), so a specsheet can end up tagged Interior, Exterior, both, or neither - never
forced into an either/or choice it doesn't actually have.

Only Exterior has real, common self-describing keywords in this app's actual specsheet
data (InHouse writes "Underwater...", "Inground..." right in the category text).
Interior almost never announces itself the same way - a plain "Recessed LED Downlight"
title block doesn't say "for indoor use" even though that's what it is - so the interior
keyword list here is intentionally short and mostly won't match. That's expected, not a
bug: guessing "Interior" as a fallback default for anything that isn't clearly Exterior
would make the field misleading (an unclassified product and a *confirmed* interior
product would look identical), which defeats the point of having it. Leave it blank and
let a human tag it once in the Library, same as an unclassified typology.
"""

EXTERIOR_KEYWORDS = (
    "underwater", "under water", "inground", "in-ground", "in ground",
    "outdoor", "exterior", "marine",
)
INTERIOR_KEYWORDS = ("interior", "indoor")


def classify_environment(category: str) -> list[str]:
    """Best-effort Interior/Exterior tags from a specsheet's parsed `category` text.
    Returns a subset of ["Interior", "Exterior"] - empty if neither is confidently implied."""
    text = (category or "").lower()
    result = []
    if any(kw in text for kw in INTERIOR_KEYWORDS):
        result.append("Interior")
    if any(kw in text for kw in EXTERIOR_KEYWORDS):
        result.append("Exterior")
    return result
