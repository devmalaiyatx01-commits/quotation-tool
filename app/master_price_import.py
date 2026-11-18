"""Re-imports a Master Price List workbook (see app/master_price_export.py) - the other
half of that file's round-trip design, for the recurring "the vendor's whole price list
changed" case (every 6-8 months, per direct instruction): export, update the Price/
Status columns against whatever the new revision says, re-upload the same file, and
every real change gets applied without a developer re-deriving which specsheet each row
means or writing a new one-off backfill script.

Matched by "Price Entry ID" (each sheet's first column) - never by Series/Family/
Variant/etc. text, which isn't a safe re-match key on its own (two real specsheets in
this library - Vena S 10's two accessory variants - share identical Series/Family/
Variant/Product Name and differ only in a field that isn't even a column here). A row
whose price differs from what's stored gets updated and, unless the sheet's own Status
cell says "Locked", reset to "Keep Open" - a changed number hasn't been independently
re-verified just because someone typed it into a spreadsheet. A row simply missing from
the re-uploaded file is left untouched - never read as "delete this price".

A blank Price Entry ID in the "Master Price List" sheet is rejected, not guessed at: with
no ID, safely resolving which specsheet a brand-new row belongs to would mean fuzzy-
matching Series/Family/Variant text, exactly the kind of guess this project's whole
pricing-matching discipline (app/price_matcher.py) exists to avoid. A blank ID in the
"Needs mapping" sheet is different and fully supported - that sheet's whole purpose is
prices with no specsheet attached yet, so a new row there becomes a new unmapped
PriceEntry, landing in the same human-reviewed queue any other unmapped price does.
"""
from pathlib import Path

import openpyxl

from . import models
from .master_price_export import DIFFERENTIATING_FIELDS, FIELD_COLUMN_LABELS


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _status_from_cell(v) -> str:
    return "locked" if str(v or "").strip().lower() == "locked" else "open"


def import_master_price_list(db, file_path: Path, discipline: str = "architectural") -> dict:
    wb = openpyxl.load_workbook(file_path, data_only=True)
    summary = {"updated": [], "unchanged_count": 0, "created": [], "not_found_ids": [], "rejected_blank_id": 0,
               "problems": []}

    if "Master Price List" in wb.sheetnames:
        ws = wb["Master Price List"]
        headers = [c.value for c in ws[1]]
        id_col = headers.index("Price Entry ID")
        price_col = headers.index("Price (INR)")
        status_col = headers.index("Status")
        product_col = headers.index("Product Name")

        # An accessory part has one price (app/accessory_parts.py). An export lists it once, but an
        # older or hand-edited sheet can carry two of its rows; if they give two figures, which one
        # is right is a person's call, so neither is written.
        part_figures = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            pid, price = row[id_col], _to_float(row[price_col])
            if pid is None or price is None:
                continue
            p = db.get(models.PriceEntry, int(pid))
            if p is not None and p.accessory_part_id:
                part_figures.setdefault(p.accessory_part_id, set()).add(price)
        clashing = {part_id for part_id, figures in part_figures.items() if len(figures) > 1}
        for part_id in clashing:
            part = db.get(models.AccessoryPart, part_id)
            summary["problems"].append(
                "Accessory part \"%s\" is given different prices on different rows (%s) - it has one "
                "price, so none of them was written." % (
                    part.name if part else part_id,
                    ", ".join(format(v, ",g") for v in sorted(part_figures[part_id]))))

        for row in ws.iter_rows(min_row=2, values_only=True):
            if all(v is None for v in row):
                continue
            pid, price, status_cell = row[id_col], _to_float(row[price_col]), row[status_col]
            if pid is None:
                summary["rejected_blank_id"] += 1
                continue
            p = db.query(models.PriceEntry).filter(models.PriceEntry.id == int(pid)).first()
            if p is None:
                summary["not_found_ids"].append(int(pid))
                continue
            if p.accessory_part_id in clashing:
                continue
            new_status = _status_from_cell(status_cell)
            if price is not None and price != p.price:
                summary["updated"].append({"id": p.id, "product_name": row[product_col], "old_price": p.price, "new_price": price})
                p.price = price
                p.status = new_status
                # An accessory part's price rows always agree (app/accessory_parts.py): the new
                # figure is the part's, on every row it has.
                if p.accessory_part_id:
                    for other in db.query(models.PriceEntry).filter(
                            models.PriceEntry.accessory_part_id == p.accessory_part_id,
                            models.PriceEntry.id != p.id).all():
                        other.price = price
            elif new_status != p.status:
                summary["updated"].append({"id": p.id, "product_name": row[product_col], "old_price": p.price, "new_price": p.price, "status_only": True})
                p.status = new_status
            else:
                summary["unchanged_count"] += 1

    if "Needs mapping" in wb.sheetnames:
        ws2 = wb["Needs mapping"]
        headers2 = [c.value for c in ws2[1]]
        id_col = headers2.index("Price Entry ID")
        price_col = headers2.index("Price (INR)")
        source_file_col = headers2.index("Source File")
        source_sheet_col = headers2.index("Source Sheet")
        desc_col = headers2.index("Description (as parsed)")
        field_labels = {"accessory": "Accessory", **FIELD_COLUMN_LABELS}
        field_cols = {field: headers2.index(label) for field, label in field_labels.items()}

        for row in ws2.iter_rows(min_row=2, values_only=True):
            if all(v is None for v in row):
                continue
            pid, price = row[id_col], _to_float(row[price_col])
            if pid is not None:
                p = db.query(models.PriceEntry).filter(models.PriceEntry.id == int(pid)).first()
                if p is None:
                    summary["not_found_ids"].append(int(pid))
                    continue
                if price is not None and price != p.price:
                    summary["updated"].append({"id": p.id, "product_name": row[desc_col], "old_price": p.price, "new_price": price})
                    p.price = price
                else:
                    summary["unchanged_count"] += 1
                continue
            # Blank ID: a brand-new price row someone added by hand - only real if it has a
            # price to carry; an otherwise-blank template row is silently skipped, not an error.
            if price is None:
                continue
            config = {}
            for field, col in field_cols.items():
                val = row[col]
                if val not in (None, ""):
                    config[field] = str(val)
            entry = models.PriceEntry(
                discipline=discipline,
                specsheet_id=None,
                source_file=row[source_file_col] or "Master Price List (manually added)",
                source_sheet=row[source_sheet_col] or "",
                source_label=row[desc_col] or "",
                config=config, price=price, status="open",
            )
            db.add(entry)
            db.flush()
            summary["created"].append({"id": entry.id, "description": entry.source_label, "price": price})

    db.commit()
    return summary
