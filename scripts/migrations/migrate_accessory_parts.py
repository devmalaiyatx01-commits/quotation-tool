"""The InHouse accessory library: its two tables, and the price column that points at it.

    accessory_parts   (id, discipline, make, name, code, params, image, created_at, updated_at)
    accessory_links   (id, specsheet_id, part_id, sort_order, created_at, updated_at)
                      unique (specsheet_id, part_id)
    price_entries.accessory_part_id   (nullable, -> accessory_parts.id, indexed)

On a database that has never had them (the live one), this creates them and nothing else: the
library starts empty, and people fill it from the NPD product form. No product, price or BOQ line
changes.

On a database that ran this script's first version (local copies, 2026-10-05), it also brings
`accessory_links` to its final shape. That version linked every InHouse accessory to a SUGGESTED
part (one per name, `confirmed` false). Suggestions were dropped from the design - picking an
accessory from the library is the decision - so:
  * unconfirmed links are deleted (a suggestion carries no person's decision);
  * a confirmed link, if any, is kept;
  * the `accessory_key` and `confirmed` columns go, `sort_order` comes, and the unique key becomes
    (specsheet_id, part_id);
  * an accessory record left with nothing at all - no product, specification, picture, code,
    price or BOQ line - is deleted. Anything a person entered (the U-shape profile's 35x35 and its
    picture) is kept.

Safe to run more than once, and all in one transaction (PostgreSQL rolls back table changes too),
so --dry-run does everything, reports, and rolls it back.

    python scripts/migrations/migrate_accessory_parts.py --dry-run
    python scripts/migrations/migrate_accessory_parts.py

Run it BEFORE restarting the app on new code: the app reads these columns, and its start-up
create_all() makes missing tables but never adds or drops a column.
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

from app import models
from app.database import engine


def columns(conn, table):
    return {c["name"] for c in inspect(conn).get_columns(table)}


def ensure_parts(conn):
    if inspect(conn).has_table("accessory_parts"):
        print("  accessory_parts already there")
    else:
        models.AccessoryPart.__table__.create(bind=conn)
        print("  created accessory_parts")


def ensure_links(conn):
    if not inspect(conn).has_table("accessory_links"):
        models.AccessoryLink.__table__.create(bind=conn)
        print("  created accessory_links")
        return
    cols = columns(conn, "accessory_links")
    if "confirmed" in cols:
        suggested = conn.execute(text("SELECT count(*) FROM accessory_links WHERE NOT confirmed")).scalar()
        kept = conn.execute(text("SELECT count(*) FROM accessory_links WHERE confirmed")).scalar()
        conn.execute(text("DELETE FROM accessory_links WHERE NOT confirmed"))
        print("  accessory_links: deleted %d suggested (unconfirmed) link(s), kept %d confirmed" % (suggested, kept))
        conn.execute(text("ALTER TABLE accessory_links DROP COLUMN confirmed"))
        print("  accessory_links: dropped column confirmed")
    if "accessory_key" in cols:
        conn.execute(text("ALTER TABLE accessory_links DROP CONSTRAINT IF EXISTS uq_accessory_link"))
        conn.execute(text("ALTER TABLE accessory_links DROP COLUMN accessory_key"))
        print("  accessory_links: dropped column accessory_key (and its unique key)")
    if "sort_order" not in columns(conn, "accessory_links"):
        conn.execute(text("ALTER TABLE accessory_links ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"))
        print("  accessory_links: added sort_order")
    names = {u["name"] for u in inspect(conn).get_unique_constraints("accessory_links")}
    if "uq_accessory_link_part" not in names:
        conn.execute(text("ALTER TABLE accessory_links ADD CONSTRAINT uq_accessory_link_part "
                          "UNIQUE (specsheet_id, part_id)"))
        print("  accessory_links: unique (specsheet_id, part_id)")
    else:
        print("  accessory_links already in its final shape")


def ensure_price_column(conn):
    if "accessory_part_id" in columns(conn, "price_entries"):
        print("  price_entries.accessory_part_id already there")
        return
    conn.execute(text("ALTER TABLE price_entries ADD COLUMN accessory_part_id INTEGER "
                      "REFERENCES accessory_parts(id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_price_entries_accessory_part_id "
                      "ON price_entries (accessory_part_id)"))
    print("  added price_entries.accessory_part_id (+ index)")


def drop_empty_parts(conn):
    """Accessory records nobody put anything into - left by the first version's suggestions."""
    rows = conn.execute(text("""
        SELECT p.id, p.name FROM accessory_parts p
        WHERE NOT EXISTS (SELECT 1 FROM accessory_links l WHERE l.part_id = p.id)
          AND NOT EXISTS (SELECT 1 FROM price_entries e WHERE e.accessory_part_id = p.id)
          AND coalesce(p.image, '') = '' AND coalesce(p.code, '') = ''
          AND (p.params IS NULL OR p.params::text IN ('[]', 'null'))
        ORDER BY p.id""")).fetchall()
    used = {r[0] for r in conn.execute(text(
        "SELECT (config->>'accessory_part')::int FROM boq_items "
        "WHERE config->>'accessory_part' IS NOT NULL")).fetchall()}
    empty = [(i, n) for i, n in rows if i not in used]
    for i, _n in empty:
        conn.execute(text("DELETE FROM accessory_parts WHERE id = :i"), {"i": i})
    print("  deleted %d accessory record(s) that held nothing%s" % (
        len(empty), (": " + ", ".join(n for _i, n in empty)) if empty else ""))
    for i, n, params, image in conn.execute(text(
            "SELECT id, name, params, image FROM accessory_parts ORDER BY id")).fetchall():
        print("  kept: #%d %s  specs=%s  picture=%s" % (i, n, params, image or "-"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            ensure_parts(conn)
            ensure_links(conn)
            ensure_price_column(conn)
            drop_empty_parts(conn)
            links = conn.execute(text("SELECT count(*) FROM accessory_links")).scalar()
            prices = conn.execute(text("SELECT count(*) FROM price_entries")).scalar()
            print("  now: %d library accessory(ies), %d product link(s), %d price row(s) in all" % (
                conn.execute(text("SELECT count(*) FROM accessory_parts")).scalar(), links, prices))
        except Exception:
            trans.rollback()
            raise
        if args.dry_run:
            trans.rollback()
            print("--dry-run: all of the above rolled back - nothing was written.")
        else:
            trans.commit()
            print("committed.")


if __name__ == "__main__":
    main()
