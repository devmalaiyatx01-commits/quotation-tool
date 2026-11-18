# How a change is made here

Written for: anyone, a person or a coding agent, about to change the Quotation Tool.

The app is in production. People's quotes and prices live in it, one person builds it, and the
coding agent that does most of the typing forgets everything between sessions. A request on its
own ("make the BOQ faster") is not a change. A change is **a reason you can point at, a check that
proves it, and a way back**. Every step below exists because skipping it once went wrong:

| What happened | The step that prevents it |
|---|---|
| Load and scale tests against the live server are risky | Budgets and local measurement (section 4) |
| A cleanup by `LIKE '%ZZ%'` deleted 350 real activity rows | Tests clean up by id (feature-tester) |
| A scroll test passed 13/13 while people could not scroll | Acceptance is tested with real input (step 5) |
| A release branched from a stale doc nearly rolled a deploy back | The VM's commit is read, not assumed ([RELEASE.md](RELEASE.md)) |
| Large uncommitted work rode along with unrelated releases | One change per branch, in its own worktree (step 3) |

## 1. Lanes: how much process a change needs

Process should match the risk. A typo does not need a rehearsal, and a price backfill does not get
by on a quick look.

| Lane | Examples | Owner approves before building | Verify | Ships |
|---|---|---|---|---|
| `docs` | docs, comments, agent and process files | no | quick gate | with the next release |
| `fix` | a bug with a known wrong result | no (the evidence is the reproduction) | full gate, feature-tester, repo-reviewer | release |
| `feature` | anything people will see or use differently | **yes**, the change record's design | full gate, feature-tester, UI agents if it is a screen, repo-reviewer | release |
| `data` | a backfill, an import, anything near prices | **yes**, and the `--dry-run` output too | fingerprints before and after, dry-run reviewed | release, then a **separate yes** for the live run |
| `schema` | a migration | **yes** | migration rules (CLAUDE.md), rehearsed on a scratch copy | its own release, never with a risky feature |
| `infra` | the VM, Caddy, systemd, DNS | **yes** | a tested rollback | by hand, recorded in [FinalDeployment.md](FinalDeployment.md) |

When in doubt, take the stricter lane.

## 2. The lifecycle

Each change goes through eight steps. The change record ([docs/changes/](docs/changes/README.md))
carries it through all of them. It is the one place that says what is being done, why, and what
state it is in, so that the next session, the reviewer and the owner all read the same thing.

1. **Ask.** The request, in the owner's own words, goes into the record. A change nobody asked for
   is not made, even a small cleanup. Note it under "Next step" or in the plan instead.
2. **Record.** Copy [docs/changes/TEMPLATE.md](docs/changes/TEMPLATE.md), or run `/change`, which
   has the `change-planner` agent draft it. Fill in why, evidence, scope (in *and* out), design,
   acceptance, risks, budgets and rollback. For a `feature`, `data`, `schema` or `infra` change the
   owner reads it and says yes, and the yes is quoted in the record ("Approved: ...") **before any
   code is written**. Changing the plan is cheap now and expensive later.
3. **Branch.** One change per branch, each in its own worktree from `main`. `main` is what is live,
   and the main folder stays on it:
   ```bash
   git worktree add ../DTT-<slug> -b <lane>/<slug> main
   ```
4. **Build** the smallest change that meets the acceptance criteria:
   - **Reuse before you write.** `python scripts/maintenance/code_map.py <file> --grep <word>` and
     grep first. This repo already has one home each for product codes (`app/product_codes.py`),
     pictures (`line_diagram_for()`), permissions and logging.
   - **Rules are data, not code** (section 5). No new `if make == "..."` in shared code.
   - **New features get their own module** (`app/<feature>.py`), with thin routes in `app/main.py`.
   - **Change the docs in the same branch** as the code they describe: README for people, the
     context pack for agents, and TUTORIAL.md when there is a lesson.
5. **Verify.** Run `python scripts/maintenance/gate.py` while building and `--full` before review
   (section 3). The `feature-tester` agent then proves each acceptance criterion against a real
   local server with real input, and cleans up by id. For a screen, `mobile-user-tester` and
   `ui-ux-reviewer` check it as well. Evidence (command output, counts, a screenshot path) goes in
   the record's verification log. "It works" with no evidence counts as not verified.
6. **Review.** The `repo-reviewer` agent reads the diff against this repo's rules. Each finding is
   fixed, or answered in the record with a reason. The owner reads the record and the diff summary.
7. **Release.** Commits happen when the owner asks. The `release-manager` agent prepares the
   release by following [RELEASE.md](RELEASE.md), and nothing reaches the VM without the owner's
   yes to that release.
8. **Close.** Set the record's status to `live` with the commit. `main` = GitHub = VM. Delete the
   merged branch and remove its worktree. Update memory and `PROJECT_STATE.md` if the shape of the
   app changed, and add a lesson to TUTORIAL.md if there was one.

