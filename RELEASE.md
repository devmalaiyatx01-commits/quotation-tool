# Releasing to the Oracle VM

Written for: whoever ships a release. The `release-manager` agent prepares it; the owner approves it.

This is the procedure every release since 2026-10-01 has followed, written down once so that it is
followed, not remembered. [FinalDeployment.md](FinalDeployment.md) describes the server itself and
records what went out. This file says **how** something goes out.

**The two rules that are never bent** (CLAUDE.md 1 and 2): nothing changes on the VM without the
owner's yes to *this* release, and nothing heavy is ever run there (no load tests, and no rehearsal
when the server is short of memory). A yes to an earlier release does not carry over to this one.

## 0. What a release is

- **`main` is what is live.** A release is a reviewed branch (or several, merged into
  `release/<date>-<slug>`) that becomes `main` only once it is running on the VM. Afterwards
  `main` = GitHub = VM, the state today's branch cleanup relied on (2 Nov 2026).
- Every change in it has a change record with `Status: ready`. Write a release note
  `docs/releases/<date>-<slug>.md` from the template at the end of this file.
- Ship **only verified work**, never a working tree. A migration and a risky feature go in separate
  releases where possible, so a rollback is unambiguous.

## 1. Prepare (nothing on the VM changes, no approval needed)

Read-only commands only. Each `ssh` asks the owner first (`.claude/settings.json`).

1. **Read what is live; never take it from a doc.** On 6 Oct a branch made from a stale doc would
   have rolled a deploy back.
   ```bash
   ssh -i ~/.ssh/id_ed25519 ubuntu@quotes.example.com 'git -C /opt/quotation-tool log -1 --format=%H; git -C /opt/quotation-tool status --porcelain --untracked-files=no | head; systemctl is-active quotation-tool; free -m | sed -n 2p'
   ```
   The live commit must be an ancestor of the release (`git merge-base --is-ancestor <live> <release>`)
   and the VM's tree must be clean. Otherwise stop.
2. **Scope.** `git log --oneline <live>..<release>` and `git diff --stat <live> <release>`. Each
   commit belongs to a `ready` change record, and any file that belongs to none is a finding.
3. **Gate.** `python scripts/maintenance/gate.py --full --base <live>` on the release branch: no FAIL,
   and every WARN answered in a record.
4. **Scripts that have to run on the VM.** For every new or changed file in `scripts/migrations/`
   and `scripts/backfills/`, give the exact `--dry-run` command, then the real one, in order. A
   data script needs **its own yes** (memory "deploys leave data untouched"). Without it, the
   release changes code only.
5. **Rehearsal plan.** Part A runs the release against live data without changing anything people
   see. For a change to exports or prices: export every live BOQ under both the live code and the
   release, read-only, and every grand total must match. A **schema change is never rehearsed
   inside a transaction on live** (`ALTER TABLE` locks the table until the transaction ends).
   Rehearse it on a scratch copy instead (`createdb quotation_tool_rehearsal`, `pg_dump | psql`,
   drop it afterwards). Skip the rehearsal and say so if `free -m` shows under 250 MB available.
6. **Rollback.** Write down the previous commit, `git reset --hard <live>` plus restart, and for a
   migration, which backup to restore and how.
7. **Write the release note** and stop. Ask the owner for a yes **to this release's commit hash**.

## 2. Ship it (only after the yes, quoted in the release note)

Work in a folder of its own on the VM, `~/<slug>/`, and keep every output there
(`part_a.out`, `part_b.out`). Scripts are written locally with **LF line endings** (a CRLF script
fails in bash in confusing ways), copied with `scp`, and run with `bash -e`, so the first failure stops them.

**Part A changes nothing people see:**

1. `test "$(git -C /opt/quotation-tool log -1 --format=%H)" = <live>`: the very first line. If the
   VM moved since the plan, stop.
2. Back up: `sudo /opt/backup.sh` (database + files to `/opt/backups`). Reuse a backup under 30
   minutes old if you are re-running Part A.
3. Fingerprint every table, saved as `~/fingerprint_before_<date>.txt`. The SQL comes from
   `python3 scripts/maintenance/fingerprint_tables.py --sql` and goes to psql **on stdin**
   (`sudo -u postgres psql -d quotation_tool -At -F ' ' < fp.sql`). psql running as postgres cannot
   open a file in ubuntu's home directory.
4. Bring the release over as a git bundle (`git bundle create <slug>.bundle <live>..<release>`
   locally, `scp` it), then `git fetch ~/<slug>/<slug>.bundle <release>:<release>` on the VM. The
   release is now there but not yet in use.
5. Run the rehearsal from section 1.5, if there is one.

**Part B is the switch:**

1. `git merge --ff-only <release>` in `/opt/quotation-tool`. Fast-forward only: if it can't
   fast-forward, something is wrong, so stop.
2. `pip install -r requirements.txt` in the venv, **only** if requirements.txt changed (and its
   packages were approved; rule 3).
3. The approved migrations, in order, each one's dry-run first.
4. `sudo systemctl restart quotation-tool`.
5. Checks, all recorded in `part_b.out`:
   - the login page answers 200 (normally 13-14 s after the restart, give up at 60 s);
   - served static files are byte-identical to the release's git blobs (sha256), from outside too;
   - `journalctl -u quotation-tool --since '<restart>'` shows no errors;
   - fingerprints after, compared: `python3 scripts/maintenance/fingerprint_tables.py --compare
     before.txt after.txt`. Every changed table needs an explanation (activity_log and boqs change
     when people work during the deploy), and an unexplained change is investigated;
   - memory: `systemctl show quotation-tool -p MemoryCurrent` and the workers' RSS
     (`ps -o pid,rss,cmd --ppid $(systemctl show -p MainPID --value quotation-tool)`), recorded
     next to the release note's expected numbers;
   - the release's own smoke checks from its change records, without signing in as a real person.
     A check that signs a session cookie for someone else's account on live is not done.
6. **If a check fails:** `git reset --hard <live>`, restart, check the login answers, and tell the
   owner. Restoring the database needs the owner's yes, unless the release's migration is what broke it.

## 3. Close

1. Add a row to the "Deployed" table in [FinalDeployment.md](FinalDeployment.md): date, commit,
   what went out, the evidence (fingerprints, checks, memory, downtime), the rollback.
2. Set the release note and each change record to `Status: live (<commit>)`.
3. Fast-forward local `main` to the release, and push to GitHub when the owner asks. Then
   `main` = GitHub = VM.
4. Delete the merged branches and remove their worktrees. Only `main` should be left.
5. Update memory (what is live, what was learned). If the release taught something, add it to
   TUTORIAL.md.

## Release note template

```markdown
# Release <date>: <what, in plain words>

Live before: `<hash>`   Release: `<hash>`   Branch: `release/<date>-<slug>`
Status: planned | approved | live (<hash>) | rolled back
Approved: "<the owner's words>", <date time>      <- nothing in section 2 runs before this line exists

## Changes
- docs/changes/<record>.md - one line each

## Scripts on the VM
none | each with its --dry-run, then the real command, and whose yes it needs

## Rehearsal
what runs in Part A, and what must match

## Expected
fingerprints: identical except <tables, why>; memory: <MB, from memory_profile.py>; downtime: ~15 s

## Rollback
git reset --hard <live hash> && sudo systemctl restart quotation-tool   (+ data steps, if any)

## Result
filled in after Part B: each check and its output file
```
