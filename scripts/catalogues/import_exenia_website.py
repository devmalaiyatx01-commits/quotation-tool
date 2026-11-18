"""Points the Exenia rows at Exenia's own product pages, and imports the products the
printed catalogue never gave us.

Why the catalogue is the wrong source of truth
----------------------------------------------
The 2026 catalogue prints eight products to a page, so every Exenia row's "original PDF"
opened a page describing seven other luminaires as well - useless for checking one BOQ
line against. Exenia publishes a page per product
(https://www.exenia.eu/en/product/atollo-ceiling/), and that is what a specifier checks.
Each row now records it in `source_url`, which the Library's Details view links directly.

What the product pages carry that the catalogue index lines do not
------------------------------------------------------------------
  - a breadcrumb stating Exenia's own classification: Indoor/Outdoor > typology > family
  - a specification table (isolation class, protection class, lifetime, incandescent wire)
  - Exenia's REAL configurator. It is a WooCommerce composite product, so each step is a
    `.composite_component` whose data-nav_title is the step name and whose options sit in
    data-options_data as JSON. That matters beyond convenience: the couplings this project
    had to reconstruct from the catalogue's photometric rows are stated outright there as
    single options - "Narrow Spot 11 - 8,2W" is one choice, not a beam crossed with a
    wattage, and "CRI92 2700K" is one choice, not a CRI crossed with a temperature.

One row per version
-------------------
The catalogue prints DARK FIX COMPACT as one entry, but Exenia sells it as four: round
trim, round trimless, square trim, square trimless. Those are different apertures and
different cut-outs, not a finish - the same distinction InHouse draws by giving each its
own specsheet. So the "Version" step becomes `variant` and each of its values becomes its
own library row, while every other step (finish, optic, colour, control, accessories)
stays a dropdown on that row. 211 product pages produce 424 rows.

Existing rows are reused rather than replaced - the first version of a product keeps that
product's row and id, so the BOQ items pointing at it still resolve - and every matched row
takes the website's fields and options. The catalogue parse gave those rows four axes;
Exenia's own configurator gives them seven or eight, which is why a version could not be
chosen before. Catalogue rows with no product page at all are left exactly as they were.

Coupled steps are split into the fields they name, with the coupling written as a rule
(restructure_coupled_field_values.py owns those): "CRI92 3000K" into cri + cct, and
"Narrow Spot 11 - 8,2W" into beam_angle + wattage. Both scripts produce the same shape, so
running either after the other changes nothing.

Only 6 products publish a single-product datasheet PDF ("...-FV.pdf"); where one exists it
replaces the shared catalogue page, otherwise the catalogue page stays as a secondary
reference and the product page is the primary one.

Fetches are cached under data/exenia_cache/; pass --refresh to ignore it. Dry run by
default; pass --commit to write.
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
import concurrent.futures as cf
import html
import json
import re
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import requests
from bs4 import BeautifulSoup

from app.database import SessionLocal, IMAGES_DIR
from app import models
from app.exenia_codes import STEP_FIELDS, field_key_for, slug
from restructure_coupled_field_values import split_exenia_colour, split_exenia_optic

MAKE = "Exenia"
SITEMAP_INDEX = "https://www.exenia.eu/sitemap_index.xml"
PRODUCT_URL = re.compile(r"^https://www\.exenia\.eu/en/product/[^/]+/$")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
CACHE = _REPO_ROOT / "data" / "exenia_cache"
# Deliberately low: a six-way pass over the full catalogue earned a spell of 403s from
# their edge. Three is slower and does not.
WORKERS = 3

# Exenia's configurator step names -> our field keys, and the slug an unnamed step falls
# back to. Both live in app/exenia_codes.py, which the running app also reads them from
# when it composes an order code - one copy, so the two can never disagree about which
# field a step's value was stored under.
FIELDS = STEP_FIELDS

# Steps that identify the product rather than offering a choice - "Housing body" is always
# the product's own name, one option, which is not something to pick.
IGNORED_STEPS = {"Housing body", "Apparel Body", "System body"}

# "Version" does not name a setting, it names a different luminaire. Its values are apertures
# and bodies - "1x Round Trim" / "1x Square Trimless" is a different cut-out and a different
# visible face; "1x" / "2x" is one head or two; "Small 360 - 15+6W" / "Large 650 - 25+11W" is
# a different size drawing different power. A specifier picks one of those the way they pick
# between two InHouse specsheets, so each becomes its own library row with `variant` set -
# the column whose docstring already gives "With Trim" / "Trimless" as the example.
#
# Everything else Exenia asks - finish, optic, colour, control, accessories - is a setting on
# a chosen luminaire and stays a dropdown.
VARIANT_STEP = "Version"

# A step whose every option is punctuation is their shop's UI, not the product: "Order
# separately" and "Select to activate the diffuser" render as a "+" button in Exenia's
# checkout, and a dropdown offering "+" is a question with no answer. Dropped at parse time
# so they never reach the library, the Details view, or a BOQ line.
UI_AFFORDANCE = re.compile(r"^[\s+\-–—*/|.,;:]*$")

# A parenthetical on a step name is an instruction to the buyer, not part of the field's
# name: "Power supply (For Emergency option contact the factory)" is the Power supply step,
# and carrying the sentence into the label gives a form field called "Power Supply For
# Emergency Option Contact The Factory". The note is worth keeping, so it stays in `params`
# under the name Exenia published; only the field label is trimmed.
STEP_NOTE = re.compile(r"\s*\([^)]*\)\s*$")

# The page's own map of Exenia product code -> photograph, used to give each version its own
# picture. See variant_images() for how the two halves are joined.
GALLERY_VAR = re.compile(r"var\s+gallery_colori\s*=\s*'(.*?)';", re.S)

# On the DARK and REVO families the Accessories step offers ONE option - "DARK Accessories" -
# and the real choice sits inside it as three variation selects, one per mounting slot, with
# the note "Up to three accessories can be installed on the product at the same time". Taking
# the option title at face value put the wrapper's name in the library where the accessories
# belong, so those products offered a BOQ line for a thing called "DARK Accessories" and no
# way to ask for a snoot.
#
# The slots are not interchangeable, and the order is physical: slot 1 sits against the
# optic, slot 3 outermost. A honeycomb only fits slot 1; a snoot or visor can go anywhere.
ACCESSORY_SLOT = re.compile(r"pa_accessor\w*-(\d+)", re.I)
ACCESSORY_NOTE = re.compile(r"Up to [a-z]+ accessor[a-z ]*[^.<]*", re.I)
# "No accessories" is the absence of one, and "Choose an option" is the placeholder - neither
# is something to put on a BOQ.
NOT_AN_ACCESSORY = {"choose an option", "no accessories", "none", ""}

# Spec-table rows worth keeping as fields, under our own names.
SPEC_FIELDS = {
    "Protection class": "ip_rating",
    "CRI": "cri",
    "Isolation class": "isolation_class",
    "Lifetime": "lifetime",
    "Incandescent wire": "incandescent_wire",
    "Certification": "certification",
    "Class F": "class_f",
    "Power supply": "power_supply",
}

# Looked up case-insensitively: the site is inconsistent about it ("Primary Optics
# Accessories" on one product, "Primary optics accessories" on another), and a missed
# variant silently splits one axis across two dropdowns.
FIELDS_CI = {k.lower(): v for k, v in FIELDS.items()}
IGNORED_STEPS_CI = {s.lower() for s in IGNORED_STEPS}

# Catalogue names and website names for the same product do not always agree on the word.
NAME_SYNONYMS = {"fix": "fixed", "orientable": "adjustable"}


def norm(s: str) -> str:
    s = (s or "").lower().replace("®", "").replace("™", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(NAME_SYNONYMS.get(w, w) for w in s.split())


def uniq(seq):
    return list(OrderedDict.fromkeys(x for x in seq if x))


# A few of their pages carry a byte from the cp1252 range where the character belongs -
# "L=564mm  13W" is an en dash that was written as its Windows codepoint. Left alone it
# reaches the library, the Configurator and the BOQ line as an invisible control character in
# the middle of a product's name.
def fix_mojibake(text: str) -> str:
    if not text or not any("" <= c <= "" for c in text):
        return text
    try:
        return text.encode("cp1252", "strict").decode("cp1252")
    except UnicodeEncodeError:
        return "".join(
            (bytes([ord(c)]).decode("cp1252", "replace") if "" <= c <= "" else c)
            for c in text)


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
    return s


def cached(name, produce, refresh):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    data = produce()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def product_urls(sess) -> list:
    """Every English product URL, from Exenia's own Yoast sitemaps - twelve of them, and the
    listing is paginated, so the sitemaps are both cheaper and complete."""
    index = sess.get(SITEMAP_INDEX, timeout=45).text
    urls = set()
    for sm in re.findall(r"<loc>([^<]*product-sitemap[^<]*)</loc>", index):
        text = sess.get(sm, timeout=60).text
        urls |= {u for u in re.findall(r"<loc>([^<]+)</loc>", text) if PRODUCT_URL.match(u)}
    return sorted(urls)


# Their markup writes raw JSON into HTML attributes that are themselves inside a JSON string
# - `data-bundle_form_data="{"layout":...}"`, `data-product_variations="[{"attributes":...}]"`
# - and never escapes the inner quotes, so the option list will not parse and the whole
# component used to be dropped without a word. On one product page every component is
# affected, which would have imported the product with no options at all.
#
# The attributes hold cart state and are of no interest here, so they are emptied and the
# parse retried. Ending the match on an ESCAPED quote is what makes this safe: inside the
# malformed region every quote is bare, so `}\"` or `]\"` can only be the attribute's real
# closing delimiter and never a brace within the payload.
EMBEDDED_JSON_ATTR = re.compile(r'(data-[a-z_]+=\\")[\[{].*?[\]}](\\")')


def parse_options_data(text: str):
    """-> (options, repaired). Raises ValueError only when nothing can be recovered.

    A parser that swallows its own failures cannot be trusted in production: the catalogue
    looked complete while a component was missing from 25 products. Callers count what this
    could not read and the import reports it, so the next breakage is visible on the run
    rather than months later in a BOQ.
    """
    try:
        return json.loads(text), False
    except ValueError:
        pass
    repaired = EMBEDDED_JSON_ATTR.sub(lambda m: m.group(1) + m.group(2), text)
    return json.loads(repaired), True


# A step can offer a single option that is a WooCommerce bundle rather than a product -
# "Tracks 48", "Power supplies 48" - with the real choices inside it as attribute selects.
# On TRACK 48 that hides the power supply's control protocol (DALI/PUSH, ON-OFF, DALI), its
# power (29W to 480W) and its dimensions, which are exactly what a BOQ line for a track
# system has to state. Left unexpanded the step shows one option named after the bundle,
# which is not a choice at all.
BUNDLE_ATTRIBUTE = re.compile(r"bundle_attribute_pa_([a-z0-9_\-]+)", re.I)
# Quantity is how many of a bundled item you want, not a specification of the product.
SKIP_BUNDLE_ATTRS = {"qta", "quantita"}
# Their attribute names are Italian; these read badly as English field labels.
BUNDLE_ATTR_NAMES = {"controllo": "control", "potenza": "power", "dimensioni": "dimensions",
                     "finitura": "finish", "lunghezza": "length", "colore": "colour"}


def bundle_attributes(product_html: str):
    """{attribute name: [value, ...]} from the selects inside a bundle wrapper option."""
    if not product_html or "bundle_attribute" not in product_html:
        return {}
    inner = BeautifulSoup(product_html, "html.parser")
    out = OrderedDict()
    for sel in inner.select("select"):
        m = BUNDLE_ATTRIBUTE.search(sel.get("name") or "")
        if not m:
            continue
        attr = m.group(1).lower().rstrip("_-0123456789")
        if attr in SKIP_BUNDLE_ATTRS:
            continue
        values = uniq(fix_mojibake(" ".join(o.get_text(" ", strip=True).split()))
                      for o in sel.select("option"))
        values = [v for v in values if v.lower() not in NOT_AN_ACCESSORY]
        if values:
            name = BUNDLE_ATTR_NAMES.get(attr, attr.replace("-", " "))
            out[name] = uniq(out.get(name, []) + values)
    return out


def accessory_slots(product_html: str):
    """[(slot number, [accessory, ...])] from the variation selects inside a wrapper option."""
    if not product_html or "accessor" not in product_html.lower():
        return []
    inner = BeautifulSoup(product_html, "html.parser")
    out = []
    for sel in inner.select("select"):
        m = ACCESSORY_SLOT.search(sel.get("name") or "")
        if not m:
            continue
        names = uniq(" ".join(o.get_text(" ", strip=True).split()) for o in sel.select("option"))
        names = [n for n in names if n.lower() not in NOT_AN_ACCESSORY]
        if names:
            out.append((int(m.group(1)), names))
    return sorted(out)


def bundled_accessories(product_html: str):
    """[(accessory name, its own Exenia code)] from a WooCommerce bundle inside an option.

    The DARK families offer accessories as variation selects, which accessory_slots() reads.
    The REVO families offer them a second way, as an optional-item bundle: "Independent
    accessories: Acs Revo Compact" and "Interchangeable lenses: Optics Multimetrik Compact"
    each hide seven or two real products inside their option's product_html. Read only for
    the wrapper's title, as they were, the app showed one meaningless dropdown entry and no
    way to add a Visor, a Snoot or a Protective glass at all.

    Each item states both halves on itself, so nothing here is inferred: `data-bundle_sku`
    is the accessory's own ordering code (9G086) and `data-product_title` is its name.
    """
    if not product_html or "data-bundle_sku" not in product_html:
        return []
    inner = BeautifulSoup(product_html, "html.parser")
    out = []
    for el in inner.select("[data-bundle_sku]"):
        sku = (el.get("data-bundle_sku") or "").strip()
        name = fix_mojibake(" ".join(
            (el.get("data-product_title") or el.get("data-title") or "").split()))
        if sku and name and name.lower() not in NOT_AN_ACCESSORY:
            out.append((name, sku))
    return list(OrderedDict(out).items())


def expand_accessories(step_options):
    """-> (accessory names, [(slot, [names])], note) for an Accessories step.

    `step_options` is the raw option list, so a wrapper option can be opened up. A step that
    already lists real accessories is returned unchanged.
    """
    slots, note = [], ""
    for o in step_options:
        ph = (o.get("option_product_data") or {}).get("product_html") or ""
        found = accessory_slots(ph)
        if found:
            slots = found
            m = ACCESSORY_NOTE.search(ph)
            if m and not note:
                note = " ".join(m.group(0).split()).strip()
    if not slots:
        titles = uniq(fix_mojibake(" ".join(str(o.get("option_title") or "").split()))
                      for o in step_options)
        return [t for t in titles if t.lower() not in NOT_AN_ACCESSORY], [], ""
    names = uniq(n for _, group in slots for n in group)
    return names, slots, note


def parse_gallery(page_html: str) -> dict:
    """{Exenia product code: image url}, from the page's own gallery_colori map. A key can
    list several codes ("81,81-W"), so each is indexed separately."""
    m = GALLERY_VAR.search(page_html)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    entry = data[0] if isinstance(data, list) and data else data
    out = {}
    for key, val in (entry or {}).items():
        url = val.get("img") if isinstance(val, dict) else None
        if not url:
            continue
        for code in (c.strip() for c in str(key).split(",")):
            if code:
                out.setdefault(code, url)
    return out


def gallery_image(sku: str, gallery: dict) -> str:
    """The image for one version's code. Codes also appear with a finish suffix ("81-9"), so
    an exact match wins and a suffixed one is the fallback."""
    if not sku:
        return ""
    if sku in gallery:
        return gallery[sku]
    for code, url in gallery.items():
        if code.split("-", 1)[0] == sku:
            return url
    return ""


def variant_images(soup, raw: str) -> dict:
    """{version name: image url} - each body's own photograph.

    Exenia's page swaps the picture when a version is picked, and the luminaires differ
    visibly: a square trimless aperture is not a round trimmed one. The whole mapping is in
    the page already, in two halves that have to be joined - gallery_colori is keyed by
    Exenia's product codes, and each option's own code is its option_product_data.sku.

    Confirmed against the live page: driving a browser and clicking all four versions of
    DARK FIX COMPACT returns exactly the four images this produces.
    """
    gallery = parse_gallery(raw)
    if not gallery:
        return {}
    out = {}
    for c in soup.select(".composite_component"):
        if " ".join((c.get("data-nav_title") or "").split()) != VARIANT_STEP:
            continue
        box = c.select_one("[data-options_data]")
        if not box:
            continue
        try:
            opts = json.loads(html.unescape(box["data-options_data"]))
        except ValueError:
            continue
        for o in opts:
            sku = ((o.get("option_product_data") or {}).get("sku") or "").strip()
            url = gallery_image(sku, gallery)
            if url:
                out[fix_mojibake(" ".join(str(o.get("option_title") or "").split()))] = url
    return out


def parse_page(raw: str, url: str) -> dict:
    soup = BeautifulSoup(raw, "html.parser")
    h1 = soup.select_one("h1")
    crumbs = [" ".join(a.get_text(" ", strip=True).split())
              for a in soup.select('[class*=breadcrumb] a, .woocommerce-breadcrumb a')]
    specs = OrderedDict()
    for tb in soup.select("table"):
        for tr in tb.select("tr"):
            cells = [" ".join(c.get_text(" ", strip=True).split()) for c in tr.select("td,th")]
            if len(cells) == 2 and cells[0] and cells[1]:
                specs.setdefault(cells[0], cells[1])
    steps = OrderedDict()
    option_skus = OrderedDict()
    bundled = OrderedDict()
    unreadable = []
    accessories = {"names": [], "slots": [], "note": ""}
    # name -> its own Exenia code, gathered from any step that hides a bundle of real
    # products inside one wrapper option. Which step that is varies by family, so this
    # looks at all of them rather than trusting a name.
    accessory_codes = OrderedDict()
    for c in soup.select(".composite_component"):
        title = " ".join((c.get("data-nav_title") or "").split())
        box = c.select_one("[data-options_data]")
        if not title or not box:
            continue
        try:
            raw_options, repaired = parse_options_data(html.unescape(box["data-options_data"]))
            if repaired:
                unreadable.append((title, "recovered after repair"))
        except (ValueError, KeyError, TypeError) as e:
            unreadable.append((title, repr(e)[:70]))
            continue
        opts = [fix_mojibake(" ".join(str(o.get("option_title") or "").split()))
                for o in raw_options]
        for o in raw_options:
            for name, sku in bundled_accessories(
                    (o.get("option_product_data") or {}).get("product_html") or ""):
                accessory_codes.setdefault(name, sku)
        # Exenia has no single product code: their configurator composes one from the SKU of
        # every option chosen - housing body 610, control E0, power 51, finish 52, colour /9530
        # gives 610E051 52/9530. Keeping the per-option SKUs is what lets a code be built.
        #
        # EVERY step is kept, in page order, including the ones whose only option is "+" and
        # the options that carry no SKU at all. Both are load-bearing:
        #
        #   The "+" steps are not decoration, they are SEPARATORS. An Exenia product page
        #   describes several separately-ordered products, and a "+" step is where one code
        #   ends and the next begins - M2 reads 610/E0/51/52//9530, then "Order separately",
        #   then the decorative ring 6109050/17//M, then "Select to activate adapter", then
        #   the adapter 7139018. Dropping them ran the three codes together into one string
        #   that matched nothing Exenia sells.
        #
        #   An option with an empty SKU (M2's "CRI85 3000K", its "Flush-mounting adapter") is
        #   a real choice Exenia does not publish a code for. Keeping it is what lets the
        #   composer refuse rather than silently pick the neighbouring option that does have
        #   one.
        # THREE outcomes per option, and collapsing the last two is how a short code gets
        # printed as though it were whole:
        #
        #   "W"    Exenia codes this option.
        #   ""     Exenia sent the option's product data and it carries no SKU. That is a
        #          statement: this option does not appear in the code. M2's "Secondary
        #          optical accessories" and Superlinea's "UGR" are like this.
        #   None   Exenia sent no product data for the option at all. We know NOTHING about
        #          whether it codes. SPOT REVO Small Semi-Recessed's "Factory-fitted
        #          accessories" is like this - and it does code, as the trailing N of
        #          385 _ R W 23 S N. Read as "no SKU" it silently shortened the code.
        option_skus[title] = {
            fix_mojibake(" ".join(str(o.get("option_title") or "").split())):
            (((o.get("option_product_data") or {}).get("sku") or "").strip()
             if (o.get("option_product_data") or {}) else None)
            for o in raw_options}
        if not opts or all(UI_AFFORDANCE.match(o) for o in opts):
            continue
        # The Accessories step may name a wrapper rather than the accessories themselves.
        if title.strip().lower() == "accessories":
            names, slots, note = expand_accessories(raw_options)
            if names:
                accessories = {"names": names, "slots": slots, "note": note}
                opts = names
        else:
            # Any other step whose option is a bundle: take the choices from inside it.
            for o in raw_options:
                for attr, values in bundle_attributes(
                        (o.get("option_product_data") or {}).get("product_html") or "").items():
                    key = f"{title} {attr}"
                    bundled[key] = uniq(bundled.get(key, []) + values)
        steps[title] = uniq(steps.get(title, []) + opts)
    img = soup.find("meta", property="og:image")
    desc = soup.find("meta", attrs={"name": "description"})
    return {
        "url": url,
        "variant_images": variant_images(soup, raw),
        "accessories": accessories,
        "accessory_codes": accessory_codes,
        "option_skus": option_skus,
        "bundled": bundled,
        "unreadable": unreadable,
        "name": " ".join(h1.get_text(" ", strip=True).split()) if h1 else "",
        "crumbs": crumbs, "specs": specs, "steps": steps,
        "pdfs": sorted(set(re.findall(r"https?://[^\s\"'<>\\]+\.pdf", raw, re.I))),
        "image": (img.get("content") if img else "") or "",
        "description": (desc.get("content") if desc else "") or "",
    }


def fetch_pages(sess, urls):
    def one(url):
        for attempt in range(3):
            try:
                r = sess.get(url, timeout=60)
                if r.status_code == 200:
                    return parse_page(r.text, url), None
                err = f"HTTP {r.status_code}"
            except Exception as e:
                err = repr(e)[:90]
            time.sleep(1 + attempt)
        return None, (url, err)

    pages, errors = [], []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for page, err in ex.map(one, urls):
            (errors if err else pages).append(err or page)
    return {"pages": pages, "errors": errors}


def is_product(page: dict) -> bool:
    """A real luminaire page, not a variant/component entry. Those exist in the sitemap
    ("10000lm", "33w-900ma-2") with no configurator and only "Home > Products" for a
    breadcrumb - they are options of other products, not products."""
    crumbs = page.get("crumbs") or []
    return bool(page.get("steps")) and len(crumbs) > 3 and crumbs[2] in ("Indoor", "Outdoor")


def datasheet(page: dict) -> str:
    """Exenia's single-product datasheet, where they publish one - "<code>-FV.pdf"."""
    for u in page.get("pdfs") or []:
        if re.search(r"-fv\.pdf$", u, re.I):
            return u
    return ""


