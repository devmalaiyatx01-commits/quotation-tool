# Quotation Tool (V1)

> Just want to run it? See **[HOW_TO_RUN.md](HOW_TO_RUN.md)** — double-click `run.bat`.

A tool for building lighting Bills of Quantities *and* priced Quotations from
a library of product specsheets. Upload manufacturer PDF datasheets once,
then assemble a BOQ by floor / area / room, picking products (and their
finish / CCT / beam / wattage variants) or adding accessory & driver lines,
and export either a plain BOQ or a priced Quotation - each as `.xlsx` or PDF -
in the studio's own document formats.

## What it does

- **Accounts & access** - every page and API route requires signing in
  (`/login`). Two roles: `admin` can open the Nomenclature tab (product codes,
  which affect every BOQ built afterward); `member` can do everything else
  - build/edit/export BOQs, use the Configurator, upload specsheets. See
  `deployment.md` section 3.3 for the reasoning and how this fits into a
  real deployment.
  - **Google Sign-In** (once configured - see `GoogleAuthSetup.md`) is the
    normal way in: the login page shows a single "Sign in with Google"
    button, and only `@example.com` Google accounts are accepted -
    checked in application code (`app/google_auth.py`), not left to Google
    Cloud Console settings alone. The **first** time a given
    `@example.com` address signs in, an account is created for it
    automatically (as `member`) - no admin has to provision it first, which
    is the actual point of switching to this over passwords. Promote
    someone to `admin` afterward with `python create_user.py` (same email,
    any password - the password field is unused once Google Sign-In is
    active, only `role` matters).
  - **Password login** (`python create_user.py` - prompts for email/
    password/role, safe to re-run to reset a password or change a role)
    still works, for `@example.com` addresses only, as a **fallback for
    as long as Google Sign-In isn't configured** (no
    `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` set) - so
    setting up Google Sign-In is never a way to accidentally lock yourself
    out mid-setup. Once those two values are set, password login is fully
    disabled (not just hidden - a direct `POST /login` is rejected too),
    matching the point of restricting sign-in to `@example.com` in the
    first place.
- **Database** - SQLite by default (`data/app.db`, WAL mode - no setup
  needed, works out of the box). Set the `DATABASE_URL` environment
  variable to a Postgres DSN (`postgresql+psycopg2://user:pass@host/dbname`)
  to run against Postgres instead - no code change needed, this app has no
  raw SQL or SQLite-specific queries. Already have real data in
  `data/app.db`? Run `python scripts/migrations/migrate_to_postgres.py` after creating the
  Postgres database to copy every specsheet/BOQ/rule across (preserving
  IDs) before switching `DATABASE_URL` over - see `deployment.md`
  section 3.2.
- **Phones, tablets and narrow windows** (2026-10-28, branch `Scr`) - every screen fits the
  device it is opened on, with no sideways scrolling and nothing shrunk: the same tabs, buttons
  and steps as on a desktop, arranged to fit. Below 1100 px the tabs fold behind one button in
  the top bar that names the tab you are on. In the BOQ editor on a phone, each line shows its
  product, its finish / wattage / beam / CCT, its quantity and its price at a glance; the
  outline (Find & Jump) slides up from a button kept at the bottom of the screen; the BOQ list
  shows each BOQ as a card; pop-up menus (Export, Copy / Move, ...) rise from the bottom edge;
  dialogs take the whole screen. The project details fold into one line on a phone or tablet,
  a quick BOQ's products are cards, and NPD's option prices fit. On a tablet a BOQ line takes two
  rows. A phone held sideways is treated as a phone, and touch screens get finger-sized
  controls. The browser's Back (a phone's Back gesture) returns to the previous screen, or closes
  an open dialog or menu, instead of leaving the app. A desktop at 1366 px or wider is laid out
  exactly as before, except that the editor's preview eye is its designed tab again (it was a 9 px
  dot); between 1101 and 1340 px only the words beside the logo are hidden, so the tabs fit. The
  layout is the "Small screens" section at the end of `static/style.css`; see TUTORIAL.md 3.11.
