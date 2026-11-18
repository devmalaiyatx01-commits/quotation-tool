"""One-off import: scrapes DuraGreen's ENTIRE lighting catalog
(https://duragreenlighting.co.uk/lights/) into the specsheet library - every category
listed on the site's own navigation, not just Downlights.

Supersedes import_duragreen_downlights.py, which (bug, found 2026-08-18) scraped
"https://duragreenlighting.co.uk/lights/downlights/" - a curated "Flagship Selection"
landing page (7 product families, 52 SKUs), NOT the real, complete Downlights category
(17 product families). The site's own root nav (duragreenlighting.co.uk/lights/) links
every category tile to its real, canonical archive page at
"/product-category/{slug}/" - confirmed by reading the tile hrefs directly off that
page, not guessed - and that's what this script enumerates products from instead.

Reuses the exact same per-product scraping logic already proven against the Downlights
pilot (variant cards + their spec-table modal + their real downloadable spec-sheet PDF -
see CARD/MODAL PARSING below), verified unchanged against 3 new, previously-unscraped
product pages (acme-fixed, mood-round, evoline) before this script was written.

CATEGORIES below were enumerated directly off each category's real archive page (product
family counts confirmed by an actual fetch, not assumed) - "Power Supplies" is imported
as typology "Driver" (mirrors import_drivers.py's existing convention: a driver isn't a
luminaire, and is deliberately excluded from the general Library grid by
`GET /api/specsheets`'s own `typology != "Driver"` filter). Every category is included,
even ones that aren't traditional luminaires (Demo Kit, Lighting Control Systems) - real
catalog products the user asked to be fetched in full, not something to quietly skip.

This site occasionally serves a "Page crashed" Chromium failure under Playwright
(observed directly while investigating this script, cause unconfirmed - not every
request, no discernible pattern) - every navigation here is retried with a fresh page
(the crashed Page object itself becomes unusable) up to RETRIES times before that one
category/product is logged as failed and skipped, rather than taking the whole run down.

Same one-time local prerequisites as import_duragreen_downlights.py (not a real app
dependency):

    pip install playwright beautifulsoup4
    playwright install chromium

Commits after every product PAGE (not once at the very end) - a long, ~100-product-page
run over a site that occasionally crashes Chromium mid-navigation must never let one
late failure silently discard everything already scraped; found the hard way on this
script's own first run, where a Windows-console Unicode print crash on product #2 of
Spotlights lost all 17 already-scraped Downlights families because the only commit()
was after the whole run.
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
import sys
import re
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import pdfplumber
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.database import SessionLocal, IMAGES_DIR
from app import models
from app.main import _dedup_fingerprints, _unique_upload_dest
from app.typology_classifier import classify_typology
from app.environment_classifier import classify_environment

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MAKE = "Duragreen"
RETRIES = 3

# (category slug, display name, typology override or None to auto-classify)
CATEGORIES = [
    ("ceiling-recessed-downlights", "Downlights", None),
    ("ceiling-recessed-spotlights", "Spotlights", None),
    ("ceiling-surface", "Ceiling Surface", None),
    ("suspension", "Suspension", None),
    ("wall-lights", "Wall Lights", None),
    ("step-lights", "Step Lights", None),
    ("track-lights", "Track Lights", None),
    ("floor-lamps", "Floor Lamps", None),
    ("table-lamps", "Table Lamps", None),
    ("led-strip", "LED Strip", None),
    ("outdoor", "Outdoor", None),
    ("power-supplies", "Power Supplies", "Driver"),
    ("lighting-control-systems", "Lighting Control Systems", None),
    ("shelf-joinery-lights", "Shelf & Joinery Lights", None),
    ("demo-kit", "Demo Kit", None),
]

MODAL_LABEL_TO_CANONICAL = {
    "IP RATING": "ip_rating",
    "OPTICS": "beam_angle",
    "UGR": "ugr",
    "CCT": "cct",
    "CONTROL": "control",
    "INPUT": "input_voltage",
    # Power Supplies / drivers use their own different label set for the same
    # underlying concepts (confirmed against real scraped driver rows, e.g.
    # "DuraGreen CC ON-OFF 10W": Control Type/IP Zone/Maximum Power/Input Voltage)
    "IP ZONE": "ip_rating",
    "CONTROL TYPE": "control",
    "MAXIMUM POWER": "wattage",
    "INPUT VOLTAGE": "input_voltage",
}


def _unique_image_dest(name: str) -> Path:
    dest = IMAGES_DIR / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 2
    while (candidate := IMAGES_DIR / f"{stem}__{i}{suffix}").exists():
        i += 1
    return candidate


def _split_list(value, seps=("/", ",")):
    if not value:
        return []
    for sep in seps:
        if sep in value:
            return [p.strip() for p in value.split(sep) if p.strip()]
    return [value.strip()]


def extract_pdf_output_lines(pdf_path):
    """Returns the full 'DC <V>V <I>mA <W>W[*N]' driver-output strings found on a
    downloaded spec-sheet PDF's page 1, in order, deduped. Used to cross-check (and,
    where they disagree, correct) wattage against the site's own modal table - see
    backfill_duragreen_wattage_from_pdf.py's module docstring for why this exists:
    DuraGreen's website modal and its own downloadable PDF don't always agree, and the
    PDF is what "Original PDF" actually shows the user, so that's what needs to win."""
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    lines = re.findall(r"DC\s*\d+(?:\.\d+)?V\s*\d+(?:\.\d+)?mA\s*\d+(?:\.\d+)?W(?:\*\d+)?", text)
    seen = []
    for line in lines:
        normalized = " ".join(line.split())
        if normalized not in seen:
            seen.append(normalized)
    return seen


