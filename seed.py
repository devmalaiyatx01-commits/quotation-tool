"""Loads the sample specsheet PDFs (SampleInputs/SampleSpecsheets) into the local
database so there's something to test the BOQ builder against on first run.
Safe to re-run - skips files that are already in the library.
"""
from pathlib import Path

from app.database import Base, engine, SessionLocal, UPLOADS_DIR, IMAGES_DIR
from app import models
from app.pdf_parser import parse_specsheet_pages
from app.typology_classifier import classify_typology

SAMPLE_DIR = Path(__file__).resolve().parent / "SampleInputs" / "SampleSpecsheets"

# Typology (business classification, e.g. Configure.gs's "Architectural Luminaires"
# field) is NOT the same thing as `category` (the literal product-type text parsed
# off one PDF's title block). A typology groups multiple products - notice Filo S 05
# and Vena S 10 are two different specsheets/categories that both belong to the same
# "Flexible Strip Light" typology. Typology can't be reliably auto-derived from a
# PDF the way `category` is - it's a curatorial/business classification decision, so
# it's assigned here explicitly rather than parsed. When new specsheets get uploaded,
# assign their typology from the Specsheet Library detail view.
TYPOLOGY_BY_PRODUCT_NAME = {
    "FLARE -Mini": "Spotlight",
    "GLANCE 1000": "Wall Grazer / Wall Washer",
    "Filo S 05 - LL 106001": "Flexible Strip Light",
    "Vena S 10 - LL 106001": "Flexible Strip Light",
    "MIST 1010 TV": "Flexible Neon / LED Band",
    "NYX -Midi": "Downlight",
    "RAYON Spot –LL103002": "Track Light",
}

# No starter config rules are seeded (2026-08-13, previously removed). The earlier
# placeholder set here read as real lighting constraints ("wet-rated drivers don't
# carry a DALI bus") but was invented example data, never verified against an actual
# manufacturer spec - it reached production undistinguished from a real rule and
# silently hid real product options (see TUTORIAL.md 4.10/4.13-era case studies).
# Config rules should only ever be entered deliberately, by someone who has actually
# confirmed the constraint against the real product - there is no shortcut for that.


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = {s.filename for s in db.query(models.Specsheet).all()}
        pdfs = sorted(SAMPLE_DIR.glob("*.pdf"))
        if not pdfs:
            print(f"No sample PDFs found under {SAMPLE_DIR}")
            return
        added = 0
        for pdf in pdfs:
            if pdf.name in existing:
                print(f"skip (already imported): {pdf.name}")
                continue
            dest = UPLOADS_DIR / pdf.name
            dest.write_bytes(pdf.read_bytes())
            for parsed in parse_specsheet_pages(dest, IMAGES_DIR):
                sheet = models.Specsheet(
                    filename=parsed["filename"],
                    page_number=parsed["page_number"],
                    category=parsed["category"],
                    typology=TYPOLOGY_BY_PRODUCT_NAME.get(parsed["product_name"]) or classify_typology(parsed["category"]),
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
                added += 1
                print(f"imported: {pdf.name} (page {parsed['page_number']}) -> {parsed['product_name']}")
        db.commit()
        print(f"\nDone. {added} new specsheet(s) imported.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
