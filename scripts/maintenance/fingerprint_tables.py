"""A fingerprint of every table - row count + md5 of its rows - to prove a change left data alone.

    python scripts/maintenance/fingerprint_tables.py > before.txt          # local database
    ... make the change ...
    python scripts/maintenance/fingerprint_tables.py > after.txt
    python scripts/maintenance/fingerprint_tables.py --compare before.txt after.txt

On the VM, without the app's environment or .env (as the owner's deploys have done since
2026-09-24): print the SQL and give it to psql as the postgres user, on stdin -

    python3 scripts/maintenance/fingerprint_tables.py --sql | sudo -u postgres psql -d quotation_tool -At -F ' ' > ~/fingerprint_before_<date>.txt

Why: a code deploy must not change people's working data (prices, parameter values, specsheets,
BOQs). "It shouldn't" is not evidence; two identical fingerprints are. A table that changed
between the two files is listed with its row counts, so a difference is explained (people kept
working during the deploy: activity_log, boqs) or investigated - never waved through.

Read-only: the local run uses a READ ONLY transaction; the SQL is SELECTs only.
"""
# --- repo-root shim (see scripts/README.md) ------------------------------------------
import pathlib as _pathlib, sys as _sys
_REPO_ROOT = next((p for p in _pathlib.Path(__file__).resolve().parents
                   if (p / "app").is_dir()), None)
if _REPO_ROOT is None:
    raise SystemExit("Cannot find the repo root: no parent directory contains app/.")
_sys.path.insert(0, str(_REPO_ROOT))
# --- end shim ------------------------------------------------------------------------
import argparse

# One SELECT per table, generated in the database and run by psql's \gexec. Rows are ordered by
# their text so the md5 does not depend on physical row order (an UPDATE moves a row on disk).
TABLES_SQL = ("SELECT table_name FROM information_schema.tables "
              "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name")
ONE_TABLE = ("SELECT '{t}', count(*), md5(coalesce(string_agg(x::text, E'\\n' ORDER BY x::text), '')) "
             "FROM public.\"{t}\" x")
PSQL = ("SELECT format('SELECT %L, count(*), md5(coalesce(string_agg(x::text, E''\\n'' ORDER BY x::text), '''')) "
        "FROM public.%I x', table_name, table_name) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name \\gexec\n")


def read(path):
    out = {}
    for line in _pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1].isdigit():
            out[parts[0]] = (int(parts[1]), parts[2])
    if not out:
        raise SystemExit("%s holds no fingerprint lines (table rows md5)." % path)
    return out


def compare(a_path, b_path):
    a, b = read(a_path), read(b_path)
    same = changed = 0
    for t in sorted(set(a) | set(b)):
        if t not in a or t not in b:
            print("%-34s only in %s" % (t, b_path if t not in a else a_path))
            changed += 1
        elif a[t] != b[t]:
            print("%-34s CHANGED  rows %d -> %d (%+d)" % (t, a[t][0], b[t][0], b[t][0] - a[t][0]))
            changed += 1
        else:
            same += 1
    print("\n%d tables identical, %d changed." % (same, changed))
    return 1 if changed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sql", action="store_true", help="print the psql script instead of running it")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="diff two fingerprint files")
    args = ap.parse_args()
    if args.sql:
        _sys.stdout.write(PSQL)
        return 0
    if args.compare:
        return compare(*args.compare)

    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SET TRANSACTION READ ONLY"))
        for (t,) in db.execute(text(TABLES_SQL)).all():
            name, rows, digest = db.execute(text(ONE_TABLE.format(t=t.replace('"', '""')))).one()
            print("%s %d %s" % (name, rows, digest))
    finally:
        db.rollback()
        db.close()
    return 0


if __name__ == "__main__":
    _sys.exit(main())
