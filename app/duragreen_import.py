"""Bringing a DuraGreen product into the app from its web page or its spec sheet, from NPD (DuraGreen).

On 19 Oct 2026 the whole catalogue came in through four scripts run by hand: a crawl of the
website, a builder that read every spec sheet, a backfill that turned each reading into a
product, and the code-rule backfill. A product DuraGreen launches after that should not need a
developer. This module does the same work for one product page, or one spec sheet PDF, at a time:

    a link   https://duragreenlighting.co.uk/product/evoline/
             -> every variant card on that page (each one a product in the app: "1 Spot Fixed
                Line", "2 Spot Fixed Line" ...), each with its spec sheet downloaded and read
    a PDF    -> one product, named by the person, read from the sheet alone

It reads by exactly the rules the scripts used - they are the same functions, in
app/duragreen_sheet.py - so a product added here is the product the backfill would have made.

Two steps, always: `preview` reads and stores the result under a token and changes nothing in
the database; the person looks at what was found (new or an update, how many codes, what
changes) and `apply` writes only the products they ticked. A new product arrives "pending", as
the website's new products did on 19 Oct: nobody can quote it until somebody approves it.

What a reading never touches: the product's prices (DuraGreen's website and sheets publish none -
a price comes from a person, and changes only where they type one into the preview row; see
main._dg_import_prices), its approval, a code a person typed in ("entered by ..."), and the codes
and code template that came from the 2026 General Catalogue.
A reading that finds no codes leaves the product's codes as they are - a sheet that failed to
read must not wipe what a good one gave.
"""
import json
import re
import secrets
import shutil
import time
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urljoin, urlparse

import httpx

from app import duragreen
from app.database import DATA_DIR, IMAGES_DIR, UPLOADS_DIR
from app.duragreen import MAKE
from app.duragreen_sheet import (
    CANONICAL, CONTROL_SYNONYMS, MOUNT_WORDS, OPTIC_WORDS, SITE_TYPOLOGY, WEB_LABELS, build_product,
    decompose, first_word, norm_cct, parse_code_tables, parse_finishes, parse_legend, parse_matrix,
    ip_options, merged_options, parse_specs, spec_values, unique, parse_track_parts, read_pdf, sheet_lines, cct_options_from, split_controls,
    split_list, split_shared_sheet,
)

SITE_HOSTS = {"duragreenlighting.co.uk", "www.duragreenlighting.co.uk"}
# DuraGreen's site sits behind a browser check that turns away a client announcing itself as a
# script (a plain request was answered 403 "Checking your browser" on 21 Oct); one that sends what
# a browser sends is served the page. One page and its few sheets per import, a pause between.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}
TIMEOUT = 60
PAUSE = 0.5
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024

PREVIEW_DIR = DATA_DIR / "duragreen_imports"
KEEP_SECONDS = 2 * 24 * 3600

# The sources of a code part read from a spec sheet or a web card - what a fresh reading may
# replace. A part a person entered ("entered by ...") is never one of these.
SHEET_PART_SOURCES = {"order_codes", "code_legend", "sheet_spec", "sheet_components", "website",
                      "sheet_finishes"}
# Parts the code-rule backfill removed on 19 Oct as the old range matrix's, never DuraGreen's
# word for this product (backfill_duragreen_code_rules.STALE_SOURCES / STALE_STATUSES). A fresh
# reading leaves them out too, so an imported product is the product as it stands today.
STALE_SOURCES = {"code_matrix"}
STALE_STATUSES = {"range", "matched"}


class ImportProblem(Exception):
    """Something the person can act on, said in their words."""


# ------------------------------------------------------------------ fetching, from DuraGreen only

def check_url(url):
    """The product page's address, or ImportProblem. Only DuraGreen's own site is ever fetched -
    the server must not become a way to fetch any address someone types."""
    url = str(url or "").strip()
    if not url:
        raise ImportProblem("Paste the address of the product's page on DuraGreen's website.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    u = urlparse(url)
    if (u.hostname or "").lower() not in SITE_HOSTS:
        raise ImportProblem("That is not a DuraGreen address - it should start with "
                            "https://duragreenlighting.co.uk/product/")
    if not u.path.startswith("/product/") or u.path.rstrip("/") == "/product":
        raise ImportProblem("That is a DuraGreen page, but not a product's page - open the product on "
                            "the website and copy the address from there (it contains /product/).")
    return "https://%s%s" % (u.hostname.lower(), u.path if u.path.endswith("/") else u.path + "/")


def _fetch(url, limit, what):
    """The body of a DuraGreen address, following its redirects only while they stay on
    DuraGreen's site. Returns (final url, bytes).

    The site's browser check also turns a burst of requests away for a while (seen on 21 Oct:
    a page's sheets and pictures, fetched twice in a few minutes, came back refused, and a
    minute later were served again). So a refusal is tried once more after a pause before it is
    reported - a product must not come in without its sheet because of a passing refusal."""
    try:
        return _fetch_once(url, limit, what)
    except _Refused:
        time.sleep(RETRY_AFTER)
        try:
            return _fetch_once(url, limit, what)
        except _Refused as e:
            raise ImportProblem(str(e))


RETRY_AFTER = 8


class _Refused(Exception):
    """The site turned the request away for now (its browser check, 429 or 503)."""


def _fetch_once(url, limit, what):
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=False) as client:
        for _hop in range(6):
            host = (urlparse(url).hostname or "").lower()
            if urlparse(url).scheme != "https" or host not in SITE_HOSTS:
                raise ImportProblem("DuraGreen's website sent the %s request elsewhere (%s) - not followed."
                                    % (what, url))
            try:
                with client.stream("GET", url) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        head = b"".join(r.iter_bytes())[:4000] if r.status_code in (403, 503) else b""
                        if b"Checking your browser" in head or r.status_code in (429, 503):
                            raise _Refused(
                                "DuraGreen's website turned the request for the %s away for now (it does this "
                                "after many requests in a row). Try again in a few minutes, or download the spec "
                                "sheet from the website yourself and add it with “From a spec sheet PDF”." % what)
                        raise ImportProblem("DuraGreen's website answered %s for the %s (%s)."
                                            % (r.status_code, what, url))
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body.extend(chunk)
                        if len(body) > limit:
                            raise ImportProblem("The %s is larger than %d MB - not read." % (what, limit >> 20))
                    return url, bytes(body)
            except httpx.HTTPError as e:
                raise ImportProblem("Could not reach DuraGreen's website for the %s: %s" % (what, e))
        raise ImportProblem("DuraGreen's website redirected the %s too many times." % what)


