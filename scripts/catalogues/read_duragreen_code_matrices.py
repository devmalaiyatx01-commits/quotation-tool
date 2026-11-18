"""Every "Specification Code" table DuraGreen prints, in every document it publishes - so each range's
nomenclature rests on all of DuraGreen's own words, not on one catalogue.

Found on 26 Oct 2026 by searching every page of every document (the 2026 and 2025 General
Catalogues, the ACME public price list, "The Chosen Ones", the 2024 look book, all 583 spec sheets)
and every page of the website:

  * ACME  - the 2026 catalogue (p.33), the 2025 catalogue (PDF p.72; its matrix pages carry no printed number), the price list (p.27), and the
            matrix 35 ACME spec sheets print. They are not one table: see the comparison.
  * MOOD  - the 2026 catalogue (p.222), the 2025 catalogue (PDF p.161), and 8 MOOD spec sheets.
  * EVOLINE - the price list only (p.127). The catalogues explain EVOLINE page by page.
  * BELLA 41, GLAMO 68, ARTFOCUS 36 - the price list (pp.161, 185, 207).
  * "The Chosen Ones" (2025) - one table across its System ranges, printed sideways (p.151).
  * FLOW  - none anywhere: only each catalogue page's own example and legend.

Writes "Duragreen Catalogue/duragreen_code_matrices.json" (every edition, column by column, with
its document, page and date) and "Duragreen Catalogue/code_matrices_compared.csv" (each range's
codes edition by edition, and where they disagree). Reads PDFs only; touches no database.

    python scripts/catalogues/read_duragreen_code_matrices.py
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
import csv
import hashlib
import json
import re
from collections import defaultdict

import fitz

_sys.path.insert(0, str(_REPO_ROOT / "scripts" / "catalogues"))
import read_duragreen_general_catalogue as gc  # noqa: E402  (the 2026 reader: its matrix reader is shared)

CRAWL = _REPO_ROOT / "Duragreen Catalogue" / "site_crawl"
OUT = _REPO_ROOT / "Duragreen Catalogue" / "duragreen_code_matrices.json"
CSV_OUT = _REPO_ROOT / "Duragreen Catalogue" / "code_matrices_compared.csv"

STANDARD = ["Type", "Engine", "Power", "Colour Temp", "Driver", "Beam Width", "Bezel Colour", "Baffle Colour", "Options"]
MOOD_HEADS = ["Type", "Cut out", "Power", "Colour Temp", "Driver", "Beam Width", "Bezel Colour", "Baffle Colour", "Options"]
STRUCTURE = ["Type", "Engine", "Power", "Colour Temp", "Driver", "Beam Width", "Structure Colour", "Baffle Colour", "Options"]

# (range, document under site_crawl/, PDF page, the date its upload carries, headings). Spec sheets
# are found and grouped below, not listed.
DOCUMENTS = [
    ("ACME", "2026-DuraGreen-General-Catalogue.pdf", 56, "2026-08", gc.MATRICES["ACME"][1]),
    ("MOOD", "2026-DuraGreen-General-Catalogue.pdf", 245, "2026-08", gc.MATRICES["MOOD"][1]),
    ("ACME", "other_documents/2025-ENGAGING-GENERRAL-CATALOGUE.pdf", 72, "2025-01", STANDARD),
    ("MOOD", "other_documents/2025-ENGAGING-GENERRAL-CATALOGUE.pdf", 161, "2025-01", MOOD_HEADS),
    ("ACME", "other_documents/ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 33, "2024-09", STANDARD),
    ("EVOLINE", "other_documents/ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 133, "2024-09", STANDARD),
    ("BELLA 41", "other_documents/ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 167, "2024-09", STRUCTURE),
    ("GLAMO 68", "other_documents/ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 191, "2024-09", STANDARD),
    ("ARTFOCUS 36", "other_documents/ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 213, "2024-09", STRUCTURE),
]
SIDEWAYS = ("THE CHOSEN ONES (System ranges)", "other_documents/2025---THE-CHOSEN-ONES.pdf", 174, "2025-04", STANDARD)
gc.SEGMENT_FIELD.setdefault("Structure Colour", "finish")


def printed_number(page):
    """The page number printed in the footer ("127"), or None."""
    nums = [l for l in gc.lines_of(page) if l["y"] > page.rect.height - 60 and re.fullmatch(r"\d{1,3}", l["text"].split()[-1])]
    return int(nums[-1]["text"].split()[-1]) if nums else None


def read_sideways(page, headings):
    """A table printed turned 90 degrees clockwise: headings run down the page, each column's
    entries are lines stacked from right to left. Read as the upright table it is: a line's place
    along the page is its column, and right-to-left is top-to-bottom."""
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = gc.clean("".join(s["text"] for s in line["spans"]))
            if text:
                x0, y0, x1, y1 = line["bbox"]
                lines.append({"u": (y0 + y1) / 2, "v": -(x0 + x1) / 2, "text": text})
    heads = []
    for h in headings:
        cand = [l for l in lines if l["text"] == h and (not heads or l["u"] > heads[-1]["u"])]
        heads.append({"heading": h, "u": cand[0]["u"], "v": cand[0]["v"]})
    head_v = max(h["v"] for h in heads)
    body = [l for l in lines if l["v"] > head_v + 4 and not re.fullmatch(r"\d{1,3}", l["text"]) and "DuraGreen" not in l["text"]]
    segments = []
    for j, h in enumerate(heads):
        mine = sorted([l for l in body if min(range(len(heads)), key=lambda k: abs(heads[k]["u"] - l["u"])) == j],
                      key=lambda l: l["v"])
        entries = []
        for l in mine[1:]:
            m = re.match(r"^([A-Z0-9]{1,5})\s*/\s*(.+)$", l["text"])
            if m:
                entries.append({"code": m.group(1), "meaning": m.group(2).strip()})
            elif entries:
                entries[-1]["meaning"] = gc.clean(entries[-1]["meaning"] + " " + l["text"])
        segments.append({"segment": h["heading"], "field": gc.SEGMENT_FIELD.get(h["heading"], ""),
                         "example": mine[0]["text"] if mine else "", "entries": entries})
    return {"segments": segments, "example_tokens": [s["example"] for s in segments], "dash_after": []}


def upload_month(url):
    m = re.search(r"/uploads/(\d{4})/(\d{2})/", url)
    return "%s-%s" % m.groups() if m else ""


def sheet_editions(crawl):
    """The matrix every spec sheet prints, grouped: one edition per distinct table."""
    files_of = defaultdict(list)
    for url, local in crawl["pdf_files"].items():
        files_of[local].append(url)
    products_of = defaultdict(set)
    for p in crawl["products"].values():
        for card in p.get("cards") or []:
            for d in card.get("downloads") or []:
                local = crawl["pdf_files"].get(d.get("url"))
                if local:
                    products_of[local].add("%s / %s" % (p["title"], card["name"]))
    groups = {}
    for path in sorted((CRAWL / "pdfs").glob("*.pdf")):
        doc = fitz.open(str(path))
        pno = next((i + 1 for i, p in enumerate(doc) if re.search(r"specification\s+code", gc.clean(p.get_text()), re.I)), None)
        if pno is None:
            continue
        page = doc[pno - 1]
        heads = MOOD_HEADS if "Cut out" in gc.clean(page.get_text()) else STANDARD
        read = gc.read_matrix_page(page, heads)
        key = hashlib.md5(json.dumps(read["segments"], sort_keys=True).encode()).hexdigest()[:10]
        g = groups.setdefault(key, {"read": read, "files": [], "dates": set(), "products": set(), "pdf_page": pno})
        g["files"].append(path.name)
        g["dates"].update(upload_month(u) for u in files_of.get(path.name, []))
        g["products"].update(products_of.get(path.name, set()))
    out = []
    for key, g in groups.items():
        types = " ".join(e["code"] for e in g["read"]["segments"][0]["entries"])
        rng = "MOOD" if g["read"]["segments"][1]["segment"] == "Cut out" else "ACME"
        dates = sorted(d for d in g["dates"] if d)
        out.append(dict(range=rng, source="spec sheets (%d, uploaded %s to %s)" % (len(g["files"]), dates[0] if dates else "?",
                                                                                    dates[-1] if dates else "?"),
                        document=g["files"][0], pdf_page=g["pdf_page"], printed_page=None,
                        date=dates[-1] if dates else "", files=sorted(g["files"]), products=sorted(g["products"]),
                        types=types, **g["read"]))
    return out


def main():
    crawl = json.loads((CRAWL / "crawl.json").read_text(encoding="utf-8"))
    editions = []
    for rng, rel, pno, date, heads in DOCUMENTS:
        doc = fitz.open(str(CRAWL / rel))
        read = gc.read_matrix_page(doc[pno - 1], heads)
        editions.append(dict(range=rng, source=rel.split("/")[-1], document=rel, pdf_page=pno,
                             printed_page=printed_number(doc[pno - 1]), date=date, **read))
    rng, rel, pno, date, heads = SIDEWAYS
    doc = fitz.open(str(CRAWL / rel))
    editions.append(dict(range=rng, source=rel.split("/")[-1], document=rel, pdf_page=pno,
                         printed_page=printed_number(doc[pno - 1]), date=date, **read_sideways(doc[pno - 1], heads)))
    editions += sheet_editions(crawl)

    OUT.write_text(json.dumps({"editions": editions}, ensure_ascii=False, indent=1), encoding="utf-8")
    # side by side: every (range family, column, code) and what each edition says it means
    family = lambda e: "ACME" if e["range"] == "ACME" else "MOOD" if e["range"] == "MOOD" else e["range"]
    rows = []
    for fam in sorted({family(e) for e in editions}):
        mine = sorted([e for e in editions if family(e) == fam], key=lambda e: e["date"], reverse=True)
        cols = []
        for e in mine:
            for s in e["segments"]:
                if s["segment"] not in cols:
                    cols.append(s["segment"])
        for col in cols:
            codes = []
            for e in mine:
                for s in e["segments"]:
                    if s["segment"] == col:
                        for en in s["entries"]:
                            if en["code"] not in codes:
                                codes.append(en["code"])
            for code in codes:
                says = []
                for e in mine:
                    seg = next((s for s in e["segments"] if s["segment"] == col), None)
                    m = next((en["meaning"] for en in (seg["entries"] if seg else []) if en["code"] == code), "")
                    says.append(m)
                meanings = {m.lower().replace(" ", "") for m in says if m}
                status = "conflict" if len(meanings) > 1 else ("all agree" if all(says) else "some editions lack it")
                rows.append([fam, col, code, status] + ["%s %s p.%s: %s" % (e["date"], e["source"][:28], e["printed_page"] or e["pdf_page"], s or "-")
                                                        for e, s in zip(mine, says)])
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["range", "column", "code", "status", "what each edition says (newest first)"])
        w.writerows(rows)
    for e in editions:
        print("%-28s %-8s %-48s p.%-4s types: %s" % (e["range"], e["date"], e["source"][:48], e["printed_page"] or e["pdf_page"],
                                                     " ".join(x["code"] for x in e["segments"][0]["entries"])))
    print("\nconflicts:", sum(r[3] == "conflict" for r in rows), "of", len(rows), "codes ->", CSV_OUT.name)
    print("written:", OUT.name)


if __name__ == "__main__":
    main()
