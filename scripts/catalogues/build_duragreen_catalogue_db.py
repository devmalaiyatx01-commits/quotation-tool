"""Builds a standalone, structured database of the Duragreen catalogue:
"Duragreen Catalogue/duragreen_catalogue.sqlite".

WHY THIS EXISTS
InHouse products in the app are configurable: one product carries every wattage, CCT, beam,
finish and trim it is sold in, and a product code is built from the choices. Duragreen's 357
rows are not. They were scraped from the website's variant cards (import_duragreen_catalog.py),
which give a short spec table and nothing else - no finishes, no trim, no cut-out, no order
codes. All of that IS published, in the spec-sheet PDF each row already links to:

  * a table of real order codes per wattage x optic   ("6W 82 120° AFL5706W-AL-30N120")
  * a "Code example" legend decoding one code segment by segment
  * for the ACME and MOOD ranges, a full "Specification Code" matrix: every option of every
    segment with its code (Colour Temp 2760 = 2700K-6000K, Driver D = DALI, Bezel BN = Black
    Nickel ...)
  * the finish palettes, the separately-orderable components (engine / bezel / baffle SKUs),
    the cut-out and ceiling thickness, class, material and certifications

This script reads those PDFs and the app's own rows, and writes them into one SQLite file
shaped for configuration. It is an analysis database: it never writes to the app's database,
and nothing in the app reads it. Bringing it into the app is a separate, reviewed step.

PROVENANCE IS PART OF EVERY ROW
Every option row says where it came from (`source`) and how sure it is (`status`):
  stated   - printed by Duragreen for this product (its own sheet, its own code table)
  range    - printed by Duragreen for the range this product belongs to (the ACME/MOOD
             matrix), applied here because the product is in that range
  matched  - the value on the sheet was matched to a matrix entry by meaning, not spelling
             ("On/off" -> "N/Non Dim"); the synonym used is in the value_synonyms table
Nothing is guessed: a code that is not printed anywhere is left NULL, never derived.

When the website and the PDF disagree, the PDF wins - the rule backfill_duragreen_wattage_
from_pdf.py already established, since the PDF is what a person actually sees - and the
disagreement is recorded in app_gaps.

Run from the repo root (reads the local database through DATABASE_URL, and data/uploads):
    python scripts/catalogues/build_duragreen_catalogue_db.py
Re-running rebuilds the file from scratch, and then writes the same data as an Excel workbook
beside it (export_duragreen_catalogue_xlsx.py).
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
import datetime
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.stdout.reconfigure(encoding="utf-8")

import pdfplumber

from app.database import SessionLocal
from app import models
from app.duragreen_sheet import (  # the sheet-reading rules, shared with the app's importer
    CANONICAL,
    sheet_lines,
    parse_specs,
    parse_code_tables,
    base_watt,
    natural_key,
    decompose,
    parse_legend,
    parse_finishes,
    norm_cct,
    CONTROL_SYNONYMS,
    split_list,
    split_controls,
    cct_options_from,
    OPTIC_WORDS,
    MOUNT_WORDS,
    first_word,
    parse_track_parts,
    SITE_TYPOLOGY,
    WEB_LABELS,
    read_pdf,
    MATRIX_HEADINGS,
    parse_matrix,
)

MAKE = "Duragreen"
OUT_DIR = _REPO_ROOT / "Duragreen Catalogue"
DB_PATH = OUT_DIR / "duragreen_catalogue.sqlite"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE source_documents (
    id INTEGER PRIMARY KEY,
    file_name TEXT NOT NULL,
    pages INTEGER, chars INTEGER,
    read_ok INTEGER NOT NULL,          -- 0 = the PDF could not be read
    note TEXT
);

-- A family is one product page on duragreenlighting.co.uk ("ACME SQUARE"), the level a
-- InHouse "series" (NYX) sits at. Its products are the variant cards on that page.
CREATE TABLE families (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,         -- as the app stores it (Specsheet.series)
    range_name TEXT,                   -- ACME, MOOD, ORBIT ... (the family's own name where it is
                                       -- named after a category, e.g. "48V Track Lights")
    category TEXT,                     -- the website category, as the app's typology
    code_matrix TEXT                   -- ACME / MOOD when the range publishes a Specification Code
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,            -- = the app's specsheets.id, so every row maps back
    family_id INTEGER REFERENCES families(id),
    name TEXT NOT NULL,                -- the variant card's name
    category TEXT,                     -- website category (app typology)
    sheet_title TEXT,                  -- the first lines of the spec sheet, as printed
    size TEXT,                         -- engine / cut-out size, e.g. "57mm"
    optic_type TEXT,                   -- Fixed / Adjustable / Zoomable / Framing / Pinhole / Wall Wash
    mounting_style TEXT,               -- Mini-Trim / Trimless / Surface / Semi-Recessed / Pendant / Track ...
    cutout TEXT,
    ceiling_thickness TEXT,
    ip_rating TEXT, ugr TEXT, cri TEXT, cri_detail TEXT, lifetime TEXT, efficacy TEXT,
    binning TEXT, input_voltage TEXT, output TEXT, ambient_temp TEXT, class TEXT,
    material TEXT, certifications TEXT,
    code_prefix TEXT,                  -- the type letters every order code of this product starts with
    example_code TEXT,                 -- the "Code example" line, as printed
    order_code_count INTEGER,
    project_reference TEXT,            -- "DuraGreen project: ..." where the sheet names one
    source_document_id INTEGER REFERENCES source_documents(id)
);

-- The configurable axes: one row per (product, attribute, value), the shape of the app's
-- Specsheet.options. `code` is the order-code segment for that value, where Duragreen prints one.
CREATE TABLE product_options (
    product_id INTEGER REFERENCES products(id),
    attribute TEXT NOT NULL,           -- wattage, cct, beam_angle, control, cri, finish, bezel_finish,
                                       -- baffle_finish, accessory, type, size, ip_rating
    value TEXT NOT NULL,
    code TEXT,
    source TEXT NOT NULL,              -- order_codes, code_legend, code_matrix, sheet_spec, sheet_finishes,
                                       -- sheet_components, website
    status TEXT NOT NULL,              -- stated / range / matched (see the script's docstring)
    note TEXT,
    UNIQUE (product_id, attribute, value)
);

-- Every row of every "W system ... code" table: the real, orderable codes.
CREATE TABLE order_codes (
    id INTEGER PRIMARY KEY,
    product_id INTEGER REFERENCES products(id),
    code TEXT NOT NULL,
    wattage TEXT,                      -- as printed; a twin head reads "10W x 2"
    lumens TEXT,                       -- system lumens, where the table has one lumen column
    lumens_92ra TEXT, lumens_98ra TEXT,  -- where it gives one lumen column per CRI
    efficacy TEXT,                     -- lm/W, where the table prints it
    optics TEXT, cct TEXT, cri TEXT, length TEXT, spots TEXT, input_voltage TEXT,
    extra TEXT,                        -- anything else on the row, kept rather than dropped
    parsed_by TEXT,                    -- heading: one value per column the heading names;
                                       -- value shapes: the values did not line up with the heading
    type_code TEXT, size_code TEXT, wattage_code TEXT, variant_code TEXT,
    cct_prefix TEXT,                   -- a letter before the CCT digits ("H"), not decoded by Duragreen
    cct_code TEXT, control_code TEXT,
    beam_prefix TEXT,                  -- a letter before the beam digits ("L"), not decoded by Duragreen
    beam_code TEXT, suffix_code TEXT,
    decomposed INTEGER NOT NULL,       -- 1 when split into real parts (see decompose() in the script)
    table_header TEXT, source_line TEXT,
    version_label TEXT                 -- the label over the row's table ("Recessed Trim", "Surface"), if any
);

-- The "Code example" legend: one printed code taken apart segment by segment.
CREATE TABLE code_legend (
    product_id INTEGER REFERENCES products(id),
    position INTEGER, token TEXT, meaning TEXT, attribute TEXT
);

-- The ACME and MOOD "Specification Code" matrices: every option of every code segment.
CREATE TABLE code_matrices (
    matrix TEXT NOT NULL, segment_order INTEGER, segment TEXT NOT NULL,
    code TEXT NOT NULL, meaning TEXT NOT NULL, example_token TEXT,
    printed_on_products INTEGER        -- how many spec sheets carry this exact matrix
);

-- Parts ordered separately: light engines, bezels, baffles.
CREATE TABLE components (
    product_id INTEGER REFERENCES products(id),
    sku TEXT NOT NULL, kind TEXT, note TEXT
);

CREATE TABLE finishes (
    product_id INTEGER REFERENCES products(id),
    palette TEXT NOT NULL,             -- Standard Curated (MOQ 20) / ACME bezel / MOOD baffle / Listed on sheet
    name TEXT NOT NULL, ral TEXT, code TEXT, moq TEXT
);

-- Every "Key: value" the sheet prints, and every spec the website gave, as written.
CREATE TABLE specs (
    product_id INTEGER REFERENCES products(id),
    source TEXT NOT NULL,              -- pdf / website
    key TEXT NOT NULL, canonical_key TEXT, value TEXT
);

CREATE TABLE drivers (
    id INTEGER PRIMARY KEY,            -- = the app's specsheets.id
    name TEXT, brand TEXT, output_type TEXT, control TEXT, tunable INTEGER,
    output_voltage TEXT, wattage TEXT, ip_rating TEXT, input_voltage TEXT,
    source_document_id INTEGER REFERENCES source_documents(id)
);

-- How sheet wording was matched to a matrix entry - every "matched" option uses one of these.
CREATE TABLE value_synonyms (attribute TEXT, sheet_value TEXT, matrix_meaning TEXT, why TEXT);

-- Everything found wrong or missing. kind:
--   app_gap          the app lacks, or misstates, something the catalogue says
--   source_conflict  Duragreen's own sources disagree (website vs spec sheet, or a sheet with itself)
--   unreadable       no usable spec sheet
CREATE TABLE issues (
    product_id INTEGER, kind TEXT NOT NULL, field TEXT, app_value TEXT, catalogue_value TEXT, issue TEXT
);

-- Where each product was read from on the website (--from-crawl only; empty otherwise).
-- app_id is the app's specsheets.id, or NULL for a product the website has and the app
-- does not - its product_id is then 900000+ so it can never be mistaken for an app row.
CREATE TABLE web_pages (
    product_id INTEGER, app_id INTEGER, url TEXT, page_title TEXT, card_name TEXT,
    variation_id TEXT, category TEXT, description TEXT, tagline TEXT, image_url TEXT,
    spec_sheet TEXT, downloads TEXT, notes TEXT, finishes TEXT, spec TEXT,
    matched_by TEXT                    -- how app_id was found: series and name / name and spec sheet ...
);

CREATE VIEW v_configurator AS
SELECT p.id AS product_id, f.name AS family, p.name AS product, o.attribute,
       group_concat(o.value, ' / ') AS options,
       group_concat(coalesce(o.code, '?'), ' / ') AS codes,
       count(*) AS option_count
FROM product_options o JOIN products p ON p.id = o.product_id
LEFT JOIN families f ON f.id = p.family_id
GROUP BY p.id, o.attribute;

CREATE VIEW v_family_overview AS
SELECT f.name AS family, f.range_name, f.category, f.code_matrix,
       count(DISTINCT p.id) AS products,
       (SELECT count(*) FROM order_codes oc JOIN products p2 ON p2.id = oc.product_id
        WHERE p2.family_id = f.id) AS order_codes
FROM families f LEFT JOIN products p ON p.family_id = f.id
GROUP BY f.id;
"""

