# NPD: several dimension drawings per product, printed together in the sheet's DIMENSION box

Branch: `feature/npd-more-dimension-drawings`
Lane: feature
Status: live (e314921)
Asked: "Also, under NPD allow user to add multiple images under dimensions, and not just 1. Context: Some specsheets have multiple images under the dimension clubbed over a same place. Main and Oracle both", 2026-11-18
Approved: the request above; it states the behaviour
Special cases: +0
Memory: no change expected

## Why

Some manufacturers' sheets print two or three drawings in their DIMENSION area: a front view and a section, for
example. NPD took one "Line diagram", so the generated sheet could show only one of them.

## Evidence

- The NPD form had one upload, `line_diagram`, saved under the label "Line Diagram". Every reader of a dimension
  drawing looks for one picture:
  - the generated sheet (`npd.pick_images`, then the DIMENSION box);
  - the BOQ line (`_line_diagram_url`);
  - a row's Dimension column choices (`_dimension_choices`);
  - the exports (`excel_export.line_diagram_for`).
- NPD products imported from PDFs carry their drawing as "Additional Diagram 2", the export's `LINE_DIAGRAM_LABEL`
  (134 products locally). A drawing uploaded in NPD as "Line Diagram" sat beside it, and the sheet kept printing the
  old one.
- 16 NPD products also carry an "Additional Diagram 3" from the parser. Rendered, it is not a drawing: it is an
  accessory collage on MIST 0808 3D (#68) and accessory photos on SERA 1910 TV (#765). So it must not be printed as a
  dimension.

## Scope

In:
- **NPD form:** under the line diagram, "More dimension drawings". It adds several files at once and shows each saved
  drawing as a tile, with × to remove it (removal applies on save).
- **Storage:** further drawings are saved as "Dimension 2", "Dimension 3", ... (`npd.dimension_order`).
- **The main drawing:** a new upload now replaces "Additional Diagram 2" (and an older "Line Diagram"), so the sheet
  prints the one just uploaded.
- **The generated sheet:** the DIMENSION box prints the main drawing and every "Dimension N" together, in a grid.
- **BOQ rows** of an NPD product may choose "Dimension drawing N" in their Dimension column, and every export prints
  the row's choice (`line_diagram_for`, by label).

Out: the parser's other pictures; manufacturer-PDF products (not NPD); the Excel layout of a Dimension cell (one picture
per row, the row's choice).

## Acceptance

- [x] A1 NYX -Mini (#330), browser, real file picks: two files are listed before saving, and saved as Dimension 2 and
  3. The main drawing is untouched. The product's answer lists them.
- [x] A2 Its generated sheet prints the main drawing and both new ones in the DIMENSION box (6 images on the page).
- [x] A3 Its BOQ Dimension choices are Line drawing, Dimension drawing 2 and Dimension drawing 3.
- [x] A4 Removing Dimension 2 in the form and saving leaves Dimension 3.
- [x] A5 #68 and #765, which already had a parser picture, print exactly the pictures they printed on `main`.
- [x] A6 No page errors; gate.py 0 FAIL.

## Risks

- A new main drawing now replaces the old "Additional Diagram 2" reference, which is what every export prints. So
  saved BOQ lines that show the line drawing print the new one, old quotations included. That is intended (it is the
  product's drawing), and the log records the pictures before and after. The old file stays on disk.
- The first save of an NPD product puts its pictures in order: product image first, then drawings. A product whose
  `image_path` pointed at its drawing (a replaced product image used to be appended last) shows its photo again as
  its thumbnail. This is a one-time correction.
- A drawing's number is never reused: it is the highest the product's files have ever had, plus one.

## Rollback

`git reset --hard <previous>` and restart. Drawings added meanwhile stay on their products and are ignored by the old
code.

## Verification log

2026-11-18, coordinator, `scratchpad/dims/ui_test.py` and `sheets.py` (branch on 8029, `main` on 8028): A1-A6 passed.
#330 was snapshotted and restored column by column (afterwards no column differs). The 2 files the app wrote were
removed by their recorded paths. 2 activity rows and users #444-445 were deleted. Also seen, not changed: on `main`, #68's
DIMENSION box prints a light-distribution curve, because its "Additional Diagram 2" from the PDF import is not a drawing.

## Next step

Into the release batch; deploy on the owner's yes.
