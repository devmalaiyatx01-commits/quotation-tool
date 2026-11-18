"""Reads DuraGreen's track spec sheets as a lighting engineer reads them, into one JSON file.

A track is not a luminaire, and its sheet is not laid out like one. There is no code legend:
each part is printed with its own code, the colours it comes in, and - for a feed - the side its
polarity groove is on, drawn as a little icon with the groove in red:

    TRACK 1L 1m        GB2100- | B | W |          the 1 m surface track, black or white
    End feed   [icon]  GB11- | B | W |            groove on the lower edge, as drawn

So this reader works from where things are on the page, not from the order the text comes out
in (that order puts "Middle feed | Straight Connecto | Code | XTS14 | Code | XTS24 ..." - which code
belongs to which part cannot be told from it):

  * a code belongs to the part heading above it and to its left (the sheet is a grid of cells);
  * a track row's length comes from the "TRACK 1L 1m" words on its own line, its mounting from
    the column it is in (SURFACE & SUSPENDED on the left, FLUSH MOUNTING on the right);
  * the colours are the letters printed after the code on the same line (B, W);
  * a feed's polarity groove is read from the red stroke of its icon, against the icon's outline.

Every code read from a sheet must also be printed on the 2026 General Catalogue's own page for
that system (p.577-579 for 220-240V, p.498-502 for 48V), or the reader stops: two DuraGreen
documents agreeing is the check that nothing was misread.

Also read: the 48V MINI-LINE 22 sheets (TC22ST, TC22RT, TC22ST-S, TC22ST-C) - one code per
track, the same for every length, and the accessories printed beside it; the catalogue's 48V
accessory page (p.502); and the MINI-LINE 11 (Stucci Micro 11) sheet, whose accessories are
printed as "9601-S/..." - the part after the slash is not printed, and is recorded as not printed.

    python scripts/catalogues/read_duragreen_track_sheets.py
writes "Duragreen Catalogue/duragreen_tracks_2026.json" and, for each track part, a picture of
its drawing cropped from the sheet into "Duragreen Catalogue/track_drawings/".
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
import hashlib
import json
import re
import statistics

import fitz

FOLDER = _REPO_ROOT / "Duragreen Catalogue"
PDFS = FOLDER / "site_crawl" / "pdfs"
CATALOGUE = FOLDER / "site_crawl" / "2026-DuraGreen-General-Catalogue.pdf"
OUT = FOLDER / "duragreen_tracks_2026.json"
DRAWINGS = FOLDER / "track_drawings"
FOLIO_OFFSET = 29          # the catalogue's printed page = PDF page - 29 in this section (p.577 -> 548)

# The 220-240V systems: their sheets (surface and flush are one sheet, printed twice under two
# names), and the catalogue page that prints the same codes.
SYSTEMS = [
    {"key": "TRACK 1L", "sheets": ["TRACK-1L-SURFACE-SUSPENDED-SPEC.pdf", "TRACK-1L-FLUSH-MOUNTING-SPEC.pdf"],
     "catalogue_page": 577},
    {"key": "TRACK 3L", "sheets": ["TRACK-3L-SURFACE-SUSPENDED-SPEC.pdf", "TRACK-3L-FLUSH-MOUNTING-SPEC.pdf"],
     "catalogue_page": 578},
    {"key": "TRACK 3L-DIM", "sheets": ["TRACK-3L-DIM-SURFACE-SUSPENDED-SPEC.pdf", "TRACK-3L-DIM-FLUSH-MOUNTING-SPEC.pdf"],
     "catalogue_page": 579},
]
# What the profile drawings show (conductors labelled on the cross-section, the dimension
# arrows). Read off the drawings once; each number is checked to be printed on the sheet.
SYSTEM_FACTS = {
    "TRACK 1L": {"circuits": "1 circuit (L1 + N)", "polarity_mark": "Polarity line",
                 "Surface & Suspended": {"profile_dimension": "30 x 20 mm", "printed": ["30", "20"]},
                 "Flush Mounting": {"profile_dimension": "50 x 20 mm (30 mm channel)", "printed": ["50", "30", "20"]}},
    "TRACK 3L": {"circuits": "3 circuits (L1, L2, L3 + N)", "polarity_mark": "Polarity ridge",
                 "Surface & Suspended": {"profile_dimension": "31.5 x 32.5 mm", "printed": ["31,5", "32,5"]},
                 "Flush Mounting": {"profile_dimension": "56 x 32.5 mm (31.5 mm channel)", "printed": ["56", "31,5", "32,5"]}},
    "TRACK 3L-DIM": {"circuits": "3 circuits (L1, L2, L3 + N) + DALI (DA+, DA-)", "polarity_mark": "Polarity ridge",
                     "Surface & Suspended": {"profile_dimension": "31.5 x 32.5 mm", "printed": ["31,5", "32,5"]},
                     "Flush Mounting": {"profile_dimension": "56 x 32.5 mm (31.5 mm channel)", "printed": ["56", "31,5", "32,5"]}},
}
# The part headings a 220-240V sheet prints, as printed -> as an engineer names them. "Straight
# Connecto" is the sheet's own truncation.
PART_NAMES = {"End feed": "End feed", "L-feed": "L-feed", "T-feed": "T-feed", "X-feed": "X-feed",
              "Middle feed": "Middle feed", "Straight Connecto": "Straight connector",
              "Straight Connector": "Straight connector", "Adjustable corner": "Adjustable corner connector",
              "Flexible corner": "Flexible corner connector", "End cap": "End cap"}
CODE_WORD = re.compile(r"^([A-Z]{2,6}\d{2,4})-\|?$")
LETTERS = {"B": "Black", "W": "White"}      # the catalogue's colour table (p.33): B Black, W White


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def words_of(page):
    return [{"x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": w[4]} for w in page.get_text("words")]


def lines_of(page):
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": re.sub(r"\s+", " ", text)})
    return sorted(out, key=lambda l: (round(l["y0"]), l["x0"]))


def save_picture(page, box, name, dpi=220):
    """Render `box` of `page` to track_drawings/<name>, trimmed to what is drawn (plus a margin),
    so a small part does not sit in a large white field."""
    from PIL import Image, ImageOps
    DRAWINGS.mkdir(exist_ok=True)
    path = DRAWINGS / name
    page.get_pixmap(dpi=dpi, clip=box).save(str(path))
    img = Image.open(path).convert("RGB")
    ink = ImageOps.invert(img.convert("L")).point(lambda v: 255 if v > 18 else 0).getbbox()
    if ink:
        pad = 12
        img.crop((max(ink[0] - pad, 0), max(ink[1] - pad, 0), min(ink[2] + pad, img.width),
                  min(ink[3] + pad, img.height))).save(path)
    return name


def is_red(colour):
    return bool(colour) and colour[0] > 0.7 and colour[1] < 0.35 and colour[2] < 0.35


def segments(drawing):
    """A drawing's straight pieces as (x0, y0, x1, y1): an L-shaped groove is one path of two
    lines, so its bounding box alone says nothing about which edge it runs along."""
    out = []
    for item in drawing.get("items", []):
        if item[0] == "l":
            a, b = item[1], item[2]
            out.append((min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
        elif item[0] == "re":
            r = item[1]
            out.append((r.x0, r.y0, r.x1, r.y1))
    return out


# --- the 220-240V sheets ---------------------------------------------------------------------

def part_headings(lines):
    """[(x0, y0, y1, name)] for every part heading on the page; a two-line heading ("Adjustable
    corner" over "connector") is one heading."""
    out = []
    for i, l in enumerate(lines):
        name = PART_NAMES.get(l["text"])
        if not name:
            continue
        y1 = l["y1"]
        below = [m for m in lines if m["text"] == "connector" and abs(m["x0"] - l["x0"]) < 4 and 0 < m["y0"] - l["y1"] < 6]
        if below:
            y1 = below[0]["y1"]
        out.append({"x0": l["x0"], "y0": l["y0"], "y1": y1, "name": name})
    return out


def owner(code, headings):
    """The heading a code sits under: above it and to its left; of the nearest row of headings,
    the one furthest right."""
    cands = [h for h in headings if h["y0"] <= code["y0"] and h["x0"] <= code["x0"] + 5]
    if not cands:
        return None
    top = max(h["y0"] for h in cands)
    row = [h for h in cands if h["y0"] >= top - 15]
    return max(row, key=lambda h: h["x0"])


def finish_letters(code, words):
    """The colour letters printed after a code on its own line: "GB2100- | B | W |" -> ["B", "W"]."""
    same = [w for w in words if abs((w["y0"] + w["y1"]) / 2 - (code["y0"] + code["y1"]) / 2) < 3
            and code["x1"] <= w["x0"] < code["x1"] + 80]
    return [w["text"] for w in sorted(same, key=lambda w: w["x0"]) if w["text"] in LETTERS]


def polarity(part, rows, drawings):
    """Each row's polarity groove, read from the red stroke of its icon against the icon's outline.
    The icon sits left of the code, in the code's own row of the table."""
    rows = sorted(rows, key=lambda r: r["cy"])
    pitch = statistics.median([b["cy"] - a["cy"] for a, b in zip(rows, rows[1:])]) if len(rows) > 1 else 22
    for i, r in enumerate(rows):
        top = r["cy"] - pitch / 2
        bottom = r["cy"] + pitch / 2
        left, right = r["x0"] - 120, r["x0"] - 4
        inside = [d for d in drawings if left <= d["rect"].x0 and d["rect"].x1 <= right
                  and top <= d["rect"].y0 and d["rect"].y1 <= bottom]
        red = [s for d in inside if is_red(d.get("color")) for s in segments(d)]
        if not red:
            r["polarity_groove"] = ""
            continue
        # The icon's outline is what hugs the red groove - within 9pt of it. Taking every stroke
        # in the row instead let the part's own big drawing reach in on the 3L-DIM sheet and
        # stretch the outline 60pt to the left, which turned a groove on the straight side into
        # one on the branch side.
        near = fitz.Rect(min(s[0] for s in red) - 9, min(s[1] for s in red) - 9,
                         max(s[2] for s in red) + 9, max(s[3] for s in red) + 9)
        outline = [d["rect"] for d in inside if not is_red(d.get("color")) and near.contains(d["rect"])]
        if not outline:
            r["polarity_groove"] = ""
            continue
        box = fitz.Rect(outline[0])
        for o in outline[1:]:
            box |= o
        flat = [s for s in red if s[3] - s[1] < 1.5 and s[2] - s[0] > 3]       # horizontal pieces
        upright = [s for s in red if s[2] - s[0] < 1.5 and s[3] - s[1] > 3]     # vertical pieces
        r["icon"] = [round(v, 1) for v in box]
        r["red"] = [[round(v, 1) for v in s] for s in red]
        # Each icon is read against its own outline: which edge the groove runs along.
        if part == "End feed" and flat:
            y = flat[0][1]
            r["polarity_groove"] = "Lower edge (as drawn)" if y > (box.y0 + box.y1) / 2 else "Upper edge (as drawn)"
        elif part == "L-feed" and flat:
            y = min(s[1] for s in flat)
            r["polarity_groove"] = "Outside corner" if abs(y - box.y0) < 2.5 else "Inside corner"
        elif part == "T-feed" and flat and upright:
            y = max(flat, key=lambda s: s[2] - s[0])[1]          # the groove along the bar
            x = upright[0][0]
            side = "straight side" if abs(y - box.y1) < 2.5 else "branch side"
            branch = "branch right" if x > (box.x0 + box.x1) / 2 else "branch left"
            r["polarity_groove"] = "Groove on the %s, %s (as drawn)" % (side, branch)
        else:
            r["polarity_groove"] = ""


def dotted(drawing):
    """The cell borders are dotted lines; a drawing's own lines are solid ("[] 0")."""
    return bool(re.search(r"\[\s*\d", str(drawing.get("dashes") or "")))


def drawing_box(heading, table_x0, next_y, drawings, page):
    """The part's own drawing: everything drawn in its cell left of its code table. A stroke
    counts if it touches the cell - a drawing reaches up beside its own heading, and its long
    dimension lines run past the cell's edge - and the picture is cut to the cell."""
    area = fitz.Rect(heading["x0"] - 8, heading["y0"] - 4, table_x0 - 6, next_y - 4)
    # Left out: the dots of the dotted cell borders (tiny marks never decide a drawing's extent),
    # and the code table's rules, which start inside the cell and run out of it to the right.
    parts = [d["rect"] for d in drawings if d["rect"].intersects(area) and not dotted(d)
             and not (d["rect"].width < 2.5 and d["rect"].height < 2.5)
             and d["rect"].x1 <= area.x1 + 2
             and d["rect"].width < area.width * 0.98 and d["rect"].height < area.height * 0.98]
    if not parts:
        return None
    box = fitz.Rect(parts[0])
    for q in parts[1:]:
        box |= q
    box = fitz.Rect(box.x0 - 3, box.y0 - 3, box.x1 + 3, box.y1 + 3) & area
    return box & page.rect


def read_system(system):
    path = PDFS / system["sheets"][0]
    doc = fitz.open(path)
    page = doc[0]
    words, lines, drawings = words_of(page), lines_of(page), page.get_drawings()
    flush_x = min(w["x0"] for w in words if w["text"] == "FLUSH" and w["y0"] < 200)
    headings = part_headings(lines)

    profiles = {"Surface & Suspended": [], "Flush Mounting": []}
    parts = {}
    for w in words:
        m = CODE_WORD.match(w["text"])
        if not m:
            continue
        stem = m.group(1)
        letters = finish_letters(w, words)
        # the words before the code on its own line - in its own column, not the other table's
        right_column = w["x0"] >= flush_x - 20
        same_line = sorted([v for v in words if abs(v["y0"] - w["y0"]) < 3 and v["x1"] <= w["x0"]
                            and (v["x0"] >= flush_x - 20) == right_column], key=lambda v: v["x0"])
        label = " ".join(v["text"] for v in same_line if v["text"] not in ("|",))
        length = re.search(r"\b(\d+)m\b", label)
        if label.startswith("TRACK") and length:
            mounting = "Flush Mounting" if w["x0"] >= flush_x - 20 else "Surface & Suspended"
            profiles[mounting].append({"type": label, "length": length.group(1) + "m", "stem": stem,
                                       "finish_letters": letters, "y": round(w["y0"], 1)})
            continue
        h = owner(w, headings)
        if h is None:
            raise SystemExit("%s: code %s sits under no part heading" % (path.name, stem))
        part = parts.setdefault(h["name"], {"name": h["name"], "heading": h, "rows": []})
        part["rows"].append({"stem": stem, "finish_letters": letters, "x0": w["x0"],
                             "cy": (w["y0"] + w["y1"]) / 2})

    # The drawings are rendered from a copy of the page with the headings and the code tables'
    # words taken out, so a picture shows the part and its dimensions, not "End feed" or "Code".
    clean = fitz.open(path)[0]
    for h in headings:
        clean.add_redact_annot(fitz.Rect(h["x0"] - 1, h["y0"] - 1, h["x0"] + 200, h["y1"] + 1))
    for w in words:
        if CODE_WORD.match(w["text"]) or w["text"] in ("POLARITY", "GROOVE", "Code", "|", "B", "W"):
            clean.add_redact_annot(fitz.Rect(w["x0"], w["y0"], w["x1"], w["y1"]))
    clean.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)

    out_parts = []
    for name, part in parts.items():
        if name in ("End feed", "L-feed", "T-feed"):
            polarity(name, part["rows"], drawings)
        h = part["heading"]
        table_x0 = min(r["x0"] for r in part["rows"])
        header = [w for w in words if w["text"] in ("POLARITY", "Code") and h["y0"] < w["y0"] < min(r["cy"] for r in part["rows"])
                  and w["x0"] > h["x0"] + 40 and w["x0"] < table_x0 + 40]
        if header:
            table_x0 = min(table_x0, min(w["x0"] for w in header))
        later = [g["y0"] for g in headings if g["y0"] > h["y1"] + 20 and abs(g["x0"] - h["x0"]) < 120]
        box = drawing_box(h, table_x0, min(later + [page.rect.height - 20]), drawings, page)
        picture = ""
        if box is not None and box.width > 20 and box.height > 20:
            picture = save_picture(clean, box, "duragreen_track_%s.png" % part["rows"][0]["stem"])
        out_parts.append({
            "name": name,
            "rows": [{k: v for k, v in r.items() if k in ("stem", "finish_letters", "polarity_groove", "icon", "red")}
                     for r in sorted(part["rows"], key=lambda r: r["cy"])],
            "drawing": picture, "drawing_box": [round(v, 1) for v in box] if box is not None else None,
        })

    facts = SYSTEM_FACTS[system["key"]]
    text = page.get_text("text")
    for mounting in ("Surface & Suspended", "Flush Mounting"):
        for number in facts[mounting]["printed"]:
            if not re.search(r"(?<![\d,])%s(?![\d,])" % re.escape(number), text):
                raise SystemExit("%s: the profile dimension %s is not printed on the sheet" % (path.name, number))
    # The sheets printed under the other name must be the same sheet: every code, the same.
    codes = sorted(r["stem"] for rs in profiles.values() for r in rs) + sorted(r["stem"] for p in out_parts for r in p["rows"])
    for other in system["sheets"][1:]:
        other_text = fitz.open(PDFS / other)[0].get_text("text")
        missing = [c for c in codes if not re.search(r"\b%s-" % re.escape(c), other_text)]
        if missing:
            raise SystemExit("%s does not print %s, which %s does" % (other, ", ".join(missing), path.name))
    return {
        "key": system["key"], "voltage": "220-240V", "sheets": [{"file": f, "sha256": sha(PDFS / f)} for f in system["sheets"]],
        "catalogue_page": system["catalogue_page"], "printed_page": system["catalogue_page"] - FOLIO_OFFSET,
        "circuits": facts["circuits"], "polarity_mark": facts["polarity_mark"],
        "profiles": {m: {"profile_dimension": facts[m]["profile_dimension"],
                         "rows": sorted(rs, key=lambda r: r["y"])} for m, rs in profiles.items()},
        "parts": out_parts,
    }


