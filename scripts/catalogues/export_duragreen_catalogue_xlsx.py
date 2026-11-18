"""Exports the Duragreen catalogue database to one Excel workbook - every table and view, every
row: "Duragreen Catalogue/Duragreen Catalogue.xlsx".

The SQLite file (built by build_duragreen_catalogue_db.py) stays the source of truth; this is the
same data for people who work in Excel. The builder runs this at the end of every build, so the
two cannot drift apart. It can also be run on its own, in seconds:

    python scripts/catalogues/export_duragreen_catalogue_xlsx.py

VALUES AND FORMULAS
Every cell that comes from the database is a plain value. Where a sheet's rows are keyed by a
product's ID, the product's family and name are written beside it so the sheet reads on its own;
they are looked up from the database at export time. The Product Matrix is the Options sheet
pivoted to one row per product, also written as values.

The counts are live formulas over the data sheets (COUNTA, COUNTIF, COUNTIFS, SUMIF, SUM): the Read
Me's rows per sheet, everything on Summary, a family's products and order codes, a product's order
codes. A copy someone edits therefore keeps its totals right. Each formula also carries the value
this export computed from the database - the FormulaCache the quotation exports use - so a
previewer that does not calculate still shows the number.

TEXT STAYS TEXT
Code parts are codes, not numbers: "05" is the 5° beam, "0" the 0-10V driver, "06W" a wattage.
They are written as text in text-formatted columns, so Excel cannot strip a leading zero, and the
tables are marked so Excel does not flag deliberate text as numbers stored as text. Lumens,
efficacy and minimum-order figures that are plain numbers are written as numbers so they sort;
"485 x 2" and "292-296" stay as printed.
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
import re
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter, quote_sheetname
from openpyxl.worksheet.hyperlink import Hyperlink

from app.quotation_export import FormulaCache, _embed_cached_values

OUT_DIR = _REPO_ROOT / "Duragreen Catalogue"
DB_PATH = OUT_DIR / "duragreen_catalogue.sqlite"
XLSX_NAME = "Duragreen Catalogue.xlsx"

FONT = "Arial"
BODY = Font(name=FONT, size=10)
BOLD = Font(name=FONT, size=10, bold=True)
HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
TITLE = Font(name=FONT, size=16, bold=True)
SECTION = Font(name=FONT, size=12, bold=True)
LINK = Font(name=FONT, size=10, color="0563C1", underline="single")
SUBTLE = Font(name=FONT, size=10, italic=True, color="595959")
HEAD_FILL = PatternFill("solid", fgColor="2F2A22")       # the app's other internal exports use it
TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
TOP = Alignment(vertical="top")
TOP_WRAP = Alignment(vertical="top", wrap_text=True)
HEAD_ALIGN = Alignment(vertical="center", wrap_text=True)

ATTR_ORDER = ["type", "size", "wattage", "cct", "beam_angle", "control", "cri", "ip_rating", "finish",
              "bezel_finish", "baffle_finish", "body_finish", "accessory", "component"]
ATTR_LABEL = {"type": "Type", "size": "Size", "wattage": "Wattage", "cct": "CCT",
              "beam_angle": "Beam angle", "control": "Control", "cri": "CRI", "ip_rating": "IP rating",
              "finish": "Finish", "bezel_finish": "Bezel finish", "baffle_finish": "Baffle finish",
              "body_finish": "Body finish", "accessory": "Accessories", "component": "Components"}
STATUSES = ["stated", "range", "matched"]
SPEC_MEANING = "As printed on the spec sheet; where the sheet prints nothing, as the website gives it."


class Col:
    """One column of a data sheet: its heading, where it comes from, what it means, and how its
    values are written. kind: text, long (wrapped), code (text-formatted, so "05" keeps its zero),
    longcode (both), int, num (a number when the value is one), yesno (1/0 -> Yes/No), formula
    (`formula(row_number, row)` returns the formula and the value it should come to)."""

    def __init__(self, header, field, meaning, kind="text", formula=None):
        self.header, self.field, self.meaning, self.kind, self.formula = header, field, meaning, kind, formula


def natural_key(text):
    """Sort "6W", "8W", "10W" (and "5°", "15°", "120°") by their numbers, not as text."""
    return [(0, float(t), "") if re.fullmatch(r"\d+(?:\.\d+)?", t) else (1, 0.0, t.lower())
            for t in re.split(r"(\d+(?:\.\d+)?)", "" if text is None else str(text)) if t]


def convert(value, kind):
    if value is None:
        return None
    if kind == "yesno":
        return "Yes" if value else "No"
    if kind == "int":
        return int(value)
    if kind == "num" and isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
        number = float(value)
        return int(number) if number == int(number) else number
    if isinstance(value, str) and value.startswith("="):
        # openpyxl would write this as a formula; nothing in the database starts so today
        raise ValueError(f"value would be read as a formula: {value!r}")
    return value if kind in ("num", "int") else str(value)


WRAPPED = ("long", "longcode")
TEXT_FORMATTED = ("code", "longcode")


def _width(col, rows):
    lengths = sorted(len(str(r.get(col.field) if col.kind != "formula" else "000")) for r in rows[:4000]
                     if col.kind == "formula" or r.get(col.field) is not None)
    typical = lengths[int(len(lengths) * 0.95)] if lengths else 0
    cap = 70 if col.kind in WRAPPED else 42
    return max(9, min(cap, max(len(col.header) + 3, typical + 2)))


def criteria_safe(text, where):
    """A value used as a COUNTIF criterion must mean only itself: no wildcard (* ? ~), no leading
    comparison sign, and short enough for COUNTIF (255). Checked rather than escaped - nothing in
    the database breaks these today, and a value that ever does should stop the export."""
    if text is None:
        return
    # `text[:1] and`: the empty string is "in" every string, so a blank value read as one that
    # starts with a comparison sign and stopped the export (found 19 Oct, products with no typology).
    if re.search(r"[*?~]", text) or (text[:1] and text[:1] in "=<>") or len(text) > 255:
        raise ValueError(f"{where}: {text!r} cannot be used as a COUNTIF criterion as it stands")


# ------------------------------------------------------------------ reading the database

def load(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    def q(sql, *args):
        return [dict(r) for r in con.execute(sql, args)]

    data = {name: q(f"SELECT * FROM {name}") for name in
            ("families", "products", "product_options", "order_codes", "code_legend", "code_matrices",
             "components", "finishes", "specs", "drivers", "issues", "value_synonyms",
             "source_documents", "meta")}
    data["v_configurator"] = q("SELECT * FROM v_configurator")
    data["v_family_overview"] = q("SELECT * FROM v_family_overview")
    # rowid keeps each table's own order (the order things appear on the sheet) for sorting below
    for name in ("code_legend", "code_matrices", "components", "finishes", "specs", "issues",
                 "product_options", "value_synonyms", "meta"):
        data[name] = q(f"SELECT rowid AS _rowid, * FROM {name}")
    con.close()
    return data


def add_names(rows, products, families):
    """Write the family, range and product name beside each product ID, for reading."""
    for r in rows:
        p = products.get(r["product_id"])
        f = families.get(p["family_id"]) if p else None
        r["family"] = f["name"] if f else None
        r["range"] = f["range_name"] if f else None
        r["product"] = p["name"] if p else None
    return rows


def by_product(rows, *then):
    return sorted(rows, key=lambda r: (natural_key(r["family"]), natural_key(r["product"]),
                                       r["product_id"], *[r[k] if k != "_natural_value" else
                                                          natural_key(r["value"]) for k in then]))


# ------------------------------------------------------------------ writing

def write_table(wb, title, cols, rows, cache, freeze, text_ranges):
    ws = wb.create_sheet(title)
    for c, col in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=c, value=col.header)
        cell.font, cell.fill, cell.alignment = HEAD, HEAD_FILL, HEAD_ALIGN
    for r, row in enumerate(rows, start=2):
        for c, col in enumerate(cols, start=1):
            if col.kind == "formula":
                formula, value = col.formula(r, row)
                cell = cache.put(ws, r, c, formula, value, "0")
            else:
                cell = ws.cell(row=r, column=c, value=convert(row.get(col.field), col.kind))
                if col.kind in TEXT_FORMATTED:
                    cell.number_format = "@"
            cell.font = BODY
            cell.alignment = TOP_WRAP if col.kind in WRAPPED else TOP
    last = max(2, len(rows) + 1)
    for c, col in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(c)].width = _width(col, rows)
    # Every text cell here is text on purpose (a code, or a spec value as printed, such as "110"),
    # so Excel's "number stored as text" warning is switched off for the whole table.
    text_ranges[title] = [f"A2:{get_column_letter(len(cols))}{last}"]
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = freeze
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{last}"
    return ws


def letter_of(cols, header):
    return get_column_letter([c.header for c in cols].index(header) + 1)


def _sheet_parts(entries):
    """Sheet title -> its XML part, read from the workbook's own index."""
    def attrs(text):
        return dict(re.findall(r'([\w:]+)="([^"]*)"', text))
    rels = {a.get("Id"): a.get("Target") for a in
            (attrs(m) for m in re.findall(r"<Relationship\b([^>]*)>", entries["xl/_rels/workbook.xml.rels"].decode()))}
    out = {}
    for m in re.findall(r"<sheet\b([^>]*)>", entries["xl/workbook.xml"].decode()):
        a = attrs(m)
        target = rels[a["r:id"]]
        name = (a["name"].replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
                .replace("&apos;", "'").replace("&amp;", "&"))
        out[name] = target.lstrip("/") if target.startswith("/xl/") else "xl/" + target
    return out


