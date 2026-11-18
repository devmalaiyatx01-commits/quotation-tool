"""Adds code_attributes.prefix and code_attributes.suffix.

A rule turns a value into a code - 7W into 7, 3000K into 30 - and a number alone does not say
what it measures: in "DL-...-7-27-95-15" nobody can tell the wattage from the CCT. The prefix
and suffix are written around what a rule produces, so the same product reads 7W-27K-CRI95-15D:
every numeric segment carries its unit. Typed abbreviations are taken as they are.

Safe to re-run - checks each column before adding it, same pattern as migrate_boq_branching.py.

    python scripts/migrations/migrate_code_attribute_affixes.py --dry-run
    python scripts/migrations/migrate_code_attribute_affixes.py
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

from sqlalchemy import inspect, text

from app.database import engine

COLUMNS = [("prefix", "VARCHAR DEFAULT ''"), ("suffix", "VARCHAR DEFAULT ''")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    args = ap.parse_args()
    inspector = inspect(engine)
    if "code_attributes" not in set(inspector.get_table_names()):
        print("table 'code_attributes' doesn't exist yet - the app creates it with both columns.")
        return
    existing = {c["name"] for c in inspector.get_columns("code_attributes")}
    todo = [(name, ddl) for name, ddl in COLUMNS if name not in existing]
    for name, _ddl in COLUMNS:
        if name in existing:
            print("code_attributes.%s already exists" % name)
    if not todo:
        return
    if args.dry_run:
        for name, ddl in todo:
            print("would add code_attributes.%s %s" % (name, ddl))
        return
    with engine.begin() as conn:
        for name, ddl in todo:
            conn.execute(text("ALTER TABLE code_attributes ADD COLUMN %s %s" % (name, ddl)))
            print("added code_attributes.%s" % name)


if __name__ == "__main__":
    main()
