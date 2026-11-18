# DuraGreen: an IP rating printed as two values becomes two choices, in Add Product and in NPD

Branch: `fix/duragreen-ip-options`
Lane: data
Status: live (9b65fe6), code only; the data step awaits the owner's yes
Asked: "for duragreen products , wherever under IP two values are written as one option ( seperated by and), fix them and allow user to choose either one of them , fix this thing on NPD as well. This is becasue the specsheets print the option as a plain text instead of two different options seperated by a slash. Context: Ease of user to make a BOQ by choosing only one value of IP. Local first", 2026-11-04
Approved: the request above, for the local database. The live backfill needs its own yes, with its dry run.
Special cases: +0
Memory: no change expected

## Why

A designer adding a DuraGreen downlight to a BOQ opens the IP Rating dropdown and sees one choice, "IP44 and IP65
options (from the front)". They cannot say which one they are quoting, so the BOQ line cannot state its IP.

## Evidence

Local database, 615 DuraGreen products:

- `fields.ip_rating` holds the sheet's text: 112 "IP20 and IP44 options", 3 "IP44 and IP65 options（from the front）",
  14 "Front IP44, optional IP65". That is 129 products with two values in one.
- `options.ip_rating` is empty on all 615, so `config_rules.stated_values` offers the field's text as one value.
- `ip_rating` is not in `duragreen.CONFIG_FIELDS`, so NPD has no option row for it, and a save could not keep one.
- Nothing depends on the joined text: 0 vendor codes state an IP in their config, 0 price adders on IP. 13 BOQ lines sit on
  these products, and none has an IP in its config.

## Scope

In:
- `app/duragreen_sheet.py`: `ip_options()`, the one reader, used by `build_product` and the web-card reader in
  `app/duragreen_import.py`, so a re-read gives the same result. `ip_rating` becomes a choice field.
- `app/duragreen.py`: `ip_rating` joins `CONFIG_FIELDS`, so NPD shows, saves and can price it.
- `app/duragreen.py` also: `NOT_IN_THE_CODE`, so an IP choice never changes a code and a naming structure cannot name one.
- `app/main.py`: an NPD save keeps the IP text in step with its list, and refuses IP as a structure segment.
- `app/duragreen_sheet.py` `merged_options`: a value with a price adder survives a re-read, for every option and not only IP, because a
  dropped value's price would go at the next save. A priced 10W kept beside a sheet's 12W turns wattage back into a choice, by design.
- `scripts/backfills/backfill_duragreen_npd.py`, the re-read: the same two rules. `scripts/maintenance/audit_duragreen_codes.py`: a check that no
  choice outside the code changes a code.
- `scripts/backfills/backfill_duragreen_ip_options.py`: the 129 products, with `--dry-run`, an undo file and `--undo`.

Out:
- Other spellings of one value ("IP65 front", "IP65 Front"): each is one value, as asked. See Next step.
- Other makes (none has this text). Any price, code, or BOQ line.
- The sheet's own words stay in `params` "DURAGREEN SPEC". The engineer-group copy of the value is rewritten by a re-read or an NPD save,
  as for every other field.

## Design

Stored the way every other DuraGreen choice is: `options.ip_rating` a list, `fields.ip_rating` the same values joined with " / ".
The Configurator, NPD and Match already read those. How the text is read:

- "IP20 and IP44 options" -> IP20, IP44
- "IP44 and IP65 options（from the front）" -> IP44 front, IP65 front, the spelling 50 other products already use
- "Front IP44, optional IP65" -> IP44 front, IP65. The sheet does not say the option is also from the front, so that is not added.

Only the wording "X and Y options" and "X, optional Y" splits. Two ratings can describe two parts of one fitting (a driver
compartment and an optic), which are not choices, so anything else stays one value as printed.

## Acceptance

- [x] A1 Add Product > Configure, a DuraGreen product from the 129: the IP Rating dropdown offers the two values separately, and
  picking either keeps the product matched.