# ------------------------------------------------------------------ reading the page, without a browser
#
# The 19 Oct crawl drove a real browser (Playwright) and BeautifulSoup. Neither is installed on
# the server, and neither is needed: the page's HTML already carries every variant card and its
# spec pop-up, and the pictures' real addresses sit in data-lzl-src (the site swaps them in when
# a card scrolls into view). So the page is read with the standard library's own HTML parser,
# into a small tree that answers the same questions the crawl's selectors asked.

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
         "source", "track", "wbr"}


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def has_class(self, *names):
        mine = (self.attrs.get("class") or "").split()
        return all(n in mine for n in names)

    def walk(self):
        """Every node and string under this one, in document order (this node first)."""
        yield self
        for c in self.children:
            if isinstance(c, _Node):
                yield from c.walk()
            else:
                yield c

    def find_all(self, test):
        return [n for n in self.walk() if isinstance(n, _Node) and n is not self and test(n)]

    def find(self, test):
        return next((n for n in self.walk() if isinstance(n, _Node) and n is not self and test(n)), None)

    def text(self):
        """BeautifulSoup's get_text(" ", strip=True): every string trimmed, empty ones dropped."""
        return " ".join(s.strip() for s in self.walk() if isinstance(s, str) and s.strip())


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {}, None)
        self.stack = [self.root]
        self.skip = 0                     # inside <script> or <style>: not page text

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag in ("script", "style"):
            self.skip += 1
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, {k: (v if v is not None else "") for k, v in attrs},
                                             self.stack[-1]))

    def handle_endtag(self, tag):
        # Close the nearest open element of this name; a stray end tag is ignored.
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                if tag in ("script", "style"):
                    self.skip = max(0, self.skip - 1)
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip:
            self.stack[-1].children.append(data)


def _parse_html(html):
    b = _TreeBuilder()
    b.feed(html)
    b.close()
    return b.root


def _img_src(img):
    if img is None:
        return None
    for key in ("data-lzl-src", "src"):
        v = img.attrs.get(key) or ""
        if v.startswith("http"):
            return v
    return None


def _read_card(li, root):
    """One variant card and its spec pop-up, kept as printed - the crawl's parse_card, read the
    same way (labels, their order, the footnotes, every download by its own label)."""
    h2 = li.find(lambda n: n.tag == "h2" and n.has_class("woocommerce-loop-product__title"))
    title = h2.text() if h2 is not None else ""
    if not title:
        return None
    btn = li.find(lambda n: n.tag == "a" and n.has_class("add_to_cart_button") and "data-product_id" in n.attrs)
    vid = btn.attrs["data-product_id"] if btn is not None else None
    tag = li.find(lambda n: n.has_class("variation_title_tagline"))
    holder = li.find(lambda n: n.has_class("vci-image"))
    card = {
        "name": title,
        "variation_id": vid,
        "tagline": tag.text() if tag is not None else "",
        "finishes": [n.attrs["data-tooltip"].strip() for n in li.find_all(
            lambda n: n.has_class("vci-nav-image") and (n.attrs.get("data-tooltip") or "").strip())],
        "image_url": _img_src(holder.find(lambda n: n.tag == "img")) if holder is not None else None,
        "spec": [],
        "notes": [],
        "downloads": [],
    }
    modal = root.find(lambda n: n.attrs.get("id") == "oz-variation-modal-%s" % vid) if vid else None
    if modal is not None:
        for row in modal.find_all(lambda n: n.tag == "tr"):
            cells = [c for c in row.children if isinstance(c, _Node) and c.tag == "td"]
            if len(cells) == 2:
                label = cells[0].text().rstrip(":").strip()
                value = cells[1].text()
                if label and value:
                    card["spec"].append([label, value])
        for a in modal.find_all(lambda n: n.tag == "a" and n.attrs.get("href")):
            href = a.attrs["href"]
            if href.startswith("http") and "/wp-content/uploads/" in href:
                card["downloads"].append({"label": a.text(), "url": href})
        table = modal.find(lambda n: n.tag == "table")
        if table is not None:
            # every string from the table onwards, as the crawl's find_all_next(string=True) did
            after = False
            for item in root.walk():
                if item is table:
                    after = True
                if not after or not isinstance(item, str):
                    continue
                t = " ".join(item.split())
                if t.startswith("*") and len(t) > 10 and t not in card["notes"]:
                    card["notes"].append(t)
                if t in ("Contact Us",):
                    break
    return card


def read_page(html, url):
    """A product page as the crawl kept it: title, category, description and its variant cards."""
    root = _parse_html(html)
    h1 = root.find(lambda n: n.tag == "h1")
    crumb = root.find(lambda n: n.has_class("woocommerce-breadcrumb"))
    crumbs = [a.text() for a in crumb.find_all(lambda n: n.tag == "a")] if crumb is not None else []
    desc = root.find(lambda n: n.has_class("woocommerce-product-details__short-description")) or \
        root.find(lambda n: n.has_class("elementor-widget-woocommerce-product-short-description"))
    cards = [c for li in root.find_all(lambda n: n.tag == "li" and n.has_class("product", "product-type-variation"))
             if (c := _read_card(li, root)) is not None]
    return {"url": url, "title": h1.text() if h1 is not None else "",
            "breadcrumb": crumbs[1:] if crumbs[:1] == ["Home"] else crumbs,
            "description": desc.text() if desc is not None else "", "cards": cards}


