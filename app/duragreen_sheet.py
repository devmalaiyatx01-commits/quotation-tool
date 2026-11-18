"""Reading a DuraGreen spec sheet, and a DuraGreen web page's variant card, into what a product is.

DuraGreen's truth is in two places: its website's variant cards (a short spec table, the spec
sheet's link, a picture) and the spec sheet PDF itself - the order-code tables ("6W 82 120°
AFL5706W-AL-30N120"), the "Code example" legend that decodes one code piece by piece, the
finish palettes, the separately ordered parts. Everything here reads those, and nothing here
touches the database.

These functions were written for the 19 Oct 2026 whole-catalogue run -
scripts/catalogues/build_duragreen_catalogue_db.py (reading every sheet) and
scripts/backfills/backfill_duragreen_npd.py (turning each reading into a product: build_product) -
and were moved here unchanged on 21 Oct, so that the app's own importer (app/duragreen_import.py:
one product page or one PDF at a time, from NPD (DuraGreen)) reads a sheet by exactly the same
rules. Two copies of a parser drift, and then the same sheet gives two answers. The scripts
import them from here.

The rule they all keep: nothing is guessed. A code that is not printed is left out, and a row
whose values do not line up with its heading is read by the values' shapes, keeping anything that
fits no column rather than dropping it.
"""
import json
import re

import pdfplumber

from app.duragreen import ENGINEER_GROUPS, ip_options, norm, spec_values


# Text from the photometry column that the PDF's reading order lands on a spec line: a test
# label ("10W Ra98 3000K 15°", sometimes without the beam) and the column's own heading.
PHOTO_LABEL_TAIL = re.compile(r"(?:^|\s+)\d+(?:\.\d+)?W\s+Ra\s?\d+\s+\d{4}K\b.*$")

COLUMN_HEADING_TAIL = re.compile(r"\s+datasheet\s+-\s.*$", re.I)

TRIPLE_TAIL = re.compile(r"\s+\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+\d+(?:\.\d+)?$")

HEADER_TAIL = re.compile(r"\s+h\(m\)\s+E\(lx\)\s+D\(m\).*$")

KEY_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9 /().*&-]{1,40}?)\s*[:：]\s*(.+)$")

# Printed key -> the app's field name, where the app has one.
CANONICAL = {
    "ip rating": "ip_rating", "ip zone": "ip_rating", "optics": "beam_angle", "ugr": "ugr",
    "cri": "cri", "cri/rf/rg/r9": "cri", "lifetime": "lifetime", "cct": "cct",
    "efficacy (lm/w)": "efficacy", "typical source efficacy (im/w)": "efficacy",
    "typical source efficacy (lm/w)": "efficacy", "control": "control", "binning": "binning",
    "input": "input_voltage", "output": "output", "ambient temp": "ambient_temp",
    "class": "class", "ceiling thickness": "ceiling_thickness", "material": "material",
    "optional accessories": "accessories", "optionsl accessories": "accessories",
    "certifications": "certifications", "certification": "certifications",
    "voltage": "input_voltage", "power(w)": "wattage", "leds/m": "leds_per_m",
    "min. cut length": "min_cut_length", "suitable tracks": "suitable_tracks",
    "fire resistance test": "fire_resistance", "plug type": "plug_type", "mounting": "mounting",
    "finishing": "finish", "standard finish": "finish", "cutout": "cutout",
}

CONTINUES = {"output", "certifications", "cct", "accessories", "control"}


def clean_value(value):
    value = HEADER_TAIL.sub("", value)
    value = PHOTO_LABEL_TAIL.sub("", value)
    value = COLUMN_HEADING_TAIL.sub("", value)
    while TRIPLE_TAIL.search(value):
        value = TRIPLE_TAIL.sub("", value)
    return " ".join(value.split()).strip(" ,;")


def sheet_lines(pages):
    return [" ".join(l.split()) for p in pages for l in (p["text"] or "").splitlines() if l.strip()]


def parse_specs(lines):
    """(printed key, canonical key, value) for every "Key: value" line, with wrapped values joined."""
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = KEY_LINE.match(line)
        acc = re.match(r"^Optional [Aa]ccessories\s+(?![:：])(.+)$", line)
        if acc and not m:
            m = None
            key, value = "Optional accessories", acc.group(1)
        elif m:
            key, value = m.group(1).strip().rstrip("*").strip(), m.group(2)
            if key.lower().startswith("efficacy") and i > 0 and lines[i - 1].startswith("Typical Source"):
                key = "Efficacy (lm/W)"
        else:
            i += 1
            continue
        canon = CANONICAL.get(key.lower())
        value = clean_value(value)
        # wrapped values: an Output's further "DC ..." lines, or a list that ended on / or ,
        while i + 1 < len(lines) and canon in CONTINUES and not KEY_LINE.match(lines[i + 1]) \
                and not lines[i + 1].startswith(("Typical Source", "h(m)")):
            nxt = clean_value(lines[i + 1])
            if (canon == "output" and nxt.startswith("DC ")) or (value.endswith(("/", ",")) and nxt):
                value = (value + (" " if canon == "output" else "") + nxt).strip()
                i += 1
            else:
                break
        if key.upper() != "SKU" and value:
            out.append((key, canon, value))
        i += 1
    return out


WATT = re.compile(r"^\d+(?:\.\d+)?W(?: x \d+)?$")

# a lowercase x is a twin head's multiplier as some sheets print it (OWQE6810Wx2-30N15-B)
CODE_TOKEN = re.compile(r"^[A-Z][A-Z0-9./\-x]{4,}$")

# The columns a code table's heading can name, tried in this order at each position of the
# heading. "lm system(92Ra)" must be tried before plain "lm system"; one sheet's "Lm/W" heading
# comes out of its text layer as "L m / C W".
HEADING_COLUMNS = [
    ("wattage", re.compile(r"W\s*system", re.I)),
    ("lumens_cri", re.compile(r"lm\s*system\s*\(\s*(\d+)\s*Ra\s*\)", re.I)),
    ("lumens", re.compile(r"lm\s*system", re.I)),
    ("efficacy", re.compile(r"L\s*m\s*/\s*C?\s*W", re.I)),
    ("optics", re.compile(r"optics", re.I)),
    ("cct", re.compile(r"CCT", re.I)),
    ("cri", re.compile(r"CRI", re.I)),
    ("length", re.compile(r"length", re.I)),
    ("spots", re.compile(r"Spots?", re.I)),
    ("input_voltage", re.compile(r"Input\s*vol\w*", re.I)),
    ("code", re.compile(r"code", re.I)),
]

ROW_FIELDS = ("wattage", "lumens", "lumens_92ra", "lumens_98ra", "efficacy", "optics", "cct", "cri",
              "length", "spots", "input_voltage", "extra")


