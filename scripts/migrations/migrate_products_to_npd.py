"""Bring existing products under NPD management, carrying their prices with them.

Pricing now happens in one place - a product's base price plus an adder per option - so the
products that were priced through the old List Pricing queue have to be moved onto that
footing rather than left behind on it.

What this does, per product:

Runs over every InHouse product by default, and nothing else. NPD is InHouse-only for now;
drivers were briefly included here and taken back out, since they are made by MEAN WELL,
OSRAM, BAG and eleven others, and pulling them in put all of those makers in a tab meant for
one catalogue. Their prices were never in the specsheets anyway - a driver is quoted from the
verified rate tables in static/app.js (CC_DRIVER_PRICES and the two 24V DC tables), which is
what the Add Driver form reads. Pass --typology Driver explicitly to include them.

  * origin becomes "npd" and approval_status "approved". Approved, not pending: these are
    already in the library and already being quoted from, and marking them tentative would
    silently remove them from the catalogue mid-project.
  * base_price comes from the product's own unconditioned price entry - the row whose config
    is empty, meaning "this price applies whatever is chosen". Where there is none, 0.
  * price_adders are derived from the conditioned entries: an entry saying cct = TW costs
    P against a base of B becomes an adder of P - B on that value, which is the same
    number expressed the way NPD holds it.

Nothing is deleted. Every PriceEntry stays exactly as it was - they remain the audit trail
for where a figure came from, the accessory rows are still what the Configurator prices
accessories from, and a product can be put back simply by clearing the three columns.

    python scripts/migrations/migrate_products_to_npd.py --dry-run
    python scripts/migrations/migrate_products_to_npd.py
    python scripts/migrations/migrate_products_to_npd.py --typology Downlight --make InHouse
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
import collections

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal
from app import models

# A driver is specified on what it drives, and those facts live in `fields` on the parsed
# rows rather than in a printed parameter table - the stock-info block is all most of them
# print. Given here so a driver in NPD has somewhere to put them.
DRIVER_PARAMETERS = [
    ("ELECTRICAL PARAMETERS", "WATTAGE", "wattage", True),
    ("ELECTRICAL PARAMETERS", "INPUT VOLTAGE", "input_voltage", False),
    ("ELECTRICAL PARAMETERS", "OUTPUT VOLTAGE", "output_voltage", False),
    ("ELECTRICAL PARAMETERS", "CURRENT TYPE", "c_c_c_v", False),
    ("ELECTRICAL PARAMETERS", "CONTROL", "control", True),
    ("ELECTRICAL PARAMETERS", "IP RATING", "ip_rating", True),
    ("ELECTRICAL PARAMETERS", "DIMENSIONS", "dimensions", False),
]


def ensure_driver_template(db, discipline="architectural"):
    have = {(t.group_name, t.label) for t in db.query(models.ParameterTemplate)
            .filter(models.ParameterTemplate.typology == "Driver",
                    models.ParameterTemplate.discipline == discipline).all()}
    added = 0
    base = (db.query(models.ParameterTemplate)
            .filter(models.ParameterTemplate.typology == "Driver").count())
    for i, (group, label, key, cfg) in enumerate(DRIVER_PARAMETERS):
        if (group, label) in have:
            continue
        db.add(models.ParameterTemplate(
            discipline=discipline, typology="Driver", group_name=group, label=label,
            field_key=key, sort_order=base + i, is_configurable=cfg, user_added=False))
        added += 1
    return added


def derive_pricing(sheet):
    """(base_price, adders, notes, ok) for one product, read off its existing price entries.

    `ok` is False when base + adders cannot reproduce every price this product already
    quotes. Such a product is left alone rather than half-converted: NPD pricing short-
    circuits the price-entry lookup entirely (see resolveNpdPrice in static/app.js), so a
    product moved across with an incomplete rule would quietly stop quoting a real price.
    """
    notes = []
    product_rows = [p for p in (sheet.price_entries or [])
                    if "accessory" not in (p.config or {})]
    if not product_rows:
        return 0.0, {}, notes, True

    prices = {float(p.price) for p in product_rows}
    if len(prices) == 1:
        # One price, however many rows carry it. Any conditions on those rows describe the
        # only variant the product comes in - they are not a price that varies - so the
        # price is simply the base. Reading them as adders on a base of 0 would make the
        # product free until somebody re-picked the one option it has (the OZONE and
        # SOLARA rows, whose single price is written against a wattage or an IP rating).
        return prices.pop(), {}, notes, True

    # A field pinned to the same value on every row cannot be what makes the price differ,
    # so it carries no pricing information and is dropped. Filo S 10's rows both say
    # wattage 9.6W/M and differ only on IP rating; kept, the pair would read as two
    # two-field rows that no base-plus-adder rule can express, when in truth it is one
    # ordinary IP-rating adder.
    common = {}
    for i, p in enumerate(product_rows):
        cfg = {k: str(v) for k, v in (p.config or {}).items()}
        common = dict(cfg) if i == 0 else {k: v for k, v in common.items() if cfg.get(k) == v}
    reduced = [({k: str(v) for k, v in (p.config or {}).items() if k not in common},
                float(p.price)) for p in product_rows]

    base = 0.0
    unconditioned = [price for cfg, price in reduced if not cfg]
    if unconditioned:
        base = unconditioned[0]
        if len(set(unconditioned)) > 1:
            notes.append("several unconditioned prices; took %s" % base)

    adders = {}
    for cfg, price in reduced:
        if len(cfg) != 1:
            continue
        (field, value), = cfg.items()
        delta = round(price - base, 2)
        if delta:
            adders.setdefault(field, {})[value] = delta

    ok = True
    for cfg, price in reduced:
        if len(cfg) < 2:
            continue
        # A row conditioned on two fields at once is not itself an adder, but it is often
        # already implied by the single-field ones: a product's Tunable White Wall Wash at
        # total T is exactly its base B plus the adder A1 its own TW row asks and the adder A2
        # its own Wall Wash row asks. Where the arithmetic agrees the row is covered and
        # there is nothing to carry over; where it does not, the product genuinely prices
        # a combination on its own terms and no pair of adders can say so.
        implied = base + sum(adders.get(f, {}).get(v, 0.0) for f, v in cfg.items())
        if abs(implied - price) > 0.005:
            notes.append("%s costs %s, but base plus adders makes %s - no adder rule can "
                         "say that, so this product is left on its price entries"
                         % ("+".join("%s %s" % (f, v) for f, v in sorted(cfg.items())),
                            price, round(implied, 2)))
            ok = False

    if not unconditioned and adders:
        notes.append("no unconditioned price, so it costs 0 until %s is chosen"
                     % " or ".join(sorted(adders)))
    return base, adders, notes, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--typology", action="append",
                    help="only this typology (repeatable); default is every InHouse product")
    ap.add_argument("--make", action="append",
                    help="only this make (repeatable), alongside --typology; default InHouse")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    # Only when drivers are being asked for by name - the default pass is InHouse, which has
    # no drivers, and adding a template for a typology nobody is migrating just leaves an
    # ELECTRICAL PARAMETERS group sitting on a typology with no products.
    if args.typology and "Driver" in args.typology:
        added = ensure_driver_template(db)
        if added and not args.dry_run:
            db.commit()
        print("driver template parameters added: %d" % added)

    q = db.query(models.Specsheet).filter(models.Specsheet.origin == "parsed")
    if args.typology:
        q = q.filter(models.Specsheet.typology.in_(args.typology))
        if args.make:
            q = q.filter(models.Specsheet.make.in_(args.make))
        rows = q.all()
    else:
        # the default pass: everything InHouse makes, and only that
        rows = q.filter(models.Specsheet.make == "InHouse").all()

    print("products to move: %d" % len(rows))
    print()
    moved = priced = 0
    all_notes = []
    left_behind = []
    by_typology = collections.Counter()
    for s in rows:
        base, adders, notes, ok = derive_pricing(s)
        for n in notes:
            all_notes.append("%s (id %d): %s" % (s.product_name, s.id, n))
        if not ok:
            left_behind.append(s)
            continue
        s.origin = "npd"
        s.approval_status = "approved"
        s.base_price = base
        s.price_adders = adders
        moved += 1
        by_typology[s.typology] += 1
        if base or adders:
            priced += 1

    print("moved: %d   left on price entries: %d" % (moved, len(left_behind)))
    print("by typology:", dict(by_typology))
    print("carrying a price already: %d   starting at 0: %d" % (priced, moved - priced))
    print("products with adders derived: %d"
          % sum(1 for s in rows if s.origin == "npd" and s.price_adders))
    if left_behind:
        print()
        print("left on price entries (%d):" % len(left_behind))
        for s in left_behind:
            print("   %s (id %d)" % (s.product_name, s.id))
    if all_notes:
        print()
        print("worth a person's eye (%d):" % len(all_notes))
        for n in all_notes[:20]:
            print("   %s" % n)
        if len(all_notes) > 20:
            print("   ... and %d more" % (len(all_notes) - 20))

    if args.dry_run:
        db.rollback()
        print()
        print("--dry-run: nothing written.")
    else:
        db.commit()
        print()
        print("moved %d products onto NPD pricing. No price entry was deleted." % moved)
    db.close()


if __name__ == "__main__":
    main()
