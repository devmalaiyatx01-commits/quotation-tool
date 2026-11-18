"""One-off migration: adds specsheets.environment, in place, without touching any
existing data. Safe to re-run - checks before acting.

Goes through the app's own `engine`, same as migrate_reference_number.py - correctly
targets whichever backend DATABASE_URL currently points at (SQLite or Postgres). Unlike
the plain TEXT/FLOAT columns those scripts added, this is a JSON column (a list, e.g.
["Interior"] or ["Interior", "Exterior"]) - SQLite and Postgres spell a JSON column
default differently (SQLite takes the literal text; Postgres needs it cast with
`::json`), so this one branches on dialect where migrate_pricing.py didn't need to.
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
    if "specsheets" not in set(inspector.get_table_names()):
        print("table 'specsheets' doesn't exist yet - nothing to migrate.")
        return
    existing_cols = {c["name"] for c in inspector.get_columns("specsheets")}
    if "environment" in existing_cols:
        print("specsheets.environment already exists")
        return
    default_sql = "'[]'" if engine.dialect.name == "sqlite" else "'[]'::json"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE specsheets ADD COLUMN environment JSON DEFAULT {default_sql}"))
    print("added specsheets.environment")


if __name__ == "__main__":
    main()