def card_row(page, card):
    """One variant card -> the product it is, the way the 19 Oct build read a card
    (build_duragreen_catalogue_db._card_row): what the card's spec table says, and the spec sheet
    it offers. Which app product it is, if any, is found by the caller."""
    spec = {label: value for label, value in card["spec"]}
    fields = {}
    for label, value in spec.items():
        canon = WEB_LABELS.get(label.upper())
        if canon and canon not in fields:
            fields[canon] = value
    watts = re.findall(r"\d+(?:\.\d+)?W", card.get("tagline") or "", re.I)
    if watts:
        fields["wattage"] = watts[0]
    options = {}
    if watts:
        options["wattage"] = watts
    if "cct" in fields:
        options["cct"] = cct_options_from(fields["cct"])
    if "beam_angle" in fields:
        options["beam_angle"] = unique(v for value in split_list(fields["beam_angle"]) for v in spec_values("beam_angle", value))
    if "control" in fields:
        options["control"] = unique(v for p in fields["control"].split(",") for v in spec_values("control", p))
    if fields.get("cri"):
        fields["cri"] = " / ".join(spec_values("cri", fields["cri"]))
    if len(ip_options(fields.get("ip_rating"))) > 1:
        options["ip_rating"] = ip_options(fields["ip_rating"])
        fields["ip_rating"] = " / ".join(options["ip_rating"])
    size = re.match(r"^(\d+)\s*(.*)$", card["name"])
    sheet = next((d["url"] for d in card["downloads"]
                  if d["url"].lower().endswith(".pdf") and "specification" in d["label"].lower()), "")
    category = (page.get("breadcrumb") or [""])[0]
    return {
        "name": card["name"], "series": page["title"].strip(),
        "family": "%smm" % size.group(1) if size else "",
        "variant": size.group(2).strip() if size else card["name"],
        # The website's own category where it names a class; otherwise a lighting designer's reading
        # of the product's words (a pendant filed under "Suspension" is a Pendant).
        "category": category,
        "typology": SITE_TYPOLOGY.get(category, "") or duragreen.typology_for(page["title"], card["name"], category),
        "fields": fields, "options": options, "params": spec, "spec_sheet": sheet,
        "web": {"url": page["url"], "card_name": card["name"], "image_url": card.get("image_url") or "",
                "spec": json.dumps(card["spec"]), "notes": json.dumps(card.get("notes") or []),
                "downloads": json.dumps(card["downloads"])},
    }


def pdf_name(url):
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


# ------------------------------------------------------------------ reading the sheet into a product

