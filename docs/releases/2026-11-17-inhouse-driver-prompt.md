# Release 2026-11-17: adding a InHouse downlight asks "Add a driver?" (code only)

Live before: `9b65fe6654855f9cdbe93904b8fad6127104e275`   Release: `6e5e71a382cd5baa538453e255a29380db87bed1`   Branch: `release/2026-11-17-inhouse-driver-prompt` (on the VM only; locally this is `main`)
Status: live (6e5e71a), code only
Approved: "Yes, deploy 6e5e71a", 2026-11-17

**What that yes answered.** The owner's words name the hash. They answered this question, put to them by the coordinating agent: "Deploy commit 6e5e71a (the InHouse downlight 'Add a driver?' question; the server moves from 9b65fe6 to 6e5e71a: one front-end file plus docs, no data, no migration) to the Oracle VM now?". Earlier the owner wrote "push this change to main and oracle"; GitHub `main` is already `6e5e71a`. The yes covers code only: it does not cover the two pending DuraGreen data scripts from the 5 Nov release, and they are not run.

## Changes
- docs/changes/2026-11-17-inhouse-downlight-driver-prompt.md - `6e5e71a`, status ready: after a InHouse downlight is added from Add Product in a detailed BOQ, a window asks "Add a driver?"; Yes opens the driver configurator under it. `static/app.js` +39 lines, nothing on the server.
- No record: `f89d9d5`, closing the 5 Nov release. Four docs files (`FinalDeployment.md`, two change records, one release note), so this is noted rather than a stop.

## Scripts on the VM
none. No file under `scripts/migrations/` or `scripts/backfills/` changed and `requirements.txt` did not change. The two DuraGreen data scripts waiting since 5 Nov (`backfill_duragreen_ip_options.py`, `backfill_duragreen_spec_values.py`) still need their own yes and are not run, not even as a dry run.

## Rehearsal
**None on the VM.** `free -m` showed 204 MB available at 07:53:27 UTC, under the 250 MB line, and no `*_export.py`, `boq_summary.py` or `excel_*` file changed, so there is nothing to compare. The behaviour was proven locally (the change record's log: feature-tester, real clicks, 28 tables identical).

**Part A's backup on low memory.** Sample `free -m` every 5 s during it, into `~/inhouse-driver-prompt/backup_memory.log`. On 5 Nov it began at 58 MB available, bottomed at 42 MB and the login page stopped answering for 20 s. Today it starts at about 200 MB. Stop and report if the service goes inactive, the kernel logs an out-of-memory kill, or swap free drops under 300 MB (871 MB now).

**The bundle.** `git bundle create inhouse-driver-prompt.bundle 9b65fe6..main` while local `main` is `6e5e71a`; on the VM `git fetch ~/inhouse-driver-prompt/inhouse-driver-prompt.bundle main:release/2026-11-17-inhouse-driver-prompt`, and check its hash equals the release before merging.

## Expected
- Fingerprints: identical except `activity_log` and `boqs` (and their lines) if people work during the deploy. Any other change is investigated.
- Memory: unchanged by a front-end change (gate's worst scenario `ask_index` 187.6 MB). Now: service 407 MB (`MemoryCurrent`), workers 193 and 194 MB RSS, 204 MB available, swap used 152 MB.
- Downtime: ~15-60 s (18 s on 4 Nov, 57 s on 5 Nov).
- Served files, sha256 of the release's blobs: `static/app.js` `37b11ff0055dde987ea1dec5e1b4c289d35a2eadc1779c68cc3198fcac145bbc`, `static/style.css` `825126a72e67e5395ba6f0ae750c18c73eda5e7840366a2b62f84a0eca441d94` (unchanged).
- Smoke, without signing in: login 200; `/static/app.js` from outside matches the blob and contains `DRIVER_PROMPT_RULES`. The question itself needs a session, so it is not exercised on live.

## Rollback
git reset --hard 9b65fe6654855f9cdbe93904b8fad6127104e275 && sudo systemctl restart quotation-tool   (then check the login page answers; no data steps, as no data changes)

## Result
**Live since 2026-11-17 08:26:07 UTC as `6e5e71a`, code only.** Every check passed and nothing was rolled back. Output: `~/inhouse-driver-prompt/` on the VM (`part_a.out`, `part_b.out`, `backup_memory.log`), and `~/fingerprint_{before,after}_2026-11-17.txt`.

- Part A (07:54:48-07:57:19 UTC): VM on `9b65fe6`, clean, active. Backup `db_20261117_075449.sql` (ends "dump complete") and `files_20261117_075449.tar.gz`, 07:54:49-07:57:10; available memory never under 218 MB, swap free never under 862 MB, service active in all 28 samples, no out-of-memory line. Fingerprint, 28 tables. Bundle verified and fetched as `release/2026-11-17-inhouse-driver-prompt` = `6e5e71a`, 6 files. Rehearsal: none, as planned.
- Part B (08:26:07 UTC): fast-forward `9b65fe6..6e5e71a`, no tracked edits after, `requirements.txt` unchanged, no migration. Restart 08:26:07; **login 200 after 18 s**. `static/app.js` (`37b11ff0...5bbc`) and `static/style.css` (`825126a7...1d94`): blob, disk and served identical, and again from outside through `https://quotes.example.com`; the served `app.js` contains `DRIVER_PROMPT_RULES`. Journal since the restart, re-read at 08:33:45: 31 requests, 0 5xx, no error lines, no out-of-memory line; Caddy's journal shows no upstream errors.
- **Fingerprints: 24 tables identical, 4 changed**, all explained by one person working before the switch: `activity_log` 2927 -> 2962 (+35), `accessory_parts` 97 -> 98 (+1), `accessory_links` 322 -> 332 (+10), `specsheets` 1584 -> 1584 (rows updated). The 35 new activity rows are all user #4, 08:01:55-08:25:00, before the 08:26:07 restart, so under the old code: 1 `accessory.create` and 2 `accessory.update` ("'U'-SHAPE PROFILE"), 17 `npd.update` and 15 `npd.status` (Vena S and Filo S products). 0 activity rows after the restart. The release changes `static/app.js` only and writes nothing to the database. The 29 minutes between the two fingerprints (Part B waited for its approval prompt) is why people's work shows in the diff.
- Memory: before the switch the service held 489 MB, workers 234 and 232 MB, 119 MB available. 46 s after: 300 MB, workers 141 and 143 MB, 362 MB available. 7 minutes after: 316 MB, workers 152 and 148 MB, 327 MB available.
- Not exercised on live: the "Add a driver?" window needs a session; proven locally (the change record's log).
- Not run: `backfill_duragreen_ip_options.py` and `backfill_duragreen_spec_values.py`, which still await their own yes.

Prepared on 17 Nov from the main folder, read-only on the VM at 07:53:27 UTC:
- VM on `9b65fe6654855f9cdbe93904b8fad6127104e275`, no tracked edits, service active, no error lines in the journal in 24 h, no out-of-memory line in the kernel log in 48 h. `9b65fe6` is an ancestor of `6e5e71a`. PASS
- Scope: 2 commits, 6 files (`static/app.js` +39 and five docs). No requirements, migration, backfill, export or server file. PASS
- Gate on `6e5e71a`: `gate.py --full --base 9b65fe6`: 0 FAIL, 1 WARN (run on `main`, because `main` already holds the release), special cases none added, escaping PASS, worst memory scenario `ask_index` 187.6 MB. Its scope line counts 8 files because two untracked `.claude/skills/lesson/` files are in the folder; the commit range holds 6, and that is what the VM receives. PASS
