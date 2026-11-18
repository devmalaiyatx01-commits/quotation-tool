"""One-off import: loads driver SKUs from SampleInputs/DRIVERS.pdf into the specsheet
library as typology "Driver".

DRIVERS.pdf is not a normal one-product-per-page datasheet - it's an inventory/stock
report table (191 SKUs across 9 pages). Critically, roughly half its rows (every
odd-numbered SKU, #1/#3/#5/...) have genuinely scrambled text in the PDF's own content
stream - confirmed by inspecting raw word positions, not a parsing bug here. Only the
~95 even-numbered rows, which extract cleanly via pdfplumber's table detection, are
imported; the corrupted rows are skipped rather than guessed at, since this data feeds
real client BOQs. Re-running this script is safe: every row is checked against the
same dedup fingerprint the upload endpoint uses (app.main._dedup_fingerprints) before
inserting, so it never creates duplicates.
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
import re
import shutil
from pathlib import Path

import pdfplumber

from app.database import SessionLocal, UPLOADS_DIR
from app import models
from app.main import _dedup_fingerprints

SOURCE_PDF = _REPO_ROOT / "SampleInputs" / "DRIVERS.pdf"

CONTROL_PATTERNS = [
    (re.compile(r"DALI\s*2\s*&\s*0-10V\s*DIMMABLE"), "DALI 2 & 0-10V"),
    (re.compile(r"DALI\s*DT8\s*DIMMABLE"), "DALI DT8"),
    (re.compile(r"4-IN-1\s*DIMMABLE"), "4-IN-1"),
    (re.compile(r"3IN1\s*DIMMABLE"), "3IN1"),
    (re.compile(r"PHASE-CUT\s*DIMMABLE"), "Phase-Cut"),
    (re.compile(r"0-10V\s*DIMMABLE"), "0-10V"),
    (re.compile(r"DALI\s*DIMMABLE"), "DALI"),
    (re.compile(r"NON\s*DIMMABLE"), "Non Dim"),
]
WATTAGE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*W\b")
IP_RE = re.compile(r"\bIP\d{2}\b")
VOLT_RE = re.compile(r"\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\s*V(?:\s*DC)?\b")


def _clean(text: str) -> str:
    """Collapses a table cell's line-wraps back into one readable string - a wrap right
    after a hyphen/slash is mid-token (join with nothing); any other wrap is between
    separate words (join with a space)."""
    text = re.sub(r"-\n", "-", text or "")
    text = re.sub(r"/\n", "/", text)
    text = text.replace("\n", " ")
    return " ".join(text.split())


def _parse_row(row: list, page_number: int) -> dict:
    num, product_code, brand, _series, model, _type, desc, nature = row[:8]
    available = row[10] if len(row) > 10 else ""
    desc_flat = _clean(desc)
    model_clean = _clean(model)
    brand_clean = _clean(brand or "")
    product_code_clean = _clean(product_code or "")

    control = ""
    for pat, label in CONTROL_PATTERNS:
        if pat.search(desc_flat):
            control = label
            break

    watt_matches = WATTAGE_RE.findall(desc_flat)
    wattage = f"{watt_matches[-1]}W" if watt_matches else ""

    ip_matches = IP_RE.findall(desc_flat)
    ip_rating = ip_matches[-1] if ip_matches else ""

    # Exclude a match immediately followed by "DIMMABLE" - that's "0-10V" naming the
    # dimming *protocol* (as in "0-10V DIMMABLE DRIVER"), not a voltage rating. Confirmed
    # this false-positive is real: e.g. "MEAN WELL LCM-40 0-10V DIMMABLE DRIVER 40W IP20"
    # has no actual DC voltage stated at all, and without this check "0-10V" was being
    # picked up as if it were one.
    volt_matches = [
        m.group(0).strip() for m in VOLT_RE.finditer(desc_flat)
        if not desc_flat[m.end():m.end() + 10].strip().startswith("DIMMABLE")
    ]
    input_voltage = volt_matches[-1] if volt_matches else ""

    # first line of the description is the short control-type label, e.g.
    # "DALI 2 & 0-10V DIMMABLE DRIVER" - used as this page's "category" text,
    # mirroring how every other specsheet's category is its own PDF-parsed title
    category = _clean((desc or "").split("\n")[0]) or "Driver"

    fields = {"wattage": wattage, "ip_rating": ip_rating, "control": control, "input_voltage": input_voltage}
    options = {k: [v] for k, v in fields.items() if v}

    return {
        "filename": SOURCE_PDF.name,
        "page_number": page_number,
        "category": category,
        "typology": "Driver",
        "product_name": model_clean or product_code_clean,
        "series": brand_clean,
        "family": "",
        "variant": "",
        "make": brand_clean,
        "raw_text": desc_flat,
        "params": {"STOCK INFO": {"PRODUCT CODE": product_code_clean, "NATURE": _clean(nature or ""), "AVAILABLE": str(available or "")}},
        "fields": fields,
        "options": options,
        "diagrams": [],
        "image_path": "",
    }


def main():
    if not SOURCE_PDF.exists():
        print(f"source PDF not found: {SOURCE_PDF}")
        return

    dest = UPLOADS_DIR / SOURCE_PDF.name
    if not dest.exists():
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_PDF, dest)
        print(f"copied source PDF to {dest}")

    parsed_rows = []
    with pdfplumber.open(SOURCE_PDF) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            for table in page.extract_tables():
                for row in table:
                    if not row or not row[0] or row[0] == "#" or "TOTAL" in str(row[0]):
                        continue
                    parsed_rows.append(_parse_row(row, page_idx))

    print(f"{len(parsed_rows)} cleanly-extractable driver rows found in {SOURCE_PDF.name}")

    db = SessionLocal()
    try:
        fingerprint_index = {}
        for s in db.query(models.Specsheet).all():
            for fp in _dedup_fingerprints(s.category, s.product_name, s.series, s.family, s.variant, s.make, s.raw_text, s.fields, s.params, s.options):
                fingerprint_index.setdefault(fp, s)

        created, skipped = 0, 0
        for parsed in parsed_rows:
            fps = _dedup_fingerprints(
                parsed["category"], parsed["product_name"], parsed["series"], parsed["family"],
                parsed["variant"], parsed["make"], parsed["raw_text"], parsed["fields"], parsed["params"], parsed["options"],
            )
            if any(fp in fingerprint_index for fp in fps):
                skipped += 1
                continue
            sheet = models.Specsheet(
                filename=parsed["filename"], page_number=parsed["page_number"], category=parsed["category"],
                typology=parsed["typology"], product_name=parsed["product_name"], series=parsed["series"],
                family=parsed["family"], variant=parsed["variant"], make=parsed["make"],
                source_pdf_path=str(dest), image_path=parsed["image_path"], diagrams=parsed["diagrams"],
                raw_text=parsed["raw_text"], params=parsed["params"], fields=parsed["fields"], options=parsed["options"],
            )
            db.add(sheet)
            db.flush()
            for fp in fps:
                fingerprint_index.setdefault(fp, sheet)
            created += 1
        db.commit()
        print(f"{created} driver specsheets created, {skipped} already present (skipped)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
