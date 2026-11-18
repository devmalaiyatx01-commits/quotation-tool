"""New Product Development - authoring a product, rather than reading one out of a PDF.

Everywhere else in this app a Specsheet is a *reading* of a document somebody else produced,
and the pricing is a second, separate reading of a price sheet, joined to it by hand. NPD
inverts that: the product is defined here, and its specsheet PDF is generated from the
definition. That means the configuration a BOQ line can be ordered in and the money that
configuration costs come from one place and cannot drift apart, which is the whole reason for
the tab.

Three jobs live here:

  * assemble()  - turn what the form collected into the same `params`/`fields`/`options`
                  shapes the parser produces, so an authored product is indistinguishable
                  from a parsed one to the Library, Match, Configurator and every export.
  * price_for() - base price plus the adder for each chosen option. Kept as base + adders
                  rather than a price per whole configuration because the combinations
                  multiply: four wattages, three CCTs, four beams and two finishes is 96
                  configurations and 13 numbers.
  * generate_specsheet_pdf() - the InHouse-format sheet, laid out from the same definition.
"""
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

# The four banners a InHouse sheet is divided into, in the order they are printed. A template
# may name others; anything not in this list is printed after these, in template order, rather
# than dropped - a typology the studio describes differently is not an error.
GROUP_ORDER = [
    "MECHANICAL PARAMETERS",
    "OPTICAL PARAMETERS",
    "SOURCE PARAMETERS",
    "POWER SUPPLY PARAMETERS",
]

# Identity is not a specification. A parameter says what the product IS - 12W, IP65, 3000K;
# an identity field says which product it is - the series, the size, the optic - and its job is
# to name and to code it. So it is held as a parameter group like any other (one template row
# per field, per typology, which is how the studio's vocabulary grows through use), but it is
# printed where the product is named - the second black bar - rather than in the parameter
# columns, and it never appears among them.
IDENTITY_GROUP = "IDENTITY"

# Where a product keeps its specsheet name line when it is held as written rather than composed
# from the series and family - see identity_bits().
NAME_LINE_KEY = "name_line"

# How a multi-option value is written on a specsheet: "2700K / 3000K / 4000K". The parser
# splits on the same separator (Specsheet.options), so a generated sheet re-read by the parser
# would yield back the option list it was generated from.
OPTION_SEPARATOR = " / "


def _per_metre(text, i):
    """Whether the "/" at text[i] is the unit in "10W/M" - watts per metre - rather than a
    separator between two options."""
    before = text[:i].rstrip()
    return before[-1:] in ("W", "w") and re.match(r"\s*[Mm](?![A-Za-z])", text[i + 1:]) is not None


def split_options(value):
    """The options in a printed value. "WHITE / BLACK" -> ["WHITE", "BLACK"].

    A strip light's wattage is per metre - "7.2W/M / 9.6W/M" is two options, not four. Split on
    every "/" it read as ["7.2W", "M", "9.6W", "M"], which is how 30 InHouse strips came to
    offer a wattage called "M" and never matched the prices recorded for "9.6W/M".

    A "/" inside brackets qualifies one option rather than separating two: "SURFACE ( FIXED /
    ADJUSTABLE )" is one mounting, whose bracket the adjustability parameter offers as its own
    choice. Split there, it offered a mounting called "ADJUSTABLE )". The parsed catalogues
    already read brackets this way (pdf_parser keeps "7W CV (24V/48V)" whole).

    A value with no "/" between options may list them with commas - "2700K, 3000K, 4000K" on 22
    InHouse strips and bands - and is read that way too, as the PDF reader always has. A comma
    between two digits is a thousands mark ("50,000hrs"), not a separator."""
    if value is None:
        return []
    parts = _split_at(str(value), lambda text, i, ch: ch == "/" and not _per_metre(text, i))
    if len(parts) == 1:
        parts = _split_at(parts[0], lambda text, i, ch: ch == "," and not _thousands(text, i))
    return parts


def _split_at(text, is_separator):
    """The parts of `text` between separators, never splitting inside brackets."""
    parts, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)       # a stray ")" - "4000K)" - opens nothing
        elif depth == 0 and is_separator(text, i, ch):
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _thousands(text, i):
    """Whether the comma at text[i] sits between two digits - 1,00,000 or 50, 000."""
    before, after = text[:i].rstrip(), text[i + 1:].lstrip()
    return before[-1:].isdigit() and after[:1].isdigit()


