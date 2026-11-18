"""Applies a payload from export_row_updates_for_deploy.py to the rows this server holds.

Updates in place, matched on (source_url, variant). Nothing is inserted and nothing is
deleted here except by --retire-unmatched, which is opt-in and refuses to touch a row that
anything points at.

What this will not do, by construction:

  - It never writes list_price or list_price_by_wattage. Prices are set here, not there.
  - It never touches users, BOQs, BOQ items, price entries or their ids.
  - A row in the payload that this server does not have is REPORTED, not inserted -
    inserting would be export_specsheets_for_deploy.py's job, and quietly doing it here
    would hide a mismatch between the two catalogues.
  - An image named in the payload that is not on disk here leaves the row's existing picture
    alone rather than pointing it at a file that does not exist.

    python scripts/maintenance/apply_row_updates_on_deploy.py row_updates.json                    # dry run
    python scripts/maintenance/apply_row_updates_on_deploy.py row_updates.json --commit
    python scripts/maintenance/apply_row_updates_on_deploy.py row_updates.json --commit --retire-unmatched
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
import json
import os
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal, IMAGES_DIR
from app import models

COLUMNS = ["filename", "category", "typology", "product_name", "series", "family", "variant",
           "environment", "page_number", "raw_text", "params", "fields", "options"]


def key(source_url, variant):
    return ((source_url or "").strip(), (variant or "").strip())


def main():
    commit = "--commit" in sys.argv
    retire = "--retire-unmatched" in sys.argv
    path = next((a for a in sys.argv[1:] if not a.startswith("-")), "row_updates.json")
    payload = json.load(open(path, encoding="utf-8"))
    rows, makes = payload["rows"], payload["makes"]
    by_key = {key(r["source_url"], r["variant"]): r for r in rows}

    db = SessionLocal()
    here = db.query(models.Specsheet).filter(models.Specsheet.make.in_(makes)).all()
    print(f"payload   : {len(rows)} row(s), makes {makes}")
    print(f"this server: {len(here)} row(s) for those makes")

    matched, missing_here, unmatched_here = [], [], []
    seen = set()
    for s in here:
        rec = by_key.get(key(s.source_url, s.variant))
        if rec:
            matched.append((s, rec))
            seen.add(key(s.source_url, s.variant))
        else:
            unmatched_here.append(s)
    missing_here = [r for k, r in by_key.items() if k not in seen]

    print(f"   matched and will be updated : {len(matched)}")
    print(f"   in the payload but not here : {len(missing_here)}   (reported, never inserted)")
    print(f"   here but not in the payload : {len(unmatched_here)}")
    for r in missing_here[:5]:
        print(f"      payload-only: {r['product_name'][:40]!r} {r['source_url']}")

    # Whether each unmatched row could be retired - checked, and reported, either way.
    retirable, held = [], []
    for s in unmatched_here:
        refs = db.query(models.BOQItem).filter(models.BOQItem.specsheet_id == s.id).count()
        prices = db.query(models.PriceEntry).filter(
            models.PriceEntry.specsheet_id == s.id).count()
        (held if (refs or prices) else retirable).append((s, refs, prices))
    for s, refs, prices in held:
        print(f"      KEEPING #{s.id} {s.product_name[:34]!r}: {refs} BOQ item(s), "
              f"{prices} price(s) reference it")
    for s, _, _ in retirable[:6]:
        print(f"      retirable: #{s.id} {s.product_name[:44]!r}")
    if retirable and not retire:
        print(f"   {len(retirable)} row(s) could be retired - pass --retire-unmatched to do it")

    img_ok = sum(1 for _, r in matched
                 if r.get("image_name") and (IMAGES_DIR / r["image_name"]).exists())
    img_missing = sum(1 for _, r in matched
                      if r.get("image_name") and not (IMAGES_DIR / r["image_name"]).exists())
    print(f"   images: {img_ok} present on this server, {img_missing} named but not on disk "
          f"(those rows keep the picture they have)")

    if not commit:
        db.close()
        print("\nDRY RUN - nothing written. Re-run with --commit.")
        return

    changed = 0
    for s, rec in matched:
        for c in COLUMNS:
            setattr(s, c, rec[c])
        name = rec.get("image_name") or ""
        if name and (IMAGES_DIR / name).exists():
            s.image_path = str(IMAGES_DIR / name)
            diagrams = [{"path": str(IMAGES_DIR / d), "width": 0, "height": 0,
                         "label": "Product Image" if i == 0 else f"Additional Diagram {i+1}"}
                        for i, d in enumerate(rec.get("diagram_names") or [])
                        if (IMAGES_DIR / d).exists()]
            s.diagrams = diagrams or [{"path": s.image_path, "width": 0, "height": 0,
                                       "label": "Product Image"}]
        changed += 1
    db.commit()
    print(f"\nupdated {changed} row(s) in place.")

    if retire and retirable:
        removed = 0
        for s, _, _ in retirable:
            # Re-checked at write time, not trusted from the survey above.
            if db.query(models.BOQItem).filter(models.BOQItem.specsheet_id == s.id).count():
                continue
            if db.query(models.PriceEntry).filter(
                    models.PriceEntry.specsheet_id == s.id).count():
                continue
            for r in db.query(models.ConfigRule).filter(
                    models.ConfigRule.specsheet_id == s.id).all():
                db.delete(r)
            db.flush()
            db.delete(s)
            db.flush()
            removed += 1
        db.commit()
        print(f"retired {removed} row(s) that nothing referenced.")

    total = db.query(models.Specsheet).filter(models.Specsheet.make.in_(makes)).count()
    print(f"{makes} now: {total} row(s)")
    db.close()


if __name__ == "__main__":
    main()
