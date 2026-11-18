"""Give every DuraGreen product that has no picture one from its own spec sheet.

DuraGreen's website shows no picture on some of its cards, and the track backfill creates a
system's parts (MINI-LINE 11's end feeds, joints and end cap) with none, so 11 products showed
"no image" in NPD and Add Product (owner, 27 Oct 2026: "images are still not fixed"). Their spec
sheets do picture them: app/duragreen_import.picture_for takes the drawing printed beside the
product's own name, a listed crop where the sheet draws a part only inside its system drawing, or
- for a sheet of one product alone - the largest picture on its first page. A shared sheet's
largest picture is the whole system, never one part's, so a part with no drawing of its own stays
without and is reported.

What this does: for make Duragreen, where `image_path` is EMPTY or names a file not on disk, saves
the picture into data/images and sets `image_path` (and `diagrams`, if that is empty too).

What this never does: replace a picture a product has, change a price, a code, an option, a BOQ
line, or delete anything.

--dry-run does everything into "Duragreen Catalogue/picture_preview/" instead of data/images, so a
person can look at every picture first, and rolls the database back.

    python scripts/backfills/backfill_duragreen_pictures.py --dry-run
    python scripts/backfills/backfill_duragreen_pictures.py
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
import argparse
import csv
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

from app import duragreen, duragreen_import as imp, models
from app.database import IMAGES_DIR, SessionLocal

MAKE = duragreen.MAKE
FOLDER = _REPO_ROOT / "Duragreen Catalogue"
PDFS = FOLDER / "site_crawl" / "pdfs"
PREVIEW = FOLDER / "picture_preview"


def sheet_file(sheet, pdf_files):
    """The spec sheet a product holds, on this machine (the same rule as the npd backfill's
    own_sheet): an uploaded file, or DuraGreen's link as the crawl saved it. None if neither."""
    ref = (sheet.source_pdf_path or "").strip()
    if ref.startswith("http"):
        name = pdf_files.get(ref)
        return (PDFS / name) if name and (PDFS / name).exists() else None
    return Path(ref) if ref and Path(ref).exists() else None


def has_picture(sheet):
    p = (sheet.image_path or "").strip()
    return bool(p) and Path(p).exists()


def _prices(sheets):
    return {(s.id, s.base_price, json.dumps(s.price_adders or {}, sort_keys=True), s.list_price,
             json.dumps(s.list_price_by_wattage or {}, sort_keys=True)) for s in sheets}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="pictures into Duragreen Catalogue/picture_preview/, database rolled back")
    args = ap.parse_args()
    crawl = PDFS.parent / "crawl.json"
    pdf_files = json.loads(crawl.read_text(encoding="utf-8")).get("pdf_files", {}) if crawl.exists() else {}
    folder = PREVIEW if args.dry_run else IMAGES_DIR
    if args.dry_run and PREVIEW.exists():
        shutil.rmtree(PREVIEW)
    db = SessionLocal()
    report = []
    try:
        sheets = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).order_by(models.Specsheet.id).all()
        prices_before = _prices(sheets)
        n_boq_items = db.query(models.BOQItem).count()
        n_codes = db.query(models.VendorOrderCode).count()
        # which products share a sheet - a part is never given a sibling's drawing
        on_sheet = defaultdict(list)
        files = {}
        for s in sheets:
            f = sheet_file(s, pdf_files)
            files[s.id] = f
            if f is not None:
                on_sheet[str(f.resolve())].append(s.product_name or "")
        for s in sheets:
            if has_picture(s):
                continue
            f = files[s.id]
            row = {"specsheet_id": s.id, "product": s.product_name, "series": s.series,
                   "status": s.approval_status, "sheet": f.name if f else "", "picture": "", "how": ""}
            if f is None:
                row["how"] = "no spec sheet on this machine"
                report.append(row)
                continue
            siblings = [n for n in on_sheet[str(f.resolve())] if n != (s.product_name or "")]
            slug = re.sub(r"\s+", "_", "%s %s" % (s.series or "", s.product_name or "")).strip("_")
            path, how = imp.picture_for(str(f), s.product_name or "", siblings, slug, folder=folder)
            row["how"] = how
            if path:
                row["picture"] = Path(path).name
                s.image_path = path
                if not s.diagrams:
                    s.diagrams = [{"path": path, "width": 0, "height": 0, "label": "Product Image"}]
            report.append(row)
        db.flush()

        after = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()
        assert {s.id for s in sheets} == {s.id for s in after}, "a product appeared or vanished"
        assert prices_before == _prices(after), "a price changed"
        assert n_boq_items == db.query(models.BOQItem).count(), "a BOQ line changed"
        assert n_codes == db.query(models.VendorOrderCode).count(), "an order code changed"

        given = [r for r in report if r["picture"]]
        print("products without a picture: %d | given one from their own sheet: %d | still without: %d"
              % (len(report), len(given), len(report) - len(given)))
        for r in report:
            print("  %5s %-44s %-24s %s" % (r["specsheet_id"], (r["product"] or "")[:44], r["picture"][:24] or "-", r["how"]))
        print("checked: prices unchanged (%d products), BOQ lines unchanged (%d), order codes unchanged (%d)"
              % (len(prices_before), n_boq_items, n_codes))
        tag = "_dry_run" if args.dry_run else ""
        with open(FOLDER / ("pictures_report%s.csv" % tag), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["specsheet_id", "product", "series", "status", "sheet", "picture", "how"])
            w.writeheader()
            w.writerows(report)
        if args.dry_run:
            db.rollback()
            print("DRY RUN - rolled back; the pictures are in %s to look at" % PREVIEW)
        else:
            db.commit()
            print("committed")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
