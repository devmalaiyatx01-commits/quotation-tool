# Plan: an engineering framework for the Quotation Tool

Written for: the owner, and every session that picks up a phase.
Started 2026-11-02. Each phase below is its own change, with its own record, branch and review.
No phase starts without the one before it being done. Tick the box and add the commit when it is.

## The goal, as asked

> "Deployments and code changes should happen in a structured manner like a senior software
> developer would do, so that tokens are used effectively, unseen bugs dont appear, RAM usage is
> optimised, the unnecessary case based lines in the code base is reduced. Every time a change is
> introduced in the app, it goes through a well defined reason backed development framework and
> not just prompt injections."

How each part of the goal is met, and how we will know:

| Goal | Met by | Measured by |
|---|---|---|
| a structured, reason-backed process | DEVELOPMENT.md lifecycle, change records, lanes, owner approval in the record | every merged branch has a record (the gate fails one without) |
| tokens used well | docs/context/ (load order, packs, anchors), code_map.py, subagents briefed by path | packs stay true (anchor check). A session reads one pack, not the files |
| no unseen bugs | gates, acceptance criteria tested with real input, repo-reviewer, release rehearsal, fingerprints | gate results and the verification log in each record; incidents after release |
| RAM optimised | memory_profile.py, a budget per scenario, phase 3 reductions | docs/quality/memory_baseline.json, and the workers' RSS on the VM after each release |
| fewer case-based lines | the "rules are data" rule, a ratchet in the gate, phase 3 refactors | audit_special_cases.py: 51 on 2026-11-02, never up, aiming down |
| structured deployments | RELEASE.md, release-manager, release notes | each release note's checks; main = GitHub = VM after each |

## Phase 0: housekeeping (needs the owner's yes; nothing on the VM)

- [x] Move the main folder (`Documents\DesignTeamTool`, detached at the old `7174c29`) onto
  `main` (owner: "yes for all three", 2026-11-02). A three-way merge had shown its uncommitted
  edits are all in `main` already, apart from a stray pair of conflict markers in FinalDeployment.md.
  The 20 edits were stashed and tagged `archive/main-folder-wip-20261102` (restore:
  `git stash apply archive/main-folder-wip-20261102`). Four untracked files that `main` tracks
  were identical by `git hash-object` and were left for git to recreate. Any that differ are
  moved aside, never overwritten.
- [ ] Remove the worktrees whose branch is gone (`DTT-rag`, `DTT-llm`, `DTT-inverse`, `DTT-scr`,
  `DTT-dg1026`, `DTT-rel1026`, `DTT-rel1026-rehearse`, `DTT-live1026`, `DTT-latency-release`,
  `DesignTeamTool-perf`). Each one is checked for uncommitted or untracked work first; `DTT-dg1026`
  has one untracked file. `DTT-retention` and `DTT-release` hold archived work (tags `archive/*`):
  keep them, or remove them knowing the tags keep the commits.
- [x] Merge `docs/dev-framework` (this framework) into `main`: docs and tools only, no deploy needed
  (owner: "yes for all three", 2026-11-02, a fast-forward).

Done when `git worktree list` shows the main folder on `main` plus only active work, and
`main` = GitHub = VM code.

## Phase 1: the framework (branch `docs/dev-framework`, built and verified 2026-11-02)

- [x] DEVELOPMENT.md (lifecycle, lanes, gates, budgets, "rules are data"), RELEASE.md (the release
  runbook, written down from the procedure used since 2026-10-01).
- [x] Change records: docs/changes/ (README, TEMPLATE, the first record), release notes: docs/releases/.
- [x] Context management: docs/context/ (load order, session start and end, five packs with
  checked anchors), and code_map.py.
- [x] Tools: gate.py, audit_special_cases.py, memory_profile.py (+ baseline), fingerprint_tables.py.
- [x] Agents: change-planner, feature-tester, release-manager, and repo-reviewer, brought into
  the repo and updated. Commands: /change (new), /verify and /deploy-check (updated).
