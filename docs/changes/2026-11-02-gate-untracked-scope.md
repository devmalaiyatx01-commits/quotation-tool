# The gate stops counting the owner's private untracked files as part of a change

Branch: `fix/gate-untracked-scope`
Lane: fix
Status: live (dae90e4)
Asked: found while closing the framework change (owner: "yes for all three", 2026-11-02); a defect in that change's own gate
Approved: not needed for the fix lane
Special cases: +0
Memory: no change (a script, not the app)

## Why

The pre-commit hook runs `gate.py` in the main folder, which holds hundreds of the owner's own
untracked files (catalogue PDFs, status reports, spreadsheets) that are never committed. The gate
treated every one of them as part of the change. That made the scope list noise, and worse, the
secrets and leftovers checks read them, so a password-like line in a private note would fail every
commit even though the file is never committed.

## Evidence

`python scripts/maintenance/gate.py` in the main folder on `main` (e0fbbf0), 2026-11-02:
`PASS scope 833 file(s) changed`, with no change in progress. All 833 were untracked files outside the source tree.

## Scope

In: `changed_files()` in `scripts/maintenance/gate.py` counts untracked files only under app/,
static/, templates/, scripts/, docs/, .claude/ and .githooks/.
Out: every other check, and the tracked diff, which is unchanged (and includes staged new files anywhere).

## Design

Untracked files outside the source tree are never part of a change until someone `git add`s them,
and from then on the tracked diff includes them, because `git diff <base>` reads the index. So
limiting the untracked set to source folders loses nothing the hook needs to see.

## Acceptance

- [x] A1 In the main folder on `main`, the gate's scope no longer counts the owner's untracked files (833 before).
- [x] A2 A new untracked file under `scripts/` is still listed, and a planted secret in it still fails.
- [x] A3 A new root-level file that is staged (`git add`) is still listed.
- [x] A4 This branch's own gate run passes (it lists this record and gate.py).

## Risks

A new untracked root-level file is invisible to a manual gate run until it is staged. The hook
runs after staging, so commits are still fully checked.

## Budgets

None affected: no app code changes.

## Rollback

Revert this commit. The gate goes back to counting every untracked file.

## Verification log

- 2026-11-02 A1: an untracked root file holding a password-like line and an `sk-or-v1-` key is not
  listed and causes no FAIL. A2: the same key in an untracked `scripts/zz_probe.py` is listed and
  FAILs `secrets`, without the key echoed. A3: a staged root-level file is listed. A4: clean run
  PASSED, scope 3 files. All probe files deleted and the staged one unstaged afterwards.
- 2026-11-02 after the merge (main 9e3e575), in the main folder: scope 833 -> 2 files, both real
  untracked source (`.claude/skills/lesson/SKILL.md`, `next_lesson.py`: the /lesson skill was
  never committed). Secrets PASS. The gate PASSED with 1 WARN ("on main", as designed).

## Next step

None after the merge.
