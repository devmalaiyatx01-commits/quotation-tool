"""The data fixes from 'Product Code Fixes - Design Team.xlsx' (28 Sep 2026), one at a time.

    python scripts/backfills/fix_product_code_issues.py all --dry-run   # review every fix, writes nothing
    python scripts/backfills/fix_product_code_issues.py F1 --dry-run    # one fix, writes nothing
    python scripts/backfills/fix_product_code_issues.py F1              # applies it

Each was approved one by one and applied to the local database on 28 Sep 2026:
  F1     SERA CHAIN SV: drop the 'IP Test' IP option (only IP67 is real)
  F2     CURVE -Small Trimless: Trim WITH TRIM -> TRIMLESS (InHouse's page 8 has the same slip)
  F3     Wattage 2X5W gets the abbreviation 2X5W (the rule read the LED count and coded 2W)
  F4-F9  six products entered twice: the older upload is held, the 6 Aug copy stays
         (an older upload a BOQ line uses is left approved and reported, not held)
  F13    END CAPS shares END CAP's code EC (product text stays as InHouse prints it)
  F14    ALUMINUM PROFILE shares ALUMINIUM PROFILE's code AP (text unchanged)
  F15    ROUND HOLE APARTURE -> APERTURE on the NYX products, keeping the code RHA
  F16    Series variant '1' gets the abbreviation SV1
  F17    MIST 0808 3D: CCT '4000K)' -> '4000K'
  F18    MERIDIAN -M: Variant 'With Trimless' -> 'Trimless' (printed title unchanged)
  T4     remove NIL's abbreviation N - needs app/product_codes.py to treat NIL as not applicable
F10-F12 are not here: they are BOQ lines, and choosing a reflector finish or a CCT for a line is a
person's decision, not a script's.

Products are matched by name and by the value being corrected, never by id, so the same fix runs
correctly on the local and the live database (ids differ). Each fix first checks the value is still
the wrong one and stops on anything unexpected, so running it twice changes nothing the second time.
Direct edits skip NPD's re-approval step: an approved product whose specification changes here
stays approved (decided for #322 on 28 Sep).
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
from pathlib import Path

from app.database import SessionLocal
from app import models, product_codes as pc


def code_status(db, s):
    """How the product's codes stand: every configuration, how many have no code."""
    r = pc.code_for_sheet(db, s)
    scheme = pc.scheme_for(db, s.discipline or models.DEFAULT_DISCIPLINE, s.make, s.typology)
    sep = scheme.separator if scheme.separator is not None else "-"
    skus = list(pc.configurations(r, sep, pc.clean_code(scheme.prefix or "")))
    return {"skus": len(skus), "uncoded": sum(1 for code, _c in skus if not code),
            "missing": r["missing"], "pattern": r["partial"]}


def f1(db, dry):
    """SERA CHAIN SV: the IP rating option list offers 'IP Test', which is not a rating."""
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == "InHouse",
                                             models.Specsheet.product_name == "SERA CHAIN SV").all()
    assert len(rows) == 1, "expected one SERA CHAIN SV, found %d" % len(rows)
    s = rows[0]
    before = list((s.options or {}).get("ip_rating") or [])
    if "IP Test" not in before:
        print("  #%d %s: IP options are %s - nothing to fix." % (s.id, s.product_name, before))
        return
    # Everything else on the product already states IP67; the option list is the only odd one out.
    assert (s.fields or {}).get("ip_rating") == "IP67", (s.fields or {}).get("ip_rating")
    after = [o for o in before if o != "IP Test"]
    print("  #%d %s" % (s.id, s.product_name))
    print("    IP rating options: %s  ->  %s" % (" / ".join(before), " / ".join(after)))
    print("    codes before:", code_status(db, s))
    s.options = {**(s.options or {}), "ip_rating": after}   # reassigned whole, so the JSON change is saved
    print("    codes after: ", code_status(db, s))
    if dry:
        db.rollback()
        print("  DRY RUN - nothing written.")
    else:
        db.commit()
        print("  Written.")


