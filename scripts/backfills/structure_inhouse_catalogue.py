"""Splits the InHouse catalogue's composite columns into the fields a product code is built from -
step 2 of the plan in "Product Classification and Codes.xlsx" (sheets 1 and 6) - using only what
each product states.

Two rules decide everything here
--------------------------------
* Nothing is filled in that the product does not state. A field is written only where the
  product's own series text, variant column or parameters say it; a product that says nothing is
  left saying nothing. The first version of this script filled gaps with what was usual - "Round"
  where a name did not say Square, "Single" where it did not say Dual, "220-240V AC" where no DC
  voltage was written, a colour system inferred from the CCT list - and was wrong where it
  mattered: PULSE is marked 220-240V AC by that rule, and its own control gear says 24V/48V DC.
* Nothing a sheet prints is rewritten. The first version also unified spellings in printed values
  ("ON –OFF" -> "ON-OFF", ">95(R9>80)" -> "> 95 (R9 > 80)") and added a BENDING DIRECTION row to
  five LUMEA sheets, which changed 53 generated specsheets. A product code does not need either:
  product_codes.norm() already reads "ON –OFF" and "ON-OFF" as one value. So the run now checks
  every product's printed output - parameters, title line, name bar - and stops if any would
  change.

What it changes
---------------
1. The composite columns. The series column carried several facts; each that the text states
   moves to a field of its own, and what is left is InHouse's own designation, kept as written:
     series  "NYX Dual Square"      -> series NYX, lamp_count Dual, aperture_shape Square
     series  "RAYONLinearDiffused"  -> series RAYON, form Linear, series_variant DIFFUSED
     series  "SOLARA Midi"          -> series SOLARA, family Midi
     series  "CURVE –Mini -Pin"     -> series CURVE, family Mini, series_variant PIN
     series  "PULSE 1000 RGBW"      -> series PULSE, series_variant RGBW (the 1000 is its
                                       LUMINAIRE LENGTH, 1000 mm, which the sheet states)
     series  "Vena S 10 12"         -> series VENA, series_variant S 10 12
     series  "LUMEA 1010BH TV"      -> series LUMEA, series_variant 1010BH TV
   A flexible product's designation (1010BH TV, 0408SV, S 10 12) is its identity in InHouse's own
   shorthand. It is kept whole rather than read as a profile size and a bending direction,
   because the sheets do not say what its digits measure.
   Also from what the product states: adjustability and trim where blank and the variant column
   says Fixed, Adjustable, With Trim or Trimless; supply_voltage where the variant says 24V or
   48V DC; sub_typology where blank and the MOUNTING parameter gives exactly one mounting.
2. Option lists that no longer match their printed value. A "/" inside brackets now qualifies one
   option ("SURFACE ( FIXED / ADJUSTABLE )" is one mounting), and the "/" in "10W/M" is a unit,
   so lists split the old way ("SURFACE ( FIXED", "ADJUSTABLE )"; "10W", "M") are rebuilt. A
   per-metre wattage list is rebuilt as 10W/M, the spelling its prices were recorded against -
   30 strips had offered a wattage called M. Only the option list changes; the printed value
   stays exactly as the sheet prints it.
3. PULSE 1000 RGBW PIXEL had no typology; sheet 2 puts it with the wall grazers.

What it keeps
-------------
* Everything a sheet prints: its parameters, its title line and its name bar. The name bar was
  composed from the series ("NYX DUAL SQUARE - Midi"); with the series split it would print "NYX -
  Midi", so the line each product prints is held as written (fields.name_line, read by
  npd.identity_bits) wherever the split fields would compose a different one.
* Prices. Base prices, option adders and price entries are not written. Every adder and every
  price entry is checked to still match an option the product offers; the run stops if one would
  stop matching.
* Approval. A product approved stays approved.

How it writes
-------------
Each product is written the way NPD's own save writes it (main._npd_compose over the same body the
form sends), and checked before and after: a no-edit save must change nothing, or the next person
to open a product would send it back to "awaiting approval" without having changed it.

Run it on the catalogue as InHouse wrote it. It is safe to re-run on its own output - every step
reads the product as it is and writes only what differs - and it removes the fields only it
writes (form, aperture_shape, lamp_count, supply_voltage, series_variant, and the retired
colour_system and profile_size) wherever the product no longer states them.

    python scripts/backfills/structure_inhouse_catalogue.py              # dry run: prints, writes nothing
    python scripts/backfills/structure_inhouse_catalogue.py --apply      # writes
    ... --leave "PULSE 1000 RGBW PIXEL"     # leave a product exactly as it is (repeatable)

Run rebuild_option_lists.py first on a database where products were saved before their templates
last changed: a product a no-edit save would change is written here without being recomposed.

On the live database (28 Sep 2026) PULSE 1000 RGBW PIXEL was left: it had been moved to the
typology "Not Named Yet" and was awaiting approval, and structuring it would have added a printed
ADJUSTABILITY line to its sheet.
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
import copy
import json
import re
from collections import Counter

from app.database import SessionLocal
from app import models, npd
from app import main as appmain

MAKE = "InHouse"
DISCIPLINE = "architectural"

# The 23 series, as sheet 5 lists them. Matched on the start of the series text, longest first.
SERIES = sorted(["CURVE", "EMBER", "JADE", "MERIDIAN", "NEBEL", "NYX", "OZONE", "SOLARA",
                 "VESPER", "FLARE", "RAYON", "FILO", "VENA", "LUMEA", "MIST", "SERA", "TIDE",
                 "AURA", "RIVA", "DAWN24", "GLANCE", "PULSE", "RASO"], key=len, reverse=True)
SIZES = ["Nano", "Micro", "Mini", "Small", "Midi", "Maxi", "Large", "Grand"]
FLEXIBLE = {"Flexible Strip Light", "Flexible Neon / LED Band", "Flexible LED Lens Grazer"}
TRACK_FORMS = ("LINEAR", "SPOT", "SUSPENDED")
# The MOUNTING parameter, where it gives one mounting, in the sub-typology's words.
MOUNTING = [
    (re.compile(r"^SURFACE(\s*\(.*\))?$"), "Surface"),
    (re.compile(r"^GROUND RECESSED$"), "Inground"),
    (re.compile(r"^WALL RECESSED$"), "Wall Recessed"),
]
# Sheet 2: "PULSE 1000 RGBW PIXEL has no typology at all. It belongs with the wall grazers next
# to PULSE 1000 RGBW."
TYPOLOGY_FIXES = {"PULSE 1000 RGBW PIXEL": "Wall Grazer / Wall Washer"}

# The fields only this script writes. Anything else it touches only where the product left it
# blank; these it owns, so it also takes them away where the product no longer states them.
OWN_FIELDS = ("form", "aperture_shape", "lamp_count", "supply_voltage", "series_variant")
# Written by the first version from what was usual rather than what was stated - removed.
RETIRED_FIELDS = ("colour_system", "profile_size")


# ---------------------------------------------------------------- what the product states

def split_series(raw):
    """(series, the rest of the text) - "RAYONLinearDiffused" -> ("RAYON", "LinearDiffused")."""
    text = (raw or "").strip()
    for name in SERIES:
        if text.upper().startswith(name):
            return name, text[len(name):].strip(" -–")
    return text, ""


def written_series(sheet):
    """The series text as InHouse wrote it. On the catalogue as imported that is the series
    column; once split, the name line held for the sheet keeps it (upper-cased, as the sheet
    prints it), followed by " - <size>" when the product had a size of its own."""
    held = str((sheet.fields or {}).get(npd.NAME_LINE_KEY) or "").strip()
    if held:
        return held.split(" - ")[0]
    return sheet.series or ""


def _options_of(value):
    return [o.upper() for o in npd.split_options(value) if o.upper() not in ("NA", "N/A")]


def derive(sheet):
    """{column or field: value} for what this product states. Blank means it states nothing."""
    f = sheet.fields or {}
    typology = sheet.typology or ""
    name = sheet.product_name or ""
    written = written_series(sheet)
    series, rest = split_series(written)
    out = {"series": series}
    if not typology and name in TYPOLOGY_FIXES:
        typology = out["typology"] = TYPOLOGY_FIXES[name]
    upper = " ".join(re.findall(r"[A-Z0-9]+", rest.upper()))

    # size: the family column, else a size word written in the series
    family = (sheet.family or "").strip()
    if not family:
        family = next((s for s in SIZES if re.search(r"\b%s\b" % s.upper(), upper)), "")
    out["family"] = family

    out["form"] = ""
    if typology == "Track Light":
        out["form"] = next((w.title() for w in TRACK_FORMS if w in upper.replace(" ", "")), "")

    # Square and Dual are written in the series or the name. Round and single are not written
    # anywhere, so they are not recorded: a code says nothing where the product says nothing.
    whole = (written + " " + name).upper()
    out["aperture_shape"] = "Square" if "SQUARE" in whole else ""
    out["lamp_count"] = "Dual" if "DUAL" in whole else ""

    variant = (sheet.variant or "").strip()
    adj = str(f.get("adjustability") or "").strip()
    if not adj and variant.upper() in ("FIXED", "ADJUSTABLE", "FIXED / ADJUSTABLE"):
        adj = variant.upper()
    out["adjustability"] = adj
    trim = str(f.get("trim_details") or "").strip()
    if not trim and variant.upper() in ("WITH TRIM", "TRIMLESS"):
        trim = variant.upper()
    out["trim_details"] = trim

    v = variant.upper().replace(" ", "")
    out["supply_voltage"] = "48V DC" if "48V" in v else "24V DC" if "24V" in v else ""

    sub = str(f.get("sub_typology") or "").strip()
    if not sub:
        mounting = npd.split_options(f.get("mounting"))
        if len(mounting) == 1:
            sub = next((to for rx, to in MOUNTING if rx.match(mounting[0].strip().upper())), "")
    out["sub_typology"] = sub

    out["series_variant"] = designation(typology, series, upper, out, f)
    return out


def designation(typology, series, rest, derived, fields):
    """What is left of the series text once every fact with a field of its own is taken out:
    InHouse's own designation (PIN, DIFFUSED, RGBW PIXEL, S 10 12), upper-cased, as written."""
    if not rest:
        return ""
    if typology in FLEXIBLE or series == "TIDE":
        return rest
    explained = []
    for key in ("family", "form", "aperture_shape", "lamp_count", "sub_typology"):
        explained += re.findall(r"[A-Z0-9]+", str(derived.get(key) or "").upper())
    explained += _options_of(derived.get("adjustability"))
    length = re.findall(r"\d+", str(fields.get("luminaire_length") or ""))
    explained += length[:1]
    # Longest first, and as run-together text as well as words: "LINEARDIFFUSED" is two words
    # InHouse ran together, so LINEAR is taken out of it, leaving DIFFUSED.
    text = rest.replace(" ", "  ")
    for word in sorted(set(w for w in explained if len(w) >= 3 or w.isdigit()), key=len, reverse=True):
        text = text.replace(word, " ")
    return " ".join(re.findall(r"[A-Z0-9]+", text))


