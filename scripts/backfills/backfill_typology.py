"""One-off data backfill: runs the typology classifier (app/typology_classifier.py)
against every specsheet currently sitting Unclassified, for libraries that had
specsheets uploaded before auto-classification existed. Safe to re-run - only
touches rows where typology is still blank, and only ever sets a typology,
never clears one a human already assigned.
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
from app.database import SessionLocal
from app import models
from app.typology_classifier import classify_typology


def main():
    db = SessionLocal()
    try:
        unclassified = db.query(models.Specsheet).filter(
            (models.Specsheet.typology == "") | (models.Specsheet.typology.is_(None))
        ).all()
        if not unclassified:
            print("Nothing Unclassified - nothing to do.")
            return
        updated = 0
        for s in unclassified:
            guess = classify_typology(s.category)
            if guess:
                print(f"{s.product_name!r} ({s.category!r}) -> {guess!r}")
                s.typology = guess
                updated += 1
            else:
                print(f"{s.product_name!r} ({s.category!r}) -> no confident match, left Unclassified")
        db.commit()
        print(f"\n{updated} of {len(unclassified)} previously-unclassified specsheet(s) updated.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
