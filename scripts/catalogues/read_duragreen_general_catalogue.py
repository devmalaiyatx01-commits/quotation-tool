"""Reads DuraGreen's 2026 General Catalogue into the rules its product codes are built by.

The spec sheets print order codes for a few configurations - mostly 3000K, non-dim - and nothing
for the rest. The General Catalogue (712 pages) says how a code is built for ANY configuration:

  * two "Specification Code" matrices - ACME (page 33 of the catalogue, PDF page 56) and MOOD
    (PDF page 245) - giving every segment of the code in order, every option of each segment
    with its letters, and where the separators go;
  * on every one of its 269 product spreads, a decoded "Code example" (AFL 50 08W- C -30 N 36-W B H,
    then one line per part: "C / curve baffle", "W/ white bezel" ...), the finish palette as the
    page prints it (bezel, baffle, Standard, Curated), the options (optics, CCT, control, output,
    optional accessories) and the printed code tables.

All of it is read here by position, from the PDF's own text - nothing is typed in by hand - into
"Duragreen Catalogue/duragreen_general_catalogue_2026.json", which the code rules backfill loads
(scripts/backfills/backfill_duragreen_code_rules.py). Nothing here touches the app's database.

    python scripts/catalogues/read_duragreen_general_catalogue.py
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
import sys

import fitz

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = _REPO_ROOT / "Duragreen Catalogue" / "site_crawl" / "2026-DuraGreen-General-Catalogue.pdf"
OUT = _REPO_ROOT / "Duragreen Catalogue" / "duragreen_general_catalogue_2026.json"
FOLIO_OFFSET = 23          # PDF page = printed page + 23 (PDF p56 prints "33")

# The two matrices, by PDF page, and the segment headings they print, in code order.
MATRICES = {
    "ACME": (56, ["Type", "Engine", "Power", "Baffle Style", "Bezel Style", "Colour Temp", "Driver",
                  "Beam Width", "Bezel Colour", "Baffle Colour", "Options"]),
    "MOOD": (245, ["Type", "Cut out", "Power", "Colour Temp", "Driver", "Beam Width", "Bezel Colour",
                   "Baffle Colour", "Options"]),
}
# Heading -> the app's field key, for the segments a person chooses when quoting.
SEGMENT_FIELD = {"Colour Temp": "cct", "Driver": "control", "Beam Width": "beam_angle",
                 "Bezel Colour": "bezel_finish", "Baffle Colour": "baffle_finish", "Options": "accessories",
                 "Power": "wattage", "Type": "type", "Engine": "size", "Cut out": "size",
                 "Baffle Style": "style", "Bezel Style": "trim"}


def clean(text):
    """The PDF's own ligatures and spacing, undone: "bafﬂe" is baffle."""
    return re.sub(r"\s+", " ", str(text).replace("ﬂ", "fl").replace("ﬁ", "fi").replace("（", "(")
                  .replace("）", ")")).strip()


