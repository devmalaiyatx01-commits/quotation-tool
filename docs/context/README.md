# Context: what to read, in what order, and what to leave alone

Written for: Claude sessions and subagents working in this repo, and for the owner, to see why.

A coding agent starts every session knowing nothing, and what it reads costs tokens: money, time,
and attention. A long, unfocused context makes it worse at the task, not better. The codebase is
too big to read: `static/app.js` is ~23,000 lines and `app/main.py` ~9,500. So reading is
planned, like everything else.

## Load in this order, and stop when you have enough

| # | Read | When | Cost |
|---|---|---|---|
| 1 | `CLAUDE.md` | always (it loads itself) | the rules and the map |
| 2 | **the change record** for your branch, `docs/changes/<date>-<slug>.md` | always, when there is a branch | what, why, status, **next step** |
| 3 | **one** context pack from the table below | the area you are changing | ~80 lines |
| 4 | the code, through `scripts/maintenance/code_map.py` and grep, in ranges of up to ~200 lines | to change or check something | only what you need |
| 5 | a deep doc, **by section** (TUTORIAL.md's contents, FinalDeployment.md's headings) | when the pack points there | one section |

| Area | Pack |
|---|---|
| Routes, permissions, activity log, models, sessions, caches | [backend.md](backend.md) |
| The single-page app: tabs, rendering, escaping, styles, small screens | [frontend.md](frontend.md) |
| BOQ and quotation exports (.xlsx/.pdf), pictures in exports | [exports.md](exports.md) |
| NPD, product codes, Configurator, vendors (DuraGreen, Exenia, InHouse) | [npd-and-vendors.md](npd-and-vendors.md) |
| Migrations, backfills, scripts, the local database, prices | [data-and-scripts.md](data-and-scripts.md) |
| Shipping to the VM | [RELEASE.md](../../RELEASE.md) (it is its own pack) |

## Starting a session (three commands)

```bash
git status -sb && git worktree list           # where am I, and on what
grep -l "^Branch: \`$(git rev-parse --abbrev-ref HEAD)\`" docs/changes/*.md   # my change record
python scripts/maintenance/code_map.py static/app.js --sections            # only if the task is front end
```

Read the record's **Next step** first. If there is no record and the task changes the app, the
first job is to write one (`/change`).

## Ending a session, or reaching a natural break

Update the change record: the status, a line in the verification log for anything you ran, and
**Next step**. Whatever exists only in the chat is lost when the session ends. Memory notes are for
the owner's preferences and for pointers. The repo holds the facts.

## Rules that save the most

- **Map, then read.** `code_map.py app/main.py --routes --grep npd` costs about 30 lines.
  Reading the routes it lists costs a few hundred. Reading the whole file costs about 120,000 tokens.
- **Never paste a big output into the context.** Pipe it through `tail`, `grep` or `--stat`, or
  write it to the scratchpad and read the part you need.
- **Delegate the wide or noisy work** to a subagent: searches across many files, test runs,
  reviews. Its tool output stays in its own context and only its conclusion comes back. Brief it
  with **paths, not the conversation**: the change record, the pack, the server URL.
- **One task per session.** When a phase is done (built, verified), record the step and start fresh
  rather than carrying a long history into the next phase.
- **Don't open** `data/`, `SampleInputs/`, `SampleInputOld/`, `archive/`, `.env*`, or any
  `.pdf`/`.xlsx`/`.pptx` unless the task is about that file (CLAUDE.md). `.claude/settings.json`
  enforces most of this list.

## Keeping the packs true

A pack that describes code which has since changed is worse than no pack, because it sends the
next session to the wrong place with confidence. So:

- Packs name code by **anchor**, `` `app/main.py::_required_permission` ``, never by line number.
  `gate.py` checks that every anchor still exists, and fails the branch that breaks one.
- A branch that changes how an area works updates that area's pack **in the same branch**.
- A pack holds only what is not obvious from the code: where things are, the rules that bite, and
  how to check a change. It never repeats CLAUDE.md. It links to it.
