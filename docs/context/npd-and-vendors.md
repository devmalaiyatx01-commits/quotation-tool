# Context pack: NPD, product codes, the Configurator, and vendors

Read this when you are changing how products are authored (NPD), how a product code or a vendor's
order code is built, what the Configurator asks, or a brand's products. For DuraGreen use the
`duragreen-codes` skill, and for any other brand the `vendor-products` skill. They hold the step
by step. This pack is the map.

## Where things are

- **NPD (New Product Development)** authors a product instead of reading one from a PDF:
  `app/npd.py::assemble` turns the form into `params`/`fields`/`options`, and
  `app/npd.py::price_for` / `app/npd.py::price_breakdown` price a configuration from the product's
  own base price and adders. Routes: `code_map.py app/main.py --routes --grep npd`. The screen:
  `static/app.js::renderNpd`; its brand switch is `static/app.js::npdChosenBrand` over
  `static/app.js::NPD_BRANDS`, a list written into the code (on the plan to come from data).
- **The studio's product codes** (InHouse and the studio's own) are assembled in exactly one
  place: `app/product_codes.py::compose`, through `app/product_codes.py::code_for_sheet`. The
  structure and abbreviations are data, edited in NPD -> Product codes.
- **A vendor's own order codes** (DuraGreen, and any brand after it) are never built from the
  studio's tables. They live in `app/models.py::VendorOrderCode`, `app/models.py::VendorCodePart`,
  `app/models.py::VendorCodeRule`, `app/models.py::VendorCodeStructure` and
  `app/models.py::VendorProductNaming`. DuraGreen's reading of them is `app/duragreen.py`
  (`app/duragreen.py::is_duragreen`, and `app/duragreen.py::describe` for how sure a code is).
- **What the Configurator asks** per brand and typology: `app/configurator.py::candidates` and
  `app/configurator.py::label_for`, decided in NPD. It is the one list both the Configure tab and
  the product form take. While nobody has decided a DuraGreen typology, `app/duragreen.py::configure_extras`
  adds that make's other options to Configure. A product appears under its typology and the kind its maker
  files it under (`app/typology_match.py::category_kind`).
- **Exenia** composes its codes from options: `app/exenia_codes.py::option_skus`.
- **The InHouse accessory library**: `app/accessory_parts.py` (`app/accessory_parts.py::is_part_brand`).

## The special-case debt lives here

Most of the 51 special cases in shared code (`audit_special_cases.py`, 2026-11-02) are brand
dispatch in this area. By brand: DuraGreen 44 (35 of them in `app/main.py`: its own
`/api/npd/duragreen/...` routes and filters by `duragreen.MAKE`), Exenia 5 (main.py, pdf_parser.py,
app.js) and InHouse 2. app.js holds 9 of the 51. They exist because each vendor was built before the framework was general. The
direction (docs/plans/2026-11-02-engineering-framework.md, phase 3) is one set of vendor routes
keyed by make, and screens driven by what a product *has* (vendor codes, a config layout), never
by its name. **Do not add to the debt.** A new brand goes through the vendor tables and its own
module, and the gate fails a branch that adds a brand check without a reason.

## Rules that bite here

- **The vendor's documents are the truth.** A code is only as sure as its least sure piece. Two
  sources that disagree mean no code. Never invent one (CLAUDE.md, "Vendor products").
- **A range's code table is that range's rule only.** DuraGreen's ACME table is the ACME downlights'.
- **A re-read never deletes a row a person entered** ("entered by ...").
- **A code load never touches a price**, and decorative pricing is one brand at a time, only when asked.

## How to check a change here

- DuraGreen: `python scripts/maintenance/audit_duragreen_codes.py --compare HEAD`: 0 round-trip
  failures, and a reason for every code that changed.
- InHouse and studio codes: compare `code_for_sheet` for every product before and after. A change
  that alters any code is a `data`-lane change (codes may later key prices).
- `python scripts/maintenance/audit_special_cases.py`: the count must not go up.
