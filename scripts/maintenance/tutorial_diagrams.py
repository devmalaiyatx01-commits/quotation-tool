"""Every diagram in the illustrated TUTORIAL.pdf, keyed to the section it explains.

Each figure is registered with @fig(key, title, caption). The key says where it goes:

  "ATLAS"         the architecture atlas at the front of the book
  "0.3", "10.4.1" right after the first paragraph of the section with that number
  "H:<text>"      the same, for a heading with no number (Part I's sections)
  "P:<text>"      right after the paragraph that starts with <text> (the lessons in 15.10)

Everything drawn here was checked against the code, not only against the tutorial's prose:
the middleware order, the permission rules, the routes, the tables and their keys, the price
resolution order and the export matrix all come from app/main.py, app/models.py and
static/app.js as they stood on 2026-09-23. If the code changes, the figure that shows it is
listed by key in this file - grep for the function name.
"""
from tutorial_diagram_engine import Flow, Seq, bar_chart, complexity_chart

FIGS = []


def fig(key, title, caption=""):
    def deco(fn):
        FIGS.append(dict(key=key, title=title, caption=caption, make=fn))
        return fn
    return deco


def chain(f, items, col=0, row=0, horizontal=False, span=1, labels=None, style="normal"):
    """Place items (id, text, kind, sub) in a straight line and join them in order."""
    prev = None
    for i, it in enumerate(items):
        nid, text, kind = it[0], it[1], it[2] if len(it) > 2 else "process"
        sub = it[3] if len(it) > 3 else None
        c, r = (col + i, row) if horizontal else (col, row + i)
        f.node(nid, text, c, r, kind, span=span, sub=sub)
        if prev:
            f.edge(prev, nid, (labels or {}).get(nid), style=style)
        prev = nid
    return f


