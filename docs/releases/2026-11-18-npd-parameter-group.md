# Release 2026-11-18: a parameter added under a group is listed in that group (server only)

Live before: `1b2b367aa509b7b4cf60479115663f249f9df13d`   Release: `ef5047a278fdcddd956fe65dae64a5099967c918`   Branch: `release/2026-11-18-npd-parameter-group` (on the VM only; locally this is `main`)
Status: live (ef5047a), code only
Approved: "Yes, deploy ef5047a", 2026-11-18

**What that yes answered.** The owner's words name the hash. They answered this question, put to them by the coordinating agent: "Deploy commit ef5047a (NPD: a parameter added under a group is listed in that group, no duplicate group; the server moves from 1b2b367 to ef5047a, code only, no data) to the Oracle VM now?". The request behind it was the owner's report of a second "MECHANICAL PARAMETERS" group after adding TRIM DETAILS, ending "Fix this on oracle and main". The yes covers code only: no parameter row on live is changed by the deploy (rows already out of place only display inside their group), and the two pending DuraGreen data scripts from the 5 Nov release are not run.

## Changes
- docs/changes/2026-11-18-npd-parameter-group.md - `ef5047a`, fix lane, status ready: `GET /api/npd/parameters` groups by name (ignoring case and spacing) in the order each group first appears, and `npd_add_parameter` places a new parameter after the last one of its group and renumbers the typology's template 0..n-1, as a move already does. `app/main.py` +30/-8.
- No record: `216098e`, closing the 1b2b367 release (`FinalDeployment.md`, one change record, one release note). Docs only, so this is noted rather than a stop.

## Scripts on the VM
none. No file under `scripts/migrations/` or `scripts/backfills/` changed and `requirements.txt` did not change. The two DuraGreen data scripts waiting since 5 Nov (`backfill_duragreen_ip_options.py`, `backfill_duragreen_spec_values.py`) still need their own yes and are not run, not even as a dry run.