def cross_check_wattage_against_pdf(card, pdf_path):
    """Mutates card["fields"]/card["options"]/card["params"]/card["raw_text"] in place
    if the PDF's own output lines disagree with what the modal-scrape already put
    there. Skips silently (leaves the modal's data alone) if the PDF uses a "W*N"
    multiplier notation (e.g. "6W*2") - not safe to resolve with a simple regex, and
    printed by the caller as a row needing manual review rather than guessed at."""
    pdf_lines = extract_pdf_output_lines(pdf_path)
    if not pdf_lines or any("*" in line for line in pdf_lines):
        return "ambiguous" if pdf_lines and any("*" in line for line in pdf_lines) else None

    pdf_watts = []
    for line in pdf_lines:
        m = re.search(r"(\d+(?:\.\d+)?W)$", line)
        if m and m.group(1) not in pdf_watts:
            pdf_watts.append(m.group(1))

    stored_watts = (card["options"] or {}).get("wattage") or []
    if sorted(pdf_watts) == sorted(stored_watts):
        return None

    before = dict(card["fields"]), dict(card["options"])
    card["fields"]["wattage"] = pdf_watts[0] if pdf_watts else card["fields"].get("wattage")
    card["options"]["wattage"] = pdf_watts
    card["params"].setdefault("DURAGREEN SPEC", {})["Output*"] = " ".join(pdf_lines)
    card["raw_text"] = re.sub(r"Output\*?:\s*[^;]*", f"Output*: {' '.join(pdf_lines)}", card["raw_text"] or "")
    return (before, (dict(card["fields"]), dict(card["options"])))


def _goto_with_retry(page_ref, url, wait_until="networkidle", timeout=30000):
    """Returns True/False rather than the Page itself - a crash invalidates the Page
    object, so the caller always keeps using the same `page_ref[0]` it was given, and
    this function replaces its contents (browser/context stay put, only the Page is
    recreated) when a retry is needed."""
    browser, make_page = page_ref["browser"], page_ref["make_page"]
    for attempt in range(1, RETRIES + 1):
        try:
            page_ref["page"].goto(url, timeout=timeout, wait_until=wait_until)
            return True
        except Exception as e:
            print(f"    (attempt {attempt}/{RETRIES} failed for {url}: {e})")
            try:
                page_ref["page"].close()
            except Exception:
                pass
            page_ref["page"] = make_page()
            time.sleep(1.5)
    return False


