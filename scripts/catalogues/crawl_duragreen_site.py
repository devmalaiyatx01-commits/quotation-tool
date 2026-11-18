"""Reads DuraGreen's whole website the way a lighting engineer would, into a snapshot file.

Every product page the site's own sitemap lists (not only the ones its category tiles link to,
which is how import_duragreen_catalog.py found 69 families when the sitemap has 324 pages), and
every category page, so each product's category is what the site itself files it under. For
each variant card it keeps what an engineer reads off the page: the full spec table in the
order printed, the footnotes under it ("Listed driver currents apply to non-dimmable drivers
only"), the finish swatches, the tagline's wattages and CRI, the description, and every
download - specification sheet, photometric data, installation guide - by its own label.

Every specification sheet is downloaded too, because the sheet is where the order codes are
(see build_duragreen_catalogue_db.py, which reads them). Photometric zips are recorded by URL
and not downloaded: they are for lighting calculations, not for the catalogue.

Nothing here writes to the app's database. The output is

    Duragreen Catalogue/site_crawl/crawl.json          the snapshot, one entry per page
    Duragreen Catalogue/site_crawl/pdfs/<file>.pdf      every specification sheet

and a re-run resumes: a page already in crawl.json is not fetched again unless --refresh is
given, and a PDF already on disk is not downloaded again.

    python scripts/catalogues/crawl_duragreen_site.py              # resume / complete
    python scripts/catalogues/crawl_duragreen_site.py --refresh    # fetch every page again
    python scripts/catalogues/crawl_duragreen_site.py --refresh --refresh-pdfs   # a full re-scrape
    python scripts/catalogues/crawl_duragreen_site.py --limit 5    # a trial run

A full re-scrape downloads every sheet again too: DuraGreen re-issues sheets under the same file
name, and a sheet kept from an earlier run would hide the change. A changed sheet's old copy is
moved to site_crawl/pdfs_superseded/<date>/ and the run lists it, so its codes can be compared.

Polite by construction: one page at a time, a pause between pages, and only what robots.txt
allows (it disallows only basket, admin and WooCommerce log paths, none of which this reads).
Needs the same local-only tools as the importer: playwright + chromium, beautifulsoup4.
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
_sys.path.insert(0, str(_REPO_ROOT / "scripts" / "catalogues"))
# --- end shim ------------------------------------------------------------------------
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from import_duragreen_catalog import USER_AGENT, _goto_with_retry, _scroll_to_bottom

SITE = "https://duragreenlighting.co.uk"
OUT_DIR = _REPO_ROOT / "Duragreen Catalogue" / "site_crawl"
PDF_DIR = OUT_DIR / "pdfs"
SNAPSHOT = OUT_DIR / "crawl.json"
SUPERSEDED_DIR = OUT_DIR / "pdfs_superseded" / datetime.now().strftime("%Y%m%d")
PAUSE = 1.0         # seconds between pages - one person clicking through, not a load test

HEADERS = {"User-Agent": USER_AGENT}


def _text(el):
    return el.get_text(" ", strip=True) if el is not None else ""


# DuraGreen's site stops answering for about a minute after a burst of requests (22 Oct 2026:
# a 204-product category's pagination timed out mid-crawl). So every plain request waits it out
# and tries again, instead of losing the run.
REFUSED_WAIT = 75     # seconds
ATTEMPTS = 4


def _get(url, timeout=60):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code not in (403, 429, 500, 502, 503, 504) or attempt == ATTEMPTS:
                return r
            why = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            if attempt == ATTEMPTS:
                raise
            why = type(e).__name__
        print(f"    ({why} for {url}; waiting {REFUSED_WAIT}s, attempt {attempt}/{ATTEMPTS})")
        time.sleep(REFUSED_WAIT)


def _goto_patiently(ref, url):
    """A page through the browser, with one more try after the site's refusal minute is over."""
    if _goto_with_retry(ref, url):
        return True
    print(f"    (waiting {REFUSED_WAIT}s before one more try)")
    time.sleep(REFUSED_WAIT)
    return _goto_with_retry(ref, url)


def sitemap_urls(name):
    r = _get(f"{SITE}/{name}")
    r.raise_for_status()
    return [re.sub(r"<!\[CDATA\[|\]\]>", "", u).strip()
            for u in re.findall(r"<loc>(.*?)</loc>", r.text)]


