"""Make every NPD parameter independent: no two template rows of a typology share a key.

A parameter template row's `field_key` is where its value is stored - in the NPD form, in a
product's `fields` and options, and in its price adders. Two rows with one key were therefore one
value printed twice: typing IP67 into IP RATING under GENERAL also filled IP RATING under POWER
SUPPLY, although one is the luminaire's rating and the other its driver's. The rule this enforces
is the owner's: every parameter is its own, whatever it is called and wherever it is printed.

The shared keys came from npd.STARTER_TEMPLATE (every typology made with "New typology" gave both
IP RATING rows `ip_rating`), from "Add parameter" (which built the key from the label alone), and
from the original seeding, which read some manufacturers' sheets that print a section twice. The
code paths are fixed; this repairs what they already produced, in two steps per typology.

1. The same line entered twice - same section, same name, same key - is one row too many, and the
   extra is removed. It cannot be made a second independent value: a product's printed sheet
   holds one value per name per section (`params[section][name]`), so a second row would take
   typing that never prints. "Add parameter" already refuses to create such a row. Nothing a
   product prints changes, since both rows showed the same value. Each removed row is printed in
   full, so it can be re-inserted exactly.

2. Any key still used by more than one row is split. One row keeps the key - the first in print
   order that has no library name of its own - and every other row gets a key of its own: the
   library's name where it has one (npd.GROUP_SCOPED_KEYS: a power supply's IP RATING is
   `driver_ip_rating`, its POWER `driver_power`, matched by label or key), otherwise
   npd.distinct_field_key ("general_ip_rating"). This covers every parameter, ACCESSORIES too:
   the row that keeps `accessories` goes on showing the accessory list, and a second ACCESSORIES
   row becomes a value of its own, starting from what it prints today.

Existing NPD products keep everything they print. For each re-keyed row, the value the product
printed under that section (its `params`) is copied to the new key. Only the value - not an
option list: the choice a buyer makes, and the adders that price it, belong to the row that kept
the key, and giving the re-keyed row options of its own would offer a second choice (a second
IP20/IP66 on a priced Vena strip) that no price follows. Prices are not touched at all: an adder
stays on the key it was entered against, which is still there - nothing moves, so nothing vanishes.

Safe to run more than once: once every key in a typology is unique there is nothing to do.

    python scripts/migrations/migrate_split_shared_parameter_keys.py --dry-run
    python scripts/migrations/migrate_split_shared_parameter_keys.py
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
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import models, npd
from app.database import SessionLocal


def library_key(group, key, label):
    """The name the library gives this parameter under this section, if it has one - found by
    the label ("POWER" -> driver_power) or the key ("ip_rating" -> driver_ip_rating)."""
    g = group.strip().upper()
    return (npd.GROUP_SCOPED_KEYS.get((g, npd.key_from_label(label)))
            or npd.GROUP_SCOPED_KEYS.get((g, key)))


def plan_typology(rows):
    """(removals, moves) for one typology's template rows, given in print order.

    removals: [row] - exact repeats of a line already in the same section (step 1).
    moves:    [(row, old_key, new_key)] - rows given a key of their own (step 2)."""
    removals, kept, seen = [], [], set()
    for r in rows:
        line = (r.group_name, r.label, r.field_key)
        if line in seen:
            removals.append(r)
        else:
            seen.add(line)
            kept.append(r)

    taken = {r.field_key for r in kept}
    by_key = defaultdict(list)
    for r in kept:
        by_key[r.field_key].append(r)
    moves = []
    for key, keyed in by_key.items():
        if len(keyed) < 2:
            continue
        named = [r for r in keyed if library_key(r.group_name, key, r.label)]
        keeper = next((r for r in keyed if r not in named), keyed[0])
        for r in keyed:
            if r is keeper:
                continue
            library = library_key(r.group_name, key, r.label)
            new = (library if library and library not in taken
                   else npd.distinct_field_key(r.group_name, key, taken))
            taken.add(new)
            moves.append((r, key, new))
    return removals, moves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    PT = models.ParameterTemplate
    rows = db.query(PT).order_by(PT.discipline, PT.typology, PT.sort_order, PT.id).all()
    by_typology = defaultdict(list)
    for r in rows:
        by_typology[(r.discipline, r.typology)].append(r)

    total_removed = total_rows = total_products = total_values = 0
    for (discipline, typology), trows in sorted(by_typology.items()):
        removals, moves = plan_typology(trows)
        if not removals and not moves:
            continue
        print("\n%s / %s" % (discipline, typology))
        # Everything needed to put a removed row back, and the id of every re-keyed one:
        # UPDATE parameter_templates SET field_key = '<old>' WHERE id = <id>.
        for r in removals:
            print("   remove repeat   row %-5d %-26s %-22s key=%s sort_order=%s configurable=%s "
                  "user_added=%s" % (r.id, r.group_name, r.label, r.field_key, r.sort_order,
                                     r.is_configurable, r.user_added))
        for r, old, new in moves:
            print("   own key         row %-5d %-26s %-22s %s -> %s"
                  % (r.id, r.group_name, r.label, old, new))
        total_removed += len(removals)
        total_rows += len(moves)

        products = (db.query(models.Specsheet)
                    .filter(models.Specsheet.origin == "npd",
                            models.Specsheet.discipline == discipline,
                            models.Specsheet.typology == typology).all())
        for sheet in products:
            fields = dict(sheet.fields or {})
            params = sheet.params or {}
            said = []
            for r, old, new in moves:
                printed = str((params.get(r.group_name) or {}).get(r.label) or "").strip()
                if not printed:
                    continue
                if new in fields:
                    # Already has a value under that name (a parsed product can); keep it, and
                    # say so if it is not what the sheet prints.
                    if str(fields[new]).strip() != printed:
                        print("      product %-5d keeps its own %s=%r (sheet prints %r)"
                              % (sheet.id, new, fields[new], printed))
                    continue
                fields[new] = printed
                said.append("%s=%r" % (new, printed))
            if not said:
                continue
            total_products += 1
            total_values += len(said)
            kept_adders = sorted({old for _r, old, _n in moves
                                  if old in (sheet.price_adders or {})})
            print("      product %-5d %-34s %s%s" % (
                sheet.id, (sheet.product_name or "")[:34], ", ".join(said),
                "   [adders stay on %s]" % ", ".join(kept_adders) if kept_adders else ""))
            if not args.dry_run:
                # A new dict, not an edit in place: a JSON column only notices being reassigned.
                sheet.fields = fields
        if not args.dry_run:
            for r in removals:
                db.delete(r)
            for r, _old, new in moves:
                r.field_key = new

    print("\n%d repeated row(s) to remove; %d row(s) to give their own key; "
          "%d value(s) to carry over on %d NPD product(s)."
          % (total_removed, total_rows, total_values, total_products))
    if args.dry_run:
        print("--dry-run: nothing written.")
        db.rollback()
        return
    db.commit()
    print("done.")


if __name__ == "__main__":
    main()