def finish(db, dry):
    if dry:
        db.rollback()
        print("  DRY RUN - nothing written.")
    else:
        db.commit()
        print("  Written.")


def f2(db, dry):
    """CURVE -Small Trimless: Trim says WITH TRIM, copied from a slip on InHouse's own page 8, so it
    gets the With Trim version's codes. The two places NPD keeps the value - the field the form and
    the code read, and the line the specsheet prints - both change, as a save in NPD would."""
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == "InHouse",
                                             models.Specsheet.product_name == "CURVE -Small").all()
    trimless = [s for s in rows if (s.variant or "").strip() == "Trimless"]
    assert len(trimless) == 1, "expected one CURVE -Small Trimless, found %d" % len(trimless)
    s = trimless[0]
    field = (s.fields or {}).get("trim_details")
    printed = ((s.params or {}).get("MECHANICAL PARAMETERS") or {}).get("TRIM DETAILS")
    if field == "TRIMLESS" and printed == "TRIMLESS":
        print("  #%d %s (%s): Trim already TRIMLESS - nothing to fix." % (s.id, s.product_name, s.variant))
        return
    assert field == "WITH TRIM" and printed == "WITH TRIM", (field, printed)
    print("  #%d %s (%s)" % (s.id, s.product_name, s.variant))
    print("    Trim field:                           %s  ->  TRIMLESS" % field)
    print("    Printed line MECHANICAL PARAMETERS / TRIM DETAILS: %s  ->  TRIMLESS" % printed)
    print("    identity code before:", pc.code_for_sheet(db, s)["identity_code"])
    s.fields = {**s.fields, "trim_details": "TRIMLESS"}
    params = {g: dict(v) for g, v in s.params.items()}
    params["MECHANICAL PARAMETERS"]["TRIM DETAILS"] = "TRIMLESS"
    s.params = params
    print("    identity code after: ", pc.code_for_sheet(db, s)["identity_code"])
    others = [o for o in rows if o.id != s.id]
    for o in others:
        print("    the With Trim version #%d keeps:" % o.id, pc.code_for_sheet(db, o)["identity_code"])
    finish(db, dry)


def set_abbreviation(db, field, value, code):
    """What NPD → Product codes does when an abbreviation is typed (npd_code_set_abbreviation): the
    same checks, one row per value, and any other value already using the code reported."""
    import re
    code = pc.clean_code(code)
    assert re.fullmatch(r"[A-Z0-9]+", code) and pc.explicit(code), code
    rows = db.query(models.FieldCode).filter(models.FieldCode.field == field).all()
    mine = [r for r in rows if pc.norm(r.option_value) == pc.norm(value)]
    clashes = [r.option_value for r in rows if pc.clean_code(r.code) == code and pc.norm(r.option_value) != pc.norm(value)]
    before = mine[0].code if mine else ""
    if mine:
        mine[0].code = code
    else:
        db.add(models.FieldCode(field=field, option_value=value, code=code))
    db.flush()
    return before, clashes


def show_codes(db, sheets, label):
    for s in sheets:
        r = pc.code_for_sheet(db, pc_fresh(db, s))
        print("    %s #%d %s: %s" % (label, s.id, s.product_name, r["partial"]))


def pc_fresh(db, s):
    db.expire(s)
    return s


