"""Parses a raw uploaded pricing file (SampleInputs/Pricing/*.xlsx or *.pdf - the studio's own
real price sheets) into candidate PriceEntry rows (see app/models.py) - always unmapped
(specsheet_id=None) and always status="open": this module's only job is getting a real
price sheet's numbers in front of a human to map/confirm in the List Pricing tab, never
to guess which specsheet a row belongs to (that's the same "never auto-match money"
discipline the EMBER/NYX backfills already established, just moved from a one-off script
into a real upload path).

Two real header shapes cover most of the price sheets actually in use (confirmed by
direct inspection, not assumed) - see detect_format(). Anything that doesn't match either
falls back to a much cruder "grab a description and a trailing number" extraction -
correctness of the SPLIT isn't the point for a row nobody's confirmed yet; getting the
data in front of a human is.
"""
import re

import openpyxl


def _norm(h) -> str:
    """Collapses whitespace/newlines and lowercases a header cell, so 'List Price\\nStatic',
    'List price', 'List prices\\nStatic' etc. all normalize to a comparable form."""
    return re.sub(r"\s+", " ", str(h or "")).strip().lower()


def _find_col(headers: list[str], *prefixes) -> int | None:
    for i, h in enumerate(headers):
        for p in prefixes:
            if h.startswith(p):
                return i
    return None


def _cell_text(row, idx) -> str:
    if idx is None or idx >= len(row) or row[idx] is None:
        return ""
    return str(row[idx]).strip()


_PRICE_RE = re.compile(r"[\d][\d,]*\.?\d*")


