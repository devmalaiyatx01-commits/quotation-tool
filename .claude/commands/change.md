---
description: Start a change - a change record first, the owner's yes when its lane needs one, then a branch
---
Start a change to the Quotation Tool by the process in `DEVELOPMENT.md`. No app code is written in
this step.

1. Have the `change-planner` agent draft the record. Brief it with the request below, quoted
   exactly, and today's date. It writes `docs/changes/<date>-<slug>.md` and returns its questions.
2. Show the owner the record's Why, Design, Acceptance and Scope out (short), and the planner's
   questions. For a `feature`, `data`, `schema` or `infra` lane, ask for a yes to the design and
   **wait**. Quote the yes in the record's `Approved:` line and set `Status: approved`.
   For `docs` and `fix`, carry on.
3. Make the branch and its worktree from `main`, named as the record's `Branch:` line says:
   `git worktree add ../DTT-<slug> -b <lane>/<slug> main`. Move the record into that worktree
   (it belongs to the branch) and set `Status: building`.
4. Say what the next step is (build, then `/verify`), and write it under the record's `Next step`.

Request: $ARGUMENTS
