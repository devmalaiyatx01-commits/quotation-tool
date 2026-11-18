"""DuraGreen in NPD: how its products are read, and how their order codes are built.

DuraGreen is its own brand in NPD, beside InHouse and never mixed with it. A InHouse product
is authored here and its specsheet is generated from what was typed; a DuraGreen product is
DuraGreen's, and its truth is DuraGreen's own spec sheet and catalogue. So the two differ in
the direction of truth, and they keep separate data: DuraGreen's order codes live in
vendor_order_codes / vendor_code_parts / vendor_code_rules (models.py says why the studio's
code tables cannot hold them), and nothing here reads or writes FieldCode, CodeAttribute or
CodeScheme.

What is shared is only machinery that is the same for any product: a DuraGreen product is a
Specsheet, so the Library, Configurator and BOQ take it as they take any other, and it is
priced by the same base price + adder sum (npd.price_for) once somebody enters prices.

THE ORDER CODE
DuraGreen's 2026 General Catalogue says how a code is built. Its ACME range page (p.33) prints
the code table every range follows, and each product page prints one worked example:

    AFL 50 08W - C - TL - 30 N 24 - W B S
    type size power  baffle  trim  CCT driver beam  bezel baffle option

A spec sheet prints only a few whole codes - usually 3000K, non-dim, one per wattage x beam -
so a BOQ line asking for 2700K DALI with a black bezel finds no printed row. This module builds
that code the way DuraGreen does, and says for every piece where it came from:

  head   type ... beam. Copied from the product's own printed code (so its version - trimless,
         snoot, /S shallow - is exactly what DuraGreen printed), with the chosen wattage, CCT,
         driver and beam put in. The catalogue's examples are not used for the head: a few of
         them drop a piece their own code table keeps (p.210 prints AFL7810W-SS-30N36, its table
         AFL7810W-SS-TL-30N10).
  tail   the finish, from the product's catalogue example: bezel + baffle colour (+ option), TB
         for a trimless bezel, or one structure colour.

WHOSE RULES A PRODUCT FOLLOWS (scheme_of)
The two code tables are not DuraGreen's rule for every product. ACME's table (p.33) is the
rule for the ACME downlights - the catalogue's ACME section - and MOOD's (p.222) for the MOOD
section. A step light, a shelf light, a pendant, a 48V track spot or an outdoor uplight is
none of those, and its code is its own: WFL352.5W-30N-5-W (Sparkle step light) has a pattern
number where an ACME code has a beam. So each product's code follows, in this order:

  its own spec sheet   the codes printed in its code table, cut into pieces where they can be
                       (wattage, CCT, driver, beam each found in the code), plus its sheet's
                       decoded legend;
  its own page         its page of the 2026 catalogue: the decoded example and the codes the
                       page prints;
  its range's table    ACME's or MOOD's - only for a product in that range's section.

A piece none of these print may still be offered, with its source named - but always as
unconfirmed, never written plain: "ACME's table writes Dali as D" is not DuraGreen saying so
for a step light.

Each piece's letters are looked up in this order, and the code is only as sure as its least
sure piece:

  printed      the whole code is a row of the product's own code table, as printed.
  composed     every piece is printed for this product - in its own codes or its own page.
  catalogue    a piece comes from its own range's code table (ACME p.33, MOOD p.222), or is
               its power written the way its own codes write power (08W).
  pattern      a CCT or beam this product's codes do not print, written the way they write the
               ones they do: ALP6003W-AG-30N120 prints its 3000K as 30, so its 2700K is 27. The
               product's own sheet is the evidence for the form; no other range's table is used.
  general      a piece comes from elsewhere in its own range: the range's colour table used for
               a structure colour, or the same words on another page of the same range.
  unconfirmed  a piece is printed nowhere for this product or its range: it comes from another
               range's table or page, or follows DuraGreen's pattern where the product's own codes
               do not show it ("2700K-5000K" -> 2750). Shown, and labelled, so it is checked
               before ordering.

Anything else gets no code, and says why: a choice not made yet, or an option DuraGreen prints
no code for (Heritage Brass, Bespoke). A code that looks complete and is wrong orders the wrong
fitting; an honest gap does not. This is the rule app/exenia_codes.py keeps for Exenia.
"""
import functools
import itertools
import re
from collections import defaultdict

MAKE = "Duragreen"

# Statuses of a found code, best first. A built code takes the weakest of its pieces'.
PRINTED, COMPOSED, CATALOGUE, PATTERN, GENERAL, UNCONFIRMED = (
    "printed", "composed", "catalogue", "pattern", "general", "unconfirmed")
RANK = {PRINTED: 0, COMPOSED: 1, CATALOGUE: 2, PATTERN: 3, GENERAL: 4, UNCONFIRMED: 5}
STATUS_TEXT = {
    PRINTED: "DuraGreen's own code, as printed for this product",
    COMPOSED: "Built only from codes DuraGreen prints for this product",
    CATALOGUE: "Built with the code table DuraGreen prints for this product's range",
    PATTERN: "Built by the pattern of this product's own printed codes (its 3000K printed as 30, so 2700K is 27)",
    GENERAL: "Uses another part of what DuraGreen prints for this product's range (another of its pages, or an earlier edition of its code table)",
    UNCONFIRMED: "A piece is printed nowhere for this product or its range - confirm with DuraGreen before ordering",
}

# How a lighting engineer reads a DuraGreen product: the facts grouped by what they decide.
# (group, [(field key, label as shown)]). A field a product does not state is simply absent.
ENGINEER_GROUPS = [
    ("OPTICAL", [
        ("beam_angle", "Optics / beam"), ("cct", "CCT"), ("cri", "CRI"), ("cri_detail", "CRI detail"),
        ("ugr", "UGR"), ("binning", "Binning"), ("efficacy", "Typical source efficacy (lm/W)"),
        ("optic_type", "Optic type"),
    ]),
    ("ELECTRICAL", [
        ("wattage", "System power"), ("control", "Control / driver"), ("input_voltage", "Input"),
        ("output", "Driver output"), ("class", "Electrical class"), ("circuits", "Circuits"),
    ]),
    ("MECHANICAL", [
        ("size", "Engine / size"), ("mounting_style", "Mounting"), ("cutout_dimensions", "Cut-out"),
        ("ceiling_thickness", "Ceiling thickness"), ("material", "Material"), ("finish", "Finish"),
        ("bezel_finish", "Bezel finish"), ("baffle_finish", "Baffle finish"),
        ("length", "Length"), ("profile_dimension", "Profile dimension"), ("polarity_groove", "Polarity groove"),
        ("ip_rating", "IP rating"), ("ambient_temp", "Ambient temperature"),
    ]),
    ("GENERAL", [
        ("lifetime", "Lifetime"), ("certifications", "Certifications"),
        ("accessories", "Accessories"), ("components", "Separately ordered parts"),
        ("project_reference", "DuraGreen project"),
    ]),
]
GROUP_OF = {key: group for group, items in ENGINEER_GROUPS for key, _label in items}
LABEL_OF = {key: label for _group, items in ENGINEER_GROUPS for key, label in items}
LABEL_OF.update({"type": "Type", "style": "Baffle style", "trim": "Bezel style", "trim_bezel": "Trimless bezel",
                 "voltage": "Voltage", "mounting": "Mounting", "variant": "Version", "version": "Version", "suffix": "Suffix", "sep": "-"})

# The options a DuraGreen product is configured on when it is quoted - those with a choice.
# A track is quoted on its length and colour, a track feed on the side its polarity groove is on
# (scripts/backfills/backfill_duragreen_tracks.py says where each comes from).
CONFIG_FIELDS = ["version", "mounting", "wattage", "cct", "beam_angle", "control", "voltage", "cri", "finish",
                 "bezel_finish", "baffle_finish", "accessories", "length", "polarity_groove", "ip_rating"]

# A product typed in by hand ("Type it in", 17 Nov 2026) is described the way a InHouse product of
# its typology is: by that typology's NPD template, in its groups and order. Three of InHouse's
# parameters are DuraGreen details under another name and are stored under DuraGreen's, so the
# product reads like every other DuraGreen product (its page, Search, Ask, Configure). InHouse's
# optics type ("LENS") and C.C/C.V are not DuraGreen's optic type ("Pinhole") or driver output,
# so they stay apart.
SAME_DETAIL = {"operating_temperature": "ambient_temp", "color_consistency": "binning",
               "lumen_maintenance": "lifetime"}
OWN_OPTIONS_GROUP = "DURAGREEN OPTIONS"     # its own choices the template leaves out
MORE_DETAILS_GROUP = "MORE DETAILS"         # held details DuraGreen's own groups have no place for
_NOT_TYPED = {"sep", "suffix"}              # pieces of a code, not details of a product


def details_list(template_rows, offered, identity=()):
    """What a DuraGreen product of one typology is described by when typed in by hand.

    template_rows: [(group, label, key)] of its typology's NPD template in order (identity rows
    left out), or [] when there is none - then DuraGreen's own groups. offered: the options
    DuraGreen products of this typology are quoted on, most used first; those the list does not
    already name follow under OWN_OPTIONS_GROUP. Returns {source, groups: [{group, items:
    [{key, label}]}], others: [{key, label}]} - others being every other DuraGreen detail, for
    "+ Add a detail"."""
    groups, by_name, seen = [], {}, set()

    def put(group, key, label):
        if not key or key in seen or key in identity or key in _NOT_TYPED:
            return
        seen.add(key)
        if group not in by_name:
            by_name[group] = {"group": group, "items": []}
            groups.append(by_name[group])
        by_name[group]["items"].append({"key": key, "label": label})

    if template_rows:
        for group, label, key in template_rows:
            put(group, SAME_DETAIL.get(key, key), label)
    else:
        for group, items in ENGINEER_GROUPS:
            for key, label in items:
                put(group, key, label)
    for key in offered:
        put(OWN_OPTIONS_GROUP, key, LABEL_OF.get(key, key.replace("_", " ").capitalize()))
    others = [{"key": k, "label": label} for k, label in LABEL_OF.items()
              if k not in seen and k not in identity and k not in _NOT_TYPED]
    return {"source": "template" if template_rows else "duragreen", "groups": groups, "others": others}


def quotable_keys(extra=()):
    """DuraGreen's own options (CONFIG_FIELDS), then `extra` - the details a product's typology
    is described by, any of which may be a choice when quoting."""
    return list(CONFIG_FIELDS) + [k for k in extra if k not in CONFIG_FIELDS]


def offered_order(counts):
    """Options products hold ({key: how many}) in the order they are offered: DuraGreen's own
    order first, then the rest, most held first."""
    return [k for k in CONFIG_FIELDS if counts.get(k)] + \
        [k for k, _n in sorted(counts.items(), key=lambda kv: -kv[1]) if k not in CONFIG_FIELDS and _n]


def more_details(fields, labels=None):
    """[(key, name, value)] for held details DuraGreen's own groups have no place for - typed in
    from the typology's template - named by `labels`."""
    return [(k, (labels or {}).get(k) or k.replace("_", " ").capitalize(), str(v))
            for k, v in (fields or {}).items() if k not in LABEL_OF]


# Builder part names (build_duragreen_catalogue_db.decompose) -> the field a part encodes.
# A part with no field (type, size, variant, the undecoded prefixes, a fixed suffix) belongs to
# the product, never to a choice, and is only ever copied from the printed code.
PART_FIELD = {"wattage": "wattage", "cct": "cct", "control": "control", "beam_angle": "beam_angle"}
# A choice DuraGreen's codes do not tell apart. No code states an IP, so a code cannot be asked to:
# required like a bezel colour, choosing IP44 made a code a person typed from DuraGreen's answer "no
# answer", and the line quietly got a built code instead. A row that does state one is still held to it.
NOT_IN_THE_CODE = ("ip_rating",)
# The options a printed code can be told apart by. A row must state each one the product offers
# a choice of before it is used (see order_code).
COVERED = ("wattage", "cct", "beam_angle", "control", "voltage", "mounting", "version")
# Options that pick WHICH of a product's printed versions a code comes from - each version a
# different fitting, so one's rows are never borrowed for another:
#   voltage   an outdoor product sold for mains and for 24V DC prints both, the 24V codes ending
#             -24 (FOCUS 70: UL7007W-27N15 and UL7008W-27N15-24);
#   mounting  a sheet that labels each of its tables ("Recessed Trim", "Recessed Trimless",
#             "Surface") prints one version per table (the 68 Glass Pendant: RTPGE..., TLPGE...,
#             STPGE...). The labels are the sheet's own words.
#   version   a sheet printing several fittings nothing else tells apart - ORBIT's recessed-trim
#             RTC23 and trimless TLC23, ARTFOCUS with and without its standard base (/SB): each
#             row carries its version (backfill_duragreen_code_rules.offer_versions, 26 Oct 2026).
# Without such a choice, a sheet printing several versions gets no code until a person removes
# the others (see order_code).
SELECTORS = ("voltage", "mounting", "version")
DC_SUFFIX = "-24"


