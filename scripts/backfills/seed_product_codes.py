"""Sets up the studio's product code for InHouse: the attributes a code is made of, the
abbreviation for every value, and the structure of the code for each product type - built from
what the catalogue states, so that every SKU the catalogue offers has a code of its own.

After this runs, everything it wrote is edited in NPD -> Product codes. This script is the
starting point, not the source of truth: it never overwrites an attribute, a structure or an
abbreviation somebody has changed in the app. It does bring its own earlier rows up to date -
a row it wrote and nobody has touched since - which is how a database seeded by an earlier
version of this script arrives at this one.

What a structure holds, per typology
------------------------------------
* The segments that name the product - but only those the typology's products state. A
  spotlight has no trim and no reflector, and no InHouse downlight states a supply voltage, so
  those segments are not in those structures. A segment only some products state (Square on a
  NYX, the designation PIN on a CURVE) is left out of the code of a product that does not state
  it, rather than filled with what is usual.
* Every choice a product offers when it is quoted - its option lists, which are what the
  Configurator asks. Each option is a SKU of its own, so each must change the code: that is why
  a wall grazer's code carries its fixed-or-adjustable bracket, a lens grazer's its beam, an
  underwater fitting's its mounting and cable entry.
* Nothing that only repeats the typology: every flexible strip is flexible.
The check that decides whether a structure is right is product_codes.sku_audit(): every
configuration of every product, and no two with the same code.

What it writes
--------------
* code_attributes  - one row per thing a code can say, with the rule that codes a value nobody
                     has abbreviated and the unit written around a number: 7W, 30K, CRI95, 24D,
                     IP20, L1000, DIP67. Colour system and profile size are removed: no product
                     states either, and the first version of this script had inferred them.
* field_codes      - the abbreviation for every value. RGBW, which the first version coded RG
                     like RGB, becomes RW; abbreviations only the inferred values used (Round,
                     Single, 220-240V AC, "FIXED / ADJUSTABLE") are removed.
* code_schemes     - one per InHouse typology, plus a InHouse default for any typology added later.

    python scripts/backfills/seed_product_codes.py              # dry run: prints, writes nothing
    python scripts/backfills/seed_product_codes.py --apply      # writes

Run structure_inhouse_catalogue.py first: the structures are built for the fields it writes.
Safe to re-run: anything already as intended is left as it is.
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

from app.database import SessionLocal, engine, Base
from app import models, product_codes

MAKE = "InHouse"
DISCIPLINE = "architectural"
SEEDED_BY = "seed_product_codes.py"

# key, label, part, rule, multi, note, prefix, suffix - identity first, then configuration.
ATTRIBUTES = [
    ("model_no", "Model number", "identity", "text", False,
     "InHouse's own number. Not in the code: InHouse gives one number to more than one product "
     "(LL103003 is a RAYON Linear and a RAYON Spot).", "", ""),
    ("typology", "Product type", "identity", "list", False,
     "What the thing is. Decides which segments the rest of the code carries.", "", ""),
    ("sub_typology", "Mounting", "identity", "list", False,
     "How it is installed, as the product states it: recessed, semi-recessed, surface, wall, "
     "inground. A different product, not a setting.", "", ""),
    ("series", "Series", "identity", "list", False,
     "The product line. Three letters, assigned once per series and never re-used.", "", ""),
    ("form", "Form", "identity", "list", False,
     "Track lights: spot, linear or suspended, as the series names it.", "", ""),
    ("luminaire_length", "Length", "identity", "number", False,
     "Linear fittings: the LUMINAIRE LENGTH in millimetres, after L. 1000 mm is L1000.", "L", ""),
    ("series_variant", "Series variant", "identity", "text", False,
     "InHouse's own designation, as its catalogue writes it after the series: VENA S 10, LUMEA "
     "1010BH TV, CURVE Pin, PULSE RGBW. Kept whole - the sheets do not say what its digits "
     "measure.", "", ""),
    ("adjustability", "Adjustability", "identity", "list", False,
     "Fixed or adjustable. Where a product offers both, it is chosen when quoting.", "", ""),
    ("family", "Family", "identity", "text", False,
     "Nano, Micro, Mini, Small, Midi, Maxi, Large, Grand - or as the product writes it "
     "(MERIDIAN's M and S).", "", ""),
    ("aperture_shape", "Aperture shape", "identity", "list", False,
     "Square, where the product's name says so. Nothing is recorded where it does not.", "", ""),
    ("lamp_count", "Heads", "identity", "list", False,
     "Dual, where the product's name says so: a twin-head fitting is 2H.", "", ""),
    ("trim_details", "Trim", "identity", "list", False,
     "With trim, trimless or pin hole. A recessed fitting's; a surface fitting has none.", "", ""),
    ("optics_type", "Optical system", "identity", "list", False,
     "Lens, reflector, lens + reflector, diffused.", "", ""),
    ("supply_voltage", "Supply voltage", "identity", "list", False,
     "24V DC or 48V DC, where the product's variant says so.", "", ""),
    ("bending_direction", "Bending", "identity", "list", False,
     "Flexible types: top view, side view or 3D.", "", ""),
    ("mounting", "Mounting option", "config", "list", False,
     "The MOUNTING parameter, where a product offers more than one: floor or wall recessed, "
     "surface or recessed.", "", ""),
    ("wattage", "Wattage", "config", "number", False,
     "The watts, then W. A decimal point becomes P: 14.4W is 14P4W.", "", "W"),
    ("cct", "CCT", "config", "kelvin", False,
     "Hundreds of kelvin, then K: 3000K is 30K. TW for a tunable range, RG and RW for RGB and "
     "RGBW.", "", "K"),
    ("cri", "CRI", "identity", "number", False,
     "The bin, after CRI: CRI80, CRI90, CRI95, CRI97. Each product states one.", "CRI", ""),
    ("beam_angle", "Beam angle", "config", "numbers", False,
     "Degrees, then D: 24° is 24D, an elliptical 18°X20° is 18X20D. Named beams by "
     "abbreviation: WW, MED, WIDE, ASYM.", "", "D"),
    ("finish", "Body finish", "config", "list", False, "Two letters.", "", ""),
    ("reflector_finish", "Reflector finish", "config", "list", False,
     "Two letters. Independent of the body finish - never folded into it.", "", ""),
    ("ip_rating", "IP", "config", "number", False, "IP and its two digits: IP20, IP67.", "IP", ""),
    ("driver_ip_rating", "Driver IP", "config", "number", False,
     "The driver's IP, where the product offers a choice of driver: DIP20, DIP67.", "DIP", ""),
    ("control", "Control", "config", "list", False,
     "Two letters. It decides the driver that is quoted with the fitting.", "", ""),
    ("cable_lead_option", "Cable entry", "config", "list", False,
     "Where the cable leaves a flexible profile, where the product offers a choice: front, side "
     "or bottom entry.", "", ""),
    ("accessories", "Accessories", "config", "list", True,
     "Zero or many, each its own code, after everything else.", "", ""),
]

# Attributes an earlier version wrote that no product states. Removed once no structure names
# them - a structure somebody saved in the app that still does keeps them.
RETIRED = ("colour_system", "profile_size")

# What earlier versions of this script wrote where this one says something else. A row still
# holding one of these was never changed in the app, so it is brought up to date; anything else
# is somebody's decision and is left alone.
EARLIER = {
    "model_no": {"note": ["InHouse's own number. Not decided yet - it slots in ahead of "
                          "everything else when it is."]},
    "sub_typology": {"note": ["Recessed, semi-recessed, surface, inground. A different product, "
                              "not a setting.",
                              "How it is installed: recessed, semi-recessed, surface, wall, "
                              "inground, flexible. A different product, not a setting."]},
    "form": {"note": ["Track lights: spot, linear or suspended."]},
    "luminaire_length": {"note": ["Linear fittings: the nominal length in millimetres, after L. "
                                  "1000 mm is L1000."]},
    "series_variant": {"note": ["InHouse's own sub-designations (LUMEA's BH, VENA's S 10). "
                                "Replaced by a real axis once their meaning is known."]},
    "adjustability": {"note": ["Fixed, adjustable, or fixed-or-adjustable."]},
    "family": {"label": ["Size"], "note": ["Nano, Micro, Mini, Small, Midi, Maxi, Large, Grand."]},
    "aperture_shape": {"note": ["Round or square.",
                                "Round or square - the shape of the opening in the ceiling."]},
    "lamp_count": {"label": ["Lamp count"],
                   "note": ["Single or dual.",
                            "Light heads in one fitting: a single downlight is 1H, a twin is 2H."]},
    "trim_details": {"note": ["With trim, trimless or pin hole - what separates two products "
                              "sharing a name most often."]},
    "supply_voltage": {"note": ["24V DC, 48V DC or mains - a different product with a different driver.",
                                "24V DC, 48V DC or mains AC - a different product with a "
                                "different driver."]},
    "wattage": {"note": ["The number, no unit. A decimal point becomes P: 14.4W is 14P4."]},
    "cct": {"note": ["First two digits of the Kelvin value: 3000K is 30. TW for a tunable range.",
                     "Hundreds of kelvin, then K: 3000K is 30K. TW for a tunable range."]},
    "cri": {"note": ["The bin: 80, 90, 95, 97.",
                     "The bin, after CRI: CRI80, CRI90, CRI95, CRI97."]},
    "beam_angle": {"note": ["Degrees, no symbol. WW for wall wash; an elliptical beam is written 18X20."]},
    "ip_rating": {"note": ["Two digits, no 'IP'."]},
}
# Rules an earlier version wrote. A family is coded as written (the team's decision, made in
# the panel), so MERIDIAN's M and S code as M and S rather than waiting for abbreviations.
EARLIER_RULES = {"family": ["list"]}

# field -> {value as the catalogue spells it: code}.
CODES = {
    "typology": {"Downlight": "DL", "Spotlight": "SL", "Track Light": "TL",
                 "Flexible Strip Light": "ST", "Flexible Neon / LED Band": "NB",
                 "Flexible LED Lens Grazer": "GZ", "Wall Grazer / Wall Washer": "WW",
                 "Uplight": "UP", "Underwater Fixture": "UW", "Step Light": "SP"},
    "sub_typology": {"Recessed": "RC", "Semi Recessed": "SR", "Surface": "SU", "Wall": "WM",
                     "Surface/Wall": "SW", "Wall Recessed": "WR", "Inground": "IG",
                     "Ground Recessed": "GR", "Underwater Recessed": "UR",
                     "Underwater Surface": "US", "Flexible": "FL",
                     "Flexible Homogeneous": "FH", "Flexible Anti Glare": "FA"},
    "series": {"CURVE": "CRV", "EMBER": "EMB", "JADE": "JAD", "MERIDIAN": "MER", "NEBEL": "NEB",
               "NYX": "NYX", "OZONE": "OZN", "SOLARA": "SOL", "VESPER": "VSP", "FLARE": "FLR",
               "RAYON": "RAY", "FILO": "FIL", "VENA": "VEN", "LUMEA": "LUM", "MIST": "MST",
               "SERA": "SER", "TIDE": "TID", "AURA": "AUR", "RIVA": "RIV", "DAWN24": "DWN",
               "GLANCE": "GLN", "PULSE": "PLS", "RASO": "RAS"},
    "form": {"Spot": "ST", "Linear": "LN", "Suspended": "SP"},
    "adjustability": {"FIXED": "FX", "ADJUSTABLE": "AD"},
    "family": {"Nano": "NN", "Micro": "MC", "Mini": "MN", "Small": "SM", "Midi": "MD",
               "Maxi": "MX", "Large": "LG", "Grand": "GR"},
    "aperture_shape": {"Square": "SQ"},
    "lamp_count": {"Dual": "2H"},
    "trim_details": {"WITH TRIM": "WT", "TRIMLESS": "TM", "PIN HOLE": "PN"},
    "optics_type": {"LENS + REFLECTOR": "LR", "LENS": "LE", "REFLECTOR": "RF", "DIFFUSED": "DF",
                    "LENS + DOME MILKY / ALUMINUM FLAT DIFFUSER": "LD"},
    "supply_voltage": {"24V DC": "24V", "48V DC": "48V"},
    "bending_direction": {"TOP BENDING": "TV", "SIDE BENDING": "SV", "3D BENDING": "3D"},
    # The same words as the Mounting abbreviations where they mean the same thing. A surface
    # grazer's "( FIXED / ADJUSTABLE )" is its bracket, coded by adjustability, so it is SU.
    "mounting": {"SURFACE": "SU", "RECESSED": "RC", "FLOOR": "FLO", "FLOOR RECESSED": "FR",
                 "WALL RECESSED (UNDERWATER)": "WRU", "SURFACE (UNDERWATER)": "SUU",
                 "GROUND RECESSED": "GR", "WALL RECESSED": "WR",
                 "SURFACE ( FIXED / ADJUSTABLE )": "SU",
                 "FLOOR RECESSED (WALK & DRIVE OVER APPLICATION)": "FRD"},
    # A range is written with TO, so 1-3W cannot be read as 1W or as 13W.
    "wattage": {"1-3W": "1TO3W", "1-6W": "1TO6W"},
    "cct": {"TW (2700K-6500K)": "TW", "TW (2700K-6000K)": "TW", "RGB": "RG", "RGBW": "RW"},
    "beam_angle": {"Wall Wash": "WW", "MEDIUM": "MED", "WIDE": "WIDE", "ASYMMETRIC": "ASYM"},
    "finish": {"WHITE": "WH", "BLACK": "BK", "GREY": "GY", "DARKGREY": "DG",
               "TEXTURED GREY": "TG", "GRAPHITE GRAY": "GP", "STAINLESS STEEL": "SS",
               "STAINLESS STEEL TRIM": "SS", "ALUMINIUM": "AL", "Custom / RAL": "CS"},
    "reflector_finish": {"MATTE WHITE": "MW", "MATTE BLACK": "MB", "WHITE": "WH", "BLACK": "BK",
                         "CHROME": "CR", "DARK CHROME": "DC", "BLACK CHROME": "BC", "GOLD": "GD",
                         "SAND SILVER": "SS", "SILVER": "SV"},
    "control": {"ON-OFF": "OO", "0-10": "TV", "0-10V": "TV", "DALI": "DA", "PWM": "PW",
                "DMX": "DX", "DMX 512": "DX", "Phase Cut": "PC"},
    "cable_lead_option": {"FRONT ENTRY": "FE", "SIDE ENTRY": "SE", "BOTTOM ENTRY": "BE",
                          "END CAPS": "EC", "CLOSED END CAP": "CEC"},
    "accessories": {
        "HONEYCOMB": "H", "SNOOT": "S", "IP68 Connector": "IC", "Water Stopper": "WS",
        "HONEYCOMB LOUVRE": "HL", "NIL": "N", "SOFTENING LENS": "SL", "SPREAD LENS": "SL2",
        "‘U’-SHAPE PROFILE": "USP", "MOUNTING CLIPS": "MC", "OVAL HOLE": "OH",
        "PIN HOLE": "PH", "ROUND HOLE APARTURE": "RHA", "END CAP": "EC", "EXTENSION ARM": "EA",
        "GROUND SPIKE": "GS", "SURFACE MOUNT BASE": "SMB", "ADJUSTABLE CLIPS": "AC",
        "FLEXIBLE PROFILE": "FP", "TRIANGULAR PROFILE": "TP", "END CAPS": "EC2",
        "RECESSING BLOCK": "RB", "RIGID PROFILE": "RP", "DIFFUSER": "D", "HALFCUT SHIELD": "HS",
        "LINEAR MICRO LOUVER": "LML", "MESH LOUVER": "ML", "ADJUSTABLE MOUNTING BRACKET": "AMB",
        "FIXED BRACKET": "FB", "LENS COVER": "LC", "OPAL DIFFUSER": "OD",
        "ALUMINIUM PROFILE": "AP", "RIGID ALUMINUM PROFILE": "RAP", "SIGNAL ADAPTOR": "SA",
        "SINGLE SIDE SNOOT": "SSS", "T CONNECTOR": "TC",
        "ALUMINIUM PROFILE –18.1X18.9MM": "AP1", "ALUMINUM PROFILE": "AP2",
        "BASE CONNECTOR": "BC", "DOME MILKY OR ALUMINUM FLAT DIFFUSER": "DMO",
        "LOOPING CABLE": "LC2", "MOUNTING BASE": "MB", "RIGID PC PROFILE": "RPP",
        "SILICON COVER": "SC", "SILICON END CAPS": "SEC"},
}

# Abbreviations this version replaces on purpose: (field, value) -> (the code there, the new
# one). WHT predates the proposal; the digits are what the first version wrote before every
# code had to say what it is; RG for RGBW was a slip that gave RGB and RGBW one code.
REPLACES = {
    ("finish", "WHITE"): ("WHT", "WH"),
    ("lamp_count", "DUAL"): ("2", "2H"),
    ("supply_voltage", "24VDC"): ("24", "24V"),
    ("supply_voltage", "48VDC"): ("48", "48V"),
    ("cct", "RGBW"): ("RG", "RW"),
}

# Abbreviations for values only the first version's inferences used, as it wrote them. Removed
# where they still read exactly that; changed in the app since, they are left.
REMOVED = [
    ("colour_system", "Static white", "SW"), ("colour_system", "Tunable white", "TW"),
    ("colour_system", "RGB", "RG"), ("colour_system", "RGBW", "RW"),
    ("colour_system", "RGBW pixel", "PX"),
    ("aperture_shape", "Round", "RD"), ("lamp_count", "Single", "1H"),
    ("adjustability", "FIXED / ADJUSTABLE", "FD"), ("supply_voltage", "220-240V AC", "AC"),
]

# A configuration segment holds what the buyer chooses from what the product offers. A product
# that offers none of it - a FLARE spotlight has a lens and no reflector, so no reflector finish
# - has nothing to choose, and its code is complete without that segment rather than never.
CONFIG = {a[0] for a in ATTRIBUTES if a[2] == "config"}


def seg(keys, optional=""):
    optional = set(optional.split())
    return [{"key": k, "include": True, "optional": k in CONFIG or k in optional}
            for k in keys.split()]


SCHEMES = {
    # A family is written on every downlight but MERIDIAN -D, which is told apart by its
    # designation; Square, Dual and trim only where a product states them.
    "Downlight": seg("typology sub_typology series series_variant adjustability family "
                     "aperture_shape lamp_count trim_details optics_type wattage cct cri "
                     "beam_angle finish reflector_finish ip_rating control accessories",
                     optional="series_variant family aperture_shape lamp_count trim_details"),
    # FLARE states no trim, no reflector, no aperture shape, no voltage and no control.
    "Spotlight": seg("typology sub_typology series adjustability family optics_type wattage cct "
                     "cri beam_angle finish ip_rating accessories"),
    # Each RAYON wattage is a product of its own; a suspended pendant states no adjustability.
    "Track Light": seg("typology series form series_variant adjustability optics_type "
                       "supply_voltage wattage cct cri beam_angle finish ip_rating control "
                       "accessories", optional="series_variant adjustability"),
    # The strips' designation (S 10, S 10 12) tells them apart, and their beam: VENA S 10 comes
    # bare at 120° and with a 30° lens cover, one product each. Every strip is flexible, which
    # the typology already says.
    "Flexible Strip Light": seg("typology series series_variant wattage cct cri beam_angle "
                                "ip_rating control accessories"),
    "Flexible Neon / LED Band": seg("typology sub_typology series series_variant wattage cct "
                                    "cri beam_angle ip_rating control accessories"),
    # SERA offers its beam, and one SERA its mounting and its driver, when quoted.
    "Flexible LED Lens Grazer": seg("typology series series_variant mounting wattage cct cri "
                                    "beam_angle ip_rating driver_ip_rating control accessories"),
    # GLANCE and PULSE offer the bracket fixed or adjustable, and GLANCE its driver.
    "Wall Grazer / Wall Washer": seg("typology sub_typology series series_variant "
                                     "luminaire_length adjustability wattage cct cri beam_angle "
                                     "finish ip_rating driver_ip_rating control accessories",
                                     optional="series_variant"),
    "Uplight": seg("typology sub_typology series adjustability family optics_type "
                   "supply_voltage wattage cct cri beam_angle finish ip_rating accessories"),
    "Step Light": seg("typology sub_typology series adjustability optics_type wattage cct cri "
                      "beam_angle finish ip_rating accessories"),
    # Two kinds of fitting: AURA and RIVA spots, with a size, a bracket, an optic and a voltage;
    # TIDE, a flexible profile with a designation instead. Each segment one of them lacks is
    # left out of its code. TIDE and AURA offer their mounting, and TIDE its cable entry.
    "Underwater Fixture": seg("typology sub_typology series series_variant family adjustability "
                              "optics_type supply_voltage mounting wattage cct cri beam_angle "
                              "finish ip_rating control cable_lead_option accessories",
                              optional="sub_typology series_variant family adjustability "
                                       "optics_type supply_voltage"),
}
# Any typology added later uses the default: every segment in order, blanks skipped, so it gets
# a code from its first product and can be given a stricter structure of its own.
DEFAULT = [{"key": a[0], "include": a[0] != "model_no",
            "optional": a[0] not in ("typology", "series")} for a in ATTRIBUTES]


def shape(segments):
    return "-".join(s["key"] + ("?" if s["optional"] else "") for s in segments if s["include"])


def run(db, out=print):
    """Apply to the session; main() decides whether to commit."""
    Base.metadata.create_all(bind=engine)          # the two tables, if the app has not run yet
    existing = {a.key: a for a in db.query(models.CodeAttribute)
                .filter(models.CodeAttribute.discipline == DISCIPLINE)}
    added_attrs, upgraded = 0, []
    for n, (key, label, part, rule, multi, note, prefix, suffix) in enumerate(ATTRIBUTES):
        row = existing.get(key)
        if row is None:
            db.add(models.CodeAttribute(discipline=DISCIPLINE, key=key, label=label, part=part,
                                        rule=rule, multi=multi, note=note, prefix=prefix,
                                        suffix=suffix, sort_order=n))
            added_attrs += 1
            continue
        before = dict(label=row.label, rule=row.rule, note=row.note or "",
                      prefix=row.prefix or "", suffix=row.suffix or "")
        earlier = EARLIER.get(key, {})
        # A unit is only ever added where there is none: a blank one is one nobody has chosen.
        if not (row.prefix or row.suffix):
            row.prefix, row.suffix = prefix, suffix
        for field, target in (("label", label), ("note", note)):
            if (getattr(row, field) or "") in earlier.get(field, []):
                setattr(row, field, target)
        if row.rule in EARLIER_RULES.get(key, []):
            row.rule = rule
        after = dict(label=row.label, rule=row.rule, note=row.note or "",
                     prefix=row.prefix or "", suffix=row.suffix or "")
        if after != before:
            upgraded.append((key, {k: (before[k], after[k]) for k in after if before[k] != after[k]}))
    out("Attributes: %d added, %d brought up to date" % (added_attrs, len(upgraded)))
    for key, changes in upgraded:
        out("   %-18s %s" % (key, "; ".join(
            "%s %r -> %r" % (f, a if f != "note" else a[:28] + "...", b if f != "note" else b[:28] + "...")
            for f, (a, b) in changes.items())))

    db.flush()
    attrs = {a.key: a for a in db.query(models.CodeAttribute)
             .filter(models.CodeAttribute.discipline == DISCIPLINE)}
    rows = db.query(models.FieldCode).all()
    by = {}
    for r in rows:
        by.setdefault((r.field, product_codes.norm(r.option_value)), r)
    added, kept, replaced, removed = 0, [], [], []
    for field, values in CODES.items():
        for value, code in values.items():
            n = product_codes.norm(value)
            row = by.get((field, n))
            if row is None:
                # Two spellings of one value ("0-10" and "0-10V" are not; "WALL RECESSED
                # (UNDERWATER)" and "WALLRECESSED(UNDERWATER)" are) need only one row.
                row = by[(field, n)] = models.FieldCode(field=field, option_value=value, code=code)
                db.add(row)
                added += 1
            elif product_codes.clean_code(row.code) != code:
                was = REPLACES.get((field, n))
                if was and product_codes.clean_code(row.code) == was[0]:
                    replaced.append((field, row.option_value, row.code, code))
                    row.code = code
                else:
                    kept.append((field, row.option_value, row.code, code))
    gone = set()
    for field, value, code in REMOVED:
        row = by.get((field, product_codes.norm(value)))
        if row is not None and product_codes.clean_code(row.code) == code:
            removed.append((field, row.option_value, row.code))
            gone.add(row.id)
            db.delete(row)
    # Any other code typed as digits alone gets its attribute's unit - "30" for 3000K becomes
    # 30K - so it still says the same thing, and now says what it is.
    for r in rows:
        a = attrs.get(r.field)
        code = product_codes.clean_code(r.code)
        if r.id in gone or not code or product_codes.explicit(code) \
                or a is None or not (a.prefix or a.suffix):
            continue
        new = (a.prefix or "") + code + (a.suffix or "")
        replaced.append((r.field, r.option_value, r.code, new))
        r.code = new
    out("Abbreviations: %d added, %d replaced, %d removed" % (added, len(replaced), len(removed)))
    for f, v, old, new in replaced:
        out("   replaced %s %r: %s -> %s" % (f, v, old, new))
    for f, v, old in removed:
        out("   removed  %s %r (%s) - no product states it" % (f, v, old))
    for f, v, old, new in kept:
        out("   kept %s %r as %s (this says %s) - changed in the app since" % (f, v, old, new))

    schemes = {(s.typology or ""): s for s in db.query(models.CodeScheme)
               .filter(models.CodeScheme.discipline == DISCIPLINE, models.CodeScheme.make == MAKE)}
    made, refreshed, left = 0, [], []
    for typology, segments in [("", DEFAULT)] + list(SCHEMES.items()):
        row = schemes.get(typology)
        name = typology or "(InHouse default)"
        if row is None:
            db.add(models.CodeScheme(discipline=DISCIPLINE, make=MAKE, typology=typology,
                                     prefix="", separator="-", segments=segments,
                                     updated_by=SEEDED_BY))
            made += 1
            out("   added     %-28s %s" % (name, shape(segments)))
        elif row.segments != segments:
            # Only a structure nobody has saved in the app since this script wrote it.
            if row.updated_by == SEEDED_BY:
                refreshed.append((name, shape(row.segments), shape(segments)))
                row.segments = segments
            else:
                left.append((name, row.updated_by))
    out("Code structures: %d added, %d brought up to date, %d left as saved in the app"
        % (made, len(refreshed), len(left)))
    for name, was, now in refreshed:
        out("   %-28s %s\n   %-28s -> %s" % (name, was, "", now))
    for name, by_whom in left:
        out("   left %s alone - saved in the app by %s" % (name, by_whom))

    db.flush()
    in_use = {x.get("key") for s in db.query(models.CodeScheme).all() for x in (s.segments or [])}
    for key in RETIRED:
        row = attrs.get(key)
        if row is not None and key not in in_use:
            out("   removed the attribute %s - no product states it" % row.label)
            db.delete(row)
        elif row is not None:
            out("   kept the attribute %s - a structure saved in the app still uses it" % row.label)
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--dry-run", action="store_true", help="print only (the default)")
    args = ap.parse_args()
    db = SessionLocal()
    run(db)
    if args.apply:
        db.commit()
        print("Written.")
    else:
        db.rollback()
        print("Dry run - nothing written. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
