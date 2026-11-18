"""Imports the Exenia catalogue into the specsheet library from the printed PDF
(SampleInputs/Exenia 2026 Catalogue.pdf), classified by Exenia's OWN taxonomy.

Why the PDF and not the website
-------------------------------
exenia.eu is behind Cloudflare bot protection: product pages return a challenge that
never resolves for an automated browser, and their DIALux plugin feed is published but
empty. The printed catalogue is the publisher's own distribution of the same data, so it
is the source of record here. Nothing is scraped.

How the pages are read
----------------------
Pages 106-137 of the PDF are the "Indice / Index" - a dense product index, and the only
part of the catalogue with per-product technical data. Everything before it is project
photography and case studies.

Each PDF page is a TWO-PAGE SPREAD (A4 landscape, 1190pt wide) carrying up to four
products in a column grid, which is why a naive whole-page text extract interleaves four
products' specs into one unreadable run. Three positional facts make it separable, all
verified directly against the file rather than assumed:

  - Product names are the only Gotham-Bold spans at 8-10.5pt. Section headers are 12pt,
    "Indice / Index" is Bold 14pt, and body text is Gotham-Book 6pt - so font alone
    identifies a product name, where an ALL-CAPS text heuristic does not (it matches the
    "CRI92 2700K..." rows, which look like headings but are body copy).
  - The spread's two printed pages start at x=36 and x=631, so a product's column x
    places it on one printed page or the other.
  - The printed page number is stamped once per spread at x=1155 and always names the
    RIGHT page, giving  left = 2*pdf - 4,  right = 2*pdf - 3  (checked on six spreads).

Section assignment therefore runs through the printed page number, not the PDF page:
SECTIONS below is the catalogue's own contents page (PDF p102) verbatim, and a product
belongs to the last section starting at or before its printed page. That matters on the
spreads that straddle a section change - p135 carries Projectors on its left page and
Bollard on its right, and a per-PDF-page mapping would file half of them wrongly.

Taxonomy
--------
Exenia splits its catalogue Indoor / Outdoor first and then by fixture type, which maps
onto the app's existing model with no new concepts: `environment` already holds
Interior/Exterior, and `typology` holds the per-brand vocabulary (see
backfill_duragreen_site_typology.py for the same treatment of DuraGreen). The typology
names below are Exenia's English ones, matching their website's own category menu.

Dry run by default; pass --commit to write.
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
import sys
import unicodedata
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import fitz

from app.database import SessionLocal, IMAGES_DIR
from app import models

PDF = Path("SampleInputs/Exenia 2026 Catalogue.pdf")
MAKE = "Exenia"
FIRST_PAGE, LAST_PAGE = 106, 137
SPREAD_MID = 595.0          # x midpoint between the two printed pages
NAME_MIN, NAME_MAX = 8.0, 10.5   # Gotham-Bold point size of a product name

# The catalogue's own contents page (PDF p102), as (printed page it starts on,
# environment, Exenia's English typology). "NEWS" is not a typology - those products are
# repeated in their real sections later, so the range is simply not imported.
SECTIONS = [
    (208, "Interior", "Base Projectors"),
    (212, "Interior", "Wall lamps"),
    (216, "Interior", "Downlights"),
    (230, "Interior", "Linear"),
    (234, "Interior", "Pendant - Ceiling lamps"),
    (242, "Interior", "Tracks"),
    (258, "Interior", "Systems"),
    (260, "Interior", "Floor lamps"),
    (262, "Exterior", "Projectors"),
    (267, "Exterior", "Bollard"),
    (268, "Exterior", "Wall lamps"),
    (270, "Exterior", "Downlights"),
    (271, "Exterior", "Softlines"),
]


def deaccent(s: str) -> str:
    """The PDF's text layer encodes accented characters and typographic dashes in a way
    that round-trips as mojibake; strip to ASCII so names match what a user would type."""
    s = s.replace("–", "-").replace("—", "-").replace("’", "'")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def section_for(printed_page: int):
    match = None
    for start, env, typ in SECTIONS:
        if printed_page >= start:
            match = (env, typ)
    return match


def products_on_page(page):
    """Every product name on one spread, as (x, y, name)."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        parts = [s["text"].strip() for l in b["lines"] for s in l["spans"]
                 if "Bold" in s["font"] and NAME_MIN <= s["size"] <= NAME_MAX and s["text"].strip()]
        if not parts:
            continue
        name = re.sub(r"\s+", " ", " ".join(parts)).strip()
        name = re.sub(r"\s*COD:.*$", "", name).strip()
        name = deaccent(name)
        # "/ /" is the leftover of a 12pt section header whose slashes are Bold; a real
        # product name always carries letters or digits.
        if len(name) < 3 or not re.search(r"[A-Za-z0-9]{2}", name):
            continue
        if name.lower().startswith("indice"):
            continue
        out.append((b["bbox"][0], b["bbox"][1], name))
    out.sort(key=lambda r: r[0])
    return out


