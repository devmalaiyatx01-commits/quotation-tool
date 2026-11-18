"""Checks every DuraGreen product's order codes, read-only - the evidence before and after any
change to app/duragreen.py, its readers or its data.

    python scripts/maintenance/audit_duragreen_codes.py                     # the summary
    python scripts/maintenance/audit_duragreen_codes.py --product 432       # one product, every combination
    python scripts/maintenance/audit_duragreen_codes.py --compare HEAD      # vs the engine at a git revision
    python scripts/maintenance/audit_duragreen_codes.py --json out.json     # everything, for a closer look

What it checks, for every DuraGreen product in the database DATABASE_URL points at:

  rules        whose rules write its code (duragreen.scheme_of): its range's table (ACME, MOOD), or
               only its own catalogue page and sheet (PAGE), or only its sheet (SHEET), or none.
  round trip   every printed code, asked for with its own configuration, must come back - itself,
               or its start when the code ends in a finish the row does not name (the engine then
               rightly asks for the finish). A printed code that does not come back is a bug.
  every combination
               of the options a BOQ line chooses (the first --cap per product): how many get a
               printed code, a built one (and how surely), and how many get none - and why.
  --compare REV
               the same combinations through the engine as it was at git revision REV: every code
               that changed, sorted into "now coded", "now none" and "different code". Status
               changes alone are counted, not listed. A different code needs a reason every time.

Nothing is written to the database. Slow on purpose-built data (a few minutes for 600 products);
--cap lowers the combinations per product.
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
import collections
import importlib.util
import itertools
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from app import duragreen, models
from app.database import SessionLocal

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_rows(db):
    """Every DuraGreen product with its codes, parts - and the catalogue rules as plain copies,
    the way app/main.py hands them to the engine."""
    rules = [SimpleNamespace(scope=r.scope, segment=r.segment, attribute=r.attribute, position=r.position,
                             dash_after=r.dash_after, meaning=r.meaning, code=r.code, source=r.source)
             for r in db.query(models.VendorCodeRule).filter(models.VendorCodeRule.make == duragreen.MAKE)
             .order_by(models.VendorCodeRule.scope, models.VendorCodeRule.position, models.VendorCodeRule.id)]
    codes, parts = collections.defaultdict(list), collections.defaultdict(list)
    for c in db.query(models.VendorOrderCode).filter(models.VendorOrderCode.make == duragreen.MAKE).order_by(
            models.VendorOrderCode.sort_order, models.VendorOrderCode.id):
        codes[c.specsheet_id].append(c)
    for p in db.query(models.VendorCodePart).filter(models.VendorCodePart.make == duragreen.MAKE).order_by(
            models.VendorCodePart.attribute, models.VendorCodePart.id):
        parts[p.specsheet_id].append(p)
    sheets = (db.query(models.Specsheet).filter(models.Specsheet.make == duragreen.MAKE)
              .order_by(models.Specsheet.id).all())
    return sheets, codes, parts, rules


def engine_at(rev):
    """app/duragreen.py as it was at git revision `rev`, loaded as its own module."""
    source = subprocess.run(["git", "show", "%s:app/duragreen.py" % rev], cwd=_REPO_ROOT,
                            capture_output=True, check=True).stdout
    path = _pathlib.Path(tempfile.mkdtemp()) / "duragreen_at_rev.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("duragreen_at_rev", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def combos(sheet, cap):
    # Not what no code states: varied here, half of a capped product's combinations would be repeats.
    fields = [k for k in duragreen.CONFIG_FIELDS if duragreen._offered(sheet, k) and k not in duragreen.NOT_IN_THE_CODE]
    axes = [[(k, v) for v in duragreen._offered(sheet, k)] for k in fields]
    return [dict(c) for c in itertools.islice(itertools.product(*axes), cap)] if axes else []


def kind(result):
    if result["code"] and result["missing"]:
        return "partial (a finish to choose)"
    return result["status"] or ("incomplete" if result["missing"] else "none")


def _other_version(sheet, row, codes, rules):
    """Whether the product's own name picks a version of its sheet's codes (duragreen's
    _version_by_name) and `row` is not one of them - a sibling's printed row, not this product's."""
    printed = [c for c in codes if not duragreen.is_example(c)]
    picked = duragreen._version_by_name(sheet, printed, rules)
    return picked is not None and row not in picked


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cap", type=int, default=300, help="combinations worked out per product (default 300)")
    ap.add_argument("--product", type=int, help="one product: its rules and every combination's code")
    ap.add_argument("--compare", metavar="REV", help="also run the engine as it was at this git revision")
    ap.add_argument("--json", metavar="PATH", help="write every product's detail here")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        sheets, codes_by, parts_by, rules = load_rows(db)
    finally:
        db.close()
    old = engine_at(args.compare) if args.compare else None
    if args.product:
        sheets = [s for s in sheets if s.id == args.product]
        if not sheets:
            raise SystemExit("no DuraGreen product #%d" % args.product)

    totals, reasons, moves, diffs, detail = (collections.Counter(), collections.Counter(), collections.Counter(),
                                             collections.Counter(), [])
    for s in sheets:
        codes, parts = codes_by[s.id], parts_by[s.id]
        scheme = duragreen.scheme_of(codes)
        totals["rules: " + scheme["name"]] += 1
        bad = []
        for c in codes:
            if duragreen.is_example(c) or not c.config:
                continue
            r = duragreen.order_code(s, c.config, codes, parts, rules)
            back = r["code"] == c.code or (r["code"] and r["missing"] and c.code.startswith(r["code"]))
            if back:
                totals["round trip ok"] += 1
            elif r["code"] and r["code"].startswith(c.code + "-"):
                # the sheet's short code with the catalogue's fixed tail added (-TB, a trimless
                # bezel): DuraGreen's full code for it, accepted since the 19 Oct checks
                totals["round trip ok (catalogue adds its fixed tail)"] += 1
            elif r["code"] and _other_version(s, c, codes, rules):
                # the sheet prints a sibling's codes too (SA beside SF), and this product's own
                # name picks the other version ("1 Spot Fixed" is SF): the row is the sibling's
                totals["round trip: the row is another version's (the name picks)"] += 1
            elif r["missing"] and not any(k in c.config for k in r["missing"]):
                # the row itself leaves a choice open (Sparkle's table prints no driver): asked
                # for alone it cannot name one code, and the engine rightly asks for the choice
                totals["round trip: row leaves a choice open"] += 1
            elif not r["code"] and r["reason"]:
                # an honest refusal (several unlabelled versions, a row that leaves a choice open)
                # - a printed code nobody can reach yet, but never a wrong one
                totals["round trip: refused, with the reason"] += 1
                bad.append({"printed": c.code, "config": c.config, "got": "", "why": r["reason"]})
            else:
                totals["round trip FAILED"] += 1
                bad.append({"printed": c.code, "config": c.config, "got": r["code"], "why": r["reason"]})
        rows = []
        for chosen in combos(s, args.cap):
            r = duragreen.order_code(s, chosen, codes, parts, rules)
            k = kind(r)
            totals["combination: " + k] += 1
            # Comparing two engines cannot catch a code lost to a choice both treat alike, so this
            # compares the configuration with itself: no choice a code does not state may change its code.
            for key in duragreen.NOT_IN_THE_CODE:
                for value in duragreen._offered(s, key):
                    other = duragreen.order_code(s, {**chosen, key: value}, codes, parts, rules)
                    totals["choice not in the code: " + ("same code" if other["code"] == r["code"] else "CHANGED THE CODE")] += 1
            if not r["code"]:
                reasons[r["reason"][:120]] += 1
            row = {"chosen": chosen, "code": r["code"], "status": r["status"], "reason": r["reason"]}
            if old is not None:
                o = old.order_code(s, chosen, codes, parts, rules)
                moves[(o["status"] or "-", r["status"] or "-")] += 1
                if o["code"] != r["code"]:
                    change = ("now coded" if not o["code"] else "now none" if not r["code"] else "different code")
                    diffs[change] += 1
                    row["was"] = {"code": o["code"], "status": o["status"], "reason": o["reason"]}
                    row["change"] = change
            rows.append(row)
        detail.append({"id": s.id, "name": s.product_name, "series": s.series, "typology": s.typology,
                       "approval": s.approval_status, "rules": scheme, "round_trip_failed": bad,
                       "combinations": rows})
        if args.product:
            print("#%d %s / %s - %s" % (s.id, s.series, s.product_name, scheme["text"]))
            for c in codes:
                print("   %-34s %s  (%s)" % (c.code, json.dumps(c.config, ensure_ascii=False), c.source))
            for row in rows:
                print("   %-60s %-30s %-12s %s" % (json.dumps(row["chosen"], ensure_ascii=False)[:60], row["code"],
                                                   row["status"], "" if row["code"] else row["reason"][:70]))

    print("\nDuraGreen products: %d" % len(sheets))
    for k in sorted(totals):
        print("  %-40s %7d" % (k, totals[k]))
    if reasons:
        print("\nWhy a combination gets no code (the 15 commonest):")
        for why, n in reasons.most_common(15):
            print("  %6d  %s" % (n, why))
    if old is not None:
        print("\nAgainst the engine at %s - codes that changed:" % args.compare)
        for k, n in diffs.most_common():
            print("  %6d  %s" % (n, k))
        print("status moves (then -> now):")
        for (a, b), n in moves.most_common():
            if a != b:
                print("  %6d  %s -> %s" % (n, a, b))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"totals": dict(totals), "products": detail}, f, ensure_ascii=False, indent=1)
        print("\ndetail written to", args.json)
    if totals["round trip FAILED"]:
        print("\nROUND TRIP FAILURES: %d printed codes do not come back - see --json or --product" % totals["round trip FAILED"])


if __name__ == "__main__":
    main()
