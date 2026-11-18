# Context pack: the backend

Read this when you are changing routes, permissions, logging, models, sessions or caches.
The rules themselves are in CLAUDE.md, "How code is written here". This pack says where they live.

## Where things are

- **Every route is in `app/main.py`**, ~9,500 lines. Never read it whole:
  `python scripts/maintenance/code_map.py app/main.py --routes --grep <word>`.
- **Permissions.** `app/main.py::_required_permission` maps `(method, path)` to a permission key
  from `app/main.py::PERMISSIONS`, and `app/main.py::AuthGuardMiddleware` enforces it on every
  request. It also re-checks the account each time (`app/main.py::_account_state`), so a person
  switched off is stopped at their next click. A new route = one line in `_required_permission`.
  `app/main.py::user_can` is for *showing* things; it is not a guard.
- **Activity log.** `app/main.py::_ACTIVITY_ROUTES` is a table of (method, path pattern, action,
  label). `app/main.py::_activity_route` reads it, and `app/main.py::log_activity` writes the row.
  **GET downloads are logged too** (the export routes are in the table). Any other POST, PUT,
  PATCH or DELETE that is not listed is logged as "Changed something", which tells nobody anything,
  so every new write route gets a real line.
- **Who is asking.** `app/main.py::effective_user_id`: while an admin previews someone else, this
  is that person, and `app/main.py::_identity` gives both ids. Sessions are a signed cookie
  (Starlette `SessionMiddleware`) holding `user_id`.
- **Database.** `app/database.py::get_db` gives a route its session, and `app/database.py::SessionLocal`
  gives a script or background check its own. `DATABASE_URL` comes from the environment; never
  read `.env*`. `DATA_DIR` is the checkout's own `data/`, but pictures are found through paths
  stored on each specsheet, so a worktree still sees them.
- **Models.** `app/models.py`: `app/models.py::BOQ` -> `app/models.py::BOQFloor` ->
  `app/models.py::BOQRoom` (an "area") -> `app/models.py::BOQItem`; products are
  `app/models.py::Specsheet`, prices `app/models.py::PriceEntry`, vendor codes
  `app/models.py::VendorOrderCode`. Six relationships use `cascade="all, delete-orphan"`: move a
  child by reassigning its parent, never by removing it from a list.
- **Turning rows into JSON.** `app/main.py::boq_detail`, `app/main.py::item_dict`,
  `app/main.py::specsheet_summary` and `app/main.py::specsheet_detail`. A new field goes here once,
  not into each route.
- **Lookups.** `app/main.py::get_or_404` for one row. `app/main.py::get_boq_or_404` loads a whole
  BOQ (floors -> areas -> items -> specsheet) in a handful of queries instead of one per row: use
  it for anything that walks a BOQ. Every floor, area or item change calls `app/main.py::touch_boq`,
  so the BOQ list's "Updated" column stays true.

## Caches: memory that every worker keeps

Each worker is a separate process with its own copy of every cache below. On the VM that means x2.

| Cache | Bound | Measured |
|---|---|---|
| `app/assistant.py::index_for` (Ask) | one index per discipline, rebuilt every 120 s, never dropped | +39 MB retained per worker |
| `app/accessories.py::build_cached` | one catalogue per discipline, 60 s | inside the NPD/Library numbers |
| `app/main.py::_image_version_cache` | one short entry per picture path served, refreshed after 30 s, never dropped | small |
| `app/catalogue_query.py::catalogue_for` | one compact catalogue per discipline, rebuilt after 120 s, kept until replaced | 5.9 MB for 1,229 products (22.3 MB peak while building) |
| `functools.lru_cache` in `app/duragreen.py`, `app/match_index.py` | up to 65,536 entries each | not measured separately |
| `functools.lru_cache` on `typology_key` and `category_kind` (`app/typology_match.py`) | 4,096 entries each | measured in `2026-11-17-configure-latency.md` |

A new cache states its bound and its measured size in the change record (DEVELOPMENT.md 4).
Remeasure with `python scripts/maintenance/memory_profile.py`.

## Rules that bite here

- A permission check written inside an endpoint is a finding. So is a button hidden in app.js used
  as the only protection.
- A route that writes and also streams a file closes its session before it returns
  (`app/main.py::export_boq_route` shows why: `db.rollback()`, then the one write, then `db.close()`).
- `None` reaching arithmetic or `.lower()` has crashed routes before. Coerce at the edge
  (`app/main.py::_coerce_price`).
- Brand checks (`if make == ...`) do not go in here. See [npd-and-vendors.md](npd-and-vendors.md)
  and DEVELOPMENT.md 5.

## How to check a change here

- `python scripts/maintenance/gate.py` (quick), then `--full` for memory.
- Call the real route on a local server as a temporary user (the `feature-tester` agent knows how),
  and check its permission: once as a member who lacks the key (403), once as one who has it.
- For a new write route: the activity row it writes, its label, and that cleanup deleted it by id.