def group_rows(prods, tol=40):
    """Products laid out in a grid, grouped into rows by y. A spread carries two rows of
    up to four products, so x alone cannot separate them - the two products sharing a
    column would have their specs merged."""
    rows, cur = [], []
    for p in sorted(prods, key=lambda r: r[1]):
        if cur and abs(p[1] - cur[0][1]) > tol:
            rows.append(sorted(cur, key=lambda r: r[0])); cur = []
        cur.append(p)
    if cur:
        rows.append(sorted(cur, key=lambda r: r[0]))
    return rows


def cell_text(page, rows, ri, ci, page_w):
    """Body text inside one product's grid cell: bounded left/right by its neighbours in
    the same row, and below by the start of the next row."""
    row = rows[ri]
    x0 = row[ci][0]
    x1 = row[ci + 1][0] if ci + 1 < len(row) else page_w
    y0 = row[ci][1]
    y1 = rows[ri + 1][0][1] - 15 if ri + 1 < len(rows) else page.rect.height
    lines = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        bx, by = b["bbox"][0], b["bbox"][1]
        if not (x0 - 8 <= bx < x1 - 8):
            continue
        if not (y0 - 12 <= by < y1):
            continue
        for l in b["lines"]:
            t = " ".join(s["text"] for s in l["spans"]).strip()
            if t:
                lines.append(deaccent(re.sub(r"\s+", " ", t)))
    return lines


def cell_image(page, rows, ri, ci, page_w, dest_stem):
    """The product photo inside one grid cell - the largest embedded image within the
    cell's bounds. Each cell carries a photo, a dimension line-drawing and several small
    icons, so "largest" is what reliably picks the photo. The photo sits ABOVE its name,
    so the search starts at the previous row's floor rather than at the name itself."""
    row = rows[ri]
    x0 = row[ci][0]
    x1 = row[ci + 1][0] if ci + 1 < len(row) else page_w
    y1 = rows[ri + 1][0][1] - 15 if ri + 1 < len(rows) else page.rect.height
    y0 = rows[ri - 1][0][1] if ri > 0 else 0
    best = None
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 1:
            continue
        bx0, by0, bx1, by1 = b["bbox"]
        if not (x0 - 20 <= bx0 < x1 - 8):
            continue
        if not (y0 <= by0 < y1):
            continue
        area = (bx1 - bx0) * (by1 - by0)
        if area < 400:
            continue
        if best is None or area > best[0]:
            best = (area, b)
    if best is None:
        return None
    data, ext = best[1].get("image"), (best[1].get("ext") or "png")
    if not data:
        return None
    dest = IMAGES_DIR / f"{dest_stem}.{ext}"
    i = 2
    while dest.exists():
        dest = IMAGES_DIR / f"{dest_stem}__{i}.{ext}"
        i += 1
    dest.write_bytes(data)
    return str(dest)


FIELD_RULES = [
    ("lumen_package", re.compile(r"^[\d.,]+\s*Lm\b", re.I)),
    ("control",       re.compile(r"\b(ON-OFF|DALI|Casambi|1-10V|Push|Lumentalk|DMX)\b", re.I)),
    ("cct",           re.compile(r"\bCRI\s?\d{2}\b", re.I)),
    ("accessories",   re.compile(r"\b(Honeycomb|Snoot|Visor|Elliptical lens|Protective glass|Diffusing lens|Diffuser)\b", re.I)),
    ("beam_angle",    re.compile(r"\b(Spot|Flood|Washer|Narrow|Wide|Multimetrik|Medium|Large)\b.*?\d", re.I)),
    ("ip_rating",     re.compile(r"\bIP\d{2}\b")),
    ("ik_rating",     re.compile(r"\bIK\d{2}\b")),
]


