# Context pack: data, migrations, backfills and scripts

Read this when you are writing or running anything under `scripts/`, changing the schema, or
touching data people entered, above all prices.

## Where things are

| Folder | What | Its rule |
|---|---|---|
| `scripts/migrations/` | schema changes | checks before applying, `--dry-run`, prints what it did, safe to re-run |
| `scripts/backfills/` | one-off data fixes | `--dry-run` reviewed by the owner first when prices are near |
| `scripts/catalogues/` | vendor importers | they import each other, so keep them together |
| `scripts/pricelists/` | decorative INR price lists | one brand at a time, only when asked |
| `scripts/maintenance/` | audits, the gate, the tools below | read-only unless the docstring says otherwise |

Every script starts with the **repo-root shim** (copy it from any script in `scripts/maintenance/`)
and builds paths as `_REPO_ROOT / "data" / ...`. `Path(__file__).parent` is `scripts/<dir>/`, not the
root, and fails silently (verify_repo.py checks for it). How to write a migration:
[scripts/README.md](../../scripts/README.md).

## The tools for proving data was left alone

- `python scripts/maintenance/fingerprint_tables.py > before.txt`, then the same into `after.txt`,
  then `--compare before.txt after.txt`: row count + md5 of every table, read-only. Locally this is
  the evidence that a change, a test run or a backfill touched only what it should.
  On the VM it is RELEASE.md's before/after (`--sql` form, through psql).
- `python scripts/maintenance/memory_profile.py`: writes one temporary user to the local
  database and deletes it by id. Every other tool here is read-only.

## Rules that bite here

- **Prices already entered must never vanish** (CLAUDE.md rule 4): no existing price becomes NULL,
  0 or deleted, and an overwrite shows the old value in its dry run.
- **Clean up by id, never by text match.** Record `max(id)` of each table you will write before
  you start, and delete only `id > floor` rows that are yours. A `LIKE '%ZZ%'` cleanup once deleted
  350 real activity rows.
- **The local database is a copy of live** (synced 9 Oct), and people's real work is in it.
  Treat it as you would live: tests make their own rows and remove them.
- **A heuristic may merge, never delete.**
- **Never read `.env*`.** `DATABASE_URL` and `SESSION_SECRET` come from the Windows user environment.

## How to check a change here

- `--dry-run` first, always, and paste its summary into the change record.
- Fingerprints before and after a real run: every changed table is the one you meant, with the
  row count you expected.
- On the VM a data script runs only with the owner's separate yes (RELEASE.md, section 1.4).
