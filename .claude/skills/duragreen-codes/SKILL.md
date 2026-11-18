---
name: duragreen-codes
description: DuraGreen in NPD - how its order codes (nomenclature) are built, checked and loaded; the re-scrape and backfill pipeline; and how to answer "why does this DuraGreen product get this code, or none". Use for any DuraGreen code, nomenclature, spec sheet, catalogue, website import or NPD (DuraGreen) task. For another brand, use vendor-products.
---

# DuraGreen codes

Read `vendor-products` for the general rules; this is DuraGreen's version. The full reasoning is
in the docstring at the top of `app/duragreen.py` - read it before changing how a code is built.

## Whose rule a product follows (`duragreen.scheme_of`)

| Rule | Products | Where a piece may come from, surest first |
|---|---|---|
| `ACME` | catalogue PDF pp.57-223 (the ACME downlights) | own sheet & page -> ACME table (p.33) -> other ACME pages |
| `MOOD` | catalogue PDF pp.246-289 (the MOOD downlights) | own sheet & page -> MOOD table (p.222) -> other MOOD pages |
| `EVOLINE` | catalogue PDF pp.226-241, 394-395, 434-435, 534-541, 546-547 (recessed lines, surface square, pendant, 48V) | own sheet & page -> EVOLINE table (the **ACME price list** p.127, Sep 2024 - its only table) -> other EVOLINE pages |
| `PAGE` | any other catalogue page: step lights, shelf lights, spotlights, pendants, wall, 48V, 220V track spots, outdoor | own sheet's printed codes and own page only |
| `SHEET` | no catalogue page (strips, neon, track parts, a few others) | own sheet's printed codes only |
| `NONE` | nothing printed anywhere | no code until a person adds DuraGreen's |

Outside that column every piece is **unconfirmed** (another range's table, another range's page,
or DuraGreen's pattern such as `2700K -> 27`), and where ACME's and MOOD's tables disagree
(`Mains Dim`: `P` / `M`) there is **no code**. Ranges are decided by the template's **PDF page**
(`printed.pdf_page`); the source text's `p.42` is the printed folio, 23 less (`FOLIO_OFFSET`).

Statuses (`order_code(...)["status"]`), and how each reaches a BOQ line (`describe`):
`printed` (whole code printed, or typed in from DuraGreen's answer), `composed` (all pieces this
product's), `catalogue` (its range's table, or power written as its codes write it), `pattern`
(a CCT or beam written as its own codes write the ones they print: 30 for 3000K, so 27 for
2700K), `general` (elsewhere in its own range) are written plain; `unconfirmed` carries
"(unconfirmed - check with DuraGreen)" onto the line.

**Deriving the logic from the product's own codes** (owner, 22 Oct): a printed code is decoded
into the product's own options (`decode_piece`: `ALP6003W-AG-30N120`'s 30 is its 3000K, N its
On/off - only when exactly one option takes the piece and every source agrees), and the product's
other options follow the pattern its codes show (`pattern`). Driver letters have no pattern, so a
control the product's codes do not print stays `unconfirmed`.

## Every code table DuraGreen prints (searched 26 Oct, every document and web page)

`python scripts/catalogues/read_duragreen_code_matrices.py` reads them all into
`Duragreen Catalogue/duragreen_code_matrices.json`, side by side in `code_matrices_compared.csv`.