def _mark_text_columns(path, text_ranges):
    """Tell Excel the text in each data table is text on purpose, so "05", "30" or a spec value
    such as "110" is not flagged as a number stored as text. openpyxl reads <ignoredErrors> but
    never writes it, so it is added to the saved file. The schema places it after the page setup
    and before any drawing, comment or table - none of these sheets has one (checked below), so
    the end of the sheet is its place."""
    with zipfile.ZipFile(path) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    parts = _sheet_parts(entries)
    for title, ranges in text_ranges.items():
        part = parts[title]
        xml = entries[part].decode("utf-8")
        for later in ("<drawing", "<legacyDrawing", "<tableParts", "<extLst", "<ignoredErrors"):
            if later in xml:
                raise RuntimeError(f"{title}: holds {later}, so <ignoredErrors> cannot simply be appended")
        tag = ('<ignoredErrors><ignoredError sqref="%s" numberStoredAsText="1"/></ignoredErrors>'
               % " ".join(ranges))
        if xml.count("</worksheet>") != 1:
            raise RuntimeError(f"{title}: unexpected sheet XML")
        entries[part] = xml.replace("</worksheet>", tag + "</worksheet>").encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in entries.items():
            z.writestr(name, blob)
    tmp.replace(path)


# ------------------------------------------------------------------ the workbook

