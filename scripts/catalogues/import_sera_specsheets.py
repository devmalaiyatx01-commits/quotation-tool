"""One-off import: loads the 6 SERA PDFs under "SampleInputs/InHouse Specification
Sheets/Flexible Lens Grazer/" into the specsheet library, all under the real
typology "Flexible LED Lens Grazer".

Every one of these PDFs' own title literally reads "Flexible LED Lens Grazer
( Top/3D/Side Bend )" (checked directly - not invented) - a genuine, distinct
product type, not the broader "Wall Grazer / Wall Washer" typology
classify_typology() would otherwise guess purely because "grazer" matches first
in TYPOLOGY_KEYWORDS. Per direct instruction, given an explicit typology here
instead - same override the Library upload panel's own new typology picker now
offers a human (see the `typology` param on POST /api/specsheets/upload).

Reuses the exact same ingestion path a real browser upload takes
(app.main.upload_specsheets): each page parsed independently
(pdf_parser.parse_specsheet_pages), checked against the *entire* existing
library via the same two exact-match dedup fingerprints the upload endpoint
uses (app.main._dedup_fingerprints). Safe to re-run.
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
from app.environment_classifier import classify_environment

SOURCE_DIR = _REPO_ROOT / "SampleInputs" / "InHouse Specification Sheets" / "Flexible Lens Grazer"
TYPOLOGY = "Flexible LED Lens Grazer"


def main():
    pdf_paths = sorted(SOURCE_DIR.glob("*.pdf"))
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
                print(f"  skip (duplicate of #{existing.id} {existing.product_name!r}): {src.name} p{parsed['page_number']}")
                continue

            assert "Flexible LED Lens Grazer" in parsed["category"], \
                f"{src.name}: expected category to confirm this typology, got {parsed['category']!r}"

            sheet = models.Specsheet(
                filename=parsed["filename"],
                page_number=parsed["page_number"],
                category=parsed["category"],
                typology=TYPOLOGY,
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
            print(f"  + [{TYPOLOGY}] {src.name} p{parsed['page_number']}: {sheet.product_name!r}")

        if not any_new_from_this_file:
            dest.unlink(missing_ok=True)

    db.commit()
    print(f"\nDone. Created {total_created} specsheet(s), skipped {total_skipped} duplicate page(s).")


if __name__ == "__main__":
    main()
