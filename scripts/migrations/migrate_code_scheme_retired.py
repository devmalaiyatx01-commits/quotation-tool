"""Adds code_schemes.retired_at.

"Use brand default" on the Nomenclature tab used to delete the typology's own code structure,
so one click by mistake lost an order of segments somebody had worked out, with no way back
but rebuilding it by hand. The row is now kept and stamped with retired_at instead: a retired
structure codes nothing, and "Restore its own structure" brings it back exactly as it was.

Safe to re-run - checks the column before adding it, same pattern as
migrate_code_attribute_affixes.py. Existing rows get NULL, which means "in use", so nothing
any product's code says changes.

    python scripts/migrations/migrate_code_scheme_retired.py --dry-run
    python scripts/migrations/migrate_code_scheme_retired.py
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

COLUMNS = [("retired_at", "TIMESTAMP")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    args = ap.parse_args()
    inspector = inspect(engine)
    if "code_schemes" not in set(inspector.get_table_names()):
        print("table 'code_schemes' doesn't exist yet - the app creates it with the column.")
        return
    existing = {c["name"] for c in inspector.get_columns("code_schemes")}
    todo = [(name, ddl) for name, ddl in COLUMNS if name not in existing]
    for name, _ddl in COLUMNS:
        if name in existing:
            print("code_schemes.%s already exists" % name)
    if not todo:
        return
    if args.dry_run:
        for name, ddl in todo:
            print("would add code_schemes.%s %s" % (name, ddl))
        return
    with engine.begin() as conn:
        for name, ddl in todo:
            conn.execute(text("ALTER TABLE code_schemes ADD COLUMN %s %s" % (name, ddl)))
            print("added code_schemes.%s" % name)


if __name__ == "__main__":
    main()
