"""Adds the Architectural/Decorative split: Specsheet.discipline, BOQ.discipline,
User.disciplines.

Everything that exists today is architectural - that is the only tool there has ever been - so
existing products and BOQs are stamped "architectural" explicitly rather than left NULL. A NULL
here would mean "belongs to no tool", which is not true of any current row and would make every
catalogue query need a NULL branch forever.

Existing accounts get ["architectural"] for the same reason: they are the architectural team,
and an empty list would lock them out of the tool they use. Decorative access is granted
deliberately, per account (see grant_discipline.py).

Safe to re-run: checks for each column first, and only stamps rows that have no value yet.
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

DEFAULT = "architectural"


def main():
    dialect = engine.dialect.name
    insp = inspect(engine)
    added = []

    def cols(table):
        return {c["name"] for c in insp.get_columns(table)}

    with engine.begin() as conn:
        if "discipline" not in cols("specsheets"):
            conn.execute(text(f"ALTER TABLE specsheets ADD COLUMN discipline VARCHAR DEFAULT '{DEFAULT}'"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_specsheets_discipline ON specsheets (discipline)"))
            added.append("specsheets.discipline")
        if "discipline" not in cols("boqs"):
            conn.execute(text(f"ALTER TABLE boqs ADD COLUMN discipline VARCHAR DEFAULT '{DEFAULT}'"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_boqs_discipline ON boqs (discipline)"))
            added.append("boqs.discipline")
        if "discipline" not in cols("price_entries"):
            conn.execute(text(f"ALTER TABLE price_entries ADD COLUMN discipline VARCHAR DEFAULT '{DEFAULT}'"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_entries_discipline ON price_entries (discipline)"))
            added.append("price_entries.discipline")
        if "disciplines" not in cols("users"):
            # JSON on Postgres, TEXT on SQLite - SQLAlchemy's JSON type reads both back as a list.
            json_type = "JSON" if dialect == "postgresql" else "TEXT"
            conn.execute(text(f"ALTER TABLE users ADD COLUMN disciplines {json_type}"))
            added.append("users.disciplines")

    # Stamp existing rows. Separate from the ALTERs so a re-run still repairs anything that
    # was inserted while the column existed but had no value.
    with engine.begin() as conn:
        n_s = conn.execute(text(
            f"UPDATE specsheets SET discipline = '{DEFAULT}' WHERE discipline IS NULL OR discipline = ''"
        )).rowcount
        n_b = conn.execute(text(
            f"UPDATE boqs SET discipline = '{DEFAULT}' WHERE discipline IS NULL OR discipline = ''"
        )).rowcount
        n_p = conn.execute(text(
            f"UPDATE price_entries SET discipline = '{DEFAULT}' WHERE discipline IS NULL OR discipline = ''"
        )).rowcount
        if dialect == "postgresql":
            n_u = conn.execute(text(
                """UPDATE users SET disciplines = '["architectural"]'::json
                   WHERE disciplines IS NULL OR disciplines::text IN ('null', '[]')"""
            )).rowcount
        else:
            n_u = conn.execute(text(
                """UPDATE users SET disciplines = '["architectural"]'
                   WHERE disciplines IS NULL OR disciplines IN ('null', '[]')"""
            )).rowcount

    print(f"  columns added: {', '.join(added) if added else 'none (already present)'}")
    print(f"  stamped architectural: {n_s} specsheets, {n_b} BOQs, {n_p} prices, {n_u} users")
    print(f"Done ({dialect}).")


if __name__ == "__main__":
    main()
