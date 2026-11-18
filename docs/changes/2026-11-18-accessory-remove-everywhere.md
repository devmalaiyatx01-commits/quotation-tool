# Removing an accessory from the library takes it off every product that takes it

Branch: `feature/accessory-remove-everywhere`
Lane: feature
Status: live (e314921)
Asked: "Goal: User must be able to remove any accessory from the accesssory database, once removed, it must be removed from the accessory section of every product as well. For example: I need to remove the USP2421 entirely from the database", 2026-11-18
Approved: the request above. It states the behaviour wanted, and this record adds no choice beyond keeping prices and BOQ lines.
Special cases: +0
Memory: no change expected

## Why

NPD > Accessory library offers Delete only for an accessory no product takes. Removing one in use meant opening every
product that takes it, unticking it, saving, and then deleting it. The owner wants one action.

## Evidence

- `accessory_parts.delete` refuses an accessory a product takes, or that a BOQ line was added as. The library list draws
  Delete only when `e.products` is empty.
- "USP2421", read-only on live (18 Nov): the stored code is `USP24X21`, part #171, "'U'-SHAPE PROFILE", Dimension 24mm
  X 21mm. No product takes it, it has no price and no BOQ line was added as it, so today's Delete already removes it.
  Its siblings (#56-#64, U-shape profiles in other sizes) are taken by 2-4 products each.

## Scope

In:
- Delete is offered on every library accessory.
- When products take it, the question names them (up to eight, then "and N more") and says what happens. Confirming:
  - takes it off each product (the library link, its name in the product's accessory list, and the printed
    ACCESSORIES line);
  - then removes it from the library.
- Its price rows go back to List Pricing's waiting list, as the existing delete already does. No price is lost.
- BOQ lines already made are left exactly as they are.
- The activity log keeps, per product, the accessory list and printed line from before, so the removal can be undone
  by hand.

Out:
- Datasheet names never in the library (a product's printed name with no library record) are not matched by name: two
  accessories of one name in different sizes are different records.
- Editing BOQ lines.

## Design

`accessory_parts.delete(db, part, everywhere=True)` calls `take_off_every_product`. For each product it records the
names the library does not back, removes the link, and rewrites the list and printed line with
`write_accessory_list(legacy=those names)`, the same writer a product save uses. When one of two same-named accessories
goes, the other loses its size suffix, as labels always do once a name is unique on the product.

`DELETE /api/accessory-library/{id}?everywhere=true` uses the existing permission ("Create and edit products") and the
existing activity entry, which now says how many products lost it.

## Acceptance

- [x] A1 In-process, one transaction rolled back: U-shape profile 17mm X 15mm (#59) was removed from #153, #154, #297
  and #299. Each lost exactly that entry from its list and printed line, kept its other accessories and every other
  field, and the part and its links are gone. Its 3 price rows are kept, released to the waiting list. A plain delete
  still refuses an accessory in use.
- [x] A2 Browser, real clicks: Delete appears on an accessory in use. Its question names the 4 products and says
  "BOQ lines already made keep it", and Cancel changes nothing.
- [x] A3 Browser: a temporary accessory is removed; a toast confirms it and the activity log records it.
- [x] A4 No page errors.
- [x] A5 gate.py: 0 FAIL. (gate.py --full: 0 FAIL, 1 WARN (grid escaping, answered); worst memory ask_index 187.3 MB, run on the final commit against live's `ef5047a`)

## Risks

- Removing an accessory in use changes products' printed sheets. Mitigation: the question names every product first,
  and the log keeps each product's before-list.
- A BOQ line keeps an accessory id that no longer exists. Nothing reads it except the delete check and the line-save
  cleanup, both of which tolerate it.

## Rollback

`git reset --hard <previous>` and restart. Removals already made are undone from the activity log's before-lists.

## Verification log

2026-11-18, coordinator: `scratchpad/accdel/inproc.py` (rolled back) and `scratchpad/accdel/ui_test.py` (port 8024).
Cleaned by id: the temporary part (#171 locally), its 2 activity rows and user #435. Afterwards there are 97
accessories and 322 links, as before.

## Next step

Commit and release on the owner's yes.