def heading_columns(header):
    """A code table's heading -> the column each value of its rows belongs to, in order. None when
    the heading holds a word not listed above - its rows are then read by their values' shapes."""
    cols, pos = [], 0
    while pos < len(header):
        if header[pos].isspace():
            pos += 1
            continue
        for name, rx in HEADING_COLUMNS:
            m = rx.match(header, pos)
            if m:
                cols.append(f"lumens_{m.group(1)}ra" if name == "lumens_cri" else name)
                pos = m.end()
                break
        else:
            return None
    return cols


def normalise_row(tokens):
    """A row's printed tokens -> one token per value. A twin head prints its multiplier three ways
    ("10W x 2", "15W *2", "6W*2") and all become "10W x 2"; a range split over two tokens ("292-"
    "296", "20°-" "50°") is joined back, as is an efficacy split from its unit ("78Lm" "/W"); a spot
    count ("1 Spot") and an optic written in words ("Asymmetrical Wallwash") stay one value."""
    tokens, out, i = list(tokens), [], 0
    word = re.compile(r"[A-Za-z][A-Za-z\-]*")
    while i < len(tokens):
        t = tokens[i]
        # a twin head printed with its multiplier glued on: "10Wx" "2", "8Wx2", "462x" "2"
        m = re.fullmatch(r"(\d+(?:\.\d+)?W?)[xX×](\d*)", t)
        if m:
            if m.group(2):
                t = f"{m.group(1)} x {m.group(2)}"
            elif i + 1 < len(tokens) and tokens[i + 1].isdigit():
                t = f"{m.group(1)} x {tokens[i + 1]}"
                i += 1
        if t.endswith("-") and len(t) > 1 and i + 1 < len(tokens) and not CODE_TOKEN.match(t):
            tokens[i + 1] = t + tokens[i + 1]
            i += 1
            continue
        if t in ("x", "X", "×", "*") and out and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            out[-1] = f"{out[-1]} x {tokens[i + 1]}"
            i += 2
            continue
        m = re.fullmatch(r"[xX×*](\d+)", t)
        if m and out:
            out[-1] = f"{out[-1]} x {m.group(1)}"
            i += 1
            continue
        if t.lower() == "/w" and out:
            out[-1] += "/W"
            i += 1
            continue
        if word.fullmatch(t) and out and word.fullmatch(out[-1].replace(" ", "")) and i < len(tokens) - 1:
            out[-1] = f"{out[-1]} {t}"
            i += 1
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?W?)\*(\d+)", t)
        if m:
            t = f"{m.group(1)} x {m.group(2)}"
        if t.isdigit() and i + 1 < len(tokens) and tokens[i + 1].lower().startswith("spot"):
            t = f"{t} {tokens[i + 1]}"
            i += 1
        out.append(t)
        i += 1
    return out


def _row_values(tokens, columns):
    """One code-table row -> its fields. When the row has exactly one value per column its heading
    names, each value goes to that column. Otherwise each value is placed by its shape, and
    anything that fits no column is kept in `extra` - never dropped."""
    code, values = tokens[-1], tokens[:-1]
    row = dict.fromkeys(ROW_FIELDS)
    row["code"] = code
    cols = [c for c in (columns or []) if c != "code"]
    if columns and columns[-1] == "code" and len(cols) == len(values):
        for col, value in zip(cols, values):
            row[col] = value
        row["parsed_by"] = "heading"
    else:
        row["parsed_by"] = "value shapes"
        row["wattage"] = values[0] if values else None
        lumens, extra = [], []
        for t in values[1:]:
            if "°" in t:
                row["optics"] = t
            elif re.fullmatch(r"\d{4}K(-\d{4}K)?", t):
                row["cct"] = t
            elif re.fullmatch(r"Ra\d{2}|\d{2}Ra", t):
                row["cri"] = t
            elif re.fullmatch(r"\d+(?:\.\d+)?(mm|m)", t):
                row["length"] = t
            elif re.fullmatch(r"(?i)\d+(?:\.\d+)?\s*lm/w", t):
                row["efficacy"] = t
            elif re.fullmatch(r"(?i)\d+(?:\.\d+)?(?: x \d+)?(?:lm)?", t):
                lumens.append(t)
            elif re.fullmatch(r"(?i)(DC|AC)?\d+(?:\.\d+)?V", t):
                row["input_voltage"] = t
            elif re.fullmatch(r"(?i)\d+ spots?", t):
                row["spots"] = t
            else:
                extra.append(t)
        row["lumens"] = " / ".join(lumens) or None
        row["extra"] = " ".join(extra) or None
    # A lumens column can hold an efficacy ("99Lm/W", on strips) or carry its unit ("246Lm").
    if row["lumens"] and re.search(r"(?i)lm/w$", row["lumens"]):
        row["efficacy"], row["lumens"] = row["lumens"], None
    for key in ("lumens", "lumens_92ra", "lumens_98ra"):
        if row[key]:
            row[key] = re.sub(r"(?i)\s*lm$", "", row[key])
    if row["efficacy"]:
        row["efficacy"] = re.sub(r"(?i)\s*lm/w$", "", row["efficacy"])
    return row


# A row starts with its wattage - "10W", or a twin head's "10Wx 2" / "8Wx2" (ACME wall lights:
# until 22 Oct 2026 the glued x hid every row of those tables, and the products had no code).
ROW_START = re.compile(r"^\d+(?:\.\d+)?W(?=\b|[xX×*])")
# A table's version label: "Recessed" on a line of its own, "Trim" or "Surface" in front of the
# first row ("Trim 12W 717 15° RTPGE6812W-30N15-B"). Words only, at most three.
LABEL_LINE = re.compile(r"^[A-Za-z][A-Za-z\-]*(?: [A-Za-z][A-Za-z\-]*){0,2}$")
LABELLED_ROW = re.compile(r"^([A-Za-z][A-Za-z\-]*(?: [A-Za-z][A-Za-z\-]*){0,2}) (\d+(?:\.\d+)?W(?=\b|[xX×*]).*)$")


def _tidy_row(line):
    """A row as the other sheets print it: GLAMO's 48V and track sheets space the watts ("6 W
    437-433 15°- 50° MATL6806W-30N1550"), which hid every row of their tables until 26 Oct 2026."""
    line = re.sub(r"^(\d+(?:\.\d+)?)\s+W\b", r"\1W", line.strip())
    return re.sub(r"°\s*-\s*(?=\d)", "°-", line)


def _whole_codes(lines, start):
    """The codes printed under a table that has no ordinary rows - an RGBW strip's "R=4W 20Lm/W
    120° GS2840-180-12W-RGBWCW" - kept whole, for no configuration: the sheet does not say which."""
    out = []
    for line in lines[start:start + 8]:
        if re.match(r"^W\s+system", line, re.I):
            break
        for tok in line.split():
            if CODE_TOKEN.match(tok) and "-" in tok and re.search(r"\d", tok) and tok not in out:
                out.append(tok.rstrip("-"))
    return out