def export_workbook(db_path=DB_PATH, xlsx_path=None):
    db_path = Path(db_path)
    xlsx_path = Path(xlsx_path) if xlsx_path else db_path.parent / XLSX_NAME
    d = load(db_path)
    families = {f["id"]: f for f in d["families"]}
    products = {p["id"]: p for p in d["products"]}
    docs = {s["id"]: s for s in d["source_documents"]}
    meta = {m["key"]: m["value"] for m in d["meta"]}

    for name in ("product_options", "order_codes", "code_legend", "components", "finishes", "specs", "issues"):
        add_names(d[name], products, families)

    cache = FormulaCache()
    text_ranges = {}
    wb = Workbook()
    readme = wb.active
    readme.title = "Read Me"
    summary = wb.create_sheet("Summary")
    OC = quote_sheetname("Order Codes")
    sheets = []          # (title, cols, rows, description, database source) - for the index and the guide

    def table(title, cols, rows, description, source, freeze):
        write_table(wb, title, cols, rows, cache, freeze, text_ranges)
        sheets.append((title, cols, rows, description, source))

    # ---- counts the formulas below should come to, from the database itself
    codes_per_product = Counter(o["product_id"] for o in d["order_codes"])
    products_per_family = Counter(p["family_id"] for p in d["products"])
    codes_per_family = Counter(products[o["product_id"]]["family_id"] for o in d["order_codes"])

    # ---- Product Matrix: the Options sheet pivoted, one row per product
    opts = defaultdict(list)
    for o in d["product_options"]:
        opts[(o["product_id"], o["attribute"])].append(o)
    coded_attrs = {o["attribute"] for o in d["product_options"] if o["code"]} - {"component"}
    matrix_cols = [Col("Product ID", "product_id", "The app's own ID for the product (its specsheet ID).", "int"),
                   Col("Family", "family", "The product's family, looked up for reading."),
                   Col("Product", "product", "The product's name - its variant card on the website."),
                   Col("Category", "category", "The website category - the app's typology.")]
    for attr in ATTR_ORDER:
        label = ATTR_LABEL[attr]
        matrix_cols.append(Col(label, attr, f"Every {label.lower()} choice for the product, in order. "
                               "* = only in its range's code matrix, not on its own sheet. "
                               "† = only on the website.", "long"))
        if attr in coded_attrs:
            matrix_cols.append(Col(f"{label} codes", attr + "__codes",
                                   f"The order-code part of each {label.lower()} choice to the left, in the "
                                   "same order. ? = Duragreen prints no code. ~ = matched by meaning.",
                                   "longcode"))
    matrix_rows = []
    for p in sorted(d["products"], key=lambda p: (natural_key(families[p["family_id"]]["name"]),
                                                   natural_key(p["name"]), p["id"])):
        row = {"product_id": p["id"], "family": families[p["family_id"]]["name"], "product": p["name"],
               "category": p["category"]}
        for attr in ATTR_ORDER:
            chosen = sorted(opts.get((p["id"], attr), []), key=lambda o: natural_key(o["value"]))
            if not chosen:
                continue
            row[attr] = " / ".join(o["value"] + ("*" if o["status"] == "range" else "")
                                   + ("†" if o["source"] == "website" else "") for o in chosen)
            row[attr + "__codes"] = " / ".join((o["code"] or "?") + ("~" if o["status"] == "matched" else "")
                                               for o in chosen)
        matrix_rows.append(row)

    # ---- Families (with v_family_overview's two counts, as live formulas)
    fam_rows = sorted(d["families"], key=lambda f: natural_key(f["name"]))
    for f in fam_rows:
        criteria_safe(f["name"], "family name")
    fam_cols = [
        Col("Family ID", "id", "This family's ID in the database (not an app ID).", "int"),
        Col("Family", "name", "The product page on duragreenlighting.co.uk the products come from, as the "
            "app stores it (its series)."),
        Col("Range", "range_name", "The Duragreen range: the family name's first word (ACME, MOOD, ORBIT ...), "
            "or the family's own name where it is named after a category."),
        Col("Category", "category", "The website category - the app's typology."),
        Col("Code matrix", "code_matrix", "ACME or MOOD where the family's range prints a Specification Code "
            "matrix. Whether it applies to a product is decided per product (Options, status 'range')."),
        Col("Products", "v_family_overview.products", "Products in the family - a live count of the Products "
            "sheet.", "formula",
            lambda r, f: (f"COUNTIF(Products!$B:$B,$A{r})", products_per_family[f["id"]])),
        Col("Order codes", "v_family_overview.order_codes", "Order codes its products' sheets list - a live count "
            "of the Order Codes sheet.", "formula",
            lambda r, f: (f"COUNTIF({OC}!$C:$C,$B{r})", codes_per_family[f["id"]])),
    ]

    # ---- Products
    prod_rows = []
    for p in sorted(d["products"], key=lambda p: (natural_key(families[p["family_id"]]["name"]),
                                                   natural_key(p["name"]), p["id"])):
        row = dict(p)
        f = families.get(p["family_id"])
        row["family"], row["range"] = f["name"], f["range_name"]
        row["spec_sheet"] = docs[p["source_document_id"]]["file_name"] if p["source_document_id"] else None
        if row["order_code_count"] != codes_per_product[p["id"]]:
            raise ValueError(f"product {p['id']}: order_code_count disagrees with its order_codes rows")
        prod_rows.append(row)
    prod_cols = [
        Col("Product ID", "id", "The app's own ID for the product (its specsheet ID).", "int"),
        Col("Family ID", "family_id", "The family's ID on the Families sheet.", "int"),
        Col("Family", "family", "The family's name, looked up for reading."),
        Col("Range", "range", "The family's range, looked up for reading."),
        Col("Product", "name", "The product's name - its variant card on the website."),
        Col("Category", "category", "The website category - the app's typology."),
        Col("Sheet title", "sheet_title", "The first three lines of the spec-sheet PDF, as printed.", "long"),
        Col("Size", "size", "The engine or cut-out size."),
        Col("Optic type", "optic_type", "Fixed, Adjustable, Zoomable, Framing, Pinhole or Wall Wash - read from "
            "the product's name."),
        Col("Mounting style", "mounting_style", "Mini-Trim, Trimless, Semi-Recessed, Surface, Pendant, Track, "
            "Wall, Spike or Recessed - read from the product's name and category."),
        Col("Cut-out", "cutout", "Every cut-out the sheet prints."),
        Col("Ceiling thickness", "ceiling_thickness", SPEC_MEANING),
        Col("IP rating", "ip_rating", SPEC_MEANING),
        Col("UGR", "ugr", SPEC_MEANING),
        Col("CRI", "cri", SPEC_MEANING),
        Col("CRI / RF / RG / R9", "cri_detail", "The sheet's full CRI/RF/RG/R9 line, where it prints one."),
        Col("Lifetime", "lifetime", SPEC_MEANING),
        Col("Efficacy (lm/W)", "efficacy", SPEC_MEANING),
        Col("Binning", "binning", SPEC_MEANING),
        Col("Input voltage", "input_voltage", SPEC_MEANING),
        Col("Output", "output", SPEC_MEANING + " The driver output per wattage.", "long"),
        Col("Ambient temp", "ambient_temp", SPEC_MEANING),
        Col("Class", "class", SPEC_MEANING),
        Col("Material", "material", SPEC_MEANING, "long"),
        Col("Certifications", "certifications", SPEC_MEANING, "long"),
        Col("Code prefix", "code_prefix", "The type letters the product's order codes start with.", "code"),
        Col("Example code", "example_code", "The sheet's 'Code example' line, as printed.", "code"),
        Col("Order codes", "order_code_count", "Order codes the sheet lists - a live count of the Order Codes "
            "sheet. The database stores the same number.", "formula",
            lambda r, p: (f"COUNTIF({OC}!$B:$B,$A{r})", codes_per_product[p["id"]])),
        Col("Project reference", "project_reference", "A project the sheet names under 'DuraGreen project:'."),
        Col("Source document ID", "source_document_id", "The spec-sheet PDF's ID on the Source Documents sheet.",
            "int"),
        Col("Spec sheet", "spec_sheet", "The spec-sheet PDF's file name, looked up for reading."),
    ]

    named = [Col("Product ID", "product_id", "The app's own ID for the product (its specsheet ID).", "int"),
             Col("Family", "family", "The product's family, looked up for reading."),
             Col("Product", "product", "The product's name, looked up for reading.")]

    # ---- Options
    attr_rank = {a: i for i, a in enumerate(ATTR_ORDER)}
    opt_rows = sorted(d["product_options"], key=lambda o: (
        natural_key(o["family"]), natural_key(o["product"]), o["product_id"],
        attr_rank.get(o["attribute"], 99), natural_key(o["value"])))
    opt_cols = named + [
        Col("Attribute", "attribute", "What is being chosen: type, size, wattage, cct, beam_angle, control, cri, "
            "ip_rating, finish, bezel_finish, baffle_finish, body_finish, accessory or component."),
        Col("Value", "value", "One choice, as printed (a few tidied: '2700-6000K' as '2700K-6000K', 'white "
            "bezel' as 'White' under bezel finish).", "long"),
        Col("Code", "code", "The order-code part for this choice. Empty where Duragreen prints none.", "code"),
        Col("Source", "source", "Where the choice was read: order_codes, code_legend, code_matrix, sheet_spec, "
            "sheet_finishes, sheet_components or website."),
        Col("Status", "status", "stated, range or matched - see Read Me."),
        Col("Note", "note", "How a code was arrived at, a palette's RAL or minimum order, or why a value is the "
            "website's.", "long"),
    ]

    # ---- Order Codes
    oc_rows = sorted(d["order_codes"], key=lambda o: (natural_key(o["family"]), natural_key(o["product"]),
                                                     o["product_id"], o["id"]))
    oc_cols = [Col("ID", "id", "This row's ID in the database.", "int")] + named + [
        Col("Code", "code", "The order code, exactly as printed.", "code"),
        Col("Wattage", "wattage", "System power as printed; a twin head reads '10W x 2'."),
        Col("Lumens", "lumens", "System lumens as the table prints them, where it has one lumen column.", "num"),
        Col("Lumens 92Ra", "lumens_92ra", "System lumens at CRI 92, where the table gives one column per CRI.",
            "num"),
        Col("Lumens 98Ra", "lumens_98ra", "System lumens at CRI 98, where the table gives one column per CRI.",
            "num"),
        Col("Efficacy (lm/W)", "efficacy", "Where the table prints lm/W (LED strips, one High Efficiency table).",
            "num"),
        Col("Optics", "optics", "Beam angle, or a zoom range ('20°-50°')."),
        Col("CCT", "cct", "Where the table has a CCT column."),
        Col("CRI", "cri", "Where the table has a CRI column."),
        Col("Length", "length", "Where the table has a length column."),
        Col("Spots", "spots", "Number of heads, where the table states it."),
        Col("Input voltage", "input_voltage", "Where the table has an input-voltage column."),
        Col("Other printed text", "extra", "Anything else on the row - kept rather than dropped."),
        Col("Read by", "parsed_by", "heading: one value per column the table heading names. value shapes: the "
            "values did not line up with the heading, so each was placed by its shape."),
        Col("Type code", "type_code", "The type letters (AFL = adjustable, per the sheets' legends).", "code"),
        Col("Size code", "size_code", "The engine or cut-out size digits.", "code"),
        Col("Wattage code", "wattage_code", "The wattage as written in the code ('06W').", "code"),
        Col("Variant code", "variant_code", "Anything between the wattage and the CCT - material, shape, trim, "
            "twin head ('-AL', 'X2-Q').", "code"),
        Col("CCT prefix", "cct_prefix", "A letter printed before the CCT digits ('H', on the High Efficiency "
            "range only). Duragreen does not say what it means.", "code"),
        Col("CCT code", "cct_code", "The CCT digits (30 = 3000K).", "code"),
        Col("Control code", "control_code", "The control letter (N = non-dim).", "code"),
        Col("Beam prefix", "beam_prefix", "A letter printed before the beam digits ('L', on some step and "
            "shelf lights). Duragreen does not say what it means.", "code"),
        Col("Beam code", "beam_code", "The beam digits - the Optics column's own digits (05 = 5°, 2050 = "
            "20°-50°).", "code"),
        Col("Suffix code", "suffix_code", "Anything after the beam - usually a bezel or body finish ('-B').",
            "code"),
        Col("Split into parts", "decomposed", "Yes when the parts join back into the code exactly, a CCT and "
            "control part was found, and the beam part agrees with the Optics column. No: kept whole.", "yesno"),
        Col("Table heading", "table_header", "The heading of the table the row came from.", "long"),
        Col("Source line", "source_line", "The row exactly as the PDF's text reads.", "long"),
    ]

    # ---- the smaller tables
    legend_rows = by_product(d["code_legend"], "position")
    legend_cols = named + [
        Col("Position", "position", "The segment's place in the example code.", "int"),
        Col("Token", "token", "The segment, as printed.", "code"),
        Col("Meaning", "meaning", "What the sheet says it means."),
        Col("Attribute", "attribute", "The attribute the meaning was read as (type, size, wattage, cct, control, "
            "beam_angle, finish, variant ...)."),
    ]
    matrix_tbl_rows = sorted(d["code_matrices"], key=lambda m: (m["matrix"], m["segment_order"], m["_rowid"]))
    matrix_tbl_cols = [
        Col("Matrix", "matrix", "ACME or MOOD."),
        Col("Segment order", "segment_order", "The segment's place in the code.", "int"),
        Col("Segment", "segment", "The matrix's column heading: Type, Engine or Cut out, Power, Colour Temp, "
            "Driver, Beam Width, Bezel Colour, Baffle Colour, Options."),
        Col("Code", "code", "The code for this option.", "code"),
        Col("Meaning", "meaning", "The option, as printed."),
        Col("Example token", "example_token", "This segment of the matrix's own printed example code.", "code"),
        Col("Printed on (sheets)", "printed_on_products", "How many spec sheets print this matrix.", "int"),
    ]
    comp_rows = by_product(d["components"], "_rowid")
    comp_cols = named + [
        Col("SKU", "sku", "The part number, as printed.", "code"),
        Col("Kind", "kind", "Light engine, Bezel, Bezel (trimless), Baffle or Track part."),
        Col("Note", "note", "The part's printed name, or how it is ordered.", "long"),
    ]
    fin_rows = by_product(d["finishes"], "palette", "_rowid")
    fin_cols = named + [
        Col("Palette", "palette", "Standard Curated (7 finishes, minimum order 20), Listed on sheet, or a range "
            "matrix's bezel / baffle colours."),
        Col("Finish", "name", "The finish."),
        Col("RAL", "ral", "The RAL number, where printed.", "code"),
        Col("Code", "code", "The finish's code, where a matrix gives one.", "code"),
        Col("MOQ", "moq", "Minimum order quantity, where printed.", "num"),
    ]
    spec_rows = sorted(d["specs"], key=lambda s: (natural_key(s["family"]), natural_key(s["product"]),
                                                 s["product_id"], s["source"] != "pdf", s["_rowid"]))
    spec_cols = named + [
        Col("Source", "source", "pdf: the spec sheet. website: the product's card on duragreenlighting.co.uk."),
        Col("Key", "key", "The spec's name, as printed."),
        Col("Canonical key", "canonical_key", "The app's field name for it, where the app has one."),
        Col("Value", "value", "As printed (photometry text that the PDF's layout runs into the line removed).",
            "long"),
    ]
    drv_rows = sorted(d["drivers"], key=lambda x: (natural_key(x["brand"] or "~"), natural_key(x["name"])))
    for x in drv_rows:
        x["spec_sheet"] = docs[x["source_document_id"]]["file_name"] if x["source_document_id"] else None
        criteria_safe(x["brand"], "driver brand")
    drv_cols = [
        Col("ID", "id", "The app's own ID for the driver.", "int"),
        Col("Name", "name", "As the website names it."),
        Col("Brand", "brand", "Read from the name; blank where the name gives none."),
        Col("Output type", "output_type", "CC (constant current) or CV (constant voltage), from the name."),
        Col("Control", "control", "From the name, else the website."),
        Col("Tunable", "tunable", "Yes when the name says Tunable.", "yesno"),
        Col("Output voltage", "output_voltage", "Output voltage or range, from the name."),
        Col("Wattage", "wattage", "From the name, else the website."),
        Col("IP rating", "ip_rating", "From the name, else the website."),
        Col("Input voltage", "input_voltage", "From the website."),
        Col("Source document ID", "source_document_id", "The PDF's ID on the Source Documents sheet.", "int"),
        Col("Spec sheet", "spec_sheet", "The PDF's file name, looked up for reading."),
    ]
    issue_rows = sorted(d["issues"], key=lambda i: (i["kind"], i["issue"], natural_key(i["family"]),
                                                   natural_key(i["product"]), i["_rowid"]))
    issue_cols = named + [
        Col("Kind", "kind", "app_gap: the app lacks or misstates it. source_conflict: Duragreen's own sources "
            "disagree. unreadable: no usable spec sheet."),
        Col("Field", "field", "What it is about."),
        Col("App value", "app_value", "What the app holds, where relevant.", "long"),
        Col("Catalogue value", "catalogue_value", "What the catalogue says.", "long"),
        Col("Issue", "issue", "The finding.", "long"),
    ]
    view_rows = sorted(d["v_configurator"], key=lambda v: (natural_key(v["family"]), natural_key(v["product"]),
                                                          v["product_id"], attr_rank.get(v["attribute"], 99)))
    view_cols = [
        Col("Product ID", "product_id", "The app's own ID for the product.", "int"),
        Col("Family", "family", "The product's family."),
        Col("Product", "product", "The product's name."),
        Col("Attribute", "attribute", "What is being chosen."),
        Col("Options", "options", "Every value, ' / '-separated, in the database's own order.", "long"),
        Col("Codes", "codes", "The code for each value, in the same order. ? = none printed.", "long"),
        Col("Option count", "option_count", "How many values.", "int"),
    ]
    syn_rows = sorted(d["value_synonyms"], key=lambda s: s["_rowid"])
    syn_cols = [
        Col("Attribute", "attribute", "The attribute the rule is for."),
        Col("Sheet value", "sheet_value", "The wording on a spec sheet."),
        Col("Matrix meaning", "matrix_meaning", "The matrix entry it is matched to."),
        Col("Why", "why", "Why the two mean the same.", "long"),
    ]
    users = {}
    for p in d["products"]:
        if p["source_document_id"]:
            users[p["source_document_id"]] = (p["id"], p["name"], "Product")
    for x in d["drivers"]:
        if x["source_document_id"]:
            users[x["source_document_id"]] = (x["id"], x["name"], "Driver")
    doc_rows = []
    for s in sorted(d["source_documents"], key=lambda s: s["id"]):
        row = dict(s)
        row["used_by_id"], row["used_by"], row["used_by_kind"] = users.get(s["id"], (None, None, None))
        doc_rows.append(row)
    doc_cols = [
        Col("ID", "id", "The PDF's ID in the database.", "int"),
        Col("File name", "file_name", "The PDF's name in the app's uploads folder."),
        Col("Pages", "pages", "Pages in the PDF.", "int"),
        Col("Characters", "chars", "Characters of text the PDF holds.", "int"),
        Col("Read OK", "read_ok", "No when the PDF could not be read.", "yesno"),
        Col("Note", "note", "Why it could not be read."),
        Col("Used by ID", "used_by_id", "The product or driver whose sheet this is (its app ID).", "int"),
        Col("Used by", "used_by", "Its name."),
        Col("Used by kind", "used_by_kind", "Product or Driver."),
    ]
    meta_rows = sorted(d["meta"], key=lambda m: m["_rowid"])
    meta_cols = [Col("Key", "key", "What the line records."),
                 Col("Value", "value", "Its value.", "long")]

    # ---- the data sheets, in reading order
    table("Product Matrix", matrix_cols, matrix_rows,
          "One row per product: every configurable choice and its code, side by side.",
          "product_options, pivoted", "D2")
    table("Families", fam_cols, fam_rows,
          "The product pages on the website, each with its range and live counts.",
          "families + v_family_overview", "C2")
    table("Products", prod_cols, prod_rows,
          "One row per product (variant card): every spec its sheet prints.", "products", "F2")
    table("Options", opt_cols, opt_rows,
          "Every configurable choice: product x attribute x value, with its code, source and status.",
          "product_options", "D2")
    table("Order Codes", oc_cols, oc_rows,
          "Every orderable code in every code table, with its figures and its parts.", "order_codes", "F2")
    table("Code Legend", legend_cols, legend_rows,
          "Each sheet's decoded 'Code example', segment by segment.", "code_legend", "D2")
    table("Code Matrices", matrix_tbl_cols, matrix_tbl_rows,
          "The ACME and MOOD option-to-code matrices.", "code_matrices", "B2")
    table("Components", comp_cols, comp_rows,
          "Parts ordered separately: light engines, bezels, baffles, track parts.", "components", "D2")
    table("Finishes", fin_cols, fin_rows,
          "Finish palettes per product, with RAL, code and minimum order where printed.", "finishes", "D2")
    table("Specs", spec_cols, spec_rows,
          "Every 'Key: value' from each spec sheet and from the website, as written.", "specs", "D2")
    table("Drivers", drv_cols, drv_rows, "The power supplies.", "drivers", "C2")
    table("Issues", issue_cols, issue_rows,
          "Everything missing from the app, contradictory between sources, or unreadable.", "issues", "D2")
    table("Configurator View", view_cols, view_rows,
          "The database's own v_configurator view: options and codes per product and attribute.",
          "v_configurator", "D2")
    table("Value Synonyms", syn_cols, syn_rows,
          "How sheet wording was matched to matrix entries.", "value_synonyms", "A2")
    table("Source Documents", doc_cols, doc_rows,
          "Each spec-sheet PDF read, and what uses it.", "source_documents", "C2")
    table("Build Info", meta_cols, meta_rows, "When and how the database was built.", "meta", "A2")

    # status / kind colours, as rules on the column rather than paint on each cell
    ws = wb["Options"]
    status_col = letter_of(opt_cols, "Status")
    rng = f"{status_col}2:{status_col}{len(opt_rows) + 1}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"range"'],
                                                  fill=PatternFill("solid", fgColor="FFF2CC")))
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"matched"'],
                                                  fill=PatternFill("solid", fgColor="DDEBF7")))
    ws = wb["Issues"]
    kind_col = letter_of(issue_cols, "Kind")
    rng = f"{kind_col}2:{kind_col}{len(issue_rows) + 1}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"source_conflict"'],
                                                  fill=PatternFill("solid", fgColor="FCE4D6")))
    ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=['"unreadable"'],
                                                  fill=PatternFill("solid", fgColor="EDEDED")))

    # ---- Column Guide, from the same column definitions the sheets were written with
    guide_rows = [{"sheet": title, "column": c.header, "source": c.field, "meaning": c.meaning}
                  for title, cols, _, _, source in sheets for c in cols]
    guide_cols = [Col("Sheet", "sheet", "The sheet."), Col("Column", "column", "The column's heading."),
                  Col("From the database", "source", "The table and field it comes from; a count, where it is "
                      "one."), Col("Meaning", "meaning", "What it holds.", "long")]
    write_table(wb, "Column Guide", guide_cols, guide_rows, cache, "C2", text_ranges)

    write_summary(summary, d, prod_cols, opt_cols, oc_cols, issue_cols, cache)
    write_readme(readme, meta, sheets, cache)

    # Built as a working file and moved into place once, finished: the two rewrites below never
    # leave a half-done workbook under the real name.
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    work = xlsx_path.with_name(xlsx_path.name + ".part")
    wb.save(work)
    _retrying(_embed_cached_values, work, cache)
    _retrying(_mark_text_columns, work, text_ranges)
    try:
        _retrying(work.replace, xlsx_path)
    except PermissionError:
        raise SystemExit(f"'{xlsx_path.name}' is open somewhere (Excel?) - close it and run this again. "
                         f"The new copy is waiting at {work}") from None
    return xlsx_path


