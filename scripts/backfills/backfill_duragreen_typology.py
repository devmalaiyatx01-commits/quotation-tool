"""Give every DuraGreen product that has no typology the class a lighting designer files it under.

DuraGreen's website names a class for some categories only (Downlights, Spotlights, Track Lights,
LED Strip, Step Lights, Power Supplies), so 228 products - pendants, wall lights, projectors, garden
spikes, the "System 36/50/57/68/78" ranges, wiring accessories, demo kits - came in with none, and
NPD and the Add Product filters could not find them by kind. app/duragreen.typology_for reads each
product's own name, series and the website's category (see its rules for the reasoning).

What this does: fills `specsheets.typology` where it is EMPTY, for make Duragreen. A typology a
person or an earlier load set is never changed, even where the rules would say otherwise - those
are listed in the report as "kept" so a person can look. A product whose words say nothing (the
website's bare "Circular- Pro Lights2" rows) stays empty and is reported.

What this never does: change a price, a code, an option, a BOQ line, or delete anything.

All in one transaction. --dry-run does everything, reports, and rolls back.

    python scripts/backfills/backfill_duragreen_typology.py --dry-run
    python scripts/backfills/backfill_duragreen_typology.py
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
import csv
import json
from collections import Counter

from app import duragreen, models
from app.database import SessionLocal

MAKE = duragreen.MAKE
FOLDER = _REPO_ROOT / "Duragreen Catalogue"


def _prices(sheets):
    return {(s.id, s.base_price, json.dumps(s.price_adders or {}, sort_keys=True), s.list_price,
             json.dumps(s.list_price_by_wattage or {}, sort_keys=True)) for s in sheets}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        sheets = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).order_by(models.Specsheet.id).all()
        prices_before = _prices(sheets)
        n_price_entries = db.query(models.PriceEntry).count()
        n_boq_items = db.query(models.BOQItem).count()
        n_codes = db.query(models.VendorOrderCode).count()

        tally, set_to, report = Counter(), Counter(), []
        for s in sheets:
            proposed = duragreen.typology_for(s.series, s.product_name, s.category)
            row = {"specsheet_id": s.id, "series": s.series or "", "product": s.product_name or "",
                   "category": s.category or "", "typology_before": s.typology or "", "rules_say": proposed}
            if (s.typology or "").strip():
                if proposed and proposed != s.typology:
                    tally["kept (the rules would say otherwise - a person may look)"] += 1
                    report.append(dict(row, action="kept"))
                else:
                    tally["already classified"] += 1
                continue
            if not proposed:
                tally["left empty - its words say nothing"] += 1
                report.append(dict(row, action="left empty"))
                continue
            s.typology = proposed
            set_to[proposed] += 1
            tally["classified"] += 1
            report.append(dict(row, action="classified"))
        db.flush()

        after = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()
        assert {s.id for s in sheets} == {s.id for s in after}, "a product appeared or vanished"
        assert prices_before == _prices(after), "a price changed"
        assert n_price_entries == db.query(models.PriceEntry).count(), "a price entry changed"
        assert n_boq_items == db.query(models.BOQItem).count(), "a BOQ line changed"
        assert n_codes == db.query(models.VendorOrderCode).count(), "an order code changed"

        for what, n in sorted(tally.items()):
            print("%5d  %s" % (n, what))
        for typ, n in set_to.most_common():
            print("        %4d  -> %s" % (n, typ))
        print("checked: prices unchanged (%d products, %d price entries), BOQ lines unchanged (%d), "
              "order codes unchanged (%d)" % (len(prices_before), n_price_entries, n_boq_items, n_codes))
        tag = "_dry_run" if args.dry_run else ""
        out = FOLDER / ("typology_report%s.csv" % tag)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["specsheet_id", "action", "series", "product", "category",
                                              "typology_before", "rules_say"])
            w.writeheader()
            w.writerows(report)
        print("report:", out)
        if args.dry_run:
            db.rollback()
            print("DRY RUN - rolled back")
        else:
            db.commit()
            print("committed")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
