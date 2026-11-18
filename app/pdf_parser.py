"""Parses InHouse-style lighting specsheet PDFs into structured data - one page,
one product. A page is laid out as a title block (category + product name), a
product photo, and a handful of key/value parameter tables (Mechanical /
Optical / Source / Power Supply / General parameters). Table boundaries as
detected by pdfplumber are not perfectly consistent between sheets (some
sheets merge several parameter groups into a single table, others split
them), so the parser walks rows in order and tracks a "current section"
header rather than trusting table boundaries directly.

Some PDFs are single-page datasheets (one product); others are multi-page
catalogs, one page per size/trim variant of a product line (e.g. "NYX
-Mini With Trim", "NYX -Mini Trimless", "NYX -Midi With Trim", ...) -
parse_specsheet_pages() handles both by parsing every page independently
and returning one result per page.
"""
import re
from pathlib import Path

import pdfplumber
import fitz  # PyMuPDF

from app.exenia_codes import compose_exenia_code

# bare-key (whitespace stripped, upper-cased) -> canonical field name
CANONICAL_FIELDS = {
    "FINISH": "finish",
    "IPRATING": "ip_rating",
    "IKRATING": "ik_rating",
    "BEAMANGLE": "beam_angle",
    "CCT": "cct",
    "ACTUALPOWER": "wattage",
    "CONTROL": "control",
    "BALLAST/CONTROLGEAR": "control_gear",
    "BALLAST/CONTROLGEAR:": "control_gear",
    "OVERALLWARRANTY": "warranty",
    "CRI": "cri",
    "ACCESSORIES": "accessories",
    "MATERIAL": "material",
    "MOUNTING": "mounting",
    "UGR": "ugr",
    "COLORCONSISTENCY": "color_consistency",
    "LUMENMAINTENANCE": "lumen_maintenance",
    "LUMENPACKAGE": "lumen_package",
    "CUTOUTDIMENSIONS": "cutout_dimensions",
    "LUMINAIRELENGTH": "luminaire_length",
    "PROFILEDIMENSION": "profile_dimension",
    "OPERATINGTEMPERATURE": "operating_temperature",
    "ADJUSTABILITY": "adjustability",
    "TRIMDETAILS": "trim_details",
    "OPTICSTYPE": "optics_type",
    "REFLECTORFINISH": "reflector_finish",
    "INPUTVOLTAGE": "input_voltage",
    "LEDTYPE": "led_type",
    "LEDNUMBER": "led_number",
}

# fields worth offering as a dropdown of options when adding to a BOQ
VARIANT_FIELDS = ["finish", "cct", "beam_angle", "wattage", "control", "ip_rating"]

SECTION_HINT = "PARAMETER"
IGNORE_HEADERS = {"PRODUCTIMAGE", "DIMENSION", "POLARANDCONEDIAGRAM"}

STRIP_KEYWORDS = ("strip", "band", "tape", "grazer")


def _norm_key(text: str) -> str:
    return "".join(text.split()).upper()


