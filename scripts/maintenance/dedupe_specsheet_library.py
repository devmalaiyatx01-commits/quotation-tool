"""Remove products the library lists more than once, keeping everything each copy knows.

A shared name is not enough to call two rows a duplicate. NEBEL -Mini is in the catalogue
twice at 7W and 10W, RAYONLinearAdjustable at 6W and 12W, ALLOOKING's ALD150-24-620CV once
per control type - all real, distinct products the parser named alike. Deleting on name would
throw those away. So a pair is only merged when the two rows specify the *same thing*: every
parsed field, compared with whitespace and case normalised, because the same PDF read twice
comes back with the same words spaced differently ("220VAC-240VAC" against
"220V AC - 240V AC").

Two kinds are handled, and nothing else:

  * A product authored in NPD that was already in the library, parsed from the manufacturer's
    own PDF. Authoring it again introduced fields the parsed row never had - the driver's own
    power and IP rating, the sub-typology - so the two are not interchangeable and the newer
    one cannot simply be deleted. The parsed row is kept, because it is the one carrying BOQ
    lines and the price entries a figure was verified against, and everything the authored row
    added is copied onto it first.

  * The same PDF uploaded more than once, parsing identically each time. The better-spaced
    copy is kept - a later upload often reads cleanly where an earlier one lost spaces.

Rows referenced by a BOQ line, a price entry or a config rule are never deleted; the script
refuses rather than orphaning a quotation.

    python scripts/maintenance/dedupe_specsheet_library.py --dry-run
    python scripts/maintenance/dedupe_specsheet_library.py
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
import re
import collections
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal
from app import models


def squash(value):
    """Compare on the words, not the spacing between them.

    Whitespace is removed rather than merely collapsed, because the difference between two
    parses of one PDF is spaces that went missing, not spaces that doubled up: "15deg / 24deg"
    against "15deg/ 24deg", "220V AC - 240V AC" against "220VAC-240VAC". Punctuation is kept,
    since it still carries meaning - it is what separates one option from the next - and so
    "7W" and "10W" stay as different as they are.
    """
    return re.sub(r"\s+", "", str(value or "")).lower()


def tight(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def identity(sheet):
    return "|".join(tight(p) for p in (sheet.make, sheet.product_name, sheet.series,
                                       sheet.family, sheet.variant))


def readable_fields(sheet):
    return {k: squash(v) for k, v in (sheet.fields or {}).items() if squash(v)}


def same_specification(a, b):
    """Do these two rows describe the same product? Compared on `fields`, which is what the
    Configurator offers and what a BOQ line is built from - a difference there is a different
    product, however alike the two are named.

    Equality is the rule for two parsed rows. Authoring a product that is already in the
    library is the one exception: the whole reason to author it is to say more about it than
    the manufacturer's PDF did, so the authored row carries fields the parsed one never had -
    the driver's own power and IP rating, the sub-typology. Requiring equality there would
    call the pair two different products and leave the duplicate standing, which is the very
    thing this script exists to remove. So an authored row counts as the same product when it
    agrees with the parsed row everywhere they overlap and only adds to it.
    """
    fa, fb = readable_fields(a), readable_fields(b)
    if fa == fb:
        return True
    if authored_in_npd(a) == authored_in_npd(b):
        return False
    authored, parsed = (fa, fb) if authored_in_npd(a) else (fb, fa)
    shared = set(authored) & set(parsed)
    return (bool(shared)
            and all(authored[k] == parsed[k] for k in shared)
            and set(parsed) <= set(authored))


def flatten_params(params):
    out = {}
    for group, rows in (params or {}).items():
        if isinstance(rows, dict):
            for label, value in rows.items():
                out[(tight(group), tight(label))] = squash(value)
    return out


def spacing_only(a_params, b_params):
    """True when two parameter tables hold the same readings and differ only in spacing."""
    return flatten_params(a_params) == flatten_params(b_params)


def references(db, sheet_id):
    return {
        "boq lines": db.query(models.BOQItem)
                       .filter(models.BOQItem.specsheet_id == sheet_id).count(),
        "price entries": db.query(models.PriceEntry)
                           .filter(models.PriceEntry.specsheet_id == sheet_id).count(),
        "config rules": db.query(models.ConfigRule)
                          .filter(models.ConfigRule.specsheet_id == sheet_id).count(),
    }


def authored_in_npd(sheet):
    """A row written here rather than read from a manufacturer's PDF. A parsed row always
    records the file it came from; an authored one has nothing to record."""
    return not (sheet.source_pdf_path or "").strip()


def better_spaced(sheet):
    """How much of this row's text kept its spaces. Two parses of one PDF are otherwise
    equal, and the one that reads "RAYON Linear - LL103004" is worth more than the one that
    reads "RAYONLinear-LL103004"."""
    text = " ".join([str(sheet.product_name or ""), str(sheet.category or "")] +
                    [str(v) for v in (sheet.fields or {}).values()])
    return text.count(" ")


def merge_into(keeper, donor, notes):
    """Everything the donor knows that the keeper does not."""
    fields = dict(keeper.fields or {})
    added = [k for k, v in (donor.fields or {}).items()
             if str(v).strip() and not str(fields.get(k, "")).strip()]
    for key in added:
        fields[key] = donor.fields[key]
    if added:
        keeper.fields = fields
        notes.append("carried %d field(s) across: %s" % (len(added), ", ".join(sorted(added))))

    # The parameter tables hold the same readings; take the donor's whenever its text kept
    # more of its spacing, since that text is printed on the specsheet as-is.
    if donor.params and spacing_only(keeper.params, donor.params):
        keeper_text = sum(str(v).count(" ") for g in (keeper.params or {}).values()
                          if isinstance(g, dict) for v in g.values())
        donor_text = sum(str(v).count(" ") for g in (donor.params or {}).values()
                         if isinstance(g, dict) for v in g.values())
        donor_groups = sum(str(g).count(" ") for g in (donor.params or {}))
        keeper_groups = sum(str(g).count(" ") for g in (keeper.params or {}))
        if (donor_text, donor_groups) > (keeper_text, keeper_groups):
            keeper.params = donor.params
            notes.append("took the donor's parameter table (same readings, spacing intact)")

    if squash(keeper.category) == squash(donor.category) and \
            str(donor.category or "").count(" ") > str(keeper.category or "").count(" "):
        keeper.category = donor.category
        notes.append("took the donor's description line (same words, spacing intact)")

    # Only when the donor actually has a price and the keeper has none. Both being zero is
    # not a price worth carrying, and reporting it as one made the log say work had happened
    # where none had.
    if not keeper.base_price and donor.base_price:
        keeper.base_price = donor.base_price
        notes.append("took the donor's base price of %s" % donor.base_price)
    if not keeper.price_adders and donor.price_adders:
        keeper.price_adders = donor.price_adders
        notes.append("took the donor's price adders")


def plan(db):
    """[(keeper, [donors], why)] - every group this script is willing to touch."""
    groups = collections.defaultdict(list)
    for s in db.query(models.Specsheet).all():
        groups[identity(s)].append(s)

    jobs, skipped = [], []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        # Split into sets that genuinely specify the same thing. Anything on its own is a
        # distinct product that merely shares a name, and is left alone.
        buckets = []
        for s in rows:
            for bucket in buckets:
                if same_specification(bucket[0], s):
                    bucket.append(s)
                    break
            else:
                buckets.append([s])
        for bucket in buckets:
            if len(bucket) < 2:
                if len(rows) > 1:
                    skipped.append(bucket[0])
                continue
            authored = [s for s in bucket if authored_in_npd(s)]
            parsed = [s for s in bucket if not authored_in_npd(s)]
            if authored and parsed:
                # keep the parsed row: it is the one a BOQ and a verified price point at
                keeper = max(parsed, key=lambda s: (sum(references(db, s.id).values()),
                                                    better_spaced(s)))
                donors = [s for s in bucket if s is not keeper]
                why = "authored again in NPD over a row already in the library"
            else:
                keeper = max(bucket, key=lambda s: (sum(references(db, s.id).values()),
                                                    better_spaced(s)))
                donors = [s for s in bucket if s is not keeper]
                why = "the same PDF uploaded more than once, parsing identically"
            jobs.append((keeper, donors, why))
    return jobs, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    jobs, skipped = plan(db)

    print("groups to merge: %d" % len(jobs))
    print("rows that share a name but specify something different, left alone: %d"
          % len(skipped))
    print()

    removed = 0
    blocked = []
    for keeper, donors, why in jobs:
        print("%s  (%s)" % (keeper.product_name, why))
        print("   keeping  id %-5d  %s" % (keeper.id, (keeper.filename or "")[:44]))
        for donor in donors:
            refs = references(db, donor.id)
            notes = []
            merge_into(keeper, donor, notes)
            for note in notes:
                print("      %s" % note)
            # A price entry on the donor that the keeper already has word for word is the
            # same fact recorded twice; anything else is a figure somebody verified and is
            # not this script's to throw away.
            keeper_prices = {(str(sorted((p.config or {}).items())), float(p.price))
                             for p in (keeper.price_entries or [])}
            spare, duplicated = [], []
            for p in list(donor.price_entries or []):
                key = (str(sorted((p.config or {}).items())), float(p.price))
                (duplicated if key in keeper_prices else spare).append(p)
            if refs["boq lines"] or refs["config rules"] or spare:
                blocked.append((donor, refs, len(spare)))
                print("      NOT removing id %d - %s"
                      % (donor.id, ", ".join("%d %s" % (v, k) for k, v in refs.items() if v)
                         + (", %d price(s) the keeper does not have" % len(spare) if spare else "")))
                continue
            print("      removing id %-5d %s%s"
                  % (donor.id, (donor.filename or "")[:40],
                     "  (+%d duplicate price row(s))" % len(duplicated) if duplicated else ""))
            if not args.dry_run:
                for p in duplicated:
                    db.delete(p)
                for path in {donor.image_path} | {d.get("path") for d in (donor.diagrams or [])}:
                    if path and "npd_%d_" % donor.id in str(path):
                        Path(path).unlink(missing_ok=True)
                db.delete(donor)
            removed += 1
        print()

    print("rows removed: %d" % removed)
    if blocked:
        print("rows kept because something still points at them: %d" % len(blocked))

    if args.dry_run:
        db.rollback()
        print()
        print("--dry-run: nothing written.")
    else:
        db.commit()
        print()
        print("done.")
    db.close()


if __name__ == "__main__":
    main()
