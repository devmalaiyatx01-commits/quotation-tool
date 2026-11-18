"""Adds Specsheet.source_url - the manufacturer's own page for one product.

Existing rows keep NULL, which reads as "no product page known", exactly what a row
imported from a PDF means today. See the column's docstring in app/models.py.

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
    cols = {c["name"] for c in inspect(engine).get_columns("specsheets")}
    if "source_url" in cols:
        print("specsheets.source_url already exists - nothing to do.")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE specsheets ADD COLUMN source_url VARCHAR"))
    print(f"Added specsheets.source_url ({engine.dialect.name}).")


if __name__ == "__main__":
    main()
