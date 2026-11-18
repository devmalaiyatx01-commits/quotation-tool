# Ask and Match understand a designer's own words, and answer searches from real values

Branch: `feature/ask-natural-language`
Lane: feature
Status: live (dae90e4)
Asked: "The ask tab and matching tab must understand the text in natural language, even though exact wording is not matched, it should behave like a GPT model but for the app's database. For example, if i want too get a duragreen product having nearly 1400Lm with cutout less than 100mm it gets hallucinated. Fix these things. Act as a lighting designer , prepare a set of questions which a person making a BOQ can ask, and based on those questions train the RAG acting as a AI engineer", 2026-11-03
Approved: "Fix these things" (2026-11-03), the owner's direct instruction to build; design below not separately reviewed
Special cases: +0
Memory: a compact catalogue cached for 120 s per tool; see Budgets

## Why

A designer asks Ask for "a duragreen product having nearly 1400Lm with cutout less than 100mm" and gets an
invented answer. They cannot trust it, so they go back to searching by hand. Match reads the same sentence as
one row with nothing usable in it.

## Evidence

Read-only on the local database, 2026-11-03:

- BM25 for that question ranks `100 Fixed`, `100 Trimless` and `100 Adjustable` first, because "100mm" shares
  the number. Their cut-out is 100 mm, which is not less than 100 mm.
- No Ask record held a lumen figure: 0 of 366 DuraGreen products have `lumen_output`. `match_index` already
  estimates 299 of them from efficacy x wattage, but Ask never used it. So the model had nothing to answer from.
- The right answer exists: 55 DuraGreen products meet both limits, checked against raw columns.
- Match's paste reader took a three-product sentence as one requirement, and found none in a sentence with no
  schedule wording.

## Scope

In:
- `app/catalogue_query.py`: designer vocabulary as data, a request reader, search and answer from real values,
  the model as a fallback reader only, sentences into Match rows.
- `app/assistant.py`: product searches go there first and need no model; records state lumens; unit figures not
  in the records are flagged.
- `app/npd.py`: `lowest_price()`, one rule for a product's cheapest price, following `match_price()`.
- `templates/index.html`, `static/style.css`, `README.md`, `TUTORIAL.md`, `docs/context/backend.md`: Ask is always offered,
  its styles, and the docs that describe it.
- `app/main.py` Match route, `app/match_index.py`, and Ask and Match in `static/app.js`: sentence rows, row make,
  any-typology rows, a cut-out limit.
- `docs/quality/ask_questions.json` and `scripts/maintenance/ask_eval.py`: the designer's question bank and its check.
- `.claude/agents/RAG.md`: the context the agent must keep (main folder, as the owner opened it).

Out:
- Creating BOQs or Quotations from text. Ask still changes nothing.
- Conversation memory across questions.
- Embeddings or a vector database (would need a package and approval); constraint search answers what they could not.
- Any change to data, prices or how schedules and PDFs read in Match.

## Design

Numbers are compared by code, never by a model. A request becomes constraints; `check()` tests each product
against `match_index`'s values, the same ones Match uses, so the two tabs cannot disagree. The answer is written
from those values with no model call, so searches also work with the model off or the allowance spent.

Rejected: only tightening the prompt (the facts were not in the prompt); handing the model more records
(1,229 products do not fit, and it still would not compare reliably); embeddings (word-meaning similarity does
not understand "less than").

The model's one job is `interpret()`: wording the rules left unread becomes proposed constraints, whose lumen,
wattage and cut-out numbers must be in the sentence, and whose brand and kind must exist. "Training" is the bank
plus vocabulary rows, proven by `ask_eval.py`.

Match: only short pasted sentences take this path (`looks_like_sentences`). The limit is a new `cutout` field, so
a schedule's `cutout_dimensions` still does not make uncut-out products "cannot check".

## Acceptance

- [ ] A1 The owner's sentence in Ask returns DuraGreen products only, each with a stated cut-out under 100 mm and
  lumens within 1,190-1,610, and not `100 Fixed`; lumens marked est.; the reading is shown; no AI request used.
- [ ] A2 `ask_eval.py` passes every question, and its second opinion on raw columns finds no wrong result.
- [ ] A3 With the model off, the owner's sentence is still answered; a records question says why it cannot be.
- [ ] A4 "What is the NPD price of NYX -Midi?" and "Which BOQs used 75 ZOOM?" still use the records path, and someone
  without `boqs.view` gets no BOQ in either path.
