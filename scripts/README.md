# scripts/

One-off and occasional scripts. The app itself never imports anything from here.

| Folder | What goes in it |
|---|---|
| `migrations/` | Schema changes (`migrate_*.py`). Idempotent, safe to re-run, and most support `--dry-run`. |
| `backfills/` | One-off data corrections (`backfill_*.py`). |
| `catalogues/` | Vendor catalogue importers and their helpers. **They import one another** (`from import_exenia_website import ...`), so they have to stay in the same folder. |
| `pricelists/` | Decorative-brand INR price-list builders. |
| `maintenance/` | Audits, dedupes, thumbnail warmers, deploy export/import helpers, repo checks. |

## Running one

From the **repo root**, always:

```bash
python scripts/migrations/migrate_dimension_image.py --dry-run
```

Paths such as `"SampleInputs/..."` resolve against the directory you run the script from, and
`run.bat` plus every doc assume that's the repo root.

## The repo-root shim

Most scripts begin with a block marked `--- repo-root shim`. It's there because running
`python scripts/x/y.py` puts `scripts/x/` on `sys.path`, not the repo root, which causes two
problems:

- `from app.database import ...` would fail with `ModuleNotFoundError`, and
- `Path(__file__).parent` would point at `scripts/x/` instead of the root. Nothing errors: a
  migration looks for `scripts/x/data/app.db`, finds nothing, reports "nothing to migrate",
  and exits successfully.

The shim walks up to the directory that contains `app/`, adds it to `sys.path`, and names it
`_REPO_ROOT`. **In a new script, copy the shim and build paths from `_REPO_ROOT`.**

## Adding a migration

1. Copy a recent one, for example `migrations/migrate_dimension_image.py`.
2. Check whether the change already exists, apply it only if it doesn't, and print what happened.
3. Support `--dry-run`, then run it locally, first with `--dry-run` and then without.
4. On the VM, run it only with approval for that specific deploy (`FinalDeployment.md`).
