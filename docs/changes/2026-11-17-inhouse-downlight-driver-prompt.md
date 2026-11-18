# Adding a InHouse downlight asks whether to add its driver

Branch: `feature/inhouse-downlight-driver-prompt`
Lane: feature
Status: live (6e5e71a)
Asked: "Add a feature, whenever user adds a inhouse downlight to a BOQ, automatically ask him to add a driver, if user clicks yes, open the driver configurator, if no, then proceed as usual. Local first", 2026-11-17
Approved: the request above. It fixes the whole design (when it asks, what Yes and No do), and this record adds no choice beyond it except the two exclusions under Scope.
Special cases: +1 because the owner asked for this one brand and typology; it is one entry in a list (`DRIVER_PROMPT_RULES`), not an `if`, and the audit does not count it. Moving it into NPD's per-typology settings is a follow-up.
Memory: no change expected

## Why

A InHouse downlight runs on a separate driver, and a BOQ without one is short a line the client will need.
DriverRules.md: "The driver is not enforced as mandatory ... nothing in the app currently refuses a BOQ without it."
Today the designer has to remember to press "+ Add Driver" after every downlight.

## Evidence

- `static/app.js` `openAddFromLibraryModal`, after "Add to Room": the window closes and nothing more is offered.
- `openAddDriverModal(roomId, parentItem)` already places a driver directly under a given product (the quick-BOQ
  Driver button uses it).

## Scope

In: after a new line is added from Add Product in a detailed BOQ, when its product is InHouse and its typology is a
downlight, a window asks "Add a driver?". "Yes, add a driver" opens the driver configurator with the downlight as its
parent. "No, not now" or closing it leaves the BOQ as it is.

Out (and why):
- Reconfiguring a line: nothing is being added.
- A quick BOQ's own Add Product: it goes straight on to placing the line in areas, and a second window would replace
  that step. Every quick-BOQ row already has a Driver button. (A downlight added to a quick BOQ from the Library's
  "Add to BOQ" is asked, as anywhere else: that add has no placement step, and a quick BOQ takes driver rows.)
- Making a driver mandatory.
- Filling the driver's wattage from the downlight: the configurator opens on its own defaults, as from "+ Add Driver".

## Design

In `static/app.js`: the brands and typologies that are asked about are one list, `DRIVER_PROMPT_RULES`
(`[{make: "inhouse", typology: /downlight/i}]`), read by `wantsDriverPrompt()`, so another brand or typology is a
line of data, not a new `if`. `askToAddDriver()` uses the app's `openModal` and calls the existing
`openAddDriverModal(roomId, product)`. Nothing on the server changes.

## Acceptance

