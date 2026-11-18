"""Builds the NPD parameter templates out of the products already in the library.

The point is not to invent a list of what describes a downlight - the studio's own specsheets
already say, several hundred times over. For each typology this reads every product of that
typology, counts which (group, parameter) pairs its specsheets actually carry, and keeps the
ones that appear on at least half of them. A parameter present on two sheets out of three
hundred is that product's quirk, not the typology's shape, and starting every new product with
it would be worse than starting without.

    python scripts/maintenance/seed_parameter_templates.py                 # every typology with enough products
    python scripts/maintenance/seed_parameter_templates.py --typology Downlight
    python scripts/maintenance/seed_parameter_templates.py --dry-run       # show what it would write

Re-running is safe: existing rows are left alone, including any parameter someone added by
hand while entering a product (user_added=True), which is never removed by a reseed.
"""
# --- repo-root shim ------------------------------------------------------------------
# Running a script file puts ITS OWN directory on sys.path, not the repo root, so `app`
# would not import and a `Path(__file__).parent` would no longer be the project root.
# Both are fixed here by walking up to the directory that contains app/ - the same rule
# app/database.py uses. Run this script from anywhere; paths below stay repo-relative.
import pathlib as _pathlib, sys as _sys
_REPO_ROOT = next((p for p in _pathlib.Path(__file__).resolve().parents
                   if (p / "app").is_dir()), None)
if _REPO_ROOT is None:
    raise SystemExit("Cannot find the repo root: no parent directory contains app/.")
_sys.path.insert(0, str(_REPO_ROOT))
# --- end shim ------------------------------------------------------------------------
import sys
import re
import argparse
import collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func

from app.database import SessionLocal
from app import models

# A parameter has to appear on this share of a typology's products to count as describing the
# typology rather than one product.
PRESENT_ON = 0.5
# Below this there is not enough evidence to call anything typical.
MIN_PRODUCTS = 3

# Fields a buyer chooses between, as opposed to fixed facts about the product. Only these get
# an option list and price adders in the NPD form. Matched on the flattened field key, so it
# does not matter what a given manufacturer calls the column.
CONFIGURABLE_KEYS = {
    "wattage", "cct", "beam_angle", "finish", "control", "cri", "lumen_output",
    "length", "mounting", "ip_rating", "optics_type", "reflector_finish",
    "trim_details", "adjustability", "accessories",
}


# The vocabulary these specsheets are written in. Needed because pdf text extraction returns a
# table cell with its spaces collapsed - the page prints "CUTOUT DIMENSIONS" and the parser
# reads "CUTOUTDIMENSIONS" - so both the stored key and the label the generated sheet prints
# have to put the spaces back.
KNOWN_WORDS = [
    "cutout", "dimensions", "trim", "details", "material", "finish", "adjustability",
    "ip", "ik", "rating", "operating", "temperature", "optics", "type", "reflector",
    "beam", "angle", "ugr", "accessories", "source", "colour", "color",
    "consistency", "lumen", "maintenance", "package", "output", "actual", "power",
    "cct", "cri", "ballast", "control", "gear", "input", "voltage", "factor",
    "supply", "parameters", "mechanical", "optical", "electrical", "wattage",
    "mounting", "length", "warranty", "lifetime", "efficacy", "driver",
]


def split_words(label):
    """A printed label as its individual words, with run-together spellings separated."""
    text = re.sub(r"[^A-Za-z0-9]+", " ", str(label or "")).strip()
    out = []
    for token in text.split():
        low = token.lower()
        if low.isalpha() and len(low) > 6 and low not in KNOWN_WORDS:
            rest = low
            while rest:
                for w in sorted(KNOWN_WORDS, key=len, reverse=True):
                    if rest.startswith(w):
                        out.append(w)
                        rest = rest[len(w):]
                        break
                else:
                    out.append(rest)
                    break
        else:
            out.append(low)
    return out


def display_label(label):
    """What the generated specsheet prints - the label with its spaces restored.

    "CUTOUTDIMENSIONS" -> "CUTOUT DIMENSIONS", matching how a real InHouse sheet reads. The
    run-together form is an artefact of reading the PDF, never how the page looked.

    Only a label that is one unbroken run of letters and digits is split, because that is the
    only shape the artefact takes. A label that already carries a space or a mark of its own
    was read correctly and is left exactly as it is: splitting on punctuation turned the
    sheet's "C.C/ C.V" into "C C C V", "T.H.D" into "T H D" and "L70/B50" into "L70 B50" -
    none of which is what the page says.
    """
    text = str(label or "").strip()
    if not text:
        return ""
    if not text.isalnum():
        return text.upper()
    words = split_words(text)
    return " ".join(w.upper() for w in words) if words else text.upper()


