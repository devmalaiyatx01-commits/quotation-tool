"""Add the columns quick BOQs need to tables that already exist.

New tables (boq_areas, boq_ref_images) are created by the app itself at startup. New columns
on tables that are already there are not - SQLAlchemy's create_all() only ever creates whole
tables - so the three below are added here:

    boqs.mode               "detailed" (what every existing BOQ is) or "quick"
    boq_items.area_ids      which of the project's areas a product goes into
    boq_items.ref_image_ids which reference pictures belong beside it

Safe to run more than once: each column is added only if it isn't there, and nothing is
written to any row that already has a value.

    python scripts/migrations/migrate_quick_boq.py --dry-run
    python scripts/migrations/migrate_quick_boq.py
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
from app import models

# column -> the type to add it as, per database. SQLite has no real JSON type (it stores JSON
# as TEXT and the driver reads it back through the same json serializer), so both work out the
# same in the app; they just have to be spelled differently in the ALTER.
NEW_COLUMNS = [
    ("boqs", "mode", {"postgresql": "VARCHAR", "sqlite": "VARCHAR"}, "'detailed'"),
    ("boq_items", "area_ids", {"postgresql": "JSON", "sqlite": "JSON"}, None),
    ("boq_items", "ref_image_ids", {"postgresql": "JSON", "sqlite": "JSON"}, None),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dialect = engine.dialect.name
    inspector = inspect(engine)
    planned = []
    for table, column, types, default in NEW_COLUMNS:
        if table not in inspector.get_table_names():
            print("   %-12s %-16s table not there yet - the app creates it at startup"
                  % (table, column))
            continue
        have = {c["name"] for c in inspector.get_columns(table)}
        if column in have:
            print("   %-12s %-16s already there" % (table, column))
            continue
        sql = "ALTER TABLE %s ADD COLUMN %s %s" % (table, column, types.get(dialect, "VARCHAR"))
        if default:
            sql += " DEFAULT %s" % default
        planned.append(sql)
        print("   %-12s %-16s will be added" % (table, column))

    # The tables that are wholly new are created here too, so a deploy does not have to wait
    # for the first request to the app for them to exist.
    models.Base.metadata.create_all(engine, tables=[
        models.BOQArea.__table__, models.BOQRefImage.__table__])
    print()
    print("boq_areas / boq_ref_images: present")

    if not planned:
        print("nothing else to do.")
        return
    if args.dry_run:
        print("--dry-run: would run")
        for sql in planned:
            print("   " + sql)
        return
    with engine.begin() as c:
        for sql in planned:
            c.execute(text(sql))
    # Every BOQ that existed before quick mode is a detailed one. The column default only
    # applies to rows written from now on, so say it explicitly for the ones already there.
    with engine.begin() as c:
        filled = c.execute(text("UPDATE boqs SET mode='detailed' WHERE mode IS NULL")).rowcount
    print("added %d column(s); %d existing BOQ(s) marked as detailed." % (len(planned), filled))


if __name__ == "__main__":
    main()