def f3(db, dry):
    """SOLARA Midi and SOLARA Wall: wattage '2X5W' is coded 2W, because the rule reads the first
    number - here the count of LEDs. A typed abbreviation beats the rule, as 1-3W → 1TO3W does."""
    sheets = [s for s in db.query(models.Specsheet).filter(models.Specsheet.make == "InHouse").all()
              if pc.norm((s.fields or {}).get("wattage")) == pc.norm("2X5W")]
    print("  products with wattage 2X5W:", ", ".join("#%d %s" % (s.id, s.product_name) for s in sheets))
    ctx = pc.load_context(db, "architectural")
    if ctx.typed("wattage", "2X5W") == "2X5W":
        print("  2X5W already has the abbreviation 2X5W - nothing to fix.")
        return
    show_codes(db, sheets, "before")
    before, clashes = set_abbreviation(db, "wattage", "2X5W", "2X5W")
    print("  Wattage abbreviation for '2X5W': %r  ->  '2X5W'%s" % (
        before or "(none - coded by rule as 2W)", "" if not clashes else "  (also used by %s)" % clashes))
    show_codes(db, sheets, "after ")
    finish(db, dry)


# F4-F9: each product entered twice - an early upload and the 6 Aug re-upload of InHouse's newer
# PDF. The newer copy stays; the older is held (invisible outside NPD, nothing deleted, reversible).
# (old copy's name, old copy's source PDF, kept copy's name)
DUPLICATES = [
    ("NYX Dual Square -Midi", "Nyx Series.pdf", "NYX Dual Square -Midi"),
    ("RAYONSpot", "RAYON 48V DC Track Spot__2.pdf", "RAYON Spot - – LL 103003"),
    ("Vena S 15 -LL 106007", "InHouse LED Strip Vena S 15 - 60LEDS per M - Diffuser.pdf", "Vena S 15 - LL 106007"),
    ("RAYONLinear", "RAYON 48V DC Track Linear__2.pdf", "RAYON Linear - LL103005"),
    ("DAWN24 -Mini", "DAWN24 Mini.pdf", "DAWN24 -Mini"),
    ("RAYONLinearDiffused", "RAYON 48V DC Track Linear Diffused.pdf", "RAYONLinear Diffused - LL103007"),
]


def f4(db, dry):
    """An old copy a BOQ line uses is left approved and reported: holding it would hide the product a
    line was priced against, and moving the line to the kept copy is a person's decision (as F10-F12
    are). Once somebody has moved it, running F4-F9 again holds it. On the live database on 28 Sep
    2026 that was Vena S 15 (BOQ 57) and DAWN24 -Mini (BOQ 35)."""
    for old_name, old_pdf, kept_name in DUPLICATES:
        named = db.query(models.Specsheet).filter(models.Specsheet.make == "InHouse",
                                                  models.Specsheet.product_name.in_([old_name, kept_name])).all()
        old = [s for s in named if s.product_name == old_name and Path(s.source_pdf_path or "").name == old_pdf]
        # the same variant too: NYX Dual Square -Midi is also the name of its With Trim version
        variant = (old[0].variant or "").strip() if len(old) == 1 else None
        kept = [s for s in named if s.product_name == kept_name and Path(s.source_pdf_path or "").name != old_pdf
                and (s.variant or "").strip() == variant]
        assert len(old) == 1 and len(kept) == 1, (old_name, [s.id for s in old], [s.id for s in kept])
        o, k = old[0], kept[0]
        lines = db.query(models.BOQItem).filter(models.BOQItem.specsheet_id == o.id).all()
        if lines and o.approval_status != "held":
            boqs = sorted({"BOQ %d %s" % (i.room.floor.boq.id, i.room.floor.boq.project_name) for i in lines})
            print("  left approved: #%d %s (%s) is on %d BOQ line(s) - %s. Move them to #%d, then run "
                  "F4-F9 again." % (o.id, o.product_name, old_pdf, len(lines), "; ".join(boqs), k.id))
            continue
        if o.approval_status == "held":
            print("  #%d %s: already held (keeping #%d)." % (o.id, o.product_name, k.id))
            continue
        assert k.approval_status == "approved", "the copy being kept (#%d) is not approved" % k.id
        print("  hold #%-3d %-22s (%s)   keep #%-3d %s   [%s -> held]" % (
            o.id, o.product_name, old_pdf, k.id, k.product_name, o.approval_status))
        o.approval_status = "held"
    finish(db, dry)