def cards(items, cols=3, kind="process", width=493):
    """A grid of boxes with no arrows - for 'these are the parts of X' pictures."""
    f = Flow(cols=cols, vgap=14)
    for i, it in enumerate(items):
        text, sub = it[0], it[1] if len(it) > 1 else None
        k = it[2] if len(it) > 2 else kind
        f.node("n%d" % i, text, i % cols, i // cols, k, sub=sub)
    return f.drawing()


# =============================================================================== ATLAS

@fig("ATLAS", "The whole system on one page",
     "Where every request travels, from a person's browser to the database and back. The "
     "database and the app are bound to 127.0.0.1, so only Caddy is reachable from outside; "
     "two independent firewalls sit in front of it.")
def atlas_network():
    f = Flow(cols=4, vgap=22)
    f.zone("The internet (untrusted)", 0, 0, 0, 1, "untrusted")
    f.zone("Oracle Cloud VM - quotes.example.com", 1, 1, 3, 3, "server")
    f.zone("Localhost only", 0, 2, 0, 3, "data")
    f.node("dns", "DNS (Cloudflare)", 0, 0, "external", sub="name -> public IP")
    f.node("br", "Browser", 0, 1, "actor", sub="static/app.js - the single-page app")
    f.node("sl", "Security List", 1, 1, "external", sub="Oracle's cloud firewall: 22, 80, 443")
    f.node("ip", "iptables / ufw", 2, 1, "external", sub="the VM's own firewall, default DROP")
    f.node("cad", "Caddy :443", 3, 1, "process", sub="TLS (Let's Encrypt), HSTS, reverse proxy")
    f.node("uv", "uvicorn - 2 workers", 3, 2, "process", sub="127.0.0.1:8000, run by systemd")
    f.node("mw", "FastAPI middleware", 2, 2, "process", sub="Session -> AuthGuard -> Activity")
    f.node("rt", "Route function", 1, 2, "process", sub="app/main.py + Depends(get_db)")
    f.node("pg", "PostgreSQL", 0, 2, "store", sub="127.0.0.1:5432, role quotation_app")
    f.node("fs", "data/", 0, 3, "doc", sub="uploads, images, exports")
    f.node("mod", "Worker modules", 1, 3, "process", sub="pdf_parser, npd, *_export, match_index ...")
    f.edge("br", "dns", "1 resolve", both=True)
    f.edge("br", "sl", "2 HTTPS").edge("sl", "ip").edge("ip", "cad")
    f.edge("cad", "uv", "3 plain HTTP").edge("uv", "mw").edge("mw", "rt").edge("rt", "pg", "4 SQL", style="data")
    f.edge("rt", "mod", "calls", route="v").edge("mod", "fs", "files", style="data")
    return f.drawing()


@fig("ATLAS", "What runs on the server, and what it talks to",
     "The app process in the middle; everything it depends on around it. Solid lines are "
     "every-request paths, dashed ones happen occasionally (a sign-in, an optional AI call, a "
     "deploy, the nightly backup).")
def atlas_components():
    f = Flow(cols=5, vgap=22)
    f.zone("Outside the VM", 0, 0, 0, 3, "neutral")
    f.zone("The VM", 1, 0, 3, 3, "server")
    f.zone("Outside the VM", 4, 0, 4, 3, "neutral")
    f.node("goo", "Google", 0, 1, "external", sub="SSO for @example.com")
    f.node("groq", "Groq API", 0, 2, "external", sub="optional LLM, Match tab only")
    f.node("cad", "Caddy", 2, 0, sub="HTTPS in front")
    f.node("sysd", "systemd", 3, 0, sub="Restart=always")
    f.node("app", "FastAPI app", 2, 1, sub="uvicorn x2, app/main.py")
    f.node("gh", "GitHub", 4, 1, "external", sub="source of every deploy")
    f.node("pg", "PostgreSQL", 2, 2, "store", sub="every BOQ, price, user")
    f.node("data", "data/", 3, 2, "doc", sub="PDFs, images, exports")
    f.node("bk", "/opt/backup.sh", 2, 3, sub="cron 02:00, keeps 14 days")
    f.node("os", "Object Storage", 4, 3, "store", sub="off-site copy")
    f.edge("cad", "app", "proxies")
    f.edge("sysd", "app", "keeps alive", a_side="bottom", b_side="right-t", dashed=True)
    f.edge("app", "goo", "verify ID token", a_side="left-t", b_side="right", dashed=True)
    f.edge("app", "groq", "fill fields", a_side="bottom-l", b_side="right", dashed=True)
    f.edge("gh", "app", "git pull", a_side="left", b_side="right-b", dashed=True)
    f.edge("app", "pg", "SQL", style="data")
    f.edge("app", "data", "files", a_side="bottom-r", b_side="top", style="data")
    f.edge("pg", "bk", "pg_dump", dashed=True)
    f.edge("data", "bk", "tar", a_side="bottom", b_side="right-t", dashed=True)
    f.edge("bk", "os", "copy", a_side="right-b", b_side="left", dashed=True)
    return f.drawing()


@fig("ATLAS", "The code map: which file does what",
     "The browser half and the server half, and the modules main.py delegates to. The rule "
     "from 4.4 is visible here: no module below main.py imports FastAPI, and all but two never "
     "touch the database either. match_index and accessories read it through a session main.py "
     "passes in (checked with grep, 2026-09-23).")
def atlas_codemap():
    f = Flow(cols=4, vgap=18)
    f.zone("Browser", 0, 0, 3, 0, "untrusted")
    f.zone("Server entry", 0, 1, 3, 1, "server")
    f.zone("No FastAPI, no database - plain data in, plain data out", 0, 2, 3, 3, "trusted")
    f.zone("", 0, 4, 1, 4, "trusted")
    f.zone("No FastAPI; main.py hands them a db session", 2, 4, 3, 4, "data")
    f.node("html", "templates/index.html", 0, 0, "doc", sub="an almost-empty shell")
    f.node("js", "static/app.js", 1, 0, span=2, sub="the whole front end: state, render(), api(), escapeHtml()")
    f.node("css", "static/style.css", 3, 0, "doc")
    f.node("main", "app/main.py", 0, 1, span=2, sub="routes, AuthGuard, _required_permission, activity table")
    f.node("mdl", "models.py + database.py", 2, 1, "store", sub="tables, engine, get_db, data/ paths")
    f.node("auth", "auth.py / google_auth.py", 3, 1, sub="PBKDF2, Google SSO")
    f.node("pp", "pdf_parser.py", 0, 2, sub="specsheet PDF -> fields")
    f.node("prof", "profile_pictures.py", 1, 2, sub="cut profile pictures")
    f.node("cfg", "config_rules.py", 2, 2, sub="Configurator rule engine")
    f.node("npd", "npd.py", 3, 2, sub="compose, price_for, specsheet PDF")
    f.node("xl", "excel_export.py", 0, 3, sub="BOQ .xlsx, line_diagram_for()")
    f.node("pdfx", "pdf_export.py", 1, 3, sub="BOQ .pdf")
    f.node("qx", "quotation_export.py", 2, 3, sub="priced .xlsx, live formulas")
    f.node("qpdf", "quotation_pdf_export.py", 3, 3, sub="priced .pdf")
    f.node("req", "requirement_reader / _excel", 0, 4, sub="client schedules in")
    f.node("llm", "llm_extract.py", 1, 4, sub="Groq fallback, only adds")
    f.node("mi", "match_index.py", 2, 4, sub="catalogue as intervals")
    f.node("acc", "accessories.py", 3, 4, sub="catalogue derived on request")
    f.edge("js", "main", "fetch /api/...", a_side="bottom-l", b_side="top")
    f.edge("main", "mdl", "Session")
    return f.drawing()


@fig("ATLAS", "The data model",
     "Tables and how they point at each other. A BOQ is a tree (floors, rooms, items); an item "
     "copies what it needs from a specsheet rather than reading it live (5.3), and may hang "
     "under another item as an accessory. Branches are BOQs pointing at their original.")
def atlas_er():
    f = Flow(cols=4, vgap=26, hgap=18)
    f.node("boq", "boqs", 0, 0, "table", fields=["id", "project_name, client", "mode: detailed | quick",
                                                  "discount_percent, currency", "parent_boq_id -> boqs (branch)",
                                                  "created_by_id"])
    f.node("fl", "boq_floors", 1, 0, "table", fields=["id", "boq_id -> boqs", "name, sort_order"])
    f.node("rm", "boq_rooms", 2, 0, "table", fields=["id", "floor_id -> boq_floors", "name, sort_order", "remark"])
    f.node("it", "boq_items", 3, 0, "table", fields=["id", "room_id -> boq_rooms", "specsheet_id -> specsheets",
                                                     "parent_item_id -> boq_items", "qty, price (copied)",
                                                     "area_ids, ref_image_ids"])
    f.node("ar", "boq_areas", 0, 1, "table", fields=["boq_id -> boqs (cascade)", "quick BOQ areas"])
    f.node("ri", "boq_ref_images", 1, 1, "table", fields=["boq_id -> boqs (cascade)", "label, filename"])
    f.node("ex", "boq_exports", 2, 1, "table", fields=["boq_id -> boqs", "file_path"])
    f.node("ss", "specsheets", 3, 1, "table", fields=["id, make, typology", "params / fields / options",
                                                      "origin: parsed | npd", "base_price, price_adders"])
    f.node("pe", "price_entries", 3, 2, "table", fields=["specsheet_id -> specsheets", "config {field: value}", "price, status"])
    f.node("cr", "config_rules", 2, 2, "table", fields=["specsheet_id / typology", "conditions -> hidden options"])
    f.node("pt", "parameter_templates", 1, 2, "table", fields=["typology, group_name, label", "field_key (unique per typology)", "sort_order"])
    f.node("fc", "field_codes", 0, 2, "table", fields=["field, option_value", "code (product-code segment)"])
    f.node("us", "users", 0, 3, "table", fields=["email (unique, indexed)", "role: admin | member | no access"])
    f.node("up", "user_permissions", 1, 3, "table", fields=["user_id -> users (cascade)", "permission, expires_at"])
    f.node("al", "activity_log", 2, 3, "table", fields=["user_id, user_email", "action, summary, at"])
    f.node("dp", "driver_prices", 3, 3, "table", fields=["driver_type, control_type", "ip_rating, wattage, price"])
    f.edge("fl", "boq", "N:1").edge("rm", "fl", "N:1").edge("it", "rm", "N:1")
    f.edge("it", "ss", "N:1 (nullable)", route="v")
    f.edge("pe", "ss", "N:1", route="v")
    f.edge("ar", "boq", "N:1", route="v").edge("up", "us", "N:1", route="h")
    f.edge("ri", "boq", "N:1", a_side="top", b_side="bottom-r")
    f.edge("ex", "boq", "N:1", a_side="top", b_side="right-b")
    return f.drawing()


@fig("ATLAS", "One BOQ, as a tree",
     "The shape every detailed export walks. A quick BOQ keeps the same tree behind the scenes "
     "- one hidden floor and one hidden room - and names its areas separately (boq_areas).")
def atlas_tree():
    f = Flow(cols=5, vgap=18)
    f.node("b", "BOQ", 2, 0, "start", sub="Bodhi Marg Residence")
    f.node("f1", "Floor", 1, 1, sub="Ground")
    f.node("f2", "Floor", 3, 1, sub="First")
    f.node("r1", "Room / area", 0, 2, sub="Lobby")
    f.node("r2", "Room / area", 2, 2, sub="Kitchen")
    f.node("r3", "Room / area", 3, 2, sub="Master Bedroom")
    f.node("i1", "Item", 0, 3, "good", sub="downlight x 12")
    f.node("i2", "Item", 1, 3, "good", sub="strip 5 m")
    f.node("a1", "Accessory item", 1, 4, "warn", sub="driver, parent_item_id -> strip")
    f.node("i3", "Item", 3, 3, "good", sub="spotlight x 4")
    f.edge("b", "f1").edge("b", "f2").edge("f1", "r1").edge("f1", "r2").edge("f2", "r3", route="v")
    f.edge("r1", "i1").edge("r2", "i2").edge("i2", "a1", "sits under").edge("r3", "i3")
    return f.drawing()


@fig("ATLAS", "The request pipeline inside the app",
     "Middleware wraps the route like layers of an onion. Starlette runs the middleware "
     "added last first, so a request meets them in this order (app/main.py, lines 636-651).")
def atlas_pipeline():
    f = Flow(cols=3, vgap=16)
    chain(f, [
        ("in", "Request arrives from Caddy", "start"),
        ("s", "SessionMiddleware", "process", "reads and verifies the signed cookie -> request.session"),
        ("g", "AuthGuardMiddleware", "process", "signed in? account still OK? permission? preview?"),
        ("a", "ActivityMiddleware", "process", "is this a logged route? note the BOQ it touches"),
        ("r", "Router -> route function", "process", "Depends(get_db) opens a session"),
        ("out", "Response", "end", "the activity row is written only if it succeeded"),
    ], col=1)
    f.node("x401", "401 / redirect to /login", 0, 2, "bad")
    f.node("x403", "403 you need <permission>", 2, 2, "bad")
    f.edge("g", "x401", "no session", route="h", style="bad")
    f.edge("g", "x403", "refused", route="h", style="bad")
    return f.drawing()


@fig("ATLAS", "How a price is found for a BOQ line",
     "resolveListPrice() in static/app.js. An NPD product is priced by its own rule; anything "
     "else looks for the most specific verified price entry, and refuses to guess when the "
     "candidates disagree.")
def atlas_price():
    f = Flow(cols=3, vgap=16)
    f.node("s", "A product and the options chosen so far", 1, 0, "start")
    f.node("n", "Authored in NPD?", 1, 1, "decision")
    f.node("np", "base_price + adder for each chosen option", 2, 1, "good", sub="+ power-supply adder for the control")
    f.node("pe", "Any product price entries?", 1, 2, "decision")
    f.node("none", "No price - quote 0, marked unpriced", 0, 2, "warn")
    f.node("m", "Keep entries whose config the choices satisfy", 1, 3, sub="an unchosen field matches anything; accessory rows never count")
    f.node("k", "Keep only the most specific (most config keys)", 1, 4)
    f.node("d", "Do they all agree on price?", 1, 5, "decision")
    f.node("wait", "null - a price-changing field is still unset", 0, 5, "warn")
    f.node("ok", "That price + power-supply adder", 2, 5, "good")
    f.edge("s", "n").edge("n", "np", "yes", style="good").edge("n", "pe", "no")
    f.edge("pe", "none", "no", style="warn").edge("pe", "m", "yes").edge("m", "k").edge("k", "d")
    f.edge("d", "wait", "no", style="warn").edge("d", "ok", "yes", style="good")
    return f.drawing()


@fig("ATLAS", "Which exporter builds which document",
     "The Export button asks three questions; the answers pick one route and one module. A "
     "quick BOQ skips the question about detail and goes to the consolidated pair.")
def atlas_exports():
    f = Flow(cols=4, vgap=14)
    f.node("q1", "Export button: document, then detail, then format", 0, 0, "actor", rspan=5)
    f.node("bs", "BOQ, Summarised", 1, 0, sub="/export-boq-summary")
    f.node("bd", "BOQ, Detailed", 1, 1, sub="/export")
    f.node("qs", "Quotation, Summarised", 1, 2, sub="/export-quotation-summary")
    f.node("qd", "Quotation, Detailed", 1, 3, sub="/export-quotation")
    f.node("qa", "Quotation, area as subheader", 1, 4, sub="/export-quotation-areas")
    f.node("m1", "boq_summary.py", 2, 0, "doc", sub=".xlsx / .pdf")
    f.node("m2", "excel_export / pdf_export", 2, 1, "doc")
    f.node("m3", "boq_summary.py", 2, 2, "doc", sub="priced, deduplicated")
    f.node("m4", "quotation_export / _pdf_export", 2, 3, "doc")
    f.node("m5", "same writers, area_banners=True", 2, 4, "doc")
    f.node("x", "Add -pdf for the PDF; ?preview=true opens it inline", 3, 2, "note")
    for a, b in (("bs", "m1"), ("bd", "m2"), ("qs", "m3"), ("qd", "m4"), ("qa", "m5")):
        f.edge(a, b)
    for b in ("bs", "bd", "qs", "qd", "qa"):
        f.edge("q1", b, a_side="right", b_side="left")
    return f.drawing()


@fig("ATLAS", "The front end in one loop",
     "static/app.js has no framework. One state object, one render() that picks a view, and "
     "api() for every request. Every render is numbered so a slow, older one cannot draw over "
     "a newer screen (renderSuperseded).")
def atlas_spa():
    f = Flow(cols=4, vgap=18)
    f.node("ev", "Click, typing, drag", 0, 0, "actor")
    f.node("h", "Event handler", 1, 0)
    f.node("st", "state", 2, 0, "store", sub="view, currentBoq, collapsed sets ...")
    f.node("sv", "setView(view)", 3, 0, sub="refuses views this person may not open")
    f.node("r", "render()", 3, 1, sub="generation = ++renderGeneration")
    f.node("v", "renderLibrary / renderEditor / renderNpd ...", 2, 1, span=1)
    f.node("api", "api(path)", 1, 1, sub="fetch + error handling in one place")
    f.node("srv", "Server /api/...", 0, 1, "external")
    f.node("sup", "Superseded? generation changed", 2, 2, "decision")
    f.node("stop", "Return without drawing", 3, 2, "warn")
    f.node("dom", "Build HTML with escapeHtml() -> #app", 1, 2, "good")
    f.edge("ev", "h").edge("h", "st", "writes").edge("st", "sv").edge("sv", "r").edge("r", "v")
    f.edge("v", "api", "await data").edge("api", "srv", both=True)
    f.edge("v", "sup", route="v").edge("sup", "stop", "yes", style="warn").edge("sup", "dom", "no", style="good")
    return f.drawing()


@fig("ATLAS", "Shipping a change to the live server",
     "The deploy as it is actually done (FinalDeployment.md): only verified work, from an "
     "isolated branch, with a backup first and every migration dry-run and reviewed.")
def atlas_deploy():
    f = Flow(cols=4, vgap=14)
    f.zone("Your laptop", 0, 0, 1, 4, "trusted")
    f.zone("The VM", 2, 0, 3, 4, "server")
    f.node("v", "Verify locally", 0, 0, sub="real data, real files, clean up by id")
    f.node("w", "Isolated worktree / release branch", 1, 0, sub="only this change's hunks")
    f.node("t", "Re-verify the release copy", 1, 1, sub="compile, compare exports, boot it")
    f.node("p", "Push to GitHub main", 1, 2, sub="fast-forward only")
    f.node("ap", "Explicit approval for THIS change?", 0, 2, "decision")
    f.node("b", "Backup first", 2, 0, "store", sub="sudo /opt/backup.sh")
    f.node("pl", "git pull --ff-only", 3, 0)
    f.node("rs", "systemctl restart", 3, 1, sub="only if app code changed")
    f.node("dr", "Migration --dry-run", 3, 2, sub="read every line of it")
    f.node("ok", "Looks right?", 3, 3, "decision")
    f.node("rb", "Roll back: git checkout <prev>", 2, 3, "bad", sub="data: undo by id, or restore the backup")
    f.node("run", "Run it; dry-run again shows 0 left", 3, 4, "good")
    f.node("rec", "Record in FinalDeployment.md", 2, 4, "doc", sub="commit, backup, rollback point")
    f.edge("v", "w").edge("w", "t").edge("t", "p").edge("ap", "p", "yes", style="good")
    f.edge("p", "b", a_side="right", b_side="left")
    f.edge("b", "pl").edge("pl", "rs").edge("rs", "dr").edge("dr", "ok")
    f.edge("ok", "run", "yes", style="good").edge("run", "rec").edge("ok", "rb", "no", style="bad")
    return f.drawing()


# =============================================================================== PART 0

@fig("0.1", "A program, and the processes running it",
     "One file of instructions; two copies running at once on the live server, so one "
     "person's slow export does not stop everyone else.")
def p0_program():
    f = Flow(cols=3)
    f.node("file", "app/main.py", 1, 0, "doc", sub="a recipe: text on disk")
    f.node("run", "uvicorn --workers 2", 1, 1, sub="starts the recipe twice")
    f.node("p1", "Process 1", 0, 2, "good", sub="its own memory")
    f.node("p2", "Process 2", 2, 2, "good", sub="its own memory")
    f.node("port", "One port, 127.0.0.1:8000", 1, 3, "external", sub="requests shared between them")
    f.edge("file", "run", "is run by").edge("run", "p1").edge("run", "p2")
    f.edge("p1", "port", a_side="bottom", b_side="left").edge("p2", "port", a_side="bottom", b_side="right")
    return f.drawing()


@fig("0.2", "The fifteen words, grouped",
     "The vocabulary of 0.2 arranged by what each word describes. Each has a real example in "
     "this repo - the table in the text names the file.")
def p0_words():
    f = Flow(cols=4, vgap=12)
    f.zone("Values", 0, 0, 0, 3, "neutral")
    f.zone("Units of code", 1, 0, 1, 3, "server")
    f.zone("Between programs", 2, 0, 2, 3, "data")
    f.zone("Around the code", 3, 0, 3, 3, "warn")
    for i, (t, s) in enumerate([("variable", "a named value"), ("list", "[1, 2, 3]"), ("dictionary", '{"name": "Lobby"}'), ("condition / loop", "if / for")]):
        f.node("v%d" % i, t, 0, i, sub=s)
    for i, (t, s) in enumerate([("function", "def hash_password()"), ("argument / return", "in / out"), ("class / object", "class BOQItem"), ("module / package", "excel_export.py")]):
        f.node("c%d" % i, t, 1, i, sub=s)
    for i, (t, s) in enumerate([("API", "every /api/... address"), ("JSON", "text both sides read"), ("library / framework", "Pillow / FastAPI"), ("dependency", "requirements.txt")]):
        f.node("b%d" % i, t, 2, i, "store", sub=s)
    for i, (t, s) in enumerate([("environment variable", "DATABASE_URL"), ("bug", "code doing what was written"), ("traceback", "file + line where it gave up"), ("process", "a program running")]):
        f.node("a%d" % i, t, 3, i, "warn", sub=s)
    return f.drawing()


@fig("0.3", "The three places code runs, and who can be trusted",
     "The most important picture in the course. Hiding a button in the browser is courtesy; "
     "refusing the request on the server is security.")
def p0_three_places():
    f = Flow(cols=3, vgap=18)
    f.zone("Browser - the person controls it", 0, 0, 0, 2, "untrusted")
    f.zone("Server - the only enforcer", 1, 0, 1, 2, "server")
    f.zone("Database - unreachable from outside", 2, 0, 2, 2, "data")
    f.node("js", "static/app.js", 0, 0, "actor", sub="JavaScript, draws the screen")
    f.node("py", "app/main.py", 1, 0, sub="Python, decides what is allowed")
    f.node("pg", "Postgres", 2, 0, "store", sub="SQL, stores the data")
    f.node("hide", "Hides Delete for a member", 0, 1, "note", sub="courtesy: can be skipped")
    f.node("ref", "Refuses DELETE without the permission", 1, 1, "good", sub="security: cannot be skipped")
    f.node("only", "Listens on 127.0.0.1 only", 2, 1, "good")
    f.node("curl", "curl, or an edited page", 0, 2, "bad", sub="sends any request it likes")
    f.edge("js", "py", "HTTPS requests").edge("py", "pg", "SQL")
    f.edge("curl", "ref", "still meets", route="h", style="bad")
    return f.drawing()


@fig("0.4", "Opening the app, step by step",
     "The six steps of 0.4. Every action in the app repeats steps 4-6; steps 1-3 happen once "
     "per connection.")
def p0_open():
    s = Seq([("b", "Browser", "actor"), ("d", "DNS (Cloudflare)", "external"), ("c", "Caddy on the VM", "process"),
             ("a", "The app", "process")])
    s.msg("b", "d", "1. What IP is quotes.example.com?")
    s.msg("d", "b", "the public IP address", reply=True)
    s.msg("b", "c", "2. open a TCP connection to port 443")
    s.msg("b", "c", "3. TLS handshake: agree encryption, check the certificate")
    s.divider("every request from here on")
    s.msg("b", "c", "4. GET /api/boqs + cookie")
    s.msg("c", "a", "forwarded to 127.0.0.1:8000")
    s.msg("a", "c", "5. 200 OK + JSON", reply=True)
    s.msg("c", "b", "encrypted back", reply=True)
    s.self_("b", "6. draw the result")
    return s.drawing()


@fig("0.5", "A spreadsheet versus a database",
     "A relational database refuses data that breaks its rules: an item must belong to a real "
     "room, and deleting a floor removes its rooms (cascade=\"all, delete-orphan\").")
def p0_db():
    f = Flow(cols=3, vgap=18)
    f.node("sp", "A spreadsheet", 0, 0, "warn", sub="any cell, any value, no rules")
    f.node("db", "A relational database", 2, 0, "store", sub="types, keys and rules it enforces")
    f.node("t1", "boq_floors", 1, 1, "table", fields=["id 3", "name 'Ground'"])
    f.node("t2", "boq_rooms", 1, 2, "table", fields=["id 7", "floor_id 3  (must exist)"])
    f.node("bad", "INSERT room with floor_id 99", 0, 2, "bad", sub="refused: no such floor")
    f.node("del", "DELETE floor 3", 2, 2, "warn", sub="its rooms go with it")
    f.edge("t2", "t1", "foreign key").edge("bad", "t2", style="bad").edge("del", "t2", style="warn")
    f.edge("sp", "db", "the difference", route="h", dashed=True)
    return f.drawing()


@fig("0.6", "The five questions of production",
     "Any system you build should answer all five. Where this app answers each one.")
def p0_five():
    f = Flow(cols=2, vgap=12)
    qs = [("Who can get in?", "Google SSO, company domain only (10.4)"),
          ("What can they reach?", "one permission table + AuthGuard (10.5, 10.10)"),
          ("What happens when someone leaves?", "'No access' ends the session at the next click (10.5.5)"),
          ("What happens when a change goes wrong?", "backup, dry-run, rollback point (Part VI, VIII)"),
          ("Can you get the data back?", "nightly pg_dump + off-site copy, restored once (6.10)")]
    for i, (q, a) in enumerate(qs):
        f.node("q%d" % i, q, 0, i, "process")
        f.node("a%d" % i, a, 1, i, "good")
        f.edge("q%d" % i, "a%d" % i)
    return f.drawing()


@fig("0.7", "The order to read this course in",
     "The Parts as layers: each rests on the ones below it.")
def p0_order():
    f = Flow(cols=4, vgap=12)
    rows = [("Parts XIII-XVI: judgement - extending, AI tools, LLMs, agents", "warn"),
            ("Part XII: interviews", "note"),
            ("Parts VII-XI: practices - Git, testing, system design, security, debugging", "process"),
            ("Part VI: how it reaches real users", "external"),
            ("Part V: where the data lives", "store"),
            ("Part III + IV: the two halves of a web app", "process"),
            ("Parts 0, I, II: the foundation", "start")]
    for i, (t, k) in enumerate(rows):
        f.node("r%d" % i, t, 0, i, k, span=4)
    return f.drawing()


@fig("0.8", "Your first twenty minutes",
     "Five checks that turn the abstractions of Part 0 into things you have seen.")
def p0_first():
    f = Flow(cols=5, vgap=16)
    chain(f, [("a", "Start the app", "start", "HOW_TO_RUN.md"),
              ("b", "F12 -> Network, reload", "process", "watch step 4 of 0.4"),
              ("c", "Find the route", "process", 'search main.py for "/api/boqs"'),
              ("d", "Console: 2 + 2", "process", "JavaScript in the browser"),
              ("e", "git log --oneline", "end", "the real history")], horizontal=True)
    return f.drawing()


# =============================================================================== PART I

@fig("H:The 30-second mental model", "The 30-second mental model",
     "The same shape almost every web system has: a browser, a program that answers it, and a "
     "database behind that program.")
def p1_model():
    f = Flow(cols=3, vgap=18)
    f.node("b", "Your browser", 1, 0, "actor", sub="runs static/app.js")
    f.node("u", "uvicorn", 1, 1, sub="opens the port, hands each request on")
    f.node("fa", "FastAPI (app/main.py)", 1, 2, sub="what each URL means")
    f.node("sa", "SQLAlchemy (models.py)", 0, 3, "store")
    f.node("pp", "pdf_parser.py", 1, 3, sub="PDF -> data")
    f.node("xe", "excel_export.py", 2, 3, sub="data -> .xlsx")
    f.node("pg", "Postgres (live) / SQLite (early dev)", 0, 4, "store")
    f.edge("b", "u", "HTTP + JSON", both=True).edge("u", "fa").edge("fa", "sa").edge("fa", "pp").edge("fa", "xe")
    f.edge("sa", "pg", "SQL")
    return f.drawing()


@fig("H:`127.0.0.1` vs `0.0.0.0`", "Who can reach an address",
     "127.0.0.1 is the loopback: only programs on the same machine. 0.0.0.0 listens on every "
     "network card. The live app binds 127.0.0.1 and lets Caddy be the only public door.")
def p1_addresses():
    f = Flow(cols=3, vgap=16)
    f.zone("The same machine", 0, 0, 2, 0, "trusted")
    f.zone("The same network", 0, 1, 2, 1, "neutral")
    f.zone("The whole internet", 0, 2, 2, 2, "untrusted")
    f.node("lo", "127.0.0.1", 0, 0, "good", sub="app :8000, Postgres :5432")
    f.node("any", "0.0.0.0", 0, 1, "warn", sub="any interface - LAN devices too")
    f.node("pub", "Public IP / domain", 0, 2, "external", sub="Caddy :80 / :443 only")
    f.node("w1", "Reachable only from the VM itself", 1, 0, span=2, kind="note")
    f.node("w2", "Reachable by other devices on the network", 1, 1, span=2, kind="note")
    f.node("w3", "Reachable by strangers - so only a hardened proxy listens here", 1, 2, span=2, kind="note")
    return f.drawing()


@fig("H:Anatomy of one HTTP request", "Anatomy of one request and its reply",
     "The real request openAddFromLibraryModal sends, split into its four parts, and the reply.")
def p1_anatomy():
    f = Flow(cols=4, vgap=12)
    f.zone("Request", 0, 0, 1, 3, "server")
    f.zone("Response", 2, 0, 3, 3, "data")
    f.node("m", "Method", 0, 0, "process", sub="POST - create")
    f.node("p", "Path", 1, 0, sub="/api/rooms/5/items")
    f.node("h", "Headers", 0, 1, span=2, sub="Content-Type: application/json, Cookie: session=...")
    f.node("bd", "Body (JSON)", 0, 2, span=2, sub='{"specsheet_id": 6, "item_code": "CRA2.2", "qty": "10"}', kind="code")
    f.node("ver", "HTTP/1.1 or HTTP/2", 0, 3, span=2, kind="note")
    f.node("st", "Status", 2, 0, "good", sub="200 OK")
    f.node("rh", "Headers", 3, 0, sub="Content-Type: application/json")
    f.node("rb", "Body (JSON)", 2, 1, span=2, sub='{"id": 42, "room_id": 5, "finish": "BLACK"}', kind="code")
    f.node("rn", "HTTP/2 has no reason phrase: res.statusText is empty live (3.6)", 2, 2, span=2, kind="warn")
    return f.drawing()


@fig("H:Anatomy of one HTTP request", "Choosing the status code",
     "The order a request is judged in, and the code each failure earns. 401 means 'who are "
     "you?'; 403 means 'I know who you are, and no'.")
def p1_status():
    f = Flow(cols=3, vgap=12)
    f.node("r", "Request arrives", 1, 0, "start")
    f.node("a", "Body matches the declared shape?", 1, 1, "decision")
    f.node("a1", "422 Unprocessable", 0, 1, "bad")
    f.node("b", "Signed in?", 1, 2, "decision")
    f.node("b1", "401 Unauthorized", 0, 2, "bad")
    f.node("c", "Allowed?", 1, 3, "decision")
    f.node("c1", "403 Forbidden", 0, 3, "bad")
    f.node("d", "Does the row exist?", 1, 4, "decision")
    f.node("d1", "404 Not Found (get_or_404)", 0, 4, "bad")
    f.node("e", "Values acceptable?", 1, 5, "decision")
    f.node("e1", "400 Bad Request (_coerce_price)", 0, 5, "bad")
    f.node("ok", "200 OK", 1, 6, "good")
    f.node("crash", "Code crashes anywhere: 500 - read journalctl", 2, 3, "warn")
    for x, y in (("a", "a1"), ("b", "b1"), ("c", "c1"), ("d", "d1"), ("e", "e1")):
        f.edge(x, y, "no", style="bad")
    f.edge("r", "a").edge("a", "b", "yes").edge("b", "c", "yes").edge("c", "d", "yes").edge("d", "e", "yes").edge("e", "ok", "yes", style="good")
    return f.drawing()


@fig("H:JSON as the shared language", "JSON between Python and JavaScript",
     "Neither side speaks the other's language; both speak JSON text.")
def p1_json():
    f = Flow(cols=5, vgap=14)
    chain(f, [("py", "Python dict", "process", "{'id': 42}"),
              ("fa", "FastAPI serialises", "process"),
              ("tx", "JSON text", "code", '{"id": 42}'),
              ("fe", "res.json() parses", "process"),
              ("js", "JavaScript object", "process", "{id: 42}")], horizontal=True)
    return f.drawing()


@fig("H:Asynchronous programming, concretely", "What await actually pauses",
     "await pauses this one function, not the browser. Other clicks and requests carry on "
     "while the network answers.")
def p1_async():
    s = Seq([("f", "api() function", "process"), ("b", "The browser", "actor"), ("n", "Network / server", "external")])
    s.msg("f", "n", "fetch('/api/boqs') starts")
    s.note("f", "f", "await: this function pauses here", "note")
    s.self_("b", "still scrolls, animates, handles other clicks")
    s.msg("b", "n", "another request can start meanwhile")
    s.msg("n", "f", "response arrives", reply=True)
    s.self_("f", "resumes on the next line: res.json()")
    return s.drawing()


@fig("H:One request, traced end to end", "One request, traced end to end",
     "The eight steps of 'Add to Room'. Every feature in the app has this shape; only step 6 "
     "changes.")
def p1_trace():
    f = Flow(cols=2, vgap=12)
    steps = [("1 Browser", "openAddFromLibraryModal builds a payload, calls api()", "actor"),
             ("2 Network", "TCP connection, HTTP request written onto it", "external"),
             ("3 uvicorn", "reads bytes, builds a request object", "process"),
             ("4 Routing", "POST /api/rooms/{room_id}/items -> add_item()", "process"),
             ("5 Dependency injection", "get_db() opens a session, closes it after", "process"),
             ("6 Endpoint body", "BOQItem(...) -> db.add -> db.commit = INSERT", "store"),
             ("7 Serialisation", "room_dict(r) -> JSON text", "process"),
             ("8 Browser", "patch state, redraw only that room", "good")]
    for i, (t, s, k) in enumerate(steps):
        c, r = (0, i) if i < 4 else (1, 7 - i)
        f.node("s%d" % i, t, c, r, k, sub=s)
    for i in range(7):
        f.edge("s%d" % i, "s%d" % (i + 1))
    return f.drawing()


# =============================================================================== PART II

@fig("2.1", "Mutability: two names, one list",
     "Passing a mutable list into a function passes the list itself. Changing it there "
     "changes it for the caller too; a string would have been copied instead.")
def p2_mut():
    f = Flow(cols=3, vgap=16)
    f.node("a", "items (caller)", 0, 0, "process")
    f.node("b", "rows (inside the function)", 0, 1, "process")
    f.node("l", "[ A, B, C ]", 1, 0, "store", rspan=2, sub="one list in memory")
    f.node("ap", "rows.append(D)", 2, 1, "warn")
    f.node("res", "the caller now sees [A, B, C, D]", 2, 0, "bad")
    f.edge("a", "l", "points to").edge("b", "l", "points to").edge("ap", "l", "changes")
    return f.drawing()


@fig("2.2", "Choosing a data structure",
     "The questions that pick one. The examples are real ones from this repo.")
def p2_ds():
    f = Flow(cols=3, vgap=12)
    f.node("q1", "Does order matter?", 1, 0, "decision")
    f.node("q2", "Look things up by a key?", 1, 1, "decision")
    f.node("q3", "Only whether it is present?", 1, 2, "decision")
    f.node("q4", "Parent / child links?", 1, 3, "decision")
    f.node("list", "List", 2, 0, "good", sub="room.items in sort_order")
    f.node("dict", "Dict / hash map", 0, 1, "good", sub="CANONICAL_FIELDS: text -> key")
    f.node("set", "Set", 0, 2, "good", sub="state.collapsedFloors")
    f.node("tree", "Tree", 0, 3, "good", sub="BOQ -> floor -> room -> item")
    f.node("graph", "Graph (cycles allowed)", 2, 3, "note", sub="not needed here")
    f.edge("q1", "list", "yes, by position", style="good").edge("q1", "q2", "no")
    f.edge("q2", "dict", "yes", style="good").edge("q2", "q3", "no")
    f.edge("q3", "set", "yes", style="good").edge("q3", "q4", "no")
    f.edge("q4", "tree", "no cycles", style="good").edge("q4", "graph", "cycles")
    return f.drawing()


@fig("2.2", "How running time grows",
     "Big-O compares shapes, not speeds. By n = 20 the quadratic curve has left the chart.")
def p2_bigo():
    return complexity_chart()


@fig("2.3", "Fixed-point iteration in resolve_fields()",
     "One field's rule can change what is valid in the next, so a single pass is not enough. "
     "Repeat until nothing changes - and cap it so a circular rule set cannot hang the server.")
def p2_fixed():
    f = Flow(cols=3, vgap=14)
    f.node("s", "Options + chosen values", 1, 0, "start")
    f.node("p", "Apply every rule once, in FIELD_ORDER", 1, 1)
    f.node("c", "Anything changed this pass?", 1, 2, "decision")
    f.node("n", "Passes < 5?", 0, 2, "decision")
    f.node("done", "Stable answer", 2, 2, "good")
    f.node("cap", "Stop anyway - the cap", 0, 3, "warn")
    f.edge("s", "p").edge("p", "c").edge("c", "done", "no", style="good").edge("c", "n", "yes")
    f.edge("n", "p", "yes", route="vh", a_side="top", b_side="left").edge("n", "cap", "no", style="warn")
    return f.drawing()


@fig("2.3", "Inserting in the middle of an ordered list",
     "duplicate_item / add_item: every sibling after the insertion point moves down one, then "
     "the new row takes the gap. O(n) in the number of siblings.")
def p2_insert():
    f = Flow(cols=6, vgap=12)
    before = ["0 A", "1 B", "2 C", "3 D"]
    after = ["0 A", "1 B", "2 NEW", "3 C", "4 D"]
    for i, t in enumerate(before):
        f.node("b%d" % i, t, i, 0, "process")
    f.node("lab1", "before: insert after B", 4, 0, "text", span=2)
    for i, t in enumerate(after):
        f.node("a%d" % i, t, i, 1, "good" if "NEW" in t else ("warn" if i > 2 else "process"))
    f.node("lab2", "after: C and D each moved down one (sort_order += 1), NEW took position 2",
           0, 2, "text", span=6)
    f.edge("b2", "a3", "+1", style="warn", route="d").edge("b3", "a4", "+1", style="warn", route="d")
    return f.drawing()


@fig("2.4", "Where the classes in this app come from",
     "Inheritance and encapsulation in practice: every model inherits its table machinery from "
     "SQLAlchemy's Base, and room.items hides a SQL query behind an attribute.")
def p2_oop():
    f = Flow(cols=4, vgap=16)
    f.node("base", "Base (SQLAlchemy)", 1, 0, "external", span=2, sub="Column, table mapping, session tracking")
    for i, m in enumerate(["BOQ", "BOQRoom", "BOQItem", "Specsheet"]):
        f.node("m%d" % i, m, i, 1)
        f.edge("m%d" % i, "base", "inherits", route="vhv" if i not in (1, 2) else "v")
    f.node("acc", "room.items", 1, 2, "code", sub="looks like a list")
    f.node("sql", "SELECT * FROM boq_items WHERE room_id = ?", 2, 2, "store", span=2, sub="runs the first time you touch it")
    f.edge("acc", "sql", "actually")
    return f.drawing()


@fig("LEGEND", "How to read the diagrams",
     "One visual language across every figure. Colour means the same thing on every page.")
def legend():
    f = Flow(cols=4, vgap=12)
    items = [("process", "Code that runs", "a function, a module, a step"),
             ("decision", "A decision", "yes / no branches"),
             ("start", "Start or end", "where a flow begins"),
             ("store", "Stored data", "a table, a database, a cache"),
             ("doc", "A file or document", "a PDF, an export, data/"),
             ("actor", "A person or browser", "outside the server's control"),
             ("external", "Another system", "Google, Groq, GitHub, a firewall"),
             ("good", "The good path", "what should happen"),
             ("bad", "The bad path", "a failure or the wrong way"),
             ("warn", "Needs care", "a trade-off or risk"),
             ("note", "A note", "context, not a step"),
             ("code", "Literal code", "as written in the repo")]
    for i, (k, t, s) in enumerate(items):
        f.node("k%d" % i, t, i % 4, i // 4, k, sub=s)
    return f.drawing()


def all_figures():
    """Every registered figure, from all the figure modules."""
    import tutorial_diagrams_p3_6  # noqa: F401  (registers on import)
    import tutorial_diagrams_p7_10  # noqa: F401
    import tutorial_diagrams_p11_16  # noqa: F401
    return FIGS
