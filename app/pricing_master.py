"""The "Pricing Master" workbook - every InHouse product and every accessory it allows,
whether or not it has a price yet, with unpriced rows defaulted to 0 for someone to fill
in. Exported by GET /api/list-pricing/pricing-master-export, re-imported by POST
/api/list-pricing/pricing-master-import.

This is deliberately NOT the same artifact as app/master_price_export.py's "Master Price
List", and the two are kept separate rather than merged:

  - Master Price List answers "what do we currently charge?" - it contains only rows that
    already have a real price, and exists so a whole-catalogue price revision can be
    applied by editing numbers and re-importing (matched by Price Entry ID).
  - Pricing Master answers "what still needs a price?" - it contains a row for every
    product and every accessory that product's own datasheet declares, so a gap is a
    visible 0 rather than an absent row. It is the fill-in path described by the two
    supported ways to add prices: a script, or this sheet.

Merging them would break both jobs: a revision sheet padded with hundreds of 0s invites
someone to "correct" a 0 into a real price with no verification, while a gap-finding
sheet that omits unpriced rows cannot show a gap at all.

WHY "Specsheet ID" IS A COLUMN, AND WHY IT MATTERS
Every row carries the specsheet's own database id. A row that has never been priced has
no Price Entry ID yet (there is no PriceEntry to have one), so on re-import the Specsheet
ID plus "Priced Item" is what identifies where the number belongs. That is an exact
machine key, not a name guess - the distinction this project has repeatedly had to
protect (two real specsheets in this library share identical Series/Family/Variant/
Product Name and differ only in a field that is not even a column here, so re-matching on
text alone would silently attach a price to the wrong product).

Scoped to make == "InHouse" for now, per direct instruction. INHOUSE_ONLY below is the
single place that scoping lives, so widening it later is a one-line change rather than a
hunt through query filters.
"""
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from . import models
from .master_price_export import DIFFERENTIATING_FIELDS, FIELD_COLUMN_LABELS

INHOUSE_ONLY = "InHouse"

# The literal that means "this row is the product's own price, not an accessory's". Kept
# as a sentinel string in the same column as accessory names (rather than a separate
# boolean column) so a human reading the sheet sees one unambiguous "what is being priced"
# value per row - and so a blank there is an error worth reporting, not a silent default.
PRODUCT_PRICE_LABEL = "Product price"

HEADER_FILL = PatternFill("solid", fgColor="2F2A22")
HEADER_FONT = Font(color="FFFFFF", bold=True)
LOCKED_FILL = PatternFill("solid", fgColor="D7ECD9")
OPEN_FILL = PatternFill("solid", fgColor="FBE8AE")
UNPRICED_FILL = PatternFill("solid", fgColor="F5D3CD")
THIN = Side(style="thin", color="D9D3C7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEADERS = (
    ["Price Entry ID", "Specsheet ID", "Series", "Family", "Variant", "Product Name",
     "Typology", "Priced Item"]
    + [FIELD_COLUMN_LABELS[f] for f in DIFFERENTIATING_FIELDS]
    + ["Applies To (reference)", "Price (INR)", "Status", "Source"]
)
COL_WIDTHS = [13, 12, 18, 12, 14, 26, 18, 26, 14, 16, 14, 14, 12, 16, 34, 13, 12, 28]

STATUS_LOCKED = "Locked"
STATUS_OPEN = "Keep Open"
STATUS_UNPRICED = "Not priced"


def split_field_options(raw: str) -> list[str]:
    """Same "/"-separated split, NIL-dropping convention as app/main.py's
    _split_field_options and static/app.js's splitAccessoryOptions - duplicated here
    rather than imported to keep this module free of a FastAPI import cycle, but
    deliberately identical: a difference in what counts as "a real option" would make a
    declared accessory appear here under a spelling the app itself never matches."""
    out = []
    for part in (raw or "").split("/"):
        cleaned = " ".join(part.split())
        if cleaned and cleaned.upper() != "NIL":
            out.append(cleaned)
    return out


