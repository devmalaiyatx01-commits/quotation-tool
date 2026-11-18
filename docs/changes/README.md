# Change records

Every change to the app has one: a short file saying **why** it is made, the **evidence**, how it
will be **checked**, and how to **undo** it. The process around it is in
[DEVELOPMENT.md](../../DEVELOPMENT.md).

Why a file and not a conversation: the coding agent forgets everything between sessions, and a
chat scrolls away. The record is what the next session reads first (its "Next step" line is the
handoff), what the reviewer checks the diff against, and what the owner approves. Months later it
also answers "why is this code like this?" better than a commit message can.

- **Name:** `YYYY-MM-DD-<slug>.md`, dated the day it was started.
- **Start one:** `/change <request>`, or copy [TEMPLATE.md](TEMPLATE.md).
- **The gate** (`scripts/maintenance/gate.py`) finds a branch's record by its `Branch:` line and
  fails when Why, Evidence, Acceptance or Rollback is empty.
- **Keep it short.** A `docs` change can be ten lines. A `data` change near prices cannot.
- **Never delete one.** A dropped change keeps its record and its reason.

## Index

| Record | Lane | Status |
|---|---|---|
| [2026-11-02-dev-framework](2026-11-02-dev-framework.md) | docs | ready (in main) |
| [2026-11-02-gate-untracked-scope](2026-11-02-gate-untracked-scope.md) | fix | ready (in main) |
