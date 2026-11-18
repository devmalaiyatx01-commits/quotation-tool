"""Loads a payload from export_specsheets_for_deploy.py onto a server, as new rows.

Additive by design. The server has live data - BOQ items and price entries referencing
specsheets by id, and users - and none of it is touched: this only inserts rows for the makes
in the payload, after refusing to run if the server already holds any of them. Nothing here
updates or deletes.

Paths arrive as bare filenames because an absolute path from the exporting machine means
nothing here; they are rebuilt against this installation's own data directory. A file that
did not make it across leaves the row without an image rather than pointing at nothing.

    python scripts/catalogues/import_specsheets_from_deploy.py deploy_payload.json          # dry run
    python scripts/catalogues/import_specsheets_from_deploy.py deploy_payload.json --commit
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

from app.database import SessionLocal, IMAGES_DIR, UPLOADS_DIR
from app import models

COLUMNS = ["filename", "category", "typology", "product_name", "series", "family", "variant",
           "environment", "page_number", "make", "raw_text", "params", "fields", "options",
           "list_price", "list_price_by_wattage", "source_url"]


def main():
    commit = "--commit" in sys.argv
    path = next((a for a in sys.argv[1:] if not a.startswith("-")), "deploy_payload.json")
    payload = json.load(open(path, encoding="utf-8"))
    rows, rules = payload["rows"], payload["rules"]
    makes = sorted({r["make"] for r in rows})

    db = SessionLocal()
    existing = (db.query(models.Specsheet)
                .filter(models.Specsheet.make.in_(makes)).count())
    print(f"payload      : {len(rows)} row(s), {len(rules)} rule(s), makes {makes}")
    print(f"already here : {existing} row(s) for those makes")
    if existing:
        db.close()
        raise SystemExit(
            "ABORTED: this server already holds rows for those makes. This script only ever "
            "adds, so re-running it would duplicate the catalogue. Remove them deliberately "
            "first if that is what you want.")

    have_img = sum(1 for r in rows if r["image_file"]
                   and (IMAGES_DIR / r["image_file"]).exists())
    want_img = sum(1 for r in rows if r["image_file"])
    print(f"images       : {have_img} of {want_img} present in {IMAGES_DIR}")
    have_pdf = sum(1 for r in rows if r["source_pdf_file"]
                   and (UPLOADS_DIR / r["source_pdf_file"]).exists())
    want_pdf = sum(1 for r in rows if r["source_pdf_file"])
    print(f"source PDFs  : {have_pdf} of {want_pdf} present in {UPLOADS_DIR}")
    print(f"typologies   : {dict(Counter(r['typology'] for r in rows).most_common(6))}")

    if not commit:
        db.close()
        print("\nDRY RUN - nothing written. Re-run with --commit.")
        return

    id_for_key = {}
    for r in rows:
        s = models.Specsheet(**{c: r[c] for c in COLUMNS})
        img = IMAGES_DIR / r["image_file"] if r["image_file"] else None
        if img and img.exists():
            s.image_path = str(img)
            label = (r.get("diagram_labels") or ["Product Image"])[0]
            s.diagrams = [{"path": str(img), "width": 0, "height": 0, "label": label}]
        if r["source_pdf_url"]:
            s.source_pdf_path = r["source_pdf_url"]
        elif r["source_pdf_file"] and (UPLOADS_DIR / r["source_pdf_file"]).exists():
            s.source_pdf_path = str(UPLOADS_DIR / r["source_pdf_file"])
        db.add(s)
        db.flush()                      # so the row has its id for the rule remap
        id_for_key[r["_key"]] = s.id
    db.commit()

    written = 0
    for rule in rules:
        sid = id_for_key.get(rule["_key"])
        if sid is None:
            continue
        db.add(models.ConfigRule(
            typology=rule["typology"], specsheet_id=sid, triggers=rule["triggers"],
            target_fields=rule["target_fields"], action=rule["action"],
            affected_options=rule["affected_options"]))
        written += 1
    db.commit()

    total = db.query(models.Specsheet).count()
    print(f"\nCOMMITTED. {len(id_for_key)} row(s) and {written} rule(s) added; "
          f"{total} specsheets now.")
    db.close()


if __name__ == "__main__":
    main()