def read_sheet(row, pdf_path):
    """One product's spec sheet read as build_duragreen_catalogue_db.build() read each product
    (its step 4), held in memory instead of SQLite: (product columns, options, code-table rows,
    whether the sheet could be read). `row` is a card_row() (or a bare one for a PDF alone).

    The range matrix is applied only when this sheet prints it itself. On 19 Oct a product could
    also take its range's matrix from a sister product's sheet, but every part that gave was
    removed again by the code-rule backfill (STALE_*), and nothing else it gave was offered."""
    pages, read_ok = [], False
    if pdf_path:
        try:
            pages = read_pdf(pdf_path)
            read_ok = True
        except Exception:  # noqa: BLE001 - an unreadable sheet is reported, not fatal
            pages = []
    product, opts, codes_out = {}, {}, []
    if row["typology"] == "Driver":
        return product, [], [], read_ok

    def add_option(attribute, value, code, source, status, note=None):
        value = (value or "").strip()
        if not value:
            return
        o = opts.get((attribute, value))
        if o is None:
            opts[(attribute, value)] = {"attribute": attribute, "value": value, "code": code,
                                        "source": source, "status": status, "note": note}
        else:
            # ON CONFLICT ... code = coalesce(code, excluded.code), note = coalesce(note, excluded.note)
            if o["code"] is None:
                o["code"] = code
            if o["note"] is None:
                o["note"] = note

    # the sheet's own code matrix (ACME and MOOD sheets print one)
    rng = (row["series"].split() or ["?"])[0].upper()
    segments = None
    for n, p in enumerate(pages, start=1):
        if "Specification Code" in p["text"] and segments is None:
            segments = parse_matrix(pdf_path, n)
    seg = {}
    if segments:
        for _order, segname, _ex, entries in segments:
            for code, meaning in entries:
                seg.setdefault(segname, {})[meaning.lower()] = code

    text = "\n".join(p["text"] for p in pages)
    lines = sheet_lines(pages)
    pdf_spec = {}
    for _key, canon, value in parse_specs(lines):
        if canon and canon not in pdf_spec:
            pdf_spec[canon] = value
    web_spec = {}
    for key, value in row["params"].items():
        canon = CANONICAL.get(key.rstrip("*").strip().lower())
        if canon and canon not in web_spec:
            web_spec[canon] = value

    def spec(canon):
        return pdf_spec.get(canon) or web_spec.get(canon)

    def spec_source(canon):
        return "sheet_spec" if pdf_spec.get(canon) else "website"

    codes = parse_code_tables(lines)
    _example, legend = parse_legend(lines)
    cutouts = sorted({(lambda c: c if c.endswith("mm") else c + "mm")(re.sub(r"\s", "", c)) for c in
                      re.findall(r"cut\s*out\s*[:：]\s*([\d.]+\s*(?:[*xX×]\s*[\d.]+)?\s*mm|[\d.]+)", text, re.I)})
    size = row["family"] or (re.search(r"[ФΦØ]\s?(\d+)", text).group(1) + "mm"
                             if re.search(r"[ФΦØ]\s?(\d+)", text) else None)
    prefixes = {m.group(0) for c in codes if (m := re.match(r"^[A-Z]+", c["code"]))}
    project = re.search(r"DuraGreen project:\s*\n?\s*([^\n]+)", text)
    product.update({
        "name": row["name"], "size": size,
        "optic_type": first_word(row["name"] + " " + row["variant"], OPTIC_WORDS),
        "mounting_style": first_word(row["name"] + " " + row["variant"] + " " + row["typology"], MOUNT_WORDS),
        "cutout": " / ".join(cutouts) or None, "ceiling_thickness": spec("ceiling_thickness"),
        "ip_rating": spec("ip_rating"), "ugr": spec("ugr"), "cri": spec("cri"),
        "cri_detail": pdf_spec.get("cri") if "/" in (pdf_spec.get("cri") or "") else None,
        "lifetime": spec("lifetime"), "efficacy": spec("efficacy"), "binning": spec("binning"),
        "input_voltage": spec("input_voltage"), "output": spec("output"), "ambient_temp": spec("ambient_temp"),
        "class": spec("class"), "material": spec("material"), "certifications": spec("certifications"),
        "project_reference": project.group(1).strip() if project else None,
        "source_document_id": 1 if pdf_path else None,
    })

    for c in codes:
        parts = decompose(c["code"], c["wattage"], c["optics"]) or {}
        codes_out.append({
            "code": c["code"], "wattage": c["wattage"], "lumens": c["lumens"], "lumens_92ra": c["lumens_92ra"],
            "lumens_98ra": c["lumens_98ra"], "efficacy": c["efficacy"], "optics": c["optics"], "cct": c["cct"],
            "cri": c["cri"], "length": c["length"], "spots": c["spots"], "input_voltage": c["input_voltage"],
            "extra": c["extra"], "type_code": parts.get("type_code"), "size_code": parts.get("size_code"),
            "wattage_code": parts.get("wattage_code"), "variant_code": parts.get("variant_code"),
            "cct_prefix": parts.get("cct_prefix") or None, "cct_code": parts.get("cct_code"),
            "control_code": parts.get("control_code"), "beam_prefix": parts.get("beam_prefix") or None,
            "beam_code": parts.get("beam_code"), "suffix_code": parts.get("suffix_code"),
            "decomposed": 1 if parts else 0, "version_label": c.get("version") or None,
        })
        add_option("wattage", c["wattage"], parts.get("wattage_code"), "order_codes", "stated")
        if c["optics"]:
            add_option("beam_angle", c["optics"],
                       ((parts.get("beam_prefix") or "") + parts["beam_code"]) if parts.get("beam_code") else None,
                       "order_codes", "stated",
                       "printed with an '%s' in front, which Duragreen does not decode" % parts["beam_prefix"]
                       if parts.get("beam_prefix") else None)
        if c["cct"]:
            add_option("cct", c["cct"],
                       ((parts.get("cct_prefix") or "") + parts["cct_code"]) if parts.get("cct_code") else None,
                       "order_codes", "stated",
                       "printed with an 'H' in front, which Duragreen does not decode"
                       if parts.get("cct_prefix") else None)

    legend_codes = {}
    for _pos, tok, meaning, attr in legend:
        if attr in ("cct", "control", "beam_angle", "bezel_finish", "body_finish", "baffle_finish",
                    "accessory", "type", "size"):
            value = meaning
            if attr == "cct":
                value = (re.search(r"\d{4}K(?:-\d{4}K)?", meaning, re.I) or [meaning])[0].upper()
            elif attr == "beam_angle":
                value = (re.search(r"\d+°", meaning) or [meaning])[0]
            elif attr == "accessory":
                value = re.sub(r"[（(]optional[)）]", "", meaning, flags=re.I).strip()
            elif attr == "size":
                mm = re.search(r"\d+(?:[*xX/]\d+)?\s?mm", meaning)
                value = mm.group(0).replace(" ", "") if mm else meaning
            elif attr in ("bezel_finish", "body_finish", "baffle_finish"):
                value = re.sub(r"\b(bezel|structure|baffle)\b", "", meaning, flags=re.I).strip().title()
            elif attr == "control":
                target = next((b for a, b, _ in CONTROL_SYNONYMS if a.lower() == meaning.lower()), None)
                same = [v for v in split_controls(spec("control")) if target and
                        next((b for a, b, _ in CONTROL_SYNONYMS if a.lower() == v.lower()), None) == target]
                if same:
                    legend_codes[("control", same[0])] = (tok, "legend: %s / %s" % (tok, meaning))
                    continue
            add_option(attr, value, tok, "code_legend", "stated")

    for v in cct_options_from(spec("cct")):
        add_option("cct", v, None, spec_source("cct"), "stated")
    for v in split_controls(spec("control")):
        code, note = legend_codes.get(("control", v), (None, None))
        add_option("control", v, code, spec_source("control"), "stated", note)
    for v in split_list(spec("beam_angle")):
        if re.search(r"\d", v):
            add_option("beam_angle", v, None, spec_source("beam_angle"), "stated")
    for v in split_list(spec("accessories"), seps=(",",)):
        add_option("accessory", v, None, spec_source("accessories"), "stated")
    if spec("ip_rating"):
        add_option("ip_rating", spec("ip_rating"), None, spec_source("ip_rating"), "stated")
    cri_vals = set(re.findall(r"\((\d{2})Ra\)", " ".join(c["header"] for c in codes)))
    first = re.search(r"\b(\d{2})\b", spec("cri") or "")
    if first:
        cri_vals.add(first.group(1))
    for v in sorted(cri_vals):
        add_option("cri", "CRI " + v, None, "sheet_spec", "stated")

    def bare(v):                                  # SQLite's replace(replace(v, ' ', ''), 'K', '')
        return (v or "").replace(" ", "").replace("K", "")

    for field in ("wattage", "cct", "beam_angle"):
        for v in row["options"].get(field) or []:
            have = [o["value"] for o in opts.values() if o["attribute"] == field]
            if field == "wattage":
                base = lambda x: re.sub(r"\s*x\s*\d+$", "", (x or "").strip())  # noqa: E731 - base_watt
                if base(v) in {base(x) for x in have}:
                    continue
            if not any(bare(x) == bare(v) for x in have):
                add_option(field, v, None, "website", "stated", "on the website, not in this spec sheet's own tables")

    if segments:
        basis, mname = "stated", rng
        cct_codes = {norm_cct(k): v for k, v in seg.get("Colour Temp", {}).items()}
        for o in [o for o in opts.values() if o["attribute"] == "cct"]:
            code = cct_codes.get(norm_cct(o["value"]))
            if code:
                o["code"] = o["code"] if o["code"] is not None else code
                o["note"] = o["note"] if o["note"] is not None else "code from the %s matrix (%s)" % (mname, basis)
        drv = seg.get("Driver", {})
        for o in [o for o in opts.values() if o["attribute"] == "control"]:
            v = o["value"]
            target = next((b for a, b, _ in CONTROL_SYNONYMS if a.lower() == v.lower()), None)
            code = drv.get(target.lower()) if target else None
            if code:
                status_note = "same words" if target.lower() == v.lower() else "matched: sheet '%s' = matrix '%s'" % (v, target)
                o["code"] = o["code"] if o["code"] is not None else code
                o["note"] = o["note"] if o["note"] is not None else "%s (%s matrix, %s)" % (status_note, mname, basis)
                if target.lower() != v.lower():
                    o["status"] = "matched"
        for segname, attr in (("Bezel Colour", "bezel_finish"), ("Baffle Colour", "baffle_finish"),
                              ("Options", "accessory"), ("Type", "type")):
            for meaning, code in seg.get(segname, {}).items():
                if attr == "type" and code not in prefixes:
                    continue
                add_option(attr, meaning.title() if attr != "type" else meaning, code, "code_matrix", basis,
                           "%s matrix" % mname)

    for palette, name, ral, moq, fcode in parse_finishes(lines, text):
        add_option("finish", name, fcode, "sheet_finishes", "stated",
                   palette + (", RAL %s" % ral if ral else "") + (", MOQ %s" % moq if moq else ""))
    for sku in re.findall(r"SKU\s*[:：]\s*([A-Z0-9][A-Z0-9\-]+)", text):
        kind = {"EN": "Light engine", "BZ": "Bezel", "BF": "Baffle"}.get(sku.split("-")[0], None)
        if kind == "Bezel" and sku.endswith("-TL"):
            kind = "Bezel (trimless)"
        add_option("component", "%s %s" % (kind or "Part", sku), sku, "sheet_components", "stated")
    for sku, name, finishes in parse_track_parts(lines):
        add_option("component", "%s %s" % (name or "Track part", sku), sku, "sheet_components", "stated",
                   "finish letter %s" % finishes if finishes else None)
    return product, list(opts.values()), codes_out, read_ok