def assemble(templates, values, options_by_key=None, configurable_keys=None):
    """(params, fields, options) for a product, from its typology's template and the values
    entered against it.

    `templates` are ParameterTemplate rows in print order; `values` maps field_key -> the
    printed value ("WHITE / BLACK"); `options_by_key` optionally gives the option list
    explicitly, for a configurable parameter whose options were entered as a list rather than
    typed as one string.

    params is grouped for printing, fields is flat for querying, options is flat lists for the
    Configurator's dropdowns - the same three shapes app/pdf_parser.py produces, so nothing
    downstream needs to know which way a product arrived.
    """
    options_by_key = options_by_key or {}
    params, fields, options = {}, {}, {}

    # Which parameters offer a choice is decided per product, not only by the typology's
    # template: the form asks it on each parameter ("affects price"), and this product may
    # legitimately disagree with the template - a finish that is fixed on one luminaire and
    # a choice on the next. Falls back to the template's own flag when the caller says
    # nothing, which is what a product created before this was asked looks like.
    def is_choice(t):
        if configurable_keys is None:
            return bool(t.is_configurable)
        return bool(configurable_keys.get(t.field_key, t.is_configurable))

    for t in templates:
        raw = values.get(t.field_key)
        if raw is None or str(raw).strip() == "":
            continue
        printed = str(raw).strip()
        params.setdefault(t.group_name, {})[t.label] = printed
        # One key, one value. Where a InHouse sheet prints the same word under two banners but
        # means two different quantities - POWER as the LED's draw under Source and the
        # driver's rating under Power Supply - the template gives them separate keys, so this
        # is genuinely one fact per key rather than a collision being resolved.
        fields.setdefault(t.field_key, printed)
        if is_choice(t):
            opts = options_by_key.get(t.field_key) or split_options(printed)
            if len(opts) > 1 or t.field_key in options_by_key:
                options.setdefault(t.field_key, opts)

    return params, fields, options


def _power_supply_adder(specsheet, control):
    """The power supply's own price for the control chosen.

    It is a separate physical component with its own price, not a different price for the
    same fixture - SOLARA Midi's is Rs N with On/Off and Rs N with Dali - so it is
    added whichever way the fixture itself is priced. Mirrors resolvePowerSupplyAdder() in
    static/app.js; the two must agree or the Configurator and this endpoint would quote
    different money for the same configuration.
    """
    def norm(text):
        return "".join(c for c in str(text or "").upper() if c.isalnum())

    if specsheet is None or not control:
        return 0.0
    for entry in (specsheet.price_entries or []):
        cfg = entry.config or {}
        if cfg.get("accessory") and norm(cfg["accessory"]) == "POWERSUPPLY"            and norm(cfg.get("control")) == norm(control):
            return float(entry.price)
    return 0.0


def price_for(specsheet, config):
    """What one configuration of an authored product costs.

    base price + the adder for each chosen option. A value with no adder recorded costs
    nothing extra, which is the default and the common case: most options on most products are
    the same money. Returns None when the product has no base price yet, so a caller can tell
    "not priced" from "priced at zero" - a BOQ line defaulting to 0 silently would be the one
    mistake worth avoiding here.
    """
    if specsheet is None or specsheet.base_price is None:
        return None
    total = float(specsheet.base_price)
    adders = specsheet.price_adders or {}
    for key, chosen in (config or {}).items():
        if chosen in (None, ""):
            continue
        per_value = adders.get(key)
        if isinstance(per_value, dict):
            try:
                total += float(per_value.get(str(chosen), 0) or 0)
            except (TypeError, ValueError):
                pass
    total += _power_supply_adder(specsheet, (config or {}).get("control"))
    return round(total, 2)


def priced_by_rule(specsheet):
    """Whether a product is priced by base + adders: one authored in NPD, or a DuraGreen product,
    whose prices are entered in NPD (DuraGreen) the same way. Every other catalogue row keeps its
    price the way it always has - set in the BOQ, or from a mapped price entry. Mirrors
    pricedByRule() in static/app.js."""
    if specsheet is None:
        return False
    from .duragreen import is_duragreen
    return specsheet.origin == "npd" or is_duragreen(specsheet)


