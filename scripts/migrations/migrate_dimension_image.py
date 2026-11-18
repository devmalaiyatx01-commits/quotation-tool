"""Add boq_items.dimension_image: which picture a row puts in the Dimension column.

A new column on a table that already exists, so SQLAlchemy's create_all() will not add it - this
does. Blank for every existing row, which means the line drawing, exactly as before.

Safe to run more than once: the column is only added if it is not already there.

    python scripts/migrations/migrate_dimension_image.py --dry-run
    python scripts/migrations/migrate_dimension_image.py
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
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import inspect, text

from app.database import engine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    have = {c["name"] for c in inspect(engine).get_columns("boq_items")}
    if "dimension_image" in have:
        print("boq_items.dimension_image already there - nothing to do.")
        return
    sql = "ALTER TABLE boq_items ADD COLUMN dimension_image VARCHAR DEFAULT ''"
    if args.dry_run:
        print("--dry-run: would run\n   " + sql)
        return
    with engine.begin() as c:
        c.execute(text(sql))
    with engine.begin() as c:
        filled = c.execute(text("UPDATE boq_items SET dimension_image='' WHERE dimension_image IS NULL")).rowcount
    print("added boq_items.dimension_image; %d existing row(s) set to blank (the line drawing)." % filled)


if __name__ == "__main__":
    main()
