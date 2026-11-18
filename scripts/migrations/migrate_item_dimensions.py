"""Adds BOQItem.dimensions and BOQItem.show_line_diagram - the export's Dimension column.

`dimensions` is free text ("Ø62 mm x 70 mm"), typed by hand. `show_line_diagram` draws the
manufacturer's dimensioned line drawing in that cell instead, which today only InHouse
publishes in a form this app holds (Specsheet.diagrams, label "Additional Diagram 2" - all 140
InHouse specsheets have one, and nothing else in the library does).

Existing rows get "" and false: no dimension text, no drawing - exactly what every BOQ built
before this column existed actually means. Nothing is inferred for them, because a measurement
nobody entered is not a measurement.

Safe to re-run: checks for each column first.
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

# Postgres and SQLite spell the boolean default differently, and this repo runs both (Oracle
# serves Postgres; a local checkout can be either).
BOOL_DEFAULT = {"postgresql": "BOOLEAN DEFAULT FALSE", "sqlite": "BOOLEAN DEFAULT 0"}


def main():
    dialect = engine.dialect.name
    cols = {c["name"] for c in inspect(engine).get_columns("boq_items")}
    todo = []
    if "dimensions" not in cols:
        todo.append(("dimensions", "VARCHAR DEFAULT ''"))
    if "show_line_diagram" not in cols:
        todo.append(("show_line_diagram", BOOL_DEFAULT.get(dialect, "BOOLEAN DEFAULT FALSE")))

    if not todo:
        print("boq_items.dimensions and .show_line_diagram already exist - nothing to do.")
        return

    with engine.begin() as conn:
        for name, ddl in todo:
            conn.execute(text(f"ALTER TABLE boq_items ADD COLUMN {name} {ddl}"))
            print(f"  added boq_items.{name}")

    # An ALTER with a DEFAULT fills existing rows on Postgres but the column is still nullable,
    # and NULL is not false to Python's `if item.show_line_diagram`. Normalising now means the
    # exporters never have to care which of the two a row predates.
    with engine.begin() as conn:
        conn.execute(text("UPDATE boq_items SET dimensions = '' WHERE dimensions IS NULL"))
        conn.execute(text("UPDATE boq_items SET show_line_diagram = FALSE WHERE show_line_diagram IS NULL"))
    print(f"Done ({dialect}).")


if __name__ == "__main__":
    main()