def lines_of(page):
    """[{y, x0, x1, size, text}] - one per visual line, top to bottom."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            out.append({"y": min(s["bbox"][1] for s in spans), "x0": min(s["bbox"][0] for s in spans),
                        "x1": max(s["bbox"][2] for s in spans), "size": max(s["size"] for s in spans),
                        "text": clean("".join(s["text"] for s in spans))})
    out.sort(key=lambda r: (round(r["y"]), r["x0"]))
    return out


def words_of(page):
    return [{"x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": clean(w[4])}
            for w in page.get_text("words")]


# ---------------------------------------------------------------- the two matrices

def read_matrix(doc, name):
    pno, headings = MATRICES[name]
    out = read_matrix_page(doc[pno - 1], headings)
    return dict(name=name, pdf_page=pno, printed_page=pno - FOLIO_OFFSET, **out)


def read_matrix_page(page, headings):
    """One printed "Specification Code" table, column by column: {segments, example_tokens,
    dash_after}. Shared by this catalogue's two tables and read_duragreen_code_matrices.py, which
    reads every other edition DuraGreen prints (spec sheets, the 2025 catalogue, the price list)."""
    words = words_of(page)
    top = next(w for w in words if w["text"] == "Specification")["y1"]
    region = [w for w in words if w["y0"] > top]
    # A heading may be two words ("Colour Temp"); find each by its first word, left to right.
    cols = []
    used = set()
    for h in headings:
        parts = h.split()
        cand = [w for w in region if w["text"] == parts[0] and id(w) not in used
                and (not cols or w["x0"] > cols[-1]["x0"])]
        found = None
        for w in sorted(cand, key=lambda w: (round(w["y0"]), w["x0"])):
            if len(parts) == 1:
                found = (w, w)
                break
            nxt = next((v for v in region if v["text"] == parts[1] and abs(v["y0"] - w["y0"]) < 3
                        and 0 < v["x0"] - w["x1"] < 12), None)
            if nxt:
                found = (w, nxt)
                break
        first, last = found
        used.add(id(first))
        # Headings are centred over their columns; entries are left-aligned within them. So an
        # entry belongs to the heading whose centre is nearest its own - its left edge would put
        # a column's entries under the heading to its left.
        cols.append({"heading": h, "x0": first["x0"], "center": (first["x0"] + last["x1"]) / 2, "y": first["y0"]})
    head_y = max(c["y"] for c in cols)
    # Below the headings and above the footer, whose page number ("33") would otherwise end up as
    # the last words of the column above it.
    body = [l for l in lines_of(page) if head_y + 4 < l["y"] < page.rect.height - 45]
    nearest = lambda l: min(range(len(cols)), key=lambda j: abs(cols[j]["center"] - (l["x0"] + l["x1"]) / 2))
    segments = []
    for j, c in enumerate(cols):
        mine = sorted([l for l in body if nearest(l) == j], key=lambda l: l["y"])
        example = mine[0]["text"] if mine else ""
        entries = []
        for l in mine[1:]:
            t = l["text"]
            m = re.match(r"^([A-Z0-9]{1,5})\s*/\s*(.+)$", t)
            if m:
                entries.append({"code": m.group(1), "meaning": m.group(2).strip()})
            elif entries:                       # "Regency" / "Champagne", "Lens type," / "Fixed"
                entries[-1]["meaning"] = clean(entries[-1]["meaning"] + " " + t)
        segments.append({"segment": c["heading"], "field": SEGMENT_FIELD.get(c["heading"], ""),
                         "example": example, "entries": entries})
    # Where the separators go. The bars between the example's parts are neither text nor vector
    # drawings - they are painted into the table's background picture - so they are found in the
    # rendered page: a short run of near-black pixels in the gap between two example parts.
    ex_y = min(l["y"] for l in body if l["y"] < page.rect.height - 50)
    ex_lines = sorted([l for l in body if abs(l["y"] - ex_y) < 6], key=lambda l: l["x0"])
    zoom = 4
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(0, ex_y - 2, page.rect.width, ex_y + 14))
    dash_after = []
    for a, b in zip(ex_lines, ex_lines[1:]):
        x_from, x_to = int((a["x1"] + 1) * zoom), int((b["x0"] - 1) * zoom)
        dark = 0
        for x in range(max(0, x_from), min(pix.width, x_to)):
            column = [sum(pix.pixel(x, y)[:3]) / 3 for y in range(pix.height)]
            dark += any(v < 60 for v in column)
        if dark >= 4 * zoom:                     # a bar at least ~4 points long
            dash_after.append(cols[nearest(a)]["heading"])
    return {"segments": segments, "example_tokens": [l["text"] for l in sorted(ex_lines, key=lambda l: l["x0"])],
            "dash_after": dash_after}


# ---------------------------------------------------------------- one product spread

COLOUR_WORDS = r"(black|white|grey|gray|silver|gold|brass|bronze|chrome|champagne|copper|nickel|nickle|steel|painted|specular|matt|sus)"
STYLE_MEANINGS = r"(curve|semi|pinhole|oval|square|shallow|alabaster|snoot|in-?out|recessed spotlight|surface spotlight|standard base|glass|glare shield|mini-line track|short$|trimless$)"


def classify(meaning, position):
    """What one legend line's part encodes, from its own wording."""
    m = meaning.lower()
    if position == 0:
        return "type"
    if "system power" in m:
        return "wattage"
    if re.search(r"\b\d{4}k\b", m):
        return "cct"
    if re.search(r"non ?dim|dimm|dali|phase|0-10", m):
        return "control"
    # "5 / style" (Sparkle step lights, p.452) is the face pattern - the product itself, one per
    # website product (35-1 ... 35-8) - not a beam: read as a beam, no Sparkle product could ever
    # be coded, since none offers a beam to choose (changed 22 Oct 2026).
    if m == "style":
        return "style"
    if re.search(r"beam angle|zoomable|wall ?wash|framing|asymmetric", m):
        return "beam_angle"
    if "24vdc" in m:
        return "voltage"
    if "trimless bezel" in m:
        return "trim_bezel"
    if m == "trimless" or m.startswith("trimless") and "bezel" not in m:
        return "trim"
    if re.search(r"honeycomb|lens|optional", m):
        return "accessories"
    if re.search(r"\bbezel\b", m) and re.search(COLOUR_WORDS, m):
        return "bezel_finish"
    if re.search(r"\bbaffle\b", m) and re.search(COLOUR_WORDS, m):
        return "baffle_finish"
    if re.search(STYLE_MEANINGS, m):
        return "style"
    # "36 / 36 engine" is a size; "B / black engine" is a finish - the colour word decides, so the
    # size test (a number with what it measures) comes first.
    if re.search(r"cutout|\d+\s*(engine|diameter|spots?)|diameter|spots?$|length|^\d+\s*mm$|\d+\s*\*\s*\d+", m) \
            and not re.search(COLOUR_WORDS, m):
        return "size"
    if re.search(COLOUR_WORDS, m) or re.search(r"\bstructure\b|regency|dark brass|solid brass", m):
        return "finish"
    if re.search(r"engine|mm$", m):
        return "size"
    return "other"