- **Import a BOQ or quotation from Excel** (2026-10-28, branch `Inverse`) - for a BOQ that went out
  as an .xlsx and was worked on in Excel: **BOQs -> Import BOQ**, choose the file, check what was
  read, **Create BOQ**. It reads the app's own BOQ and quotation layouts and ones close to them
  (headings by meaning - "Qty"/"Quantity", "Make"/"Brand" - floors and areas from banners, an
  AREA or FLOOR column, or sheet names; accessory blocks; prices and discounts; the cover's project
  details and the NOTE page's wording). Each line is linked to its NPD product where the name (or
  manufacturer's code) matches, marked "to check" where it nearly does, and otherwise kept as a
  typed row with its details; any line can be changed before creating. A line with no price of
  its own is priced from NPD as the Configurator would. Nothing is saved until Create, and it
  always makes a **new** BOQ - an import never changes one that exists. Rows and sheets left out
  are listed with the reason. `app/boq_import.py`; see TUTORIAL.md 8.2.7.
  **With the free AI model switched on** (branch `FreeLLM`; HOW_TO_RUN.md "Optional: a free AI
  model"), the window also takes PDFs and CSVs, and "Let the AI model read what the app can't"
  sends a file the rules cannot read to a free OpenRouter model, which returns its lines as JSON.
  A line whose description is not in the file is dropped, and every line it read is "to check".
  For a line the rules could not link, the model may pick one product from a shortlist of NPD
  products and nothing else, also "to check". Any failure, a used-up daily allowance included,
  falls back to the rules with the reason on screen. `app/boq_ai.py`; see TUTORIAL.md 15.11.
- **Ask** (always shown; searches by specification need no AI model) - a question about the products,
  and the BOQs for someone who may see them ("Which 12W 3000K downlights are IP44?", "What did we
  quote NYX -Midi at, and on which BOQs?"), answered from the app's own records. The server finds
  the records the question is about by its words (free, no package, no embeddings), and the model
  answers from those alone, naming the ones it used; each is a button that opens it. A source it
  was not given is dropped and a price no record holds is pointed out. Counts come from the
  database ("used in 21 BOQs"), not from the model. One request per question, at most half of
  the day's allowance (the rest stays for reading files); a question nothing matches costs
  nothing. Changes nothing; each question is in the activity log. `app/assistant.py`; see
  TUTORIAL.md 15.12.
- **Specsheet Library** - upload PDF datasheets (like the ones in
  `SampleInputs/SampleSpecsheets`). **Every page is parsed as its own
  potential product**, not just the first - a single-page datasheet yields
  one specsheet as you'd expect, but a manufacturer catalog PDF (one page
  per size/trim variant of a product line - e.g. "NYX -Mini With Trim",
  "NYX -Mini Trimless", "NYX -Midi With Trim", ...) yields one correctly
  classified specsheet *per page*, each with its own diagrams, instead of
  one entry that only captured the first page and mixed every page's images
  together. Each specsheet record has: category (that page's own
  product-type description, e.g. "Recessed LED Downlight –Adjustable –With
  Trim"), product name, *every* diagram on that page (product render,
  dimensioned drawing, polar/cone diagram, etc.), and every parameter
  grouped by section (Mechanical/Optical/Source/Power Supply...), with any
  slash-separated options (e.g. `15°/ 24°/ 36°`) captured as pick-list
  options. The specsheet detail view also embeds the original PDF, jumped
  to that product's own page for a multi-page catalog, plus an "open in new
  tab" link. **Uploading a page whose content exactly matches one already in
  the library - including re-uploading the very same file, whether under its
  original filename or a renamed copy - never creates a duplicate entry**,
  for every typology alike. Checked two independent ways, either of which is
  enough to skip it: the page's own extracted text, and separately its
  complete set of parsed parameters (classification plus every field/param/
  option value) - so it also catches two pages whose raw text differs in
  some inconsequential way (a different rescan/export of the same PDF) but
  whose every actual parameter is identical, not just byte-for-byte-identical
  text. Both checks are exact, never fuzzy/"close enough" - see Known V1
  Limitations. A duplicate filename is still saved under a suffixed name
  (`name__2.pdf`) rather than overwriting whatever's already at that path -
  and cleaned up again afterward if every page in it turned out to be a
  duplicate, so nothing redundant piles up in `data/uploads`.
- **Compare Specsheets** - from the Library tab, pick any two specsheets
  (search by name/series/category) to see a full side-by-side table of
  every parameter - typology/series/family/variant/category/make plus
  every parsed field - with the ones that actually differ highlighted.
  Useful both for specsheet analysis (what really distinguishes two
  similar-looking catalog pages, e.g. a lens-cover vs. diffuser-cover
  option of the same strip light) and for confirming two entries really
  are exact duplicates before deleting one.
- **Classification hierarchy** - five dimensions, each answering a different
  question:
  - **Typology** - business category (e.g. "Downlight") - what configuration
    rules and the Configurator are scoped to. Auto-assigned at upload from a
    keyword match against the parsed category text (`app/typology_classifier.py`,
    e.g. "downlight" -> `Downlight`) - confidently, or not at all (left
    "Unclassified" for one manual assignment) - since a wrong guess would
    silently apply the wrong typology's rules to a real BOQ item.
  - **Series** - the product line (e.g. "NYX", "OZONE") - shared across all
    its sizes.
  - **Family** - the size within a series (e.g. "Mini", "Midi", "Maxi") -
    auto-split from the product name against a known size-word vocabulary,
    so a genuinely standalone name like "GLANCE 1000" isn't mis-split just
    because it contains a hyphen.
  - **Variant** - distinguishes otherwise-identical catalog pages (e.g. "With
    Trim" vs "Trimless").
  - **Environment** - Interior and/or Exterior, **not mutually exclusive** -
    some products are genuinely rated/sold for both, so this is stored as a
    small set rather than a single value (`app/environment_classifier.py`).
    Auto-guessed the same "leave it blank rather than guess" way as Typology,
    but from a much narrower signal: only when the category text explicitly
    says so ("underwater", "inground", "outdoor", "exterior", "interior").
    Most Interior-context products (a plain downlight, a track light) never
    say "interior" in their own PDF text at all, so this stays unclassified
    far more often than Typology does - expect to tag most of it by hand.

  None of these are reliably auto-derivable with full confidence, so all
  five are editable from each specsheet's detail view in the Library tab -
  but **locked (read-only) by default**, since they drive which configuration
  rules and Configurator matches apply to every BOQ item built from this
  specsheet - an accidental edit here is riskier than most fields. Click
  **"Use with Caution: Edit"** to unlock them for a genuine correction; the
  same lock/unlock pattern already used for BOQ item rows.
  **Family and Variant only appear there when they're actually relevant**:
  a single-datasheet product line with no size/trim variants (a strip light,
  a neon band) has neither, so the detail view hides whichever of the two
  no product under that same typology ever uses (never hides a field that
  already has a value, or when the specsheet has no typology yet to judge
  relevance by), instead of showing an always-empty input with a "Mini,
  Midi, Maxi" example that doesn't apply to that kind of product. The
  Library, the Direct product picker, and the Configurator (which gets
  Typology -> Series -> Family drill-down dropdowns before the spec fields)
  all group by series, so a large catalog stays navigable instead of one
  giant flat list.
- **Browsing the Library** - a classification summary at the top of the
  Library tab shows the total specsheet count and, as clickable chips, how
  many specsheets exist under each Typology (click one to filter the grid
  to it) - a growing **Unclassified** chip is called out on its own too,
  since that's exactly the kind of library gap worth surfacing rather than
  silently folding into "All." Series and Family filters cascade below it
  (each one's options - with their own counts - scoped to whichever
  Typology/Series is currently selected, the same drill-down the
  Configurator already uses), so narrowing from "every specsheet" down to
  "just the NYX Square family within Downlight" is a few clicks, not a
  search string. A second row of chips filters by **Environment**
  (Interior/Exterior/Unclassified) the same way, except "Interior" means
  "includes Interior" rather than an exact match, since a specsheet can be
  tagged both. Every card also shows its key specs (CCT, Wattage, Beam,
  IP Rating, Control, Finish, Mounting - whichever the source PDF actually
  had) as small tags directly on the card, so browsing to compare products
  doesn't require opening Details first just to see what a product's specs
  even are.
- **BOQs** - create a BOQ (project, area/scope, client, date, and an
  optional **Kind Attention** - the specific contact person at the client,
  distinct from the client/project name itself, shown on the NOTE page and
  every floor sheet's header block. Matches the sample template exactly:
  genuinely optional, left blank shows just the label with nothing after
  it, same as the sample's own saved BOQ does). Floors and rooms/areas are
  free text you choose per-project (there's no fixed "Ground Floor / First
  Floor" list baked in, since that's specific to each BOQ) - previously
  used names show up as suggestions so wording stays consistent across
  projects. Every field in the editor - project details, floor/area names,
  item rows - **autosaves on blur**, no separate "Save" step anywhere in
  the BOQ editor; adding, reordering, duplicating, and deleting rows only
  ever re-renders the one area you changed, not the whole BOQ, so those
  stay quick even on a large BOQ. Buttons that delete something say what
  they'll take with them (hover for the full explanation on any button);
  a button is disabled for the moment its own request is in flight so a
  slow connection reads as "working" rather than "did that click land."
- **Floor vs. Area/Room, made explicit** - a Floor and an Area/Room can look
  similar at a glance (both are just a named card you type into), which is
  an easy thing to mix up on a first BOQ. Each card now carries a small
  **FLOOR**/**AREA** tag, floors are visually nested (a left accent bar,
  indented body) so a room reads as *inside* its floor rather than a peer
  of it, and both are collapsible - click the ▾/▸ to fold a section down to
  its name and a count badge, which also makes a large BOQ far easier to
  scan. A **Structure** panel at the top of the editor lists every
  Floor → Area as clickable chips - click one to scroll straight to it
  (expanding it first if it was collapsed), a quick map of the whole BOQ
  without hunting through the page. All of this is local UI state built
  from data already loaded in the browser - no extra network round trips,
  so it doesn't cost anything in latency.
- **Duplicate a floor, copy or move an area** - a tick box at the head of
  every Floor and Area header, the same one rows already have. Tick a floor
  and **Duplicate floor** makes a second copy of the whole thing - every
  area on it, every row in every area, accessories still grouped under the
  products they belong to - placed directly below the original and named
  "&lt;name&gt; (copy)", ready to be edited into the variant you actually
  wanted, with the cursor already in its name field so you can type the
  real name straight away. Tick one or more areas and **Copy to...** or
  **Move to...** sends them whole to any other floor in the same BOQ: the
  area's name, its area remark and all its rows travel together, a move
  leaves the floor it was on, and a copy leaves the original exactly where
  it was. A copy landing back on its own floor is named
  "&lt;name&gt; (copy)" so the two can be told apart; landing on a
  different floor it keeps its name as it was. The action bar only appears
  once something is ticked, so the editor carries no extra buttons the rest
  of the time, and it is **pinned to the bottom of the window** while it is
  there - so a floor ticked at the very end of a long BOQ is acted on where
  you are standing, with no scrolling back to find the buttons. Copy and
  Move are each one click, not a menu to open first. Ticking floors and
  areas at the same time is fine - each gets its own button, and nothing
  you ticked is silently un-ticked. Everything a copy touches is recorded
  in the activity log, same as adding the rows by hand would be.
  **Whatever you tick also turns blue in the Find & Jump panel on the
  left**, which is the only place the whole BOQ is visible at once - so on
  a project with a dozen floors you can read off everything you are about
  to copy without scrolling the document hunting for ticks, and click any
  of them to jump straight there. Ticking something that has scrolled out
  of view in that panel scrolls it back into view.
- **Edit Notes** - the BOQ editor's "Edit Notes" button opens the full
  NOTE-page text (terms of sale, warranty, payment terms, and the closing
  signature) as a single editable block, defaulting to the studio's
  standard terms the first time you open it. A blank line starts a new
  paragraph; wrap text in `**like this**` to bold it - inline, mid-sentence,
  or a whole line. The "From:" company letterhead and the "To:"/"Kind
  Attention:" lines stay fixed and aren't part of this text. "Reset to
  Default" refills the textarea with the standard terms so you can edit
  from a clean copy.
- **Adding items** - inside a room, add a product three ways, in this order
  (since 2026-10-21):
  - **Frequently used** - the window opens here: the products this team has
    quoted most (in the most BOQs, then on the most lines), as tiles grouped
    by typology, with Brand and Typology filters. One click picks a product.
    Only approved products count, the same as everywhere else.
  - **Configure** - a product appears under its typology and under the kind its
    maker files it in (since 2026-11-05), so a DuraGreen wall-wash downlight is
    found under Downlight and under Wall Grazer. **Search** also reads a product's
    specifications and everything it offers (IP44, 3000K, DALI), with what is
    named listed first. Pick a typology first, then narrow by the
    specs you actually need (e.g. "IP66, DALI") - only products in that
    typology that genuinely have that combination show up to pick from. Your
    classification choices carry over as the starting selection once you
    pick one. This is the better path once a typology has more than one
    product in the library, since you can search by requirement instead of
    needing to already know a product's name. The typology list includes an
    **"Unclassified"** option too (since 2026-08-07) - products that haven't
    been assigned a typology yet in the Library are still fully reachable
    here, not just via Search. Since 2026-10-21 every value it offers is the
    product's own, as NPD holds it: its list of choices, or the one value it
    states (a InHouse downlight's single CRI, "> 90 (R9 > 90)", is a CRI
    choice here and reaches the BOQ row). A field no product in the selection
    has - Length on a downlight - is not asked at all, and only approved
    products of the tool you are in are offered, as in Search.
    **Which details it asks is decided in NPD** (2026-10-21): the
    **Configurator questions** tile on NPD (and the button of that name on
    NPD - DuraGreen) opens, per typology, the list of details shown, in
    order, each renamable, with the details not shown under it and a live
    preview of this tab beside it. A detail some product of the typology is
    sold in more than one of is always asked - it is locked, and says why
    (a BOQ line has to say which; it may build the code or change the
    price) - so what is decided is the order, the names, and which fixed
    details (one value per product: a CRI, an IP rating, a UGR) are shown.
    The product form follows the same list. A typology nobody has set keeps
    the automatic list; **Back to automatic** returns to it. Saving needs
    "Create and edit products" and is in the activity log.
  - **Search** - search/browse the whole library by name, series or
    category, then pick its finish/CCT/beam/wattage/IP rating/control
    variant.

  Either way, you land on the same finalize step: review the
  auto-suggested description built from the specsheet, edit it, set
  qty/unit/S.No. A live **Generated Product Code** (e.g. `LL-WHT-30`, built
  from whichever Finish/CCT/Beam/etc. short-codes are defined for the chosen
  values - see Product Codes below) is shown alongside as a reference/copy
  target only - it never overwrites S.No, which stays this studio's own
  project-specific numbering (e.g. "CRA2.2") typed in by hand. There's also
  a blank row for anything that isn't a specsheet product, and an
  **Accessory Row**.
  - **Add an associated accessory at the same time** (since 2026-08-07) -
    if the product being added has any accessories parsed from its *own*
    specsheet (e.g. a strip light's "Accessories: HONEYCOMB / SPREAD LENS")
    a checkbox appears below Description: "Also add an accessory with this
    product." Only that product's own accessory types are offered - not
    the whole library's (that's what the standalone "+ Accessory Row"
    Configurator is for) - `GET /api/specsheets/{id}/accessories`. If the
    specsheet also has a dimension-type field with real options (e.g. a
    strip's Profile Dimension), a picker for that appears too, folded into
    the accessory's description alongside the type. Confirming creates the
    accessory as its **own separate row** right after the product - never
    merged into the product's own row/fields - exactly as if "+ Accessory
    Row" had been used afterward, so it groups under the same
    "ACCESSORIES" export label as any other accessory row.
- **Add Accessory** (since 2026-08-06) - picking "+ Accessory Row" opens a
  small Configurator rather than dropping in a blank row: a searchable
  list of real accessory types, sourced by flattening and deduping every
  specsheet's own parsed `fields.accessories` value across the whole
  library (e.g. a downlight's "Accessories: HONEYCOMB / SPREAD LENS /
  SOFTENING LENS" row) - `GET /api/accessory-types`. This grows on its own
  as more specsheets get uploaded, nothing to maintain by hand. Clicking a
  type just fills the Description field - it's never a dead end if what
  you need isn't listed, since Description stays freely editable either
  way. Deliberately doesn't try to merge near-duplicate spellings that
  differ only by a missing space (a real artifact of how some source PDFs
  extract text) - occasional near-duplicates in the list are a minor
  annoyance; silently merging two possibly-different values is the kind of
  shortcut that has corrupted real data in this app before (see
  `TUTORIAL.md`'s `_undouble()` case study).
- **Add Driver** - a dedicated mini-configurator for driver rows, separate
  from the main product Configurator and, since 2026-08-06, **not backed by
  the Specsheet library at all** - drivers are treated as a fixed set of
  valid electrical-spec combinations, not individual SKUs to browse. Four
  fields, each depending on the last: **Voltage Type** (Constant Voltage /
  Constant Current - descriptive only, doesn't restrict anything after it),
  **Control Type** (Non Dim / Phase Cut / 0-10V / DALI), **IP** (IP Rated /
  Non IP Rated - Phase Cut and 0-10V only ever offer Non IP Rated), and
  **Wattage** (the valid list depends on Control Type + IP - see
  `DriverRules.md` for the full table). No brand, product name, or specsheet
  link is shown or stored (`specsheet_id` stays null) - just "Driver" plus
  those four choices, folded into an editable auto-suggested description.
  A driver row is still an accessory row underneath (`is_accessory=True`),
  so it groups into the same **ACCESSORIES** block as any other accessory
  added alongside it on export - see Export below. Drivers are deliberately
  **not** part of the general specsheet catalogue (`GET /api/specsheets`
  excludes typology `Driver`) - they don't show up in the Library grid, the
  Direct picker, the main Configurator's typology list, or Compare
  Specsheets. The ~95 driver SKUs originally imported by `scripts/catalogues/import_drivers.py`
  from `SampleInputs/DRIVERS.pdf` still exist in the database untouched, but
  the Add Driver flow no longer reads from them.
- **Requirement Match** (remade 2026-09-18, line items 2026-09-21) - import a client's requirement schedule as a
  spreadsheet (a downloadable template, with the catalogue's typologies in a dropdown and a
  prompt for converting a PDF with an AI assistant) and every row gets the closest Library
  products, then add the picks to a new quick BOQ or an existing one in one step. Every row
  starts picked with its closest match; one Pick / ✓ Picked button per product picks another or
  removes the pick, and each row says its state in words (Picked, No match, Needs typology).
  Hard rules,
  never relaxed: same typology, same shape when stated, and every stated value within a
  per-parameter tolerance the user sets (globally or per row) - a 10W product is never offered
  for a 2W requirement. A product that does not state a value is never suggested, only listed
  apart. Matching runs in the browser over a catalogue index the server builds
  (`app/match_index.py`), so moving a tolerance slider re-matches every row at once; the
  spreadsheet reader is `app/requirement_excel.py`. PDFs and pasted text still work.
  Products defined in NPD are added at their NPD price (`npd.match_price`, the same
  `price_for()` the Configurator's price mirrors); an option the requirement leaves open that
  changes the price is shown as a choice in the Add dialog, never picked silently.
  Each row is read in three named bands - **Required** (what the sheet asks), **Suggested**
  (what the app offers) and **Given** (the line items the row will put in the BOQ) - so a line
  item is never mistaken for a suggestion. Given is pinned in view and adds that one row on its
  own. A row contributes up to three lines: the product, the **driver** that runs it, and an
  **accessory or profile** from that product's own datasheet; the driver and accessories are
  written under the product (`parent_item_id`) exactly as the BOQ's own Add Driver / Add
  Accessory windows place them. **Every line item in the client's schedule is kept**, because a
  quotation cannot answer fewer lines than were asked for. A driver or power supply is answered
  from the driver catalogue (`driver_prices`, edited in NPD -> Classification - see
  `DriverRules.md`), started on the nearest combination that carries the load the line states; a
  connector, end cap or profile becomes its own line in the sheet's own words. Both go in keeping
  their own Ref. An accessory row is not filled in for you: it offers the accessory catalogue,
  narrowed to the accessories this line's own words point at, and naming the product it usually
  goes with is what gives the line that product's own price and order code - the product need not
  be in the BOQ at all. A fixture row's **+ Accessory / profile** offers both that product's own
  datasheet and the whole catalogue. Only notes, totals and headings are left out. The template's Typology column -
  and the app's own first field - offer **Driver** and **Accessory** above the catalogue's
  typologies, so a line an assistant could not classify is kept with that cell empty and named in
  one click. Where the sheet numbers its rows, the import reports any number missing from that
  run: on one real 26-item schedule an assistant had silently returned 15 rows, dropping nine
  drivers and two connectors. Drivers are priced from the driver price list and accessories from
  the product's own accessory prices.
  Each row shows the client's own picture for it: import the client's PDF together with the
  sheet and every row's picture is cut from its Source page, found by its Ref, with no AI
  involved (`app/requirement_pictures.py`); pictures pasted into the sheet's rows are used too.
  The PDF is kept under a random token for 30 days for this, then removed.
  With the free AI model switched on (branch `FreeLLM`), "Use AI to read what the rules can't"
  sends a sheet or PDF the rules refuse to the model instead; each row it reads says "Read by
  the AI model - check it.", and the matching itself stays the hard rules above.
  A client PDF may be up to 150 MB (it was 40 MB): the file is copied to disk and read a page at
  a time. Above 40 MB, each row's picture is cut only when the PDF's pictures are modest (32 MB
  once opened); otherwise the requirements are read and matched as usual and the page says why
  there are no pictures. See TUTORIAL.md 9.4.
- **Accessory catalogue** (2026-09-21) - every accessory the library knows and the products each
  one goes with, in **NPD -> Accessories** and on each product's own page in the Specsheet
  Library. "Honeycomb" is listed by 325 products across three brands; until now the only way to
  learn that was to open 325 specsheets. It is *derived* from the specsheets themselves on
  request (`app/accessories.py`, `GET /api/accessories`) rather than kept as a second table, so
  it can never disagree with them: uploading a specsheet adds its accessories, correcting one
  corrects them. Spellings that differ only in case or spacing (HONEYCOMB / Honeycomb) are
  counted as one accessory while each product keeps the words its own datasheet printed. A price
  belongs to the *pair* - this accessory under this product - because the same part costs
  different amounts under different fixtures. The panel also reports what is untidy (one
  accessory spelled several ways, text the parser took for a name, accessory prices attached to
  no product and therefore unreachable), and lists pairs that may be the same part spelled with a
  different word for a person to judge - "Solite lens" against "Solite Lense" reads exactly like
  "Beam Widening Lens (+10°)" against "(+20°)", and only someone who knows the products can tell
  those two cases apart.
  **Renaming an accessory writes back into the specsheets**, which is the point: the catalogue is
  only a reading of them, so making it consistent means making *them* consistent. One box does
  both jobs - a name that belongs to another accessory merges the two - and it moves all three
  records together (the products that list it, its `PriceEntry`, its order code), because those
  are keyed on the name and renaming one without the others would break the data rather than tidy
  it. `scripts/backfills/tidy_accessory_names.py` does the same for every accessory spelled more
  than one way, with a dry run first. Run on 2026-09-22: 157 product listings and 36 accessory
  prices settled on one spelling each (HONEYCOMB -> Honeycomb, EXTENSIONARM -> EXTENSION ARM,
  END CAP -> End cap...), with every product-accessory pair, price and order code verified
  unchanged in number.
- **Pricing & Quotation export** - every item has a **Price** field
  (defaults to 0, editable manually - Add Product/Add Driver both offer it
  up front, or edit it inline in the item table anytime; a price-list-
  driven default is future work, not V1), a **Model No.** field for the
  manufacturer's own SKU (distinct from S.No, which is this studio's own
  numbering), and a **Reference No.** field - a third, separate code from
  S.No, optional, shown as its own column on the BOQ export (toggleable
  like any other column) and as the Quotation export's "REF NO." column.
  S.No itself is what populates the Quotation's own "S. No." column too -
  earlier, S.No's value was landing under "REF NO." there instead with no
  way to fill in a real reference number at all, since the two concepts
  didn't have separate fields yet. Each BOQ also has a **Quotation pricing** strip in the
  meta panel: **Discount on products** (the standard discount, default 0), **Currency**
  (default INR), and **Show the discount on the quotation**. Since 2026-09-29 the standard
  discount goes on product lines only - accessories, drivers and power supplies are quoted at
  their price - and any line can carry a discount of its own (0 leaves a product out).
  Since 2026-10-01 that checkbox alone decides what the client sees: ticked, every line of the
  quotation (Excel and PDF, every layout) shows its Price, Disc. % and Discounted Price and the
  quotation states its standard discount - at 0% too; unticked, final prices only, with no
  discount column, row, rate, formula or note anywhere, cover and Summary included.
  Every row in the editor shows its **Price** and **Disc %** on the line with its finish,
  wattage, beam, CCT and quantity; clicking either opens the line's **Price & discount**
  window (live "Client pays" preview), whose **Save & next** (Enter) prices a whole BOQ from
  the keyboard. On a wide screen the editor uses the extra width, and each row fits on one line.
  Currency amounts use a plain-text prefix ("Rs.", "$", "EUR", "GBP"), not
  a Unicode currency symbol glyph (₹, €, £) - those aren't reliably
  supported by every font/viewer (Excel's number-format fonts, reportlab's
  base-14 PDF fonts), and a missing glyph renders as an opaque box instead
  of the amount. Four
  export buttons cover the two document types x two formats: **Export
  BOQ (Excel/PDF)** is the plain product-tracking export (unchanged);
  **Export Quotation (Excel/PDF)** generates a separate, pricing-facing
  document matching the studio's real quotation format
  (`SampleInputs/SampleQuotation`): the same Cover and terms page as the
  BOQ export, plus a **Summary** sheet with per-floor subtotals and a Grand
  Total, and item tables with Price/Discounted Price/Amount columns
  (accessory rows get their own Accessories Net Price/Amount of
  Accessories pair instead). The Quotation PDF is landscape, not portrait -
  its item table has 19 columns (REF NO./Model No./LED COLOR/AREA plus
  five pricing columns on top of the BOQ export's usual set), too many for
  portrait's ~174mm usable width. When products are on different discounts,
  a **Disc. %** column is added. See `app/quotation_export.py`'s module
  docstring for the discount rule (`line_discount`) and how a document decides
  what to show (`DiscountPlan`) - every priced export shares them, so no two
  formats can disagree on an amount.
- **Row protection** - every item row starts **locked** (read-only) so a
  stray click can't silently change crucial BOQ data. Clicking **Edit** on a
  locked row offers a choice rather than immediately unlocking raw text
  fields:
  - **Edit Manually** - the original behavior: unlock just this row's fields
    for direct typing. **Lock** locks it again.
  - **Use Configurator** - re-opens the same Add-Product modal used to add
    the item in the first place, seeded with this row's current specsheet's
    typology/series/family and its own finish/wattage/beam/control as the
    starting selection - so correcting or changing a product's configuration
    goes through the same config-rule-aware picker as adding one, instead of
    free-typing a combination that might not actually be valid for that
    product. Confirming updates this same row (including switching to a
    genuinely different matching product) rather than adding a new one.
    Only offered when the row has a specsheet linked to it (not a blank/
    accessory row, which has nothing to reconfigure).

  **Duplicate** clones a row (all fields, right after the original, same
  room) for quickly adding a near-identical item. **Copy to...** clones a
  row the same way but into **any area/room on any floor of the same BOQ**
  - pick the destination floor and room from a small picker; the copy is
  appended to the end of that room, and the original row is untouched. (Both
  are true copies, not moves - the original always stays exactly where it
  was.)
- **Area remarks** - each room/area has one whole-area **Remark** field
  (below its item table) for a note that applies to the area as a whole -
  distinct from the per-item Remarks column. Exported as a single
  full-width row beneath that area's items.
- **Configuration Rules** - a typology-scoped rules engine (ported from the
  studio's existing Google Apps Script product configurator, `Configure.gs`)
  that shows/hides/enables/disables Finish/CCT/Beam/Wattage/IP Rating/Control
  options live while you're picking a product's variant, based on real
  fixture constraints (e.g. "IP65/66 wet-rated strips don't offer DALI",
  "the wall-wash optic on this downlight only comes in the higher wattage").
  Rules are scoped per **typology**, not per exact product or per category,
  so they carry over to any specsheet classified under it. They have no screen
  since 2026-09-28, when the Config Rules tab gave way to **Nomenclature**: every
  rule in use is one of Exenia's couplings, written by
  `scripts/catalogues/build_exenia_config_rules.py`, and the Configurator still
  applies them. See `app/config_rules.py` for the evaluation
  engine and `seed.py`'s `SAMPLE_CONFIG_RULES`/`TYPOLOGY_BY_PRODUCT_NAME` for
  the starter classification + rule set seeded for the 7 sample specsheets
  (6 typologies - two of the strip products share one).
- **Identity and product codes in NPD** (2026-09-22) - the NPD form's first step, *Identity*,
  holds what **names** a product as opposed to what specifies it: Series, Family, Sub-typology,
  Variant, and any further field a typology needs (**+ Add identity field** - Optic, Lens,
  Mounting...). Each value carries a **short code** beside it - Nyx -> NYX, Midi -> M - because
  naming is what a product code is made of, and whoever invents a series is the only person who
  knows its abbreviation. An added field belongs to the *typology* (a `ParameterTemplate` row
  under the `IDENTITY` group), so the next product of that kind starts with it; its value is
  stored in the specsheet's `fields` like any parameter, and each code is a `FieldCode` row -
  the same table the rest of the product code already comes from. On the generated specsheet
  the added identity values print in the **name bar** after the series and family, with the
  model number still flush right; they never appear among the parameter columns. Sub-typology
  and variant code but are not printed there, because the title line already says them. The
  step previews both the name bar and the assembled code as you type, and the Configurator's
  generated code is built by the same rule - identity segments first, then the configured
  options - so the two cannot disagree. Endpoints: `GET /api/npd/identity`,
  `POST /api/npd/identity-code` (identity fields only, so it is not a back door into the whole
  Product Codes table), and a product's `identity` on `GET /api/specsheets/{id}`.
- **Product codes, built by a structure the team edits** (2026-09-25) - every SKU of a brand
  that has a code structure - every configuration of every product - gets a code of its own:
  `DL-RC-CRV-AD-MC-WT-LR-7W-27K-CRI95-15D-WH-MW-IP20` - product type, mounting, series,
  adjustability, size, trim, optic (the **identity**), then wattage, CCT, CRI, beam, finishes,
  IP, control and accessories (the **configuration**, chosen when quoting). A code says **only
  what the product states**: a segment a product has no value for is left out of its code, never
  filled with what is usual, and InHouse's own designations are kept as written (CURVE PIN,
  RAYON DIFFUSED, VENA S1012, LUMEA 1010BHTV). **Every choice a product offers when quoted is a
  segment**, so no two configurations share a code - a grazer's fixed or adjustable bracket, a
  lens grazer's beam, an underwater fitting's mounting and cable entry all change it. Every
  numeric segment carries its unit (7W, 27K, CRI95, 15D, IP20, L1000, DIP67 for a driver's IP,
  1TO3W for a range), and a code of digits alone is refused. **NPD -> Product codes** is where it
  is all changed, without a developer: per typology, which segments the code carries and in what
  order (drag, or the arrows), whether each is in the code and whether it may be left blank, the
  prefix and separator, the abbreviation for every value (or a rule with its unit - 14.4W
  becomes 14P4W), and new attributes the catalogue has never recorded. A typology without a
  structure of its own follows the **Brand default**; **Use brand default** puts a typology's own
  structure aside rather than deleting it, and **Restore its own structure** on that typology's
  tab brings it back exactly as it was (saving a new structure there replaces it). The panel checks the rule
  the whole scheme exists for - one SKU, one code - across every configuration (13,099 for
  InHouse), listing products with a value or code missing (Open lands on the field) and products
  whose configurations share codes, and gathers every missing abbreviation into one list with a
  suggestion for each. On a product's NPD page each **code sits beside the value it
  abbreviates**, the way a price sits beside its option; the whole code reads along the bar at
  the foot of the page, with anything missing as a link to its field. In the **Configurator** the
  code fills in as options are chosen and goes on its own line in the description once complete
  (**Include the product code in the description** turns it off); accessories ticked alongside
  end the code. Set up for InHouse from *Product Classification and Codes.xlsx* by
  `scripts/backfills/structure_inhouse_catalogue.py` (splits the old composite series column into
  the fields it states, and rebuilds option lists split the old way - "SURFACE ( FIXED" and
  "ADJUSTABLE )", or a wattage called M - while every sheet prints exactly as before),
  `scripts/migrations/migrate_code_attribute_affixes.py` and
  `scripts/backfills/seed_product_codes.py` (attributes with their units, abbreviations, one
  structure per typology). See `DataBaseSchema.md` 3.6a.
- **Search NPD by parameter** (2026-10-19) - the NPD list's search box finds products by what
  they offer as well as by name or code, for price reviews that start from a parameter: `12W`
  (wattage), `3000K` (CCT), `24°` (beam), `IP65`, `CRI90`, `55mm` (cut-out or length), or any word
  (`dali`, `black`, `trimless`). Terms are separated by spaces and every one must match, so
  `12W 3000K` is the products offering both; a phrase in quotes is one term. Values are compared as
  numbers, so `12W` never finds 112W, and a strip's `12W/M` is found only by `12W/M`. A range - a
  tunable white `TW (2700K-6500K)`, a `1-3W` - finds a product only when it has no option of that
  value itself, and its chip says so ("covers 5000K"): every tunable white also offers a plain
  3000K, so `3000K` shows that option's price rather than the TW adder, while `5000K`, which no
  product offers on its own, still finds the 41 tunable whites. The line under
  the box says how the query was read; each result shows what matched, with that option's price
  adder, and a click opens the product at that parameter, where its price is set. The list
  (`GET /api/npd/products`) carries each product's searchable values (`params`, split by
  `npd.split_options`) and `price_adders`.
- **NPD for DuraGreen** (2026-10-19) - NPD opens on one brand at a time, chosen from the
  **Brand** dropdown at the top of the tab, and each brand is a separate screen over separate
  data. InHouse is authored in NPD and coded by the studio's product code, as above. DuraGreen is
  DuraGreen's: its products are read from its website and every spec sheet the way a lighting
  engineer reads them - optics (beam, CCT, CRI, UGR, binning, efficacy), electrical (system power,
  control, input, driver output, class), mechanical (engine size, mounting, cut-out, ceiling
  thickness, material, finishes, IP, ambient temperature) and general (lifetime, certifications,
  accessories, parts ordered separately) - with links to the product's page and DuraGreen's own
  spec sheet. A DuraGreen BOQ line gets **DuraGreen's own order code**, built the way DuraGreen's
  **2026 General Catalogue** says a code is built: the product's printed code with the chosen
  wattage, CCT, driver and beam put in from its range's code table (ACME or MOOD), then the finish
  (bezel + baffle, `TB` for trimless, or a structure colour). So 2700K DALI with an Antique Brass
  bezel gets `AFL5708W-AL-27D120-AB` although the sheet prints only 3000K non-dim. Each code says how
  sure it is (printed, built from this product's parts, its range's table, the general table, or
  **unconfirmed**); where no rule gives a piece, the line carries no code and the Configurator says
  why - nothing is guessed. The code tester shows where every piece came from, and **How DuraGreen
  builds this code** shows the catalogue page's example and table. Codes are checked against
  every code DuraGreen prints - none wrong. The NPD (DuraGreen) page shows each product's printed
  codes and code parts, has a **code tester**, and takes codes DuraGreen sends by email (**Add an
  order code**, **Record what one option adds to the code**). Products the website has and the app
  did not arrive **awaiting approval** and stay out of the Library until released. **Pricing**
  works as for InHouse - a base price plus an adder per option - and is blank until a price list
  exists. Separate tables (`vendor_order_codes`, `vendor_code_parts`, `vendor_code_rules`), so DuraGreen's letters never
  reach a InHouse code. Filled by `scripts/catalogues/crawl_duragreen_site.py` ->
  `build_duragreen_catalogue_db.py --from-crawl` -> `scripts/backfills/backfill_duragreen_npd.py`, then
  `scripts/catalogues/read_duragreen_general_catalogue.py` -> `scripts/backfills/backfill_duragreen_code_rules.py`.
  **Tracks** are read from their own spec sheets, which print every part's code in full: a track is
  quoted on **length** and **colour** (`GB2300-W` = TRACK 1L surface, 3 m, white; MINI-LINE 22's
  `TC22ST` is one code for every length, so the length goes on the line beside it), and each feed,
  connector, joint and end cap is a product of its own - one BOQ line per part number - quoted on
  colour and, for a feed, the side its **polarity groove** is on, as the sheet's icon draws it.
  Each part's picture is its drawing from the sheet; new parts arrive awaiting approval. MINI-LINE
  11 (Stucci Micro 11) accessories are printed "9601-S/..." without their finish part, so they carry
  no code until DuraGreen gives one. `scripts/catalogues/read_duragreen_track_sheets.py` ->
  `scripts/backfills/backfill_duragreen_tracks.py`.
  **Adding a new DuraGreen product** (2026-10-21) needs no script: **+ Add a DuraGreen product**
  offers three ways. **From its web page**: paste the product's address on duragreenlighting.co.uk
  and every version on that page is read with its spec sheet. **From a spec sheet PDF**: upload the
  sheet DuraGreen sent. **Type it in**. The first two are read by exactly the functions the 19 Oct
  catalogue run used (`app/duragreen_sheet.py`, called by `app/duragreen_import.py`), and are shown
  first: each version says whether it is **new** or an **update** of a product already in the app,
  its order codes and options, what an update would change, and any warning (a sheet that could not be
  read starts unticked). Nothing is saved until **Bring in**. New products arrive awaiting approval;
  an update never touches approval, codes a person typed in, or the catalogue's codes. **Prices** can be
  set or changed in the same step: each product's **Prices** section shows what it costs now, with a
  box for the base price and one per option (the adder), pre-filled with the current figures. A box
  left blank keeps its price - an import never removes one - and changing a price needs **Edit
  prices**, as everywhere else; every change is in the activity log with its old and new figure. A sheet
  read from the website is linked to DuraGreen's copy; an uploaded one is kept in `data/uploads`. Once
  approved, the product is in the Library, Configure, Search and Frequently used like any other.
  See `DataBaseSchema.md` 3.6b and `app/duragreen.py`.
  **The four key series** (2026-10-22): NPD (DuraGreen) opens on a shelf of **ACME, MOOD, EVOLINE
  and FLOW** - pick one, then DuraGreen's own range (ACME CURVE, MOOD SQUARE ...) and size; the
  shelf last opened is remembered in the browser. Add Product has the same shelf as a **DuraGreen
  series** tab. Whichever way a DuraGreen product is picked, **Printed by DuraGreen** above the form
  lists every code its sheet prints with the options it is printed for (filterable: "12W", "3000K");
  one click sets those options, says whether the code is complete, and moves to the finish if
  DuraGreen's catalogue adds one. A code still waiting for its finish shows greyed with "…" and is
  never written on the line. Every DuraGreen product also has a class a lighting designer would
  give it (Pendant, Wall Light, Projector, Garden Spike, Wiring Accessory ...), filled only where it
  had none (`scripts/backfills/backfill_duragreen_typology.py`).
  **The BOQ grid** (2026-10-27, branch Retention): a detailed BOQ's areas are tables, about 30 rows
  to a screen, edited like a spreadsheet - click a cell to select it, type or Enter / F2 to change it,
  Enter / Tab to save and move on, Esc to put it back, Ctrl+Z / Ctrl+Y to undo and redo. A product's
  finish, CCT, beam, wattage and control open its Configurator on Enter (its price and code follow;
  F2 types over them by hand). Each row's actions are one "⋯" menu (Ctrl+D duplicates, Alt+↑/↓
  moves). Under every area, type a product's name to add it ("nyx midi 3000K" sets the CCT where the
  product has it), or paste rows copied from Excel. Area and BOQ totals are always shown; the
  project details fold to one line; Export offers "Again" for the last export. Grid / Cards in the
  toolbar switches back to the cards.
  **DuraGreen's naming** (2026-10-27): Nomenclature, with DuraGreen chosen, shows DuraGreen's own
  naming - never the studio's product code: its code tables (ACME, MOOD, EVOLINE, ARTFOCUS 36,
  BELLA 41, GLAMO 68) segment by segment with their letters, and every product filed under the naming
  it follows (a table, its own catalogue page, its own spec sheet - grouped by the letters DuraGreen
  starts its codes with - or no code yet). A code DuraGreen sends can be added to one of its tables
  (marked "entered"; what DuraGreen printed never changes), and a new naming structure can be made
  for a range no table covers. A product no catalogue page names can be put in any of them (product
  page -> Naming), each segment **fixed** for the product, **chosen when quoting** (an option the
  Configurator asks, with its price adder) or **not in its code**; "Add a DuraGreen product -> Type it
  in" does the same in one go, with the code shown as it is decided.
  **Options as rows** (2026-10-27): on a DuraGreen product page every quoted option is a row of its
  own - its value, the code DuraGreen writes for it and how sure that code is, and what it adds to
  the base price - as InHouse NPD shows them; a printed fact with several values (two cut-outs) is
  a list of values. Nothing is written "A / B / C" in one box. Every DuraGreen product has a
  picture, taken from its own spec sheet where the website shows none
  (`scripts/backfills/backfill_duragreen_pictures.py`). The list draws 60 cards at a time.
  **No Driver** (2026-10-26): any DuraGreen product whose order code carries a driver letter can
  be quoted with Control "No Driver", for when we supply our own driver. Its code then carries `X`,
  which is what every one of DuraGreen's code tables prints for No Driver.
- **Product Codes** - a short-code lookup per Finish/CCT/Beam/Wattage/IP
  Rating/Control option value (e.g. Finish "WHITE" -> "WH"), ported from the
  studio's Configurator's own SKU-builder (`Configure.gs`'s
  `generateProductCode`). Global across every typology, not per-product -
  set them on the **Nomenclature** tab, which lists every value a brand's
  products use with an editable code next to each (the older
  `/api/field-codes/option-values` list has no screen of its own any more). A
  value with no code defined simply contributes nothing to the generated
  code rather than falling back to raw parsed text. The Add-Product modal
  uses this table to build the live Generated Product Code described above -
  shown for reference/copying only, separate from S.No.
- **Export** - two formats, same underlying data, picked from either the BOQs
  list or the editor toolbar:
  - **Export .xlsx** (`app/excel_export.py`) - matches the sample template's
    actual cell styling (fonts, fills, borders, column widths - read directly
    off the sample workbook, not guessed): a **Cover sheet** with the studio's
    actual logo (`app/assets/logo.png`, extracted from the sample template's
    own embedded image) above a thin divider, the date standalone/bold with
    no label, then PROJECT/PROJECT CODE/AREA/CLIENT as "LABEL :" rows -
    **Project Code** (since 2026-08-06) is the studio's own project reference
    (e.g. "CRA-2026-014"), editable in the New BOQ dialog or the editor's
    meta panel, genuinely optional and shown blank if unset, same as every
    other cover-page field; one sheet per floor
    (header block, S.No/Image/Description/Finish/Wattage/Beam/Control/Make/Qty/Unit
    columns, the floor-title and room-label banner rows, product rows with
    their S.No/Image cells merged, aspect-correct embedded product photos),
    and the standard NOTE/terms sheet. This is the working format - reopen
    it in Excel to keep adjusting cell-level detail.
  - **Export PDF** (`app/pdf_export.py`) - a clean, print-ready `.pdf` of the
    same cover info (same logo, same date/PROJECT/PROJECT CODE/AREA/CLIENT layout),
    per-floor/room item tables (product photos included) and terms page -
    for sending straight to a client to open and read, without needing
    Excel or preserving any further editability. Shares its company
    letterhead/logo and terms text with the `.xlsx` export (imported from
    the same module) so the two can never drift apart on that wording.
  - **Accessories get their own "ACCESSORIES" label**, in both formats - a
    run of consecutive accessory rows (Accessory Row, Add Driver) always
    forms its own merged S.No/Image block with a plain "ACCESSORIES" label,
    never a product photo (a driver row has no specsheet linked at all since
    2026-08-06 - see Add Driver above) and never merged into whichever
    product happens to precede it. A driver added right after other accessories
    shares that same "ACCESSORIES" block with them.
  - **Choose columns before exporting** - either export first opens a
    checklist of every column (S.No/Image/Description/Finish/Wattage/Beam/
    Control/Make/Qty/Unit/Remarks, all checked by default); unchecking any
    of them leaves it out of that specific export entirely - the common
    case being unchecking **Image** for a client who doesn't need product
    photos in what they're sent. Removing a column reclaims its space
    rather than leaving a blank gap (the `.xlsx` column layout rebuilds
    around whichever are left; the `.pdf`'s remaining column widths scale
    back up to fill the page).
- **History** - every specsheet and every BOQ (plus each exported file) is
  kept in a local SQLite database (`data/app.db`), so past BOQs can be
  reopened, edited, duplicated, or re-exported later.
- **Help tab** - a plain-language, in-app reference for the design team
  (not a developer doc - that's what this README/`TUTORIAL.md` are for):
  how to build a BOQ, add products four different ways, export a BOQ vs a
  Quotation, and answers to common questions ("the product I need isn't in
  the Library," "why does this show Unclassified," ...). Static content
  (`HELP_SECTIONS` in `static/app.js`) with a jump-to-topic chip row at the
  top - no API calls, so it can't go stale from missing data, only from the
  UI itself changing underneath it (kept in sync by copying real button
  labels verbatim rather than paraphrasing them).

## Setup

Requires Python 3.10+.

```
pip install -r requirements.txt
```

## Run it

```
run.bat
```

(or manually: `python seed.py` once to load the sample specsheets, then
`python -m uvicorn app.main:app --reload` to start the server.)

Then open **http://127.0.0.1:8000** in a browser.

`seed.py` imports the PDFs in `SampleInputs/SampleSpecsheets` into the library
on first run so there's something to build a test BOQ against; it's safe to
re-run (it skips files already imported). Upload additional/real specsheets
any time from the Library tab - a new upload gets its **typology**
auto-assigned from a keyword match against its parsed category text
(`app/typology_classifier.py`, e.g. "downlight" -> `Downlight`), the same
taxonomy configuration rules and the Configurator picker use. A genuinely new
product family (nothing in the keyword table matches) still shows
"Unclassified" and needs one manual assignment from the detail view -
by design, not a gap: guessing wrong would silently apply the wrong
typology's rules to a real BOQ item, so it only ever auto-assigns when
confident. `scripts/backfills/backfill_typology.py` re-runs the classifier against every
currently-Unclassified specsheet, for a library that had uploads before this
existed.

`scripts/migrations/migrate_typology.py`, `scripts/migrations/migrate_room_remark.py`, `scripts/migrations/migrate_series_family.py`, and
`scripts/migrations/migrate_kind_attention.py` are one-off scripts, already run, that added columns
in place on a database that already had real data (`typology`,
`config_rules.category` renamed, `boq_rooms.remark`,
`series`/`family`/`variant`/`page_number`, `boqs.kind_attention`) - a fresh
`python seed.py` against an empty database sets everything up correctly
without needing them. Kept for reference/as migration examples (see
`TUTORIAL.md` section 6.1). `scripts/catalogues/reimport_catalogs.py` is a similar one-off that
re-processed specsheets uploaded before multi-page catalogs parsed
correctly - confirms nothing in a real BOQ references the entries it's
replacing before touching them.

`scripts/catalogues/import_drivers.py` loads the driver library the **Add Driver** mini-
configurator picks from, from `SampleInputs/DRIVERS.pdf` - not a normal
one-product-per-page datasheet but an inventory/stock-report table (191
SKUs). Only ~95 of those rows extract cleanly; the rest have genuinely
scrambled text in the PDF's own content stream (confirmed by inspecting raw
word positions, not a parsing bug) and are deliberately skipped rather than
guessed at. Safe to re-run - every row is checked against the same
duplicate-detection fingerprint the upload endpoint uses before inserting.

## Data & storage

Everything lives under `data/`, created automatically:

- `data/app.db` - SQLite database (specsheets, BOQs, floors, rooms, items, export history)
- `data/uploads/` - original uploaded specsheet PDFs
- `data/images/` - product photos extracted from each PDF
- `data/exports/` - every generated BOQ `.xlsx`, timestamped

Delete `data/app.db` (and optionally the other folders) to reset to a clean
slate; `run.bat` will reseed the sample specsheets on next start.

## Known V1 limitations

- Duplicate detection on upload is deliberately **exact-match only, never
  fuzzy** - two specsheets that are 90% the same but genuinely differ in
  even one real parameter (a newer catalog revision that adds a warranty
  clause, two accessory options of the same strip light) are correctly kept
  as separate entries, not silently merged. This can mean near-identical-
  looking entries stay in the library side by side; use **Compare
  Specsheets** (Library tab) to see exactly what's different between two
  entries that look alike before deciding whether to delete one by hand.
- No pricing/rate/amount columns (matches the sample BOQ, which doesn't have
  them either).
- PDF parsing is tuned to the layout of the sample InHouse-style datasheets
  (title block + grouped key/value parameter tables + a handful of diagrams).
  Every page is parsed independently (`parse_specsheet_pages`), so both
  single-page datasheets and multi-page catalogs (one page per size/trim
  variant) work correctly - but a differently structured PDF may still parse
  incompletely; the raw text and the original PDF are always kept and
  viewable in the specsheet detail view as a fallback, and any item's fields
  can be typed/edited by hand regardless of what was parsed.
  `scripts/maintenance/reparse_in_place.py` re-derives every specsheet already in the library
  from its source PDF with whatever the current parser produces - safe to
  re-run any time `app/pdf_parser.py` changes and you want existing entries
  to reflect it, without losing their id (and therefore any BOQ items that
  reference them) or manually-corrected classification.
- The S.No/Image merge in the export groups a product with its immediately
  following accessory rows (a generalizable rule); the sample file's own
  merging of the Make/Wattage/Beam/Control columns was done by hand per-BOQ
  and isn't reproduced, since it wasn't a consistent rule to begin with.
- The company letterhead ("From:" block) is a fixed constant in
  `app/excel_export.py` (based on the sample template) rather than editable
  in the UI. The NOTE page's terms/closing text itself **is** editable per
  BOQ - see "Edit Notes" below.
- Reordering items is via up/down buttons rather than drag-and-drop.
- The embedded PDF viewer in the specsheet detail view needs a real desktop
  browser's built-in PDF viewer (Chrome/Edge/Firefox all have one) - it won't
  render in minimal/headless browser setups.
- Configuration rules have no builder or screen (the Config Rules tab was
  removed 2026-09-28); they are written by the Exenia importer. The evaluation
  engine applies several rules with the same target/action cumulatively, which
  is how OR-style coverage is expressed.
- ~~No admin/login gating on who can edit configuration rules~~ **Done
  (2026-08-04)** - login is required for the whole app (see deployment.md
  section 3.3); opening the Nomenclature tab specifically requires the
  `config.edit` permission ("Open the Nomenclature tab", set on the People tab). Accounts are provisioned with `python create_user.py` (no
  public self-registration, matching a confidential internal tool).
- Typology is a single flat classification, not the full multi-level chain
  hinted at in `Configure.gs` (Application -> Architectural Luminaires ->
  Product Type -> Series -> Sub Series -> Product Family) - there's no real
  data behind those extra levels for the 7 sample specsheets, so only the
  "Architectural Luminaires" level (renamed `typology` here) was implemented.
  Extend `Specsheet`/`ConfigRule` with more classification fields if the
  catalog grows enough to need the fuller hierarchy.
- New specsheets are uploaded with a blank typology by design (it can't be
  reliably parsed from a PDF) - assign one from the Library detail view
  before it'll show up in the Configurator picker or be affected by
  typology-scoped rules.
