# Release 2026-11-18: the 17-18 Nov batch (BOQ grid, faster Configure and Search, DuraGreen details, NPD deletes and drawings, accessory removal, one-off scripts removed)

Live before: `ef5047a278fdcddd956fe65dae64a5099967c918`   Release: `e3149216bde8a4b2a4e14299c4904af27233e2df`   Branch: `release/batch-20261118` (on the VM; locally this is `main` = `origin/main`)
Status: live (e314921), code only
Approved: "Yes, deploy e314921", 18 Nov 2026, evening IST (the owner's answer to AskUserQuestion, relayed by the coordinating agent)

**What that yes answered.** The owner's words name the hash. They answered this question: "Deploy e314921 to the Oracle VM? It holds the 11 changes from this chat plus today's review fixes (now on main and GitHub). It's code only: no data step, no DuraGreen backfills, rollback = reset to ef5047a. Saying yes also accepts these 5 known gaps: (1) Search "3000K" takes 223 ms, not the 150 ms target (live takes 1,354 ms); (2) DuraGreen BOQ lines don't show wattage or control in the description, the same as live today; (3) the project-details fold is remembered only for the BOQ you last opened, the same as live; (4) BOQ list rows whose name wraps are 58 px; (5) the phone tester's report isn't in yet (you chose not to wait)." The yes covers code only. No data script runs, not even as a dry run.

An earlier yes ("Yes, deploy e91bcb2", 18 Nov ~17:20 IST) was for another commit and was not used: the release-manager stopped before contacting the VM because four records were not `ready` (see the release-batch record's log).

## Changes
Umbrella record: docs/changes/2026-11-18-release-batch.md (status ready; owner exceptions 1-5 above written into it).
- docs/changes/2026-11-17-remove-one-off-scripts.md - `b35b567`: 21 one-off scripts that already ran are deleted (they disappear from the VM's checkout too; none is run).
- docs/changes/2026-11-17-design-md.md - `0a64ac6`: DESIGN.md, how the tool should look and behave (docs).
- docs/changes/2026-11-18-npd-delete-on-boq.md - `361e55e`: an admin can delete an NPD product BOQs use; it is retired, the BOQs keep it and export as before.
- docs/changes/2026-11-18-accessory-remove-everywhere.md - `b225386`: removing an accessory takes it off every product that takes it.
- docs/changes/2026-11-17-configure-latency.md - `1c043ab`: fewer round trips per Configure click; Search keeps up with typing (A4 is owner exception 1).
- docs/changes/2026-11-17-duragreen-type-it-in-details.md - `017049b`: DuraGreen Type it in asks details by typology (A4 is owner exception 2).
- docs/changes/2026-11-17-boq-editor-grid.md, docs/changes/2026-11-17-ui-phases-2-5.md - `f499cb8`: the BOQ editor as a grid, and DESIGN.md phases 2-5 (grid A5, UI B7 and the phone test are owner exceptions 3-5).
- docs/changes/2026-11-18-nomenclature-library-codes.md - `f851d3b`: Nomenclature shows each library accessory's own code.
- docs/changes/2026-11-18-npd-more-dimension-drawings.md - `715fd1e`: several dimension drawings per NPD product; Replace on the line diagram works again.
- docs/changes/2026-11-18-npd-delete-any-parameter.md - `5d43fe3`: any parameter can be removed from a typology and from every product of it.
- Batch commits, all under the umbrella record: `c089cd5` (overlaps, the latency review's findings, gate on Windows consoles), `1358871` (phone menu contrast), `e8206fc` (first repo-review and UX-review fixes), `e91bcb2` and `2c63667` (grid undo per BOQ, second review's fixes), `e314921` (records ready, docs only).
- `4494811`: closing docs of the ef5047a release (FinalDeployment.md row, its release note and record). Docs only.

Scope: 17 commits, 51 files (7 server files under `app/`, `static/app.js`, `static/style.css`, `templates/index.html`, `scripts/maintenance/gate.py`, 21 deleted scripts, docs). `requirements.txt` unchanged. No file under `scripts/migrations/` is new or changed; under `scripts/backfills/` and `scripts/catalogues/` there are only deletions.

## Scripts on the VM
none. `backfill_duragreen_ip_options.py` and `backfill_duragreen_spec_values.py` still need their own yes and are not run, not even as a dry run. The 21 deleted one-off scripts leave the VM's checkout with the fast-forward; they had all run before.

## Rehearsal
**None on the VM.** `free -m` showed 188 MB available at 12:48:33 UTC, under RELEASE.md's 250 MB line, and no `*_export.py`, `boq_summary.py`, `excel_*` or price file changed. Exports were proven locally instead: BOQ #79's BOQ and Quotation .xlsx are identical to `main`'s cell for cell (release-batch B3), and the dimension-drawing change alters an export only after a person saves a new drawing.

**Part A's backup on low memory.** As in the last three releases: Part A refuses to start the backup if under 150 MB is available, and samples `free -m` and the service every 5 s during it into `~/release-batch-20261118/backup_memory.log`. Stop and report if the service goes inactive, the kernel logs an out-of-memory kill, or swap free drops under 300 MB (905 MB now).

**The bundle.** `git bundle create release-batch-20261118.bundle ef5047a..main` while local `main` is `e314921`; on the VM `git fetch ~/release-batch-20261118/release-batch-20261118.bundle main:release/batch-20261118`, and check its hash equals the release, and that none of the 14 files the release adds already exists untracked on the VM, before merging.

## Expected
- Fingerprints: all 28 tables identical, except `activity_log` and whatever a person's own work touches (`boqs`, `boq_items`, `specsheets` ...) if people work during the deploy, each explained by the activity log. Any other change is investigated.
- Memory: the gate's worst scenario `ask_index` 187.3 MB (`gate.py --full --base ef5047a` on `2c63667`, code-identical to `e314921`). Now (12:48:33 UTC): service 388 MB (`MemoryCurrent` 406,913,024 B), available 188 MB, swap used 118 MB, active since 07:00:08.
- Downtime: ~15-60 s (13 s and 24 s on 18 Nov).
- Files on disk and served, sha256 of the release's blobs:
  - `static/app.js` `79bab9e513d5ff00bbb36864642c9df6e449cfbcc8e0f938a6deb287f2e7c593` (live: `8d8327c2...1aba`)
  - `static/style.css` `a81b8f7af9c573b4564c0041d5386768cdb51a7f92c95e577dc038c7d68344a2` (live: `0cee1ff6...eae`)
  - `app/main.py` `c28019c3f48382e944a5540c1ec9194ca5643be833e3d3b9230f07c45124c2ae` (disk only)
  - `templates/index.html` `eaed0321dc0f3d2072a949447400cdbb9c83defd227a3e165bfff2847165e9f0` (disk only)
- Smoke, without signing in: login 200; served `app.js` and `style.css` match the blobs, from the VM and from outside; the new `GET /api/npd/duragreen/details` and `GET /api/npd/parameters?typology=Downlight` answer 401 with no session; the journal shows no error. The grid, Configure, the NPD deletes and drawings need a session, so they are not exercised on live (proven locally, in the records).

## Rollback
git reset --hard ef5047a278fdcddd956fe65dae64a5099967c918 && sudo systemctl restart quotation-tool   (then check the login page answers 200; no data steps, as the deploy changes no data). What people do under the new code before a rollback stays as data, as each record's rollback says: a retired NPD product keeps status "deleted", which the old code shows as an unknown status (set back to "approved" by id from the log if needed); an accessory removed everywhere, or a parameter deleted, is put back from the activity log's before-lists and details; extra drawings stay on their products. (The batch record names `4494811` as the rollback point; it is `ef5047a` plus docs, and the rule is to reset only to the recorded live commit, so `ef5047a`.)

## Result
**Live since 2026-11-18 12:57:11 UTC (18:27 IST) as `e314921`, code only.** Every check passed and nothing was rolled back. Output: `~/release-batch-20261118/` on the VM (`part_a.out`, `part_b.out`, `reread.out`, `requests_after.txt`, `backup_memory.log`, `journal_after.txt`, `activity_after.txt`, `activity_max_before.txt`, `fp.sql`), and `~/fingerprint_{before,after}_2026-11-18d.txt` (the `d` keeps the day's three earlier releases' files).

- Part A (12:54:00-12:56:42 UTC): VM on `ef5047a`, branch `main`, no tracked edits, service active; 251 MB available at the start. Backup `db_20261118_125404.sql` (10,448,723 B, ends "dump complete") and `files_20261118_125404.tar.gz` (861,958,618 B), 12:54:04-12:56:35; available memory never under 222 MB, swap free never under 854 MB, service active in all 30 samples, no out-of-memory line. `activity_log` max id 3303 just before the fingerprint; fingerprint 12:56:39-12:56:41, 28 tables. Bundle verified and fetched as `release/batch-20261118` = `e314921`: 17 commits, 51 files (14 added, none already on disk; 21 deleted), `requirements.txt` unchanged, no migration. The VM computed the same four blob hashes as listed under Expected. Rehearsal: none, as planned.
- Part B (12:57:11 UTC): VM re-checked on `ef5047a` and clean; fast-forward `ef5047a..e314921`, no tracked edits after, the 21 one-off scripts gone from disk; no pip install, no migration, no backfill. On disk, `static/app.js`, `static/style.css`, `app/main.py` and `templates/index.html` equal the release's blobs. Restart 12:57:11, service active since 12:57:12, **login 200 after 21 s** (12:57:32), and 200 through `https://quotes.example.com`.
- Served files: `static/app.js` (`79bab9e5...c593`) and `static/style.css` (`a81b8f7a...44a2`) equal the release's blobs, from the app, through Caddy from the VM, and from outside (this PC, 12:57:49).
- No session: `GET /api/npd/duragreen/details` (new in this release) and `GET /api/npd/parameters?typology=Downlight` answer 401 `{"detail":"Not authenticated"}`, from the VM and (the first) from outside.
- Journal: at 12:57:40, 33 lines since the restart, no traceback, exception or error line, no 5xx; re-read at 12:58:31: 37 lines, 12 requests, all the checks' own (`requests_after.txt`: `/login` 3, `/static/app.js` 3, `/static/style.css` 3, the two 401 routes 3), 0 5xx, no error lines, no restarts, Caddy `http.log.error` 0, no out-of-memory line.
- **Fingerprints: all 28 tables identical** (`fingerprint_tables.py --compare`: "28 tables identical, 0 changed"), after taken 12:57:40-12:57:41; no `activity_log` row after id 3303 at the switch or at the re-read. Nobody worked during the deploy, so nothing needed explaining.
- Memory: before the switch the service held 380 MB (`MemoryCurrent` 398,352,384 B), workers 169 and 198 MB (RSS 173,376 and 202,996 KB), 207 MB available, swap used 151 MB. 29 s after: 259 MB (271,585,280 B), workers 136 and 136 MB, 421 MB available, swap used 125 MB. 80 s after (12:58:31): 255 MB (267,603,968 B), workers 133 and 131 MB, 413 MB available, swap used 122 MB. For comparison, the gate's worst scenario measured locally is `ask_index` 187.3 MB; a worker on live held up to 198 MB under the old code. Fresh workers are always smaller, so the next days' readings are what will show the new code's size.
- Downtime: 21 s (restart to login 200).
- Not exercised on live: the grid, Configure and Search, DuraGreen details, the NPD deletes and drawings, accessory removal. They need a session, so they were proven locally (the records' logs).
- Not run: `backfill_duragreen_ip_options.py` and `backfill_duragreen_spec_values.py`, which still await their own yes. No row was changed by the deploy.
- After the deploy (from the batch record): the owner uploads the line diagrams of NPD #289, #1524, #1525 and #1526 again.

Prepared on 18 Nov from the main folder, read-only on the VM at 12:48:33 UTC:
- VM on `ef5047a278fdcddd956fe65dae64a5099967c918`, branch `main`, no tracked edits, service active since 07:00:08 UTC, no error lines in the journal since then, no out-of-memory line in the kernel log in 48 h. `ef5047a` is an ancestor of `e314921`. PASS
- Scope: 17 commits, 51 files; every code commit belongs to a `ready` record (all 12 records `ready`); `4494811` and `e314921` are docs. No requirements, migration or backfill to run. PASS
- Gate: `gate.py --full --base ef5047a` on `2c63667` (coordinator): 0 FAIL, 1 WARN (grid escaping, answered in the grid record), worst memory `ask_index` 187.3 MB. Re-run here, the quick gate on `e314921` (`main`): 0 FAIL, 2 WARN (escaping, answered; "on main", as `main` holds the release). Its scope line counts 53 files because two untracked `.claude/skills/lesson/` files are in the folder; the commit range holds 51, and that is what the VM receives. PASS
