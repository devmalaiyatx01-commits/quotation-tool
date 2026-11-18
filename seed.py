"""Loads the sample specsheet PDFs (SampleInputs/SampleSpecsheets) into the local
database so there's something to test the BOQ builder against on first run.
Safe to re-run - skips files that are already in the library.
"""
import json
import shutil
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


EXAMPLES = Path(__file__).resolve().parent / "seed_data" / "example_products.json"
EXAMPLE_IMAGES = EXAMPLES.parent / "images"

# Prototype only: a demo BOQ (floor, room, product, quantity, unit) built from the examples.
SAMPLE_BOQ = [
    ("Ground Floor", "Living Room", "CURVE -Midi", 8, "Nos."),
    ("Ground Floor", "Living Room", "RAYON Linear", 2, "Nos."),
    ("Ground Floor", "Living Room", "Vena S 10", 12, "Mtr."),
    ("Ground Floor", "Kitchen", "SOLARA Midi", 4, "Nos."),
    ("Ground Floor", "Kitchen", "Filo S 05", 6, "Mtr."),
    ("Outdoor", "Garden", "FLARE -Mini", 6, "Nos."),
    ("Outdoor", "Garden", "DAWN24 -Midi", 8, "Nos."),
    ("Outdoor", "Facade", "GLANCE 1000", 5, "Nos."),
    ("Outdoor", "Pool", "Aura -Midi", 4, "Nos."),
]


def load_example_products(db) -> int:
    """Prototype only: real products parsed from manufacturer spec sheets, up to three per
    typology, with their specifications and pictures and no prices. Loaded once, into an empty
    library, so a library someone has filled is never touched."""
    if not EXAMPLES.exists() or db.query(models.Specsheet).count():
        return 0
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    rows = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    for row in rows:
        diagrams = []
        for pic in row.pop("images", []):
            target = IMAGES_DIR / pic["file"]
            if not target.exists():
                shutil.copy2(EXAMPLE_IMAGES / pic["file"], target)
            diagrams.append({"path": str(target), "width": pic["width"], "height": pic["height"],
                             "label": pic["label"]})
        if diagrams:
            row["diagrams"] = diagrams
            row["image_path"] = diagrams[0]["path"]
        db.add(models.Specsheet(**row))
    db.commit()
    print(f"loaded {len(rows)} example products with pictures (specifications only, no prices)")
    return len(rows)


def _pick(options: dict, key: str, prefer: str = "") -> str:
    """One value a designer would choose from a product's option list."""
    values = (options or {}).get(key) or []
    if prefer and prefer in values:
        return prefer
    return values[0] if values else ""


def load_sample_boq() -> bool:
    """Prototype only: a demo BOQ built through the app's own API, so every line is exactly what
    the screens would create. Skipped when sign-in is on or when any BOQ already exists."""
    from fastapi.testclient import TestClient
    from app.main import PROTOTYPE_NO_AUTH, app

    if not PROTOTYPE_NO_AUTH:
        print("sample BOQ skipped: sign-in is on (PROTOTYPE_AUTH=on)")
        return False
    with TestClient(app) as client:
        if client.get("/api/boqs").json():
            return False
        sheets = client.get("/api/specsheets").json()
        sheets = sheets if isinstance(sheets, list) else sheets.get("items", [])
        by_name = {}
        for s in sheets:
            by_name.setdefault(s.get("product_name", ""), s)
        # The library list is a summary; the option lists come from the example data itself.
        options_by_name = {}
        if EXAMPLES.exists():
            for row in json.loads(EXAMPLES.read_text(encoding="utf-8")):
                options_by_name.setdefault(row.get("product_name", ""), row.get("options") or {})
        boq = client.post("/api/boqs", json={"project_name": "Sample Residence (demo)", "client": "Demo Client",
                                             "project_code": "DEMO-001", "mode": "detailed"}).json()
        floors, rooms, added = {}, {}, 0
        for floor, room, name, qty, unit in SAMPLE_BOQ:
            sheet = next((s for n, s in by_name.items() if n.startswith(name)), None)
            if sheet is None:
                continue
            if floor not in floors:
                floors[floor] = client.post(f"/api/boqs/{boq['id']}/floors", json={"name": floor}).json()["id"]
            if (floor, room) not in rooms:
                rooms[(floor, room)] = client.post(f"/api/floors/{floors[floor]}/rooms", json={"name": room}).json()["id"]
            opts = sheet.get("options") or options_by_name.get(sheet.get("product_name", ""), {})
            added += 1
            client.post(f"/api/rooms/{rooms[(floor, room)]}/items", json={
                "specsheet_id": sheet["id"], "item_code": f"L{added}", "make": sheet.get("make", ""),
                "description": f"{sheet.get('product_name', '')}: {sheet.get('category', '')}",
                "finish": _pick(opts, "finish"), "wattage": _pick(opts, "wattage"),
                "beam": _pick(opts, "beam_angle"), "led_color": _pick(opts, "cct", "3000K"),
                "qty": qty, "unit": unit})
        print(f"created sample BOQ '{boq.get('project_name', 'Sample Residence (demo)')}' with {added} lines (no prices)")
        return True


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        load_example_products(db)
        load_sample_boq()
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
