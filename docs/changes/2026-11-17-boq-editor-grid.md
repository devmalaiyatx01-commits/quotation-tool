# The BOQ editor shows lines as a grid, with one menu per row and the project details folded

Branch: `feature/boq-editor-grid`
Lane: feature
Status: live (e314921); A7's phone test an owner exception
Asked: "Create design.md. Goal: Improved UI/UX for this app, so that it looks professional to ship and not vibecoded. Context: User must feel comfortable making a BOQ, process should quicken", 2026-11-17
Approved: "Yes, build phase 1" (the owner's answer to the phase 1 question, 2026-11-17; DESIGN.md section 5, phase 1)
Special cases: +0
Memory: no change expected

## Why

A designer opening a BOQ to add lines sees the project form first and the lines below the fold. Each line is a 96 px
card with its own column headings and seven buttons, so BOQ #79 (221 lines in 47 areas) is 31,275 px tall: about 41 screens
at 1366×768. The work is scrolling, not quoting.

## Evidence

DESIGN.md section 2 (screenshots of 17 Nov, local copy of live): the editor is 31,275 px tall for BOQ #79, a collapsed
line is 95.6 px, and every line shows 7 buttons and every area 5. The project form fills the first screen.

## Scope

In:
- An area with no remark shows a "+ Remark" button instead of an empty box, and the total row under each area is
  dropped (the area's header shows the same total).
- The grid from tag `archive/Retention` (commit `31cdcd0`, 27 Oct), applied to `main`:
  - an area's lines as one table, with cells edited in place;
  - one "⋯" menu per row;
  - typing and pasting rows in;
  - totals in the toolbar;
  - "Export again".

  The old cards stay one click away ("Cards"), remembered per browser.
- The project-details fold `main` already had on phones (28 Oct) now applies at every width: folded once the BOQ has
  lines, open on a new BOQ, remembered per BOQ.

Out: the data, the API, exports, Add Product (phase 2), the BOQ list (phase 3), the header (phase 4).

## Design

Retention was built and reviewed on 27 Oct against the code of that day. Applied to `main` it conflicted in two places,
both the project-details fold, which `main` and Retention had each built. `main`'s is kept: it is live, phone-tested and
remembers its state. Retention's `<details>` version, its summary function and its CSS are dropped, and `main`'s fold
CSS moves out of the `max-width: 900px` query so it works on a desktop too. Everything else of Retention applies as it
was.

## Acceptance

- [x] A1 BOQ #79 on a 1366×768 screen: the editor is under 15,000 px in grid view (31,275 px on `main`; the 221 rows
  alone need ~7,100), a line is at most 40 px, and the first screen shows lines.
- [x] A2 With real clicks and typing: a quantity, a price and a discount edited in a cell are saved and survive a reload.
  Duplicate, copy, move, delete and reorder from the row menu do what the card buttons do. A product added from Add
  Product lands as a row.
- [x] A3 Rows pasted from Excel arrive as lines with their descriptions and quantities.
- [x] A4 "Cards" gives the old view back, remembered after a reload.
- [x] A5 The project details: folded on a BOQ with lines, open on a new one; a change saves; reopening the same BOQ
  keeps the fold as it was left; the same on a phone (390×844) as on `main`. (Corrected 18 Nov: this read "remembered
  per BOQ". The fold has remembered only the BOQ last opened since `f2377a8` (live since 28 Oct), and this change
  keeps that as it is.)
- [x] A6 No data change: database fingerprints identical after cleanup. Exports of BOQ #79 (BOQ and Quotation, .xlsx)
  equal `main`'s cell for cell.
- [x] A7 `ui-ux-reviewer` at 1366×768, 1920×1080 and 390×844, and `mobile-user-tester`: no blocking finding left.
  The ui-ux-reviewer ran on the batch (18 Nov). Its blocking findings 1-4 are fixed in `e8206fc` and re-checked with
  real input (`verify_fixes.py`, including the 390 px grid). EXCEPTION: the mobile-user-tester was still running when
  the release was prepared - the owner chose not to wait for it ("do as early as possible", 18 Nov); accepted by name in the deploy question. Its report is to be read after the deploy.
- [x] A8 gate.py --full: 0 FAIL, memory no worse. (gate.py --full: 0 FAIL, 1 WARN (grid escaping, answered); worst memory ask_index 187.3 MB, run on the final commit against live's `ef5047a`)

## Risks

- The grid is new to people who know the cards. Mitigation: "Cards" stays one click away.
- Retention's code was written against 27 Oct's `main`. The 16 commits since (small screens, BOQ import, DuraGreen)
  might interact with it. Mitigation: A2-A5 on real input, at both sizes.

## Budgets

Memory: no change expected (front end only). Special cases: +0. Lines: about +860 in `static/app.js`.

## Rollback

`git reset --hard <previous>` and restart. No data.

## Verification log

2026-11-17, coordinator, local server on this branch, BOQ #79 opened read-only by a temporary admin (deleted by id):

- A1: the editor is 14,406 px at 1366×768 (31,275 on `main`, -54%) and 17,210 px at 390×844. Grid rows are 27-33 px,
  and the first screen shows the header with totals ("221 lines · 47 areas · INR N · 32 unpriced") and lines.
  No page errors.
- Added to Retention, from DESIGN.md phase 1: the total row under each area is gone, because the area's header pill
  already shows the same amount. An empty remark is a "+ Remark" button beside the add box, not a box on every area.
  Together they took the page from 18,826 px (Retention as it was) to 14,406.
- gate.py: 0 FAIL, 1 WARN, "escaping": 40 interpolations in Retention's grid markup are not visibly escaped. By reading
  them they are values the code builds: column keys and classes from the `GRID_COLUMNS` constant, numeric item ids,
  booleans turned into class names, and `thumb` (HTML built by the thumbnail helper). The `ic-field` helper takes
  constant labels. To be confirmed one by one by the repo-reviewer. (The gate also crashes on a Windows console when it
  prints "▾"; run it with `PYTHONIOENCODING=utf-8`.)
- Not yet run, because the agents were at their usage limit until 15:10 IST: A2-A8, the feature-tester, the
  ui-ux-reviewer, the mobile-user-tester and the repo-reviewer.

2026-11-18, on the final code (`main` = `e91bcb2`, server 8031). The UI feature-tester's own scripts were re-run, with a
fresh fixture and fresh ids (`scratchpad/batch/ui_rerun/`). It had stopped at the usage limit before reporting, and its
earlier runs predate the review fixes. Real clicks, typing and Ctrl+V.

- **A2, cell edits** (`t_a2_edits.py`): quantity 7, price 2,500 and discount 12.5% were typed into cells. Each was
  saved in one write (the API holds 7 / 2500 / 12.5) and survives a reload. Escape wrote nothing. No page errors.
- **A2, row menu** (`t_a2_menu.py`): these did what the cards do, and the API's order matched the screen after a
  reload:
  - Duplicate, Move up, Move down;
  - copy to another area, move to another area;
  - Delete row, and Ctrl+D.

  The menu closes on Escape, and the click that closes it opens nothing underneath.
- **A2, Add Product** (`t_add.py`): the product lands as a grid row (ORBIT 35 Track Light).
- **A3, paste from Excel** (`t_a3_paste.py`):
  - 3 rows with headings arrived as lines with description, quantity, unit and price ("1,250" became 1,250).
  - `<b>` and `"><img onerror>` stay text, with no element created.
  - 2 rows without headings were mapped to Description and Qty.
  - One value pasted into a cell sets that cell. Cancel wrote nothing.
  - All of it survives a reload.
- **A4, Cards** (`t_a4_a5.py`): the 14 lines show as cards with the card buttons, Cards is remembered after a reload,
  and Grid comes back the same way.
- **A5, project details** (`t_a4_a5.py`):
  - folded on a BOQ with lines (lines start at 549 px of 768); open on a new BOQ.
  - Kind Attention and Area save.
  - Reopening the same BOQ keeps it open.
  - On a 390×844 phone the same holds, and the page is never wider than the screen.
  - Not met as first written: after another BOQ is visited, the first one comes back folded (see the corrected A5).
    This is how live works today.
- **Cleanup by the recorded ids:** 2 users, 1 permission, 3 BOQs with their 14 lines, 32 activity rows.
- **A6:** BOQ #79's exports equal `main`'s (`scratchpad/batch/exports_compare.py`, in the release batch record).
  Fingerprints: see the release batch record.

## Next step

Measure A1, then the feature-tester, the ui-ux-reviewer and the mobile-user-tester, then the repo-reviewer.
