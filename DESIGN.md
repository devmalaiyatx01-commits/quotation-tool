# DESIGN.md: how the Quotation Tool should look and behave

This document sets the target for the app's UI and UX: what to change, in what order, and the rules every screen follows
from now on. The goal is an app that looks like a product a company ships, not a tool that grew screen by screen. A
designer should feel at ease building a BOQ, and it should be faster.

It is a plan, not a record of what exists. Each phase below becomes its own change record (`docs/changes/`), its own
branch, and gets the owner's yes before it is built (DEVELOPMENT.md, feature lane).

## 1. What we measure

"Feels professional" and "faster" are vague, so every phase is held to numbers taken from the real app with real input
(Playwright clicks and typing, the `feature-tester` method):

| Measure | Today (17 Nov 2026, local copy of live) | Target |
|---|---|---|
| Height of the editor for BOQ #79 (B-7, Hauz Khas: 221 lines in 47 areas) | 31,275 px, about 41 screens at 1366×768 | under 15,000 px (the rows alone need ~7,100) |
| Space around each area's lines (header, add box, remark, padding) | about 200 px | under 120 px |
| Height of one line, collapsed | 96 px, plus a remark box per area | 40 px |
| Buttons visible on every line | 7 (+, Edit, Duplicate, Copy/Move, ↑, ↓, Delete) | 1 menu, actions on hover/selection |
| Buttons visible on every row of the BOQ list | 5 (Open, Duplicate, + Branch, Export, Delete) | 0: the row opens it, one menu for the rest |
| Clicks and keystrokes to add 10 products to an area | measured before phase 1 | half |
| Distinct colours in `static/style.css` | 182 hex values | the tokens in section 4 (about 20) |
| Distinct font sizes | 26 | 7 |
| Distinct corner radii | 15 | 3 |

A phase is done when its numbers are measured again and written in its change record.

## 2. What the screens show today

Seen in screenshots of the local server, desktop 1366×768 and phone 390×844:

- **The editor puts the form before the work.** The top half of the first screen is project name, code, area, client,
  date, kind attention, made by, discount, currency and an "auto-saved" note. The BOQ's floors and lines start below
  the fold. Someone opening a BOQ to add lines scrolls past this every time.
- **Lines are cards, not rows.** Each line is a 96 px card with its own column headings (REF NO., DESCRIPTION, FINISH,
  WATTAGE…) repeated on every card, then a row of seven buttons. A BOQ cannot be read the way the designer thinks of it,
  as a table, and the buttons are louder than the data.
- **Destructive actions are everywhere and red.** Delete Floor, Delete Area and Delete on every line are filled
  red buttons. Red draws the eye, so the screen's loudest elements are the ones nobody wants to press.
- **Every area repeats the same toolbar:** + Add Product, + Blank Row, + Accessory Row, + Add Driver, Delete Area,
  plus an always-open "Remark for this area" box with a sentence of explanation.
- **Explanations live on the screen.** "Find & Jump" carries a four-line paragraph. "Reference pictures" and the remark
  box explain themselves in place. Help text written for the first day stays for the hundredth.
- **The BOQ list is a table of buttons.** Five buttons per row make each row about 108 px tall. "Open" is a button
  although the whole row could open it. Delete sits beside Open.
- **The header is crowded and mixes audiences:** 9 tabs (Specsheet Library wraps to two lines), "Preview as…", the
  signed-in email (wrapping) and Logout. Tabs a designer uses daily (BOQs, Match, Ask) sit beside admin screens
  (Nomenclature, Activity, People).
- **Add Product is the strongest screen.** Picture cards, brand and typology chips with counts, and "Frequently used"
  first. It is the model for the rest: pictures, counts, one clear choice.
- **Phone:** the editor stacks the same buttons into wrapping rows (Add Area/Room and Delete Floor; four area buttons on
  three lines). It works, but it reads as a desktop page squeezed down.

Work already done and set aside: tag `archive/Retention` (commit `31cdcd0`, 27 Oct) built "BOQ grid: areas as
spreadsheets, one row menu, typing and pasting rows in". Phase 1 starts from it, rebased on `main`, rather than from
scratch.

## 3. Principles

1. **The BOQ is a table.** Designers and clients read a BOQ as rows and columns, and the export is a spreadsheet. The
   editor shows it the same way.
2. **One primary action per area of the screen.** One filled dark button where the eye should go ("+ Add product" in an
   area, "+ New BOQ" on the list). Everything else is secondary, ghost, or in a menu.