- [x] A2 The chosen IP reaches the BOQ line, and only that one value.
- [x] A3 NPD (DuraGreen) shows the IP as two options. Saving the product unedited keeps them.
- [x] A4 Order codes unchanged: `audit_duragreen_codes.py --compare HEAD`, 0 round-trip failures, and its "choice not in the code"
  line shows 0 "CHANGED THE CODE". A code a person typed in, and a product in a naming structure, keep their code whichever IP is chosen.
- [x] A5 Prices unchanged, and every table other than `specsheets` fingerprints the same after the local backfill.
- [x] A6 A product with one IP ("IP44", "IP65 front") reads as before. A re-read of a product gives the two values.
- [x] A7 `--dry-run` writes nothing; a second real run changes nothing.

## Risks

- Saving a product in NPD drops an option the page does not send: covered by A3.
- Adding a field to `CONFIG_FIELDS` could multiply code combinations: `duragreen.vector_fields` only uses keys its codes state; A4 checks.
- Existing BOQ lines carry no IP, so they do not change.

- A sheet that later prints one IP leaves a two-value list in place, by choice: this also protects a list a person typed. A re-read's preview
  does not show it, so a person has to notice and remove the old list in NPD.
- An NPD tab left open across the backfill and saved afterwards sends back the old sentence. Reload open tabs around the live run.
- A code typed for one IP beside a printed row for the same configuration gives "the codes disagree" when that IP is chosen. A visible refusal,
  not a wrong code, and no code states an IP today.

- With no IP chosen, a line prints both values, as an unchosen CCT does, and Configure takes the first, also as for CCT. Consistent, so left.

## Budgets

Memory: no change. Special cases: +0, the rule reads wording, not a make. `app/main.py`: +6 lines.

## Rollback

Code: `git reset --hard <previous>` and restart. Data: `backfill_duragreen_ip_options.py --undo <file>` puts each product's printed text
back and removes the list, skipping any product whose IP changed or was priced since. No product it changed had a list before.

## Verification log

