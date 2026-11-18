"""Loads DuraGreen's code-building rules from its 2026 General Catalogue, so a BOQ line gets the
code DuraGreen's rules give its configuration - not only the few codes a spec sheet prints.

Reads "Duragreen Catalogue/duragreen_general_catalogue_2026.json", which
scripts/catalogues/read_duragreen_general_catalogue.py writes from the catalogue PDF. Then:

  * vendor_code_rules gets the catalogue's two code tables, ACME (p.33, the one every range's
    pages follow) and MOOD (p.222), entry by entry in their printed order, and every product
    page's decoded example ("30 = 3000K", "N = non dim") as LEGEND rows. These rows are the
    catalogue's and nothing else, so they are rebuilt from it on every run.
  * each DuraGreen product is linked to its catalogue page by its printed codes: a page links
    when its code table prints one of the product's codes. MOOD's spec sheets add a -W the
    catalogue leaves off (MFL5506W-30N24-W against MFL5506W-30N24), so a code also links when it
    is the other with a finish added.
  * a linked product gets its page's code example as its code template (a vendor_order_codes row
    marked "(code example)", cut into pieces and carrying the page's legend), the page's printed
    codes it does not have yet (decoded into a configuration by the product's own words), and
    the finishes the page shows - bezel, baffle or structure colour - as options to quote it on.
  * the code parts the 19 Oct backfill took from the spec sheets' printed code matrix are
    removed. That matrix is older than the catalogue and disagrees with it (phase cut is M there,
    P in the catalogue; AB is Antique Bronze there, Antique Brass in the catalogue); the
    catalogue's tables now answer the same question. Every removed row is written to a CSV.

What this never does: change a price (base_price, price_adders, list_price, price_entries),
delete a product or a printed code, change a BOQ line, or remove an option a product already
has. Products whose printed codes are also another product's are listed, not changed.

All in one transaction. --dry-run does everything, reports, and rolls back.

    python scripts/backfills/backfill_duragreen_code_rules.py --dry-run
    python scripts/backfills/backfill_duragreen_code_rules.py
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
from collections import Counter, defaultdict
from itertools import product
from types import SimpleNamespace

from app import duragreen, models
from app.database import SessionLocal
from app.duragreen_sheet import cct_options_from, sibling_number_of

MAKE = duragreen.MAKE
FOLDER = _REPO_ROOT / "Duragreen Catalogue"
SOURCE = FOLDER / "duragreen_general_catalogue_2026.json"
CAT = duragreen.SOURCE_NAME

# Parts the 19 Oct backfill took from the spec sheets' old code matrix, or matched to it by
# meaning. A part a person entered, or one printed in the product's own codes, is never touched.
STALE_SOURCES = {"code_matrix"}
STALE_STATUSES = {"range", "matched"}

# Which palette group gives the options of which finish piece. ACME pages print one palette for
# bezel and baffle alike (Standard + Curated); MOOD pages print the two apart.
PALETTE_FOR = {"bezel_finish": ("bezel", "standard", "curated"),
               "baffle_finish": ("baffle", "standard", "curated"),
               "finish": ("standard", "curated", "bezel")}
# The options a page states in its spec column: (field, label, how the list is split). Control
# is split on commas only, because "On/off" and "0/1-10V" contain a slash.
SPEC_OPTIONS = [("cct", "CCT", r"/"), ("control", "Control", r","), ("beam_angle", "Optics", r"/")]


def cut(example_code, legend):
    """The example as [[name, token], ...] joining back into it exactly. Each legend token is
    found in order; whatever lies between two tokens is a separator. A few examples leave out a
    piece their legend explains (p.210's legend has TL, its example has not): that piece is
    skipped, since the code's head is taken from the printed codes anyway (app/duragreen.py)."""
    parts, i = [], 0
    for item in legend:
        j = example_code.find(item["token"], i)
        if j < 0:
            parts.append(["skipped", item["token"]])
            continue
        if j > i:
            parts.append(["sep", example_code[i:j]])
        parts.append([item["field"], item["token"]])
        i = j + len(item["token"])
    if i < len(example_code):
        parts.append(["sep", example_code[i:]])
    skipped = [t for n, t in parts if n == "skipped"]
    parts = [p for p in parts if p[0] != "skipped"]
    # Between two explained pieces there should only be separators. Anything else is a piece
    # the legend does not explain (p.421's example starts MFP, its legend says STP).
    skipped += [t for n, t in parts if n == "sep" and not re.fullmatch(r"[-/()]+", t)]
    assert "".join(t for _n, t in parts) == example_code
    return parts, skipped


def links(product_codes, page_codes, key=lambda code: code):
    """How many of a product's printed codes a page's code table prints (allowing MOOD's -W).
    `key` reads both sides the same way first (cct_blind)."""
    n = 0
    for p in product_codes:
        p = key(p)
        if any(p == key(c) or p.startswith(key(c) + "-") or key(c).startswith(p + "-") for c in page_codes):
            n += 1
    return n


def cct_blind(code):
    """A code with its CCT digits masked. The CCT is a choice, not the fitting: p.410 prints the
    Alabaster Globe pendant's codes at 2700K (RTPE5706W-AG-27N120), its spec sheet at 3000K
    (RTPE5706W-AG-30N120) - the same three fittings, which an exact comparison never linked.
    A High Efficiency CCT (H30) is not masked: no catalogue page prints one, and an HE fitting is
    not the standard page's fitting, so it must not take that page as its own rule."""
    return re.sub(r"-\d{2}(?=[A-Z])", "-##", code)


def sibling_number(product_codes, spread):
    """A numbered sibling's own number, on a page that prints another sibling - else None.

    Sparkle's page (p.452) prints the -5 and -6 face patterns: codes WFL351.3W-5 ..., example
    WFL352.5W-30N-5-W with "5 / style". The 35-1 product's own sheet prints WFL351.3W-1: the same
    code with its own number where the page has 5. So the page's rule is its rule too, with 1 as
    its style. Only a bare-digit style piece the page's codes end in counts - the page itself
    then shows that the number is the last piece of the short code."""
    styles = [it["token"] for it in spread["legend"] if it["field"] == "style" and re.fullmatch(r"\d", it["token"])]
    if len(styles) != 1:
        return None
    stems = {c[:-1] for c in (spread["codes"] or []) if c.endswith("-" + styles[0])}
    numbers = {sibling_number_of(p) for p in product_codes}
    if not stems or len(numbers) != 1 or None in numbers:
        return None
    number = numbers.pop()
    if number != styles[0] and any(p[:-1] in stems for p in product_codes):
        return number
    return None


def name_unknown(sheet, rows, own, rules, range_name):
    """A printed code's unnamed pieces ("?30": its sheet's table does not say which CCT) named by
    duragreen.decode_piece - the one rule the website importer uses too. Returns how many."""
    named = 0
    for c in rows:
        cfg = dict(c.config or {})
        for k, v in list(cfg.items()):
            if str(v).startswith("?"):
                value = duragreen.decode_piece(sheet, k, v[1:], own.get(k, []), rules, range_name)
                if value:
                    cfg[k] = value
                    named += 1
        if cfg != c.config:
            c.config = cfg
    return named


def adapt_to_sibling(parts, number):
    """The example's pieces with its style piece replaced by the sibling's own number."""
    return [[n, number if n == "style" else t] for n, t in parts]


def every_code(pattern_row, own_words, rules, range_name, sheet):
    """{code: configuration} for every configuration of the product's options, each built the
    way `pattern_row` (one of its own printed codes) is built. A code reading two ways is left
    out (None), and so is any option whose code would only be DuraGreen's pattern. A catalogue
    code is then decoded by looking it up here - cutting "MFL141.3W-30N15" apart by position
    cannot tell where 30 ends and N begins."""
    fields = [duragreen.PART_FIELD[n] for n, _t in pattern_row.parts if duragreen.PART_FIELD.get(n)]
    tokens = {}
    for field in fields:
        tokens[field] = []
        for value in duragreen._offered(sheet, field):
            tok, status, _src, _note = duragreen.lookup(field, value, own_words.get(field, []), rules, range_name)
            if tok and status != duragreen.UNCONFIRMED:
                tokens[field].append((value, tok))
    out = {}
    # the version this pattern is - its mounting, its voltage - goes with every code built from it
    version = {k: v for k, v in (pattern_row.config or {}).items() if k in duragreen.SELECTORS}
    for combo in product(*[tokens[f] for f in fields]):
        config = dict(version, **{f: v for f, (v, _t) in zip(fields, combo)})
        token_of = {f: t for f, (_v, t) in zip(fields, combo)}
        pieces = [[n, token_of[duragreen.PART_FIELD[n]] if duragreen.PART_FIELD.get(n) else t]
                  # a finish suffix is left off; the 24V DC suffix is what this version is
                  for n, t in pattern_row.parts if n != "suffix" or t == duragreen.DC_SUFFIX]
        code = "".join(t for _n, t in pieces)
        out[code] = None if code in out else (config, pieces)
    return out


# The code tables DuraGreen prints outside the 2026 catalogue (read_duragreen_code_matrices.py):
# EVOLINE's own, and the earlier editions of ACME's (app/duragreen.py EARLIER_EDITIONS).
# (scope, range in duragreen_code_matrices.json, document, PDF page)
EXTRA_TABLES = [
    ("EVOLINE", "EVOLINE", "ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 133),
    ("ACME 2025", "ACME", "2025-ENGAGING-GENERRAL-CATALOGUE.pdf", 72),
    ("CHOSEN ONES 2025", "THE CHOSEN ONES (System ranges)", "2025---THE-CHOSEN-ONES.pdf", 174),
    ("ACME 2024", "ACME", "ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 33),
    ("BELLA 41", "BELLA 41", "ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 167),
    ("GLAMO 68", "GLAMO 68", "ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 191),
    ("ARTFOCUS 36", "ARTFOCUS 36", "ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf", 213),
]
# Entries a table prints that every other DuraGreen table contradicts - left out, with the reason.
# (scope, field, code, meaning): why
MISPRINTS = {
    ("EVOLINE", "cct", "2765", "2700K-6000K"):
        "every other DuraGreen table writes 2765 for 2700K-6500K and 2700K-6000K as 2760 (both catalogues, "
        "The Chosen Ones, the price list's ACME, ARTFOCUS and GLAMO tables); no printed code uses either",
}
SOURCE_LABEL = {"ACME-PUBLIC-PRICE-LIST-UK-UPDATED.pdf": "DuraGreen ACME public price list (Sep 2024)",
                "2025-ENGAGING-GENERRAL-CATALOGUE.pdf": "DuraGreen 2025 General Catalogue",
                "2025---THE-CHOSEN-ONES.pdf": "DuraGreen 2025 The Chosen Ones"}
MATRICES_JSON = _REPO_ROOT / "Duragreen Catalogue" / "duragreen_code_matrices.json"


def extra_tables():
    """[(scope, source text, the table as read)] - every table in EXTRA_TABLES, found by its
    document and page, so a re-read of the documents is what a re-run loads."""
    editions = json.loads(MATRICES_JSON.read_text(encoding="utf-8"))["editions"]
    out = []
    for scope, rng, doc, pno in EXTRA_TABLES:
        m = next(e for e in editions if e["range"] == rng and e["document"].endswith(doc) and e["pdf_page"] == pno)
        what = "code table" if scope in duragreen.RANGE_PAGES else "code table, %s edition" % scope.split()[-1]
        if scope == "ACME 2025":
            what += "; the same table 35 ACME spec sheets print"
        page = "p.%s" % m["printed_page"] if m["printed_page"] else "PDF p.%s" % pno   # the 2025 matrices print no number
        out.append((scope, "%s %s (%s %s)" % (SOURCE_LABEL[doc], page,
                                                "the System ranges'" if scope.startswith("CHOSEN") else m["range"], what), m))
    return out


def version_label(version, others, rules):
    """"RTC23 - mini-trim contour": the version's own code, and what DuraGreen's legends say its
    distinguishing piece means - only where they give exactly one meaning (PJRT is printed both as
    a recessed and as a surface pendant, so it stays just PJRT114)."""
    meanings = defaultdict(set)
    for r in rules:
        if r.attribute in ("type", "style", "trim"):
            meanings[r.code.upper()].add(r.meaning.strip())
    said = []
    for piece in re.findall(r"/[A-Z]+|-\d+$|^[A-Z]+", version):
        if all(piece in o for o in others):
            continue                                   # every version has it: says nothing apart
        if piece == duragreen.DC_SUFFIX:
            said.append("24V DC")
            continue
        key = piece.lstrip("/").upper()
        m = meanings.get(key, set())
        # A single letter means too many things across the catalogue (F is a floor light on one
        # page and frosted or framing on others) to name another product's piece.
        if len(m) == 1 and len(key) > 1:
            said.append(next(iter(m)))
    return version + (" - " + ", ".join(said) if said else "")


def offer_versions(db, sheets, rules, report):
    """Products whose sheet prints several fittings that nothing chosen tells apart - ORBIT's
    recessed-trim RTC23 and trimless TLC23, ARTFOCUS with and without its standard base (/SB), a
    pendant on a recessed or a surface rose - are given the version as a choice, as mounting and
    voltage are (duragreen.SELECTORS): each printed row carries its version, and the product lists
    them. Without it these products had no code at all, for any configuration."""
    n = 0
    for sid, sheet in sheets.items():
        rows = [c for c in db.query(models.VendorOrderCode).filter(models.VendorOrderCode.specsheet_id == sid,
                                                                   models.VendorOrderCode.make == MAKE)
                if not duragreen.is_example(c) and c.parts and not str(c.source or "").startswith("entered by")]
        versions = sorted({duragreen._version(c) for c in rows})
        options = dict(sheet.options or {})
        if len(versions) < 2:
            if "version" in options:                   # an earlier run's, no longer true
                options.pop("version")
                sheet.options = options
            continue
        if any(len(duragreen._offered(sheet, k)) > 1 for k in ("voltage", "mounting")) \
                or duragreen._version_by_name(sheet, rows, rules) is not None \
                or all(duragreen._version_by_choice(sheet, c.config or {}, rows) is not None for c in rows):
            continue                                   # already told apart by a choice or the name
        labels = {v: version_label(v, [o for o in versions if o != v], rules) for v in versions}
        for c in rows:
            c.config = dict(c.config or {}, version=labels[duragreen._version(c)])
        options["version"] = [labels[v] for v in versions]
        sheet.options = options
        n += 1
        report.append({"specsheet_id": sid, "product": sheet.product_name, "what": "versions offered as a choice",
                       "detail": " | ".join(labels[v] for v in versions)})
    return n


def offer_no_driver(db, sheets, report):
    """Every product whose code carries a control letter, and which offers a control, is also
    offered No Driver - X in every code table DuraGreen prints - for when the studio quotes its own
    driver (owner, 26 Oct 2026). duragreen.with_no_driver decides; the importer uses the same."""
    n = 0
    for sid, sheet in sheets.items():
        codes = db.query(models.VendorOrderCode).filter(models.VendorOrderCode.specsheet_id == sid,
                                                        models.VendorOrderCode.make == MAKE).all()
        options = duragreen.with_no_driver(sheet.options, codes)
        if options != dict(sheet.options or {}):
            sheet.options = options
            n += 1
            report.append({"specsheet_id": sid, "product": sheet.product_name, "what": "No Driver offered",
                           "detail": ", ".join(options["control"])})
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = json.loads(SOURCE.read_text(encoding="utf-8"))
    spreads = cat["spreads"]
    db = SessionLocal()
    report = []
    try:
        sheets = {s.id: s for s in db.query(models.Specsheet).filter(models.Specsheet.make == MAKE)}
        prices_before = {(s.id, s.base_price, json.dumps(s.price_adders or {}, sort_keys=True), s.list_price,
                          json.dumps(s.list_price_by_wattage or {}, sort_keys=True)) for s in sheets.values()}
        n_price_entries = db.query(models.PriceEntry).join(models.Specsheet).filter(models.Specsheet.make == MAKE).count()
        n_boq_items = db.query(models.BOQItem).count() if hasattr(models, "BOQItem") else None

        # 1) the rules -------------------------------------------------------------------
        extra = extra_tables()
        # A letter a person added to one of these tables from DuraGreen's answer (Nomenclature,
        # "entered by ...") is theirs, not the catalogue's: a reload never deletes it (27 Oct 2026).
        old_rules = db.query(models.VendorCodeRule).filter(
            models.VendorCodeRule.make == MAKE,
            ~models.VendorCodeRule.source.like("entered by%"),
            models.VendorCodeRule.source.like(CAT + "%") | models.VendorCodeRule.scope.in_([t[0] for t in extra])).all()
        for r in old_rules:
            db.delete(r)
        db.flush()
        n_rules = Counter()
        for name, m in cat["matrices"].items():
            src = "%s p.%s (%s code table)" % (CAT, m["printed_page"], name)
            for pos, seg in enumerate(m["segments"]):
                entries = seg["entries"] or [{"code": seg["example"], "meaning": "system power, e.g. %s" % seg["example"]}]
                for e in entries:
                    db.add(models.VendorCodeRule(make=MAKE, scope=name, segment=seg["segment"], attribute=seg["field"],
                                                 position=pos, dash_after=seg["segment"] in (m.get("dash_after") or []),
                                                 meaning=e["meaning"], code=e["code"], source=src))
                    n_rules[name] += 1
        for scope, src, m in extra:
            for pos, seg in enumerate(m["segments"]):
                if not seg["field"]:
                    continue
                for e in seg["entries"]:
                    why = MISPRINTS.get((scope, seg["field"], e["code"], e["meaning"]))
                    if why:
                        print("left out: %s %s %s = %s - %s" % (scope, seg["field"], e["code"], e["meaning"], why))
                        continue
                    db.add(models.VendorCodeRule(make=MAKE, scope=scope, segment=seg["segment"], attribute=seg["field"],
                                                 position=pos, dash_after=seg["segment"] in (m.get("dash_after") or []),
                                                 meaning=e["meaning"], code=e["code"], source=src))
                    n_rules[scope] += 1
        for s in spreads:
            src = "%s p.%s" % (CAT, s["printed_pages"][1])
            for pos, item in enumerate(s["legend"]):
                db.add(models.VendorCodeRule(make=MAKE, scope="LEGEND", segment=" ".join(s["title"][:2]),
                                             attribute=item["field"], position=pos, meaning=item["meaning"],
                                             code=item["token"], source=src))
                n_rules["LEGEND"] += 1
        db.flush()
        rules = db.query(models.VendorCodeRule).filter(models.VendorCodeRule.make == MAKE).all()

        # 2) remove the old matrix's parts ---------------------------------------------------
        stale = (db.query(models.VendorCodePart)
                 .filter(models.VendorCodePart.make == MAKE)
                 .filter((models.VendorCodePart.source.in_(STALE_SOURCES)) |
                         (models.VendorCodePart.status.in_(STALE_STATUSES))).all())
        stale_rows = [{"id": p.id, "specsheet_id": p.specsheet_id, "attribute": p.attribute, "value": p.value,
                       "code": p.code, "status": p.status, "source": p.source, "note": p.note} for p in stale]
        for p in stale:
            db.delete(p)
        db.flush()

        # 3) link products to pages ------------------------------------------------------------
        codes_of = defaultdict(list)
        for c in db.query(models.VendorOrderCode).filter(models.VendorOrderCode.make == MAKE).order_by(
                models.VendorOrderCode.sort_order, models.VendorOrderCode.id):
            codes_of[c.specsheet_id].append(c)
        # an earlier run's templates and catalogue codes are replaced, so a re-run is the same as a first run
        # Only the two kinds of row this script writes - "2026 General Catalogue p.N" and
        # "... p.N (code example)" - are replaced. Anything else naming the catalogue (the track
        # backfill's accessory codes, a code a person typed in) is another writer's, and stays.
        own_row = re.compile(r"^%s p\.\d+(?: %s)?$" % (re.escape(CAT), re.escape(duragreen.EXAMPLE_TAG)))
        for sid, rows in codes_of.items():
            keep = []
            for c in rows:
                if own_row.match(c.source or ""):
                    db.delete(c)
                else:
                    keep.append(c)
            codes_of[sid] = keep
        db.flush()

        owner = defaultdict(set)          # printed code -> products printing it
        for sid, rows in codes_of.items():
            for c in rows:
                owner[c.code].add(sid)

        linked, sibling_of = {}, {}
        for sid, sheet in sheets.items():
            mine = [c.code for c in codes_of.get(sid, [])]
            if not mine:
                continue
            # A page's code table, not its example: the example of a trimless page can read
            # like its mini-trim sister's code (p.218 prints AFL7810W-SS-30N2050-TBB).
            scored = sorted(((links(mine, s["codes"] or [s["example_code"]]), k) for k, s in enumerate(spreads)),
                            reverse=True)
            best = [k for n, k in scored if n and n == scored[0][0]]
            if not best:
                # The same codes at another CCT. Stricter than the exact match: one page only, and
                # it must print every one of the product's codes.
                blind = sorted(((links(mine, s["codes"] or [s["example_code"]], cct_blind), k)
                                for k, s in enumerate(spreads)), reverse=True)
                top = [k for n, k in blind if n == len(mine)]
                if len(top) == 1:
                    best = top
                    report.append({"specsheet_id": sid, "product": sheet.product_name, "what": "linked ignoring CCT",
                                   "detail": "p.%s prints its codes at another CCT" % spreads[top[0]]["pdf_pages"][0]})
            if not best:
                # a numbered sibling of the products the page prints (Sparkle 35-1 on the -5/-6 page)
                found = [(k, num) for k, s in enumerate(spreads) if (num := sibling_number(mine, s))]
                if len(found) == 1:
                    sibling_of[sid] = found[0][1]
                    best = [found[0][0]]
            if len(best) == 1:
                linked[sid] = spreads[best[0]]
            elif len(best) > 1:
                report.append({"specsheet_id": sid, "product": sheet.product_name, "what": "ambiguous",
                               "detail": "pages " + ", ".join(str(spreads[k]["pdf_pages"][0]) for k in best)})

        # 4) template, catalogue codes, finishes ----------------------------------------------
        n_templates = n_added_codes = n_undecoded = n_options = n_spec_options = n_named = n_suffix_options = 0
        for sid, s in linked.items():
            sheet = sheets[sid]
            parts, skipped = cut(s["example_code"], s["legend"])
            page = s["printed_pages"][1]
            if skipped:
                report.append({"specsheet_id": sid, "product": sheet.product_name, "what": "example leaves out a piece",
                               "detail": "p.%s %s leaves out %s" % (page, s["example_code"], " ".join(skipped))})
            legend = [[it["field"], it["token"], it["meaning"]] for it in s["legend"]]
            printed = {"example": s["example"], "legend": legend, "page": page,
                       "pdf_page": s["pdf_pages"][1], "skipped": skipped}
            if sid in sibling_of:
                # The page's example is another sibling's; this product's is the same with its own
                # number. Said in the template, so NPD can show both.
                number = sibling_of[sid]
                parts = adapt_to_sibling(parts, number)
                old_style = next(it["token"] for it in s["legend"] if it["field"] == "style")
                printed["adapted"] = {"style": [old_style, number], "page_example": s["example_code"]}
                legend = [[f, number if f == "style" else t, m] for f, t, m in legend]
                printed["legend"] = legend
                report.append({"specsheet_id": sid, "product": sheet.product_name, "what": "numbered sibling",
                               "detail": "p.%s prints pattern %s; this product is pattern %s (its own codes)"
                                         % (page, old_style, number)})
            db.add(models.VendorOrderCode(make=MAKE, specsheet_id=sid, code="".join(t for _n, t in parts), config={},
                                          printed=printed,
                                          parts=parts, source="%s p.%s %s" % (CAT, page, duragreen.EXAMPLE_TAG),
                                          sort_order=900))
            n_templates += 1

            # finishes the page shows, appended to the product's own list - the finish pieces
            # are the ones after the code's head (its beam, or its last chosen piece)
            end = duragreen._head_end(parts)
            tail = [n for n, _t in parts[end + 1:]] if end >= 0 else []
            options = dict(sheet.options or {})
            changed = False
            for field in ("bezel_finish", "baffle_finish", "finish"):
                if field not in tail:
                    continue
                names = []
                for group in PALETTE_FOR[field]:
                    names += [p["name"] for p in (s.get("palette") or {}).get(group, [])]
                    if names and group in ("bezel", "baffle"):
                        break
                have = list(options.get(field) or [])
                seen = {duragreen.canon(field, v) for v in have}
                for n in names:
                    if duragreen.canon(field, n) not in seen:
                        have.append(n)
                        seen.add(duragreen.canon(field, n))
                        changed = True
                        n_options += 1
                if have:
                    options[field] = have
            # the CCTs, controls and optics the page states, which a spec sheet often leaves out
            for field, label, splitter in SPEC_OPTIONS:
                said = (s.get("specs") or {}).get(label) or ""
                # A CCT line is a sentence ("2700K, 3000K (2400K ... on request)"): its CCTs are
                # read out of it, never split from it (duragreen_sheet.cct_options_from).
                stated = cct_options_from(said) if field == "cct" else \
                    [v.strip() for v in re.split(splitter, said) if v.strip()]
                have = list(options.get(field) or [])
                seen = {duragreen.canon(field, v) for v in have}
                for v in stated:
                    if duragreen.canon(field, v) and duragreen.canon(field, v) not in seen:
                        have.append(v)
                        seen.add(duragreen.canon(field, v))
                        changed = True
                        n_spec_options += 1
                if have:
                    options[field] = have
            # The finish a printed code ends in: T14106W-30N2050-AB is Antique Brass, so the product
            # comes in it even where the page's swatches were not read. The suffix is the first
            # finish piece of the catalogue's code (MOOD's sheets print the bezel as -W).
            first = next((n for n in tail if n in ("bezel_finish", "finish")), None)
            # Its own range's colour table names the letters; outside both ranges, only a meaning
            # both tables give counts (app/duragreen.py: another range's table is a lead only).
            scopes = [duragreen.range_of_page(s["pdf_pages"][1])] if duragreen.range_of_page(s["pdf_pages"][1]) \
                else list(duragreen.RANGE_PAGES)
            for c in (codes_of.get(sid, []) if first else []):
                token = next((t for n, t in (c.parts or []) if n == "suffix"), "").lstrip("-")
                said = {duragreen.canon(first, r.meaning): r.meaning for r in rules
                        if r.scope in scopes and r.attribute == duragreen.TABLE_FIELD.get(first, first) and r.code == token}
                if token and len(said) == 1:
                    have = list(options.get(first) or [])
                    if not any(duragreen.canon(first, v) in said for v in have):
                        have.append(next(iter(said.values())))
                        options[first] = have
                        changed = True
                        n_suffix_options += 1
            if changed:
                sheet.options = options

            mine = codes_of.get(sid, [])
            own = defaultdict(list)
            for field, meaning, token, src in duragreen._own_meanings(mine, [], SimpleNamespace(
                    printed={"legend": legend}, source="p.%s" % s["pdf_pages"][1])):
                own[field].append((meaning, token, src))
            range_name = duragreen.range_of_page(s["pdf_pages"][1])

            # A printed code whose CCT or driver the sheet's table does not name ("?30"): named
            # now, when exactly one of the product's options takes that piece.
            n_named += name_unknown(sheet, mine, own, rules, range_name)

            # the page's printed codes the product does not have yet
            shaped = [c for c in mine if c.parts]
            if not shaped:
                continue
            built = [(p, every_code(p, own, rules, range_name, sheet))
                     for p in {duragreen._version(c): c for c in shaped}.values()]
            have = {c.code for c in mine}
            order = max([c.sort_order or 0 for c in mine] + [0])
            for code in s["codes"]:
                if code in have or any(h.startswith(code + "-") for h in have):
                    continue
                found = next((codes_built[code] for _p, codes_built in built if codes_built.get(code)), None)
                if found is None:
                    n_undecoded += 1
                    report.append({"specsheet_id": sid, "product": sheet.product_name, "what": "catalogue code not decoded",
                                   "detail": code})
                    continue
                order += 1
                config, pieces = found
                db.add(models.VendorOrderCode(make=MAKE, specsheet_id=sid, code=code, config=config, printed={},
                                              parts=pieces, source="%s p.%s" % (CAT, page),
                                              sort_order=order))
                n_added_codes += 1

        # The same naming for products no catalogue page prints: their own codes are all there is
        # (a path light's ALP6003W-AG-30N120 - its 30 is its 3000K, its N non dim).
        for sid, sheet in sheets.items():
            mine = codes_of.get(sid, [])
            if sid in linked or not any(str(v).startswith("?") for c in mine for v in (c.config or {}).values()):
                continue
            own = defaultdict(list)
            for field, meaning, token, src in duragreen._own_meanings(mine, [], None):
                own[field].append((meaning, token, src))
            n_named += name_unknown(sheet, mine, own, rules, "")

        # 5) products that print the same codes as another ---------------------------------------
        shared = Counter()
        for code, sids in owner.items():
            if len(sids) > 1:
                for sid in sids:
                    shared[sid] += 1
        for sid, n in sorted(shared.items()):
            report.append({"specsheet_id": sid, "product": sheets[sid].product_name, "what": "shares codes",
                           "detail": "%d printed codes also on product(s) %s" % (n, ", ".join(
                               str(o) for o in sorted({o for c in codes_of.get(sid, []) for o in owner[c.code]} - {sid})))})
        unlinked = [sid for sid in sheets if codes_of.get(sid) and sid not in linked]
        for sid in unlinked:
            report.append({"specsheet_id": sid, "product": sheets[sid].product_name, "what": "no catalogue page",
                           "detail": ", ".join(c.code for c in codes_of[sid][:3])})
        db.flush()

        # 6) a sheet that prints several fittings: the version is a choice ------------------------
        n_versioned = offer_versions(db, sheets, rules, report)
        db.flush()

        # 7) No Driver (X) wherever the code carries a driver letter ----------------------------
        n_no_driver = offer_no_driver(db, sheets, report)
        db.flush()

        # checks ----------------------------------------------------------------------------
        after = {s.id: s for s in db.query(models.Specsheet).filter(models.Specsheet.make == MAKE)}
        assert set(after) == set(sheets), "a product appeared or vanished"
        assert prices_before == {(s.id, s.base_price, json.dumps(s.price_adders or {}, sort_keys=True), s.list_price,
                                  json.dumps(s.list_price_by_wattage or {}, sort_keys=True)) for s in after.values()}, "a price changed"
        assert n_price_entries == db.query(models.PriceEntry).join(models.Specsheet).filter(
            models.Specsheet.make == MAKE).count(), "a price entry changed"
        if n_boq_items is not None:
            assert n_boq_items == db.query(models.BOQItem).count(), "a BOQ line changed"

        print("rules: %s" % dict(n_rules))
        print("old matrix parts removed: %d (%s)" % (len(stale_rows), dict(Counter(r["attribute"] for r in stale_rows))))
        print("products linked to a catalogue page: %d of %d with printed codes (%d not linked)"
              % (len(linked), sum(1 for sid in sheets if codes_of.get(sid)), len(unlinked)))
        print("templates: %d   catalogue codes added: %d (%d not decoded)   finish options added: %d"
              % (n_templates, n_added_codes, n_undecoded, n_options))
        print("finishes added from printed suffixes: %d" % n_suffix_options)
        print("CCT / control / optics options added from the pages: %d   printed codes' unknown options named: %d"
              % (n_spec_options, n_named))
        print("products offered their sheet's versions as a choice: %d" % n_versioned)
        print("products offered No Driver (X): %d" % n_no_driver)
        print("report: %s" % dict(Counter(r["what"] for r in report)))
        print("checked: prices unchanged (%d products, %d price entries), BOQ lines unchanged"
              % (len(prices_before), n_price_entries))

        tag = "_dry_run" if args.dry_run else ""
        with open(FOLDER / ("code_rules_report%s.csv" % tag), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["specsheet_id", "product", "what", "detail"])
            w.writeheader()
            w.writerows(report)
        if stale_rows:
            with open(FOLDER / ("code_parts_removed%s.csv" % tag), "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(stale_rows[0]))
                w.writeheader()
                w.writerows(stale_rows)
        if args.dry_run:
            db.rollback()
            print("DRY RUN - rolled back")
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