- [ ] A5 A search with nothing to return says so and names the nearest and what each misses; invents nothing.
- [ ] A6 Words not understood are named in a note.
- [ ] A7 On Match, pasting the owner's sentence gives one row: make Duragreen, any typology, about 1400 lm, cut-out
  under 100 mm, with candidates obeying the limit. A multi-sentence paste gives one row per product with area and qty.
- [ ] A8 On Match, a real schedule, PDF and labelled "Supply of" paragraph read exactly as on `main`.
- [ ] A9 A saved Match session restores make, any typology and limits. Clearing the make chip searches every make.
- [ ] A10 Interpolated values in the new HTML are escaped; a question containing `<script>` renders as text.
- [ ] A11 A real model call for unusual wording gives a reading whose lumen, wattage and cut-out numbers are all in the
  sentence and unread by the rules, and whose CCT, beam, IP, CRI and UGR are written there or a trade meaning from the
  vocabulary. A kind of fitting may be inferred; it is marked as the AI's reading, and dropped if it empties the search. A
  physical feature may not be added by the model at all.
- [ ] A12 A hostile question (repeated words built to stall a regular expression, a 400-digit number) answers in under a
  second with no error, on Ask and on Match. "lumens is is is ..." once stalled a whole worker.
- [ ] A13 With the model off, the Ask tab is still offered, and its search works on screen.
- [ ] A14 A sentence row names what it names: "10 NYX Midi" offers only NYX -Midi, and where several fit equally the row says
  "Choose one" rather than picking the alphabetically first. Adding a row from a sentence to a new quick BOQ works.
- [ ] A15 Ask and Match give the same products for the same sentence: "about 1400 lm" is 1,190-1,610 lm in both.

## Risks

- A wrong default meaning (bathroom = IP44) silently narrows results. Guarded by showing the reading every time.
- Match regressions on real documents. Guarded by routing only short sentences here, A8.
- Memory on the small VM. Guarded by reading `params` only where needed, as `build_index` does; budget below.
- Price visibility: Ask already showed NPD prices to anyone it answers; this keeps that.
- Sends typed text to the model only when words go unread, as Ask already does with every question.

## Budgets

Memory: new cache `catalogue_query.catalogue_for`, one per tool, rebuilt after 120 s, kept until replaced: 5.9 MB held for 1,229 products, 22.3 MB peak while building (tracemalloc, 2026-11-04, with the name pattern and price rows; empty on the decorative tool). `gate.py --full` figures are in the log.
A paste is capped at 2,000 characters and 40 clauses, and spends at most one model request. Speed: reading a request 4.7 ms,
searching the catalogue 4.5 ms. Special cases: +0, wording is tables. `app/main.py` about +30 lines, `static/app.js` about +100. `npd.py` +20.
Gate WARN answered: all flagged interpolations are escaped values, a number, or a choice between constant strings.

## Rollback

`git reset --hard <previous>` on the VM and restart. No schema or data change.

## Verification log

- 2026-11-03 feature-tester, stopped early at the coordinator's request because the code is about to change, so a fresh run is needed. **Not a pass.** Pass: A1 (55 products, 55 by a raw-column check, AI counter unchanged, four desktop sizes), A2 (`ask_eval.py` 0 of 65 failed), A4 (price matches the database; no BOQ for someone without `boqs.view`), A11 (numbers). Fail: A3 (server answers, but the Ask tab is not offered with the model off), A5 and A6 in part, A7 (`app/main.py:3525` reads a `request` the function does not have: 500 on the owner's sentence).
- 2026-11-03 feature-tester, A8: pasted labelled "Supply of" text reads the same as `main` (7 paste shapes). Not run: real schedule and PDF, A9, A10 on screen, the row editor's cut-out field, adding a sentence row to a quick BOQ, Ask chips at 390 px, most of the smoke run, memory, `gate.py`.
- 2026-11-03 feature-tester, hostile: a 62-character question ("lumens is is is ...") froze a throwaway local server (`/login` timed out 3 times at 20 s; the question had no answer at 75 s). A 400-digit number gives a 500. The page says "no AI request was used" after using one (counter 12 -> 13). Sentence-path findings on Match were seen through a tester-only stand-in for the missing `request`, in a separate process; no app file was edited.
- 2026-11-03 feature-tester, cleanup: 21 activity rows (3868-3888) and users 278, 279 deleted by id; no BOQs or export files made. AI requests 7 -> 13 (6 used). Fingerprints: 27 tables identical; `users` 19 -> 18 because id 277, not this run's, was gone by the end (real accounts stop at id 25). Who removed it is not confirmed.

