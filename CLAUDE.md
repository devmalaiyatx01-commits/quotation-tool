# CLAUDE.md — working in this repo

Read automatically at the start of every session. It stays short on purpose: it says
**where things are and which rules are non-negotiable**, and points elsewhere for detail.
Don't copy other docs into it. Link to them instead.

## What this is

The **Quotation Tool** for the studio's lighting design team. People upload manufacturer
specsheet PDFs, build a BOQ (floors → areas → items, or a quick BOQ), and export BOQs or
priced Quotations as .xlsx/PDF. FastAPI + SQLAlchemy + Postgres on the server, one
vanilla-JS single-page app in the browser. It is in production and people's work lives in it.

Explain the *why* in comments and docs (see "Documentation" below).

## Map

| Path | What |
|---|---|
| `app/main.py` | Every route, the permission guard (`_required_permission`, `AuthGuardMiddleware`), the activity-log route table. **About 9,500 lines: map it (`code_map.py app/main.py --routes --grep <word>`) and read ranges.** |
| `app/models.py` | Every table. |
| `app/database.py` | Engine, session, and `DATA_DIR`/`UPLOADS_DIR`/`IMAGES_DIR` (all under `data/`). |
| `app/*_export.py`, `app/boq_summary.py` | Excel/PDF exports. `line_diagram_for()` in `excel_export.py` is the single choke point for pictures. |
| `app/pdf_parser.py`, `app/profile_pictures.py` | Specsheet parsing and picture extraction. |
| `app/product_codes.py` | The only place a product code is assembled. Its structure and abbreviations are data, edited in NPD → Product codes. |
| `app/duragreen.py` | DuraGreen in NPD: how its products are read, and the only place a DuraGreen order code is found (from `vendor_order_codes`, never the studio's code tables). |
| `app/configurator.py` | Which details the Configurator asks per brand and typology, as decided in NPD - the one list both the Configure tab and the product form take. |
| `app/duragreen_sheet.py`, `app/duragreen_import.py` | Reading a DuraGreen spec sheet / web card (shared with the scripts under `scripts/catalogues/` and `scripts/backfills/`, which import it), and NPD's "add from its web page or a PDF": preview, then import. |
| `app/auth.py`, `app/google_auth.py` | PBKDF2 passwords, and Google SSO restricted to the company domain. |
| `static/app.js` | The whole front end. **About 23,000 lines: `code_map.py static/app.js --grep <word>`, never read it whole.** |
| `templates/` | `index.html`, `login.html`, `choose.html`. |
| `scripts/migrations/` | Schema changes. Idempotent, with `--dry-run`. |
| `scripts/backfills/` | One-off data fixes. |
| `scripts/catalogues/` | Vendor importers (Exenia, Duragreen, Lumenpulse, InHouse, drivers). Siblings import each other, so keep them together. |
| `scripts/pricelists/` | Decorative-brand INR price-list builders. |
| `scripts/maintenance/` | Audits, dedupes, thumbnail warmers, deploy helpers, and the change tools: `gate.py`, `code_map.py`, `audit_special_cases.py`, `memory_profile.py`, `fingerprint_tables.py`. |
| `docs/` | `changes/` (a record per change), `context/` (what to read per area), `quality/` (measured baselines), `releases/`, `plans/`. |
| `seed.py`, `create_user.py` | Stay at the root: `run.bat` and the docs call them by bare name. |
| `archive/` | Weekly reports and presentations. Not source; don't read. |

## Commands

```bash
run.bat                                    # real local DB, http://127.0.0.1:8000 (runs seed.py first)
run_dev.bat                                # sandbox, port 8001 - NOTE .env.development points at a stale DB
python scripts/migrations/<name>.py --dry-run   # always dry-run first
python scripts/maintenance/verify_repo.py  # compile every .py + check every markdown link
python scripts/maintenance/gate.py         # every mechanical check on this branch (--full adds memory)
python scripts/maintenance/code_map.py static/app.js --grep <word>   # find code without reading it
```

Run scripts **from the repo root**. Scripts under `scripts/` start with a small *repo-root shim*
that puts the root on `sys.path` and defines `_REPO_ROOT`. Keep it in any script you copy.
Build paths with `_REPO_ROOT / "data" / ...`, **never** `Path(__file__).parent`, because the
latter is `scripts/<dir>/`, not the root, and it fails silently.

`DATABASE_URL` and `SESSION_SECRET` come from the Windows user environment. Never read `.env*`.

## Non-negotiable rules

1. **Never deploy to the Oracle VM without explicit approval for that specific change.** Work
   locally first, always. Release steps: `RELEASE.md`; the server and its history: `FinalDeployment.md`.
2. **Never run load or scale tests against the VM.**
3. **Never install packages without approval.**
4. **Prices already entered must never vanish.** Any migration or backfill that touches prices
   gets a `--dry-run` reviewed first.
5. **Decorative brand pricing: one brand at a time, and only when asked.** Never carry one
   brand's rules over to another.
6. **Commit or push only when asked.** Read the target before you delete or overwrite anything.
7. **Treat errors as unacceptable.** Verify with evidence (command output, counts, a generated
   file) and never claim anything you haven't checked.

## How a change is made

Every change follows [DEVELOPMENT.md](DEVELOPMENT.md). In short:

- **A reason first.** A change record in `docs/changes/` (`/change` drafts one) holds the why,
  the evidence, numbered acceptance checks and the rollback. For a feature, data, schema or infra
  change, the owner says yes to it **before** any code is written.
- **One change per branch**, in its own worktree from `main`. `main` is what is live.
- **Done means proven**: `gate.py --full` with no FAIL, the `feature-tester` agent's evidence for
  each acceptance check, and the `repo-reviewer` agent's review answered. The quick gate runs on
  every commit (`.githooks/pre-commit`). Never skip it with `--no-verify` unless the owner says so.
- **Rules are data, not code**: no new `if make == "..."` in shared code (the gate counts them).
  Memory has a budget per scenario (`docs/quality/`).
- **Releases** follow `RELEASE.md`, prepared by the `release-manager` agent.
- **Context**: read the branch's record first, then one pack in `docs/context/`.

## How code is written here

- **Permissions** are decided centrally in `_required_permission(method, path)`. A new endpoint
  gets a line there, not a check inside the endpoint. Hiding a button is not a permission check.
- **Every destructive or important action is logged** through the activity route table in `app/main.py`.
- **Front-end HTML:** every interpolated value goes through `escapeHtml()`, or `attr()` inside
  attributes. No exceptions.
- **Migrations:** check whether the change already exists, apply it, print what happened,
  support `--dry-run`, and stay safe to re-run.
- **SQLAlchemy delete-orphan trap:** move a child by reassigning its parent
  (`room.floor = floor`), never by removing it from a list.
- **A heuristic may merge, never delete** (hard-won in `app/profile_pictures.py`).
- **There's no automated test suite.** `gate.py` checks what can be checked mechanically.
  Behaviour is verified with throwaway scripts in the scratchpad against real endpoints and data
  (the `feature-tester` agent's method), then every row and file they created is cleaned up by id.
- Match the surrounding style. Comments explain *why* and are written in plain English.

## Vendor products and their order codes (DuraGreen, and any brand after it)

Detail and steps: the `vendor-products` skill (any brand) and `duragreen-codes` (DuraGreen).

- **The vendor's own documents are the truth** (website, spec sheets, catalogue). Fetch all of it
  (the sitemap, not the menus), save every spec sheet under its own file name per upload (vendors
  reuse names across products), and never invent a code.
- **A code is only as sure as its least sure piece**: printed whole for this configuration >
  pieces printed for *this* product (its codes, its sheet's legend, its catalogue page) > *its own
  range's* code table > *its own codes' pattern* (its 3000K printed as 30, so its 2700K is 27) >
  anything else, which is **unconfirmed** and labelled so all the way onto the BOQ line. Where two
  sources disagree there is no code, and the reason names both.
- **Every product's own sheet is read**, wherever it lives: a sheet a person uploaded, or one the
  vendor's website no longer links, is read by the same reader as the website's. A sheet that
  labels its tables ("Recessed Trim", "Surface") or prints a 24V DC twin (`-24`) is quoted on that
  choice (mounting, voltage), never refused as "several versions".
- **A range's code table is that range's rule only.** DuraGreen's ACME table is the ACME
  downlights'; a step light, shelf light, pendant or outdoor fitting follows the codes printed in
  its own spec sheet and on its own catalogue page.
- **People choose from what is printed.** NPD shows every combination a sheet prints (a column per
  option: W, CCT, beam, driver ...), lets a person pick one as a single choice, shows every other
  combination's code and how sure it is, and takes a code the vendor sends with every option it
  orders - never pre-filled with a built guess.
- Vendor codes live in `vendor_order_codes` / `vendor_code_parts` / `vendor_code_rules` (and the naming
  structures people make: `vendor_code_structures`, `vendor_product_naming`), never in
  the studio's code tables. A re-read never deletes a row a person entered ("entered by ...").
- **Pricing (tentative, owner to confirm):** prices may later be keyed by code, so a wrong code is
  also a wrong price. Base price + adders per option stays the model until the owner confirms the
  vendor's price structure. A code load never touches a price.
- Verify with `python scripts/maintenance/audit_duragreen_codes.py --compare HEAD`: 0 round-trip
  failures, and a reason for every code that changed.

## Saving tokens

- The load order and the area packs are in [docs/context/README.md](docs/context/README.md).
- Grep or `code_map.py` before you read. For the big files, read line ranges, not whole files.
- **Don't open** `data/` (807 MB of runtime files), `SampleInputs/` (880 MB of PDFs),
  `SampleInputOld/`, `archive/`, or `*.pdf`/`*.xlsx`/`*.pptx`, unless the task is about that file.
- `TUTORIAL.md` runs to 7,600 lines. Use its table of contents and read only the relevant section.
- One task per session. Big plans and investigations go in the scratchpad, not the chat.

## Documentation

| Doc | Job |
|---|---|
| `DEVELOPMENT.md` | How a change is made: lanes, the lifecycle, gates, budgets, who does what. |
| `RELEASE.md` | How a release reaches the VM: prepare, approve, Part A, Part B, close. |
| `docs/changes/`, `docs/context/`, `docs/quality/`, `docs/plans/` | Change records, context packs, measured baselines, the framework's rollout plan. |
| `PROJECT_STATE.md` | Current-state orientation (its detail is from 2026-08; refresh is on the plan). Start here if you've lost the thread. |
| `README.md` | Features, from the user's side. |
| `HOW_TO_RUN.md` | Start, stop, and develop locally. |
| `FinalDeployment.md` | The live Oracle VM: deploys, the pending hardening list. |
| `DNS.md` | The address `quotes.example.com` (moved from DuckDNS 2026-10-22, the old name redirects): DNS, certificates, Caddy, the runbook and rollback. |
| `DataBaseSchema.md` | Tables and columns. |
| `TUTORIAL.md` | The course and the *why* behind decisions. **Update it when work teaches a real lesson**, in the relevant Part, and add a dated line to Part XVII's changelog. |
| `DriverRules.md`, `ConfigurationRules.md`, `pricingContext.md` | Domain rules. |

When a doc makes a claim about the code, check it against the code before repeating or editing it.
