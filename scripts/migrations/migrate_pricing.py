"""One-off migration: adds price/led_color/model_no to boq_items and discount_percent/
currency to boqs, in place, without touching any existing data. Safe to re-run - checks
each column before adding it.

Unlike the earlier migrate_*.py scripts (which hardcode `sqlite3.connect()` against
data/app.db), this one goes through the app's own `engine` - so it correctly targets
whichever backend DATABASE_URL currently points at (SQLite by default, or Postgres once
that's set up - see deployment.md section 3.2). Plain `ADD COLUMN ... FLOAT DEFAULT 0` /
`... TEXT DEFAULT ''` is valid in both dialects, so no dialect branching is needed for
these particular column types.
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

COLUMNS = {
    "boq_items": [
        ("price", "FLOAT DEFAULT 0"),
        ("led_color", "TEXT DEFAULT ''"),
        ("model_no", "TEXT DEFAULT ''"),
    ],
    "boqs": [
        ("discount_percent", "FLOAT DEFAULT 0"),
        ("currency", "TEXT DEFAULT 'INR'"),
    ],
}


def main():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in COLUMNS.items():
            if table not in existing_tables:
                print(f"table {table!r} doesn't exist yet - nothing to migrate (a fresh Base.metadata.create_all() will create it correctly).")
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_ddl in columns:
                if col_name in existing_cols:
                    print(f"{table}.{col_name} already exists")
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"))
                print(f"added {table}.{col_name}")


if __name__ == "__main__":
    main()