def is_dc(value) -> bool:
    """Whether a voltage option is DuraGreen's 24V DC version ("24VDC", "24V DC", "DC 24V")."""
    return bool(re.search(r"24\s*V?\s*DC|DC\s*24", str(value or ""), re.I))


# Products whose control is their driver's, supplied with them as a line of its own. The owner,
# 22 Oct 2026: "control of strips is the control of driver which we give along with it". A strip
# line still says which control (it decides the driver), but the strip's own code is the same for
# every control - so a printed strip code need not, and cannot, say which control it is for.
DRIVER_SEPARATE_TYPOLOGIES = {"Flexible Strip Light"}


def control_is_drivers(sheet, codes) -> bool:
    """Whether a product's control choice picks its driver, not its code: a strip whose own codes
    carry no control letter. A product typed as a strip whose codes do carry one (EVOLINE's 48V
    Light Stripe, M22DF12W-30N120) keeps control in its code."""
    if (getattr(sheet, "typology", "") or "").strip() not in DRIVER_SEPARATE_TYPOLOGIES:
        return False
    return not any(name == "control" for c in codes for name, _t in (c.parts or []))


DRIVER_NOTE = ("Control is the driver's: this product's code is the same for every control, and its driver "
               "goes on a line of its own")

# Every code table DuraGreen prints writes X for No Driver: ACME 2024, 2025 and 2026, MOOD, EVOLINE,
# BELLA 41, GLAMO 68, ARTFOCUS 36 and The Chosen Ones. The studio often quotes its own driver in
# place of DuraGreen's, and then the fitting is ordered without one (owner, 26 Oct 2026).
NO_DRIVER = "No Driver"


def with_no_driver(options, codes):
    """`options` with No Driver added to the control choices, for a product whose code carries a
    control letter and which already offers a control. A product whose code has no control letter
    (a strip: its control is its driver's) is left as it is - no driver would change nothing in its
    code - and so is one offering no control, where No Driver would become its only choice and
    every code an X."""
    out = dict(options or {})
    controls = list(out.get("control") or [])
    if not controls or not any(name == "control" for c in codes for name, _t in (c.parts or [])):
        return out
    if not any(canon("control", v) == canon("control", NO_DRIVER) for v in controls):
        out["control"] = controls + [NO_DRIVER]
    return out
# The choices the catalogue example adds after the beam. accessories is optional ("H Honeycomb
# (Optional)"): left out when nothing is chosen.
TAIL_CHOICES = ("bezel_finish", "baffle_finish", "finish", "accessories")

# What a piece of each choice looks like in a code DuraGreen prints. A printed piece that does
# not look like this carries something more - High Efficiency products write their CCT as H30 -
# and is never swapped for a table's code, which would silently drop the H.
SHAPE = {"wattage": r"\d+(\.\d+)?W(X\d)?", "cct": r"\d{2}|\d{4}|WD", "control": r"[A-Z0-9]",
         "beam_angle": r"\d{1,4}|WS|WW|F|L\d{2}"}


def plain(field, token) -> bool:
    shape = SHAPE.get(field)
    return not shape or bool(re.fullmatch(shape, str(token or "")))


# Where each range's code table applies: the PDF pages of the 2026 catalogue that show its products.
# ACME and MOOD print their tables in that catalogue; EVOLINE's only table is in DuraGreen's ACME
# public price list (p.127, Sep 2024) - found 26 Oct 2026 by searching every document DuraGreen
# publishes (scripts/catalogues/read_duragreen_code_matrices.py). FLOW prints none anywhere.
SOURCE_NAME = "2026 General Catalogue"
EXAMPLE_TAG = "(code example)"
RANGE_PAGES = {"ACME": [(57, 223)], "MOOD": [(246, 289)],
               # recessed lines, surface square, square pendant, and the 48V dot line / stripe / surface
               "EVOLINE": [(226, 241), (394, 395), (434, 435), (534, 541), (546, 547)],
               # the semi-recessed spotlights whose tables the price list prints (type RT)
               "ARTFOCUS 36": [(318, 319)], "BELLA 41": [(322, 323), (328, 329)],
               "GLAMO 68": [(334, 335), (338, 339)]}
TABLE_SOURCE = {"ACME": "catalogue p.33", "MOOD": "catalogue p.222", "EVOLINE": "the ACME price list p.127",
                "BELLA 41": "the ACME price list p.161", "GLAMO 68": "the ACME price list p.185",
                "ARTFOCUS 36": "the ACME price list p.207"}
# Earlier editions of a range's table, newest first. DuraGreen restructured ACME's table in 2026
# (styles moved out of Type, Phase Cut became P, a new colour list); the earlier editions - the
# 2025 catalogue (the same table 35 ACME spec sheets print), The Chosen Ones (2025) and the price
# list (2024) - still name the colours the 2026 table dropped, which ACME products are still sold in
# (Antique Bronze is AB, Polished Chrome PC). They are read only for a meaning the 2026 table does
# not list, and only where they agree; where the 2026 table has a code, it decides.
EARLIER_EDITIONS = {"ACME": ("ACME 2025", "CHOSEN ONES 2025", "ACME 2024")}
EDITION_RANGE = {scope: rng for rng, scopes in EARLIER_EDITIONS.items() for scope in scopes}
# The catalogue numbers its pages 23 behind the PDF (PDF p.56 prints "33"). RANGE_PAGES are PDF
# pages; a source text's "p.42" is the printed number (read_duragreen_general_catalogue.py).
FOLIO_OFFSET = 23
# The table a product outside both ranges is shown for reference. Nothing taken from it for such
# a product is ever more than unconfirmed (see lookup).
GENERAL_TABLE = "ACME"
# A structure colour has no table of its own; the bezel colour table is DuraGreen's colour list.
TABLE_FIELD = {"finish": "bezel_finish"}


# --- what kind of fitting a product is ----------------------------------------------------------
# A lighting designer's classes, as the studio already files DuraGreen's typed products:
# 1. SYSTEM_RULES first, on name + series + category together: what a thing is part of decides
#    before what it looks like. A pendant hung from the 48V track, a track connector, a wall-wash
#    head on the track are all quoted as Track Light; a Zigbee driver is still a Driver.
# 2. Then TYPOLOGY_RULES on the product's own name, then its series, then the website's category
#    (DuraGreen files a pendant under "Suspension", a sconce under "Wall Lights"). Most specific
#    first: a wall WASHER is no wall light, a garden spike no spotlight, a multi-spot array
#    (EVOLINE, FLOW: "6 Spots Line") a Spotlight, a recessed snoot a Downlight, an adjustable
#    surface head a Spotlight. DuraSmart's "CASAMBI CC 15W" is a driver with Casambi inside.
#    A row with no words of its own ("Circular- Pro Lights2") is left empty, not guessed.
# Only ever used to fill an EMPTY typology: a person's choice is never overwritten.
SYSTEM_RULES = [
    (r"wiring accessor", "Wiring Accessory"),
    (r"presentation kit|demo (kit|box|case)|\bdemo\b", "Demo Kit"),
    (r"\bdrivers?\b|power suppl|\b(cc|cv)\b[^|]*\d\s*w\b", "Driver"),
    (r"\btracks?\b|track light|mini-line", "Track Light"),
]
TYPOLOGY_RULES = [
    (r"durasmart|casambi|zigbee|controller|sensor|gateway|remote|dimmer switch", "Controller & Control System"),
    (r"\bstrips?\b|\bneon\b|\bcob\b", "Flexible Strip Light"),
    (r"garden spike", "Garden Spike"),
    (r"bollard", "Bollard"),
    (r"projector", "Projector"),
    (r"uplight", "Uplight"),
    (r"wall[\s-]*wash|washer|grazer", "Wall Grazer / Wall Washer"),
    (r"\bstep\b|path light|wall[\s-]*-?\s*path", "Step Light"),
    (r"pendant|suspension", "Pendant"),
    (r"table lamp", "Table Lamp"),
    (r"floor lamp", "Floor Lamp"),
    (r"wall light|wall lights|sconce|tall arm|short arm", "Wall Light"),
    (r"honeycomb|spread lens|^spread$|accessor", "Accessory"),
    (r"\d+\s*spots?\b|spotlight|in out|contour|\bspot\b|surface adjustable", "Spotlight"),
    (r"downlight|snoot|adjustable|fixed|trimless|mini-trim|square|\bline\b|linear", "Downlight"),
]


def typology_for(series, name, category=""):
    """The class a lighting designer files a DuraGreen product under, or "" when its own words
    do not say. The system it belongs to first, then its own name, its series, the website's
    category - see SYSTEM_RULES and TYPOLOGY_RULES."""
    texts = [str(t or "").lower() for t in (name, series, category)]
    texts = [t for t in texts if t and t != "none"]
    whole = " | ".join(texts)
    for pattern, typology in SYSTEM_RULES:
        if re.search(pattern, whole):
            return typology
    for t in texts:
        for pattern, typology in TYPOLOGY_RULES:
            if re.search(pattern, t):
                return typology
    return ""


# --- the four series the studio quotes most (owner, 22 Oct 2026) --------------------------------
# NPD and Add Product open these as their own shelves. A product belongs to one when its series
# or its own name says so: the track versions ("48V Track Lights" / "EVOLINE Dot Line") belong
# with their series, a demo box that only carries a series' name does not.
KEY_SERIES = ("ACME", "MOOD", "EVOLINE", "FLOW")


def key_series_of(series, name, typology=""):
    """"ACME", "MOOD", "EVOLINE" or "FLOW" when the product's series or name says so, else ""."""
    if typology == "Demo Kit":
        return ""
    for text in (series, name):
        words = str(text or "").upper()
        for key in KEY_SERIES:
            if re.search(r"\b%s\b" % key, words):
                return key
    return ""


def is_duragreen(sheet) -> bool:
    return (getattr(sheet, "make", "") or "").strip().lower() == MAKE.lower()


def regroup_params(fields, params, labels=None):
    """params rebuilt from fields in the engineer's groups; the website's own card, its notes
    and its downloads are DuraGreen's words and are kept as they are. A detail DuraGreen's groups
    have no place for (one typed in from its typology's template: a lumen package) goes under
    MORE_DETAILS_GROUP, named by `labels` ({key: name}, the template's)."""
    fields = fields or {}
    rebuilt = {g for g, _items in ENGINEER_GROUPS} | {MORE_DETAILS_GROUP}
    kept = {k: v for k, v in (params or {}).items() if k not in rebuilt}
    grouped = {}
    for group, items in ENGINEER_GROUPS:
        block = {label: fields[k] for k, label in items if str(fields.get(k) or "").strip()}
        if block:
            grouped[group] = block
    more = {name: v for _k, name, v in more_details(fields, labels) if v.strip()}
    if more:
        grouped[MORE_DETAILS_GROUP] = more
    grouped.update(kept)
    return grouped


def norm(value) -> str:
    """Two spellings of one option compare equal: case, spaces, the degree sign, and the
    different dashes are not differences ("120°" = "120", "2700-6000K" = "2700K-6000K")."""
    return _norm(str(value or ""))


@functools.lru_cache(maxsize=65536)
def _norm(text):
    text = text.upper().replace("–", "-").replace("—", "-")
    text = re.sub(r"[\s°º]", "", text)
    return re.sub(r"(\d)K-(\d)", r"\1-\2", text)


# The same option in DuraGreen's different words. The catalogue's product pages say "On/off,
# Phase Cut, 0/1-10V, Dali"; its tables say "Non Dim, Phase Cut (ACME) / Mains Dim (MOOD),
# 0-10V, DALI". Both sides are brought to one spelling before they are compared.
_SAME_WORDS = [
    (r"on\s*/\s*off|non[\s-]*dim(mable|ming)?", "nondim"),
    (r"phase[\s-]*cut|mains[\s-]*dim(mable|ming)?|triac|leading[\s-]*edge|trailing[\s-]*edge", "phasecut"),
    (r"0\s*/\s*1\s*-\s*10\s*v|1\s*-\s*10\s*v|0\s*-\s*10\s*v", "zero10v"),
    (r"wall[\s-]*wash(er)?", "wallwash"),
    (r"spread\s*lens(e)?", "spread"),
    (r"honey\s*comb", "honeycomb"),
    (r"nickle", "nickel"),
    (r"\bmatte\b", "matt"),
]
# Words a page adds around the option itself ("white bezel", "beam angle 36°", "Honeycomb
# (Optional)", "Black Painted").
_FILLER = r"\b(bezel|baffle|structure|engine|painted|colou?r|beam|angle|zoomable|optional|system|power)\b"


def canon(field, text) -> str:
    """One option in one spelling, for comparing a product's wording with the catalogue's."""
    return _canon(field, str(text or ""))