def read_legend(lines):
    """The decoded example: its tokens and one (token, meaning, field) per legend line."""
    head = next((l for l in lines if re.match(r"Code example", l["text"], re.I)), None)
    if head is None:
        return "", []
    col = [l for l in lines if l["y"] > head["y"] and abs(l["x0"] - head["x0"]) < 40 and l["y"] - head["y"] < 260]
    out, last = [], head["y"]
    for l in col:
        if l["y"] - last > 30:
            break
        out.append(l["text"])
        last = l["y"]
    example, legend = (out[0] if out else ""), []
    for i, t in enumerate(out[1:]):
        # "W/ white bezel      TB /  trimless bezel" prints two alternatives on one line
        for part in re.split(r"\s{3,}", t):
            m = re.match(r"^([A-Za-z0-9.]+)\s*/\s*(.+)$", part.strip())
            if m:
                legend.append({"token": m.group(1), "meaning": clean(m.group(2))})
    for i, e in enumerate(legend):
        e["field"] = classify(e["meaning"], i)
    return example, legend


def finish_vocabulary(doc, matrices):
    """Every finish name the catalogue itself uses: the colours of both matrices, ACME's finish
    page (PDF 52) and MOOD's (PDF 244), and the two a palette adds on its own (Bespoke, Black
    Nickel). A palette is read against these names, so a swatch label is recognised whatever way
    the page stacks its words, and the photometry table beside it (h(m), E(lx)) is never a finish."""
    names = {"Bespoke", "Black Nickel"}
    for m in matrices.values():
        for seg in m["segments"]:
            if seg["field"] in ("bezel_finish", "baffle_finish"):
                names.update(e["meaning"] for e in seg["entries"])
    for pno in (52, 244):
        text = clean(doc[pno - 1].get_text("text").replace("\n", " "))
        for n in ["Black Painted", "White Painted", "Bright Chrome", "Regency Champagne", "Antique Copper",
                  "Antique Brass", "Dark Brass", "Black Specular", "Silver Specular", "Black Matt", "White Matt",
                  "Silver Matt", "Gold Specular", "Grey Specular"]:
            if n in text:          # taken only where the page prints it
                names.add(n)
    return sorted(names, key=lambda n: (-len(n.split()), n))