def build(row, pdf_path, sheet_name):
    """read_sheet() turned into what the app stores, by the backfill's own build_product():
    {fields, options, params, codes, parts, pdf_name}."""
    product, opts, codes, read_ok = read_sheet(row, pdf_path)
    cat = {"products": {1: product} if product else {}, "options": {1: opts}, "codes": {1: codes},
           "docs": {1: {"file_name": sheet_name, "read_ok": 1 if read_ok else 0}}}
    split_shared_sheet(cat)
    built = build_product(cat, 1, row["web"])
    built["parts"] = [p for p in built["parts"]
                      if p["source"] not in STALE_SOURCES and p["status"] not in STALE_STATUSES]
    built["read_ok"] = read_ok
    return built


def name_unknown_configs(built, rules, fields, options):
    """A printed code whose CCT or driver the sheet did not decode ("?30"): named when exactly one
    of the product's options takes that code under DuraGreen's rules - what the code-rule backfill
    did for the 19 Oct products (backfill_duragreen_code_rules.py)."""
    sheet = SimpleNamespace(options=options, fields=fields)
    rows = [SimpleNamespace(code=c["code"], config=c["config"], parts=c["parts"]) for c in built["codes"]]
    parts = [SimpleNamespace(attribute=p["attribute"], value=p["value"], code=p["code"], status=p["status"],
                             source=p["source"]) for p in built["parts"]]
    own = {}
    for field, meaning, token, src in duragreen._own_meanings(rows, parts, None):
        own.setdefault(field, []).append((meaning, token, src))
    named = 0
    for c in built["codes"]:
        for k, v in list(c["config"].items()):
            if not str(v).startswith("?"):
                continue
            # one rule for reading a printed piece, shared with the code-rule backfill
            value = duragreen.decode_piece(sheet, k, v[1:], own.get(k, []), rules)
            if value:
                c["config"][k] = value
                named += 1
    return named


# ------------------------------------------------------------------ pictures

def fetch_image(url, slug):
    """A card's picture into data/images, or "" - no picture beats a broken one."""
    if not url:
        return ""
    name = "duragreen_" + re.sub(r"[^A-Za-z0-9_-]+", "_", slug)[:80] + (Path(url.split("?")[0]).suffix or ".png")
    dest = IMAGES_DIR / name
    if dest.exists():
        return str(dest)
    try:
        _final, data = _fetch(url, MAX_IMAGE_BYTES, "picture")
    except ImportProblem:
        return ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return str(dest)