A change that turns out to be wrong is set to `dropped` with the reason, and stays in
`docs/changes/`. Knowing what was tried and why it failed is worth keeping.

## 3. Gates: what "done" means

`python scripts/maintenance/gate.py` runs every mechanical check in one go. Each prints PASS,
WARN or FAIL with its evidence. A FAIL blocks. A WARN must be answered in the change record.
The quick gate also runs **before every commit** ([.githooks/pre-commit](.githooks/pre-commit),
on since 2026-11-02 through `git config core.hooksPath .githooks`), so a FAIL never reaches a
commit. `--no-verify` skips it: the owner's call, in an emergency, with the reason recorded.

| Gate | Checks | Why |
|---|---|---|
| change record | the branch has a record with Why, Evidence, Acceptance, Rollback filled | a change without a reason is not reviewed |
| repo | everything compiles, every doc link resolves, no duplicate JS function names | syntax errors and silent JS shadowing |
| scope | the list of changed files; a migration together with app code is flagged | a mixed diff cannot be reviewed or rolled back cleanly |
| special cases | no new brand or record special cases in shared code, unless the record says "Special cases: +N because ..." | each one is hand-work for the next brand (section 5) |
| context anchors | every `file::name` in `docs/context/` still exists | a stale map sends the next session to the wrong place |
| secrets, leftovers | no keys or passwords in changed lines, no `debugger`/`breakpoint()` in shipped code | both have reached commits elsewhere |
| escaping | new HTML in app.js with `${...}` that is not visibly escaped is listed | XSS (CLAUDE.md: every value through `escapeHtml()`) |
| size | how much `app/main.py` and `static/app.js` grew | growth should be a decision, not an accident |
| memory (`--full`) | each heavy scenario's peak against `docs/quality/memory_baseline.json` | the VM's RAM (section 4) |

The gate checks what can be checked mechanically. It does not tell you whether the change is right.
That is the job of the acceptance criteria and the review.

## 4. Budgets

Each budget is a number with a measurement behind it, so it can be checked instead of argued about.
The current numbers and how to remeasure them are in [docs/quality/README.md](docs/quality/README.md).

- **Memory.** The VM has 954 MB for Postgres, Caddy and two app workers. A worker keeps the most
  it has ever needed, so the server needs about twice the worst scenario, all the time. Measured
  2026-11-02: importing the app costs 147 MB, and the worst scenario (the Ask index) peaks at
  186 MB. **No scenario may grow by more than 15 MB or 10% without a reason in the record.** A new
  module-level cache states its bound (entries, seconds) and its measured size. A heavy library is
  imported inside the function that needs it when that keeps it out of every worker.
- **Special cases.** 51 in shared code on 2026-11-02. The number may only go down.
- **Size.** `app/main.py` is 9,540 lines and `static/app.js` 23,227. New areas go into their own
  modules, and the gate shows the growth on every branch.
- **Context.** A session reads the record, one context pack and the code ranges it needs. See
  [docs/context/README.md](docs/context/README.md).
- **Speed.** The memory profile prints each scenario's time. A change that makes a screen visibly
  slower says so in its record, with numbers before and after.

## 5. Rules are data, not code

Most special cases come from writing a brand's rule into shared code:

```python
if (s.make or "") == "Exenia":          # app/main.py - the next brand needs another line here
    layout = config_layout(s)
```

This repo already has the places those rules belong:

- **A brand's codes and options** go in NPD's tables (`vendor_order_codes`, `vendor_code_parts`,
  `vendor_code_rules`, the product-code structures), edited in NPD, never in code.
- **Which details the Configurator asks** come from `app/configurator.py`, as decided in NPD.
- **A rule that really is one brand's alone** goes in that brand's module (`app/duragreen.py`,
  `app/exenia_codes.py`). Shared code then asks a question ("does this product have vendor
  codes?"), never a name.
- **The front end** shows what the server says a product can do, not what its brand is called.

Before writing `if make == ...` or `if id == ...`, find where that fact should live. If no table
exists for it yet, adding one is usually the real change. When a special case truly is needed,
write "Special cases: +1 because ..." in the record, and the reviewer judges the reason.

## 6. Who does what

| Who | Does | Never |
|---|---|---|
| Owner | asks, approves records and releases, reads the diff summary | is asked to approve something that has no record |
| `change-planner` | turns a request into a change record: evidence, design, acceptance, risks | writes app code |
| `feature-tester` | proves each acceptance criterion on a local server, with real input and evidence | edits the app, touches the VM, cleans up by text match |
| `mobile-user-tester`, `ui-ux-reviewer` | use and judge screens at every size | edit the app |
| `repo-reviewer` | reviews the diff against this repo's rules and the record | edits anything |
| `release-manager` | prepares a release, and runs it only with the owner's yes to that commit quoted in its brief | deploys without that yes, or runs a data script without its own yes |

Commands: `/change <request>` starts a change, `/verify` checks one, and `/deploy-check` prepares a
release.