def field_key(label):
    """The flattened name the rest of the app knows a parameter's value by.

    Mirrors how the PDF parser already flattens a printed label ("CUTOUT DIMENSIONS" ->
    cutout_dimensions)."""
    key = "_".join(split_words(label))
    # the canonical names the app already uses, where they differ from a literal flattening
    alias = {
        "actual_power": "wattage",
        "ballast_control_gear": "control_gear",
        "colour_consistency": "color_consistency",
    }
    return alias.get(key, key)


# Two parameters on a InHouse sheet are printed under one name in two groups but are not the
# same quantity, and giving them one key made a product state the wrong figure for one of them:
#
#   ACTUAL POWER (source)  is what the LED draws;  POWER (power supply) is what the driver is
#   rated for, and is always the larger - 5W/7W/9W against 6W/8W/10W on NYX -Mini.
#   IP RATING (mechanical) is the luminaire's ingress rating; IP RATING (power supply) is the
#   driver's, and a recessed downlight is commonly IP44 at the ceiling with an IP20 driver.
#
# So the group decides the key for these, and the value each one holds is its own.
GROUP_SCOPED_KEYS = {
    ("POWER SUPPLY PARAMETERS", "power"): "driver_power",
    ("POWER SUPPLY PARAMETERS", "ip_rating"): "driver_ip_rating",
}


def scoped_field_key(group, label):
    """The key a parameter stores its value under, once the group it is printed in is known."""
    key = field_key(label)
    return GROUP_SCOPED_KEYS.get((display_label(group), key), key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--typology", action="append", help="only this typology (repeatable)")
    ap.add_argument("--make", default="InHouse",
                    help="read templates from this manufacturer's sheets only "
                         "(default InHouse; pass '' for every make)")
    ap.add_argument("--replace", action="store_true",
                    help="clear previously seeded rows first, keeping anything a user added")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    if args.replace and not args.dry_run:
        # Only the seeded rows. A parameter someone added while entering a product is theirs,
        # and a reseed is not a reason to take it away.
        gone = (db.query(models.ParameterTemplate)
                .filter(models.ParameterTemplate.user_added == False)  # noqa: E712
                .delete(synchronize_session=False))
        db.commit()
        print("cleared %d previously seeded parameters" % gone)
        print()

    q = db.query(models.Specsheet.typology, models.Specsheet.params,
                 models.Specsheet.discipline).filter(models.Specsheet.typology != "")
    if args.make:
        # One manufacturer's sheets, not every manufacturer's. Reading them all mixes four
        # different documentation styles into one template - Exenia's "Configurator (Exenia)"
        # and Lumenpulse's "Documents" are how those companies lay out a page, not what a
        # luminaire is made of, and neither belongs on a sheet this studio issues.
        q = q.filter(func.lower(models.Specsheet.make) == args.make.strip().lower())
    rows = q.all()
    if not rows:
        raise SystemExit("No products found for make=%r - nothing to read a template from."
                         % args.make)

    by_typology = collections.defaultdict(list)
    for typology, params, discipline in rows:
        if args.typology and typology not in args.typology:
            continue
        by_typology[(discipline, typology)].append(params or {})

    existing = collections.defaultdict(set)
    for t in db.query(models.ParameterTemplate).all():
        existing[(t.discipline, t.typology)].add((t.group_name, t.label))

    written = skipped_typologies = 0
    for (discipline, typology), sheets in sorted(by_typology.items()):
        if len(sheets) < MIN_PRODUCTS:
            skipped_typologies += 1
            continue
        counts = collections.Counter()
        order = {}
        for params in sheets:
            seen = 0
            for group, kv in params.items():
                if not isinstance(kv, dict):
                    continue
                for label in kv:
                    counts[(group, label)] += 1
                    order.setdefault((group, label), seen)
                    seen += 1
        threshold = len(sheets) * PRESENT_ON
        keep = [(g, l) for (g, l), n in counts.items() if n >= threshold]
        if not keep:
            skipped_typologies += 1
            continue
        keep.sort(key=lambda gl: (order.get(gl, 999), gl[1]))

        made = 0
        for i, (group, label) in enumerate(keep):
            if (group, label) in existing[(discipline, typology)]:
                continue
            key = scoped_field_key(group, label)
            row = models.ParameterTemplate(
                discipline=discipline, typology=typology,
                group_name=display_label(group), label=display_label(label),
                field_key=key, sort_order=i,
                is_configurable=key in CONFIGURABLE_KEYS, user_added=False,
            )
            if not args.dry_run:
                db.add(row)
            made += 1
        written += made
        print("%-26s %-14s %4d products -> %3d parameters (%d new)"
              % (typology, discipline, len(sheets), len(keep), made))

    if args.dry_run:
        db.rollback()
        print("\n--dry-run: nothing written. %d parameters would be added." % written)
    else:
        db.commit()
        print("\nwrote %d parameter rows" % written)
    print("typologies skipped for too few products (<%d) or no common parameters: %d"
          % (MIN_PRODUCTS, skipped_typologies))
    db.close()


if __name__ == "__main__":
    main()