def parse_code_tables(lines):
    """Every row of every "W system ..." table. A row whose code wrapped onto the next line is
    joined back; a doubled header (two tables side by side) splits each row in two.

    A sheet printing several versions of a product labels each table: "Recessed" on its own line
    and "Trim" in front of the first row, then "Recessed" "Trimless", then "Surface" (the 68 Glass
    Pendant: RTPGE..., TLPGE..., STPGE...). Until 22 Oct 2026 a label ended the table before any row
    was read, and such sheets gave no codes at all. The label is kept on each row as `version`."""
    rows = []
    i = 0
    while i < len(lines):
        # "W system lm system optics code", and on some ranges "W System Lm System Optics Code"
        if not re.match(r"^W\s+system", lines[i], re.I):
            i += 1
            continue
        header = lines[i]
        columns = heading_columns(header)
        if columns and columns.count("code") > 1:         # two tables side by side
            columns = columns[:columns.index("code") + 1]
        i += 1
        label = []
        table_start, rows_before = i, len(rows)
        while i < len(lines):
            line = _tidy_row(lines[i])
            # label lines before the rows - only when a row follows within two lines
            if LABEL_LINE.match(line.strip()) and len(label) < 2 and any(
                    ROW_START.match(_tidy_row(lines[j])) or LABELLED_ROW.match(_tidy_row(lines[j]))
                    for j in range(i + 1, min(i + 3, len(lines)))):
                label.append(line.strip())
                i += 1
                continue
            m = LABELLED_ROW.match(line)
            if m and len(label) < 3:
                label.append(m.group(1))
                line = m.group(2)
            if not ROW_START.match(line):
                break
            tokens = line.split()
            if not CODE_TOKEN.match(tokens[-1]) and i + 1 < len(lines) \
                    and CODE_TOKEN.match(lines[i + 1].strip()) and " " not in lines[i + 1].strip():
                tokens.append(lines[i + 1].strip())
                i += 1
            tokens = normalise_row(tokens)
            # side-by-side tables: split where a wattage follows a code
            parts, start = [], 0
            for k in range(1, len(tokens)):
                if WATT.match(tokens[k]) and CODE_TOKEN.match(tokens[k - 1]):
                    parts.append(tokens[start:k])
                    start = k
            parts.append(tokens[start:])
            for part in parts:
                if len(part) >= 2 and CODE_TOKEN.match(part[-1]) and re.search(r"\d", part[-1]):
                    # a code never ends in a dash: "EFL0612W-30N35-" is the PDF's layout, not a version
                    part = part[:-1] + [part[-1].rstrip("-")]
                    r = _row_values(part, columns)
                    r["header"], r["line"] = header, lines[i]
                    r["version"] = " ".join(label)
                    rows.append(r)
            i += 1
        if len(rows) == rows_before:
            for code in _whole_codes(lines, table_start):
                r = _row_values([code], ["code"])
                r["header"], r["line"], r["version"] = header, code, ""
                rows.append(r)
    return rows


def base_watt(value):
    """"6W x 2" -> "6W": a twin head's per-head wattage, which is what the website lists."""
    return re.sub(r"\s*x\s*\d+$", "", (value or "").strip())


def natural_key(text):
    """Sort "6W", "8W", "10W" (and "5°", "15°", "120°") by their numbers, not as text."""
    return [(0, float(t), "") if re.fullmatch(r"\d+(?:\.\d+)?", t) else (1, 0.0, t.lower())
            for t in re.split(r"(\d+(?:\.\d+)?)", text or "") if t]


def decompose(code, wattage, optics=None):
    """Split an order code into its segments, anchored on the wattage the table gives for it
    ("AFL5706W-AL-30N120" with 6W -> AFL | 57 | 06W | -AL | 30 | N | 120).

    A code counts as split only when its parts are real parts: they join back into the code
    exactly, a CCT + control segment is found, and - where the table states the optics - the beam
    segment is those optics' own digits ("15°" -> 15, "5°" -> 05, "20°-50°" -> 2050). Anything
    less returns None: better no answer than a wrong one. A letter printed in front of the CCT
    digits ("-H30N15", only on the High Efficiency range, whose sheets print no legend) is kept
    as cct_prefix, and one in front of the beam digits ("30NL15", on step and shelf lights) as
    beam_prefix - both undecoded, since Duragreen does not say what they mean."""
    w = re.match(r"^(\d+(?:\.\d+)?)W", wattage or "")
    if not w:
        return None
    num = w.group(1)
    candidates = [num + "W"]
    if "." not in num and len(num) == 1:
        candidates.insert(0, "0" + num + "W")
    want_beam = "".join(re.findall(r"\d+", optics or ""))
    for wc in candidates:
        at = code.find(wc)
        if at <= 0:
            continue
        head, rest = code[:at], code[at + len(wc):]
        # The type may carry digits of its own - M22ST (48V track spots), FFL1524V (24V linear),
        # MAS35L - with the size, if any, the digits it ends on (M22ST|114). Until 22 Oct 2026 the
        # type had to be letters only, which left 48 such codes uncut and so never adaptable.
        hm = re.match(r"^([A-Z][A-Z0-9]*?)(\d*)$", head)
        if not hm:
            continue
        cm = re.search(r"-([A-Z]{0,2}?)(\d{2,4}|WD)([MD0ZCNXT])(.*)$", rest)
        if not cm:
            continue
        variant, pre, cct, ctl, tail = rest[:cm.start()], cm.group(1), cm.group(2), cm.group(3), cm.group(4)
        beam_pre = ""
        if want_beam:
            beam = None
            for lead in ("", tail[:1] if tail[:1].isalpha() else None):
                if lead is None:
                    continue
                # the digits must end where the beam part ends: 12° is not the start of "120"
                after = tail[len(lead):]
                beam = next((b for b in (want_beam.zfill(2), want_beam)
                             if after.startswith(b) and not after[len(b):len(b) + 1].isdigit()), None)
                if beam is not None:
                    beam_pre = lead
                    break
            if beam is None:
                continue          # the beam part does not say what the optics column says
        else:
            beam = re.match(r"(\d{2,3}|WS|F)?", tail).group(1) or ""
        suffix = tail[len(beam_pre) + len(beam):]
        if hm.group(1) + hm.group(2) + wc + variant + "-" + pre + cct + ctl + beam_pre + beam + suffix != code:
            continue
        return dict(type_code=hm.group(1), size_code=hm.group(2), wattage_code=wc, variant_code=variant,
                    cct_prefix=pre, cct_code=cct, control_code=ctl, beam_prefix=beam_pre, beam_code=beam,
                    suffix_code=suffix)
    return None


