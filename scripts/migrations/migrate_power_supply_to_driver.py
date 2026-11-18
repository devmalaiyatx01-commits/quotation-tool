"""Take the power supply off the fixtures that carried it, now that it is priced as a driver.

A luminaire that listed "Power supply" as an accessory, priced per control, cost more when
DALI was chosen than when On/Off was - SOLARA Midi was Rs N dearer. That is not what the
price list says. The control a downlight is dimmed by does not change what the downlight
costs; it changes which remote driver is bought alongside it, and a driver is its own quoted
line. Those figures live in the driver_prices table now (see seed_driver_prices.py).

Refuses to delete a price it cannot find among the driver rates. That is the whole safety of
this script: it will not take a figure off a product unless the same figure is already
quotable from the driver form, so running it before the driver rates are seeded removes
nothing and says so.

Price rows that belong to no product are left alone - they were never on a fixture, so there
is nothing to take off one.

    python scripts/migrations/migrate_power_supply_to_driver.py --dry-run
    python scripts/migrations/migrate_power_supply_to_driver.py
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
import argparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal, engine
from app import models


def norm(text):
    return "".join(c for c in str(text or "").upper() if c.isalnum())


def is_power_supply(entry):
    return norm((entry.config or {}).get("accessory")) == "POWERSUPPLY"


def driver_rates(db):
    """Every price the driver form can quote, so nothing is deleted that it cannot."""
    from sqlalchemy import inspect
    if "driver_prices" not in inspect(engine).get_table_names():
        return set()
    return {float(r.price) for r in db.query(models.DriverPrice).all() if r.price is not None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    quotable = driver_rates(db)
    print("prices the driver form can quote: %d" % len(quotable))
    if not quotable:
        print()
        print("No driver rates found - run seed_driver_prices.py first. Nothing removed.")
        db.close()
        return

    rows = [p for p in db.query(models.PriceEntry).all()
            if is_power_supply(p) and p.specsheet_id]
    print("power supplies priced on a fixture: %d" % len(rows))
    print()

    by_sheet = {}
    for p in rows:
        by_sheet.setdefault(p.specsheet_id, []).append(p)

    removed = kept = 0
    for sheet_id, entries in by_sheet.items():
        sheet = db.query(models.Specsheet).filter(models.Specsheet.id == sheet_id).first()
        name = (sheet.product_name if sheet else "?") or "?"
        unquotable = [p for p in entries if float(p.price) not in quotable]
        if unquotable:
            print("   %-22s LEFT ALONE - %s not among the driver rates"
                  % (name[:22], [p.price for p in unquotable]))
            kept += len(entries)
            continue
        for p in entries:
            print("   %-22s remove %-46s Rs %s"
                  % (name[:22], str(p.config), p.price))
            if not args.dry_run:
                db.delete(p)
            removed += 1
        # "Power supply" was only ever in the accessory list because those price rows were
        # read as accessories; with them gone it is not one.
        if sheet is not None:
            options = dict(sheet.options or {})
            names = [a for a in options.get("accessories") or [] if norm(a) != "POWERSUPPLY"]
            if names != (options.get("accessories") or []):
                print("   %-22s accessory list -> %s" % (name[:22], names or "(none)"))
                if not args.dry_run:
                    if names:
                        options["accessories"] = names
                    else:
                        options.pop("accessories", None)
                    sheet.options = options

    print()
    print("price rows removed: %d" % removed)
    if kept:
        print("left in place because the driver form cannot quote them: %d" % kept)

    if args.dry_run:
        db.rollback()
        print()
        print("--dry-run: nothing written.")
    else:
        db.commit()
        print()
        print("done. Those fixtures are now one price whatever the control.")
    db.close()


if __name__ == "__main__":
    main()
