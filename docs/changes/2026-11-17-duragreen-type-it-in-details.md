# Adding a DuraGreen product by hand asks for its details, the way a InHouse product does

Branch: `feature/duragreen-type-it-in-details`
Lane: feature
Status: live (e314921)
Asked: "For the duragreen NPD , allow a user to add a duragreen product in a similar fshion they add a inhouse product, for example , allow user to define some parameters, like CCT, Wattage, Beam Angle, etc etc based on the typology, keep the default structure as that of InHouse for common typologes. Context: User' s goal is not to generate an accurate specsheet for durgreen but to add enough information so that the product can be confugured and quoted on a BOQ", 2026-11-17
Approved: "Yes, build it" (the owner's answer to the design question, 2026-11-17)
Special cases: +0
Memory: no change expected

## Why

A designer has a DuraGreen product that is not on DuraGreen's website: the vendor sent its details in an email or a
quote. They open NPD (DuraGreen) > Add a DuraGreen product > Type it in. They can type its series, name, size and
typology. They cannot type its wattage, CCT, beam or anything else, unless they first choose a naming structure, and
then only the details that structure names. The product is saved with no details, so Configure cannot offer it and a
BOQ line for it says nothing. Adding a InHouse product goes the other way: you pick a typology and NPD lists that
typology's parameters (cutout, finish, IP, beam, power, CCT, CRI, control ...). Each one is a single value, or a list
of options that can each add to the price.

The designer is not trying to rebuild DuraGreen's spec sheet. They want enough on the product to configure it and
quote it.

## Evidence

- `static/app.js` `dgAddByHandDraw` (about line 15858): Type it in's fields are series, product name, size,
  typology and web page. Wattage, CCT, beam and so on appear only as segments of the chosen naming structure
  (`dgNamingRowsHtml`). With "Not yet - I will add its codes in the product" chosen, nothing about the product's
  specification can be typed.
- `app/main.py` `npd_duragreen_create` (about line 8801) already accepts `fields` (DuraGreen's own details,
  `duragreen.LABEL_OF`) and `options`. The form never sends `fields`.
- The Configurator is not limited to a list of details. `configurator.candidates` asks whatever details a brand's
  products of a typology hold (`options` for choices, `fields` for stated values), labelled from that typology's NPD
  template (`_template_labels`). A product saved with details is configurable without changes to Configure.
- InHouse's form is built from `parameter_templates` (`GET /api/npd/parameters?typology=`): 14 typologies, 399
  rows, in four groups (MECHANICAL, OPTICAL, SOURCE, POWER SUPPLY). Each row is one value, or "Affects price" with
  option rows and an amount each adds (`npdParameterRow`).
- Local data (a copy of live, 9 Oct): 449 of DuraGreen's 615 products sit under a typology that has a InHouse template
  (Downlight 205, Track Light 105, Spotlight 91, Flexible Strip Light 17, Step Light 11, Uplight 11, Wall Grazer /
  Wall Washer 9). The other 166 do not: Driver 45, Pendant 27, Wiring Accessory 21, Wall Light 16, Demo Kit 15,
  Projector 13, Garden Spike 11, and a few others.
- The two brands overlap but are not the same:
  - Twelve keys are shared: wattage, cct, beam_angle, cri, control, finish, ip_rating, input_voltage, material,
    cutout_dimensions, ugr and accessories.
  - Three InHouse keys mean the same as a DuraGreen key under another name. operating_temperature is ambient_temp
    ("-20°C TO +55°C" against "-20 ℃ to 45 ℃"). color_consistency is binning ("<3 SDCM" against "2 Step MacAdams").
    lumen_maintenance is lifetime ("L70B50@80,000hrs." against ">50,000 hrs (L70 B10)").
  - Two keys look alike but are not: InHouse's optics_type ("LENS + REFLECTOR") is not DuraGreen's optic_type
    ("Adjustable", "Pinhole"), and C.C/C.V is not DuraGreen's driver output.
  - Some InHouse templates leave out details DuraGreen's products are quoted on. Spotlight, Step Light and Uplight
    have no CONTROL row, and no template has a bezel or baffle finish.
- A DuraGreen product stores a choice twice: as a list in `options` (`["8W", "10W", "12W"]`) and joined in `fields`
  (`"8W / 10W / 12W"`), as product #586 does.

## Scope

In:
- NPD (DuraGreen) > Add a DuraGreen product > Type it in: once a typology is picked, a list of the product's
  details, each a single value or a set of choices, with what each choice adds to the price (only for people who
  may edit prices).
- One read-only route that lists those details for a typology.
- The create route and the product save route accept those details. The DuraGreen product page shows and edits a
  detail that only the InHouse template names (under "More details"), so nothing typed at Add is lost afterwards.

Out:
- InHouse NPD and the InHouse templates: read, never written. Adding a detail at "Type it in" does not add a row to
  any template.
- The website and PDF import ("From its web page or a PDF"), order codes and naming structures: unchanged.
- No migration, no backfill, no existing product touched.
- Making the product approved. It starts pending, as today. Configure offers approved products, so someone approves
  it in NPD before it is quoted, as with every DuraGreen product.

## Design

**The list of details for a typology** (new pure function in `app/duragreen.py`, served by
`GET /api/npd/duragreen/details?typology=`, permission as the other NPD (DuraGreen) reads):

1. When the typology has a InHouse template (matched by `typology_match.typology_key`, so "Track Light" finds
   "Track Light"), its rows in its groups, order and names. That is "the default structure as InHouse's for common
   typologies". IDENTITY rows are left out (series, name and size are asked above).
2. Then DuraGreen's own choices that DuraGreen products of this typology are quoted on and the template does not
   name. Read from the products' `options` keys, not written down per typology: bezel and baffle finish for
   downlights, control for spotlights. They sit under a group "DURAGREEN OPTIONS".
3. A typology with no InHouse template (Pendant, Driver, Wall Light ...) gets DuraGreen's own groups
   (`ENGINEER_GROUPS`: OPTICAL, ELECTRICAL, MECHANICAL, GENERAL), the ones its product page already shows.
4. "+ Add a detail" offers any other DuraGreen detail (`LABEL_OF`), for this product only.

The three same-meaning keys are kept as data in `duragreen.py` (`SAME_DETAIL`). A row is saved under DuraGreen's key
(OPERATING TEMPERATURE is stored as ambient_temp), so a typed-in product looks like every other DuraGreen product to
the product page, Search, Ask and Configure. Every other template row is stored under its own key (lumen_package,
adjustability, ik_rating ...).

**Each row**, as InHouse's `npdParameterRow`, with one difference in wording: InHouse's switch says "Affects price";
here it says **"Chosen when quoting"**, because the point is that the Configurator asks it. Off: one value. On: option
rows, each with "+₹ adds" (shown only with "Edit prices"). Saved as DuraGreen saves choices today: `options[key]` is
the list, `fields[key]` is the list joined with " / ", and `price_adders[key]` holds the amounts. A base price box as
today.

**With a naming structure:** the structure section stays as it is (fixed / quoted / out per segment, with the code
shown). A detail the chosen structure names is shown in the details list as "set in How DuraGreen names it", the way
InHouse's ACCESSORIES row says "set in Accessories below". One place to type each value. Values typed before a
structure was picked are carried into it: one value becomes fixed, choices become quoted, with their amounts. They come
back if the structure is set to "Not yet". Nothing typed is lost.

**Server:** one helper decides which detail keys a DuraGreen product may hold: `LABEL_OF`, plus the keys of its
typology's details list, plus any it already holds. `npd_duragreen_create`, `npd_duragreen_update` and
`_dg_option_keys` use it instead of `LABEL_OF` / `CONFIG_FIELDS` alone. `_engineer_groups` and `regroup_params` add a
"MORE DETAILS" group for held fields outside `ENGINEER_GROUPS`, labelled by the template (`configurator.label_for`).
The permission line for the new route goes in `_required_permission`. Create is already logged.

Rejected:
- Translating every InHouse row to a DuraGreen key and dropping the rest. LUMEN PACKAGE, ADJUSTABILITY, IK and TRIM
  DETAILS would vanish, and the form would no longer be "the InHouse structure".
- Storing every row under the InHouse key, the same-meaning ones included. The product page would show an empty
  "Ambient temperature" next to a filled "Operating temperature".
- Writing DuraGreen additions into the InHouse templates. That would change InHouse's form.

## Acceptance

- [x] A1 Type it in, typology Downlight: the details list shows InHouse's Downlight parameters (IDENTITY excluded) in
  its four groups and order, then "DURAGREEN OPTIONS" with Voltage, Bezel finish and Baffle finish (the options DuraGreen downlights are
  quoted on that the template does not name). Typology Spotlight: its
  InHouse rows, then Control under DURAGREEN OPTIONS.
- [x] A2 Typology Pendant (no InHouse template): DuraGreen's OPTICAL, ELECTRICAL, MECHANICAL and GENERAL details.
  Changing typology keeps every value already typed for a key both lists have.
- [x] A3 With "Edit prices", add a Downlight with no structure:
  - choices: power 8W / 10W / 12W (+0 / +200 / +400), CCT 2700K / 3000K, control On/off / Dali (+0 / +500);
  - single values: beam 36°, CRI 90, finish White, operating temperature "-20°C to +45°C", lumen package "800 lm";
  - base price ₹N.

  It is saved pending with those `options`, `fields` ("8W / 10W / 12W", ambient_temp "-20°C to +45°C",
  lumen_package "800 lm") and `price_adders`.
- [x] A4 After approving the A3 product: Configure (DuraGreen, Downlight) offers its power, CCT and control choices and
  finds it. 10W, 3000K, Dali prices ₹N and adds a BOQ line whose description carries the chosen values. The line
  says no order code, as any DuraGreen product without codes does today.
  (Found 18 Nov: the line's description carries the CCT (3000K) but not the power or the control. The
  description is built by the shared `build_description` (`app/pdf_parser.py`), which prints CCT, IP, CRI and the
  like for every parsed brand and never power or control - so every DuraGreen line reads this way today on `main`
  and live. This change does not touch it; a follow-up for the owner. The price, 1,700, is right.)
- [x] A5 The A3 product's page shows Lumen package "800 lm" under More details. Edited to "850 lm" and saved, it stays,
  and every other detail and price stays the same.
- [x] A6 With a naming structure picked, its segments' rows read "set in How DuraGreen names it". A power typed as
  choices before picking it arrives in the structure as quoted, with its amounts. Back to "Not yet", the choices are in
  the details list again.
- [x] A7 Without "Edit prices": no amount or base boxes, and the add works without prices. A request that sends prices
  anyway is refused (403), as today.
- [x] A8 Hostile input: a key not in the list is dropped; a value with `<script>` shows as text; a choice repeated or
  blank is dropped; a negative amount or base is refused.
- [x] A9 Nothing existing changes: database fingerprints are identical after the tester's cleanup, and Configure's
  answers for the 5 Nov cases (Duragreen 75 Wall Wash, a InHouse NYX -Midi, an Exenia product) are the same as on
  `main`.
- [x] A10 gate.py --full: 0 FAIL, memory no worse. (gate.py --full: 0 FAIL, 1 WARN (grid escaping, answered); worst memory ask_index 187.3 MB, run on the final commit against live's `ef5047a`)

## Risks

- Someone adding a parameter to a InHouse template in InHouse NPD now also changes DuraGreen's Type it in list for that
  typology. This is intended ("the structure of InHouse"), and it only affects products added afterwards.
- A InHouse-only detail on a DuraGreen product (Optics type: "LENS") next to DuraGreen's Optic type ("Adjustable") reads
  as two similar names. They mean different things, so they are kept apart rather than merged by guesswork.
- Prices: only the new product's own prices are written, under the existing "Edit prices" check. The save route only
  accepts more keys, never fewer, so no held detail or price can be dropped by this change.
- Codes: untouched. A typed-in product has no order code until codes are added or a structure names it. The BOQ line
  says so, as today.

## Budgets

Memory: no change expected (one small read route; the list is built from about 30 template rows and the typology's
DuraGreen products' option keys). Special cases: +0: the three same-meaning keys and the group names are data in
`duragreen.py`, and the typology rule is the template table. Lines: about +60 in `app/main.py`, +60 in
`app/duragreen.py`, +200 in `static/app.js`.

## Rollback

`git reset --hard <previous>` and restart. Products added with the new form keep their details, and the old code shows
the ones in `LABEL_OF`; a InHouse-only detail stays in the row, unseen, and is not lost.

## Verification log

2026-11-17, coordinator, against a local server on this branch (port 8013), real routes and a real browser:

- gate.py: 0 FAIL, 0 WARN; special cases none added (the brand rules went into `app/duragreen.py`: `details_list`,
  `quotable_keys`, `offered_order`, `more_details`, `SAME_DETAIL`).
- API script (`scratchpad/dgdetails/api_test.py`): A1 groups and own options for Downlight and Spotlight, A2 Pendant on
  DuraGreen's groups, A3 the product saved pending with options, " / "-joined fields, ambient_temp from OPERATING
  TEMPERATURE, lumen_package, adders 0/200/400 and 500, base 1000, params with MORE DETAILS; a key not in the list dropped,
  a blank or repeated choice dropped; A5 the page shows Lumen package under More details, edited to 850 lm and kept with every
  other detail and price; A8 a negative base or adder refused (422). Cleaned by id: product #2665, 2 activity rows, 2 users.
- Browser (Playwright, real clicks and typing, `scratchpad/dgdetails/ui_test.py`): A1 the four InHouse groups then DURAGREEN
  OPTIONS on screen; power switched to choices, 8W and 10W typed with +200; A6 picking ACME shows power as "set in How
  DuraGreen names it" with 8W · 10W, and "Not yet" gives back both choices and the 200; A2 Spotlight and back keeps them;
  A3 saved from the form with base 1000; no page errors. Found and fixed: the base price box was only listened to when a
  structure was picked, so a base typed without one was lost. Cleaned by id: #2666, #2667, their activity rows and users.
- Still to do: A4 (approve, Configure, price, BOQ line), A7 with a user who has "Create and edit products" but not "Edit
  prices", A9 fingerprints and Configure comparison, A10 gate --full; then the feature-tester and repo-reviewer (agents were
  at the usage limit on 17 Nov).

2026-11-18, on the release batch (`release/batch-20261118`):

- feature-tester, `scratchpad/ftb/t_dg.py` (real clicks):
  - A3, A5, A6, A7 and A8 passed, including a member without "Edit prices" (no amount boxes, the add works, prices sent
    anyway get 403) and a `<script>` name shown as text.
  - A4: approved, Configure offers 8/10/12W, 2700/3000K and On/off/Dali, finds the product and prices 10W · 3000K · Dali
    at 1,700. The line says no order code. The description gap is noted under A4.
- A9, coordinator, `scratchpad/batch/configure_compare.py` (batch 8040 against main 8041, same database):
  - the 5 Nov cases answer the same: 75 Wall Wash, DuraGreen Downlight 3000K, NYX -Midi 3000K and an Exenia projector;
  - options, descriptions and matching products (same ids, same order) are identical;
  - the one difference is `option_words`, which the latency change takes out of Configure's matches by design.

  Fingerprints after cleanup: see the release batch record.
- A10: in the release batch record (gate --full on the batch).

## Next step

Ships with the release batch of 18 Nov (`2026-11-18-release-batch.md`). Follow-up for the owner: whether a BOQ line's
description should also carry the chosen power and control, for every brand.
