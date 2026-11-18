# Release 2026-11-05: DuraGreen Configure and Search reach every product (code only)

Live before: `dae90e498e14349dc225dec664af03010dac0e09`   Release: `9b65fe6654855f9cdbe93904b8fad6127104e275`   Branch: `release/2026-11-05-duragreen-configure` (on the VM only; locally this is `main`)
Status: live (9b65fe6), code only; the data step awaits the owner's yes
Approved: "commit the changes to main, and yes on oracle as well, also improve the latency of the app", 2026-11-05

**What that yes answered.** The owner's words do not repeat the hash. They directly answered this question, put to them verbatim by the coordinating agent: "Do you approve releasing commit `9b65fe6` to the live server, code only? Reply "yes, release 9b65fe6" and it goes ahead". With it they had the list of what works straight away, what will not show yet, the 54 MB of memory, the backup, the downtime, the rollback, the saved-CRI oddity and the workers' growth. `9b65fe6654855f9cdbe93904b8fad6127104e275` is the only commit the question names, so the yes is taken as being for it. It covers code only: no data script has a yes. "Commit" is not "push", so nothing goes to GitHub. The latency request in the same sentence is separate work and not part of this release.

**The earlier words, which were not taken as approval.** Earlier on 2026-11-05 the owner said: "push this to oracle". That directly answered a message describing this work (the IP change at `b72a632` and the Configure and Search change, then uncommitted), which said: "Should I commit and push this? Going live also needs your yes, plus a separate yes for each of the two data updates after you've seen their dry runs." Those words name no hash because the commit did not exist yet; the coordinating agent committed the working tree as `9b65fe6` afterwards, in response to that instruction. RELEASE.md asks for a yes to the release's commit hash, so the release stopped before anything changed on the VM and asked again.

## Changes
- docs/changes/2026-11-04-duragreen-ip-options.md - `b72a632`, status ready: an IP rating printed as two values becomes two choices, in Add Product and in NPD. Shows on live only after its data script.
- docs/changes/2026-11-05-duragreen-configure-exhaustive.md - `9b65fe6`, status ready: Configure and Search reach every DuraGreen product and every value it offers. Part works on code alone, part needs its data script. Its Status line still says "uncommitted", which is out of date: it was committed as `9b65fe6`.
- No record: `dc0197e`, closing the 4 Nov release. Five files, all docs (`FinalDeployment.md`, three change records, one release note), so this is noted rather than a stop.

**Works with code alone:** Wall Wash under Downlight, every product under its maker's kind, the bezel, baffle and version questions, and Search.

**Will NOT show on live until the two data scripts run:** the IP choices; the CRI, beam and control spellings; the removal of the misread controls (`110` on 55 Adjustable, `2 Step MacAdams` on 57 Straight Adjustable, and the run-on Casambi names).

Between the code and the data step, one known oddity from the repo-reviewer's final read: a saved bare `98` or `92` on a product still holding `98Ra`, in a selection where another product lists `CRI 98`, opens on `CRI 98`, and the first tweak deselects the product with a message. No wrong line is written; at most 12 lines locally; the data step removes it. The record's Search counts were measured with the local data step already run, so Search's counts on code alone have not been measured.

## Scripts on the VM
**None run for real in this release.** No file under `scripts/migrations/` changed and `requirements.txt` did not change, so no migration and no `pip install`. Neither data script has a yes, so the release changes code only.

Each script runs from `/opt/quotation-tool` under the service's environment, so `.env` is never read or printed:

    sudo systemd-run --wait --pipe --collect -p User=ubuntu -p WorkingDirectory=/opt/quotation-tool -p EnvironmentFile=/opt/quotation-tool/.env -p Nice=19 -p MemoryMax=600M /opt/quotation-tool/venv/bin/python3 <script> [--dry-run]

| Order | Script | Now | Later, each on its own quoted yes from the owner, after they have seen its dry run |
|---|---|---|---|
| 1 | `scripts/backfills/backfill_duragreen_ip_options.py` | `--dry-run` after the switch, once healthy; output in `~/duragreen-configure/dry_run_ip_options.out` | the same command without `--dry-run` (undo: `--undo data/duragreen_ip_options_undo_<time>.json`) |
| 2 | `scripts/backfills/backfill_duragreen_spec_values.py` | `--dry-run` after the switch, once healthy; output in `~/duragreen-configure/dry_run_spec_values.out` | the same command without `--dry-run` (undo: `--undo data/duragreen_spec_values_undo_<time>.json`) |
| - | `scripts/backfills/backfill_duragreen_npd.py` | changed in `b72a632`, **not run, not even as a dry run**: it re-reads the vendor's website, nobody asked for that, and it is heavy | not planned |

Both dry runs load the 600-odd DuraGreen products and then roll back, writing nothing. Skip either and say so if `free -m` shows under 250 MB available at that point. Reload any open NPD tab around a real run: a tab left open and saved afterwards sends back the old values.

