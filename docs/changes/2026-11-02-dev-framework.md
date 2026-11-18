# A development framework: change records, gates, budgets, context packs, agents

Branch: `docs/dev-framework`
Lane: docs
Status: live (dae90e4)
Asked: "Create a plan, workflow, .md files for context management, agents for feature testing and deployments. Goal: Deployments and code changes should happen in a structured manner like a senior software developer would do, so that tokens are used effectively, unseen bugs dont appear, RAM usage is optimised, the unnecessary case based lines in the code base is reduced. Every time a change is introduced in the app, it goes through a well defined reason backed development framework and not just prompt injections", 2026-11-02
Approved: "yes for all three" (commit and merge into main, move the main folder onto main, turn on the pre-commit hook), 2026-11-02
Special cases: +0
Memory: no change (no app code changes)

## Why

Changes reach the app as requests to a coding agent that forgets everything between sessions.
The rules that keep production safe were real but scattered: CLAUDE.md, memory notes that only
one machine's agent can read, old scratchpads, and the owner's head. Nothing checked a change
mechanically before review. RAM and special cases had no measured baseline, so "optimise RAM"
and "reduce case-based lines" could not be judged either way.

## Evidence

- The release procedure (bundle, Part A/B, fingerprints, rehearsal) existed only in memory notes
  and per-release scratch scripts. FinalDeployment.md's "Ongoing" section still says `git pull`.
- `repo-reviewer` and the `lesson` skill existed only as untracked files in a folder on an old
  commit (7174c29), not in `main`.
- CLAUDE.md said `app/main.py` was ~7,950 lines and `static/app.js` ~19,600. They are 9,540 and 23,227.
- Measured today: **51 special cases** in shared code (37 in app/main.py, 35 of them DuraGreen),
  and a **147 MB** per-worker import floor, with the worst scenario (the Ask index) at **186 MB**,
  on a 954 MB VM that runs two workers.
- 26 branches were cleaned up today. Five of them held work that was in no other branch.

## Scope

In: DEVELOPMENT.md, RELEASE.md, docs/changes, docs/context, docs/quality, docs/plans, four tools
(gate, special-case audit, memory profile, fingerprints) plus code_map, agents (change-planner,
feature-tester, release-manager, repo-reviewer brought into the repo), commands (/change, /verify,
/deploy-check), CLAUDE.md, a TUTORIAL.md section, and (on the owner's yes) the git pre-commit hook
`.githooks/pre-commit` with its LF rule in `.gitattributes`.
Out: any change to app/, static/ or templates/ (the plan lists those as later changes, one record
each), Claude Code hooks and the rest of plan phase 2, the VM.

## Design

The gate reuses `verify_repo.py` and the special-case audit, so each check has one definition.
The ratchet compares against `main`, never against a stored number someone could edit. The memory
profile runs each scenario in a fresh process, because a worker keeps its peak and one scenario
must not inflate the next. The fingerprint SQL is the same in both forms (Python locally, psql on
the VM), so a local result means the same as a server one. Context packs name code by `file::name`,
because line numbers go stale, and the gate checks every anchor.

## Acceptance

- [x] A1 `gate.py` on this branch passes with 0 FAIL.
- [x] A2 The gate fails a throwaway change that adds a brand special case, a debugger statement,
  a secret, a stale context anchor, and a branch with no record; it warns on an unescaped `${...}`.
  Then everything is reverted.
- [x] A3 `memory_profile.py`: two runs agree within 2 MB per scenario, cleanup leaves 0 rows, baseline recorded.
- [x] A4 `fingerprint_tables.py`: two runs identical (28 tables); the psql form gives the same 28 fingerprints.
- [x] A5 `code_map.py` finds `_required_permission`, `_ACTIVITY_ROUTES`, `renderEditor` and the export routes.
- [x] A6 No file under app/, static/ or templates/ changed.
- [x] A7 Every agent and command file has front matter with a name/description, and every doc link resolves.

## Risks

- A process too heavy to follow gets skipped. Lanes keep the docs lane light, and the quick gate runs in seconds.
- Heuristic checks (escaping, special cases in JS) can be wrong. They WARN, or name the exact line,
  so a person can judge it.
- `memory_profile.py` writes a temporary user to the local DB. It is deleted by id, and the
  leftovers are printed (0 in every run).

## Budgets

No app change: memory, speed and special cases are unchanged (gate: special cases +0, size +0).

## Rollback

Delete the branch and the worktree. Nothing reaches the VM. The files are docs and local tools.

## Verification log

- 2026-11-02 audit_special_cases.py: 51 in shared code, 0 new vs main.
- 2026-11-02 memory_profile.py x3: peaks within 2 MB run to run; cleanup 0 left each time; baseline recorded (b970813).
- 2026-11-02 fingerprint_tables.py x2: 28 identical; psql-form statements run through the driver: identical to the Python form.
- 2026-11-02 gate.py negative test (A2): six violations planted on a throwaway branch
  (`docs/zz-gate-probe`): missing record, a Lumenpulse check in app/npd.py and in app.js, a
  stale anchor, an `sk-or-v1-` key, `breakpoint()`, and an unescaped `${s.name}`. Result: 5 FAIL +
  1 WARN, exit 1. Files restored byte-identical (sha256), probe branch deleted. Run twice: before
  and after the fixes below.
- 2026-11-02 fixes the probe and the review found: the secrets check echoed the key (now
  `<redacted>`); multi-word brands (MEAN WELL, E-POWER, AI DIMMING) were never matched (now 16/16
  matching cases right, total still 51, and `b === r.brand` is not "B & R"); changed-file paths
  with spaces were split (now `-z`).
- 2026-11-02 gate.py --full: PASSED, 0 FAIL, 0 WARN; memory compared with the baseline, worst ask_index 185.9 MB.
- 2026-11-02 local database after all test runs: 28 tables identical to the fingerprint taken
  before them; 0 temporary users left.
- Not run: the `repo-reviewer` agent on this branch (step 6). The owner approved the merge without it.
- 2026-11-02 main folder moved onto `main` (b970813): 20 uncommitted edits stashed and tagged
  `archive/main-folder-wip-20261102`; 4 untracked files identical to main's (hash-object) left for
  git to recreate, and they came back identical; 0 tracked changes afterwards.
- 2026-11-02 pre-commit hook on (`core.hooksPath .githooks`): its first real run is this branch's commit.

## Next step

Plan phase 0 item 2 (remove the old worktrees, each checked first) needs the owner's yes. Then
phase 2's remaining items, each as its own change with its own record.