def match_price(specsheet, chosen):
    """The price a product added from the Match tab gets, and any choice it still depends on.

    The same money the Configurator would show for the same choices: price_for() over the options
    the requirement settled, plus any option the product only comes in one way (the Configurator
    pre-selects those). Nothing else is assumed - an option the requirement did not settle adds
    nothing, exactly as a Configurator dropdown left on "Select..." adds nothing.

    Returns (price, open). `price` is None for a product not authored in NPD, which keeps its
    price the way it always has: set in the BOQ. `open` lists each option the requirement left
    unsettled that WOULD change the price - {field: {option: extra money}} - so the page can show
    the choice and its cost instead of quietly pricing one of them. The power supply counts: its
    price depends on the control, so an unsettled control with differently priced supplies is
    such a choice too."""
    if not priced_by_rule(specsheet) or specsheet.base_price is None:
        return None, {}
    options = specsheet.options or {}
    config = dict(chosen or {})
    for key, values in options.items():
        if key not in config and isinstance(values, list) and len(values) == 1:
            config[key] = values[0]
    price = price_for(specsheet, config)
    unsettled = [k for k in (specsheet.price_adders or {}) if k not in config]
    has_supply = any((e.config or {}).get("accessory") and "".join(c for c in str(e.config["accessory"]).upper()
                     if c.isalnum()) == "POWERSUPPLY" for e in (specsheet.price_entries or []))
    if has_supply and "control" not in config:
        unsettled.append("control")
    open_choices = {}
    for key in unsettled:
        extra = {}
        for option in options.get(key) or []:
            extra[str(option)] = round(price_for(specsheet, {**config, key: option}) - price, 2)
        if len(set(extra.values())) > 1:
            open_choices[key] = extra
        elif extra and next(iter(extra.values())):
            # Every option costs the same extra: no choice to make, but the money is real.
            price = round(price + next(iter(extra.values())), 2)
    return price, open_choices


def lowest_price(specsheet):
    """What a product costs at its cheapest, by match_price()'s own rules: the least a person could be
    quoted, whichever options they then choose, or None for no price. A Filo S prices by its IP alone,
    so it starts at the cheapest IP. A base of 0 with nothing unavoidable is no price: 81 InHouse
    products are like that, and a quotation must not read them as free."""
    if not priced_by_rule(specsheet) or specsheet.base_price is None:
        return None
    options = specsheet.options or {}
    config = {k: v[0] for k, v in options.items() if isinstance(v, list) and len(v) == 1}
    total = price_for(specsheet, config)
    fields = {k for k in (specsheet.price_adders or {}) if k not in config}
    if "control" not in config and any((e.config or {}).get("accessory") for e in (specsheet.price_entries or [])):
        fields.add("control")
    for key in fields:
        extras = [price_for(specsheet, {**config, key: option}) - price_for(specsheet, config) for option in options.get(key) or []]
        if extras:
            total += min(extras)
    return round(total, 2) if total > 0 else None


def price_breakdown(specsheet, config):
    """The same sum, itemised - what the Configurator shows so a price can be understood
    rather than just accepted."""
    if specsheet is None or specsheet.base_price is None:
        return None
    lines = [{"label": "Base price", "amount": float(specsheet.base_price)}]
    adders = specsheet.price_adders or {}
    for key, chosen in (config or {}).items():
        if chosen in (None, ""):
            continue
        per_value = adders.get(key)
        if not isinstance(per_value, dict):
            continue
        try:
            amount = float(per_value.get(str(chosen), 0) or 0)
        except (TypeError, ValueError):
            continue
        if amount:
            lines.append({"label": "%s: %s" % (key.replace("_", " ").title(), chosen),
                          "amount": amount})
    supply = _power_supply_adder(specsheet, (config or {}).get("control"))
    if supply:
        lines.append({"label": "Power supply: %s" % (config or {}).get("control"),
                      "amount": supply})
    return {"lines": lines, "total": round(sum(l["amount"] for l in lines), 2)}


