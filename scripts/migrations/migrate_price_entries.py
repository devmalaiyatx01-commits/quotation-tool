"""One-off migration: creates the new price_entries table (see app/models.py's
PriceEntry) - a brand-new table, not a column added to an existing one, so this is a
plain CREATE TABLE via SQLAlchemy's own metadata rather than a hand-written ALTER TABLE
(migrate_pricing.py's pattern is for adding columns to a table that already has rows;
there's nothing to preserve here). Safe to re-run - create_all() only creates tables that
don't already exist.
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
from app.database import engine
from app import models


def main():
    models.Base.metadata.create_all(bind=engine, tables=[models.PriceEntry.__table__])
    print("price_entries table created (or already existed).")


if __name__ == "__main__":
    main()
