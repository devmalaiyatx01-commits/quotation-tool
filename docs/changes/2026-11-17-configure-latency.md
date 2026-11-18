# Faster Add Product: fewer round trips per Configure click, and typing in Search keeps up

Branch: `fix/latency-20261105`
Lane: fix
Status: live (e314921); A4 an owner exception
Asked: "commit the changes to main, and yes on oracle as well, also improve the latency of the app", 2026-11-05
Approved: the request above (the fix lane needs no separate yes before building; a deploy needs its own)
Special cases: +0
Memory: smaller, see Budgets

## Why

A designer changing a value in Add Product > Configure waits for the panel, then the product, then its options, then
the description, one after another, on every click. From the studio's connection each of those is a round trip to
Hyderabad of 160-340 ms, so a click takes about a second before the line settles, though the server works for
under a tenth of it. Typing in Search after 5 Nov waits about 0.7 s per key.

## Evidence

- Live, 7-17 Nov, from the app's request log (it records requests, not their times): 2,349 requests in 10 days. After pictures
  and page loads, the most used actions are the Configure panel (88), the description and code (77), a product's options (49) and
  adding a line (42). The full product list was fetched 11 times.
- Live's own work is small: the server answers its login page to itself in 2 ms, and its processor ran a test loop faster than
  this laptop (0.76 s against 1.42 s). `sar` over 10 days: about 99% idle, almost no paging.
- The network is most of a click: from this machine a request on an open connection took 0.16-0.34 s, a new connection 1.6-4.0 s
  (TLS included; github.com 0.5-2.0 s and google.com 0.8-1.4 s from the same machine at the same time).
- `static/app.js`: each Configure change awaits `POST /api/typologies/resolve-options`, then `GET /api/specsheets/{id}` for the
  product already selected, then `POST /api/specsheets/{id}/resolve-options`, then `POST .../suggest-description`: four in a row.
  The product is fetched again on every click though it does not change while the window is open.
- Search: every result row looks for its same-named siblings across all results (`pickerDistinguisher(s, matches)`), so 994 rows
  for "3000K" make about a million comparisons: 706-744 ms per key in the 5 Nov test, against 11 ms before Search read specs.
- Server, local profile of the Configure request: 89 ms, of which `_option_words` (built for every match, used only by Search)
  and `typology_key`/`category_kind` over every approved product are about 30 ms. `GET /api/specsheets`: 279 ms, 88 of them in
  `_option_words`.
- Also found, not changed here: during the 5 Nov release's backup live's login page did not answer for 20 s (42 MB free), and
  after its restart the app took 57 s to answer. The same backup runs every day at 02:00 UTC (`/opt/backup.sh`, cron). That is
  the server's settings, an infra change for the owner to decide.

## Scope

In:
- `static/app.js`: the product is fetched once per window; its options are asked together with the panel's request when it
  is still selected, and with the product when it is newly picked. Search groups same-named rows once per keystroke and waits
  for a short pause in typing.
- `app/main.py`: Search's option words are built only for the product list, and `options` loaded only there.
- `app/typology_match.py`: `typology_key` and `category_kind` remember answers they have worked out.

Out:
- What any request returns, apart from `option_words`. It now appears only in `GET /api/specsheets` (the product list
  Search reads). The other answers built on `specsheet_summary` no longer carry it, and nothing reads it there:
  - the Configure matches;
  - `GET /api/specsheets/{id}`;
  - the specsheet save and upload answers;
  - each specsheet inside `/api/list-pricing`.
- The backup and the server's settings. The first-connection time, which is the network's.

## Design

Round trips, not server time, are what a person waits for here, so the change removes waits rather than tuning queries.
A product does not change while the Add Product window is open, so one fetch serves every later click. Its options depend only on
what the panel sends, so they are asked at the same moment as the panel's own request and used if the panel's answer leaves the
product selected and the state sent was exactly the state that would have been sent after; otherwise they are asked again, as
before. The description still waits for the options, since it is built from them. Per click: four round trips to two.

