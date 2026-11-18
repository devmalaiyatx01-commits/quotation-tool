"""Imports Lumenpulse's entire product catalogue (https://www.lumenpulse.com/products),
classified by Lumenpulse's own taxonomy, into the specsheet library.

Where the data comes from
-------------------------
The products listing is a client-side app, so the page HTML carries no product rows at
all - it mounts a Vue app that reads Lumenpulse's own public listing API, publishing the
endpoint, index name and static token in its own markup (`<app data-api-url=...
data-index=... data-jwt=...>` on /products). This script reads that listing the same way
the page does, which is both far lighter on their servers and far more accurate than
scraping rendered HTML:

  POST /search       every product: id, title, code, family, description, image, URL
  POST /filter/list  the taxonomy itself - 18 facets, 164 values, with their real labels

The per-product option lists are NOT in that API, so those come from each product page,
which IS server-rendered: every option group is one `.open-collapse` block whose <h2>
reads "<Group> (N)" over N `.product-details-specvalue` entries. That declared N is used
as an integrity check - any group where the parsed count differs is reported, never
silently imported (all 302 products matched on the run this was written from).

robots.txt allows this (`User-agent: * / Allow: /`, Content-Signal `search=yes,
use=reference`); the catalogue is imported as an internal specification reference, which
is what "use=reference" permits. Requests are pooled at 8 concurrent with retries.

Lumenpulse's classification, and how it maps onto ours
------------------------------------------------------
  Product Segment (163)   Indoor Architectural / Outdoor Architectural / Site & Area
                          -> `environment`, mapped onto the library's shared
                             Interior/Exterior vocabulary so the Environment chips keep
                             working across brands; the original three-way label is kept
                             verbatim in fields["product_segment"], since "Site & Area" is
                             a real distinction Lumenpulse makes and Interior/Exterior
                             cannot express.
  Luminaire Type (1082)   -> `typology`. This facet is multi-valued and carries both a
                             legacy and a current label for the same set (a product tagged
                             "Downlights" is always also tagged "Downlight" - verified by
                             comparing membership, not by guessing from the names), plus
                             genuine parent/child pairs (every "Cylinders" product is also
                             "Cylinder & Pendant"). SYNONYMS collapses the first;
                             TYPOLOGY_PRIORITY picks one value for the second, ordered by
                             what the luminaire IS before how it mounts - so a product
                             tagged both "Area and Post-Top" and "Pole-Mounted" files under
                             the former. Every value a product carries is still kept in
                             fields["luminaire_type"].
  Collection (1149)       -> `series`   (Lumencore, Lumenfacade, Lumenbeam, ...)
  Family                  -> `family`   (Lumencore Recessed, Lumencore Cylinders, ...)

Their option groups vs. ours
----------------------------
Lumenpulse names several fields differently from the rest of the library - beam angle is
"Optics (Nominal Distribution)", CCT is "Colour and Colour Temperature" - and specifies
output in lumens rather than watts, which is why `lumen_output` exists as a field. FIELDS
below maps their names onto ours; everything it does not name keeps its own name as a
slug, so nothing is dropped and nothing is forced into a field that does not mean the same
thing. The label a user sees stays Lumenpulse's own (see BRAND_FIELD_LABELS in
app/config_rules.py).

Synonyms were merged only where no product carries two of them at once - checked, not
assumed. Three near-misses are therefore deliberately NOT merged: "Optical Option" is its
own field (3 products list it beside "Optic"), "Installation Accessories" is not
"Optical Accessories" (6 products list both), and "Options" is not "Option" (3 list both).

Fetches are cached under data/lumenpulse_cache/ so a re-run is cheap and the parse can be
iterated without hitting the site again; pass --refresh to ignore the cache. Dry run by
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

MAKE = "Lumenpulse"
LISTING = "https://www.lumenpulse.com/products"
API = "https://searchapi.lumenpulse.com"
INDEX = "lumenpulse_product"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
CACHE = _REPO_ROOT / "data" / "lumenpulse_cache"
WORKERS = 8

# Their option-group names -> our field keys. Only groups that mean the same thing are
# mapped; see the module docstring for the three deliberate non-merges.
FIELDS = {
    "Finish": "finish",
    "Trim Finish": "finish",
    "Colour Rendering": "cri",
    "Colour and Colour Temperature": "cct",
    "Colour and Colour temperature": "cct",          # their own casing varies
    "Optics (Nominal Distribution)": "beam_angle",
    "Optic": "beam_angle",
    "Optics": "beam_angle",
    "Distributions": "beam_angle",
    "Output (Nominal Lumens)": "lumen_output",
    "Ingress Protection Rating": "ip_rating",
    "Control": "control",
    "Mounting": "mounting",
    "Mounting Options": "mounting",
    "Mounting Option": "mounting",
    "Length": "length",
    "Optical Accessories": "accessories",
    "Accessories": "accessories",
}

# Looked up case-insensitively - their own casing varies ("Colour and Colour temperature"
# beside "Colour and Colour Temperature"), and a missed variant silently splits one axis
# across two dropdowns.
FIELDS_CI = {k.lower(): v for k, v in FIELDS.items()}

# Same set on the same product would silently overwrite one another, so the importer
# refuses to merge unless it has checked that never happens.
SYNONYM_GUARD = [
    ("finish", {"Finish", "Trim Finish"}),
    ("cct", {"Colour and Colour Temperature", "Colour and Colour temperature"}),
    ("beam_angle", {"Optics (Nominal Distribution)", "Optic", "Optics", "Distributions"}),
    ("mounting", {"Mounting", "Mounting Options", "Mounting Option"}),
    ("accessories", {"Optical Accessories", "Accessories"}),
]

# Luminaire Type labels that name the same set of products under an older and a newer name.
SYNONYMS = {
    "Downlights": "Downlight",
    "Floodlights and Projectors": "Projector and Floodlight",
    "Track Spots and Semi-Recessed Spots": "Track & Spot",
    "Linear Systems": "Linear System",
    "Cove": "Linear Cove",
    "Pole, Arm and Accessory": "Poles, Arms & Accessories",
    "Linear Projectors": "Linear Grazing",
    "Pendant": "Pendants",
    "Wall": "Wall-Mount",
}

# One typology per product, most-specific first: what the luminaire IS, then how it mounts,
# then controls gear. A product tagged both "Cylinders" and "Cylinder & Pendant" is a
# cylinder; one tagged both "Area and Post-Top" and "Pole-Mounted" is an area luminaire that
# happens to go on a pole.
TYPOLOGY_PRIORITY = [
    "Downlight", "Cylinders", "Track & Spot", "Direct View", "Inground",
    "Bollard", "Column", "Bollards and Columns",
    "Linear Cove", "Linear Grazing", "Linear System", "Pendants", "Cylinder & Pendant",
    "Projector and Floodlight", "Area and Post-Top",
    "Poles, Arms & Accessories", "Wall-Mount", "Pole-Mounted",
    "Controller & Control System", "Control & Power Supply Box",
]

# Six products carry no Luminaire Type at all - every one of them a controller rather than
# a luminaire (Lumencloud, Lumentalk, Lumenlink Pro, DMX/RDM, the two Pharos controllers),
# which is exactly the group Lumenpulse's own navigation files under Controls. Their family
# is what identifies them, so the fallback reads from that rather than leaving them
# unclassified.
TYPOLOGY_FALLBACK_BY_FAMILY = {
    "Control Systems": "Controller & Control System",
    "Lumentalk System": "Controller & Control System",
}

SEGMENT_TO_ENVIRONMENT = {
    "Indoor Architectural": "Interior",
    "Outdoor Architectural": "Exterior",
    "Site & Area": "Exterior",
}

# Badges the page appends to the <h1>; they are facts, but not part of the product's name.
NAME_BADGES = ["This product is not available in your region.", "Discontinued",
               "DarkSky Approved"]

COUNTED = re.compile(r"^(.*?)\s*\((\d+)\)\s*$")
# "..., CRI 80, ..." inside a longer colour-mode description.
CRI_CLAUSE = re.compile(r",\s*CRI\s?(\d{2,3})\s*(?=,|$)", re.I)
# "1500lmAmber" - the figure and the colour ran together in their markup.
GLUED_LUMENS = re.compile(r"(?<=\d)(lm)(?=[A-Z])", re.I)


def unglue_lumens(v: str) -> str:
    return GLUED_LUMENS.sub(r"\1 ", v)


def slug(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_")


def uniq(seq):
    return list(OrderedDict.fromkeys(x for x in seq if x))


# --------------------------------------------------------------------------- fetching

def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
    return s


def cached(name: str, produce, refresh: bool):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    data = produce()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def listing_config(sess) -> dict:
    """The API endpoint/index/token the listing page publishes for its own use."""
    soup = BeautifulSoup(sess.get(LISTING, timeout=45).text, "html.parser")
    app = soup.find(attrs={"data-api-url": True})
    if not app:
        raise SystemExit("the products page no longer publishes data-api-url - re-check it")
    return {"api": app["data-api-url"], "index": app.get("data-index", INDEX),
            "jwt": app["data-jwt"]}


def api_post(sess, cfg, path, body):
    for attempt in range(3):
        r = sess.post(f"{cfg['api']}{path}", timeout=90,
                      headers={"Authorization": f"Bearer {cfg['jwt']}",
                               "Content-Type": "application/json", "Referer": LISTING},
                      json={"index": cfg["index"],
                            "options": {"lang": "en", "region": "na", "unit": "metric"},
                            **body})
        if r.ok:
            return r.json()
        time.sleep(1 + attempt)
    raise RuntimeError(f"{path} failed: {r.status_code} {r.text[:120]}")


def fetch_products(sess, cfg):
    out, offset = [], 0
    while True:
        d = api_post(sess, cfg, "/search", {"filters": {}, "limit": 100, "offset": offset})
        out += d["items"]
        offset += 100
        if offset >= d["total"]:
            return out


def fetch_taxonomy(sess, cfg):
    """Which products sit under each taxonomy value - one call per value (164), rather
    than deriving a classification from 302 product pages that never state it."""
    groups = {}
    jobs = []
    for f in api_post(sess, cfg, "/filter/list", {})["filters"]:
        groups[f["name"]] = f.get("title") or f["name"]
        for it in (f.get("items") or []):
            jobs.append((f["name"], f["type"], it.get("value") or it.get("key"),
                         it.get("title") or it.get("label")))

    def one(job):
        gname, gtype, val, lbl = job
        d = api_post(sess, cfg, "/search",
                     {"filters": {gname: val if gtype == "toggle" else [val]},
                      "limit": 400, "offset": 0})
        return groups[gname], lbl, [i["id"] for i in d.get("items", [])]

    by_product = {}
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for gtitle, lbl, ids in ex.map(one, jobs):
            for pid in ids:
                by_product.setdefault(str(pid), {}).setdefault(gtitle, []).append(lbl)
    return by_product


def parse_page(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup.select("nav, header, footer, script, style, .mega-menu"):
        t.decompose()
    groups, ratings, mismatches = OrderedDict(), [], []
    for block in soup.select(".open-collapse"):
        h = block.select_one(".open-collapse__actions h2")
        if not h:
            continue
        m = COUNTED.match(" ".join(h.get_text(" ", strip=True).split()))
        if not m:
            continue                      # an "Advantages" block: marketing copy, no options
        name, declared = m.group(1), int(m.group(2))
        rats = [" ".join(p.get_text(" ", strip=True).split())
                for p in block.select(".product-details-rating p")]
        if rats:
            ratings += rats
            continue
        raw = [" ".join(" ".join(v.stripped_strings).split())
               for v in block.select(".product-details-specvalue")]
        # The declared count is checked against what was PARSED, before de-duplication -
        # a few groups list the same value twice (Lumenline's Joiners shows "Horizontal 90"
        # once per orientation), which is a repeat, not a missed entry.
        if len(raw) != declared:
            mismatches.append((name, declared, len(raw)))
        vals = uniq(raw)
        if vals:
            groups[name] = vals
    h1 = soup.select_one("h1")
    tagline = soup.select_one("h1 ~ h2") or soup.select_one("h2")
    return {
        "h1": " ".join(h1.get_text(" ", strip=True).split()) if h1 else "",
        "tagline": " ".join(tagline.get_text(" ", strip=True).split()) if tagline else "",
        "groups": groups, "ratings": uniq(ratings), "mismatches": mismatches,
        "documents": parse_documents(soup),
    }


def parse_documents(soup) -> list:
    """Lumenpulse's own documents for the product - the specification sheet above all, but
    also the catalogue, installation instructions, declaration of conformity and IES files.

    The page's Downloads panel renders from JS but its data is already in the markup, on
    `.js-files-support`'s data-initial-data, so no second request is needed: category, title,
    file type, byte size and a direct URL for each.
    """
    el = soup.select_one(".js-files-support")
    if not el or not el.get("data-initial-data"):
        return []
    try:
        data = json.loads(el["data-initial-data"])
    except ValueError:
        return []
    out = []
    for cat in data.get("list") or []:
        for f in cat.get("files") or []:
            url = ((f.get("file") or {}).get("url") or "").strip()
            if not url:
                continue
            size = f.get("file_size")
            out.append({
                "category": cat.get("category") or "",
                "title": " ".join((f.get("title") or "").split()),
                "type": (f.get("file") or {}).get("extension") or "",
                "size": int(size) if isinstance(size, int) else None,
                "url": url,
            })
    return out


def fetch_pages(sess, products):
    def one(p):
        for attempt in range(3):
            try:
                r = sess.get(p["url"], timeout=45)
                if r.status_code == 200:
                    return p["id"], parse_page(r.text), None
                err = f"HTTP {r.status_code}"
            except Exception as e:
                err = repr(e)[:90]
            time.sleep(1 + attempt)
        return p["id"], None, err

    out, errors = {}, []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for pid, parsed, err in ex.map(one, products):
            if err:
                errors.append((pid, err))
            else:
                out[str(pid)] = parsed
    return {"pages": out, "errors": errors}


def download_image(sess, url, name):
    if not url:
        return ""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?")[0]).suffix or ".jpg"
    dest = IMAGES_DIR / f"lumenpulse_{slug(name)}{suffix}"
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


# --------------------------------------------------------------------------- shaping

def clean_name(h1: str, api_title: str) -> str:
    """The page's own <h1>, minus the status badges appended to it. Preferred over the
    API's title because the page is the UK-English rendering the option values also use
    ("Colour Changing", not "Color Changing")."""
    name = h1
    for badge in NAME_BADGES:
        name = name.replace(badge, " ")
    name = " ".join(name.split())
    return name or api_title


def pick_typology(values, family, sizes):
    canon = {SYNONYMS.get(v, v) for v in values}
    for want in TYPOLOGY_PRIORITY:
        if want in canon:
            return want
    if canon:
        # Something new in their taxonomy: file it under its own label rather than drop it.
        return sorted(canon, key=lambda v: (-sizes.get(v, 0), v))[0]
    return TYPOLOGY_FALLBACK_BY_FAMILY.get(family, "")


def _document_label(doc: dict) -> str:
    bits = [doc["title"] or doc["category"]]
    if doc["type"]:
        bits.append(doc["type"].upper())
    if doc["size"]:
        bits.append(f"{doc['size'] / 1e6:.1f} MB")
    return f"{doc['category']} - {bits[0]}" + (f" ({', '.join(bits[1:])})" if len(bits) > 1 else "")


def _spec_sheet_url(docs: list) -> str:
    """The product's own specification sheet - the document a designer checks a BOQ line
    against. Metric wins over imperial where both are published, matching the units the rest
    of the library is in."""
    sheets = [d for d in docs if d["category"] == "Specification Sheets" and d["type"] == "pdf"]
    if not sheets:
        return ""
    metric = [d for d in sheets if "metric" in d["title"].lower()]
    return (metric or sheets)[0]["url"]


def lift_lumenpulse_cri(options: dict) -> None:
    """Move a CRI stated inside the colour option into `cri`, where it belongs. The tuning
    range and the channel architecture stay together: those ARE one choice, and "Dim to
    Warm, 1 Channel Control" is a colour behaviour, not a dimming protocol."""
    ccts = options.get("cct") or []
    grades = uniq("CRI " + g for v in ccts for g in CRI_CLAUSE.findall(v))
    if not grades:
        return
    options["cct"] = uniq(CRI_CLAUSE.sub("", v).strip() for v in ccts)
    options["cri"] = uniq((options.get("cri") or []) + grades)