# --- the 48V sheets -----------------------------------------------------------------------

M22 = re.compile(r"^M22-L[A-Z]{2}(?:-[A-Z])?-\d$")


def spec_table(lines):
    """{label: value} from a 48V sheet's "Code: / Type: / ..." table (labels left, values right)."""
    labels = [l for l in lines if re.match(r"^[A-Z][A-Za-z ]+:$", l["text"])]
    out = {}
    for lab in labels:
        # A label's box runs past its text ("Finishing:" ends at x=123, its value starts at 114),
        # so a value is matched by the column it is in, not by being right of the label's box.
        # The drawings' dimension figures on the same line sit far to the right, past x+260.
        vals = [l for l in lines if abs(l["y0"] - lab["y0"]) < 3 and lab["x0"] + 30 < l["x0"] < lab["x0"] + 260
                and l not in labels]
        if vals:
            out[lab["text"].rstrip(":")] = " ".join(v["text"] for v in sorted(vals, key=lambda v: v["x0"]))
    return out


def read_mini_line_22(file, title):
    doc = fitz.open(PDFS / file)
    lines = lines_of(doc[0])
    spec = spec_table(lines)
    lengths = re.findall(r"(\d{3,4})\s*mm", spec.get("Dimensions", "").split("L:")[-1]) if "L:" in spec.get("Dimensions", "") else []
    accessories = []
    for l in lines:
        if M22.match(l["text"]):
            # The name is printed centred under the code ("Reinforcing plate", then its
            # parenthesis on a second line) - found by place, not by reading order.
            mid = (l["x0"] + l["x1"]) / 2
            under = sorted([m for m in lines if not M22.match(m["text"]) and 0 < m["y0"] - l["y0"] < 34
                            and abs((m["x0"] + m["x1"]) / 2 - mid) < 90], key=lambda m: m["y0"])
            name = under[0]["text"] if under else ""
            note = under[1]["text"] if len(under) > 1 and under[1]["text"].startswith("(") else ""
            accessories.append({"code": l["text"], "name": re.sub(r"\s+", " ", name).strip(), "note": note.strip("() "),
                                "_line": l, "_under": under[:2 if note else 1]})
    # Each accessory's drawing is the one above its code, below the accessory before it (or the
    # "Accessories not included" heading) - cropped as its picture.
    page = doc[0]
    heading = next((l for l in lines if l["text"].startswith("Accessories not included")), None)
    for i, a in enumerate(accessories):
        l = a.pop("_line")
        above = accessories[i - 1]["_under"] if i else []
        top = max([m["y1"] for m in above] + ([heading["y1"]] if heading else [0])) + 2
        mid = (l["x0"] + l["x1"]) / 2
        box = fitz.Rect(mid - 125, top, mid + 125, l["y0"] - 2) & page.rect
        if box.height > 30:
            a["drawing"] = save_picture(page, box, "duragreen_track_%s.png" % a["code"], dpi=200)
    for a in accessories:
        a.pop("_under", None)
    return {"file": file, "sha256": sha(PDFS / file), "title": title, "code": spec.get("Code", ""),
            "spec": spec, "lengths_mm": lengths, "accessories": accessories}