def picture_from_pdf(pdf_path, slug):
    """The largest picture on a sheet's first page, for a product added from its PDF alone."""
    try:
        import fitz
        with fitz.open(pdf_path) as doc:
            if not len(doc):
                return ""
            best = None
            for img in doc[0].get_images(full=True):
                w, h = img[2], img[3]
                if w >= 120 and h >= 120 and (best is None or w * h > best[1]):
                    best = (img[0], w * h)
            if best is None:
                return ""
            pix = fitz.Pixmap(doc, best[0])
            if pix.n - pix.alpha >= 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            name = "duragreen_" + re.sub(r"[^A-Za-z0-9_-]+", "_", slug)[:80] + ".png"
            dest = IMAGES_DIR / name
            n = 2
            while dest.exists():
                dest = IMAGES_DIR / name.replace(".png", "_%d.png" % n)
                n += 1
            dest.parent.mkdir(parents=True, exist_ok=True)
            pix.save(str(dest))
            return str(dest)
    except Exception:  # noqa: BLE001 - a picture is a nicety; the product still comes in
        return ""


# Parts a sheet draws only inside its system drawing, with no illustration of their own: the part
# as drawn there, cropped - (page, rect in PDF points), checked by eye 27 Oct 2026. Keyed by the
# sheet's file name and the product's name.
PICTURE_CROPS = {
    ("Surface-Micro-11-1.pdf", "Micro 11 Horizontal Corner Joint"): (2, (164, 226, 230, 274)),
    ("Surface-Micro-11-1.pdf", "Micro 11 Vertical Corner Joint"): (2, (392, 312, 448, 362)),
    ("Surface-Micro-11-1.pdf", "Micro 11 End cap"): (2, (404, 504, 439, 523)),
}
LABEL_REACH = 30        # points: a sheet prints a part's name right beside its drawing


def _label(page, name):
    """Where `name` is printed on the page, trying it whole and then without its leading words
    ("Micro 11 Wired concealed End feed" is printed "Wired concealed End feed"), or None."""
    words = name.split()
    for i in range(0, max(1, len(words) - 1)):
        hits = page.search_for(" ".join(words[i:]))
        if hits:
            return hits[0]
    return None


def _distance(label, rect):
    cx, cy = (label.x0 + label.x1) / 2, (label.y0 + label.y1) / 2
    dx = max(rect.x0 - cx, 0, cx - rect.x1)
    dy = max(rect.y0 - cy, 0, cy - rect.y1)
    return (dx * dx + dy * dy) ** 0.5


def _image_pixmap(fitz, doc, xref, smask):
    pix = fitz.Pixmap(doc, xref)
    if smask:
        mask = fitz.Pixmap(doc, smask)
        if (mask.width, mask.height) == (pix.width, pix.height):
            pix = fitz.Pixmap(pix, mask)
    if pix.colorspace and pix.colorspace.n >= 4:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix


def picture_for(pdf_path, name, siblings, slug, folder=None):
    """A product's picture taken from its own spec sheet: (saved path, how) or ("", why not).

    1. A crop listed in PICTURE_CROPS.
    2. The drawing beside the product's own name - a sheet showing a system's parts (MINI-LINE 11)
       draws each part beside its name. Only a drawing that lies on the page, is not the system
       drawing behind every label (over 30% of the page), is within LABEL_REACH of the name, and
       is nearer to this name than to any sibling's (`siblings`: the other products on the same
       sheet) - so a part is never given its neighbour's drawing.
    3. A sheet of this product alone (no siblings): the largest picture on its first page.
    Anything else gets no picture: a shared sheet's largest picture is the whole system."""
    try:
        import fitz
    except ImportError:
        return "", "PyMuPDF is not installed"
    folder = Path(folder) if folder else IMAGES_DIR
    try:
        with fitz.open(pdf_path) as doc:
            crop = PICTURE_CROPS.get((Path(pdf_path).name, name))
            if crop:
                page = doc[crop[0] - 1]
                return _save_png(page.get_pixmap(dpi=300, clip=fitz.Rect(*crop[1])), slug, folder), \
                    "cropped from its sheet's system drawing (p.%d)" % crop[0]
            for page in doc:
                label = _label(page, name)
                if label is None:
                    continue
                others = [lb for lb in (_label(page, s) for s in siblings if s != name) if lb is not None]
                best = None
                for img in page.get_images(full=True):
                    for r in page.get_image_rects(img[0]):
                        area = r.width * r.height
                        if r.width < 60 or r.height < 40 or area > 0.3 * page.rect.width * page.rect.height:
                            continue
                        # a sheet laid out as a spread lets a part's drawing run off its page's edge
                        # (MINI-LINE 11's jointing connector: 62-72% on it); a drawing mostly on the
                        # neighbouring page (its straight track: 28%) is that page's
                        if (r & page.rect).get_area() < 0.6 * area:
                            continue
                        d = _distance(label, r)
                        if d <= LABEL_REACH and all(_distance(o, r) >= d for o in others) \
                                and (best is None or d < best[0]):
                            best = (d, img[0], img[1])
                if best is not None:
                    pix = _image_pixmap(fitz, doc, best[1], best[2])
                    return _save_png(pix, slug, folder), "the drawing beside its name (p.%d)" % (page.number + 1)
            if siblings:
                return "", "its sheet shows %d products and no drawing beside this one's name" % (len(siblings) + 1)
            cand = [(i[2] * i[3], i[0], i[1]) for i in doc[0].get_images(full=True) if i[2] >= 120 and i[3] >= 120]
            if not cand:
                return "", "its sheet has no picture on its first page"
            _area, xref, smask = max(cand)
            return _save_png(_image_pixmap(fitz, doc, xref, smask), slug, folder), "the largest picture on its sheet"
    except Exception as e:  # noqa: BLE001 - a picture is a nicety; nothing else depends on it
        return "", "the sheet could not be read: %s" % e


