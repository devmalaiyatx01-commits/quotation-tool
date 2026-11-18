"""Add boqs.created_by_id: who made each BOQ, so the list can be filtered to one person's work.

A new column on a table that already exists, so create_all() will not add it - this does.

Then it fills in the maker ONLY where the activity log says so for certain: the person who
created, duplicated, branched, or turned a quick BOQ into this one. The log began on
2026-09-11, so older BOQs have no such event and are left blank ("not recorded"). They are
deliberately NOT guessed from whoever edited them first - that would credit an admin who
only fixed a typo. Anyone can claim a blank one as theirs in the BOQ's details, and an admin
can set it to anyone.

Safe to run more than once: the column is only added if missing, and only blank makers are
filled.

    python scripts/migrations/migrate_boq_created_by.py --dry-run
    python scripts/migrations/migrate_boq_created_by.py
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
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import inspect, text

from app.database import engine

CREATION_EVENTS = ("boq.create", "boq.duplicate", "boq.branch", "boq.to_detailed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    have = {c["name"] for c in inspect(engine).get_columns("boqs")}
    add_sql = ["ALTER TABLE boqs ADD COLUMN created_by_id INTEGER",
               "CREATE INDEX IF NOT EXISTS ix_boqs_created_by_id ON boqs (created_by_id)"]
    if "created_by_id" in have:
        print("boqs.created_by_id already there.")
    elif args.dry_run:
        print("--dry-run: would run")
        for s in add_sql:
            print("   " + s)
    else:
        with engine.begin() as c:
            for s in add_sql:
                c.execute(text(s))
        print("added boqs.created_by_id")

    # The earliest creation event per BOQ, by a person whose account still exists.
    find = text("""
        SELECT b.id, b.project_name, b.branch_name, a.user_id, u.email
        FROM boqs b
        JOIN LATERAL (
            SELECT l.user_id FROM activity_log l
            WHERE l.boq_id = b.id AND l.action IN :events AND l.user_id IS NOT NULL
            ORDER BY l.at ASC LIMIT 1
        ) a ON TRUE
        JOIN users u ON u.id = a.user_id
        %s
        ORDER BY b.id""" % ("WHERE b.created_by_id IS NULL" if "created_by_id" in have else ""))
    from sqlalchemy import bindparam
    find = find.bindparams(bindparam("events", expanding=True))
    with engine.connect() as c:
        rows = c.execute(find, {"events": list(CREATION_EVENTS)}).fetchall()
        total = c.execute(text("SELECT COUNT(*) FROM boqs")).scalar()

    for r in rows:
        name = r.project_name + (" / " + r.branch_name if r.branch_name else "")
        print("   BOQ %-5d %-45s made by %s" % (r.id, name[:45], r.email))
    print("%d of %d BOQ(s) have a recorded maker to fill in; the rest stay 'not recorded'."
          % (len(rows), total))

    if args.dry_run:
        print("--dry-run: nothing written.")
        return
    with engine.begin() as c:
        filled = 0
        for r in rows:
            filled += c.execute(text("UPDATE boqs SET created_by_id = :u WHERE id = :b "
                                     "AND created_by_id IS NULL"), {"u": r.user_id, "b": r.id}).rowcount
    print("filled %d." % filled)


if __name__ == "__main__":
    main()
