# Quality baselines

Written for: whoever changes the app, and the owner, to see whether it is getting better.

Each number here was measured, with the command that measures it, so "better" and "worse" are
facts, not opinions. Remeasure after any change that could move one, and update this page in the
same branch.

## Memory (per app worker)

`python scripts/maintenance/memory_profile.py`. The baseline is [memory_baseline.json](memory_baseline.json),
recorded 2026-11-02 at `b970813` on the owner's laptop, BOQ #79 (221 lines, pictures on disk for
8 of its 8 products). Two runs agreed within 2 MB in every scenario.

| Scenario | Peak MB | Retained after | What it is |
|---|---|---|---|
| import | 147 | - | the floor: importing the app, before any request |
| boq_list | 155 | +8 | GET /api/boqs |
| boq_open | 155 | +8 | GET /api/boqs/79 |
| library | 163 | +16 | GET /api/specsheets (1.8 MB of JSON) |
| npd_products | 181 | +34 | GET /api/npd/products |
| export_xlsx | 155 | +8 | BOQ .xlsx |
| export_pdf | 166 | +11 | BOQ .pdf |
| quotation_xlsx | 162 | +8 | quotation .xlsx |
| ask_index | **186** | **+39** | the Ask tab's index of every product and BOQ |

What these say (each is a candidate change, with its own record, in the plan's phase 3):

- **The floor is most of it.** 147 of the 186 MB is paid before a request arrives, in each of the
  two workers. On the VM the workers were 143 and 182 MB (2026-09). Libraries that only a few
  routes use (PyMuPDF, reportlab, pdfplumber, openpyxl) are imported at start-up.
- **Ask and NPD keep memory for good.** +39 MB and +34 MB stay in the worker after one use: the
  Ask index is cached per discipline and never dropped (`app/assistant.py::index_for`), and the NPD
  list is the next largest.
- **Exports are not the worst.** The picture-cache fix of 2026-10-29 (TUTORIAL 9.4) already
  brought them down.

The rule (DEVELOPMENT.md 4): a scenario may not grow by more than 15 MB or 10% without a reason in
the change record. `gate.py --full` checks it.

## Special cases in shared code

`python scripts/maintenance/audit_special_cases.py` (and `--list <file>`). It is compared against
`main`, never against a stored number.

| 2026-11-02 | DuraGreen | Exenia | InHouse | Total |
|---|---|---|---|---|
| app/main.py | 35 | 2 | | 37 |
| static/app.js | 6 | 2 | 1 | 9 |
| app/configurator.py, npd.py, pdf_parser.py, pricing_master.py | 3 | 1 | 1 | 5 |
| **Total** | 44 | 5 | 2 | **51** |

Hard-coded record ids (`if id == 1530`): 0. The audit keeps it there.

## Size

| File | 2026-11-02 |
|---|---|
| app/main.py | 9,540 lines |
| static/app.js | 23,227 lines |
| static/style.css | 4,230 lines |

`gate.py` prints the growth on every branch.

## Found while measuring (2026-11-02)

- The two consolidated export routes are not in the activity table, so those downloads are not
  logged (docs/context/exports.md).
- NPD's brand list is written into app.js (`NPD_BRANDS`), not read from data.