| Range | Printed table | Notes |
|---|---|---|
| ACME | 2026 catalogue PDF p.56 (printed 33); 2025 catalogue PDF p.72 (= 35 spec sheets); price list printed p.27 (PDF 33); The Chosen Ones printed p.151 (PDF 174) | 2026 restructured it: styles moved from Type to Baffle/Bezel Style, Phase Cut `P` (earlier `M` Mains Dim), new colours; `AB` = Antique Brass in 2026, Antique **Bronze** before. The 2026 table decides; earlier editions (`EARLIER_EDITIONS`) fill only colours it dropped (BG BN SB AB-bronze PC) |
| MOOD | 2026 catalogue PDF p.245 (prints no number; p.246 prints 219); 2025 catalogue PDF p.161; 8 spec sheets | identical in all |
| EVOLINE | ACME public price list p.127 (Sep 2024) only | loaded as range table `EVOLINE`; writes 2700K-6000K as `2765` (ACME's table: `2760`) |
| BELLA 41, GLAMO 68, ARTFOCUS 36 | price list pp.161, 185, 207 | loaded since 26 Oct (type `RT`, Structure Colour) |
| FLOW | **none anywhere** | only each page's legend (W white bezel, B black baffle); other baffle colours have no published letter |

**Verified against the sheets** (`python scripts/maintenance/verify_duragreen_nomenclature.py`):
every edition, every piece of every printed code - **0 differ**. But the sheets print almost only
3000K + On/off (2,581 rows), so the check confirms each table's skeleton; the tables supply every
other CCT, driver and finish. Loaded since 26 Oct also: `BELLA 41`, `GLAMO 68`, `ARTFOCUS 36` (price
list pp.161/185/207; catalogue PDF pp.318-319, 322-323, 328-329, 334-335, 338-339).

**A code for every combination, in this order** (`duragreen.LOOKUP_ORDER`, shown on every NPD
product): printed whole > the product's own pieces > its range's table > its own range elsewhere
(other pages, an earlier edition for a meaning the newest lacks) > its own codes' pattern > other
ranges' tables where all agree (unconfirmed) > none, with the reason. Rules that make it reach far:
- **An own prefix** (`_own_prefix`): High Efficiency prints `H30` and `H27`, so a CCT is looked up
  bare and written with its H (`H35`). Only when *every* printed code carries it.
- **A range's own change is not a disagreement** (`lookup.lead`): ACME's 2026 table alone writes
  Phase Cut `P`; ACME 2024-25 and every other table write `M`, so a product outside ACME takes `M`
  (unconfirmed). ACME products keep `P`.
- **Misprints are set aside, with the reason** (`MISPRINTS` in the code-rules backfill): EVOLINE's
  `2765 = 2700K-6000K` contradicts six tables (`2765` = 2700K-6500K, `2760` = 2700K-6000K).
- **A sheet printing several fittings offers the version** (`offer_versions`, selector `version`):
  RTC23 / TLC23, SA36 / SA36/SB, PURSE / PUSSE ... Each row carries its version; the person picks.
- **A table with no ordinary rows** keeps its codes whole (`_whole_codes`); spaced watts (`6 W`)
  are read (`_tidy_row`) - GLAMO's 48V and track sheets had no codes until 26 Oct.
- **No Driver for every product that has a driver letter** (`duragreen.with_no_driver`, owner
  26 Oct: the studio often quotes its own driver). Every table DuraGreen prints writes `X` = No
  Driver, so a product whose code carries a control letter and which offers a control is also
  offered `No Driver`: code-rules backfill step 7, and the importer's `apply()` for new products
  (389 products locally). Not offered: strips (their control is the driver's), drivers, products
  offering no control (No Driver would be their only choice, every code an X).
- **A re-read never defers to a row another backfill rebuilds** (`duragreen_import.apply`): only a
  code a person entered stands in for the sheet's own row. Until 26 Oct a sheet code was skipped
  when the catalogue's example held it, the code-rules backfill then rebuilt the catalogue rows,
  and the STICK bollard's code moved between products from one run to the next. Proof of a fix of
  this kind: the pipeline twice, `dg_state` after each, identical.
- **Every product has a picture from its own sheet** (`duragreen_import.picture_for`, owner 27 Oct:
  "images are still not fixed"): a listed crop (`PICTURE_CROPS`, checked by eye), else the drawing
  beside the product's own name (a system sheet like MINI-LINE 11 draws each part by its name) -
  never the system drawing (>30% of the page), never a drawing nearer a sibling's name, within
  `LABEL_REACH` and mostly on the page - else, for a sheet of one product alone, its largest
  picture. `scripts/backfills/backfill_duragreen_pictures.py` fills only EMPTY or missing
  pictures (dry run renders into `Duragreen Catalogue/picture_preview/` to look at first); the
  importer uses the same rule when a card has none. Pipeline order now: npd -> code rules ->
  tracks -> typology -> pictures.
- **`order_code(..., memo=)`**: `combinations` shares one dict across a product's combinations so
  its own meanings and each option's lookup are worked out once - proven identical on all
  106,724 combinations (`memo_same.py` in the 27 Oct scratchpad), 2.6x faster.
- **A loose match keeps the shape** (`_shape`): "every word of the value is in the meaning" finds
  White in White Matt, but a CCT or beam must name as many numbers as the entry - 6500K is not
  2700K-6500K. Until 26 Oct fixed 6500K neons took `2765` and a 4000K `1840`.
- **The pattern writes no code for a fragment or a mix** (`_pattern`): a CCT or beam with brackets,
  `+`, a comma, RGB or "on request" (`3000K (2400K` was `3024`, `2700K-6000K + RGB` was `2760`). The
  LUNRAY garden spikes / projector still carry those split fragments as options - a reading fix to make.
- What stays uncoded has no published letter: Bespoke, three MOOD baffles, Heritage Brass, FLOW's
  Black Nickel, RGB, strips that print no CCT. Coverage 26 Oct (after the reload): 89.6% of
  93,131 combinations, 0 round-trip failures.
- **Every open question for DuraGreen** is in `Duragreen Catalogue/ILDL_DuraGreen_Order_Code_Questions_2026-10-26.pdf`
  (24 questions, product lists in its appendix), built from the engine's own answers by a script.
  When DuraGreen answers, the answers go into the tables / `vendor_order_codes` ("entered by"), never
  into a guess.
- **Known, not yet fixed: catalogue page labels drift.** `FOLIO_OFFSET = 23` (reader, engine) is
  true up to PDF p.245 only; the printed number is PDF - 27 from p.246 and PDF - 29 from p.292. So a
  "2026 General Catalogue p.N" label past p.245 points 4-6 pages early (the bollard's "p.656" is
  PDF p.679, printed 650). Codes are unaffected - `range_of` adds the same 23 back - so the fix must
  change the labels, the parse and the live rows together (a migration), not the constant alone.

NPD shows it all at the foot of each product: `GET /api/npd/duragreen/products/<id>/nomenclature`
(`duragreen.nomenclature`) - the code piece by piece, the order above, and every option's code and
source. The two sides of the product page scroll on their own (`.dg-pane`).

Documents beyond the product pages live in `Duragreen Catalogue/site_crawl/other_documents/` (from
the site's /catalogue/ page): 2025 catalogue, ACME price list, The Chosen Ones, 2024 look book.

## The four key series, classes, and quoting in one click (owner, 22 Oct)

- **ACME, MOOD, EVOLINE, FLOW** matter most. `duragreen.key_series_of(series, name, typology)`
  says which a product is on (its series or name says so; a demo box never is). NPD (DuraGreen)
  opens on a shelf of the four, then DuraGreen's own range names and sizes; Add Product has a
  **DuraGreen series** tab with the same shelf.
- **Classes**: `duragreen.typology_for` reads a lighting designer's class from the product's words
  (`SYSTEM_RULES` first - a pendant on the 48V track is a Track Light - then `TYPOLOGY_RULES` on
  name, series, category). It only fills an EMPTY typology: `backfill_duragreen_typology.py`
  (dry-run first; report CSV lists the "kept" ones where a person's class and the rules differ).
  The npd backfill and the importer use it for a new product the website names no class for.
- **Printed by DuraGreen** (Add Product, above the form, any tab): `printed_choices` gives each
  printed code with the options that order it, the finish that reproduces it exactly where the
  row names none (found by asking `order_code`, so they cannot disagree), and what the options
  give (`gives`, `missing`). A click sets the form's dropdowns; the code is still the server's
  answer (`suggest-description`). `GET /api/npd/duragreen/products/<id>/printed`.
- **A code waiting for its finish is a head, not a code**: `order_code` returns the head with
  `missing`; `describe` writes nothing and `suggest-description` sends it as
  `product_code_partial` (shown "…", nothing to copy) - until 22 Oct the head went on the line.
- **A beam in letters glued to the driver** (`30NWW`) is the head's end, not the finish
  (`_head_end`): the wall-wash track heads lost their `WW` until 22 Oct.
- The sheet's short finish (MOOD prints `-W`) and the catalogue's (`-WB`, bezel + baffle) differ;
  the engine builds the catalogue's. Ask DuraGreen which they want on an order.
- **Linking a product to its catalogue page** (code-rules backfill): the page whose code table
  prints the most of its codes; failing that, one page printing *every* code with the CCT masked
  (`cct_blind`: p.410 prints the Alabaster pendants at 27, their sheet at 30). An `H30` (High
  Efficiency) is never masked - HE fittings are not the standard page's fittings.
- **Renamed in the 2026 catalogue** (open, owner's decision): five MOOD products print old codes on
  their website sheets and new ones in the catalogue (`AFP6006W` -> `AFL6006W-P`, `MFW75` ->
  `MFL75` ...) - `Duragreen Catalogue/renamed_in_catalogue_2026.csv`. Never link them by a fuzzy
  size/watt/beam match: it pairs unrelated fittings (an outdoor wall light with ACME IN-OUT).

Check: `scratchpad`-style throwaway test - for every approved key-series product, each printed
row's vector through `suggest-description` must give `gives`, never a different code, and never
leak a head onto the description.

## Naming structures (Nomenclature tab, 27 Oct 2026)

Nomenclature with DuraGreen chosen shows DuraGreen's naming, never the studio's product code
(`GET /api/npd/duragreen/naming`, `dgNaming*` in app.js): the printed tables as structures, each
product filed by `duragreen.naming_class` (a table, PAGE, SHEET grouped by `type_letters`, NONE).
- **A letter DuraGreen sends** goes into its table as a `vendor_code_rules` row with source
  `entered by ...` (`POST .../structures/entries`; refused when the meaning or the code is already in
  that segment). Only entered rows can be removed; the code-rules backfill never deletes them.
- **A structure made in NPD**: skeleton in `vendor_code_structures`, letters in `vendor_code_rules`
  under its name. Its name may not be a DuraGreen table's (`RESERVED_NAMES`).
- **A product put in a structure** (`vendor_product_naming`; product page -> Naming, or "Type it
  in"): only one no catalogue page names (409 otherwise). Per segment fixed / quoted / out; its type
  and size are never quoted (`configurator.IDENTITY_KEYS`). Its codes come from
  `duragreen.structure_code` - printed/entered whole first, then its own parts, the structure's
  letters, its pattern - via `naming=` on order_code, combinations, printed_choices, nomenclature.
  Every product without a row takes the old path unchanged: prove it with `--compare HEAD`.

## Answering "why does product X get code Y (or none)?"

    python scripts/maintenance/audit_duragreen_codes.py --product <id>

prints its rule, every printed code with the configuration it orders, and every combination's
code and status (or the reason it has none). In the app: NPD -> DuraGreen -> the product -> the
code tester's pieces table says where each piece came from; "Codes for every combination" shows
all of them.

## Re-scrape and reload (local only)

Back up first (`pg_dump` from `C:\Program Files\PostgreSQL\18\bin`, credentials from
`DATABASE_URL`; never read `.env*`). Then, from the repo root:

    python scripts/catalogues/crawl_duragreen_site.py --refresh --refresh-pdfs   # website + every sheet (slow; waits out refusals)
    python scripts/catalogues/build_duragreen_catalogue_db.py --from-crawl       # -> duragreen_catalogue_site.sqlite + .xlsx
    python scripts/catalogues/read_duragreen_general_catalogue.py               # -> duragreen_general_catalogue_2026.json
    python scripts/catalogues/read_duragreen_track_sheets.py                    # -> duragreen_tracks_2026.json
    python scripts/backfills/backfill_duragreen_npd.py --dry-run                # read it, then without --dry-run
    python scripts/backfills/backfill_duragreen_code_rules.py --dry-run         # read it, then without --dry-run
    python scripts/backfills/backfill_duragreen_tracks.py --dry-run             # read it, then without --dry-run
    python scripts/maintenance/audit_duragreen_codes.py --compare HEAD          # the evidence

The three backfills run in that order: npd rewrites each product's sheet codes (keeping codes a
person entered), code_rules rebuilds the catalogue's rules, templates and page codes, tracks the
track systems. None touches a price, an approval or a BOQ line, and each says so in its output.
**Never on the Oracle VM without the owner's explicit yes - and a separate yes for the data.**

## Checks before calling a change done

- `audit_duragreen_codes.py`: **0 round trip failures** (a sibling's row the product's name rules
  out is counted apart: "the row is another version's"); every code that changed against `HEAD`
  explained (a range now right, a source now disagreeing, a printed code now found ...).
- API: open a product, `/combinations`, the tester (`/api/npd/duragreen/code`), add and delete a
  code, a BOQ description (`/api/specsheets/<id>/suggest-description`) - with a temporary user, then
  every row removed by id (user, codes, activity rows).
- Browser: NPD (DuraGreen) product screen - the rule line, the printed table and its Try buttons,
  the printed-combination picker, every-combination filters, record + add + remove a code; no
  console errors.

## Known traps (each has bitten once)

- **One file name, several products' sheets** (`68-SPEC-2.pdf` x3, `AFL78.pdf` x2): the crawl
  keeps each upload apart (`pdf_files` in `crawl.json`); never look a sheet up by name alone.
- **Numbered siblings** (Sparkle `35-1` ... `35-8`): one sheet prints all five patterns; each
  product keeps its own (`split_shared_sheet`), and links to the page that prints a sibling with
  the example adapted to its own number (`sibling_number` in the code-rule backfill).
- **`-24`** at the end of an outdoor code is the 24V DC version, printed beside the mains one
  (FOCUS 70: `UL7007W-27N15` and `UL7008W-27N15-24`). Where the sheet prints both and states a
  24VDC version, the product gets a **Voltage** choice (`name_voltages` in the sheet reader); the
  voltage picks which rows the code comes from, and a wattage printed only for the other voltage is
  unconfirmed - the two versions are different fittings.
- **`H30`**: the High Efficiency range prints its CCT with an H; never swapped for a table's `30`.
- **`?30` in a row's configuration**: the sheet's table does not say which CCT; the code does.
- **Neon strips** print one row per CCT with identical table values; the CCT is named from the
  code's last piece only when every piece names exactly one of the product's CCTs.
- **A strip's control is its driver's** (owner, 22 Oct 2026: "control of strips is the control of
  driver which we give along with it"). For a product typed Flexible Strip Light whose codes carry
  no control letter, the control choice stays on the line (it picks the driver) but is not part of
  the code (`control_is_drivers`). EVOLINE's 48V Light Stripe is typed as a strip but its code has
  `N`, so it keeps control in its code. The GS2835-120 strips print no CCT in their codes at all -
  refused until DuraGreen says how a CCT is ordered.
- **A catalogue example can leave out a piece** its own legend lists (p.210 drops `-TL`): such an
  example is never used as a pattern on its own.
- **Labelled tables are versions**: "Recessed Trim" / "Recessed Trimless" / "Surface" over a
  sheet's tables (68 Glass Pendant: RTPGE / TLPGE / STPGE). Until 22 Oct the label ended the table
  and the sheet gave no codes. Now each row keeps its label, and a product whose every row is
  labelled (two or more labels) is quoted on **Mounting** (`name_mountings`, `SELECTORS`).
- **A product not on the website** (uploaded by a person, or dropped from the site) is read from
  the sheet it holds, by the importer's `build` + `apply` (`own_sheet` in the npd backfill) - the
  Alabaster path lights' `ALP6003W-AG.pdf` had never been read.

## Pricing (tentative - owner to confirm when DuraGreen's price list arrives)

Prices may be keyed by code. Until the owner says otherwise the model stays base price + adders
per option (`npd.priced_by_rule`), entered in NPD; a code load never touches a price.