# ------------------------------------------------------------------ reading a sheet
# The readers of a sheet's specs, code tables, legend, finishes and parts live in
# app/duragreen_sheet.py (the app's importer uses them too). Only the range matrices, which this
# script alone reads, are read here.


DRIVER_BRANDS = ("TCI", "TRIDONIC", "LTECH", "MEANWELL", "OSRAM", "DuraGreen")
# Families the website names after a category rather than a range (checked against all 69).
CATEGORY_NAMED_FAMILIES = {"HIGH EFFICIENCY RECESSED", "Shelf & Joinery Lights"}


def parse_driver_name(name):
    tokens = name.split()
    brand = tokens[0] if tokens and tokens[0] in DRIVER_BRANDS else None
    kind = next((t for t in tokens if t in ("CC", "CV")), None)
    ctl = re.search(r"\b(ON-OFF|DALI,0/1-10V|DALI|0-10V|Triac|ZIGBEE|CASAMBI|PUSH)\b", name, re.I)
    volt = re.search(r"\b(\d+(?:\.\d+)?-\d+V|\d+V)\b", name)
    watt = re.search(r"\b(\d+(?:\.\d+)?W)\b", name)
    ip = re.search(r"\b(IP\d\d)\b", name)
    return dict(brand=brand, output_type=kind, control=ctl.group(1) if ctl else None,
                tunable=1 if "tunable" in name.lower() else 0,
                output_voltage=volt.group(1) if volt else None,
                wattage=watt.group(1) if watt else None, ip_rating=ip.group(1) if ip else None)