- [x] CLAUDE.md points to all of it. TUTORIAL.md 16.14 teaches it.

## Phase 2: make it mechanical, so following it does not depend on remembering it

- [x] **A git pre-commit hook** running the quick gate: `.githooks/pre-commit` in the repo, turned
  on with `git config core.hooksPath .githooks` (owner: "yes for all three", 2026-11-02). It is
  shared by every worktree; one on a commit from before the gate has nothing to run, and commits
  as before. It is kept LF by `.gitattributes`. Evidence it was needed: until now no check ran at commit.
- [ ] **A Claude Code hook** that refuses a command which would change the VM (`ssh ... systemctl
  restart|git merge|git reset|psql -c "UPDATE|DELETE|ALTER"`) unless the session's release note
  holds an "Approved:" line. Today the `ssh` "ask" prompt is the only guard. The `update-config`
  skill does it.
- [ ] **A rehearsal harness**, `scripts/maintenance/rehearse_exports.py`: export every BOQ under two
  commits, read-only, and compare grand totals. It runs locally and in Part A. Each release so far
  has rebuilt this check by hand.
- [ ] **Speed in the budget**: memory_profile.py already times each scenario. Record those times in
  the baseline and flag one that is more than 25% slower.
- [ ] Record each release's VM memory (MemoryCurrent, the workers' RSS) in its release note, so
  the server's trend is kept, not just the laptop's.

## Phase 3: pay the debt down, one measured change at a time

Each item is its own change (record, branch, gate, review, release), in this order, because the
first ones are the cheapest and each one makes the next easier to measure.

1. **Log the two consolidated exports.** Two lines in `_ACTIVITY_ROUTES`. Evidence:
   docs/quality/README.md. Done when downloading one writes an activity row.
2. **Lower the per-worker floor (147 MB).** Import PyMuPDF, pdfplumber, reportlab and openpyxl
   inside the functions that use them, not at start-up (all seven heavy libraries load in the 3.8 s
   import today). Done when `import` is measurably lower and no scenario is higher. Both workers
   gain it.
3. **Bound the Ask index (+39 MB kept per worker).** Keep the compact text it searches, not ORM
   rows, or drop it after idle. Done when ask_index's retained memory falls and Ask answers the same.
4. **The NPD list (+34 MB kept).** Find what it keeps (likely whole specsheet rows with raw text)
   and return only the columns the list shows.
5. **Vendor dispatch by make, not by name.** Fold `/api/npd/duragreen/...` into vendor routes keyed
   by make, with DuraGreen's rules staying in app/duragreen.py; let the server send capabilities
   (`has_vendor_codes`, `config_layout`) so app.js stops checking brand names; read `NPD_BRANDS` from
   data. Done when the special-case count falls from 51 and DuraGreen's codes round-trip 0 failures
   (audit_duragreen_codes.py). This is the biggest item and may be split per screen.
6. **Split app/main.py by area** into FastAPI routers (people, library, boqs, exports, npd, vendors,
   match), one area per change and with no behaviour change. Verified by the route table being
   identical before and after (`code_map.py --routes`), the gate, and export totals. Done when
   main.py is the app's wiring rather than its body.
7. **Refresh the orientation docs.** PROJECT_STATE.md's state is from 2026-08; Context.md,
   deployment.md, oracleDep.md and googleCloudDep.md describe older setups. Mark them historical or
   fold what is still true into the current docs.

## Phase 4: check that the framework itself works (monthly, ten minutes)

- Special-case count, memory baseline, main.py and app.js size: compared with last month in
  docs/quality/README.md.
- Releases this month: how many, how many rolled back, how many found a problem in Part A
  (the cheap place) rather than in Part B or afterwards.
- Change records: any merged branch without one, any WARN left unanswered.
- One thing in the framework that cost more than it saved. Change or drop it, with a record.
