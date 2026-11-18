"""Adds ConfigRule.specsheet_id - the optional per-product scope for a configuration rule.

Existing rules are all typology-wide and stay that way (the column is nullable and every
current row keeps NULL), so nothing changes for them. See ConfigRule's docstring in
app/models.py for why a per-product scope became necessary.

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
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("config_rules")}
    if "specsheet_id" in cols:
        print("config_rules.specsheet_id already exists - nothing to do.")
        return

    is_sqlite = engine.dialect.name == "sqlite"
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE config_rules ADD COLUMN specsheet_id INTEGER"))
        # SQLite cannot add a FK to an existing table; the column alone is what the app
        # reads, and Postgres gets the real constraint plus an index.
        if not is_sqlite:
            conn.execute(text(
                "ALTER TABLE config_rules ADD CONSTRAINT config_rules_specsheet_id_fkey "
                "FOREIGN KEY (specsheet_id) REFERENCES specsheets(id)"
            ))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_config_rules_specsheet_id ON config_rules (specsheet_id)"))
    print(f"Added config_rules.specsheet_id ({engine.dialect.name}).")


if __name__ == "__main__":
    main()
