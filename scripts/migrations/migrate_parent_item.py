"""One-off migration: adds boq_items.parent_item_id, a self-referential link from an
accessory row to the product row it's associated with (see the multi-accessory feature -
accessories are always inserted directly after their parent product, and deleting a
product cascades to delete its accessory rows via this column). Safe to re-run - checks
before acting, same pattern as migrate_reference_number.py.
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
    if "parent_item_id" in existing_cols:
        print("boq_items.parent_item_id already exists")
        return
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE boq_items ADD COLUMN parent_item_id INTEGER "
            "REFERENCES boq_items(id) ON DELETE SET NULL"
        ))
    print("added boq_items.parent_item_id")


if __name__ == "__main__":
    main()
