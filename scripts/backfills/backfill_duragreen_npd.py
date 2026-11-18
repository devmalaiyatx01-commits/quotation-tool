"""Fills DuraGreen's products in the app from DuraGreen's own website and spec sheets, as NPD
(DuraGreen) shows them - and records DuraGreen's printed order codes, so a BOQ line gets the
code DuraGreen prints for its configuration.

Reads "Duragreen Catalogue/duragreen_catalogue_site.sqlite", which build_duragreen_catalogue_db.py
--from-crawl writes from a fresh crawl of the whole website (crawl_duragreen_site.py) and every
spec sheet it downloaded. For each variant card on the website:

  * its app row (matched by page title + card name, the importer's identity) gets what an
    engineer reads off the sheet, grouped as app/duragreen.ENGINEER_GROUPS groups it - optical,
    electrical, mechanical, general - into `params`, the flattened values into `fields`, and the
    options it is quoted on into `options`. The spec sheet wins over the website where they
    disagree (the rule build_duragreen_catalogue_db.py and backfill_duragreen_wattage_from_pdf.py
    keep); the website's own card stays in params["DURAGREEN SPEC"], as printed.
  * its source_url is set to its page, and its source_pdf_path to the newest spec sheet - by
    default DuraGreen's own hosted copy (a URL; /api/specsheets/{id}/pdf redirects to it, as it
    does for Lumenpulse), which stays current when DuraGreen re-issues a sheet and costs the
    server no disk. `--pdf copy` copies the downloaded file into data/uploads instead. Either way
    an older local file is kept, never deleted.
  * its printed order codes go to vendor_order_codes, and the code each option contributes to
    vendor_code_parts. Those two tables are DuraGreen's alone: nothing here touches field_codes,
    code_attributes or code_schemes, which are the studio's own (InHouse) product code.

A product on the website that the app does not have is added - make Duragreen, origin "parsed",
approval "pending", so it waits in NPD (DuraGreen) for a person to release it before anyone can
quote it. A product in the app the website no longer shows is left exactly as it is and listed.

What this never does: change a price (base_price, price_adders, list_price, price_entries),
delete a product, change a BOQ line, or turn a DuraGreen row into origin "npd" (that would put it
in InHouse's NPD list). Options marked "range" in the catalogue - applied from the range's matrix,
not printed for the product - are not offered as choices; their codes are kept, marked, for when
DuraGreen confirms them.

All in one transaction. --dry-run does everything, reports, and rolls back; it also copies no
files and downloads nothing.

    python scripts/backfills/backfill_duragreen_npd.py --dry-run
    python scripts/backfills/backfill_duragreen_npd.py
    python scripts/backfills/backfill_duragreen_npd.py --pdf copy      # local copies instead of links
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
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import duragreen_import as imp      # a sheet a product holds is read as NPD reads an upload
from app import duragreen, models
from app.database import SessionLocal, UPLOADS_DIR, IMAGES_DIR
from app.duragreen import ENGINEER_GROUPS, MAKE, norm
from app.environment_classifier import classify_environment
from app.duragreen_sheet import (  # shared with the app's importer
    code_pieces,
    unique,
    merged_options,
    option_value_for_code,
    ATTR_FIELD,
    CHOICE_FIELDS,
    FINISH_ATTRS,
    PRODUCT_COLUMNS,
    part_status,
    _is_trimless_code,
    split_shared_sheet,
    build_product,
    clean_cct_options,
)

CATALOGUE = _REPO_ROOT / "Duragreen Catalogue" / "duragreen_catalogue_site.sqlite"
PDFS = _REPO_ROOT / "Duragreen Catalogue" / "site_crawl" / "pdfs"
REPORT = _REPO_ROOT / "Duragreen Catalogue" / "backfill_report.csv"


def own_sheet(sheet, pdf_files):
    """The spec sheet a product already holds, on this machine: an uploaded file, or DuraGreen's
    link as the crawl saved it (crawl.json's pdf_files). None when neither is here - nothing is
    downloaded from a backfill."""
    ref = sheet.source_pdf_path or ""
    if ref.startswith("http"):
        name = pdf_files.get(ref)
        return (PDFS / name) if name and (PDFS / name).exists() else None
    return Path(ref) if ref and Path(ref).exists() else None


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_catalogue():
    con = sqlite3.connect(CATALOGUE)
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]
    data = {
        "web": q("SELECT * FROM web_pages ORDER BY product_id"),
        "products": {r["id"]: r for r in q("SELECT * FROM products")},
        "families": {r["id"]: r for r in q("SELECT * FROM families")},
        "docs": {r["id"]: r for r in q("SELECT * FROM source_documents")},
        "drivers": {r["id"]: r for r in q("SELECT * FROM drivers")},
        "options": defaultdict(list), "codes": defaultdict(list),
    }
    for r in q("SELECT rowid, * FROM product_options ORDER BY rowid"):
        data["options"][r["product_id"]].append(r)
    for r in q("SELECT * FROM order_codes ORDER BY id"):
        data["codes"][r["product_id"]].append(r)
    data["built_at"] = dict(con.execute("SELECT key, value FROM meta").fetchall()).get("built_at")
    con.close()
    return data


def copy_pdf(name, current, apply):
    """The fresh sheet's path in data/uploads - the current file when it is already the same
    document, otherwise a new copy (the old one is kept: BOQ exports may name it)."""
    src = PDFS / name
    if not src.exists():
        return current, "missing"
    if current and Path(current).exists() and sha(current) == sha(src):
        return current, "same"
    dest = UPLOADS_DIR / name
    if dest.exists() and sha(dest) == sha(src):
        return str(dest), "reused"
    i = 2
    while dest.exists():
        dest = UPLOADS_DIR / f"{Path(name).stem}__{i}{Path(name).suffix}"
        i += 1
    if apply:
        shutil.copy2(src, dest)
    return str(dest), "new copy"


def fetch_image(url, slug, apply):
    if not url:
        return ""
    name = "duragreen_" + re.sub(r"[^A-Za-z0-9_-]+", "_", slug)[:80] + Path(url.split("?")[0]).suffix
    dest = IMAGES_DIR / name
    if apply and not dest.exists():
        # The standard library rather than `requests`: the Oracle VM's venv has no `requests`
        # (checked 19 Oct), and installing a package for one picture download is not worth it.
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (the studio catalogue)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if r.status == 200:
                    dest.write_bytes(r.read())
        except OSError:
            return ""          # no picture beats a broken one; the product still comes in
    return str(dest) if dest.exists() or not apply else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="do everything, report, and roll back")
    ap.add_argument("--pdf", choices=("link", "copy"), default="link",
                    help="link to DuraGreen's hosted spec sheet (default) or copy it into data/uploads")
    ap.add_argument("--app-ids", type=Path, default=None,
                    help="a JSON {catalogue's app id: [series, product_name, family, variant]} from the "
                         "database the catalogue was built against - needed when loading into another one")
    args = ap.parse_args()
    apply = not args.dry_run

    cat = load_catalogue()
    crawl = PDFS.parent / "crawl.json"
    pdf_files = json.loads(crawl.read_text(encoding="utf-8")).get("pdf_files", {}) if crawl.exists() else {}
    moved = split_shared_sheet(cat)
    print(f"catalogue built {cat['built_at']}: {len(cat['web'])} website variants; "
          f"{moved} code row(s) given to the trimless/mini-trim twin they belong to")

    db = SessionLocal()
    tally, report = Counter(), []
    try:
        before = {s.id: s for s in db.query(models.Specsheet).filter(models.Specsheet.make == MAKE)}
        prices_before = {(s.id, s.base_price, json.dumps(s.price_adders or {}, sort_keys=True), s.list_price)
                         for s in before.values()}
        n_price_entries = db.query(models.PriceEntry).join(models.Specsheet).filter(
            models.Specsheet.make == MAKE).count()
        boq_before = db.query(models.BOQItem.id, models.BOQItem.description, models.BOQItem.config).join(
            models.Specsheet).filter(models.Specsheet.make == MAKE).all()
        # the CCTs BOQ lines chose, per product - never cleaned away (see the CCT step below)
        used_cct, cct_cleaned = {}, []
        for item in db.query(models.BOQItem).join(models.Specsheet).filter(models.Specsheet.make == MAKE):
            chosen = {str((item.config or {}).get("cct") or ""), str(item.led_color or "")} - {""}
            used_cct.setdefault(item.specsheet_id, set()).update(chosen)
        seen_app = set()
        # The catalogue says which app row a card matched when it was BUILT. A product this script
        # added since then is not in that answer, so it is looked up again here, by the same
        # identity - otherwise a second run adds every new product a second time (caught by
        # re-running the dry run after the first local run: "added (pending): 157" again).
        by_identity = {((s.series or "").strip().upper(), (s.product_name or "").strip().upper()): s.id
                       for s in before.values()}

        # The catalogue was built against ONE database and records that database's ids. Loaded into
        # another (live, whose backfill-made products have other ids), an id can name a different
        # product there - on 22 Oct's rehearsal 150 named a non-DuraGreen row. With --app-ids each is
        # translated by the product's identity; one that does not translate exactly is left to the
        # identity match below, never guessed.
        id_map = json.loads(args.app_ids.read_text(encoding="utf-8")) if args.app_ids else None
        here = {}
        for s in before.values():
            key = tuple((x or "").strip() for x in (s.series, s.product_name, s.family, s.variant))
            here.setdefault(key, []).append(s.id)
        for web in cat["web"]:
            pid, app_id = web["product_id"], web["app_id"]
            if id_map is not None and app_id is not None:
                ident = id_map.get(str(app_id))
                hits = here.get(tuple((x or "").strip() for x in ident)) if ident else None
                app_id = hits[0] if hits and len(hits) == 1 else None
                tally["catalogue id translated by identity" if app_id is not None else "catalogue id not translated"] += 1
            if app_id is None:
                app_id = by_identity.get((web["page_title"].strip().upper(), web["card_name"].strip().upper()))
            built = build_product(cat, pid, web)
            if app_id is not None:
                sheet = before.get(app_id)
                if sheet is None:
                    tally["matched an app id that is not DuraGreen - skipped"] += 1
                    continue
                seen_app.add(app_id)
                action = "updated"
            else:
                driver = cat["drivers"].get(pid)
                sheet = models.Specsheet(
                    make=MAKE, origin="parsed", approval_status="pending",
                    filename=built["pdf_name"] or "", product_name=web["card_name"],
                    series=web["page_title"],
                    family=(re.match(r"^(\d+)", web["card_name"]).group(1) + "mm")
                    if re.match(r"^(\d+)", web["card_name"]) else "",
                    variant=re.sub(r"^\d+\s*", "", web["card_name"]),
                    category=web["category"] or "",
                    typology="Driver" if driver else (cat["products"].get(pid) or {}).get("category")
                    or duragreen.typology_for(web["page_title"], web["card_name"], web["category"]),
                    environment=classify_environment(web["category"] or ""),
                )
                db.add(sheet)
                action = "added (pending)"

            old = (dict(sheet.fields or {}), dict(sheet.options or {}))
            # Fields the website card carried that the sheet does not state stay as they were.
            fields = dict(sheet.fields or {})
            fields.update(built["fields"])
            options = dict(sheet.options or {})
            for key, new in built["options"].items():
                options[key] = merged_options(options.get(key), new, (sheet.price_adders or {}).get(key) or ())
            # The list can hold what the sheet no longer prints (a priced value, a list a person typed), and
            # a line with no IP chosen prints this field, so it says what the list says.
            if options.get("ip_rating"):
                fields["ip_rating"] = " / ".join(options["ip_rating"])
            # The fresh reading decides what is offered (merged_options), and since 22 Oct it reads a
            # CCT line as CCTs (cct_options_from), so an earlier reading's "3000K (2400K" or
            # "0.5 6420 0.22" drops out above. This is the guard behind it: any CCT option still not
            # a CCT is read again as one - except a value a BOQ line chose or a price adder names,
            # kept exactly. Every change goes to the CSV below.
            if options.get("cct"):
                keep = used_cct.get(sheet.id, set()) | set(((sheet.price_adders or {}).get("cct") or {}))
                options["cct"], cct_changes = clean_cct_options(options["cct"], keep)
                for entry, became in cct_changes:
                    cct_cleaned.append({"specsheet_id": sheet.id, "product": web["card_name"],
                                        "was": entry, "now": " / ".join(became) or "(no CCT in it - removed)"})
            params = dict(sheet.params or {})
            params.update(built["params"])
            sheet.fields, sheet.options, sheet.params = fields, options, params
            sheet.source_url = web["url"]
            if built["pdf_name"] and args.pdf == "copy":
                # the file the crawl saved for this link: DuraGreen reuses names across products
                path, how = copy_pdf(pdf_files.get(web["spec_sheet"], built["pdf_name"]), sheet.source_pdf_path, apply)
                sheet.source_pdf_path = path
                sheet.filename = Path(path).name
                tally["spec sheet: " + how] += 1
            elif web["spec_sheet"] and built["pdf_name"]:
                # Only a sheet the crawl actually downloaded and read is linked: 8 of DuraGreen's
                # own "Specification sheet" links answered 404 on 19 Oct, and a product pointed at
                # one would lose the sheet it has today. Those keep what they have.
                tally["spec sheet: linked" + (" (was a local copy, kept on disk)"
                                              if (sheet.source_pdf_path or "") and not
                                              (sheet.source_pdf_path or "").startswith("http") else "")] += 1
                sheet.source_pdf_path = web["spec_sheet"]
                sheet.filename = Path(web["spec_sheet"].split("?")[0]).name
            if app_id is None and not sheet.image_path:
                sheet.image_path = fetch_image(web["image_url"], web["page_title"] + "_" + web["card_name"], apply)
                if sheet.image_path:
                    sheet.diagrams = [{"path": sheet.image_path, "width": 0, "height": 0,
                                       "label": "Product Image"}]
            db.flush()

            # The sheet's codes and parts are read afresh - but never one a person entered in NPD
            # (source "entered by ..."): that is DuraGreen's answer to a question, which no sheet
            # holds. (Until 22 Oct 2026 every row was deleted here; the catalogue's and the tracks'
            # rows are still rebuilt by the code-rule and track backfills, run after this one.)
            mine = ~models.VendorOrderCode.source.like("entered by%")
            db.query(models.VendorOrderCode).filter(models.VendorOrderCode.specsheet_id == sheet.id, mine).delete(
                synchronize_session=False)
            db.query(models.VendorCodePart).filter(models.VendorCodePart.specsheet_id == sheet.id,
                                                   ~models.VendorCodePart.source.like("entered by%")).delete(
                synchronize_session=False)
            for c in built["codes"]:
                db.add(models.VendorOrderCode(make=MAKE, specsheet_id=sheet.id, source=built["pdf_name"] or "",
                                              discipline=sheet.discipline or models.DEFAULT_DISCIPLINE, **c))
            for p in built["parts"]:
                db.add(models.VendorCodePart(make=MAKE, specsheet_id=sheet.id,
                                             discipline=sheet.discipline or models.DEFAULT_DISCIPLINE, **p))
            tally[action] += 1
            tally["order codes"] += len(built["codes"])
            tally["code parts"] += len(built["parts"])
            changed = [k for k in sorted(set(old[0]) | set(fields)) if old[0].get(k) != fields.get(k)]
            report.append({"specsheet_id": sheet.id, "action": action, "series": sheet.series,
                           "product": sheet.product_name, "url": web["url"],
                           "matched_by": web.get("matched_by") or "",
                           "website_family": web["page_title"],
                           "fields_changed": ", ".join(changed),
                           "options_before": json.dumps(old[1], ensure_ascii=False),
                           "options_after": json.dumps(options, ensure_ascii=False),
                           "order_codes": len(built["codes"]), "spec_sheet": built["pdf_name"] or ""})

        gone = [s for i, s in before.items() if i not in seen_app]
        # A product not on the website now is read from the spec sheet it already holds - one a
        # person uploaded (the Alabaster path lights' ALP6003W-AG.pdf) or one the crawl still has.
        # Everything above starts from a website card, so until 22 Oct 2026 nothing ever read these
        # sheets, and such products had no code at all. Read by the website importer's own reader
        # and written by its own apply() - the path "add from a PDF" in NPD takes - which replaces
        # only codes read from that same sheet and never touches a price or an approval.
        rules = db.query(models.VendorCodeRule).filter(models.VendorCodeRule.make == MAKE).all()
        for s in gone:
            path = own_sheet(s, pdf_files)
            action, n_codes = "not on the website now - left as it was", ""
            if path is not None and (s.typology or "") != "Driver":
                row = imp.pdf_row({"product_name": s.product_name or "", "series": s.series or "",
                                   "family": s.family or "", "variant": s.variant or "", "typology": s.typology or ""})
                built = imp.build(row, str(path), imp.pdf_name(s.source_pdf_path) if (s.source_pdf_path or "")
                                  .startswith("http") else path.name)
                if built["read_ok"] and built["codes"]:
                    imp.apply(db, models, s, row, built, discipline=s.discipline, rules=rules,
                              pdf_ref=s.source_pdf_path, picture=lambda: "")
                    if (s.options or {}).get("cct"):
                        keep = used_cct.get(s.id, set()) | set(((s.price_adders or {}).get("cct") or {}))
                        cleaned, cct_changes = clean_cct_options(s.options["cct"], keep)
                        if cct_changes:
                            s.options = dict(s.options, cct=cleaned)
                            for entry, became in cct_changes:
                                cct_cleaned.append({"specsheet_id": s.id, "product": s.product_name, "was": entry,
                                                    "now": " / ".join(became) or "(no CCT in it - removed)"})
                    action, n_codes = "not on the website now - read from its own spec sheet", len(built["codes"])
                    tally["read from its own spec sheet (not on the website)"] += 1
            report.append({"specsheet_id": s.id, "action": action, "series": s.series, "product": s.product_name,
                           "url": s.source_url or "", "order_codes": n_codes,
                           "spec_sheet": path.name if path is not None else ""})
        db.flush()

        # The things this must never do, checked rather than assumed.
        after = {s.id: s for s in db.query(models.Specsheet).filter(models.Specsheet.make == MAKE)}
        assert prices_before == {(s.id, s.base_price, json.dumps(s.price_adders or {}, sort_keys=True),
                                  s.list_price) for i, s in after.items() if i in before}, "a price changed"
        assert n_price_entries == db.query(models.PriceEntry).join(models.Specsheet).filter(
            models.Specsheet.make == MAKE).count(), "a price entry changed"
        assert set(before) <= set(after), "a product was deleted"
        assert not any(s.origin == "npd" for s in after.values()), "a DuraGreen row became origin npd"
        assert boq_before == db.query(models.BOQItem.id, models.BOQItem.description, models.BOQItem.config).join(
            models.Specsheet).filter(models.Specsheet.make == MAKE).all(), "a BOQ line changed"

        for k, v in sorted(tally.items()):
            print(f"  {k}: {v}")
        print(f"  in the app but not on the website now (left as they were): {len(gone)}")
        print(f"  checked: prices unchanged ({len(prices_before)} rows, {n_price_entries} price entries), "
              f"no product deleted, none made origin npd, {len(boq_before)} BOQ lines unchanged")
        # A dry run writes a report of its own: overwriting the real run's report with a later
        # rehearsal's would lose the record of what the real run added (it did, on 19 Oct).
        report_path = REPORT.with_name(REPORT.stem + "_dry_run.csv") if args.dry_run else REPORT
        with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["specsheet_id", "action", "series", "product", "url",
                                              "matched_by", "website_family",
                                              "fields_changed", "options_before", "options_after",
                                              "order_codes", "spec_sheet"])
            w.writeheader()
            w.writerows(report)
        print(f"  report: {report_path}")
        # Every CCT option read again, as it was and as it is now - so any one can be put back.
        cct_path = REPORT.with_name("cct_options_cleaned" + ("_dry_run" if args.dry_run else "") + ".csv")
        with open(cct_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["specsheet_id", "product", "was", "now"])
            w.writeheader()
            w.writerows(cct_cleaned)
        print(f"  CCT options read again: {len(cct_cleaned)} entries on "
              f"{len({r['specsheet_id'] for r in cct_cleaned})} products -> {cct_path}")
        if args.dry_run:
            db.rollback()
            print("--dry-run: everything above rolled back - nothing was written, no file copied.")
        else:
            db.commit()
            print("committed.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
