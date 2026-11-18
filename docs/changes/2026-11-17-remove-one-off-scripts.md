# Remove 21 one-off scripts that have already run

Branch: `chore/remove-one-off-scripts`
Lane: docs
Status: live (e314921)
Asked: "Clean the structure of this repo, remove unnecessary code files", 2026-11-17
Approved: "Delete ~30 one-off scripts" (the owner's answer to the cleanup question, 2026-11-17; retiring old docs and tidying the root were not chosen)
Special cases: +0
Memory: no change expected

## Why

`scripts/` holds 144 files. Many were written once to fix one state of the data in September or October and have
already run. They are still listed beside the tools people do run, so anyone looking for a tool reads past them. Their
code is kept in git history.

## Evidence

- Each removed file is named nowhere else in the repo: not imported by a sibling (checked by module name, whole word),
  not mentioned in any doc, `.bat` or script (`git grep -w <name>`).
- Each one's docstring says it is a one-time fix of past data ("One-time backfill", "One-off correction", or it
  repairs rows saved before a feature existed).
- Kept although they also match nothing: `import_lumenpulse_catalogue.py` (a vendor importer), `dedupe_specsheet_library.py`
  and `warm_image_thumbnails.py`. CLAUDE.md lists importers, dedupes and thumbnail warmers as tools.
  Migrations, price-list builders and the tutorial diagram scripts are not touched.

## Scope

In: delete these 21 files.

- `scripts/backfills/`:
  - backfill_accessories_water_stopper_ip68.py
  - backfill_driver_flag.py
  - backfill_flare_spotlight_prices.py
  - backfill_linear_profile_accessories.py
  - backfill_nyx_accessory_prices.py
  - backfill_nyx_hole_accessory_wattage.py
  - backfill_profile_pictures.py
  - backfill_sera_typology.py
  - backfill_typology_uplight_spot_step.py
  - backfill_underwater_typology.py
  - remove_misspelt_accessory_price.py
  - split_inhouse_accessories_by_product.py
- `scripts/catalogues/`:
  - backfill_duragreen_missing_pdfs.py
  - backfill_exenia_accessories.py
  - backfill_exenia_missing_products.py
  - backfill_exenia_variant_images.py
  - fix_exenia_dark_images.py
  - remove_excluded_duragreen_categories.py
- `scripts/maintenance/`:
  - cleanup_inhouse_unclassified_duplicates.py
  - extract_model_numbers.py
  - repair_flattened_labels.py

Out: the app, data, migrations, and the docs and root files (not chosen).

## Design

Delete only; nothing replaces them. To read one again: `git show f89d9d5:<path>`.

## Acceptance

- [x] A1 `gate.py`: 0 FAIL; every remaining `.py` compiles and every markdown link resolves.
- [x] A2 None of the 21 names appears anywhere in the tree afterwards.

## Risks

None to the running app: no app module imports a script. A re-run of an old fix would need `git show`.

## Rollback

`git revert` the commit.

## Verification log

## Next step

Commit when the owner asks.