def category_members(url):
    """{product URL} the category page lists, following its own pagination."""
    found, page_url, seen_pages = [], url, set()
    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        r = _get(page_url)
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        main = soup.select_one("main") or soup
        for a in main.select("a[href*='/product/']"):
            href = a["href"].split("?")[0].split("#")[0]
            if href not in found:
                found.append(href)
        nxt = soup.select_one("a.next.page-numbers")
        page_url = nxt["href"] if nxt else None
        time.sleep(PAUSE)
    return found


def parse_card(li, soup):
    """One variant card and its spec pop-up, kept as printed - labels and order included."""
    title = _text(li.select_one("h2.woocommerce-loop-product__title"))
    if not title:
        return None
    btn = li.select_one("a.add_to_cart_button[data-product_id]")
    vid = btn["data-product_id"] if btn else None
    card = {
        "name": title,
        "variation_id": vid,
        "tagline": _text(li.select_one(".variation_title_tagline")),
        "finishes": [s.get("data-tooltip").strip() for s in li.select(".vci-nav-image[data-tooltip]")
                     if (s.get("data-tooltip") or "").strip()],
        "image_url": (li.select_one(".vci-image img") or {}).get("src") if li.select_one(".vci-image img") else None,
        "spec": [],          # [[label, value], ...] in the order the table prints them
        "notes": [],         # footnotes printed under the table
        "downloads": [],     # [{label, url}] - every file the pop-up offers
    }
    modal = soup.select_one(f"#oz-variation-modal-{vid}") if vid else None
    if modal is not None:
        for row in modal.select("table tr"):
            cells = row.select("td")
            if len(cells) == 2:
                label = _text(cells[0]).rstrip(":").strip()
                value = _text(cells[1])
                if label and value:
                    card["spec"].append([label, value])
        for a in modal.select("a[href]"):
            href = a.get("href", "")
            if href.startswith("http") and "/wp-content/uploads/" in href:
                card["downloads"].append({"label": _text(a), "url": href})
        table = modal.select_one("table")
        after = table.find_all_next(string=True) if table is not None else []
        for s in after:
            t = " ".join(s.split())
            if t.startswith("*") and len(t) > 10 and t not in card["notes"]:
                card["notes"].append(t)
            if t in ("Contact Us",):
                break
    return card


def parse_product_page(html, url):
    soup = BeautifulSoup(html, "html.parser")
    h1 = _text(soup.select_one("h1"))
    crumbs = [_text(a) for a in soup.select(".woocommerce-breadcrumb a")]
    desc_el = soup.select_one(".woocommerce-product-details__short-description") \
        or soup.select_one(".elementor-widget-woocommerce-product-short-description")
    cards = [c for li in soup.select("li.product.product-type-variation")
             if (c := parse_card(li, soup)) is not None]
    # A simple (non-variable) product has no variant cards: keep its own attribute table.
    simple = []
    for row in soup.select("table.woocommerce-product-attributes tr, table.shop_attributes tr"):
        simple.append([_text(row.select_one("th")), _text(row.select_one("td"))])
    page_pdfs = sorted({a["href"] for a in soup.select("a[href$='.pdf'], a[href$='.PDF']")
                        if "/wp-content/uploads/" in a["href"]})
    return {
        "url": url, "title": h1, "breadcrumb": crumbs[1:] if crumbs[:1] == ["Home"] else crumbs,
        "description": _text(desc_el), "cards": cards, "attributes": simple,
        "page_pdfs": page_pdfs,
    }


def pdf_name(url):
    return unquote(urlparse(url).path.rsplit("/", 1)[-1])


def local_names(urls):
    """{url: its file name in pdfs/}. DuraGreen's own file name - unless DuraGreen has uploaded
    different files under one name, as it often has: 68-SPEC-2.pdf is three products' sheets
    (2024/04 ACME Square, 2024/10 ACME Straight, 2024/11 the Glass Pendant), AFL78.pdf both the
    High Efficiency 78 and the ACME 78. Each of those is kept apart by its upload month
    (2024-10_68-SPEC-2.pdf). Until 22 Oct 2026 they were saved by name alone, so only the first was
    ever downloaded and every product linking another was read from that one's sheet.
    builder: build_duragreen_catalogue_db.py reads this map from the snapshot's "pdf_files"."""
    by_name = {}
    for u in sorted(urls):
        by_name.setdefault(pdf_name(u), []).append(u)
    out, used = {}, set()
    for name, group in by_name.items():
        for u in group:
            m = re.search(r"/uploads/(\d{4})/(\d{2})/", u)
            first = name if len(group) == 1 else ("%s-%s_%s" % (m.group(1), m.group(2), name) if m else name)
            local, n = first, 2
            while local in used:                  # two uploads in one month: a copy number too
                local = "%s__%d%s" % (Path(first).stem, n, Path(first).suffix)
                n += 1
            used.add(local)
            out[u] = local
    return out


