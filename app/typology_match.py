"""Deciding whether a requirement's typology and a product's typology are the same thing.

A requirement match should not offer a wall lamp for a downlight requirement, however well its
wattage and beam angle happen to line up. Typology is the one field where a near miss is not a
near miss at all - it is a different product.

Why this is not string equality
-------------------------------
The obvious rule, `requirement.typology == product.typology`, makes matching WORSE here, and
the library says so plainly:

    Downlights              301 rows
    Downlight               114 rows        <- the same thing, entered under two names
    Luminaires for tracks    84 rows
    Track Lights             65 rows        <- likewise

and app/typology_classifier.py, reading a requirement paragraph, produces the singular
("Downlight", "Track Light"). So exact matching on a downlight requirement would return the
114 and silently discard the 301 - the majority of the downlights in the catalogue.

So sameness is decided on a normalised key: case and punctuation folded away, a trailing
plural dropped, and the handful of genuine synonyms this catalogue actually contains mapped
together. Everything here is a synonym pair that EXISTS in the data, not a general-purpose
lighting thesaurus - a made-up equivalence would merge two things a specifier keeps apart.
"""
import re
from functools import lru_cache

# Synonyms actually present in this library, checked against the typology counts. Written as
# normalised key -> canonical key, applied after the plural is dropped.
SYNONYMS = {
    "luminaire for track": "track light",
    "luminaires for track": "track light",
    "track luminaire": "track light",
    "spot light": "spotlight",
    "wall lamp": "wall light",
    "wall washer": "wall grazer",
    "pendant  ceiling lamp": "pendant",
    "pendant ceiling lamp": "pendant",
    "ceiling lamp": "pendant",
    "projector and floodlight": "projector",
    "floodlight": "projector",
    "flexible strip light": "strip light",
    "linear strip": "strip light",
    "cob strip": "strip light",
}


@lru_cache(maxsize=4096)
def typology_key(name: str) -> str:
    """A comparable form of a typology name. Empty string when there is nothing to compare."""
    key = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    key = " ".join(key.split())
    if not key:
        return ""
    # Drop a trailing plural on the last word only: "Downlights" -> "downlight", while
    # "Luminaires for tracks" -> "luminaires for track", which SYNONYMS then maps.
    words = key.split()
    if len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    key = " ".join(words)
    return SYNONYMS.get(key, key)


@lru_cache(maxsize=4096)
def category_kind(category: str) -> str:
    """The kind a product's maker files it under, from its category's first part: "Downlights - MOOD
    ROUND" is "downlight". Empty when that part names no kind in itself ("Outdoor - WASHFLEX Linear"
    gives "outdoor", which is no typology, and so matches nothing). A wall-wash downlight is both a
    wall washer and a downlight, so a person looking under either should find it."""
    return typology_key(re.split(r"\s+-\s+", category or "")[0])


def same_typology(requirement_typology: str, product_typology: str) -> bool:
    """Whether these name the same kind of product.

    Unknown on EITHER side means this test cannot be applied, and it answers True rather than
    excluding the product. That is deliberate: a requirement whose typology could not be read
    must not silently lose every candidate, which would turn an unrecognised paragraph into an
    empty result with no explanation.
    """
    a, b = typology_key(requirement_typology), typology_key(product_typology)
    if not a or not b:
        return True
    return a == b