# ---------------- per-product-page parsing (unchanged from the Downlights pilot) ----------------

def _parse_variant_card(li, soup):
    title_el = li.select_one("h2.woocommerce-loop-product__title")
    if not title_el:
        return None
    product_name = title_el.get_text(strip=True)

    tagline_el = li.select_one(".variation_title_tagline")
    tagline = tagline_el.get_text(strip=True) if tagline_el else ""
    wattage_options = re.findall(r"\d+(?:\.\d+)?W", tagline, re.IGNORECASE)
    cri_m = re.search(r"CRI\s*(\d+)", tagline, re.IGNORECASE)

    cart_btn = li.select_one("a.add_to_cart_button[data-product_id]")
    variation_id = cart_btn["data-product_id"] if cart_btn else None

    finish_options = []
    for swatch in li.select(".vci-nav-image[data-tooltip]"):
        label = (swatch.get("data-tooltip") or "").strip()
        if label:
            finish_options.append(label)

    img_el = li.select_one(".vci-image img")
    image_url = img_el["src"] if img_el and img_el.get("src") else None

    modal_params = {}
    spec_pdf_url = None
    if variation_id:
        modal = soup.select_one(f"#oz-variation-modal-{variation_id}")
        if modal:
            for row in modal.select("table tr"):
                cells = row.select("td")
                if len(cells) == 2:
                    key = cells[0].get_text(strip=True).rstrip(":").strip()
                    value = cells[1].get_text(strip=True)
                    if key and value:
                        modal_params[key] = value
            downloads = modal.select_one(".oz-download-section-wrap")
            if downloads:
                for a in downloads.select("a[href]"):
                    href = a.get("href", "")
                    if href.lower().endswith(".pdf") and "specification" in a.get_text(strip=True).lower():
                        spec_pdf_url = href
                        break

    fields = {}
    for label, value in modal_params.items():
        canon = MODAL_LABEL_TO_CANONICAL.get(label.upper())
        if canon and canon not in fields:
            fields[canon] = value
    if wattage_options:
        fields["wattage"] = wattage_options[0]
    if cri_m:
        fields["cri"] = cri_m.group(1)
    elif "CRI/RF/RG/R9" in modal_params:
        m = re.search(r"\d+", modal_params["CRI/RF/RG/R9"])
        if m:
            fields["cri"] = m.group(0)
    accessories_raw = next(
        (v for k, v in modal_params.items() if k.strip().upper() in ("OPTIONSL ACCESSORIES", "OPTIONAL ACCESSORIES")),
        None,
    )
    if accessories_raw:
        fields["accessories"] = accessories_raw
    if finish_options:
        fields["finish"] = finish_options[0]

    options = {}
    if wattage_options:
        options["wattage"] = wattage_options
    if "cct" in fields:
        options["cct"] = _split_list(fields["cct"])
    if "beam_angle" in fields:
        options["beam_angle"] = _split_list(fields["beam_angle"])
    if "control" in fields:
        options["control"] = [p.strip() for p in fields["control"].split(",") if p.strip()]
    if finish_options:
        options["finish"] = finish_options

    size_m = re.match(r"^(\d+)\s*(.*)$", product_name)
    family = f"{size_m.group(1)}mm" if size_m else ""
    variant = size_m.group(2).strip() if size_m else product_name

    raw_lines = [f"Variant: {product_name}"]
    if tagline:
        raw_lines.append(tagline)
    raw_lines.extend(f"{k}: {v}" for k, v in modal_params.items())

    return {
        "product_name": product_name,
        "family": family,
        "variant": variant,
        "fields": fields,
        "options": options,
        "params": {"DURAGREEN SPEC": modal_params},
        "raw_text": "; ".join(raw_lines),
        "image_url": image_url,
        "spec_pdf_url": spec_pdf_url,
    }


