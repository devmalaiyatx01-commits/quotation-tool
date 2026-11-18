"""One-off migration: adds boq_items.reference_number, in place, without touching any
existing data. Safe to re-run - checks before acting.

Goes through the app's own `engine`, same as migrate_pricing.py - correctly targets
whichever backend DATABASE_URL currently points at (SQLite or Postgres).
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
from sqlalchemy import inspect, text

from app.database import engine


def main():
    inspector = inspect(engine)
    if "boq_items" not in set(inspector.get_table_names()):
        print("table 'boq_items' doesn't exist yet - nothing to migrate.")
        return
    existing_cols = {c["name"] for c in inspector.get_columns("boq_items")}
    if "reference_number" in existing_cols:
        print("boq_items.reference_number already exists")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE boq_items ADD COLUMN reference_number TEXT DEFAULT ''"))
    print("added boq_items.reference_number")


if __name__ == "__main__":
    main()
