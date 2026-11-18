# Release 2026-11-18: NPD Rules, choices that depend on other choices (code only)

Live before: `6e5e71a382cd5baa538453e255a29380db87bed1`   Release: `7d020cc1fd96948ba4c3eda9d52b907e18a75fab`   Branch: `release/2026-11-18-npd-option-rules` (on the VM only; locally this is `main`)
Status: live (7d020cc), code only
Approved: "Yes, deploy 7d020cc", 2026-11-18

**What that yes answered.** The owner's words name the hash. They answered this question, put to them by the coordinating agent: "Deploy commit 7d020cc (NPD Rules: the rule builder on products, and Add Product's accessories following the rules; the server moves from 6e5e71a to 7d020cc, code only, no data or migration, no rules created) to the Oracle VM now?". Earlier the owner wrote "push the rules change to main and oracle"; GitHub `main` is already `7d020cc`. The yes covers code only: no rule is created on live, and the two pending DuraGreen data scripts from the 5 Nov release are not run.

## Changes
- docs/changes/2026-11-17-npd-option-rules.md - `7d020cc`, status ready: NPD product page gets a "Rules" section (a sentence builder over `config_rules`); Add Product's accessory list re-asks the server with the current choices and drops ruled-out accessories. `app/main.py` +172/-33 (writes to `/api/config-rules` need `npd.edit` or `config.edit`; `GET /api/specsheets/{id}/accessories` takes `?state=` and `&only=hidden`; typology rules carry their brand as a trigger `{"field":"make"}`), `static/app.js` +333, `static/style.css` +47.
- No record: `96f5f10`, closing the 17 Nov release. Four docs files (`FinalDeployment.md`, two change records, one release note), so this is noted rather than a stop.

## Scripts on the VM
none. No file under `scripts/migrations/` or `scripts/backfills/` changed and `requirements.txt` did not change. The two DuraGreen data scripts waiting since 5 Nov (`backfill_duragreen_ip_options.py`, `backfill_duragreen_spec_values.py`) still need their own yes and are not run, not even as a dry run.

## Rehearsal
**None on the VM.** `free -m` showed 187 MB available at 05:16:18 UTC, under the 250 MB line, and no `*_export.py`, `boq_summary.py` or `excel_*` file changed, so there is nothing to compare. The behaviour was proven locally (the change record's log: feature-tester, real clicks, `config_rules` back to 1,209 after the tests).

**Part A's backup on low memory.** Sample `free -m` every 5 s during it, into `~/npd-option-rules/backup_memory.log`. On 17 Nov it started near 200 MB and never went under 218 MB. Stop and report if the service goes inactive, the kernel logs an out-of-memory kill, or swap free drops under 300 MB (888 MB now).

**The bundle.** `git bundle create npd-option-rules.bundle 6e5e71a..main` while local `main` is `7d020cc`; on the VM `git fetch ~/npd-option-rules/npd-option-rules.bundle main:release/2026-11-18-npd-option-rules`, and check its hash equals the release before merging.

## Expected
- Fingerprints: identical except `activity_log` and `boqs` (and their lines) if people work during the deploy. `config_rules` must be unchanged (the imported Exenia rules only). Any other change is investigated.
- Memory: no change expected (record); gate's worst scenario `ask_index` 187.6 MB, `npd_products` +1.5 MB, library +6.1 MB. Now: service 420 MB (`MemoryCurrent`), workers 192 and 198 MB RSS, 187 MB available, swap used 135 MB.
- Downtime: ~15-60 s (18 s on 17 Nov).
- Served files, sha256 of the release's blobs: `static/app.js` `fab5ed732556c96bd3ece36beb4739a065459b8468ac35bc8afc948c06b260e6`, `static/style.css` `c5e4ff07f4c6f2896e9a077b2f1ab64ccefba9f61e01aa1b9be54df938dd3e40`.
- Smoke, without signing in: login 200; served `app.js` and `style.css` match the blobs from outside; the journal shows no error. The Rules section and the accessory filtering need a session, so they are not exercised on live.

## Rollback
git reset --hard 6e5e71a382cd5baa538453e255a29380db87bed1 && sudo systemctl restart quotation-tool   (then check the login page answers; no data steps, as no data changes. Rules people make after the switch stay in `config_rules`; the old code ignores those on accessories.)

## Result
**Live since 2026-11-18 05:20:42 UTC as `7d020cc`, code only.** Every check passed and nothing was rolled back. Output: `~/npd-option-rules/` on the VM (`part_a.out`, `part_b.out`, `backup_memory.log`, `journal_after.txt`), and `~/fingerprint_{before,after}_2026-11-18.txt`.

- Part A (05:17:24-05:19:54 UTC): VM on `6e5e71a`, clean, active. Backup `db_20261118_051724.sql` (ends "dump complete") and `files_20261118_051724.tar.gz` (861 MB), 05:17:24-05:19:48; available memory never under 206 MB, swap free never under 874 MB, service active in all 29 samples, no out-of-memory line. Fingerprint, 28 tables (`config_rules` 1209 rows, `09aa613c...1d`). Bundle fetched as `release/2026-11-18-npd-option-rules` = `7d020cc`, 7 files. Rehearsal: none, as planned. (Two misses in the script, neither a check that failed: listing `/opt/backups` needs `sudo`, so the backup was checked again with `sudo` and appended to `part_a.out`; `git bundle verify` was run outside the repository, and the fetch plus the hash check stand in for it.)
- Part B (05:20:42 UTC): fast-forward `6e5e71a..7d020cc`, no tracked edits after, `requirements.txt` unchanged, no migration. Restart 05:20:42; **login 200 after 24 s**. `static/app.js` (`fab5ed73...60e6`) and `static/style.css` (`c5e4ff07...3e40`): blob, disk and served identical, and again from outside through `https://quotes.example.com`. Without a session, `GET /api/specsheets/281/accessories?only=hidden` and `GET /api/config-rules` answer 401. Journal since the restart, re-read at 05:22:10: 9 requests, 0 5xx, no error lines, no out-of-memory line; Caddy's journal shows no upstream errors.
- **Fingerprints: all 28 tables identical**, `config_rules` included (1209 rows, same md5). 0 activity rows after the restart.
- Memory: before the switch the service held 400 MB, workers 188 and 193 MB, 230 MB available. 53 s after: 275 MB, workers 142 and 140 MB, 371 MB available. 88 s after: 244 MB, workers 111 and 114 MB, 396 MB available.
- Not exercised on live: the Rules section and the accessory filtering need a session; proven locally (the change record's log). No rule was created.
- Not run: `backfill_duragreen_ip_options.py` and `backfill_duragreen_spec_values.py`, which still await their own yes.

Prepared on 18 Nov from the main folder, read-only on the VM at 05:16:18 UTC:
- VM on `6e5e71a382cd5baa538453e255a29380db87bed1`, no tracked edits, service active, no error lines in the journal in 24 h, no out-of-memory line in the kernel log in 48 h. `6e5e71a` is an ancestor of `7d020cc`. PASS
- Scope: 2 commits, 7 files (`app/main.py`, `static/app.js`, `static/style.css` and four docs). No requirements, migration, backfill or export file. PASS
- Gate on `7d020cc`: `gate.py --full --base 6e5e71a`: 0 FAIL, 2 WARN (run on `main`, because `main` already holds the release; escaping names `${ruledNote()}`, answered in the record: HTML built by `ruledNote`, which escapes each name), special cases none added, worst memory scenario `ask_index` 187.6 MB. Its scope line counts 9 files because two untracked `.claude/skills/lesson/` files are in the folder; the commit range holds 7, and that is what the VM receives. PASS