def _undouble(text: str) -> str:
    """Some catalog PDFs render certain cells' text with every character
    doubled up in place (e.g. "CUTOUT" -> "CCUUTTOOUUTT") - a text-layer
    rendering artifact on that specific cell, not real content - while
    spaces stay single ("TRIM DETAILS" -> "TTRRIIMM DDEETTAAIILLSS", not
    "TTRRIIMM  DDEETTAAIILLSS").

    Detection must be exact, not "mostly doubled": an earlier version used a
    threshold ("most adjacent pairs match"), which silently corrupted
    completely ordinary values like "2700K / 3000K / 4000K" into
    "270K / 300K / 400K" - repeated zeros in round numbers rack up enough
    coincidental adjacent-equal-character pairs to cross a fuzzy threshold on
    their own. The real artifact doubles *every* character with no
    exceptions, so after stripping spaces the string must split perfectly
    into identical adjacent pairs the whole way through - not "most of it."
    A single non-doubled pair anywhere means it's real text, full stop.

    Even an *exact* alternating-pair match isn't quite enough on its own,
    though: a short value like "55 mm" trivially satisfies it too (non-space
    "55mm" splits into "5m"/"5m" purely by coincidence - any XXYY-shaped
    string of two distinct repeated characters will), so a length floor is
    needed to rule out short numeric/unit coincidences. But a single fixed
    floor doesn't work either: short doubled *keys* are real too ("CCT" ->
    "CCCCTT" is only 6 characters, well under any floor high enough to rule
    out "55 mm"). The distinguishing signal is digits: every real
    coincidental false-positive found in practice was a number+unit value
    ("55mm", "2700K"); genuine doubled text is always a plain word/label with
    no digits in it. So digit-containing strings get a strict floor (long
    enough that all-zeros-heavy numbers can't fake it), digit-free strings
    get a much shorter one (an all-letters "accidental XXYY" is vanishingly
    rare, unlike an all-digits one)."""
    if not text or len(text) < 4:
        return text
    non_space = text.replace(" ", "")
    min_len = 16 if any(c.isdigit() for c in non_space) else 6
    if len(non_space) < min_len or len(non_space) % 2 != 0 or non_space[0::2] != non_space[1::2]:
        return text  # not uniformly doubled - real text, leave it alone even if it has some repeats

    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == " ":
            out.append(" ")
            i += 1
        elif i + 1 < n and text[i + 1] == ch:
            out.append(ch)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _clean_cell(v):
    if v is None:
        return ""
    return _undouble(" ".join(str(v).split()).strip())


def _split_options(value: str):
    if not value:
        return []
    parts = re.split(r"\s*/\s*", value) if "/" in value else re.split(r"\s*,\s*", value)
    return [p.strip() for p in parts if p.strip()]


def _split_wattage_options(value: str):
    """Wattage values look like '10W', '24W / 36W', '5 W/M', '5W/M, 10W/M' -
    a plain '/' split breaks the '/M' (per metre) suffix, so extract numeric+W tokens directly."""
    if not value:
        return []
    return re.findall(r"\d+(?:\.\d+)?\s*W(?:/M)?", value, re.IGNORECASE)


def _extract_title(tables):
    for table in tables:
        rows = []
        for row in table:
            joined = " ".join(_clean_cell(c) for c in row if _clean_cell(c))
            if joined:
                rows.append(joined)
        if rows:
            if len(rows) >= 2:
                return rows[0], rows[1]
            return "", rows[0]
    return "", ""


def _extract_params(tables):
    """Returns {section: {key: value}} walking rows across all tables in order."""
    params = {}
    section = "GENERAL PARAMETERS"
    for table in tables:
        for row in table:
            cells = [_clean_cell(c) for c in row]
            non_empty = [c for c in cells if c]
            if not non_empty:
                continue
            if len(non_empty) == 1:
                head = _norm_key(non_empty[0])
                if head in IGNORE_HEADERS:
                    continue
                if SECTION_HINT in head:
                    section = non_empty[0]
                continue
            key, value = non_empty[0], " ".join(non_empty[1:])
            head = _norm_key(key)
            if SECTION_HINT in head:
                section = key
                continue
            params.setdefault(section, {})
            params[section].setdefault(key, value)
    return params


def _flatten_fields(params):
    fields = {}
    for section, kv in params.items():
        for key, value in kv.items():
            canon = CANONICAL_FIELDS.get(_norm_key(key))
            if canon and canon not in fields:
                fields[canon] = value
    return fields


LOGO_BAND_PT = 60  # header/footer branding graphics live within this many points of the page edge - not product diagrams

# "Series" is the shared product-line name ("NYX", "OZONE", "NYX Square"); "family" is the
# size/variant within it ("Mini", "Midi", "Nano"). Recognized only from a known vocabulary of
# size words, so a genuinely standalone product name ("GLANCE 1000", "Filo S 05 - LL 106001")
# isn't incorrectly split just because it happens to contain a hyphen.
FAMILY_VOCAB = {"nano", "micro", "mini", "midi", "maxi", "small", "medium", "large",
                 "xs", "s", "m", "l", "xl", "xxl"}