# ---------------------------------------------------------------- option lists

def _naive_split(value):
    """How a value was split before brackets and W/M were understood."""
    return [p.strip() for p in str(value or "").split("/") if p.strip()]


def repaired_options(key, printed, stored):
    """The option list this printed value should carry, or None to leave the stored one alone.

    Only a list that is the old, broken split of this very value is replaced - a list entered
    or parsed any other way is somebody's, and is kept."""
    if not isinstance(stored, list) or not printed:
        return None
    if key == "wattage":
        per_metre = re.findall(r"(\d+(?:\.\d+)?)\s*W\s*/\s*M\b", printed, flags=re.I)
        if per_metre:
            want = list(dict.fromkeys("%sW/M" % n for n in per_metre))
            return want if want != stored else None
        # The PDF reader picked wattages out as "<number>W" tokens, which turned FLARE's range
        # "1-3W" into an option of 3W - a wattage the sheet does not state.
        tokens = re.findall(r"\d+(?:\.\d+)?\s*W", printed, flags=re.I)
        if stored == tokens and npd.split_options(printed) != stored:
            return npd.split_options(printed)
    if stored == _naive_split(printed):
        want = npd.split_options(printed)
        return want if want != stored else None
    return None


# ---------------------------------------------------------------- the save path


def simulate_save(db, sheet):
    """What NPD's own save would store for this product if it were opened and saved unchanged:
    the same detail the form loads, the same body npdSave() sends, the same composition."""
    detail = json.loads(appmain.npd_product_detail(sheet.id, db).body)
    templates = (db.query(models.ParameterTemplate)
                 .filter(models.ParameterTemplate.typology == sheet.typology,
                         models.ParameterTemplate.discipline == DISCIPLINE)
                 .order_by(models.ParameterTemplate.sort_order, models.ParameterTemplate.id).all())
    affects = dict(detail["affects"])
    for t in templates:                          # npdRenderForm's default for the rest
        affects.setdefault(t.field_key, bool(t.is_configurable))
    body = {
        "product_name": detail["product_name"], "typology": detail["typology"],
        "series": detail["series"], "family": detail["family"], "variant": detail["variant"],
        "model_no": detail["model_no"], "make": detail["make"],
        "sub_typology": detail["sub_typology"], "description_line": detail["description_line"],
        "values": detail["values"], "affects": affects, "base_price": detail["base_price"],
        "price_adders": detail["price_adders"],
        "accessories": [{"name": a["name"], "price": a["price"]} for a in detail["accessories"]],
    }
    params, fields, options, base, adders, _acc, title = appmain._npd_compose(
        db, body, DISCIPLINE, templates)
    merged = dict(sheet.options or {})
    merged.update(options)
    return {"params": params, "fields": fields, "options": merged,
            "category": title or sheet.category, "base_price": base, "price_adders": adders}