# ------------------------------------------------------------------ build

def load_app_rows():
    db = SessionLocal()
    try:
        rows = (db.query(models.Specsheet).filter(models.Specsheet.make == MAKE)
                .order_by(models.Specsheet.id).all())
        return [dict(id=r.id, name=r.product_name or "", category=r.typology or "", series=r.series or "",
                     family=r.family or "", variant=r.variant or "", pdf=r.source_pdf_path or "",
                     fields=dict(r.fields or {}), options=dict(r.options or {}),
                     params=dict((r.params or {}).get("DURAGREEN SPEC") or {}),
                     diagrams=list(r.diagrams or [])) for r in rows]
    finally:
        db.rollback()
        db.close()


# --------------------------------------------------------------- reading the website crawl

CRAWL = OUT_DIR / "site_crawl" / "crawl.json"
CRAWL_PDFS = OUT_DIR / "site_crawl" / "pdfs"
NEW_ID_BASE = 900000


def _card_row(page, card, app_by_key, n, pdf_files=None):
    """One website variant card in the shape load_app_rows() gives, so build() reads both alike.
    Fields and options are derived from the card exactly as import_duragreen_catalog.py derives
    them, so a matched product is compared like for like with what the app already holds.

    `pdf_files` is the crawl's {sheet URL: file in pdfs/}: DuraGreen has uploaded different
    products' sheets under one name (68-SPEC-2.pdf is three), and the crawl keeps each apart
    (crawl_duragreen_site.local_names). An older snapshot without it falls back to the name."""
    spec = {label: value for label, value in card["spec"]}
    fields = {}
    for label, value in spec.items():
        canon = WEB_LABELS.get(label.upper())
        if canon and canon not in fields:
            fields[canon] = value
    watts = re.findall(r"\d+(?:\.\d+)?W", card.get("tagline") or "", re.I)
    if watts:
        fields["wattage"] = watts[0]
    options = {}
    if watts:
        options["wattage"] = watts
    if "cct" in fields:
        options["cct"] = cct_options_from(fields["cct"])
    if "beam_angle" in fields:
        options["beam_angle"] = split_list(fields["beam_angle"])
    if "control" in fields:
        options["control"] = [p.strip() for p in fields["control"].split(",") if p.strip()]
    size = re.match(r"^(\d+)\s*(.*)$", card["name"])
    sheet = next((d["url"] for d in card["downloads"]
                  if d["url"].lower().endswith(".pdf") and "specification" in d["label"].lower()), "")
    local = CRAWL_PDFS / (pdf_files or {}).get(sheet, Path(sheet).name) if sheet else None
    category = (page.get("breadcrumb") or [""])[0]
    app = app_by_key.get((page["title"].strip().upper(), card["name"].strip().upper()))
    return dict(
        id=app["id"] if app else NEW_ID_BASE + n,
        app_id=app["id"] if app else None,
        name=card["name"],
        category=app["category"] if app else SITE_TYPOLOGY.get(category, ""),
        series=page["title"].strip(), family=f"{size.group(1)}mm" if size else "",
        variant=size.group(2).strip() if size else card["name"],
        pdf=str(local) if local and local.exists() else "",
        # the sheet as DuraGreen names it - what its codes' source says, whichever file it was read from
        pdf_label=unquote(Path(urlparse(sheet).path).name) if sheet else "",
        fields=fields, options=options, params=spec,
        diagrams=app["diagrams"] if app else [],
        web=dict(url=page["url"], page_title=page["title"], card_name=card["name"],
                 variation_id=card.get("variation_id"), category=category,
                 description=page.get("description") or "", tagline=card.get("tagline") or "",
                 image_url=card.get("image_url") or "", spec_sheet=sheet,
                 downloads=card["downloads"], notes=card.get("notes") or [],
                 finishes=card.get("finishes") or [], spec=card["spec"]),
    )


def _sheet_stem(path):
    """A spec sheet's file name without the copy number a download or upload added:
    "AFL55__2.pdf" and "AFL55.pdf" are the same document."""
    if not path:
        return ""
    return re.sub(r"__\d+$", "", Path(str(path).split("?")[0]).stem).upper()


