"""One-time backfill: corrects `wattage` (fields/options/params/raw_text) for DuraGreen
specsheets already imported before import_duragreen_catalog.py's PDF-vs-modal wattage
cross-check existed - see cross_check_wattage_against_pdf() there for the full
reasoning (DuraGreen's own website modal and its own downloadable PDF don't always
agree on driver wattage; confirmed via a real product, "110 Curve Trimless Fixed",
where the modal listed a "DC 36V 300mA 12W" option the PDF simply doesn't have,
verified wrong via the PDF's own page-2 SKU code table).

A fresh import now applies this at scrape time, so this script only needs to run once,
against rows created before that fix landed. Prints every row before writing. Safe to
re-run - a row already matching, or using an ambiguous "W*N" driver notation, is left
alone either way."""
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

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models
from import_duragreen_catalog import cross_check_wattage_against_pdf


def main():
    db = SessionLocal()
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == "Duragreen").filter(models.Specsheet.source_pdf_path != "").all()
    print(f"{len(rows)} row(s) with a real PDF to check\n")

    updated = 0
    ambiguous = []
    for s in rows:
        card = {"fields": dict(s.fields or {}), "options": dict(s.options or {}), "params": dict(s.params or {}), "raw_text": s.raw_text or ""}
        try:
            result = cross_check_wattage_against_pdf(card, s.source_pdf_path)
        except Exception as e:
            print(f"  #{s.id} {s.product_name!r}: could not read PDF ({e}) - left as-is")
            continue

        if result == "ambiguous":
            ambiguous.append(s)
            continue
        if not result:
            continue

        before, after = result
        print(f"  #{s.id} {s.product_name!r} ({s.series})")
        print(f"      wattage options: {before[1].get('wattage')} -> {after[1].get('wattage')}")

        s.fields = card["fields"]
        s.options = card["options"]
        s.params = card["params"]
        s.raw_text = card["raw_text"]
        updated += 1

    db.commit()
    print(f"\nDone. Updated {updated} row(s).")
    if ambiguous:
        print(f"\n{len(ambiguous)} row(s) skipped - PDF uses a 'W*N' multiplier notation, needs a human to check manually:")
        for s in ambiguous:
            print(f"  #{s.id} {s.product_name!r} ({s.series}) - {s.source_pdf_path}")


if __name__ == "__main__":
    main()
