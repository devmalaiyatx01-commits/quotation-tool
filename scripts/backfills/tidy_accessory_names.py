"""Makes one accessory's spelling the same everywhere it is recorded.

Datasheets spell the same part several ways - HONEYCOMB and Honeycomb, SNOOT and Snoot,
EXTENSIONARM and EXTENSION ARM - and every one of those spellings became its own entry in the
app: its own row in NPD -> Accessories, its own name in the Match tab's picker, its own key for a
price. This settles each group on one spelling.

Why all four places move together
---------------------------------
An accessory's name is not only a label, it is the key that ties three records together:

  * `Specsheet.options["accessories"]` - the products that list it;
  * a `PriceEntry` whose `config["accessory"]` is that exact string - what it costs under that
    product (resolveAccessoryPrice in static/app.js matches the string exactly);
  * `Specsheet.params["Accessory codes (Exenia)"]` - a dict keyed by that exact string.

Renaming one and not the others does not tidy the data, it breaks it: the price and the order
code stop resolving and the accessory silently goes back to costing nothing. So
app.accessories.rename() moves all of them in one transaction, and this script is a thin wrapper
around it that shows its working first.

What it will and will not do
----------------------------
It only merges spellings that already differ by nothing but case, punctuation or spacing. Names
that differ by a real word - "Honeycomb Louver" and "Honeycomb louvre" - are left alone, because
whether those are one part is a judgement about the products, not about the text. Merge those in
NPD -> Accessories, where a person chooses which name to keep.

The raw `fields["accessories"]` text a datasheet was parsed from is never rewritten: that is the
record of what the PDF actually printed. Where a product had only that raw text and no structured
list, the list is written for it, which is the field the app reads anyway.

    python scripts/backfills/tidy_accessory_names.py              # dry run: prints, writes nothing
    python scripts/backfills/tidy_accessory_names.py --apply      # writes

Safe to re-run: once a group is consistent it is no longer a group, and there is nothing to do.
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
from app.database import SessionLocal
from app import accessories, models


def census(db):
    """What must survive: every (product, accessory) pair, every price, every order code.

    Counted on the folded key, which a rename never changes - so these numbers are the same
    before and after unless something was genuinely lost.
    """
    pairs = 0
    codes = 0
    for sheet in db.query(models.Specsheet).all():
        names = accessories._names_of(sheet)
        pairs += len({accessories.fold(n) for n in names})
        for name in (sheet.params or {}).get("Accessory codes (Exenia)") or {}:
            codes += 1
    prices = sum(1 for p in db.query(models.PriceEntry).all() if (p.config or {}).get("accessory"))
    return {"product-accessory pairs": pairs, "accessory prices": prices, "order codes": codes}


def main():
    apply = "--apply" in _sys.argv
    db = SessionLocal()
    try:
        before = census(db)
        plan = accessories.plan_tidy(db)
        if not plan:
            print("Every accessory is already spelled one way. Nothing to do.")
            return
        print("%d accessor%s spelled more than one way:\n"
              % (len(plan), "y is" if len(plan) == 1 else "ies are"))
        total = 0
        for group in plan:
            print("  keep  %s" % group["keep"])
            for v in group["variants"]:
                mark = "   (keep)" if v["name"] == group["keep"] else "-> renamed"
                print("        %-26s %3d product(s) %s" % (v["name"], v["products"], mark))
            total += group["products"]
        print("\n%d product listing(s) would be rewritten." % total)
        print("Before: " + ", ".join("%s %d" % (k, v) for k, v in before.items()))

        if not apply:
            print("\nDry run - nothing was written. Re-run with --apply to make these changes.")
            return

        done = {"products": 0, "prices": 0, "codes": 0}
        for group in plan:
            touched = accessories.rename(db, group["key"], group["keep"])
            for k in done:
                done[k] += touched[k]
        db.commit()
        accessories.forget()
        after = census(db)
        print("\nWrote: %d product(s), %d accessory price(s), %d order code(s)."
              % (done["products"], done["prices"], done["codes"]))
        print("After:  " + ", ".join("%s %d" % (k, v) for k, v in after.items()))
        for k, v in before.items():
            if after[k] != v:
                print("  !! %s changed from %d to %d - that should not happen" % (k, v, after[k]))
        left = accessories.plan_tidy(db)
        print("Accessories still spelled more than one way: %d" % len(left))
    finally:
        db.close()


if __name__ == "__main__":
    main()