# Cached: a lookup compares the chosen value with hundreds of catalogue meanings, and NPD's list
# of every combination makes hundreds of lookups - the same few strings each time.
@functools.lru_cache(maxsize=65536)
def _canon(field, text):
    t = text.lower().replace("–", "-").replace("—", "-")
    for pattern, same in _SAME_WORDS:
        t = re.sub(pattern, same, t)
    t = re.sub(_FILLER, " ", t)
    t = re.sub(r"(\d)\s*[°º]", r"\1", t)
    if field == "cct":
        t = re.sub(r"(\d)\s*k\b", r"\1", t)
    if field == "beam_angle":
        t = re.sub(r"\b0+(\d)", r"\1", t)          # the table writes 5° as 05
    return re.sub(r"[^a-z0-9.\-/]", "", t)


def _words(text):
    """An option's own words, for "every word of White is in White Matt"."""
    return _words_of(str(text or ""))


@functools.lru_cache(maxsize=65536)
def _words_of(text):
    t = text.lower()
    for pattern, same in _SAME_WORDS:
        t = re.sub(pattern, same, t)
    return frozenset(w for w in re.split(r"[^a-z0-9.]+", re.sub(_FILLER, " ", t)) if w)


_IP = r"IP\s?\d[\dX]"
_IP_THIS_AND_THAT = re.compile(r"^\s*(" + _IP + r")\s+and\s+(" + _IP + r")\s+options?\s*(?:[(（]\s*(from the front)\s*[)）])?$", re.I)
_IP_THIS_OPTIONALLY_THAT = re.compile(r"^\s*(front\s+)?(" + _IP + r")\s*,\s*optional\s+(" + _IP + r")\s*$", re.I)


def ip_options(value):
    """The IP ratings a product can be ordered in, from what its sheet prints.

    A sheet prints a choice as a sentence: "IP20 and IP44 options", "IP44 and IP65 options（from the
    front）", "Front IP44, optional IP65". Offered whole, a person could quote neither. Only those
    wordings are split: two ratings elsewhere can be two parts of one fitting, a driver compartment and
    an optic, which is no choice at all. Anything else comes back as printed, as one value.

    "front" is kept where the sheet says it, spelled as the products rated "IP44 front" are, and not
    added where it does not: "optional IP65" does not say from which side."""
    text = str(value if value is not None else "").strip()
    if len(text) > 80:
        return [text]

    def rating(part):
        return part.replace(" ", "").upper()

    both = _IP_THIS_AND_THAT.match(text)
    if both:
        suffix = " front" if both.group(3) else ""
        return list(dict.fromkeys([rating(both.group(1)) + suffix, rating(both.group(2)) + suffix]))
    either = _IP_THIS_OPTIONALLY_THAT.match(text)
    if either:
        return list(dict.fromkeys([rating(either.group(2)) + (" front" if either.group(1) else ""), rating(either.group(3))]))
    return [text] if text else []


_CRI_VALUE = re.compile(r"(?:CRI\s*)?(\d{2,3})\s*(?:Ra)?(?:\s+with\s+(\d{2,3})\s*Ra\s+option)?", re.I)


_NOT_A_CONTROL = re.compile(r"\d+(?:\.\d+)?|\d+\s*Step\s+MacAdams?", re.I)
_CONTROL_RUN_ON = re.compile(r"^(Casambi)\s+(?:Mini-Trim|GLL-\d+)$", re.I)


def spec_values(field, value):
    """The values one printed entry holds, each written the one way, so a person picking a value in
    Configure finds every product offering it. Anything not recognised comes back as printed.

    CRI: "98Ra" and "CRI 98" are the same, and "92Ra with 97Ra option" is two. Beam: "Zoomable 20°-50°"
    is the plain "20°-50°", which is what its order codes state; the optic type says it zooms. "120°
    Trimless" is a beam with the fitting's trim caught in it. Control: a sheet's layout can run the next
    column into it. "110" and "2 Step MacAdams" are no controls, and read as one made "110" the default on
    55 Adjustable, whose line then had no code. "Casambi Mini-Trim" is Casambi. IP: see ip_options."""
    if field == "ip_rating":
        return ip_options(value)
    text = str(value if value is not None else "").strip()
    if field == "cri":
        m = _CRI_VALUE.fullmatch(text) if len(text) <= 80 else None
        if m:
            return list(dict.fromkeys("CRI " + g for g in m.groups() if g))
    if field == "control":
        if not text or _NOT_A_CONTROL.fullmatch(text):
            return []
        return [_CONTROL_RUN_ON.sub(r"\1", text)]
    if field == "beam_angle":
        return [re.sub(r"^(\d+(?:\.\d+)?°)\s+Trimless$", r"\1", re.sub(r"^zoomable\s+(?=\d)", "", text, flags=re.I), flags=re.I)] if text else []
    return [text] if text else []


def current_spellings(state, offered):
    """{field: value} for each value in `state` that `offered` does not hold as written but does under its
    one spelling. A BOQ line saved as "Zoomable 20°-50°" or a CRI of "98" still means what the product now
    lists: shown as blank, the panel's next tweak would fill in the first option and change the line."""
    out = {}
    for field, value in (state or {}).items():
        listed = offered.get(field) or []
        if not value or value in listed:
            continue
        have = {norm(o): o for o in listed}
        if norm(value) in have:             # a hand-typed "36" on a product listing "36°"
            out[field] = have[norm(value)]
            continue
        for reading in spec_values(field, value):
            if norm(reading) in have:
                out[field] = have[norm(reading)]
                break
    return out


def configure_extras(sheets, skip):
    """The options of this make's products that Configure's ten shared questions leave out (a bezel or
    baffle finish, a version), as {key, label}, so a person can narrow by every one. Another make's
    products add nothing here: this is what DuraGreen's own CONFIG_FIELDS lists."""
    mine = [s for s in sheets if is_duragreen(s)]
    return [{"key": k, "label": LABEL_OF.get(k, k)} for k in CONFIG_FIELDS
            if k not in skip and any(_offered(s, k) for s in mine)]


def structure_attributes():
    """The details a naming structure's segment may name. Not one no code tells apart: a printed code
    would still win over the structure, so one product would follow two rules."""
    return [k for k in CONFIG_FIELDS + ["type", "size", "style", "trim", "variant"] if k in LABEL_OF and k not in NOT_IN_THE_CODE]


def segment_refused(attribute):
    """Why a naming structure's segment may not name this detail, or "" when it may."""
    return "%s is not part of an order code." % LABEL_OF.get(attribute, attribute) if attribute in NOT_IN_THE_CODE else ""


def _offered(sheet, key):
    opts = (getattr(sheet, "options", None) or {}).get(key)
    if isinstance(opts, list):
        return [str(o).strip() for o in opts if str(o).strip()]
    value = str((getattr(sheet, "fields", None) or {}).get(key) or "").strip()
    return [value] if value and "/" not in value and "," not in value else []


# Pieces of a code that name the product rather than a choice made when quoting it.
VERSION_PIECES = ("type", "size", "variant", "suffix")


def is_example(code_row) -> bool:
    """A catalogue example kept as the product's code template - not a code to order as is."""
    return EXAMPLE_TAG in str(getattr(code_row, "source", "") or "")


def _version(code_row):
    """Which version of a product a printed code is for: its fixed pieces joined ("AFL57-AL"),
    or for a code that could not be cut into pieces, its leading letters ("GS")."""
    pieces = code_row.parts or []
    if pieces:
        # A suffix of letters is a finish (-W, -AB) and does not make a version; any other
        # suffix does (UL104's sheet prints every code twice, plain and -24 for 24VDC - a
        # product that states both gets a voltage choice instead: see SELECTORS).
        return "".join(t for name, t in pieces if name in VERSION_PIECES
                       and not (name == "suffix" and re.fullmatch(r"-?[A-Z]+", t)))
    m = re.match(r"[A-Z]+", str(code_row.code or ""))
    return m.group(0) if m else ""


def _decided(sheet, chosen, key):
    """The value of `key` this configuration settles: chosen, or the only one the product has."""
    value = str((chosen or {}).get(key) or "").strip()
    if value:
        return value
    only = _offered(sheet, key)
    return only[0] if len(only) == 1 else ""


def range_of_page(page):
    """The range whose section a PDF page of the catalogue is in, or ""."""
    for name, spans in RANGE_PAGES.items():
        if any(first <= int(page or 0) <= last for first, last in spans):
            return name
    return ""


def _template_page(template):
    """The printed page number of a catalogue example ("... p.42 (code example)")."""
    m = re.search(r"p\.(\d+)", str(getattr(template, "source", "") or ""))
    return int(m.group(1)) if m else 0


def family_of(sheet):
    """The range a product's own name places it in - "ACME RECESSED", "ACME Wall Lights", "MOOD
    Outdoor Downlight" - for a product outside both ranges' sections; "" when it names neither,
    or both."""
    words = ("%s %s" % (getattr(sheet, "series", "") or "", getattr(sheet, "product_name", "") or "")).upper()
    named = [name for name in RANGE_PAGES if re.search(r"\b%s\b" % name, words)]
    return named[0] if len(named) == 1 else ""


def range_of(template):
    """The range whose code table is a product's rule: "ACME" or "MOOD" for a product whose
    catalogue page is in that range's section, "" for every other product - and for one with no
    catalogue page at all.

    Read from the PDF page the reader recorded. Until 22 Oct 2026 the printed number in the
    source text was compared with these PDF pages, 23 apart, which filed 58 products under the
    wrong range: shelf lights built with MOOD's table, MOOD downlights with ACME's."""
    if template is None:
        return ""
    page = (template.printed or {}).get("pdf_page")
    if not page:
        printed = _template_page(template)
        page = printed + FOLIO_OFFSET if printed else 0
    return range_of_page(page)


def _rule_range(rule):
    """Which range a catalogue rule speaks for: its table's, or its page's for a page's legend."""
    return _source_range(rule.scope, str(rule.source or ""))


_index_cache = {"rules": None, "size": -1, "index": {}}


def _index(rules):
    """The rules as (meaning, code, source) lists by (scope, attribute) - and a page's legend by
    (LEGEND, attribute, its range) - built once per rules list, not on every piece looked up."""
    if _index_cache["rules"] is not rules or _index_cache["size"] != len(rules):
        index = {}
        for r in rules:
            key = ("LEGEND", r.attribute, _rule_range(r)) if r.scope == "LEGEND" else (r.scope, r.attribute)
            index.setdefault(key, []).append((r.meaning, r.code, r.source))
        _index_cache.update(rules=rules, size=len(rules), index=index)
    return _index_cache["index"]


@functools.lru_cache(maxsize=4096)
def _source_range(scope, source):
    # cached: every piece looked up asks this of ~2,400 rules
    if scope in RANGE_PAGES:
        return scope
    if scope in EDITION_RANGE:
        return EDITION_RANGE[scope]
    m = re.search(r"p\.(\d+)", source)
    return range_of_page(int(m.group(1)) + FOLIO_OFFSET) if m else ""


def scheme_of(codes, naming=None):
    """Whose rules write this product's code - for NPD to say, and for the audit to count.

    {"name": ACME | MOOD | PAGE | SHEET | NONE | STRUCTURE, "range": "ACME"/"MOOD"/"" (the
     structure's name for STRUCTURE), "page": printed page of its catalogue example or 0,
     "example": that code, "printed": how many printed codes, "cut": how many of them are cut into
     pieces, "text": one sentence for a person}"""
    template = next((c for c in codes if is_example(c)), None)
    printed = [c for c in codes if not is_example(c)]
    rng = range_of(template)
    page = _template_page(template) if template is not None else 0
    cut = sum(1 for c in printed if c.parts)
    if naming:
        name = naming.get("structure") or ""
        return {"name": "STRUCTURE", "range": name, "page": 0, "example": "", "printed": len(printed),
                "cut": cut, "made": bool(naming.get("made")),
                "text": ("Named by the %s naming structure%s, put there in NPD%s. A code DuraGreen printed "
                         "or sent for exactly a configuration comes first; every other code is built from "
                         "the structure's letters." % (
                             name, "" if naming.get("made") else " (DuraGreen's printed table)",
                             (" by " + naming["entered_by"]) if naming.get("entered_by") else ""))}
    if rng:
        name = rng
        text = ("%s %s product: its own spec sheet and catalogue page (p.%s) first, then the %s code "
                "table (%s). A piece only another range prints is marked unconfirmed."
                % ("An" if rng[0] in "AEIOU" else "A", rng, page, rng, TABLE_SOURCE[rng]))
    elif template is not None:
        name = "PAGE"
        text = ("Its range prints no code table of its own (DuraGreen prints them for %s only), so its code "
                "follows its own spec sheet's printed codes and its own catalogue page (p.%s). A piece neither "
                "prints is marked unconfirmed, even where another range's table has one."
                % (", ".join(RANGE_PAGES), page))
    elif printed:
        name = "SHEET"
        text = ("No page of DuraGreen's 2026 catalogue shows this product, so its code follows only its own "
                "spec sheet's printed codes. A piece they do not print is marked unconfirmed.")
    else:
        name = "NONE"
        text = ("DuraGreen prints no order code for this product, on its spec sheet or in the 2026 "
                "catalogue. Add the codes DuraGreen sends.")
    return {"name": name, "range": rng, "page": page, "example": template.code if template is not None else "",
            "printed": len(printed), "cut": cut, "text": text}