def spec_state(sheet_like):
    """Everything a save compares, except the structured accessory list. A save writes that
    list for a product that only has the datasheet's raw accessories text - settling it is
    tidy_accessory_names.py's job, not this script's, so it is left out of the comparison here
    and left exactly as stored when writing."""
    g = (lambda k: sheet_like[k]) if isinstance(sheet_like, dict) else (lambda k: getattr(sheet_like, k))
    options = {k: v for k, v in (g("options") or {}).items() if k != "accessories"}
    return json.dumps([g("params") or {}, g("fields") or {}, options,
                       g("category") or ""], sort_keys=True, default=str)


def printed(sheet):
    """Everything the generated specsheet prints from the product's data: the parameter table,
    the title line, the name bar - joined as npd.generate_specsheet_pdf draws it, since a held line
    and the same line composed from its parts print alike - and the model number beside it."""
    bar = " - ".join(npd.identity_bits(sheet)) or (sheet.product_name or "")
    return json.dumps([sheet.params or {}, sheet.category or "", bar,
                       (sheet.fields or {}).get("model_no") or "", sheet.product_name or ""],
                      sort_keys=True, default=str)


def price_keys(db, sheet):
    """Every (field, option) a price is recorded against - adders and price entries."""
    keys = set()
    for field, per in (sheet.price_adders or {}).items():
        for option in (per or {}):
            keys.add((field, option))
    for e in db.query(models.PriceEntry).filter(models.PriceEntry.specsheet_id == sheet.id):
        for field, option in (e.config or {}).items():
            if field not in ("accessory",):
                keys.add((field, option))
    return keys