LEGEND_PAIR = re.compile(r"(?:^|\s)([A-Z0-9().\-]{1,8})\s*/\s+")


def classify_meaning(meaning, position):
    m = meaning.lower()
    # The first segment of every code is the product type ("AFL/ adjustable", "MFW/ wall wash
    # 58/58mm 30/30°") - checked first, since its wording can look like a size or a beam.
    if position == 1:
        return "type"
    if "system power" in m:
        return "wattage"
    if re.search(r"\d{4}k", m):
        return "cct"
    if re.search(r"non dim|dali|zigbee|casambi|0-10v|triac|mains dim", m):
        return "control"
    if "beam angle" in m or re.match(r"^\d+°", m):
        return "beam_angle"
    colour = re.search(r"black|white|brass|chrome|bronze|nickel|grey|gold|silver", m)
    if "bezel" in m:
        # "white bezel" is a finish; "trimless bezel" is a kind of bezel
        return "bezel_finish" if colour else "variant"
    if "structure" in m:
        return "body_finish"
    if "baffle" in m and ("black" in m or "white" in m):
        return "baffle_finish"
    if "optional" in m or "honeycomb" in m or "lens" in m:
        return "accessory"
    if re.search(r"cutout|engine|diameter|\d+mm", m):
        return "size"
    if "cri" in m or "rf/rg" in m:
        return "cri"
    if colour:
        return "finish"
    return "variant"


def parse_legend(lines):
    """(example code, [(position, token, meaning, attribute)]) from a "Code example:" block."""
    for i, line in enumerate(lines):
        if line.startswith("Code example") or line.startswith("Code Example"):
            break
    else:
        return None, []
    example = lines[i + 1] if i + 1 < len(lines) else ""
    pairs = []
    for line in lines[i + 2:]:
        if re.match(r"^W\s+system", line, re.I):
            break
        starts = list(LEGEND_PAIR.finditer(line))
        if not starts:
            break
        for n, m in enumerate(starts):
            end = starts[n + 1].start() if n + 1 < len(starts) else len(line)
            meaning = line[m.end():end].strip()
            if meaning:
                pairs.append((m.group(1).strip(), meaning))
    return example, [(k, tok, mean, classify_meaning(mean, k)) for k, (tok, mean) in enumerate(pairs, start=1)]


FINISH_VOCAB = ["Black Painted", "White Painted", "Bright Chrome", "Regency Champagne", "Antique Copper",
                "Antique Brass", "Dark Brass", "Black Nickel", "Solid Brass", "Antique Bronze", "Brushed Grey",
                "Polished Chrome", "Polished Brass", "Satin Nickel", "Black Matt", "White Matt", "Silver Matt",
                "Grey Specular", "Black Specular", "Silver Specular", "Gold Specular", "Rose Gold", "Bespoke",
                "Heritage Brass"]

VOCAB_LOWER = {n.lower() for n in FINISH_VOCAB}


def _stacked_palette(top_line, bottom_line):
    """A palette printed as two rows of words in columns - "Black White Bright ..." over "Painted
    Painted Chrome ..." - read column by column, or [] when the two lines are not one. Numbers on
    the lines are dimensions from the drawing beside it and are dropped. Accepted only when most
    of the pairs are finishes DuraGreen already names (FINISH_VOCAB): two ordinary lines of equal
    length must not become a palette."""
    top = [t for t in top_line.split() if not re.fullmatch(r"[\d.,]+", t)]
    bottom = [t for t in bottom_line.split() if not re.fullmatch(r"[\d.,]+", t)]
    if len(top) < 2 or len(top) != len(bottom) or not all(t.isalpha() for t in top + bottom):
        return []
    names = [f"{a} {b}" for a, b in zip(top, bottom)]
    known = sum(1 for n in names if n.lower() in VOCAB_LOWER)
    return names if known * 2 > len(names) else []


def parse_finishes(lines, text):
    """(palette, name, ral, moq, code) for every finish the sheet offers.

    DuraGreen prints finishes three ways, and each is read as printed:
      * stacked palettes, two rows of words in columns, under a heading - "Standard" (RAL numbers
        on the line below) and "Curated Finishes (MOQ 20 applies)"; the older sheets say
        "Standard Curated Finishes" in one heading;
      * a coded finish beside the dimension drawing - "-RC Regency Champagne" - which also gives
        DuraGreen's code for it;
      * otherwise a known finish name anywhere in the text, kept as "Listed on sheet". That last is
        loose - "Black White / Painted Painted" contains "White Painted" but not "Black Painted",
        which is how one sheet once read as offering only white - so it is recorded for analysis
        and never offered as a choice (backfill_duragreen_npd.py leaves it out)."""
    found, have = [], set()

    def add(palette, name, ral=None, moq=None, code=None):
        key = name.lower()
        if key not in have:
            have.add(key)
            found.append((palette, name, ral, moq, code))

    for i in range(len(lines) - 1):
        names = _stacked_palette(lines[i], lines[i + 1])
        if not names:
            continue
        before = " ".join(lines[max(0, i - 3):i])
        moq = re.search(r"MOQ\s*(\d+)", before)
        palette = ("Curated" + (f" (MOQ {moq.group(1)})" if moq else "") if re.search(r"curated", before, re.I)
                   and not re.search(r"standard\s+curated", before, re.I)
                   else "Standard Curated" if re.search(r"standard\s+curated", before, re.I) else "Standard")
        ral = re.findall(r"\b(9\d{3})\b", lines[i + 2]) if i + 2 < len(lines) else []
        for k, name in enumerate(names):
            add(palette, name, ral[k] if k < len(ral) else None, moq.group(1) if moq else None)

    flat = " ".join(text.split())
    for m in re.finditer(r"-([A-Z]{1,3})\s+(" + "|".join(re.escape(n) for n in FINISH_VOCAB) + r")\b", flat):
        add("Coded on sheet", m.group(2), code=m.group(1))
    for name in FINISH_VOCAB:
        if re.search(r"\b" + re.escape(name) + r"\b", flat):
            ral = re.search(re.escape(name) + r"\s+(9\d{3})\b", flat)
            add("Listed on sheet", name, ral.group(1) if ral else None)
    return found


def norm_cct(v):
    """"2700K - 6000K", "2700-6000K" and "2700k-6000k" all compare as "2700-6000"."""
    return re.sub(r"[\sKk]", "", v or "").replace("–", "-")