def _range_word(series):
    return (str(series or "").split() or [""])[0].upper()


def load_crawl_rows():
    """Every variant card on every product page of the crawl, matched to the app's row.

    First by the identity the importer gave it: page title as series, card name as product name.
    Then, because DuraGreen renames families (MOOD ROUND and MOOD SQUARE are now one MOOD page,
    RADIANCE Wall Sconce is RADIANCE Uplift Wall Sconce), a card left over is matched to an app
    row left over only on two pieces of evidence - the same product name AND either the same spec
    sheet document, or the same range word with exactly one candidate on each side. A match only
    ever updates the existing product; anything weaker stays a new, pending product and the old
    row is left as it is, both listed for a person to judge. Recorded in `matched_by`."""
    snap = json.loads(CRAWL.read_text(encoding="utf-8"))
    app_rows = load_app_rows()
    app_by_key = {(r["series"].strip().upper(), r["name"].strip().upper()): r for r in app_rows}
    rows, seen, n = [], set(), 0
    for page in snap["products"].values():
        for card in page["cards"]:
            n += 1
            row = _card_row(page, card, app_by_key, n, snap.get("pdf_files"))
            # A card the site shows on two pages (a family page and a "sale" page) is one product.
            key = (row["series"].upper(), row["name"].upper())
            if key in seen:
                continue
            seen.add(key)
            row["matched_by"] = "series and name" if row["app_id"] else ""
            rows.append(row)

    taken = {r["app_id"] for r in rows if r["app_id"]}
    spare_app = [a for a in app_rows if a["id"] not in taken and a["category"] != "Driver"]
    spare_web = [r for r in rows if not r["app_id"]]
    by_name = lambda items, key: {k: [i for i in items if key(i) == k] for k in {key(i) for i in items}}
    app_named = by_name(spare_app, lambda a: a["name"].strip().upper())
    web_named = by_name(spare_web, lambda r: r["name"].strip().upper())
    for name, webs in web_named.items():
        apps = app_named.get(name) or []
        for r in webs:
            sheet = _sheet_stem(r["web"]["spec_sheet"])
            same_sheet = [a for a in apps if sheet and _sheet_stem(a["pdf"]) == sheet]
            same_range = [a for a in apps if _range_word(a["series"]) == _range_word(r["series"])]
            webs_in_range = [w for w in webs if _range_word(w["series"]) == _range_word(r["series"])]
            if len(same_sheet) == 1:
                match, basis = same_sheet[0], "name and spec sheet (family renamed on the website)"
            elif len(same_range) == 1 and len(webs_in_range) == 1:
                match, basis = same_range[0], "name, only one in its range (family renamed on the website)"
            else:
                continue
            if match["id"] in taken:
                continue
            taken.add(match["id"])
            r.update(id=match["id"], app_id=match["id"], category=match["category"],
                     diagrams=match["diagrams"], matched_by=basis)
    return rows


