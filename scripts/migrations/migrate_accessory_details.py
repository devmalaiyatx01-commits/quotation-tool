"""Add the table that holds an accessory's own parameters and picture.

    accessory_details (key PK, name, params JSON, image, updated_at)

A new table, so the app's create_all() at start-up would make it too. This makes it explicitly,
before a restart, and says what it did, which is how every schema change here is shipped. It
reads and writes no other table: nothing already stored changes, and no price is touched.
See models.AccessoryDetail for what the table is for.

Safe to run more than once: the table is only created if it is not already there.

    python scripts/migrations/migrate_accessory_details.py --dry-run
    python scripts/migrations/migrate_accessory_details.py
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

from sqlalchemy import inspect
from sqlalchemy.schema import CreateTable

from app import models
from app.database import engine

TABLE = models.AccessoryDetail.__table__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if inspect(engine).has_table(TABLE.name):
        print("%s already there - nothing to do." % TABLE.name)
        return
    if args.dry_run:
        print("--dry-run: would run\n" + str(CreateTable(TABLE).compile(engine)).strip())
        return
    TABLE.create(bind=engine)
    print("created %s (%s)" % (TABLE.name, ", ".join(c.name for c in TABLE.columns)))


if __name__ == "__main__":
    main()