def _save_png(pix, slug, folder):
    name = "duragreen_" + re.sub(r"[^A-Za-z0-9_-]+", "_", slug)[:80] + ".png"
    dest = folder / name
    n = 2
    while dest.exists():
        dest = folder / name.replace(".png", "_%d.png" % n)
        n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(dest))
    return str(dest)


# ------------------------------------------------------------------ the preview, kept under a token

def _tidy():
    """Previews nobody applied are removed after KEEP_SECONDS."""
    if not PREVIEW_DIR.exists():
        return
    cutoff = time.time() - KEEP_SECONDS
    for d in PREVIEW_DIR.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def new_token():
    _tidy()
    token = secrets.token_hex(12)
    (PREVIEW_DIR / token).mkdir(parents=True, exist_ok=True)
    return token


def token_dir(token):
    if not re.fullmatch(r"[0-9a-f]{24}", str(token or "")):
        raise ImportProblem("That preview is not valid any more - read the page or the PDF again.")
    d = PREVIEW_DIR / token
    if not (d / "preview.json").exists():
        raise ImportProblem("That preview has expired - read the page or the PDF again.")
    return d


def save_preview(token, preview):
    (PREVIEW_DIR / token / "preview.json").write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")


def load_preview(token):
    return json.loads((token_dir(token) / "preview.json").read_text(encoding="utf-8"))


def read_from_url(url, token):
    """Every variant card on a DuraGreen product page, with its spec sheet downloaded into the
    preview's folder: (page, [(row, pdf path or None, sheet name or None, warnings)]). Building
    each into a product is left to the caller (build()), which first finds out whether the app
    already has it - an existing product's own typology is what its sheet is read with."""
    url = check_url(url)
    final, body = _fetch(url, MAX_PAGE_BYTES, "page")
    page = read_page(body.decode("utf-8", errors="replace"), final)
    if not page["cards"]:
        if urlparse(final).path in ("", "/"):
            raise ImportProblem("DuraGreen's website sent that address to its home page - the product "
                                "has probably been retired or renamed.")
        raise ImportProblem("No products were found on that page. If it is a category page, open one "
                            "product and use that product's address.")
    sheets = {}                                   # one download per distinct spec sheet
    out = []
    for card in page["cards"]:
        row = card_row(page, card)
        warnings, path, name = [], None, None
        if row["spec_sheet"]:
            name = pdf_name(row["spec_sheet"])
            if row["spec_sheet"] not in sheets:
                try:
                    time.sleep(PAUSE)
                    _final, data = _fetch(row["spec_sheet"], MAX_PDF_BYTES, "spec sheet")
                    if not data.startswith(b"%PDF"):
                        raise ImportProblem("the spec sheet link did not give a PDF")
                    dest = PREVIEW_DIR / token / ("sheet_%d.pdf" % len(sheets))
                    dest.write_bytes(data)
                    sheets[row["spec_sheet"]] = (str(dest), "")
                except ImportProblem as e:
                    sheets[row["spec_sheet"]] = (None, str(e))
            path, problem = sheets[row["spec_sheet"]]
            if problem:
                warnings.append("Spec sheet not read: %s" % problem)
        else:
            warnings.append("The website offers no spec sheet for this product - only its web card was read.")
        out.append((row, path, name, warnings))
    return page, out


def pdf_row(identity):
    """The row for a product added from its spec sheet alone. `identity` is what the person
    typed: series and product_name, and optionally family (size), variant, typology, source_url.
    The size is only what the person typed: the website's rule that a card name's leading number
    is its size ("57 Mini-Trim") reads "1 Spot Fixed Line" as 1mm, and here a person is at hand."""
    return {"name": identity["product_name"], "series": identity["series"], "family": identity.get("family") or "",
            "variant": identity.get("variant") or identity["product_name"],
            "category": "", "typology": identity.get("typology") or "",
            "fields": {}, "options": {}, "params": {}, "spec_sheet": "",
            "web": {"url": identity.get("source_url") or "", "card_name": identity["product_name"], "image_url": "",
                    "spec": "[]", "notes": "[]", "downloads": "[]"}}


def reading_warnings(built, had_pdf, name):
    """What the person should know about a reading before bringing it in."""
    out = []
    if had_pdf and not built["read_ok"]:
        out.append("The spec sheet %s could not be read as a PDF." % (name or ""))
    elif had_pdf and not built["codes"]:
        out.append("No order-code table was found on the spec sheet - the product comes in without codes; "
                   "add DuraGreen's codes by hand in NPD once it is in.")
    undecided = sum(1 for c in built["codes"] if any(str(v).startswith("?") for v in c["config"].values()))
    if undecided:
        out.append("%d of its codes print a CCT or driver letter the sheet does not explain - they are kept, "
                   "but a BOQ line can only use them once that letter is named in NPD." % undecided)
    return out


# ------------------------------------------------------------------ what a reading would change, and writing it

def merged(sheet, built):
    """(fields, options, params) the product will have: the sheet's reading laid over what it
    holds, as backfill_duragreen_npd.py lays it - a field the sheet does not state stays as it
    was, and an option list keeps its order with the sheet deciding what is in it."""
    fields = dict(getattr(sheet, "fields", None) or {})
    fields.update(built["fields"])
    options = dict(getattr(sheet, "options", None) or {})
    for key, new in built["options"].items():
        options[key] = merged_options(options.get(key), new, (getattr(sheet, "price_adders", None) or {}).get(key) or ())
    # The list can hold what the sheet no longer prints (a priced value, a list a person typed), and
    # a line with no IP chosen prints this field, so it says what the list says.
    if options.get("ip_rating"):
        fields["ip_rating"] = " / ".join(options["ip_rating"])
    params = dict(getattr(sheet, "params", None) or {})
    params.update(built["params"])
    return fields, options, params