def build(db_path, from_crawl=False):
    rows = load_crawl_rows() if from_crawl else load_app_rows()
    print(f"{'website' if from_crawl else 'app'} rows: {len(rows)}")

    OUT_DIR.mkdir(exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    cur = con.cursor()

    cur.executemany("INSERT INTO value_synonyms VALUES (?,?,?,?)",
                    [("control", a, b, why) for a, b, why in CONTROL_SYNONYMS])

    # 1) documents
    pages_by_id, doc_id_by_pdf, pages_by_pdf = {}, {}, {}
    for r in rows:
        pdf = r["pdf"]
        if not pdf or pdf in doc_id_by_pdf:
            pages_by_id[r["id"]] = pages_by_pdf.get(pdf, [])
            continue
        note, pages, ok = None, [], 1
        if not Path(pdf).exists():
            ok, note = 0, "file missing"
        else:
            try:
                pages = read_pdf(pdf)
            except Exception as e:  # noqa: BLE001 - an unreadable sheet is recorded, not fatal
                ok, note = 0, f"unreadable: {type(e).__name__}"
        cur.execute("INSERT INTO source_documents (file_name, pages, chars, read_ok, note) VALUES (?,?,?,?,?)",
                    (r.get("pdf_label") or Path(pdf).name, len(pages), sum(len(p["text"]) for p in pages), ok, note))
        doc_id_by_pdf[pdf] = cur.lastrowid
        pages_by_pdf[pdf] = pages
        pages_by_id[r["id"]] = pages

    # 2) the code matrices - parsed once per distinct matrix, recorded with how many sheets print it
    matrices, matrix_of_product = {}, {}
    seen_text = {}
    for r in rows:
        for n, p in enumerate(pages_by_id.get(r["id"]) or [], start=1):
            t = p["text"]
            if "Specification Code" not in t:
                continue
            name = (r["series"].split() or ["?"])[0].upper()
            key = t[t.find("Specification Code"):]
            if key not in seen_text:
                parsed = parse_matrix(r["pdf"], n)
                if parsed:
                    seen_text[key] = name
                    matrices.setdefault(name, {"segments": parsed, "count": 0})
            if key in seen_text:
                matrices[seen_text[key]]["count"] += 1
                matrix_of_product[r["id"]] = seen_text[key]
    for name, m in matrices.items():
        for order, seg, ex, entries in m["segments"]:
            for code, meaning in entries:
                cur.execute("INSERT INTO code_matrices VALUES (?,?,?,?,?,?,?)",
                            (name, order, seg, code, meaning, ex, m["count"]))

    # 3) families
    fam_id = {}
    for r in rows:
        if r["category"] == "Driver" or r["series"] in fam_id:
            continue
        # A family's first word is its range (ACME, MOOD, ORBIT ...), except for the few named after
        # a category instead ("48V Track Lights", "HIGH EFFICIENCY RECESSED") - those are their own.
        first = (r["series"].split() or [""])[0]
        generic = re.search(r"\d", first) or r["series"] in CATEGORY_NAMED_FAMILIES
        rng = r["series"] if generic else first.upper()
        cur.execute("INSERT INTO families (name, range_name, category, code_matrix) VALUES (?,?,?,?)",
                    (r["series"], rng, r["category"], rng if rng in matrices else None))
        fam_id[r["series"]] = cur.lastrowid

    def add_option(pid, attribute, value, code, source, status, note=None):
        value = (value or "").strip()
        if not value:
            return
        cur.execute("""INSERT INTO product_options VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(product_id, attribute, value) DO UPDATE SET
                       code = coalesce(product_options.code, excluded.code),
                       note = coalesce(product_options.note, excluded.note)""",
                    (pid, attribute, value, code, source, status, note))

    def gap(pid, field, app_value, cat_value, issue, kind="app_gap"):
        cur.execute("INSERT INTO issues VALUES (?,?,?,?,?,?)",
                    (pid, kind, field, None if app_value is None else str(app_value),
                     None if cat_value is None else str(cat_value), issue))

    matrix_lookup = defaultdict(dict)       # matrix -> segment -> {meaning_norm: code}
    for name, m in matrices.items():
        for _, seg, _, entries in m["segments"]:
            for code, meaning in entries:
                matrix_lookup[name].setdefault(seg, {})[meaning.lower()] = code

    # 4) products and drivers
    for r in rows:
        pid = r["id"]
        pages = pages_by_id.get(pid) or []
        text = "\n".join(p["text"] for p in pages)
        lines = sheet_lines(pages)
        doc_id = doc_id_by_pdf.get(r["pdf"])
        web = r.get("web")
        if web:
            cur.execute("INSERT INTO web_pages VALUES (" + ",".join("?" * 16) + ")",
                        (pid, r.get("app_id"), web["url"], web["page_title"], web["card_name"],
                         web["variation_id"], web["category"], web["description"], web["tagline"],
                         web["image_url"], web["spec_sheet"], json.dumps(web["downloads"]),
                         json.dumps(web["notes"]), json.dumps(web["finishes"]), json.dumps(web["spec"]),
                         r.get("matched_by") or None))
            if r.get("app_id") is None:
                gap(pid, "product", None, f"{web['page_title']} / {web['card_name']}",
                    "on the website, not in the app", kind="new_on_website")

        if r["category"] == "Driver":
            d = parse_driver_name(r["name"])
            f = r["fields"]
            cur.execute("INSERT INTO drivers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, r["name"], d["brand"], d["output_type"], d["control"] or f.get("control"),
                         d["tunable"], d["output_voltage"], d["wattage"] or f.get("wattage"),
                         d["ip_rating"] or f.get("ip_rating"), f.get("input_voltage"), doc_id))
            continue

        specs = parse_specs(lines)
        pdf_spec = {}
        for key, canon, value in specs:
            cur.execute("INSERT INTO specs VALUES (?,?,?,?,?)", (pid, "pdf", key, canon, value))
            if canon and canon not in pdf_spec:
                pdf_spec[canon] = value
        web_spec = {}
        for key, value in r["params"].items():
            canon = CANONICAL.get(key.rstrip("*").strip().lower())
            cur.execute("INSERT INTO specs VALUES (?,?,?,?,?)", (pid, "website", key, canon, value))
            if canon and canon not in web_spec:
                web_spec[canon] = value

        def spec(canon):
            # the PDF wins; the website fills what the PDF does not print
            return pdf_spec.get(canon) or web_spec.get(canon)

        def spec_source(canon):
            # which of the two a spec() value came from - an option must say so truthfully
            return "sheet_spec" if pdf_spec.get(canon) else "website"

        codes = parse_code_tables(lines)
        example, legend = parse_legend(lines)
        cutouts = sorted({(lambda c: c if c.endswith("mm") else c + "mm")(re.sub(r"\s", "", c)) for c in
                          re.findall(r"cut\s*out\s*[:：]\s*([\d.]+\s*(?:[*xX×]\s*[\d.]+)?\s*mm|[\d.]+)", text, re.I)})
        title = " / ".join(lines[:3]) if lines else None
        size = r["family"] or (re.search(r"[ФΦØ]\s?(\d+)", text).group(1) + "mm"
                               if re.search(r"[ФΦØ]\s?(\d+)", text) else None)
        prefixes = Counter(re.match(r"^[A-Z]+", c["code"]).group(0) for c in codes if re.match(r"^[A-Z]+", c["code"]))
        project = re.search(r"DuraGreen project:\s*\n?\s*([^\n]+)", text)

        cur.execute("INSERT INTO products VALUES (" + ",".join("?" * 28) + ")",
                    (pid, fam_id.get(r["series"]), r["name"], r["category"], title, size,
                     first_word(r["name"] + " " + r["variant"], OPTIC_WORDS),
                     first_word(r["name"] + " " + r["variant"] + " " + r["category"], MOUNT_WORDS),
                     " / ".join(cutouts) or None, spec("ceiling_thickness"),
                     spec("ip_rating"), spec("ugr"), spec("cri"),
                     pdf_spec.get("cri") if "/" in (pdf_spec.get("cri") or "") else None,
                     spec("lifetime"), spec("efficacy"), spec("binning"), spec("input_voltage"),
                     spec("output"), spec("ambient_temp"), spec("class"), spec("material"),
                     spec("certifications"),
                     prefixes.most_common(1)[0][0] if prefixes else None, example, len(codes),
                     project.group(1).strip() if project else None, doc_id))

        # order codes
        for c in codes:
            parts = decompose(c["code"], c["wattage"], c["optics"]) or {}
            cur.execute("""INSERT INTO order_codes (product_id, code, wattage, lumens, lumens_92ra, lumens_98ra,
                           efficacy, optics, cct, cri, length, spots, input_voltage, extra, parsed_by,
                           type_code, size_code, wattage_code, variant_code, cct_prefix, cct_code, control_code,
                           beam_prefix, beam_code, suffix_code, decomposed, table_header, source_line, version_label)
                           VALUES (""" + ",".join("?" * 29) + ")",
                        (pid, c["code"], c["wattage"], c["lumens"], c["lumens_92ra"], c["lumens_98ra"],
                         c["efficacy"], c["optics"], c["cct"], c["cri"], c["length"], c["spots"],
                         c["input_voltage"], c["extra"], c["parsed_by"],
                         parts.get("type_code"), parts.get("size_code"), parts.get("wattage_code"),
                         parts.get("variant_code"), parts.get("cct_prefix") or None, parts.get("cct_code"),
                         parts.get("control_code"), parts.get("beam_prefix") or None, parts.get("beam_code"),
                         parts.get("suffix_code"), 1 if parts else 0, c["header"], c["line"], c.get("version") or None))
            add_option(pid, "wattage", c["wattage"], parts.get("wattage_code"), "order_codes", "stated")
            if c["optics"]:
                # the beam part as printed, with any undecoded letter in front of it ("L15")
                add_option(pid, "beam_angle", c["optics"],
                           ((parts.get("beam_prefix") or "") + parts["beam_code"]) if parts.get("beam_code") else None,
                           "order_codes", "stated",
                           f"printed with an '{parts['beam_prefix']}' in front, which Duragreen does not decode"
                           if parts.get("beam_prefix") else None)
            if c["cct"]:
                # the CCT part as printed, with any undecoded letter in front of it ("H30")
                add_option(pid, "cct", c["cct"],
                           ((parts.get("cct_prefix") or "") + parts["cct_code"]) if parts.get("cct_code") else None,
                           "order_codes", "stated",
                           "printed with an 'H' in front, which Duragreen does not decode"
                           if parts.get("cct_prefix") else None)

        # the legend
        legend_codes = {}       # (attribute, sheet value) -> (code, note), applied once the sheet's values exist
        for pos, tok, meaning, attr in legend:
            cur.execute("INSERT INTO code_legend VALUES (?,?,?,?,?)", (pid, pos, tok, meaning, attr))
            if attr in ("cct", "control", "beam_angle", "bezel_finish", "body_finish", "baffle_finish",
                        "accessory", "type", "size"):
                value = meaning
                if attr == "cct":
                    value = (re.search(r"\d{4}K(?:-\d{4}K)?", meaning, re.I) or [meaning])[0].upper()
                elif attr == "beam_angle":
                    value = (re.search(r"\d+°", meaning) or [meaning])[0]
                elif attr == "accessory":
                    value = re.sub(r"[（(]optional[)）]", "", meaning, flags=re.I).strip()
                elif attr == "size":
                    mm = re.search(r"\d+(?:[*xX/]\d+)?\s?mm", meaning)
                    value = mm.group(0).replace(" ", "") if mm else meaning
                elif attr in ("bezel_finish", "body_finish", "baffle_finish"):
                    # "white bezel" -> "White": the column already says it is the bezel
                    value = re.sub(r"\b(bezel|structure|baffle)\b", "", meaning, flags=re.I).strip().title()
                elif attr == "control":
                    # the legend's "non dim" is the sheet's own "On/off": give that option the code
                    target = next((b for a, b, _ in CONTROL_SYNONYMS if a.lower() == meaning.lower()), None)
                    same = [v for v in split_controls(spec("control")) if target and
                            next((b for a, b, _ in CONTROL_SYNONYMS if a.lower() == v.lower()), None) == target]
                    if same:
                        legend_codes[("control", same[0])] = (tok, f"legend: {tok} / {meaning}")
                        continue
                add_option(pid, attr, value, tok, "code_legend", "stated")

        # the sheet's own spec lists
        for v in cct_options_from(spec("cct")):
            add_option(pid, "cct", v, None, spec_source("cct"), "stated")
        for v in split_controls(spec("control")):
            code, note = legend_codes.get(("control", v), (None, None))
            add_option(pid, "control", v, code, spec_source("control"), "stated", note)
        for v in split_list(spec("beam_angle")):
            if re.search(r"\d", v):
                add_option(pid, "beam_angle", v, None, spec_source("beam_angle"), "stated")
        for v in split_list(spec("accessories"), seps=(",",)):
            add_option(pid, "accessory", v, None, spec_source("accessories"), "stated")
        if spec("ip_rating"):
            add_option(pid, "ip_rating", spec("ip_rating"), None, spec_source("ip_rating"), "stated")
        # CRI: the code tables' "(92Ra)"/"(98Ra)" headings, and the first number of the CRI line -
        # only the first, since "typ. 98/95/104/97" goes on to RF, RG and R9.
        cri_vals = set(re.findall(r"\((\d{2})Ra\)", " ".join(c["header"] for c in codes)))
        first = re.search(r"\b(\d{2})\b", spec("cri") or "")
        if first:
            cri_vals.add(first.group(1))
        for v in sorted(cri_vals):
            add_option(pid, "cri", "CRI " + v, None, "sheet_spec", "stated")

        # what the website gave that the sheet does not print
        for field in ("wattage", "cct", "beam_angle"):
            for v in r["options"].get(field) or []:
                if field == "wattage":
                    # the website gives a twin head's per-head wattage ("6W") where the sheet
                    # prints "6W x 2" - the same fitting, so not a value of its own
                    cur.execute("SELECT value FROM product_options WHERE product_id=? AND attribute='wattage'",
                                (pid,))
                    if base_watt(v) in {base_watt(x) for (x,) in cur.fetchall()}:
                        continue
                cur.execute("SELECT 1 FROM product_options WHERE product_id=? AND attribute=? AND "
                            "replace(replace(value,' ',''),'K','') = replace(replace(?,' ',''),'K','')",
                            (pid, field, v))
                if not cur.fetchone():
                    add_option(pid, field, v, None, "website", "stated",
                               "on the website, not in this spec sheet's own tables")

        # the range matrix: codes for the values the sheet lists
        # A range matrix applies to a product whose own sheet prints it, or - as "range" - to one
        # whose order codes start with a type that matrix lists. Being in the range by name is not
        # enough: ACME 110's pendant codes start LCP, which the ACME matrix does not cover.
        rng = (r["series"].split() or [""])[0].upper()
        type_codes = {code for _, seg, _, entries in (matrices.get(rng) or {}).get("segments", [])
                      if seg == "Type" for code, _ in entries}
        mname = matrix_of_product.get(pid) or (rng if set(prefixes) & type_codes else None)
        if mname:
            basis = "stated" if matrix_of_product.get(pid) == mname else "range"
            seg = matrix_lookup[mname]
            cct_codes = {norm_cct(k): v for k, v in seg.get("Colour Temp", {}).items()}
            cur.execute("SELECT value FROM product_options WHERE product_id=? AND attribute='cct'", (pid,))
            for (v,) in cur.fetchall():
                code = cct_codes.get(norm_cct(v))
                if code:
                    cur.execute("UPDATE product_options SET code=coalesce(code, ?), note=coalesce(note, ?) "
                                "WHERE product_id=? AND attribute='cct' AND value=?",
                                (code, f"code from the {mname} matrix ({basis})", pid, v))
            drv = seg.get("Driver", {})
            cur.execute("SELECT value FROM product_options WHERE product_id=? AND attribute='control'", (pid,))
            for (v,) in cur.fetchall():
                target = next((b for a, b, _ in CONTROL_SYNONYMS if a.lower() == v.lower()), None)
                code = drv.get(target.lower()) if target else None
                if code:
                    status_note = "same words" if target.lower() == v.lower() else f"matched: sheet '{v}' = matrix '{target}'"
                    cur.execute("UPDATE product_options SET code=coalesce(code, ?), note=coalesce(note, ?) "
                                "WHERE product_id=? AND attribute='control' AND value=?",
                                (code, f"{status_note} ({mname} matrix, {basis})", pid, v))
                    if target.lower() != v.lower():
                        cur.execute("UPDATE product_options SET status='matched' WHERE product_id=? "
                                    "AND attribute='control' AND value=?", (pid, v))
            for segname, attr in (("Bezel Colour", "bezel_finish"), ("Baffle Colour", "baffle_finish"),
                                  ("Options", "accessory"), ("Type", "type")):
                for meaning, code in seg.get(segname, {}).items():
                    if attr == "type" and code not in prefixes:
                        continue    # a range's type list names many products; keep only this one's
                    add_option(pid, attr, meaning.title() if attr != "type" else meaning, code, "code_matrix", basis,
                               f"{mname} matrix")
            # wattage codes the matrix prints in its example ("08W") follow the table's own codes;
            # beams not in this product's code table are not added - the matrix lists the range's.
            # What the sheet offers but its matrix has no code for cannot be ordered by code - a
            # contradiction inside Duragreen's own documents, worth asking them about.
            for attr in ("cct", "control"):
                cur.execute("SELECT group_concat(value, ' / ') FROM product_options WHERE product_id=? "
                            "AND attribute=? AND code IS NULL AND source IN ('sheet_spec', 'website')", (pid, attr))
                missing = cur.fetchone()[0]
                if missing:
                    gap(pid, attr, None, missing,
                        f"offered on the sheet, but the {mname} code matrix has no code for it",
                        kind="source_conflict")

        # finishes
        for palette, name, ral, moq, fcode in parse_finishes(lines, text):
            cur.execute("INSERT INTO finishes VALUES (?,?,?,?,?,?)", (pid, palette, name, ral, fcode, moq))
            add_option(pid, "finish", name, fcode, "sheet_finishes", "stated",
                       f"{palette}" + (f", RAL {ral}" if ral else "") + (f", MOQ {moq}" if moq else ""))
        if mname:
            for segname, palette in (("Bezel Colour", f"{mname} bezel"), ("Baffle Colour", f"{mname} baffle")):
                for meaning, code in seg.get(segname, {}).items():
                    cur.execute("INSERT INTO finishes VALUES (?,?,?,?,?,?)",
                                (pid, palette, meaning.title(), None, code, None))

        # components ordered separately
        for sku in re.findall(r"SKU\s*[:：]\s*([A-Z0-9][A-Z0-9\-]+)", text):
            kind = {"EN": "Light engine", "BZ": "Bezel", "BF": "Baffle"}.get(sku.split("-")[0], None)
            if kind == "Bezel" and sku.endswith("-TL"):
                kind = "Bezel (trimless)"
            cur.execute("INSERT INTO components VALUES (?,?,?,?)", (pid, sku, kind,
                        "accessories ordered separately" if "ordered separately" in text else None))
            add_option(pid, "component", f"{kind or 'Part'} {sku}", sku, "sheet_components", "stated")
        for sku, name, finishes in parse_track_parts(lines):
            note = name if not finishes else f"{name or ''} - finish letter {finishes} (Black / White)".strip(" -")
            cur.execute("INSERT INTO components VALUES (?,?,?,?)", (pid, sku, "Track part", note))
            add_option(pid, "component", f"{name or 'Track part'} {sku}", sku, "sheet_components", "stated",
                       f"finish letter {finishes}" if finishes else None)

        # ---- what the app holds against what the catalogue says
        f = r["fields"]
        if not pages:
            gap(pid, "spec sheet", r["pdf"] and Path(r["pdf"]).name, None,
                ("the spec sheet could not be read" if r["pdf"] else "no spec sheet is linked")
                + " - nothing beyond the website card is known", kind="unreadable")
        if f.get("accessories") and re.search(r"aluminium|brass|stone|steel|glass|alloy|die-?cast", f["accessories"], re.I):
            gap(pid, "accessories", f["accessories"], spec("material"),
                "a material is stored in the app's accessories field")
        # a sheet that contradicts itself: the product is named Fixed but its code example decodes
        # the type as adjustable, or the other way round
        legend_type = next((mean for pos, tok, mean, attr in legend if attr == "type"), "")
        named = first_word(r["name"] + " " + r["variant"], OPTIC_WORDS)
        for word in ("adjustable", "fixed"):
            if named and named.lower() in ("fixed", "adjustable") and named.lower() != word \
                    and re.search(r"\b" + word + r"\b", legend_type, re.I):
                gap(pid, "code type", named, legend_type,
                    f"the product is named {named}, but its own code example decodes the type as '{legend_type}'",
                    kind="source_conflict")
        cur.execute("SELECT group_concat(value, ' / '), count(*) FROM product_options WHERE product_id=? "
                    "AND attribute='wattage'", (pid,))
        wv, wn = cur.fetchone()
        if wn and wn > 1 and "/" not in (f.get("wattage") or ""):
            gap(pid, "wattage", f.get("wattage"), wv, "the app's field shows one wattage; the catalogue offers several")
        app_w = sorted({base_watt(v) for v in r["options"].get("wattage") or []}, key=natural_key)
        table_w = sorted({base_watt(c["wattage"]) for c in codes}, key=natural_key)
        if codes and app_w and app_w != table_w:
            gap(pid, "wattage options", " / ".join(app_w), " / ".join(table_w),
                "the website and the spec sheet's code table list different wattages", kind="source_conflict")
        for attr, label in (("finish", "finish"), ("size", "cut-out / size"), ("component", "components"),
                            ("bezel_finish", "bezel finish"), ("baffle_finish", "baffle finish")):
            cur.execute("SELECT count(*) FROM product_options WHERE product_id=? AND attribute=?", (pid, attr))
            n = cur.fetchone()[0]
            if n and not f.get(attr if attr != "size" else "cutout_dimensions"):
                gap(pid, attr, None, f"{n} option(s)", f"the catalogue has {label}; the app has no such field")
        if codes:
            gap(pid, "order codes", None, f"{len(codes)} code(s)", "the catalogue lists order codes; the app stores none")
        if any(not d.get("width") for d in r["diagrams"]):
            gap(pid, "image size", "0 x 0", None, "the picture's size was never recorded")

    cur.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built_at", datetime.datetime.now().isoformat(timespec="seconds")),
        ("built_by", "scripts/catalogues/build_duragreen_catalogue_db.py"),
        ("source", "the website crawl (site_crawl/crawl.json) and the spec sheets it downloaded; in "
                   "issues, 'the app' means the website's own variant card" if from_crawl else
                   "the app's Duragreen specsheet rows (website scrape) and their spec-sheet PDFs"),
        ("status_stated", "printed by Duragreen for this product"),
        ("status_range", "printed by Duragreen for the product's range (ACME/MOOD matrix)"),
        ("status_matched", "sheet wording matched to a matrix entry by meaning - see value_synonyms"),
        ("rule", "PDF wins over the website; a code printed nowhere is left NULL"),
    ])
    con.commit()
    return con