- 2026-11-04 feature-tester, local server from this worktree, real Playwright input. PASS: A1 (#572, #390, #361 in Configure, both values, product stays matched), A2 for a chosen IP (line, .xlsx, PDF show only that value), A4 (1,444 configurations on the 129, 0 differ by IP; audit `--compare HEAD`: no round-trip failure, no code changed), A5 (only `specsheets` differs; the undo file applied to a temp copy gives the pre-backfill md5 65e61a09), A6 for Add Product and re-read through the web card reader, A7 (`--dry-run`: to change 0), smoke (#79 quotation Grand Total 3,394,245.00, same as dc0197e, every cell identical), Vena IP20 1050 / IP66 1310 same as dc0197e, member PUT 403, `gate.py` 0 FAIL.
- 2026-11-04 feature-tester, open findings. (1) A3 partly FAILS: an unedited save keeps everything, but editing or removing an IP option in NPD leaves `fields.ip_rating` and the MECHANICAL `params` row on the old values, so a line with no IP chosen and the Library card print a value no longer offered; removing both brings them back as a value list. (2) #571 and #572, the owner's case, hold two different readings: "IP20 and IP44 options" (DURAGREEN SPEC) and "IP44 and IP65 options（from the front）" (MECHANICAL); which is right was not checked against the sheet (no network). (3) With no IP chosen the line is not blocked: "IP20 / IP44" on the line from Search, Frequently used and DuraGreen series; Configure silently takes the first value. CCT behaves the same. (4) NPD (DuraGreen) search for "IP44" finds nothing, as before this change: `dgVisible` does not search parameters. (5) Not from this change: an option or description holding `<img ... onerror=...>` makes the BOQ and Quotation PDF return 500, on dc0197e too. Not run: `gate.py --full` and memory (asked not to), the Match screen itself (its server route was tested, to avoid any AI request), a re-read from a real PDF.
- 2026-11-04 feature-tester, cleanup by id: BOQ #320 through the API, 17 export rows and files, 57 activity rows (4104-4160), users 300 and 301. #572 and #412 restored after NPD saves. `fingerprint_tables.py --compare`: 28 tables identical, 0 changed.
- 2026-11-05 feature-tester, re-check after the fixes, real Playwright input. PASS: A3 finding closed on #572 (IP65 front edited to IP66 front, one removed, both removed one save after the other and in one save: `fields.ip_rating`, `options.ip_rating`, the MECHANICAL row and a line with no IP all follow, and a later save does not bring it back; #412's plain field still edits); A4 typed code (CLAUDEIP3TEST01 stays with no IP, IP44 front and IP65 front in the code tester, Search and Configure; a code stating IP44 front is used only for IP44 front); a price adder of 250 on IP65 front stays in the list through a stand-in re-read that no longer prints it (card_row, build, merged, apply rolled back; without the adder the value drops) and through the next NPD save, priced 1250; owner's case in Configure (one value on the line); audit 126,487 "same code", 0 "CHANGED THE CODE"; backfill `--dry-run` to change 0; Vena #152 IP20 1050 / IP66 1310; #79 quotation Grand Total 3,394,245.00, all four exports 200. `vendor_product_naming` holds 0 rows, so the naming-structure case could not be run.
- 2026-11-05 feature-tester, open finding (low, re-read only): a re-read writes `fields.ip_rating` from the sheet alone while the list keeps other values. Sheet prints "IP20 and IP44 options" over a priced IP65 front: the list is IP65 front, IP20, IP44 and a line with no IP prints "IP20 / IP44". Sheet prints "IP44": the list stays IP44 front, IP65 front and the field says "IP44". The next NPD save corrects the first case's field. Seen once, not reproduced in 3 repeats: on the first Add Product of a run a CCT choice did not stick (line #6187); likely timing in the test.
- 2026-11-05 feature-tester, cleanup by id: code rows 67361-67363 through the API, BOQ #321 through the API, 4 export rows (303-306) and files, 43 activity rows (4161-4203), user 335. #572 and #412 restored from column-text snapshots, row md5 identical, SQL NULL kept. `fingerprint_tables.py --compare`: 28 tables identical, 0 changed.

- 2026-11-05 builder: after a re-read `fields.ip_rating` now follows the list, closing the tester's last finding. Proven by calling the re-read's merge with stand-ins for its two cases, not on screen. A typed code with each IP, fix on: kept in 248 of 250 (the other 2 are one product that does not use it with no IP either); fix off: lost in 224 of 250. Not run: a naming structure, as none exists (0 rows).

- 2026-11-05 builder, after the final review: `--undo` skips a product whose IP is priced, IP is no longer offered or accepted as a structure segment, and a value typed in place of an emptied list stays. Results are in the next line.

- 2026-11-05 /verify, final code: gate.py --full 0 FAIL, 0 WARN, worst memory scenario ask_index 187.4 MB; fingerprints before and after identical, including the profiler's own temporary user; audit_duragreen_codes.py --compare HEAD --cap 60: 2852 + 57 round trips ok, no failure category, 0 codes changed, "choice not in the code" 31,344 same and 0 changed (126,487 same at the default cap on the code before the last review). `--dry-run` to change 0. Proven by calling the code, not on screen: a structure naming IP is refused (422), `--undo` skips a priced product. Not run: a value typed in place of an emptied list; mobile-user-tester and ui-ux-reviewer (no layout changed; only the values in existing dropdowns); a naming structure (0 rows exist); a re-read from a real PDF (no network, stand-ins used). Earlier tester runs and their cleanup are above. TUTORIAL.md: no new Part; the lesson is in Part XV, 15.13.

## Next step

For the owner: (1) #571 and #572 hold two readings, DURAGREEN SPEC "IP20 and IP44 options" and MECHANICAL "IP44 and IP65 options (from the front)"; the dropdown offers the second, and the real sheet needs checking. (2) The live deploy needs a yes, and its backfill a separate yes with its dry run.

Separate, not from this change: unescaped text in an option makes a BOQ's PDF export return 500 (reportlab); it needs its own record.