def reachable(sheet, field, option):
    offered = [o for o in (sheet.options or {}).get(field) or []]
    offered += npd.split_options((sheet.fields or {}).get(field))
    return any(str(o).strip().lower() == str(option).strip().lower() for o in offered)


# ---------------------------------------------------------------- the run


def run(db, out=print, leave=()):
    """Apply the changes to the session and return the problems found - [] means safe to commit.
    Nothing is committed here; main() decides. Products named in `leave` are not read or written."""
    sheets = (db.query(models.Specsheet)
              .filter(models.Specsheet.make == MAKE, models.Specsheet.origin == "npd",
                      models.Specsheet.discipline == DISCIPLINE)
              .order_by(models.Specsheet.id).all())
    left = [s for s in sheets if s.product_name in leave]
    sheets = [s for s in sheets if s.product_name not in leave]
    out("%d %s products" % (len(sheets), MAKE))
    for s in left:
        out("   left as it is, as asked: #%d %s (%s)" % (s.id, s.product_name, s.typology or "no typology"))
    missing = set(leave) - {s.product_name for s in left}
    if missing:
        out("   asked to leave, but no such product: %s" % ", ".join(sorted(missing)))
    out("")

    # Before anything: a no-edit save must already change nothing, or recomposing below would
    # carry unrelated changes in with these.
    unstable = [s for s in sheets if spec_state(simulate_save(db, s)) != spec_state(s)]
    if unstable:
        out("A no-edit save would already change %d product(s) - their fields are written "
            "directly rather than through the save path:" % len(unstable))
        for s in unstable:
            out("   #%d %s" % (s.id, s.product_name))
    unstable_ids = {s.id for s in unstable}

    money_before = {s.id: (s.base_price, json.dumps(s.price_adders or {}, sort_keys=True))
                    for s in sheets}
    prices_before = {s.id: {k: reachable(s, *k) for k in price_keys(db, s)} for s in sheets}
    printed_before = {s.id: printed(s) for s in sheets}
    moves, repaired, held, touched = Counter(), [], [], []

    for s in sheets:
        before_bits = npd.identity_bits(s)
        fields = copy.deepcopy(s.fields or {})
        options = copy.deepcopy(s.options or {})
        changed = []

        for key, stored in list(options.items()):
            want = repaired_options(key, fields.get(key), stored)
            if want is not None:
                repaired.append((s.id, s.product_name, key, stored, want))
                options[key] = want
                changed.append(key + " options")

        d = derive(type("S", (), {**{c: getattr(s, c) for c in ("typology", "product_name", "series",
                                                                 "family", "variant")},
                                  "fields": fields})())
        for col in ("typology", "series", "family"):
            if col in d and d[col] != (getattr(s, col) or ""):
                moves[(col, getattr(s, col) or "", d[col])] += 1
                setattr(s, col, d[col])
                changed.append(col)
        for key in ("adjustability", "trim_details", "sub_typology") + OWN_FIELDS:
            old = str(fields.get(key) or "").strip()
            new = d.get(key) or ""
            if key in OWN_FIELDS and not new and old:
                moves[(key, old, "(removed)")] += 1
                fields.pop(key, None)
                changed.append(key)
            elif new and new != old and (key in OWN_FIELDS or not old):
                moves[(key, old, new)] += 1
                fields[key] = new
                changed.append(key)
        for key in RETIRED_FIELDS:
            if key in fields:
                moves[(key, str(fields[key]), "(removed)")] += 1
                fields.pop(key, None)
                options.pop(key, None)
                changed.append(key)

        # The name the sheet prints, held as it reads today wherever the split fields would
        # compose a different one.
        probe = type("S", (), {"series": s.series, "family": s.family, "params": s.params,
                               "fields": {k: v for k, v in fields.items()
                                          if k != npd.NAME_LINE_KEY}})()
        if not fields.get(npd.NAME_LINE_KEY) and npd.identity_bits(probe) != before_bits:
            fields[npd.NAME_LINE_KEY] = " - ".join(before_bits) or (s.product_name or "")
            held.append((s.id, s.product_name, fields[npd.NAME_LINE_KEY],
                         " - ".join(npd.identity_bits(probe))))
            changed.append("name_line")

        if not changed:
            continue
        s.fields, s.options = fields, options
        if s.id not in unstable_ids:
            saved = simulate_save(db, s)
            kept = dict(saved["options"])
            kept.pop("accessories", None)
            if "accessories" in options:
                kept["accessories"] = options["accessories"]
            s.params, s.fields, s.options = saved["params"], saved["fields"], kept
            s.category = saved["category"]
        touched.append((s, changed))

    out("Written into fields of their own, or removed where the product does not state them:")
    for (key, old, new), n in sorted(moves.items(), key=lambda x: (x[0][0], str(x[0][2]))):
        out("   %-16s %-30r -> %-30r x%d" % (key, old, new, n))
    out("\nOption lists rebuilt to match the value they were split from (%d):" % len(repaired))
    for sid, name, key, was, now in repaired:
        out("   #%-4d %-26s %-10s %r -> %r" % (sid, name[:26], key, was, now))
    out("\nName line held as printed (%d)" % len(held))

    # ---- checks
    problems = []
    for s in sheets:
        if printed(s) != printed_before[s.id]:
            problems.append("#%d %s: its specsheet would print differently" % (s.id, s.product_name))
    for s, _changed in touched:
        if (s.base_price, json.dumps(s.price_adders or {}, sort_keys=True)) != money_before[s.id]:
            problems.append("#%d %s: base price or adders changed" % (s.id, s.product_name))
    gained = []
    for s in sheets:
        for key, was in prices_before[s.id].items():
            now = reachable(s, *key)
            if was and not now:
                problems.append("#%d %s: price for %s=%s no longer matches an option"
                                % (s.id, s.product_name, key[0], key[1]))
            if now and not was:
                gained.append("#%d %s: %s=%s" % (s.id, s.product_name, key[0], key[1]))
    after_unstable = [s for s in sheets
                      if s.id not in unstable_ids and spec_state(simulate_save(db, s)) != spec_state(s)]
    for s in after_unstable:
        problems.append("#%d %s: a no-edit save would still change it" % (s.id, s.product_name))

    out("\nPrices now matching an option they could not match before (%d):" % len(gained))
    for g in gained:
        out("   " + g)
    out("\n%d of %d products change. Printed specsheets: %s. Base prices and adders: unchanged. "
        "No-edit save after: %s." % (len(touched), len(sheets),
                                     "unchanged" if not any("print" in p for p in problems) else "CHANGED",
                                     "stable" if not after_unstable else "UNSTABLE"))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--dry-run", action="store_true", help="print only (the default)")
    ap.add_argument("--leave", action="append", default=[], metavar="PRODUCT NAME",
                    help="leave this product exactly as it is (repeatable; matched by name)")
    args = ap.parse_args()

    db = SessionLocal()
    problems = run(db, leave=tuple(args.leave))
    if problems:
        print("\nSTOPPED - nothing written:")
        for p in problems:
            print("   " + p)
        db.rollback()
        return 1
    if args.apply:
        db.commit()
        print("Written.")
    else:
        db.rollback()
        print("Dry run - nothing written. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
