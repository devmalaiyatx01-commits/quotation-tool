"""Give each DuraGreen product whose sheet prints two IP ratings as one sentence the two as choices.

A sheet prints "IP20 and IP44 options" as plain text, so the Add Product panel offered that sentence as
one IP Rating and nobody could quote either. app/duragreen_sheet.ip_options is the one reading of that
wording, and new reads already use it; this brings the products already stored into line.

What this changes: `fields.ip_rating` and `options.ip_rating`, make Duragreen, only where ip_options
finds two values. What it never changes: a price, a code, a BOQ line, `params` (which keeps the sheet's
own words), or a product that already has an IP option list, which a person may have set. Those are
listed and kept.

    python scripts/backfills/backfill_duragreen_ip_options.py --dry-run
    python scripts/backfills/backfill_duragreen_ip_options.py
    python scripts/backfills/backfill_duragreen_ip_options.py --undo data/duragreen_ip_options_undo_<time>.json

Safe to re-run: a product already split reads as one value per option and is not touched. A real run
writes each product's printed text to that undo file. No product it changes had an IP list before, so
undoing puts the text back and removes the list, skipping any product whose IP was changed or priced since.
"""
import argparse
import datetime
import json
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy.orm import load_only  # noqa: E402
from sqlalchemy.orm.attributes import flag_modified  # noqa: E402

from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.duragreen import MAKE  # noqa: E402
from app.duragreen_sheet import ip_options  # noqa: E402


def restore(path, dry_run):
    db = SessionLocal()
    done, skipped, priced = 0, [], []
    for entry in json.loads(Path(path).read_text(encoding="utf-8")):
        s = db.get(models.Specsheet, entry["id"])
        values = ip_options(entry["field"])
        if s is None or (s.options or {}).get("ip_rating") != values:
            skipped.append(entry["id"])
            continue
        # With no list, the next save in NPD would delete a price set on one of its values.
        if (s.price_adders or {}).get("ip_rating"):
            priced.append(entry["id"])
            continue
        fields, options = dict(s.fields or {}), dict(s.options or {})
        fields["ip_rating"] = entry["field"]
        options.pop("ip_rating")
        s.fields, s.options = fields, options
        flag_modified(s, "fields")
        flag_modified(s, "options")
        done += 1
    print("restored: %d, skipped because changed since: %s, skipped because an IP is priced: %s" % (done, skipped, priced))
    if dry_run:
        db.rollback()
        print("--dry-run: nothing was written.")
    else:
        db.commit()
    db.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what would change, and write nothing")
    ap.add_argument("--undo", metavar="FILE", help="put back what a real run changed")
    args = ap.parse_args()
    if args.undo:
        return restore(args.undo, args.dry_run)
    db = SessionLocal()
    undo, kept, readings = [], [], Counter()
    S = models.Specsheet
    # Not raw_text or params: the live server is small, and neither is read here.
    products = (db.query(S).options(load_only(S.id, S.product_name, S.fields, S.options, S.base_price, S.price_adders))
                .filter(S.make == MAKE).order_by(S.id).all())
    prices_before = [(s.id, s.base_price, json.dumps(s.price_adders, sort_keys=True)) for s in products]
    for s in products:
        fields = dict(s.fields or {})
        printed = str(fields.get("ip_rating") or "")
        values = ip_options(printed)
        if len(values) < 2:
            continue
        options = dict(s.options or {})
        if options.get("ip_rating"):
            kept.append((s.id, s.product_name, options["ip_rating"]))
            continue
        readings["%r -> %s" % (printed, values)] += 1
        undo.append({"id": s.id, "field": printed})
        fields["ip_rating"] = " / ".join(values)
        options["ip_rating"] = values
        s.fields, s.options = fields, options
        flag_modified(s, "fields")
        flag_modified(s, "options")
    print("DuraGreen products read: %d" % len(products))
    for line, count in readings.most_common():
        print("  %4d  %s" % (count, line))
    print("to change: %d" % len(undo))
    print("kept, already has an IP option list: %d %s" % (len(kept), kept[:5] if kept else ""))
    # What must not move, checked after the changes and before anything is saved.
    if prices_before != [(s.id, s.base_price, json.dumps(s.price_adders, sort_keys=True)) for s in products]:
        db.rollback()
        db.close()
        raise SystemExit("A price would change. Nothing was written.")
    if args.dry_run:
        db.rollback()
        print("--dry-run: nothing was written.")
    else:
        if undo:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = _REPO_ROOT / "data" / ("duragreen_ip_options_undo_%s.json" % stamp)
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(undo, ensure_ascii=False, indent=1), encoding="utf-8")
            print("undo file: %s" % path)
        db.commit()
        print("written: %d" % len(undo))
    db.close()


if __name__ == "__main__":
    main()
