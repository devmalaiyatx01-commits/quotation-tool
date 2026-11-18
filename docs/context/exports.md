# Context pack: exports

Read this when you are changing what a BOQ or quotation looks like as .xlsx or .pdf, or what goes
into one. Exports are what clients receive, so a wrong number or a missing picture here is the
most visible mistake the app can make.

## Where things are

| Export | Function | Library |
|---|---|---|
| BOQ .xlsx | `app/excel_export.py::export_boq` | openpyxl |
| BOQ .pdf | `app/pdf_export.py::export_boq_pdf` | reportlab |
| Quotation .xlsx / .pdf | `app/quotation_export.py::export_quotation`, `app/quotation_pdf_export.py::export_quotation_pdf` | openpyxl, reportlab |
| Consolidated (and every quick BOQ) | `app/consolidated_export.py::export_consolidated_boq`, `app/consolidated_export.py::export_consolidated_quotation` | openpyxl |
| Summaries | `app/boq_summary.py` | openpyxl, reportlab |

- **Routes**: `python scripts/maintenance/code_map.py app/main.py --routes --grep export`. Each
  route writes the file under `data/exports/` with a timestamped name and records a `BOQExport`
  row (a PDF preview does not). Eight export routes are in the activity table. The two
  consolidated ones are not, so those downloads go unlogged (found 2026-11-02, on the plan's
  list). The person's download is named by `app/main.py::_download_name`
  (`ILDL_<project>_<date>_BOQ.xlsx`).
- **Pictures: one choke point.** `app/excel_export.py::line_diagram_for` decides the picture for
  every row of every export, BOQ and quotation, Excel and PDF. Change picture behaviour there
  and nowhere else, or the exports will disagree with each other.
- **Fitting text**: `app/excel_fit.py` sizes rows and columns to their text (`app/excel_fit.py::text_pt`).
- **Quick BOQs** have no real floors. `app/main.py::_refuse_detailed_export` refuses the
  floor-by-floor exports for them, so only the consolidated pair applies.

## Rules that bite here

- **S.No autofill is never saved.** `app/excel_export.py::_autofill_sno` fills blank numbers in
  memory, and each route calls `db.rollback()` before its one intended write (the `BOQExport` row).
  A new export route copies that order exactly, or exporting silently edits the BOQ.
- **Totals are the contract.** A change to anything that feeds a price is verified by exporting
  real BOQs before and after and comparing every grand total (RELEASE.md, rehearsal).
- **Memory.** Exports are the heavy end of the app (measured 2026-11-02 on BOQ #79, 221 lines):
  BOQ .pdf peaks at 166 MB, quotation .xlsx at 162 MB, BOQ .xlsx at 155 MB, against a 147 MB floor.
  A picture-heavy change remeasures these (`memory_profile.py --only export_xlsx,export_pdf,quotation_xlsx`).

## How to check a change here

- Export BOQ #79 (the largest detailed BOQ locally) and a quick BOQ. Open the file and look at it:
  the gate cannot see layout.
- Compare grand totals before and after, for every local BOQ if the change touches prices.
- `python scripts/maintenance/gate.py --full`: the memory scenarios cover the three main exports.
- Delete the export files and `BOQExport` rows your checks created, by id.