# --- looking up one piece --------------------------------------------------------------------

def _pattern(field, value, like):
    """DuraGreen's way of writing a value no table lists: (token, status) or None.
    `like` is the token the template has in that place, which shows the form (08W, 12WX2)."""
    v = str(value or "")
    # A fragment or a mix is not a temperature or a beam: "3000K (2400K" (a piece of "2700K, 3000K
    # (2400K, 3500K,4000K on request)") was written 3024 until 26 Oct 2026, and "2700K-6000K + RGB"
    # 2760 - a code for half of what was chosen. "Zoomable 15°-50°" stays 1550, as GLAMO prints it.
    if field in ("cct", "beam_angle") and re.search(r"[(),+]|\bRGB|\brequest\b", v, re.I):
        return None
    if field == "wattage":
        m = re.search(r"(\d+(?:\.\d+)?)\s*W", v, re.I)
        if not m:
            return None
        num = m.group(1)
        unpadded = re.match(r"[1-9](\.\d+)?W", str(like or ""), re.I)     # p.656 writes 5W, not 05W
        if "." not in num and len(num) == 1 and not unpadded:
            num = "0" + num                       # 8W is written 08W
        twin = re.search(r"W(X\d)$", str(like or ""), re.I)
        times = re.search(r"[x×]\s*(\d)", v, re.I)
        tail = ("X" + times.group(1)) if times else (twin.group(1).upper() if twin else "")
        # The code table gives Power as a number, so the form is DuraGreen's rule.
        return num + "W" + tail, CATALOGUE
    if field == "cct":
        ends = re.findall(r"(\d{4})\s*K?", v, re.I)
        if len(ends) == 2:
            return ends[0][:2] + ends[1][:2], UNCONFIRMED      # 2700K-5000K -> 2750
        if len(ends) == 1:
            return ends[0][:2], UNCONFIRMED
    if field == "beam_angle":
        ends = re.findall(r"(\d+)\s*°?", v)
        if len(ends) == 2:
            return ends[0] + ends[1], UNCONFIRMED                # 20°-50° -> 2050
        if len(ends) == 1:
            return ends[0].zfill(2), UNCONFIRMED
    return None


def _shows_pattern(field, value, own):
    """(meaning, piece) of this product's own printed pieces that is written by DuraGreen's
    pattern in the same shape as `value` - a single CCT for a single CCT, a range for a range -
    or None. ("3000K", "30") shows how this product writes "2700K"."""
    def shape(v):
        return len(re.findall(r"\d{4}" if field == "cct" else r"\d+", str(v or "")))
    want = shape(value)
    for meaning, token, _source in own:
        made = _pattern(field, meaning, "")
        if made and made[0] == token and shape(meaning) == want:
            return meaning, token
    return None


def decode_piece(sheet, field, token, own, rules, range_name=""):
    """Which of a product's own options a piece of one of its printed codes stands for - the 30 in
    ALP6003W-AG-30N120 is its 3000K - or None.

    A sheet's table often does not say which CCT or driver its row is for, though its code does
    ("?30" in the row's configuration). An option takes the piece when its own codes, its own page
    or its own range's table say so; or, failing those, when every published source that names it
    agrees - both range tables alike (N is Non Dim in ACME and in MOOD), or DuraGreen's pattern for a
    CCT or beam (3000K is 30). Exactly one of the product's options must take it: reading a printed
    code is not guessing a new one, but it is still never a choice between two."""
    index = _index(rules)
    # High Efficiency's H30 is read as its 30: the H is the version's mark, not the colour.
    bare = re.fullmatch(r"[A-Z]{1,2}(\d{2,4})", str(token or "")) if field == "cct" else None
    if bare:
        token = bare.group(1)

    def tables_agree(value):
        # Both catalogue tables must name it (as before EVOLINE's was found), and no range's table
        # may write it otherwise - EVOLINE's included.
        said = {}
        for name in RANGE_PAGES:
            found, loose = _match(field, value, index.get((name, TABLE_FIELD.get(field, field)), []))
            if found and not loose:
                said[name] = {tok for tok, _src in found}
        return bool(said) and all(s == {token} for s in said.values()) and \
            (all(n in said for n in ("ACME", "MOOD")) or range_name)

    hits = []
    for value in _offered(sheet, field):
        tok, status, _src, note = lookup(field, value, own, rules, range_name, "", "" if range_name else family_of(sheet))
        if tok != token or "matched by words" in note:
            continue
        made = _pattern(field, value, "")
        if status in (COMPOSED, CATALOGUE, PATTERN, GENERAL) or tables_agree(value) \
                or (made and made[0] == token and field in ("cct", "beam_angle")):
            hits.append(value)
    return hits[0] if len(hits) == 1 else None


def _match(field, value, entries):
    """The tokens of the entries [(meaning, token, source)] that say `value`: exact first, then a
    meaning whose words contain all of the value's (White -> White Matt) as long as only one does."""
    want = canon(field, value)
    if not want:
        return [], False
    exact = [(tok, src) for meaning, tok, src in entries if canon(field, meaning) == want]
    if exact:
        return exact, False
    words = _words(value)
    loose = [(tok, src) for meaning, tok, src in entries if words and words <= _words(meaning)
             and _shape(field, meaning) == _shape(field, value)]
    return loose, True


def _shape(field, value):
    """How many temperatures a CCT names (3000K one, a tunable 2700K-6500K two), or numbers a beam
    does, else 0. A loose match keeps it: until 26 Oct 2026 every word of 6500K being in
    2700K-6500K gave a fixed 6500K neon the tunable range's 2765, and a 4000K the 1840."""
    if field not in ("cct", "beam_angle"):
        return 0
    return len(re.findall(r"\d{4}" if field == "cct" else r"\d+", str(value or "")))


def _one(found):
    toks = sorted({tok for tok, _src in found})
    return toks[0] if len(toks) == 1 else None, toks


def lookup(field, value, own, rules, range_name, like="", family=""):
    """(token, status, source, note) for one chosen option, or (None, "", "", why not).

    own        [(meaning, token, source)] printed for this product: its own codes and its own page.
    rules      VendorCodeRule rows (any object with scope, attribute, meaning, code, source).
    range_name the product's range ("ACME", "MOOD") or "" - see range_of. Only its own range's
               table and pages can make a piece more than unconfirmed.
    family     for a product outside both ranges, the range its name places it in ("ACME RECESSED",
               "MOOD Outdoor") - see family_of. Its family's table is the closer lead: still
               unconfirmed, but where ACME and MOOD disagree (Phase Cut P / M) it decides."""
    found, loose = _match(field, value, own)
    tok, toks = _one(found)
    if tok:
        return tok, (UNCONFIRMED if loose else COMPOSED), found[0][1], ("matched by words" if loose else "")
    if len(toks) > 1:
        return None, "", "", "this product prints %s for %s" % (" and ".join(toks), value)

    table_field = TABLE_FIELD.get(field, field)
    index = _index(rules)

    def table(scope):
        # A table with a column of the field's own (BELLA's and ARTFOCUS's Structure Colour is
        # the finish) answers from it; one without, from the column standing in for it.
        return index.get((scope, field)) or index.get((scope, table_field), [])

    def lead(name):
        # Another range's table as a lead. Where that range's newest table renamed a code its own
        # earlier editions agree on - ACME's 2026 table writes Phase Cut P, ACME 2024-25 and every
        # other DuraGreen table M - the change is that range's own: the lead keeps the earlier code.
        newest = table(name)
        earlier = [e for scope in EARLIER_EDITIONS.get(name, ()) for e in table(scope)]
        if not earlier:
            return newest
        out = []
        for meaning, tok, src in newest:
            olds = [e for e in earlier if canon(field, e[0]) == canon(field, meaning)]
            if olds and len({t for _m, t, _s in olds}) == 1 and tok != olds[0][1]:
                out.extend(olds)
            else:
                out.append((meaning, tok, src))
        return out

    def pages(same_range):
        # A product outside both ranges has no "same range": every other page is another's.
        if same_range:
            return index.get(("LEGEND", field, range_name), []) if range_name else []
        return [e for rng in tuple(RANGE_PAGES) + ("",) if not (range_name and rng == range_name)
                for e in index.get(("LEGEND", field, rng), [])]

    # Where to look, surest first: (entries, status, what a conflict is called, note on a find,
    # whether a match by words counts). Inside its own range a piece is DuraGreen's rule for this
    # product; outside it, a lead only - and where the leads disagree (ACME writes Mains Dim as P,
    # MOOD as M) there is no lead, because nothing says which one this product follows.
    steps = []
    if range_name:
        own_status = CATALOGUE if (table_field == field or index.get((range_name, field))) else GENERAL
        # Its range's table, word for word; then, for a meaning that table does not list, the earlier
        # editions of it (they must agree); only then the table again, matched by words.
        steps.append((table(range_name), own_status, "the %s table" % range_name, "", False))
        earlier = [e for scope in EARLIER_EDITIONS.get(range_name, ()) for e in table(scope)]
        if earlier:
            steps.append((earlier, GENERAL, "the earlier editions of the %s table" % range_name,
                          "from an earlier edition of the %s table - the 2026 table does not list %s"
                          % (range_name, value), False))
        steps.append((table(range_name), own_status, "the %s table" % range_name, "", True))
        steps.append((pages(True), GENERAL, "the %s pages" % range_name,
                      "the same words on another %s page" % range_name, False))
    # Then the product's own pattern: where its printed codes show how it writes a CCT or a beam
    # (its 3000K as 30), another of its own options is written the same way. Its own sheet is the
    # evidence, so this comes before any other range's table.
    steps.append(("own pattern", PATTERN, "", "", False))
    if family and family != range_name:
        steps.append((table(family), UNCONFIRMED, "the %s table" % family,
                      "from the %s table: an %s-family product, but outside the %s section, so not "
                      "its rule" % (family, family, family), True))
    others = [name for name in RANGE_PAGES if name != range_name]
    steps.append(([e for name in others for e in lead(name)], UNCONFIRMED,
                  "the %s table%s" % (" and ".join(others), "s" if len(others) > 1 else ""),
                  "from another range's code table, not this product's rule", True))
    steps.append((pages(False), UNCONFIRMED, "the catalogue's pages",
                  "the same words on another range's page, not this product's", False))
    for entries, status, where, note, loose_ok in steps:
        if entries == "own pattern":
            made = _pattern(field, value, like)
            shown = _shows_pattern(field, value, own) if made and made[1] == UNCONFIRMED else None
            if shown:
                return made[0], PATTERN, "this product's own codes", \
                    "written as its own codes write %s (%s)" % (shown[0], shown[1])
            continue
        found, loose = _match(field, value, entries)
        if loose and not loose_ok:
            continue           # a page's words are matched exactly or not at all
        tok, toks = _one(found)
        if tok:
            st = UNCONFIRMED if loose else status
            return tok, st, found[0][1], "; ".join(n for n in (note, "matched by words" if loose else "") if n)
        if len(toks) > 1:
            return None, "", "", "%s write%s %s as %s" % (where, "" if where.endswith("s") else "s",
                                                         value, " or ".join(toks))

    made = _pattern(field, value, like)
    if made:
        return made[0], made[1], "DuraGreen's pattern", "printed nowhere" if made[1] == UNCONFIRMED else ""
    return None, "", "", "DuraGreen prints no code for %s %s" % (LABEL_OF.get(field, field).lower(), value)


# --- the code for one configuration ------------------------------------------------------------

