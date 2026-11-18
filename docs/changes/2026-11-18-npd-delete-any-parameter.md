# NPD: any parameter can be removed from a typology, and from every product of it

Branch: `feature/npd-delete-any-parameter`
Lane: feature
Status: live (e314921)
Asked: "Also, allow the user to delete any parameter from any group as well. For example, i want to remove a few parameters from grazers, but there is no option for doing it right now. On main and oracle", 2026-11-18
Approved: the request above; it states the behaviour, and this record adds only the price guard that CLAUDE.md rule 4 requires
Special cases: +0
Memory: no change expected

## Why

A typology's seeded parameters (POWER FACTOR, T.H.D on Wall Grazer / Wall Washer) had no Remove: only a parameter someone
added could go. The owner wants to take parameters off a typology's sheets.

## Evidence

- `npd_delete_parameter` refused any row with `user_added` false, and the form drew Remove only for those rows.
- A product holds a parameter in three places: its value (`fields[key]`), its options (`options[key]`) and its printed
  line (`params[group][label]`, composed on save). The save carries back any submitted value the template does not
  name (`_npd_compose`), so an open form that still holds the value would put it back.
- A priced parameter's adders are rebuilt from the form on save, so removing a priced parameter would drop its prices.

## Scope

In:
- Remove is offered on every parameter to whoever may author products.
- Removing a parameter asks first, naming its group and typology. It then goes, in one step, from:
  - the template;
  - every product of that typology authored in NPD: its value, its options, and its printed line under that group.
  Manufacturer-PDF products are untouched.
- The open form forgets the parameter.
- Refused with the product names while any product of the typology prices it: "Remove those prices first".
- The activity log keeps the template row and what each product held, so a removal can be put back by hand.

Out: Configurator choices people saved in NPD > Configurator (a key no product holds is already dropped there).

## Acceptance

- [x] A1 A seeded parameter (POWER FACTOR, Wall Grazer / Wall Washer) has Remove. The question says it goes from every
  product of the kind; after it the row is gone from the form; the toast says "from 4 products".
- [x] A2 The template row is gone. None of the 4 grazer products holds the value or prints the line. Nothing else on
  them changed, and their approval is untouched.
- [x] A3 A parameter that sets prices (CCT on Downlight) is refused with 409, naming the products, and nothing changes.
- [x] A4 The log entry lists what each product held. No page errors; gate.py 0 FAIL.
- [x] A5 (added 18 Nov after the repo-review) Refused, naming what is in the way, when:
  - a price row is keyed on the parameter (a power supply priced per CONTROL);
  - a required segment of the brand's code structure for the typology spells it (an optional one does not refuse);
  - an NPD rule of this brand uses it (another brand's rule does not refuse).

  A product deleted from NPD neither blocks the removal nor changes.

## Risks

- Removing a parameter changes every product of the kind at once. The question names the typology, and the log keeps
  every value for putting back.

## Rollback

`git reset --hard <previous>` and restart. Parameters removed meanwhile are put back from the log's details.

## Verification log

2026-11-18, coordinator, `scratchpad/pdel/ui_test.py` (port 8035, real clicks), run three times: A1-A4 passed. Each run
restored the template row (#241) and the 4 products (#293, #295, #296, #1524) by id, and they compared equal to their
snapshot. The temporary users (#449-451) and their activity rows were deleted. The second run's single failure was
the check firing before the form redrew; with the redraw awaited it passes.

2026-11-18 evening, the repo-reviewer on the release batch found three cases A1-A4 did not cover. All are fixed in
`npd_delete_parameter`:

1. Retired products were included. A retired product's adder could block the removal for good, because nobody can
   open it to clear the price. A removal could also rewrite a retired product's sheet, which BOQ lines still use. Now
   they are left out, as in the NPD list.
2. The guard read only `price_adders`. A power supply priced per CONTROL (a `PriceEntry` whose config names the key)
   let CONTROL go, and the Configurator would then stop adding that price to quotes. Now any price row keyed on the
   parameter refuses the removal.
3. A required code segment was removed silently, and every code of the typology went blank. Rules were left pointing
   at nothing. Now both refuse, naming the structure or the rule.

`scratchpad/batch/pdel_guards.py` proves A5 in one transaction that is rolled back (commit swapped for flush; 0
temporary rows left). The UI test (A1-A4) passes again on the final code. The owner's grazer case, POWER FACTOR, is
not priced, coded or ruled, and is removed as before.

## Next step

Into the release batch; deploy on the owner's yes.