# The headings a page prints over its swatches, and the group each starts. FLOW pages print
# "Structure" (the body colour) and "Baffle"; MOOD prints "bezel" and "baffle"; ACME "Standard"
# and "Curated Finishes".
HEADING_GROUP = {"bezel": "bezel", "Bezel": "bezel", "baffle": "baffle", "Baffle": "baffle",
                 "Standard": "standard", "Structure": "standard", "Curated": "curated"}


def read_palette(page_lines, words, vocabulary):
    """The finishes the page prints, grouped under the heading above them: bezel, baffle,
    Standard, Curated. A swatch's name is two stacked words (with a RAL number under some), and
    MOOD prints its baffles in two rows - so each column's words are matched to the catalogue's
    own finish names, longest first."""
    # A heading stands alone or reads "Curated Finishes (MOQ 20 applies)": "Standard Base
    # (Integrated Driver)" on a pendant is not one.
    heads = [w for w in words if w["text"] in HEADING_GROUP
             and not any(abs(o["y0"] - w["y0"]) < 2 and 0 <= o["x0"] - w["x1"] < 3 and o["text"] != "Finishes"
                         for o in words)]
    if not heads:
        # Some pages print their swatches as a plain list beside round swatches, with no heading
        # ("White Painted" / "Black Painted" on ORBIT PENDANT p.421): a line that is exactly a
        # finish name is then one swatch.
        names = []
        for line in page_lines:
            hit = next((n for n in vocabulary if line["text"].strip().lower() == n.lower()), None)
            if hit and hit not in [x["name"] for x in names]:
                names.append({"name": hit, "ral": None})
        return {"standard": names} if names else {}
    vocab = [(n, n.lower().split()) for n in vocabulary]
    groups = {}
    for h in heads:
        label = HEADING_GROUP[h["text"]]
        # the next heading to the right on the same band bounds this group
        right = min([g["x0"] for g in heads if g is not h and abs(g["y0"] - h["y0"]) < 30 and g["x0"] > h["x0"]]
                    + [h["x0"] + 400])
        below = [w for w in words if h["y1"] < w["y0"] < h["y1"] + 75 and h["x0"] - 6 <= w["x0"] < right - 6]
        # A label is printed either on one line ("Black Matt", MOOD) or stacked ("Black" over
        # "Painted", ACME), centred under its swatch. So words are first joined into the line
        # fragments they form, and fragments are grouped into a swatch's column by their centres.
        frags = []
        for w in sorted(below, key=lambda w: (round(w["y0"]), w["x0"])):
            f = frags[-1] if frags else None
            # a word space in this 5-6 pt type is about 1.5 pt; ACME's neighbouring swatch labels
            # ("Regency" | "Antique") sit only a little further apart, so the join is kept tight
            if f and abs(w["y0"] - f["y0"]) < 2 and 0 <= w["x0"] - f["x1"] < 3:
                f["words"].append(w["text"])
                f["x1"] = w["x1"]
            else:
                frags.append({"words": [w["text"]], "x0": w["x0"], "x1": w["x1"], "y0": w["y0"]})
        cols = {}
        mid = lambda f: (f["x0"] + f["x1"]) / 2
        for f in sorted(frags, key=lambda f: (round(mid(f) / 9), f["y0"])):
            key = min(cols, key=lambda k: abs(k - mid(f)), default=None)
            if key is None or abs(key - mid(f)) > 12:
                key = mid(f)
                cols[key] = []
            cols[key].append(f)
        names = []
        for key in sorted(cols):
            seq = [t for f in sorted(cols[key], key=lambda f: f["y0"]) for t in f["words"]]
            low = [t.lower() for t in seq]
            i = 0
            while i < len(seq):
                hit = next((n for n, parts in vocab if low[i:i + len(parts)] == parts), None)
                if hit is None:
                    i += 1
                    continue
                j = i + len(hit.split())
                ral = seq[j] if j < len(seq) and re.fullmatch(r"9\d{3}", seq[j]) else None
                if hit not in [x["name"] for x in names]:
                    names.append({"name": hit, "ral": ral})
                i = j
        if names:
            # a page can print two blocks under one heading (a pendant's canopy and body): one list
            have = {x["name"] for x in groups.get(label, [])}
            groups.setdefault(label, []).extend(x for x in names if x["name"] not in have)
    return groups