CONTROL_SYNONYMS = [
    ("On/off", "Non Dim", "On/off control is a non-dimming driver"),
    ("Non dim", "Non Dim", "same words"),
    ("Phase Cut", "Mains Dim", "phase-cut dimming is dimming on the mains"),
    ("Mains Dim", "Mains Dim", "same words"),
    ("Triac", "Mains Dim", "TRIAC dimming is phase-cut mains dimming"),
    ("0/1-10V", "0-10V", "the sheet's 0/1-10V is the matrix's 0-10V driver"),
    ("0-10V", "0-10V", "same words"),
    ("1-10V", "0-10V", "the sheet's 1-10V is the matrix's 0-10V driver"),
    ("Dali", "DALI", "same words"),
    ("DALI", "DALI", "same words"),
    ("Zigbee", "Zigbee", "same words"),
    ("Casambi", "Casambi", "same words"),
]


def split_list(value, seps=("/", ",")):
    """A printed list -> its items, on whichever separator it uses."""
    if not value:
        return []
    for sep in seps:
        if sep in value:
            return [p.strip() for p in value.split(sep) if p.strip()]
    return [value.strip()]


def split_controls(value):
    # "On/off" and "0/1-10V" contain a slash that is not a separator, so split on commas only
    return [p.strip() for p in (value or "").split(",") if p.strip()]


_CCT_RANGE = re.compile(r"(\d{4})\s*K?\s*[-–~]\s*(\d{4})\s*K", re.I)
_CCT_ONE = re.compile(r"(?<![\d.])(\d{4})\s*K\b", re.I)
_COLOUR_MODES = ("RGBCW", "RGBWW", "RGBW", "RGB")


def cct_options_from(text):
    """The CCTs a statement offers, each as an option: singles ("3000K"), ranges ("1800K-3000K")
    and colour modes (RGB, RGBW, RGBCW) - in the order printed, each once.

    The CCT line of a sheet or a website card is a sentence, not a list: "2700K, 3000K (2400K,
    3500K,4000K on request)", "4000K 1800-3000K(Warm Dim), 2700K - 6000K (Tunable White)". Split on
    commas or slashes it gave options such as "3000K (2400K" and "4000K on request)"; and where the
    sheet's photometric table bled into the line, "0.5 6420 0.22" and "110". So the CCTs are read
    out of the text instead, and anything that is not one is no option (22 Oct 2026: 111 such values
    across 610 products). A range's words (Warm Dim, Tunable White) are left off: the code tables
    key on the range itself."""
    t = str(text or "").replace("（", "(").replace("）", ")").replace("：", ":")
    found, spans = [], []
    for m in _CCT_RANGE.finditer(t):
        found.append((m.start(), "%sK-%sK" % (m.group(1), m.group(2))))
        spans.append((m.start(), m.end()))
    for m in _CCT_ONE.finditer(t):
        if not any(a <= m.start() < b for a, b in spans):
            found.append((m.start(), m.group(1) + "K"))
    plus_rgb = re.search(r"(\d{4})\s*K?\s*-\s*(\d{4})\s*K\s*\+\s*RGB\b", t, re.I)
    for mode in _COLOUR_MODES:
        for m in re.finditer(r"\b%s\b" % mode, t, re.I):
            if not any(a <= m.start() < b for a, b in spans) and not (plus_rgb and mode == "RGB"):
                found.append((m.start(), mode))
                spans.append((m.start(), m.end()))
    out = []
    for _pos, value in sorted(found):
        if plus_rgb and value == "%sK-%sK" % (plus_rgb.group(1), plus_rgb.group(2)):
            value += " + RGB"              # one mode: tunable white with RGB
        if norm(value) not in {norm(v) for v in out}:
            out.append(value)
    return out


def clean_cct_options(values, keep=()):
    """A product's CCT options with every entry read again by cct_options_from: "3000K (2400K"
    becomes 3000K and 2400K, "0.5 6420 0.22" goes. An entry that already is one CCT stays exactly
    as written, and so does every value in `keep` - one a BOQ line chose or a price adder names,
    which a re-reading never takes away. Returns (options, [(entry, what it became)])."""
    out, changed = [], []

    def add(v):
        if norm(v) not in {norm(o) for o in out}:
            out.append(v)

    for v in values or []:
        v = str(v)
        read = cct_options_from(v)
        if v in keep or [norm(x) for x in read] == [norm(v)]:
            add(v)
            continue
        changed.append((v, read))
        for x in read:
            add(x)
    return out, changed


OPTIC_WORDS = [("Zoomable", "Zoomable"), ("Zoom", "Zoomable"), ("Framing", "Framing"),
               ("Pinhole", "Pinhole"), ("Wall Wash", "Wall Wash"), ("Adjustable", "Adjustable"),
               ("Fixed", "Fixed")]

MOUNT_WORDS = [("Mini-Trim", "Mini-Trim"), ("Trimless", "Trimless"), ("Semi-Recessed", "Semi-Recessed"),
               ("Semi Recessed", "Semi-Recessed"), ("Surface", "Surface"), ("Pendant", "Pendant"),
               ("Track", "Track"), ("Wall", "Wall"), ("Spike", "Spike"), ("Recessed", "Recessed")]


def first_word(text, table):
    for word, label in table:
        if word.lower() in (text or "").lower():
            return label
    return None


TRACK_FINISH_PART = re.compile(r"([A-Z]{2,}[A-Z0-9]*\d+)-\s*\|\s*([A-Z])\s*\|\s*([A-Z])\s*\|")

TRACK_PART_LINE = re.compile(r"^([A-Z]\d{2}-[A-Z]{2,4}-\d+)$")


def parse_track_parts(lines):
    """Track-system parts, printed two ways: "TRACK 3L 1m XTS4100- | B | W |" (a part number
    taking a finish letter) and "M22-LSS-1" on its own line with the part's name under it.
    Returns (sku, name, finish letters or None)."""
    parts = []
    for i, line in enumerate(lines):
        last = 0
        for m in TRACK_FINISH_PART.finditer(line):
            label = line[last:m.start()].strip(" |") or None
            parts.append((m.group(1), label, f"{m.group(2)} / {m.group(3)}"))
            last = m.end()
        m = TRACK_PART_LINE.match(line)
        if m:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            # the line under a part number is its name - unless it is another part or a spec line
            name = nxt if nxt and not TRACK_PART_LINE.match(nxt) and ":" not in nxt else None
            parts.append((m.group(1), name, None))
    return parts


# The website's own category -> the app's typology, for a product the app does not have yet.
# Only categories whose name says unambiguously what the luminaire is; "Suspension", "Ceiling
# Surface", "Outdoor" and the like describe a mounting or a place, not a typology, so a new
# product from them gets "" and a person classifies it - the rule typology_classifier.py keeps.
SITE_TYPOLOGY = {
    "Downlights": "Downlight", "Spotlights": "Spotlight", "Track Lights": "Track Light",
    "LED Strip": "Flexible Strip Light", "Step Lights": "Step Light", "Power Supplies": "Driver",
}