3. **Destructive actions are quiet and confirmed.** Delete lives in a menu, is red only as text, and is followed by an
   undo toast ("Line deleted · Undo") rather than a modal wherever the server can restore it.
4. **The keyboard works.** Enter moves down, Tab moves across, `/` focuses search, Esc closes a window, and arrow keys
   move in pickers. A designer adding 30 lines should not need the mouse for each.
5. **Show the state, not instructions.** "Saved" or "Saving…" beside the title replaces "Saved automatically when you
   leave a field". Help goes in a tooltip (ⓘ) or the Help tab, not in paragraphs on the working screen.
6. **Defaults are decisions.** The editor opens on the lines, not the form. Add Product opens with the cursor in search.
   A new BOQ starts with one floor and one area so the first line can be added at once.
7. **Same thing, same look, everywhere.** A button, chip, table, modal or empty state looks and behaves the same in
   BOQs, NPD, Match and Ask. Section 4 is the list; nothing outside it is invented per screen.
8. **Nothing about data changes.** These are presentation changes. Prices, exports, permissions and the API stay as
   they are, and every phase proves it (fingerprints, export byte-compare).

## 4. The design system ("Aperture", made explicit)

The app already has a palette in `:root` (`static/style.css`: ink, paper, brown, moss, danger, Archivo). It is a good
base: warm, calm and specific to a lighting studio. The problem is that 182 other colours were added beside it. This
section is the closed list.

**Colour.** Only the tokens below. A new need gets a new token here first.

| Token | Use |
|---|---|
| `--ink` #17140f, `--ink-soft` #3a352c | text, primary buttons, the header |
| `--muted` #7a7263 | secondary text, labels |
| `--paper` #faf7f2, `--paper-dim` #f1ece2, `--paper-raised` #ffffff | page, panels and table heads, cards and inputs |
| `--line` #e6ddce, `--line-strong` #d5c8ae | borders, row dividers |
| `--brown` #6b4a32, `--brown-wash` #ece1d4 | accent: selection, active chip, links |
| `--moss` #4b6b4f, `--moss-wash` #e7ede2 | success, "approved", saved |
| `--danger` #a33b2c, `--danger-wash` #f6e4de | errors, and destructive text in menus |
| `--gold-wash` #f5ecd9 | highlight: a line just added, a search hit |
| `--focus` (new) `0 0 0 3px rgba(107,74,50,.25)` | the one focus ring, for every control |

**Type.** Archivo throughout, with numbers in tabular figures (`font-variant-numeric: tabular-nums`) in every price,
quantity and total column so they line up.

| Step | Size / line height | Use |
|---|---|---|
| display | 24 / 30, 700 | screen title (BOQ name) |
| title | 18 / 24, 700 | section and modal titles |
| subtitle | 15 / 22, 600 | floor and area names |
| body | 14 / 20, 400 | default |
| table | 13 / 18, 400 | grid cells |
| label | 12 / 16, 600 | form labels, column heads (sentence case, not capitals) |
| micro | 11 / 14, 600 | chips, counts, badges |

**Spacing.** A 4 px scale: 4, 8, 12, 16, 24, 32, 48. Page gutters are 24 px on desktop and 16 px on a phone.

**Radius.** 6 px for controls, 10 px for panels, cards and modals, and 999 px for chips. Nothing else.

**Elevation.** Three levels: flat (borders only), raised (`0 1px 2px rgba(23,20,15,.06)`) for cards, and overlay
(`0 12px 32px rgba(23,20,15,.18)`) for menus, modals and toasts.

**Components.** One definition each, used everywhere:

- **Button:** primary (ink fill), secondary (white, line border), ghost (no border), danger (danger text, ghost; filled
  red only inside a confirm dialog). Heights 32 (default) and 28 (in tables). Icons are 16 px from one inline SVG set.
  No emoji, and no "+" typed as text where an icon belongs.
- **Row menu (⋯):** every per-row action that is not the main one: Duplicate, Copy to…, Move to…, Branch, Export,
  then a divider, then Delete.
- **Data grid:** a sticky header, 40 px rows (32 px "compact" by choice, remembered per person), zebra off, a
  hover-row tint, a selection checkbox column, and a bulk-action bar that appears at the top when rows are selected.
- **Chips:** filters and counts as in Add Product (`Downlight (32)`).
- **Modal:** a title, a body that scrolls, and a footer with secondary on the left and primary on the right. Esc closes
  it, it keeps focus inside, and a phone shows it full-screen.
