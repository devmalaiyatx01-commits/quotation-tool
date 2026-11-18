# How to run the Quotation Tool yourself (no Claude Code needed)

## First time only

Open a terminal in this folder and install the dependencies once:

```
pip install -r requirements.txt
```

## Every time you want to use the app

Just **double-click `run.bat`** in this folder
(`c:\Users\admin\Documents\DesignTeamTool\run.bat`).

A terminal window will open, load the sample specsheets (skipped if already
loaded), and start the server. Once it prints:

```
Starting server at http://127.0.0.1:8000
```

open **http://127.0.0.1:8000** in your browser and **sign in**. First time
on a fresh setup with no accounts yet? Run `python create_user.py` in a
terminal first to create your login (see "Logging in" below).

To stop the app, close that terminal window (or press `Ctrl+C` in it).

*(Command-line equivalent, if you'd rather type it than double-click:
`run.bat` from a terminal opened in this folder.)*

## Logging in

Every page requires signing in. Two ways, depending on whether Google
Sign-In has been set up yet (`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
environment variables - see **[`GoogleAuthSetup.md`](GoogleAuthSetup.md)**
for the one-time setup, done through Google Cloud Console, not this repo):

- **Google Sign-In configured** (the normal case): the login page shows
  just "Sign in with Google" - any real `@example.com` Google account
  works, and an account is created automatically the first time each
  address signs in (as `member` - promote to `admin` with `create_user.py`,
  see below).
- **Not configured yet**: the login page falls back to the original email/
  password form. Create your account once with:
  ```
  python create_user.py
  ```
  It'll prompt for an email, password, and role (`admin` or `member`) -
  `admin` can open the Nomenclature tab (product codes), `member` can do everything
  else (build/edit/export BOQs, upload specsheets). Re-run it against the
  same email later to reset a forgotten password or change a role. Once
  Google Sign-In is configured, this script's password prompt stops
  mattering for logging in (only `role` still does) - password login itself
  is disabled the moment Google Sign-In is live, not just hidden.

## Database: this machine runs on PostgreSQL

This install now runs against a local PostgreSQL server, not the
`data/app.db` SQLite file directly (that file is still there as the
*original* copy, untouched, but the running app no longer reads it - see
`deployment.md` section 3.2 for why and how this happened). Concretely:

- Postgres needs to be running before `run.bat` will work. If the app
  fails to start with a "connection refused" error, start it with:
  ```
  "C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" -D "C:\Program Files\PostgreSQL\18\data" start
  ```
- The app finds Postgres via the `DATABASE_URL` environment variable,
  already set for this Windows user account (`[Environment]::GetEnvironmentVariable('DATABASE_URL', 'User')`
  in PowerShell shows it) - `run.bat` doesn't need editing, and this
  variable is **not** stored in any file this project's git history
  tracks.
- **Registering Postgres (and everything else) as a real Windows service**
  that survives reboots/crashes is covered below under "Going live for the
  team" - `setup_production_services.ps1` does this in one step, rather
  than the one-off `pg_ctl register` command this section used to suggest.
- To go back to plain SQLite (e.g. to troubleshoot): remove the
  `DATABASE_URL` user environment variable
  (`[Environment]::SetEnvironmentVariable('DATABASE_URL', $null, 'User')`
  in PowerShell) and restart `run.bat` - the app falls straight back to
  `data/app.db` with no code change needed. Note this switches which data
  you see - new BOQs/specsheets added while on Postgres won't show up
  until you switch back.

## Going live for the team

Everything up to here (`run.bat`) is a manually-started, single-user setup
- fine for you alone, not what you want once other people depend on this
being up. `setup_production_services.ps1` turns it into a real deployment
in one step:

1. Make sure `DATABASE_URL` and `SESSION_SECRET` are already set as this
   Windows user's environment variables (they should already be, if
   you've been running `run.bat` - check with
   `[Environment]::GetEnvironmentVariable('SESSION_SECRET', 'User')` in
   PowerShell; if it's empty, generate one:
   `[Environment]::SetEnvironmentVariable('SESSION_SECRET', (python -c "import secrets; print(secrets.token_hex(32))"), 'User')`).
2. Open PowerShell **as Administrator**, `cd` into this folder, and run:
   ```
   powershell -ExecutionPolicy Bypass -File .\setup_production_services.ps1
   ```

That one script:
- Installs [Caddy](https://caddyserver.com) (a reverse proxy that gives you
  HTTPS automatically, even with no public domain name - see `Caddyfile`
  for exactly how) and [NSSM](https://nssm.cc) (wraps an ordinary .exe as a
  proper Windows service).
- Registers **three** Windows services, all set to start automatically:
  `postgresql-quotation-tool` (the database), `QuotationTool` (the app
  itself, via `run_service.bat` - not `run.bat`, which is for interactive
  use only), and `QuotationToolCaddy` (the HTTPS reverse proxy).
- Opens the Windows Firewall for ports 80/443, **restricted to this
  machine's local subnet only** - not the open internet.
- Prints the URL to share with your team: `https://<this-machine's-LAN-IP>`.

**The first time each teammate visits, their browser will show a security
warning.** That's expected: `tls internal` in the `Caddyfile` means Caddy
issues its own certificate rather than getting one from a public
certificate authority (there's no public domain name for this to have
one). Click through it (Advanced → Proceed) - the connection is still
genuinely encrypted, just not verified by a public CA. Exporting Caddy's
local root certificate so it can be installed on every teammate's machine
(removing the warning entirely) is a reasonable next improvement, not a
blocker to going live today.

Once live, **`run.bat` is no longer how the team's copy runs** - use
`Restart-Service QuotationTool` (from an elevated PowerShell) to pick up a
deployed code change, and see "Developing alongside a live deployment"
below for how to keep working on new features without touching what the
team is using.

## Developing alongside a live deployment

Once the team is on the live service, don't edit files in this same folder
and expect the running service to stay stable - it won't restart itself
mid-edit, but the *next* restart (a crash, a reboot, or you deploying a
different change) picks up whatever's on disk at that moment, finished or
not. Two separate concerns, two separate fixes:

**Don't touch the team's data while testing** - use `run_dev.bat` instead
of `run.bat`. It points at a completely separate database
(`lighting_boq_dev`, already created and seeded with the sample
specsheets) on a separate port (8001), so anything you add, delete, or
break while testing a new feature never touches what the team sees.

**Don't let in-progress code reach the live service** - work from a
second checkout of this repo, not this same folder, so half-finished
edits here can't end up running there. A [git
worktree](https://git-scm.com/docs/git-worktree) is the cheapest way to
get one (same repo, same history, a second working directory, no second
clone to keep in sync):
```
git worktree add ../DesignTeamTool-dev V1/U
```
Do your feature work in `DesignTeamTool-dev` (`run_dev.bat` works the same
way there - copy `.env.development` over once), commit and push as usual.
When a change is tested and ready for the team, pull it into *this*
folder (the one the live service actually runs from) and restart the
service:
```
git pull
Restart-Service QuotationTool
```

## Backups

`backup.ps1` dumps the live Postgres database and copies
`data/uploads`/`data/images`/`data/exports` together into
`C:\Backups\QuotationTool\<timestamp>\` - a database backup without the
files it references (product photos, uploaded specsheets, exported
quotations) is useless on its own. It's already scheduled to run daily at
2 AM via Task Scheduler (task name `QuotationToolBackup`) and keeps the
last 14 days, deleting older ones automatically.

**Move `$BackupRoot` in `backup.ps1` to an external drive or a synced
cloud folder (OneDrive/Google Drive) once you have one available** - as
configured today, a backup living on this same machine's disk protects
against an accidental deletion but not against this machine's disk
actually failing, which is the failure mode that matters most.

To restore: `pg_restore` the `.dump` file into a fresh database, then copy
the backed-up `uploads`/`images`/`exports` folders back into `data/`.

## Where things go

| What | Where |
|---|---|
| New specsheet PDFs | Don't drop them in a folder — use the **Specsheet Library** tab's "Upload specsheet(s)" button in the browser. They're saved to `data/uploads/` automatically. |
| Product photos extracted from specsheets | `data/images/` (automatic, no action needed) |
| Every BOQ you create | Saved automatically as you work — in the `lighting_boq` Postgres database on this machine (see "Database" above), not `data/app.db` |
| Every exported BOQ `.xlsx` | `data/exports/`, timestamped, also downloaded to your browser's normal Downloads folder |
| The sample files this was built from | `SampleInputs/` (reference only — not read by the app after the first `seed.py` import) |

## Starting fresh

Uploaded PDFs/images/exports are still plain files under `data/` regardless
of database backend, so deleting that folder still clears them. The
**database** itself now lives in Postgres, not `data/app.db` - to reset it,
connect with `psql -U postgres` and drop/recreate the `lighting_boq`
database (or just drop its tables), then re-run `run.bat` to recreate the
schema and reload the sample specsheets.

## Something not working?

See the "Known V1 limitations" section in `README.md`.

## Optional: a free AI model for the Match tab and Import BOQ

Off by default, and the app behaves exactly as it always has while it is off. When it is on:

- **Import BOQ** offers "Let the AI model read what the app can't". A PDF, a CSV, or a
  spreadsheet whose layout the rules cannot read goes to the model, which returns the lines as
  JSON; any line whose description is not in the file is dropped, and every line it read is
  marked "to check". For lines the rules could not link to NPD, the model is shown a shortlist of
  up to 8 NPD products per line and may pick one of those only, also "to check". See
  `app/boq_ai.py`.
- **Match** offers "Use AI to read what the rules can't": a requirement sheet the rules refuse is
  read by the model instead, each row marked "Read by the AI model - check it."

The model reads and proposes; it never decides. Every failure (no allowance left, no private
provider free, a timeout, a reply that is not JSON) falls back to the rules, and the screen says
why.

### The key: OpenRouter's free models

OpenRouter (openrouter.ai) is one key for many models, and models whose id ends in `:free` cost
nothing. Its limits for free models (checked 28 Oct 2026): **20 requests a minute, and 50 a day**,
or **1,000 a day once $10 of credit has been bought on the account** (a one-time purchase; free
models never spend it). Reading a file takes one request per 12,000-14,000 characters (a few
pages), and suggesting products one per 15 unlinked lines, so 50 a day is a handful of files;
1,000 is plenty for the studio.

The app only ever sends `:free` models (a fallback that reached a paid model would be billed),
and asks for providers that do not keep or train on what is sent (`data_collection: deny`) -
client wording leaves the building otherwise. If no free model has such a provider at that
moment, the model is not asked and the screen says so. Each request names at most 3 models
(OpenRouter's limit) with their "thinking" switched off, so a model listed in
`LLM_EXTRACT_MODEL` must allow both; the default list in `app/llm_extract.py` was tried with a
real key on 29 Oct 2026. Free models are often busy for a while ("rate-limited upstream"); the
next in the list is then used, and if all are busy the screen says so - try again in a minute.

Locally, set it in the Windows user environment (like `DATABASE_URL`), then start `run.bat` from a
**new** terminal:

```
setx LLM_EXTRACT_ENABLED 1
setx OPENROUTER_API_KEY "sk-or-v1-..."
```

On the server it has a file of its own, `/etc/quotation-tool/llm.env` (owner root, mode 600,
holding `LLM_EXTRACT_ENABLED=1` and `OPENROUTER_API_KEY=...`), loaded by the drop-in
`/etc/systemd/system/quotation-tool.service.d/llm.conf` (`[Service]` /
`EnvironmentFile=/etc/quotation-tool/llm.env`). Kept apart from `.env` so the key can be replaced
or removed without touching the server's other secrets, and only root can read it - systemd
reads the file before the app starts. To change the key: edit that file, then
`sudo systemctl restart quotation-tool`. To switch the AI off: remove the drop-in, then
`sudo systemctl daemon-reload` and restart. Changes to the live VM need the owner's approval,
like any deploy.

Optional settings:

```
LLM_EXTRACT_MODEL=a/model:free,b/model:free   # the models to try, in order (default: app/llm_extract.py)
LLM_DAILY_REQUESTS=950                        # this server's own daily cap (default: follows the key's plan)
LLM_ALLOW_TRAINING=1                          # also allow providers that may train on the text - off by default
```

Without `LLM_DAILY_REQUESTS`, the cap follows the key: the app asks OpenRouter (`GET /credits`,
an account question that uses none of the allowance, at most once an hour) whether $10 of
credit has been bought, and caps the day at 48 (of 50) or 950 (of 1,000). The daily count is
kept in `data/llm_usage/<UTC date>.txt` (one byte per request), so both server workers and
every restart share it. The key is read on the server only; the browser is told only whether
the AI is available.

**Ask** (the tab, `app/assistant.py`) answers a question from the app's own records, one request
per question, and may use half of the day's cap (24 of 48 on the free plan) so file reading
always keeps the other half. A question that matches no record is answered without a request.

`GROQ_API_KEY` still works in place of `OPENROUTER_API_KEY` (one model, Groq's own limits).

### How the Match tab uses it on a PDF

It exists for
requirement documents written in someone else's house style, where the extractor's regexes
find 3-5 of 9 fields instead of the 8-9 they find on the studio's own. It is asked only about the
fixtures the regexes did poorly on - 3 of 52 on the sample SampleClient document, about 1,500
tokens rather than the 23,000 the whole PDF would cost - and it may only ADD fields the
regexes missed, never overwrite one.

It does not decide how many requirements a document has (since 2026-09-17). That comes from
`app/requirement_reader.py`, which reads the PDF's layout with no model and no network. So the
count is the same whether this is on, off, or out of quota. The model is asked to find the
requirements itself only when pasted text gave the reader nothing at all.
`LLM_EXTRACT_MIN_FIELDS=7` (optional) sets how few fields make a fixture worth asking about.

Leave `LLM_EXTRACT_ENABLED` unset to turn it off; no restart-time key check, no error if the
key is absent. Any failure - timeout, quota, bad response - returns exactly what the regexes
found, so the Match tab keeps working. See `app/llm_extract.py` for the reasoning.