# The importer's own label map (import_duragreen_catalog.MODAL_LABEL_TO_CANONICAL), repeated
# rather than imported: importing that module pulls in Playwright, which this script never needs.
WEB_LABELS = {
    "IP RATING": "ip_rating", "OPTICS": "beam_angle", "UGR": "ugr", "CCT": "cct",
    "CONTROL": "control", "INPUT": "input_voltage", "IP ZONE": "ip_rating",
    "CONTROL TYPE": "control", "MAXIMUM POWER": "wattage", "INPUT VOLTAGE": "input_voltage",
}


def read_pdf(path):
    # dedupe_chars: a few sheets draw every letter twice, one on top of the other, for a heavier
    # look - read as text that is "WW ssyysstteemm" and "MMPPWWEE55770066WW", and the 48V Alabaster
    # pendant's code table was invisible until 26 Oct 2026. Only a character printed exactly on top
    # of the same character is dropped, so every other sheet reads as before.
    with pdfplumber.open(path) as doc:
        return [{"text": p.dedupe_chars().extract_text() or ""} for p in doc.pages]


# Builder part columns, in code order -> the name a piece carries in VendorOrderCode.parts.
# The undecoded letter in front of a CCT ("H30") or a beam ("L15") travels with that part, as the
# builder already codes the option ("H30"), so swapping 30 for 27 can never strand an H.
PIECES = [("type", ("type_code",)), ("size", ("size_code",)), ("wattage", ("wattage_code",)),
          ("variant", ("variant_code",)), ("cct", ("cct_prefix", "cct_code")),
          ("control", ("control_code",)), ("beam_angle", ("beam_prefix", "beam_code")),
          ("suffix", ("suffix_code",))]


def code_pieces(row):
    """The code cut into named pieces with whatever sits between them kept as "sep" pieces, so
    the pieces always join back into the code exactly - or [] when they cannot."""
    code, pos, out = row["code"], 0, []
    for name, cols in PIECES:
        token = "".join(row[c] or "" for c in cols)
        if not token:
            continue
        i = code.find(token, pos)
        if i < 0:
            return []
        if i > pos:
            out.append(["sep", code[pos:i]])
        out.append([name, token])
        pos = i + len(token)
    if pos < len(code):
        out.append(["sep", code[pos:]])
    return out if "".join(t for _n, t in out) == code else []


def unique(values):
    seen, out = set(), []
    for v in values:
        if v and norm(v) not in seen:
            seen.add(norm(v))
            out.append(v)
    return out


def merged_options(old, new, priced=()):
    """The sheet's options in the order the product already lists them, new ones after. The
    sheet wins on what is offered - a value it no longer states is dropped - but people know these
    lists by their order, and a backfill that reshuffles every list reads as if it changed them.
    `priced` are values with a price adder, which stay whatever the sheet says: dropped here, the
    next save in NPD would delete their price, and a price entered must never vanish."""
    old = old if isinstance(old, list) else []
    wanted = {norm(v): v for v in new}
    keep = {norm(v) for v in priced}
    kept = [v for v in old if norm(v) in wanted or norm(v) in keep]
    seen = {norm(v) for v in kept}
    return kept + [v for v in new if norm(v) not in seen]


def option_value_for_code(options, attribute, code):
    """The option a code part stands for on this product ("30" -> "3000K"), preferring the one
    the product lists as a choice; None when the product names none."""
    hits = [o["value"] for o in options if o["attribute"] == attribute and (o["code"] or "") == code]
    return hits[0] if hits else None


MATRIX_HEADINGS = ["Type", "Engine", "Cut out", "Power", "Colour Temp", "Driver", "Beam Width",
                   "Bezel Colour", "Baffle Colour", "Options"]


def parse_matrix(pdf_path, page_no):
    """A "Specification Code" matrix read by word position. Headings are centred over their
    columns and entries left-aligned, so the columns are found from the entries' own left edges
    and each is given to the nearest heading."""
    with pdfplumber.open(pdf_path) as doc:
        words = doc.pages[page_no - 1].extract_words(x_tolerance=1.5)
    lines = {}
    for w in words:
        lines.setdefault(round(w["top"]), []).append(w)
    head_top = next((t for t, ws in sorted(lines.items())
                     if " ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"])).startswith("Type")
                     and "Driver" in " ".join(w["text"] for w in ws)), None)
    if head_top is None:
        return None
    hw = sorted(lines[head_top], key=lambda w: w["x0"])
    cols, i = [], 0
    while i < len(hw):
        for h in sorted(MATRIX_HEADINGS, key=lambda s: -len(s.split())):
            parts = h.split()
            if [w["text"] for w in hw[i:i + len(parts)]] == parts:
                cols.append((h, hw[i]["x0"]))
                i += len(parts)
                break
        else:
            i += 1
    body = sorted((t, ws) for t, ws in lines.items() if t > head_top + 2)
    starts = sorted(w["x0"] for _, ws in body for w in ws if re.match(r"^[A-Za-z0-9]{1,5}/", w["text"]))
    clusters = []
    for x in starts:
        if clusters and x - clusters[-1][-1] <= 4:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    col_x = {}
    for c in clusters:
        name = min(cols, key=lambda nc: abs(nc[1] - min(c)))[0]
        col_x[name] = min(col_x.get(name, min(c)), min(c))
    edges = sorted((x, name) for name, x in col_x.items())
    cells = {name: [] for name, _ in cols}
    example = {}
    for n, (top, ws) in enumerate(body):
        for w in sorted(ws, key=lambda w: w["x0"]):
            if n == 0:
                example[min(cols, key=lambda nc: abs(nc[1] - w["x0"]))[0]] = w["text"]
                continue
            owner = None
            for x, name in edges:
                if w["x0"] >= x - 2:
                    owner = name
            if owner is None:
                continue
            row = cells[owner]
            if row and row[-1][0] == top:
                row[-1][1] += " " + w["text"]
            else:
                row.append([top, w["text"]])
    out = []
    for order, (name, _) in enumerate(cols, start=1):
        entries = []
        for _, seg in cells[name]:
            m = re.match(r"^([A-Za-z0-9]{1,5})\s*/\s*(.+)$", seg)
            if m:
                entries.append([m.group(1), m.group(2).strip()])
            elif entries:
                entries[-1][1] += " " + seg
        out.append((order, name, example.get(name, ""), entries))
    return out


# catalogue product_options.attribute -> the app's field key
ATTR_FIELD = {"wattage": "wattage", "cct": "cct", "beam_angle": "beam_angle", "control": "control",
              "cri": "cri", "finish": "finish", "bezel_finish": "bezel_finish",
              "baffle_finish": "baffle_finish", "accessory": "accessories", "component": "components",
              "type": "type", "size": "size", "ip_rating": "ip_rating"}