def _applies_to_text(specsheet, config: dict) -> str:
    """Human-readable context for what a row's price covers, for the reference column.
    Only ever informational - unlike the per-field columns, nothing here is parsed back on
    import, so it can safely show a full option set ("5W/7W/9W") without that text ever
    being mistaken for a real single-value constraint."""
    constrained = {k: v for k, v in (config or {}).items() if k != "accessory"}
    if constrained:
        return "only when " + ", ".join(
            f"{FIELD_COLUMN_LABELS.get(k, k)} = {v}" for k, v in sorted(constrained.items()))
    options = specsheet.options or {}
    bits = []
    for field in ("wattage", "cct"):
        values = options.get(field) or []
        if values:
            bits.append(f"{FIELD_COLUMN_LABELS[field]} {'/'.join(values)}")
    return "any configuration" + (f" ({'; '.join(bits)})" if bits else "")


def _row_for(specsheet, priced_item: str, config: dict, entry, applies: str = None) -> list:
    """One sheet row. `entry` is the real PriceEntry when this (product, item, config)
    already has one, or None when it doesn't - the 0-priced gap case this sheet exists to
    surface. `applies` replaces the reference column's text (an accessory part's row)."""
    constrained = {k: v for k, v in (config or {}).items() if k != "accessory"}
    return [
        entry.id if entry else None,
        specsheet.id,
        specsheet.series or "",
        specsheet.family or "",
        specsheet.variant or "",
        specsheet.product_name or "",
        specsheet.typology or "",
        priced_item,
        *[constrained.get(f, "") for f in DIFFERENTIATING_FIELDS],
        applies if applies is not None else _applies_to_text(specsheet, config),
        entry.price if entry else 0,
        (STATUS_LOCKED if entry.status == "locked" else STATUS_OPEN) if entry else STATUS_UNPRICED,
        (entry.source_file or "") if entry else "",
    ]


def build_pricing_master_rows(db) -> list:
    """Every row the sheet should contain, in reading order: each InHouse product's own
    price first, then each accessory its datasheet declares, then any configuration-scoped
    price it already has (a Tunable White chip adder, an IP-rating-specific price).

    Products and declared accessories are emitted whether or not a price exists - that is
    the entire point of this sheet. Configuration-scoped rows are only emitted where one
    already exists, because the set of *possible* configurations is combinatorial (six
    fields, several options each) and filling the sheet with thousands of speculative
    permutations would bury the real gaps it is meant to expose."""
    specsheets = (
        db.query(models.Specsheet)
        .filter(models.Specsheet.make == INHOUSE_ONLY)
        .order_by(models.Specsheet.series, models.Specsheet.family,
                  models.Specsheet.variant, models.Specsheet.product_name)
        .all()
    )
    by_specsheet = {}
    for entry in db.query(models.PriceEntry).filter(models.PriceEntry.specsheet_id.isnot(None)).all():
        by_specsheet.setdefault(entry.specsheet_id, []).append(entry)
    # Accessories picked from the accessory library (app/accessory_parts.py) are priced there:
    # their rows carry the accessory's Price Entry ID, the same on every product that takes it, so
    # a figure typed against any one of them is the accessory's figure.
    from . import accessories, accessory_parts
    links = accessory_parts.links_of(db, [s.id for s in specsheets])
    part_rows = accessory_parts.price_rows(db, [l.part_id for by_sheet in links.values() for l in by_sheet])

    rows = []
    for s in specsheets:
        entries = by_specsheet.get(s.id, [])
        on_parts = {accessories.fold(label): l for l, label in accessory_parts.labelled(links.get(s.id, []))}
        used = set()

        # The product's own price. A 0-priced placeholder row is only emitted when the
        # product has NO product-level price at all - 23 real InHouse products are priced
        # exclusively per IP rating or per wattage (Vena's IP20/IP66 pair, MIST 1615's
        # 5W/10W pair), and those already appear as their own rows below. Emitting an
        # unconstrained 0 row for them too would mark a fully-priced product "Not priced"
        # and invite someone to invent an IP-agnostic number the vendor's list never gave.
        base = next((e for e in entries if not (e.config or {})), None)
        product_level = [e for e in entries if "accessory" not in (e.config or {})]
        if base:
            used.add(base.id)
        if base or not product_level:
            rows.append(_row_for(s, PRODUCT_PRICE_LABEL, {}, base))

        for accessory in split_field_options((s.fields or {}).get("accessories")):
            link = on_parts.get(accessories.fold(accessory))
            if link is not None:
                part = link.part
                shared = "library accessory '%s' - one price for all %d product(s) that take it" % (
                    part.name, len(part.links))
                # One row: its rows always agree, and the import writes the figure to all.
                for e in (part_rows.get(part.id) or [None])[:1]:
                    rows.append(_row_for(s, accessory, {"accessory": accessory}, e, applies=shared))
                continue
            # An accessory can legitimately carry more than one price under one product
            # (a Power supply priced per Control type) - every one of those is its own
            # row, and only when none exists at all does the accessory get a single
            # 0-priced gap row.
            matches = [e for e in entries if (e.config or {}).get("accessory") == accessory]
            for e in sorted(matches, key=lambda e: sorted((e.config or {}).items())):
                used.add(e.id)
                rows.append(_row_for(s, accessory, e.config or {}, e))
            if not matches:
                rows.append(_row_for(s, accessory, {"accessory": accessory}, None))

        # Anything left: configuration-scoped product prices, plus any accessory price
        # whose name no longer appears in the datasheet's own list (a real inconsistency
        # worth showing rather than dropping from the sheet).
        for e in sorted(entries, key=lambda e: sorted((e.config or {}).items())):
            if e.id in used:
                continue
            rows.append(_row_for(s, (e.config or {}).get("accessory") or PRODUCT_PRICE_LABEL, e.config or {}, e))
    return rows