- 2026-11-03 builder, after the first review and test: 22 review findings and F1-F14 fixed. ask_eval.py 148 of 148 (bank, sentences, 17 hostile inputs, the real Match route). gate.py --full: 0 FAIL, 1 WARN, worst memory scenario ask_index 187.5 MB. WARN answered: all 6 flagged interpolations are escaped values, a number, or constants. The `users` difference in the first run is explained: id 277 was memory_profile.py's temporary user, made and removed by the gate running at the same time. Real accounts: 18, highest id 25.

- 2026-11-03 feature-tester, fresh run on the fixed code. **Not a pass.** Fail: A7 in part (a room that is also a place word gets no area, so "Bathroom: 6 downlights" after "Living room needs ..." sits in Living room); A14 in part (a "Choose one" row also says "No product meets every rule" and "NO MATCH" above "2 product(s) meet every rule"); A15 in part ("around 1400 lm downlight" gets a Round chip from the letters in "around": Ask 115, Match 103; "Ground floor lobby ..." Ask 338, Match 316; the owner's sentence is the same in both, 55 = 55 by id); F12 in part ("at least 1000 lumens": the row's slider says "Products from 1000-Infinitylm"); F14 in part ("say what it is in the box above" with no box on screen). Also found: 45 pasted lines give 40 rows with no word about the other 5; the real model added an "adjustable" chip for "gentle on the eyes", which A11 does not cover and which narrowed the answer from 60 to 45.
- 2026-11-03 feature-tester, pass: A1 (55 products, 55 by a raw-column check, four desktop sizes, AI counter unchanged), A2 (`ask_eval.py` 0 of 148 failed), A3, A4 (price and BOQ answers match the database; with a stand-in model, someone without `boqs.view` sends no BOQ record), A5, A6, A7's owner's sentence, A8 (15-row real schedule, a sheet with "≤12W" cells, 3 real PDFs, 8 paste shapes: identical to `main` at 36d9e0e; `SampleInputs/Match Tab Samples` holds no PDF, so the PDFs came from other sample folders), A9, A10, A11 as worded, A12 (41 hostile strings on Ask, slowest 0.04 s; 37 on Match, slowest 0.10 s; `/login` slowest 0.15 s meanwhile), A13, adding sentence rows to a new quick BOQ, F2, F3, F4, F6, F8, F9, F10's product choice, F11, F13, the row editor's cut-out field, Ask chips at 390 px, permissions (403 from the server), smoke (login, BOQ list, BOQ #79 and its .xlsx and .pdf, NPD, Library). Not compared: BOQ #79's grand total on `main`, as no export code changed and exporting there would write to the read-only main folder; its BOQ JSON is identical on both.
- 2026-11-03 feature-tester, budgets: `memory_profile.py --only import,ask_index,npd_products`: 147.3, 186.3, 181.2 MB, within 0.7 MB of the baseline (no scenario builds `catalogue_for`). The catalogue measured 1.5 MB held and 13.4 MB peak on a second build, against the 2.3 and 8.7 above. `gate.py`: 0 FAIL, 1 WARN (the 6 interpolations already answered).
- 2026-11-03 feature-tester, cleanup: AI requests 13 -> 17 (4 real, the limit). BOQs 311, 312 deleted through `DELETE /api/boqs/{id}`; by id: export rows 281-283 and their 3 files, activity rows 3889-3970, permission row 68, users 281-283 and 285-287 (285-287 from a short Library re-check). Servers stopped. Fingerprints against the start of the run: 28 tables identical, 0 changed.

- 2026-11-03 builder, after the second review and the fresh test: findings fixed (wrong areas, "can" read as a downlight, "warm white 3000K" read as a white finish, negations, quantities taken from specifications, rows past 40 vanishing, continuation lines, "Choose one" wording, shapes found inside words, Infinity on screen, invented features). ask_eval.py 236 of 236. gate.py --full: 0 FAIL, 1 WARN (all flagged interpolations are escaped values, a number or constants), worst memory scenario ask_index 187.3 MB.

- 2026-11-03 feature-tester, narrow re-test after the builder's fixes, 0 real AI requests (counter 17 -> 17). **Not a pass.** Fail: A7 in part, quantity (a number right before a specification is lost with no word and the Add dialog says 1: `10 IP65 bollards`, `Lobby needs 4 12W downlights`, `8 3000K downlights`, `10 CRI 90 downlights`, `20 1400 lm downlights`; `10 75 ZOOM` and `4 x 75 ZOOM` lose it too, with a note); A7 in part, area (a room said another way takes the previous room: `Lobby needs 4 downlights. 6 downlights for the bathroom` is Lobby with the bathroom's IP44); A15 in part (`12W downlight except NYX Midi`: Ask 67 without NYX -Midi, Match 69 with it, the row warns "Not checked in Match"); A11 in part (on Match with Use AI ticked, a kind from a stand-in model shows "Downlight guessed", not "(AI's reading)"). Lower: "fits 55 products equally" although 12 tie for closest and the list is ordered closest first; 400 digits in the Cut-out box reads "not read" but the chip makes the page 3334 px wide.
- 2026-11-03 feature-tester, narrow re-test, pass: the earlier items 1-7 as briefed (the 6 area sentences and a multi-area quick BOQ with each line in its area; Choose one with the first candidate truly closest, 40 lm from 1400; same ids for the 4 "round" sentences; no Infinity on the slider; Cut-out box "not read"; 45 lines give 40 rows and 5 listed; stand-in features add nothing and a kind shows "(AI's reading)" on Ask; the blocked row's message points at Edit values). Second review's sentences, except as above. `Can you suggest a InHouse pendant in brass?` reads InHouse, pendant, brass and says the catalogue has none, which is true (0 InHouse pendants in the database). A1 (55, raw check 55), A8 (same schedule, `≤12W` sheet, 8 paste shapes and 2 PDFs identical to a clean copy of 36d9e0e served from the scratchpad), A9, A12 (Ask 52 strings, slowest 0.13 s; Match 49, slowest 0.22 s), A14 (NYX -Midi qty 10, ₹N Living room). Not re-run: A2, A3-A6 beyond the sentences above, A10, permissions, smoke, memory, `gate.py`.
- 2026-11-03 feature-tester, narrow re-test, cleanup: BOQs 313, 314 deleted through `DELETE /api/boqs/{id}`; by id: activity rows 3971-4015, users 289, 290. Servers stopped. Fingerprints against the start of this re-test: 28 tables identical, 0 changed.

- 2026-11-03 builder, not yet tested by the feature-tester: after the third review, comparisons with no table row are reported
  rather than reversed, quantities are searched past a rejected number, a line with unread words stands as its own row, area
  protection applies to its own line only, "in the lobby" ends a carried area, and rows that could not check everything or
  that the model read are never auto-picked. Open by choice: limit-only rows still list alphabetically under the "closest" tag,
  though a person must pick; "Which warranty applies?" can spend two requests.

- 2026-11-04 feature-tester, final narrow check on the code above, 0 real AI requests (counter 0 -> 0, a new day). **Not a pass.** Fail: A15 in part (`In the kitchen, 4 downlights`: Ask 235 with IP44, Match 413 with no IP chip); "DALI driver 20W" stays its own row but reads as "Luminaires for tracks" limited to Exenia's 7 Driver Box products, with none of the 140 drivers offered; F6 (400 digits in the Cut-out box still makes the page 3324 px wide); F5 in part (the row and bar no longer say "equally", but the page's hint line still does). Lower: rooms read from "for the bathroom" or "in the lobby" keep the small letter onto the BOQ, and `Bathroom: 2 NYX Midi. 6 NYX Midi for the bathroom` puts both lines in an area named "bathroom".
- 2026-11-04 feature-tester, final narrow check, pass: quantities (all earlier failures, `4 x 75 ZOOM`, `10 75 ZOOM`, `75 ZOOM 10 nos`, `about 10 downlights`, `100 adjustable downlights`; through Add to BOQ `10 IP65 bollards` qty 10, and the Lobby sentence 10, 4, 2 in Lobby), the room list including `Please send by Friday` left out as one row, `12W downlight except NYX Midi` (67 = 67, no NYX -Midi), `downlight cut-out no bigger than 100 mm` (161 = 161, raw check clean), the stand-in model on Match (row says the AI read it, not auto-picked), `trimless 7W 2700K downlight` (warns, not auto-picked), owner's sentence (55 = 55 by id, raw check 55), A8 (schedule, `≤12W` sheet, 8 paste shapes, 2 PDFs identical to the clean copy of 36d9e0e), A9, A14 (qty 10, ₹N Living room), smoke (login, BOQ list, BOQ #79 and its .xlsx and .pdf, Library). A12, 3 rounds per string: Ask 52 strings, slowest 0.49 s (`F2 500 chars`, first request); Match 49, slowest 0.62 s (`unicode`); none over 1 s, no error; after the catalogue cache ran out 0.33 s and 0.43 s. `ask_eval.py` 3 runs, 0 of 320 failed each, hostile 22 of 22: the one earlier failure did not recur and is still unidentified. Not re-run: A2's raw review beyond those 3 runs, A3-A6, A10, A13, permissions, NPD, memory, `gate.py`.
- 2026-11-04 feature-tester, final narrow check, cleanup: BOQs 315-318 deleted through `DELETE /api/boqs/{id}`; by id: export rows 284, 285 and their 2 files, activity rows 4016-4086, users 292, 293. Servers stopped. Fingerprints against the start of this check: 28 tables identical, 0 changed.

