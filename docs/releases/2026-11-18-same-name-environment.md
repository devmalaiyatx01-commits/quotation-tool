# Release 2026-11-18: Add Product tells same-named products apart by where they go (front end only)

Live before: `7d020cc1fd96948ba4c3eda9d52b907e18a75fab`   Release: `1b2b367aa509b7b4cf60479115663f249f9df13d`   Branch: `release/2026-11-18-same-name-environment` (on the VM only; locally this is `main`)
Status: live (1b2b367), code only
Approved: "Yes, deploy 1b2b367", 2026-11-18

**What that yes answered.** The owner's words name the hash. They answered this question, put to them by the coordinating agent: "Deploy commit 1b2b367 (Add Product labels same-named products 'Exterior' / 'Environment not set', e.g. the two Filo S 05; the server moves from 7d020cc to 1b2b367, front end only, no data) to the Oracle VM now?". The request behind it was the owner's report that Add Product did not show the IP68 connector for Filo S 05 on live, and "Fix this". The yes covers code only: no product's data and no rule (#1225 included) is changed on live, and the two pending DuraGreen data scripts from the 5 Nov release are not run.

## Changes
- docs/changes/2026-11-18-same-name-environment.md - `1b2b367`, fix lane, status ready: where two products share a name (Filo S 05 #150 interior / #281 exterior; Filo S 10 #151 / #282), Add Product labels each by its environment ("Exterior", "Environment not set"), so a designer can pick the one whose accessories include the IP68 Connector. `static/app.js` +27/-5, `static/style.css` +4.
- No record: `e222ab7`, closing the 7d020cc release (`FinalDeployment.md`, one change record, one release note). Docs only, so this is noted rather than a stop.

## Scripts on the VM
none. No file under `scripts/migrations/` or `scripts/backfills/` changed and `requirements.txt` did not change. The two DuraGreen data scripts waiting since 5 Nov (`backfill_duragreen_ip_options.py`, `backfill_duragreen_spec_values.py`) still need their own yes and are not run, not even as a dry run.

## Rehearsal
**None on the VM.** `free -m` showed 246 MB available at 05:59:02 UTC, under the 250 MB line, and no `*_export.py`, `boq_summary.py`, `excel_*` or server file changed, so there is nothing to compare. The behaviour was proven locally (the change record's log).

**Part A's backup on low memory.** Sample `free -m` every 5 s during it, into `~/same-name-environment/backup_memory.log`. On 18 Nov (05:17) it never went under 206 MB available. Stop and report if the service goes inactive, the kernel logs an out-of-memory kill, or swap free drops under 300 MB (914 MB now).

**The bundle.** `git bundle create same-name-environment.bundle 7d020cc..main` while local `main` is `1b2b367`; on the VM `git fetch ~/same-name-environment/same-name-environment.bundle main:release/2026-11-18-same-name-environment`, and check its hash equals the release before merging.

## Expected
- Fingerprints: identical except `activity_log` and `boqs` (and their lines) if people work during the deploy, explained by the activity log. `specsheets`, `config_rules` and the accessory tables must be unchanged. Any other change is investigated.
- Memory: no change expected (record); gate's worst scenario `ask_index` 186.8 MB. Now: service 334 MB (`MemoryCurrent` 349,872,128 B), 246 MB available, swap used 109 MB.
- Downtime: ~15-60 s (24 s on 18 Nov 05:20).
- Served files, sha256 of the release's blobs: `static/app.js` `8d8327c2791662617ba3e1c8753dd3be85a96d51892af2a83b6601965d6c1aba`, `static/style.css` `0cee1ff6b1f01b1fa0bf77f63ce400e1973657b42b4736c63a1095c9df247eae`.
- Smoke, without signing in: login 200; served `app.js` and `style.css` match the blobs from outside; the journal shows no error. The Add Product labels need a session, so they are not exercised on live.

## Rollback
git reset --hard 7d020cc1fd96948ba4c3eda9d52b907e18a75fab && sudo systemctl restart quotation-tool   (then check the login page answers; no data steps, as no data changes.)

## Result
**Live since 2026-11-18 06:06:28 UTC as `1b2b367`, code only.** Every check passed and nothing was rolled back. Output: `~/same-name-environment/` on the VM (`part_a.out`, `part_b.out`, `backup_memory.log`, `journal_after.txt`), and `~/fingerprint_{before,after}_2026-11-18b.txt` (the `b` keeps the 05:20 release's files of the same day).

- Part A (06:01:19-06:03:31 UTC): VM on `7d020cc`, clean, active. Backup `db_20261118_060119.sql` (10.4 MB, ends "dump complete") and `files_20261118_060119.tar.gz` (861 MB), 06:01:19-06:03:27; available memory never under 270 MB, service active in all 26 samples, no out-of-memory line. Fingerprint, 28 tables (`config_rules` 1210 rows: one more than at 05:20, rule #1225 made by user #4 at 05:29:19 under 7d020cc). Bundle verified and fetched as `release/2026-11-18-same-name-environment` = `1b2b367`, 6 files. Rehearsal: none, as planned.
- Part B (06:06:28 UTC): fast-forward `7d020cc..1b2b367`, no tracked edits after, `requirements.txt` unchanged, no migration or backfill. Restart 06:06:28; **login 200 after 13 s**. `static/app.js` (`8d8327c2...1aba`) and `static/style.css` (`0cee1ff6...eae`): blob, disk and served identical, and again from outside through `https://quotes.example.com`. Journal since the restart, re-read at 06:10:36: 29 requests, 0 5xx, no error lines, no out-of-memory line. Caddy logged 2 "connection refused" at 06:06:31 and 06:06:34 (a person's `/api/npd/products` and `/architectural` inside the restart window), none after. (My ssh session dropped during the restart; the script ran to its end on the VM, and its output was read back.)
- **Fingerprints: 26 tables identical, 2 changed, both explained by the activity log.** `config_rules` (1210 -> 1210 rows, new md5): user #4 edited rule #1225 (`PUT /api/config-rules/1225`, activity #3065, 06:07:59). `activity_log` +2: that edit and `PUT /api/npd/products/281` (#3066, 06:08:01), which left `specsheets` identical. Both after the restart, by a person in NPD, not by the deploy.
- Memory: before the switch the service held 316 MB, workers 159 and 146 MB, 267 MB available, swap used 134 MB. ~3 min after: 293 MB, workers 141 and 144 MB, 312 MB available. ~4 min after: 300 MB, workers 141 and 146 MB, 337 MB available.
- Not exercised on live: the Add Product labels need a session; proven locally (the change record's log).
- Not run: `backfill_duragreen_ip_options.py` and `backfill_duragreen_spec_values.py`, which still await their own yes. No product and no rule was changed by the deploy.

Prepared on 18 Nov from the main folder, read-only on the VM at 05:59:02 UTC:
- VM on `7d020cc1fd96948ba4c3eda9d52b907e18a75fab`, no tracked edits, service active, no error lines in the journal since the 05:20 restart, no out-of-memory line in the kernel log in 48 h. `7d020cc` is an ancestor of `1b2b367`. PASS
- Scope: 2 commits, 6 files (`static/app.js`, `static/style.css` and four docs). No requirements, migration, backfill, export or server file. PASS
- Gate on `1b2b367`: `gate.py --full --base 7d020cc`: 0 FAIL, 1 WARN (run on `main`, because `main` already holds the release), special cases none added, escaping PASS, worst memory scenario `ask_index` 186.8 MB. Its scope line counts 8 files because two untracked `.claude/skills/lesson/` files are in the folder; the commit range holds 6, and that is what the VM receives. PASS
