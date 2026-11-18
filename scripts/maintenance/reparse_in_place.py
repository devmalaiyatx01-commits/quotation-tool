"""Re-parses every specsheet currently in the DB from its source PDF with the
CURRENT app/pdf_parser.py, updating fields/options/params/raw_text/category/
product_name/series/family/variant IN PLACE (same id, same row) rather than
deleting and recreating - so any BOQ item referencing a specsheet keeps
working, and nothing needs re-classifying by hand afterward.

Written specifically to recover from the _undouble bug: an earlier version
of that function used a fuzzy "mostly looks doubled" threshold that silently
corrupted some ordinary values (e.g. "2700K" -> "270K") on some specsheets
but not others, non-deterministically depending on surrounding text length -
meaning there was no reliable way to know which rows were affected without
just re-deriving everything fresh. Safe to re-run any time the parser
changes and you want existing library entries to reflect it.
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
from pathlib import Path

from app.database import SessionLocal, IMAGES_DIR
from app import models
from app.pdf_parser import parse_specsheet_pages


def main():
    db = SessionLocal()
    try:
        sheets = db.query(models.Specsheet).all()
        by_source = {}
        for s in sheets:
            by_source.setdefault(s.source_pdf_path, []).append(s)

        updated, skipped = 0, 0
        for source_path, rows in by_source.items():
            if not source_path or not Path(source_path).exists():
                print(f"skip - source PDF missing: {source_path!r} ({len(rows)} row(s))")
                skipped += len(rows)
                continue
            parsed_pages = {p["page_number"]: p for p in parse_specsheet_pages(Path(source_path), IMAGES_DIR)}
            for s in rows:
                parsed = parsed_pages.get(s.page_number or 1)
                if not parsed:
                    print(f"skip id={s.id} ({s.product_name!r}) - no matching page {s.page_number} in {source_path!r}")
                    skipped += 1
                    continue
                before = (s.fields or {}).get("cct"), (s.fields or {}).get("wattage")
                s.category = parsed["category"]
                s.product_name = parsed["product_name"]
                s.series = parsed["series"]
                s.family = parsed["family"]
                s.variant = parsed["variant"]
                s.raw_text = parsed["raw_text"]
                s.params = parsed["params"]
                s.fields = parsed["fields"]
                s.options = parsed["options"]
                # diagrams/image_path intentionally left alone - re-extracting would rewrite
                # the same PNG files, no need to churn them for a text-only bugfix
                after = (s.fields or {}).get("cct"), (s.fields or {}).get("wattage")
                marker = "  <-- CCT/WATTAGE CHANGED" if before != after else ""
                print(f"updated id={s.id} {s.product_name!r}: cct/wattage {before} -> {after}{marker}")
                updated += 1
        db.commit()
        print(f"\n{updated} specsheet(s) re-parsed and updated in place, {skipped} skipped.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
