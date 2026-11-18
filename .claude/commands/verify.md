---
description: Check the current change before calling it done
---
Verify the current change before calling it done. Report only what you actually ran and saw.

1. Find the change record (`docs/changes/*.md` whose `Branch:` names this branch). Without one,
   stop: the change is not ready for verification (DEVELOPMENT.md, step 2).
2. Run `python scripts/maintenance/gate.py --full` and show its summary line plus every FAIL and
   WARN. Fix each FAIL. Answer each WARN in the record, in one line.
3. Have the `feature-tester` agent prove the record's acceptance checks on a local server (brief it
   with the record's path, the worktree and a server URL if one is running). For a visible change,
   add `mobile-user-tester` and `ui-ux-reviewer`. Show its table of results.
4. Have the `repo-reviewer` agent review the diff against the record. Fix each finding, or answer it
   in the record with a reason.
5. Check that every temporary row, file or account the checks created was deleted by id, and that
   the fingerprints before and after match.
6. Add the results to the record's verification log, set `Status: ready` if everything passed, and
   name the TUTORIAL.md Part for any lesson.

$ARGUMENTS