# The ones a product is quoted on - get an option list when there is more than one value.
CHOICE_FIELDS = ("wattage", "cct", "beam_angle", "control", "cri", "finish", "bezel_finish",
                 "baffle_finish", "ip_rating")
# One IP rating is a fact about the fitting, kept as a plain field; only two are a choice.
LIST_ONLY_WHEN_SEVERAL = ("ip_rating",)

FINISH_ATTRS = ("finish", "bezel_finish", "baffle_finish", "body_finish")

# catalogue products columns -> field key (labels/groups come from app/duragreen.py)
PRODUCT_COLUMNS = {"size": "size", "optic_type": "optic_type", "mounting_style": "mounting_style",
                   "cutout": "cutout_dimensions", "ceiling_thickness": "ceiling_thickness",
                   "ip_rating": "ip_rating", "ugr": "ugr", "cri": "cri", "cri_detail": "cri_detail",
                   "lifetime": "lifetime", "efficacy": "efficacy", "binning": "binning",
                   "input_voltage": "input_voltage", "output": "output", "ambient_temp": "ambient_temp",
                   "class": "class", "material": "material", "certifications": "certifications",
                   "project_reference": "project_reference"}


def part_status(opt):
    """How sure an option's code is - see models.VendorCodePart."""
    if opt["status"] == "matched":
        return "matched"
    note = opt["note"] or ""
    if opt["status"] == "range" or ", range)" in note or "matrix (range)" in note:
        return "range"
    return "stated"


def _is_trimless_code(row):
    """A code for the trimless version: -TL- as a part of its own ("AFL7810W-SS-TL-30N10-SB")."""
    return "TL" in re.split(r"[-/]", (row["variant_code"] or "") + "-" + row["code"])


def split_shared_sheet(data):
    """One sheet serves a mini-trim product and its trimless twin, and prints both products'
    codes (78 Short Snoot: AFL7810W-SS-30N10-SB and AFL7810W-SS-TL-30N10-SB). Each product keeps
    only its own: the -TL rows go to the product named Trimless, the rest to the other.

    Decided per product from its own rows, not by which file they came from: the twins often
    point at two downloaded copies of the same sheet (name.pdf, name__2.pdf), so grouping by file
    found nothing - and the code check (verify_codes.py) then caught 103 configurations that
    would have been given the twin's code. A product whose rows are all one kind is left alone."""
    moved = 0
    for pid, rows in list(data["codes"].items()):
        if not rows or pid not in data["products"]:
            continue
        name = data["products"][pid]["name"] or ""
        keep = None
        kinds = {_is_trimless_code(r) for r in rows}
        if len(kinds) > 1:
            trimless = "trimless" in name.lower()
            keep = [r for r in rows if _is_trimless_code(r) == trimless]
        # Numbered siblings: Sparkle's sheet prints all five face patterns' codes, each ending in
        # its number (WFL351.3W-1 ... WFL351.3W-8), and the website sells each pattern as its own
        # product, named by it ("35-1 Step Light"). Each keeps the codes ending in its own number.
        numbers = {sibling_number_of(r["code"]) for r in rows}
        named = re.search(r"\b\d+-(\d)\b", name)
        if None not in numbers and len(numbers) > 1 and named and named.group(1) in numbers:
            keep = [r for r in (keep if keep is not None else rows) if sibling_number_of(r["code"]) == named.group(1)]
        if keep:
            moved += len(rows) - len(keep)
            data["codes"][pid] = keep
    return moved


def sibling_number_of(code):
    """The single digit a numbered sibling's code ends in ("WFL351.3W-1" -> "1"), else None."""
    m = re.search(r"-(\d)$", code or "")
    return m.group(1) if m else None


def name_ccts_from_codes(codes, cct_options):
    """Names the CCT of rows the sheet's table prints alike, from their own codes.

    A neon strip's table prints 7W 115° four times, as E67-BS0612-07W-27, -30, -40 and -65: the
    rows differ only in a last two-digit piece, and DuraGreen writes a CCT as its first two digits.
    Left alone, the four look like four codes for one configuration and none can be used. Each is
    named only when every such piece names exactly one of the product's own CCTs and no two the
    same one - so a 24 (24V on the LED strips) or a range (2700K-6500K) never becomes a CCT.
    The piece is kept as printed["cct_from_code"], so NPD can say where the CCT came from.
    Returns how many rows were named."""
    groups = {}
    for c in codes:
        if not c["config"].get("cct"):
            groups.setdefault(json.dumps(c["config"], sort_keys=True), []).append(c)
    named = 0
    for rows in groups.values():
        if len(rows) < 2:
            continue
        cut = [re.match(r"^(.*-)(\d{2})$", c["code"]) for c in rows]
        if not all(cut) or len({m.group(1) for m in cut}) != 1:
            continue
        hits = {}
        for c, m in zip(rows, cut):
            match = [o for o in cct_options if re.fullmatch(m.group(2) + r"\d{2}\s*K", str(o).strip(), re.I)]
            if len(match) != 1:
                hits = None
                break
            hits[c["code"]] = match[0]
        if not hits or len(set(hits.values())) != len(hits):
            continue
        for c, m in zip(rows, cut):
            c["config"]["cct"] = hits[c["code"]]
            c["printed"]["cct_from_code"] = m.group(2)
            named += 1
    return named


PENDANT_MOUNTING = {"RTP": "Recessed Trim", "TLP": "Recessed Trimless", "STP": "Surface"}


def name_mountings(codes, labels, options):
    """A sheet that labels each of its code tables prints one version of the product per table:
    the 68 Glass Pendant's "Recessed Trim" (RTPGE...), "Recessed Trimless" (TLPGE...) and "Surface"
    (STPGE...). The product is then quoted on that choice - "mounting", in the sheet's own words -
    and each row says which version it is. Only when every row carries a label and there are at
    least two: a sheet that labels some tables and not others does not say what the rest are.
    Returns how many rows were named."""
    if codes and not any(labels):
        # An unlabelled sheet of pendant versions: DuraGreen's own labelled sheets and catalogue
        # legends name the canopy prefixes ("RTP = recessed pendant", "STP = surface pendant"),
        # and every labelled table agrees (22 Oct 2026: 13 of 13 each). Only these three.
        heads = [re.match(r"[A-Z]+", c["code"]).group(0)[:3] if re.match(r"[A-Z]+", c["code"]) else "" for c in codes]
        if all(h in PENDANT_MOUNTING for h in heads):
            labels = [PENDANT_MOUNTING[h] for h in heads]
    kinds = list(dict.fromkeys(l for l in labels if l))
    if len(kinds) < 2 or not codes or not all(labels):
        return 0
    for c, label in zip(codes, labels):
        c["config"]["mounting"] = label
    options["mounting"] = kinds
    return len(codes)


DC_LABEL = "24VDC"          # as the catalogue's legends write it ("24 / 24VDC")


