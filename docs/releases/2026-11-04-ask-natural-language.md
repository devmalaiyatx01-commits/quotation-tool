# Release 2026-11-04: Ask and Match understand a designer's own words

Live before: `441d76e86660027a9a7fd988cf332007111974d8`   Release: `dae90e498e14349dc225dec664af03010dac0e09`   Branch: `release/2026-11-04-ask-natural-language` (on the VM only; locally this is `main`)
Status: live (dae90e4)
Approved: "push to oracle", 2026-11-04

The owner's words did not repeat the hash. They directly answered a message that said: "The Ask and Match work is pushed to `main` on GitHub at `dae90e4`, but it isn't live... To release it, say so". `dae90e4` is the only candidate, so the yes is taken as being for `dae90e4`. It covers code only.

## Changes
- docs/changes/2026-11-03-ask-natural-language.md - `5095dcf`, `dae90e4`: Ask and Match read a designer's own sentence and answer searches from real values (`app/catalogue_query.py`, `app/assistant.py`, `app/npd.py`, `app/match_index.py`, the Match route in `app/main.py`, `static/app.js`, `static/style.css`, `templates/index.html`).
- docs/changes/2026-11-02-dev-framework.md - `e0fbbf0`: the development framework. Tools, agents and docs only; no file under `app/`, `static/` or `templates/`.
- docs/changes/2026-11-02-gate-untracked-scope.md - `9e3e575`, `36d9e0e`: `gate.py` and its record only.
- No record: `b970813`, two lines in `FinalDeployment.md` recording the 29 Oct deploys. Docs only, so this is noted rather than a stop.

## Scripts on the VM
None. No file under `scripts/migrations/` or `scripts/backfills/` changed, and `requirements.txt` did not change, so no `pip install`. Code only; no data script was asked for.

## Rehearsal
Skipped, as RELEASE.md section 1.5 requires: `free -m` showed 115 MB available at 06:13 UTC (62-88 MB earlier today), under the 250 MB line. A release that does not change exports or prices has no export comparison to make anyway: no `*_export.py`, `boq_summary.py` or `excel_*` file changed, and `npd.lowest_price()` is new and called only by `catalogue_query.py`, not by anything that prices a BOQ line. Local evidence stands in for it, in the change record's log. Scratch-clone check: the fast-forward from the live commit applies, and the new `.gitattributes` leaves a Linux checkout's tree clean.

## Expected
fingerprints: identical except `activity_log` and `boqs` if people work during the deploy; memory: about 147 MB per worker at import, up to about 187 MB in the worst scenario (`gate.py --full` 187.6 MB), against 259 and 269 MB RSS now after 6 days' uptime; downtime: ~15 s

## Rollback
git reset --hard 441d76e86660027a9a7fd988cf332007111974d8 && sudo systemctl restart quotation-tool   (then check the login page answers; no data steps, and no database restore without the owner's yes)

## Result
Output on the VM: `~/ask-natural-language/part_a.out`, `part_b.out`, `backup_memory.log`, `journal_since_restart.txt`; `~/fingerprint_before_2026-11-04.txt`, `~/fingerprint_after_2026-11-04.txt`.

Gate, locally on `dae90e4`: `gate.py --full --base 441d76e`: 0 FAIL, 2 WARN, worst memory scenario `ask_index` 187.6 MB. WARN 1: run on `main`, because `main` already held the release at the owner's request. WARN 2: 13 flagged interpolations (the record answers 6), each read here: escaped values, numbers or constants. Its scope line says 48 files because it counts two untracked `.claude/skills/lesson/` files; the commit range holds 46, and that is what the VM received.

- Part A: VM on the live commit, no tracked edits, service active. PASS (`part_a.out`)
- Backup: `db_20261104_061642.sql`, `files_20261104_061642.tar.gz`, 06:16:42-06:20:11 UTC. Available 102 -> 119 MB, lowest 95 MB; swap free 857 -> 842 MB, lowest 839 MB. PASS (`part_a.out`, `backup_memory.log`)
- Fast-forward `441d76e..dae90e4`, 46 files, tree clean afterwards. PASS (`part_b.out`)
- Restart 06:20:45 UTC, login 200 after 18 s. PASS (`part_b.out`)
- Served `static/app.js` 076cd8f8... and `static/style.css` 5cb9c935... equal the release's blobs, from the VM and from outside; the app's changed files on disk equal their blobs. PASS (`part_b.out`)
- `POST /api/ask` and `POST /api/requirement-match` with no session: 401, so no AI request. PASS (`part_b.out`)
- Journal since the restart: no error lines, then and at 06:43 UTC (280 requests 200, none 5xx). PASS (`journal_since_restart.txt`)
- Caddy: 21 upstream errors, all `GET /login` from 06:20:46 to 06:21:02, the restart itself and this release's own probes. Explained.
- Fingerprints: 28 tables identical, 0 changed. PASS (`part_b.out`)
- Memory: before 556 MB, workers 262 and 272 MB, 122 MB available; 68 s after 297 MB, workers 144 and 146 MB, 386 MB available; 06:43 UTC workers 122 and 149 MB, 361 MB available. Within the expected figures. No restarts, no out-of-memory events.
- Downtime from outside, one request a second: 502 from 06:20:45 to 06:21:01, 200 from 06:21:03, about 18 s (expected ~15 s).

Not exercised on live: Ask's and Match's new behaviour. It needs a session, and nobody's account is signed in for a check; a question could also use the AI allowance. The local tester proved it on this code, with the limits the change record states. The new catalogue cache's memory has not been seen on live.
