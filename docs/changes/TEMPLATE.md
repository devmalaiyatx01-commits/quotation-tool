# <What changes, in plain words>

Branch: `<lane>/<slug>`
Lane: feature | fix | data | schema | infra | docs
Status: proposed
Asked: "<the owner's words>", <date>
Approved: <the owner's yes to the design, quoted, with the date - feature, data, schema and infra lanes, before building>
Special cases: +0
Memory: no change expected

<!-- Status moves: proposed -> approved -> building -> verifying -> ready -> live (<commit>), or dropped (why).
     Delete these comments as you fill the record in. The gate reads "Branch:" and "Special cases:". -->

## Why

<!-- The problem, for the person who has it. Not "add X", but "a designer does Y and Z happens". -->

## Evidence

<!-- What shows the problem is real and how big it is: activity_log counts, a reproduction with
     its exact input and output, a measurement, the owner's report quoted. For a fix, the failing
     case before any code changes. -->

## Scope

In:
Out:  <!-- what this change deliberately does NOT do - stops a change growing while it is built -->

## Design

<!-- Where the change goes and why there: the existing mechanism it reuses (code_map / grep found
     it), the module it lives in, the data it reads. If you considered and rejected an approach,
     one line on why. If it adds a special case, a rule written into code, why it cannot be data. -->

## Acceptance

<!-- Observable checks, each one a person or the feature-tester can do and see pass or fail.
     "A designer opens BOQ #79 (221 lines) on a 1366x768 screen and it is usable in under 2 s",
     not "the BOQ is faster". Number them; the verification log refers to them. -->

- [ ] A1
- [ ] A2

## Risks

<!-- What could break, who would notice, and how the change guards against it: prices, permissions,
     other brands, exports, small screens, memory, people's work in progress. -->

## Budgets

<!-- Memory (memory_profile.py: which scenario, before -> after), speed, special cases, lines added
     to app/main.py and static/app.js. "No change expected" is fine when it is true; the gate checks. -->

## Rollback

<!-- How to undo it on the VM: usually `git reset --hard <previous>` + restart. For data or
     schema: which backup restores what, and whether the old code can run on the new schema. -->

## Verification log

<!-- Dated lines, newest last: what was run, the result, where the evidence is.
     2026-11-02 gate.py --full: PASSED, 0 FAIL, 1 WARN (escaping: ${rowsHtml} is built HTML) -->

## Next step

<!-- One line: what the next session does first. This is the handoff - keep it current. -->