def _retrying(fn, *args, attempts=12, wait=0.5):
    """Windows can hold a file that was written a moment ago - a virus scanner, the search indexer,
    the preview pane - and a rewrite then fails with "access denied" for a second or two. Each
    step here is safe to repeat (it reads its input afresh and writes a new file), so it is
    retried briefly before the failure is believed."""
    import time
    for attempt in range(attempts):
        try:
            return fn(*args)
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(wait)


def _head_row(ws, row, labels, start_col=1):
    for c, label in enumerate(labels, start=start_col):
        cell = ws.cell(row=row, column=c, value=label)
        cell.font, cell.fill, cell.alignment = HEAD, HEAD_FILL, HEAD_ALIGN


def _formula(ws, cache, row, col, formula, value, bold=False):
    cell = cache.put(ws, row, col, formula, value, "#,##0")
    cell.font = BOLD if bold else BODY
    cell.alignment = TOP
    return cell


def _label(ws, row, col, text, font=BODY, wrap=False):
    cell = ws.cell(row=row, column=col, value=text)
    cell.font = font
    cell.alignment = TOP_WRAP if wrap else TOP
    return cell


def write_summary(ws, d, prod_cols, opt_cols, oc_cols, issue_cols, cache):
    """Counts by category, attribute, range, brand and issue - every figure a formula over the
    data sheets, carrying the number the database gives (checked independently after export)."""
    P, O, OC, I, F, D = "Products", "Options", quote_sheetname("Order Codes"), "Issues", "Families", "Drivers"
    pc = {c.header: get_column_letter(i) for i, c in enumerate(prod_cols, start=1)}
    oc = {c.header: get_column_letter(i) for i, c in enumerate(opt_cols, start=1)}
    occ = {c.header: get_column_letter(i) for i, c in enumerate(oc_cols, start=1)}
    ic = {c.header: get_column_letter(i) for i, c in enumerate(issue_cols, start=1)}
    products, options, codes = d["products"], d["product_options"], d["order_codes"]
    codes_per_product = Counter(o["product_id"] for o in codes)

    _label(ws, 1, 1, "Summary", TITLE)
    _label(ws, 2, 1, "Every figure on this sheet is a live formula over the sheet it names.", SUBTLE)
    row = 4

    def section(title, labels):
        nonlocal row
        _label(ws, row, 1, title, SECTION)
        _head_row(ws, row + 1, labels)
        row += 2

    def total_row(first, last, cols):
        nonlocal row
        _label(ws, row, 1, "Total", BOLD)
        for c in cols:
            letter = get_column_letter(c)
            value = sum(ws.cell(row=r, column=c).value is not None and cache.values[(ws.title, f"{letter}{r}")]
                        for r in range(first, last + 1))
            _formula(ws, cache, row, c, f"SUM({letter}{first}:{letter}{last})", value, bold=True)
        for c in range(1, max(cols) + 1):
            ws.cell(row=row, column=c).fill = TOTAL_FILL
        row += 2

    # 1. products by category
    section("Products by category", ["Category", "Products", "With order codes", "Order codes"])
    first = row
    for cat in sorted({p["category"] or "" for p in products}, key=natural_key):
        criteria_safe(cat, "category")
        # A product with no typology yet has an empty Category cell. Its row is labelled for a
        # reader, and counted with the criterion "" - which matches empty cells - rather than by
        # pointing at the label, which would count products literally called "(unclassified)".
        _label(ws, row, 1, cat or "(unclassified)")
        crit = f"$A{row}" if cat else '""'
        mine = [p for p in products if (p["category"] or "") == cat]
        _formula(ws, cache, row, 2, f"COUNTIF({P}!${pc['Category']}:${pc['Category']},{crit})", len(mine))
        _formula(ws, cache, row, 3, f"COUNTIFS({P}!${pc['Category']}:${pc['Category']},{crit},"
                 f"{P}!${pc['Order codes']}:${pc['Order codes']},\">0\")",
                 sum(1 for p in mine if codes_per_product[p["id"]] > 0))
        _formula(ws, cache, row, 4, f"SUMIF({P}!${pc['Category']}:${pc['Category']},{crit},"
                 f"{P}!${pc['Order codes']}:${pc['Order codes']})",
                 sum(codes_per_product[p["id"]] for p in mine))
        row += 1
    total_row(first, row - 1, [2, 3, 4])

    # 2. configurable choices
    section("Configurable choices (Options sheet)",
            ["Attribute", "Stated", "Range", "Matched", "Total", "With a code", "Without a code"])
    first = row
    attrs = sorted({o["attribute"] for o in options}, key=lambda a: (ATTR_ORDER + [a]).index(a))
    for attr in attrs:
        criteria_safe(attr, "attribute")
        _label(ws, row, 1, attr)
        mine = [o for o in options if o["attribute"] == attr]
        for c, status in enumerate(STATUSES, start=2):
            _formula(ws, cache, row, c, f"COUNTIFS({O}!${oc['Attribute']}:${oc['Attribute']},$A{row},"
                     f"{O}!${oc['Status']}:${oc['Status']},\"{status}\")",
                     sum(1 for o in mine if o["status"] == status))
        _formula(ws, cache, row, 5, f"SUM(B{row}:D{row})", len(mine))
        _formula(ws, cache, row, 6, f"COUNTIFS({O}!${oc['Attribute']}:${oc['Attribute']},$A{row},"
                 f"{O}!${oc['Code']}:${oc['Code']},\"<>\")", sum(1 for o in mine if o["code"]))
        _formula(ws, cache, row, 7, f"E{row}-F{row}", sum(1 for o in mine if not o["code"]))
        row += 1
    total_row(first, row - 1, [2, 3, 4, 5, 6, 7])

    # 3. order codes
    section("Order codes (Order Codes sheet)", ["", "Rows"])
    for label, formula, value in (
            ("Order codes listed", f"COUNTA({OC}!$A:$A)-1", len(codes)),
            ("Split into parts", f"COUNTIF({OC}!${occ['Split into parts']}:${occ['Split into parts']},\"Yes\")",
             sum(1 for o in codes if o["decomposed"])),
            ("Read by their table's heading", f"COUNTIF({OC}!${occ['Read by']}:${occ['Read by']},\"heading\")",
             sum(1 for o in codes if o["parsed_by"] == "heading")),
            ("Read by their values' shapes", f"COUNTIF({OC}!${occ['Read by']}:${occ['Read by']},\"value shapes\")",
             sum(1 for o in codes if o["parsed_by"] == "value shapes")),
            ("Products with order codes", f"COUNTIF({P}!${pc['Order codes']}:${pc['Order codes']},\">0\")",
             sum(1 for p in products if codes_per_product[p["id"]] > 0)),
            ("Products without order codes", f"COUNTIF({P}!${pc['Order codes']}:${pc['Order codes']},0)",
             sum(1 for p in products if codes_per_product[p["id"]] == 0))):
        _label(ws, row, 1, label)
        _formula(ws, cache, row, 2, formula, value)
        row += 1
    row += 1

    # 4. families by range
    section("Families by range", ["Range", "Families", "Products"])
    first = row
    fam = {f["id"]: f for f in d["families"]}
    for rng in sorted({f["range_name"] for f in d["families"]}, key=natural_key):
        criteria_safe(rng, "range")
        _label(ws, row, 1, rng)
        _formula(ws, cache, row, 2, f"COUNTIF({F}!$C:$C,$A{row})",
                 sum(1 for f in d["families"] if f["range_name"] == rng))
        _formula(ws, cache, row, 3, f"COUNTIF({P}!${pc['Range']}:${pc['Range']},$A{row})",
                 sum(1 for p in products if fam[p["family_id"]]["range_name"] == rng))
        row += 1
    total_row(first, row - 1, [2, 3])

    # 5. drivers by brand
    section("Drivers by brand", ["Brand", "Drivers"])
    first = row
    for brand in sorted({x["brand"] for x in d["drivers"] if x["brand"]}, key=natural_key):
        _label(ws, row, 1, brand)
        _formula(ws, cache, row, 2, f"COUNTIF({D}!$C:$C,$A{row})",
                 sum(1 for x in d["drivers"] if x["brand"] == brand))
        row += 1
    _label(ws, row, 1, "(no brand in the name)")
    _formula(ws, cache, row, 2, f"COUNTIFS({D}!$A:$A,\"<>\",{D}!$C:$C,\"\")",
             sum(1 for x in d["drivers"] if not x["brand"]))
    row += 1
    total_row(first, row - 1, [2])

    # 6. issues
    section("Issues (Issues sheet)", ["Issue", "Kind", "Rows"])
    first = row
    seen = sorted({(i["issue"], i["kind"]) for i in d["issues"]},
                  key=lambda t: (t[1], -sum(1 for i in d["issues"] if (i["issue"], i["kind"]) == t)))
    for issue, kind in seen:
        criteria_safe(issue, "issue")
        _label(ws, row, 1, issue, wrap=True)
        _label(ws, row, 2, kind)
        _formula(ws, cache, row, 3, f"COUNTIFS({I}!${ic['Issue']}:${ic['Issue']},$A{row},"
                 f"{I}!${ic['Kind']}:${ic['Kind']},$B{row})",
                 sum(1 for i in d["issues"] if i["issue"] == issue and i["kind"] == kind))
        row += 1
    total_row(first, row - 1, [3])

    ws.column_dimensions["A"].width = 62
    for letter in "BCDEFG":
        ws.column_dimensions[letter].width = 15