def _own_meanings(printed, parts, template, every_printed=()):
    """[(field, meaning, token, source)] this product's own sheet and page print."""
    def twin(field, value, token):
        # "6W x 2" is 06WX2 in the catalogue; the spec sheets' codes were cut as 06W + X2
        m = re.search(r"[x×]\s*(\d)", str(value or ""), re.I) if field == "wattage" else None
        return token + "X" + m.group(1) if m and not token.upper().endswith("X" + m.group(1)) else token

    out = []
    for c in printed:
        cfg = c.config or {}
        for name, token in (c.parts or []):
            field = PART_FIELD.get(name)
            if field and cfg.get(field) and plain(field, token):
                out.append((field, cfg[field], twin(field, cfg[field], token), "printed code " + c.code))
    for p in parts:
        if (p.status or "stated") == "stated":
            out.append((p.attribute, p.value, twin(p.attribute, p.value, p.code), p.source or "spec sheet"))
    # A printed code that could not be cut still shows how this product writes its power: Sparkle's
    # WFL3503W-1 is its 3W, so a built code writes 03W (the page's example writes 2.5W, unpadded).
    # The padded form first, and it must end where a piece ends (a '-' or the end of the code).
    for c in every_printed:
        watts = re.fullmatch(r"(\d+(?:\.\d+)?)W", str((c.config or {}).get("wattage") or ""))
        if c.parts or not watts:
            continue
        num = watts.group(1)
        forms = (["0" + num + "W"] if "." not in num and len(num) == 1 else []) + [num + "W"]
        token = next((f for f in forms if re.search(re.escape(f) + r"(?=-|$)", c.code)), None)
        if token:
            out.append(("wattage", c.config["wattage"], token, "printed code " + c.code))
    if template is not None:
        page = _template_page(template)
        for field, token, meaning in (template.printed or {}).get("legend", []):
            out.append((field, meaning, token, "%s p.%s" % (SOURCE_NAME, page)))
        # A whole printed code identical to the catalogue's example is cut by the example's own
        # pieces: STICK's sheet prints SBG1653-02-5W for its 5.2W, so its 5.2W is written 5W.
        # Without this the example's 5W was rebuilt from the value, as SBG1653-02-5.2W.
        tparts = getattr(template, "parts", None) or []
        whole = "".join(t for _n, t in _without_optional(tparts))
        for c in every_printed:
            cfg = c.config or {}
            if c.parts or not tparts or c.code not in (getattr(template, "code", ""), whole):
                continue
            for name, token in tparts:
                field = PART_FIELD.get(name)
                if field and cfg.get(field) and not str(cfg[field]).startswith("?") and plain(field, token):
                    out.append((field, cfg[field], token, "printed code " + c.code))
    return out


def _head_end(pieces):
    """Where a code's head ends: at its beam - or, in a code with no beam, at its last chosen
    piece. Sparkle's step lights print WFL352.5W-30N-5-W: no beam, the driver N, then the face
    pattern (-5) and the bezel colour (-W), which belong to the tail. -1 when nothing is chosen."""
    names = [n for n, _t in pieces]
    if "beam_angle" in names:
        return names.index("beam_angle")
    chosen = [i for i, n in enumerate(names) if PART_FIELD.get(n)]
    if not chosen:
        return -1
    end = chosen[-1]
    # A beam written in letters and glued to the driver letter - AATL6815W-30NWW, the wall-wash
    # track head - is cut as a "suffix", but with no dash before it it is the head's last piece,
    # not the finish. Read as the finish it was dropped, and the line got AATL6815W-30N-B.
    if end + 1 < len(names) and names[end + 1] == "suffix" and not str(pieces[end + 1][1]).startswith("-"):
        end += 1
    return end


def _tail_pieces(template):
    """The template's pieces after the head (see _head_end): [(name, token, optional)]. A piece
    printed in brackets - (24) for 24VDC, (-TL) - is optional and left out unless asked for."""
    pieces = (template.parts or []) if template is not None else []
    # Only a code that HAS no beam ends its head at its last chosen piece. An example whose legend
    # lists a beam the example itself lacks (p.284: legend WS, example 36) had its beam set aside
    # as unexplained, and ending there would carry that "36-" into the tail (MFL10020W-30NWS36-...).
    legend_fields = {row[0] for row in ((template.printed or {}).get("legend") or [])} if template is not None else set()
    if "beam_angle" not in {n for n, _t in pieces} and "beam_angle" in legend_fields:
        return []
    end = _head_end(pieces)
    if end < 0:
        return []
    out, inside = [], False
    for name, token in pieces[end + 1:]:
        if name == "sep":
            if "(" in token:
                inside = True
            out.append(("sep", token.replace("(", "").replace(")", ""), False))
            if ")" in token:
                inside = False
            continue
        out.append((name, token, inside or name == "voltage"))
    return out


def _without_optional(pieces):
    """A catalogue example's pieces without the ones printed in brackets: SA3606W(/SB)-30N15
    is SA3606W-30N15, the /SB being the optional standard base."""
    out, inside = [], False
    for name, token in pieces or []:
        if name == "sep":
            opens, closes = "(" in token, ")" in token
            if opens:
                inside = True
            token = token.replace("(", "").replace(")", "")
            if closes:
                inside = False
            if token and not (inside and not closes):
                out.append(["sep", token])
            continue
        if not inside:
            out.append([name, token])
    return out


def _join(pieces):
    code = "".join(pieces)
    code = re.sub(r"-{2,}", "-", code)
    return code.strip("-")


def order_code(sheet, chosen, codes, parts, rules=(), memo=None, naming=None):
    """The DuraGreen order code for one configuration, and how sure it is.

    `codes` are the product's VendorOrderCode rows (its printed codes, and its catalogue example
    as template), `parts` its VendorCodePart rows, `rules` DuraGreen's VendorCodeRule rows.
    `memo`, a dict shared by calls for the SAME product and rows (combinations), keeps what does
    not depend on the configuration, so it is worked out once.
    `naming`, for a product a person put in a naming structure, is that structure with how the
    product fills it ({"structure", "segments", "modes", "made", "source"}) - its code is then
    built by structure_code; every other product's is built here, exactly as before 27 Oct 2026.
    Returns
        {"code": "" or the code, "head": the code up to the beam, "status": printed | composed |
         catalogue | general | unconfirmed | "", "reason": why there is no code (or what to
         check), "missing": [field keys to choose], "pieces": [how each piece was found]}
    """
    def none(reason, missing=()):
        return {"code": "", "head": "", "status": "", "missing": list(missing), "reason": reason, "pieces": []}

    if naming:
        return structure_code(sheet, chosen, codes, parts, naming)
    template = next((c for c in codes if is_example(c)), None)
    printed = [c for c in codes if not is_example(c)]
    if not printed and template is None:
        return none("DuraGreen prints no order code for this product")
    range_name = range_of(template)
    family = "" if range_name else family_of(sheet)

    # A printed row is only evidence for the options it states. A strip light's table row
    # "12W GS2835-128-12W-30-98" says nothing about CCT in its columns, so on a product offering
    # 2700K and 3000K it cannot tell which the code is for - used anyway, a 2700K line would get
    # a 3000K code. Every option the product offers a choice of must be stated by the row.
    drivers = control_is_drivers(sheet, codes)
    choices = [k for k in COVERED if len(_offered(sheet, k)) > 1 and not (drivers and k == "control")]
    if printed:
        usable = [c for c in printed if all(k in (c.config or {}) for k in choices)]
        if not usable and template is None:
            return none("DuraGreen's code table does not say which %s its codes are for"
                        % " / ".join(LABEL_OF.get(k, k) for k in choices
                                     if not any(k in (c.config or {}) for c in printed)))
        printed = usable

    # A product whose sheet prints several versions and says which is which - by voltage, or by
    # the labels over its tables - is quoted on that choice, and the code comes only from the
    # chosen version's rows: a mains line never borrows a 24V code, a surface one never a recessed.
    selected = {}
    for key in SELECTORS:
        if len(_offered(sheet, key)) > 1:
            value = _decided(sheet, chosen, key)
            if not value:
                return none("choose %s first" % LABEL_OF.get(key, key), [key])
            mine = [c for c in printed if norm((c.config or {}).get(key)) == norm(value)]
            if printed and not mine:
                return none("DuraGreen prints no code for %s %s on this product"
                            % (LABEL_OF.get(key, key).lower(), value))
            printed = mine
            selected[key] = value
    dc = is_dc(_decided(sheet, chosen, "voltage"))
    # The versions are different fittings, so a wattage printed only for another of them is no
    # evidence for this one (FOCUS 70 prints its 8W only as 24V DC).
    version_watts = {norm((c.config or {}).get("wattage")) for c in printed} if selected else None

    # One sheet often prints the codes of several versions of a product side by side - recessed
    # RTC and trimless TLC, SA and SF, with and without /SB - and nothing a BOQ line chooses says
    # which version this product is. Matched on its options alone, a line would take whichever
    # version's code happened to fit. So a product whose printed codes come in more than one
    # version gets no code until a person removes the other versions' codes in NPD (DuraGreen).
    # A code a person typed in is theirs for this product, and is not counted as a version.
    versions = sorted({_version(c) for c in printed if not str(getattr(c, "source", "") or "").startswith("entered by")})
    if len(versions) > 1:
        # Versions a choice already tells apart: EVOLINE's Dot Line prints its 6-dot version only
        # at 8W and its 14-dot only at 12W, so the wattage chosen IS the version.
        picked = _version_by_choice(sheet, chosen, printed)
        if picked is None:
            picked = _version_by_name(sheet, printed, rules)
        if picked is not None:
            printed = picked
            versions = [None]
    if len(versions) > 1:
        return none("the spec sheet prints codes for %d versions of this product (%s) and does not "
                    "say which one this is - remove the other versions' codes in NPD (DuraGreen)"
                    % (len(versions), ", ".join(v + "…" for v in versions)))

    # A whole code - one a person typed in from DuraGreen's answer, or a printed one that could
    # not be cut into pieces - stating exactly this configuration is the answer as it stands.
    # Until 22 Oct 2026 such a code was never looked at when the product also had cut codes, so a
    # code entered in NPD changed nothing for most products.
    skip = ("control",) if drivers else ()
    whole = {c.code for c in printed if not c.parts and _states_exactly(sheet, c, chosen, skip)}
    if whole:
        also = {c.code for c in printed if c.parts and _states_exactly(sheet, c, chosen, skip)}
        if len(whole | also) > 1:
            return none("the codes printed or entered for this configuration disagree (%s) - remove the "
                        "wrong one in NPD (DuraGreen)" % ", ".join(sorted(whole | also)))
        code = whole.pop()
        return {"code": code, "head": code, "status": PRINTED, "missing": [],
                "reason": DRIVER_NOTE if drivers and (chosen or {}).get("control") else "",
                "pieces": [{"field": "pattern", "value": "", "code": code, "status": PRINTED,
                            "source": next(c.source for c in printed if c.code == code) or "",
                            "note": "the whole code, for exactly this configuration"}]}

    # The head's pattern: a printed code cut into pieces. The catalogue example is the pattern
    # only for a product with no printed code at all, and only when the example has every piece
    # its own legend explains - a few examples drop one (p.210 leaves out the -TL its table has).
    shaped = [c for c in printed if c.parts]
    if shaped:
        base_rows = shaped
    elif printed:
        # Only whole printed codes that could not be cut up: usable exactly as printed, never changed.
        found = _printed_only(sheet, chosen, printed)
        if drivers and found["code"] and (chosen or {}).get("control"):
            found["reason"] = DRIVER_NOTE
        return found
    elif (template.printed or {}).get("skipped"):
        return none("DuraGreen's catalogue example for this product (p.%s) leaves out %s that its own "
                    "legend lists, and the product has no printed code to correct it - ask DuraGreen"
                    % (_template_page(template), " ".join(template.printed["skipped"])))
    else:
        base_rows = [template]

    head_fields = []
    for name, _t in (base_rows[0].parts or []):
        if PART_FIELD.get(name) and PART_FIELD[name] not in head_fields:
            head_fields.append(PART_FIELD[name])
        if name == "beam_angle":
            break
    tail = _tail_pieces(template)
    tail_fields = [n for n, _t, opt in tail if n in TAIL_CHOICES and not opt]
    tail_offered = [k for k in tail_fields if _offered(sheet, k)]
    fixed_only = not tail_fields and any(n != "sep" and not opt for n, _t, opt in tail)
    builds_tail = bool(tail_offered) or fixed_only

    decided = {k: _decided(sheet, chosen, k) for k in head_fields + tail_offered}
    missing = [k for k in head_fields if not decided[k]]
    if missing:
        return none("choose %s first" % ", ".join(LABEL_OF.get(k, k) for k in missing), missing)

    # What the product's own codes mean is the same for every configuration that uses the same
    # printed rows; `memo` (one call of combinations) keeps it, keyed by exactly those rows, so a
    # product's 500 combinations work it out once, not 500 times (27 Oct: 0.56 s of 1.8 s).
    key = ("own", tuple(id(c) for c in printed), id(template))
    own = memo.get(key) if memo is not None else None
    if own is None:
        own = _own_meanings(printed, parts, template, [c for c in codes if not is_example(c)])
        if memo is not None:
            memo[key] = own
    own_by = {}
    for field, meaning, token, source in own:
        own_by.setdefault(field, []).append((meaning, token, source))

    def looked_up(field, value, mine, like, prefix=""):
        # One option's code is the same on every configuration of this product that uses the same
        # own meanings and the same printed piece, so `memo` keeps it (`own` is the memo's own list).
        k = ("lookup", field, value, prefix, like, id(own))
        if memo is not None and k in memo:
            return memo[k]
        got = lookup(field, value, mine, rules, range_name, like, family)
        if memo is not None:
            memo[k] = got
        return got

    # 1) The head. A printed row that is this configuration is used as printed; otherwise the
    #    closest printed row, with each differing choice looked up.
    def differs(c):
        cfg = c.config or {}
        return [k for k in head_fields if norm(cfg.get(k)) != norm(decided[k])]

    pieces_out, why_not, volt_in_head = [], "", False
    ranked = sorted(base_rows, key=lambda c: (is_example(c), len(differs(c)) if not is_example(c) else 99))
    # Rows that are this configuration. With a finish to build, only their heads must agree;
    # without one the whole printed code is the answer, so the whole codes must.
    exact_heads = {(_head_of(c) if builds_tail else c.code) for c in ranked if not is_example(c) and not differs(c)}
    if len(exact_heads) > 1:
        return none("the spec sheet prints %d codes for this configuration (%s) - it does not say "
                    "which one this product takes" % (len(exact_heads), ", ".join(sorted(exact_heads))))
    head, head_status = None, PRINTED
    for c in ranked:
        diff = head_fields if is_example(c) else differs(c)
        built, status, notes, ok = [], PRINTED if not is_example(c) else COMPOSED, [], True
        seq = _without_optional(c.parts) if is_example(c) else (c.parts or [])
        end = _head_end(seq)
        carries_dc = False
        for i, (name, token) in enumerate(seq):
            field = PART_FIELD.get(name)
            if name == "suffix" and builds_tail and not (i == end and not str(token).startswith("-")):
                # The printed -W is the finish, built below - but a printed -24 is the 24V DC
                # version itself, and stays (the catalogue puts it before the finish too).
                if token == DC_SUFFIX:
                    built.append(token)
                    carries_dc = True
                break
            if field in diff:
                # A prefix every printed code of this product puts on the piece - High Efficiency's
                # H30 / H27 for 3000K / 2700K - is the product's own rule: the piece is looked up
                # without it and written with it (H35 for 3500K).
                prefix = _own_prefix(field, token, printed)
                if not plain(field, token[len(prefix):]):
                    ok, why_not = False, ("DuraGreen writes this product's %s as %s, which its tables do not "
                                          "explain - ask DuraGreen for the %s code"
                                          % (LABEL_OF.get(field, field).lower(), token, decided[field]))
                    break
                mine = [(m, t[len(prefix):] if prefix and t.startswith(prefix) else t, s)
                        for m, t, s in own_by.get(field, [])]
                tok, st, src, note = looked_up(field, decided[field], mine, token[len(prefix):], prefix)
                if not tok:
                    ok, why_not = False, note
                    break
                if prefix:
                    tok = prefix + tok
                    note = "; ".join(n for n in (note, "with the %s every printed code of this product puts "
                                                       "before it (%s)" % (prefix, token)) if n)
                if field == "wattage" and not re.search(r"X\d$", token, re.I):
                    tok = re.sub(r"X\d$", "", tok, flags=re.I)   # this code prints the X2 as a piece of its own
                if field == "wattage" and version_watts is not None and norm(decided[field]) not in version_watts:
                    st, note = UNCONFIRMED, "%s is printed only for another %s of this product" % (
                        decided[field], " / ".join(LABEL_OF.get(k, k).lower() for k in selected))
                built.append(tok)
                status = max(status, st, key=RANK.get)
                notes.append({"field": field, "value": decided[field], "code": tok, "status": st,
                              "source": src, "note": note})
            else:
                built.append(token)
            if i == end and (builds_tail or is_example(c)):
                break                                   # an example's own finish is not this line's
        if ok:
            head, head_status, pieces_out, head_row = "".join(built), status, notes, c
            volt_in_head = carries_dc
            break
    if head is None:
        return none(why_not or "no printed code can be adapted to this configuration")
    pieces_out.insert(0, {"field": "pattern", "value": "", "code": head_row.code,
                          "status": CATALOGUE if is_example(head_row) else PRINTED,
                          "source": head_row.source or "", "note": "the code the others are put into"})

    # 2) The tail: the finish, in the order the catalogue example puts it. A tail with no choice
    #    in it - TB alone, on a trimless Alabaster - is part of the code as it stands; one whose
    #    choices this product lists no options for is left to the spec sheet's printed form.
    if not builds_tail:
        printed_codes = {c.code for c in printed}
        status = PRINTED if head in printed_codes else (COMPOSED if head_status == PRINTED else head_status)
        return {"code": head, "head": head, "status": status, "missing": [],
                "reason": "" if status == PRINTED else STATUS_TEXT[status], "pieces": pieces_out}
    still = [k for k in tail_offered if not decided[k] and k != "accessories"]
    if still:
        return {"code": head, "head": head, "status": head_status, "missing": still,
                "reason": "DuraGreen's code continues with the finish - choose %s to complete it"
                          % ", ".join(LABEL_OF.get(k, k) for k in still), "pieces": pieces_out}
    status, built, extra = head_status, [head], []
    for name, token, optional in tail:
        if name == "sep":
            built.append(token)
            continue
        if optional:
            if name == "voltage" and dc:
                if not volt_in_head:            # the catalogue's (24): wanted for a 24V DC line
                    built.append(token)
                continue
            extra.append("%s (%s) is optional and left out" % (token, LABEL_OF.get(name, name)))
            continue
        if name not in TAIL_CHOICES:
            built.append(token)                         # TB, a fixed piece of this version
            continue
        if not decided.get(name):
            if name != "accessories" and name in tail_fields and name not in tail_offered:
                extra.append("the catalogue adds a %s code here; this product lists no options for it"
                             % LABEL_OF.get(name, name).lower())
            continue
        tok, st, src, note = looked_up(name, decided[name], own_by.get(name, []), token)
        if not tok:
            if name == "accessories":
                extra.append("%s has no letter in the code - order it separately" % decided[name])
                continue
            return none(note)
        built.append(tok)
        status = max(status, st, key=RANK.get)
        pieces_out.append({"field": name, "value": decided[name], "code": tok, "status": st, "source": src, "note": note})
    code = _join(built)
    if code in {c.code for c in printed}:
        status = PRINTED           # the whole code, finish and all, is one DuraGreen prints
    elif status == PRINTED:
        status = COMPOSED          # the head is printed; the finish was added to it
    reason = "; ".join([STATUS_TEXT[status]] * (status != PRINTED) + extra)
    return {"code": code, "head": head, "status": status, "missing": [], "reason": reason,
            "pieces": pieces_out}


