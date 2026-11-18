---
description: Prepare (never perform) a deploy to the Oracle VM
---
Prepare a release to the Oracle VM by `RELEASE.md`, section 1. **Nothing on the VM changes.** The
owner approves each release explicitly, by its commit hash.

Have the `release-manager` agent prepare it (brief it with the branch or commits to ship). It may run
only the read-only VM commands RELEASE.md lists (each `ssh` asks the owner). It writes
`docs/releases/<date>-<slug>.md` and returns:

1. what is live (read from the VM, not from a doc) and the commits to ship, one line each;
2. every script in `scripts/migrations/` and `scripts/backfills/` to run on the VM, in order, with
   its `--dry-run` command and then the real one, and whose yes each needs;
3. the backup, the rehearsal, and the rollback;
4. anything risky: prices, deletions, permissions, a large data rewrite, a migration shipped with a feature.

Then stop, and ask the owner for a yes to that release's commit hash.

$ARGUMENTS