def summary(con):
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    print("\n--- tables")
    for (t,) in q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(f"  {t:18} {q(f'SELECT count(*) FROM {t}')[0][0]:>7,}")
    print("\n--- source documents: read", q("SELECT sum(read_ok), count(*) FROM source_documents")[0])
    print("--- order codes decomposed:", q("SELECT sum(decomposed), count(*) FROM order_codes")[0])
    print("--- code rows read by:", q("SELECT parsed_by, count(*) FROM order_codes GROUP BY 1"))
    print("--- products with order codes:", q("SELECT count(*) FROM products WHERE order_code_count>0")[0][0],
          "of", q("SELECT count(*) FROM products")[0][0])
    print("--- options by attribute / status:")
    for a, s, n, coded in q("SELECT attribute, status, count(*), sum(code IS NOT NULL) FROM product_options "
                            "GROUP BY 1,2 ORDER BY 1,2"):
        print(f"  {a:14} {s:8} {n:>6}  coded {coded}")
    print("--- issues:")
    for kind, issue, n in q("SELECT kind, issue, count(*) FROM issues GROUP BY 1, 2 ORDER BY 1, 3 DESC"):
        print(f"  {kind:15} {n:>5}  {issue[:110]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=None, help="where to write the SQLite file")
    ap.add_argument("--from-crawl", action="store_true",
                    help="read the website crawl (crawl_duragreen_site.py) and its fresh spec sheets "
                         "instead of the app's rows; writes duragreen_catalogue_site.sqlite by default")
    args = ap.parse_args()
    out = args.out or (OUT_DIR / "duragreen_catalogue_site.sqlite" if args.from_crawl else DB_PATH)
    con = build(out, from_crawl=args.from_crawl)
    args.out = out
    summary(con)
    con.close()
    print(f"\nwritten: {args.out}")
    # The Excel copy of the same data is rebuilt with it, so the two can never disagree.
    from export_duragreen_catalogue_xlsx import export_workbook
    xlsx = OUT_DIR / "Duragreen Catalogue (website).xlsx" if args.from_crawl else None
    print(f"written: {export_workbook(args.out, xlsx)}")


if __name__ == "__main__":
    main()
