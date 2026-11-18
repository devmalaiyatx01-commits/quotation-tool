# A parameter added under a group is listed in that group, not in a second group of the same name

Branch: `fix/npd-parameter-group`
Lane: fix
Status: live (ef5047a)
Asked: "While defining a new parameter under a group, the parameter goes under the group but an identical separate group is being created instead of the parameter being displayed under the existing group. Fix this. For example: If i add a parameter named as trim details under mechanical parameter it must be listed under the Mechanical Parameter Group only instead of creating a duplicate group and fitting in there. Fix this on oracle and main", 2026-11-18
Approved: the request above (fix lane)
Special cases: +0
Memory: no change expected

## Why

On live, adding TRIM DETAILS under MECHANICAL PARAMETERS showed a second "MECHANICAL PARAMETERS" block at the foot of
the product form, holding only the new parameter (owner's screenshot, 18 Nov).

## Evidence

- `npd_add_parameter` numbered a new parameter `max(sort_order) + 1`, after every row of the typology, so it landed
  after OPTICAL, SOURCE and POWER SUPPLY.
- `GET /api/npd/parameters` started a new group block whenever the next row's group differed from the previous one.
  So a row of an earlier group, at the end, opened a second block of that name.
- The generated spec sheet groups by name already (`params.setdefault(group_name)`), so only the form was affected.

## Scope

In:
- `GET /api/npd/parameters` groups by name (ignoring case and spacing), in the order each group first appears.
  Parameters already misplaced on live show inside their group, with no data change.
- Adding a parameter places it after the last parameter of its group and renumbers the template 0..n-1, as moving a
  parameter does. A group name typed in another case takes the existing group's spelling.

Out: changing rows already on live.

## Acceptance

- [x] A1 A parameter numbered after every group, as the old code added it, shows inside its own group; the groups
  stay the four of the Downlight template.
- [x] A2 Adding "ZZ TRIM DETAILS" under "Mechanical Parameters": it is saved under "MECHANICAL PARAMETERS", listed in
  that group, and the groups stay four.
- [x] A3 The product form (NPD > #330) shows MECHANICAL PARAMETERS once, with the new parameter in it.
- [x] A4 No page errors; gate.py 0 FAIL.

## Risks

Adding a parameter renumbers the typology's other rows, without reordering them, as a move already did.

## Rollback

`git reset --hard <previous>` and restart.

## Verification log

2026-11-18, coordinator, `scratchpad/pgroup/ui_test.py` on port 8025: all checks passed. Cleaned by id: template rows
#400-401, 1 activity row and user #436. Every Downlight template position was restored by id (compared equal).

## Next step

Commit, then release on the owner's yes.
