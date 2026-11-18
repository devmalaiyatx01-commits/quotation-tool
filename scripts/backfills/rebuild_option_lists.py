"""Brings each InHouse product's option lists up to date with its typology's template - what opening
the product in NPD and saving it without an edit would do, and nothing else.

Why this exists
---------------
A product's option lists (the choices the Configurator asks, and what a product code's
configuration segments are made from) are built when the product is saved in NPD, from the fields
its typology's template marks as a choice. Change a template, and every product saved before the
change keeps its old lists until somebody opens and saves it. On the live database on 28 Sep 2026
that was 76 InHouse products: 50 offered no reflector finish, although their sheets print one and
their template makes it a choice, so the Configurator could not ask it and a product code could not
carry it. The local database had been saved through, so the same products had their lists there.

What it changes
---------------
Only option lists, and only by adding or rebuilding a list the save would build - a stored list the
save does not produce is kept (the save itself merges, never removes). A product whose no-edit save
would change anything else - a printed line, a field, the title - is left alone and listed, because
that is a different change and not this script's to make. Stored accessory lists are kept as they
are, as structure_inhouse_catalogue.py keeps them.

Checked before anything is written: every printed specsheet prints exactly as before, no base
price or adder changes, every price still matches an option the product offers, and a no-edit save
afterwards changes nothing. The run stops, writing nothing, if any check fails.

Run it before structure_inhouse_catalogue.py, so that script sees every product in the state a save
leaves it in. Safe to re-run: a product already up to date is not touched.

    python scripts/backfills/rebuild_option_lists.py              # dry run: prints, writes nothing
    python scripts/backfills/rebuild_option_lists.py --apply      # writes
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
import json
from collections import Counter

from app.database import SessionLocal
from app import models

# The sibling that already simulates NPD's save and knows what a sheet prints and what a price
# is recorded against - one implementation of each, shared.
from structure_inhouse_catalogue import (MAKE, DISCIPLINE, simulate_save, spec_state, printed,
                                         price_keys, reachable)


def _without_accessories(options):
    return {k: v for k, v in (options or {}).items() if k != "accessories"}


def run(db, out=print):
    """Apply to the session and return the problems found - [] means safe to commit."""
    sheets = (db.query(models.Specsheet)
              .filter(models.Specsheet.make == MAKE, models.Specsheet.origin == "npd",
                      models.Specsheet.discipline == DISCIPLINE)
              .order_by(models.Specsheet.id).all())
    out("%d %s products\n" % (len(sheets), MAKE))
    rebuilt, left, problems = [], [], []
    by_list = Counter()
    for s in sheets:
        saved = simulate_save(db, s)
        if spec_state(saved) == spec_state(s):
            continue
        if (json.dumps(saved["params"] or {}, sort_keys=True) != json.dumps(s.params or {}, sort_keys=True)
                or json.dumps(saved["fields"] or {}, sort_keys=True, default=str)
                != json.dumps(s.fields or {}, sort_keys=True, default=str)
                or (saved["category"] or "") != (s.category or "")):
            left.append(s)
            continue
        print_before = printed(s)
        money_before = (s.base_price, json.dumps(s.price_adders or {}, sort_keys=True))
        prices_before = {k: reachable(s, *k) for k in price_keys(db, s)}
        old = _without_accessories(s.options)
        new = _without_accessories(saved["options"])
        if "accessories" in (s.options or {}):
            new["accessories"] = s.options["accessories"]
        s.options = new
        lines = []
        for key in sorted(set(old) | set(_without_accessories(new))):
            if old.get(key) != new.get(key):
                by_list[key] += 1
                lines.append("%s: %s -> %s" % (key, old.get(key), new.get(key)))
        rebuilt.append((s, lines))

        if printed(s) != print_before:
            problems.append("#%d %s: its specsheet would print differently" % (s.id, s.product_name))
        if (s.base_price, json.dumps(s.price_adders or {}, sort_keys=True)) != money_before:
            problems.append("#%d %s: base price or adders changed" % (s.id, s.product_name))
        for key, was in prices_before.items():
            if was and not reachable(s, *key):
                problems.append("#%d %s: price for %s=%s no longer matches an option"
                                % (s.id, s.product_name, key[0], key[1]))
        if spec_state(simulate_save(db, s)) != spec_state(s):
            problems.append("#%d %s: a no-edit save would still change it" % (s.id, s.product_name))

    out("Option lists rebuilt, by list: %s" % dict(sorted(by_list.items())))
    for s, lines in rebuilt:
        out("   #%-4d %-30s %s" % (s.id, (s.product_name or "")[:30], "; ".join(lines)))
    out("\nLeft alone - a no-edit save would change more than option lists (%d):" % len(left))
    for s in left:
        out("   #%-4d %s" % (s.id, s.product_name))
    out("\n%d of %d products rebuilt. Printed specsheets: %s. Base prices and adders: %s. "
        "Prices matching an option: %s." % (
            len(rebuilt), len(sheets),
            "CHANGED" if any("print" in p for p in problems) else "unchanged",
            "CHANGED" if any("adders" in p for p in problems) else "unchanged",
            "SOME LOST" if any("no longer matches" in p for p in problems) else "all kept"))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--dry-run", action="store_true", help="print only (the default)")
    args = ap.parse_args()

    db = SessionLocal()
    problems = run(db)
    if problems:
        print("\nSTOPPED - nothing written:")
        for p in problems:
            print("   " + p)
        db.rollback()
        return 1
    if args.apply:
        db.commit()
        print("Written.")
    else:
        db.rollback()
        print("Dry run - nothing written. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