def share_code(db, dry, field, value, code, partner):
    """Give `value` the code `partner` already has, so one item written two ways has one code.
    Product text is left as the manufacturer prints it."""
    ctx = pc.load_context(db, "architectural")
    have, partner_code = ctx.typed(field, value), ctx.typed(field, partner)
    assert partner_code == code, (partner, partner_code)
    if have == code:
        print("  %s '%s' already coded %s - nothing to fix." % (field, value, code))
        return
    before, clashes = set_abbreviation(db, field, value, code)
    print("  %s '%s': %s  ->  %s   (now shared with: %s)" % (field, value, before or "(none)", code, ", ".join(clashes)))
    finish(db, dry)


def f13(db, dry):
    share_code(db, dry, "accessories", "END CAPS", "EC", "END CAP")
    ctx = pc.load_context(db, "architectural")
    print("  Cable entry END CAPS (a different field) stays:", ctx.typed("cable_lead_option", "END CAPS"))


def f14(db, dry):
    share_code(db, dry, "accessories", "ALUMINUM PROFILE", "AP", "ALUMINIUM PROFILE")
    print("  RIGID ALUMINUM PROFILE (a different item) stays:", pc.load_context(db, "architectural").typed("accessories", "RIGID ALUMINUM PROFILE"))


def replace_token(text, old, new):
    """`old` replaced where it stands as one item of a ' / ' list or as the whole value."""
    parts = [p.strip() for p in str(text).split(" / ")]
    return " / ".join(new if p == old else p for p in parts) if old in parts else text


def f15(db, dry):
    """ROUND HOLE APARTURE → APERTURE on every product that lists it, in one transaction: the new
    spelling gets RHA first, the text changes, then the old spelling's code goes - so the accessory
    is never without a code. raw_text keeps InHouse's own spelling, as the record of the source."""
    old, new = "ROUND HOLE APARTURE", "ROUND HOLE APERTURE"
    sheets = [s for s in db.query(models.Specsheet).all() if old in (s.fields or {}).get("accessories", "")]
    ctx = pc.load_context(db, "architectural")
    if not sheets and ctx.typed("accessories", new) == "RHA":
        print("  No product lists %s any more - nothing to fix." % old)
        return
    assert ctx.typed("accessories", old) == "RHA"
    set_abbreviation(db, "accessories", new, "RHA")
    print("  abbreviation: %s -> RHA added" % new)
    for s in sheets:
        before = s.fields["accessories"]
        s.fields = {**s.fields, "accessories": replace_token(before, old, new)}
        params, changed = {g: dict(v) for g, v in s.params.items()}, []
        for g, grp in params.items():
            for k, v in grp.items():
                nv = replace_token(v, old, new)
                if nv != v:
                    grp[k] = nv
                    changed.append("%s / %s" % (g, k))
        s.params = params
        assert old not in str(s.fields) + str(s.params), s.id
        print("  #%-3d %-24s field + printed line (%s)" % (s.id, s.product_name, ", ".join(changed)))
    for r in db.query(models.FieldCode).filter(models.FieldCode.field == "accessories").all():
        if pc.norm(r.option_value) == pc.norm(old):
            db.delete(r)
            print("  abbreviation for the old spelling %s (RHA) removed" % old)
    db.flush()
    ctx = pc.load_context(db, "architectural")
    print("  check: %s -> %r, %s -> %r" % (new, ctx.code_for("accessories", new)[0], old, ctx.typed("accessories", old)))
    finish(db, dry)


def f16(db, dry):
    ctx = pc.load_context(db, "architectural")
    if ctx.typed("series_variant", "1") == "SV1":
        print("  Series variant 1 already coded SV1 - nothing to fix.")
        return
    before, clashes = set_abbreviation(db, "series_variant", "1", "SV1")
    assert not clashes, clashes
    print("  Series variant '1': %s  ->  SV1   (used by: %s)" % (before or "(none - digits alone are refused)",
          ", ".join("#%d %s" % (s.id, s.product_name) for s in db.query(models.Specsheet).all()
                    if (s.fields or {}).get("series_variant") == "1")))
    finish(db, dry)


