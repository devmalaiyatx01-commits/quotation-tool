# Release batch, 18 Nov 2026: every change of the 17-18 Nov chat that was not yet live

Branch: `release/batch-20261118`
Lane: feature
Status: live (e314921)
Asked: "push on main and oracle both, what all changes have been proposed in this chat", 2026-11-18
Approved: the request above, for the changes listed below, each of which the owner asked for; the Oracle deploy needs its own yes naming the commit
Special cases: +0
Memory: measured in Verification

## Why

Seven changes asked for on 17-18 Nov were built on their own branches, from different starting points, and some
touch the same code (Add Product's window, Search, the accessory add-on). They go out together. They are combined
here and proven together, rather than shipped one by one into code they were not tested against.

## Evidence

- `git merge-base main <branch>` for each was one of `f89d9d5`, `9b65fe6`, `216098e` or `ef5047a`. All are older than
  `main` (`4494811`), which since then gained the driver question, NPD Rules, environment tags and parameter groups.
- The latency branch's repo-reviewer report of 17 Nov lists 6 findings not yet fixed. Its feature-tester stopped at
  the usage limit.
- The UI branch has no feature-tester, ui-ux-reviewer or repo-reviewer evidence yet. The DuraGreen details branch
  lacks A4, A7, A9 and A10.

## Scope

In, each with its own record:

| Change | Record |
|---|---|
| Remove 21 one-off scripts | `2026-11-17-remove-one-off-scripts.md` |
| DESIGN.md | `2026-11-17-design-md.md` |
| An admin can delete an NPD product BOQs use (retired; exports unchanged) | `2026-11-18-npd-delete-on-boq.md` |
| Removing an accessory takes it off every product | `2026-11-18-accessory-remove-everywhere.md` |
| Faster Configure and Search | `2026-11-17-configure-latency.md` |
| DuraGreen Type it in: details by typology | `2026-11-17-duragreen-type-it-in-details.md` |
| BOQ editor grid and DESIGN.md phases 2-5 | `2026-11-17-boq-editor-grid.md`, `2026-11-17-ui-phases-2-5.md` |
| Nomenclature shows library accessory codes, as NPD does | `2026-11-18-nomenclature-library-codes.md` |
| NPD: several dimension drawings per product (and Replace on the line diagram works again) | `2026-11-18-npd-more-dimension-drawings.md` |
| NPD: any parameter can be removed from a typology, and from every product of it | `2026-11-18-npd-delete-any-parameter.md` |

Out: the two DuraGreen data steps (`backfill_duragreen_ip_options.py`, `backfill_duragreen_spec_values.py`), which
each need their own yes. No migration; no data step.

## Design

Each branch was committed and cherry-picked onto `main` (`4494811`) in this order: scripts, DESIGN.md, NPD delete,
accessory removal, latency, DuraGreen details, UI. Only the UI met a textual conflict: both sides added CSS at the end
of `style.css`, and both are kept. The open findings of the latency review are fixed on this branch, and the whole is
then proven by the feature-tester, the ui-ux-reviewer and the repo-reviewer.

## Acceptance

- [x] B1 Every included record's acceptance checks hold on this branch (feature-tester, real input). Exceptions, each
  in its own record, for the owner to accept by name before the deploy:
  1. latency A4 - Search takes 223 ms, against the 150 ms target (main takes 1,354 ms);
  2. DuraGreen A4 - a BOQ line's description has no power or control, which is today's behaviour for every parsed
     brand;
  3. grid A5 - the fold is remembered for the BOQ last opened only, as live since 28 Oct;
  4. UI B7 - list rows whose name wraps are 58 px;
  5. grid A7 / UI B9 - the mobile-user-tester's report was not waited for (owner: "do as early as possible").

  (Corrected 18 Nov evening: B1 was first ticked before the grid's A2-A5 and the UI record's B7-B8 had evidence on
  this code. The release-manager stopped the deploy on that; see the log.)
- [x] B2 Main's live features still work here: the driver question, NPD Rules (accessories follow rules), environment
  tags, parameter groups.
- [x] B3 Exports of BOQ #79 equal `main`'s, cell for cell.
- [x] B4 ui-ux-reviewer and repo-reviewer: no blocking finding left.
- [x] B5 gate.py --full: 0 FAIL. Final run (gate.py --full: 0 FAIL, 1 WARN (grid escaping, answered); worst memory ask_index 187.3 MB, run on the final commit against live's `ef5047a`). First run (1 WARN, the grid's escaping, answered below); worst memory scenario ask_index 187.6 MB
  against a 186 MB baseline. Fingerprints: identical after the checks, apart from the three link timestamps explained
  below.

## Risks

The UI changes the most-used screen. "Cards" gives the old line view back.

## Rollback

`git reset --hard 4494811` on the VM and restart. No data.

## Verification log

- 18 Nov, two more changes asked for during verification were added: Nomenclature library codes and NPD dimension
  drawings. Each has its own record and its own tests; both kept the CSS of both sides at the end of `style.css`.
- The UX review (stopped at the usage limit) found the phone fold-out's Manage items unreadable: the drop-down's dark
  text on the dark menu. Fixed, and checked on a 390x844 screenshot. The script's contrast figure was misled by the
  flattened wrapper's white background, so the screenshot is the evidence.
- Escaping WARN (40 interpolations in the grid markup): answered in `2026-11-17-boq-editor-grid.md`; the repo-reviewer
  confirms each.
- Line diagram Replace ("unable to replace the line diagram"): reproduced on `main` with `scratchpad/dims/replace_test.py`
  (real file chooser + Save on #765). On main the form and the BOQ/export kept the old drawing, because an uploaded
  "Line Diagram" sat beside the parser's "Additional Diagram 2" and the readers took the older one. Fixed by `715fd1e`:
  a replacement takes the main drawing's place. #765 was restored by id and compared equal. A read-only check on live
  found 4 NPD products holding an unused uploaded drawing (#289, #1524, #1525, #1526). After the deploy the owner
  uploads those drawings again; no data step.
- repo-reviewer (9 findings), fixed in `e8206fc`:
  1. grid undo could change another BOQ (HIGH); each step now carries its BOQ.
  2. the NPD list read approval one row at a time.
  3. a drawing's add/remove was not logged.
  4. a drawing number could be reused after a removal.
  5. removing a drawing did not count as a spec change.
  6. the main drawing printed twice on the generated sheet.
  7. "Add and add another" appeared in quick BOQs.
  8. a retired product's grid row still offered Configure.
  9. the retired filter hid products with no status.

  Left, low: other lists (List Pricing mapped rows, pricing master, accessory catalogue, Nomenclature counts) still
  count a retired product. They neither price nor configure it.
- ui-ux-reviewer: the blocking findings are fixed in `e8206fc`:
  1. opening Add Product on Configure as the remembered tab threw, because it was built before its state existed.
  2. the phone grid showed one word of the description.
  3. the DuraGreen Type it in switch sat on the value.
  4. the focus ring was invisible on the dark header.

  Also fixed: muted text contrast, the cool-palette leftovers, new drawings previewing before save, Nomenclature's
  "+N more" opening in place, Esc closing a window, a product opening at the top of its form, "Add to area" wording,
  44 px phone menu items, and the phone toolbar no longer pinned. Left (not blocking): removing the "Add floor" panel
  and shortening the Find & Jump paragraph.
- `41c2e41` (delete any parameter) cherry-picked as `5d43fe3` with no conflict; its own tests ran 3 times on its branch.

18 Nov, afternoon, coordinator, on this branch (server 8040; `main` on 8041; same local database):

- **The fixes, with real input.** `scratchpad/batch/verify_fixes.py`, 21 checks, all pass:
  - grid undo stays in its BOQ, and each BOQ keeps its own history (Ctrl+Z, then Ctrl+Y);
  - a retired product's row cannot be configured, by cell or by Ctrl+Enter;
  - Add Product opens on Configure without an error, and Esc closes it and the DuraGreen window;
  - the focus ring shows on the header;
  - a product opens at the top of its form;
  - new drawings preview before saving, are numbered 2, 3, then 3, 4 after a removal (never reused), and both saves are
    logged before and after;
  - the DuraGreen switch sits beside its value;
  - the description column is 224 px wide on a 390 px phone.

  It found one more defect, now fixed: the first undo fix threw away another BOQ's undo history when Ctrl+Z was pressed
  in this one. Undo now takes the newest step of the BOQ on screen and leaves the rest.
- **The data tester's open items.** `scratchpad/batch/verify2.py`, all pass:
  - a product on no BOQ is erased;
  - on a retired product's row, the grid menu shows "Change in the Configurator" disabled with the cards' reason
    (before, it was hidden; the record asks for it disabled with the reason), while on a configurable row it works;
  - Cancel on "remove everywhere" changes nothing.

  The tester's other FAILs were its own expectations:
  - the question named 5 takers, the fifth being the tester's own product;
  - `params` changes on the 4 Vena products, which is the printed accessory line.

  `relabel_check.py` (rolled back) shows that removing #59 everywhere leaves every other accessory's price and code on
  those products. The one that is relabelled ("(7mm X 17mm)" dropped once it is the only U-shape) keeps its price,
  because library prices follow the part.
- **B2.** The four live features' own tests, re-pointed at this branch, all pass: the driver question (7 checks), NPD
  Rules (25), environment tags (6), parameter groups (8). Delete any parameter passes here too (11).

  The environment-tag test first failed: it renames products inside the loaded list. The new same-name cache keys on the
  list object, so it missed the rename. The app never renames in place: every change to the library replaces
  `state.specsheets` (6 places). With the test handing over a new list, it passes.
- **B3.** `exports_compare.py`: BOQ #79's BOQ and Quotation .xlsx are identical to `main`'s. 4,561 and 6,274 cells, the
  same merged cells, and the same pictures per sheet.
- **Configure against `main`.** `configure_compare.py`: options and descriptions are identical for 75 Wall Wash,
  DuraGreen Downlight, NYX -Midi and an Exenia projector. Matching products are identical (same ids and order) apart
  from `option_words`, which the latency change takes out of Configure's matches by design.
- **Data.** The two testers stopped at the usage limit without cleaning up. Their rows were removed by the ids they
  recorded (`scratchpad/ids.json`, `ftb/ids.json`): 6 users, 13 permissions, 8 BOQs with 7 floors, 8 areas, 27 rows and
  8 exports, 10 test products, 1 link, 1 price and 107 activity rows. The 8 export files went too.

  Two real rows had moved only in their timestamps:
  - part #59's `updated_at`, from my Nomenclature test's code edit and restore. It was put back to live's value, which
    gives the saved hash exactly.
  - NYX -Mini's three accessory links #457-459, deleted and re-added by id when its retire was tested (first by my
    delete-on-BOQ test, before the saved fingerprint, then by the tester). They now carry the 6 Oct originals read from
    live.

  Against `fp_before`, accessory_links differs only there; the other 27 tables are identical. Every check since then has
  left all 28 tables identical.

18 Nov, ~17:20 IST: `e91bcb2` was merged to `main` and pushed, and the owner said "Yes, deploy e91bcb2". The
release-manager then stopped before contacting the VM. Four included records were not `ready`, the grid's A2-A5 and
A7 had no evidence on this code, and there was no release note. That stop was right. What followed:

- The grid's A2-A5 and the UI record's B1-B3, B5, B7 and B8 were run again on the final code, using the UI tester's
  own scripts with a fresh fixture (see those records).
- The mobile-user-tester ran on the grid (grid A7, UI B9); its result is in the grid record.
- The repo-reviewer reviewed the three commits made after its first review (`1358871..e91bcb2`). All 9 earlier
  findings hold as fixed, and nothing deletes a price. It found these, all fixed and proven:
  - 3 MEDIUM, in the parameter delete: retired products, price rows keyed on the parameter, and code segments and
    rules. See that record's A5 and `scratchpad/batch/pdel_guards.py`.
  - LOW-MEDIUM: an undo step took the BOQ that was open when its save returned. It now takes the one the edit was
    made in.
  - Two older undo weaknesses are fixed as well: a held Ctrl+Z ran saves at once, and a step on a deleted row
    blocked every undo before it. Proven with real keys by `scratchpad/batch/undo2.py`.
  - LOW: the two checks for "removes a drawing" disagreed; now there is one rule.
  - LOW: a permissions comment was out of date.

  The review fixes were re-checked on this branch: `verify_fixes.py` (21), `verify2.py` (9) and the parameter-delete UI
  test (11) all pass.
- Four test servers the stopped testers had left running (ports 8030-8032 and 8036, with code from before the fixes)
  were stopped.

18 Nov, 12:54-12:58 UTC (18:24-18:28 IST), release-manager: **deployed `e314921` to the VM** on the owner's "Yes, deploy
e314921" (release note `docs/releases/2026-11-18-release-batch.md`). Code only: no migration, no backfill, no pip.
- Backup `db_20261118_125404.sql` ("dump complete") and `files_20261118_125404.tar.gz`; available memory never under
  222 MB during it, service active throughout.
- Switch 12:57:11 UTC, fast-forward `ef5047a..e314921`; login 200 after 21 s, also through `https://quotes.example.com`.
- `static/app.js` `79bab9e5...c593` and `static/style.css` `a81b8f7a...44a2`: release blob = disk = served, from the VM
  and from outside. `app/main.py` and `templates/index.html` on disk = their blobs.
- New `GET /api/npd/duragreen/details` with no session: 401. Journal re-read at 12:58:31: 12 requests, all the checks'
  own, 0 5xx, no error lines, no out-of-memory line.
- **Fingerprints: all 28 tables identical**, and no activity row after id 3303.
- Memory: 380 MB before, 255 MB 80 s after (workers 133 and 131 MB), 413 MB available.
- Output: `~/release-batch-20261118/` and `~/fingerprint_{before,after}_2026-11-18d.txt` on the VM.

## Next step

Live as `e314921` since 18 Nov 12:57 UTC (see the log). Earlier plan: merge to `main`, push, then the owner's yes on the
commit for the VM (RELEASE.md), all done. After the deploy the owner uploads
the line diagrams of #289, #1524, #1525 and #1526 again (see above). Follow-ups for the owner: power and control on a
BOQ line's description (DuraGreen A4); Search's last 73 ms (latency A4); the low review item (other lists counting a
retired product).