def _own_prefix(field, token, printed):
    """The letters every printed code of this product puts before its `field` piece - "H" for High
    Efficiency's H30 and H27 - or "". Only a CCT is read so: the prefix must be on the piece of
    every printed code that has one, never on some."""
    if field != "cct":
        return ""
    m = re.fullmatch(r"([A-Z]{1,2})(\d{2,4})", str(token or ""))
    if not m:
        return ""
    pieces = [t for c in printed for n, t in (c.parts or []) if PART_FIELD.get(n) == field]
    return m.group(1) if pieces and all(re.fullmatch(re.escape(m.group(1)) + r"\d{2,4}", t) for t in pieces) else ""


def _version_by_choice(sheet, chosen, printed):
    """The printed rows of the one version a chosen option picks out, or None. A choice picks a
    version only when each version's rows state it, and no value of it is printed for two
    versions - then the value chosen belongs to exactly one. Rows a person typed in are kept."""
    typed = [c for c in printed if str(getattr(c, "source", "") or "").startswith("entered by")]
    rows = [c for c in printed if c not in typed]
    by_version = {}
    for c in rows:
        by_version.setdefault(_version(c), []).append(c)
    for key in COVERED:
        value = _decided(sheet, chosen, key)
        if not value:
            continue
        values = {v: {norm((c.config or {}).get(key)) for c in cs} for v, cs in by_version.items()}
        if any("" in vs for vs in values.values()):
            continue                                    # a version's rows do not all state it
        seen = [x for vs in values.values() for x in vs]
        if len(seen) != len(set(seen)):
            continue                                    # a value printed for two versions
        hit = [v for v, vs in values.items() if norm(value) in vs]
        if len(hit) == 1:
            return by_version[hit[0]] + typed
    return None


def _version_by_name(sheet, printed, rules):
    """The printed rows of the one version a product's own name picks out, or None.

    ARTFOCUS's sheet prints the fixed light (SF36...) beside the adjustable spot's one, two and
    three heads (SA36..., X2, X3) - other website products. "1 Spot Fixed" names its own: the
    catalogue's legend says SF is "surface fixed", and it has one head. Only "fixed" /
    "adjustable" and a spot count are read from the name, each against the catalogue's own words
    for the version's type letters, and only one surviving version counts."""
    name = ("%s %s" % (getattr(sheet, "series", "") or "", getattr(sheet, "product_name", "") or "")).lower()
    kinds = {"fixed", "adjustable"} & set(re.findall(r"[a-z]+", name))
    spots = re.search(r"\b(\d+)\s*spots?\b", name)
    if len(kinds) != 1 and not spots:
        return None
    meanings = {}
    for r in rules:
        if r.scope == "LEGEND" and r.attribute == "type":
            meanings.setdefault(r.code, set()).add(r.meaning.lower())
    keep = []
    for c in printed:
        pieces = dict((n, t) for n, t in (c.parts or []) if n in ("type", "wattage"))
        typed = pieces.get("type", "")
        if len(kinds) == 1:
            said = meanings.get(typed, set())
            if not said or not any(next(iter(kinds)) in m for m in said):
                continue
        if spots:
            twin = re.search(r"X(\d)$", str(pieces.get("wattage", "")), re.I) or \
                re.search(r"[x×]\s*(\d)", str((c.config or {}).get("wattage") or ""), re.I)
            if int(twin.group(1) if twin else 1) != int(spots.group(1)):
                continue
        keep.append(c)
    return keep if keep and len({_version(c) for c in keep}) == 1 else None


def _states_exactly(sheet, row, chosen, skip=()):
    """Whether a code row is for exactly this configuration: every option it states is the one
    settled here, and every option the product offers a choice of that has been chosen is
    stated - a code typed in without its bezel colour is no answer for a black bezel. `skip`:
    choices that are not part of this product's code (a strip's control is its driver's)."""
    cfg = row.config or {}
    if not cfg:
        return False
    keys = set(cfg) | {k for k in (chosen or {}) if str(chosen[k] or "").strip() and len(_offered(sheet, k)) > 1
                       and k not in skip and k not in NOT_IN_THE_CODE}
    for k in keys:
        want = _decided(sheet, chosen, k)
        if not want or k not in cfg or norm(cfg[k]) != norm(want):
            return False
    return True


def _head_of(code_row):
    """A printed code up to its beam: MOOD's spec sheets print MFL5506W-30N24-W where the
    catalogue prints MFL5506W-30N24, and the -W is the bezel finish, not part of the head."""
    out = []
    for name, token in (code_row.parts or []):
        if name == "suffix":
            break
        out.append(token)
    return "".join(out) if out else code_row.code


def _printed_only(sheet, chosen, printed):
    keys = []
    for c in printed:
        for k in (c.config or {}):
            if k not in keys:
                keys.append(k)
    decided = {k: _decided(sheet, chosen, k) for k in keys}
    missing = [k for k in keys if not decided[k]]
    if missing:
        return {"code": "", "head": "", "status": "", "missing": missing, "pieces": [],
                "reason": "choose %s first" % ", ".join(LABEL_OF.get(k, k) for k in missing)}
    exact = {c.code for c in printed
             if all(norm((c.config or {}).get(k)) == norm(decided[k]) for k in (c.config or {}))}
    if len(exact) == 1:
        code = exact.pop()
        return {"code": code, "head": code, "status": PRINTED, "missing": [], "reason": "", "pieces": []}
    return {"code": "", "head": "", "status": "", "missing": [], "pieces": [],
            "reason": ("the spec sheet prints %d codes for this configuration" % len(exact)) if exact
            else "DuraGreen prints no code for this configuration on this product"}


# --- naming structures: a product a person put in one (27 Oct 2026) ----------------------------
#
# DuraGreen names its products by its code tables - ACME's, MOOD's, EVOLINE's ... each a row of
# segments (Type, Engine size, Power ...), each segment a list of letters. A product on one of
# those ranges' catalogue pages follows its table by itself (range_of). The owner, 27 Oct 2026:
# "always follow DuraGreen product naming, but if in future a new product comes along with new
# codes, the user must have 100 percent flexibility to add its detail in NPD ... if it already
# belongs to any group, for example ACME, the ACME naming structure should fit for that product;
# if not, the user must be allowed to create a new structure and naming."
#
# So a product no catalogue page names - only its own sheet's codes, or none at all - can be put
# in a structure by a person: one of DuraGreen's printed tables, or one a person made
# (models.VendorCodeStructure). Per segment the product's detail is fixed (one value, kept in
# NPD), quoted (an option chosen when it is quoted, priced by its adder) or out (not in its code).
# Its code is then the structure's segments in order, each piece's letters from, surest first:
#   its own codes        a code printed or entered whole for exactly this configuration (PRINTED),
#                        then a code part entered for this product (COMPOSED);
#   the structure        its letters word for word (CATALOGUE): DuraGreen's printed table, or the
#                        letters a person entered from what DuraGreen sent;
#   its pattern          power written as the structure writes it (08W, CATALOGUE as in
#                        order_code); a CCT or beam written the way the structure writes the ones
#                        it lists - 3000K is 30, so 2700K is 27 (PATTERN);
#   words only           a meaning matched by its words alone (UNCONFIRMED, so it says so).
# Anything else gets no code, and the reason says which letters to add. A structure never borrows
# another's letters: the person put the product in THIS one.

