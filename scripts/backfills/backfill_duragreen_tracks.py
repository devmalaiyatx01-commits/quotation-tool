"""DuraGreen's track systems, as their spec sheets print them: what each part is, what it is
quoted on (length, colour, the side of its polarity groove), and its order code.

Reads "Duragreen Catalogue/duragreen_tracks_2026.json", which
scripts/catalogues/read_duragreen_track_sheets.py writes from the sheets (and checks against the
2026 General Catalogue's own track pages). Then, for DuraGreen's products:

  * 220-240V TRACK 1L, 3L and 3L-DIM - the six track products (surface & suspended, flush) are
    quoted on length (1m / 2m / 3m) and colour (Black / White), with every code the sheet prints:
    GB2300-W is the 1L surface track, 3 m, white. Their mounting, profile size and circuits are
    filled in, and the "separately ordered parts" list is rewritten - the old one paired codes
    with stray dimension figures ("55 88 GB12").
  * The parts those sheets print - end feed, L-, T-, X- and middle feed, connectors, end cap -
    are products of their own, one per part (as DuraGreen's website already does for its 48V
    parts), because each has its own code and is its own line on a BOQ: quoted on colour, and a
    feed on the side its polarity groove is on, read from the sheet's icon. A part printed for two
    systems (XTS41, the 3L and 3L-DIM end cap) is one product.
  * 48V MINI-LINE 22 - the four tracks get their code (TC22ST is one code for every length, so
    the length is asked and stated beside it) and corrected facts; the accessories printed beside
    them and on the catalogue's accessory page (p.473) get their codes, as new products where the
    app had none.
  * 48V MINI-LINE 11 (Stucci Micro 11) - the four tracks get their code. Its accessories are
    printed "9601-S/..." with the part after the slash left out, so they are added without a code
    and say so; nobody is given a code DuraGreen has not printed.

New products arrive pending, as the website backfill's do, and wait in NPD (DuraGreen) for a
person to release them. Each new part's picture is its drawing, cut from the sheet.

What this never does: change a price (base_price, price_adders, list_price, price_entries),
delete a product, change a BOQ line, or remove an option a product has. The only rows deleted are
this script's own earlier codes (replaced on every run) and the track products' "separately
ordered parts" code parts, which held the garbled pairs - all written to an undo file first.

All in one transaction. --dry-run does everything, reports, and rolls back; it copies no pictures.

    python scripts/backfills/backfill_duragreen_tracks.py --dry-run
    python scripts/backfills/backfill_duragreen_tracks.py
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
from collections import Counter
from datetime import datetime

from app import duragreen, models
from app.database import IMAGES_DIR, SessionLocal

MAKE = duragreen.MAKE
FOLDER = _REPO_ROOT / "Duragreen Catalogue"
SOURCE = FOLDER / "duragreen_tracks_2026.json"
DRAWINGS = FOLDER / "track_drawings"
# Where a code from the catalogue's accessory page comes from. Deliberately not of the form
# "2026 General Catalogue p.N", which backfill_duragreen_code_rules.py rebuilds as its own.
CATALOGUE_ACCESSORIES = "DuraGreen 2026 General Catalogue p.473 (MINI-LINE 22 accessories)"

# The six 220-240V track products, by the name the website gives them.
PROFILE_PRODUCT = {
    ("TRACK 1L", "Surface & Suspended"): "TRACK 1L SURFACE & SUSPENDED",
    ("TRACK 1L", "Flush Mounting"): "TRACK 1L FLUSH MOUNTING",
    ("TRACK 3L", "Surface & Suspended"): "TRACK 3L SURFACE & SUSPENDED",
    ("TRACK 3L", "Flush Mounting"): "TRACK 3L FLUSH MOUNTING",
    ("TRACK 3L-DIM", "Surface & Suspended"): "TRACK 3L-DIM SURFACE & SUSPENDED",
    ("TRACK 3L-DIM", "Flush Mounting"): "TRACK 3L-DIM FLUSH MOUNTING",
}
MOUNTING = {"Surface & Suspended": "Surface & suspended", "Flush Mounting": "Flush mounting"}
# MINI-LINE 22's tracks, by website name -> the sheet the reader titled them by.
MINI22_PRODUCT = {"SURFACE TRACK": "TC22ST", "TRIMLESS 12.8 TRACK": "TC22RT",
                  "SURFACE MICRO TRACK": "TC22ST-S", "SURFACE ROUND TRACK": "TC22ST-C"}
# Accessory names that the sheets print the same way for different tracks ("Surface horizontal L
# connector" is M22-LSS-1, -S-1 and -C-1): named here by the track they belong to.
ACCESSORY_NAME = {
    "M22-LSS-S-1": "Surface Micro track horizontal L connector",
    "M22-LSS-C-1": "Surface Round track horizontal L connector",
    "M22-LSS-C-3": "Surface Round track horizontal T connector",
    "M22-LSS-8": "Reinforcing plate (surface track)",
    "M22-LRS-8": "Reinforcing plate (trimless track)",
}
TRACK_OF = {"TC22ST": "MINI-LINE 22 surface track (TC22ST)", "TC22RT": "MINI-LINE 22 trimless 12.8 track (TC22RT)",
            "TC22ST-S": "DECO TRACK surface micro (TC22ST-S)", "TC22ST-C": "DECO TRACK surface round (TC22ST-C)"}


def merged(old, new):
    """An option list with the new values added after the old, none twice."""
    out = list(old or [])
    seen = {duragreen.norm(v) for v in out}
    for v in new:
        if duragreen.norm(v) not in seen:
            out.append(v)
            seen.add(duragreen.norm(v))
    return out


def picture(name, apply):
    """The diagram entry for a drawing the reader cut from a sheet (copied into data/images)."""
    if not name or not (DRAWINGS / name).exists():
        return None
    from PIL import Image
    target = IMAGES_DIR / name
    if apply:
        shutil.copyfile(DRAWINGS / name, target)
    with Image.open(DRAWINGS / name) as img:
        w, h = img.size
    return {"path": str(target), "width": w, "height": h, "label": "Product Image"}


class Run:
    def __init__(self, db, apply):
        self.db, self.apply = db, apply
        self.report, self.undo = [], {"created": [], "updated": {}, "codes_deleted": [], "parts_deleted": []}
        self.tally = Counter()
        # By name: DuraGreen names are not all unique (two "ORBIT 45 Track Light" - 220V and 48V),
        # so a name that means two products is refused, never guessed between.
        self.named = {}
        everything = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()
        for s in everything:
            if s.product_name:
                self.named.setdefault(s.product_name, []).append(s)
        self.by_code = {}
        for s in everything:
            code = ((s.params or {}).get("DURAGREEN SPEC") or {}).get("Code")
            if code:
                self.by_code.setdefault(code, []).append(s)

    def note(self, sheet, what, detail=""):
        self.report.append({"specsheet_id": getattr(sheet, "id", ""), "product": getattr(sheet, "product_name", ""),
                            "what": what, "detail": detail})
        self.tally[what] += 1

    def remember(self, s):
        if s.id and str(s.id) not in self.undo["updated"] and s.id not in self.undo["created"]:
            self.undo["updated"][str(s.id)] = {"fields": s.fields, "options": s.options, "params": s.params,
                                               "image_path": s.image_path, "diagrams": s.diagrams}

    def one(self, name):
        """The one DuraGreen product called `name`, or None; two of them stop the run."""
        found = self.named.get(name) or []
        if len(found) > 1:
            raise SystemExit("%d DuraGreen products are called %r (ids %s) - which one is meant cannot be told"
                             % (len(found), name, [s.id for s in found]))
        return found[0] if found else None

    def product(self, name, like, **columns):
        """The product called `name`, made pending (modelled on `like`) if the app has none."""
        s = self.one(name)
        if s is not None:
            self.remember(s)
            return s, False
        s = models.Specsheet(
            make=MAKE, origin="parsed", approval_status="pending", discipline=like.discipline,
            product_name=name, filename=columns.get("filename", like.filename or ""),
            series=columns.get("series", like.series or ""), family=columns.get("family", ""),
            variant=columns.get("variant", name), category=like.category or "", typology=like.typology or "",
            environment=list(like.environment or []), source_url=like.source_url or "",
            source_pdf_path=columns.get("source_pdf_path", like.source_pdf_path or ""),
            fields={}, options={}, params={}, diagrams=[], price_adders={})
        self.db.add(s)
        self.db.flush()
        self.named[name] = [s]
        self.undo["created"].append(s.id)
        self.note(s, "added (pending)", name)
        return s, True

    def set_codes(self, s, rows, sources):
        """Replace this script's codes for `s` (those from `sources`) with `rows`; a code a person
        entered is never touched."""
        for c in self.db.query(models.VendorOrderCode).filter(models.VendorOrderCode.specsheet_id == s.id,
                                                              models.VendorOrderCode.make == MAKE):
            if (c.source or "") in sources:
                self.undo["codes_deleted"].append({"specsheet_id": s.id, "code": c.code, "config": c.config,
                                                   "printed": c.printed, "source": c.source})
                self.db.delete(c)
        self.db.flush()
        for i, (code, config, printed, source) in enumerate(rows):
            self.db.add(models.VendorOrderCode(make=MAKE, discipline=s.discipline, specsheet_id=s.id, code=code,
                                               config=config, printed=printed, parts=[], source=source, sort_order=i))
        self.tally["codes written"] += len(rows)

    def drop_component_parts(self, s):
        """The 19 Oct "separately ordered parts" code parts: each paired a code with whatever text
        stood beside it on the sheet ("55 88 GB12"). The parts are products of their own now."""
        for p in self.db.query(models.VendorCodePart).filter(models.VendorCodePart.specsheet_id == s.id,
                                                             models.VendorCodePart.attribute == "components",
                                                             models.VendorCodePart.source == "sheet_components"):
            self.undo["parts_deleted"].append({"id": p.id, "specsheet_id": s.id, "attribute": p.attribute,
                                               "value": p.value, "code": p.code, "status": p.status,
                                               "source": p.source, "note": p.note})
            self.db.delete(p)

    def fill(self, s, fields=None, options=None, image=None, extra_params=None):
        f = dict(s.fields or {})
        f.update({k: v for k, v in (fields or {}).items() if v})
        o = dict(s.options or {})
        for k, values in (options or {}).items():
            if len(values) > 1:
                o[k] = merged(o.get(k), values)
        params = dict(s.params or {})
        params.update(extra_params or {})
        s.fields, s.options = f, o
        s.params = duragreen.regroup_params(f, params)
        if image and not s.diagrams:
            s.diagrams = [image]
            s.image_path = image["path"]


def run_220v(run, data):
    letters = data["colour_letters"]
    # A part printed by more than one system (the same codes) is one product, named for all of them.
    systems_of = {}
    for system in data["systems_220v"]:
        for part in system["parts"]:
            systems_of.setdefault(tuple(r["stem"] for r in part["rows"]), []).append(system["key"])
    done = set()
    for system in data["systems_220v"]:
        surface_sheet = system["sheets"][0]["file"]
        parts_text = " · ".join("%s %s" % (p["name"], " / ".join(r["stem"] for r in p["rows"])) for p in system["parts"])
        profile_products = {}
        for i, (mounting, block) in enumerate(system["profiles"].items()):
            name = PROFILE_PRODUCT[(system["key"], mounting)]
            s = run.one(name)
            if s is None:
                run.note(None, "track product missing", name)
                continue
            run.remember(s)
            profile_products[mounting] = s
            sheet_file = system["sheets"][i]["file"]
            lengths = [r["length"] for r in block["rows"]]
            colours = [letters[x] for x in block["rows"][0]["finish_letters"]]
            run.fill(s, fields={
                "mounting_style": MOUNTING[mounting], "profile_dimension": block["profile_dimension"],
                "length": " / ".join(lengths), "finish": " / ".join(colours), "input_voltage": "220-240V",
                "circuits": system["circuits"], "components": parts_text,
            }, options={"length": lengths, "finish": colours},
                extra_params={"TRACK SYSTEM": {"System": system["key"], "Codes": " / ".join(r["stem"] for r in block["rows"]),
                                               "Colour letter": "B black, W white, after the code",
                                               "Catalogue": "2026 General Catalogue p.%s" % system["printed_page"]}})
            rows = []
            for r in block["rows"]:
                for x in r["finish_letters"]:
                    rows.append(("%s-%s" % (r["stem"], x), {"length": r["length"], "finish": letters[x]},
                                 {"type": r["type"], "as_printed": "%s- | %s |" % (r["stem"], " | ".join(r["finish_letters"]))},
                                 sheet_file))
            run.set_codes(s, rows, {sheet_file})
            run.drop_component_parts(s)
            run.note(s, "track updated", "%d codes, length %s, colour %s" % (len(rows), lengths, colours))
        like = profile_products.get("Surface & Suspended") or next(iter(profile_products.values()), None)
        if like is None:
            continue
        for part in system["parts"]:
            stems = tuple(r["stem"] for r in part["rows"])
            if stems in done:
                continue                       # already made under the first system printing it
            done.add(stems)
            keys = systems_of[stems]
            label = "TRACK " + " / ".join(k.replace("TRACK ", "") for k in keys)
            name = "%s %s" % (label, part["name"])
            s, _new = run.product(name, like, series=like.series, family=label, variant=part["name"],
                                  filename=surface_sheet)
            colours = [letters[x] for x in part["rows"][0]["finish_letters"]]
            grooves = [r["polarity_groove"] for r in part["rows"] if r.get("polarity_groove")]
            run.fill(s, fields={
                "finish": " / ".join(colours), "polarity_groove": " / ".join(grooves) if len(grooves) > 1 else (grooves[0] if grooves else ""),
                "mounting_style": "Fits %s tracks, surface & suspended and flush" % " and ".join(keys),
                "input_voltage": "220-240V", "circuits": system["circuits"],
            }, options={"finish": colours, "polarity_groove": grooves},
                image=picture(part.get("drawing"), run.apply),
                extra_params={"TRACK SYSTEM": {k: v for k, v in {
                    "Part of": " and ".join(keys), "Codes": " / ".join(stems),
                    "Colour letter": "B black, W white, after the code",
                    "Polarity groove": "as drawn on the sheet's icon - the red line" if grooves else "",
                    "Catalogue": "2026 General Catalogue p.%s" % system["printed_page"]}.items() if v}})
            rows = []
            for r in part["rows"]:
                for x in r["finish_letters"]:
                    config = {"finish": letters[x]}
                    if len(grooves) > 1:
                        config["polarity_groove"] = r["polarity_groove"]
                    rows.append(("%s-%s" % (r["stem"], x), config,
                                 {"part": part["name"], "as_printed": "%s- | %s |" % (r["stem"], " | ".join(r["finish_letters"]))},
                                 surface_sheet))
            run.set_codes(s, rows, {surface_sheet})
            run.note(s, "track part coded", "%d codes%s" % (len(rows), ", polarity %s" % grooves if len(grooves) > 1 else ""))


def mm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def run_mini_line_22(run, data):
    tracks = {t["code"]: t for t in data["mini_line_22"]["tracks"]}
    accessories = {}                                   # code -> (name, note, drawing, track code, sheet file)
    for t in data["mini_line_22"]["tracks"]:
        for a in t["accessories"]:
            accessories.setdefault(a["code"], (a["name"], a.get("note", ""), a.get("drawing"), t["code"], t["file"]))
    extra = {a["Code"]: a for a in data["mini_line_22"]["catalogue_accessories"]}
    like, track_product = None, {}
    for product_name, code in MINI22_PRODUCT.items():
        t = tracks.get(code)
        s = run.one(product_name)
        if s is None or t is None:
            run.note(None, "track product missing", product_name)
            continue
        run.remember(s)
        like = like or s
        track_product[code] = s
        spec = t["spec"]
        dims = re.match(r"W:\s*([\d.]+)\s*mm\s+H:\s*([\d.]+)", spec.get("Dimensions", ""))
        lengths = ["%s mm" % v for v in t["lengths_mm"]]
        own = [a["code"] for a in t["accessories"]]
        run.fill(s, fields={
            "mounting_style": spec.get("Mounting", ""), "material": "Aluminium" if "Alumin" in spec.get("Material", "") else spec.get("Material", ""),
            "finish": "Black", "ip_rating": spec.get("IP rating", ""), "input_voltage": spec.get("Voltage connection", ""),
            "circuits": spec.get("Circuits", ""), "length": " / ".join(lengths),
            "profile_dimension": "W %s x H %s mm" % dims.groups() if dims else "",
            "cutout_dimensions": mm(spec.get("Cut out for plasterboard", "")),
            "components": " · ".join("%s %s" % (ACCESSORY_NAME.get(c, accessories[c][0]), c) for c in own),
        }, options={"length": lengths},
            extra_params={"TRACK SYSTEM": {k: v for k, v in {
                "System": "MINI-LINE 22 48V", "Code": code,
                "Length": "the same code for every length - state the length beside it" if len(lengths) > 1 else "",
                "Catalogue": "2026 General Catalogue p.%s" % t["printed_page"] if t.get("printed_page") else ""}.items() if v}})
        note = {"note": "DuraGreen prints one code for every length - state the length beside it"} if len(lengths) > 1 else {}
        rows = [(code, {"length": L}, dict(note), t["file"]) for L in lengths] if len(lengths) > 1 else [(code, {}, {}, t["file"])]
        run.set_codes(s, rows, {t["file"]})
        run.drop_component_parts(s)
        run.note(s, "track updated", "%s, lengths %s" % (code, lengths or "one"))
    if like is None:
        return
    for code in list(accessories) + [c for c in extra if c not in accessories]:
        printed_name, note, drawing, track, sheet_file = accessories.get(code, (None, "", None, None, None))
        cat = extra.get(code)
        name = ACCESSORY_NAME.get(code) or printed_name or (cat or {}).get("Type", "").capitalize() or code
        existing = [s for s in run.by_code.get(code, []) if s.make == MAKE]
        if existing:
            s = existing[0]
            run.remember(s)
        else:
            # modelled on the track it is printed with, so it links that track's own sheet
            own = track_product.get(track, like)
            s, _new = run.product(name, own, series=own.series, family="MINI-LINE 22", variant=name,
                                  filename=sheet_file or "", source_pdf_path=own.source_pdf_path if sheet_file else "")
            # this script's own product: its sheet is set on every run, so a re-run corrects it
            s.filename, s.source_pdf_path = sheet_file or "", own.source_pdf_path if sheet_file else ""
        source = sheet_file or CATALOGUE_ACCESSORIES
        printed_page = (tracks.get(track) or {}).get("printed_page") if track else (cat or {}).get("printed_page")
        fields = {"finish": "Black"}
        if cat:
            fields.update({"material": cat.get("Material", ""), "finish": cat.get("Finishing", "Black"),
                           "input_voltage": cat.get("Voltage", ""), "mounting_style": cat.get("Fixing", "")})
        run.fill(s, fields=fields, image=picture(drawing or (cat or {}).get("drawing"), run.apply),
                 extra_params={"TRACK SYSTEM": {k: v for k, v in {
                     "Used with": TRACK_OF.get(track, "MINI-LINE 22 48V tracks"), "Note": note,
                     "Source": "spec sheet %s" % sheet_file if sheet_file else CATALOGUE_ACCESSORIES,
                     "Catalogue": "2026 General Catalogue p.%s" % printed_page if printed_page else ""}.items() if v}})
        run.set_codes(s, [(code, {}, {"name": name}, source)], {source})
        run.note(s, "track part coded", code)


def run_mini_line_11(run, data):
    m11 = data["mini_line_11"]
    like = None
    for t in m11["tracks"]:
        s = next((x for x in run.by_code.get(t["code"], []) if x.make == MAKE), None)
        if s is None:
            run.note(None, "track product missing", t["title"])
            continue
        run.remember(s)
        like = like or s
        spec = t["spec"]
        dims = spec.get("Dimensions", "")
        length = re.search(r"L:\s*(\d+)\s*mm", dims)
        run.fill(s, fields={"finish": "Black", "material": "Aluminium" if "Alumin" in spec.get("Material", "") else "",
                            "mounting_style": spec.get("Mounting", ""), "ip_rating": spec.get("IP rating", ""),
                            "input_voltage": spec.get("Voltage connection", ""), "circuits": spec.get("Circuits", ""),
                            "length": "%s mm" % length.group(1) if length else "", "profile_dimension": mm(dims)},
                 extra_params={"TRACK SYSTEM": {"System": "MINI-LINE 11 48V (Stucci Micro 11)", "Code": t["code"]}})
        run.set_codes(s, [(t["code"], {}, {}, t["file"])], {t["file"]})
        run.note(s, "track updated", t["code"])
    if like is None:
        return
    for a in m11["accessories"]:
        name = "Micro 11 " + a["name"] if a["name"] else "Micro 11 part " + a["printed"]
        s, _new = run.product(name, like, series=like.series, family="MINI-LINE 11", variant=a["name"],
                              filename=like.filename or "")
        run.fill(s, extra_params={"TRACK SYSTEM": {
            "Used with": "MINI-LINE 11 48V (Stucci Micro 11) track", "Code as printed": a["printed"],
            "Note": "DuraGreen prints this code without the part after the slash, so it has no order "
                    "code here - ask DuraGreen for the full code and add it in NPD (DuraGreen)"}})
        run.note(s, "no code - printed without its finish part", a["printed"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        sheets = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()
        prices_before = {(s.id, s.base_price, json.dumps(s.price_adders or {}, sort_keys=True), s.list_price,
                          json.dumps(s.list_price_by_wattage or {}, sort_keys=True)) for s in sheets}
        ids_before = {s.id for s in sheets}
        n_price_entries = db.query(models.PriceEntry).count()
        n_boq_items = db.query(models.BOQItem).count()

        run = Run(db, apply=not args.dry_run)
        run_220v(run, data)
        run_mini_line_22(run, data)
        run_mini_line_11(run, data)
        # The website's 48V page "SURFACE TRACK" has one card with no product on it; the 19 Oct
        # backfill stored its name as the text "None". It is reported, and left pending.
        empty = [s for s in db.query(models.Specsheet).filter(models.Specsheet.make == MAKE)
                 if str(s.product_name or "").strip() in ("", "None")]
        for s in empty:
            run.note(s, "website page with no product", "%s - left pending, nothing to quote" % (s.series or ""))
        db.flush()

        after = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()
        assert ids_before <= {s.id for s in after}, "a product vanished"
        assert prices_before == {(s.id, s.base_price, json.dumps(s.price_adders or {}, sort_keys=True), s.list_price,
                                  json.dumps(s.list_price_by_wattage or {}, sort_keys=True))
                                 for s in after if s.id in ids_before}, "a price changed"
        assert all(not s.base_price and not (s.price_adders or {}) for s in after if s.id not in ids_before), "a new product has a price"
        assert n_price_entries == db.query(models.PriceEntry).count(), "a price entry changed"
        assert n_boq_items == db.query(models.BOQItem).count(), "a BOQ line changed"

        for what, n in sorted(run.tally.items()):
            print("%5d  %s" % (n, what))
        print("checked: prices unchanged (%d products, %d price entries), BOQ lines unchanged (%d), no product removed"
              % (len(prices_before), n_price_entries, n_boq_items))
        tag = "_dry_run" if args.dry_run else ""
        with open(FOLDER / ("tracks_report%s.csv" % tag), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["specsheet_id", "product", "what", "detail"])
            w.writeheader()
            w.writerows(run.report)
        if args.dry_run:
            db.rollback()
            print("DRY RUN - rolled back")
        else:
            undo = FOLDER / ("tracks_undo_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
            undo.write_text(json.dumps(run.undo, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
            db.commit()
            print("committed; undo record:", undo)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