- 2026-11-04 feature-tester, short check of the last fixes, 0 real AI requests. One failure, minor: a row read as a driver (`DALI driver 20W`, `4 DALI drivers 30W`) still warns "Not understood: driver", and `12W downlight with DALI driver` warns the same. Pass: `10 downlights 3000K` then `DALI driver 20W` is two rows, the second a driver row from the driver catalogue, and both reach a new quick BOQ (qty 10; qty 1, ₹N); `12W downlight with DALI driver` stays a downlight; `In the kitchen, 4 downlights` is Kitchen with IP44, 235 = 235 by id; `6 downlights for the bathroom` reads Bathroom; 400 digits in the Cut-out box leaves the page as wide as the window at 1366 and 1093; no "equally" on the page; owner's sentence 55 = 55 by id; `10 NYX Midi` Choose one; the real schedule identical to the clean copy of 36d9e0e; a saved session restores the driver row. Cleanup: BOQ 319 through `DELETE /api/boqs/{id}`; by id: activity rows 4087-4096, users 295, 296. Servers stopped. Fingerprints: 28 tables identical, 0 changed.

- 2026-11-04 builder, closing /verify: ask_eval.py 330 of 330 (bank, Match sentences, 22 hostile inputs, the real Match route). gate.py --full: 0 FAIL, 1 WARN (all flagged interpolations are escaped values, a number or constants), worst memory scenario ask_index 187.0 MB. Three reviews and four test runs, each finding fixed or listed below. Fingerprints 28 of 28 identical after the last run. The false "Not understood: driver" note was fixed after the last on-screen run and proven by ask_eval.py only.
- 2026-11-04 limits of this evidence: the repo-reviewer did not re-read the fixes after its third review; the feature-tester tested them instead. A3-A6, A10, A13, permissions and NPD were last run on screen before the final two rounds, and passed. The 127 MB villa PDF was last compared in the second run; it gave 0 rows on both. One hostile-input failure in ask_eval.py appeared once, was not identified, and did not recur in 7 later runs.
- 2026-11-04 open by choice: limit-only Match rows list alphabetically under the "closest" tag, though a person must pick; Match warns on, but does not filter, physical words and finish, so those sentences give more products there than in Ask; "12W or 15W" keeps the first value and reports the second; the free model can spend two requests where it decides a question is not a search; `classify_shape` finding "round" inside "around" and "Ground" remains for documents, as on main, and needs its own change. TUTORIAL.md: Part XV, 15.13.

- 2026-11-04 release-manager, on live as `dae90e4` (docs/releases/2026-11-04-ask-natural-language.md): login 200, served files byte-identical, journal clean, 28 fingerprints identical, workers 144 and 146 MB after the restart. `gate.py --full --base 441d76e` flags 13 interpolations, not the 6 above; each was read: escaped values, numbers or constants. The new behaviour was not exercised on live: it needs a session, and could use the AI allowance.

## Next step

None for the release. The owner tries Ask and Match on live.
