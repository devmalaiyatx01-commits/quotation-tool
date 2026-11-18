# NPD rules: choices that depend on other choices, starting with "IP66 strip, no IP68 connector"

Branch: `feature/npd-option-rules`
Lane: feature
Status: live (7d020cc)
Asked: "Goal: For striplights, when a user chooses IP66 option, IP68 connector (under accessories) should not be shown. Context: On the InHouse website, rule based configuration must happen. Make a framework under NPD to define rules while configuring a product. It must be user friendly to define, UI/UX must be so that the feature doesnt look clustered. Act as a product manager to decide how to fit this feature, and do it locally first", 2026-11-17
Approved: the request above. It delegates the product decisions to this design and asks for a local build.
Special cases: +0
Memory: no change expected

## Why

A designer adds an IP66 strip and is offered an IP68 connector. That connector is for a strip that is not sealed this
way, so it does not belong on that line. The studio knows many such "this, then not that" facts, and the InHouse website
will need the same facts to configure products. Today there is nowhere in NPD to write them down.

## Evidence

- A rules engine already exists and runs whenever a product is configured: `config_rules` (1,209 rows, all Exenia
  "Show Only" rules from its importer) and `app/config_rules.py`, ported from the Apps Script configurator. It holds
  up to five conditions per rule, Hide/Show/Enable/Disable on another field, per typology or per product. Its screen
  was retired on 28 Sep (Nomenclature replaced Config Rules), so no one can write a rule today.
- The engine does not know accessories. Add Product's accessory list (`GET /api/specsheets/{id}/accessories`, drawn
  by `loadAccessoryAddon`) is fetched once when the product is picked and never looks at the choices.