Rejected: one combined endpoint for panel, options and description (a bigger change to the most used screen, for one more trip);
HTTP caching of the product (the window's own cache is simpler and cannot go stale across windows).

## Acceptance

- [x] A1 With 200 ms of network delay emulated, a Configure change on a selected product settles (description updated) in about
  half the time it did on `9b65fe6`, and sends 2 requests in sequence instead of 4.
- [x] A2 Every value, match, description, code and line is the same as on `9b65fe6` for the same clicks: Duragreen 75 Wall Wash,
  a InHouse NYX -Midi, an Exenia product, a Reconfigure of a saved line, and a pick that drops the selected product.
- [x] A3 A change whose answer remaps a saved spelling (`state_read`) still uses options asked with the remapped state.
- [ ] A4 (EXCEPTION - 223 ms against 150 ms; main takes 1,354 ms; for the owner to accept in the deploy question) Search: "3000K" responds within 150 ms of the last keystroke; the same rows, order, headings and sibling labels as `9b65fe6`.
- [x] A5 `GET /api/specsheets` returns the same JSON as `9b65fe6`. The Configure matches are the same apart from `option_words`.
- [x] A6 gate.py --full: 0 FAIL; memory no worse. (gate.py --full: 0 FAIL, 1 WARN (grid escaping, answered); worst memory ask_index 187.3 MB, run on the final commit against live's `ef5047a`)

## Risks

- A product edited in NPD in another tab while an Add Product window stays open shows its old details until the window reopens.
  Before, it showed them from the next click.
- A request asked early and not used costs the server a little work. It is only sent while a product is selected.
- If anything assumes the product object is fresh per click, it now gets the same object. Nothing in `static/app.js` writes to it.

## Budgets

Memory: `lru_cache` on `typology_key` and `category_kind`, 4,096 entries each, per worker (two workers on the VM).
Measured 18 Nov: filled from all 340 typology and category names in the library, both together take 82.9 KB; at the
cap, about 1 MB. Recorded in the cache table of `docs/context/backend.md`. Special cases: +0.

## Rollback

`git reset --hard <previous>` and restart. No data.

## Verification log

2026-11-18, answers to the repo-reviewer of 17 Nov (all fixed in `release/batch-20261118`):
1. The first change after a Reconfigure or a pick made outside Configure took 3 round trips. Fixed: the early block also
   fetches the product, and Search, Frequently used and Reconfigure put the product they already hold into the
   window's cache. That cache is now declared at the top of the window, so no path reaches it before it exists.
2. `_option_words` changed case handling. Fixed: back to the set it was, so `GET /api/specsheets` is as on `main`.
3. The record under-described where `option_words` went. Fixed: the full list is under Out.
4. The cache bound and size were missing. Fixed: under Budgets, and in the backend.md cache table.
5. A click during the 120 ms pause could be followed by a redraw. Fixed: clicking a row cancels the pending redraw.
6. The same-name key was copied three times. Fixed: one `sameNameKey` (shared with the environment tag), and the
   stranded comment is back above `pickerDistinguisher`.

2026-11-18, feature-tester on the release batch (`scratchpad/ftb/t_latency.py`, `t_search_api.py`; branch against `main`,
same database, real selects and clicks):
- A1: median 524 ms against 983 ms on `main` (0.53x); 2 requests in sequence against 4.
- A2: every step identical: NYX -Midi (5 steps), DuraGreen 75 Wall Wash (5), Exenia (3), a Reconfigure, and a pick that
  drops the selected product.
- A3: the product is asked with the remapped 4000K; the final form and description equal `main`'s.
- A4: the same 1,156 rows, order, headings and sibling labels as `main`. Speed: "3000K" redraws 223 ms (median) after the
  last keystroke, against 1,354 ms on `main` - six times faster, but short of the 150 ms this record set. Left open; not
  a regression.
- A5: `GET /api/specsheets` the same JSON as `main` (1,232 products); #332 and #586 equal apart from `option_words`.

## Next step

Ships with the release batch of 18 Nov (`2026-11-18-release-batch.md`). Search's last 73 ms (A4) is a follow-up.