def read_catalogue_accessories(doc, pdf_page):
    """The catalogue's 48V accessory blocks (p.502): heading, then Code / Type / Material ..."""
    page = doc[pdf_page - 1]
    lines = lines_of(page)
    headings = [l for l in lines if l["text"].isupper() and l["text"] not in ("TECHNICAL FEATURES",) and len(l["text"]) > 4]
    out = []
    for h in headings:
        block = [l for l in lines if h["y0"] < l["y0"] < h["y0"] + 140 and h["x0"] - 8 <= l["x0"] < h["x0"] + 150]
        pairs = {}
        for l in block:
            # "Code  M22-LSS-4" on one line (lines_of has already collapsed the spaces), or the
            # label and its value as two lines side by side (the closure plug's block).
            m = re.match(r"^(Code|Type|Material|Finishing|Fixing|Insulation class|Voltage) (.+)$", l["text"])
            if m:
                pairs[m.group(1)] = m.group(2).strip()
            elif l["text"] in ("Code", "Type", "Material", "Finishing", "Fixing"):
                val = [v for v in lines if abs(v["y0"] - l["y0"]) < 3 and v["x0"] > l["x1"] + 5]
                if val:
                    pairs[l["text"]] = val[0]["text"]
        if re.match(r"^M22-", pairs.get("Code", "")):
            item = {"heading": h["text"], "page": pdf_page, "printed_page": pdf_page - FOLIO_OFFSET, **pairs}
            # the block's photo: under its heading, above its "TECHNICAL FEATURES" line
            tf = next((l for l in lines if l["text"] == "TECHNICAL FEATURES" and abs(l["x0"] - h["x0"]) < 12
                       and l["y0"] > h["y1"]), None)
            if tf is not None:
                # inside the column: the catalogue's column rules sit just left of each heading
                box = fitz.Rect(h["x0"] + 1, h["y1"] + 2, h["x0"] + 145, tf["y0"] - 3)
                item["drawing"] = save_picture(page, box, "duragreen_track_%s.png" % pairs["Code"], dpi=200)
            out.append(item)
    return out


