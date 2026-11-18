"""Generates a single "Master Price List" workbook covering every product in the
Specsheet library that has at least one mapped PriceEntry - a cleaner, always-current
alternative to asking which price applies to which product every time a list price
changes. Regenerated fresh from the live database on every call (see the
/api/list-pricing/master-export route in app/main.py) - there's no separate file to keep
in sync; re-downloading it after mapping more prices in the List Pricing tab is the
whole update mechanism.

One row per (specsheet, PriceEntry) pair, not one row per specsheet - a product's price
often depends on its own configuration (a wattage-scoped base price, a Control-driven
accessory), so collapsing to a single number per product would hide real, distinct list
prices. Series/Family/Variant alone don't reliably distinguish two rows either - two
products in the same series can genuinely differ only by, say, IP Rating - so every real
distinguishing field (Finish/CCT/Beam Angle/Wattage/IP Rating/Control) gets its own
column rather than being buried in one summary cell. A field the price row doesn't
constrain shows the specsheet's own full option set instead of going blank (e.g. a
flat-priced product's Wattage column shows "5W/7W/9W", not nothing) - that value IS the
real answer to "what does this price cover", not an unknown.

A second sheet lists every still-unmapped PriceEntry (specsheet_id is None) - the real
price-sheet rows waiting on a human to say which product they price - so gaps are
visible without a separate trip to the List Pricing tab.

This file is also the round-trip import format (see app/master_price_import.py) for
the recurring "the vendor's whole price list changed" case (every 6-8 months, per
direct instruction) - export, update the Price/Status columns against whatever the new
revision says, re-upload the same file, and every changed number gets applied without
re-deriving which specsheet each row means. "Price Entry ID" (first column, both
sheets) is what makes that safe: Series/Family/Variant/Wattage/etc. text is for a human
to read, not a reliable re-match key on its own - two real specsheets in this library
(Vena S 10's two accessory variants) share identical Series/Family/Variant/Product Name
and differ only in their own Accessories field, which isn't even a column here.
"""
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from . import models

# Same fields the Configurator itself keys a price on (see CONFIG_FIELD_KEYS in
# static/app.js) - order matches how they're presented there too.
DIFFERENTIATING_FIELDS = ["finish", "cct", "beam_angle", "wattage", "ip_rating", "control"]
FIELD_COLUMN_LABELS = {
    "finish": "Finish", "cct": "CCT", "beam_angle": "Beam Angle",
    "wattage": "Wattage", "ip_rating": "IP Rating", "control": "Control",
}

HEADER_FILL = PatternFill("solid", fgColor="2F2A22")
HEADER_FONT = Font(color="FFFFFF", bold=True)
LOCKED_FILL = PatternFill("solid", fgColor="D7ECD9")
UNMAPPED_FILL = PatternFill("solid", fgColor="FBE8AE")
THIN = Side(style="thin", color="D9D3C7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _mapped_field_value(config: dict, specsheet, field: str) -> str:
    """What this PriceEntry row shows for one differentiating field: the row's own
    constraint if it has one (this price only applies at that exact value), otherwise
    the specsheet's real option set for that field (this price applies across all of
    them) - never a blank standing in for "don't know", since both cases are actually
    known."""
    if field in config:
        return str(config[field])
    options = (specsheet.options or {}).get(field) or []
    if options:
        return "/".join(options)
    value = (specsheet.fields or {}).get(field, "")
    return str(value) if value and value != "-" else ""


def _style_header(ws, headers):
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=col_idx)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"