def colour_states_cri(cct_options) -> bool:
    """Whether the colour choice already names the CRI grade. Exenia sells the LED bin and
    its colour temperature as one line item because the die determines both - offering CRI
    again beside it would let a row ask for CRI98 next to a CRI92 colour."""
    return bool(cct_options) and all(re.search(r"\bCRI\s?\d", v, re.I) for v in cct_options)


def real_steps(page: dict) -> "OrderedDict":
    """The configurator steps that are actually about the product. Applied here rather than
    only in parse_page so it holds for a cached parse too."""
    out = OrderedDict()
    for step, values in (page.get("steps") or {}).items():
        if step.lower() in IGNORED_STEPS_CI:
            continue
        keep = [v for v in values if not UI_AFFORDANCE.match(v)]
        if keep:
            out[step] = keep
    return out


def build_row(page: dict, version: str = "") -> dict:
    crumbs = page["crumbs"]
    environment, typology = crumbs[2], crumbs[3]
    family = crumbs[4] if len(crumbs) > 4 else ""

    fields, options = OrderedDict(), OrderedDict()
    for step, values in real_steps(page).items():
        if step == VARIANT_STEP:
            continue          # this row IS one version; it is not a choice within the row
        name = STEP_NOTE.sub("", step) or step
        key = field_key_for(name)
        options[key] = uniq(options.get(key, []) + values)

    # Choices that live inside a bundle wrapper - a track's power supply states its control
    # protocol, power and dimensions there. Named after the step they came from, so a power
    # supply's wattage is never mistaken for the luminaire's own.
    for name, values in (page.get("bundled") or {}).items():
        key = slug(name)
        if key not in options:
            options[key] = list(values)

    # Separate the coupled steps into the fields they actually name, so a re-run produces
    # the same shape restructure_coupled_field_values.py leaves behind rather than
    # re-bundling it. Colour states a CRI grade with the temperature; the optic step states
    # the power drawn through each lens.
    split = split_exenia_colour(options.get("cct") or [])
    if split:
        options["cri"], options["cct"] = split[0], split[1]
    osplit = split_exenia_optic(options.get("beam_angle") or [])
    if osplit:
        options["beam_angle"], options["wattage"] = osplit[0], osplit[1]

    for key, values in options.items():
        fields[key] = " / ".join(values)
    # What the manufacturer says about fitting them, kept where a specifier will read it.
    acc = page.get("accessories") or {}
    if acc.get("slots"):
        fields["accessory_fitting"] = acc.get("note") or (
            f"Up to {len(acc['slots'])} accessories can be installed at the same time")
        for slot, names in acc["slots"]:
            fields[f"accessory_slot_{slot}"] = " / ".join(names)
    # Accessories sold as a bundle become real, pickable accessories with their own codes -
    # the Configurator's picker reads fields["accessories"], and the codes ride alongside so
    # an accessory line can carry the number it is ordered by.
    if page.get("accessory_codes"):
        have = [v for v in (fields.get("accessories") or "").split(" / ") if v.strip()]
        merged = uniq(have + list(page["accessory_codes"]))
        fields["accessories"] = " / ".join(merged)
        options["accessories"] = merged

    for label, value in page["specs"].items():
        key = SPEC_FIELDS.get(label)
        if not key or key in fields:
            continue
        if key == "cri":
            # The spec table states the grades the product is BUILT in, space-separated:
            # "92 98" is CRI92 and CRI98, not a value. Written as it stands it reached the
            # Library card and the Configurator as the string "92 98", which is neither
            # grade. Where the configurator sells colour as one line ("CRI92 2700K") the
            # grade is already part of that choice and is not repeated here at all.
            grades = re.findall(r"\d{2,3}", value)
            if colour_states_cri(options.get("cct") or []):
                continue
            value = " / ".join(f"CRI{g}" for g in grades) if grades else value
        fields[key] = value

    params = OrderedDict()
    steps = real_steps(page)
    if steps:
        params["Configurator (Exenia)"] = OrderedDict(
            (k, " / ".join(v)) for k, v in steps.items())
    if page["specs"]:
        params["Specifications (Exenia)"] = OrderedDict(page["specs"])
    if acc.get("slots"):
        params["Accessories (Exenia)"] = OrderedDict(
            [("Fitting", fields["accessory_fitting"])]
            + [(f"Position {slot} (nearest the optic first)", " / ".join(names))
               for slot, names in acc["slots"]])
    if page.get("option_skus"):
        params["Option codes (Exenia)"] = OrderedDict(
            (step, json.dumps(codes, ensure_ascii=False))
            for step, codes in page["option_skus"].items())
    params["Classification (Exenia)"] = OrderedDict(
        (k, v) for k, v in (("Environment", environment), ("Typology", typology),
                            ("Family", family)) if v)
    docs = OrderedDict()
    for u in page["pdfs"]:
        docs[u.rsplit("/", 1)[-1]] = u
    if docs:
        params["Documents"] = docs
    params["Source"] = OrderedDict([("Product page", page["url"])])

    raw = [page["name"], page["description"], f"Make: {MAKE}",
           f"Environment: {environment}", f"Typology: {typology}", f"Family: {family}",
           page["url"]]
    raw += [f"{k}: {' / '.join(v)}" for k, v in steps.items()]
    raw += [f"{k}: {v}" for k, v in page["specs"].items()]

    return {
        "name": page["name"], "variant": version,
        "typology": typology, "series": family or page["name"],
        "family": family, "environment": ["Interior" if environment == "Indoor" else "Exterior"],
        "category": f"{environment} - {typology}",
        "fields": fields, "options": options, "params": params,
        "raw_text": "\n".join(x for x in raw if x and not x.endswith(": ")),
        "url": page["url"], "datasheet": datasheet(page),
        # This version's own photograph where Exenia publishes one, the product's shared
        # photo otherwise.
        "image_url": (page.get("variant_images") or {}).get(version) or page["image"],
    }


