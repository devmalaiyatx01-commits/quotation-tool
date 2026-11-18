"""Creates configurator_fields - which details the Configurator asks for each brand's typology,
decided in NPD (see models.ConfiguratorField and app/configurator.py).

Only creates the table, never alters or fills one, so it cannot change any product, price or BOQ
line: a typology with no rows keeps the Configurator's automatic behaviour until someone saves a
choice in NPD. Safe to re-run: a table already there is left alone. (The app also creates missing
tables when it starts; this script exists so the change can be made, and seen, on purpose - and
dry-run first - before a deploy.)

    python scripts/migrations/migrate_configurator_fields.py --dry-run
    python scripts/migrations/migrate_configurator_fields.py
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

from sqlalchemy import inspect

from app import models
from app.database import engine

TABLES = [models.ConfiguratorField]
# Columns added after the table was first created. The app creates a missing table when it starts,
# so a running server can make the table before this script runs - from an older models.py, without
# a column added since (that happened locally on 21 Oct: `label` was missing). Each is added here
# when it is not there; all are nullable, so existing rows are untouched.
LATER_COLUMNS = {"configurator_fields": [("label", "VARCHAR")]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    args = ap.parse_args()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            for model in TABLES:
                name = model.__tablename__
                if inspect(conn).has_table(name):
                    print("  %s already there" % name)
                    have = {c["name"] for c in inspect(conn).get_columns(name)}
                    for column, sql_type in LATER_COLUMNS.get(name, []):
                        if column in have:
                            print("    %s.%s already there" % (name, column))
                        else:
                            conn.exec_driver_sql("ALTER TABLE %s ADD COLUMN %s %s NULL" % (name, column, sql_type))
                            print("    added %s.%s" % (name, column))
                else:
                    model.__table__.create(bind=conn)
                    print("  created %s" % name)
        except Exception:
            trans.rollback()
            raise
        if args.dry_run:
            trans.rollback()
            print("--dry-run: rolled back - nothing was written.")
        else:
            trans.commit()
            print("committed.")


if __name__ == "__main__":
    main()
