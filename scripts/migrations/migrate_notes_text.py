"""One-off migration: adds BOQ.notes_text in place, without touching any
existing BOQs/floors/rooms/items. Safe to re-run - checks before acting, same
pattern as migrate_kind_attention.py (see TUTORIAL.md section 6.1).
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

DB_PATH = _REPO_ROOT / "data" / "app.db"


def main():
    if not DB_PATH.exists():
        print(f"No database at {DB_PATH} - nothing to migrate.")
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(boqs)")]
    if "notes_text" not in cols:
        cur.execute("ALTER TABLE boqs ADD COLUMN notes_text TEXT DEFAULT ''")
        print("added boqs.notes_text")
    else:
        print("boqs.notes_text already exists")

    con.commit()
    con.close()


if __name__ == "__main__":
    main()
