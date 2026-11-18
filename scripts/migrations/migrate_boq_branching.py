"""One-off migration: adds boqs.parent_boq_id (a self-referential link from a branch BOQ
back to the original it was branched from) and boqs.branch_name (blank for an original BOQ,
user-chosen for a branch). See the BOQ branching feature - a branch is a full independent
copy, never merged back, same one-way-fork semantics as a GitHub branch nobody merges.
Safe to re-run - checks before acting, same pattern as migrate_parent_item.py.
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
from sqlalchemy import inspect, text

from app.database import engine


def main():
    inspector = inspect(engine)
    if "boqs" not in set(inspector.get_table_names()):
        print("table 'boqs' doesn't exist yet - nothing to migrate.")
        return
    existing_cols = {c["name"] for c in inspector.get_columns("boqs")}
    with engine.begin() as conn:
        if "parent_boq_id" not in existing_cols:
            conn.execute(text(
                "ALTER TABLE boqs ADD COLUMN parent_boq_id INTEGER "
                "REFERENCES boqs(id) ON DELETE SET NULL"
            ))
            print("added boqs.parent_boq_id")
        else:
            print("boqs.parent_boq_id already exists")
        if "branch_name" not in existing_cols:
            conn.execute(text("ALTER TABLE boqs ADD COLUMN branch_name VARCHAR DEFAULT ''"))
            print("added boqs.branch_name")
        else:
            print("boqs.branch_name already exists")


if __name__ == "__main__":
    main()
