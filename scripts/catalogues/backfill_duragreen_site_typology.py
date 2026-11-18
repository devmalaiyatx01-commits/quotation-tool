"""Re-classifies every DuraGreen specsheet's typology using DURAGREEN'S OWN website
taxonomy (https://duragreenlighting.co.uk/lights/) instead of the InHouse-oriented
keyword classifier in app/typology_classifier.py.

Why a separate vocabulary at all
--------------------------------
The two brands genuinely categorise their catalogues differently, and forcing DuraGreen
into InHouse's typology names loses real distinctions: DuraGreen sells "Surface",
"Suspension", "Shelf & Joinery Lights" and "Outdoor" as first-class categories, none of
which exist in the InHouse vocabulary, while InHouse's "Wall Grazer / Wall Washer" and
"Flexible Neon / LED Band" have no DuraGreen equivalent. Running DuraGreen through
classify_typology() previously produced 90 unclassified rows and silently collapsed
distinct families into whichever InHouse keyword happened to appear in the text.

The two vocabularies are kept apart by BRAND, not by a second column: `typology` holds
whichever taxonomy that row's own manufacturer uses, and the UI scopes its typology
chips/dropdowns to the selected brand so the two lists never mix (see
renderClassificationChips / buildConfiguratorPanel / renderPricingTypologyChips in
static/app.js). A second column would have to be threaded through the Library filter,
the Configurator, the Match tool, price coverage and the Pricing Master export - all of
which already read `typology`.

Where each row's category comes from
------------------------------------
  - Scraped rows (import_duragreen_catalog.py): `category` is already
    "{site category} - {series}", so the prefix IS the authoritative site category.
    Only "Ceiling Surface" is renamed, to the site's own display name "Surface".
  - The 52 rows imported from the PDF catalogue instead carry a singular "Downlight"
    prefix that is not a real DuraGreen category and is wrong for some of them (a row
    literally named "...Wall Light", and the whole ULTRAFIN shelf range). Those are
    mapped by series/name - see PDF_RULES.

Deliberately NOT touched
------------------------
  - Power Supplies keep typology "Driver". That is not a DuraGreen category decision:
    GET /api/specsheets filters `typology != "Driver"` on purpose, because drivers are
    added through the Add Driver mini-configurator rather than browsed as products.
    Renaming them would make 37 drivers appear in the Library and Configurator.
  - The four categories excluded per direct instruction (Floor Lamps, Table Lamps,
    Lighting Control Systems, Demo Kit) are left exactly as they are by this script -
    excluding them is a separate decision from classifying them.

Dry run by default; pass --commit to write.
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

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models

# DuraGreen's own category list, read off the live site 2026-08-29. The four the user
# excluded are listed so they are recognised and skipped rather than silently falling
# through to "unclassified".
SITE_CATEGORIES = {
    "Downlights", "Spotlights", "Shelf & Joinery Lights", "Surface", "Suspension",
    "Wall Lights", "Step Lights", "Track Lights", "LED Strip", "Outdoor",
}
EXCLUDED_CATEGORIES = {"Floor Lamps", "Table Lamps", "Lighting Control Systems", "Demo Kit"}

# The importer's display name -> the site's own display name, where they differ.
CATEGORY_ALIASES = {"Ceiling Surface": "Surface", "Led Strip": "LED Strip"}


def duragreen_category(s) -> str | None:
    """The DuraGreen site category this specsheet belongs to, or None to leave it alone."""
    if (s.typology or "") == "Driver":
        return None  # Power Supplies - see module docstring
    prefix = (s.category or "").split(" - ")[0].strip()
    prefix = CATEGORY_ALIASES.get(prefix, prefix)
    if prefix in EXCLUDED_CATEGORIES:
        return None
    if prefix in SITE_CATEGORIES:
        return prefix
    if prefix == "Downlight":
        # The PDF-catalogue import. Its prefix is not a real category, so classify from
        # the row's own series/name - every ACME series is a recessed downlight family,
        # and the two exceptions name themselves.
        if (s.series or "").strip() == "Shelf & Joinery Lights":
            return "Shelf & Joinery Lights"
        if "wall light" in (s.product_name or "").lower():
            return "Wall Lights"
        return "Downlights"
    return None


def main():
    commit = "--commit" in sys.argv
    db = SessionLocal()
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == "Duragreen").all()

    changed, unchanged, skipped = [], 0, []
    for s in rows:
        target = duragreen_category(s)
        if target is None:
            skipped.append(s)
            continue
        if target == (s.typology or ""):
            unchanged += 1
            continue
        changed.append((s, target))

    print(f"DuraGreen specsheets: {len(rows)}")
    print(f"  to re-classify : {len(changed)}")
    print(f"  already correct: {unchanged}")
    print(f"  left untouched : {len(skipped)} (Drivers + the 4 excluded categories)")
    print()

    from collections import Counter
    moves = Counter((s.typology or "(unclassified)", t) for s, t in changed)
    print("Re-classifications (old InHouse typology -> DuraGreen category):")
    for (old, new), n in moves.most_common():
        print(f"  {n:>4}  {old:<28} -> {new}")

    for s, target in changed:
        s.typology = target

    if not commit:
        db.rollback()
        print("\nDRY RUN - nothing written. Re-run with --commit.")
        db.close()
        return

    db.commit()
    final = Counter((s.typology or "(unclassified)") for s in
                    db.query(models.Specsheet).filter(models.Specsheet.make == "Duragreen").all())
    print("\nCOMMITTED. DuraGreen typologies now:")
    for t, n in final.most_common():
        print(f"  {n:>4}  {t}")
    db.close()


if __name__ == "__main__":
    main()
