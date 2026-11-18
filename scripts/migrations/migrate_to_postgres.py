"""One-off: copies every row from the existing SQLite database (data/app.db) into a
Postgres database, preserving primary key IDs exactly - BOQItem.specsheet_id,
BOQFloor.boq_id, BOQRoom.floor_id, etc. all reference those IDs directly, so a copy that
renumbered them would silently break every foreign key in the copied data. Run this once,
at the point you actually switch DATABASE_URL over to Postgres - see deployment.md
section 3.2 for when that's worth doing.

This migrates the DATABASE only. Uploaded PDFs/diagram images/exports live on disk under
data/ (uploads/, images/, exports/) as file paths stored in the database, not as database
BLOBs - if Postgres is running on the same machine, those paths still resolve as-is after
this script runs; if you're also moving to a different machine, copy the whole data/
directory there too, or every specsheet's "view PDF"/diagram thumbnail will 404 even
though its database row copied fine.

Safe to re-run: a table that already has rows on the Postgres side is skipped rather than
duplicated, so an interrupted run can just be run again.

Usage (Postgres must already exist and be reachable - this script only copies data, it
doesn't install or start a database server):
    Windows (PowerShell):  $env:DATABASE_URL = "postgresql+psycopg2://user:pass@host/dbname"; python scripts/migrations/migrate_to_postgres.py
    Linux/Mac:              DATABASE_URL=postgresql+psycopg2://user:pass@host/dbname python scripts/migrations/migrate_to_postgres.py
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
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base, DB_PATH
from app import models

# Parent tables before the children that foreign-key into them, so every row a foreign key
# points at is already there by the time the referencing row is inserted.
TABLES_IN_ORDER = [
    models.Specsheet, models.User, models.ConfigRule, models.FieldCode,
    models.BOQ, models.BOQFloor, models.BOQRoom, models.BOQItem, models.BOQExport,
]


def main():
    target_url = os.environ.get("DATABASE_URL")
    if not target_url or not target_url.startswith("postgresql"):
        print("Set DATABASE_URL to the target Postgres DSN first, e.g.:")
        print('  PowerShell:  $env:DATABASE_URL = "postgresql+psycopg2://user:pass@host/dbname"')
        print('  bash:        export DATABASE_URL=postgresql+psycopg2://user:pass@host/dbname')
        sys.exit(1)
    if not DB_PATH.exists():
        print(f"No source SQLite database at {DB_PATH} - nothing to migrate.")
        sys.exit(1)

    source_engine = create_engine(f"sqlite:///{DB_PATH}")
    target_engine = create_engine(target_url)
    SourceSession = sessionmaker(bind=source_engine)
    TargetSession = sessionmaker(bind=target_engine)

    Base.metadata.create_all(bind=target_engine)  # same schema create_all() gives a brand-new database

    src = SourceSession()
    dst = TargetSession()
    is_postgres = dst.bind.dialect.name == "postgresql"
    try:
        for model in TABLES_IN_ORDER:
            table_name = model.__tablename__
            existing = dst.query(model).count()
            if existing:
                print(f"skip {table_name}: target already has {existing} row(s) - not overwriting")
                continue
            rows = src.query(model).all()
            columns = [c.name for c in model.__table__.columns]
            for row in rows:
                dst.add(model(**{c: getattr(row, c) for c in columns}))
            dst.commit()
            print(f"copied {len(rows)} row(s) into {table_name}")

            if is_postgres:
                # Postgres' own auto-increment sequence doesn't know about the explicit ids
                # we just inserted - without this, the next insert with no id given would
                # try id=1 again and collide with a row copied above.
                dst.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table_name}), 1))"
                ))
                dst.commit()
    finally:
        src.close()
        dst.close()

    print("Done. Point the app at Postgres by setting DATABASE_URL in its own environment and restarting it.")


if __name__ == "__main__":
    main()
