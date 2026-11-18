# Deployment Guide: Prototype → Production

This document is the plan for moving the Quotation Tool from "runs on one
person's laptop via `run.bat`" to "a shared, always-on system 20-30 studio
staff use daily, holding confidential company/client data." It covers what
has to change and why, the recommended architecture, a concrete step-by-step
deployment procedure, and how to operate it afterward.

**Status as of 2026-08-06: NOT deployed.** Corrected after this line
previously (incorrectly) claimed a live local Windows-services deployment -
confirmed against the actual machine (`Get-Service` shows no
`QuotationTool`/`QuotationToolCaddy`/`postgresql-quotation-tool` services
exist at all; the only thing listening on port 8000 is a plain
`uvicorn --reload` process matching `run.bat`, not the no-reload
`run_service.bat` a real service would run) that this never actually
happened, or was undone at some point without the doc being corrected. Only
plain Postgres (`postgresql-x64-18`, the stock installer's own service) is
running. Take any other "already live"-sounding language elsewhere in this
file as the plan this was written from, not a status report - Section 5's
step-by-step procedure below has not been executed on this machine.

What *does* already exist, ready to run whenever deployment actually
happens: `setup_production_services.ps1` (registers the three services),
`Caddyfile` (the HTTPS reverse proxy config it would install), `run_service.bat`
(the no-reload production entry point), and `backup.ps1` (the backup
script, not yet scheduled since there's no live data to back up). These are
prepared, not evidence anything is live - see `HOW_TO_RUN.md`'s "Going live
for the team" section to actually run that step.

**Longer-term direction, also not started**: moving hosting off this
laptop entirely, to a cloud VM's Always Free tier - the reason being
local hosting's core operational problem, that the app is only reachable
while this specific laptop stays on. Two complete, from-scratch,
no-prior-experience walkthroughs exist for this (account creation through
ongoing operations), one per provider - **[`oracleDep.md`](oracleDep.md)**
(bigger free VM) and **[`googleCloudDep.md`](googleCloudDep.md)** (smaller
free VM, but no SSH key setup and shares an account with Google Sign-In) -
pick one, not both; `googleCloudDep.md` section 21 has the actual
comparison. Neither has been executed yet - both are finished guides, not
something that's been done. Sections below describe the full
local-deployment plan this was written from.

---

## 1. Where this stands today (the honest baseline)

- A single FastAPI process, started manually via `run.bat`
  (`uvicorn app.main:app --reload`), bound to `127.0.0.1:8000`.
- SQLite file database (`data/app.db`) - no separate database server.
- ~~No authentication or authorization anywhere~~ **Done (2026-08-04)** - see
  section 3.3. Every page and API route now requires a login session; a
  coarse `admin`/`member` role gates who can edit Config Rules/Product
  Codes. Still outstanding: this only matters once the app is actually
  reachable by more than one machine - authentication doesn't substitute for
  the network/hosting restrictions in 3.1, it adds to them.
- **No HTTPS** - plain HTTP only.
- **No process supervision** - if the process crashes, the terminal is
  closed, or the machine restarts, nothing brings it back automatically.
- ~~Unpinned dependencies~~ **Done (2026-08-04)** - `requirements.txt` now
  pins the exact version of each package this app actually imports.
- All data (specsheets, BOQs, uploaded PDFs, extracted images, exported
  files) lives under `data/` on one machine's local disk, with no backups.
- Schema changes are hand-written one-off scripts (`migrate_*.py`,
  `scripts/catalogues/reimport_catalogs.py`, ...) rather than a real migration framework - fine
  when one developer remembers what ran and when; risky for a shared
  production database.
- Built and tested for one user on one machine at a time - concurrent access
  has never been load-tested or explicitly reasoned about.

None of this is a criticism of the prototype - every item above is a normal,
correct simplification for "build fast, validate the idea with one designer
testing it." Production, for 20-30 real users handling confidential company
and client data, is a different set of constraints. This document exists to
close that gap deliberately.

---

## 2. Gap analysis

| Concern | Prototype today | Needed for production | Why it matters |
|---|---|---|---|
| Access control | None - open to anyone who reaches the port | Login required for every page; role separation (who can edit Config Rules/Product Codes vs. who can only build BOQs) | BOQs reveal client names, product specs, and (indirectly) pricing structure; an untrained or uninvited user can also delete real data with one click today |
| Transport | Plain HTTP | HTTPS/TLS everywhere, no HTTP fallback | Company/client data must never cross any network unencrypted |
| Network exposure | Ad hoc / localhost | Reachable only from the company network or VPN - never the open internet | 20-30 internal users don't need public exposure; "unreachable from outside" is the strongest, simplest protection available |
| Database | SQLite, single writer | SQLite in WAL mode (minimum) or PostgreSQL (more headroom) | SQLite serializes writers - fine at light concurrency, but "database is locked" errors become real once several people save at once |
| Process management | `uvicorn --reload`, manual start, dies with the terminal | A supervised service (systemd / Windows service / container) that restarts automatically | 20-30 people relying on this daily can't have "someone closed a terminal window" as a failure mode |
| Backups | None | Scheduled, tested backups of `data/app.db` + `uploads/` + `images/` + `exports/` together | This is the only copy of every specsheet and every BOQ the studio has ever built |
| Dependencies | Unpinned | Pinned exact versions, reviewed before upgrading | An unrelated future install shouldn't be able to silently break or introduce a vulnerable package |
| Migrations | Hand-written one-off scripts | A real migration tool (Alembic) going forward | A shared production database needs an ordered, repeatable change history, not tribal knowledge |
| Secrets | None exist yet | Session/auth secrets in environment variables, never committed to git | Standard practice the moment any credential exists |
| Observability | Console output only | Structured logs to a file; basic uptime/error alerting | With 20-30 users, you need to find out it's down before they tell you |
| Confidentiality posture | Implicit (only one person's machine) | Explicit: who has access, what's backed up where, who revokes access when someone leaves | Formalizing what's currently informal is required once more people and a server are involved |

---

## 3. Architecture recommendation

Given 20-30 internal users, no public-facing requirement, and the existing
stack (FastAPI + SQLAlchemy + a vanilla-JS frontend with no build step), the
recommendation is the smallest set of changes that closes every gap above -
not a rewrite or a different tech stack.

### 3.1 Hosting

**Chosen: Option A, this same development machine (2026-08-05)** - not a
dedicated server/NAS, but the same reasoning applies: it stays on the
office LAN, reachable only from the local subnet (firewall rule in
`setup_production_services.ps1`), data never leaves the building. The
tradeoff called out below is real here too - this machine needs to stay
powered on and network-connected for the team to reach the tool, and
there's no redundancy if it goes down. Worth moving to dedicated hardware
once that becomes the limiting factor, not before.

**Option A - the studio's own server/NAS on the office LAN (preferred if available)**
- Data never leaves the building.
- No cloud costs, no need to reason about a cloud provider's
  shared-responsibility model.
- Access is naturally limited to whoever's on the office network or VPN.
- Downside: someone has to physically maintain the machine (power, OS
  patching); no built-in geographic redundancy.

**Option B - a single small private cloud VM** (e.g. a $5-20/month
DigitalOcean Droplet, AWS Lightsail instance, or Azure B-series VM)
- Sensible if staff work remotely/multi-site, or there's no suitable
  on-prem machine.
- Must be locked down: cloud firewall/security-group rule allowing only the
  office IP(s) or a VPN's IP range, SSH key-only login (no passwords), no
  public HTTP access without auth in front of it.

Either way: **the FastAPI process itself is never directly reachable from
the internet.** A reverse proxy (Nginx or Caddy) is the only thing exposed,
terminates TLS, and even that is restricted to the office network/VPN - not
bound to `0.0.0.0` for the world.

### 3.2 Database: SQLite (WAL mode) by default, PostgreSQL is a `DATABASE_URL` away

For 20-30 users doing BOQ work (mostly reading the library + editing their
own BOQ; rarely two people editing the *same* BOQ at the same instant),
SQLite in WAL mode is genuinely fine as a first production step - not a toy
at this scale. **Done (2026-08-04)**: `app/database.py` sets both of the
following on every SQLite connection (a `PRAGMA` is per-connection, not
database-wide, hence the `event.listens_for(engine, "connect")` hook):

```python
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
```

1. **WAL (Write-Ahead Logging) mode** lets readers proceed concurrently with
   a writer instead of blocking on every write.
2. **`busy_timeout=5000`** means a momentary write-write collision retries
   for up to 5 seconds instead of immediately raising "database is locked."

Verified directly (not just by reading the code): `PRAGMA journal_mode` and
`PRAGMA busy_timeout` both come back as `wal`/`5000` on a real connection,
and the `app.db-wal`/`app.db-shm` sidecar files appear next to `app.db` once
a connection is opened - confirming WAL mode is actually active, not just
requested. Those sidecar files are already covered by the existing
`data/` entry in `.gitignore`, so nothing further was needed there.

**Switching to PostgreSQL (also done, 2026-08-04 - now a config change, not
a future rewrite)**: worth doing once real "database is locked" errors show
up in the logs, you want to run more than one app worker process, or you
want managed, off-box backups. `app/database.py` now reads `DATABASE_URL`
from the environment - unset (every local/dev setup, and the default in
production until you deliberately opt in) means SQLite exactly as before;
set it to `postgresql+psycopg2://user:pass@host/dbname` and the app
connects to Postgres instead (with `pool_pre_ping=True`, so a connection
Postgres silently closed after sitting idle doesn't surface as a confusing
mid-request error on the next request to reuse it). The app has no raw SQL
or SQLite-specific queries outside that one file - it's SQLAlchemy ORM
throughout - so this really is just the connection setup, not a rewrite.

Verified as far as this environment allows: `psycopg2-binary` is installed
and importable, and setting `DATABASE_URL` to a Postgres DSN makes
`app.database.engine` correctly report `dialect.name == "postgresql"` and
`pool_pre_ping == True` with no SQLite PRAGMA attempted - confirmed without
needing a live Postgres server, since SQLAlchemy's `create_engine` doesn't
actually connect until first use. **Not verified**: an actual connection
against a running Postgres server, since none exists in this dev
environment - test that for real before relying on it in production.

**Moving existing data over**: `scripts/migrations/migrate_to_postgres.py` copies every row
from the existing SQLite database into a Postgres database you've already
created, preserving primary key IDs exactly (foreign keys like
`BOQItem.specsheet_id` reference those IDs directly, so renumbering them
would silently break every relationship in the copied data) and resetting
Postgres' auto-increment sequences afterward so new rows don't collide with
the copied ones. The script only moves the database; if Postgres ends up on
a different machine than the one serving
`data/uploads/`/`data/images/`/`data/exports/`, copy that whole `data/`
directory there too, or specsheet PDFs/diagram thumbnails will 404 even
though their database rows copied fine.

**This dev machine is now actually running on Postgres (2026-08-04)**, not
just capable of it - PostgreSQL 18 installed locally, a dedicated
non-superuser role/database created (`lighting_boq_app`/`lighting_boq`,
not the `postgres` superuser), the real dev data migrated across (208
specsheets, 2 BOQs and everything under them), and `DATABASE_URL` set as a
persistent per-user Windows environment variable - deliberately **not** in
any git-tracked file (`run.bat` included), so the credential never risks
being committed. Full details, including how to switch back to SQLite or
restart Postgres if it's not running, are in `HOW_TO_RUN.md`.

Verified for real this time, not just reviewed: the previously-untestable
Postgres-specific sequence-reset SQL (`pg_get_serial_sequence`/`setval`)
ran against real Postgres and was confirmed correct directly (each
sequence's `last_value` matches its table's actual `max(id)` post-
migration); a full read+write functional pass against the live app - login,
session, listing/reading the real migrated BOQs and specsheets, both
export formats producing real non-empty files, and a create → update →
delete round trip on a throwaway floor/room/item - all passed against
Postgres with no code changes needed beyond the `DATABASE_URL` switch,
confirming the "keeping functionality the same" goal.

**Known gap, not yet closed**: Postgres is running as a plain background
process on this machine, not a registered Windows service - `pg_ctl
register` needs an elevated (Administrator) shell, which wasn't available
during this session. It will **not** survive a reboot as-is. `HOW_TO_RUN.md`
has the one-time elevated command to register it properly, and the
one-line command to restart it manually in the meantime if it's down.

### 3.3 Authentication & authorization

**Done (2026-08-04)**: option 2 below (simple built-in login) is
implemented - not because the other two aren't valid, but because it's the
one that gives per-user identity and the `admin`/`member` role split
without depending on infrastructure (a reverse proxy, a company SSO
provider) that may not exist yet at every deployment target.

1. **Reverse-proxy Basic Auth** (not used) - Nginx/Caddy prompting for a
   single shared username/password before any request reaches the app would
   have been faster to ship, but gives no per-user identity or audit trail -
   not enough to support the `admin`/`member` split below.
2. **Simple built-in login** (implemented) -
   - `app.models.User`: email (unique), `password_hash`, `role`
     (`admin`/`member`).
   - `app/auth.py`: password hashing via the stdlib's PBKDF2-HMAC-SHA256
     (260,000 iterations) rather than adding `passlib`/`bcrypt` as a new
     dependency - PBKDF2 at that iteration count is NIST-approved and
     plenty for a 20-30 person internal tool, not a public service under
     credential-stuffing load.
   - `app/main.py`: a `SessionMiddleware` (Starlette, itsdangerous-signed
     cookie) plus an `AuthGuardMiddleware` that redirects an unauthenticated
     page request to `/login` and 401s an unauthenticated `/api/*` request.
     `/login` and `/static/*` are the only paths reachable while logged
     out. **Middleware registration order matters and was verified
     empirically** (a probe middleware + `TestClient`, not just reasoning
     about it): `SessionMiddleware` must be added *after*
     `AuthGuardMiddleware` in the source so it ends up as the outer layer
     and parses the cookie into `request.session` before the guard reads
     it - registering them the other way silently breaks the whole thing
     (the guard would always see an empty session).
   - `require_admin`, a FastAPI dependency, gates the five Config
     Rules/Product Codes *mutation* endpoints (POST/PUT/DELETE) - their
     *read* endpoints stay open to any logged-in user, since the
     `admin`-only concern is specifically "who can change a rule that
     affects every BOQ built afterward," not "who can see the current
     rules."
   - `SESSION_SECRET` comes from the environment (see Step 5 below); the
     app refuses to start with the insecure dev fallback once
     `ENVIRONMENT=production` is set.
   - `create_user.py`: the only way to provision an account (no public
     self-registration, appropriate for a confidential internal tool) -
     prompts for email/password/role, safe to re-run against an existing
     email to reset a password or change a role.
   - `templates/login.html`: a standalone page with its own inline styles,
     not dependent on `app.js`/the SPA shell, since it has to render and
     work *before* a session exists.
3. **Company SSO** (not used, but still an option later) - if the studio
   already uses Google Workspace or Microsoft 365, swapping the login route
   for OAuth against that instead of managing separate passwords is a
   contained change (same `User` table + session, different code path to
   populate `request.session["user_id"]`) - easier for end users, more
   setup work up front, worth it once there are enough accounts that
   password resets become a real support burden.

Verified end-to-end against a live server, not just by reading the code:
wrong password (redirects with an error, no session set), correct login
(session cookie set, `/api/me` returns the right email/role), an
authenticated member blocked with 403 from creating a config rule but still
able to read the list, and logout actually invalidating the session
(`/api/me` goes back to 401 afterward).

### 3.4 TLS/HTTPS

**Done (2026-08-05), the "internal CA" option below** - Caddy (`Caddyfile`,
registered as the `QuotationToolCaddy` service) terminates HTTPS using its
own internal certificate authority (`tls internal`), since this machine
has no public hostname for Let's Encrypt to issue a real certificate
against. Real encryption in transit, but each teammate's browser shows a
one-time security warning on first visit (no public CA vouches for the
cert) - click through it. Installing Caddy's root certificate on every
teammate's machine removes that warning; not done yet, not a blocker to
using the tool today.

- Reachable only inside the office LAN/VPN: an internal CA certificate (or
  even a self-signed one distributed to the 20-30 machines) is enough - no
  public certificate authority required.
- Reachable via a VPN client or a cloud VM with a real hostname: Caddy with
  Let's Encrypt automates issuance and renewal with almost no ongoing effort.

### 3.5 File storage & backups

**Partially done (2026-08-05)**: `backup.ps1` dumps Postgres + copies
`data/uploads`/`images`/`exports` together, scheduled daily via Task
Scheduler (`QuotationToolBackup`), 14-day retention - verified genuinely
restorable, not just that the files get created (see TUTORIAL.md). **Not
done**: the backups currently land on this same machine's disk, which
protects against an accidental `rm` but not a disk failure - moving
`$BackupRoot` in `backup.ps1` to an external drive or synced cloud folder
is the next real improvement here, not yet done.

`data/app.db`, `data/uploads/`, `data/images/`, and `data/exports/` together
**are** the product - a backup of the database without the PDFs/images it
references is useless, and vice versa. Concretely:

- A nightly scheduled job (cron on Linux, Task Scheduler on Windows) copies
  all of `data/` to a separate disk/location - ideally somewhere off-machine
  entirely (a second server, cloud storage, even a scheduled sync to
  OneDrive/Google Drive), so a single hardware failure can't take out both
  the live data and its only backup.
- **Test the restore, not just the backup.** Schedule a recurring (e.g.
  quarterly) check: restore the latest backup to a scratch folder and
  confirm the app actually runs against it. An untested backup is a hope,
  not a plan.
- Keep more than one snapshot (e.g. 14 daily + 6 monthly) rather than one
  rolling copy, so a mistake noticed a week later is still recoverable.

---

## 4. Confidentiality checklist

Concrete, checkable items rather than abstract principles - work through
this before onboarding real users:

- [ ] The app is not reachable from the public internet under any
      circumstance (verify with an actual test from an outside network, not
      just "I didn't open the port")
- [ ] Every connection uses HTTPS; no plain-HTTP fallback left enabled on
      the reverse proxy
- [x] Login is required before any page (Library, Config Rules, BOQ list)
      renders real data - done (3.3), verified end-to-end
- [ ] `SESSION_SECRET` is set to a real generated value, not left on the
      insecure dev fallback (`app/main.py` already refuses to start on the
      fallback once `ENVIRONMENT=production` is set - this item is just
      confirming that variable actually got set, not implementing anything)
- [ ] `.env`/secrets are listed in `.gitignore` and were never committed
      (check with `git log --all -p -- .env`)
- [ ] The server's disk is encrypted at rest (BitLocker on Windows, LUKS on
      Linux, or the cloud provider's default encrypted-volume option)
- [ ] The OS-level firewall only allows the reverse proxy's port in from the
      expected network/VPN range
- [ ] Backups are stored with the same access restrictions as the live
      system - a backup on an unsecured USB drive undoes everything else
- [ ] A named person owns "who currently has an account" and removes access
      promptly when someone leaves the studio
- [ ] Dependencies are pinned and periodically checked for known
      vulnerabilities (`pip install pip-audit && pip-audit`)

---

## 5. Step-by-step deployment procedure

Assumes a Linux server/VM per Option A/B above (the more common, better-
supported production target); Windows-service equivalents are noted where
they differ.

### Step 0 - Decide before you deploy
- Office server or cloud VM (3.1)?
- Built-in login or company SSO (3.3)? Write down, right now, who gets an
  account and who is an `admin`.
- Who owns backups, and who checks the restore each quarter?

### Step 1 - Provision the server
- Linux (recommended): Ubuntu 22.04 LTS, minimal install,
  `sudo apt update && sudo apt upgrade`.
- Windows: Windows Server, or a spare always-on Windows machine, fully
  patched.
- Create a dedicated, non-root/non-admin service account to run the app
  under (least privilege).

### Step 2 - Install runtime dependencies
```bash
sudo apt install python3.11 python3.11-venv nginx -y
```
(Windows: install Python 3.11+ from python.org; install Nginx-for-Windows or
Caddy as the reverse proxy.)

### Step 3 - Get the code onto the server
```bash
git clone <your private repo URL> /opt/lighting-boq-tool
cd /opt/lighting-boq-tool
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # after pinning versions - see Step 4
```

### Step 4 - Pin dependencies (do this once, before the first production install)
On a working dev machine, with the app running exactly as intended:
```bash
pip freeze > requirements.txt
```
Commit that. Production installs should always come from a pinned file,
never an unpinned "whatever's newest today."

### Step 5 - Configure environment
`app/main.py` reads `SESSION_SECRET` and `ENVIRONMENT` directly from the
process environment via `os.environ` (no `.env`-file loader/`python-dotenv`
dependency needed - the systemd `EnvironmentFile=` in Step 7 sets real
process env vars, which is enough). It refuses to start with the insecure
dev fallback secret once `ENVIRONMENT=production` is set, so this step is
not optional in production:
```bash
export SESSION_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
export ENVIRONMENT=production
```
Put the generated value in the systemd unit's `EnvironmentFile` (Step 7),
never in git. `DATABASE_URL` is optional - leave it unset to keep using
SQLite (the default), or set it to a Postgres DSN if you've already decided
to run against Postgres (3.2) and moved existing data over with
`scripts/migrations/migrate_to_postgres.py` first.

### Step 6 - First run & create the first account
```bash
mkdir -p data
python seed.py            # optional - only meaningful on a brand-new, empty database
python create_user.py     # create the first admin account; prompts for email/password/role
```
Copy your existing `data/` folder (`app.db`, `uploads/`, `images/`,
`exports/`) here instead of running `seed.py` if you already have real
specsheets/BOQs from prototype use - `seed.py` is only for starting from
nothing.

### Step 7 - Run it under a real process supervisor

**Linux - systemd unit** (`/etc/systemd/system/lighting-boq.service`):
```ini
[Unit]
Description=Quotation Tool
After=network.target

[Service]
User=lighting-boq
WorkingDirectory=/opt/lighting-boq-tool
Environment="PATH=/opt/lighting-boq-tool/venv/bin"
EnvironmentFile=/opt/lighting-boq-tool/.env
ExecStart=/opt/lighting-boq-tool/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now lighting-boq
sudo systemctl status lighting-boq
```

**Windows** - equivalent via NSSM (Non-Sucking Service Manager) or Task
Scheduler configured to "run at startup" and "restart on failure," pointed
at the same `uvicorn` command.

Note it's bound to `127.0.0.1` only - unreachable from anywhere except the
reverse proxy on the same machine.

### Step 8 - Reverse proxy + TLS (Nginx example)
`/etc/nginx/sites-available/lighting-boq`:
```nginx
server {
    listen 443 ssl;
    server_name boq.yourcompany.internal;

    ssl_certificate     /etc/ssl/lighting-boq/fullchain.pem;
    ssl_certificate_key /etc/ssl/lighting-boq/privkey.pem;

    client_max_body_size 25M;   # specsheet PDF uploads

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
server {
    listen 80;
    server_name boq.yourcompany.internal;
    return 301 https://$host$request_uri;
}
```
```bash
sudo ln -s /etc/nginx/sites-available/lighting-boq /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Step 9 - Firewall
```bash
sudo ufw default deny incoming
sudo ufw allow from <office-network-or-VPN-CIDR> to any port 443
sudo ufw allow OpenSSH   # or restrict to the VPN similarly
sudo ufw enable
```

### Step 10 - Confirm authentication (3.3) is actually enforced before inviting real users
The app itself already requires login (3.3) - this step is verification,
not implementation: confirm `SESSION_SECRET` is set to a real generated
value (Step 5), not the insecure dev fallback, and that hitting any page or
`/api/*` route without a session redirects/401s as expected. Do not proceed
to Step 11 on the strength of TLS alone - TLS protects data in transit, not
who's allowed to see it at all.

### Step 11 - Smoke test
- From a machine on the allowed network: load the URL, log in, upload a
  test specsheet, create a test BOQ, export both `.xlsx` and `.pdf`, then
  delete the test BOQ.
- From a machine **not** on the allowed network (e.g. a phone on mobile
  data): confirm the URL is simply unreachable.

### Step 12 - Set up backups (3.5)
Before onboarding the 20-30 users, not after.

### Step 13 - Onboard users
Share the URL and how they get an account (invite, or self-register plus
admin approval, depending on the choice made in 3.3). Point them at
`TUTORIAL.md`/`README.md` for how the tool itself works.

---

## 6. Ongoing operations

- **Deploying an update**: `git pull`, `pip install -r requirements.txt` (if
  it changed), `sudo systemctl restart lighting-boq`. Consider running a
  staging copy on the same server (different port, separate `data/`
  directory) to test an update against before restarting the instance
  20-30 people are actively using.
- **Schema changes**: once real users' data lives in this database, retire
  the current hand-written `migrate_*.py` one-off-script pattern in favor
  of **Alembic** - `alembic init`, autogenerate migrations from model
  changes, review the generated migration before applying it. This gives
  every change an ordered, repeatable, reversible history instead of
  depending on one person remembering what ran and when.
- **Monitoring**: at minimum, a free external uptime check (e.g. UptimeRobot
  pinging the login page every few minutes) plus a log review
  (`journalctl -u lighting-boq -f` on Linux) after every deploy.
- **Disk usage**: `data/exports/` grows forever - every export is kept
  indefinitely today. Watch disk usage and decide a retention policy (e.g.
  keep the last N exports per BOQ, or archive older ones to cold storage)
  before it becomes a real problem.

---

## 7. Rollback plan

- Note the previous release's git commit hash before every deploy.
- If a deploy misbehaves: `git checkout <previous-hash>`, reinstall
  dependencies if `requirements.txt` changed, restart the service.
- Avoid shipping a schema migration and a risky feature in the same
  release - it makes rollback ambiguous (does rolling back the code also
  mean rolling back the schema?). Ship them separately when possible.
- Because backups are already scheduled (Step 12), a migration that already
  ran and caused a problem can be recovered by restoring the pre-migration
  backup rather than hand-writing a down-migration under time pressure.

---

## 8. Open decisions for the studio to make before go-live

This document deliberately doesn't decide these unilaterally - they're
business/operations calls, not technical ones:

- On-prem server vs. cloud VM (3.1)?
- Built-in login vs. company SSO (3.3)?
- Who holds the `admin` role (Config Rules/Product Codes editing rights),
  versus who's a regular BOQ-building user?
- Who owns "someone left the studio, revoke their access" as a standing
  responsibility?
- What's the backup retention window, and who checks the restore each
  quarter?
- Is there already an internal domain/hostname to use, or does one need to
  be requested from IT?