# Leading whitespace before the dash is OPTIONAL - some source PDFs extract with no
# inter-word spacing at all (e.g. "FLARE-Mini", "LEDSpotlight-Adjustable" instead of
# "FLARE -Mini", "LED Spotlight -Adjustable"), and requiring a space here meant those
# never split into series/family or got a variant at all. Safe to relax: a bare hyphen
# only actually triggers a split in _split_series_family when the text after it is a
# recognized size word (FAMILY_VOCAB), so an unrelated hyphenated product code (e.g.
# "LL103002" after a dash) still falls through unsplit either way.
_SEP_RE = re.compile(r"\s*[-–]\s*")


def _split_series_family(product_name: str):
    """'NYX -Mini' -> ('NYX', 'Mini'); 'NYX Square -Mini' -> ('NYX Square', 'Mini');
    'GLANCE 1000' (no recognized size suffix) -> ('GLANCE 1000', '')."""
    if not product_name:
        return product_name or "", ""
    matches = list(_SEP_RE.finditer(product_name))
    if matches:
        last = matches[-1]
        head, tail = product_name[: last.start()].strip(), product_name[last.end():].strip()
        if tail.lower() in FAMILY_VOCAB:
            return head, tail
    return product_name.strip(), ""


def _extract_variant(category: str) -> str:
    """Trailing descriptor that distinguishes otherwise-identical catalog pages sharing
    one series+family, e.g. 'Recessed LED Downlight –Adjustable –With Trim' -> 'With Trim'."""
    if not category:
        return ""
    matches = list(_SEP_RE.finditer(category))
    if not matches:
        return ""
    return category[matches[-1].end():].strip()


def _looks_like_code_symbol(pix) -> bool:
    """Is this graphic a QR code or barcode rather than a picture of the product?

    Catalogue pages print a QR beside each product, and it is a raster image sitting in the
    body of the page exactly like a product render - so it was being picked up as one, and a
    BOQ line ended up showing a QR code where the luminaire should be. That is worse than a
    blank cell: it looks deliberate, and nobody reading sixty rows stops to wonder.

    Told apart by how often the image flips between black and white along a row. A QR alternates
    constantly; a photograph of a light fitting has large smooth areas. Measured across this
    library: QR codes 0.24-0.27, real product photos 0.007-0.16 - the highest of those being a
    louvred inground fitting whose grille is genuinely striped. The threshold sits between.

    Darkness alone does NOT work as a test and was tried first: a black downlight on a white
    background is mostly extreme pixels too, and using that flagged 208 real photographs.
    """
    w, h = pix.width, pix.height
    if not w or not h or max(w, h) / min(w, h) > 1.3 or min(w, h) < 24:
        return False
    # Always 64 samples across, whatever the source size. Striding by whole pixels instead
    # samples a small image far more finely than a large one, and the rate is then not
    # comparable between them: the same QR read at its native 162px scored 0.196 against a
    # threshold calibrated at 64 wide, and slipped through.
    N = 64
    xs = [round(i * (w - 1) / (N - 1)) for i in range(N)]
    ys = [round(j * (h - 1) / (N - 1)) for j in range(N)]
    flips = pairs = 0
    for y in ys:
        prev = None
        for x in xs:
            try:
                px = pix.pixel(x, y)
            except Exception:
                return False
            dark = (sum(px[:3]) / 3) < 128 if isinstance(px, (tuple, list)) else px < 128
            if prev is not None:
                pairs += 1
                flips += (dark != prev)
            prev = dark
    return pairs > 0 and flips / pairs > 0.20