def name_voltages(codes, options, fields):
    """A product sold in a mains and a 24V DC version gets a voltage choice, and each printed row
    the voltage it is for.

    FOCUS 70's sheet says "Input: 24VDC Version / AC 220-240V Version" and prints both versions'
    codes - UL7007W-27N15 and UL7008W-27N15-24 - and its catalogue page explains the piece:
    "24 / 24VDC". Until 22 Oct 2026 the two were read as two versions of the product, and every
    configuration was refused. Only when both kinds of row are printed (cut, with -24 as the
    piece after the beam) and the sheet states a 24V DC version. Returns how many rows were named."""
    def ends_dc(c):
        return any(n == "suffix" and t == "-24" for n, t in (c["parts"] or []))
    dc_rows = [c for c in codes if c["parts"] and ends_dc(c)]
    mains_rows = [c for c in codes if c["parts"] and not ends_dc(c)]
    # The sheet states it on its Input line, or in a Voltage column of the table itself (the ACME 68
    # outdoor trimless prints "10W 24VDC 860 15° ...-24" beside "10W AC220-240V 860 15° ...").
    said = " ".join([str(fields.get("input_voltage") or "")] +
                    [str(v) for c in codes for v in (c.get("printed") or {}).values()])
    if not dc_rows or not mains_rows or not re.search(r"24\s*V?\s*DC|DC\s*24", said, re.I):
        return 0
    m = re.search(r"AC\s*\d{3}\s*-\s*\d{3}\s*V|\d{3}\s*-\s*\d{3}\s*V\s*~?\s*AC", said, re.I)
    mains = re.sub(r"\s+", " ", m.group(0)).strip() if m else "AC 220-240V"
    for c in dc_rows:
        c["config"]["voltage"] = DC_LABEL
    for c in mains_rows:
        c["config"]["voltage"] = mains
    options["voltage"] = [mains, DC_LABEL]
    return len(dc_rows) + len(mains_rows)


def build_product(cat, pid, web):
    """Everything the app row should hold, from the catalogue - fields, options, params, codes."""
    p = cat["products"].get(pid) or {}
    opts = [o for o in cat["options"].get(pid, [])]
    # What the product is offered in. A finish read from the sheet's decoded "Code example" is
    # that one example ("...-W" = white bezel), not the colours it comes in - offered alone, an
    # ACME downlight sold in seven bezel colours would show White as its only choice. It stays
    # a code part (below), and is not an option.
    # A finish found only by its name appearing somewhere in the sheet ("Listed on sheet") is a
    # reading, not a palette: "Black White / Painted Painted" contains "White Painted" and not
    # "Black Painted". Only the palettes and coded finishes the parser read as laid out are offered.
    offered = [o for o in opts if o["status"] in ("stated", "matched")
               and not (o["source"] == "code_legend" and o["attribute"] in FINISH_ATTRS)
               and not (o["source"] == "sheet_finishes" and (o["note"] or "").startswith("Listed on sheet"))]

    options = {}
    for attr, key in ATTR_FIELD.items():
        if key not in CHOICE_FIELDS:
            continue
        vals = unique(o["value"] for o in offered if o["attribute"] == attr)
        vals = unique(v for value in vals for v in spec_values(key, value))
        if len(vals) > 1:
            options[key] = vals
        elif vals and key not in LIST_ONLY_WHEN_SEVERAL:
            options[key] = vals            # a single option is still the product's own statement

    fields = {}
    for col, key in PRODUCT_COLUMNS.items():
        if p.get(col):
            fields[key] = str(p[col]).strip()
    for attr, key in ATTR_FIELD.items():
        vals = unique(o["value"] for o in offered if o["attribute"] == attr)
        vals = unique(v for value in vals for v in spec_values(key, value))
        if vals and key not in ("type",):
            fields[key] = " / ".join(vals)
    # A CRI printed in the sheet's table with no option behind it ("92Ra") reads the same way.
    if fields.get("cri") and "cri" not in options:
        vals = spec_values("cri", fields["cri"])
        fields["cri"] = " / ".join(vals)
        if len(vals) > 1:
            options["cri"] = vals

    params = {}
    for group, items in ENGINEER_GROUPS:
        block = {label: fields[key] for key, label in items if fields.get(key)}
        if block:
            params[group] = block
    params["DURAGREEN SPEC"] = {label: value for label, value in json.loads(web["spec"] or "[]")}
    notes = json.loads(web["notes"] or "[]")
    if notes:
        params["DURAGREEN NOTES"] = {"Note %d" % (i + 1): n for i, n in enumerate(notes)}
    downloads = json.loads(web["downloads"] or "[]")
    if downloads:
        params["DURAGREEN DOWNLOADS"] = {d["label"]: d["url"] for d in downloads}

    codes, labels = [], []
    for n, r in enumerate(cat["codes"].get(pid) or []):
        # the label over the row's table ("Surface"); a SQLite row has keys() but no get()
        labels.append((r["version_label"] if "version_label" in r.keys() else None) or "")
        config, printed = {}, {}
        for k in ("wattage", "lumens", "lumens_92ra", "lumens_98ra", "efficacy", "optics", "cct", "cri",
                  "length", "spots", "input_voltage", "extra"):
            if r.get(k):
                printed[k] = r[k]
        if r["wattage"]:
            config["wattage"] = r["wattage"]
        if r["optics"]:
            config["beam_angle"] = r["optics"]
        if r["cct"]:
            config["cct"] = r["cct"]
        elif r["cct_code"]:
            token = (r["cct_prefix"] or "") + r["cct_code"]
            # A code part the product names no option for can never match a choice - "?" says so.
            config["cct"] = option_value_for_code(opts, "cct", token) or "?" + token
        if r["control_code"]:
            config["control"] = (option_value_for_code(offered, "control", r["control_code"])
                                 or option_value_for_code(opts, "control", r["control_code"])
                                 or "?" + r["control_code"])
        if r["cri"]:
            config["cri"] = r["cri"]
        codes.append({"code": r["code"], "config": config, "printed": printed,
                      "parts": code_pieces(r) if r["decomposed"] else [], "sort_order": n})
    name_ccts_from_codes(codes, options.get("cct") or [])
    name_voltages(codes, options, fields)
    name_mountings(codes, labels, options)

    parts = []
    for o in opts:
        key = ATTR_FIELD.get(o["attribute"])
        if key and o["code"]:
            parts.append({"attribute": key, "value": o["value"], "code": o["code"],
                          "status": part_status(o), "source": o["source"], "note": o["note"] or ""})
    doc = cat["docs"].get(p.get("source_document_id")) or {}
    return {"fields": fields, "options": options, "params": params, "codes": codes, "parts": parts,
            "pdf_name": doc.get("file_name") if doc.get("read_ok") else None}