- The case asked for:
  - InHouse strips Filo S 05 (#281) offer IP20 or IP65, and their accessories are "NIL / Water Stopper / IP68
    Connector / U-shape profile".
  - Vena S (#152-155) offer IP20 or IP66.
  - Typology "Flexible Strip Light", 14 products.

## Scope

In:
- **NPD product page, a "Rules" section** below Accessories, on a saved product:
  - Each rule reads as one sentence: "When IP rating is IP65 or IP66, hide IP68 Connector from Accessories." It shows
    its reach ("Every Flexible Strip Light" or "This product") and Edit / Remove.
  - "+ Add rule" opens one sentence builder in place. A condition ("When [IP rating] is [chips]", up to five,
    joined by "and"), then "then [hide | offer only] from [Accessories | a choice]" with that thing's values as
    chips, then "for (•) every Flexible Strip Light / ( ) this product only".
  - A live preview of the sentence, then Cancel / Add rule.
  - The choices offered are the product's own: parameters with more than one option, named as its template names
    them, and its accessory list.
- **Add Product:** the accessory list asks for the current choices when it opens and again whenever a choice
  changes. A ruled-out accessory leaves the list, is unticked if it was picked, and a line says "Not offered with
  these choices: IP68 Connector".
- **Server:**
  - `/api/config-rules` takes and lists rules per product (`specsheet_id`), and checks every rule's shape.
  - `GET /api/specsheets/{id}/accessories?state=` returns `hidden_accessories`.
  - Writing a rule needs "Create and edit products" or "Configuration": it is part of defining a product.

Out:
- Creating the strip rule in live data. It is made in NPD by a person, and this record's tests make one and remove it.
- Exporting rules to the InHouse website: rules are rows of `config_rules`, ready for the website's engine, but the
  handoff repo is not changed here.
- "Grey out" (Enable/Disable) and hide-a-whole-question actions: the engine has them, the builder offers only the two
  that read plainly. Rules made elsewhere with them still show, as text.
- Accessories as a condition. The configuration a person makes does not include accessories.

## Design

Reuse the engine; add only what it lacks.
- Accessories become one more field the engine can act on. This is done in the accessory route, not in
  resolve-options, so the answer every configuring screen reads keeps its shape.
- The route passes the product's own options for every field, as resolve-options does: the engine settles each field
  to one of its options, and a field it is not given loses the choice a condition reads. Found in testing.
- Rules save on their own, as people expect of a list they edit, not with the product's Save.
- The section stays one quiet list. The builder appears only while a rule is written, and the count sits beside the
  heading.

The engine applies only the most specific of several matching rules for one field, which is the Apps Script behaviour
it was ported from. Two rules on accessories set off by different choices: only the deeper one applies. This is kept,
so the 1,209 Exenia rules behave exactly as before. The builder's one-condition rules, the common case, are unaffected.

Rejected:
- A new rules table. `config_rules` already has the shape, the engine and the per-product scope.
- Bringing back the old Config Rules tab. That is a table of every typology's rules, away from the product a person
  is defining, which is the clutter the owner asked to avoid.

## Acceptance

- [ ] A1 NPD, Filo S 05 (#281): Rules shows "No rules yet". Add rule "When IP rating is IP65, hide IP68 Connector from
  Accessories, every Flexible Strip Light". The list shows that sentence, "Every Flexible Strip Light", and a count of 1.
- [ ] A2 Add Product, an approved InHouse strip under the rule (#282, Filo S 10; #281 awaits approval and cannot be added), IP rating IP65: the accessory list has no IP68 Connector, and the note names it. At IP20 it
  is back. Ticked at IP20, then IP65 chosen: it is unticked.
- [ ] A3 Edit the rule to IP66, scope "this product": saved and shown. Remove: gone, with confirmation.
- [ ] A4 Refused: a condition with no value, nothing to hide, a rule acting on its own condition (in the page); a
  malformed rule body (400 from the server); a person without "Create and edit products" or "Configuration" (403).
- [ ] A5 Unchanged: resolve-options answers for the 5 Nov comparison cases equal `main`'s. The Exenia rules apply as
  before. A product with no rules gets its full accessory list.
- [ ] A6 No page errors; fingerprints identical after cleanup.
- [ ] A7 gate.py --full: 0 FAIL.

## Risks

- A wrong rule hides a real accessory everywhere in a typology. Mitigation: the sentence and its reach are always
  shown; removing a rule is one click; nothing is deleted from the product.
- An extra request per choice change in Add Product (the accessory list). Mitigation: only the newest answer is used,
  and a failure leaves the list as it was.

## Budgets

Memory: no change. Special cases: +0. Lines: +76 in `app/main.py`, about +280 in `static/app.js`.

## Rollback

`git reset --hard <previous>` and restart. Rules people made stay in `config_rules`, and the old code still applies the
ones on choices. Rules on accessories are then ignored and do nothing.

## Verification log

2026-11-17, coordinator, local server on this branch, real clicks (`scratchpad/rules/ui_test.py`), Filo S 05 (#281):
- A1: "No rules yet". A rule was built with the sentence builder, and the preview read "When IP rating is IP65, hide
  IP68 Connector from Accessories." It was then listed with "Every Flexible Strip Light" and a count of 1. The
  accessory chips are the product's own list: Water Stopper, IP68 Connector, U-shape profile, and not the printed
  row's "NIL".
- A2: in Add Product at IP20 the list offers IP68 Connector. Ticking it, then choosing IP65, removes it from the
  list, unticks it and shows "Not offered with these choices: IP68 Connector". Back at IP20 it is offered again and
  the note goes.
- A3: edited to "this product only" and saved. Removed after confirming.
- A4: an empty condition is refused in the page. The server answers 400 to a condition with no values and to a rule
  with no typology.
- A6: no page errors.
- In process: with the rule "IP65 or IP66 → hide IP68 Connector", Filo S 05 hides it at IP65 and Vena S (#152) at
  IP66. Neither hides it at IP20 or with no choice, and a value a product does not offer (IP66 on Filo S) hides
  nothing.
- Found and fixed while testing:
  - The engine settles every field to one of its options, so the accessory route must pass every field's options,
    or the IP choice was wiped before the rule could read it.
  - A list redraw referenced a function out of its scope.
  - "IP" was written "Ip" in the sentence.
  - The builder offered the template's printed "NIL" as an accessory.
- Cleanup by id after every run: the rules made (rule ids above the floor, none left), test BOQs #331-335 with their
  areas and lines, their activity rows, and users #377-381. `config_rules` is back to 1,209.
- repo-reviewer (17 Nov), each finding answered:
  1. High: a rule "for every Flexible Strip Light" also reached other brands, because typology names are shared.
     Fixed. A typology rule now carries its brand as a condition (`{"field": "make", "values": ["InHouse"]}`,
     `RULE_BRAND_FIELD`), and every place rules are evaluated puts the product's (or Configure panel's) brand in the
     state. The page says "Every InHouse Flexible Strip Light", and a rule made without a brand reads "Every <typology>,
     every brand". Test: the same rule hides the connector for #281 (InHouse) and nothing for the same product as
     another brand.
  2. Fixed: two stray carriage returns made `style.css` a whole-file rewrite. `git diff --stat` now shows +38.
  3. Fixed: the Rules section could draw a previous product's rules after a quick switch. It is now drawn only for the
     product still open.
  4. Fixed: on the Configure path the accessory list never asked again; its own choices now ask. A choice changed
     while the first list loads is asked about once more.
  5. Fixed: the accessory check was sent before resolve-options settled the choices; it now runs after.
  6. Fixed: a malformed condition, a non-list `affected_options`, a bad `specsheet_id` and an `*Only` rule naming
     nothing are refused (400), not repaired.
  7. Fixed: a refresh asks `?only=hidden`, which skips the prices and details, and only when the product has a rule on
     accessories (`has_accessory_rules`).
  8. Info, intended: whoever may author products may now change any rule, including the imported Exenia ones. Every
     change is in the activity log.
- Found while fixing 1: the accessory route put the rules' own condition fields into the engine's order, and with the
  brand among them the engine reset it (a product has no "make" options). The brand stays out of the order.
- A5, `scratchpad/rules/compare.py`, main (8019) against this branch (8018), same database: 15 requests, 14 identical.
  They cover the product list, Configure for five typologies, product resolve-options (#586, #281 at IP65, the Exenia
  #1281 with its imported rules), description, BOQ #79 and list pricing. The 15th, #281's accessories, differs apart
  from the two new keys only in one accessory picture (`accessory_part-56_1.png`), which this worktree's `data/images`
  does not hold. No changed line touches pictures (`git diff main -- app/main.py`: 0 lines naming images).
- After the fixes, 19 browser and API checks passed, with no page errors. Cleaned by id each run: rules above the floor
  (none left, `config_rules` = 1,209), BOQs #336-337, users #383-387 and their activity rows.
- gate.py: the WARN "escaping" names `${ruledNote()}`. That is HTML built by `ruledNote`, which escapes each accessory
  name with `escapeHtml`.

2026-11-17, feature-tester, own servers (branch 8020, main 8022, same database), real clicks/typing/wheel in Playwright,
scripts and screenshots in the session scratchpad `ft/`:
- A1 PASS (12/12): #281 shows "No rules yet"; rule built by clicks; preview and list read "When IP rating is IP65, hide
  IP68 Connector from Accessories." with "Every InHouse Flexible Strip Light" and count 1; a double-click on Add rule
  saved one rule; stored with `{"field":"make","values":["InHouse"]}`; survives a reload.
- A2 PASS on #282 (Filo S 10 - LL 106002, same typology): IP20 offers the connector, ticked, IP65 drops it, unticks it,
  note names it, back at IP20 it returns; the product added at IP65 has no connector line. #281 itself cannot be
  picked in Add Product (it is "Awaiting approval"; Search lists only #150), so A2 as written is not doable by a person.
- A3 PART: on #281, scope to "this product" and back saved and shown (brand condition restored once); Cancel changes
  nothing; Remove asks, Cancel keeps it, OK removes it. "Edit to IP66" can only be done from a product offering IP66
  (Vena S #152); there the old IP65 has no chip and cannot be unpicked, so the save stored "IP65 or IP66".
- A4 PASS: page refuses empty condition, nothing to hide, acting on its own condition, a sixth condition; server 400 on
  14 malformed bodies, 404 unknown product/rule, 422 not-JSON; member without npd.edit/config.edit 403 on POST/PUT/
  DELETE and sees no Add/Edit/Remove; npd.edit-only and config.edit-only members may write; writes are logged.
- A5 PASS: 25 of 28 requests identical to main; the 3 differences are the new `specsheet_id` key in the rule list and
  accessory pictures this worktree's `data/images` does not hold. Exenia #1281 rules give the same CCT on both.
  BOQ #79: BOQ .xlsx 1,763 cells identical, quotation floor totals 208,310 / 157,820 / 201,675 on both, PDFs 200.
- Configure path PASS: IP comes from the panel (not a product dropdown); at IP65 the connector is hidden with the note,
  a Driver IP change asks again and keeps it hidden, panel IP20 offers it. DuraGreen strips have no IP or accessories:
  shown in process (same strip as DuraGreen hides nothing) and with a choice rule (DuraGreen #695 keeps Dali; a
  brandless control rule does reach it).
- Hostile values show as text, nothing injected. FAIL (minor): a 400-character unbroken value pushes the page to
  3,236 px wide and that rule's Edit/Remove off screen.
- Phone 390x844 by touch, and 1366/1536/1280/1093 desktops: section and builder fit, no sideways scroll. A6 no page
  errors. A7 gate.py --full 0 FAIL, 1 WARN (answered above); memory npd_products +1.5 MB, library +6.1 MB.
- Found: (1) another brand's typology rule is listed on a InHouse product, and Edit > Save with nothing changed
  rewrote its brand to InHouse (DuraGreen #695 lost it); (2) Filo S 05's condition list shows "IP rating" twice
  (`ip_rating` and `driver_ip_rating`).
- Cleanup by id: rules 1236-1247, BOQ #339 (lines 6374-6375) through its API, exports 322-329 and their 8 files,
  48 activity rows, permissions 70-76, users 390-393. fingerprint --compare: 28 tables identical, 0 changed.

- Answers to the feature-tester and the ui-ux-reviewer (17 Nov), coordinator:
  - Tester 1 (a rule could change brand): the page now lists only rules for this brand, or for every brand. Editing
    a typology rule keeps the brand it had. Test: a Duragreen strip rule is not shown on #281 and is left untouched.
  - Tester 2 (a value the product lacks could not be unpicked): a rule's held values always show as chips. Test:
    editing the IP65/IP66 rule from #281 shows IP66, unpicking it saves IP65, and the InHouse brand is kept.
  - Tester 3: two parameters with the same name are told apart by their banner ("IP rating (power supply)").
  - Tester 4: long values wrap (`overflow-wrap: anywhere`) in the list, chips and preview.
  - Tester 5: A2 now names #282, because #281 awaits approval.
  - UX 1-9 applied:
    - On a phone a rule is one column, Edit/Remove are 32 px tall and the builder loses its inner frame.
    - The heading is 14.5px, like the steps.
    - Edit/Remove are larger, and Remove stays red on hover.
    - A blank builder starts on "Choose…" and its preview shows "…".
    - On a phone the chips are 36 px and each scope option sits on its own line.
    - The "Not offered" note sits under the list, as a row in its own type.
    - The radio buttons are ink, not blue.
    - Word spacing and radii follow the tokens, and the editor is at most 760 px wide.
    - The intro now says each rule saves on its own.
    - Not done: scope as chips (kept as styled radio buttons).
  - After these: 25 checks with real clicks (`scratchpad/rules/ui_test.py`) all pass, with no page errors. Cleaned by
    id: rules above the floor (none left), BOQs #340-341, users #396-397 and their activity rows.

- 2026-11-18, coordinator, after the session break: the run cut off by it was repeated in full on a fresh server, and all
  25 checks passed. They include the UX review's fixes: the blank builder starts on "Choose…", the preview reads "…" for
  what is not chosen yet, Edit and Remove are larger with Remove red on hover, the phone layout is fixed, and the note
  sits under the list. They also include the tester's fixes: another brand's rule is neither listed nor rebranded, a
  value held but not offered can be unpicked, and the two IP ratings are told apart. Cleaned by id: rules above the
  floor (none left, `config_rules` = 1,209), BOQ #374, user #430 and its 11 activity rows. The memory profiler's
  temporary account #398, left by the interrupted gate run, was deleted by id (no rows hung off it).

## Next step

Commit, then release on the owner's yes naming the commit.
