"""One-off migration: adds boqs.project_code, in place, without touching any existing
data. Safe to re-run - checks before acting.

Goes through the app's own `engine`, same as migrate_reference_number.py - correctly
targets whichever backend DATABASE_URL currently points at (SQLite or Postgres).
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
    if "boqs" not in set(inspector.get_table_names()):
        print("table 'boqs' doesn't exist yet - nothing to migrate.")
        return
    existing_cols = {c["name"] for c in inspector.get_columns("boqs")}
    if "project_code" in existing_cols:
        print("boqs.project_code already exists")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE boqs ADD COLUMN project_code TEXT DEFAULT ''"))
    print("added boqs.project_code")


if __name__ == "__main__":
    main()