def _extract_diagrams_for_page(fitz_page, images_dir: Path, base_name: str) -> list[dict]:
    """Extracts every distinct product diagram on ONE page (product render, dimensioned
    drawing, polar/cone diagram, etc.) - not just one. Recurring header-logo and footer-mark
    graphics are excluded by page position; raster fragments that sit close together
    (a diagram is often assembled from several overlapping images) are clustered and
    represented by their largest member so the composite isn't split into fragments.
    """
    doc = fitz_page.parent
    page_h = fitz_page.rect.height
    seen_rects = set()
    candidates = []
    for img in fitz_page.get_images(full=True):
        xref = img[0]
        rects = fitz_page.get_image_rects(xref)
        if not rects:
            continue
        r = rects[0]
        key = (round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1))
        if key in seen_rects:
            continue
        seen_rects.add(key)
        if r.y0 < LOGO_BAND_PT or r.y0 > page_h - LOGO_BAND_PT:
            continue  # header logo / footer corner mark, not a product diagram
        try:
            pix = fitz.Pixmap(doc, xref)
        except Exception:
            continue
        if pix.n - pix.alpha >= 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        if _looks_like_code_symbol(pix):
            continue  # the catalogue's QR for this product, not a picture of it
        candidates.append({"y0": r.y0, "area": pix.width * pix.height, "pix": pix})

    candidates.sort(key=lambda c: c["y0"])
    clusters = []
    for c in candidates:
        if clusters and (c["y0"] - clusters[-1][-1]["y0"]) < 80:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    results = []
    for counter, cluster in enumerate(clusters, start=1):
        best = max(cluster, key=lambda c: c["area"])
        label = "Product Image" if counter == 1 else f"Additional Diagram {counter}"
        out_path = images_dir / f"{base_name}_{counter}.png"
        best["pix"].save(str(out_path))
        results.append({"path": str(out_path), "width": best["pix"].width, "height": best["pix"].height, "label": label})
    return results


def parse_specsheet_pages(pdf_path: Path, images_dir: Path) -> list[dict]:
    """Parses EVERY page of a specsheet PDF as its own potential product, not just the
    first. Most datasheets are one page = one product, in which case this simply returns
    a one-element list - but manufacturer catalogs often pack many size/trim variants of
    one series into a single PDF (one page each, e.g. "NYX -Mini With Trim", "NYX -Mini
    Trimless", "NYX -Midi With Trim", ...), and treating the whole file as a single
    specsheet would silently keep only the first page's product and mix every page's
    diagrams into its gallery. Returns one dict per page, in page order."""
    pdf_path = Path(pdf_path)
    base_name = re.sub(r"[^A-Za-z0-9_-]+", "_", pdf_path.stem)
    results = []

    with pdfplumber.open(pdf_path) as pdf, fitz.open(pdf_path) as fitz_doc:
        for page_idx, (pl_page, fz_page) in enumerate(zip(pdf.pages, fitz_doc), start=1):
            tables = pl_page.extract_tables() or []
            raw_text = pl_page.extract_text() or ""

            category, product_name = _extract_title(tables)
            params = _extract_params(tables)
            fields = _flatten_fields(params)
            options = {}
            for f in VARIANT_FIELDS:
                if not fields.get(f):
                    continue
                options[f] = _split_wattage_options(fields[f]) if f == "wattage" else _split_options(fields[f])

            series, family = _split_series_family(product_name)
            variant = _extract_variant(category)

            page_base_name = base_name if len(pdf.pages) == 1 else f"{base_name}_p{page_idx}"
            try:
                diagrams = _extract_diagrams_for_page(fz_page, images_dir, page_base_name)
            except Exception:
                diagrams = []

            haystack = f"{category} {product_name}".lower()
            unit = "Mtr" if any(k in haystack for k in STRIP_KEYWORDS) else "Nos."

            results.append({
                "filename": pdf_path.name,
                "page_number": page_idx,
                "category": category,
                "product_name": product_name,
                "series": series,
                "family": family,
                "variant": variant,
                "make": "InHouse",  # this parser only ever handles InHouse-format PDF tables;
                                    # other brands (e.g. DuraGreen, import_duragreen_downlights.py)
                                    # go through their own ingestion path and set `make` themselves
                "raw_text": raw_text,
                "params": params,
                "fields": fields,
                "options": options,
                "diagrams": diagrams,
                "image_path": diagrams[0]["path"] if diagrams else "",
                "default_unit": unit,
            })
    return results