def _parse_price(value) -> float | None:
    """A price cell is sometimes a real number, sometimes text like "Please refer driver
    sheet" (no number at all - a real gap in that source, not a parsing failure) or has a
    stray currency symbol/comma. Returns None for anything without a real number - that
    row is simply skipped, never guessed at."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _PRICE_RE.search(str(value).replace(",", ""))
    return float(m.group(0)) if m else None


def detect_format(header_row: list) -> str | None:
    """'type_a' (Series/Brand/Description/Wattage/.../List Price - the Downlights/Drivers/
    Linear Profiles/Magnetic Track shape), 'type_b' (Sl No./Image/Description/Wattage/.../
    List Price - the Exterior Lighting shape), or None (doesn't match either - use the
    fallback ingestion instead)."""
    normed = [_norm(h) for h in header_row]
    if any(h.startswith("sl no") for h in normed):
        return "type_b"
    if any(h.startswith("series") or h.startswith("seriers") for h in normed):
        return "type_a"
    return None


def _find_header_row(ws, max_scan=5):
    """The header isn't always row 1 - Magnetic Track 48V.xlsx has a merged title row
    first. Scans the first few rows for one detect_format() recognizes."""
    for row in ws.iter_rows(min_row=1, max_row=max_scan, values_only=True):
        fmt = detect_format(list(row))
        if fmt:
            return list(row), fmt
    return None, None


def _is_multi_value(text: str) -> bool:
    """A price sheet's CCT/beam-angle cell is often the WHOLE real option list for one
    product (e.g. "2700K, 3000K, 4000K, 6000K", or a beam angle list like "10°/15°/25°/
    30°/50°/60°/80°" - the same multi-option shape a real specsheet's own fields.cct/
    fields.beam_angle already use), not a single chosen value. priceEntryMatches() (see
    static/app.js) does exact string equality against whatever ONE value a user actually
    picks - storing the whole joined list there would make the entry permanently
    unmatchable regardless of configuration (verified live: caught this on 3 real
    Spotlight rows, then found 70 more already-parsed entries with the same shape). Every
    real price sheet inspected this session only ever varies price by wattage (sometimes
    control) - never by CCT or beam angle alone - so dropping an ambiguous multi-value
    field from config is safe, not a loss of real pricing information."""
    return "," in text or "/" in text


def _config_from(cct, beam, control, ip_rating) -> dict:
    config = {}
    if cct and not _is_multi_value(cct):
        config["cct"] = cct
    if beam and not _is_multi_value(beam):
        config["beam_angle"] = beam
    if control:
        config["control"] = control
    if ip_rating:
        config["ip_rating"] = ip_rating
    return config



def _source_specs(wattage="", cct="", beam="", control="", ip_rating="", dimensions=""):
    """Every spec the source row stated, verbatim - multi-value cells included, unlike
    _config_from which deliberately drops them. Feeds app/price_matcher.py's range/set
    reading; see PriceEntry.source_specs in app/models.py for why the two differ."""
    raw = {"wattage": wattage, "cct": cct, "beam_angle": beam,
           "control": control, "ip_rating": ip_rating, "dimensions": dimensions}
    return {k: v for k, v in raw.items() if v}


def _parse_type_a_sheet(ws, source_file, source_sheet) -> list[dict]:
    header, _ = _find_header_row(ws)
    normed = [_norm(h) for h in header]
    col = {
        "series": _find_col(normed, "series", "seriers"),
        "brand": _find_col(normed, "brand"),
        "description": _find_col(normed, "description"),
        "wattage": _find_col(normed, "wattage"),
        "cct": _find_col(normed, "color temperature"),
        "beam": _find_col(normed, "beam angle"),
        "control": _find_col(normed, "control"),
        "ip": _find_col(normed, "ip rating"),
        "price": _find_col(normed, "list price", "list prices"),
    }
    if col["price"] is None or col["description"] is None:
        return []

    entries = []
    # Series/Brand are only stated on a base product's own row - variant/driver sub-rows
    # below it (e.g. "Power supply" rows) leave them blank, inheriting the row above -
    # forward-filled here purely so source_label stays readable, not a correctness change.
    last_series, last_brand = "", ""
    last_base_label = ""
    header_seen = False
    for row in ws.iter_rows(values_only=True):
        row = list(row)
        if not header_seen:
            if detect_format(row) == "type_a":
                header_seen = True
            continue
        description = _cell_text(row, col["description"])
        if not description:
            continue
        series = _cell_text(row, col["series"]) or last_series
        brand = _cell_text(row, col["brand"]) or last_brand
        last_series, last_brand = series or last_series, brand or last_brand

        price = _parse_price(row[col["price"]] if col["price"] < len(row) else None)
        if price is None:
            continue

        wattage = _cell_text(row, col["wattage"])
        cct = _cell_text(row, col["cct"])
        beam = _cell_text(row, col["beam"])
        control = _cell_text(row, col["control"])
        ip_rating = _cell_text(row, col["ip"])

        # No wattage of its own (and a base row already exists to attach to) = an
        # accessory/component priced per parent product (a "Spike"/"Snoot" mount, a track
        # connector, a driver), not a standalone item - real data confirms the same
        # accessory costs a different amount under different parent fixtures (Exterior
        # Lighting.xlsx's "Spike" is Rs N under one Spot Light, Rs N under another),
        # so config keys on the accessory NAME, scoped to whichever specsheet a human maps
        # this row to - never the inherited wattage/ip, which describe the PARENT row's
        # context, not a real property of the accessory itself. `control` is kept, though,
        # when the sub-row states one - a real, distinguishing case, not noise: this exact
        # file's own "Power supply" sub-rows repeat under one downlight at 4 different
        # prices (one per control type), and dropping control here would
        # collapse those 4 real prices into indistinguishable same-config duplicates.
        if not wattage and last_base_label:
            acc_config = {"accessory": description}
            if control:
                acc_config["control"] = control
            label = f"{description} (accessory for: {last_base_label})"
            if control:
                label = f"{description} [{control}] (accessory for: {last_base_label})"
            entries.append({
                "source_file": source_file, "source_sheet": source_sheet,
                "source_label": label, "config": acc_config, "price": price,
                "source_specs": _source_specs(control=control),
            })
            continue

        config = _config_from(cct, beam, control, ip_rating)
        if wattage:
            config["wattage"] = wattage

        label_parts = [p for p in [series, description] if p]
        detail_parts = [p for p in [wattage, control, ip_rating] if p]
        label = " - ".join(label_parts)
        if detail_parts:
            label += f" ({', '.join(detail_parts)})"
        label = label.strip()
        last_base_label = label

        entries.append({
            "source_file": source_file, "source_sheet": source_sheet,
            "source_label": label, "config": config, "price": price,
            "source_specs": _source_specs(wattage, cct, beam, control, ip_rating),
        })
    return entries


def _parse_type_b_sheet(ws, source_file, source_sheet) -> list[dict]:
    header, _ = _find_header_row(ws)
    normed = [_norm(h) for h in header]
    col = {
        "description": _find_col(normed, "description"),
        "dimensions": _find_col(normed, "dimensions", "diamensions"),
        "wattage": _find_col(normed, "wattage"),
        "cct": _find_col(normed, "color temperature"),
        "optics": _find_col(normed, "optics"),
        "ip": _find_col(normed, "ip rating"),
        "price": _find_col(normed, "list price", "list prices"),
    }
    if col["price"] is None or col["description"] is None:
        return []

    entries = []
    header_seen = False
    last_description = ""
    last_base_label = ""
    for row in ws.iter_rows(values_only=True):
        row = list(row)
        if not header_seen:
            if detect_format(row) == "type_b":
                header_seen = True
            continue
        description = _cell_text(row, col["description"]) or last_description
        last_description = description
        if not description:
            continue

        price = _parse_price(row[col["price"]] if col["price"] < len(row) else None)
        if price is None:
            continue

        wattage = _cell_text(row, col["wattage"])
        dimensions = _cell_text(row, col["dimensions"])
        cct = _cell_text(row, col["cct"])
        optics = _cell_text(row, col["optics"])  # this sheet's beam-angle column is labeled "Optics"
        ip_rating = _cell_text(row, col["ip"])

        # Same accessory-detection rule as _parse_type_a_sheet - no wattage of its own,
        # with a base row already established to attach to (real example: Exterior
        # Lighting.xlsx's "Spike"/"snoot"/"Base" rows sit directly under a "Surface mounted
        # Spot Light" row and share its price sheet's per-parent pricing).
        if not wattage and last_base_label:
            entries.append({
                "source_file": source_file, "source_sheet": source_sheet,
                "source_label": f"{description} (accessory for: {last_base_label})",
                "config": {"accessory": description}, "price": price, "source_specs": {},
            })
            continue

        config = _config_from(cct, optics, "", ip_rating)
        if wattage:
            config["wattage"] = wattage
        if dimensions:
            config["dimensions"] = dimensions

        detail_parts = [p for p in [wattage, dimensions, ip_rating] if p]
        label = description
        if detail_parts:
            label += f" ({', '.join(detail_parts)})"
        label = label.strip()
        last_base_label = label

        entries.append({
            "source_file": source_file, "source_sheet": source_sheet,
            "source_label": label, "config": config, "price": price,
            "source_specs": _source_specs(wattage, cct, optics, "", ip_rating, dimensions),
        })
    return entries


def _parse_fallback_sheet(ws, source_file, source_sheet) -> list[dict]:
    """Anything that doesn't match either known header shape (e.g. INGROUNDS.xlsx's
    merged-cell layout) - grabs whatever text is on a row plus the last numeric-looking
    cell as its price. Crude, but a human maps/confirms every row before it affects
    anything, so getting the raw data captured matters more than a clean split here."""
    entries = []
    for row in ws.iter_rows(values_only=True):
        cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
        # Needs a label AND a price, not just one bare cell - a merged-cell layout's
        # continuation rows often carry nothing but a lone row-index number (e.g. this
        # sheet's own row "[3]", "[4]" - just the "No" column repeating on an otherwise-
        # blank row), and treating that number as a price would fabricate a nonsense
        # "row 3, price ₹N" entry. Real single-value price rows do exist too (a plain
        # accessory line with no separate label cell), but those are indistinguishable
        # from a bare index number here - erring toward dropping rather than fabricating.
        if len(cells) < 2:
            continue
        price = _parse_price(cells[-1])
        # A floor, not an arbitrary guess - the cheapest genuine price seen across every
        # real price sheet this session (a 5W On/Off power supply adder) is Rs N; small
        # bare integers (a row-index number, a bare wattage figure sitting alone in a
        # merged-cell continuation row) are the actual garbage this is filtering, and none
        # of those are anywhere near this range in the files actually seen so far.
        if price is None or price < 50:
            continue
        label = " ".join(cells[:-1])
        entries.append({
            "source_file": source_file, "source_sheet": source_sheet,
            "source_label": label, "config": {}, "price": price, "source_specs": {},
        })
    return entries


_PDF_PRICE_LINE_RE = re.compile(r"^[₹]?\s*([\d][\d,]*\.\d{2})\s*$")


def _parse_pdf_fallback(text: str, source_file: str) -> list[dict]:
    """These aren't the plain multi-column prose the Match tool's fitz-based extraction
    was built for - both real PDF price sheets seen so far (NYX Pricing.pdf, New
    Ozone.pdf) are table layouts where fitz extracts one CELL per line, not one row per
    line (verified directly: neither file's raw text is garbled at all, just shaped
    differently than assumed at first) - a table row is really "however many cell-lines
    sit between one price-line and the next". Each row in both files ends on a line that
    is JUST a 2-decimal number (₹N in one file, a bare 1234.56 with no currency
    symbol at all in the other) - matching on that, not a currency symbol, is what
    generalizes across both. Requires exactly 2 decimal places so a bare beam-angle/
    wattage integer elsewhere in the cell sequence is never mistaken for a price."""
    entries = []
    buffer: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _PDF_PRICE_LINE_RE.match(line)
        if not m:
            buffer.append(line)
            continue
        label = " ".join(buffer).strip()
        buffer = []
        if not label:
            continue
        entries.append({
            "source_file": source_file, "source_sheet": "",
            "source_label": label, "config": {}, "price": float(m.group(1).replace(",", "")),
        })
    return entries


# (filename, sheet name) pairs that are fully, already covered by a different mechanism
# entirely - not a general "skip drivers" rule, just this one specific known duplicate.
# "Downlights and drivers for all downlights.xlsx"'s "Drivers" tab is the exact real
# source CC_DRIVER_PRICES (static/app.js) was built from - re-parsing it into PriceEntry
# rows would just recreate 32 already-covered duplicates in "Needs mapping" every time
# this file gets re-uploaded (caught live: a user found exactly this after the first
# upload). No dedup pass exists in v1 for anything else (see PriceEntry/upload docstrings)
# - this is a targeted skip for one confirmed case, not a general dedup mechanism.
_SKIP_SHEETS = {
    ("Downlights and drivers for all downlights.xlsx", "Drivers"),
}


def parse_pricing_excel(path, filename: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    entries = []
    for raw_sheet_name in wb.sheetnames:
        sheet_name = raw_sheet_name.strip()
        if (filename, sheet_name) in _SKIP_SHEETS:
            continue
        ws = wb[raw_sheet_name]
        header, fmt = _find_header_row(ws)
        if fmt == "type_a":
            entries += _parse_type_a_sheet(ws, filename, sheet_name)
        elif fmt == "type_b":
            entries += _parse_type_b_sheet(ws, filename, sheet_name)
        else:
            entries += _parse_fallback_sheet(ws, filename, sheet_name)
    return entries


def parse_pricing_pdf(path, filename: str) -> list[dict]:
    import fitz
    doc = fitz.open(path)
    text = "\n".join(page.get_text() for page in doc)
    return _parse_pdf_fallback(text, filename)


def parse_pricing_file(path, filename: str) -> list[dict]:
    if filename.lower().endswith(".pdf"):
        return parse_pricing_pdf(path, filename)
    return parse_pricing_excel(path, filename)