def read_mini_line_11():
    """The Stucci Micro 11 sheet: its track's code, and its accessories as printed - "9601-S/..."
    with the part after the slash left out."""
    file = "Surface-Micro-11-1.pdf"
    doc = fitz.open(PDFS / file)
    stems = []
    for page in doc:
        lines = lines_of(page)
        for i, l in enumerate(lines):
            if re.match(r"^\d{4}(?:-[A-Z])?-?/(?:\.\.\.|…|ST1)$", l["text"]):
                # the name is the line under the code, in the same place across the page - not
                # the next line in reading order, which can be another part's name beside it
                mid = (l["x0"] + l["x1"]) / 2
                name = next((m["text"] for m in lines if not re.match(r"^\d{4}", m["text"])
                             and 0 < m["y0"] - l["y0"] < 40 and abs((m["x0"] + m["x1"]) / 2 - mid) < 90), "")
                stems.append({"printed": l["text"], "name": name[:1].upper() + name[1:],
                              "complete": not l["text"].endswith(("...", "…")) and not l["text"].endswith("-/ST1")})
    tracks = []
    for f, title in [("Surface-Micro-11-1.pdf", "Surface Micro 11"), ("Surface-curved-track-R300-1.pdf", "Surface curved track R300"),
                     ("Surface-curved-track-R500-1.pdf", "Surface curved track R500"),
                     ("Surface-Curved-Track-Ceiling-to-Wall-1.pdf", "Surface Curved Track - Ceiling to Wall")]:
        d = fitz.open(PDFS / f)
        spec = spec_table(lines_of(d[d.page_count - 1]))
        tracks.append({"file": f, "sha256": sha(PDFS / f), "title": title, "code": spec.get("Code", ""), "spec": spec})
    return {"tracks": tracks, "accessories": stems}