# ---------------------------------------------------------------- the product's identity
#
# A InHouse sheet's first black bar reads "Recessed LED Downlight - Adjustable - With Trim".
# That is not a sentence somebody typed: it is the product's identity spelled out, and every
# part of it is held separately -
#
#     Recessed    sub-typology     how it is installed
#     Downlight   typology         what kind of luminaire it is
#     Adjustable  adjustability    a parameter already on the sheet
#     With Trim   trim details     a parameter already on the sheet
#
# Composing the line rather than storing it keeps those parts addressable, which matters
# beyond this page. FieldCode already maps an option value to a short code for the generated
# product code (LL-WH-30-24...), and identity parts become codeable the same way once each has
# a row: a sub-typology cannot contribute "RC" to a code while it exists only inside a
# sentence. Structured now so inventory and ordering can read the same parts later.
TITLE_SEPARATOR = " – "          # en dash, as the printed sheet uses


def compose_title(sub_typology, typology, fields):
    """The description line the sheet prints, built from the parts that make it up."""
    fields = fields or {}
    head = " ".join(x for x in [(sub_typology or "").strip(),
                                "LED",
                                (typology or "").strip()] if x)
    tail = []
    for key in ("adjustability", "trim_details"):
        v = str(fields.get(key) or "").strip()
        # A multi-option value describes a choice, not this product's identity, so it stays
        # out of the name - "FIXED / ADJUSTABLE" would make nonsense of the line.
        if v and "/" not in v:
            tail.append(v.title())
    return TITLE_SEPARATOR.join([head] + tail) if head else ""


def title_parts(sub_typology, typology, fields):
    """The same line, itemised - what the form shows, so the composition is visible rather
    than something the reader has to infer from the result."""
    fields = fields or {}
    out = [("Sub-typology", (sub_typology or "").strip()),
           ("Typology", (typology or "").strip())]
    for key, label in (("adjustability", "Adjustability"), ("trim_details", "Trim details")):
        v = str(fields.get(key) or "").strip()
        out.append((label, v if v and "/" not in v else ""))
    return out


# ---------------------------------------------------------------- the generated specsheet
#
# Drawn to the InHouse page rather than to a layout of our own: the generated sheet is what
# the Configurator reads a product from, so it has to be the same document a parsed InHouse
# sheet is. Every measurement below was taken off a real one (Nyx Series__2.pdf, page 1)
# rather than chosen, which is why they are odd numbers.

PAGE_W, PAGE_H = 540.0, 780.0
X0, X1 = 23.9, 517.7
HDR_Y0, HDR_Y1 = 9.1, 67.4
BAR1_Y0, BAR1_Y1 = 70.5, 80.0
BAR2_Y0, BAR2_Y1 = 81.7, 91.3
LEFT_X0, LEFT_X1 = 23.8, 241.4
RIGHT_X0, RIGHT_X1 = 246.2, 517.7
SPLIT_X = 381.9
ROW_H = 15.7
BANNER_H = 9.8
IMG_BOX = (94.9, 287.3)          # PRODUCT IMAGE box, banner included
DIM_BOX = (294.0, 461.7)         # DIMENSION box, banner included
FOOT_Y0, FOOT_Y1 = 748.8, 764.5

# sampled off the original sheet rather than guessed - the band and the crop behind the
# logo have to be the same grey, or the mark sits on a visible patch
BAND = colors.HexColor("#BEBFBE")
LINE = colors.HexColor("#525252")
BLACK = colors.black

ASSETS = Path(__file__).parent / "assets"

# GROUP_ORDER is declared once, near assemble() at the top of this file - it was declared a
# second time here with the same four names, which changed nothing but meant an edit to one of
# them would have been silently overridden by the other.

FOOTNOTE = "(i) Delivered lumen output value is based on 3000K"


def _fit(c, path, x0, top, x1, bottom, pad=6):
    """Draw a picture inside a box, centred, keeping its proportions.

    The box is given in the page's own top-down coordinates, like every measurement in this
    module, and converted here - ReportLab measures from the bottom, and mixing the two puts a
    picture the height of the page away from where it belongs."""
    try:
        img = ImageReader(str(path))
        iw, ih = img.getSize()
    except Exception:
        return False
    bw, bh = (x1 - x0) - 2 * pad, (bottom - top) - 2 * pad
    if iw <= 0 or ih <= 0 or bw <= 0 or bh <= 0:
        return False
    s = min(bw / iw, bh / ih)
    w, h = iw * s, ih * s
    c.drawImage(img, x0 + (x1 - x0 - w) / 2,
                PAGE_H - bottom + ((bottom - top) - h) / 2, w, h, mask="auto")
    return True