def write_readme(ws, meta, sheets, cache):
    _label(ws, 1, 1, "Duragreen Catalogue", TITLE)
    _label(ws, 2, 1, "Every table of duragreen_catalogue.sqlite, every row - Duragreen's range, laid out for "
           "configuration, with where each fact came from.", SUBTLE)
    row = 4
    for label, value in (("Database built", meta.get("built_at")),
                         ("Workbook exported", datetime.datetime.now().isoformat(timespec="seconds")),
                         ("Built from", meta.get("source")),
                         ("Rule", meta.get("rule"))):
        _label(ws, row, 1, label, BOLD)
        _label(ws, row, 2, value, wrap=True)
        row += 1
    row += 1
    _label(ws, row, 1, "How to read it", SECTION)
    row += 1
    notes = [
        "Product ID is the app's own ID for the product (its specsheet ID), so every row here maps back to "
        "the product in the app.",
        "Status of a choice: stated = Duragreen prints it for this product. range = printed in its range's "
        "code matrix (ACME or MOOD), applied because the product's own codes belong to that range. matched = "
        "the sheet's wording was matched to a matrix entry by meaning; Value Synonyms lists every rewording.",
        "No code is ever derived: an empty code means Duragreen prints none. Where the website and the spec "
        "sheet disagree, the spec sheet wins and the disagreement is on the Issues sheet.",
        "On the Product Matrix: * = only in the range's code matrix, not on the product's own sheet. "
        "† = only on the website. In the codes columns: ? = no code printed, ~ = code matched by meaning.",
        "The counts - Rows below, everything on Summary, and the count columns on Products and Families - are "
        "live formulas. Everything else is data copied from the database.",
        "Every data sheet has filters on its header row. The sheet names below are links. Column Guide says "
        "what every column means.",
        "The findings and the questions for Duragreen are in README.md, in the same folder as this file.",
    ]
    for note in notes:
        _label(ws, row, 1, "•", BODY)
        _label(ws, row, 2, note, wrap=True)
        row += 1
    row += 1
    _label(ws, row, 1, "Sheets", SECTION)
    row += 1
    _head_row(ws, row, ["Sheet", "What it holds", "Rows", "From the database"])
    row += 1
    index = [("Summary", "Counts by category, attribute, range, brand and issue - live formulas over the "
              "sheets below.", None, "")] + [(t, desc, rows, src) for t, _, rows, desc, src in sheets] + \
            [("Column Guide", "What every column on every data sheet means.", "guide", "")]
    for title, desc, rows, source in index:
        cell = _label(ws, row, 1, title, LINK)
        # a link inside the workbook: a location, not a target, so no external relationship is made
        cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"{quote_sheetname(title)}!A1", display=title)
        _label(ws, row, 2, desc, wrap=True)
        if rows is not None:
            n = (sum(len(c) for _, c, _, _, _ in sheets) if rows == "guide" else len(rows))
            _formula(ws, cache, row, 3, f"COUNTA({quote_sheetname(title)}!$A:$A)-1", n)
        _label(ws, row, 4, source)
        row += 1
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 92
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 30


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=DB_PATH, help="the SQLite file to export")
    ap.add_argument("--out", type=Path, default=None, help="where to write the .xlsx (default: beside the db)")
    args = ap.parse_args()
    path = export_workbook(args.db, args.out)
    print(f"written: {path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