def build_description(specsheet, chosen: dict, code: str | None = None) -> str:
    """Composes a starting-point BOQ description from a specsheet + chosen variant values.

    Mirrors the style used in the sample BOQ (category line, then Color Temp / IP /
    SDCM / CRI / UGR / lumen maintenance / dimension lines) - meant to be edited
    by the user before the item is saved, not used verbatim.
    """
    fields = specsheet.fields or {}
    lines = []
    name = specsheet.product_name or specsheet.category or ""
    cat = specsheet.category or ""
    lines.append(f"{name}_{cat}".strip("_") if name and cat else (cat or name))

    # The manufacturer's own ordering code, on its own line directly under the product name.
    # This is what gets typed onto a purchase order, so it goes where a reader looks first -
    # and only when the manufacturer publishes one. It is never derived or guessed: a
    # plausible-looking wrong code orders the wrong fixture.
    #
    # Exenia is the case that needs work: they publish no code per product, only a SKU per
    # configurator option, and the real code is those concatenated in order. One page can
    # also order several products - a luminaire, its decorative ring, its adapter - which
    # Exenia writes as separate codes joined by "+", and so does this.
    #
    # A PARTIAL code never reaches this line. `product_code` on an Exenia row holds only the
    # stem (body + version), and printing that as "Product Code: 38580" when the real one is
    # 38580RW23SN is the worst failure available here: it is a real-looking part number, it
    # is short, and nothing on the line says so. Someone orders from it. Where the choices do
    # not determine a code exactly, this line is simply absent, and the Configurator says why.
    # For every other make `product_code` IS the published code, so it stands as it is.
    #
    # The line can be turned off. A code is only as good as what the manufacturer published,
    # and a specifier who knows a particular one is wrong needs a way to keep it off the
    # document rather than editing it out of every line by hand. `include_product_code` in
    # `chosen` carries that choice; absent, the code is included, because a right code is
    # worth having and this is the common case.
    #
    # `code`, when given, is the studio's own code from app/product_codes.py and is final: a
    # brand coded in NPD is described by that code or by none at all. "" means its code is
    # not complete yet, and falling back to the manufacturer's model number there would put
    # an address from a different system on the line with nothing to say so.
    if code is None:
        code = (chosen.get("product_code") or "").strip()
        if (specsheet.make or "") == "Exenia":
            code = code or compose_exenia_code(specsheet, chosen)
        else:
            code = code or (fields.get("product_code") or "").strip()
    if code and chosen.get("include_product_code", True) is not False:
        lines.append(f"Product Code: {code}")

    cct = chosen.get("cct") or fields.get("cct")
    if cct:
        lines.append(f"Color Temp:{cct}")

    # The prefix is added only when it is missing. Stripping every "IP" and re-adding one -
    # which is what this did - mangles the 291 rows across all four makes whose rating is
    # compound: "IP20 internal compartment IP54 optical housing" came out as "IP20 internal
    # compartment 54 optical housing", and "IP20 / IP66" as "IP20 / 66", on the BOQ line and
    # in the exported quotation.
    ip = (chosen.get("ip_rating") or fields.get("ip_rating") or "").strip()
    if ip:
        lines.append(ip if re.match(r"^\s*(?:IP|≤|<|>)", ip, re.IGNORECASE) else f"IP{ip}")

    if fields.get("color_consistency"):
        lines.append(f"SDCM {fields['color_consistency'].replace('COLOR CONSISTENCY', '').strip()}")

    # A chosen value wins, exactly as for cct/ip_rating above: on makes that bin CRI as a
    # real orderable option (Exenia sells the same fixture as CRI92 or CRI98 - different LED
    # dies, different colour temperatures available) the field holds the whole list, and
    # quoting "CRI92 / CRI98" on a BOQ line describes no single product.
    cri = chosen.get("cri") or fields.get("cri")
    if cri:
        m = re.search(r"R9\s*[><=]*\s*\d+", cri, re.IGNORECASE)
        # InHouse states R9 in a parenthetical - "> 90 (R9 > 90)" - which is split onto its
        # own line below. Only that parenthetical is split off: Lumenpulse's "CRI 90+ (White
        # Light Only)" qualifies which light the grade applies to and has to stay.
        cri_main = re.split(r"\((?=\s*R9)", cri, flags=re.IGNORECASE)[0].strip() if m else cri.strip()
        # InHouse states a bare "> 90", Exenia states "CRI95" - only add the prefix when the
        # value doesn't already carry it, so the line doesn't read "CRI CRI95".
        lines.append(cri_main if cri_main.upper().startswith("CRI") else f"CRI {cri_main}")
        if m:
            lines.append(m.group(0))

    if fields.get("ugr"):
        lines.append(f"UGR {fields['ugr']}")

    if fields.get("lumen_maintenance"):
        lines.append(fields["lumen_maintenance"])

    # Dimension fields (unlike cct/ip_rating above) often carry a real "/"-separated list of
    # genuinely different sizes on one specsheet (e.g. a strip light's profile comes in more
    # than one cross-section) - dumping that raw text straight into a description would leave
    # a single BOQ line quoting two different fixture sizes at once, which isn't a real,
    # orderable product. So: a `chosen` value always wins (the user picked one, via the
    # dimension picker "Add Product" shows whenever a specsheet has more than one real
    # option); otherwise the raw field is only used if it's already unambiguous (no "/").
    # An ambiguous, not-yet-chosen field is left out of the description entirely rather than
    # guessed - never two values, and never a silently wrong single one either.
    def resolved_dim(field):
        chosen_val = chosen.get(field)
        if chosen_val:
            return chosen_val
        raw_val = fields.get(field)
        if raw_val and "/" not in raw_val:
            return raw_val
        return ""

    # Labeled with whichever field actually supplied the value, using that field's own
    # real specsheet entry name - not one generic "Fixture Dimensions" label for all
    # three, which was never a real specsheet term and, worse, called a strip light's
    # luminaire length or profile size a "cutout" when it isn't one.
    DIM_LABELS = {
        "cutout_dimensions": "Cutout Dimensions",
        "luminaire_length": "Luminaire Length",
        "profile_dimension": "Profile Dimension",
    }
    for dim_field, dim_label in DIM_LABELS.items():
        dim = resolved_dim(dim_field)
        if dim:
            lines.append(f"{dim_label} - {dim}")
            break

    # Everything else that was actually chosen and has nowhere else to appear. Finish,
    # wattage, beam, control and CCT are BOQ columns of their own and are deliberately not
    # repeated here; CRI, IP and the dimensions already have their own lines above. What is
    # left is the axes a catalogue defines for itself - Lumenpulse sells the same downlight
    # in nine decorative bezel finishes and three lumen packages - and a BOQ line that omits
    # them does not identify one orderable product. Labelled in the make's own words.
    from .config_rules import field_labels_for   # local: pdf_parser is imported by it too

    # The last three are not choices at all: they are instructions about the description
    # (include the code or not, which code) and the accessories, which are rows of their own.
    # Printed as "Include Product Code: True" on every line until 2026-09-25.
    # UGR, colour consistency (SDCM) and lumen maintenance are printed above from the product
    # itself; since 21 Oct NPD can have the Configurator show them, and a chosen one must not
    # print a second time as "Ugr: <19".
    already = {"finish", "wattage", "beam_angle", "control", "cct", "cri", "ip_rating",
               *DIM_LABELS, "include_product_code", "product_code", "accessories",
               "ugr", "color_consistency", "lumen_maintenance"}
    labels = field_labels_for(getattr(specsheet, "make", ""))
    for key, value in (chosen or {}).items():
        if key in already or not value:
            continue
        label = labels.get(key) or key.replace("_", " ").title()
        lines.append(f"{label}: {value}")

    return "\n".join(lines)