def _band(c, x0, y0, x1, y1, label, size=6.0, bold=True):
    """A grey banner with its label centred - PRODUCT IMAGE, MECHANICAL PARAMETERS, and so on."""
    c.setFillColor(BAND)
    c.rect(x0, PAGE_H - y1, x1 - x0, y1 - y0, stroke=0, fill=1)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.4)
    c.rect(x0, PAGE_H - y1, x1 - x0, y1 - y0, stroke=1, fill=0)
    c.setFillColor(BLACK)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawCentredString((x0 + x1) / 2, PAGE_H - y1 + (y1 - y0) / 2 - size * 0.36, label)


def _bar(c, y0, y1, text, size=6.6, right_text=""):
    """One of the two black title bars.

    `right_text` is set flush to the bar's right edge, which is where the model number goes:
    it names the orderable thing rather than describing it, so it reads as a code beside the
    name instead of being run into the middle of it.
    """
    c.setFillColor(BLACK)
    c.rect(X0, PAGE_H - y1, X1 - X0, y1 - y0, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", size)
    baseline = PAGE_H - y1 + (y1 - y0) / 2 - size * 0.36
    c.drawCentredString((X0 + X1) / 2, baseline, text or "")
    if right_text:
        c.drawRightString(X1 - 6, baseline, str(right_text))


def _wrapped(c, text, x, y_top, width, size, leading):
    """Draw a value, wrapping onto a second line where the cell is too narrow - which the
    original does for a long accessories list."""
    c.setFont("Helvetica", size)
    words = str(text).split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if c.stringWidth(trial, "Helvetica", size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lines = lines[:2]
    for i, ln in enumerate(lines):
        c.drawString(x, y_top - i * leading, ln)
    return len(lines)


def ordered_groups(params):
    """The parameter groups to print, in order. Identity is not one of them - it is printed in
    the name bar, by identity_bits() below."""
    rest = [g for g in params if g not in GROUP_ORDER and g != IDENTITY_GROUP]
    return [g for g in GROUP_ORDER if g in params] + rest


def identity_bits(sheet) -> list:
    """The identity values that name this product, in the order its typology defines them.

    Series and family first, because that is how a InHouse sheet has always named a product;
    then whatever else the typology's identity template adds. Anything blank is left out rather
    than printed as an empty gap, and the model number is not here - it is set flush right, on
    its own, by the bar itself.
    """
    # A name line held as written wins over composing one. The series used to carry the
    # product's shape, lamp count and form ("NYX Dual Square"); once those moved to fields of
    # their own the composed bar would have printed plain "NYX - Midi", so the line each sheet
    # already printed was kept verbatim. See scripts/backfills/structure_inhouse_catalogue.py.
    held = str((sheet.fields or {}).get(NAME_LINE_KEY) or "").strip()
    if held:
        return [held]
    bits = [b for b in [(sheet.series or "").upper(), (sheet.family or "")] if b]
    identity = (sheet.params or {}).get(IDENTITY_GROUP) or {}
    for label, value in identity.items():
        text = str(value or "").strip()
        # A multi-option value is a choice a buyer makes, not this product's identity - printing
        # "MEDIUM / WIDE" in the name would name two products at once.
        if text and "/" not in text and text.upper() not in [b.upper() for b in bits]:
            bits.append(text)
    return bits


def pick_images(images):
    """(product photo, line diagram) out of a product's pictures, by what they are labelled.

    Matched on words within the label rather than on the label itself, because the two sources
    of pictures name them differently and both are legitimate: a product authored here has a
    "Product Image" and a "Line Diagram", while one read from a manufacturer's PDF has
    whatever the parser called them - "Additional Diagram 2" is a real and common example.
    Substring matching reads both. Returns None for a picture the product does not have.

    Shared with the API so the edit form shows the same two pictures the generated sheet
    prints. They were matched separately before, the form on the exact label, and every
    migrated product's line diagram was invisible in the form while appearing on its sheet.
    """
    by_label = {}
    for path, label in (images or []):
        by_label.setdefault((label or "").strip().lower(), path)

    def _pick(*words):
        for key, path in by_label.items():
            if any(w in key for w in words):
                return path
        return None

    product_img = _pick("product", "image", "photo")
    dim_img = _pick("dimension", "diagram", "drawing", "line")
    if dim_img == product_img:
        dim_img = None
    return product_img, dim_img


# A product's dimension drawings: the main one (what the parser calls "Additional Diagram 2", or an
# older NPD upload's "Line Diagram") and any added in NPD as more of them, "Dimension 2", 3, ...
# Some sheets print several side by side in their DIMENSION box (owner, 18 Nov 2026). The parser's
# other pictures ("Additional Diagram 3" is an accessory collage on MIST 0808 3D) are not drawings
# and are never printed there.
MAIN_DIMENSION_LABELS = ("additional diagram 2", "line diagram")
MORE_DIMENSION_LABEL = re.compile(r"^\s*dimension\s+(\d+)\s*$", re.I)


def dimension_order(label):
    """Where a dimension drawing sorts: (0, 0) for the main one, (1, n) for "Dimension n". None for
    any other picture."""
    text = str(label or "").strip().lower()
    if text in MAIN_DIMENSION_LABELS:
        return (0, 0)
    m = MORE_DIMENSION_LABEL.match(text)
    return (1, int(m.group(1))) if m else None


def dimension_images(images):
    """Every dimension drawing among [(path, label)], main first - the DIMENSION box prints them
    all. Falls back to pick_images' one for a product whose labels follow no numbering."""
    # The main drawing is the one pick_images has always chosen - so a product carrying both the
    # parser's "Additional Diagram 2" and an older upload's "Line Diagram" prints one, as before -
    # then every drawing added as "Dimension N", in order.
    main = pick_images(images)[1]
    extras = sorted((dimension_order(label)[1], path) for path, label in (images or [])
                    if path and (dimension_order(label) or (0,))[0] == 1)
    paths = [main] if main else []
    for _n, path in extras:
        if path not in paths:
            paths.append(path)
    return paths


def _fit_many(c, paths, x0, top, x1, bottom):
    """Several pictures in one box, in a grid: one fills it, two sit side by side (or one above
    the other for wide drawings), more go two or three to a row."""
    n = len(paths)
    if n == 1:
        _fit(c, paths[0], x0, top, x1, bottom, pad=2)
        return
    cols = 2 if n <= 4 else 3
    rows = (n + cols - 1) // cols
    w, h = (x1 - x0) / cols, (bottom - top) / rows
    for i, p in enumerate(paths):
        r, k = divmod(i, cols)
        _fit(c, p, x0 + k * w + 2, top + r * h + 2, x0 + (k + 1) * w - 2, top + (r + 1) * h - 2, pad=2)


def generate_specsheet_pdf(sheet, out_path, images=None):
    """Write the InHouse-format sheet for an authored product.

    `images` is [(path, label), ...]; the one labelled like a product photograph goes in the
    PRODUCT IMAGE box and anything dimensional goes in the DIMENSION box, which is how a
    InHouse page is laid out. A box with nothing for it is drawn empty rather than removed -
    the sheet keeps its shape whether or not every picture exists yet.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(out_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle("%s - Specsheet" % (sheet.product_name or "Product"))

    # --- header band with the maker's mark -------------------------------------------
    c.setFillColor(BAND)
    c.rect(X0, PAGE_H - HDR_Y1, X1 - X0, HDR_Y1 - HDR_Y0, stroke=0, fill=1)
    logo = ASSETS / "inhouse_header.png"
    if logo.exists():
        _fit(c, logo, X0 + 14, HDR_Y0 + 6, X0 + 190, HDR_Y1 - 6, pad=0)

    # --- the two black bars ------------------------------------------------------------
    # The first is the product's own description - the same line the parsed sheet carries in
    # `category`. The second is how the product is named on the page: series, then family.
    _bar(c, BAR1_Y0, BAR1_Y1, sheet.category or "")
    name_bits = identity_bits(sheet)
    # The model number sits to the right of the name, as its own field. InHouse's own sheets
    # print it inside the name - "RASO-1 (LL 139017)" - which reads fine on paper but means the
    # code is a substring of the series and belongs to no field at all: the edit form showed
    # Model Number empty while the sheet plainly carried one. Held separately it can be
    # corrected, searched, and assembled from the short code each identity field carries.
    _bar(c, BAR2_Y0, BAR2_Y1, " - ".join(name_bits) or (sheet.product_name or ""),
         right_text=(sheet.fields or {}).get("model_no") or "")

    # --- left column: the two picture boxes --------------------------------------------
    product_img, _dim_img = pick_images(images)
    dims = [p for p in dimension_images(images) if p != product_img]

    for (top, bottom), label, imgs in ((IMG_BOX, "PRODUCT IMAGE", [product_img] if product_img else []),
                                       (DIM_BOX, "DIMENSION", dims)):
        c.setStrokeColor(LINE)
        c.setLineWidth(0.4)
        c.rect(LEFT_X0, PAGE_H - bottom, LEFT_X1 - LEFT_X0, bottom - top, stroke=1, fill=0)
        _band(c, LEFT_X0, top, LEFT_X1, top + BANNER_H, label)
        if imgs:
            _fit_many(c, imgs, LEFT_X0 + 4, top + BANNER_H + 4, LEFT_X1 - 4, bottom - 4)

    # --- right column: the parameter groups --------------------------------------------
    y = 94.9
    params = sheet.params or {}
    for group in ordered_groups(params):
        rows = params.get(group) or {}
        if not rows:
            continue
        _band(c, RIGHT_X0, y, RIGHT_X1, y + ROW_H, group, size=6.2)
        y += ROW_H
        for label, value in rows.items():
            if y + ROW_H > FOOT_Y0 - 6:
                break
            c.setStrokeColor(LINE)
            c.setLineWidth(0.4)
            c.rect(RIGHT_X0, PAGE_H - (y + ROW_H), SPLIT_X - RIGHT_X0, ROW_H, stroke=1, fill=0)
            c.rect(SPLIT_X, PAGE_H - (y + ROW_H), RIGHT_X1 - SPLIT_X, ROW_H, stroke=1, fill=0)
            c.setFillColor(BLACK)
            c.setFont("Helvetica", 5.6)
            c.drawString(RIGHT_X0 + 4, PAGE_H - y - ROW_H + 6.0, str(label))
            # a value too long for its cell wraps onto a second line inside the same row,
            # which is what the printed sheet does with a long accessories list
            _wrapped(c, value if value not in (None, "") else "",
                     SPLIT_X + 4, PAGE_H - y - 7.4, RIGHT_X1 - SPLIT_X - 8, 5.6, 6.2)
            y += ROW_H
        y += 2.2                                    # the gap the original leaves between groups

    # --- footer -------------------------------------------------------------------------
    c.setFillColor(BAND)
    c.rect(X0, PAGE_H - FOOT_Y1, X1 - X0, FOOT_Y1 - FOOT_Y0, stroke=0, fill=1)
    mark = ASSETS / "inhouse_mark.png"
    if mark.exists():
        _fit(c, mark, X1 - 34, FOOT_Y0 - 10, X1 - 6, FOOT_Y1 + 2, pad=0)
    c.setFillColor(BLACK)
    c.setFont("Helvetica", 5.2)
    c.drawString(X0 + 6, PAGE_H - FOOT_Y1 - 8.4, FOOTNOTE)

    c.showPage()
    c.save()
    return out_path





# The shape of a InHouse specsheet, as the studio's own sheets actually write it: the
# parameters that appear on at least 60% of InHouse typologies, in printed order. A typology
# created by hand starts from this rather than from nothing, because a blank sheet is not a
# useful place to begin describing a luminaire - and because everything the studio issues
# should read the same way whether or not there were products of that kind before.
#
# Derived from the library (the same reading seed_parameter_templates.py does) and then fixed
# here, so creating a typology does not depend on which products happen to be loaded today.
STARTER_TEMPLATE = [
    ("MECHANICAL PARAMETERS", "CUTOUT DIMENSIONS", "cutout_dimensions", False),
    ("MECHANICAL PARAMETERS", "MATERIAL", "material", False),
    ("MECHANICAL PARAMETERS", "FINISH", "finish", True),
    ("MECHANICAL PARAMETERS", "ADJUSTABILITY", "adjustability", True),
    ("MECHANICAL PARAMETERS", "IP RATING", "ip_rating", True),
    ("MECHANICAL PARAMETERS", "IK RATING", "ik_rating", False),
    ("MECHANICAL PARAMETERS", "OPERATING TEMPERATURE", "operating_temperature", False),
    ("OPTICAL PARAMETERS", "OPTICS TYPE", "optics_type", True),
    ("OPTICAL PARAMETERS", "REFLECTOR FINISH", "reflector_finish", True),
    ("OPTICAL PARAMETERS", "BEAM ANGLE", "beam_angle", True),
    ("OPTICAL PARAMETERS", "UGR", "ugr", False),
    ("OPTICAL PARAMETERS", "ACCESSORIES", "accessories", True),
    ("SOURCE PARAMETERS", "SOURCE", "source", False),
    ("SOURCE PARAMETERS", "COLOR CONSISTENCY", "color_consistency", False),
    ("SOURCE PARAMETERS", "LUMEN MAINTENANCE", "lumen_maintenance", False),
    ("SOURCE PARAMETERS", "ACTUAL POWER", "wattage", True),
    ("SOURCE PARAMETERS", "CCT", "cct", True),
    ("SOURCE PARAMETERS", "LUMEN PACKAGE", "lumen_package", False),
    ("SOURCE PARAMETERS", "CRI", "cri", True),
    ("POWER SUPPLY PARAMETERS", "BALLAST CONTROL GEAR", "control_gear", False),
    ("POWER SUPPLY PARAMETERS", "C.C / C.V", "c_c_c_v", False),
    ("POWER SUPPLY PARAMETERS", "INPUT VOLTAGE", "input_voltage", False),
    ("POWER SUPPLY PARAMETERS", "POWER FACTOR", "power_factor", False),
    ("POWER SUPPLY PARAMETERS", "T.H.D", "t_h_d", False),
    # driver_ip_rating, not ip_rating: this is the driver's rating, and the luminaire's own is
    # the MECHANICAL row above. Sharing one key made the form treat them as a single value, so
    # typing IP67 for the luminaire also rated its IP20 driver IP67 (fixed 2026-09-23).
    ("POWER SUPPLY PARAMETERS", "IP RATING", "driver_ip_rating", True),
]


# A parameter's key is what its value is stored under - in the form, in a product's `fields`
# and options, and in its price adders. Two template rows with one key are therefore one value
# printed twice, which is right only if they really are the same fact. A name repeated under a
# different banner usually is not: IP RATING under MECHANICAL is the luminaire's, under POWER
# SUPPLY it is the driver's. So a key is only ever shared by accident, and these keep it from
# happening.
#
# The library's own two cases keep the names seed_parameter_templates.py already gave them,
# so a typology built by hand stores the driver's rating where a seeded one does.
GROUP_SCOPED_KEYS = {
    ("POWER SUPPLY PARAMETERS", "power"): "driver_power",
    ("POWER SUPPLY PARAMETERS", "ip_rating"): "driver_ip_rating",
}


def key_from_label(text):
    """"IP RATING" -> "ip_rating"; "C.C / C.V" -> "c_c_c_v"."""
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


def distinct_field_key(group_name, label, taken):
    """The key for a new parameter `label` under `group_name`, never one already in `taken`
    (every key the typology's template uses).

    Tries, in order: the label's own key; the library's name for it under this group (above);
    the key prefixed with its group ("power_supply_ip_rating"); then a number. Never returns a
    key already taken, so a new parameter can never silently start sharing another's value."""
    base = key_from_label(label) or "parameter"
    scoped = GROUP_SCOPED_KEYS.get((str(group_name or "").strip().upper(), base))
    group = re.sub(r"_parameters$", "", key_from_label(group_name)) or "group"
    if (scoped or base) not in taken:
        return scoped or base
    candidate = group + "_" + base
    n = 2
    while candidate in taken:
        candidate = "%s_%s_%d" % (group, base, n)
        n += 1
    return candidate