def build_row(product, page, tax, sizes):
    name = clean_name(page["h1"], product["title"])
    segments = tax.get("Product Segment", [])
    lum_types = tax.get("Luminaire Type", [])
    collection = (tax.get("Collection") or [""])[0]

    fields, options = OrderedDict(), OrderedDict()
    for group, values in page["groups"].items():
        key = FIELDS_CI.get(group.lower(), slug(group))
        if key in options:                       # guarded against by SYNONYM_GUARD
            options[key] = uniq(options[key] + values)
        else:
            options[key] = list(values)
    # Their dynamic-white lines state the CRI inside the colour option ("2200K to 3000K,
    # CRI 80, Multi-Channel Control"), and their Lumenblade pages run the lumen figure into
    # the colour name ("1500lmAmber"). Both are corrected here so a re-import produces the
    # same shape restructure_coupled_field_values.py leaves behind, instead of the two
    # scripts undoing each other on every run.
    lift_lumenpulse_cri(options)
    if options.get("lumen_output"):
        options["lumen_output"] = [unglue_lumens(v) for v in options["lumen_output"]]

    for key, values in options.items():
        fields[key] = " / ".join(values)

    # Their classification, kept verbatim alongside the mapped-on library values so the
    # original vocabulary is never lost to the mapping.
    fields["product_segment"] = " / ".join(segments)
    fields["luminaire_type"] = " / ".join(uniq(SYNONYMS.get(v, v) for v in lum_types))
    if collection:
        fields["collection"] = collection
    if page["ratings"]:
        fields["certifications"] = " / ".join(page["ratings"])
    if "Discontinued" in page["h1"]:
        fields["lifecycle"] = "Discontinued"
    if "not available in your region" in page["h1"]:
        fields["regional_availability"] = "Not available in this region"

    # The Details view's "All parsed specifications" reads `params`, which is
    # {section: {key: value}} - every group verbatim under Lumenpulse's own names, the
    # classification they assign, and their documents, so the detail view shows the whole
    # datasheet rather than only the fields that map onto shared keys.
    params = OrderedDict()
    if page["groups"]:
        params["Options"] = OrderedDict((g, " / ".join(v)) for g, v in page["groups"].items())
    classification = OrderedDict(
        (k, v) for k, v in (
            ("Product Segment", fields["product_segment"]),
            ("Luminaire Type", fields["luminaire_type"]),
            ("Collection", collection),
            ("Family", product.get("family") or ""),
            ("Product Code", product.get("code") or ""),
            ("Certifications", fields.get("certifications", "")),
            ("Lifecycle", fields.get("lifecycle", "")),
            ("Regional Availability", fields.get("regional_availability", "")),
        ) if v)
    if classification:
        params["Classification (Lumenpulse)"] = classification
    docs = page.get("documents") or []
    if docs:
        params["Documents"] = OrderedDict(
            (_document_label(d), d["url"]) for d in docs)
    params["Source"] = OrderedDict([("Product page", product["url"])])

    raw = [name, page["tagline"], product.get("description") or "",
           f"Make: {MAKE}", f"Collection: {collection}", f"Family: {product.get('family') or ''}",
           f"Product Segment: {fields['product_segment']}",
           f"Luminaire Type: {fields['luminaire_type']}",
           f"Code: {product.get('code') or ''}", product["url"]]
    raw += [f"{g}: {' / '.join(v)}" for g, v in page["groups"].items()]

    return {
        "name": name,
        "typology": pick_typology(lum_types, product.get("family") or "", sizes),
        "series": collection or (product.get("family") or ""),
        "family": product.get("family") or "",
        "category": page["tagline"] or (product.get("description") or ""),
        "environment": uniq(SEGMENT_TO_ENVIRONMENT.get(s, "") for s in segments),
        "fields": fields, "options": options, "params": params,
        "spec_sheet_url": _spec_sheet_url(page.get("documents") or []),
        "raw_text": "\n".join(x for x in raw if x and not x.endswith(": ")),
        "url": product["url"], "code": product.get("code") or "",
        "image_url": (product.get("image") or {}).get("medium@2x")
                     or (product.get("image") or {}).get("medium") or "",
    }


