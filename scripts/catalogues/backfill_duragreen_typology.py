"""One-time backfill: re-classifies every already-imported DuraGreen specsheet's
typology using the fixed priority order (import_duragreen_catalog.py, 2026-08-18) -
the product's OWN name first, falling back to the broader category/series text only
when the name itself has no typology keyword.

Bug this fixes: the original scraper classified typology from `category` alone
(site category page + series name), e.g. "Downlights - FLOW DOWNLIGHTS". DuraGreen
files some genuinely different fixture types under a family name that doesn't match
what they actually are - "FLOW DOWNLIGHTS" contains a member literally named "30 Spots
Linear" - so every one of that family's spotlights got classified "Downlight" purely
because the word appeared in its family/category text, never its own name. Found from
a real, wrong Match-tool suggestion (a downlight requirement top-matching a product
named "Spots Linear").

Database-only - no re-scraping needed, since product_name/category are already
stored. Prints every row before writing, safe to re-run (a row already correct is
simply left alone)."""
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
from app.typology_classifier import classify_typology


def main():
    db = SessionLocal()
    # typology_override="Driver" rows (Power Supplies) are deliberately untouched -
    # they were never classified by this keyword logic to begin with
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == "Duragreen").filter(models.Specsheet.typology != "Driver").all()

    changed = []
    for s in rows:
        new_typology = classify_typology(s.product_name) or classify_typology(s.category)
        if new_typology != (s.typology or ""):
            changed.append((s, new_typology))

    print(f"{len(changed)} row(s) to update out of {len(rows)}\n")
    for s, new_typology in changed:
        print(f"  #{s.id} {s.product_name!r} (series={s.series!r}): {s.typology or 'Unclassified'!r} -> {new_typology or 'Unclassified'!r}")
        s.typology = new_typology

    db.commit()
    print(f"\nDone. Updated {len(changed)} row(s).")


if __name__ == "__main__":
    main()
