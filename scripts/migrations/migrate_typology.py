"""One-off migration: adds Specsheet.typology and renames ConfigRule.category to
ConfigRule.typology, in place - without touching existing BOQs/floors/rooms/items,
which reference specsheets by ID and are otherwise untouched by this change.

This repo has no migration framework (see TUTORIAL.md section 6.1) - for a schema
change this specific (new column + rename, on a database that already has real
data worth keeping), a small one-off script like this is the pragmatic middle
ground between "wipe and reseed" and installing Alembic for a single change.
Safe to re-run - every step checks before acting.
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
import sqlite3
from pathlib import Path

from seed import TYPOLOGY_BY_PRODUCT_NAME

DB_PATH = _REPO_ROOT / "data" / "app.db"

# old `category` value -> new `typology` value, for rows already seeded under the
# old (incorrect) category-as-typology scheme
CATEGORY_TO_TYPOLOGY = {
    "LED Spotlight –Adjustable": "Spotlight",
    "Surface LED Grazer –Fixed / Adjustable": "Wall Grazer / Wall Washer",
    "Flexible COB Strip (Standard)": "Flexible Strip Light",
    "Flexible LED Strip (Standard)": "Flexible Strip Light",
    "Flexible Homogeneous LED Band ( Top Bend )": "Flexible Neon / LED Band",
    "Recessed LED Downlight –Adjustable –With Trim": "Downlight",
    "Track Spot –48V DC": "Track Light",
}


def main():
    if not DB_PATH.exists():
        print(f"No database at {DB_PATH} - nothing to migrate (a fresh seed.py run will do this correctly).")
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    specsheet_cols = [r[1] for r in cur.execute("PRAGMA table_info(specsheets)")]
    if "typology" not in specsheet_cols:
        cur.execute("ALTER TABLE specsheets ADD COLUMN typology VARCHAR DEFAULT ''")
        print("added specsheets.typology")
    else:
        print("specsheets.typology already exists")

    for product_name, typology in TYPOLOGY_BY_PRODUCT_NAME.items():
        cur.execute("UPDATE specsheets SET typology = ? WHERE product_name = ? AND (typology IS NULL OR typology = '')",
                    (typology, product_name))
    print("assigned typology to known sample specsheets (left any others untouched)")

    rule_cols = [r[1] for r in cur.execute("PRAGMA table_info(config_rules)")]
    if "category" in rule_cols and "typology" not in rule_cols:
        cur.execute("ALTER TABLE config_rules RENAME COLUMN category TO typology")
        print("renamed config_rules.category -> typology")
    elif "typology" in rule_cols:
        print("config_rules.typology already exists")

    for old_category, typology in CATEGORY_TO_TYPOLOGY.items():
        cur.execute("UPDATE config_rules SET typology = ? WHERE typology = ?", (typology, old_category))
    print("remapped any existing rules from old category values to typology values")

    con.commit()

    print("\nCurrent specsheets:")
    for row in cur.execute("SELECT id, product_name, category, typology FROM specsheets ORDER BY id"):
        print(" ", row)
    print("\nCurrent config_rules:")
    for row in cur.execute("SELECT id, typology, action FROM config_rules ORDER BY id"):
        print(" ", row)

    con.close()


if __name__ == "__main__":
    main()
