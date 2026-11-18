# Nomenclature shows each library accessory's own code, as NPD does

Branch: `fix/nomenclature-library-codes`
Lane: fix
Status: live (e314921)
Asked: "the nomenclature tab shows that some accessories doesnt have any nomenclature, but the NPD tab is clear with the naming, fix this, every U shape profile has already been named as USPNNXYY format, the nomenclature tab must be consistent with the NPD tab as well. Fix this on both main and oracle. Context: Data consistency accross the whole app", 2026-11-18
Approved: the request above (fix lane)
Special cases: +0
Memory: no change expected

## Why

Nomenclature's Accessories value table showed "'U'-SHAPE PROFILE (17mm X 15mm)" and its siblings as MISSING, and
offered "Use USPM". In NPD > Accessory library the same accessory is coded USP17X15. The owner reads two screens that
disagree about the same data.

## Evidence

- The value table (`npd_codes` > `build()` in `app/main.py`) codes each value with `ctx.code_for`, which reads the
  studio's code table (`field_codes`) only.
- A InHouse accessory's code lives on its library record (`accessory_parts.code`). A product's code already uses it
  (`product_codes.with_library_accessories`), but the value table never asked.
- Local copy, InHouse: 22 accessory values were shown without a code on `main`.
- The brand cache's fingerprint (`product_codes._FINGERPRINT_SQL`) did not cover accessory codes. A code edited in NPD
  would therefore not have reached Nomenclature until something else changed.

## Scope

In:
- The Accessories value table shows the library code of each accessory a product lists under that name, read-only,
  with a "from library" badge. Several codes are shown for a name shared by several (the first two, the rest on
  hover). A library accessory with no code says "missing in library". Codes are not typed here, because a typed code
  is never used for a library accessory.
- The brand cache's fingerprint covers accessory codes and links.

Out: the duplicate-code report and the SKU codes, which are unchanged (accessories are not part of a configuration).
No data is changed.

## Acceptance

- [x] A1 Every U-shape profile row shows its USP code with source "library"; the 17mm X 15mm row reads USP17X15, as in
  NPD.
- [x] A2 InHouse accessory values without a code: 22 on `main`, 0 here.
- [x] A3 Every other attribute's values, the report and the duplicates equal `main`'s (`/api/npd/codes`, two servers,
  same database).
- [x] A4 A code edited through NPD's accessory route shows in Nomenclature on the next read.
- [x] A5 The table shows the codes with "from library" on screen, with no page errors. gate.py 0 FAIL.

## Rollback

`git reset --hard <previous>` and restart. No data.

## Verification log

2026-11-18, coordinator, `scratchpad/nomen/test.py` (branch on 8027, `main` on 8028): A1-A5 passed. Part #59's code
was changed to test A4 and restored by id to USP17X15. 1 activity row and user #440 were deleted.

## Next step

Into the release batch; deploy on the owner's yes.