def export_pricing_master(db, out_path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pricing Master"

    ws.append(HEADERS)
    for col_idx in range(1, len(HEADERS) + 1):
        c = ws.cell(row=1, column=col_idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    # Freeze through "Priced Item" (col 8) so the identifying columns stay visible while
    # scrolling right to the Price column on a sheet this wide.
    ws.freeze_panes = "I2"

    price_col = HEADERS.index("Price (INR)") + 1
    status_col = HEADERS.index("Status") + 1
    rows = build_pricing_master_rows(db)
    for row_values in rows:
        ws.append(row_values)
        r = ws.max_row
        status = row_values[status_col - 1]
        fill = {STATUS_LOCKED: LOCKED_FILL, STATUS_OPEN: OPEN_FILL}.get(status, UNPRICED_FILL)
        for col_idx in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=r, column=col_idx)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=col_idx in (8, len(HEADERS) - 3))
            cell.fill = fill
        ws.cell(row=r, column=price_col).number_format = "#,##0"

    for i, w in enumerate(COL_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    unpriced = sum(1 for r in rows if r[status_col - 1] == STATUS_UNPRICED)
    legend = wb.create_sheet("How to use this")
    lines = [
        ("Pricing Master", True),
        ("", False),
        (f"Every {INHOUSE_ONLY} product in the library, and every accessory each product's own "
         f"datasheet lists - {len(rows)} rows, of which {unpriced} have no price yet and show 0.", False),
        ("", False),
        ("To add prices: type the real number into the Price (INR) column, then upload this "
         "same file back through \"Import Pricing Master\" in the app's List Pricing tab. A row "
         "left at 0 is simply skipped, so the sheet can be filled in over several passes - "
         "nothing is ever overwritten with a 0.", False),
        ("", False),
        ("Do not edit the Price Entry ID, Specsheet ID or Priced Item columns. Those three are "
         "how the import knows which product and which accessory a price belongs to. Product "
         "names are NOT used for matching: two real products in this library share an identical "
         "name and differ only in a field that is not shown here, so matching on text would "
         "attach prices to the wrong product.", False),
        ("", False),
        ("Colours: green = Locked (independently confirmed), yellow = Keep Open (mapped to the "
         "right product, not yet double-checked), red = no price yet. A price you type in "
         "arrives as Keep Open unless you also set Status to Locked on that row.", False),
        ("", False),
        ("\"Priced Item\" says what the row prices: \"Product price\" is the fixture itself, any "
         "other value is one of that product's accessories. One product therefore spans several "
         "rows, which is intentional - a product's price genuinely depends on its configuration "
         "(a Tunable White chip or a higher IP rating costs more), and each of those is its own "
         "row with its own price.", False),
        ("", False),
        ("The Finish/CCT/Beam Angle/Wattage/IP Rating/Control columns are constraints, not "
         "description: a value there means the price on that row applies ONLY at that value. "
         "Blank means the price applies across all of them. \"Applies To\" restates the same "
         "thing in words and is reference only - editing it changes nothing.", False),
        ("", False),
        ("This differs from \"Master Price List\", the other export: that one contains only "
         "products that already have prices and exists for revising them when the vendor's list "
         "changes. This one exists to find and fill the gaps.", False),
    ]
    for text, bold in lines:
        legend.append([text])
        cell = legend.cell(row=legend.max_row, column=1)
        cell.font = Font(bold=bold, size=13 if bold and legend.max_row == 1 else 11)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    legend.column_dimensions["A"].width = 110
    for r in range(1, legend.max_row + 1):
        legend.row_dimensions[r].height = 34 if r > 1 else 24

    wb.save(out_path)
    return out_path


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def import_pricing_master(db, file_path: Path, discipline: str = "architectural") -> dict:
    """Applies a filled-in Pricing Master back to the database.

    A row is only acted on when it carries a real, positive price - a 0 (or blank) means
    "still not priced" and is skipped, never written. That is what lets the sheet be
    filled in over several passes: re-importing a partly-completed file can only ever add
    information, never blank out a price somebody already set.

    Existing rows are matched by Price Entry ID. New ones are placed by Specsheet ID plus
    Priced Item plus whatever constraint columns are filled in - all exact values, no name
    matching. An accessory name that the specsheet's own datasheet does not declare is
    reported rather than written, since a price attached to an accessory spelling the app
    never matches would silently never apply."""
    wb = openpyxl.load_workbook(file_path, data_only=True)
    if "Pricing Master" not in wb.sheetnames:
        raise ValueError('This file has no "Pricing Master" sheet - is it the right export?')
    ws = wb["Pricing Master"]

    headers = [c.value for c in ws[1]]
    try:
        col = {name: headers.index(name) for name in
               ["Price Entry ID", "Specsheet ID", "Priced Item", "Price (INR)", "Status", "Product Name"]}
    except ValueError as e:
        raise ValueError(f"This file is missing an expected column: {e}")
    field_cols = {f: headers.index(FIELD_COLUMN_LABELS[f]) for f in DIFFERENTIATING_FIELDS
                  if FIELD_COLUMN_LABELS[f] in headers}

    summary = {"updated": [], "created": [], "unchanged_count": 0,
               "skipped_unpriced": 0, "not_found_ids": [], "problems": []}

    # Rows that price an accessory part (app/accessory_parts.py). A part is listed under every
    # product that takes it, so the sheet can give one part two figures; that is reported and
    # neither is written, because a part has one price and only a person can say which is right.
    from . import accessory_parts

    def part_of(row):
        entry_id = _to_int(row[col["Price Entry ID"]])
        if entry_id is not None:
            p = db.get(models.PriceEntry, entry_id)
            return db.get(models.AccessoryPart, p.accessory_part_id) if p and p.accessory_part_id else None
        item = " ".join(str(row[col["Priced Item"]] or "").split())
        if not item or item.lower() == PRODUCT_PRICE_LABEL.lower():
            return None
        link = accessory_parts.link_for(db, _to_int(row[col["Specsheet ID"]]), item)
        return link.part if link else None

    figures = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        price = _to_float(row[col["Price (INR)"]]) if any(v is not None for v in row) else None
        if price is not None and price > 0:
            part = part_of(row)
            if part is not None:
                figures.setdefault(part.id, set()).add(round(price, 2))
    clashing = {pid for pid, values in figures.items() if len(values) > 1}
    for pid in clashing:
        part = db.get(models.AccessoryPart, pid)
        summary["problems"].append(
            f"Accessory part \"{part.name}\" is given different prices on different rows "
            f"({', '.join(format(v, ',g') for v in sorted(figures[pid]))}) - it has one price for "
            f"every product that takes it, so none of them was written.")
    parts_done = set()

    for row in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in row):
            continue
        price = _to_float(row[col["Price (INR)"]])
        product_name = row[col["Product Name"]] or ""
        # A 0/blank price is the sheet's own "not priced yet" marker, not an instruction.
        if price is None or price <= 0:
            summary["skipped_unpriced"] += 1
            continue
        status = "locked" if str(row[col["Status"]] or "").strip().lower() == "locked" else "open"

        part = part_of(row)
        if part is not None:
            if part.id in clashing or part.id in parts_done:
                continue
            parts_done.add(part.id)
            if any(" ".join(str(row[idx] or "").split()) for idx in field_cols.values()):
                summary["problems"].append(
                    f"{product_name}: \"{part.name}\" is an accessory part with one price for every "
                    f"product that takes it, so it cannot be priced only for some configurations - "
                    f"row skipped.")
                continue
            existing = accessory_parts.price_rows(db, [part.id]).get(part.id, [])
            if not existing:
                entry = models.PriceEntry(
                    discipline=discipline, specsheet_id=None, accessory_part_id=part.id,
                    source_file="Pricing Master", source_sheet="",
                    source_label=f"{part.name} (accessory part, entered via Pricing Master)",
                    config={"accessory": part.name}, price=price, status=status)
                db.add(entry)
                db.flush()
                summary["created"].append({"id": entry.id, "product_name": product_name,
                                           "priced_item": part.name, "price": price})
            elif any(e.price != price or e.status != status for e in existing):
                for e in existing:
                    summary["updated"].append({"id": e.id, "product_name": f"accessory part {part.name}",
                                               "old_price": e.price, "new_price": price})
                    e.price, e.status = price, status
            else:
                summary["unchanged_count"] += 1
            continue

        entry_id = _to_int(row[col["Price Entry ID"]])
        if entry_id is not None:
            p = db.query(models.PriceEntry).filter(models.PriceEntry.id == entry_id).first()
            if p is None:
                summary["not_found_ids"].append(entry_id)
                continue
            if p.price != price or p.status != status:
                summary["updated"].append({"id": p.id, "product_name": product_name,
                                           "old_price": p.price, "new_price": price})
                p.price = price
                p.status = status
            else:
                summary["unchanged_count"] += 1
            continue

        specsheet_id = _to_int(row[col["Specsheet ID"]])
        if specsheet_id is None:
            summary["problems"].append(f"{product_name}: a price was filled in but the Specsheet ID column is blank - that column identifies which product the price belongs to, so this row was skipped.")
            continue
        s = db.query(models.Specsheet).filter(models.Specsheet.id == specsheet_id).first()
        if s is None:
            summary["problems"].append(f"Specsheet ID {specsheet_id} ({product_name}) no longer exists - row skipped.")
            continue

        priced_item = " ".join(str(row[col["Priced Item"]] or "").split())
        if not priced_item:
            summary["problems"].append(f"{product_name}: the Priced Item column is blank, so it is not clear whether this price is for the product or one of its accessories - row skipped.")
            continue

        config = {}
        if priced_item.lower() != PRODUCT_PRICE_LABEL.lower():
            declared = split_field_options((s.fields or {}).get("accessories"))
            match = next((a for a in declared if a.upper() == priced_item.upper()), None)
            if match is None:
                summary["problems"].append(f"{product_name}: \"{priced_item}\" is not an accessory this product's datasheet lists ({', '.join(declared) or 'none'}) - row skipped, since a price stored under a name the app never matches would never apply.")
                continue
            config["accessory"] = match
        for field, idx in field_cols.items():
            value = " ".join(str(row[idx] or "").split())
            if value:
                config[field] = value

        existing = db.query(models.PriceEntry).filter(models.PriceEntry.specsheet_id == specsheet_id).all()
        already = next((p for p in existing if (p.config or {}) == config), None)
        if already is not None:
            if already.price != price or already.status != status:
                summary["updated"].append({"id": already.id, "product_name": product_name,
                                           "old_price": already.price, "new_price": price})
                already.price = price
                already.status = status
            else:
                summary["unchanged_count"] += 1
            continue

        entry = models.PriceEntry(
            discipline=discipline,
            specsheet_id=specsheet_id, source_file="Pricing Master", source_sheet="",
            source_label=f"{priced_item} (entered via Pricing Master)",
            config=config, price=price, status=status,
        )
        db.add(entry)
        db.flush()
        summary["created"].append({"id": entry.id, "product_name": product_name,
                                   "priced_item": priced_item, "price": price})

    db.commit()
    return summary
