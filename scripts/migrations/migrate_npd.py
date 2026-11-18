"""Adds the New Product Development tables and columns.

Three additions, all of them additive:

  * specsheets.approval_status - "approved" for everything already in the library; a
                             product authored in NPD starts "pending" and is kept out of the
                             Specsheet Library until somebody approves it.
  * specsheets.origin      - "parsed" for everything already in the library, "npd" for a
                             product authored through the NPD tab. Existing rows were all read
                             out of a manufacturer's PDF, so they are stamped "parsed"
                             explicitly rather than left NULL; a NULL here would mean "came
                             from nowhere", which is not true of any of them.
  * specsheets.base_price  - the NPD price before any option adders. NULL for parsed rows,
    specsheets.price_adders  which are priced from PriceEntry as before. Nothing about
                             existing pricing changes.
  * parameter_templates    - which parameters describe a product of a given typology.
                             Populated by seed_parameter_templates.py, which reads the
                             library rather than inventing a list.

Safe to re-run: checks for each column and table first, and only stamps rows with no value.
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
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import inspect, text

from app.database import engine, Base
from app import models

DEFAULT_ORIGIN = "parsed"


def main():
    dialect = engine.dialect.name
    insp = inspect(engine)
    added = []

    cols = {c["name"] for c in insp.get_columns("specsheets")}
    with engine.begin() as conn:
        if "origin" not in cols:
            conn.execute(text(
                "ALTER TABLE specsheets ADD COLUMN origin VARCHAR DEFAULT '%s'" % DEFAULT_ORIGIN))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_specsheets_origin ON specsheets (origin)"))
            added.append("specsheets.origin")
        if "approval_status" not in cols:
            conn.execute(text(
                "ALTER TABLE specsheets ADD COLUMN approval_status VARCHAR DEFAULT 'approved'"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_specsheets_approval_status "
                              "ON specsheets (approval_status)"))
            added.append("specsheets.approval_status")
        if "base_price" not in cols:
            conn.execute(text("ALTER TABLE specsheets ADD COLUMN base_price FLOAT"))
            added.append("specsheets.base_price")
        if "price_adders" not in cols:
            json_type = "JSON" if dialect == "postgresql" else "TEXT"
            conn.execute(text(
                "ALTER TABLE specsheets ADD COLUMN price_adders %s" % json_type))
            added.append("specsheets.price_adders")

    # create_all only creates what is missing, and never alters an existing table
    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)
    new_tables = sorted(set(inspect(engine).get_table_names()) - before)

    with engine.begin() as conn:
        stamped = conn.execute(text(
            "UPDATE specsheets SET origin = '%s' WHERE origin IS NULL OR origin = ''"
            % DEFAULT_ORIGIN)).rowcount
        # Everything already in the library was read from a real document, so it is approved.
        approved = conn.execute(text(
            "UPDATE specsheets SET approval_status = 'approved' "
            "WHERE approval_status IS NULL OR approval_status = ''")).rowcount

    print("  columns added: %s" % (", ".join(added) if added else "none (already present)"))
    print("  tables created: %s" % (", ".join(new_tables) if new_tables else "none (already present)"))
    print("  stamped origin='%s': %d specsheets" % (DEFAULT_ORIGIN, stamped))
    print("  stamped approval_status='approved': %d specsheets" % approved)
    print("Done (%s)." % dialect)


if __name__ == "__main__":
    main()