## Rehearsal
**None on the VM.** No `*_export.py`, `boq_summary.py`, `excel_*` or price file changed, so there is no export to compare, and `free -m` showed 187 MB available at 06:43:59 UTC (302 MB at 06:46:55), under the 250 MB line at the first read. The behaviour was proven locally (the change record's log).

**What the fix changes on live, read-only.** One SELECT on `parameter_templates` (a small table) counts, per typology, the group blocks the old code drew (a new block whenever the next row's group differs) against the distinct group names the new code draws. Where they differ, the new code shows fewer blocks with no data change. Saved as `~/npd-parameter-group/groups_{before,after}.txt`; the two must be identical (the deploy changes no row).

**Part A's backup on low memory.** Part A refuses to start the backup if under 150 MB is available, and samples `free -m` every 5 s during it into `~/npd-parameter-group/backup_memory.log`. On 18 Nov it never went under 206 MB (05:17) and 270 MB (06:01). Stop and report if the service goes inactive, the kernel logs an out-of-memory kill, or swap free drops under 300 MB (852 MB now).

**The bundle.** `git bundle create npd-parameter-group.bundle 1b2b367..main` while local `main` is `ef5047a`; on the VM `git fetch ~/npd-parameter-group/npd-parameter-group.bundle main:release/2026-11-18-npd-parameter-group`, and check its hash equals the release before merging.

## Expected
- Fingerprints: identical except `activity_log` and `boqs` (and their lines) if people work during the deploy, explained by the activity log. `parameter_templates` must be unchanged unless the activity log shows a person adding or moving a parameter. Any other change is investigated.
- Memory: no change expected (record); gate's worst scenario `ask_index` 187.9 MB. Now: service 401 MB (`MemoryCurrent` 420,073,472 B at 06:43:59), workers 174 and 187 MB at 06:46:55, available 187-302 MB, swap used 113-171 MB.
- Downtime: ~15-60 s (13 s and 24 s on 18 Nov).
- Served files, unchanged by this release, sha256 of the release's blobs: `static/app.js` `8d8327c2791662617ba3e1c8753dd3be85a96d51892af2a83b6601965d6c1aba`, `static/style.css` `0cee1ff6b1f01b1fa0bf77f63ce400e1973657b42b4736c63a1095c9df247eae` (the same as 1b2b367's).
- Smoke, without signing in: login 200; served `app.js` and `style.css` match the blobs from outside; `GET /api/npd/parameters?typology=Downlight` answers 401 with no session; the journal shows no error. The grouped form needs a session, so it is not exercised on live.

## Rollback
git reset --hard 1b2b367aa509b7b4cf60479115663f249f9df13d && sudo systemctl restart quotation-tool   (then check the login page answers; no data steps, as no data changes. A parameter a person adds under ef5047a keeps the order it was given, inside its group, so the old code also shows it there.)

## Result
**Live since 2026-11-18 07:00:07 UTC as `ef5047a`, code only.** Every check passed and nothing was rolled back. Output: `~/npd-parameter-group/` on the VM (`part_a.out`, `part_b.out`, `checks.out`, `reread.out`, `backup_memory.log`, `groups_{before,after}.txt`, `activity_after.txt`, `journal_after.txt`), and `~/fingerprint_{before,after}_2026-11-18c.txt` (the `c` keeps the 05:20 and 06:06 releases' files of the same day).

- Part A (06:52:02-06:54:37 UTC): VM on `1b2b367`, clean, active, 249 MB available at the start. Backup `db_20261118_065202.sql` (10.4 MB, ends "dump complete") and `files_20261118_065202.tar.gz` (861 MB), 06:52:02-06:54:31; available memory never under 218 MB, swap free never under 875 MB, service active in all 29 samples, no out-of-memory line. Fingerprint, 28 tables. Groups the fix changes (read-only, old blocks -> new groups): Flexible Neon / LED Band 7 -> 3, **Track 4 -> 3** (TRIM DETAILS #426, under MECHANICAL PARAMETERS at sort 11 after ACCESSORIES: the owner's case), Underwater Fixture 7 -> 5, Wall Grazer / Wall Washer 7 -> 4; 15 typologies, 383 rows. Bundle verified and fetched as `release/2026-11-18-npd-parameter-group` = `ef5047a`, 5 files. Rehearsal: none, as planned.
- Part B (07:00:07 UTC): fast-forward `1b2b367..ef5047a`, no tracked edits after, `requirements.txt` unchanged, no migration or backfill; `app/main.py` on disk = its blob (`5a5dda72...39f2`). Restart 07:00:07; **login 200 after 13 s**. `static/app.js` (`8d8327c2...1aba`) and `static/style.css` (`0cee1ff6...eae`), unchanged by this release: blob, disk and served identical, and again from outside through `https://quotes.example.com` (07:00:28). `GET /api/npd/parameters?typology=Downlight` with no session: 401 `{"detail":"Not authenticated"}`, from the VM and from outside. Journal since the restart, re-read at 07:02:16: 8 requests (the checks' own), 0 5xx, no error lines, no out-of-memory line, Caddy logged no upstream error.
- **Fingerprints: 26 tables identical, 2 changed, both explained by the activity log, and both before the switch.** `activity_log` 3090 -> 3096 (+6) and `specsheets` (1584 -> 1584 rows, new md5): user #4 saved six NPD products under the old code, after the "before" fingerprint (taken 06:54:31-06:54:37) and before the restart (07:00:07): `PUT /api/npd/products/161` twice (#3099 06:56:13, #3100 06:56:29), `/160` (#3101 06:57:12), `/159` (#3102 06:58:08), `/158` (#3103 06:58:53), `/7` (#3104 06:59:28), all "Changed the specification of RAYON ...". No activity after the restart up to 07:02:16. `parameter_templates` identical (383 rows), and the group read identical before and after: the deploy changed no row.
- Memory: before the switch the service held 369 MB (`MemoryCurrent` 387,219,456 B), workers 164 and 195 MB, 223 MB available, swap used 120 MB. 31 s after: 252 MB, workers 130 and 130 MB, 393 MB available. ~2 min after: 229 MB, workers 109 and 109 MB, 405 MB available, swap used 128 MB.
- Not exercised on live: the grouped form and adding a parameter need a session; proven locally (the change record's log).
- Not run: `backfill_duragreen_ip_options.py` and `backfill_duragreen_spec_values.py`, which still await their own yes. No parameter row, product or rule was changed by the deploy.

Prepared on 18 Nov from the main folder, read-only on the VM at 06:43:59 and 06:46:55 UTC:
- VM on `1b2b367aa509b7b4cf60479115663f249f9df13d`, no tracked edits, service active since 06:06:29, no error lines in the journal since the 06:06 restart, no out-of-memory line in the kernel log in 48 h. `1b2b367` is an ancestor of `ef5047a`. PASS
- Scope: 2 commits, 5 files (`app/main.py` and four docs). No requirements, migration, backfill, export, template or static file. PASS
- Gate on `ef5047a`: `gate.py --full --base 1b2b367`: 0 FAIL, 1 WARN (run on `main`, because `main` already holds the release), special cases none added (50), escaping PASS (app.js unchanged), worst memory scenario `ask_index` 187.9 MB. Its scope line counts 7 files because two untracked `.claude/skills/lesson/` files are in the folder; the commit range holds 5, and that is what the VM receives. PASS