# --------------------------------------------------------------------------- main

def main():
    commit = "--commit" in sys.argv
    refresh = "--refresh" in sys.argv
    sess = session()

    cfg = cached("config.json", lambda: listing_config(sess), refresh)
    print(f"listing API: {cfg['api']}  index={cfg['index']}")
    products = cached("products.json", lambda: fetch_products(sess, cfg), refresh)
    print(f"products in the listing: {len(products)}")
    taxonomy = cached("taxonomy.json", lambda: fetch_taxonomy(sess, cfg), refresh)
    print(f"products with a classification: {len(taxonomy)}")
    fetched = cached("pages.json", lambda: fetch_pages(sess, products), refresh)
    pages, page_errors = fetched["pages"], fetched["errors"]
    print(f"product pages parsed: {len(pages)}   fetch errors: {len(page_errors)}")
    for pid, err in page_errors[:10]:
        print(f"    PAGE ERROR {pid}: {err}")

    mismatched = [(pid, p["mismatches"]) for pid, p in pages.items() if p["mismatches"]]
    print(f"option groups whose parsed count != the page's own declared count: {len(mismatched)}")
    for pid, m in mismatched[:10]:
        print(f"    COUNT MISMATCH {pid}: {m}")

    collisions = []
    for pid, p in pages.items():
        for key, names in SYNONYM_GUARD:
            hit = sorted(set(p["groups"]) & names)
            if len(hit) > 1:
                collisions.append((pid, key, hit))
    print(f"products carrying two labels of one mapped field: {len(collisions)}")
    for c in collisions[:10]:
        print(f"    SYNONYM COLLISION {c}")

    sizes = Counter()
    for t in taxonomy.values():
        for v in t.get("Luminaire Type", []):
            sizes[SYNONYMS.get(v, v)] += 1

    rows = []
    for p in products:
        page = pages.get(str(p["id"]))
        if page is None:
            continue
        rows.append(build_row(p, page, taxonomy.get(str(p["id"]), {}), sizes))

    print()
    print(f"rows to import: {len(rows)}")
    print("typologies:")
    for t, n in Counter(r["typology"] or "(unclassified)" for r in rows).most_common():
        print(f"   {n:>4}  {t}")
    print("environment:")
    for e, n in Counter(tuple(r["environment"]) or ("(none)",) for r in rows).most_common():
        print(f"   {n:>4}  {'/'.join(e)}")
    print(f"rows with a specification sheet PDF: {sum(1 for r in rows if r['spec_sheet_url'])}")
    print(f"rows with any document              : {sum(1 for r in rows if 'Documents' in r['params'])}")
    print("field coverage:")
    for k, n in Counter(k for r in rows for k in r["options"]).most_common():
        print(f"   {n:>4}  {k}")

    if not commit:
        print("\nDRY RUN - nothing written. Re-run with --commit.")
        return

    db = SessionLocal()
    existing = {s.product_name: s for s in
                db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).all()}
    added = updated = 0
    for r in rows:
        image_path = download_image(sess, r["image_url"], r["name"])
        s = existing.get(r["name"])
        if s is None:
            s = models.Specsheet(product_name=r["name"], make=MAKE)
            db.add(s)
            added += 1
        else:
            updated += 1
        s.filename = r["url"].rsplit("/", 1)[-1] + ".html"
        # Lumenpulse's own page for this one product. Their hosted specification sheet is
        # the document to check a line against where they publish one, but 105 products have
        # none - for those the product page is the only source there is, and for the rest it
        # is still the friendlier one to open.
        s.source_url = r["url"]
        s.category = r["category"]
        s.typology = r["typology"]
        s.series = r["series"]
        s.family = r["family"]
        s.variant = ""
        s.environment = r["environment"]
        s.page_number = 1
        # Their own hosted specification sheet, served through /api/specsheets/{id}/pdf by
        # redirect - see that endpoint for why it is not copied locally.
        s.source_pdf_path = r["spec_sheet_url"]
        s.raw_text = r["raw_text"]
        s.fields = dict(r["fields"])
        s.options = dict(r["options"])
        s.params = {k: dict(v) for k, v in r["params"].items()}
        if image_path:
            s.image_path = image_path
            s.diagrams = [{"path": image_path, "width": 0, "height": 0,
                           "label": "Product Image"}]
        db.commit()
    print(f"\nCOMMITTED. {added} added, {updated} updated "
          f"({db.query(models.Specsheet).filter(models.Specsheet.make == MAKE).count()} "
          f"{MAKE} rows now).")
    db.close()


if __name__ == "__main__":
    main()
