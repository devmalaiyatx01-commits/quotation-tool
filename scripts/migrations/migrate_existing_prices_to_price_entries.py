"""One-off migration: copies the 27 already-verified prices out of
Specsheet.list_price/list_price_by_wattage (the EMBER and NYX backfills) into real
PriceEntry rows, status="locked" - these were already hand-confirmed (see
backfill_ember_list_price.py / backfill_nyx_list_price.py's own docstrings for how), so
they carry that confirmed status straight over rather than re-landing as "open".

The two old columns are left in place on Specsheet, unused from here on - not dropped,
to avoid a destructive migration for no real benefit (see PriceEntry's own docstring in
app/models.py for why this table replaces them going forward).

Safe to re-run: skips any specsheet that already has a PriceEntry from this exact source
file, so re-running after this script already ran once is a no-op, not a duplicate.
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

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models

EMBER_SOURCE = ("Downlights and drivers for all downlights.xlsx", "Downlights")
NYX_SOURCE = ("NYX Pricing.pdf", "")


def main():
    db = SessionLocal()
    rows = (
        db.query(models.Specsheet)
        .filter((models.Specsheet.list_price.isnot(None)) | (models.Specsheet.list_price_by_wattage.isnot(None)))
        .all()
    )
    print(f"{len(rows)} specsheet row(s) with legacy pricing found\n")

    created = 0
    for s in rows:
        source_file, source_sheet = EMBER_SOURCE if s.series == "EMBER" else NYX_SOURCE
        already = (
            db.query(models.PriceEntry)
            .filter(models.PriceEntry.specsheet_id == s.id, models.PriceEntry.source_file == source_file)
            .count()
        )
        if already:
            print(f"  #{s.id} {s.product_name!r} - already has a PriceEntry from {source_file!r}, skipping")
            continue

        if s.list_price_by_wattage:
            for wattage, price in s.list_price_by_wattage.items():
                entry = models.PriceEntry(
                    specsheet_id=s.id, source_file=source_file, source_sheet=source_sheet,
                    source_label=f"{s.product_name} ({s.variant})", config={"wattage": wattage},
                    price=price, status="locked",
                )
                db.add(entry)
                created += 1
                print(f"  #{s.id} {s.product_name!r} ({s.variant!r}) wattage={wattage!r} -> {price!r} [locked]")
        elif s.list_price is not None:
            entry = models.PriceEntry(
                specsheet_id=s.id, source_file=source_file, source_sheet=source_sheet,
                source_label=f"{s.product_name} ({s.variant})", config={},
                price=s.list_price, status="locked",
            )
            db.add(entry)
            created += 1
            print(f"  #{s.id} {s.product_name!r} ({s.variant!r}) -> {s.list_price!r} [locked]")

    db.commit()
    print(f"\nDone. Created {created} PriceEntry row(s).")


if __name__ == "__main__":
    main()
