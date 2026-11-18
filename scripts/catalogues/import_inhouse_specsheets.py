"""One-off import: loads every PDF under
"SampleInputs/InHouse Specification Sheets/" into the specsheet library.

That folder is organized into subfolders by category (Downlight, COB Strip, Exterior
Spot, ...), with several filenames repeated across subfolders (e.g. "FLARE MINI.pdf"
appears under more than one folder). None of that structure matters to how this script
classifies anything - it walks every .pdf file found anywhere under the folder and
runs each one through the exact same ingestion path a real browser upload uses
(app.main.upload_specsheets): every page is parsed independently
(pdf_parser.parse_specsheet_pages), typology is auto-classified from each page's own
parsed category text (typology_classifier.classify_typology), and each page is checked
against the *entire* existing library - not just this batch - via the same two exact-
match dedup fingerprints the upload endpoint uses (app.main._dedup_fingerprints),
skipping anything that's already there. Reusing that real code path, rather than
reimplementing it here, is what makes filename/folder irrelevance and dedup behavior
actually match what the Library tab's own upload button does. Safe to re-run.
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
import shutil
from pathlib import Path

from app.database import SessionLocal, IMAGES_DIR
from app import models
from app.main import _dedup_fingerprints, _unique_upload_dest, _discard_parsed_images
from app.pdf_parser import parse_specsheet_pages
from app.typology_classifier import classify_typology
from app.environment_classifier import classify_environment

SOURCE_DIR = _REPO_ROOT / "SampleInputs" / "InHouse Specification Sheets"


def main():
    pdf_paths = sorted(SOURCE_DIR.rglob("*.pdf"))
    print(f"Found {len(pdf_paths)} PDF file(s) under {SOURCE_DIR}")

    db = SessionLocal()
    fingerprint_index = {}
    for s in db.query(models.Specsheet).all():
        for fp in _dedup_fingerprints(s.category, s.product_name, s.series, s.family, s.variant, s.make, s.raw_text, s.fields, s.params, s.options):
            fingerprint_index.setdefault(fp, s)

    total_created = 0
    total_skipped = 0

    for src in pdf_paths:
        dest = _unique_upload_dest(src.name)
        shutil.copyfile(src, dest)

        any_new_from_this_file = False
        for parsed in parse_specsheet_pages(dest, IMAGES_DIR):
            fps = _dedup_fingerprints(
                parsed["category"], parsed["product_name"], parsed["series"], parsed["family"],
                parsed["variant"], parsed["make"], parsed["raw_text"], parsed["fields"], parsed["params"], parsed["options"],
            )
            existing = next((fingerprint_index[fp] for fp in fps if fp in fingerprint_index), None)
            if existing is not None:
                _discard_parsed_images(db, parsed)
                total_skipped += 1
                print(f"  skip (duplicate of #{existing.id} {existing.product_name!r}): {src.relative_to(SOURCE_DIR)} p{parsed['page_number']}")
                continue

            sheet = models.Specsheet(
                filename=parsed["filename"],
                page_number=parsed["page_number"],
                category=parsed["category"],
                typology=classify_typology(parsed["category"]),
                environment=classify_environment(parsed["category"]),
                product_name=parsed["product_name"],
                series=parsed["series"],
                family=parsed["family"],
                variant=parsed["variant"],
                make=parsed["make"],
                source_pdf_path=str(dest),
                image_path=parsed["image_path"],
                diagrams=parsed["diagrams"],
                raw_text=parsed["raw_text"],
                params=parsed["params"],
                fields=parsed["fields"],
                options=parsed["options"],
            )
            db.add(sheet)
            db.flush()
            for fp in fps:
                fingerprint_index.setdefault(fp, sheet)
            total_created += 1
            any_new_from_this_file = True
            label = sheet.typology or "Unclassified"
            print(f"  + [{label}] {src.relative_to(SOURCE_DIR)} p{parsed['page_number']}: {sheet.product_name!r}")

        if not any_new_from_this_file:
            dest.unlink(missing_ok=True)

    db.commit()
    print(f"\nDone. Created {total_created} specsheet(s), skipped {total_skipped} duplicate page(s).")


if __name__ == "__main__":
    main()
