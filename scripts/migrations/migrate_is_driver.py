"""One-off migration: adds boq_items.is_driver - a finer-grained sub-flag of is_accessory,
purely for the app's own UI (shows "A"/"D" in the compact row's thumbnail so accessory vs
driver rows are distinguishable at a glance - never exported). Safe to re-run - checks
before acting, same pattern as migrate_parent_item.py.
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
    if "boq_items" not in set(inspector.get_table_names()):
        print("table 'boq_items' doesn't exist yet - nothing to migrate.")
        return
    existing_cols = {c["name"] for c in inspector.get_columns("boq_items")}
    if "is_driver" in existing_cols:
        print("boq_items.is_driver already exists")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE boq_items ADD COLUMN is_driver BOOLEAN DEFAULT FALSE"))
    print("added boq_items.is_driver")


if __name__ == "__main__":
    main()