def _set_widths(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def export_master_price_list(db, out_path: Path) -> Path:
    wb = openpyxl.Workbook()

    # ---- Sheet 1: mapped prices, one row per (specsheet, PriceEntry) ----
    ws = wb.active
    ws.title = "Master Price List"
    field_headers = [FIELD_COLUMN_LABELS[f] for f in DIFFERENTIATING_FIELDS]
    headers = (["Price Entry ID", "Series", "Family", "Variant", "Make", "Product Name", "Typology", "Accessory"]
               + field_headers + ["Price (INR)", "Status", "Source File"])
    _style_header(ws, headers)
    accessory_col = 9
    price_col = len(headers) - 2

    mapped = (
        db.query(models.PriceEntry)
        .filter(models.PriceEntry.specsheet_id.isnot(None))
        .all()
    )
    # Base prices before accessory/adder rows for the same product, then by whatever
    # distinguishes them - a human scanning the sheet sees "what this product costs"
    # before "what it costs extra", grouped by the same product rather than scattered.
    def _sort_key(p):
        config = p.config or {}
        s = p.specsheet
        field_values = tuple(_mapped_field_value(config, s, f) for f in DIFFERENTIATING_FIELDS)
        return (s.series or "", s.family or "", s.variant or "", s.product_name or "",
                "accessory" in config, config.get("accessory", ""), field_values)
    mapped.sort(key=_sort_key)

    for p in mapped:
        s = p.specsheet
        config = p.config or {}
        row = ws.max_row + 1
        field_values = [_mapped_field_value(config, s, f) for f in DIFFERENTIATING_FIELDS]
        ws.append([
            p.id, s.series or "", s.family or "", s.variant or "", s.make or "", s.product_name or "",
            s.typology or "", config.get("accessory", ""),
            *field_values,
            p.price, "Locked" if p.status == "locked" else "Keep Open", p.source_file or "",
        ])
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row, column=col_idx)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=col_idx == accessory_col)
            if p.status == "locked":
                cell.fill = LOCKED_FILL
    # The accessory library's prices (models.AccessoryPart), after the products. A library
    # accessory is shared by every product that takes it, so it is named by itself and how many
    # products take it rather than repeated under each; revising its figure here revises it for
    # all of them, which is what its price is.
    # One row per accessory, even when it holds several price rows (products' own prices that
    # moved onto it): the rows always agree, the import writes a new figure to all of them, and
    # listing two would let the unchanged one put the old figure back.
    part_rows, seen_parts = [], set()
    for p in (db.query(models.PriceEntry)
              .filter(models.PriceEntry.accessory_part_id.isnot(None))
              .order_by(models.PriceEntry.accessory_part_id, models.PriceEntry.id).all()):
        if p.accessory_part_id not in seen_parts:
            seen_parts.add(p.accessory_part_id)
            part_rows.append(p)
    for p in part_rows:
        part = db.get(models.AccessoryPart, p.accessory_part_id)
        taking = len(part.links) if part else 0
        specs = ", ".join("%s %s" % (sp.get("name"), " / ".join(sp.get("options") or []))
                          for sp in (part.params if part else []) or [])
        row = ws.max_row + 1
        ws.append([
            p.id, "", "", "", part.make if part else "",
            "Library accessory: %s%s (%d product%s)" % (part.name if part else "?",
                                                       " - " + specs if specs else "", taking,
                                                       "" if taking == 1 else "s"),
            "", (p.config or {}).get("accessory", ""),
            *["" for _f in DIFFERENTIATING_FIELDS],
            p.price, "Locked" if p.status == "locked" else "Keep Open", p.source_file or "",
        ])
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row, column=col_idx)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=col_idx == accessory_col)
            if p.status == "locked":
                cell.fill = LOCKED_FILL
    _set_widths(ws, [10, 16, 12, 12, 12, 26, 16, 20, 14, 12, 12, 12, 12, 12, 13, 11, 34])
    # Freeze the identifying columns (Series..Accessory) too, not just the header row - so
    # the differentiating fields and price stay readable when scrolled right on a sheet
    # this wide.
    ws.freeze_panes = get_column_letter(accessory_col + 1) + "2"

    # ---- Sheet 2: still-unmapped price sheet rows, waiting on a human decision ----
    ws2 = wb.create_sheet("Needs mapping")
    headers2 = (["Price Entry ID", "Source File", "Source Sheet", "Description (as parsed)", "Accessory"]
                + field_headers + ["Price (INR)"])
    _style_header(ws2, headers2)

    unmapped = (
        db.query(models.PriceEntry)
        .filter(models.PriceEntry.specsheet_id.is_(None),
                models.PriceEntry.accessory_part_id.is_(None))   # a part's price is mapped - to the part
        .order_by(models.PriceEntry.source_file, models.PriceEntry.id)
        .all()
    )
    for p in unmapped:
        config = p.config or {}
        row = ws2.max_row + 1
        description = (p.source_label or "").split("\n")[0][:120]
        field_values = [str(config.get(f, "")) for f in DIFFERENTIATING_FIELDS]
        ws2.append([p.id, p.source_file or "", p.source_sheet or "", description, config.get("accessory", ""),
                    *field_values, p.price])
        for col_idx in range(1, len(headers2) + 1):
            cell = ws2.cell(row=row, column=col_idx)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=col_idx == 4)
            cell.fill = UNMAPPED_FILL
    _set_widths(ws2, [10, 34, 16, 46, 20, 14, 12, 12, 12, 12, 12, 13])
    ws2.freeze_panes = "E2"

    # ---- Sheet 3: legend ----
    legend = wb.create_sheet("How to use this")
    lines = [
        ("Master Price List", True),
        ("", False),
        ("Every price mapped in the app's List Pricing tab, one row per product configuration "
         "(a product can have more than one row - a base price plus any Control/IP-Rating/etc. "
         "adder that applies on top of it).", False),
        ("", False),
        ("Series/Family/Variant alone don't always tell two rows apart - two products in the "
         "same series can differ only by, say, IP Rating. Finish/CCT/Beam Angle/Wattage/IP "
         "Rating/Control each get their own column: the row's own value if this price is "
         "specific to it, or the product's full real option set if this price applies across "
         "all of them (e.g. a flat-priced product shows \"5W/7W/9W\" in Wattage, not a blank).", False),
        ("", False),
        ("IMPORTANT - one product, several rows: when a specific configuration costs more, it "
         "gets its OWN row here with its own full price, not a hidden surcharge applied "
         "somewhere in the app. NYX is the clearest example: NYX -Midi appears once at its "
         "base price with CCT \"2700K/3000K/4000K/TW (2700K-6500K)\" (all options, same price), "
         "and again at a higher price with CCT pinned to \"TW (2700K-6500K)\" alone - that "
         "second row IS the Tunable White chip upcharge. Same for its \"Wall Wash\" Beam Angle "
         "row (the polarizing reflector). Every price rule in this business lives here as a "
         "real row, so this sheet is genuinely the whole picture - nothing is applied on top "
         "of it invisibly.", False),
        ("", False),
        ("This means a base-price revision usually touches more than one row for the same "
         "product. Rows for one product sit together and each names its own configuration, so "
         "check the whole group when you change any one of them - e.g. if NYX -Midi's base "
         "moves, its TW row and its Wall Wash row both need the same move applied.", False),
        ("", False),
        (f'"Locked" (green) = independently confirmed. "Keep Open" (white) = mapped to the '
         f'right product, price not yet double-checked - both already feed the app\'s own '
         f'Configurator, this sheet is just a readable export of the same data.', False),
        ("", False),
        ('"Needs mapping" (yellow sheet) lists real price sheet rows nobody has attached to a '
         "product yet - the gaps still waiting on a decision.", False),
        ("", False),
        ("This file is regenerated fresh from the live database every time it's downloaded - "
         "when a price changes or a new one gets mapped in the app, just download again from "
         "the List Pricing tab's \"Export Master Price List\" button. There is nothing to keep "
         "in sync by hand.", False),
        ("", False),
        ("When the vendor's whole price list changes (every 6-8 months)", True),
        ("Update the Price column (and Status, if you're re-confirming a number) against "
         "whatever the new price list says, then re-upload this same file from \"Import Master "
         "Price List\" in the List Pricing tab. Matched by \"Price Entry ID\" in the first column "
         "- not by Series/Family/Variant text, which two real products can share - so don't "
         "edit that column. A changed price is applied immediately; it's marked Keep Open "
         "again unless you also set Status to Locked in the same edit, since a new number "
         "hasn't been independently re-verified yet just because it was typed in.", False),
        ("A brand-new product or configuration doesn't have a Price Entry ID yet - add it as a "
         "new row on the \"Needs mapping\" sheet instead (leave Price Entry ID blank there), "
         "fill in whatever Description/fields/Price you know, and it lands in the app's own "
         "\"Needs mapping\" queue for someone to attach to the right specsheet - never guessed "
         "from the text in this file. A blank-ID row added to the Master Price List sheet "
         "itself is rejected for the same reason: two real products in this library can share "
         "identical Series/Family/Variant/Product Name text, so there's no safe way to infer "
         "which specsheet a brand-new row like that belongs to.", False),
        ("A row's price is left alone if you delete the row from this file entirely - removing "
         "a row is never read as \"delete this price\". Retiring a price is still a deliberate "
         "action in the List Pricing tab itself.", False),
    ]
    for text, bold in lines:
        legend.append([text])
        legend.cell(row=legend.max_row, column=1).font = Font(bold=bold, size=13 if bold and legend.max_row == 1 else 11)
        legend.cell(row=legend.max_row, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    legend.column_dimensions["A"].width = 110
    for r in range(1, legend.max_row + 1):
        legend.row_dimensions[r].height = 30 if r > 1 else 24

    wb.save(out_path)
    return out_path