def f17(db, dry):
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == "InHouse",
                                             models.Specsheet.product_name == "MIST 0808 3D").all()
    assert len(rows) == 1
    s = rows[0]
    old, new = "4000K)", "4000K"
    opts = list((s.options or {}).get("cct") or [])
    if old not in opts:
        print("  #%d %s: CCT options are %s - nothing to fix." % (s.id, s.product_name, opts))
        return
    printed_before = s.fields["cct"]
    s.options = {**s.options, "cct": [new if o == old else o for o in opts]}
    s.fields = {**s.fields, "cct": replace_token(printed_before, old, new)}
    params = {g: dict(v) for g, v in s.params.items()}
    for g, grp in params.items():
        for k, v in grp.items():
            if v == printed_before:
                grp[k] = s.fields["cct"]
                print("  printed line %s / %s: %s  ->  %s" % (g, k, v, grp[k]))
    s.params = params
    print("  #%d %s CCT options: %s  ->  %s" % (s.id, s.product_name, " / ".join(opts), " / ".join(s.options["cct"])))
    assert old not in str(s.fields) + str(s.params) + str(s.options)
    finish(db, dry)


def f18(db, dry):
    """MERIDIAN -M Trimless: Variant is the tool's own label (the Configurator's variant filter, the
    Library badge), copied from InHouse's title as 'With Trimless'. The printed title is left as is."""
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == "InHouse",
                                             models.Specsheet.product_name == "MERIDIAN -M").all()
    target = [s for s in rows if (s.variant or "").strip() in ("With Trimless", "Trimless")]
    assert len(target) == 1, [(s.id, s.variant) for s in rows]
    s = target[0]
    if s.variant.strip() == "Trimless":
        print("  #%d %s: variant already Trimless - nothing to fix." % (s.id, s.product_name))
        return
    assert (s.fields or {}).get("trim_details") == "TRIMLESS"
    print("  #%d %s variant: %r  ->  'Trimless'   (printed title stays: %r)" % (s.id, s.product_name, s.variant, s.category))
    s.variant = "Trimless"
    finish(db, dry)


def t4(db, dry):
    """NIL is now 'not applicable' in app/product_codes.py, so its abbreviation N can go - as NPD →
    Product codes does when a code is cleared (the row is removed)."""
    assert "NIL" in pc.NOT_APPLICABLE, "app/product_codes.py does not treat NIL as not applicable yet"
    rows = [r for r in db.query(models.FieldCode).filter(models.FieldCode.field == "accessories").all()
            if pc.norm(r.option_value) == "NIL"]
    if not rows:
        print("  No abbreviation for NIL - nothing to fix.")
        return
    for r in rows:
        print("  accessories 'NIL' abbreviation %r removed (row %d)" % (r.code, r.id))
        db.delete(r)
    finish(db, dry)


def d1(db, dry):
    """Decision D1: a honeycomb louvre is the honeycomb - one code, H. #142's text stays as InHouse prints it."""
    share_code(db, dry, "accessories", "HONEYCOMB LOUVRE", "H", "HONEYCOMB")


def d2(db, dry):
    """Decision D2: the code says which tunable-white range - TW + the first two digits of each end,
    as the kelvin rule writes a single CCT (2700K is 27K). Both ranges were plain TW."""
    ranges = {"TW (2700K-6500K)": "TW2765", "TW (2700K-6000K)": "TW2760"}
    ctx = pc.load_context(db, "architectural")
    todo = {v: c for v, c in ranges.items() if ctx.typed("cct", v) != c}
    if not todo:
        print("  Both ranges already coded %s - nothing to fix." % " / ".join(ranges.values()))
        return
    for value, code in todo.items():
        users = [s for s in db.query(models.Specsheet).all()
                 if any(pc.norm(o) == pc.norm(value)
                        for o in (pc.offered(s, "cct") or pc.split_options((s.fields or {}).get("cct"))))]
        before, clashes = set_abbreviation(db, "cct", value, code)
        assert not clashes, clashes
        print("  CCT %-18s %s  ->  %s   (%d products)" % (value, before or "(none)", code, len(users)))
    finish(db, dry)