SPEC_LABELS = ["IP Rating", "Optics", "UGR", "CRI/RF/RG/R9", "CRI", "Lifetime", "CCT", "Typical Source",
               "Efficacy (lm/W)", "Control", "Binning", "Input", "Output", "Ambient temp", "Class",
               "Optional accessories", "Ceiling Thickness", "Material", "Certifications", "Voltage",
               "Power(W)", "LEDs/m"]


def read_specs(lines):
    """{label: value} from the facing page's spec block: a label on the left, its value to the right,
    continuing on the lines below until the next label."""
    labels = [l for l in lines if re.match(r"^(%s)\s*:?\s*$" % "|".join(re.escape(x) for x in SPEC_LABELS), l["text"])]
    if not labels:
        return {}
    x_val = min((l["x0"] for l in lines if l["x0"] > max(lb["x1"] for lb in labels) - 2
                 and l["y"] >= labels[0]["y"] - 2), default=None)
    out = {}
    for i, lb in enumerate(labels):
        y_next = labels[i + 1]["y"] if i + 1 < len(labels) else lb["y"] + 60
        vals = [l["text"] for l in lines if x_val is not None and abs(l["x0"] - x_val) < 25 and lb["y"] - 3 <= l["y"] < y_next - 2]
        out[lb["text"].rstrip(":").strip()] = clean(" ".join(vals))
    return out


CODE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,}[0-9][A-Z0-9./]*(?:-[A-Z0-9./()]+){1,6})\b")


def read_code_tables(text):
    """Every order code a spread prints - not the input voltage (AC220-240V) beside it."""
    return sorted({m.group(1) for m in CODE_RE.finditer(text)
                   if re.search(r"\d", m.group(1)) and not re.match(r"^(AC|DC)\d", m.group(1))})


def main():
    doc = fitz.open(SRC)
    sha = hashlib.sha256(SRC.read_bytes()).hexdigest()
    matrices = {name: read_matrix(doc, name) for name in MATRICES}
    vocabulary = finish_vocabulary(doc, matrices)
    spreads = []
    for n in range(2, doc.page_count + 1):
        right = doc[n - 1]
        if not re.search(r"Code example", right.get_text("text"), re.I):
            continue
        left = doc[n - 2]
        r_lines, l_lines = lines_of(right), lines_of(left)
        example, legend = read_legend(r_lines)
        big = sorted(l_lines, key=lambda l: -l["size"])
        title = [l["text"] for l in sorted(big[:3], key=lambda l: l["y"])]
        spreads.append({
            "pdf_pages": [n - 1, n], "printed_pages": [n - 1 - FOLIO_OFFSET, n - FOLIO_OFFSET],
            "title": title, "example": example, "example_code": re.sub(r"\s+", "", example),
            "legend": legend,
            "palette": (read_palette(l_lines, words_of(left), vocabulary)
                        or read_palette(r_lines, words_of(right), vocabulary)),
            "specs": read_specs(l_lines),
            "codes": read_code_tables(right.get_text("text") + "\n" + left.get_text("text")),
        })
    data = {"source": {"file": SRC.name, "pages": doc.page_count, "sha256": sha,
                       "read_by": "scripts/catalogues/read_duragreen_general_catalogue.py"},
            "matrices": matrices, "finish_vocabulary": vocabulary, "spreads": spreads}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(spreads)} spreads, {sum(len(s['codes']) for s in spreads)} printed codes, "
          f"matrices: {', '.join(f'{k} ({sum(len(s['entries']) for s in v['segments'])} entries)' for k, v in matrices.items())}")
    print("written:", OUT)


if __name__ == "__main__":
    main()
