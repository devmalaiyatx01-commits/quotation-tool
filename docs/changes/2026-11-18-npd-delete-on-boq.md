# An admin can delete an NPD product that BOQs use; those BOQs keep it and export as before

Branch: `feature/npd-delete-on-boq`
Lane: feature
Status: live (e314921)
Asked: "Also, allow the admin to delete any product from NPD even if it is listed on some BOQ. The BOQ will be exported as is, just the user wont be able to further edit that using the configurator.", 2026-11-18
Approved: the request above. It states the behaviour; the design below only chooses how to keep "exported as is" true.
Special cases: +0
Memory: no change expected

## Why

Deleting a discontinued or mistaken NPD product was refused while any BOQ line used it ("Move those lines ... then
delete it"). An admin had to edit people's BOQs first.

## Evidence

- `npd_delete_product` refused with 409 when BOQ lines pointed at the product. Its own comment explains why: a line
  points at its product for its picture, its spec sheet and its options.
- Every place a product is offered for a BOQ reads approved products only: the Library and pickers
  (`list_specsheets`), Configure (`configurator._products`), Ask, Match and the catalogue query.

## Scope

In:
- Delete, admins only as before, works on any NPD product.
  - On no BOQ: erased, as today.
  - On BOQ lines: retired. Its row stays with a new status, "deleted", so the lines keep their picture, sheet,
    options and price, and every export is unchanged. It leaves NPD, the Library and every picker; NPD will not
    open, edit or approve it again.
  - Its links to library accessories go, so the accessory library stops listing it. Prices and rules are untouched.
- On those BOQ lines, Edit > "Use Configurator" is disabled with the reason. A Reconfigure from anywhere is refused
  with a sentence saying the line is kept as it is. Editing the line by hand still works.
- Product codes treat a deleted product like a held one: it can neither clash nor be missing a code.
- The activity log records the retirement, its BOQ lines and BOQs, and the accessory links it had.

Out: undoing from the screen (the log holds what is needed); DuraGreen products (not deleted from this route).

## Design

A status rather than erasure, because "exported as is" needs the row: the export reads the product for the line's
picture (`line_diagram_for`) and sheet. `NPD_DELETED = "deleted"` is set only by the delete route, never by the status
route (`NPD_STATUSES` is unchanged). `_npd_live_or_404` keeps NPD's product read, update, status and delete away from
it. `item_dict` sends `product_deleted`.

## Acceptance

- [x] A1 Browser, admin, NPD > Delete on NYX -Mini (#330, on 47 lines in 9 BOQs). The question says BOQs keep it; the
  toast says "kept as it is on 47 BOQ lines"; it is gone from NPD and from `/api/specsheets`; NPD answers 404 for it;
  its 47 lines still point at it.
- [x] A2 BOQ and Quotation exports of all 9 BOQs are identical, cell for cell and in picture count, before and after
  (rolled-back transaction).
- [x] A3 Its lines carry `product_deleted`. Edit > Use Configurator is disabled with "This product was deleted from
  NPD..." and a Reconfigure call is refused with a reason.
- [x] A4 No page errors. gate.py 0 FAIL.

## Risks

- A retired product keeps its row, so its name stays in the database. It cannot be picked, and the status is visible
  in the log.
- A Reconfigure of such a line is no longer possible. That is what was asked; editing by hand remains.

## Rollback

`git reset --hard <previous>` and restart. Products retired meanwhile keep status "deleted", which the old code shows
in NPD as an unknown status; set it back to "approved" by id from the log if needed.

## Verification log

2026-11-18, coordinator:
- `scratchpad/npddel/ui_test.py` (port 8026, real clicks): A1, A3 and A4 passed. #330 was restored by id afterwards
  (status approved, its 3 accessory links #457-459). 1 activity row and user #438 were deleted.
- `scratchpad/npddel/export_check.py` (rolled back): A2 passed for BOQs #56, #57, #58, #64, #65, #68, #69, #71 and
  #79, both kinds, including #79's 1,763 / 2,757 cells and 77 pictures.

## Next step

Commit and release on the owner's yes.
