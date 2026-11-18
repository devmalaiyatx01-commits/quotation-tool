"""Add the quotation's per-line discount and its "show the discount" switch.

    boq_items.discount_percent  FLOAT, NULL   - a line's own discount; NULL follows the rule
    boqs.show_discount          BOOLEAN, TRUE - whether the exported quotation shows it

New columns on tables that already exist, so SQLAlchemy's create_all() will not add them -
this does. Nothing already stored changes: every existing line is left NULL (it follows the
quotation's rule, see app/quotation_export.line_discount) and every existing BOQ shows its
discount, as before. No price is read or written.

Safe to run more than once: each column is only added if it is not already there.

    python scripts/migrations/migrate_line_discounts.py --dry-run
    python scripts/migrations/migrate_line_discounts.py
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

# (table, column, the ALTER that adds it, the UPDATE that gives existing rows their value or None)
CHANGES = [
    ("boq_items", "discount_percent",
     "ALTER TABLE boq_items ADD COLUMN discount_percent FLOAT",
     None),                     # NULL is the right value for every existing line
    ("boqs", "show_discount",
     "ALTER TABLE boqs ADD COLUMN show_discount BOOLEAN DEFAULT TRUE",
     "UPDATE boqs SET show_discount = TRUE WHERE show_discount IS NULL"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    inspector = inspect(engine)
    for table, column, alter, fill in CHANGES:
        have = {c["name"] for c in inspector.get_columns(table)}
        if column in have:
            print("%s.%s already there - nothing to do." % (table, column))
            continue
        if args.dry_run:
            print("--dry-run: would run\n   " + alter + ("\n   " + fill if fill else ""))
            continue
        with engine.begin() as c:
            c.execute(text(alter))
        filled = 0
        if fill:
            with engine.begin() as c:
                filled = c.execute(text(fill)).rowcount
        print("added %s.%s%s" % (table, column,
                                 "; %d existing row(s) set" % filled if fill else ""))


if __name__ == "__main__":
    main()