def _scroll_to_bottom(page):
    """Some of this site's per-variant modal content (including the download-section
    PDF links) is injected by a lazy-load/observer that only fires once a card
    actually scrolls into view - `networkidle` alone doesn't trigger it, confirmed by
    re-fetching a product page whose HTML (post-networkidle, no scroll) was missing a
    "Specification sheet" link the live page genuinely has. Scrolling the full page in
    steps (not straight to the bottom in one jump, which can skip triggering some
    observers) before extracting content fixes this."""
    prev_height = 0
    for _ in range(20):
        height = page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        page.evaluate(f"window.scrollTo(0, {prev_height})")
        page.wait_for_timeout(200)
        prev_height = height
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(500)
    page.evaluate("window.scrollTo(0, 0)")


def scrape_product_page(page_ref, url):
    if not _goto_with_retry(page_ref, url):
        return None, []
    _scroll_to_bottom(page_ref["page"])
    soup = BeautifulSoup(page_ref["page"].content(), "html.parser")
    h1 = soup.select_one("h1")
    series = h1.get_text(strip=True) if h1 else ""
    cards = [c for li in soup.select("li.product.product-type-variation")
             if (c := _parse_variant_card(li, soup)) is not None]
    return series, cards


def main():
    db = SessionLocal()
    fingerprint_index = {}
    for s in db.query(models.Specsheet).all():
        for fp in _dedup_fingerprints(s.category, s.product_name, s.series, s.family, s.variant, s.make, s.raw_text, s.fields, s.params, s.options):
            fingerprint_index.setdefault(fp, s)

    total_created = 0
    total_skipped = 0
    failed_products = []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        def make_page():
            return browser.new_context(user_agent=USER_AGENT).new_page()

        page_ref = {"browser": browser, "make_page": make_page, "page": make_page()}

        for cat_slug, cat_display, typology_override in CATEGORIES:
            time.sleep(1)
            cat_url = f"https://duragreenlighting.co.uk/product-category/{cat_slug}/"
            print(f"\n=== {cat_display} ({cat_url}) ===")
            if not _goto_with_retry(page_ref, cat_url, wait_until="domcontentloaded"):
                print(f"  FAILED to load category page after {RETRIES} attempts - skipping category")
                failed_products.append(cat_url)
                continue
            page_ref["page"].wait_for_timeout(2000)
            cat_soup = BeautifulSoup(page_ref["page"].content(), "html.parser")
            product_urls = sorted({
                a["href"].split("?")[0] for a in cat_soup.select("a[href*='/product/']")
            })
            print(f"  {len(product_urls)} product page(s)")

            for page_url in product_urls:
                time.sleep(1)
                series, cards = scrape_product_page(page_ref, page_url)
                if series is None:
                    print(f"  FAILED: {page_url}")
                    failed_products.append(page_url)
                    continue
                category = f"{cat_display} - {series}" if series else cat_display
                print(f"  {page_url}\n    {series}: {len(cards)} variant(s)")

                for card in cards:
                    # classified from this variant's OWN name first, falling back to the
                    # broader category/series text only when the name itself has no
                    # typology keyword - DuraGreen files some genuinely-different fixture
                    # types under a family name that doesn't match what they actually are
                    # (e.g. series "FLOW DOWNLIGHTS" containing a member literally named
                    # "30 Spots Linear" - classifying off the family/category name alone
                    # said "Downlight" for what is, by its own name, a spotlight; found
                    # 2026-08-18 from a real bad Match-tool suggestion).
                    typology = typology_override or classify_typology(card["product_name"]) or classify_typology(category)
                    fps = _dedup_fingerprints(
                        category, card["product_name"], series, card["family"], card["variant"],
                        MAKE, card["raw_text"], card["fields"], card["params"], card["options"],
                    )
                    existing = next((fingerprint_index[fp] for fp in fps if fp in fingerprint_index), None)
                    if existing is not None:
                        total_skipped += 1
                        print(f"      skip (duplicate of #{existing.id} {existing.product_name!r}): {card['product_name']}")
                        continue

                    image_path = ""
                    if card["image_url"]:
                        try:
                            resp = page_ref["page"].context.request.get(card["image_url"])
                            if resp.ok:
                                ext = Path(card["image_url"].split("?")[0]).suffix or ".png"
                                safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"duragreen_{card['product_name']}") + ext
                                dest = _unique_image_dest(safe_name)
                                dest.write_bytes(resp.body())
                                image_path = str(dest)
                        except Exception as e:
                            print(f"      (image download failed: {e})")

                    source_pdf_path = ""
                    if card["spec_pdf_url"]:
                        try:
                            resp = page_ref["page"].context.request.get(card["spec_pdf_url"])
                            if resp.ok:
                                dest = _unique_upload_dest(Path(card["spec_pdf_url"].split("?")[0]).name)
                                dest.write_bytes(resp.body())
                                source_pdf_path = str(dest)
                                try:
                                    result = cross_check_wattage_against_pdf(card, source_pdf_path)
                                    if result == "ambiguous":
                                        print(f"      (wattage: PDF uses a 'W*N' notation - needs manual review, left as scraped: {source_pdf_path})")
                                    elif result:
                                        before, after = result
                                        print(f"      (wattage corrected from PDF: {before[1].get('wattage')} -> {after[1].get('wattage')})")
                                except Exception as e:
                                    print(f"      (wattage cross-check against PDF failed: {e})")
                            else:
                                # a dead link ON DURAGREEN'S OWN SITE, not a scraper bug - their
                                # modal linked it, but the file itself 404s right now. Logged
                                # (not silently swallowed) so this is distinguishable from a real
                                # scraping failure if someone re-checks this output later.
                                print(f"      (spec PDF link is {resp.status} on DuraGreen's own site: {card['spec_pdf_url']})")
                        except Exception as e:
                            print(f"      (spec PDF download failed: {e})")

                    diagrams = [{"path": image_path, "width": 0, "height": 0, "label": "Product Image"}] if image_path else []

                    sheet = models.Specsheet(
                        filename=Path(page_url.rstrip("/")).name + ".html",
                        # always 1, not a running index across this page's variant cards - unlike
                        # InHouse's literal multi-page catalog PDFs, every DuraGreen variant gets
                        # its OWN separate, standalone PDF (source_pdf_path), so page_number=idx
                        # was a real bug: it told the Details view "this product is on page 12 of
                        # a multi-page catalog" for a genuinely 2-page standalone document, and
                        # built a "#page=12" PDF-viewer link that page count doesn't even reach.
                        page_number=1,
                        category=category,
                        typology=typology,
                        environment=classify_environment(category),
                        product_name=card["product_name"],
                        series=series,
                        family=card["family"],
                        variant=card["variant"],
                        make=MAKE,
                        source_pdf_path=source_pdf_path,
                        image_path=image_path,
                        diagrams=diagrams,
                        raw_text=card["raw_text"],
                        params=card["params"],
                        fields=card["fields"],
                        options=card["options"],
                    )
                    db.add(sheet)
                    db.flush()
                    for fp in fps:
                        fingerprint_index.setdefault(fp, sheet)
                    total_created += 1
                    label = sheet.typology or "Unclassified"
                    print(f"      + [{label}] {sheet.product_name!r}")

                db.commit()  # per product page, not just once at the end - see module docstring

        browser.close()

    print(f"\nDone. Created {total_created} specsheet(s), skipped {total_skipped} duplicate(s).")
    if failed_products:
        print(f"\n{len(failed_products)} page(s) failed after {RETRIES} retries each - re-run this script to retry them (safe, dedup skips everything already imported):")
        for u in failed_products:
            print(f"  {u}")


if __name__ == "__main__":
    main()
