"""Creates vendor_code_structures and vendor_product_naming - the naming structures a person sets up
for a manufacturer's order codes, and which structure a product is coded by (27 Oct 2026).

DuraGreen names its products by its own code tables (ACME, MOOD ...), already in vendor_code_rules.
A product no catalogue page names - a new one DuraGreen has sent, or one only its spec sheet shows -
can now be put in one of those, or in a structure a person makes. See the two classes in
app/models.py for what each row means.

Only creates tables, never alters or fills an existing one, so it cannot change any product's code,
price or BOQ line. Safe to re-run: a table already there is left alone. (The app also creates
missing tables when it starts; this script exists so the change can be made, and seen, on purpose -
and dry-run first - before a deploy.)

    python scripts/migrations/migrate_vendor_naming.py --dry-run
    python scripts/migrations/migrate_vendor_naming.py
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

TABLES = [models.VendorCodeStructure, models.VendorProductNaming]


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
            print("done.")


if __name__ == "__main__":
    main()