def download_pdf(url, name, refresh=False):
    """One sheet, downloaded once - or, with refresh, again, to catch a sheet DuraGreen has
    re-issued under the same file name. A changed sheet's old copy is moved to SUPERSEDED_DIR,
    never overwritten: the codes already in the app were read from it."""
    dest = PDF_DIR / name
    had = dest.exists() and dest.stat().st_size > 0
    if had and not refresh:
        return dest.name, "kept"
    try:
        r = _get(url, timeout=120)
    except requests.RequestException as e:
        return dest.name, f"failed ({type(e).__name__})"
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return dest.name, f"failed ({r.status_code})"
    time.sleep(PAUSE)
    if had:
        if dest.read_bytes() == r.content:
            return dest.name, "unchanged"
        SUPERSEDED_DIR.mkdir(parents=True, exist_ok=True)
        dest.replace(SUPERSEDED_DIR / dest.name)
        dest.write_bytes(r.content)
        return dest.name, "changed"
    dest.write_bytes(r.content)
    return dest.name, "downloaded"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="fetch pages already in the snapshot again")
    ap.add_argument("--limit", type=int, default=0, help="only this many product pages (a trial)")
    ap.add_argument("--refresh-pdfs", action="store_true",
                    help="download every sheet again; a changed one's old copy goes to pdfs_superseded/<date>/")
    args = ap.parse_args()
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    snap.setdefault("products", {})
    snap.setdefault("categories", {})

    products = sitemap_urls("product-sitemap.xml")
    categories = sitemap_urls("product_cat-sitemap.xml")
    print(f"sitemap: {len(products)} product pages, {len(categories)} categories")

    for url in categories:
        if url in snap["categories"] and not args.refresh:
            continue
        snap["categories"][url] = category_members(url)
        print(f"  category {url.rstrip('/').rsplit('/', 1)[-1]}: {len(snap['categories'][url])} products")
    SNAPSHOT.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")

    todo = [u for u in products if args.refresh or u not in snap["products"]]
    if args.limit:
        todo = todo[:args.limit]
    failed = []
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def make_page():
            return browser.new_context(user_agent=USER_AGENT).new_page()

        ref = {"browser": browser, "make_page": make_page, "page": make_page()}
        for n, url in enumerate(todo, 1):
            if not _goto_patiently(ref, url):
                failed.append(url)
                continue
            _scroll_to_bottom(ref["page"])
            entry = parse_product_page(ref["page"].content(), url)
            entry["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            snap["products"][url] = entry
            # Saved after every page, so a crash late in a long run loses one page, not all.
            SNAPSHOT.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"[{n}/{len(todo)}] {entry['title'] or url}: {len(entry['cards'])} variants")
            time.sleep(PAUSE)
        browser.close()

    # Every specification sheet any page offers, downloaded once.
    urls = set()
    for entry in snap["products"].values():
        for c in entry["cards"]:
            for d in c["downloads"]:
                if d["url"].lower().endswith(".pdf"):
                    urls.add(d["url"])
        urls.update(entry.get("page_pdfs") or [])
    names = local_names(urls)
    snap["pdf_files"] = names
    SNAPSHOT.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    tally, changed = {}, []
    for u in sorted(urls):
        name, status = download_pdf(u, names[u], refresh=args.refresh_pdfs)
        tally[status] = tally.get(status, 0) + 1
        if status == "changed":
            changed.append(name)
    for name in changed:
        print("  CHANGED SHEET", name, "(old copy in", SUPERSEDED_DIR.relative_to(_REPO_ROOT), ")")
    snap["meta"] = {"crawled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "product_pages": len(snap["products"]), "categories": len(snap["categories"]),
                    "failed": failed, "pdfs": tally}
    SNAPSHOT.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nproduct pages in snapshot: {len(snap['products'])}; failed this run: {len(failed)}")
    for u in failed:
        print("  FAILED", u)
    print("PDFs:", tally)


if __name__ == "__main__":
    main()