def download_image(sess, url, name):
    if not url:
        return ""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    dest = IMAGES_DIR / f"exenia_web_{slug(name)}{Path(url.split('?')[0]).suffix or '.jpg'}"
    if dest.exists():
        return str(dest)
    try:
        r = sess.get(url, timeout=45)
        if r.ok and r.content:
            dest.write_bytes(r.content)
            return str(dest)
    except Exception:
        pass
    return ""


def main():
    commit = "--commit" in sys.argv
    refresh = "--refresh" in sys.argv
    sess = session()

    urls = cached("urls.json", lambda: product_urls(sess), refresh)
    print(f"english product URLs in the sitemaps: {len(urls)}")
    fetched = cached("pages.json", lambda: fetch_pages(sess, urls), refresh)
    pages, errors = fetched["pages"], fetched["errors"]
    # Their edge answers 403 to a catalogue-wide pass that goes too fast. A run that lost a
    # meaningful share of pages is a throttled run, not a smaller catalogue: caching it would
    # make the next run "succeed" against a hollow copy, and re-deriving rows from it would
    # quietly narrow products that are actually fine. Refuse it and keep what is on disk.
    if urls and len(errors) > max(5, len(urls) // 20):
        (CACHE / "pages.json").unlink(missing_ok=True)
        raise SystemExit(
            f"ABORTED: {len(errors)} of {len(urls)} pages failed to fetch - that is a "
            f"throttled run, not a real result. Nothing was written and the cache was "
            f"discarded; try again later, or lower WORKERS.")
    print(f"pages fetched: {len(pages)}   errors: {len(errors)}")
    for e in errors[:8]:
        print(f"    FETCH ERROR {e}")

    products = [p for p in pages if is_product(p)]
    print(f"real product pages: {len(products)}   "
          f"variant/component entries skipped: {len(pages) - len(products)}")

    # Never silent: a component this could not read is reported on the run, whether it was
    # recovered or lost.
    recovered = [(p, t) for p in pages for t, why in (p.get("unreadable") or [])
                 if why == "recovered after repair"]
    lost = [(p, t, why) for p in pages for t, why in (p.get("unreadable") or [])
            if why != "recovered after repair"]
    print(f"components recovered by repairing malformed markup: {len(recovered)}")
    print(f"components that could NOT be read: {len(lost)}")
    for p, t, why in lost[:10]:
        print(f"    UNREADABLE {p['url'].rsplit('/', 2)[-2]} / {t!r}: {why}")

    db = SessionLocal()
    rows = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()
    by_name = {}
    for s in rows:
        by_name.setdefault(norm(s.product_name), []).append(s)
    web = {norm(p["name"]): p for p in products}

    matched = {n: web[n] for n in by_name if n in web}
    # Second pass: the same words in a different order ("SUPERLINEA MINI RECESSED" is the
    # catalogue's name for "Superlinea Recessed Mini"). Only an exact token-set match links -
    # anything less is a guess, and the guesses here are all of the shape "SUPERLINEA MAX FIX
    # PROJECTOR", which fits Exenia's 48V page and their 230V page equally well. Pointing a
    # row at the wrong voltage's datasheet is worse than pointing it at nothing, so those are
    # reported for a human instead.
    unmatched_web = {n: p for n, p in web.items() if n not in matched.values() and
                     n not in {norm(x["name"]) for x in matched.values()}}
    ambiguous = []
    for n in sorted(set(by_name) - set(matched)):
        tokens = set(n.split())
        exact = [w for w, p in unmatched_web.items() if set(w.split()) == tokens]
        if len(exact) == 1:
            matched[n] = unmatched_web[exact[0]]
        else:
            near = sorted(((len(tokens & set(w.split())) / len(tokens | set(w.split())),
                            unmatched_web[w]["name"]) for w in unmatched_web), reverse=True)[:2]
            if near and near[0][0] >= 0.5:
                ambiguous.append((by_name[n][0].product_name, [b for _, b in near]))

    linked_web = {p["url"] for p in matched.values()}
    new = [web[n] for n in sorted(set(web) - set(by_name)) if web[n]["url"] not in linked_web]
    orphans = [by_name[n][0] for n in sorted(set(by_name) - set(matched))]

    print()
    print(f"library rows: {len(rows)}")
    print(f"  matched to a product page : {sum(len(by_name[n]) for n in matched)}")
    print(f"  no product page found     : {len(orphans)}")
    print(f"website products not in the library (would be added): {len(new)}")
    print(f"products publishing their own datasheet PDF: "
          f"{sum(1 for p in products if datasheet(p))}")
    print()
    print("new products by typology:")
    for t, n in Counter(p["crumbs"][3] for p in new).most_common():
        print(f"   {n:>4}  {t}")
    print()
    print("library rows with no product page (kept as they are, source unchanged):")
    for s in orphans:
        print(f"   {s.product_name}")
    if ambiguous:
        print()
        print("of those, ones whose catalogue entry covers more than one website product -")
        print("Exenia splits by track voltage where the catalogue did not, so these need a human:")
        for name, cands in ambiguous:
            print(f"   {name:<38} could be {cands}")

    if not commit:
        db.close()
        print("\nDRY RUN - nothing written. Re-run with --commit.")
        return

    # One row per version, reusing the rows already there so nothing that points at a
    # specsheet is broken - 12 BOQ items reference Exenia rows, and the first version of a
    # product keeps that product's existing row and id.
    # Keyed by page, using the SAME matching the report above did - exact name, then the
    # token-set second pass. Keying by exact name alone left "SUPERLINEA MINI RECESSED"
    # orphaned while a fresh row was created for Exenia's "Superlinea Recessed Mini", i.e.
    # the one product twice.
    # A row that already records a page belongs to THAT page, whatever it is called. Exenia
    # publishes a few products at two URLs (led-booster-kit-111 and -111-2, dots and dots-2),
    # and name matching can only ever hand those rows to one of the pair - the other found an
    # empty pool and created a fresh row on every single run. Claiming by URL first makes the
    # import idempotent; the name match is the fallback for rows that have no URL yet.
    rows_for_page = {}
    for s in rows:
        if s.source_url:
            rows_for_page.setdefault(s.source_url, []).append(s)
    for n, page in matched.items():
        for s in by_name[n]:
            if not s.source_url:
                rows_for_page.setdefault(page["url"], []).append(s)

    reused = created = 0
    for page in products:
        versions = real_steps(page).get(VARIANT_STEP) or [""]
        pool = rows_for_page.get(page["url"], [])
        claimed = []
        for version in versions:
            s = (next((x for x in pool if (x.variant or "") == version and x not in claimed), None)
                 or next((x for x in pool if not (x.variant or "") and x not in claimed), None)
                 or next((x for x in pool if x not in claimed), None))
            if s is None:
                s = models.Specsheet(product_name=page["name"], make=MAKE)
                db.add(s)
                created += 1
            else:
                reused += 1
            claimed.append(s)

            r = build_row(page, version)
            # Named per variant, so four bodies of one product do not collide on one file.
            image_path = download_image(sess, r["image_url"],
                                        f"{r['name']} {version}".strip())
            s.product_name = r["name"]
            s.variant = r["variant"]
            s.filename = r["url"].rstrip("/").rsplit("/", 1)[-1] + ".html"
            s.category, s.typology = r["category"], r["typology"]
            s.series, s.family = r["series"], r["family"]
            s.environment = r["environment"]
            s.page_number = 1
            s.source_url = r["url"]
            s.source_pdf_path = r["datasheet"]
            s.raw_text = r["raw_text"]
            s.fields, s.options = dict(r["fields"]), dict(r["options"])
            s.params = {k: dict(v) for k, v in r["params"].items()}
            # Overwrite: a row imported before per-version images existed is holding the
            # shared product photo, which for three of the four bodies is the wrong luminaire.
            if image_path:
                s.image_path = image_path
                s.diagrams = [{"path": image_path, "width": 0, "height": 0,
                               "label": "Product Image"}]
        db.commit()

    total = db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).count()
    print(f"\nwrote {reused + created} row(s) from {len(products)} product page(s): "
          f"{reused} existing row(s) updated, {created} added for versions that had none.")
    print(f"{len(orphans)} catalogue row(s) with no product page were left untouched.")
    print(f"{total} {MAKE} rows now.")
    db.close()


if __name__ == "__main__":
    main()
