"""Adds BOQItem.config - the per-item record of every configurable axis that has no column
of its own.

Existing rows are unaffected: the column is nullable and every current item keeps NULL,
which reads back as "nothing configured beyond finish/wattage/beam/control/CCT", exactly
what those rows mean today. See BOQItem.config's docstring in app/models.py for why a
JSON column rather than a column per axis.

Safe to re-run: checks for the column first.
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

from app.database import engine


def main():
    cols = {c["name"] for c in inspect(engine).get_columns("boq_items")}
    if "config" in cols:
        print("boq_items.config already exists - nothing to do.")
        return
    # SQLite has no JSON column type of its own; SQLAlchemy's JSON stores text there and
    # serialises on the way in either way, so TEXT is the portable declaration.
    coltype = "JSON" if engine.dialect.name != "sqlite" else "TEXT"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE boq_items ADD COLUMN config {coltype}"))
    print(f"Added boq_items.config ({engine.dialect.name}, {coltype}).")


if __name__ == "__main__":
    main()