ENTERED = "entered by"
NAMING_MODES = ("fixed", "quoted", "out")
# The Nomenclature tab's classes for a product nobody put in a structure (naming_class).
PAGE, SHEET, NONE_CLASS = "PAGE", "SHEET", "NONE"
# Names a structure a person makes may not take: DuraGreen's own tables and their editions, the
# catalogue pages' legends, and the classes above.
RESERVED_NAMES = {"LEGEND", PAGE, SHEET, NONE_CLASS} | set(RANGE_PAGES) | set(EDITION_RANGE)


def is_entered(source) -> bool:
    """Whether a row was typed in by a person (from what DuraGreen sent), not read from DuraGreen."""
    return str(source or "").startswith(ENTERED)


def _entry(rule):
    return {"meaning": rule.meaning, "code": rule.code, "source": rule.source or "",
            "entered": is_entered(rule.source), "id": getattr(rule, "id", None)}


def structure_segments(name, rules, skeleton=None):
    """The segments of naming structure `name`, in order: [{"segment", "attribute", "dash_after",
    "position", "entries": [{"meaning", "code", "source", "entered", "id"}]}].

    `skeleton` is a person's structure's own list of segments (VendorCodeStructure.segments), whose
    letters are the rules filed under its name by segment position. DuraGreen's printed tables have
    none: their rows are the structure, with any letters a person added to them."""
    rows = [r for r in rules if r.scope == name]
    if skeleton is not None:
        out = [{"segment": str(s.get("segment") or ""), "attribute": str(s.get("attribute") or ""),
                "dash_after": bool(s.get("dash_after")), "position": i, "entries": []}
               for i, s in enumerate(skeleton)]
        for r in rows:
            if 0 <= (r.position or 0) < len(out):
                out[r.position or 0]["entries"].append(_entry(r))
        return out
    by_position = {}
    for r in rows:
        seg = by_position.setdefault(r.position or 0, {"segment": r.segment, "attribute": r.attribute,
                                                       "dash_after": bool(r.dash_after),
                                                       "position": r.position or 0, "entries": []})
        seg["entries"].append(_entry(r))
        if not is_entered(r.source):
            seg["dash_after"] = bool(r.dash_after)     # DuraGreen's printed rows decide the dash
    return [by_position[k] for k in sorted(by_position)]


def _naming_modes(naming):
    return {k: v for k, v in ((naming or {}).get("modes") or {}).items() if isinstance(v, dict)}


def naming_values(sheet, chosen, naming):
    """{attribute: value} each segment of the product's structure takes for this configuration,
    and the attributes still to choose: (values, missing, why_not). A fixed segment takes its
    value; a quoted one the option chosen (or the only one the product offers); one with no mode
    the product's own single value where it has one; an "out" segment, and one with no value of
    any kind, is not part of the code."""
    modes = _naming_modes(naming)
    values, missing, why_not = {}, [], ""
    for seg in naming.get("segments") or []:
        attr = seg["attribute"]
        mode = (modes.get(attr) or {}).get("mode") or ""
        if not attr or mode == "out":
            continue
        if mode == "fixed":
            value = str((modes.get(attr) or {}).get("value") or "").strip()
            if not value and not why_not:
                why_not = "%s is fixed for this product but no value is set - choose it under Naming" % seg["segment"]
        else:
            value = _decided(sheet, chosen, attr)
            if not value and len(_offered(sheet, attr)) > 1:
                missing.append(attr)
                continue
            if not value and mode == "quoted" and not why_not:
                why_not = ("%s is quoted, but the product lists no options for it - add them under "
                           "Specification and pricing" % seg["segment"])
        if value:
            values[attr] = value
    return values, missing, why_not


def structure_piece(name, attr, value, segment, own=()):
    """(token, status, source, note) for one value in one segment of naming structure `name`, or
    (None, "", "", why not). `own`: [(meaning, token, source)] this product's entered code parts."""
    label = segment.get("segment") or LABEL_OF.get(attr, attr)
    found, loose = _match(attr, value, list(own))
    tok, toks = _one(found)
    if tok:
        return tok, (UNCONFIRMED if loose else COMPOSED), found[0][1], ("matched by words" if loose else "")
    if len(toks) > 1:
        return None, "", "", "this product's code parts write %s as %s" % (value, " and ".join(toks))
    entries = [(e["meaning"], e["code"], e["source"]) for e in segment.get("entries") or []]
    found, loose = _match(attr, value, entries)
    tok, toks = _one(found)
    if len(toks) > 1:
        return None, "", "", "the %s structure writes %s %s as %s - remove the wrong one in Nomenclature" % (
            name, label.lower(), value, " or ".join(toks))
    if tok:
        src = found[0][1]
        note = "entered in NPD" if is_entered(src) else ""
        if loose:
            return tok, UNCONFIRMED, src, "; ".join(n for n in (note, "matched by words") if n)
        return tok, CATALOGUE, src, note
    like = entries[0][1] if entries else ""
    made = _pattern(attr, value, like)
    if made and attr == "wattage":
        return made[0], CATALOGUE, "the %s structure" % name, "power written as the %s structure writes it" % name
    if made:
        shown = _shows_pattern(attr, value, entries)
        if shown:
            return made[0], PATTERN, "the %s structure" % name, \
                "written as the %s structure writes %s (%s)" % (name, shown[0], shown[1])
    return None, "", "", "the %s structure has no code for %s %s - add it in Nomenclature" % (
        name, label.lower(), value)


def structure_code(sheet, chosen, codes, parts, naming):
    """The order code for one configuration of a product a person put in a naming structure -
    the same answer shape as order_code (see the section above for the order of sources)."""
    def none(reason, missing=()):
        return {"code": "", "head": "", "status": "", "missing": list(missing), "reason": reason, "pieces": []}

    name = naming.get("structure") or ""
    segments = naming.get("segments") or []
    if not segments:
        return none("the %s naming structure has no segments yet - add them in Nomenclature" % name)
    printed = [c for c in codes if not is_example(c)]
    # A code printed on its sheet, or entered from DuraGreen's answer, for exactly this configuration
    # is DuraGreen's own and is used as it stands: the structure builds only what nobody printed.
    whole = sorted({c.code for c in printed if _states_exactly(sheet, c, chosen)})
    if len(whole) > 1:
        return none("the codes printed or entered for this configuration disagree (%s) - remove the "
                    "wrong one in NPD (DuraGreen)" % ", ".join(whole))
    if whole:
        code = whole[0]
        return {"code": code, "head": code, "status": PRINTED, "missing": [], "reason": "",
                "pieces": [{"field": "pattern", "value": "", "code": code, "status": PRINTED,
                            "source": next(c.source for c in printed if c.code == code) or "",
                            "note": "the whole code, for exactly this configuration"}]}
    values, missing, why_not = naming_values(sheet, chosen, naming)
    if missing:
        return none("choose %s first" % ", ".join(LABEL_OF.get(k, k) for k in missing), missing)
    if why_not:
        return none(why_not)
    own = defaultdict(list)
    for p in parts:
        if (p.status or "stated") == "stated":
            own[p.attribute].append((p.value, p.code, p.source or "entered for this product"))
    built, status = [], CATALOGUE
    pieces = [{"field": "structure", "value": name, "code": "", "status": CATALOGUE,
               "source": naming.get("source") or "", "note": "the naming structure this product's code follows"}]
    for seg in segments:
        attr = seg["attribute"]
        if attr not in values:
            continue
        tok, st, src, note = structure_piece(name, attr, values[attr], seg, own.get(attr, ()))
        if not tok:
            return none(note)
        built.append(tok)
        if seg.get("dash_after"):
            built.append("-")
        status = max(status, st, key=RANK.get)
        pieces.append({"field": attr, "value": values[attr], "code": tok, "status": st, "source": src, "note": note})
    code = _join(built)
    if not code:
        return none("no detail of this product is named by the %s structure yet - set them under Naming" % name)
    if code in {c.code for c in printed}:
        status = PRINTED
    reason = "" if status == PRINTED else "Built with the %s naming structure%s" % (
        name, " (entered in NPD)" if naming.get("made") else ", as DuraGreen prints it")
    if status == UNCONFIRMED:
        reason += "; " + STATUS_TEXT[UNCONFIRMED]
    return {"code": code, "head": code, "status": status, "missing": [], "reason": reason, "pieces": pieces}


def type_letters(codes):
    """The letters a product's DuraGreen code starts with - its type as DuraGreen names it (AFL,
    WFL, SBG, UL ...): the cut "type" piece of its first printed code (or its catalogue example),
    else the code's leading capitals - or, for a code that starts with a number (the 48V Mini-Line
    track's 9600-2/B-ST1), its first piece. "" for a product with no code."""
    ordered = sorted(codes, key=lambda c: (is_example(c), getattr(c, "sort_order", 0) or 0))
    for c in ordered:
        for name, token in (c.parts or []):
            if name == "type" and token:
                return token
    for c in ordered:
        m = re.match(r"[A-Z]+|[^-/\s]+", str(c.code or ""))
        if m:
            return m.group(0)
    return ""


def naming_class(codes, naming=None):
    """How DuraGreen names this product - the class the Nomenclature tab files it under:
    (class, structure). class is the structure's name for a product following a code table (its
    range's, by its catalogue page, or one a person put it in), PAGE for one its own catalogue page
    names, SHEET for one only its spec sheet's codes name, NONE for one with no code at all."""
    if naming:
        return naming.get("structure") or NONE_CLASS, naming.get("structure") or ""
    scheme = scheme_of(codes)
    if scheme["range"]:
        return scheme["range"], scheme["range"]
    return {"PAGE": PAGE, "SHEET": SHEET}.get(scheme["name"], NONE_CLASS), ""


def structure_nomenclature(sheet, codes, parts, naming):
    """nomenclature() for a product a person put in a naming structure: the structure piece by
    piece with this product's fixed letters in place, and every quoted option's code and source."""
    name = naming.get("structure") or ""
    modes = _naming_modes(naming)
    own = defaultdict(list)
    for p in parts:
        if (p.status or "stated") == "stated":
            own[p.attribute].append((p.value, p.code, p.source or "entered for this product"))
    structure, options, example = [], [], []
    for seg in naming.get("segments") or []:
        attr = seg["attribute"]
        mode = (modes.get(attr) or {}).get("mode") or ""
        if mode == "out":
            continue
        offered = [] if mode == "fixed" else _offered(sheet, attr)
        fixed_value = str((modes.get(attr) or {}).get("value") or "").strip() if mode == "fixed" else \
            (offered[0] if len(offered) == 1 else "")
        if mode != "fixed" and not offered:
            continue
        token = ""
        if fixed_value:
            token = structure_piece(name, attr, fixed_value, seg, own.get(attr, ()))[0] or "?"
        elif offered:
            token = structure_piece(name, attr, offered[0], seg, own.get(attr, ()))[0] or "?"
        structure.append({"token": token, "label": seg["segment"], "fixed": bool(fixed_value), "dash": False,
                          "field": attr})
        example.append(token)
        if seg.get("dash_after"):
            structure.append({"token": "-", "label": "", "fixed": True, "dash": True})
            example.append("-")
        if len(offered) > 1:
            values = []
            for v in offered:
                tok, st, src, note = structure_piece(name, attr, v, seg, own.get(attr, ()))
                values.append({"value": v, "code": tok or "", "status": st if tok else "none", "source": src or "",
                               "note": note or ""})
            options.append({"field": attr, "label": seg["segment"], "values": values, "how": ""})
    if structure and structure[-1]["dash"]:
        structure.pop()
    printed = [c for c in codes if not is_example(c)]
    return {"scheme": scheme_of(codes, naming), "example": _join(example), "structure": structure,
            "options": options, "order": [{"status": k, "text": t} for k, t in NAMING_ORDER],
            "printed": len(printed), "documents": [naming.get("source") or ("the %s naming structure" % name)]}


# Where a piece of a product in a naming structure comes from, surest first (structure_code).
NAMING_ORDER = [
    ("printed", "A code DuraGreen printed or sent whole for exactly this configuration"),
    ("composed", "A code part entered for this product from DuraGreen's answer"),
    ("catalogue", "The naming structure's own letters, word for word - DuraGreen's printed table, or letters "
                  "entered in NPD from what DuraGreen sent"),
    ("pattern", "The way the structure writes the values it lists - 3000K as 30, so 2700K is 27"),
    ("unconfirmed", "A meaning matched by its words only - written with \"(unconfirmed - check with DuraGreen)\""),
    ("none", "Nothing: the structure has no letters for it - the reason says which to add"),
]