- [x] A1 Adding NYX -Mini (#330) to an area asks "Add a driver?".
- [x] A2 Yes opens "Add Driver", listed under that NYX -Mini.
- [x] A3 No closes the window and adds nothing beyond the downlight.
- [x] A4 Adding a InHouse track light (RAYON Spot, #7) asks nothing.
- [x] A5 No page errors.
- [x] A6 Reconfigure of a downlight asks nothing; an add that goes on to placement (a quick BOQ's, `onSaved`) asks nothing.
- [x] A7 gate.py: 0 FAIL, 0 WARN, special cases none added.

## Risks

- A designer adding twenty downlights gets twenty questions. "No, not now" is one click, and the question names the
  product. If it is too much, a "Don't ask again for this BOQ" option is a small follow-up.

## Rollback

`git reset --hard <previous>` and restart. No data.

## Verification log

2026-11-17, coordinator, local server on this branch, with real clicks:
- A temporary admin made BOQ #327 with one area (#1214) and added NYX -Mini (#330), NYX -Midi (#332) and RAYON Spot (#7)
  through Add Product.
- A1-A5 passed.
- Cleaned by id afterwards: BOQ #327 with its lines #6350-6352 and its area, 6 activity rows, and user #371. Checked
  gone.

- A6, coordinator, real clicks: a temporary admin made BOQ #328 with area #1215. An add that goes on to placement
  (`onSaved`, as a quick BOQ's does) of NYX -Mini (#330) asked nothing. Reconfiguring that line (#6354) and saving
  asked nothing. No page errors. Cleaned by id: BOQ #328, line #6354, 5 activity rows, user #372, checked gone.
- A7: `gate.py --full`: 0 FAIL, 0 WARN; memory worst scenario ask_index 187.4 MB (unchanged by this front-end change).
- repo-reviewer (17 Nov): no release-blocking code defect. Its findings and their answers:
  1. A6 had no logged run: now logged above.
  2. A Library "Add to BOQ" into a quick BOQ is asked: accepted, and Scope says so.
  3. The "already has a driver" check could never be true, because add-on rows are not flagged `is_driver` when
     saved: removed, with its Scope line.
  4. The brand rule in shared code: recorded as Special cases +1, with the reason.
- feature-tester (17 Nov): every check passed twice (before and after the hasDriver removal); 28 tables identical before and after; its table follows.

2026-11-17, feature-tester, own server on this branch (port 8017), Playwright with real clicks and typing
(Add Product > Search, type the name, click the product, "Add to Room"). Temporary admin #374, view-only member
#375 (boqs.view only), detailed BOQ #329 (area #1216), quick BOQ #330. Run twice; the second run is on the
coordinator's edit without the "already has a driver" check, and its results are the same.

| Check | Result | Evidence |
|---|---|---|
| A1 NYX -Mini asks | PASS | After "Add to Room": window "Add a driver?", "NYX -Mini needs a driver to run..."; both buttons inside the 1366x768 window; Yes has focus. Lines #6355 / #6365 |
| A2 Yes opens Add Driver under it | PASS | Click Yes: "Add Driver", "Listed directly under NYX -Mini_Recessed LED Downlight..." |
| (a) completing the driver | PASS | "Add to Room" in the driver window: driver #6356 parent_item_id 6355, #6366 parent 6365, is_driver true |
| A3 No adds nothing more | PASS | NYX -Midi #6357 / #6367 only, no driver row, window closed |
| (d) close cross | PASS | Window closed, only the downlight added |
| (d) Escape | Note | Escape does nothing: the window stays and the BOQ is unchanged (no app window closes on Escape; `openModal` has no Escape handler). Shift+Tab to "No, not now" + Enter closes it with nothing added |
| A4 RAYON Spot (#7) | PASS | No window after add; lines #6360 / #6370 |
| (c) DuraGreen 55 Adjustable (#388, Downlight) | PASS | No window after add; lines #6361 / #6371 |
| A6 Reconfigure | PASS | Edit > Use Configurator > Save Changes on #6355 (has a driver) and #6357: no window, 0 lines added |
| (b) quick BOQ's own "+ Add product" | PASS | NYX -Mini #6364: next window is "Where does this go?" (placement); no "Add a driver?" in the page |
| A5 page errors | PASS | 0 page errors in every run. The only console errors are 404s for picture files missing from this worktree's data/images (Nyx_Series__2_p1_1.png etc.), not from this change |
| (e) member without boqs.edit | PASS | 0 visible Add Product / Add Driver / + / Edit buttons; POST product and POST driver under a downlight: 403 "You need Build and edit BOQs"; GET BOQ 200 |
| Hostile text | Not run | The name shown comes from the library (no test product was made); the code escapes it with escapeHtml and the gate's escaping check passes |
| Regression: + Add Driver, row "+" > Driver | PASS | Driver #6362 / #6372 (no parent), #6363 / #6373 under #6357; no driver question |
| Smoke | PASS | /login 200; BOQ list 200; BOQ #79 200; #79 BOQ .xlsx/.pdf and Quotation .xlsx/.pdf all 200 with the right file type; /api/specsheets 200; /api/npd/products 200 |
| gate.py | PASS | 0 FAIL, 0 WARN, special cases none added |

Not tested by the feature-tester: the Library's "Add to BOQ" into a quick BOQ (accepted as asking; reviewer point 2).
Cleanup by id: BOQs #329 and #330 deleted through the API (lines #6355-6373, rooms #1216-1217, the quick BOQ's
area), boq_exports #318-321 and their 4 files in data/exports, 33 activity rows (id > 4533 of users #374/#375),
permission #69, users #374 and #375. Fingerprints before and after: "28 tables identical, 0 changed."

## Next step

Commit, then release on the owner's yes naming the commit.