def parse_fields(lines):
    fields = {}
    for line in lines:
        for key, rx in FIELD_RULES:
            if rx.search(line):
                fields.setdefault(key, []).append(line)
                break
    out = {}
    for k, vals in fields.items():
        seen, uniq = set(), []
        for v in vals:
            if v not in seen:
                seen.add(v); uniq.append(v)
        out[k] = " / ".join(uniq)[:600]
    # a wattage often only appears inside a beam line ("Narrow Spot 11 - 33,1W - 62240cd")
    watts = sorted({m.group(0).replace(",", ".") for line in lines
                    for m in re.finditer(r"\b\d{1,3}(?:[.,]\d)?W\b", line)},
                   key=lambda w: float(w[:-1]))
    if watts:
        out["wattage"] = " / ".join(watts)
    return out


def extract(want_images=False):
    doc = fitz.open(PDF)
    records = []
    for pdf_page in range(FIRST_PAGE, LAST_PAGE + 1):
        page = doc[pdf_page - 1]
        prods = products_on_page(page)
        if not prods:
            continue
        rows = group_rows(prods)
        for ri, row in enumerate(rows):
          for ci, (x, y, name) in enumerate(row):
            printed = (2 * pdf_page - 4) if x < SPREAD_MID else (2 * pdf_page - 3)
            sec = section_for(printed)
            if sec is None:
                continue
            env, typ = sec
            lines = cell_text(page, rows, ri, ci, page.rect.width)
            stem = re.sub(r"[^A-Za-z0-9]+", "_", f"Exenia_{name}")[:70]
            img = cell_image(page, rows, ri, ci, page.rect.width, stem) if want_images else None
            records.append({
                "name": name, "environment": env, "typology": typ,
                "pdf_page": pdf_page, "printed_page": printed, "image_path": img,
                "fields": parse_fields(lines), "raw": "\n".join(lines)[:4000],
            })
    doc.close()
    return records


def main():
    commit = "--commit" in sys.argv
    records = extract(want_images=commit)

    from collections import Counter
    print(f"products extracted: {len(records)}")
    print()
    print("by environment / typology:")
    for (e, t), n in sorted(Counter((r["environment"], r["typology"]) for r in records).items()):
        print(f"   {e:<9} {t:<26} {n:>3}")
    dupes = [n for n, c in Counter(r["name"] for r in records).items() if c > 1]
    if dupes:
        print()
        print(f"names appearing more than once ({len(dupes)}): {dupes[:8]}")

    db = SessionLocal()
    existing = {(s.product_name or "").strip().lower()
                for s in db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()}
    new = [r for r in records if r["name"].strip().lower() not in existing]
    print()
    print(f"already in the library: {len(existing)}   to insert: {len(new)}")

    if not commit:
        print()
        print("sample of what would be created:")
        for r in new[:3]:
            print(f"   {r['name']}  [{r['environment']} / {r['typology']}]  p{r['printed_page']}")
            for k, v in r["fields"].items():
                print(f"      {k}: {v[:90]}")
        print()
        print("DRY RUN - nothing written. Re-run with --commit.")
        db.close()
        return

    for r in new:
        db.add(models.Specsheet(
            filename=PDF.name, category=f"{r['environment']} - {r['typology']}",
            typology=r["typology"], product_name=r["name"], series=r["name"],
            family="", variant="", environment=[r["environment"]],
            page_number=r["pdf_page"], make=MAKE,
            source_pdf_path=str(PDF.resolve()), image_path=r.get("image_path"),
            fields=r["fields"], params={}, options={}, raw_text=r["raw"],
        ))
    db.commit()
    total = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).count()
    print(f"\nCOMMITTED. Exenia specsheets in the library: {total}")
    db.close()


if __name__ == "__main__":
    main()
