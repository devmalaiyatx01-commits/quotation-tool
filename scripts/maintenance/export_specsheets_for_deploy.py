"""Exports whole makes - rows, their per-product rules and the names of their images - so a
deployment can be given a catalogue it does not have.

Why not just restore a dump of the table: the server has live data of its own. Twelve BOQs,
162 items and 361 price entries reference specsheets by id there, and those ids do not match
this machine's - the two libraries were built by separate import runs. Replacing the table
would silently orphan every one of those references. So this moves whole makes across as NEW
rows, lets the server assign its own ids, and remaps the config rules onto them by position.

Paths are exported as bare filenames. An absolute path from this machine means nothing on the
server, and `image_path` here is a Windows path; the importer rebuilds it against the
server's own data directory.

    python scripts/maintenance/export_specsheets_for_deploy.py Exenia Lumenpulse -o deploy_payload.json

Read-only. The companion is import_specsheets_from_deploy.py, run on the server.
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

from app.database import SessionLocal
from app import models

COLUMNS = ["filename", "category", "typology", "product_name", "series", "family", "variant",
           "environment", "page_number", "make", "raw_text", "params", "fields", "options",
           "list_price", "list_price_by_wattage", "source_url"]


def main():
    argv = sys.argv[1:]
    out = "deploy_payload.json"
    if "-o" in argv:
        i = argv.index("-o")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]      # so the path is not mistaken for a make
    makes = [a for a in argv if not a.startswith("-")] or ["Exenia", "Lumenpulse"]

    db = SessionLocal()
    rows = (db.query(models.Specsheet)
            .filter(models.Specsheet.make.in_(makes))
            .order_by(models.Specsheet.id).all())
    by_id = {s.id: i for i, s in enumerate(rows)}

    payload_rows, images, pdfs = [], set(), set()
    for i, s in enumerate(rows):
        d = {c: getattr(s, c) for c in COLUMNS}
        d["_key"] = i
        # Bare filenames: the server rebuilds these against its own data directory.
        d["image_file"] = os.path.basename(s.image_path) if s.image_path else ""
        if s.image_path:
            images.add(s.image_path)
        src = s.source_pdf_path or ""
        if src.startswith("http"):
            d["source_pdf_url"] = src
            d["source_pdf_file"] = ""
        elif src:
            d["source_pdf_url"] = ""
            d["source_pdf_file"] = os.path.basename(src)
            pdfs.add(src)
        else:
            d["source_pdf_url"] = ""
            d["source_pdf_file"] = ""
        d["diagram_labels"] = [dg.get("label", "Product Image") for dg in (s.diagrams or [])]
        payload_rows.append(d)

    rules = []
    for r in (db.query(models.ConfigRule)
              .filter(models.ConfigRule.specsheet_id.in_(list(by_id))).all()):
        rules.append({"_key": by_id[r.specsheet_id], "typology": r.typology,
                      "triggers": r.triggers, "target_fields": r.target_fields,
                      "action": r.action, "affected_options": r.affected_options})

    payload = {"makes": makes, "rows": payload_rows, "rules": rules}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)

    print(f"makes exported : {makes}")
    print(f"rows           : {len(payload_rows)}  {dict(Counter(r['make'] for r in payload_rows))}")
    print(f"config rules   : {len(rules)}")
    print(f"image files    : {len(images)}")
    print(f"source PDFs    : {len(pdfs)}")
    print(f"written to     : {out}  ({os.path.getsize(out) / 1e6:.1f} MB)")

    with open(out + ".files", "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(images | pdfs)))
    print(f"file list      : {out}.files")
    db.close()


if __name__ == "__main__":
    main()