def is_sheet_code(code_row, sheet_name):
    """A code a fresh reading of `sheet_name` replaces: one read from that same document. Codes
    from the 2026 General Catalogue and codes a person typed in are never replaced."""
    source = str(getattr(code_row, "source", "") or "")
    return bool(sheet_name) and source == sheet_name


def changes(sheet, built, existing_codes, labels):
    """What applying this reading to an existing product would change, for the preview."""
    fields, options, _params = merged(sheet, built)
    before_f, before_o = sheet.fields or {}, sheet.options or {}
    out = {"fields": [], "options": [], "codes_replaced": 0, "codes_kept": 0, "codes_new": len(built["codes"])}
    for k in sorted(set(before_f) | set(fields)):
        if str(before_f.get(k) or "") != str(fields.get(k) or ""):
            out["fields"].append({"key": k, "label": labels.get(k, k), "before": str(before_f.get(k) or ""),
                                  "after": str(fields.get(k) or "")})
    for k in sorted(set(before_o) | set(options)):
        a, b = before_o.get(k) or [], options.get(k) or []
        if a != b:
            out["options"].append({"key": k, "label": labels.get(k, k), "before": a, "after": b})
    if built["codes"]:
        out["codes_replaced"] = sum(1 for c in existing_codes if is_sheet_code(c, built["pdf_name"]))
    out["codes_kept"] = len(existing_codes) - out["codes_replaced"]
    return out


def apply(db, models, sheet, row, built, *, discipline, rules, pdf_ref, picture):
    """Writes one reading. `sheet` is the existing product or None for a new one (created
    pending). `pdf_ref` is what source_pdf_path should hold (DuraGreen's link, or the stored
    upload), `picture` a callable giving a new product its picture path. Never touches prices or
    an existing product's approval. Returns (sheet, "added" | "updated", codes written)."""
    action = "updated"
    if sheet is None:
        from app.environment_classifier import classify_environment
        sheet = models.Specsheet(
            discipline=discipline, make=MAKE, origin="parsed", approval_status="pending", filename="",
            product_name=row["name"], series=row["series"], family=row["family"], variant=row["variant"],
            category=row["category"] or "", typology=row["typology"] or "",
            environment=classify_environment(row["category"] or ""))
        db.add(sheet)
        action = "added"
    elif row.get("typology_set") and row["typology"] != (sheet.typology or ""):
        sheet.typology = row["typology"]
    fields, options, params = merged(sheet, built)
    sheet.fields, sheet.options, sheet.params = fields, options, params
    if row["web"]["url"]:
        sheet.source_url = row["web"]["url"]
    if built["pdf_name"] and built["read_ok"] and pdf_ref:
        # Only a sheet that was actually read is linked - a product never loses a working sheet
        # to a link that answered 404.
        sheet.source_pdf_path = pdf_ref
        sheet.filename = built["pdf_name"]
    if action == "added":
        path = picture()
        if path:
            sheet.image_path = path
            sheet.diagrams = [{"path": path, "width": 0, "height": 0, "label": "Product Image"}]
    db.flush()

    name_unknown_configs(built, rules, fields, options)
    written = 0
    if built["codes"]:
        current = db.query(models.VendorOrderCode).filter(models.VendorOrderCode.specsheet_id == sheet.id).all()
        # Only a code a person entered stands in for the sheet's own row. A catalogue row is no
        # stand-in: the code-rules backfill deletes and rebuilds those right after, and until
        # 26 Oct 2026 a sheet code skipped "because the catalogue holds it" vanished with it - the
        # STICK bollard's SBG1653-02-5W went to whichever product the previous run had left it on.
        held = set()
        for c in current:
            if is_sheet_code(c, built["pdf_name"]):
                db.delete(c)
            elif str(c.source or "").startswith("entered by"):
                held.add(c.code)
        for c in built["codes"]:
            if c["code"] in held:
                continue                          # a person already entered it
            db.add(models.VendorOrderCode(make=MAKE, specsheet_id=sheet.id, source=built["pdf_name"] or "",
                                          discipline=sheet.discipline or discipline, **c))
            written += 1
    if built["parts"]:
        attrs = {p["attribute"] for p in built["parts"]}
        for p in db.query(models.VendorCodePart).filter(models.VendorCodePart.specsheet_id == sheet.id):
            if p.source in SHEET_PART_SOURCES and p.attribute in attrs:
                db.delete(p)
        for p in built["parts"]:
            db.add(models.VendorCodePart(make=MAKE, specsheet_id=sheet.id,
                                         discipline=sheet.discipline or discipline, **p))
    db.flush()
    # The same No Driver choice the code-rules backfill gives every product (X in every table).
    codes = db.query(models.VendorOrderCode).filter(models.VendorOrderCode.specsheet_id == sheet.id).all()
    with_none = duragreen.with_no_driver(sheet.options, codes)
    if with_none != dict(sheet.options or {}):
        sheet.options = with_none
        db.flush()
    return sheet, action, written


def guess_identity(pdf_path):
    """What a sheet calls itself, to start the form with: its first lines."""
    try:
        lines = sheet_lines(read_pdf(pdf_path))
    except Exception:  # noqa: BLE001
        return {}
    title = [l for l in lines[:6] if not re.search(r"https?://|www\.|@|\btel\b", l, re.I)]
    words = (title[0] if title else "").strip()
    return {"series": words.upper()[:80], "product_name": (title[1] if len(title) > 1 else "").strip()[:120]}