# --- every combination, for a person to choose from ------------------------------------------

def vector_fields(sheet, codes, naming=None):
    """The options a product's codes are told apart by, in CONFIG_FIELDS order: the ones its
    printed rows state (a track's length and colour), and the head choices it offers (wattage,
    CCT, beam, driver) - the vector a spec sheet's code table prints by. Only fields the product
    lists options for: a field nobody can choose is not a column anybody can use. A product in a
    naming structure is told apart by the details its structure names, in the structure's order."""
    keys = {k for c in codes if not is_example(c) for k in (c.config or {})}
    if naming:
        modes = _naming_modes(naming)
        named = [s["attribute"] for s in naming.get("segments") or []
                 if s["attribute"] and (modes.get(s["attribute"]) or {}).get("mode") not in ("fixed", "out")]
        keys.update(named)
        order = named + [k for k in CONFIG_FIELDS if k not in named]
        return [k for k in order if k in keys and _offered(sheet, k)]
    keys.update(COVERED)
    if control_is_drivers(sheet, codes):
        keys.discard("control")          # a strip's control picks its driver, not its code
    return [k for k in CONFIG_FIELDS if k in keys and _offered(sheet, k)]


def vector_of(sheet, config):
    """A printed row's configuration in the product's own option words - what NPD sets the code
    tester to when a person picks that printed combination as one choice. A value that is none of
    the product's options ("?30": the sheet's table does not say which CCT) is left out, for the
    person to choose."""
    out = {}
    for k, v in (config or {}).items():
        match = next((o for o in _offered(sheet, k) if norm(o) == norm(v)), None)
        if match is not None:
            out[k] = match
    return out


def printed_choices(sheet, codes, parts=(), rules=(), naming=None):
    """Every whole code the product's sheet prints, as the one choice a person makes when quoting
    it: [{code, vector, gives, status, missing}] in the sheet's order.

    `vector` is the row's options in the product's own words (vector_of) - and, where the printed
    code ends in a finish its row does not name (ESQ0908W-30N50-W), the finish option that gives
    exactly that code, found by asking order_code itself, so the two can never disagree. `gives`
    is what order_code answers for the vector: the printed code itself, or its head while a finish
    is still to choose (`missing`), or the code DuraGreen's catalogue builds when its finish letters
    differ from the sheet's short form (the MOOD sheets print -W, the catalogue -WB). A row whose
    options are none of the product's is left out; the same code twice is offered once."""
    out, seen, by_tail = [], set(), {}
    for c in codes:
        if is_example(c) or not c.config or c.code in seen:
            continue
        vector = vector_of(sheet, c.config)
        if not vector:
            continue
        seen.add(c.code)
        found = order_code(sheet, vector, codes, parts, rules, naming=naming)
        head = found.get("head") or ""
        if found["code"] != c.code and found["missing"] and head and c.code.startswith(head):
            tail = c.code[len(head):]
            if tail not in by_tail:
                by_tail[tail] = _finish_giving(sheet, vector, found["missing"], c.code, codes, parts, rules, naming)
            if by_tail[tail]:
                trial = dict(vector, **by_tail[tail])
                again = order_code(sheet, trial, codes, parts, rules, naming=naming)
                if again["code"] == c.code and not again["missing"]:
                    vector, found = trial, again
        out.append({"code": c.code, "vector": vector, "gives": found["code"], "status": found["status"],
                    "missing": found["missing"]})
    return out


def _finish_giving(sheet, vector, missing, code, codes, parts, rules, naming=None):
    """The one option of the one finish still to choose that makes order_code give `code`
    exactly, as {field: option} - or {} when there are several finishes to choose, or no option
    (or more than one) gives it."""
    if len(missing) != 1:
        return {}
    field = missing[0]
    hits = [o for o in _offered(sheet, field)
            if order_code(sheet, dict(vector, **{field: o}), codes, parts, rules, naming=naming)["code"] == code]
    return {field: hits[0]} if len(hits) == 1 else {}


def printed_for(sheet, chosen, codes):
    """The printed codes that are exactly this configuration: a row counts only when it states
    every option the product offers a choice of, with the chosen value - the rule order_code
    keeps, so a 3000K row is never shown as the 2700K one's."""
    out = []
    drivers = control_is_drivers(sheet, codes)
    for c in codes:
        cfg = c.config or {}
        if is_example(c) or not cfg:
            continue
        ok = True
        for k, v in chosen.items():
            if k in cfg:
                ok = norm(cfg[k]) == norm(v)
            elif len(_offered(sheet, k)) > 1 and not (drivers and k == "control"):
                ok = False
            if not ok:
                break
        if ok and any(k in cfg for k in chosen):
            out.append(c.code)
    return out


def combinations(sheet, codes, parts, rules=(), limit=600, naming=None):
    """Every combination of the product's vector fields with the code it gets: printed (and the
    code DuraGreen prints for it), built - with how surely - or none, and why. What NPD shows so
    a person sees which combinations DuraGreen prints before choosing one.

    {"fields": [field keys], "total": combinations there are, "rows": [{"chosen": {...},
     "printed": [codes printed for exactly it], "code", "status", "missing": [...], "reason"}]}
    Only the first `limit` combinations are worked out; "total" says how many there are."""
    fields = vector_fields(sheet, codes, naming)
    axes = [_offered(sheet, k) for k in fields]
    total = 1
    for axis in axes:
        total *= len(axis)
    rows, memo = [], {}
    for combo in itertools.islice(itertools.product(*axes), limit if fields else 0):
        chosen = dict(zip(fields, combo))
        found = order_code(sheet, chosen, codes, parts, rules, memo=memo, naming=naming)
        rows.append({"chosen": chosen, "printed": printed_for(sheet, chosen, codes), "code": found["code"],
                     "status": found["status"], "missing": found["missing"], "reason": found["reason"]})
    return {"fields": fields, "total": total if fields else 0, "rows": rows}


# --- the logic, for a person to read ------------------------------------------------------------

# Where the app looks for each piece of a code, surest first (order_code and lookup keep this order).
LOOKUP_ORDER = [
    ("printed", "A code DuraGreen prints whole for exactly this configuration, on the spec sheet or its catalogue page"),
    ("composed", "Pieces this product's own codes and its own catalogue page print"),
    ("catalogue", "Its range's code table, as DuraGreen prints it (ACME / MOOD in the 2026 catalogue; EVOLINE, BELLA 41, "
                  "GLAMO 68, ARTFOCUS 36 in the ACME price list)"),
    ("general", "Elsewhere in its own range: another of its pages, or an earlier edition of its table for a code the "
                "newest one does not list"),
    ("pattern", "The pattern of its own printed codes - its 3000K printed as 30 (or H30), so 2700K is 27 (H27)"),
    ("unconfirmed", "Another range's table, where every one that lists it agrees - written with "
                    "\"(unconfirmed - check with DuraGreen)\" all the way onto the quotation"),
    ("none", "Nothing: DuraGreen publishes no letter for it, or its sources disagree - the reason is shown"),
]


def nomenclature(sheet, codes, parts, rules, naming=None):
    """How this product's order code is built, piece by piece - what NPD shows at the foot of the
    product: whose rule it follows, the code's structure (each piece named), and for every option
    the product offers, the code it adds and where that comes from. Worked out by the same lookups
    order_code makes, so the explanation and the codes can never disagree.

    {"scheme": scheme_of, "example": code, "structure": [{"token", "label", "fixed", "dash"}],
     "options": [{"field", "label", "how", "values": [{"value", "code", "status", "source", "note"}]}],
     "order": LOOKUP_ORDER, "printed": n, "documents": [sources used]}"""
    if naming:
        return structure_nomenclature(sheet, codes, parts, naming)
    template = next((c for c in codes if is_example(c)), None)
    printed = [c for c in codes if not is_example(c)]
    range_name = range_of(template)
    family = "" if range_name else family_of(sheet)
    shaped = [c for c in printed if c.parts]
    pattern_row = shaped[0] if shaped else (template if template is not None and template.parts else None)

    structure = []
    if pattern_row is not None:
        seq = pattern_row.parts or []
        if pattern_row is not template and template is not None:
            # the head as this product's own codes print it, then the finish the catalogue adds
            end = _head_end(seq)
            seq = list(seq[:end + 1]) + [[n, t] for n, t, _opt in _tail_pieces(template)]
        for name, token in seq:
            if name == "sep":
                structure.append({"token": token, "label": "", "fixed": True, "dash": True})
                continue
            field = PART_FIELD.get(name) or (name if name in TAIL_CHOICES else "")
            structure.append({"token": token, "label": LABEL_OF.get(field or name, name.replace("_", " ").title()),
                              "fixed": not field, "dash": False})

    own = _own_meanings(printed, parts, template, printed)
    own_by = defaultdict(list)
    for field, meaning, token, source in own:
        own_by[field].append((meaning, token, source))
    like_of = {PART_FIELD.get(n) or n: t for n, t in (pattern_row.parts if pattern_row is not None else [])}

    options, documents = [], set()
    for field in SELECTORS:
        offered = _offered(sheet, field)
        if len(offered) > 1:
            options.append({"field": field, "label": LABEL_OF.get(field, field), "values": [
                {"value": v, "code": "", "status": "", "source": "", "note": "picks which printed version's codes are used"}
                for v in offered], "how": "chooses the version - each is a different fitting"})
    tail_fields = [n for n, _t, _o in _tail_pieces(template)] if template is not None else []
    for field in ["wattage", "cct", "control", "beam_angle"] + [f for f in TAIL_CHOICES if f in tail_fields]:
        offered = _offered(sheet, field)
        if not offered:
            continue
        like = like_of.get(field, "")
        prefix = _own_prefix(field, like, printed)
        mine = [(m, t[len(prefix):] if prefix and t.startswith(prefix) else t, s) for m, t, s in own_by.get(field, [])]
        values = []
        for v in offered:
            tok, st, src, note = lookup(field, v, mine, rules, range_name, like[len(prefix):], family)
            if tok and prefix:
                tok, note = prefix + tok, "; ".join(n for n in (note, "with its own %s prefix" % prefix) if n)
            if src:
                documents.add({"code_legend": "its spec sheet's legend"}.get(src, re.sub(r"^printed code .*", "this product's printed codes", src)))
            values.append({"value": v, "code": tok or "", "status": st if tok else "none", "source": src or "",
                           "note": note or ""})
        options.append({"field": field, "label": LABEL_OF.get(field, field), "values": values, "how": ""})
    return {"scheme": scheme_of(codes), "example": pattern_row.code if pattern_row is not None else "",
            "structure": structure, "options": options,
            "order": [{"status": k, "text": t} for k, t in LOOKUP_ORDER],
            "printed": len(printed), "documents": sorted(documents)}


def describe(result) -> str:
    """The Product Code line a BOQ description carries for a found code. An unconfirmed code
    says so in the line itself, so the warning travels with it onto a quotation."""
    if not result or not result.get("code"):
        return ""
    # A code still waiting for its finish is only its head (AFL7503W-30N05 of AFL7503W-30N05-W):
    # nothing DuraGreen prints, so it stays off the line until the finish is chosen - the same rule
    # that keeps a partial Exenia code off it. Until 22 Oct 2026 the head was written plain.
    if result.get("missing"):
        return ""
    # How each status reaches the line (decided 21 Oct 2026). The line is copied into the
    # description, and from there onto the client's quotation and the purchase order, so it says
    # only what DuraGreen would say:
    #   PRINTED, COMPOSED, CATALOGUE - DuraGreen's own printed codes, pieces or published range
    #     table: written plain.
    #   PATTERN (22 Oct 2026) - a CCT or beam written the way this product's own printed codes
    #     write the ones they print (its 3000K is 30, so its 2700K is 27). The product's sheet is
    #     the evidence for the form, so it is written plain too.
    #   GENERAL - elsewhere in the product's own range: the range's colour table for a structure
    #     colour, or another page of the same range. Written plain too.
    #     (Until 22 Oct 2026 GENERAL also meant "ACME's table, for a product of any range", and
    #     was written plain. The owner ruled that out - ACME's nomenclature is the ACME
    #     downlights', not a step light's - so a piece from another range's table or page is now
    #     UNCONFIRMED, below.)
    #   UNCONFIRMED - follows DuraGreen's pattern but is printed nowhere. Left off, the line would
    #     hide what the studio knows; written plain, a guess would read as a part number (the rule
    #     that keeps a partial Exenia code off the line, pdf_parser.build_description). So it is
    #     written with its warning attached, which travels with it - to DuraGreen too, on the
    #     purchase order, who can confirm it.
    if result["status"] == UNCONFIRMED:
        return result["code"] + " (unconfirmed - check with DuraGreen)"
    return result["code"]
