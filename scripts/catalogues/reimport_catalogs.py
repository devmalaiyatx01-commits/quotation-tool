"""One-off fixup: the 7 "...Series.pdf" catalog uploads were imported before this
app parsed multi-page PDFs correctly (each got saved as ONE specsheet from only
its first page, with every page's diagrams pooled into that one entry's gallery).
Deletes those wrong entries and re-imports each source PDF (already on disk in
data/uploads/) with parse_specsheet_pages, producing one correctly-classified
specsheet per page. Confirmed safe to delete: no BOQ item references any of them
(see chat history) - re-run this only if that's still true for your data.
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
from app.database import SessionLocal, IMAGES_DIR
from app import models
from app.pdf_parser import parse_specsheet_pages
from app.typology_classifier import classify_typology

CATALOG_FILENAMES = [
    "Curve Series.pdf",
    "Ember Series.pdf",
    "Meridian Series.pdf",
    "Nebel Series.pdf",
    "Nyx Series.pdf",
    "Ozone Series.pdf",
    "Vesper Series.pdf",
]


def main():
    db = SessionLocal()
    try:
        old = db.query(models.Specsheet).filter(models.Specsheet.filename.in_(CATALOG_FILENAMES)).all()
        for s in old:
            referenced = db.query(models.BOQItem).filter(models.BOQItem.specsheet_id == s.id).count()
            if referenced:
                print(f"REFUSING to touch {s.filename!r} (id={s.id}) - {referenced} BOQ item(s) reference it. Skipping entirely.")
                return
        for s in old:
            print(f"deleting old entry: {s.filename!r} -> {s.product_name!r} (id={s.id})")
            db.delete(s)
        db.commit()

        added = 0
        for filename in CATALOG_FILENAMES:
            source = None
            for s in old:
                if s.filename == filename:
                    source = s.source_pdf_path
                    break
            if not source:
                print(f"skip {filename!r} - no prior record, don't know its path")
                continue
            from pathlib import Path
            if not Path(source).exists():
                print(f"skip {filename!r} - source PDF not found at {source}")
                continue
            for parsed in parse_specsheet_pages(Path(source), IMAGES_DIR):
                sheet = models.Specsheet(
                    filename=parsed["filename"],
                    page_number=parsed["page_number"],
                    category=parsed["category"],
                    typology=classify_typology(parsed["category"]),
                    product_name=parsed["product_name"],
                    series=parsed["series"],
                    family=parsed["family"],
                    variant=parsed["variant"],
                    make=parsed["make"],
                    source_pdf_path=source,
                    image_path=parsed["image_path"],
                    diagrams=parsed["diagrams"],
                    raw_text=parsed["raw_text"],
                    params=parsed["params"],
                    fields=parsed["fields"],
                    options=parsed["options"],
                )
                db.add(sheet)
                added += 1
                print(f"  imported page {parsed['page_number']}: {parsed['product_name']!r} "
                      f"(series={parsed['series']!r} family={parsed['family']!r} variant={parsed['variant']!r} "
                      f"typology={sheet.typology!r})")
        db.commit()
        print(f"\nDone. {added} specsheet(s) imported from {len(CATALOG_FILENAMES)} catalog PDF(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
