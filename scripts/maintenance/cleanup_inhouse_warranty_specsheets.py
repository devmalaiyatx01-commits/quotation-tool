"""One-off cleanup: removes InHouse specsheets that still carry a `warranty` field -
per direct instruction, new specsheets don't get a warranty entry, so a warranty field
marks a stale catalog page from before a later, warranty-less re-import replaced it.

Not a blind delete - checked directly first (73 InHouse specsheets have a warranty
field): 17 real BOQItems and 72 real PriceEntry rows (some Locked) currently reference
them. A specsheet is only actually removed here if a genuine, verified replacement
exists - matched on IDENTICAL fields (warranty aside), or fields differing only by the
one confirmed real pattern (a later re-import adding "Water Stopper"/"IP68 Connector"
to `accessories`, nothing else). Two products sharing product_name/series/family/
variant is NOT enough on its own - checked directly and rejected for exactly that
reason: "SOLARA Midi" and "FLARE-Mini" each name two genuinely different real SKUs in
this library (different IP rating, wattage range, control gear - not a re-upload of the
same thing), and 13 more products (RAYON Spot, JADE-Mini, PULSE 1000 x3, LUMEA 1010 x5,
FLARE-Micro, SOLARA Wall) have no candidate at all. All 17 of those are left completely
untouched - deleting them would remove the product from the library entirely, not
dedupe it, and forcing the two SOLARA/FLARE pairs together would merge unrelated
products' BOQ items and prices onto the wrong specsheet.

For the 56 confirmed pairs: every BOQItem pointing at the old specsheet is re-pointed to
the replacement (never left dangling - the whole reason this isn't a blind delete).
Every PriceEntry is either re-pointed (its config doesn't already exist on the
replacement) or discarded as a confirmed redundant duplicate (the replacement already
has a PriceEntry for that exact config) - checked directly that every one of the 66 such
overlaps has an IDENTICAL price and status on both sides before treating it as
redundant; this script aborts rather than silently picking one if it ever finds a real
price disagreement instead.

Safe to re-run: an already-deleted old specsheet simply won't be found a second time.
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
from collections import defaultdict

from app.database import SessionLocal
from app import models

ACCESSORY_ADDITIONS = {"Water Stopper", "IP68 Connector"}


def _field_diff(old_fields, cand_fields):
    old_f = {k: v for k, v in old_fields.items() if k != "warranty"}
    cand_f = {k: v for k, v in cand_fields.items() if k != "warranty"}
    return {k: (old_f.get(k), cand_f.get(k)) for k in set(old_f) | set(cand_f) if old_f.get(k) != cand_f.get(k)}


def _is_clean_accessory_addition(diffs):
    if set(diffs.keys()) != {"accessories"}:
        return False
    old_acc, new_acc = diffs["accessories"]
    old_set = {x.strip() for x in (old_acc or "").split("/")}
    new_set = {x.strip() for x in (new_acc or "").split("/")}
    return not (old_set - new_set) and (new_set - old_set) <= ACCESSORY_ADDITIONS


def find_safe_pairs(db):
    sheets = db.query(models.Specsheet).filter(models.Specsheet.make == "InHouse").all()
    with_warranty = [s for s in sheets if (s.fields or {}).get("warranty")]
    without_warranty = [s for s in sheets if not (s.fields or {}).get("warranty")]

    pairs, skipped = [], []
    for s in with_warranty:
        same_name = [c for c in without_warranty if (c.product_name, c.series, c.family, c.variant)
                     == (s.product_name, s.series, s.family, s.variant)]
        best, best_diffs = None, None
        for c in same_name:
            diffs = _field_diff(s.fields or {}, c.fields or {})
            if best_diffs is None or len(diffs) < len(best_diffs):
                best, best_diffs = c, diffs
        if best is not None and (len(best_diffs) == 0 or _is_clean_accessory_addition(best_diffs)):
            pairs.append((s.id, best.id))
        else:
            reason = "no candidate at all" if best is None else f"best candidate {best.id} still differs: {best_diffs}"
            skipped.append((s.id, s.product_name, reason))
    return pairs, skipped


def main():
    db = SessionLocal()
    pairs, skipped = find_safe_pairs(db)
    print(f"{len(pairs)} specsheet(s) confirmed safe to retire; {len(skipped)} left untouched:\n")
    for sid, name, reason in skipped:
        print(f"  KEEP  id={sid} {name!r} - {reason}")
    print()

    total_boq_items_moved = 0
    total_price_entries_moved = 0
    total_price_entries_dropped = 0

    for old_id, new_id in pairs:
        old = db.query(models.Specsheet).get(old_id)
        new = db.query(models.Specsheet).get(new_id)

        items = db.query(models.BOQItem).filter(models.BOQItem.specsheet_id == old_id).all()
        for i in items:
            i.specsheet_id = new_id
        total_boq_items_moved += len(items)

        new_configs = {tuple(sorted((p.config or {}).items())): p for p in
                       db.query(models.PriceEntry).filter(models.PriceEntry.specsheet_id == new_id).all()}
        old_prices = db.query(models.PriceEntry).filter(models.PriceEntry.specsheet_id == old_id).all()
        for p in old_prices:
            key = tuple(sorted((p.config or {}).items()))
            twin = new_configs.get(key)
            if twin is not None:
                assert twin.price == p.price and twin.status == p.status, (
                    f"REFUSING to auto-resolve: specsheet {old_id}->{new_id} config {key} has "
                    f"DIFFERENT price/status (old #{p.id}: {p.price}/{p.status} vs new #{twin.id}: "
                    f"{twin.price}/{twin.status}) - needs a human decision, not this script."
                )
                db.delete(p)
                total_price_entries_dropped += 1
            else:
                p.specsheet_id = new_id
                total_price_entries_moved += 1

        # Flush the reassignments to the database BEFORE deleting the old specsheet, in
        # this same iteration - a real bug caught the hard way running this the first
        # time: with no flush, SQLAlchemy's relationship cascade (Specsheet.boq_items/
        # price_entries have no explicit cascade= set) re-derives "which rows point at
        # the row being deleted" from the DATABASE, not from these pending in-memory
        # reassignments - since the UPDATE for i.specsheet_id/p.specsheet_id hadn't been
        # issued yet, it still saw the OLD foreign key and nulled it out on delete,
        # silently overwriting the reassignment above. Confirmed directly: without this
        # flush, all 16 real BOQItem re-points came back NULL instead of the new id, and
        # had to be hand-recovered afterward. Flushing first means the delete's cascade
        # sees the NEW foreign key already committed to the session, so it correctly
        # leaves these rows alone.
        db.flush()

        print(f"  id={old_id} {old.product_name!r} -> id={new_id}: "
              f"{len(items)} BOQItem(s) moved, {len(old_prices)} PriceEntry row(s) "
              f"({sum(1 for p in old_prices if tuple(sorted((p.config or {}).items())) not in new_configs)} moved, "
              f"{sum(1 for p in old_prices if tuple(sorted((p.config or {}).items())) in new_configs)} redundant-dropped)")
        db.delete(old)

    db.commit()
    print(f"\nDone. Retired {len(pairs)} specsheet(s). "
          f"{total_boq_items_moved} BOQItem(s) re-pointed, "
          f"{total_price_entries_moved} PriceEntry row(s) re-pointed, "
          f"{total_price_entries_dropped} redundant PriceEntry row(s) dropped.")


if __name__ == "__main__":
    main()