- **Toast:** bottom-centre, 4 s, with an optional "Undo". It replaces `alert()` and in-page success notes.
- **Empty state:** one line on what this is, and the one button that fills it ("No lines yet · + Add product").
- **Saved indicator:** "Saved · 10:52" or "Saving…" or "Not saved: retry" beside the title.

## 5. The screens, in order of impact

### Phase 1: the BOQ editor (where the time goes)

- **Lines as a grid.** Each area is a table: thumbnail, Ref, Description, Finish, Wattage, Beam, CCT, Qty, Price,
  Disc %, Amount, ⋯. The column heads are shown once per area and stay sticky. A line is a 40 px row, and an accessory
  or driver row is indented under its product. The cells that can be typed into (Qty, Price, Disc %, Ref) are edited in
  place. Start from `archive/Retention`.
- **One row menu** replaces + / Edit / Duplicate / Copy-Move / ↑ / ↓ / Delete. Reordering is by dragging the row handle
  (the floors already work this way), or Alt+↑/↓. Edit opens the product's configuration, as Edit does today.
- **The area toolbar becomes one split button:** "+ Add product ▾" (Blank row, Accessory row, Driver in its menu). The
  area's remark is a "+ Remark" link that opens the box, shown open only when there is text.
- **Project details become a summary bar** under the title ("Interior · Rikesh · 3 Oct 2026 · INR · no discount ·
  Edit"). It opens to the full form, and is open by default only on a new BOQ.
- **A sticky footer** with line count, totals before and after discount and currency, plus Preview and Export. The
  header keeps Back, the title, the saved indicator and the ⋯ menu (Edit notes, Duplicate, Branch).
- **Find & Jump** keeps its outline and search. The paragraph becomes an ⓘ tooltip.
- **Keyboard:** Enter on a Qty cell moves to the next line's Qty. Ctrl+D duplicates the line. Delete on a selected row
  asks once, then shows an undo toast.

### Phase 2: adding products faster

- Add Product **stays open after adding** ("Added NYX -Mini to Gym · Add another"), with the quantity typed in the
  window, so ten products are ten picks rather than ten round trips through the editor.
- It opens with **the cursor in Search**. Up/down and Enter pick, Esc closes. The tab it opened on last time is
  remembered per person.
- **Recently added in this BOQ** is a row above Frequently used.
- **Paste from Excel** into an area (part of `archive/Retention`): rows of description and quantity become blank lines
  that can be linked to products afterwards, as Import BOQ does.

### Phase 3: the BOQ list

- The whole row opens the BOQ. Duplicate, + Branch, Export and Delete move into a ⋯ menu. Rows are 48 px.
- The filter chips (Made by me / anyone, Quick or Full) sit beside the search, which gets focus with `/`.
- Branches are shown as an indented, collapsible group under their parent, not as a ▾ beside the name.

### Phase 4: the header and navigation

- **Daily work on the left:** BOQs, Library, Match, Ask.
- **"Manage ▾"** holds the authoring and admin screens a person has rights to: NPD, Nomenclature, Activity, People.
- **On the right:** a person menu (their initials and name, not the email) holding Preview as…, Help and Log out.
  "Specsheet Library" is renamed "Library", so no tab wraps at 1366 px.

### Phase 5: consistency pass across NPD, Match, Ask and modals

- Replace every colour, font size and radius outside section 4 with its token (the gate can count them, the way it
  counts special cases).
- Replace `alert()`/`confirm()` with the toast and the confirm dialog.
- Every table uses the data grid, every window uses the modal, and every empty screen uses the empty state.

## 6. How each phase is checked

- **Before and after numbers** from section 1, taken with real input, not by setting `scrollTop` (a lesson from 26 Oct:
  see memory "UI tests use real input").
- **The `ui-ux-reviewer` agent** at 1366×768, 1920×1080 and 390×844, and the **`mobile-user-tester`** for phases 1-3.
- **No data change:** database fingerprints identical; an export of BOQ #79 as BOQ and Quotation (.xlsx and PDF)
  byte-compared, or cell-compared, with `main`.
- **Accessibility:** every control reachable by keyboard with a visible focus ring, text contrast at least 4.5:1 on
  its background, and tap targets at least 40 px on a phone.
- `gate.py --full`: 0 FAIL, with memory no worse (the grid must not keep a copy of every line in the DOM twice).

## 7. Out of scope

The export formats (the Excel and PDF a client receives have their own layout rules: `app/excel_fit.py`, export
names), the data model, permissions, pricing rules, and the login page (it changes only to use the tokens).
