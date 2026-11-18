---
name: vendor-products
description: Bring a manufacturer's (vendor's) products into NPD with their own order codes, for any brand - fetching the website, spec sheets and catalogue, reading the codes and the rule that builds them, structuring them in NPD, verifying, and loading locally. DuraGreen is the worked example. Use when asked to add, fetch, scrape, re-scrape, import or update a brand's products, spec sheets, catalogue, order codes or nomenclature, or to plan a new brand's NPD.
---

# Bringing a vendor's products into NPD

A vendor product's truth is the vendor's own documents: its website, its spec sheets and its
catalogue. The app reads them; it never invents. Prices may later be keyed by the code (base
price + adders stays the model until the owner confirms otherwise), so a wrong code is a wrong
price as well as a wrong fitting. That is why the target is zero wrong codes, not "most".

For DuraGreen specifically, also load the `duragreen-codes` skill.

## Rules that do not bend

1. **Never invent a code.** Every code shown is either printed by the vendor for this product, or
   built from pieces whose source is named. A guess is labelled as one, all the way onto the BOQ line.
2. **A vendor's codes live in its own tables** (`vendor_order_codes`, `vendor_code_parts`,
   `vendor_code_rules`), never in the studio's (`CodeScheme`, `FieldCode`, `CodeAttribute`). Vendors
   reuse letters differently per range (DuraGreen: Polished Chrome is `PC` in ACME, `L` in MOOD).
3. **How sure a piece is** - the code is only as sure as its least sure piece:
   - printed whole for exactly this configuration (sheet or catalogue page, or typed in by a person
     from the vendor's answer);
   - built from pieces printed *for this product* (its own codes, its sheet's legend, its own
     catalogue page);
   - built with *its own range's* published code table;
   - anything else - another range's table, another product's page, a pattern - is **unconfirmed**:
     shown with its source, never written plain.
4. **A range's code table is that range's rule only.** DuraGreen's ACME table is the ACME
   downlights' rule, not a step light's, a pendant's or an outdoor uplight's. A product outside a
   range takes its rule from its own sheet's printed codes and its own catalogue page.
5. **When two sources disagree, there is no code** - say so and name both (ACME writes Mains Dim
   as `P`, MOOD as `M`; nothing says which a shelf light follows). Never pick the first one found.
6. **A printed row is evidence only for what it states.** A row that does not say which CCT or
   driver it is for cannot answer for a line that chose one.
7. **People choose from what is printed.** NPD shows, per product, every combination the sheet
   prints (one column per option: W, CCT, beam, driver ...), lets a person pick a printed
   combination as one choice, shows every other combination with its code and how sure it is, and
   lets a person add a code the vendor sends *with every option it orders*. A built code is never
   pre-filled as the vendor's: a typed-in code is used as the vendor's own from then on.
8. **Data is never lost.** Back up the local database before a load; every backfill has `--dry-run`
   (run it first and read it); prices are never touched by a code load; rows a person entered
   ("entered by ...") are never deleted by a re-read; BOQ lines are never changed.

## Steps

1. **Fetch, completely and politely.**
   - Walk the vendor's sitemap, not only its menus (DuraGreen's menus showed 69 families; its
     sitemap 324 pages). One page at a time, a pause between pages, robots.txt respected, and wait
     out refusals rather than fail (DuraGreen refuses for about a minute after a burst).
   - Save a snapshot file of what each page said, and download every spec sheet.
   - **Give every upload its own local file.** Vendors reuse file names across *different*
     products (DuraGreen's `68-SPEC-2.pdf` is three products' sheets). Saved by name alone, only
     the first is kept and the others' products are read from the wrong sheet.
   - A re-scrape downloads sheets again and compares bytes: a re-issued sheet is kept beside the
     old one (moved aside, never overwritten) and listed, so its codes can be compared.
2. **Read every product's codes with the vector they are printed for** - the row's wattage, CCT,
   beam, driver, finish, length. Cut a code into pieces only when the pieces join back into the
   code exactly and agree with the row (the beam digits are the row's optics); otherwise keep it
   whole. A code kept whole is still usable for exactly its row.
3. **Find the rule that builds codes the sheet does not print**: the catalogue's code tables, *which
   pages each one governs*, and each product's own catalogue page (its decoded example). Record
   page numbers in one numbering (PDF page vs printed folio - mixing them mis-filed 58 DuraGreen
   products).
   - **Search every document the vendor publishes for code tables, not just the catalogue**: the
     site's downloads page (price lists, brochures, last year's catalogue) and every spec sheet.
     DuraGreen's only EVOLINE table is in its ACME *price list*; 35 of its spec sheets print an
     older ACME edition. Search normalised text (`ﬁ` ligatures hid MOOD's "Speciﬁcation Code") and by
     shape (a page of `code/meaning` pairs under Driver / Beam Width headings), and render the pages
     around a range's start to check for a table printed as a picture or sideways.
   - **Editions differ.** Read each into data (`read_duragreen_code_matrices.py`) and compare: the
     same letters can mean different things (DuraGreen's `AB`: Antique Bronze until 2025, Antique
     Brass in 2026). The newest edition of a range's own table decides; earlier ones fill only a
     meaning it does not list, and only where they agree.
4. **Look for the patterns that break naive reading** before trusting a count:
   sibling products sharing one sheet (each keeps only its own codes); a suffix that is a voltage
   (`-24`), not a version; a prefix that changes a meaning (`H30`); a catalogue example that leaves
   out a piece its legend lists; rows that look identical in the table but differ in the code (the
   difference is an unstated option - name it only if it maps one-to-one onto the product's own
   options); the website renaming families.
5. **Structure in NPD.** A product found on the website arrives `pending`; a person releases it.
   Options are the product's own words. Codes, parts and rules go to the vendor tables with their
   source on every row.
6. **Verify with evidence**, every time:
   - round trip: every printed code, asked for with its own configuration, comes back;
   - every combination of every product: how many printed / built / unconfirmed / none, and why;
   - against the previous engine (or data): every code that changed has a reason;
   - spot-check against the PDFs themselves;
   - API and browser checks against a local server, then remove every test row by id.
7. **Load locally**, in order, dry run first, then apply; record what changed (counts, CSVs).
   Never on the Oracle VM without the owner's explicit yes for that change - and a separate yes
   for a live data load.
8. **Write it down**: the TUTORIAL lesson and changelog, PROJECT_STATE, the memory file, and the
   open questions for the vendor.

## Where the DuraGreen version of each step is

| Step | DuraGreen |
|---|---|
| Fetch | `scripts/catalogues/crawl_duragreen_site.py` (`--refresh --refresh-pdfs` for a full re-scrape) |
| Read sheets | `app/duragreen_sheet.py` (shared with the website importer), `scripts/catalogues/build_duragreen_catalogue_db.py --from-crawl` |
| Catalogue rule | `scripts/catalogues/read_duragreen_general_catalogue.py`, `scripts/catalogues/read_duragreen_track_sheets.py` |
| Load | `scripts/backfills/backfill_duragreen_npd.py`, then `..._code_rules.py`, then `..._tracks.py` |
| Build a code | `app/duragreen.py` (`order_code`, `scheme_of`, `combinations`) |
| Verify | `scripts/maintenance/audit_duragreen_codes.py` (`--compare HEAD`, `--product ID`) |
| NPD screen | `static/app.js` `dgRenderProduct`; endpoints `/api/npd/duragreen/*` in `app/main.py` |

A new brand copies this shape: its own reader module, its own code module in `app/`, the same
vendor tables, the same certainty ladder, the same checks.
