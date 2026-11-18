"""Write each DuraGreen product's CRI, beam and control the one way, so picking a value in Configure finds them all.

The same fact was stored several ways: "CRI 98" on 139 products and "98Ra" on 57, so picking one missed the
other. "Zoomable 20°-50°" sat beside "20°-50°" on the same product, and order codes only use the plain form.
"92Ra with 97Ra option" was two values in one. A control of "110" or "2 Step MacAdams" was the next column of the
sheet, read as a control. app/duragreen.spec_values is the one reading, and new reads already use it; this brings
the products already stored into line.

What this changes: `fields` and `options` for cri, beam_angle and control, make Duragreen, only where spec_values reads
them differently, and the engineer's groups in `params`, which are built from `fields`. "DURAGREEN SPEC" in
`params` keeps the sheet's own words. What it never changes: a price, a code, or a BOQ line.

A product is left alone, and listed, when a value that would be renamed carries a price adder, or is stated by one
of its code rows: a row keeps its own text, and would stop matching a product that no longer lists it.

    python scripts/backfills/backfill_duragreen_spec_values.py --dry-run
    python scripts/backfills/backfill_duragreen_spec_values.py
    python scripts/backfills/backfill_duragreen_spec_values.py --undo data/duragreen_spec_values_undo_<time>.json

Safe to re-run: a value already written this way reads back the same. A real run writes what it replaced to
that undo file. Undoing skips any product whose value was changed since.
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
from app.duragreen import MAKE, norm, regroup_params, spec_values  # noqa: E402

KEYS = ("cri", "beam_angle", "control")


def rewritten(fields, options, key):
    """(field text, option list) as they should read, or None when they already do. A field that lists
    several values is left to its option list, which is what a person chooses from."""
    held = options.get(key)
    text = str(fields.get(key) or "").strip()
    if isinstance(held, list) and held:
        values = list(dict.fromkeys(v for value in held for v in spec_values(key, value)))
        if values == held:
            return None
        return " / ".join(values) if text else None, values
    if not text or " / " in text:
        return None
    values = spec_values(key, text)
    if not values or values == [text]:
        return None
    return " / ".join(values), values if len(values) > 1 else None


def restore(path, dry_run):
    db = SessionLocal()
    done, skipped = 0, []
    for entry in json.loads(Path(path).read_text(encoding="utf-8")):
        s = db.get(models.Specsheet, entry["id"])
        fields, options = dict(s.fields or {}) if s else {}, dict(s.options or {}) if s else {}
        if s is None or any(fields.get(k) != c["new_field"] or options.get(k) != c["new_options"] for k, c in entry["changes"].items()):
            skipped.append(entry["id"])
            continue
        untouched = "old_params" in entry and s.params == regroup_params(fields, entry["old_params"])
        for key, change in entry["changes"].items():
            for store, value in ((fields, change["old_field"]), (options, change["old_options"])):
                if value is None:
                    store.pop(key, None)
                else:
                    store[key] = value
        s.fields, s.options = fields, options
        flag_modified(s, "fields")
        flag_modified(s, "options")
        # Exactly as it was, unless a save has rewritten it since: then only CRI and beam go back.
        s.params = entry["old_params"] if untouched else regroup_params(fields, s.params)
        flag_modified(s, "params")
        done += 1
    print("restored: %d, skipped because changed since: %s" % (done, skipped))
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
    S = models.Specsheet
    products = (db.query(S).options(load_only(S.id, S.product_name, S.fields, S.options, S.params, S.base_price, S.price_adders))
                .filter(S.make == MAKE).order_by(S.id).all())
    ids = [s.id for s in products]
    code_rows = {}
    for r in db.query(models.VendorOrderCode.specsheet_id, models.VendorOrderCode.config).filter(models.VendorOrderCode.specsheet_id.in_(ids)).all():
        code_rows.setdefault(r.specsheet_id, []).append(r.config or {})
    price_entries = db.query(models.PriceEntry).filter(models.PriceEntry.specsheet_id.in_(ids)).count()
    prices_before = [(s.id, s.base_price, json.dumps(s.price_adders, sort_keys=True)) for s in products]
    undo, kept, readings = [], [], Counter()
    for s in products:
        fields, options = dict(s.fields or {}), dict(s.options or {})
        changes = {}
        for key in KEYS:
            new = rewritten(fields, options, key)
            if new is None:
                continue
            old_list = options.get(key)
            before = set(old_list or [fields.get(key)])
            after = set(new[1] or [new[0]])
            renamed = {norm(v) for v in before - after}
            adders = set(((s.price_adders or {}).get(key) or {}))
            stated = {str(c.get(key)) for c in code_rows.get(s.id, []) if c.get(key)}
            # By norm, as a code row is matched to a product (duragreen._states_exactly).
            holding = sorted(v for v in adders | stated if norm(v) in renamed)
            if holding:
                kept.append((s.id, s.product_name, key, holding))
                continue
            changes[key] = {"old_field": fields.get(key), "old_options": old_list}
            readings["%s: %s -> %s" % (key, old_list or fields.get(key), new[1] or new[0])] += 1
            if new[0] is not None:
                fields[key] = new[0]
            if new[1] is not None:
                options[key] = new[1]
            changes[key].update(new_field=fields.get(key), new_options=options.get(key))
        if changes:
            undo.append({"id": s.id, "changes": changes, "old_params": s.params})
            s.fields, s.options = fields, options
            s.params = regroup_params(fields, s.params)
            for column in ("fields", "options", "params"):
                flag_modified(s, column)
    print("DuraGreen products read: %d" % len(products))
    for line, count in readings.most_common():
        print("  %4d  %s" % (count, line[:150]))
    print("products to change: %d" % len(undo))
    print("kept, a value that would be renamed is priced or stated by a code row: %d" % len(kept))
    for entry in kept:
        print("  %s" % (entry,))
    print("price entries on DuraGreen products, which match a value exactly: %d" % price_entries)
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
            path = _REPO_ROOT / "data" / ("duragreen_spec_values_undo_%s.json" % stamp)
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(undo, ensure_ascii=False, indent=1), encoding="utf-8")
            print("undo file: %s" % path)
        db.commit()
        print("written: %d" % len(undo))
    db.close()


if __name__ == "__main__":
    main()