## Rehearsal
**Skipped on the VM, as RELEASE.md section 1.5 requires:** `free -m` showed 54 MB available at 11:01:50 UTC on 5 Nov (48 MB at the coordinator's earlier read), under the 250 MB line. No `*_export.py`, `boq_summary.py` or `excel_*` file changed, so there is no export comparison to make.

Instead, locally, in the change record's log: the final code on data shaped as live holds it (the local undo files applied to 436 rows in one rolled-back session): 633 picks across 17 kinds all list products, Beam offers Wall Wash under Downlight, 769 codes with an IP chosen and 0 changed, Ask answers, fingerprints identical.

Third-party checks on the record: the repo-reviewer's final read found no Critical or High; the feature-tester proved the fixes on screen; the shortened Search placeholder was not re-checked on screen.

**Part A's backup on low memory.** Sample `free -m` before, during and after, into `~/duragreen-configure/backup_memory.log`. Last time it started at 102 MB available and its lowest was 95 MB; it now starts at about half that. Stop the backup and report if the service goes inactive, the kernel log shows an out-of-memory kill, or swap free drops under 300 MB (837 MB now). The app is not restarted first to free memory: that is not in the procedure, and it would be extra downtime the owner has not been asked about.

**The bundle.** `git bundle` needs a named ref, so: `git bundle create duragreen-configure.bundle dae90e4..main` while local `main` is `9b65fe6`, then on the VM `git fetch ~/duragreen-configure/duragreen-configure.bundle main:release/2026-11-05-duragreen-configure`, and check that the branch's hash equals the release before merging.

## Expected
- Fingerprints: identical except `activity_log` and `boqs` if people work during the deploy. Any other change is investigated.
- Memory: about 147 MB per worker at import, `library` up to about 169 MB, `ask_index` up to about 187 MB (gate on `9b65fe6`: 187.6 MB, about 375 MB if both workers do it). Now: service 628 MB (`MemoryCurrent`), and the coordinator's read of 358 and 271 MB RSS after 28 hours, against about 145 MB each just after the 4 Nov deploy. No out-of-memory events and no errors in the journal.
- Downtime: ~15 s (18 s last time).
- Served files, sha256 of the release's blobs: `static/app.js` `30758be849dc56ffb4c39317ee7c2ebad772eea5ec3ee1bf2ba585011f31f168`, `static/style.css` `825126a72e67e5395ba6f0ae750c18c73eda5e7840366a2b62f84a0eca441d94`. Also on disk, against their blobs: `app/configurator.py`, `app/duragreen.py`, `app/duragreen_import.py`, `app/duragreen_sheet.py`, `app/main.py`, `app/typology_match.py`.
- Smoke, without signing in and without any AI request: login 200; `POST /api/typologies/resolve-options` with no session is refused. The new behaviour cannot be proved on live without a session, so it will not be exercised there.

## Rollback
git reset --hard dae90e498e14349dc225dec664af03010dac0e09 && sudo systemctl restart quotation-tool   (then check the login page answers; no data steps, as no data changes, and no database restore without the owner's yes)

## Result
**Live since 2026-11-05 11:39:40 UTC as `9b65fe6`, code only.** Every check passed and nothing was rolled back. Output: `~/duragreen-configure/` on the VM (`part_a.out`, `part_b.out`, `backup_memory.log`, the two dry runs).

- Part A: VM on `dae90e4`, clean, active. Backup `db_20261105_111547.sql` (ends "dump complete") and `files_20261105_111547.tar.gz`, 11:15:46-11:26:28; lowest available memory 42 MB, swap free never under 831 MB, no out-of-memory line. **The login page did not answer within 20 s during Part A**, at that memory. Fingerprint, 28 tables. Bundle fetched as `release/2026-11-05-duragreen-configure`, 22 files, no requirements, migration or export file. Rehearsal skipped (55 MB available).
- Part B: fast-forward `dae90e4..9b65fe6`, no tracked edits after. Restart 11:39:40 UTC; **login 200 after 57 s** (18 s on 4 Nov); Caddy logged 35 upstream errors in that window. Served `app.js` and `style.css` and all 22 changed files byte-identical to the release. `POST /api/typologies/resolve-options` with no session: 401. Journal since the restart: no errors, no out-of-memory line. **Fingerprints: 28 tables identical, 0 changed.**
- Memory: before the switch the service held 662 MB, workers 273 and 359 MB, 58 MB available. 2 minutes after: 243 MB, workers 117 and 115 MB, 419 MB available.
- Dry runs on live, nothing written: `backfill_duragreen_ip_options.py` would change 129 products (112 + 14 + 3, as locally); `backfill_duragreen_spec_values.py` would change 302 (CRI 163 + 126 + 2, beam 38, control 6), keeping 0 for a price or code row; 0 price entries on DuraGreen products. Both await the owner's yes.
- Closed on 2026-11-17 by the coordinator: the release manager's session ended after Part B, before section 3, so this result, the deployment row and the record statuses are written from the VM's files. The service has since been restarted once, on 13 Nov at 06:37 UTC, by Ubuntu's daily unattended upgrade (`apt-daily-upgrade.service` re-executed systemd), not by this release. GitHub `main` is still `b72a632`: the owner asked to commit, not to push.


Prepared on 5 Nov from the main folder, read-only on the VM at 11:01:50 UTC:
- VM on `dae90e498e14349dc225dec664af03010dac0e09`, no tracked edits, service active, journal with no errors in 24 h and no out-of-memory events in 48 h. `dae90e4` is an ancestor of `9b65fe6`. PASS
- Scope: 3 commits, 22 files. No requirements, migration, export, `boq_summary` or `excel_*` file. PASS
- Gate on `9b65fe6`: `gate.py --full --base dae90e4`: 0 FAIL, 2 WARN, worst memory scenario `ask_index` 187.6 MB. WARN 1: run on `main`, because `main` already holds the release. WARN 2: two flagged interpolations, a product id number and an `escapeHtml` value, answered in the record's Budgets. Its scope line says 24 files because it counts two untracked `.claude/skills/lesson/` files; the commit range holds 22, and that is what the VM would receive. PASS