def main():
    cat = fitz.open(CATALOGUE)
    systems = [read_system(s) for s in SYSTEMS]

    # Two documents must agree: every code on the sheet is on the catalogue's page, and back.
    for s in systems:
        page_text = cat[s["catalogue_page"] - 1].get_text("text")
        in_catalogue = set(re.findall(r"\b([A-Z]{2,6}\d{2,4})-", page_text))
        on_sheet = {r["stem"] for rs in s["profiles"].values() for r in rs["rows"]} | {r["stem"] for p in s["parts"] for r in p["rows"]}
        if on_sheet != in_catalogue:
            raise SystemExit("%s: sheet and catalogue p.%s disagree - only on the sheet: %s; only in the catalogue: %s"
                             % (s["key"], s["catalogue_page"], sorted(on_sheet - in_catalogue), sorted(in_catalogue - on_sheet)))
        s["catalogue_agrees"] = True

    # The three systems draw the same feeds: a code's last two digits name the same groove side
    # on every sheet (GB36, XTS36 and XTSNC636 alike). A reading that differs is a misread icon.
    side = {}
    for s in systems:
        for p in s["parts"]:
            for r in p["rows"]:
                if r.get("polarity_groove"):
                    key = (p["name"], r["stem"][-2:])
                    side.setdefault(key, set()).add(r["polarity_groove"])
    bad = {k: v for k, v in side.items() if len(v) > 1}
    if bad:
        raise SystemExit("the same feed reads differently on different sheets: %s" % bad)

    mini22 = [read_mini_line_22("TC22ST-SPEC.pdf", "SURFACE TRACK"), read_mini_line_22("TC22RT-SPEC.pdf", "TRIMLESS 12.8 TRACK"),
              read_mini_line_22("TC22ST-S-SPEC.pdf", "SURFACE MICRO TRACK"), read_mini_line_22("TC22ST-C-SPEC.pdf", "SURFACE ROUND TRACK")]
    catalogue_text = " ".join(cat[n - 1].get_text("text") for n in range(498, 503))
    for t in mini22:
        for code in [t["code"]] + [a["code"] for a in t["accessories"]]:
            if code not in catalogue_text:
                raise SystemExit("%s: %s is not on the catalogue's MINI-LINE 22 pages" % (t["file"], code))
        # the catalogue page that prints this track ("TC22ST" alone, not inside "TC22ST-S")
        own = re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(t["code"]))
        page = next((n for n in range(498, 503) if own.search(cat[n - 1].get_text("text"))), None)
        t["catalogue_page"], t["printed_page"] = page, (page - FOLIO_OFFSET if page else None)
    extra = read_catalogue_accessories(cat, 502)

    data = {
        "source": {"read_by": "scripts/catalogues/read_duragreen_track_sheets.py",
                   "catalogue": {"file": CATALOGUE.name, "sha256": sha(CATALOGUE)}},
        "colour_letters": LETTERS,
        "systems_220v": systems,
        "mini_line_22": {"tracks": mini22, "catalogue_accessories": extra},
        "mini_line_11": read_mini_line_11(),
    }
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    for s in systems:
        n_profile = sum(len(rs["rows"]) for rs in s["profiles"].values())
        print("%-13s %d track codes, %d parts (%s), catalogue p.%s agrees"
              % (s["key"], n_profile, len(s["parts"]), ", ".join("%s %d" % (p["name"], len(p["rows"])) for p in s["parts"]),
                 s["catalogue_page"]))
    for t in mini22:
        print("%-22s %-10s lengths %s, accessories %s" % (t["title"], t["code"], t["lengths_mm"] or "-",
                                                          [a["code"] for a in t["accessories"]]))
    print("catalogue p.502:", [(a["Code"], a.get("Type")) for a in extra])
    m11 = data["mini_line_11"]
    print("MINI-LINE 11 tracks:", [(t["title"], t["code"]) for t in m11["tracks"]])
    print("MINI-LINE 11 accessories:", [(a["printed"], a["name"], a["complete"]) for a in m11["accessories"]])
    print("written:", OUT)


if __name__ == "__main__":
    main()