# Decision D6: every abbreviation means one thing, whichever segment it is in. Each code shared by two
# meanings keeps the one that disturbs fewest codes (product types always keep theirs - they start every
# code); the other gets a new code, unused anywhere, in the style of its segment (reflector finishes
# and families are two letters, named beams are words). Done before studio codes were ever deployed,
# so no quotation or order carried any of the old ones.
RECODE = [
    ("accessories", "BASE CONNECTOR", "BC", "BCN"),        # BC stays black chrome (reflector)
    ("accessories", "DIFFUSER", "D", "DIF"),               # D stays InHouse's series variant D
    ("accessories", "MOUNTING BASE", "MB", "MBS"),         # MB stays matte black (reflector)
    ("accessories", "MOUNTING CLIPS", "MC", "MCL"),        # MC stays Micro (family)
    ("accessories", "SNOOT", "S", "SNT"),                  # S stays MERIDIAN's family S
    ("accessories", "SOFTENING LENS", "SL", "SFL"),        # SL stays Spotlight (product type)
    ("bending_direction", "TOP BENDING", "TV", "TB"),      # TV stays 0-10V (control)
    ("bending_direction", "SIDE BENDING", "SV", "SB"),     # SV stays silver (reflector)
    ("family", "Grand", "GR", "GN"),                       # GR stays ground recessed (mounting)
    ("form", "Suspended", "SP", "SUS"),                    # SP stays Step Light (product type)
    ("form", "Spot", "ST", "SPT"),                         # ST stays Flexible Strip Light (product type)
    ("reflector_finish", "SAND SILVER", "SS", "SD"),       # SS stays stainless steel (body finish)
    ("beam_angle", "Wall Wash", "WW", "WASH"),             # WW stays Wall Grazer / Wall Washer (product type)
]


def d6(db, dry):
    ctx = pc.load_context(db, "architectural")
    everywhere = {}
    for field, m in ctx.codes.items():
        for n, code in m.items():
            everywhere.setdefault(code, set()).add((field, n))
    done = 0
    for field, value, old, new in RECODE:
        now = ctx.typed(field, value)
        if now == new:
            continue
        assert now == old, "%s '%s' is coded %r, expected %r - stopping" % (field, value, now, old)
        taken = [x for x in everywhere.get(new, ()) if x != (field, pc.norm(value))]
        assert not taken, "%s is already used by %s" % (new, taken)
        set_abbreviation(db, field, value, new)
        print("  %-17s %-16s %-3s ->  %s" % (field, value, old, new))
        done += 1
    beam = db.query(models.CodeAttribute).filter_by(key="beam_angle").first()
    if beam and "WW, MED" in (beam.note or ""):
        beam.note = beam.note.replace("WW, MED", "WASH, MED")
        print("  beam angle note: named beams now read WASH, MED, WIDE, ASYM")
        done += 1
    if not done:
        print("  All 13 already recoded - nothing to fix.")
        return
    finish(db, dry)


FIXES = {"F1": f1, "F2": f2, "F3": f3, "F4-F9": f4, "F13": f13, "F14": f14, "F15": f15, "F16": f16, "F17": f17,
         "F18": f18, "T4": t4, "D1": d1, "D2": d2, "D6": d6}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("fix", choices=list(FIXES) + ["all"], help="one fix, or all of them in order")
    ap.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    a = ap.parse_args()
    db = SessionLocal()
    try:
        for name in (list(FIXES) if a.fix == "all" else [a.fix]):
            print("%s%s" % (name, " (dry run)" if a.dry_run else ""))
            FIXES[name](db, a.dry_run)
    finally:
        db.close()
