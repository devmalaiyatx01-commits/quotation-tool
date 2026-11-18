import collections
import contextlib
import datetime
import hashlib
import os
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Body, Request, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.datastructures import MutableHeaders
from sqlalchemy.orm import Session, defer, load_only, selectinload
import re

from sqlalchemy import distinct, or_, func

from . import models
from .database import THUMBS_DIR, Base, engine, get_db, UPLOADS_DIR, IMAGES_DIR, EXPORTS_DIR, BASE_DIR, SessionLocal
from .pdf_parser import parse_specsheet_pages, build_description
from .exenia_codes import compose_exenia_code, config_layout
from . import accessories
from . import accessory_parts
from . import npd
from . import configurator
from . import duragreen
from . import duragreen_import
from . import product_codes
from .excel_export import (
    export_boq, COLUMN_KEYS, COLUMN_LABELS, DEFAULT_NOTES_TEXT, LINE_DIAGRAM_LABEL,
)
from .pdf_export import export_boq_pdf
from .quotation_export import (
    export_quotation, CURRENCY_PREFIXES, QUOTATION_COLUMN_KEYS, QUOTATION_COLUMN_LABELS,
)
from .quotation_pdf_export import export_quotation_pdf
from .consolidated_export import export_consolidated_boq, export_consolidated_quotation
from .consolidated_pdf_export import (
    export_consolidated_boq_pdf, export_consolidated_quotation_pdf,
)
from .boq_summary import (
    export_boq_summary, export_boq_summary_pdf, export_quotation_summary,
    export_quotation_summary_pdf, SUMMARY_QUOTATION_COLUMN_KEYS,
)
from .master_price_export import export_master_price_list
from .master_price_import import import_master_price_list
from .pricing_master import export_pricing_master, import_pricing_master
from .config_rules import resolve_fields, field_labels_for, stated_values, FIELD_ORDER, FIELD_LABELS, ALL_ACTIONS
from .config_rules import OPTION_VIS_ACTIONS, OPTION_ENABLE_ACTIONS
OPTION_VIS_ACTIONS_ALL = OPTION_VIS_ACTIONS | OPTION_ENABLE_ACTIONS
from .typology_classifier import classify_typology
from .shape_classifier import classify_shape, classify_specsheet_shape
from .environment_classifier import classify_environment
from . import price_matcher
from .requirement_matcher import (extract_requirement_fields, rank_specsheets,
                                  block_needs_manual_split, FIELD_WEIGHTS)
from .requirement_reader import read_document, read_text
from . import llm_extract
from . import match_index
from . import boq_import
from . import boq_ai
from . import assistant, catalogue_query
from .requirement_excel import (read_requirements, build_template, has_pictures, sheet_pictures, SheetError,
                                MAX_BYTES as SHEET_MAX_BYTES)
from . import requirement_pictures
from .typology_match import category_kind, typology_key

# The fields a match is actually scored on - so "did the regexes find enough?" is
# measured against what changes the ranking, not against every field that exists.
# Taken from the weights table itself rather than restated, which would drift from it.
MATCH_SCORED_FIELDS = tuple(FIELD_WEIGHTS)
from .auth import verify_password
from .google_auth import oauth, GOOGLE_CONFIGURED, is_allowed_email

Base.metadata.create_all(bind=engine)

# Real secret in production (see deployment.md step 5 - set via the service's environment,
# never committed) - signs the session cookie, so anyone who knows it could forge a login.
# The fallback below is fine for local dev only; refusing to start without a real one once
# ENVIRONMENT=production is set is what stops that fallback from ever reaching a real server.
SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    if os.environ.get("ENVIRONMENT") == "production":
        raise RuntimeError("SESSION_SECRET must be set in the environment before running with ENVIRONMENT=production - see deployment.md step 5.")
    SESSION_SECRET = "dev-only-insecure-secret-change-me"

# Prototype mode (this public copy only): sign-in is switched off so anyone can try the app
# locally without creating an account. Every visitor is treated as a local demo administrator.
# PROTOTYPE_AUTH=on restores the original sign-in (password or Google) unchanged, and it is
# always on under ENVIRONMENT=production, so a real deployment can never run without sign-in.
PROTOTYPE_NO_AUTH = (os.environ.get("PROTOTYPE_AUTH", "off").lower() not in ("on", "1", "true")
                     and os.environ.get("ENVIRONMENT") != "production")
DEMO_EMAIL = "demo@example.com"

app = FastAPI(title="Quotation Tool")


class VersionedStatic(StaticFiles):
    """StaticFiles, but a request carrying ?v=... is told it can be cached forever.

    index() already stamps app.js and style.css with their own mtime, so the URL changes the
    moment the file does. Without a Cache-Control header, though, a browser still asks the
    server about them on every single page load - and being told "not modified" costs the
    same round trip as being told anything else. Over a link with 70ms of latency and a TLS
    handshake on a cold connection, that is a quarter of a second spent confirming a 108KB
    file we already know is current.

    Only ?v= requests get the long life. An unversioned URL cannot distinguish one build from
    the next, so it keeps the cautious default.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        if b"v=" in scope.get("query_string", b""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# A browser obeys the type a file is served with, and StaticFiles takes that type from the
# file's extension - so a picture folder that ever held an .svg or .html would hand the browser a
# document able to run script as the signed-in person. Uploads are held to picture types (see
# PICTURE_SUFFIXES), and this is the second wall: `sandbox` tells the browser that whatever this
# response is, it may run no script and has no access to the app's cookies. An <img> ignores the
# header entirely, so every picture shows exactly as before.
PICTURE_RESPONSE_HEADERS = {"Content-Security-Policy": "sandbox"}


class PictureStatic(StaticFiles):
    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers.update(PICTURE_RESPONSE_HEADERS)
        return response


app.mount("/static", VersionedStatic(directory=str(BASE_DIR / "static")), name="static")
app.mount("/media/images", PictureStatic(directory=str(IMAGES_DIR)), name="images")
# The company logo/favicon (app/assets/logo.png) - already the single source of truth for
# every export's Cover sheet (see excel_export.LOGO_PATH); mounted here too so the web UI's
# topbar/login page/browser tab can show the exact same file instead of a second copy that
# could drift from it.
# Versioned the same way as /static: the pages ask for /app-assets/logo-web.png?v=<mtime>, so
# the browser can keep the logo and favicon for a year and still see a replacement at once.
# Unversioned URLs keep the cautious default.
app.mount("/app-assets", VersionedStatic(directory=str(BASE_DIR / "app" / "assets")), name="app_assets")


def _asset_version() -> int:
    """One token for the logo and favicon the pages show - changes when either file does."""
    folder = BASE_DIR / "app" / "assets"
    stamps = []
    for name in ("logo-web.png", "favicon.png"):
        try:
            stamps.append(int((folder / name).stat().st_mtime))
        except OSError:
            pass
    return max(stamps) if stamps else 0
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------- auth ----------

# /login, /static and /app-assets are the only things reachable while logged out (the login
# page itself is a standalone template with no app.js dependency, so it doesn't strictly need
# /static, but leaving JS/CSS open is harmless - it's app code, not client data). /app-assets
# specifically has to be public: the login page's own logo/favicon load from there, before
# any session exists. Everything else - every page and every /api/* route - requires a session.
# /favicon.ico too: a browser asks for it on its own wherever a page names no icon (a PDF or
# picture opened in a tab), and it is the same public file the login page already shows.
_PUBLIC_PATHS = {"/login", "/auth/google/login", "/auth/google/callback", "/favicon.ico"}
_PUBLIC_PREFIXES = ("/static/", "/app-assets/")


# ---------- what each person may do ----------
#
# Every action that changes something belongs to a named permission, and an admin gives or
# takes away each permission per person from the People tab - so who can do what is managed
# by whoever runs the company's accounts, not by whoever can edit this file. An admin holds
# every permission, and alone can manage people.
#
# Held here, as rules over the request, rather than as a check added to each endpoint - so an
# endpoint added next year is covered by the rule for its path without anyone remembering to
# add a check. Reading is open unless a rule below says otherwise.

# (key, group, name, what it lets a person do, given to a new account unless an admin changes that)
PERMISSIONS = [
    ("boqs.view", "BOQs & quotations", "See BOQs",
     "Open any BOQ and download its BOQ or quotation.", True),
    ("boqs.edit", "BOQs & quotations", "Build and edit BOQs",
     "Create BOQs, and add, edit, copy and move their floors, areas and rows.", True),
    ("boqs.delete", "BOQs & quotations", "Delete BOQs",
     "Delete a whole BOQ and everything in it. This cannot be undone.", False),
    ("specsheets.add", "Specsheet Library", "Add and edit specsheets",
     "Upload specsheet PDFs to the library and edit their details.", True),
    ("specsheets.delete", "Specsheet Library", "Delete specsheets",
     "Remove a specsheet from the library.", False),
    ("npd.edit", "Products & prices", "Create and edit products",
     "Add products in NPD and change their specification - which is what makes their "
     "specsheets.", False),
    ("prices.edit", "Products & prices", "Change prices",
     "Change product prices, option adders, accessory and driver rates, and import price "
     "lists.", False),
    ("npd.approve", "Products & prices", "Approve products",
     "Publish a product from NPD into the Specsheet Library, or take it back out.", False),
    # The key keeps its old name: it is stored against every person, and renaming it would take
    # the permission away from everyone who has it. What it opens is the Nomenclature tab now.
    ("config.edit", "Settings", "Open the Nomenclature tab",
     "See the product-code nomenclature: how each product's code is built and the abbreviation "
     "for every value. Changing them also needs “Create and edit products”.", False),
    ("activity.view", "Settings", "See the activity log",
     "See who did what, across everyone.", False),
]
PERMISSION_KEYS = [p[0] for p in PERMISSIONS]
DEFAULT_NEW_PERMISSIONS = [p[0] for p in PERMISSIONS if p[4]]

# `exports` is here because /api/exports/{id}/download hands over a saved BOQ or quotation file,
# which is exactly what "See BOQs" is about - outside this list it needed no permission at all.
_BOQ_PATH = re.compile(r"^/api/(boqs|floors|rooms|items|exports)(/|$)")
_ONE_BOQ = re.compile(r"^/api/boqs/\d+/?$")
_ONE_SPECSHEET = re.compile(r"^/api/specsheets/\d+/?$")
_NPD_PRODUCT = re.compile(r"^/api/npd/products(/\d+)?/?$")
_NPD_STATUS = re.compile(r"^/api/npd/products/\d+/status/?$")
_NPD_ONE_PRODUCT = re.compile(r"^/api/npd/products/\d+/?$")
_ACCESSORY_LIBRARY_PRICE = re.compile(r"^/api/accessory-library/\d+/price/?$")
_DG_ONE_PRODUCT = re.compile(r"^/api/npd/duragreen/products/\d+/?$")
_DG_STATUS = re.compile(r"^/api/npd/duragreen/products/\d+/status/?$")


def _required_permission(method, path):
    """What a request needs: a permission key, "a|b" for either of two, "admin" (the role), or None.

    Only the path and method decide it, so the answer is the same whoever asks - the guard
    then checks it against the person."""
    write = method in ("POST", "PUT", "PATCH", "DELETE")
    if path.startswith("/api/admin/"):
        return "admin"
    if path == "/api/activity" or path.startswith("/api/activity/"):
        return "activity.view"
    if path.startswith("/api/npd/"):
        if not write:
            return None                              # everyone may look through NPD
        # NPD (DuraGreen). The code tester is a POST only because it carries a configuration; it
        # writes nothing. Releasing a product is approval, as in InHouse's NPD. One save carries
        # specification and prices, so either permission gets a person through the door and the
        # endpoint checks what they changed. Everything else - adding a product, a printed code
        # or a code part DuraGreen sent, and reading a product in from DuraGreen's website or a
        # spec sheet (its preview included: it fetches from outside on the server's behalf) - is
        # authoring. So is setting up a naming structure (Nomenclature) and the letters in it.
        if path.startswith("/api/npd/duragreen/"):
            if path.rstrip("/") == "/api/npd/duragreen/code":
                return None
            if _DG_STATUS.match(path):
                return "npd.approve"
            if method == "PUT" and _DG_ONE_PRODUCT.match(path):
                return "npd.edit|prices.edit"
            return "npd.edit"
        if path.startswith("/api/npd/driver-prices"):
            return "prices.edit"
        # Which details the Configurator asks for a typology: part of defining the products, so it
        # asks what authoring them asks.
        if path.rstrip("/") == "/api/npd/configurator":
            return "npd.edit"
        # The short code an identity value contributes to a product code. It writes one row of
        # the Product Codes table, for identity fields only (the endpoint refuses any other
        # field), so whoever may author a product may name the abbreviation of the series they
        # just invented - without that being permission to rewrite the rest of that table.
        if path.startswith("/api/npd/identity-code"):
            return "npd.edit|config.edit"
        # The product-code preview is a POST only because it carries the unsaved form; it
        # writes nothing, and anyone who may look at a product may see the code it would get.
        if path.startswith("/api/npd/codes/preview"):
            return None
        if _NPD_STATUS.match(path):
            return "npd.approve"
        # Deleting a product is for an admin alone: it cannot be undone from the screen
        # (npd_delete_product). Removing a parameter cannot either, but it is part of authoring a
        # typology's template, so it stays with npd.edit below (owner, 18 Nov 2026); it is refused
        # while the parameter prices, codes or rules anything, and the log keeps what it removed.
        if method == "DELETE" and _NPD_ONE_PRODUCT.match(path):
            return "admin"
        if _NPD_PRODUCT.match(path):
            # One save carries both the specification and the prices, so either permission
            # gets a person through the door; the endpoint then checks what they changed.
            return "npd.edit|prices.edit"
        return "npd.edit"                            # parameters (incl. reordering) and typologies
    if method == "DELETE" and _ONE_BOQ.match(path):
        return "boqs.delete"
    if _BOQ_PATH.match(path):
        # Everything under boqs/floors/rooms/items, including the copy-and-move endpoints
        # (/api/rooms/{id}/transfer, /api/floors/{id}/duplicate, /api/items/transfer):
        # duplicating a floor writes rows into this BOQ exactly as adding them by hand
        # does, so it asks for exactly what adding them by hand asks for.
        return "boqs.edit" if write else "boqs.view"
    if path.startswith("/api/specsheets"):
        if method == "DELETE" and _ONE_SPECSHEET.match(path):
            return "specsheets.delete"
        if method == "POST" and path.rstrip("/") == "/api/specsheets/upload":
            return "specsheets.add"
        if method == "PUT" and _ONE_SPECSHEET.match(path):
            return "specsheets.add"
        return None     # reading, and lookups sent as POST only because they carry a body
    if write and (path.startswith("/api/price-entries") or path.startswith("/api/list-pricing")):
        return "prices.edit"
    # A rule ("when IP66, hide the IP68 connector") is part of defining a product, so whoever may
    # author products may write one, as may whoever keeps the configuration (NPD > Rules, 17 Nov 2026).
    if write and path.startswith("/api/config-rules"):
        return "npd.edit|config.edit"
    if write and path.startswith("/api/field-codes"):
        return "config.edit"
    # The Match tab reads the catalogue and the person's own spreadsheet, and changes nothing in
    # the database - an import is parsed and handed straight back. The one thing it keeps is the
    # client's PDF (or a sheet's pictures) under a random token for a few weeks, so each row can
    # show its picture - see app/requirement_pictures.py. What it adds to a BOQ goes through the
    # BOQ endpoints above, which is where boqs.edit is asked for.
    if path.startswith("/api/requirement-match"):
        return None
    # Ask (app/assistant.py) reads the catalogue, which everyone may read, and changes nothing.
    # BOQs are among what it reads only for someone who may see BOQs - the endpoint tells it so.
    if path.startswith("/api/ask"):
        return None
    # The InHouse accessory library (app/accessory_parts.py) is part of authoring products in NPD,
    # so changing it asks what authoring a product asks; an accessory's price is a price like any
    # other. Reading it is open to anyone who may look at the products.
    if write and _ACCESSORY_LIBRARY_PRICE.match(path):
        return "prices.edit"
    if path.startswith("/api/accessory-library"):
        return "npd.edit" if write else None
    # The accessory catalogue is a reading of the specsheet library, which everyone may read.
    # Renaming one writes back into the specsheets themselves, so it asks for the permission that
    # editing a specsheet asks for.
    if path.startswith("/api/accessories"):
        return "specsheets.add" if write else None
    return None


def _access(user_id):
    """(role, the permissions this account holds right now). Read from the database on every
    request, so a grant or a revocation applies to the person's very next click."""
    if not user_id:
        return None, set()
    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        if not user:
            return None, set()
        if user.role == NO_ACCESS:
            return NO_ACCESS, set()
        if user.role == "admin":
            return "admin", set(PERMISSION_KEYS)
        now_ = datetime.datetime.utcnow()
        held = {g.permission for g in db.query(models.UserPermission)
                .filter(models.UserPermission.user_id == user_id).all()
                if g.expires_at is None or g.expires_at > now_}
        return user.role or "member", held
    finally:
        db.close()


def _satisfies(held, needed):
    return any(n in held for n in needed.split("|"))


def _permission_name(key):
    for k, _group, name, _desc, _default in PERMISSIONS:
        if k == key:
            return name
    return key


def user_can(request, key):
    """Whether the person the app is being shown as holds a permission."""
    return key in _access(effective_user_id(request))[1]


def require_permission(key):
    """A dependency form of the same check, for an endpoint that wants it said explicitly."""
    def dependency(request: Request):
        if not user_can(request, key):
            raise HTTPException(status_code=403,
                                detail="You need \u201c%s\u201d to do this - ask an admin."
                                % _permission_name(key))
    return dependency


def _account_state(user_id):
    """"ok", "off" (an admin has switched their access off), "outside" (not a company address -
    only @example.com accounts may use the app), or "gone" (no such account any more). Told
    apart deliberately: an account row with no role recorded is an ordinary member from before
    roles existed, and must never be read as switched off."""
    if not user_id:
        return "gone"
    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        if not user:
            return "gone"
        # Asked here, on every request, and not only at sign-in: an outside account that was
        # signed in before this rule existed is stopped at its next click, not in two weeks
        # when its session cookie runs out.
        if not _is_company_email(user.email):
            return "outside"
        return "off" if user.role == NO_ACCESS else "ok"
    finally:
        db.close()


def _user_role(user_id):
    """The account's role as the database says it now - not as it was at sign-in.

    The session also carries a role, copied in when the person signed in, and until now that
    copy was what decided access. It goes stale: an account made an admin and later changed
    back would keep admin rights until it next signed out. Reading it from the database on
    each request means a change of role takes effect on the next click."""
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        return user.role if user else None
    finally:
        db.close()


# ---------- previewing the app as someone else ----------
#
# An admin can see the app exactly as another account does, to check what that person can and
# cannot reach - without their password, and without being able to act as them. While a
# preview is on, every decision about access is made for the previewed account: which tools
# open, which tabs show, which actions are refused. Changes are not: nothing is ever carried
# out under someone else's name. Instead the request is answered with what would have
# happened - "would be allowed" or "would be refused" - so an admin can press Delete on a BOQ
# and find out whether that person could have, without the BOQ going anywhere.
#
# The session keeps the admin's own id as user_id throughout. The previewed account is a
# second value, honoured only while the account that set it is still an admin.

def _identity(request):
    """(real user id, id to decide access for, is a preview on). The two ids differ only while
    an admin is previewing someone."""
    real = request.session.get("user_id")
    target = request.session.get("preview_as")
    if target and target != real and _user_role(real) == "admin" and _user_role(target):
        return real, target, True
    return real, real, False


def effective_user_id(request):
    return _identity(request)[1]


def _email_of(user_id):
    """An account's email, from a fresh session - for the guard, which has none of its own."""
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        return user.email if user else None
    finally:
        db.close()


def _demo_user():
    """(id, role) of the local demo administrator used while sign-in is off; created on first
    use with a random password nobody knows, so it can never be signed into from the login form."""
    import secrets
    from app.auth import hash_password
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.email == DEMO_EMAIL).first()
        if not user:
            user = models.User(email=DEMO_EMAIL, password_hash=hash_password(secrets.token_urlsafe(32)),
                               role="admin")
            db.add(user)
            db.commit()
            db.refresh(user)
        return user.id, user.role or "admin"
    finally:
        db.close()


class AuthGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)
        if PROTOTYPE_NO_AUTH and not request.session.get("user_id"):
            request.session["user_id"], request.session["role"] = _demo_user()
        if not request.session.get("user_id"):
            if path.startswith("/api/") or path.startswith("/media/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url="/login")

        # Whether the account is still allowed in is asked on every request, not just at
        # sign-in: someone switched off - or removed - while they have the app open stops at
        # their very next click, instead of carrying on until their session happens to expire.
        state = _account_state(request.session.get("user_id"))
        if state != "ok":
            request.session.clear()
            where = "/login?error=" + {"off": "disabled", "outside": "company_only"}.get(state, "gone")
            if path.startswith("/api/") or path.startswith("/media/"):
                return JSONResponse(
                    {"detail": {"off": "Your access to the app has been switched off.",
                                "outside": "Only @%s accounts can use the app." % COMPANY_DOMAIN,
                                }.get(state, "Your account is no longer in the app.")},
                    status_code=401, headers={"X-Login-Url": where})
            return RedirectResponse(url=where)

        real, effective, previewing = _identity(request)

        # Starting and stopping a preview, and the list of people to preview, belong to the
        # admin themself - never to the account being previewed, which could not reach them,
        # and could then never stop.
        if path.startswith("/api/admin/"):
            if _user_role(real) != "admin":
                return JSONResponse({"detail": "Only an admin can do this."}, status_code=403)
            return await call_next(request)

        needed = _required_permission(request.method, path)
        # "admin" is a role, not a permission anyone can be given, so it is checked as one - on
        # the account the app is acting as, so a preview shows what that person would get.
        role, held = _access(effective)
        refused = bool(needed) and not (role == "admin" if needed == "admin" else _satisfies(held, needed))

        # Only a request that changes something is answered instead of carried out. A download
        # is recorded in the log too, but it is a GET and changes nothing - previewing whether
        # someone can export a quotation should hand over the quotation.
        if previewing and path.startswith("/api/") and \
                request.method in ("POST", "PUT", "PATCH", "DELETE") and \
                _activity_route(request.method, path)[0] is not None:
            who = _email_of(effective)
            if refused:
                return JSONResponse(
                    {"detail": "%s would be refused this - it needs \u201c%s\u201d, which "
                               "they have not been given." % (
                                   who, " or ".join(_permission_name(n) for n in needed.split("|"))),
                     "preview": "refused"}, status_code=403,
                    headers={"X-Preview-Result": "refused"})
            return JSONResponse(
                {"detail": "%s would be allowed to do this. Nothing was changed, because you "
                           "are previewing as them." % who,
                 "preview": "allowed"}, status_code=409,
                headers={"X-Preview-Result": "allowed"})

        if refused and needed == "admin":
            return JSONResponse({"detail": "Only an admin can do this."}, status_code=403)
        if refused:
            return JSONResponse(
                {"detail": "You need \u201c%s\u201d to do this. An admin can give it to you "
                           "from the People tab." % " or ".join(
                               _permission_name(n) for n in needed.split("|"))},
                status_code=403)
        return await call_next(request)


# ---------- the activity log ----------
#
# Every change anybody makes is recorded: who, when, what, and which project it touched. The
# headline actions - a BOQ created, duplicated, branched or deleted, a specsheet deleted -
# are written by their own endpoints, with names, in the same transaction as the change.
# Everything else is caught here afterwards, so a change made through an endpoint nobody
# remembered to instrument is still recorded, just with a plainer description.

# (method, path pattern, action key, what an admin reads). A None label means the request
# does not change anything - matching and option lookups are POSTs only because they carry a
# body - and is not recorded.
_ACTIVITY_ROUTES = [
    ("POST", r"/api/specsheets/upload", "specsheet.upload", "Uploaded specsheets"),
    ("PUT", r"/api/specsheets/\d+", "specsheet.update", "Edited a specsheet"),
    ("DELETE", r"/api/specsheets/\d+", "specsheet.delete", "Deleted a specsheet"),
    ("POST", r"/api/accessories/[^/]+/rename", "accessory.rename", "Renamed an accessory"),
    ("PUT", r"/api/accessories/[^/]+/details", "accessory.details", "Set an accessory's parameters"),
    ("POST", r"/api/accessories/[^/]+/image", "accessory.image", "Set an accessory's picture"),
    ("DELETE", r"/api/accessories/[^/]+/image", "accessory.image", "Removed an accessory's picture"),
    ("POST", r"/api/accessory-library", "accessory.create", "Added an accessory to the library"),
    ("PUT", r"/api/accessory-library/\d+", "accessory.update", "Edited a library accessory"),
    ("PUT", r"/api/accessory-library/\d+/price", "accessory.price", "Priced a library accessory"),
    ("POST", r"/api/accessory-library/\d+/image", "accessory.image", "Set a library accessory's picture"),
    ("DELETE", r"/api/accessory-library/\d+/image", "accessory.image", "Removed a library accessory's picture"),
    ("DELETE", r"/api/accessory-library/\d+", "accessory.delete", "Deleted a library accessory"),
    ("POST", r"/api/npd/identity-code", "npd.identity_code", "Coded an identity value"),
    ("POST", r"/api/npd/codes/preview", None, None),
    ("POST", r"/api/npd/codes/attributes", "npd.code_attribute", "Added a product-code attribute"),
    ("PUT", r"/api/npd/codes/attributes/[^/]+", "npd.code_attribute", "Edited a product-code attribute"),
    ("DELETE", r"/api/npd/codes/attributes/[^/]+", "npd.code_attribute_delete", "Removed a product-code attribute"),
    ("PUT", r"/api/npd/codes/schemes", "npd.code_scheme", "Changed a product-code structure"),
    ("DELETE", r"/api/npd/codes/schemes/\d+", "npd.code_scheme_delete", "Put a typology on the brand default code"),
    ("POST", r"/api/npd/codes/schemes/\d+/restore", "npd.code_scheme_restore", "Restored a product-code structure"),
    ("POST", r"/api/npd/codes/abbreviations", "npd.code_abbreviation", "Set a product-code abbreviation"),
    # NPD (DuraGreen): each endpoint writes its own, more specific record (log_activity); these
    # name the action should one ever reach the catch-all.
    ("POST", r"/api/npd/duragreen/code", None, None),
    ("PUT", r"/api/npd/duragreen/products/\d+", "npd.duragreen.update", "Edited a DuraGreen product"),
    ("PUT", r"/api/npd/duragreen/products/\d+/status", "npd.duragreen.status", "Released or held a DuraGreen product"),
    ("POST", r"/api/npd/duragreen/products", "npd.duragreen.create", "Added a DuraGreen product"),
    ("POST", r"/api/npd/duragreen/products/\d+/codes", "npd.duragreen.code", "Added a DuraGreen order code"),
    ("DELETE", r"/api/npd/duragreen/codes/\d+", "npd.duragreen.code_delete", "Removed a DuraGreen order code"),
    ("POST", r"/api/npd/duragreen/products/\d+/parts", "npd.duragreen.part", "Set a DuraGreen code part"),
    ("DELETE", r"/api/npd/duragreen/parts/\d+", "npd.duragreen.part_delete", "Removed a DuraGreen code part"),
    # DuraGreen's naming structures (Nomenclature tab). The preview builds a code for a product not
    # saved yet and writes nothing.
    ("POST", r"/api/npd/duragreen/naming/preview", None, None),
    ("POST", r"/api/npd/duragreen/structures", "npd.duragreen.structure", "Made a DuraGreen naming structure"),
    ("PUT", r"/api/npd/duragreen/structures/\d+", "npd.duragreen.structure_edit", "Changed a DuraGreen naming structure"),
    ("DELETE", r"/api/npd/duragreen/structures/\d+", "npd.duragreen.structure_delete", "Removed a DuraGreen naming structure"),
    ("POST", r"/api/npd/duragreen/structures/entries", "npd.duragreen.structure_entry",
     "Added a code to a DuraGreen naming structure"),
    ("DELETE", r"/api/npd/duragreen/rules/\d+", "npd.duragreen.structure_entry_delete",
     "Removed a code from a DuraGreen naming structure"),
    ("PUT", r"/api/npd/configurator", "npd.configurator", "Set what the Configurator asks for a typology"),
    ("DELETE", r"/api/npd/configurator", "npd.configurator_reset", "Put a typology's Configurator back to automatic"),
    # Reading a page or a sheet changes nothing in the database; bringing products in does.
    ("POST", r"/api/npd/duragreen/import/preview(-pdf)?", None, None),
    ("POST", r"/api/npd/duragreen/import", "npd.duragreen.import", "Brought in DuraGreen products"),
    ("POST", r"/api/specsheets/\d+/suggest-description", None, None),
    ("POST", r"/api/specsheets/\d+/resolve-options", None, None),
    ("POST", r"/api/typologies/resolve-options", None, None),
    ("POST", r"/api/requirement-match(/.*)?", None, None),
    ("POST", r"/api/list-pricing/.*", "price.import", "Imported a price list"),
    ("PUT", r"/api/price-entries/\d+", "price.update", "Edited a price entry"),
    ("DELETE", r"/api/price-entries/\d+", "price.delete", "Deleted a price entry"),
    ("POST", r"/api/config-rules", "config.create", "Added a config rule"),
    ("PUT", r"/api/config-rules/\d+", "config.update", "Edited a config rule"),
    ("DELETE", r"/api/config-rules/\d+", "config.delete", "Deleted a config rule"),
    ("POST", r"/api/field-codes", "config.code", "Set a product code"),
    ("DELETE", r"/api/field-codes/\d+", "config.code_delete", "Removed a product code"),
    ("POST", r"/api/boqs", "boq.create", "Created a BOQ"),
    ("POST", r"/api/boqs/import", "boq.import", "Imported a BOQ from a file"),
    # Reading a file to show what it holds changes nothing, so it is not "changed something" -
    # and while an admin previews as someone else, it must not be answered as a refused write.
    ("POST", r"/api/boqs/import/preview", None, None),
    # A question changes nothing, but it sends the studio's records to the AI provider and uses
    # the day's shared allowance, so who asked is worth knowing (the question itself is not kept).
    ("POST", r"/api/ask", "ai.ask", "Asked a question on the Ask tab"),
    ("PUT", r"/api/boqs/\d+", "boq.update", "Edited BOQ details"),
    ("DELETE", r"/api/boqs/\d+", "boq.delete", "Deleted a BOQ"),
    ("POST", r"/api/boqs/\d+/duplicate", "boq.duplicate", "Duplicated a BOQ"),
    ("POST", r"/api/boqs/\d+/branch", "boq.branch", "Branched a BOQ"),
    ("POST", r"/api/boqs/\d+/to-detailed", "boq.to_detailed", "Made a detailed BOQ from a quick one"),
    ("POST", r"/api/boqs/\d+/areas", "boq.area_add", "Added an area to a quick BOQ"),
    ("PUT", r"/api/boqs/\d+/areas/\d+", "boq.area_update", "Renamed an area on a quick BOQ"),
    ("DELETE", r"/api/boqs/\d+/areas/\d+", "boq.area_delete", "Removed an area from a quick BOQ"),
    ("POST", r"/api/boqs/\d+/ref-images", "boq.image_add", "Added a reference picture"),
    ("PUT", r"/api/boqs/\d+/ref-images/\d+", "boq.image_update", "Re-labelled a reference picture"),
    ("DELETE", r"/api/boqs/\d+/ref-images/\d+", "boq.image_delete", "Removed a reference picture"),
    ("POST", r"/api/boqs/\d+/floors", "floor.create", "Added a floor"),
    ("PUT", r"/api/floors/\d+", "floor.update", "Edited a floor"),
    ("DELETE", r"/api/floors/\d+", "floor.delete", "Deleted a floor"),
    ("POST", r"/api/floors/\d+/rooms", "area.create", "Added an area"),
    ("POST", r"/api/floors/\d+/duplicate", "floor.duplicate", "Duplicated a floor"),
    ("POST", r"/api/rooms/\d+/transfer", "area.transfer", "Copied or moved an area"),
    ("PUT", r"/api/rooms/\d+", "area.update", "Edited an area"),
    ("DELETE", r"/api/rooms/\d+", "area.delete", "Deleted an area"),
    ("PUT", r"/api/boqs/\d+/structure", "boq.reorder", "Rearranged floors and areas"),
    ("POST", r"/api/rooms/\d+/items", "row.create", "Added a row"),
    ("PUT", r"/api/items/\d+", "row.update", "Edited a row"),
    ("POST", r"/api/items/\d+/move", "row.reorder", "Reordered a row"),
    ("POST", r"/api/items/\d+/duplicate", "row.duplicate", "Duplicated a row"),
    ("POST", r"/api/items/transfer", "row.transfer", "Copied or moved rows"),
    ("DELETE", r"/api/items/\d+", "row.delete", "Deleted a row"),
    ("POST", r"/api/npd/products", "npd.create", "Created an NPD product"),
    ("PUT", r"/api/npd/products/\d+", "npd.update", "Edited an NPD product"),
    ("PUT", r"/api/npd/products/\d+/status", "npd.status", "Approved or held an NPD product"),
    ("DELETE", r"/api/npd/products/\d+", "npd.delete", "Deleted an NPD product"),
    ("POST", r"/api/npd/parameters", "npd.parameter", "Added an NPD parameter"),
    ("DELETE", r"/api/npd/parameters/\d+", "npd.parameter_delete", "Removed an NPD parameter"),
    ("POST", r"/api/npd/parameters/\d+/move", "npd.parameter_move", "Reordered an NPD parameter"),
    ("POST", r"/api/npd/typologies", "npd.typology", "Added a typology"),
    ("POST", r"/api/npd/driver-prices", "price.driver_add", "Added a driver rate"),
    ("PUT", r"/api/npd/driver-prices/\d+", "price.driver", "Changed a driver rate"),
    ("DELETE", r"/api/npd/driver-prices/\d+", "price.driver_delete", "Removed a driver rate"),
    # Downloads change nothing, but a quotation carries a client's name and prices, and who
    # took a copy of it is worth knowing.
    ("GET", r"/api/boqs/\d+/export", "export.boq", "Downloaded a BOQ (.xlsx)"),
    ("GET", r"/api/boqs/\d+/export-pdf", "export.boq", "Downloaded a BOQ (.pdf)"),
    ("GET", r"/api/boqs/\d+/export-quotation", "export.quotation", "Downloaded a quotation (.xlsx)"),
    ("GET", r"/api/boqs/\d+/export-quotation-pdf", "export.quotation", "Downloaded a quotation (.pdf)"),
    ("GET", r"/api/boqs/\d+/export-quotation-areas", "export.quotation", "Downloaded a quotation by area (.xlsx)"),
    ("GET", r"/api/boqs/\d+/export-quotation-areas-pdf", "export.quotation", "Downloaded a quotation by area (.pdf)"),
    ("GET", r"/api/boqs/\d+/export-boq-summary(-pdf)?", "export.boq", "Downloaded a BOQ summary"),
    ("GET", r"/api/boqs/\d+/export-quotation-summary(-pdf)?", "export.quotation", "Downloaded a quotation summary"),
]
_ACTIVITY_ROUTES = [(m, re.compile("^" + pat + "/?$"), a, l) for m, pat, a, l in _ACTIVITY_ROUTES]


def _activity_route(method, path):
    for m, pattern, action, label in _ACTIVITY_ROUTES:
        if m == method and pattern.match(path):
            return action, label
    return ("other", "Changed something") if method in ("POST", "PUT", "PATCH", "DELETE") else (None, None)


def _boq_context(db, path):
    """(boq id, BOQ name, what else it names) for the thing a request is about - looked up
    before the change, since after a delete there is nothing left to look up."""
    m = re.match(r"^/api/(boqs|floors|rooms|items|specsheets)/(\d+)", path)
    if not m:
        return None, None, None
    kind, ident = m.group(1), int(m.group(2))
    try:
        if kind == "specsheets":
            sheet = db.get(models.Specsheet, ident)
            return None, None, (sheet.product_name if sheet else None)
        if kind == "items":
            item = db.get(models.BOQItem, ident)
            boq = item.room.floor.boq if item and item.room else None
            subject = (item.description or "").split("\n")[0][:60] if item else None
        elif kind == "rooms":
            room = db.get(models.BOQRoom, ident)
            boq = room.floor.boq if room else None
            subject = room.name if room else None
        elif kind == "floors":
            floor = db.get(models.BOQFloor, ident)
            boq = floor.boq if floor else None
            subject = floor.name if floor else None
        else:
            boq = db.get(models.BOQ, ident)
            subject = None
        if boq is None:
            return None, None, subject
        return boq.id, _boq_display_name(boq), subject
    except Exception:
        return None, None, None


def _boq_display_name(boq):
    name = boq.project_name or "Untitled Project"
    return "%s — %s" % (name, boq.branch_name) if getattr(boq, "branch_name", None) else name


def _user_email(db, user_id):
    user = db.get(models.User, user_id) if user_id else None
    return user.email if user else "unknown"


def log_activity(db, request, action, summary, boq=None, details=None, user_id=None):
    """Records one thing somebody did. Added to the caller's own session, so it commits with
    the change it describes - never one without the other - and marks the request so the
    catch-all below does not record it a second time."""
    uid = user_id if user_id is not None else request.session.get("user_id")
    db.add(models.ActivityLog(
        user_id=uid, user_email=_user_email(db, uid), action=action, summary=summary,
        boq_id=getattr(boq, "id", None) if boq is not None else None,
        boq_name=_boq_display_name(boq) if boq is not None else None,
        method=request.method, path=request.url.path,
        discipline=request.session.get("discipline"), details=details))
    request.state.activity_logged = True


class ActivityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        method, path = request.method, request.url.path
        uid = request.session.get("user_id") if "session" in request.scope else None
        action, label = (_activity_route(method, path) if path.startswith("/api/") and uid
                         else (None, None))
        if not label:
            return await call_next(request)

        db = SessionLocal()
        try:
            boq_id, boq_name, subject = _boq_context(db, path)
        finally:
            db.close()
        response = await call_next(request)
        if response.status_code >= 400 or getattr(request.state, "activity_logged", False):
            return response

        db = SessionLocal()
        try:
            # Adding something INTO a floor or area names where it went ("Added an area to
            # 'Ground'"); anything else names the thing itself ("Deleted a row: 'NYX-Mini'").
            # One dash for both read "Added an area — Ground", as if the area were Ground.
            if subject:
                into = method == "POST" and re.search(r"/(floors|rooms|items)/?$", path)
                summary = "%s %s '%s'" % (label, "to" if into else "·", subject)
            else:
                summary = label
            db.add(models.ActivityLog(
                user_id=uid, user_email=_user_email(db, uid), action=action,
                summary=summary,
                boq_id=boq_id, boq_name=boq_name, method=method, path=path,
                discipline=request.session.get("discipline")))
            db.commit()
        except Exception:
            db.rollback()          # a record that fails to write must never fail the change
        finally:
            db.close()
        return response


# Registration order matters: SessionMiddleware must be added AFTER AuthGuardMiddleware so
# that it ends up as the OUTER layer and runs first on every request (Starlette runs
# middleware in reverse-of-registration order) - otherwise AuthGuardMiddleware would read
# request.session before SessionMiddleware has parsed the cookie into it. Verified this
# ordering empirically (a probe middleware + TestClient) before relying on it here.
# Added first so it runs innermost - inside the auth guard - so a request the guard refuses
# is never recorded as something that happened.
app.add_middleware(ActivityMiddleware)
app.add_middleware(AuthGuardMiddleware)
# https_only=True in production (2026-08-14 security review) - without it, the session
# cookie has no Secure flag, so a browser would still hand it over on a plain http://
# request if one ever happened (a stray link, a misconfigured proxy, Caddy's own redirect
# briefly not applying) instead of refusing to send it outside HTTPS. Caddy already forces
# an HTTP->HTTPS redirect in front of this app, so this is defense-in-depth, not the only
# thing standing between the cookie and a plaintext connection - but for a session that can
# reach every client name and price in the database, that redundancy is the point. Gated on
# ENVIRONMENT (same flag SESSION_SECRET's own production check already uses) rather than
# always-on, since local dev runs over plain http://127.0.0.1 and a Secure cookie there
# would just silently never get sent back, breaking login with no obvious error.
app.add_middleware(
    SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", max_age=60 * 60 * 24 * 14,
    https_only=os.environ.get("ENVIRONMENT") == "production",
)


class ImmutableWithoutVaryCookie:
    """Takes `Cookie` out of the Vary header of an immutable response.

    SessionMiddleware marks every response whose request read the session `Vary: Cookie`, and the
    auth guard reads it on every request - product pictures included, whose URLs carry a version
    and are cached for a year. Vary tells a browser to reuse its copy only while the Cookie header
    is exactly the same, so the moment the session cookie is signed again every cached picture is
    fetched again (and app.js with them: the activity middleware reads the session too). An
    immutable URL is the same file whoever asks - a versioned picture, `private` and still checked
    by the guard on every request that reaches the server, or app.js and the stylesheet, public
    anyway - so for these the cookie decides nothing.

    Pure ASGI rather than BaseHTTPMiddleware: it only edits headers on their way out, and must
    wrap SessionMiddleware (added after it, so it runs outside it) to see the header at all."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                vary = headers.get("vary")
                if vary and "immutable" in headers.get("cache-control", ""):
                    rest = [v.strip() for v in vary.split(",") if v.strip() and v.strip().lower() != "cookie"]
                    if rest:
                        headers["vary"] = ", ".join(rest)
                    else:
                        del headers["vary"]
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(ImmutableWithoutVaryCookie)


# Config Rules and Product Codes affect every BOQ built afterwards. The guard already
# requires "config.edit" for their changes; this says so again on the endpoints themselves.
require_config_edit = require_permission("config.edit")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """The tab icon for a browser that asks by the conventional name (live logged 19 404s for it
    in two weeks). A day's cache: the pages themselves name the versioned file."""
    return FileResponse(BASE_DIR / "app" / "assets" / "favicon.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if PROTOTYPE_NO_AUTH or request.session.get("user_id"):
        return RedirectResponse(url="/")
    return templates.TemplateResponse(request, "login.html", {"error": error, "google_configured": GOOGLE_CONFIGURED,
                                                              "asset_v": _asset_version()})


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    # Only @example.com accounts may use the app. Once Google Sign-In is configured
    # (GOOGLE_CLIENT_ID/SECRET set - see GoogleAuthSetup.md) this path signs nobody in: a
    # company address must use Google, and there is no one else it could be for. It stays as a
    # route, not deleted, because until Google is configured (a fresh local copy, say) it is
    # the only way in at all - see login.html for the matching fallback form.
    address = email.strip().lower()
    # Refused before any password is looked at, so a non-company address learns nothing about
    # whether an account exists, and cannot be used to guess at one.
    if not _is_company_email(address):
        db.add(models.ActivityLog(
            user_email=address or "blank", action="auth.failed",
            summary="Refused sign-in - not a company address", method="POST", path="/login"))
        db.commit()
        return RedirectResponse(url="/login?error=company_only", status_code=303)
    # With Google Sign-In on, a company address must use it - that is what keeps a company
    # account with an old password from getting round Google's own checks.
    if GOOGLE_CONFIGURED:
        return RedirectResponse(url="/login?error=google_required", status_code=303)

    # Guessing is slowed down: five wrong passwords for one address in fifteen minutes and
    # that address is refused until the fifteen minutes have passed, right password or not.
    # Counted from the activity log, which records every failed attempt already.
    since = datetime.datetime.utcnow() - datetime.timedelta(minutes=15)
    recent_failures = (db.query(models.ActivityLog)
                       .filter(models.ActivityLog.action == "auth.failed",
                               models.ActivityLog.user_email == (address or "blank"),
                               models.ActivityLog.at >= since).count())
    if recent_failures >= 5:
        return RedirectResponse(url="/login?error=locked", status_code=303)

    user = db.query(models.User).filter(models.User.email == address).first()
    if not user or not verify_password(password, user.password_hash):
        # A failed attempt is recorded against the address that was typed, account or not -
        # a run of these against one address is what a guessing attempt looks like.
        db.add(models.ActivityLog(
            user_id=user.id if user else None, user_email=email.strip().lower() or "blank",
            action="auth.failed", summary="Failed sign-in (password)",
            method="POST", path="/login"))
        db.commit()
        return RedirectResponse(url="/login?error=1", status_code=303)
    if user.role == NO_ACCESS:
        db.add(models.ActivityLog(user_id=user.id, user_email=user.email, action="auth.failed",
                                  summary="Refused sign-in - access switched off",
                                  method="POST", path="/login"))
        db.commit()
        return RedirectResponse(url="/login?error=disabled", status_code=303)
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    log_activity(db, request, "auth.signin", "Signed in (password)", user_id=user.id)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/auth/google/login")
async def google_login(request: Request):
    if not GOOGLE_CONFIGURED:
        raise HTTPException(status_code=404, detail="Google Sign-In is not configured - see GoogleAuthSetup.md")
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    if not GOOGLE_CONFIGURED:
        raise HTTPException(status_code=404, detail="Google Sign-In is not configured - see GoogleAuthSetup.md")
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        # Covers the ordinary ways this can fail (user clicked "Cancel" on Google's consent
        # screen, the state/nonce round-trip didn't match, the exchange itself errored) -
        # none of these are a bug in this app, so send the user back to a normal login
        # attempt rather than a raw 500.
        return RedirectResponse(url="/login?error=google_failed", status_code=303)
    claims = token.get("userinfo") or {}
    if not is_allowed_email(claims):
        return RedirectResponse(url="/login?error=google_domain", status_code=303)
    email = claims["email"].strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        # First-ever sign-in for this @example.com address - auto-provisioned as "member"
        # (see TUTORIAL.md's 2026-08-06 Google Sign-In entry for why: the whole point of
        # switching to Google Sign-In was letting anyone with a real company account in
        # without an admin having to provision each person by hand first). `password_hash`
        # stays "" - this account only ever authenticates via Google from here on; empty
        # string rather than NULL so no schema change was needed, and verify_password()
        # already safely rejects an empty stored hash rather than crashing on it.
        user = models.User(email=email, password_hash="", role="member")
        db.add(user)
        db.flush()
        # What an admin has set new people to start with (People tab), granted as ordinary
        # rows - so from the first click, what the People tab shows is what they can do.
        for key in _new_person_permissions(db):
            db.add(models.UserPermission(user_id=user.id, permission=key,
                                         granted_by="new-account default"))
        db.commit()
        db.refresh(user)
    if user.role == NO_ACCESS:
        db.add(models.ActivityLog(user_id=user.id, user_email=user.email, action="auth.failed",
                                  summary="Refused sign-in - access switched off",
                                  method="GET", path="/auth/google/callback"))
        db.commit()
        return RedirectResponse(url="/login?error=disabled", status_code=303)
    request.session["user_id"] = user.id
    request.session["role"] = user.role
    log_activity(db, request, "auth.signin", "Signed in (Google)", user_id=user.id)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---------- the activity log, for an admin ----------

# The groups the Activity screen offers as filters, keyed by the start of an action key.
_ACTIVITY_GROUPS = {
    "boq_created": ("boq.create", "boq.duplicate", "boq.branch", "boq.import"),
    "deletions": ("boq.delete", "specsheet.delete", "floor.delete", "area.delete", "row.delete",
                  "price.delete", "price.driver_delete", "config.delete", "npd.parameter_delete",
                  "npd.delete"),
    "boq_edits": ("boq.update", "floor.", "area.", "row."),
    "catalogue": ("specsheet.", "npd.", "price.", "config."),
    "exports": ("export.",),
    "signins": ("auth.",),
}


@app.get("/api/activity")
def list_activity(request: Request, group: str = "", user: str = "", q: str = "",
                  before: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """The log, newest first, a page at a time. Needs "activity.view" (see _required_permission)."""
    query = db.query(models.ActivityLog)
    prefixes = _ACTIVITY_GROUPS.get(group)
    if prefixes:
        query = query.filter(or_(*[models.ActivityLog.action.startswith(p) for p in prefixes]))
    if user:
        query = query.filter(models.ActivityLog.user_email == user)
    if q:
        like = "%" + q.strip() + "%"
        query = query.filter(or_(models.ActivityLog.summary.ilike(like),
                                 models.ActivityLog.boq_name.ilike(like),
                                 models.ActivityLog.user_email.ilike(like)))
    if before:
        query = query.filter(models.ActivityLog.id < before)
    limit = max(1, min(limit, 500))
    rows = query.order_by(models.ActivityLog.id.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"rows": [{
        "id": r.id, "at": r.at.isoformat() if r.at else None, "user": r.user_email,
        "action": r.action, "summary": r.summary, "boq_id": r.boq_id, "boq_name": r.boq_name,
        "details": r.details or {},
    } for r in rows], "more": more, "next_before": rows[-1].id if more and rows else None}


@app.get("/api/activity/summary")
def activity_summary(db: Session = Depends(get_db)):
    """Who created which BOQ - the question the log exists to answer first - plus the people
    and the date recording began, for the screen's filters and its caveat."""
    created = (db.query(models.ActivityLog)
               .filter(models.ActivityLog.action == "boq.create")
               .order_by(models.ActivityLog.id.desc()).all())
    by_person = {}
    for r in created:
        entry = by_person.setdefault(r.user_email, {"user": r.user_email, "count": 0, "last": None})
        entry["count"] += 1
        if entry["last"] is None:
            entry["last"] = r.at.isoformat() if r.at else None
    first = db.query(func.min(models.ActivityLog.at)).scalar()
    people = [e for (e,) in db.query(models.ActivityLog.user_email).distinct().all() if e]
    return {
        "recording_since": first.isoformat() if first else None,
        "boqs_created": sorted(by_person.values(), key=lambda e: (-e["count"], e["user"])),
        "recent_boqs": [{"at": r.at.isoformat() if r.at else None, "user": r.user_email,
                         "boq_id": r.boq_id, "boq_name": r.boq_name} for r in created[:10]],
        "people": sorted(people),
        "total": db.query(models.ActivityLog).count(),
    }


@app.get("/api/me")
def get_me(request: Request, db: Session = Depends(get_db)):
    real, effective, previewing = _identity(request)
    user = db.get(models.User, effective)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    out = {"email": user.email, "role": user.role, "is_admin": user.role == "admin",
           "permissions": sorted(_access(effective)[1])}
    if previewing:
        out["preview_by"] = _email_of(real)
    return out


# ---------- people, and previewing the app as one of them ----------

COMPANY_DOMAIN = "example.com"


def _is_company_email(email):
    """Whether an address is one of the company's. The one rule for who may use the app at
    all - sign-in, the per-request guard and adding a person all ask this, so they cannot
    drift apart."""
    return (email or "").strip().lower().endswith("@" + COMPANY_DOMAIN)


# The role an admin sets for someone who should keep their account but not be able to use the
# app - someone who has left, say. Everything they could do and everything they did is kept,
# so switching them back on is one click.
NO_ACCESS = "disabled"
NEW_PERSON_SETTING = "new_person_permissions"


def _tools_of(user):
    return [d for d in (user.disciplines or []) if d in models.DISCIPLINES] \
        or [models.DEFAULT_DISCIPLINE]


def _new_person_permissions(db):
    """What a person may do the first time they sign in - set by an admin on the People tab.
    Until an admin has set it, what the app has always given a new account."""
    row = db.get(models.AppSetting, NEW_PERSON_SETTING)
    if row and isinstance(row.value, list):
        return [k for k in row.value if k in PERMISSION_KEYS]
    return list(DEFAULT_NEW_PERMISSIONS)


def _grants_of(db, user_id):
    now_ = datetime.datetime.utcnow()
    return {g.permission: g for g in db.query(models.UserPermission)
            .filter(models.UserPermission.user_id == user_id).all()
            if g.expires_at is None or g.expires_at > now_}


# A permission given "until" a date lasts to the end of that day in India, where the studio
# works - so "until 30 Sept" still works late on the 30th and stops at midnight. Stored in UTC,
# like every other time here; _access and _grants_of ignore a grant once its end has passed.
IST = datetime.timedelta(hours=5, minutes=30)


def _today_ist():
    return (datetime.datetime.utcnow() + IST).date()


def _end_of_day(day):
    return datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time()) - IST


def _last_day(expires_at):
    return (expires_at + IST - datetime.timedelta(days=1)).date()


def _day_words(day):
    return "%d %s %d" % (day.day, day.strftime("%b"), day.year)


def _read_until(payload, keys):
    """The end dates in a save, as {permission: stored end}. None when the save says nothing
    about end dates, so a request that leaves them out never clears the ones already set."""
    raw = payload.get("until")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="End dates must be given per permission")
    ends = {}
    for key in keys:
        value = raw.get(key)
        if not value:
            continue
        try:
            day = datetime.date.fromisoformat(str(value)[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="\u201c%s\u201d has an end date that "
                                "isn\u2019t a date" % _permission_name(key))
        if day < _today_ist():
            raise HTTPException(status_code=400, detail="The end date for \u201c%s\u201d has "
                                "already passed - pick today or later, or leave it empty for no "
                                "end date." % _permission_name(key))
        ends[key] = _end_of_day(day)
    return ends


def _admin_count(db):
    return db.query(models.User).filter(models.User.role == "admin").count()


def _person_row(db, u, last, made):
    grants = _grants_of(db, u.id)
    return {
        "id": u.id, "email": u.email, "role": u.role or "member",
        "tools": _tools_of(u),
        "signed_off": u.role == NO_ACCESS,
        "permissions": PERMISSION_KEYS if u.role == "admin" else
        [k for k in PERMISSION_KEYS if k in grants],
        "until": {} if u.role == "admin" else
        {k: _last_day(g.expires_at).isoformat() for k, g in grants.items() if g.expires_at},
        "signs_in_with": "password" if u.password_hash else "Google",
        "created": u.created_at.isoformat() if u.created_at else None,
        "last_active": last[u.email].isoformat() if last.get(u.email) else None,
        "boqs_created": made.get(u.email, 0),
    }


@app.get("/api/admin/people")
def admin_people(db: Session = Depends(get_db)):
    """Every account and exactly what it can do, plus the list of permissions an admin can give
    - the whole People tab in one request."""
    last = dict(db.query(models.ActivityLog.user_email, func.max(models.ActivityLog.at))
                .group_by(models.ActivityLog.user_email).all())
    made = dict(db.query(models.ActivityLog.user_email, func.count(models.ActivityLog.id))
                .filter(models.ActivityLog.action == "boq.create")
                .group_by(models.ActivityLog.user_email).all())
    people = [_person_row(db, u, last, made)
              for u in db.query(models.User).order_by(models.User.email).all()]
    return {
        "people": people,
        "permissions": [{"key": k, "group": g, "name": n, "description": d}
                        for k, g, n, d, _ in PERMISSIONS],
        "tools": list(models.DISCIPLINES),
        "new_person_permissions": _new_person_permissions(db),
        "company_domain": COMPANY_DOMAIN,
    }


@app.put("/api/admin/people/{person_id}")
def admin_update_person(person_id: int, request: Request, payload: dict = Body(...),
                        db: Session = Depends(get_db)):
    """Change what one person can do - their role, their tools, their permissions. Every
    change is recorded in the activity log, one line each, under the admin who made it."""
    person = db.get(models.User, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="No such person")
    me = request.session.get("user_id")
    changes = []

    role = payload.get("role", person.role or "member")
    if role not in ("admin", "member", NO_ACCESS):
        raise HTTPException(status_code=400, detail="Role must be admin, member or no access")
    if role == NO_ACCESS and person_id == me:
        raise HTTPException(status_code=400, detail="You can\u2019t switch off your own access - "
                            "there would be nobody left in this tab to switch it back on.")
    if role != (person.role or "member"):
        # The app always needs someone who can manage it; losing the last admin would leave
        # nobody able to give anyone access again, short of editing the database.
        if person.role == "admin" and _admin_count(db) <= 1:
            raise HTTPException(status_code=400, detail="This is the only admin. Make someone "
                                "else an admin first, so the app is never left without one.")
        was_off = person.role == NO_ACCESS
        person.role = role
        if role == NO_ACCESS:
            changes.append("Switched off %s\u2019s access - they can no longer sign in" % person.email)
        else:
            changes.append("Switched %s’s access back on, as %s" % (
                person.email, "an admin" if role == "admin" else "a member") if was_off
                else "Made %s %s" % (
                    person.email, "an admin" if role == "admin" else "a member"))

    tools = payload.get("tools")
    if tools is not None:
        tools = [t for t in tools if t in models.DISCIPLINES]
        if not tools:
            raise HTTPException(status_code=400, detail="Give them at least one tool")
        if tools != _tools_of(person):
            person.disciplines = tools
            changes.append("Changed the tools %s can open to %s"
                           % (person.email, ", ".join(t.capitalize() for t in tools)))

    # An admin can do everything and a switched-off account nothing, so in both cases what is
    # ticked is left exactly as it was - ready for whenever they are a member again.
    wanted = payload.get("permissions")
    if wanted is not None and role == "member":
        wanted = [k for k in wanted if k in PERMISSION_KEYS]
        until = _read_until(payload, wanted)
        held = _grants_of(db, person.id)
        admin_email = _email_of(me)
        for key in wanted:
            ends = until.get(key) if until is not None else None
            name = _permission_name(key)
            if key not in held:
                # Anything left for this permission has run out; clear it so each permission
                # is one row, and the row says what holds now.
                (db.query(models.UserPermission)
                 .filter(models.UserPermission.user_id == person.id,
                         models.UserPermission.permission == key)
                 .delete(synchronize_session=False))
                db.add(models.UserPermission(user_id=person.id, permission=key,
                                             granted_by=admin_email, expires_at=ends))
                changes.append("Gave %s \u201c%s\u201d%s" % (
                    person.email, name, " until " + _day_words(_last_day(ends)) if ends else ""))
            elif until is not None and held[key].expires_at != ends:
                held[key].expires_at = ends
                held[key].granted_by = admin_email
                changes.append("%s now has \u201c%s\u201d %s" % (
                    person.email, name,
                    "until " + _day_words(_last_day(ends)) if ends else "with no end date"))
        for key, grant in held.items():
            if key not in wanted:
                db.delete(grant)
                changes.append("Took \u201c%s\u201d away from %s"
                               % (_permission_name(key), person.email))

    for line in changes:
        log_activity(db, request, "people.access", line,
                     details={"person": person.email}, user_id=me)
    db.commit()
    return {"ok": True, "changes": changes}


@app.post("/api/admin/people")
def admin_add_person(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Add someone before they first sign in - with their access already set. Only a company
    address can be added, and it signs in with Google as always."""
    email = (payload.get("email") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=400, detail="That doesn\u2019t look like an email address")
    if not _is_company_email(email):
        raise HTTPException(status_code=400, detail="Only @%s addresses can use the app."
                            % COMPANY_DOMAIN)
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="%s already has an account" % email)
    role = "admin" if payload.get("role") == "admin" else "member"
    tools = [t for t in (payload.get("tools") or []) if t in models.DISCIPLINES] \
        or [models.DEFAULT_DISCIPLINE]
    person = models.User(email=email, role=role, disciplines=tools, password_hash="")
    db.add(person)
    db.flush()
    perms = [k for k in (payload.get("permissions") or []) if k in PERMISSION_KEYS]
    until = _read_until(payload, perms) or {}
    me = request.session.get("user_id")
    for key in ([] if role == "admin" else perms):
        db.add(models.UserPermission(user_id=person.id, permission=key, granted_by=_email_of(me),
                                     expires_at=until.get(key)))
    given = "" if role == "admin" else (" with " + ", ".join(
        "“%s”%s" % (_permission_name(k), " until " + _day_words(_last_day(until[k]))
                              if k in until else "") for k in perms) if perms else ", view only")
    log_activity(db, request, "people.add",
                 "Added %s as %s%s" % (email, "an admin" if role == "admin" else "a member", given),
                 details={"person": email, "permissions": perms, "tools": tools}, user_id=me)
    db.commit()
    return {"ok": True, "id": person.id, "signs_in_with": "Google"}


@app.delete("/api/admin/people/{person_id}")
def admin_remove_person(person_id: int, request: Request, db: Session = Depends(get_db)):
    """Take someone out of the app. Their past activity stays in the log under their email -
    the log never loses who did something. A company address can still sign in with Google
    again afterwards, and would then start with what new people start with."""
    person = db.get(models.User, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="No such person")
    me = request.session.get("user_id")
    if person.id == me:
        raise HTTPException(status_code=400, detail="You can\u2019t remove your own account")
    if person.role == "admin" and _admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="This is the only admin")
    email = person.email
    db.query(models.UserPermission).filter(models.UserPermission.user_id == person.id).delete()
    db.delete(person)
    log_activity(db, request, "people.remove", "Removed %s from the app" % email,
                 details={"person": email}, user_id=me)
    db.commit()
    return {"ok": True}


@app.put("/api/admin/settings/new-person-access")
def admin_set_new_person_access(request: Request, payload: dict = Body(...),
                                db: Session = Depends(get_db)):
    """What someone can do the first time they sign in, before an admin has looked at them."""
    perms = [k for k in (payload.get("permissions") or []) if k in PERMISSION_KEYS]
    me = request.session.get("user_id")
    row = db.get(models.AppSetting, NEW_PERSON_SETTING)
    if row is None:
        row = models.AppSetting(key=NEW_PERSON_SETTING)
        db.add(row)
    row.value = perms
    row.updated_by = _email_of(me)
    log_activity(db, request, "people.defaults",
                 "Changed what new people start with to: %s"
                 % (", ".join(_permission_name(k) for k in perms) or "nothing - view only"),
                 details={"permissions": perms}, user_id=me)
    db.commit()
    return {"ok": True}


@app.post("/api/admin/preview")
def start_preview(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Show the app as another account until stopped. Recorded in the log, under the admin."""
    real = request.session.get("user_id")
    target = db.get(models.User, payload.get("user_id"))
    if not target:
        raise HTTPException(status_code=404, detail="No such account")
    if target.id == real:
        raise HTTPException(status_code=400, detail="That is your own account")
    if target.role == NO_ACCESS:
        raise HTTPException(status_code=400, detail="%s cannot sign in - their access is "
                            "switched off, so there is nothing to see as them." % target.email)
    request.session["preview_as"] = target.id
    # A preview opens in a tool the previewed person can reach, or the page would refuse it.
    tools = [d for d in (target.disciplines or []) if d in models.DISCIPLINES] \
        or [models.DEFAULT_DISCIPLINE]
    if request.session.get("discipline") not in tools:
        request.session["discipline"] = tools[0]
    log_activity(db, request, "admin.preview", "Started previewing the app as %s" % target.email,
                 details={"previewed": target.email, "role": target.role}, user_id=real)
    db.commit()
    return {"ok": True, "email": target.email, "go": "/" + request.session["discipline"]}


@app.post("/api/admin/preview/stop")
def stop_preview(request: Request, db: Session = Depends(get_db)):
    target = request.session.pop("preview_as", None)
    if target:
        log_activity(db, request, "admin.preview_end",
                     "Stopped previewing as %s" % (_email_of(target) or "an account"),
                     user_id=request.session.get("user_id"))
        db.commit()
    return {"ok": True}


# ---------- serialization helpers ----------

def _image_url(path: str) -> str:
    return f"/media/images/{Path(path).name}"


# The only file types a person may upload as a picture. A saved picture is served back with a
# type read from its extension, so this list is also the list of what a browser will be asked to
# open. An .svg is a document that can carry script, and so is an .html, so neither is accepted,
# whatever the file picker offered. NPD's pictures took any extension until 2026-09-24.
PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _picture_suffix(filename, default=""):
    """The upload's extension, lower-cased - or a 400 if it is not a picture type."""
    suffix = Path(filename or "").suffix.lower() or default
    if suffix not in PICTURE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Pictures only - PNG, JPG, GIF or WebP.")
    return suffix


# Every column specsheet_summary() actually reads - used by list_specsheets() to fetch
# only these instead of every column (notably skipping raw_text/params/options, which can
# each be several KB of parsed PDF text per row and are never touched by this function).
# Keep in sync with specsheet_summary()'s body below if that function starts reading a
# new column - a column missing from this list just triggers SQLAlchemy's normal lazy-load
# fallback (a correctness safety net, not silent wrong data), so this list only needs to be
# complete enough to make list_specsheets() fast, not perfectly exhaustive.
# Every column specsheet_summary() touches must be listed here. A column it reads but this
# omits is not simply slower - `load_only` defers it, so reading it lazy-loads that one column
# with its own round trip, once per row. Adding `source_url` to the summary without adding it
# here turned one query into 1213 and took the catalogue listing from 130ms to 1.9s.
SPECSHEET_SUMMARY_COLUMNS = [
    "id", "filename", "category", "typology", "series", "family", "variant", "environment",
    "page_number", "product_name", "make", "image_path", "source_pdf_path", "source_url",
    "diagrams", "fields", "list_price", "list_price_by_wattage",
    # Pricing moved into NPD, so what a product costs is now a fact about the product rather
    # than something looked up elsewhere - and the Library is where people go to see what the
    # catalogue holds. Three small columns, so that screen can say which products are priced
    # without fetching every row in full.
    "origin", "base_price", "price_adders",
    # Whether a line's product was deleted from NPD (item_dict's product_deleted) - read for every
    # line of a BOQ, so it must be loaded with the rest, or each product costs its own query.
    "approval_status",
]


# How long a picture's version token may be reused without re-checking the file on disk.
# The token exists so an immutably-cached image URL still changes when the photograph behind
# it is replaced; caching the token for a few seconds bounds how long a replacement can go
# unnoticed, which is a different thing entirely from the year-long browser cache the token
# protects against. The catalogue endpoint stats one file per row, so on a 1,193-row library
# this is the difference between ~108ms of syscalls per request and none.
_IMAGE_VERSION_TTL = 30.0
_image_version_cache: dict[str, tuple[float, str]] = {}


def _image_version(path) -> str:
    """A short token that changes when the file behind a row's picture changes. Lets the
    image URL be cached forever and still update within _IMAGE_VERSION_TTL of the photograph
    being replaced - the same trick index() already uses for app.js."""
    key = str(path)
    now = time.monotonic()
    hit = _image_version_cache.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    try:
        version = f"{int(os.stat(path).st_mtime)}"
    except OSError:
        version = ""
    _image_version_cache[key] = (now + _IMAGE_VERSION_TTL, version)
    return version


def image_urls(s) -> tuple:
    """(full-size url, thumbnail url) for a specsheet, or (None, None) if it has no picture.
    Both carry the version token; the thumbnail is what every grid and list should use."""
    if not s.image_path:
        return None, None
    v = _image_version(s.image_path)
    base = f"/api/specsheets/{s.id}/image"
    q = f"?v={v}" if v else ""
    # The RECIPE belongs in the URL, not only in the on-disk cache key. Telling a browser a
    # picture is immutable for a year means it will never ask about that URL again - so when
    # the way thumbnails are made changes, the URL has to change too or every browser that
    # already loaded the page keeps its old copy forever. That is exactly what happened when
    # thumbnails briefly rendered transparent backgrounds as black: the file on the server
    # was corrected within the hour, and browsers went on showing black tiles because the
    # URL still said the same thing. A version for the source file, a version for the recipe.
    thumb = f"{base}?w=400&v={v}&r={_THUMB_RECIPE}" if v else f"{base}?w=400&r={_THUMB_RECIPE}"
    return f"{base}{q}", thumb


def _option_words(s) -> str:
    said = " ".join(str(v) for v in (s.fields or {}).values() if v).lower()
    words = {str(v) for vals in (s.options or {}).values() if isinstance(vals, list) for v in vals if v}
    return " ".join(sorted(w for w in words if w.lower() not in said))


def specsheet_summary(s: models.Specsheet) -> dict:
    diagrams = s.diagrams or []
    pdf_url = f"/api/specsheets/{s.id}/pdf" if s.source_pdf_path else None
    fields = s.fields or {}
    # Once, not once per URL. image_urls() stats the picture on disk to build its version
    # token, and calling it twice here meant two filesystem syscalls per row - 2,386 of them
    # on a 1,193-row catalogue, which measured 215ms of this endpoint's 254ms.
    image_url, thumb_url = image_urls(s)
    return {
        "id": s.id,
        "filename": s.filename,
        "category": s.category,
        "typology": s.typology or "",
        # Configure offers a product under its typology and the kind its maker files it under.
        "typology_key": typology_key(s.typology or ""),
        "also_kind": category_kind(s.category),
        "series": s.series or "",
        "family": s.family or "",
        "variant": s.variant or "",
        "environment": s.environment or [],
        "page_number": s.page_number or 1,
        "product_name": s.product_name,
        "make": s.make,
        "has_image": bool(s.image_path),
        "image_url": image_url,
        # What every grid and list should render: the same picture at 400px instead of the
        # manufacturer's 2000px original. See _thumbnail().
        "thumb_url": thumb_url,
        "diagram_count": len(diagrams),
        # What this product costs, the way NPD holds it: a base price plus an adder per
        # option. `adder_count` rather than the adders themselves - the Library only needs to
        # say whether a price exists, and the full table is several KB across 1,300 rows.
        "origin": s.origin or "parsed",
        "base_price": s.base_price,
        "adder_count": sum(len(v) for v in (s.price_adders or {}).values()
                           if isinstance(v, dict)),
        "pdf_url": pdf_url,
        # The manufacturer's own page for this one product - the thing to check a BOQ line
        # against when the PDF on file is a catalogue page shared by several products.
        "source_url": s.source_url or "",
        # jumps a multi-page catalog PDF straight to this product's own page when opened
        "pdf_page_url": f"{pdf_url}#page={s.page_number or 1}" if pdf_url else None,
        # surfaced on the Library card so entries that share a name/series/family/variant -
        # different revisions/optics/accessory options of "the same" nominal product, not true
        # duplicates - are still visually distinguishable at a glance, without an extra per-card
        # request. The full dict, not a curated subset: which field actually differs between two
        # same-named specsheets varies by typology (a downlight's siblings usually differ by
        # wattage/finish/IP; a strip light's usually differ by accessories/beam_angle/profile
        # instead) - the Library grid diffs this per sibling group client-side rather than this
        # endpoint guessing one fixed set of "the" distinguishing fields for every product type.
        "wattage": fields.get("wattage", ""),
        "finish": fields.get("finish", ""),
        "ip_rating": fields.get("ip_rating", ""),
        "fields": fields,
        "list_price": s.list_price,  # None = no verified price for this exact row yet - see models.py
        "list_price_by_wattage": s.list_price_by_wattage,  # {"9W": 1000.0, ...} or None - see models.py
    }


# Parsed detail that describes the product rather than offering a choice - `accessories`
# has its own picker in "Add Product", the rest are classification/reference text.
#
# `driver_power` is the driver's wattage, which NPD records under POWER SUPPLY PARAMETERS. It is
# not a choice about the luminaire: the driver is a row of its own, added with Power Supply or
# Driver under the product, and asking its wattage on the luminaire's row put a "Driver Power"
# line in the fixture's description that belongs on the driver's own row (removed 2026-09-29).
#
# One list, kept in app/configurator.py (NOT_A_CHOICE): the Configurator NPD decides reads the
# same answer to "is this ever asked as a choice?".
_NON_CONFIG_FIELDS = configurator.NOT_A_CHOICE


# A luminaire's rated life is a consequence of the configuration, not an input to it -
# L80/L90/L95 are three reporting thresholds for the same lamp, so offering them as three
# dropdowns invites a row that claims mutually contradictory ones. They stay visible as
# parsed detail; they are not asked.
_DERIVED_NOT_CHOSEN = re.compile(r"^lifetime(_|$)")

_PUNCTUATION_ONLY = re.compile(r"^[\s+\-–—*/|.,;:]*$")


def _extra_field_label(key: str) -> str:
    """A make-specific option group keeps its own name; the key is a slug of it."""
    return key.replace("_", " ").title()


def _is_real_choice(key: str, values: list) -> bool:
    """Whether an option group has earned a dropdown.

    Scraping a manufacturer's own configurator brings across its shop UI as well as its
    product: Exenia's composite builder has steps like "Order separately" and "Select to
    activate the diffuser" whose only option is "+", which is a button in their checkout and
    means nothing here - a dropdown offering "+" is a question with no answer. Others carry a
    single value that just restates the step's own name ("Mounting system": "Mounting
    system").

    So a group is a choice only when it offers two or more real, distinct values. Anything
    else is still parsed detail and still shown in the Details view; it is simply not asked
    as a question.
    """
    clean = [v for v in values if v and not _PUNCTUATION_ONLY.match(v)]
    if len(clean) < 2 or _DERIVED_NOT_CHOSEN.match(key):
        return False
    label = _extra_field_label(key).lower()
    return not all(v.strip().lower() == label for v in clean)


def _line_diagram_url(s: models.Specsheet):
    """URL of this product's dimensioned line drawing, or None. Which diagram label carries
    one is excel_export's business (LINE_DIAGRAM_LABEL) - resolved here so neither app.js nor
    two different serializers hold their own copy of that string."""
    for dg in (s.diagrams or []):
        if str(dg.get("label") or "").strip().lower() == LINE_DIAGRAM_LABEL.lower():
            return _image_url(dg["path"]) if Path(dg["path"]).exists() else None
    return None


def _dimension_choices(s) -> list:
    """The pictures a row may put in its Dimension column, in the order they are offered: the
    manufacturer's own line drawing first, then the profiles, then the accessory pictures
    extracted from the specsheet (pdf_parser.extract_profile_pictures). Only files that exist.

    Each is {"label", "name", "kind", "url"}. `label` is what a row stores in
    BOQItem.dimension_image - blank for the line drawing, because blank is what that choice has
    always meant - and `name` is what the screen shows.
    """
    if s is None:
        return []
    line, extracted = [], []
    for dg in (s.diagrams or []):
        path = dg.get("path")
        if not path or not Path(path).exists():
            continue
        label = str(dg.get("label") or "")
        if label.strip().lower() == LINE_DIAGRAM_LABEL.lower():
            line.insert(0, {"label": "", "name": "Line drawing", "kind": "line", "url": _image_url(path)})
        elif s.origin == "npd" and npd.dimension_order(label) is not None \
                and npd.dimension_order(label)[0] == 1 and not dg.get("kind"):
            # a further dimension drawing of an NPD product (18 Nov 2026), chosen by its own label -
            # not every picture a manufacturer's PDF happens to carry
            line.append({"label": label, "name": "Dimension drawing %d" % npd.dimension_order(label)[1],
                         "kind": "line", "url": _image_url(path)})
        elif dg.get("kind") in ("profile", "accessory"):
            extracted.append({"label": label, "name": label, "kind": dg["kind"], "url": _image_url(path)})
    rank = {"profile": 0, "accessory": 1}
    return line + sorted(extracted, key=lambda c: rank[c["kind"]])


# What an accessory line stores in BOQItem.dimension_image for its picture. It is the line's only
# choice, so nothing reads the value but the picker: excel_export.line_diagram_for takes an
# accessory line's picture from the file named in its config.
ACCESSORY_PICTURE_LABEL = "Accessory picture"


def _item_dimension_choices(i, sheet) -> list:
    """The pictures a BOQ row may put in its Dimension column. A product's come from its specsheet
    (_dimension_choices). An accessory line's is the picture its accessory had when it was added
    (models.AccessoryDetail) - and never a specsheet drawing, which no export prints on one."""
    if not i.is_accessory:
        return _dimension_choices(sheet)
    name = accessories.line_picture(i)
    if not name or not (IMAGES_DIR / name).exists():
        return []
    return [{"label": ACCESSORY_PICTURE_LABEL, "kind": "accessory", "url": _image_url(name),
             "name": str((i.config or {}).get("accessory") or "Accessory")}]


def _clean_line_config(raw) -> dict:
    """A BOQ row's config as stored. An accessory picture is kept only when it names a real one
    (accessories.picture_file); anything else under that key is dropped, not stored."""
    config = dict(raw) if isinstance(raw, dict) else {}
    if "accessory_image" in config:
        name = accessories.picture_file(config["accessory_image"])
        if name and (IMAGES_DIR / name).exists():
            config["accessory_image"] = name
        else:
            config.pop("accessory_image")
    # Which accessory part the line is (models.AccessoryPart) - what lets lines of the same part
    # under different products be counted together. An id or nothing.
    if "accessory_part" in config:
        try:
            part_id = int(config["accessory_part"])
        except (TypeError, ValueError):
            part_id = 0
        if part_id > 0 and not isinstance(config["accessory_part"], bool):
            config["accessory_part"] = part_id
        else:
            config.pop("accessory_part")
    return config


def specsheet_detail(s: models.Specsheet) -> dict:
    d = specsheet_summary(s)
    diagrams = [
        {"url": _image_url(dg["path"]), "width": dg.get("width"), "height": dg.get("height"), "label": dg.get("label")}
        for dg in (s.diagrams or [])
        if Path(dg["path"]).exists()
    ]
    d.update({
        "fields": s.fields or {},
        "options": s.options or {},
        "params": s.params or {},
        "raw_text": s.raw_text or "",
        "diagrams": diagrams,
        # Whether this product has a dimensioned line drawing the exports can put in the
        # Dimension column, and where to show it in the picker. Resolved here rather than
        # letting the frontend match on a label string: which label carries a line drawing is
        # a property of how the catalogue was parsed (excel_export.LINE_DIAGRAM_LABEL), not
        # something app.js should hold a second copy of.
        "line_diagram_url": _line_diagram_url(s),
        # Every picture that can go in the Dimension column - see _dimension_choices.
        "dimension_choices": _dimension_choices(s),
        "default_unit": "Mtr" if "strip" in (s.category or "").lower() or "band" in (s.category or "").lower()
        or "grazer" in (s.category or "").lower() else "Nos.",
        # Only ever populated by a human mapping a PriceEntry to this specsheet (see
        # /api/price-entries/{id}) - never auto-matched. Kept out of specsheet_summary()
        # (used by the bulk Library list) so browsing the library doesn't pay for a
        # relationship load on every row just to serve the rare specsheet that has one.
        "price_entries": [price_entry_dict(p, include_specsheet=False) for p in (s.price_entries or [])],
        # A product authored in NPD carries its own pricing instead: a base plus an adder per
        # chosen option, so the Configurator can price a configuration nobody has ever quoted
        # before. Sent alongside price_entries rather than converted into them because the two
        # answer different questions - a PriceEntry is a price somebody verified off a real
        # price sheet, these are the rule the product was defined with.
        "origin": s.origin or "parsed",
        "base_price": s.base_price,
        "price_adders": s.price_adders or {},
        # What this make calls each shared field (Lumenpulse's "Beam" is an "Optic"), and
        # the option groups that are this product's own - Decorative Bezel Finish, Trim
        # Shape, Joiners and the like. Both are needed for the Configurator to offer every
        # axis the catalogue actually sells, under the name the catalogue uses.
        "field_labels": field_labels_for(s.make),
        # The order and grouping this product's own manufacturer asks its questions in, so
        # the Configurator can mirror it rather than inventing a layout of its own. Empty
        # for makes that publish no configurator - those keep the generic grid.
        "config_layout": config_layout(s) if (s.make or "") == "Exenia" else [],
        "extra_fields": [
            {"key": k, "label": _extra_field_label(k), "options": v}
            for k, v in (s.options or {}).items()
            if k not in FIELD_ORDER and k not in _NON_CONFIG_FIELDS and _is_real_choice(k, v)
        ],
    })
    return d


def item_dict(i: models.BOQItem) -> dict:
    # Series/Family/Variant and the product image are read live off the linked Specsheet,
    # not copied/frozen onto the item like finish/wattage/price are (see DataBaseSchema.md
    # section 4) - these three are catalog *identity* labels, not commercial terms a past
    # quotation needs to stay historically accurate against, so showing whatever the
    # Library currently says is the more useful behavior for the compact row's short
    # description. Blank/accessory/driver rows have no specsheet_id, hence no image/series -
    # the frontend falls back to item.description for those.
    sheet = i.specsheet if i.specsheet_id else None
    return {
        "id": i.id,
        "room_id": i.room_id,
        "specsheet_id": i.specsheet_id,
        # its product was deleted from NPD: the line stays as it is, but cannot be reconfigured
        "product_deleted": bool(sheet is not None and (sheet.approval_status or "") == NPD_DELETED),
        "parent_item_id": i.parent_item_id,
        "sort_order": i.sort_order,
        "item_code": i.item_code,
        "reference_number": i.reference_number or "",
        "model_no": i.model_no or "",
        "description": i.description,
        "dimensions": i.dimensions or "",
        "show_line_diagram": bool(i.show_line_diagram),
        "dimension_image": i.dimension_image or "",
        "finish": i.finish,
        "wattage": i.wattage,
        "beam": i.beam,
        "control": i.control,
        "led_color": i.led_color or "",
        "config": i.config or {},
        "make": i.make,
        "qty": i.qty,
        "unit": i.unit,
        "remarks": i.remarks,
        "is_accessory": i.is_accessory,
        "is_driver": i.is_driver,
        # Quick BOQs only - empty lists on a detailed BOQ's items.
        "area_ids": list(i.area_ids or []),
        "ref_image_ids": list(i.ref_image_ids or []),
        "price": i.price or 0.0,
        # None = follows the quotation's rule (see quotation_export.line_discount)
        "discount_percent": i.discount_percent,
        "specsheet_series": (sheet.series if sheet else "") or "",
        "specsheet_family": (sheet.family if sheet else "") or "",
        "specsheet_variant": (sheet.variant if sheet else "") or "",
        # A BOQ row shows this at thumbnail size, so it asks for a thumbnail.
        "specsheet_image_url": (image_urls(sheet)[1] if sheet and sheet.image_path else None),
        # Present only when this product actually publishes a dimensioned line drawing, which
        # is what lets the row offer the "put the drawing in the Dimension column" toggle
        # instead of showing a dead option on the 1185 products that have no such drawing.
        "line_diagram_url": (_line_diagram_url(sheet) if sheet else None),
        "dimension_choices": _item_dimension_choices(i, sheet),
    }


def room_dict(r: models.BOQRoom) -> dict:
    return {
        "id": r.id,
        "floor_id": r.floor_id,
        "name": r.name,
        "sort_order": r.sort_order,
        "remark": r.remark or "",
        "items": [item_dict(i) for i in r.items],
    }


def floor_dict(f: models.BOQFloor) -> dict:
    return {
        "id": f.id,
        "boq_id": f.boq_id,
        "name": f.name,
        "sort_order": f.sort_order,
        "rooms": [room_dict(r) for r in f.rooms],
    }


def _utc_iso(dt: datetime.datetime | None) -> str | None:
    """Every datetime this app stores comes from models.now() (datetime.utcnow()) - a naive
    value with no timezone attached, so plain .isoformat() produces a string like
    "2026-08-14T10:30:00" with no 'Z'/offset. A browser's `new Date(...)` treats a string
    like that as *local* time, not UTC - silently shifting every timestamp by the viewer's
    own UTC offset (a real, previously-unnoticed bug: the BOQs list's "Updated" column has
    been showing a wrong time since it was added). Appending 'Z' explicitly is what makes
    the client parse it as UTC, which is what it actually is - required for any correct
    timezone conversion downstream (e.g. displaying it in IST for BOQ branch timestamps)."""
    return dt.isoformat() + "Z" if dt else None


def boq_summary(b: models.BOQ) -> dict:
    return {
        "id": b.id,
        "project_name": b.project_name,
        "project_code": b.project_code or "",
        "area": b.area,
        "client": b.client,
        "kind_attention": b.kind_attention or "",
        "boq_date": b.boq_date,
        "discount_percent": b.discount_percent or 0.0,
        "show_discount": b.show_discount is not False,
        "currency": b.currency or "INR",
        "created_at": _utc_iso(b.created_at),
        "updated_at": _utc_iso(b.updated_at),
        "floor_count": len(b.floors),
        "item_count": sum(len(r.items) for f in b.floors for r in f.rooms),
        "parent_boq_id": b.parent_boq_id,
        "branch_name": b.branch_name or "",
        "branch_count": len(b.branches),
        "mode": b.mode or "detailed",
        "created_by_id": b.created_by_id,
        # None when not recorded, or when the account that made it has since been removed.
        "created_by_email": b.created_by.email if b.created_by else None,
        # A branch is not a row of its own in the list, so "made by me" also has to find the
        # origin of a branch this person made - otherwise their work is invisible to the filter.
        "branch_created_by_emails": sorted({br.created_by.email for br in b.branches
                                            if br.created_by}),
    }


def area_dict(a: models.BOQArea) -> dict:
    return {"id": a.id, "name": a.name, "sort_order": a.sort_order}


def ref_image_dict(im: models.BOQRefImage) -> dict:
    return {"id": im.id, "label": im.label or "", "sort_order": im.sort_order,
            "url": _image_url(im.filename)}


def boq_detail(b: models.BOQ) -> dict:
    d = boq_summary(b)
    d["notes_text"] = b.notes_text or ""
    d["floors"] = [floor_dict(f) for f in b.floors]
    d["areas"] = [area_dict(a) for a in b.areas]
    d["ref_images"] = [ref_image_dict(im) for im in b.ref_images]
    d["exports"] = [
        {"id": e.id, "file_path": e.file_path, "exported_at": _utc_iso(e.exported_at)}
        for e in b.exports
    ]
    d["parent_boq"] = (
        {"id": b.parent_boq.id, "project_name": b.parent_boq.project_name} if b.parent_boq_id and b.parent_boq else None
    )
    d["branches"] = [
        {"id": br.id, "branch_name": br.branch_name, "updated_at": _utc_iso(br.updated_at)}
        for br in b.branches
    ]
    return d


def get_or_404(db, model, obj_id, name="object"):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{name} {obj_id} not found")
    return obj


def get_boq_or_404(db: Session, boq_id: int) -> models.BOQ:
    """Fetches a BOQ with its whole floors -> rooms -> items -> specsheet chain
    eager-loaded in a handful of bulk queries, instead of boq_detail()'s dict-building
    walk lazy-loading one query per floor, per room, per item, and per item's specsheet
    as it goes - measured directly: 18 queries to render a 14-item BOQ, which scales
    with how many products a real BOQ has (a real one can run into the hundreds - see
    the S.No autofill work's own "200 products" reference point). selectinload issues
    one extra query per level (not one per row) regardless of how many floors/rooms/
    items exist, so this stays a small constant number of round trips no matter the
    BOQ's size. Specsheets are restricted to SPECSHEET_SUMMARY_COLUMNS, the same
    column set /api/specsheets already limits itself to - item_dict() only ever reads
    id/series/family/variant/image_path from one, all covered by that set."""
    b = (
        db.query(models.BOQ)
        .options(
            selectinload(models.BOQ.floors)
            .selectinload(models.BOQFloor.rooms)
            .selectinload(models.BOQRoom.items)
            .selectinload(models.BOQItem.specsheet)
            .load_only(*(getattr(models.Specsheet, c) for c in SPECSHEET_SUMMARY_COLUMNS)),
            selectinload(models.BOQ.branches),
            selectinload(models.BOQ.exports),
            selectinload(models.BOQ.parent_boq),
        )
        .filter(models.BOQ.id == boq_id)
        .first()
    )
    if b is None:
        raise HTTPException(status_code=404, detail=f"boq {boq_id} not found")
    return b


def touch_boq(boq: models.BOQ):
    """Bumps BOQ.updated_at - called by every floor/room/item mutation below so the BOQs
    list's "Updated" column (and its default updated_at-desc sort) reflects real edit
    activity anywhere inside a BOQ, not just edits to the BOQ's own top-level fields
    (project name/client/etc - update_boq already stamped those directly)."""
    boq.updated_at = datetime.datetime.utcnow()


# ---------- pages ----------

def allowed_disciplines(request: Request, db: Session) -> list:
    """The tools this signed-in account may open. An account with none recorded reads as
    architectural - that is what every account created before the split actually had, and an
    empty list would lock out the whole existing team."""
    uid = effective_user_id(request)
    user = db.get(models.User, uid) if uid else None
    got = list(getattr(user, "disciplines", None) or [])
    valid = [d for d in got if d in models.DISCIPLINES]
    return valid or [models.DEFAULT_DISCIPLINE]


def current_discipline(request: Request) -> str:
    """Which tool this session is working in. Read from the session, never from the request -
    see this module's own note: the page asking would let a decorative-only account request
    architectural data."""
    d = request.session.get("discipline")
    return d if d in models.DISCIPLINES else models.DEFAULT_DISCIPLINE


def _render_app(request: Request, discipline: str):
    # Whether to show the admin-only tabs. Only a matter of what is offered - the server
    # refuses those actions to anyone without the permission regardless (see _required_permission).
    real, effective, previewing = _identity(request)
    is_admin = _user_role(effective) == "admin"
    perms = sorted(_access(effective)[1])
    preview = {"email": _email_of(effective), "role": _user_role(effective),
               "by": _email_of(real)} if previewing else None
    # cache-busting query param on the static asset URLs, keyed to each file's own mtime -
    # without this, a browser that already cached an old app.js/style.css can keep serving it
    # after a code change even on a manual refresh, since StaticFiles doesn't send a
    # Cache-Control header telling the browser how eagerly to revalidate
    app_js_v = int((BASE_DIR / "static" / "app.js").stat().st_mtime)
    style_css_v = int((BASE_DIR / "static" / "style.css").stat().st_mtime)
    return templates.TemplateResponse(request, "index.html", {
        "app_js_v": app_js_v, "style_css_v": style_css_v, "discipline": discipline,
        "is_admin": is_admin,
        # Whether the person looking is an admin, whoever the app is being shown as - it
        # decides whether "Preview as" is offered, and is never what decides access.
        "real_admin": _user_role(real) == "admin",
        "preview": preview,
        # What the screens offer. The server decides regardless - see _required_permission.
        "perms": perms,
        # Written into the page rather than fetched by it afterwards. The page used to ask
        # /api/me for this on every load - a round trip made only to print an email the server
        # already had when it drew the page - and the People tab, which uses it to recognise
        # your own row, could be opened before the answer came back.
        "me_email": _email_of(effective) or "",
        "asset_v": _asset_version(),
        # Whether an AI model is configured on this server, so a screen offers the choice to use
        # it only where it exists. Never the key, the provider or anything else about it.
        "ai_available": llm_extract.is_enabled(),
    })


def _enter_tool(request, discipline):
    """Records which tool this browser is in - written into the session only when it changes.

    Writing it on every page load re-signed the session cookie every time, and Starlette marks a
    response that read the session `Vary: Cookie`: a browser then reuses a cached copy only while
    the cookie is exactly the same. So each page load threw away every cached picture - live
    served 5,434 thumbnails in the week of 3-9 Oct 2026, a grid of 138 at a time, all of them
    full downloads. Once a day the session is written anyway: a cookie counts its 14 days from
    when it was last signed, and this is what keeps a session in daily use from expiring, as the
    write on every page load did before."""
    if request.session.get("discipline") != discipline:
        request.session["discipline"] = discipline
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    if request.session.get("day") != today:
        request.session["day"] = today


@app.get("/architectural", response_class=HTMLResponse)
def architectural_app(request: Request, db: Session = Depends(get_db)):
    if models.DISCIPLINES[0] not in allowed_disciplines(request, db):
        return RedirectResponse(url="/")
    _enter_tool(request, "architectural")
    return _render_app(request, "architectural")


@app.get("/decorative", response_class=HTMLResponse)
def decorative_app(request: Request, db: Session = Depends(get_db)):
    if "decorative" not in allowed_disciplines(request, db):
        return RedirectResponse(url="/")
    _enter_tool(request, "decorative")
    return _render_app(request, "decorative")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    """The chooser. Skipped entirely for an account with access to only one tool - offering a
    choice of one is a click that teaches nothing."""
    allowed = allowed_disciplines(request, db)
    if len(allowed) == 1:
        return RedirectResponse(url=f"/{allowed[0]}")
    return templates.TemplateResponse(request, "choose.html", {"allowed": allowed,
                                                               "asset_v": _asset_version()})


# ---------- specsheets ----------

@app.get("/api/specsheets")
def list_specsheets(request: Request, db: Session = Depends(get_db)):
    """The general specsheet catalogue - what the Library grid, Direct picker, the main
    Configurator's typology list, and Compare Specsheets all browse. Excludes typology
    "Driver": the ~95 driver SKUs imported by import_drivers.py aren't a lighting
    product to browse/pick here, and since 2026-08-06 the "Add Driver" flow doesn't
    read from the Specsheet library at all (see DRIVER_RULES in static/app.js and
    DriverRules.md) - so these rows just sit unused in the database rather than
    cluttering the general product picker.

    Only fetches SPECSHEET_SUMMARY_COLUMNS (2026-08-19) - this is the single most
    frequently-hit specsheet endpoint (loaded fresh on every visit to the Library,
    Add Product, Compare Specsheets, and Match tabs), and with the library now past 700
    rows, pulling every column (raw_text/params/options included, each up to several KB
    of parsed PDF text per row) roughly doubled real query time versus fetching only
    what specsheet_summary() actually uses - measured directly, not assumed (67ms vs
    34ms locally, over 574 rows).

    Returned as a plain JSONResponse (2026-08-19), not a bare list/dict - a bare return
    value goes through FastAPI's jsonable_encoder(), which recursively type-checks every
    value looking for datetimes/Enums/Pydantic models to convert; specsheet_summary()'s
    output is already 100% plain str/int/bool/list/dict (it round-tripped through
    Postgres JSON columns and f-strings, nothing exotic), so that walk is pure overhead -
    measured at ~10x slower than a plain json.dumps() of the same data (56-78ms vs
    5-6ms). JSONResponse's own render() calls json.dumps() directly and is skipped by
    FastAPI entirely once the endpoint already returns a real Response object."""
    # Column rows, not mapped Specsheet objects (2026-09-08). SPECSHEET_SUMMARY_COLUMNS is
    # exactly the set specsheet_summary() and image_urls() read, and a Row exposes them under
    # the same attribute names, so nothing downstream can tell the difference; verified
    # byte-for-byte over all 1,193 rows.
    #
    # Worth 12ms of ~110ms on the server, not the 60ms first estimated here - that figure came
    # from comparing an ORM query against a raw Core fetch on its own connection, which is not
    # the same measurement. The honest one is both forms timed side by side in the same
    # session. Little is left to win: Postgres executes this in 4ms and the remaining ~100ms
    # is moving a 1.7MB result out of it, which is why the payload, not the query, is the
    # thing that would have to change next.
    sheets = (
        db.query(*(getattr(models.Specsheet, c) for c in SPECSHEET_SUMMARY_COLUMNS), models.Specsheet.options)
        .filter(models.Specsheet.typology != "Driver")
        # Scoped to the tool this session is in - the two catalogues never mix.
        .filter(models.Specsheet.discipline == current_discipline(request))
        # A product authored in NPD is a draft until somebody approves it. This one filter
        # keeps a half-written definition out of the Library, the Configurator, Match and
        # every BOQ picker at once, because all of them browse this endpoint.
        .filter(models.Specsheet.approval_status == "approved")
        # id as the tie-break: several rows can share a product name (one catalogue lists
        # SOLARA Midi four times, at different wattages), and ordering on the name alone
        # leaves their order to whatever the database happens to return - which changes when
        # rows are rewritten, so the Library grid quietly reshuffles between visits.
        .order_by(models.Specsheet.product_name, models.Specsheet.id)
        .all()
    )
    # For Search, and only here: the values a product offers that its fields do not already say (a baffle
    # finish, a mounting), so what Configure can pick, Search can find. Every other summary went without.
    return JSONResponse([dict(specsheet_summary(s), option_words=_option_words(s)) for s in sheets])


def _norm_text(v) -> str:
    return " ".join(str(v or "").split()).strip().lower()


def _dedup_fingerprints(category, product_name, series, family, variant, make, raw_text, fields, params, options) -> set:
    """Two independent, both-exact signatures for "this is the same product page as
    one already in the library" - a page counts as a duplicate of an existing
    specsheet if EITHER matches, not just one fixed representation of "the same
    content":

    - the page's own extracted raw text (a superset of everything else parsed from
      it, since category/product_name/fields/params/options are all derived from
      it), whitespace-normalized so incidental formatting doesn't cause a false
      "these are different" negative;
    - the full set of parsed parameters - classification (category/product_name/
      series/family/variant/make) plus every fields/params/options value, each
      whitespace-normalized the same way. Catches the case raw_text alone can miss:
      two pages whose extracted *text* differs in some inconsequential way (a
      different re-scan/export of the same underlying PDF) but whose every actual
      parameter - the values a real BOQ/config rule ever reads - is identical.

    Both checks require an EXACT match on their own normalized representation;
    "close enough" is deliberately never treated as "the same" here - a fuzzy
    threshold silently corrupted real data the one time this project tried that
    shortcut (see TUTORIAL.md's `_undouble()` case study)."""
    import json

    fps = set()
    raw_norm = _norm_text(raw_text)
    if raw_norm:
        fps.add("raw:" + raw_norm)

    params_sig = json.dumps({
        "category": _norm_text(category),
        "product_name": _norm_text(product_name),
        "series": _norm_text(series),
        "family": _norm_text(family),
        "variant": _norm_text(variant),
        "make": _norm_text(make),
        "fields": {k: _norm_text(v) for k, v in (fields or {}).items()},
        "params": {sec: {k: _norm_text(v) for k, v in (kv or {}).items()} for sec, kv in (params or {}).items()},
        "options": {k: [_norm_text(x) for x in v] for k, v in (options or {}).items()},
    }, sort_keys=True)
    fps.add("params:" + params_sig)
    return fps


def _unique_upload_dest(filename: str) -> Path:
    """Never overwrite a file already sitting in data/uploads. Diagram-image
    filenames and (for the whole-file-is-a-duplicate case below) whether it's safe
    to delete the saved upload are both derived from this path's name - so writing
    a re-uploaded file to the SAME path an existing specsheet's source_pdf_path (or
    its images) already point to would silently corrupt that existing specsheet's
    PDF/images the moment anything here touches the path, before duplicate
    detection even gets a chance to run. Uploading the same filename twice instead
    lands the second copy at "name__2.pdf", "name__3.pdf", etc.

    `filename` is browser-supplied and untrusted (2026-08-14 security review) -
    Path(filename).name strips any directory components before it's ever joined onto
    UPLOADS_DIR, so a crafted upload filename like "../../app/main.py" can't write outside
    data/uploads/ no matter what path segments it contains."""
    filename = Path(filename).name
    dest = UPLOADS_DIR / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 2
    while (candidate := UPLOADS_DIR / f"{stem}__{i}{suffix}").exists():
        i += 1
    return candidate


def _discard_parsed_images(db: Session, parsed: dict) -> None:
    """Diagram images are extracted to disk as a side effect of parsing a page,
    before duplicate-detection can run (it needs the page's own parsed content to
    compare) - so a page recognized as an exact duplicate still leaves orphan image
    files behind unless they're cleaned up here.

    Image filenames are deterministic per (PDF filename stem, page number) - so
    re-uploading a file with the *same filename* re-extracts to the exact same
    paths the original specsheet's own diagrams already point to. Deleting those
    unconditionally would silently destroy the very images the kept specsheet
    displays. Only delete a path if no specsheet in the library - not just the one
    matched as the duplicate - actually references it."""
    candidate_paths = {d.get("path") for d in (parsed.get("diagrams") or []) if d.get("path")}
    if parsed.get("image_path"):
        candidate_paths.add(parsed["image_path"])
    if not candidate_paths:
        return
    in_use = set()
    for image_path, diagrams in db.query(models.Specsheet.image_path, models.Specsheet.diagrams).all():
        if image_path:
            in_use.add(image_path)
        for d in diagrams or []:
            if d.get("path"):
                in_use.add(d["path"])
    for p in candidate_paths - in_use:
        Path(p).unlink(missing_ok=True)


@app.post("/api/specsheets/upload")
def upload_specsheets(request: Request, files: list[UploadFile] = File(...), make: str = Form(""), typology: str = Form(""), db: Session = Depends(get_db)):
    """Every page of each uploaded PDF is parsed as its own potential product (see
    parse_specsheet_pages) - a single-page datasheet yields one specsheet as before;
    a multi-page catalog (one page per size/trim variant) yields one per page, each
    correctly classified and with its own diagrams, not one entry with everything
    from every page mixed together.

    `typology` (optional, 2026-08-25): explicit typology for every specsheet created
    in this batch, chosen by the uploader (an existing typology, or a brand-new one
    typed in) - see the Library upload panel's own typology picker. Overrides
    classify_typology()'s keyword guess entirely when set, since a keyword match can
    be too coarse for a real product (found from a real case: a InHouse datasheet
    whose own title literally says "Flexible LED Lens Grazer" auto-classified as the
    broader, wrong "Wall Grazer / Wall Washer" purely because "grazer" matched first -
    the uploader knows the real distinction the keyword table can't). Left blank
    (the default, and every call before this param existed), classify_typology()'s
    guess is used exactly as before - explicit choice is additive, not a new
    requirement for every upload.

    `make` (optional, 2026-08-18): parse_specsheet_pages() only ever knows how to read
    InHouse's own PDF table layout and always reports "InHouse" regardless of what was
    actually uploaded - so a different manufacturer's PDF need a way to say what it
    really is. When set, this overrides that per file in this batch; left blank, the
    parser's own guess (today, always "InHouse") is kept, unchanged from before this
    param existed. Either way the PDF itself and its raw extracted text are always
    saved and searchable even if the table-shaped fields (category/product_name/
    fields/...) come back empty for a layout the parser doesn't recognize - nothing
    about the upload is ever silently dropped, only left for a human to fill in via
    the Library's own edit form.

    A page that's an exact replica of a specsheet already in the library - matching
    on either its raw extracted text OR its complete set of parsed parameters (see
    _dedup_fingerprints) - is skipped rather than inserted again, for every
    typology alike (the check has no notion of typology at all, so nothing about a
    specific classification is ever exempt). Checked against the whole library, not
    just this upload batch, and updated as new pages are added within this same
    call so uploading several copies of one file together doesn't create
    duplicates of each other either."""
    typology = typology.strip()
    fingerprint_index = {}
    for s in db.query(models.Specsheet).all():
        for fp in _dedup_fingerprints(s.category, s.product_name, s.series, s.family, s.variant, s.make, s.raw_text, s.fields, s.params, s.options):
            fingerprint_index.setdefault(fp, s)
    created = []
    skipped = []
    for f in files:
        dest = _unique_upload_dest(f.filename)
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        any_new_from_this_file = False
        for parsed in parse_specsheet_pages(dest, IMAGES_DIR):
            if make.strip():
                parsed["make"] = make.strip()
            fps = _dedup_fingerprints(
                parsed["category"], parsed["product_name"], parsed["series"], parsed["family"],
                parsed["variant"], parsed["make"], parsed["raw_text"], parsed["fields"], parsed["params"], parsed["options"],
            )
            existing = next((fingerprint_index[fp] for fp in fps if fp in fingerprint_index), None)
            if existing is not None:
                _discard_parsed_images(db, parsed)
                skipped.append({
                    "filename": parsed["filename"], "page_number": parsed["page_number"],
                    "duplicate_of_id": existing.id, "duplicate_of_name": existing.product_name,
                })
                continue
            sheet = models.Specsheet(
                discipline=current_discipline(request),
                filename=parsed["filename"],
                page_number=parsed["page_number"],
                category=parsed["category"],
                typology=typology or classify_typology(parsed["category"]),
                environment=classify_environment(parsed["category"]),
                product_name=parsed["product_name"],
                series=parsed["series"],
                family=parsed["family"],
                variant=parsed["variant"],
                make=parsed["make"],
                source_pdf_path=str(dest),
                image_path=parsed["image_path"],
                diagrams=parsed["diagrams"],
                raw_text=parsed["raw_text"],
                params=parsed["params"],
                fields=parsed["fields"],
                options=parsed["options"],
            )
            db.add(sheet)
            db.flush()
            # A strip light's specsheet also shows the profiles the strip is fitted into, which
            # the diagram extractor above does not keep - it takes one picture per cluster, and a
            # profile sits close enough to the strip's own drawing to be treated as part of it.
            # They are cut out here instead, so the Library shows them and a BOQ row can print one
            # in its Dimension column without anybody opening the PDF. Nothing for other kinds of
            # product, and nothing at all if the page cannot be read - never at the cost of the
            # upload itself (see extract_profile_pictures).
            from . import profile_pictures
            extracted = profile_pictures.extract_profile_pictures(
                dest, parsed["page_number"], IMAGES_DIR, "profiles_%d" % sheet.id,
                typology=sheet.typology)
            if extracted:
                sheet.diagrams = profile_pictures.merge_into_diagrams(sheet.diagrams, extracted)
            for fp in fps:
                fingerprint_index.setdefault(fp, sheet)
            created.append(specsheet_detail(sheet))
            any_new_from_this_file = True
        if not any_new_from_this_file:
            # every page in this upload already existed verbatim - nothing in the DB
            # references this newly-saved copy (all matches kept their original
            # source_pdf_path), so don't leave a redundant PDF sitting in data/uploads
            dest.unlink(missing_ok=True)
    db.commit()
    return {"created": created, "skipped_duplicates": skipped}


@app.get("/api/specsheets/frequent")
def frequent_specsheets(request: Request, db: Session = Depends(get_db)):
    """The products this team quotes most, for the first tab of "Add product" - [{id, boqs,
    lines}], most used first. Used means in the most different BOQs, then on the most lines, then
    most recently: a fitting that runs down thirty rows of one project is less of a regular than
    one that appears in ten projects. Only what the pickers may offer is counted - approved
    products of this tool, no drivers, no accessory rows - and only ids come back: the page
    already holds every product's summary (GET /api/specsheets), so it joins them itself.

    Registered before /api/specsheets/{sheet_id}, which would otherwise take "frequent" for an id."""
    boqs = func.count(distinct(models.BOQ.id))
    lines = func.count(models.BOQItem.id)
    rows = (db.query(models.BOQItem.specsheet_id, boqs, lines)
            .join(models.BOQRoom, models.BOQRoom.id == models.BOQItem.room_id)
            .join(models.BOQFloor, models.BOQFloor.id == models.BOQRoom.floor_id)
            .join(models.BOQ, models.BOQ.id == models.BOQFloor.boq_id)
            .join(models.Specsheet, models.Specsheet.id == models.BOQItem.specsheet_id)
            .filter(models.BOQ.discipline == current_discipline(request),
                    models.Specsheet.approval_status == "approved",
                    models.Specsheet.typology != "Driver",
                    models.BOQItem.parent_item_id.is_(None))
            .group_by(models.BOQItem.specsheet_id)
            .order_by(boqs.desc(), lines.desc(), func.max(models.BOQ.updated_at).desc())
            .limit(150).all())
    return [{"id": sid, "boqs": nb, "lines": nl} for sid, nb, nl in rows]


@app.get("/api/specsheets/{sheet_id}")
def get_specsheet(sheet_id: int, db: Session = Depends(get_db)):
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    d = specsheet_detail(s)
    d["identity"] = _identity_of(db, s)
    # The details the product form asks, as NPD decided them for this brand's typology - or None,
    # and the form keeps its automatic list (app/configurator.py). Their names reach every label.
    d["configurator"] = configurator.for_product(db, s)
    if d["configurator"]:
        d["field_labels"] = dict(d["field_labels"], **{f["key"]: f["label"] for f in d["configurator"]})
    return d


def _identity_of(db, s) -> list:
    """[{field_key, label, value}] - what names this product, in the order its product code is
    assembled: the built-ins, then its typology's own identity fields.

    The Configurator reads this to put the identity segments at the front of the generated
    code, so the code the NPD form previews and the code the Configurator shows are built by the
    same rule from the same values. Only fields with a value are listed; a blank one contributes
    nothing, the same as a configured field with no code."""
    fields = s.fields or {}
    values = {"series": s.series or "", "family": s.family or "",
              "sub_typology": fields.get("sub_typology") or "", "variant": s.variant or ""}
    out = []
    for key, label in _identity_fields(db, s.typology or "", s.discipline or models.DEFAULT_DISCIPLINE):
        value = str(values.get(key, fields.get(key)) or "").strip()
        if value:
            out.append({"field_key": key, "label": label, "value": value})
    return out


def price_entry_dict(p: models.PriceEntry, include_specsheet: bool = True, specsheets_by_id: dict = None) -> dict:
    """`specsheets_by_id` (optional): a prefetched {id: Specsheet} map, for a caller
    that's about to build many of these at once - see list_pricing() below, which
    pulls every needed specsheet in one bulk query instead of relying on the plain
    `p.specsheet` relationship access here, which would otherwise issue one lazy-load
    SELECT per distinct specsheet_id (measured: 77 extra round-trip queries for 177
    mapped rows). Every other caller here creates/touches only a handful of rows at
    once, so their plain `p.specsheet` lazy-load stays exactly as it was - this param
    only matters at real scale."""
    d = {
        "id": p.id,
        "specsheet_id": p.specsheet_id,
        "source_file": p.source_file,
        "source_sheet": p.source_sheet or "",
        "source_label": p.source_label or "",
        "config": p.config or {},
        "source_specs": p.source_specs or {},
        "price": p.price,
        "status": p.status or "open",
    }
    if include_specsheet and p.specsheet_id:
        specsheet = specsheets_by_id.get(p.specsheet_id) if specsheets_by_id is not None else p.specsheet
        if specsheet:
            d["specsheet"] = specsheet_summary(specsheet)
    return d


@app.get("/api/list-pricing")
def list_pricing(request: Request, db: Session = Depends(get_db)):
    """Every PriceEntry (see models.py) - split into `mapped` (linked to a real specsheet,
    Locked or Keep Open, feeds the Configurator's price default) and `unmapped` (parsed
    from an uploaded price sheet but not yet linked to a product - the "needs mapping"
    queue). Nothing here was ever auto-matched to a specsheet; that link is only ever made
    by a human, through PUT /api/price-entries/{id}."""
    rows = (
        db.query(models.PriceEntry)
        .filter(models.PriceEntry.discipline == current_discipline(request))
        .order_by(models.PriceEntry.source_file, models.PriceEntry.id)
        .all()
    )
    # One bulk query for every distinct specsheet these rows reference, not one lazy-load
    # per row (see price_entry_dict's own docstring) - same load_only column restriction
    # /api/specsheets already uses, since this only ever needs the summary shape too.
    # A library accessory's price (accessory_part_id) belongs to every product that takes it, so it
    # is listed once under each of them - the same row, the same id: changing it there changes the
    # accessory's price, and the row says so ("part").
    part_rows = [p for p in rows if p.accessory_part_id]
    part_links = collections.defaultdict(list)
    if part_rows:
        for link in db.query(models.AccessoryLink).filter(
                models.AccessoryLink.part_id.in_({p.accessory_part_id for p in part_rows})).all():
            part_links[link.part_id].append(link)
    specsheet_ids = {p.specsheet_id for p in rows if p.specsheet_id}
    specsheet_ids |= {l.specsheet_id for links in part_links.values() for l in links}
    specsheets_by_id = {}
    if specsheet_ids:
        specsheets_by_id = {
            s.id: s for s in db.query(models.Specsheet)
            .options(load_only(*(getattr(models.Specsheet, c) for c in SPECSHEET_SUMMARY_COLUMNS)))
            .filter(models.Specsheet.id.in_(specsheet_ids))
            .all()
        }
    mapped = [price_entry_dict(p, specsheets_by_id=specsheets_by_id) for p in rows if p.specsheet_id]
    # What each product calls each of its library accessories - its name, or name and size when the
    # product takes two variants - which is also how its datasheet's accessory line lists them, so
    # the pricing grid's "which accessories are priced" check finds them.
    sheet_ids = {l.specsheet_id for links in part_links.values() for l in links}
    labels = {(l.specsheet_id, l.part_id): label
              for sid, links in accessory_parts.links_of(db, sheet_ids).items()
              for l, label in accessory_parts.labelled(links)} if sheet_ids else {}
    for p in part_rows:
        links = part_links.get(p.accessory_part_id, [])
        for link in links:
            sheet = specsheets_by_id.get(link.specsheet_id)
            if sheet is None:
                continue
            d = price_entry_dict(p, include_specsheet=False)
            d.update(specsheet_id=sheet.id, specsheet=specsheet_summary(sheet),
                     config={**(p.config or {}),
                             "accessory": labels.get((sheet.id, link.part_id), link.part.name)},
                     part={"id": link.part_id, "name": link.part.name, "products": len(links)})
            mapped.append(d)
    unmapped = [price_entry_dict(p) for p in rows if not p.specsheet_id and not p.accessory_part_id]
    # JSONResponse for the same reason list_specsheets() gives: price_entry_dict() and the
    # specsheet_summary() it embeds are already plain types, so FastAPI's jsonable_encoder
    # walk over the whole payload is pure overhead.
    return JSONResponse({"mapped": mapped, "unmapped": unmapped})


@app.get("/api/list-pricing/master-export")
def export_master_price_list_route(db: Session = Depends(get_db)):
    """A single readable workbook covering every mapped price in the catalog, plus a
    second sheet of what's still unmapped - see app/master_price_export.py. Regenerated
    fresh from the live database on every call, so re-downloading after mapping more
    prices is the entire "keep it updated" story - there's no separate file to maintain."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = EXPORTS_DIR / f"Master_Price_List_{ts}.xlsx"
    export_master_price_list(db, out_path)
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=out_path.name,
    )


@app.post("/api/list-pricing/master-import")
def import_master_price_list_route(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """The round-trip half of the Master Price List export - see app/master_price_import.py
    for the full matching rules. Built for the recurring "vendor's whole price list
    changed" case (every 6-8 months): update the Price/Status columns in a previously-
    exported copy of this same file and re-upload it here, instead of a developer writing
    a new one-off backfill script per revision."""
    import tempfile
    suffix = Path(file.filename or "").suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = tmp.name
    try:
        try:
            summary = import_master_price_list(db, tmp_path, current_discipline(request))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read this file as a Master Price List export: {e}")
    finally:
        os.unlink(tmp_path)
    return summary


@app.get("/api/list-pricing/pricing-master-export")
def export_pricing_master_route(db: Session = Depends(get_db)):
    """The Pricing Master workbook - every InHouse product and every accessory it allows,
    with a 0 wherever there's no price yet, so gaps are visible and fillable. See
    app/pricing_master.py for why this is a separate artifact from the Master Price List
    above rather than an option on it."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = EXPORTS_DIR / f"Pricing_Master_{ts}.xlsx"
    export_pricing_master(db, out_path)
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=out_path.name,
    )


@app.post("/api/list-pricing/pricing-master-import")
def import_pricing_master_route(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Applies a filled-in Pricing Master back to the database - the non-script half of
    the two supported ways to add prices. Rows still at 0 are skipped rather than written,
    so a partly-filled sheet can be imported repeatedly and can only ever add information."""
    import tempfile
    suffix = Path(file.filename or "").suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = tmp.name
    try:
        try:
            summary = import_pricing_master(db, tmp_path, current_discipline(request))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read this file as a Pricing Master export: {e}")
    finally:
        os.unlink(tmp_path)
    return summary


@app.post("/api/list-pricing/upload")
def upload_pricing_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Parses an uploaded price sheet (xlsx or pdf - see app/pricing_parser.py) into
    candidate PriceEntry rows, all created unmapped (specsheet_id=None, status="open") -
    this endpoint never guesses which specsheet a row belongs to. A human maps each one
    via PUT /api/price-entries/{id} from the List Pricing tab's "needs mapping" queue."""
    import tempfile
    from pathlib import Path as _Path
    from . import pricing_parser

    suffix = _Path(file.filename or "").suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file.file.read())
        tmp_path = tmp.name
    try:
        try:
            rows = pricing_parser.parse_pricing_file(tmp_path, file.filename or "upload")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not parse this file: {e}")
    finally:
        import os
        os.unlink(tmp_path)

    created = []
    for row in rows:
        entry = models.PriceEntry(
            discipline=current_discipline(request),
            source_file=row["source_file"], source_sheet=row["source_sheet"],
            source_label=row["source_label"], config=row["config"], price=row["price"],
            source_specs=row.get("source_specs") or {},
            status="open", specsheet_id=None,
        )
        db.add(entry)
        created.append(entry)
    db.commit()
    return {"created": [price_entry_dict(p) for p in created]}


def _normalize_price_config(config: dict, specsheet: models.Specsheet) -> dict:
    """Rewrites a parsed price row's config into the SPECSHEET's own vocabulary, at the
    moment a human maps the two together.

    Necessary because the two documents spell the same fact differently: a price sheet
    says the fixture is "1-3W" (a range), while the specsheet's parsed options for that
    same product are ["3W"]. `config` is matched by EXACT equality against whatever the
    Configurator's dropdown actually offers (see priceEntryMatches in static/app.js), so
    leaving the price sheet's own notation there means the price silently never applies -
    the exact failure mode already hit once with multi-value CCT cells.

    Rules, both conservative:
      - A field the specsheet has 0 or 1 option for cannot discriminate between
        configurations, so the key is dropped entirely - the price simply applies to this
        specsheet (the EMBER case, config {}).
      - A field with several real options keeps the key, rewritten to whichever option
        matches numerically ("9W" -> the specsheet's own "9W"); if none matches, the key
        is dropped rather than left unmatchable, since a human has already confirmed this
        price belongs to this product and a stale notation shouldn't veto that.
    """
    options = specsheet.options or {}
    normalized = {}
    for key, value in (config or {}).items():
        if key == "accessory":
            # Rewrite to the specsheet's OWN spelling of this accessory ("Spike" ->
            # "GROUNDSPIKE") - resolveAccessoryPrice matches by exact string against the
            # option the picker offers, so the price sheet's own wording would silently
            # never apply. None = no unambiguous counterpart on this product; kept
            # verbatim so the row stays visible and correctable rather than vanishing.
            matched = price_matcher.match_accessory_name(value, _split_field_options((specsheet.fields or {}).get("accessories")))
            normalized[key] = matched or value
            continue
        choices = options.get(key) or []
        if len(choices) <= 1:
            continue  # nothing to disambiguate
        nums = re.findall(r"\d+\.?\d*", str(value))
        match = next((c for c in choices if c == value), None)
        if match is None and nums:
            match = next((c for c in choices if re.findall(r"\d+\.?\d*", c) == nums), None)
        if match is not None:
            normalized[key] = match
    return normalized


@app.get("/api/price-entries/{entry_id}/suggestions")
def price_entry_suggestions(entry_id: int, db: Session = Depends(get_db)):
    """Ranked specsheet suggestions for one unmapped PriceEntry - see
    app/price_matcher.py. Reads the price row's own source_specs (verbatim, multi-value
    cells intact) against each candidate specsheet's fields using range/set semantics
    ("1-3W" is a range; "2700K, 3000K, 4000K" is an allowed set), which plain string
    equality cannot do. Purely advisory: this endpoint never writes anything - a human
    still picks and confirms, and records their confidence as Locked or Keep Open."""
    p = get_or_404(db, models.PriceEntry, entry_id, "price entry")
    specs = p.source_specs or {}
    if not specs:
        return {"suggestions": [], "reason": "This row has no structured specs to match on - map it by hand."}
    # Typology is never crossed (standing rule, same as the Match tool): a price row for a
    # spotlight is only ever offered spotlight candidates. Inferred from the source sheet's
    # own name, which on every real price sheet inspected names the product family it prices.
    sheet_hint = f"{p.source_sheet} {p.source_label}"
    typology_guess = classify_typology(sheet_hint)
    query = db.query(models.Specsheet).filter(models.Specsheet.typology != "Driver")
    if typology_guess:
        query = query.filter(models.Specsheet.typology == typology_guess)
    candidates = query.all()
    suggestions = price_matcher.suggest_specsheets(p, candidates, limit=5)
    return {"suggestions": suggestions, "typology_guess": typology_guess}


@app.put("/api/price-entries/{entry_id}")
def update_price_entry(entry_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Maps an unmapped PriceEntry to a real specsheet (specsheet_id), and/or changes its
    Locked/Keep Open status - the only two things a human does with a parsed row before
    it can affect a BOQ default. `config` may also be corrected here (the parser's first
    guess at wattage/cct/etc isn't always exactly what the matched specsheet's own field
    values look like)."""
    p = get_or_404(db, models.PriceEntry, entry_id, "price entry")
    if "specsheet_id" in payload:
        if p.accessory_part_id and payload["specsheet_id"] in (None, p.specsheet_id, ""):
            # A part's price, unmapped: it goes back to the waiting list rather than vanishing.
            if payload["specsheet_id"] in (None, ""):
                p.accessory_part_id = None
        else:
            p.specsheet_id = payload["specsheet_id"]
            if p.specsheet_id:
                p.accessory_part_id = None
        # Rewrite the parsed config into this specsheet's own spelling now that a human
        # has confirmed the pairing - see _normalize_price_config for why leaving the
        # price sheet's notation would make the price silently never apply.
        if p.specsheet_id and "config" not in payload:
            target = db.query(models.Specsheet).filter(models.Specsheet.id == p.specsheet_id).first()
            if target:
                p.config = _normalize_price_config(p.config, target)
    if "status" in payload and payload["status"] in ("locked", "open"):
        p.status = payload["status"]
    if "config" in payload:
        p.config = payload["config"] or {}
    if "price" in payload:
        p.price = float(payload["price"])
    # Mapped to one of that product's library accessories: the price is the accessory's
    # (app/accessory_parts.py), refused if it already costs something else.
    link = accessory_parts.link_for(db, p.specsheet_id, (p.config or {}).get("accessory"))
    if link is not None:
        try:
            accessory_parts.attach_price(db, p, link.part)
        except accessory_parts.PartError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc))
    # A library accessory's rows always agree: a new figure on one is its new figure.
    if p.accessory_part_id and "price" in payload:
        for other in accessory_parts.price_rows(db, [p.accessory_part_id]).get(p.accessory_part_id, []):
            other.price = p.price
    db.commit()
    accessories.forget()
    return price_entry_dict(p)


@app.delete("/api/price-entries/{entry_id}")
def delete_price_entry(entry_id: int, db: Session = Depends(get_db)):
    """Removes a PriceEntry outright - for a row that turned out to be junk (a parsing
    artifact, a duplicate re-upload) rather than something to map to a real product."""
    p = get_or_404(db, models.PriceEntry, entry_id, "price entry")
    db.delete(p)
    db.commit()
    return {"ok": True}


@app.put("/api/specsheets/{sheet_id}")
def update_specsheet(sheet_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """For assigning/correcting `typology` (auto-guessed at upload, but not always
    confidently - see app/typology_classifier.py), `series`/`family`/`variant`
    (auto-split from the parsed product name/category, which occasionally needs a
    human correction too - e.g. a series name the size-word heuristic didn't
    recognize), and `environment` (auto-guessed similarly - see
    app/environment_classifier.py)."""
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    for field in ("typology", "category", "make", "series", "family", "variant"):
        if field in payload:
            setattr(s, field, payload[field])
    if "environment" in payload:
        # Not free text like the fields above - restrict to the two real values so a
        # malformed payload can't wedge junk into what's meant to be a small fixed set.
        s.environment = [v for v in payload["environment"] if v in ("Interior", "Exterior")]
    db.commit()
    accessories.forget()          # a product's accessories may have just changed
    return specsheet_detail(s)


_NO_STALE_CACHE_HEADERS = {"Cache-Control": "no-cache"}


# A product photograph never changes for a given (row, file mtime) - if the picture is
# replaced the mtime moves and the URL that carries `v` changes with it, exactly the way the
# app already busts app.js. So a versioned request can be cached hard: no revalidation, no
# round trip. Without `v` the old cautious policy stands, because then the URL cannot tell a
# new photograph from an old one. `private`: the pictures are behind the sign-in, so only the
# browser that asked may keep one - which is also what lets ImmutableWithoutVaryCookie drop
# `Vary: Cookie` from them safely.
_IMMUTABLE_CACHE_HEADERS = {"Cache-Control": "private, max-age=31536000, immutable"}
# Wide enough for the largest place a thumbnail is shown (a 150px diagram tile) on a 2x
# display, and small enough that a grid of forty costs less than one original.
THUMB_WIDTHS = (200, 400, 800)


# Bumped whenever the way a thumbnail is produced changes, so every cached file from the old
# way is ignored rather than served. v1 converted straight to RGB, which fills a transparent
# background with BLACK - and a great many product renders are cut out on transparency, so
# the Library filled with luminaires on black tiles.
_THUMB_RECIPE = "v5"


def _whiten_edge_background(im):
    """Replaces a photograph's black studio background with white, flooding inward from the
    border so only black CONNECTED TO THE EDGE is touched.

    Some makers shoot on black. The naive fix - recolour every dark pixel - destroys the
    product: Lumenpulse's inground fixture is a chrome ring around a black bowl, and a global
    replace punches a white hole through the middle of it. Flooding from the border cannot
    reach the bowl, because the bright rim encloses it.

    Only runs when the border really is black (see the caller), so an ordinary photograph
    that happens to have a dark corner is left alone.
    """
    from collections import deque
    im = im.convert("RGB")
    w, h = im.size
    px = im.load()
    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        q.append((x, 0)); q.append((x, h - 1))
    for y in range(h):
        q.append((0, y)); q.append((w - 1, y))
    while q:
        x, y = q.popleft()
        if not (0 <= x < w and 0 <= y < h):
            continue
        i = y * w + x
        if seen[i]:
            continue
        r, g, b = px[x, y]
        if (r + g + b) > 120:          # ~40 average: darker than any real product highlight
            continue
        seen[i] = 1
        px[x, y] = (255, 255, 255)
        q.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return im


def _remove_corner_badge(im):
    """Paints out a manufacturer's marketing sticker - Duragreen's green "NEW" disc - when it
    floats on empty background.

    It is a sales flag, not a property of the luminaire, and it goes onto a BOQ that may be
    read months later, where "NEW" is at best noise and at worst wrong.

    Two safeguards, because the badge is drawn ON TOP and whatever was under it is gone:

      Shape. Only a small, near-circular, solidly filled green disc counts. Lumenpulse
      photograph their colour-changing fixtures emitting green light, which fills a large,
      irregular, varied area - erasing that would destroy the product, so it must not match.

      Position. Only painted out when the ring of pixels just outside it is uniform
      background. If the badge overlaps the luminaire, removing it would leave a hole, so it
      is left alone and the picture stays honest.
    """
    rgb = im.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    step = max(1, min(w, h) // 300)
    pts = [(x, y) for y in range(0, h, step) for x in range(0, w, step)
           if (lambda r, g, b: g > 70 and g > r * 1.6 and g > b * 1.6 and (g - min(r, b)) > 45)(*px[x, y])]
    if len(pts) < 12:
        return im
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    bw, bh = x1 - x0, y1 - y0
    if not bw or not bh or max(bw, bh) / min(bw, bh) > 1.4:
        return im                                   # not a disc
    if (bw * bh) / (w * h) > 0.12:
        return im                                   # too big to be a sticker
    if len(pts) / max(1, (bw / step) * (bh / step)) < 0.55:
        return im                                   # scattered, not solid
    pad = max(3, bw // 6)
    ring = []
    for x in range(max(0, x0 - pad), min(w, x1 + pad)):
        for y in (max(0, y0 - pad), min(h - 1, y1 + pad)):
            ring.append(px[x, y])
    for y in range(max(0, y0 - pad), min(h, y1 + pad)):
        for x in (max(0, x0 - pad), min(w - 1, x1 + pad)):
            ring.append(px[x, y])
    if not ring or not all(sum(c) > 690 for c in ring):
        return im                                   # sitting on the product, leave it
    out = im.convert("RGB")
    op = out.load()
    for x in range(max(0, x0 - 1), min(w, x1 + 2)):
        for y in range(max(0, y0 - 1), min(h, y1 + 2)):
            op[x, y] = (255, 255, 255)
    return out


def _border_is_black(im) -> bool:
    """Is this shot on a black background? Sampled around the frame, not at four corners:
    a corner can be dark by accident, a whole border cannot."""
    im = im.convert("RGB")
    w, h = im.size
    px = im.load()
    pts = [(x, 0) for x in range(0, w, max(1, w // 24))]
    pts += [(x, h - 1) for x in range(0, w, max(1, w // 24))]
    pts += [(0, y) for y in range(0, h, max(1, h // 24))]
    pts += [(w - 1, y) for y in range(0, h, max(1, h // 24))]
    dark = sum(1 for x, y in pts if sum(px[x, y]) <= 120)
    # 60%, from the actual distribution across this library rather than a guess: 856 images
    # have no dark border at all, 331 have 60% or more (the makers who shoot on black), and
    # between those sits a thin tail of ordinary shots where a dark part of the product
    # merely touches an edge. Whitening one of those would eat into the product, since the
    # flood cannot tell a black background from a black luminaire attached to it - so the
    # line goes below the cluster, not through the tail.
    return dark >= 0.60 * len(pts)


def _thumb_dest(src: Path, width: int) -> Path:
    """Where a given source's thumbnail lives, whether or not it has been built yet.

    Separate from _thumbnail() so the cache can be inspected - warm_thumbnails.py asks which
    entries are missing - without generating anything as a side effect of looking."""
    st = src.stat()
    return THUMBS_DIR / f"{src.stem}_{int(st.st_mtime)}_{st.st_size}_{width}_{_THUMB_RECIPE}.jpg"


def _thumbnail(src: Path, width: int) -> Path:
    """A cached resize of `src`, generated once, on white.

    Keyed on the source's name, size and mtime, so replacing a photograph produces a
    different key rather than serving the old picture, and on the recipe, so changing how
    they are made retires the previous ones.

    The white matters. These images arrive cut out on transparency, and every place the app
    shows one - a card, a BOQ row, an export - sits on white or near-white. Compositing here
    rather than leaning on the page's own background also means the file is right in itself:
    the same thumbnail dropped into a PDF or an email still looks like a product on a page.
    """
    dest = _thumb_dest(src, width)
    if dest.exists():
        return dest
    from PIL import Image
    with Image.open(src) as im:
        im.load()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.split()[-1])   # the alpha channel is the mask
            im = flat
        else:
            im = im.convert("RGB")
        # Never upscale: a 300px source enlarged to 400 is softer, not sharper. Small
        # originals are simply served at their own size.
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))),
                           Image.LANCZOS)
        # After resizing, so the flood fill walks a few hundred pixels rather than two
        # million - the result is what gets displayed either way.
        if _border_is_black(im):
            im = _whiten_edge_background(im)
        im = _remove_corner_badge(im)
        tmp = dest.with_suffix(".tmp.jpg")
        # 90 rather than 82: these are product renders with hard edges against a flat
        # background, where JPEG ringing is visible at the quality a photograph tolerates.
        im.save(tmp, "JPEG", quality=90, optimize=True, subsampling=0)
        os.replace(tmp, dest)      # atomic, so a half-written file is never served
    return dest


@app.get("/api/specsheets/{sheet_id}/image")
def get_specsheet_image(sheet_id: int, w: int = Query(0), v: str = Query(""),
                        r: str = Query(""), db: Session = Depends(get_db)):
    # `r` is never read: it exists so that changing how a thumbnail is made changes the URL,
    # which is the only thing that can dislodge an immutable cache entry. See image_urls().
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    if not s.image_path or not Path(s.image_path).exists():
        raise HTTPException(status_code=404, detail="no image")
    image_path = Path(s.image_path)
    db.close()  # release the connection now, not after the file finishes streaming
    # Sandboxed, like /media/images: an NPD picture's type came from its uploader's extension.
    headers = {**(_IMMUTABLE_CACHE_HEADERS if v else _NO_STALE_CACHE_HEADERS),
               **PICTURE_RESPONSE_HEADERS}
    if w in THUMB_WIDTHS:
        try:
            return FileResponse(_thumbnail(image_path, w), headers=headers)
        except Exception:
            # A picture that cannot be resized is still a picture worth showing.
            pass
    return FileResponse(image_path, headers=headers)


@app.get("/api/specsheets/{sheet_id}/pdf")
def get_specsheet_pdf(sheet_id: int, db: Session = Depends(get_db)):
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    source = s.source_pdf_path or ""
    # A catalogue imported from the web keeps the manufacturer's own hosted specification
    # sheet rather than a local copy: Lumenpulse's 197 sheets are 537 MB together, which is
    # not something to put on the Oracle VM's free-tier disk to duplicate a document they
    # already serve - and their copy is the one that stays current. Redirecting means the
    # viewer, "Open in a new tab" and the Configurator's link all work unchanged.
    if source.startswith("http://") or source.startswith("https://"):
        db.close()
        return RedirectResponse(source, status_code=307)
    if not source or not Path(source).exists():
        raise HTTPException(status_code=404, detail="original PDF not found on disk")
    pdf_path = source
    db.close()
    # no `filename=` -> no Content-Disposition header, so browsers render it inline (e.g. in an <iframe>).
    # Cache-Control: no-cache (2026-08-14) - ETag/Last-Modified alone let a browser reuse a
    # cached copy indefinitely without ever rechecking with the server; that's exactly how a
    # response fetched during the ~10 minutes X-Frame-Options was wrongly set to DENY could
    # keep being served from cache - blocked - even after the header was fixed server-side.
    # no-cache still allows a fast conditional 304 off the ETag below, it just forces that
    # round trip instead of trusting a possibly-stale local copy silently.
    return FileResponse(pdf_path, media_type="application/pdf", headers=_NO_STALE_CACHE_HEADERS)


@app.post("/api/specsheets/{sheet_id}/suggest-description")
def suggest_description(sheet_id: int, chosen: dict = Body(default={}), db: Session = Depends(get_db)):
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    # The manufacturer's own code is returned alongside, so the Configurator can show what a
    # purchase order will actually say without re-implementing the composition in JavaScript
    # - two copies of that logic disagreeing is exactly how a wrong part number ships.
    # "" where the make publishes none, or where the choices do not determine one exactly.
    stem = ((s.fields or {}).get("product_code") or "").strip()
    # A brand with a code structure in NPD is described by the studio's own code, composed
    # from the classification and the options chosen - and only once it is complete, so the
    # line never carries a code with a hole in it. The whole assembly comes back as well, so
    # the Configurator can show each segment and say exactly what is still missing.
    studio = product_codes.code_for_sheet(db, s, chosen)
    if studio is not None:
        return {"description": build_description(s, chosen, code=studio["code"]),
                "product_code": studio["code"], "product_code_partial": "",
                "studio_code": studio}
    if duragreen.is_duragreen(s):
        # DuraGreen's own order code (vendor_order_codes + its 2026 catalogue's code tables):
        # the printed one for exactly this configuration, or one built the way the catalogue
        # builds it, each piece labelled with where it came from - never a InHouse-style studio
        # code, and never a guess. See app/duragreen.py for the order and why each stops where it does.
        codes, parts, rules = _vendor_rows(db, s.id)
        found = duragreen.order_code(s, chosen, codes, parts, rules, naming=_dg_naming(db, s, rules))
        line = duragreen.describe(found)
        # A code still waiting for its finish is its head only: shown as partial ("…", nothing to
        # copy), never as the code - see duragreen.describe.
        waiting = bool(found["missing"])
        return {"description": build_description(s, chosen, code=line),
                "product_code": "" if waiting else found["code"],
                "product_code_partial": found["head"] if waiting else "", "vendor_code": found}
    if (s.make or "") == "Exenia":
        # Exenia's `product_code` is only the stem - the body and version. It is NOT the code
        # and must never be offered as one: 38580 beside "what goes on the purchase order",
        # when the real code is 38580RW23SN, is a short part number that looks complete.
        # It is returned separately, so the Configurator can show it as context and say
        # plainly that it is partial.
        code, partial = compose_exenia_code(s, chosen), stem
    else:
        code, partial = stem, ""
    return {"description": build_description(s, chosen), "product_code": code,
            "product_code_partial": "" if code else partial}


@app.delete("/api/specsheets/{sheet_id}")
def delete_specsheet(sheet_id: int, request: Request, db: Session = Depends(get_db)):
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    log_activity(db, request, "specsheet.delete", "Deleted specsheet '%s'" % (
        s.product_name or "untitled"), details={
            "specsheet_id": s.id, "make": s.make or "", "typology": s.typology or ""})
    db.delete(s)
    db.commit()
    accessories.forget()          # the catalogue counts this product's accessories
    return {"ok": True}


def _ocr_page_text(page) -> str:
    """Local OCR fallback for a scanned/image-only PDF with no embedded text layer at
    all, so fitz's own extraction has nothing to find. Uses fitz's own built-in
    Tesseract integration (`Page.get_textpage_ocr`, confirmed present in the pinned
    pymupdf==1.28.0) rather than a separate Python OCR package (EasyOCR, tried first
    2026-08-20, replaced same day): no new heavy dependency at all - fitz is already
    required - and Tesseract itself is a normal `apt install tesseract-ocr` (a few MB)
    on the Linux production VM, versus EasyOCR's ~1GB PyTorch/torchvision/scipy chain,
    which was also flagged as a real OOM risk on that VM's limited RAM given its prior
    Chromium OOM incident. Confidential by construction either way: OCR runs in-process
    on whichever machine calls this - the rendered page image never leaves it, unlike a
    cloud OCR API (Google Vision/AWS Textract/etc).

    Raises cleanly (not a crash) if Tesseract itself isn't installed/found on this
    machine - `get_textpage_ocr` -> `pymupdf.get_tessdata()` explicitly documents "we
    raise an exception" in that case, which the caller already wraps in a clear
    HTTPException (see extract_requirement_pdf_text).

    full=True (not the default full=False): the default only OCRs regions NOT already
    covered by readable digital text, which is the right choice for a mixed page (some
    real text, some scanned figure) - but this is only ever called once the caller has
    already confirmed the page has NO text layer at all, so there's nothing to skip;
    full=True OCRs unconditionally rather than depending on that heuristic agreeing.

    Returns the OCR textpage rather than its plain text (2026-09-17): the requirement reader
    needs where each word sits on the page, which the textpage carries and a string does not."""
    return page.get_textpage_ocr(language="eng", dpi=200, full=True)


def _read_requirements(doc, textpages=None):
    """app/requirement_reader.read_document, but never the reason an upload fails.

    The reader is new and documents are endless in variety. If it raises on one, the user still
    gets the extracted text and the Match tab falls back to reading that text, with the reason
    shown - rather than a 500 on a file that uploaded perfectly well.
    """
    try:
        return read_document(doc, textpages), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


@app.post("/api/requirement-match/extract-pdf-text")
def extract_requirement_pdf_text(file: UploadFile = File(...), use_ocr: bool = Form(False)):
    """Convenience for the Match tool's optional PDF-upload input: pulls raw text
    out of an uploaded architect requirement PDF and hands it back for the user to
    review/split into per-fixture blocks before matching. Deliberately NOT the
    InHouse-specific parse_specsheet_pages() table parser - architect requirement
    documents don't share that layout.

    fitz (PyMuPDF), not pdfplumber (2026-08-19): pdfplumber's plain extract_text()
    reconstructs a page's text by character position, which falls apart on a
    complex multi-column table layout like a real architect requirement export -
    verified directly against a real sample: it interleaves characters from
    adjacent cells ("M o C s + p t 3 o h u e a 5...") and destroys the requirement
    paragraph mid-way, which is why the Match tool's own extraction/matching was
    only ever seeing a small, garbled fraction of the real text. fitz extracts the
    exact same page cleanly and completely - same underlying PDF, different text
    reconstruction algorithm, no accuracy tradeoff for the well-formed
    single-column PDFs this endpoint also needs to keep handling.

    Errors clean rather than 500ing (2026-08-20): an unreadable/corrupt upload used to
    surface as a raw fitz exception with no clean message; a scanned/image-only PDF (no
    embedded text layer at all - fitz opens it fine, extracts nothing) used to silently
    return "" and let the caller's own downstream logic (0 fixtures found) be the only
    signal something was wrong, with no indication of *why*.

    OCR fallback (2026-08-20 - see _ocr_page_text): only engaged when the native
    text-layer extraction above comes back empty, so a well-formed PDF (the common
    case) never pays the OCR cost - and even then, only on `use_ocr=true`, not
    automatically. It's still a real per-document cost the user should knowingly
    accept rather than have sprung on them mid-upload with no warning, so
    `needs_ocr`/`page_count` let the frontend ask first, even though (unlike the
    EasyOCR-based first version of this fallback, replaced same day) no specific
    per-page timing has been benchmarked for Tesseract in this environment - this
    machine has no working Tesseract install to measure against (the Chocolatey
    install failed on missing admin rights), so any number quoted here would be a
    guess dressed up as a measurement, not a real one. Expected to be meaningfully
    lighter/faster than the EasyOCR path was (no PyTorch model to run), but "expected"
    isn't "verified" - benchmark for real on whichever machine actually runs this
    (the Oracle VM installs Tesseract cleanly via `apt`, unlike this Windows dev
    machine) before trusting a specific number in the UI copy."""
    import fitz
    import tempfile
    # Copied to disk and opened from there, never held whole in memory - see
    # MATCH_DOCUMENT_MAX_BYTES. The copy is this request's own and goes when it ends.
    handle, name = tempfile.mkstemp(prefix="match_", suffix=".pdf")
    os.close(handle)
    path = Path(name)
    doc = None
    try:
        _spool_upload(file, path, MATCH_DOCUMENT_MAX_BYTES)
        try:
            doc = fitz.open(path, filetype="pdf")
            pages = []
            for page in doc:
                pages.append(page.get_text())
                # MuPDF keeps every page's decoded pictures in a cache with no limit set; on a
                # 60 MB schedule of drawings that cache, not the file, was +182 MB. Emptied after
                # each page it was +15 MB, and the text the same.
                fitz.TOOLS.store_shrink(100)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not read this PDF: {e}")
        return _pdf_text_and_reading(doc, pages, use_ocr)
    finally:
        if doc is not None:
            doc.close()
        path.unlink(missing_ok=True)


def _pdf_text_and_reading(doc, pages, use_ocr):
    """extract_requirement_pdf_text's answer for an opened PDF and its pages' plain text."""
    text = "\n\n".join(pages)
    if text.strip():
        # `reading` (2026-09-17) is the document read by layout - one entry per requirement, plus
        # what was left out and why. See app/requirement_reader.py. `text` stays for the paste box.
        reading, reading_error = _read_requirements(doc)
        return {"text": text, "reading": reading, "reading_error": reading_error}

    if not use_ocr:
        return {"text": "", "needs_ocr": True, "page_count": len(doc)}

    try:
        textpages = {index: _ocr_page_text(page) for index, page in enumerate(doc)}
        text = "\n\n".join(page.get_text(textpage=textpages[index]) for index, page in enumerate(doc))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"This PDF has no text layer, and OCR could not read it either: {e}")
    if not text.strip():
        raise HTTPException(status_code=400, detail="No text could be extracted from this PDF, even with OCR - the scan quality may be too low to read. Try a clearer scan, or paste the requirement text directly.")
    reading, reading_error = _read_requirements(doc, textpages)
    return {"text": text, "used_ocr": True, "reading": reading, "reading_error": reading_error}


@app.get("/api/requirement-match/index")
def requirement_match_index(request: Request, db: Session = Depends(get_db)):
    """The catalogue as the Match tab compares it: each product's stated values as numbers and
    ranges, and the typologies they fall into. Matching itself runs in the page, so moving a
    tolerance slider re-matches every requirement at once without asking the server anything -
    see app/match_index.py for why the reading of values has to happen here instead.

    JSONResponse for the reason list_specsheets() gives: the content is already plain JSON."""
    return JSONResponse(match_index.build_index(db, current_discipline(request)))


@app.get("/api/requirement-match/template")
def requirement_match_template(request: Request, db: Session = Depends(get_db)):
    """The spreadsheet a requirement is filled into. Its Typology dropdown lists this catalogue's
    own typologies, so it is built per request rather than kept as a file that would go stale."""
    content = build_template(match_index.catalogue_typologies(db, current_discipline(request)))
    return Response(content=content,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="Requirement template.xlsx"',
                             "Cache-Control": "no-store"})


@app.post("/api/requirement-match/choose")
def requirement_match_choose(payload: dict = Body(...), db: Session = Depends(get_db)):
    """What each matched product becomes as a BOQ row, for a whole batch in one request.

    Per item: the product's own option strings nearest the requirement
    (app/match_index.choose_options), the description and product code the Configurator would
    compose from them (the same suggest_description()), and - for a product authored in NPD - its
    price the way the Configurator prices it (npd.match_price), with any option the requirement
    left open that would change that price. `force` settles such an option; only one of the
    product's own options is accepted there. Changes nothing.

    Body: {"items": [{"key", "specsheet_id", "wants": {...}, "force": {field: option}}]}"""
    items = payload.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    items = [i for i in items[:3000] if isinstance(i, dict)]
    ids = set()
    for item in items:
        try:
            ids.add(int(item.get("specsheet_id")))
        except (TypeError, ValueError):
            pass
    sheets = {s.id: s for s in db.query(models.Specsheet)
              .options(selectinload(models.Specsheet.price_entries))
              .filter(models.Specsheet.id.in_(ids)).all()} if ids else {}
    results = {}
    for item in items:
        key = str(item.get("key"))
        try:
            sheet = sheets.get(int(item.get("specsheet_id")))
        except (TypeError, ValueError):
            sheet = None
        if sheet is None:
            results[key] = {"error": "This product is no longer in the library."}
            continue
        chosen = match_index.choose_options(sheet, item.get("wants") or {})
        for field, option in (item.get("force") or {}).items():
            if isinstance(option, str) and option in ((sheet.options or {}).get(field) or []):
                chosen[field] = option
        price, open_choices = npd.match_price(sheet, chosen)
        results[key] = {"chosen": chosen, **suggest_description(sheet.id, dict(chosen), db),
                        "price": price, "open": open_choices}
    return {"results": results}


def _requirements_by_model(filename, data, typologies, rules_said):
    """A requirement sheet the rules could not read, read by the model into the same rows
    read_requirements returns. Each row's typology and shape go through the same classifiers as a
    row the rules read; the model only finds where each requirement is and what it states."""
    from .requirement_excel import _classify_kind, _resolve_typology
    from .shape_classifier import classify_shape
    try:
        text = boq_ai.document_text(filename, data)
    except SheetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    fixtures = llm_extract.parse_document(text)
    if not fixtures:
        reason = llm_extract.last_error()
        raise HTTPException(status_code=400, detail="%s The AI model could not read it either%s." % (
            rules_said, (" - " + reason) if reason else ""))
    known = {t["key"]: t["name"] for t in typologies}
    requirements = []
    for n, fx in enumerate(fixtures, 1):
        description = fx["text"]
        kind = _classify_kind("", description)
        key, how, shape = "", kind, ""
        if kind == "fixture":
            key, how = _resolve_typology("", description, known)
            shape = classify_shape(description.split(";", 1)[0])
        requirements.append({
            "row": n, "kind": kind, "ref": fx.get("label") or "", "description": description,
            "typology": key, "typology_written": "", "typology_status": how, "shape": shape,
            "shape_status": "guessed" if shape else "", "area": "", "qty": "", "unit": "",
            "fields": fx.get("fields") or {}, "source": "AI", "remarks": "Read by the AI model - check it.",
        })
    return {"sheet": filename, "header_row": None, "ref_gaps": {}, "columns": [], "ignored_columns": [],
            "requirements": requirements, "ignored": [], "read_by": "ai",
            "ai_model": llm_extract.LAST_MODEL}


@app.post("/api/requirement-match/import")
def requirement_match_import(request: Request, file: UploadFile = File(...), use_llm: bool = Form(False),
                             db: Session = Depends(get_db)):
    """A filled requirement sheet, read into rows. Nothing is stored: the rows come straight back
    for the page to match, with every row and column it left out and why.

    `use_llm` (the page's "Use AI" box, and only when a model is configured): a sheet whose
    headings the rules do not recognise - a client's own layout - is read by the model instead,
    each requirement marked as read by it."""
    data = file.file.read(SHEET_MAX_BYTES + 1)
    typologies = match_index.catalogue_typologies(db, current_discipline(request))
    llm_extract.LAST_ERROR = ""
    try:
        result = read_requirements(file.filename or "", data, typologies)
    except SheetError as exc:
        if not (use_llm and llm_extract.is_enabled()) or (file.filename or "").lower().endswith(".xls"):
            raise HTTPException(status_code=400, detail=str(exc))
        result = _requirements_by_model(file.filename or "", data, typologies, str(exc))
    result["filename"] = file.filename or ""
    # Pictures pasted into the sheet's own rows. Kept the way an attached PDF is (see
    # app/requirement_pictures.py), under a token, and only those in a requirement's row: a logo
    # in the title block is not anybody's picture.
    if has_pictures(file.filename or "", data):
        try:
            pictures = sheet_pictures(data, result["sheet"])
        except Exception:
            pictures = {}
        rows = {r["row"] for r in result["requirements"]}
        mine = {row: pic for row, pic in pictures.items() if row in rows}
        names = {}
        if mine:
            token, folder = requirement_pictures.new_folder()
            names = requirement_pictures.save_sheet_pictures(folder, mine)
            for r in result["requirements"]:
                if r["row"] in names:
                    r["picture_url"] = "/api/requirement-match/document/%s/sheet/%s" % (token, names[r["row"]])
        result["sheet_pictures"] = {"found": len(pictures), "used": len(names)}
    return result


# ---------- Ask: questions answered from the app's own records (app/assistant.py) ----------

@app.get("/api/ask/status")
def ask_status(request: Request):
    """Whether questions can be asked, and how many are left today."""
    return {**assistant.budget(), "boqs": user_can(request, "boqs.view")}


@app.post("/api/ask")
def ask_question(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """A question answered from the products of the tool this session is in, and its BOQs for
    someone who may see BOQs. Changes nothing; the answer says which records it used."""
    try:
        return assistant.ask(db, payload.get("question"), current_discipline(request), user_can(request, "boqs.view"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------- the client's document, for each requirement's picture ----------

# The largest client PDF the Match tab takes: Caddy's own request limit (FinalDeployment.md). It
# was 40 MB while the file was read whole into memory, and rightly: measured on a 60 MB schedule,
# that way took +309 MB to read its text and +201 MB to attach it, on a server with about 265 MB to
# spare. Now the upload is copied to disk a megabyte at a time and PyMuPDF opens the file, reading
# a page only when it is asked for, so memory follows the pages, not the file's size.
MATCH_DOCUMENT_MAX_BYTES = 150 * 1024 * 1024
_PICTURE_CACHE = {"Cache-Control": "private, max-age=31536000, immutable"}


def _spool_upload(file, path, limit) -> int:
    """Copies an upload to `path` a megabyte at a time and refuses one over `limit` bytes,
    removing the part copied. -> its size."""
    size = 0
    try:
        with open(path, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=400, detail="That PDF is over %d MB." % (limit // (1024 * 1024)))
                out.write(chunk)
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
    return size


def _match_document(token: str):
    """The attached PDF behind a token, opened. 404 when the token is malformed or the document
    has passed its keeping time - the page then asks for it to be attached again."""
    import fitz
    folder = requirement_pictures.folder_for(token)
    path = folder / "source.pdf" if folder else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="This document is no longer kept here (documents "
                            "are kept for %d days) - attach it again." % requirement_pictures.KEEP_DAYS)
    return fitz.open(path)


# A page of 150 requirements asks for 150 thumbnails, and opening the PDF was nearly all of the
# cost of serving each one - the picture inside it decodes in about 3ms. So each document stays
# open, keyed by its token, which never points at different bytes (a new upload gets a new
# token). A fitz document is not thread-safe and these are served from a thread pool, so each is
# used under its own lock; the work inside the lock is milliseconds.
_OPEN_DOCS = collections.OrderedDict()          # token -> (document, lock)
_OPEN_DOCS_MAX = 4
_OPEN_DOCS_GUARD = threading.Lock()


def _close_open_doc(token: str):
    """Let go of a document about to be deleted - see requirement_pictures.on_release."""
    with _OPEN_DOCS_GUARD:
        entry = _OPEN_DOCS.pop(token, None)
    if entry:
        try:
            entry[0].close()
        except Exception:
            pass


requirement_pictures.on_release(_close_open_doc)


@contextlib.contextmanager
def _match_document_open(token: str):
    """The attached PDF behind a token, kept open between requests, held for this request only."""
    with _OPEN_DOCS_GUARD:
        entry = _OPEN_DOCS.get(token)
        if entry is None:
            entry = (_match_document(token), threading.Lock())
            _OPEN_DOCS[token] = entry
            while len(_OPEN_DOCS) > _OPEN_DOCS_MAX:
                _old_token, (old_doc, _old_lock) = _OPEN_DOCS.popitem(last=False)
                try:
                    old_doc.close()
                except Exception:
                    pass
        else:
            _OPEN_DOCS.move_to_end(token)
    doc, lock = entry
    with lock:
        yield doc


def _locate_rows(doc, rows) -> dict:
    """requirement_pictures.locate() over rows as the page sends them, bounded and cleaned."""
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="rows must be a list")
    clean = [{"key": str(r.get("key")), "page": str(r.get("page") or "")[:20],
              "ref": str(r.get("ref") or "")[:60], "description": str(r.get("description") or "")[:400]}
             for r in rows[:3000] if isinstance(r, dict)]
    return requirement_pictures.locate(doc, clean)


@app.post("/api/requirement-match/document")
def requirement_match_document(file: UploadFile = File(...), rows: str = Form("[]")):
    """Attaches the client's PDF, so each requirement row can show its picture, and finds every
    row's picture in the same request. The PDF is kept under a random token for
    requirement_pictures.KEEP_DAYS; pictures are drawn from it on request, never stored."""
    import json
    import fitz
    try:
        wanted = json.loads(rows or "[]")
    except ValueError:
        raise HTTPException(status_code=400, detail="rows must be JSON")
    # Copied straight to where it is kept, and opened from there - never held whole in memory.
    token, folder = requirement_pictures.new_folder()
    try:
        _spool_upload(file, folder / "source.pdf", MATCH_DOCUMENT_MAX_BYTES)
        try:
            doc = fitz.open(folder / "source.pdf", filetype="pdf")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Could not read this PDF: %s" % exc)
        skipped = ""
        try:
            pages = doc.page_count
            if not 1 <= pages <= 500:
                raise HTTPException(status_code=400, detail="A requirement document of %d pages is not one this can use." % pages)
            heavy = _pictures_over_budget(doc, wanted, (folder / "source.pdf").stat().st_size)
            if heavy:
                located, skipped = {}, heavy
            else:
                located = _locate_rows(doc, wanted)
        finally:
            doc.close()
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)    # the folder this request made, and nothing else
        raise
    result = {"token": token, "name": file.filename or "", "pages": pages, "located": located}
    if skipped:
        result["pictures_skipped"] = skipped
    return result


# Finding each row's picture keeps every page it opens, with its pictures, until it is done, so its
# memory follows the pictures on those pages, not the file's size: about three times their opened
# size (a 60 MB schedule of drawings, 63 MB of pictures opened, took +203 MB). Up to the old 40 MB
# limit a PDF is located exactly as it always was; above it, only when its pictures fit this
# budget once opened - the rows are read and matched either way.
PICTURES_CHECKED_ABOVE_BYTES = 40 * 1024 * 1024
PICTURE_BUDGET_BYTES = 32 * 1024 * 1024


def _pictures_over_budget(doc, rows, file_size) -> str:
    """'' when the pictures may be located, else the reason they are not. The size is estimated
    from each picture's own dictionary (width x height x colour channels) - nothing is decoded."""
    if file_size <= PICTURES_CHECKED_ABOVE_BYTES or not isinstance(rows, list):
        return ""
    # The pages locate() would open: each row's own page, or every page when a row names none
    # (it then searches them all, up to 60).
    indexes = set()
    for row in rows:
        number = requirement_pictures.page_number((row or {}).get("page")) if isinstance(row, dict) else 0
        if 1 <= number <= doc.page_count:
            indexes.add(number - 1)
        elif doc.page_count <= 60:
            indexes = set(range(doc.page_count))
            break
    channels = {"DeviceGray": 1, "DeviceCMYK": 4}
    total = 0
    for index in indexes:
        for image in doc[index].get_images(full=True):
            width, height, colourspace = image[2], image[3], image[5]
            total += width * height * channels.get(colourspace, 3)
    if total <= PICTURE_BUDGET_BYTES:
        return ""
    return ("This PDF's pictures come to about %d MB once opened, more than this server can cut "
            "pictures from at once - the requirements are read and matched as usual. To see each "
            "row's picture, attach a PDF of just the schedule pages." % (total // (1024 * 1024)))


@app.post("/api/requirement-match/document/{token}/locate")
def requirement_match_locate(token: str, payload: dict = Body(...)):
    """Each row's picture in an already-attached PDF - for a new sheet read against the same one."""
    doc = _match_document(token)
    rows = payload.get("rows") or []
    heavy = _pictures_over_budget(doc, rows, (requirement_pictures.folder_for(token) / "source.pdf").stat().st_size)
    if heavy:
        return {"located": {}, "pictures_skipped": heavy}
    return {"located": _locate_rows(doc, rows)}


@app.get("/api/requirement-match/document/{token}/render")
def requirement_match_render(token: str, page: int, box: str = "", w: int = 320, xref: int = 0):
    """A row's picture, its row, or the whole page, as an image. With `xref` - a picture found by
    requirement_pictures.locate() - just that embedded picture is decoded, which is fast; without,
    the part of the page in `box` is drawn. The same request always gives the same image, so the
    browser may keep it."""
    with _match_document_open(token) as doc:
        if not 1 <= page <= doc.page_count:
            raise HTTPException(status_code=404, detail="No such page")
        if xref:
            if not requirement_pictures.is_image(doc, xref):
                raise HTTPException(status_code=404, detail="No such picture")
            image, media = requirement_pictures.render_image(doc, xref, max(40, min(1600, w)))
            return Response(content=image, media_type=media, headers=_PICTURE_CACHE)
        try:
            parts = [float(v) for v in box.split(",")] if box else []
        except ValueError:
            parts = []
        rect = parts if len(parts) == 4 else list(doc[page - 1].rect)
        image = requirement_pictures.render(doc, page, rect, max(40, min(1600, w)))
    return Response(content=image, media_type="image/jpeg", headers=_PICTURE_CACHE)


@app.get("/api/requirement-match/document/{token}/sheet/{name}")
def requirement_match_sheet_picture(token: str, name: str):
    """A picture that was pasted into a row of the imported sheet."""
    folder = requirement_pictures.folder_for(token)
    if not folder or not requirement_pictures.SHEET_PICTURE_RE.match(name):
        raise HTTPException(status_code=404, detail="Not found")
    path = folder / "sheet" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers=_PICTURE_CACHE)


@app.post("/api/requirement-match")
def requirement_match(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """The bulk closest-match tool: given free-text fixture requirement paragraphs
    (architect-style prose, one per fixture), extract canonical fields from each
    and rank every specsheet in the library against them. Rule-based and fully
    explainable by design (see app/requirement_matcher.py) - not fuzzy/AI matching.

    `blocks`, if provided, may be either a list of raw strings (legacy shape /
    the very first "Find Matches" click before the user has reordered anything)
    or a list of {"block": text, "field_order": [...], "field_overrides": {...}}
    once the Match tab's per-fixture priority-ranking review step has run -
    field_order carries the user's chosen scoring priority for that one fixture
    straight through to rank_specsheets (see requirement_matcher._weights_for_order).

    `field_overrides` (2026-08-20): lets one already-matched result card's own
    "Configure parameters" panel correct/add/remove a field's value in place and
    re-match just that one fixture, without re-running the whole batch from the
    original text - a user re-pasting/re-uploading and re-reviewing every other
    fixture just to fix one wrong wattage on one fixture was the exact friction
    this closes. Applied on top of whatever extract_requirement_fields(block)
    itself found: a present, non-empty override value replaces or adds that field;
    an empty-string override removes it from scoring entirely (the user cleared a
    field they don't want considered) rather than being scored as if "" were a
    real requirement value.

    `extract_only`, if true, skips scoring/ranking entirely and returns just the
    extracted fields + typology guess per block - this is what powers that same
    review step (nothing to rank against yet, no library query needed).

    Reading (2026-09-17): a block dict may also carry `fields`, `label`, `area`, `quantity`,
    `page` and `warning`, as app/requirement_reader.py produces them for an uploaded PDF. When
    `fields` is present it is used as the requirement's fields as-is - they were read from the
    document's own columns ("14w" under "LED Power"), which is more reliable than re-running
    the regexes over the text, and a user's edits in the review step arrive the same way.
    Pasted `text` is split by requirement_reader.read_text, the same format-independent reader
    the PDF path uses, and the notes it left out come back as `ignored`."""
    extract_only = bool(payload.get("extract_only"))
    # Opt-in per request, and only meaningful when a key is configured. Defaulting this ON
    # would send client requirement wording to a third party without anyone choosing to.
    use_llm = bool(payload.get("use_llm"))
    llm_labels = []
    brand = (payload.get("brand") or "").strip()

    ignored, method = [], ""
    read_as_sentences = False
    raw_blocks = payload.get("blocks")
    if not raw_blocks:
        text_requirements, ignored, method = read_text([(None, payload.get("text", ""))])
        raw_blocks = text_requirements
        # A person's own sentences have no labels or table for the document reader to find. Read
        # as a requirement ("10 NYX Midi and 8 downlights under 100mm"), they are several rows.
        # `typed` is the paste box only: a PDF's text comes through here too, and is not this.
        read_blocks = [b.get("block", "") if isinstance(b, dict) else b for b in text_requirements]
        if (payload.get("typed") and str(payload.get("text") or "").strip()
                and not any(i.get("reason") == "terms and conditions" for i in ignored)
                and catalogue_query.looks_like_sentences(read_blocks, method, payload.get("text"))):
            read_as_sentences = True
            sentences, left_out = catalogue_query.requirements_from_text(
                payload.get("text"), catalogue_query.catalogue_for(db, current_discipline(request)),
                use_model=use_llm and llm_extract.is_enabled())
            if sentences:
                raw_blocks, ignored, method = sentences, left_out, "your sentences"
    META_KEYS = ("label", "area", "quantity", "page", "warning", "make", "typology_key", "any_typology", "shape",
                 "kinds", "only_ids", "sentence", "no_auto", "not_ids", "not_make", "kind")
    normalized = []
    for b in raw_blocks:
        if isinstance(b, dict):
            normalized.append({"block": b.get("block", ""), "field_order": b.get("field_order"),
                               "field_overrides": b.get("field_overrides"),
                               "fields": b.get("fields") if isinstance(b.get("fields"), dict) else None,
                               "meta": {k: b.get(k) or "" for k in META_KEYS}})
        else:
            normalized.append({"block": b, "field_order": None, "field_overrides": None, "fields": None,
                               "meta": {k: "" for k in META_KEYS}})

    library = []
    if not extract_only:
        query = db.query(models.Specsheet).filter(models.Specsheet.typology != "Driver")
        if brand:
            query = query.filter(models.Specsheet.make == brand)
        library = query.all()

    # One budget for the whole request, not per block: the free tier occasionally queues a
    # call for ~22 seconds, and a document with many thin blocks would otherwise stack those
    # waits. Early blocks get the model, later ones fall back - every block already has a
    # usable regex answer, so the floor never moves.
    # Regexes first, for every block. Then ONE request covering whichever blocks they did
    # poorly on - not one per block, which on a queued tier pays the wait once per fixture
    # and so ends up helping with none of them. The model only ever ADDS fields the regexes
    # missed; off unless configured, and any failure leaves exactly the regex result, so the
    # Match tab behaves identically with no key, a dead network or a spent quota.
    # `use_llm` is the user's own choice on the Match tab, per upload. Off, nothing here
    # reaches the network. On, the model only FILLS fields the document's reading missed - it no
    # longer re-reads the whole document to separate requirements (2026-09-17). That re-read cost
    # tens of thousands of tokens per upload, emptied the free tier's daily quota within a few
    # documents, and could change the requirement COUNT depending on whether quota was left. The
    # count now comes from app/requirement_reader.py alone, the same every time. The one
    # exception is pasted text in which the reader found no requirement at all: there the model
    # is the only reader left to try.
    llm_extract.LAST_ERROR = ""     # so a stale reason from a previous request is never shown
    llm_status = {"requested": use_llm, "available": llm_extract.is_enabled(),
                  "split": "regex", "fixtures_before": len(normalized),
                  "fixtures_after": len(normalized), "fields_added": 0, "error": ""}
    source_text = payload.get("text") or ""
    if use_llm and llm_extract.is_enabled() and not normalized and source_text.strip() and not read_as_sentences:
        blocks, labels, split_source = llm_extract.choose_blocks(source_text, [], force=True)
        llm_status["split"] = split_source
        if split_source == "llm":
            normalized = [{"block": b, "field_order": None, "field_overrides": {}, "fields": None,
                           "meta": {k: "" for k in META_KEYS}} for b in blocks]
            llm_labels = labels
            llm_status["fixtures_after"] = len(normalized)
    base_fields = [n["fields"] if n["fields"] is not None else extract_requirement_fields(n["block"])
                   for n in normalized]
    # A sentence's model reading was held to the sentence above; this pass is for documents.
    if use_llm and llm_extract.is_enabled() and not read_as_sentences:
        augmented, added = llm_extract.augment_batch(
            list(zip(base_fields, [n["block"] for n in normalized])), MATCH_SCORED_FIELDS)
        llm_status["fields_added"] = added
    else:
        augmented = base_fields

    results = []
    for index, item in enumerate(normalized):
        block, field_order, field_overrides = item["block"], item["field_order"], item["field_overrides"]
        fields = augmented[index]
        if field_overrides:
            fields = dict(fields)
            for key, value in field_overrides.items():
                if value:
                    fields[key] = value
                else:
                    fields.pop(key, None)
        # classify_typology was built for a specsheet's short `category` text (see its own
        # docstring/comments), where any keyword anywhere is trustworthy - a full requirement
        # paragraph is a different shape of input: it also lists accessories, driver options,
        # and (on real PDF exports, confirmed on this exact document) can have an unrelated
        # row's reference-table text bleed into the same block from adjacent PDF table columns.
        # Running the classifier on the WHOLE block let a stray "Required Accessories: Honeycomb
        # louvers" line outvote the fixture's own leading description - 23 of 52 real fixtures on
        # the sample PDF were "Supply of ... downlight ..." paragraphs misclassified as "Louvred
        # Luminaire" purely because "louvre" appears later, before "downlight" is ever reached in
        # TYPOLOGY_KEYWORDS's fixed order. Classifying only the first ";"-delimited clause - the
        # actual "Supply of X" product description, always present per split_requirement_blocks -
        # fixes this without touching the keyword table itself, which is correctly grounded in
        # SampleInputs/Lighting Encyclopedia.pdf (see typology_classifier.py's own comments).
        typology_guess = classify_typology(block.split(";", 1)[0])
        # Same reasoning/truncation as typology_guess above - the leading clause only,
        # not the whole block, so a stray "square"/"round" word in an unrelated
        # accessory/reference-table line can't outvote the fixture's own description.
        shape_guess = classify_shape(block.split(";", 1)[0])
        entry = {
            "block": block,
            "extracted_fields": fields,
            # The document's own reference for this requirement where it gives one - only the
            # model-driven split recovers these, since the rule-based split has no notion of
            # a label. Empty string when the document names nothing; never invented.
            "doc_label": item["meta"]["label"] or (llm_labels[index] if index < len(llm_labels) else ""),
            "typology_guess": typology_guess,
            # The same guess as the Match tab's own typology list names it (app/typology_match.py),
            # so a PDF or pasted requirement lands in the same place a spreadsheet row does.
            "typology_key": typology_key(typology_guess),
            "shape_guess": shape_guess,
            # format-independent "this might still be more than one fixture" signal -
            # see block_needs_manual_split's own docstring. Purely informational: the
            # block is still scored/returned as-is, this only drives a UI warning. Not asked of a
            # table row: its fields came from one row's own cells, and its text is that row joined
            # with column names, which is not the prose this check was written for.
            "needs_manual_split": item["fields"] is None and block_needs_manual_split(block),
            "area": item["meta"]["area"],
            "quantity": item["meta"]["quantity"],
            "page": item["meta"]["page"],
            "warning": item["meta"]["warning"],
            "make": item["meta"]["make"],
            "any_typology": bool(item["meta"]["any_typology"]),
            "kinds": item["meta"]["kinds"] or [],
            "only_ids": item["meta"]["only_ids"] or [],
            "no_auto": bool(item["meta"]["no_auto"]),
            "kind": item["meta"]["kind"] or "fixture",
            "not_ids": item["meta"]["not_ids"] or [],
            "not_make": item["meta"]["not_make"] or [],
            "sentence": bool(item["meta"]["sentence"]),
        }
        # A sentence's kind and shape are what its reader read, never a guess from its first clause:
        # the classifiers look for letters inside words, so "around" and "Ground floor" read as Round.
        if item["meta"]["sentence"]:
            entry["typology_key"] = item["meta"]["typology_key"]
            entry["typology_guess"] = item["meta"]["typology_key"]
            entry["shape_guess"] = item["meta"]["shape"]
        if not extract_only:
            # Same typology is the FIRST and non-negotiable matching criterion (2026-08-20,
            # standing rule) - basic lighting knowledge: a downlight requirement should never
            # get a spotlight recommendation just because its wattage/CCT happen to line up
            # closer than any real downlight's does. Previously fell back to the whole library
            # when the typology guess produced zero candidates, on the theory that a wrong/
            # too-narrow guess shouldn't return nothing - but that let a bad guess silently
            # cross into the wrong luminaire type, which is worse than an honest empty result.
            # No fallback now: an empty match list (surfaced in the UI as "no candidates in the
            # library for this brand/typology") is a more truthful answer than a cross-typology
            # suggestion. When typology_guess is empty (couldn't classify the paragraph at all -
            # see classify_typology's own "leave blank rather than guess" philosophy), every
            # typology stays eligible, since there's no basis to exclude any of them.
            candidates = [s for s in library if not typology_guess or s.typology == typology_guess]
            # Shape is a SECOND non-negotiable filter (2026-08-25, standing rule), same
            # discipline as typology - a round downlight requirement should never rank a
            # square or dual-square product as any kind of match, no matter how well its
            # electrical spec lines up (found from a real reported case: OZONE Dual Square
            # -Mini scored 100% against a "Round Shaped" requirement, since nothing checked
            # shape at all). classify_specsheet_shape() defaults an unlabeled specsheet to
            # "Round" (this catalog's own real naming convention - see its docstring), but
            # shape_guess itself is never defaulted: a requirement that doesn't mention
            # shape leaves every shape eligible, exactly like an unrecognized typology does.
            candidates = [s for s in candidates if not shape_guess or classify_specsheet_shape(s) == shape_guess]
            entry["matches"] = rank_specsheets(fields, candidates, limit=5, field_order=field_order,
                                      typology=typology_guess)
        results.append(entry)
    llm_status["error"] = llm_extract.last_error()
    response = {"llm": llm_status, "results": results}
    if not payload.get("blocks"):
        # Pasted text was read here, so what the reader left out is only known here.
        response["ignored"] = ignored
        response["method"] = method
    return response


# The condition a typology-wide rule from NPD > Rules carries for its brand: {"field": "make", "values":
# ["InHouse"]}. Every place that evaluates rules puts the product's (or the panel's) brand in the state
# under this name. Data, not an `if`: any brand's rule names its own brand the same way.
RULE_BRAND_FIELD = "make"


def _rule_dicts_for_typology(db, typology: str) -> list[dict]:
    """Typology-wide rules only (specsheet_id IS NULL) - used by the Add Using
    Configurator path, where no single product has been chosen yet, so a rule scoped to
    one product must not narrow options for the whole typology."""
    rules = (
        db.query(models.ConfigRule)
        .filter(models.ConfigRule.typology == typology, models.ConfigRule.specsheet_id.is_(None))
        .all()
    )
    return [
        {"triggers": r.triggers, "target_fields": r.target_fields, "action": r.action, "affected_options": r.affected_options}
        for r in rules
    ]


def _rule_dicts_for_specsheet(db, specsheet) -> list[dict]:
    """This product's applicable rules: its typology's own, plus any scoped to just it
    (see ConfigRule.specsheet_id - a constraint like "this optic draws exactly this
    wattage" is only true of one product)."""
    rules = (
        db.query(models.ConfigRule)
        .filter(
            models.ConfigRule.typology == specsheet.typology,
            (models.ConfigRule.specsheet_id.is_(None)) | (models.ConfigRule.specsheet_id == specsheet.id),
        )
        .all()
    )
    return [
        {"triggers": r.triggers, "target_fields": r.target_fields, "action": r.action, "affected_options": r.affected_options}
        for r in rules
    ]


@app.post("/api/specsheets/{sheet_id}/resolve-options")
def resolve_options(sheet_id: int, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """Given the user's in-progress selections while adding this product to a BOQ,
    returns which fields should be hidden and which options within each visible
    field should be shown/enabled, per the configuration rules for this
    specsheet's typology."""
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    state = payload.get("state", {})
    # The ten shared details, plus any other NPD decided this typology asks (a Reflector finish).
    decided = configurator.for_product(db, s) or []
    order = configurator.precedence([f["key"] for f in decided])
    # Its choices, or the one value it states (config_rules.stated_values) - so a product picked
    # in Configure carries its single CRI onto the BOQ row exactly as one picked in Search does.
    base_options = {f: stated_values(s.options, s.fields, f) for f in order}
    # Its brand, which a typology-wide rule names ("every InHouse Flexible Strip Light"): typology
    # names are shared across brands, and one brand's rule must not narrow another's products.
    state = dict(state or {}, **{RULE_BRAND_FIELD: s.make or ""})
    return resolve_fields(_rule_dicts_for_specsheet(db, s), base_options, state, order)


@app.post("/api/typologies/resolve-options")
def resolve_typology_options(request: Request, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """The 'Add Using Configurator' path: same rule engine, but scoped to every
    specsheet classified under a typology, or filed under that kind by its maker
    (typology_match.category_kind), rather than one specific product, so a
    user can filter by desired spec (finish/CCT/beam/...) before knowing which
    product to pick. Returns the same shape as resolve-options for each field,
    PLUS `matching_specsheets` - which products under this typology actually have
    every currently-chosen value (not just "some product here has it somewhere,"
    which is all the per-field union options guarantee).

    typology is passed in the JSON body, not the URL path - some real
    classification strings contain '/' (e.g. "Wall Grazer / Wall Washer"), which
    breaks path-based routing even when percent-encoded (ASGI decodes %2F to a
    literal '/' before the router matches it).

    Optional `series`/`family`/`brand` in the body narrow the pool further (e.g. "NYX"
    typology has one "Downlight" typology-mate for every size; picking a series
    first, then a family, gets to a specific size before spec fields even apply) -
    same 404-on-empty-typology contract, but series/family/brand narrowing an existing
    typology to zero matches is a normal, valid state (not an error), so that
    case just returns empty results rather than raising. `brand` (2026-08-19) matters
    once a typology has products from more than one manufacturer under it - without
    it, e.g. picking "Downlight" mixes InHouse and DuraGreen products' field options
    together, which is confusing when the user already knows which brand they want."""
    typology = payload.get("typology", "")
    if not isinstance(typology, str):
        raise HTTPException(status_code=404, detail="no specsheets found for that typology")
    # What every other picker offers (GET /api/specsheets): approved products of this tool. A
    # product still awaiting approval in NPD, or put on hold, is not to be quoted - and until 21
    # Oct this panel offered them anyway, and products of the other tool too.
    #
    # Under its typology, or the kind its maker files it under: "75 Wall Wash" is a Wall Grazer here
    # and "Downlights - MOOD ROUND" to DuraGreen, and a designer picking Downlight never saw it. Not
    # for a driver, which no product picker offers, and not for "Unclassified", which is no kind.
    # Decided on the three small columns first, because a category's kind is not something SQL can read.
    S = models.Specsheet
    wanted_kind = typology_key(typology)
    ids = [r.id for r in db.query(S.id, S.typology, S.category)
           .filter(S.approval_status == "approved", S.discipline == current_discipline(request))
           if r.typology == typology or (wanted_kind and r.typology != "Driver" and category_kind(r.category) == wanted_kind)]
    # Without the two deferred columns nothing here reads - several KB of PDF text and parameters
    # per row, on every product of the typology (242 for Downlight), for every pick in the panel.
    # Ordered: the order rows come back in is the order options are offered in, and a rewrite of the
    # rows, by a save or a backfill, must not reshuffle the lists people know.
    sheets = (db.query(S).options(defer(S.raw_text), defer(S.params)).filter(S.id.in_(ids)).order_by(S.id).all()
              if ids else [])
    if not sheets:
        raise HTTPException(status_code=404, detail=f"no specsheets found for typology {typology!r}")

    series = payload.get("series", "")
    family = payload.get("family", "")
    # A make that sells one product in several bodies gives each its own row, distinguished by
    # variant - Exenia's round/square and trim/trimless. Narrowing to one is how you specify
    # the fixture that fits the ceiling, not just the family it belongs to.
    variant = payload.get("variant", "")
    brand = payload.get("brand", "")
    if series:
        sheets = [s for s in sheets if s.series == series]
    if family:
        sheets = [s for s in sheets if s.family == family]
    if variant:
        sheets = [s for s in sheets if (s.variant or "") == variant]
    if brand:
        sheets = [s for s in sheets if s.make == brand]
    if not sheets:
        raise HTTPException(status_code=404, detail="no specsheets match that selection")

    state = dict(payload.get("state") or {})

    # Which details are asked, in which order and under which names: as NPD decided for this
    # brand's typology (app/configurator.py), or - where nobody has - the ten shared ones.
    decided = configurator.for_typology(db, current_discipline(request), brand, typology) if brand else None
    labels = field_labels_for(brand) if brand else dict(FIELD_LABELS)
    asked = ([{"key": f["key"], "label": f["label"]} for f in decided] if decided
             else [{"key": f, "label": labels.get(f, f)} for f in FIELD_ORDER])
    if not decided:
        asked += duragreen.configure_extras(sheets, {f["key"] for f in asked} | set(configurator.NOT_A_CHOICE))
    order = configurator.precedence([f["key"] for f in asked])

    # union of the values per field across every product in the selection, order-preserving -
    # each product's choices, or the one value it states (config_rules.stated_values)
    base_options = {f: [] for f in order}
    for f in order:
        seen = set()
        for s in sheets:
            for o in stated_values(s.options, s.fields, f):
                key = o.strip().lower()
                if key not in seen:
                    seen.add(key)
                    base_options[f].append(o)

    # A saved line's older spelling, read as what the product now lists; the page takes these back.
    state_read = duragreen.current_spellings(state, base_options)
    state.update(state_read)
    # The brand chosen in the panel, for rules that name one; with none chosen such a rule does not apply.
    rule_state = dict(state, **({RULE_BRAND_FIELD: str(payload.get("brand"))} if payload.get("brand") else {}))
    result = resolve_fields(_rule_dicts_for_typology(db, typology), base_options, rule_state, order)
    result["state_read"] = state_read
    # A field no product in the selection has - Length on a downlight - is not asked. The
    # products are defined in NPD, so a value missing from every one of them is not a gap in
    # reading a PDF to be typed in here; it is not a property of these products, and a free-text
    # box saying "Not parsed from any specsheet" only made people wonder what they had missed.
    # Nor is one NPD decided this typology does not show.
    asked_keys = {f["key"] for f in asked}
    for f in order:
        if not base_options[f] or f not in asked_keys:
            result[f] = {"hidden": True, "no_values": True, "visible_options": [], "enabled_options": [], "value": ""}

    def _specsheet_matches(sheet) -> bool:
        for field, val in state.items():
            if not val:
                continue  # blank = no constraint on this field
            if field not in asked_keys:
                continue  # only what the panel asks narrows the matches - never a value nobody can see
            field_opts = stated_values(sheet.options, sheet.fields, field)
            if not field_opts:
                # this specific product has no enumerated options for this field either (it's a
                # free-text fallback in the UI, not a controlled vocabulary) - nothing to match
                # against, so a typed note here must never be treated as a filter that excludes
                # every product under the typology
                continue
            if val.strip().lower() not in {o.strip().lower() for o in field_opts}:
                return False
        return True

    result["matching_specsheets"] = [specsheet_summary(s) for s in sheets if _specsheet_matches(s)]
    # What this make calls each shared axis. Exenia asks for an "Optic" and Lumenpulse for
    # "Optics (Nominal Distribution)", both meaning beam_angle; a panel filtered to one make
    # should read in that make's words. With no make chosen the panel spans every brand, so
    # the shared names are the only honest ones. Names NPD gave win over both.
    result["field_labels"] = dict(labels, **{f["key"]: f["label"] for f in asked})
    # The questions, in the order to ask them - the panel draws its fields from this list.
    result["fields"] = asked
    result["decided_in_npd"] = bool(decided)
    # Plain JSON already - JSONResponse skips FastAPI's walk over it (18ms against json's 1ms).
    return JSONResponse(result)


# ---------- drivers ----------
#
# The "Add Driver" mini-configurator is a fixed, small rule set (Voltage Type -> Control
# Type -> IP -> Wattage - see DriverRules.md), not a catalog of individual SKUs to browse,
# so as of 2026-08-06 it's implemented entirely client-side (DRIVER_RULES in
# static/app.js) with no backend endpoint or Specsheet lookup at all - there's nothing
# here to query. The driver Specsheet rows imported by import_drivers.py still exist in
# the database (untouched) but are unused by this flow; they remain excluded from
# GET /api/specsheets above so they don't clutter the general product picker.


# ---------- configuration rules (typology configurator, ported from Configure.gs) ----------

def config_rule_dict(r: models.ConfigRule) -> dict:
    return {
        "id": r.id,
        "typology": r.typology,
        "specsheet_id": r.specsheet_id,
        "triggers": r.triggers,
        "target_fields": r.target_fields,
        "action": r.action,
        "affected_options": r.affected_options,
    }


@app.get("/api/config-fields")
def config_fields():
    """The fixed variant field set + display labels the rule builder UI works with."""
    return [{"key": f, "label": FIELD_LABELS[f]} for f in FIELD_ORDER]


def _clean_rule(payload: dict, partial: bool = False) -> dict:
    """A rule as the engine reads it, from what a person sent: up to five conditions, each a field
    and the values that set it off; an action; the fields it acts on and the values it names.
    Anything malformed is refused with a sentence, never stored half-right."""
    out = {}
    if not partial or "action" in payload:
        action = payload.get("action")
        if action not in ALL_ACTIONS:
            raise HTTPException(status_code=400, detail=f"action must be one of {sorted(ALL_ACTIONS)}")
        out["action"] = action
    if not partial or "triggers" in payload:
        triggers = []
        raw_triggers = payload.get("triggers")
        if not isinstance(raw_triggers, list):
            raise HTTPException(status_code=400, detail="at least one trigger condition is required")
        for t in raw_triggers:
            field = str(t.get("field") or "").strip() if isinstance(t, dict) else ""
            values = [str(v).strip() for v in t.get("values") if str(v).strip()] \
                if isinstance(t, dict) and isinstance(t.get("values"), list) else []
            if not field or not values:
                raise HTTPException(status_code=400, detail="every condition needs a field and at least one value")
            triggers.append({"field": field, "values": list(dict.fromkeys(values))})
        if not triggers:
            raise HTTPException(status_code=400, detail="at least one trigger condition is required")
        if len(triggers) > 5:
            raise HTTPException(status_code=400, detail="a rule takes at most five conditions")
        out["triggers"] = triggers
    if not partial or "target_fields" in payload:
        targets = [str(f).strip() for f in (payload.get("target_fields") or []) if str(f).strip()] \
            if isinstance(payload.get("target_fields"), list) else []
        if not targets:
            raise HTTPException(status_code=400, detail="at least one target field is required")
        out["target_fields"] = list(dict.fromkeys(targets))
    if not partial or "affected_options" in payload:
        raw = payload.get("affected_options", [])
        if not isinstance(raw, list):
            raise HTTPException(status_code=400, detail="affected_options must be a list")
        out["affected_options"] = list(dict.fromkeys(str(v).strip() for v in raw if str(v).strip()))
    if not partial or "typology" in payload:
        out["typology"] = str(payload.get("typology") or "").strip()
    if "specsheet_id" in payload:
        sid = payload.get("specsheet_id")
        try:
            out["specsheet_id"] = int(sid) if sid not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="specsheet_id must be a product id")
    return out


def _check_rule_names_options(action, affected_options):
    """A Show/Hide/Enable/Disable Only rule with nothing named would hide or keep nothing - for
    "Show Only", hide every option there is. Refused rather than stored."""
    if action in OPTION_VIS_ACTIONS_ALL and not affected_options:
        raise HTTPException(status_code=400, detail="name at least one value the rule hides or offers")


@app.get("/api/config-rules")
def list_config_rules(typology: str | None = None, specsheet_id: int | None = None, db: Session = Depends(get_db)):
    """Every rule, or one typology's, or - with specsheet_id - the rules that apply to one product:
    its typology's and its own (NPD > Rules)."""
    q = db.query(models.ConfigRule)
    if specsheet_id is not None:
        s = get_or_404(db, models.Specsheet, specsheet_id, "specsheet")
        q = q.filter(models.ConfigRule.typology == (s.typology or ""),
                     (models.ConfigRule.specsheet_id.is_(None)) | (models.ConfigRule.specsheet_id == s.id))
    elif typology:
        q = q.filter(models.ConfigRule.typology == typology)
    # JSONResponse, not a bare list, for the reason list_specsheets() documents at length:
    # config_rule_dict() is already plain str/int/list/dict, so FastAPI's jsonable_encoder
    # walk over 1,209 rules is pure overhead - measured at 41ms against json.dumps' 5ms.
    return JSONResponse([config_rule_dict(r) for r in q.order_by(models.ConfigRule.id).all()])


@app.post("/api/config-rules")
def create_config_rule(payload: dict = Body(...), db: Session = Depends(get_db)):
    rule = _clean_rule(payload)
    if not rule["typology"]:
        raise HTTPException(status_code=400, detail="a rule needs the typology it belongs to")
    if rule.get("specsheet_id") is not None:
        get_or_404(db, models.Specsheet, rule["specsheet_id"], "specsheet")
    _check_rule_names_options(rule["action"], rule["affected_options"])
    r = models.ConfigRule(**rule)
    db.add(r)
    db.commit()
    db.refresh(r)
    return config_rule_dict(r)


@app.put("/api/config-rules/{rule_id}")
def update_config_rule(rule_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    r = get_or_404(db, models.ConfigRule, rule_id, "config rule")
    rule = _clean_rule(payload, partial=True)
    if "typology" in rule and not rule["typology"]:
        raise HTTPException(status_code=400, detail="a rule needs the typology it belongs to")
    if rule.get("specsheet_id") is not None:
        get_or_404(db, models.Specsheet, rule["specsheet_id"], "specsheet")
    _check_rule_names_options(rule.get("action", r.action), rule.get("affected_options", r.affected_options))
    for field, value in rule.items():
        setattr(r, field, value)
    db.commit()
    return config_rule_dict(r)


@app.delete("/api/config-rules/{rule_id}")
def delete_config_rule(rule_id: int, db: Session = Depends(get_db)):
    r = get_or_404(db, models.ConfigRule, rule_id, "config rule")
    db.delete(r)
    db.commit()
    return {"ok": True}


# ---------- product-code short-codes (Configure.gs's "Product Code" sheet, ported) ----------

def field_code_dict(r: models.FieldCode) -> dict:
    return {"id": r.id, "field": r.field, "option_value": r.option_value, "code": r.code}


@app.get("/api/field-codes")
def list_field_codes(db: Session = Depends(get_db)):
    """Every defined short-code, across every field - the JS Add-Product modal
    fetches this once and looks codes up client-side (mirrors Configure.gs's own
    generateProductCode, which is entirely client-side against a preloaded
    optionsMapping)."""
    return [field_code_dict(r) for r in db.query(models.FieldCode).order_by(models.FieldCode.field, models.FieldCode.option_value).all()]


@app.get("/api/field-codes/option-values")
def field_code_option_values(db: Session = Depends(get_db)):
    """Every distinct option value seen anywhere in the library, grouped by
    field - lets the Product Codes admin panel list "what needs a code" without
    the user having to already know every value that appears across every
    specsheet/typology."""
    result = {f: [] for f in FIELD_ORDER}
    for f in FIELD_ORDER:
        seen = set()
        for (opts,) in db.query(models.Specsheet.options).all():
            for o in (opts or {}).get(f, []):
                key = o.strip().lower()
                if o.strip() and key not in seen:
                    seen.add(key)
                    result[f].append(o.strip())
        result[f].sort(key=str.lower)
    return result


@app.post("/api/field-codes")
def upsert_field_code(payload: dict = Body(...), db: Session = Depends(get_db), _can=Depends(require_config_edit)):
    """Create or update the code for one (field, option_value) pair - one row per
    pair, so re-saving a value already in the table updates it in place instead
    of creating a duplicate."""
    field = payload.get("field", "")
    option_value = (payload.get("option_value") or "").strip()
    code = (payload.get("code") or "").strip()
    if field not in FIELD_ORDER:
        raise HTTPException(status_code=400, detail=f"field must be one of {FIELD_ORDER}")
    if not option_value:
        raise HTTPException(status_code=400, detail="option_value is required")
    r = db.query(models.FieldCode).filter(
        models.FieldCode.field == field, models.FieldCode.option_value == option_value
    ).first()
    if r:
        r.code = code
    else:
        r = models.FieldCode(field=field, option_value=option_value, code=code)
        db.add(r)
    db.commit()
    db.refresh(r)
    return field_code_dict(r)


@app.delete("/api/field-codes/{code_id}")
def delete_field_code(code_id: int, db: Session = Depends(get_db), _can=Depends(require_config_edit)):
    r = get_or_404(db, models.FieldCode, code_id, "field code")
    db.delete(r)
    db.commit()
    return {"ok": True}


# ---------- suggestion lists (floor/room names, accessory descriptions) ----------

@app.get("/api/floor-names")
def floor_names(db: Session = Depends(get_db)):
    rows = db.query(distinct(models.BOQFloor.name)).all()
    return sorted({r[0] for r in rows if r[0]})


@app.get("/api/room-names")
def room_names(db: Session = Depends(get_db)):
    rows = db.query(distinct(models.BOQRoom.name)).all()
    return sorted({r[0] for r in rows if r[0]})


@app.get("/api/accessory-descriptions")
def accessory_descriptions(db: Session = Depends(get_db)):
    rows = db.query(distinct(models.BOQItem.description)).filter(models.BOQItem.is_accessory == True).all()  # noqa: E712
    return sorted({r[0] for r in rows if r[0]})


def _split_field_options(raw: str) -> list[str]:
    """Whitespace-normalizes and splits one specsheet field's raw multi-option text into a
    clean list, dropping "NIL" (how these PDFs themselves say "not applicable") rather than
    surfacing it as a real option. Shared by /api/accessory-types (library-wide) and
    /api/specsheets/{id}/accessories (one product's own values) so the two can't drift on
    what counts as "a real option" - deliberately does NOT try to merge near-duplicate
    spellings that differ only by a missing space (real artifacts of inconsistent PDF text
    extraction) - see TUTORIAL.md's `_undouble()` case study for why "close enough" merging
    is a data-integrity risk this app has already been burned by once.

    Splits on BOTH "/" and "," because the two catalogues genuinely differ: InHouse PDFs
    use slashes ("NIL / HONEYCOMB / SPREAD LENS"), while DuraGreen's scraped pages use
    commas ("Honeycomb louvre, Solite Lense, Spread Lense, Colour Filter") and 14 rows use
    both at once ("Honeycomb, Elliptical Lens,Solite lens / Water Stopper / IP68
    Connector"). Splitting on "/" alone returned those comma lists as ONE option, so the
    Add-Product accessory picker offered a single unselectable blob instead of four real
    accessories. Verified before widening: no accessory name and no dimension value
    anywhere in the library contains a comma, so nothing that was one option becomes two."""
    out = []
    for part in re.split(r"[/,]", raw or ""):
        cleaned = " ".join(part.split())
        if cleaned and cleaned.upper() != "NIL":
            out.append(cleaned)
    return out


@app.get("/api/accessories/issues")
def accessory_issues(db: Session = Depends(get_db)):
    """What is untidy in the accessory data: one accessory spelled several ways, text the parser
    took for a name, and accessory prices attached to no product. Reported only - each one is a
    judgement about what a manufacturer meant, and scripts/backfills/tidy_accessory_names.py is
    where a person applies the ones they agree with, after a dry run."""
    return JSONResponse(accessories.issues(db))


@app.get("/api/accessories")
def accessory_catalogue(request: Request, full: int = 0, db: Session = Depends(get_db)):
    """Every accessory the library knows, and how many products list each.

    The view from the accessory's side, which no screen had before: "Honeycomb" is listed by 325
    products across three brands. Derived from the specsheets themselves on every request, so it
    cannot disagree with them - see app/accessories.py for why it is not a table of its own.

    `full=1` includes each accessory's product list (635 KB, 31 KB over the wire); without it the
    reply is the names and counts alone, which is what a list screen needs."""
    cat = accessories.build_cached(db, current_discipline(request))
    # Each accessory's own parameters and picture (models.AccessoryDetail) ride along, read fresh:
    # they are a few rows, and the catalogue above is cached and shared, so it is copied, never
    # written into.
    defined = {r.key: accessory_details_dict(r) for r in db.query(models.AccessoryDetail).all()}
    blank = accessory_details_dict(None)

    def entry(a):
        out = {k: v for k, v in a.items() if full or k != "products"}
        mine = defined.get(a["key"], blank)
        out.update(params=mine["params"], image=mine["image"], image_url=mine["image_url"])
        return out
    return JSONResponse({"count": cat["count"], "accessories": [entry(a) for a in cat["accessories"]]})


@app.post("/api/accessories/{key}/rename")
def accessory_rename(key: str, request: Request, payload: dict = Body(...),
                     db: Session = Depends(get_db)):
    """Settle one accessory on one name, everywhere it is recorded.

    `to` that folds to this same key makes a group of spellings consistent (HONEYCOMB and
    Honeycomb become Honeycomb); `to` that folds to a different one merges the two accessories
    into it. Both are the same operation, and both move the product lists, the accessory's price
    entries and its order codes together - see app/accessories.py for why splitting them apart
    would break the data rather than tidy it."""
    try:
        touched = accessories.rename(db, key, payload.get("to"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not touched["products"] and not touched["prices"] and not touched["codes"]:
        raise HTTPException(status_code=400, detail="Nothing to change - that is already its name.")
    was = ", ".join(sorted(touched["was"]))
    # Logged before the commit, so the record is saved with the change. It used to be written
    # after it, and nothing committed it: no rename was ever recorded (found 2026-10-01).
    log_activity(db, request, "accessory.rename",
                 "Renamed accessory %s to '%s'" % (was or key, touched["to"]),
                 details={"from": touched["was"], "to": touched["to"],
                          "products": touched["products"], "prices": touched["prices"],
                          "codes": touched["codes"], "details": touched["details"]})
    db.commit()
    accessories.forget()
    return JSONResponse(touched)


@app.get("/api/accessories/{key}")
def accessory_one(key: str, request: Request, db: Session = Depends(get_db)):
    """One accessory, with every product that lists it, each with its own price and order code."""
    return JSONResponse(_accessory_or_404(request, db, key))


def _accessory_or_404(request, db, key):
    """The catalogue's entry for one accessory, or a 404.

    The cached catalogue can be a minute old, and each worker holds its own. An accessory a
    product was saved with a moment ago is real, so a miss reads the specsheets afresh before
    saying it does not exist."""
    discipline = current_discipline(request)

    def find():
        cat = accessories.build_cached(db, discipline)
        return next((a for a in cat["accessories"] if a["key"] == key), None)
    found = find()
    if not found:
        accessories.forget()
        found = find()
    if not found:
        raise HTTPException(status_code=404, detail="No accessory by that name in the library - "
                            "save the product that lists it first.")
    return found


def _definition_row(db, key, create=False):
    """The by-name AccessoryDetail that holds what is defined for this accessory - made when
    `create` and there is none yet."""
    row = db.get(models.AccessoryDetail, key)
    if row is None and create:
        row = models.AccessoryDetail(key=key, params=[], image="")
        db.add(row)
    return row


def accessory_details_dict(row) -> dict:
    """An accessory's own definition as the screens use it: its parameters, and its picture when
    the file is there (see models.AccessoryDetail and models.AccessoryPart)."""
    image = accessories.picture_file(row.image) if row is not None else ""
    if image and not (IMAGES_DIR / image).exists():
        image = ""
    return {"params": list(row.params or []) if row is not None else [],
            "image": image, "image_url": _image_url(image) if image else None}


# An accessory picture is drawn into a cell a few centimetres wide, so this is plenty, and it
# keeps an 8 MB phone photo from being copied into every export that prints it.
ACCESSORY_PICTURE_MAX_PX = 1200
ACCESSORY_PICTURE_MAX_BYTES = 8 * 1024 * 1024


def _save_accessory_picture(key, source) -> str:
    """Writes `source` (a file-like or a path) as a new PNG for this accessory and returns its file
    name. Opened and re-saved by Pillow, so what is stored is a picture and nothing else - a file
    that only has a picture's extension is refused here. Never overwrites: each picture is a new
    file, because BOQ lines name the file they were added with."""
    from PIL import Image as _PILImage
    try:
        with _PILImage.open(source) as im:
            im.load()
            picture = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="That file could not be read as a picture.")
    picture.thumbnail((ACCESSORY_PICTURE_MAX_PX, ACCESSORY_PICTURE_MAX_PX))
    n = 1
    while (IMAGES_DIR / ("accessory_%s_%d.png" % (key, n))).exists():
        n += 1
    name = "accessory_%s_%d.png" % (key, n)
    picture.save(IMAGES_DIR / name, "PNG", optimize=True)
    return name


@app.get("/api/accessories/{key}/details")
def accessory_details(key: str, request: Request, db: Session = Depends(get_db)):
    """One accessory's own parameters and picture, and what the specsheets listing it already offer
    towards them: the dimension values those datasheets print, each with how many products print
    it, and the profile and accessory pictures extracted from them (pdf_parser).

    The values are suggestions, never a mapping. A strip light's datasheet prints the sizes of all
    its profiles together, so which of them belong to this profile is exactly what a person
    decides here - the reason this definition exists at all."""
    entry = _accessory_or_404(request, db, key)
    out = {"key": key, "name": entry["name"], "count": entry["count"],
           **accessory_details_dict(_definition_row(db, key))}
    ids = [p["id"] for p in entry["products"]]
    sheets = (db.query(models.Specsheet)
              .options(load_only(models.Specsheet.id, models.Specsheet.product_name,
                                 models.Specsheet.fields, models.Specsheet.diagrams))
              .filter(models.Specsheet.id.in_(ids)).all()) if ids else []
    printed = {field: collections.Counter() for field in DIMENSION_FIELDS}
    pictures, seen = [], set()
    for s in sorted(sheets, key=lambda x: x.product_name or ""):
        for field in DIMENSION_FIELDS:
            for value in _split_field_options((s.fields or {}).get(field)):
                printed[field][value] += 1
        for dg in s.diagrams or []:
            path = dg.get("path")
            if dg.get("kind") not in ("profile", "accessory") or not path or not Path(path).exists():
                continue
            digest = hashlib.sha1(Path(path).read_bytes()).hexdigest()
            if digest in seen:            # one drawing printed on several datasheets
                continue
            seen.add(digest)
            pictures.append({"specsheet_id": s.id, "label": dg.get("label") or "",
                             "kind": dg["kind"], "product": s.product_name or "",
                             "url": _image_url(path)})
    out["suggestions"] = [{"field": DIMENSION_LABELS[f],
                           "values": [{"value": v, "products": n} for v, n in c.most_common()]}
                          for f, c in printed.items() if c]
    out["pictures"] = pictures[:48]
    return JSONResponse(out)


@app.put("/api/accessories/{key}/details")
def accessory_details_save(key: str, request: Request, payload: dict = Body(...),
                           db: Session = Depends(get_db)):
    """Saves the parameters an accessory is ordered on, in the order Add Product asks them. A line
    already in a BOQ keeps what it was added with."""
    entry = _accessory_or_404(request, db, key)
    try:
        params = accessories.clean_params(payload.get("params"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    row = _definition_row(db, key, create=True)
    before = list(row.params or [])
    row.name = entry["name"]
    row.params = params
    log_activity(db, request, "accessory.details",
                 "Set the parameters of accessory '%s'" % entry["name"],
                 details={"key": key, "before": before, "after": params})
    db.commit()
    return JSONResponse({"key": key, "name": entry["name"], **accessory_details_dict(row)})


@app.post("/api/accessories/{key}/image")
async def accessory_image_save(key: str, request: Request, db: Session = Depends(get_db)):
    """Gives an accessory its picture: an uploaded file, or one of the pictures extracted from a
    specsheet that lists it ({"from_specsheet": id, "label": ...} - offered by the details call).
    Either way it is copied into a file of the accessory's own, so re-reading that specsheet later
    cannot change or remove it."""
    entry = _accessory_or_404(request, db, key)
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            raise HTTPException(status_code=400, detail="Choose a picture to upload.")
        _picture_suffix(upload.filename)          # PNG, JPG, GIF or WebP, by name first
        data = await upload.read()
        if len(data) > ACCESSORY_PICTURE_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Pictures up to 8 MB.")
        import io
        name = _save_accessory_picture(key, io.BytesIO(data))
        source = {"uploaded": upload.filename}
    else:
        payload = await request.json()
        listing = {p["id"] for p in entry["products"]}
        sheet_id = payload.get("from_specsheet")
        if sheet_id not in listing:
            raise HTTPException(status_code=400, detail="Pick a picture from a product that lists this accessory.")
        sheet = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
        dg = next((d for d in sheet.diagrams or [] if d.get("label") == payload.get("label")
                   and d.get("kind") in ("profile", "accessory")), None)
        if dg is None or not Path(dg.get("path") or "").exists():
            raise HTTPException(status_code=400, detail="That picture is no longer on the specsheet.")
        name = _save_accessory_picture(key, dg["path"])
        source = {"specsheet_id": sheet.id, "label": dg.get("label")}
    row = _definition_row(db, key, create=True)
    previous = row.image or ""
    row.name = entry["name"]
    row.image = name
    log_activity(db, request, "accessory.image", "Set the picture of accessory '%s'" % entry["name"],
                 details={"key": key, "file": name, "replaced": previous, **source})
    db.commit()
    return JSONResponse({"key": key, "name": entry["name"], **accessory_details_dict(row)})


@app.delete("/api/accessories/{key}/image")
def accessory_image_remove(key: str, request: Request, db: Session = Depends(get_db)):
    """Takes the picture off an accessory. The file stays: BOQ lines added with it still print it."""
    entry = _accessory_or_404(request, db, key)
    row = _definition_row(db, key)
    if row is None or not row.image:
        raise HTTPException(status_code=400, detail="This accessory has no picture.")
    previous = row.image
    row.image = ""
    log_activity(db, request, "accessory.image", "Removed the picture of accessory '%s'" % entry["name"],
                 details={"key": key, "removed": previous})
    db.commit()
    return JSONResponse({"key": key, "name": entry["name"], **accessory_details_dict(row)})


# ---------- the InHouse accessory library (app/accessory_parts.py) ----------
#
# Each physical accessory is one record, which products pick from - so one accessory is one
# accessory however it is typed, and two that share a name are variants told apart by their
# specifications. Created and edited from the NPD product form or the library itself.


def _library_part_or_404(db, part_id):
    part = db.get(models.AccessoryPart, part_id)
    if part is None or not accessory_parts.is_part_brand(part.make):
        raise HTTPException(status_code=404, detail="That accessory is no longer in the library - "
                            "reload the page.")
    return part


def _library_dict(entry) -> dict:
    """One library accessory as the screens use it: its picture as a URL, when the file is there."""
    image = accessories.picture_file(entry.get("image"))
    if image and not (IMAGES_DIR / image).exists():
        image = ""
    return {**entry, "image": image, "image_url": _image_url(image) if image else None}


def _library_entry(db, part) -> dict:
    found = next((e for e in accessory_parts.library(db, part.discipline) if e["id"] == part.id), None)
    return _library_dict(found) if found else {"id": part.id}


def _part_error(exc):
    """A PartError as a 400 - or a 409 naming the existing accessory, when that is the problem."""
    if getattr(exc, "twin", None) is not None:
        return HTTPException(status_code=409, detail={"message": str(exc), "twin_id": exc.twin.id})
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/api/accessory-library")
def accessory_library(request: Request, db: Session = Depends(get_db)):
    """Every accessory in the library: its name and specifications, picture, code, price, and the
    products that take it."""
    parts = [_library_dict(e) for e in accessory_parts.library(db, current_discipline(request))]
    return JSONResponse({"brands": list(accessory_parts.PART_BRANDS), "accessories": parts})


@app.post("/api/accessory-library")
def accessory_library_create(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Adds an accessory: {"name", "specs": [{"name", "value"}], "code", "price"}. A name that is
    another spelling of an existing accessory takes its spelling; one with the same name and
    specifications as an existing accessory is refused with a 409 naming that accessory, to be
    used instead. A price needs "Change prices"."""
    price = payload.get("price")
    if price not in (None, "") and not user_can(request, "prices.edit"):
        raise HTTPException(status_code=403, detail="Pricing an accessory needs “Change prices” - "
                            "leave the price empty, and someone who has it can add it.")
    make = (payload.get("make") or accessory_parts.PART_BRANDS[0]).strip()
    try:
        part = accessory_parts.create(db, make, current_discipline(request), payload.get("name"),
                                      payload.get("specs"), payload.get("code") or "")
        if price not in (None, ""):
            accessory_parts.set_price(db, part, price)
    except accessory_parts.PartError as exc:
        db.rollback()
        raise _part_error(exc)
    log_activity(db, request, "accessory.create", "Added accessory '%s' to the library" % part.name,
                 details={"id": part.id, "name": part.name, "specs": accessory_parts.specs_of(part),
                          "code": part.code, "price": price})
    db.commit()
    return JSONResponse(_library_entry(db, part))


@app.put("/api/accessory-library/{part_id}")
def accessory_library_update(part_id: int, request: Request, payload: dict = Body(...),
                             db: Session = Depends(get_db)):
    """Edits an accessory's name, specifications or code ({"name", "specs", "code"}, any of them).
    Every product that takes it shows the change; BOQ lines keep what they were added with."""
    part = _library_part_or_404(db, part_id)
    try:
        change = accessory_parts.update(db, part, name=payload.get("name"), specs=payload.get("specs"),
                                        code=payload.get("code"))
    except accessory_parts.PartError as exc:
        db.rollback()
        raise _part_error(exc)
    if change["before"] != change["after"]:
        log_activity(db, request, "accessory.update", "Edited library accessory '%s'" % part.name,
                     details={"id": part.id, "products": len(part.links), **change})
    db.commit()
    return JSONResponse(_library_entry(db, part))


@app.put("/api/accessory-library/{part_id}/price")
def accessory_library_price(part_id: int, request: Request, payload: dict = Body(...),
                            db: Session = Depends(get_db)):
    """An accessory's price - the one figure for every product that takes it."""
    part = _library_part_or_404(db, part_id)
    try:
        change = accessory_parts.set_price(db, part, payload.get("price"))
    except accessory_parts.PartError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log_activity(db, request, "accessory.price",
                 "Priced library accessory '%s': %s → %s (%d product(s))" % (
                     part.name, "none" if change["before"] is None else "Rs %s" % format(change["before"], ",g"),
                     "Rs %s" % format(change["after"], ",g"), len(part.links)),
                 details={"id": part.id, "products": len(part.links), **change})
    db.commit()
    return JSONResponse(_library_entry(db, part))


@app.get("/api/accessory-library/pictures")
def accessory_library_pictures(request: Request, name: str = "", product_id: int = 0, part_id: int = 0,
                               db: Session = Depends(get_db)):
    """Pictures already in the database that an accessory could use, so a picture somebody has is
    chosen rather than uploaded again:

      * `datasheet` - the profile and accessory drawings extracted from the datasheets of the
        products that list an accessory of this name, take this one (`part_id`), or are the
        product it is being added from (`product_id`); one of each drawing, however many sheets
        print it.
      * `library` - the pictures of other accessories in the library, this one's variants first.

    Choosing one copies it into a file of the accessory's own (POST .../image)."""
    discipline = current_discipline(request)
    key = accessories.fold(name)
    taking = {l.specsheet_id for l in db.query(models.AccessoryLink).filter(
        models.AccessoryLink.part_id == part_id).all()} if part_id else set()
    sheets = (db.query(models.Specsheet)
              .options(load_only(models.Specsheet.id, models.Specsheet.product_name, models.Specsheet.make,
                                 models.Specsheet.options, models.Specsheet.fields, models.Specsheet.diagrams))
              .filter(models.Specsheet.discipline == discipline).all())
    relevant = [s for s in sheets if accessory_parts.is_part_brand(s.make) and (
        s.id == product_id or s.id in taking or
        (key and any(accessories.fold(n) == key for n in accessories._names_of(s))))]
    datasheet, seen = [], set()
    for s in sorted(relevant, key=lambda x: (x.id != product_id, x.product_name or "")):
        for dg in s.diagrams or []:
            path = dg.get("path")
            if dg.get("kind") not in ("profile", "accessory") or not path or not Path(path).exists():
                continue
            digest = hashlib.sha1(Path(path).read_bytes()).hexdigest()
            if digest in seen:            # one drawing printed on several datasheets
                continue
            seen.add(digest)
            datasheet.append({"specsheet_id": s.id, "label": dg.get("label") or "", "kind": dg["kind"],
                              "product": s.product_name or "", "url": _image_url(path)})
    library = []
    for e in accessory_parts.library(db, discipline):
        entry = _library_dict(e)
        if e["id"] == part_id or not entry["image_url"]:
            continue
        library.append({"part_id": e["id"], "name": e["name"], "variant": e["group"] == key,
                        "specs": ", ".join(s["value"] for s in e["specs"]), "url": entry["image_url"]})
    library.sort(key=lambda x: (not x["variant"], x["name"]))
    return JSONResponse({"datasheet": datasheet[:60], "library": library[:60]})


@app.post("/api/accessory-library/{part_id}/image")
async def accessory_library_image(part_id: int, request: Request, db: Session = Depends(get_db)):
    """Gives an accessory its picture: an uploaded file, or one already in the database -
    {"from_specsheet": id, "label": ...} for a drawing extracted from a datasheet, {"from_part": id}
    for another library accessory's (both offered by GET /api/accessory-library/pictures). Either
    way it is copied into a file of the accessory's own, a new one each time: BOQ lines keep the
    name they were added with, and the source changing later cannot change this one."""
    part = _library_part_or_404(db, part_id)
    key = "part-%d" % part.id
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            raise HTTPException(status_code=400, detail="Choose a picture to upload.")
        _picture_suffix(upload.filename)          # PNG, JPG, GIF or WebP, by name first
        data = await upload.read()
        if len(data) > ACCESSORY_PICTURE_MAX_BYTES:
            raise HTTPException(status_code=400, detail="Pictures up to 8 MB.")
        import io
        name = _save_accessory_picture(key, io.BytesIO(data))
        source = {"uploaded": upload.filename}
    else:
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Upload a picture, or choose one already in the library.")
        if body.get("from_part"):
            other = db.get(models.AccessoryPart, int(body["from_part"]))
            file = accessories.picture_file(other.image) if other is not None and accessory_parts.is_part_brand(other.make) else ""
            if not file or not (IMAGES_DIR / file).exists():
                raise HTTPException(status_code=400, detail="That accessory has no picture any more.")
            name = _save_accessory_picture(key, IMAGES_DIR / file)
            source = {"from_part": other.id}
        elif body.get("from_specsheet"):
            sheet = get_or_404(db, models.Specsheet, int(body["from_specsheet"]), "specsheet")
            dg = next((d for d in sheet.diagrams or [] if d.get("label") == body.get("label")
                       and d.get("kind") in ("profile", "accessory")), None)
            if not accessory_parts.is_part_brand(sheet.make) or dg is None or not Path(dg.get("path") or "").exists():
                raise HTTPException(status_code=400, detail="That picture is no longer on the datasheet.")
            name = _save_accessory_picture(key, dg["path"])
            source = {"from_specsheet": sheet.id, "label": dg.get("label")}
        else:
            raise HTTPException(status_code=400, detail="Upload a picture, or choose one already in the library.")
    previous = part.image or ""
    part.image = name
    log_activity(db, request, "accessory.image", "Set the picture of library accessory '%s'" % part.name,
                 details={"id": part.id, "file": name, "replaced": previous, **source})
    db.commit()
    return JSONResponse(_library_entry(db, part))


@app.delete("/api/accessory-library/{part_id}/image")
def accessory_library_image_remove(part_id: int, request: Request, db: Session = Depends(get_db)):
    """Takes the picture off an accessory. The file stays: BOQ lines added with it still print it."""
    part = _library_part_or_404(db, part_id)
    if not part.image:
        raise HTTPException(status_code=400, detail="This accessory has no picture.")
    previous, part.image = part.image, ""
    log_activity(db, request, "accessory.image", "Removed the picture of library accessory '%s'" % part.name,
                 details={"id": part.id, "removed": previous})
    db.commit()
    return JSONResponse(_library_entry(db, part))


@app.delete("/api/accessory-library/{part_id}")
def accessory_library_delete(part_id: int, request: Request, everywhere: bool = False, db: Session = Depends(get_db)):
    """Deletes an accessory from the library. Its price goes back to List Pricing's waiting list.
    With ?everywhere=true it is also taken off every product that takes it (their accessory lists
    and printed lines); BOQ lines already made keep it. Each product's list from before is in the
    activity log, so the removal can be undone by hand."""
    part = _library_part_or_404(db, part_id)
    gone = {"id": part.id, "name": part.name, "specs": accessory_parts.specs_of(part), "code": part.code or ""}
    try:
        done = accessory_parts.delete(db, part, everywhere=everywhere)
    except accessory_parts.PartError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    n = len(done.get("products") or [])
    log_activity(db, request, "accessory.delete", "Deleted library accessory '%s'%s" % (
        gone["name"] + (" (%s)" % gone["code"] if gone["code"] else ""),
        " and took it off %d product%s" % (n, "" if n == 1 else "s") if n else ""),
                 details={**gone, **done})
    db.commit()
    return JSONResponse({"deleted": gone, **done})


@app.get("/api/accessory-types")
def accessory_types(db: Session = Depends(get_db)):
    """Real accessory option values pulled straight out of the specsheet library's own
    parsed `fields.accessories` (e.g. a downlight's "Accessories: HONEYCOMB / SPREAD LENS /
    SOFTENING LENS" row) across every specsheet in the library - not a hand-typed list, so
    it grows on its own as more specsheets get uploaded, the same way the Library's Series/
    Family filters do. Used by the standalone "+ Accessory Row" Configurator; see
    specsheet_accessories() below for the per-product scoped version "Add Product" uses."""
    seen = set()
    for (fields,) in db.query(models.Specsheet.fields).all():
        seen.update(_split_field_options((fields or {}).get("accessories")))
    return sorted(seen, key=str.upper)


# Field keys this app has actually seen carry a real "/"-separated dimension/size option
# list on a real specsheet (see TUTORIAL.md) - e.g. a strip light's profile comes in more
# than one cross-section. Not in pdf_parser.py's VARIANT_FIELDS (those drive the main
# Configurator's finish/wattage/etc. pickers), so `options` is never pre-split for these;
# split on demand here instead, same as accessories above.
DIMENSION_FIELDS = ["profile_dimension", "cutout_dimensions", "luminaire_length"]
DIMENSION_LABELS = {"profile_dimension": "Profile Dimension", "cutout_dimensions": "Cutout Dimensions", "luminaire_length": "Luminaire Length"}


def _accessories_ruled_out(db, s, accessory_types, state_json):
    """The accessories a rule takes away for the choices made so far ("when IP66, hide the IP68
    connector"): the same engine and precedence as resolve-options, with Accessories as one more
    field a rule can act on. [] when nothing is chosen, nothing is ruled out, or the state is not
    JSON - the list is then offered whole, as before rules could name accessories."""
    if not accessory_types or not state_json:
        return []
    import json
    try:
        state = json.loads(state_json)
    except ValueError:
        return []
    if not isinstance(state, dict):
        return []
    rules = [r for r in _rule_dicts_for_specsheet(db, s) if "accessories" in (r.get("target_fields") or [])]
    if not rules:
        return []
    decided = configurator.for_product(db, s) or []
    # The brand is read from the state, never settled as a field: the engine resets every field in its
    # order to one of the product's options, and a product has no "make" options.
    order = configurator.precedence([f["key"] for f in decided] + [t["field"] for r in rules for t in r["triggers"]
                                                                    if t["field"] != RULE_BRAND_FIELD])
    # Every field with the product's own choices, as resolve-options passes them: the engine settles each
    # field to a value among its options, so a field it was not given would lose the choice that a
    # rule's condition reads.
    base_options = {f: stated_values(s.options, s.fields, f) for f in order}
    base_options["accessories"] = list(accessory_types)
    rule_state = dict({k: str(v) for k, v in state.items() if v}, **{RULE_BRAND_FIELD: s.make or ""})
    result = resolve_fields(rules, base_options, rule_state, order + ["accessories"])
    visible = {str(v).strip().lower() for v in (result.get("accessories") or {}).get("visible_options", accessory_types)}
    return [a for a in accessory_types if str(a).strip().lower() not in visible]


@app.get("/api/specsheets/{sheet_id}/accessories")
def specsheet_accessories(sheet_id: int, state: str | None = None, only: str | None = None, db: Session = Depends(get_db)):
    """Accessory types and dimension options parsed from THIS ONE specsheet's own data -
    the scoped counterpart to /api/accessory-types (library-wide): when adding a specific
    product, only the accessories that actually appear on *that product's own datasheet*
    make sense to offer alongside it, not every accessory type any product in the library
    has ever listed."""
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    fields = s.fields or {}
    # `options` already holds the accessories as a list, so it is used whenever the row has
    # one. _split_field_options() breaks a string on commas as well as slashes, which was
    # safe while no accessory name contained one - Exenia's do: their "Connector 3x1,5mmq -
    # 2 outputs IP68" is a decimal comma, and splitting there offered two accessories that
    # do not exist, "Connector 3x1" and "5mmq - 2 outputs IP68".
    accessory_types = [a for a in (s.options or {}).get("accessories") or [] if str(a).strip()]
    if not accessory_types:
        accessory_types = _split_field_options(fields.get("accessories"))
    hidden_accessories = _accessories_ruled_out(db, s, accessory_types, state)
    has_accessory_rules = bool(accessory_types) and any(
        "accessories" in (r.get("target_fields") or []) for r in _rule_dicts_for_specsheet(db, s))
    # Add Product asks again on every change of choice; it needs only this, not the prices and details.
    if only == "hidden":
        return {"hidden_accessories": hidden_accessories, "has_accessory_rules": has_accessory_rules}
    dimensions = []
    for key in DIMENSION_FIELDS:
        options = _split_field_options(fields.get(key))
        if options:
            dimensions.append({"field": key, "label": DIMENSION_LABELS[key], "options": options})
    # PriceEntry rows scoped to THIS specsheet whose config carries an "accessory" key -
    # the same accessory costs a different amount under different parent products (verified
    # directly: Exterior Lighting.xlsx's "Spike" is Rs N under one Spot Light, Rs N
    # under another), so this is never looked up globally by accessory name alone. The
    # frontend picks the best match the same way the main Configurator's resolveListPrice()
    # does (most config keys matching wins - e.g. a "Power supply" accessory keyed on both
    # accessory+control needs the control the user picks to resolve to the right one of
    # several real prices, not just any one of them).
    #
    # A InHouse product's library accessories (app/accessory_parts.py) are priced by the library
    # instead: one figure for every product that takes that accessory. Their rows are handed over
    # under the name this product lists them by, so the picker matches them as it matches its own.
    library = accessory_parts.is_part_brand(s.make)
    in_library = accessory_parts.labels_of(db, s.id) if library else set()
    accessory_prices = [
        price_entry_dict(p, include_specsheet=False)
        for p in (s.price_entries or [])
        if "accessory" in (p.config or {})
        and accessories.fold((p.config or {}).get("accessory")) not in in_library
    ]
    for row, label in accessory_parts.prices_for_sheet(db, s) if library else []:
        shared = price_entry_dict(row, include_specsheet=False)
        shared["config"] = {**(row.config or {}), "accessory": label}
        shared["part_id"] = row.accessory_part_id
        accessory_prices.append(shared)
    # An accessory is its own orderable product with its own number - Exenia's Visor is
    # 9G086 - and the BOQ line for it is what that number gets typed from, exactly as for
    # the luminaire. Only ever the manufacturer's own published code, never a derived one -
    # or, for a library accessory, the code it was given there.
    accessory_codes = dict((s.params or {}).get("Accessory codes (Exenia)") or {})
    if library:
        # The library decides, never the name: an accessory picked from it brings its own
        # specifications, picture and code, and a datasheet name not in the library yet brings an
        # explicit nothing - the by-name definition it would otherwise fall back to may be another
        # brand's accessory that happens to share the name.
        accessory_details = {t: {"key": accessories.fold(t), "part_id": None, "params": [], "image": "",
                                 "image_url": None} for t in accessory_types}
        for t, part in accessory_parts.details_for_sheet(db, s).items():
            accessory_details[t] = {**part, **accessory_details_dict(
                SimpleNamespace(params=part["params"], image=part["image"]))}
            if part["code"]:
                accessory_codes[t] = part["code"]
    else:
        # What a person has defined for each of them - the parameters it is ordered on and its
        # picture (models.AccessoryDetail) - by the name this datasheet uses. Add Product asks an
        # accessory that has them its own questions instead of offering every product dimension.
        keys = {t: accessories.fold(t) for t in accessory_types}
        rows = {r.key: r for r in db.query(models.AccessoryDetail)
                .filter(models.AccessoryDetail.key.in_(set(keys.values()))).all()} if keys else {}
        accessory_details = {t: {"key": k, **accessory_details_dict(rows[k])}
                             for t, k in keys.items() if k in rows}
    return {"accessory_types": accessory_types, "hidden_accessories": hidden_accessories,
            "has_accessory_rules": has_accessory_rules, "dimensions": dimensions,
            "accessory_details": accessory_details,
            "accessory_prices": accessory_prices,
            "accessory_codes": {k: v for k, v in accessory_codes.items() if isinstance(v, str)},
            # The same accessories seen from the catalogue's side: what else in the library takes
            # each one. Standing on a product, that is the fact its own datasheet cannot tell you.
            "catalogue": accessories.for_specsheet(db, s)}


# ---------- BOQs ----------

@app.get("/api/notes-template")
def get_notes_template():
    """The default NOTE-page text (terms + closing signature), for the frontend's Edit
    Notes modal to pre-fill/reset with - kept server-side so the large default string
    only lives in excel_export.py, not duplicated into JS."""
    return {"default_text": DEFAULT_NOTES_TEXT}


@app.get("/api/boqs")
def list_boqs(request: Request, db: Session = Depends(get_db)):
    # Branches never appear here as their own row (2026-08-14) - they're reached and managed
    # entirely through their origin's own "Branches" popover instead, so the list itself stays
    # exactly as many rows as there are real, distinct projects, no matter how many branches
    # any one of them has accumulated.
    # boq_summary() computes floor/room/item counts and branch_count by walking
    # b.floors -> f.rooms -> r.items and b.branches - lazy-loaded one query per BOQ,
    # per floor, and per room otherwise (measured: 34 queries for just 5 BOQs, on
    # this very first screen after login). selectinload issues one bulk query per
    # level regardless of row count, not one per BOQ/floor/room.
    # The rows are only counted here, so only their ids are loaded rather than every row of every
    # BOQ in full - descriptions, prices, configurations (78 -> 66 ms on 9 Oct 2026's data).
    boqs = (
        db.query(models.BOQ)
        .filter(models.BOQ.parent_boq_id.is_(None))
        .filter(models.BOQ.discipline == current_discipline(request))
        .options(
            selectinload(models.BOQ.floors).selectinload(models.BOQFloor.rooms).selectinload(models.BOQRoom.items)
            .load_only(models.BOQItem.id, models.BOQItem.room_id),
            selectinload(models.BOQ.branches).selectinload(models.BOQ.created_by),
            selectinload(models.BOQ.created_by),
        )
        .order_by(models.BOQ.updated_at.desc())
        .all()
    )
    return [boq_summary(b) for b in boqs]


@app.get("/api/boqs/{boq_id}/branches")
def list_branches(boq_id: int, db: Session = Depends(get_db)):
    """Just this one BOQ's own branches (id/name/updated_at) - powers the BOQs list's
    "Branches" popover, which needs this without fetching the boq's full floors/rooms/items
    tree (boq_detail) just to show a management list."""
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    return [
        {"id": br.id, "branch_name": br.branch_name, "updated_at": _utc_iso(br.updated_at)}
        for br in sorted(b.branches, key=lambda x: x.updated_at, reverse=True)
    ]


# What the one floor and one area behind a quick BOQ are called. They exist so that products,
# accessories, pricing, copying and every export keep working exactly as they do for a detailed
# BOQ (see BOQ.mode); the quick screens never show either of them.
QUICK_FLOOR_NAME = "Consolidated"
QUICK_ROOM_NAME = "All areas"


def quick_room(db, boq):
    """The area a quick BOQ's products live in, made on first use."""
    for f in boq.floors:
        for r in f.rooms:
            return r
    floor = models.BOQFloor(boq_id=boq.id, name=QUICK_FLOOR_NAME, sort_order=0)
    db.add(floor)
    db.flush()
    room = models.BOQRoom(floor_id=floor.id, name=QUICK_ROOM_NAME, sort_order=0)
    db.add(room)
    db.flush()
    return room


def _boq_of_item(item):
    return item.room.floor.boq


def _clean_id_list(raw, allowed):
    """The ids in `raw` that really are this BOQ's own, in the order given, without repeats -
    so a stale page, or a request naming another project's area, cannot attach one."""
    out = []
    for value in raw or []:
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value in allowed and value not in out:
            out.append(value)
    return out


def _area_ids_of(boq):
    return {a.id for a in boq.areas}


def _ref_image_ids_of(boq):
    return {im.id for im in boq.ref_images}


@app.post("/api/boqs")
def create_boq(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    mode = "quick" if payload.get("mode") == "quick" else "detailed"
    b = models.BOQ(
        mode=mode,
        # Belongs to whichever tool it was created in - never guessed from the payload, which
        # the page could get wrong or a caller could set to the other team's discipline.
        discipline=current_discipline(request),
        project_name=payload.get("project_name", "Untitled Project"),
        project_code=payload.get("project_code", ""),
        area=payload.get("area", ""),
        client=payload.get("client", ""),
        kind_attention=payload.get("kind_attention", ""),
        boq_date=payload.get("boq_date", datetime.date.today().isoformat()),
        created_by_id=request.session.get("user_id"),
    )
    db.add(b)
    db.flush()                         # the record names the BOQ by its id
    if mode == "quick":
        quick_room(db, b)
        # Areas typed on the way in - a person usually knows the areas before the products.
        for order, name in enumerate(payload.get("areas") or []):
            name = (name or "").strip()
            if name:
                db.add(models.BOQArea(boq_id=b.id, name=name, sort_order=order))
        db.flush()
    log_activity(db, request, "boq.create",
                 "Created %s BOQ '%s'" % ("quick" if mode == "quick" else "detailed",
                                           _boq_display_name(b)), boq=b,
                 details={"client": b.client or "", "project_code": b.project_code or "",
                          "mode": mode})
    db.commit()
    db.refresh(b)
    return boq_detail(b)


# ---------- importing a BOQ or a quotation from a spreadsheet (app/boq_import.py) ----------
#
# For a BOQ that went out as an .xlsx, was worked on in Excel, and is to be carried on here. Two
# steps, so nothing is stored until a person has seen it: the preview reads the file and matches
# each line to NPD; the import builds a NEW BOQ from what the person confirmed - an import never
# changes an existing one. Both sit under /api/boqs, so _required_permission asks "boqs.edit" of
# them like every other BOQ write.

def _import_products(db, discipline, ids=None):
    """The products an imported line may be linked to: approved, of this tool, not a driver - the
    same set every other product picker offers."""
    # raw_text (a whole PDF's text) and params are not needed to match or price a line, and every
    # preview loads the whole catalogue - on a server with about a gigabyte, they stay unloaded.
    query = (db.query(models.Specsheet)
             .options(selectinload(models.Specsheet.price_entries),
                      defer(models.Specsheet.raw_text), defer(models.Specsheet.params))
             .filter(models.Specsheet.discipline == discipline,
                     models.Specsheet.approval_status == "approved",
                     models.Specsheet.typology != "Driver"))
    if ids is not None:
        query = query.filter(models.Specsheet.id.in_(ids))
    return query.all()


def _import_npd_price(sheet, line):
    """What NPD prices this line at for the values it states, the way the Configurator would (the
    same choose_options + match_price the Match tab uses), or None for a product NPD does not price."""
    chosen = match_index.choose_options(sheet, boq_import.wants_for(line))
    price, open_choices = npd.match_price(sheet, chosen)
    return price, chosen, open_choices


def _npd_prices(sheet) -> bool:
    """Whether NPD gives this product a price at all (an NPD-authored or DuraGreen product with a
    base price). Any other product's price is set in the BOQ, as it always has been."""
    return bool(npd.priced_by_rule(sheet) and sheet.base_price is not None)


_EXPORT_NAME = re.compile(r"^ILDL_(.+?)_\d{2}-\d{2}-\d{4}_(?:BOQ|Quotation)(?:[ _(].*)?$", re.I)


@app.post("/api/boqs/import/preview")
def import_boq_preview(request: Request, file: UploadFile = File(...), use_ai: bool = Form(False),
                       db: Session = Depends(get_db)):
    """A BOQ or quotation spreadsheet, read into floors, areas and lines, each line matched to NPD.
    Changes nothing: the page shows this for the person to check, then sends back what to create.

    `use_ai` (the person's choice, and only when a model is configured - app/boq_ai.py): a file the
    rules cannot read (a PDF, an unusual layout) is read by the model instead, and a line the rules
    could not link is offered the model's pick from a shortlist. Everything it does is marked for
    checking; nothing it returns is stored by this request."""
    data = file.file.read(boq_import.MAX_BYTES + 1)
    llm_extract.LAST_ERROR = ""
    ai = {**boq_ai.status(), "requested": bool(use_ai), "read": False, "suggested": 0, "error": "", "model": ""}
    use_ai = bool(use_ai) and ai["available"]
    try:
        result = boq_import.read_workbook(file.filename or "", data)
    except SheetError as exc:
        old_excel = (file.filename or "").lower().endswith(".xls")
        if not use_ai or old_excel:
            hint = (" Or tick \"Let the AI model read it\" and try again." if ai["available"] and not old_excel else "")
            raise HTTPException(status_code=400, detail=str(exc) + hint)
        try:
            result = boq_ai.read_with_model(file.filename or "", data)
        except SheetError as again:
            raise HTTPException(status_code=400, detail="%s The AI model could not read it either: %s" % (exc, again))
        ai["read"] = True
    catalogue = boq_import.Catalogue(_import_products(db, current_discipline(request)))
    lines = [line for floor in result["floors"] for area in floor["areas"] for line in area["rows"]]
    for line in lines:
        line["match"] = catalogue.match(line)
    if use_ai:
        ai["suggested"] = boq_ai.suggest_products(catalogue, lines)
    counts = collections.Counter()
    for floor in result["floors"]:
        for area in floor["areas"]:
            for line in area["rows"]:
                found = line["match"]
                counts[found["status"]] += 1
                line["npd_price"] = None
                # For each product the page can offer: does NPD price it? The page says so before
                # the person picks it, rather than a line arriving at 0 unannounced.
                for summary in [found["product"]] + list(found.get("alternatives") or []):
                    if summary:
                        summary["npd_priced"] = _npd_prices(catalogue.sheets[summary["id"]])
                if not found["product"]:
                    continue
                sheet = catalogue.sheets[found["product"]["id"]]
                price, _chosen, open_choices = _import_npd_price(sheet, line)
                line["npd_price"] = price
                if line["price"] is None:
                    if not found["product"]["npd_priced"]:
                        line["notes"].append("NPD has no price for this product - set its price in the BOQ.")
                    elif open_choices:
                        line["notes"].append("Its NPD price also depends on %s, which the file does not say - "
                                             "check the price in the BOQ." % " and ".join(
                                                 sorted(k.replace("_", " ") for k in open_choices)))
    # A file the app exported is named ILDL_<project>_<date>_BOQ.xlsx: its project is the fallback
    # name when the file has no cover sheet.
    stem = re.sub(r"\.[^.]+$", "", os.path.basename(file.filename or ""))
    named = _EXPORT_NAME.match(stem)
    project = result["project"]
    project.setdefault("project_name", (named.group(1).replace("_", " ") if named else stem) or "Imported BOQ")
    result["filename"] = file.filename or ""
    result["counts"] = {"matched": counts["matched"], "check": counts["check"], "none": counts["none"]}
    ai["error"] = llm_extract.last_error() if use_ai else ""
    ai["model"] = llm_extract.LAST_MODEL if use_ai else ""
    ai["requests_today"] = llm_extract.requests_today()
    result["ai"] = ai
    return JSONResponse(result)


def _import_text(value, limit=200):
    return str(value if value is not None else "").strip()[:limit]


@app.post("/api/boqs/import")
def import_boq(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Creates the BOQ a person confirmed on the import preview, in one transaction - all of it or
    none. Every line is checked again here: a product link is kept only for an approved product of
    this tool, whatever the page sent, and prices and discounts go through the same checks as a line
    added by hand. A line with no price of its own is priced from NPD, as the Configurator prices it."""
    floors_in = payload.get("floors")
    if not isinstance(floors_in, list) or not floors_in:
        raise HTTPException(status_code=400, detail="There is nothing to import.")
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    discipline = current_discipline(request)

    lines_in, wanted_ids = 0, set()
    for f in floors_in:
        for a in (f.get("areas") if isinstance(f, dict) and isinstance(f.get("areas"), list) else []):
            for line in (a.get("rows") if isinstance(a, dict) and isinstance(a.get("rows"), list) else []):
                if isinstance(line, dict):
                    lines_in += 1
                    try:
                        if line.get("specsheet_id") is not None:
                            wanted_ids.add(int(line["specsheet_id"]))
                    except (TypeError, ValueError):
                        pass
    if not lines_in:
        raise HTTPException(status_code=400, detail="There are no lines to import.")
    if lines_in > boq_import.MAX_LINES:
        raise HTTPException(status_code=400, detail="Over %d lines - split the file." % boq_import.MAX_LINES)
    if len(floors_in) > 300 or sum(len(f.get("areas") or []) for f in floors_in if isinstance(f, dict)) > 3000:
        raise HTTPException(status_code=400, detail="Too many floors or areas for one BOQ - split the file.")
    sheets = {s.id: s for s in _import_products(db, discipline, wanted_ids)} if wanted_ids else {}
    currency = _import_text(payload.get("currency"), 3).upper()
    from .quotation_export import CURRENCY_PREFIXES

    boq_date = _import_text(project.get("boq_date"), 10)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", boq_date):
        boq_date = datetime.date.today().isoformat()
    b = models.BOQ(
        mode="detailed",
        discipline=discipline,
        project_name=_import_text(project.get("project_name"), 300) or "Imported BOQ",
        project_code=_import_text(project.get("project_code"), 100),
        area=_import_text(project.get("area"), 200),
        client=_import_text(project.get("client"), 300),
        kind_attention=_import_text(project.get("kind_attention"), 200),
        boq_date=boq_date,
        discount_percent=_coerce_discount(payload.get("discount_percent"), allow_blank=False),
        notes_text=_import_text(payload.get("notes_text"), 20000),
        currency=currency if currency in CURRENCY_PREFIXES else "INR",
        created_by_id=request.session.get("user_id"),
    )
    db.add(b)
    db.flush()
    linked = typed = 0
    for f_order, f in enumerate(floors_in):
        if not isinstance(f, dict):
            continue
        floor = models.BOQFloor(boq_id=b.id, name=_import_text(f.get("name"), 200) or "Floor %d" % (f_order + 1),
                                sort_order=f_order)
        db.add(floor)
        db.flush()
        for a_order, a in enumerate(f.get("areas") if isinstance(f.get("areas"), list) else []):
            if not isinstance(a, dict):
                continue
            room = models.BOQRoom(floor_id=floor.id, name=_import_text(a.get("name"), 200) or "Area %d" % (a_order + 1),
                                  remark=_import_text(a.get("remark"), 2000), sort_order=a_order)
            db.add(room)
            db.flush()
            # The product an accessory or driver line belongs to: the product above it in its area,
            # which is where the app puts a product's accessories - so the editor shows them under it.
            last_product = None
            for i_order, line in enumerate(a.get("rows") if isinstance(a.get("rows"), list) else []):
                if not isinstance(line, dict):
                    continue
                try:
                    sheet = sheets.get(int(line.get("specsheet_id"))) if line.get("specsheet_id") is not None else None
                except (TypeError, ValueError):
                    sheet = None
                is_accessory = bool(line.get("is_accessory"))
                if is_accessory:
                    sheet = None              # accessories and drivers are kept as typed
                fields = {k: _import_text(line.get(k), boq_import.FIELD_LIMITS.get(k, 200))
                          for k in boq_import.TEXT_FIELDS}
                chosen = {}
                price = line.get("price")
                if sheet is not None:
                    npd_price, chosen, _open_choices = _import_npd_price(sheet, fields)
                    if price is None or price == "":
                        price = npd_price
                    if not fields["description"]:
                        fields["description"] = suggest_description(sheet.id, dict(chosen), db)["description"]
                    fields["make"] = fields["make"] or (sheet.make or "")
                    linked += 1
                else:
                    typed += 1
                item = models.BOQItem(
                    room_id=room.id, specsheet_id=sheet.id if sheet else None, sort_order=i_order,
                    parent_item_id=last_product.id if (is_accessory and last_product is not None) else None,
                    item_code=fields["item_code"], reference_number=fields["reference_number"],
                    model_no=fields["model_no"], description=fields["description"],
                    dimensions=fields["dimensions"], finish=fields["finish"], wattage=fields["wattage"],
                    beam=fields["beam"], control=fields["control"], led_color=fields["led_color"],
                    config={}, make=fields["make"], qty=fields["qty"] or "1", unit=fields["unit"] or "Nos.",
                    remarks=fields["remarks"], is_accessory=is_accessory,
                    is_driver=bool(line.get("is_driver")) and is_accessory,
                    price=_coerce_price(price), discount_percent=_coerce_discount(line.get("discount_percent")),
                )
                db.add(item)
                if not is_accessory:
                    db.flush()                # its id, for the accessories under it
                    last_product = item
    filename = _import_text(payload.get("filename"), 200)
    log_activity(db, request, "boq.import",
                 "Imported BOQ '%s' from %s - %d lines, %d linked to NPD" % (
                     _boq_display_name(b), filename or "a file", linked + typed, linked),
                 boq=b, details={"filename": filename, "lines": linked + typed, "linked": linked,
                                 "typed": typed, "floors": len(floors_in)})
    db.commit()
    return {"id": b.id, "lines": linked + typed, "linked": linked}


@app.get("/api/boqs/{boq_id}")
def get_boq(boq_id: int, db: Session = Depends(get_db)):
    b = get_boq_or_404(db, boq_id)
    # JSONResponse for the reason list_specsheets() gives: boq_detail() is already plain JSON
    # (dates leave it as strings through _utc_iso), and FastAPI's own walk over it took 34ms of a
    # 117ms open on a 221-line BOQ, where json alone takes 3ms.
    return JSONResponse(boq_detail(b))


@app.put("/api/boqs/{boq_id}")
def update_boq(boq_id: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    b = get_boq_or_404(db, boq_id)
    if "created_by_id" in payload:
        # Who made a BOQ is a fact, not a preference, so it is not freely editable. Anyone who
        # can edit the BOQ may claim one whose maker was never recorded - as themselves, and
        # only then. An admin may set or correct it to any existing account, because an admin
        # is who knows who made the older ones.
        me = request.session.get("user_id")
        try:
            # "me" lets the page claim a BOQ without having to know its own account id.
            wanted = me if payload["created_by_id"] == "me" else int(payload["created_by_id"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="created_by_id must be a person's id")
        if _user_role(me) != "admin":
            if b.created_by_id is not None:
                raise HTTPException(status_code=403, detail="Who made this BOQ is already recorded. "
                                    "Only an admin can change it.")
            if wanted != me:
                raise HTTPException(status_code=403, detail="You can only record yourself as the "
                                    "maker. An admin can record someone else.")
        person = db.get(models.User, wanted)
        if not person:
            raise HTTPException(status_code=400, detail="There is no such person.")
        if b.created_by_id != wanted:
            was = b.created_by.email if b.created_by else None
            b.created_by_id = wanted
            log_activity(db, request, "boq.update",
                         "Recorded %s as the maker of BOQ '%s'%s" % (
                             person.email, _boq_display_name(b),
                             " (was %s)" % was if was else ""),
                         boq=b, details={"created_by": person.email, "was": was})
    for field in ("project_name", "project_code", "area", "client", "kind_attention", "boq_date", "notes_text"):
        if field in payload:
            setattr(b, field, payload[field])
    if "branch_name" in payload:
        # Only meaningful on an actual branch - an original has no branch_name to rename
        # (it's blank by convention, not shown anywhere), so silently ignore this on one
        # rather than let a stray request give a root project a confusing leftover value.
        if b.parent_boq_id:
            name = (payload["branch_name"] or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="branch_name cannot be blank")
            b.branch_name = name
    if "discount_percent" in payload:
        # explicit float() rather than assigning the raw payload value as-is: a Float column
        # can end up with a string bound to it depending on backend/driver (fine for SQLite's
        # dynamic typing, not guaranteed across every Postgres driver path), so coercing here
        # keeps the stored value correct regardless of which database is active.
        b.discount_percent = _coerce_discount(payload["discount_percent"], allow_blank=False)
    if "show_discount" in payload:
        b.show_discount = bool(payload["show_discount"])
    if "currency" in payload:
        if payload["currency"] not in CURRENCY_PREFIXES:
            raise HTTPException(status_code=400, detail=f"currency must be one of {sorted(CURRENCY_PREFIXES)}")
        b.currency = payload["currency"]
    b.updated_at = datetime.datetime.utcnow()
    db.commit()
    return boq_detail(b)


@app.delete("/api/boqs/{boq_id}")
def delete_boq(boq_id: int, request: Request, db: Session = Depends(get_db)):
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    # Written before the delete: afterwards there is no BOQ left to take a name from, and the
    # name of a deleted project is exactly what anybody reading this will want.
    rows = sum(len(r.items) for f in b.floors for r in f.rooms)
    log_activity(db, request, "boq.delete",
                 "Deleted BOQ '%s' (%d floor%s, %d row%s)" % (
                     _boq_display_name(b), len(b.floors), "" if len(b.floors) == 1 else "s",
                     rows, "" if rows == 1 else "s"),
                 boq=b, details={"client": b.client or ""})
    # A quick BOQ's reference pictures are files of its own, uploaded for this project only -
    # the rows go with the BOQ, and without this the files would sit in data/images for good.
    # Noted after a deleted BOQ left one behind on the live server. Product photographs are
    # not touched: those belong to the Library, and other BOQs use them.
    filenames = [im.filename for im in b.ref_images]
    db.delete(b)
    db.commit()
    # After the commit, and never at the cost of the delete itself: the rows are what the app
    # reads, and a file that has already gone is not a failure.
    #
    # Only files nothing else points at. Duplicating or branching a BOQ, and making a detailed
    # BOQ from a quick one, all give the copy its own rows against the same file - so deleting
    # any one of them must leave the picture where it is for the others.
    for filename in filenames:
        still_used = (db.query(models.BOQRefImage)
                      .filter(models.BOQRefImage.filename == filename).first())
        if still_used:
            continue
        try:
            (IMAGES_DIR / filename).unlink(missing_ok=True)
        except OSError:
            pass
    return {"ok": True}


def _deep_copy_boq(db: Session, b: models.BOQ, **overrides) -> models.BOQ:
    """Copies a BOQ's entire tree (floors -> rooms -> items) into a brand-new BOQ row -
    shared by /duplicate and /branch below, which differ only in what they override on the
    new BOQ itself (project_name, parent_boq_id/branch_name) and how the caller responds
    afterward. Item copying happens in two passes so BOQItem.parent_item_id (an accessory's
    link to its product) can be remapped through an old-id -> new-item map once every item
    in the room has its own new id - a real gap the original /duplicate had before this
    existed: a duplicated BOQ's accessories silently lost their grouping under their product."""
    fields = dict(area=b.area, client=b.client, kind_attention=b.kind_attention, boq_date=b.boq_date,
                  notes_text=b.notes_text, discount_percent=b.discount_percent, currency=b.currency,
                  show_discount=b.show_discount,
                  discipline=b.discipline,   # a duplicate or branch stays in its own tool
                  project_code=b.project_code, mode=b.mode or "detailed")
    fields.update(overrides)
    nb = models.BOQ(**fields)
    db.add(nb)
    db.flush()
    # A quick BOQ's areas and reference pictures come across too, and each product's marks are
    # remapped onto the copies - otherwise a duplicate would point at the original's areas.
    area_map, image_map = {}, {}
    for a in b.areas:
        na = models.BOQArea(boq_id=nb.id, name=a.name, sort_order=a.sort_order)
        db.add(na)
        db.flush()
        area_map[a.id] = na.id
    for im in b.ref_images:
        nim = models.BOQRefImage(boq_id=nb.id, filename=im.filename, label=im.label,
                                 sort_order=im.sort_order)
        db.add(nim)
        db.flush()
        image_map[im.id] = nim.id
    for f in b.floors:
        nf = models.BOQFloor(boq_id=nb.id, name=f.name, sort_order=f.sort_order)
        db.add(nf)
        db.flush()
        for r in f.rooms:
            nr = models.BOQRoom(floor_id=nf.id, name=r.name, sort_order=r.sort_order, remark=r.remark)
            db.add(nr)
            db.flush()
            copied = []
            for i in r.items:
                ni = models.BOQItem(
                    room_id=nr.id, specsheet_id=i.specsheet_id, sort_order=i.sort_order,
                    item_code=i.item_code, reference_number=i.reference_number, model_no=i.model_no,
                    description=i.description,
                    dimensions=i.dimensions or "",
                    show_line_diagram=bool(i.show_line_diagram),
                    dimension_image=i.dimension_image or "",
                    finish=i.finish, wattage=i.wattage, beam=i.beam, control=i.control,
                    led_color=i.led_color, make=i.make, qty=i.qty, unit=i.unit,
                    remarks=i.remarks, is_accessory=i.is_accessory, is_driver=i.is_driver, price=i.price,
                    discount_percent=i.discount_percent,
                    config=dict(i.config or {}),
                    area_ids=[area_map[x] for x in (i.area_ids or []) if x in area_map],
                    ref_image_ids=[image_map[x] for x in (i.ref_image_ids or []) if x in image_map],
                )
                db.add(ni)
                copied.append((i, ni))
            db.flush()
            old_to_new_id = {i.id: ni.id for i, ni in copied}
            for i, ni in copied:
                if i.parent_item_id and i.parent_item_id in old_to_new_id:
                    ni.parent_item_id = old_to_new_id[i.parent_item_id]
    return nb


@app.post("/api/boqs/{boq_id}/duplicate")
def duplicate_boq(boq_id: int, request: Request, db: Session = Depends(get_db)):
    b = get_boq_or_404(db, boq_id)
    nb = _deep_copy_boq(db, b, project_name=f"{b.project_name} (copy)",
                        created_by_id=request.session.get("user_id"))
    db.flush()
    log_activity(db, request, "boq.duplicate", "Duplicated BOQ '%s' as '%s'" % (
        _boq_display_name(b), _boq_display_name(nb)), boq=nb, details={"from_boq_id": b.id})
    db.commit()
    # db.refresh(nb) only reloads nb's own columns, not its relationships - fetching it
    # back through get_boq_or_404 is what actually avoids boq_detail() lazy-loading
    # nb's freshly-copied floors/rooms/items one query at a time.
    return boq_detail(get_boq_or_404(db, nb.id))


@app.post("/api/boqs/{boq_id}/branch")
def branch_boq(boq_id: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Creates a branch: a full independent copy of this BOQ, linked back to it via
    parent_boq_id, that can be edited completely separately - the original is never touched
    again by anything that happens on the branch. One-way fork, same as a GitHub branch
    nobody ever merges back - there is no merge operation here, deliberately."""
    b = get_boq_or_404(db, boq_id)
    name = (payload.get("branch_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="branch_name is required")
    nb = _deep_copy_boq(db, b, project_name=b.project_name, parent_boq_id=b.id, branch_name=name,
                        created_by_id=request.session.get("user_id"))
    db.flush()
    log_activity(db, request, "boq.branch", "Branched BOQ '%s' as '%s'" % (
        _boq_display_name(b), name), boq=nb, details={"from_boq_id": b.id})
    db.commit()
    return boq_detail(get_boq_or_404(db, nb.id))


# ---------- a quick BOQ's areas and reference pictures ----------
#
# Both exist to be typed or uploaded once and then marked against as many products as need
# them - the whole point of quick mode. Both are per BOQ: an area called "Lift Lobby" on one
# project has nothing to do with the one on another, and a picture uploaded for this project
# is offered for this project only.


@app.post("/api/boqs/{boq_id}/areas")
def add_boq_area(boq_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    b = get_boq_or_404(db, boq_id)
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Give the area a name")
    existing = next((a for a in b.areas if a.name.lower() == name.lower()), None)
    if existing:
        # Not an error: someone typing an area that is already there means "use that one",
        # and handing it back lets the page mark the product with it straight away.
        return area_dict(existing)
    a = models.BOQArea(boq_id=b.id, name=name,
                       sort_order=max([x.sort_order for x in b.areas], default=-1) + 1)
    db.add(a)
    touch_boq(b)
    db.commit()
    db.refresh(a)
    return area_dict(a)


@app.put("/api/boqs/{boq_id}/areas/{area_id}")
def rename_boq_area(boq_id: int, area_id: int, payload: dict = Body(...),
                    db: Session = Depends(get_db)):
    b = get_boq_or_404(db, boq_id)
    a = next((x for x in b.areas if x.id == area_id), None)
    if not a:
        raise HTTPException(status_code=404, detail="No such area on this BOQ")
    if "name" in payload:
        name = (payload["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Give the area a name")
        # Products hold this area by id, so every one of them follows the new name by itself.
        a.name = name
    if "sort_order" in payload:
        try:
            a.sort_order = int(payload["sort_order"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="sort_order must be a number")
    touch_boq(b)
    db.commit()
    return area_dict(a)


@app.delete("/api/boqs/{boq_id}/areas/{area_id}")
def delete_boq_area(boq_id: int, area_id: int, db: Session = Depends(get_db)):
    b = get_boq_or_404(db, boq_id)
    a = next((x for x in b.areas if x.id == area_id), None)
    if not a:
        raise HTTPException(status_code=404, detail="No such area on this BOQ")
    # Taken off every product that was marked with it, in the same commit - a product left
    # holding a deleted area's id would print a blank where an area name should be.
    touched = 0
    for f in b.floors:
        for r in f.rooms:
            for i in r.items:
                if area_id in (i.area_ids or []):
                    i.area_ids = [x for x in i.area_ids if x != area_id]
                    touched += 1
    db.delete(a)
    touch_boq(b)
    db.commit()
    return {"ok": True, "removed_from_products": touched}


@app.post("/api/boqs/{boq_id}/ref-images")
def add_boq_ref_image(boq_id: int, file: UploadFile = File(...), label: str = Form(""),
                      db: Session = Depends(get_db)):
    """One reference picture for this project, kept for the whole BOQ so it is uploaded once
    and then attached to whichever products it explains."""
    b = get_boq_or_404(db, boq_id)
    suffix = _picture_suffix(file.filename)
    im = models.BOQRefImage(boq_id=b.id, filename="pending", label=(label or "").strip(),
                            sort_order=max([x.sort_order for x in b.ref_images], default=-1) + 1)
    db.add(im)
    db.flush()                     # its id names the file, so no two uploads can collide
    im.filename = "boqref_%d_%d%s" % (b.id, im.id, suffix)
    dest = IMAGES_DIR / im.filename
    with open(dest, "wb") as fh:
        shutil.copyfileobj(file.file, fh)
    touch_boq(b)
    db.commit()
    db.refresh(im)
    return ref_image_dict(im)


@app.put("/api/boqs/{boq_id}/ref-images/{image_id}")
def rename_boq_ref_image(boq_id: int, image_id: int, payload: dict = Body(...),
                         db: Session = Depends(get_db)):
    b = get_boq_or_404(db, boq_id)
    im = next((x for x in b.ref_images if x.id == image_id), None)
    if not im:
        raise HTTPException(status_code=404, detail="No such picture on this BOQ")
    if "label" in payload:
        im.label = (payload["label"] or "").strip()
    touch_boq(b)
    db.commit()
    return ref_image_dict(im)


@app.delete("/api/boqs/{boq_id}/ref-images/{image_id}")
def delete_boq_ref_image(boq_id: int, image_id: int, db: Session = Depends(get_db)):
    b = get_boq_or_404(db, boq_id)
    im = next((x for x in b.ref_images if x.id == image_id), None)
    if not im:
        raise HTTPException(status_code=404, detail="No such picture on this BOQ")
    touched = 0
    for f in b.floors:
        for r in f.rooms:
            for i in r.items:
                if image_id in (i.ref_image_ids or []):
                    i.ref_image_ids = [x for x in i.ref_image_ids if x != image_id]
                    touched += 1
    filename = im.filename
    db.delete(im)
    touch_boq(b)
    db.commit()
    # The row is what the app reads; the file is deleted after the row is safely gone, and a
    # file that has already vanished is not an error worth failing a delete over. A copy of
    # this BOQ - a duplicate, a branch, or the detailed BOQ made from it - has its own row
    # against the same file, so the file only goes once nothing points at it any more.
    if not db.query(models.BOQRefImage).filter(models.BOQRefImage.filename == filename).first():
        try:
            (IMAGES_DIR / filename).unlink(missing_ok=True)
        except OSError:
            pass
    return {"ok": True, "removed_from_products": touched}


@app.post("/api/boqs/{boq_id}/to-detailed")
def quick_boq_to_detailed(boq_id: int, request: Request, payload: dict = Body(default={}),
                          db: Session = Depends(get_db)):
    """Turns a quick BOQ into a detailed one - the step after an order is confirmed.

    Every area becomes a room, and each product is placed into each of the areas it was marked
    for, with its specification, accessories and price carried across. A product marked for one
    area keeps its quantity, since there is nothing to split; one marked for several arrives
    with its quantity blank in each, for the person to fill in as they work through the rooms -
    the alternative, repeating the full quantity in every room, would silently multiply the
    order. The quick BOQ itself is left exactly as it is.
    """
    b = get_boq_or_404(db, boq_id)
    if (b.mode or "detailed") != "quick":
        raise HTTPException(status_code=400, detail="This is already a detailed BOQ.")
    if not b.areas:
        raise HTTPException(status_code=400, detail="Define the areas first - each one becomes "
                            "a room in the detailed BOQ.")

    name = (payload.get("project_name") or "").strip() or "%s (detailed)" % b.project_name
    nb = models.BOQ(
        mode="detailed", discipline=b.discipline, project_name=name, project_code=b.project_code,
        area=b.area, client=b.client, kind_attention=b.kind_attention,
        boq_date=datetime.date.today().isoformat(), notes_text=b.notes_text,
        discount_percent=b.discount_percent, currency=b.currency, show_discount=b.show_discount,
        created_by_id=request.session.get("user_id"))
    db.add(nb)
    db.flush()
    # The reference pictures come across with the project, and each product's marks are
    # remapped onto the copies - a picture explains the fixture, and that does not stop being
    # true once the quantities are split between rooms. The new BOQ gets its own rows pointing
    # at the same files, so re-uploading them is never necessary.
    image_map = {}
    for im in b.ref_images:
        nim = models.BOQRefImage(boq_id=nb.id, filename=im.filename, label=im.label,
                                 sort_order=im.sort_order)
        db.add(nim)
        db.flush()
        image_map[im.id] = nim.id

    floor = models.BOQFloor(boq_id=nb.id, name=(b.area or "").strip() or "Floor 1", sort_order=0)
    db.add(floor)
    db.flush()

    products = [i for f in b.floors for r in f.rooms for i in r.items if not i.parent_item_id]
    accessories = {}
    for f in b.floors:
        for r in f.rooms:
            for i in r.items:
                if i.parent_item_id:
                    accessories.setdefault(i.parent_item_id, []).append(i)

    def copy_into(room, item, split, order):
        return models.BOQItem(
            room_id=room.id, specsheet_id=item.specsheet_id, sort_order=order,
            item_code=item.item_code, reference_number=item.reference_number,
            model_no=item.model_no, description=item.description, dimensions=item.dimensions or "",
            show_line_diagram=bool(item.show_line_diagram), dimension_image=item.dimension_image or "",
            finish=item.finish,
            wattage=item.wattage, beam=item.beam, control=item.control, led_color=item.led_color,
            config=dict(item.config or {}), make=item.make,
            qty="" if split else item.qty, unit=item.unit, remarks=item.remarks,
            is_accessory=item.is_accessory, is_driver=item.is_driver, price=item.price,
            discount_percent=item.discount_percent,
            ref_image_ids=[image_map[x] for x in (item.ref_image_ids or []) if x in image_map])

    rooms_made, rows_made, to_split = 0, 0, 0
    for area in b.areas:
        room = models.BOQRoom(floor_id=floor.id, name=area.name, sort_order=area.sort_order)
        db.add(room)
        db.flush()
        rooms_made += 1
        order = 0
        for product in products:
            if area.id not in (product.area_ids or []):
                continue
            split = len(product.area_ids or []) > 1
            new_product = copy_into(room, product, split, order)
            db.add(new_product)
            db.flush()
            rows_made += 1
            order += 1
            if split:
                to_split += 1
            for acc in accessories.get(product.id, []):
                new_acc = copy_into(room, acc, split, order)
                new_acc.parent_item_id = new_product.id
                db.add(new_acc)
                rows_made += 1
                order += 1

    log_activity(db, request, "boq.to_detailed",
                 "Made detailed BOQ '%s' from quick BOQ '%s' (%d room%s, %d row%s)" % (
                     _boq_display_name(nb), _boq_display_name(b), rooms_made,
                     "" if rooms_made == 1 else "s", rows_made, "" if rows_made == 1 else "s"),
                 boq=nb, details={"from_boq_id": b.id, "rows": rows_made})
    db.commit()
    return {"boq": boq_detail(get_boq_or_404(db, nb.id)), "rooms": rooms_made,
            "rows": rows_made, "need_quantities": to_split}


# ---------- floors ----------

@app.post("/api/boqs/{boq_id}/floors")
def add_floor(boq_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    max_order = max([f.sort_order for f in b.floors], default=-1)
    f = models.BOQFloor(boq_id=boq_id, name=payload.get("name", "New Floor"), sort_order=max_order + 1)
    db.add(f)
    touch_boq(b)
    db.commit()
    db.refresh(f)
    return floor_dict(f)


@app.put("/api/floors/{floor_id}")
def update_floor(floor_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    f = get_or_404(db, models.BOQFloor, floor_id, "floor")
    if "name" in payload:
        f.name = payload["name"]
    if "sort_order" in payload:
        f.sort_order = payload["sort_order"]
    touch_boq(f.boq)
    db.commit()
    return floor_dict(f)


@app.delete("/api/floors/{floor_id}")
def delete_floor(floor_id: int, db: Session = Depends(get_db)):
    f = get_or_404(db, models.BOQFloor, floor_id, "floor")
    touch_boq(f.boq)
    db.delete(f)
    db.commit()
    return {"ok": True}


# ---------- rooms ----------

@app.post("/api/floors/{floor_id}/rooms")
def add_room(floor_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    f = get_or_404(db, models.BOQFloor, floor_id, "floor")
    max_order = max([r.sort_order for r in f.rooms], default=-1)
    r = models.BOQRoom(floor_id=floor_id, name=payload.get("name", "New Area"), sort_order=max_order + 1)
    db.add(r)
    touch_boq(f.boq)
    db.commit()
    db.refresh(r)
    return room_dict(r)


@app.put("/api/rooms/{room_id}")
def update_room(room_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    r = get_or_404(db, models.BOQRoom, room_id, "room")
    if "name" in payload:
        r.name = payload["name"]
    if "sort_order" in payload:
        r.sort_order = payload["sort_order"]
    if "remark" in payload:
        r.remark = payload["remark"]
    touch_boq(r.floor.boq)
    db.commit()
    return room_dict(r)


@app.delete("/api/rooms/{room_id}")
def delete_room(room_id: int, db: Session = Depends(get_db)):
    r = get_or_404(db, models.BOQRoom, room_id, "room")
    touch_boq(r.floor.boq)
    db.delete(r)
    db.commit()
    return {"ok": True}


@app.put("/api/boqs/{boq_id}/structure")
def arrange_boq_structure(boq_id: int, request: Request, payload: dict = Body(...),
                          db: Session = Depends(get_db)):
    """The order of a BOQ's floors, and of the areas within each, set in one go - as the left
    panel leaves it after a drag.

    The whole arrangement arrives at once rather than one row's new position at a time.
    Renumbering siblings a request at a time leaves ties, and a half-applied order when a click
    lands between two requests; this is one transaction, so the order is either all saved or
    not saved at all. It must name every floor and every area of this BOQ exactly once. Anything
    missing, repeated or belonging to another BOQ is refused, never guessed at - it means the
    page is out of date (a floor added or deleted in another tab), and the answer says so.

    An area listed under a different floor moves to that floor with everything in it. It is
    moved by giving it its new floor, not by taking it out of the old floor's list: areas are
    owned by their floor with delete-orphan, and an area removed from that list with no new
    parent in the same step would be deleted - rows, prices and all.
    """
    b = get_boq_or_404(db, boq_id)
    if (b.mode or "detailed") == "quick":
        raise HTTPException(status_code=400, detail="A quick BOQ has no floors to arrange - its "
                            "areas are put in order on its own screen.")
    wanted = payload.get("floors")
    if not isinstance(wanted, list):
        raise HTTPException(status_code=400, detail="floors must be a list")

    floors_by_id = {f.id: f for f in b.floors}
    rooms_by_id = {r.id: r for f in b.floors for r in f.rooms}
    named_floors, named_rooms = [], []
    for entry in wanted:
        if not isinstance(entry, dict) or not isinstance(entry.get("rooms"), list):
            raise HTTPException(status_code=400, detail="Each floor needs an id and a list of rooms")
        named_floors.append(entry.get("id"))
        named_rooms.extend(entry["rooms"])
    stale = ("This BOQ's floors or areas have changed since the page was loaded - reload it to "
             "see the latest arrangement, then try again.")
    if len(named_floors) != len(set(named_floors)) or set(named_floors) != set(floors_by_id):
        raise HTTPException(status_code=409, detail=stale)
    if len(named_rooms) != len(set(named_rooms)) or set(named_rooms) != set(rooms_by_id):
        raise HTTPException(status_code=409, detail=stale)

    moved = []
    for floor_order, entry in enumerate(wanted):
        floor = floors_by_id[entry["id"]]
        floor.sort_order = floor_order
        for room_order, room_id in enumerate(entry["rooms"]):
            room = rooms_by_id[room_id]
            if room.floor_id != floor.id:
                moved.append("'%s' to %s" % (room.name, floor.name))
                room.floor = floor
            room.sort_order = room_order

    summary = "Rearranged the floors and areas of '%s'" % _boq_display_name(b)
    if moved:
        summary += " - moved " + ", ".join(moved[:3]) + (" and %d more" % (len(moved) - 3) if len(moved) > 3 else "")
    log_activity(db, request, "boq.reorder", summary, boq=b, details={"moved": moved})
    touch_boq(b)
    db.commit()
    return boq_detail(get_boq_or_404(db, boq_id))


# ---------- items ----------

def _coerce_price(value, default=0.0):
    """price is manually entered for now (see BOQItem.price) - text input on the frontend
    can hand back an empty string or something non-numeric, so coerce defensively rather
    than let a bad value reach the database as-is (a Float column doesn't reliably accept
    an arbitrary string across every backend/driver combination)."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="price must be a number")


def _coerce_discount(value, allow_blank=True):
    """A discount as a percentage, 0-100. Blank means "follows the quotation's rule" on a line
    (None), and is refused for the quotation's own standard discount, which is always a number.
    A percentage outside 0-100 is refused rather than clamped: 150 is a typo for 15, and a
    quotation priced below nothing is not one anybody meant to send."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_blank:
            return None
        return 0.0
    try:
        rate = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="A discount must be a number, like 10 or 12.5")
    if not 0 <= rate <= 100:
        raise HTTPException(status_code=400, detail="A discount must be between 0 and 100%")
    return rate


# The line build_description puts a product's code on. Once a row is saved, it is the one line
# of its description the app still keeps up to date, because the code ends with the accessories
# listed under the product, and those can be added after it.
_CODE_LINE = re.compile(r"^Product Code:[^\r\n]*", re.MULTILINE)


def _row_choices(item):
    """What a product row was configured with, keyed the way the Configurator sends it: the
    five choices with columns of their own, then everything else it keeps in `config`."""
    chosen = {k: v for k, v in (item.config or {}).items() if v}
    chosen.update(finish=item.finish or "", wattage=item.wattage or "", beam_angle=item.beam or "",
                  control=item.control or "", cct=item.led_color or "")
    return chosen


def _row_accessories(item, sheet):
    """The accessories listed under a product row that `sheet` offers, in the order they are
    listed and each once. These end its code, as the ones ticked in Add Product do.

    A row picked from the product's accessory list records which accessory it is in `config`.
    A row added before that was recorded is recognised by its description, which both pickers
    start with the accessory's name. Anything else (a custom accessory, a power supply, a
    driver) is ordered as a row of its own, and the code does not carry it."""
    offered = {a.strip().lower(): a for a in product_codes.product_options(sheet, "accessories")}
    found = []
    for row in sorted(item.room.items, key=lambda r: r.sort_order):
        if row.parent_item_id != item.id or not row.is_accessory or row.is_driver:
            continue
        name = str((row.config or {}).get("accessory") or "")
        if not name:
            name = (row.description or "").split("\n", 1)[0].split(" - ", 1)[0]
        name = offered.get(name.strip().lower())
        if name and name not in found:
            found.append(name)
    return found


def _refresh_code_line(db, item):
    """Brings the Product Code line of a product row up to date with the accessories now
    listed under it. Returns what the person should be told when it could not, else None.

    Only a line that is already there is changed. A description without one either had the code
    left out on purpose or had no complete code to carry, and adding an accessory changes
    neither. A code that the accessory leaves incomplete (an accessory with no abbreviation yet)
    keeps the old line and says why, rather than deleting a code from a saved row."""
    sheet = item.specsheet
    line = _CODE_LINE.search(item.description or "")
    if sheet is None or item.is_accessory or not line:
        return None
    chosen = _row_choices(item)
    chosen["accessories"] = _row_accessories(item, sheet)
    result = product_codes.code_for_sheet(db, sheet, chosen)
    if result is None:
        return None     # no code structure for this brand, so its accessories are not in its code
    if not result["code"]:
        # Worded as the Configurator's code panel words the same gaps (renderStudioCode).
        gaps = []
        for s in result["segments"]:
            if s["status"] == product_codes.NO_CODE:
                gaps.append("no abbreviation yet for %s “%s” (NPD → Product codes)"
                            % (s["label"], ", ".join(s.get("missing") or [s["value"]])))
            elif s["status"] == product_codes.CHOOSE:
                gaps.append("%s is not chosen" % s["label"])
            elif s["status"] == product_codes.NO_VALUE:
                gaps.append("%s is not recorded for this product (its NPD page)" % s["label"])
        return ("The product code in the description was left as it was, because the new code "
                "would not be complete: %s. Reconfigure the product once that is settled to "
                "update it." % "; ".join(gaps))
    item.description = (item.description[:line.start()] + "Product Code: " + result["code"]
                        + item.description[line.end():])
    return None


@app.get("/api/items/{item_id}/code-accessories")
def item_code_accessories(item_id: int, specsheet_id: int, db: Session = Depends(get_db)):
    """The accessories a product row's code ends with, as `specsheet_id` would code them, for the
    Configurator reconfiguring that row. Worked out here and nowhere else, so a reconfigured row
    and a row that has just had an accessory added are given the same code."""
    i = get_or_404(db, models.BOQItem, item_id, "item")
    s = get_or_404(db, models.Specsheet, specsheet_id, "specsheet")
    return {"accessories": _row_accessories(i, s)}


@app.post("/api/rooms/{room_id}/items")
def add_item(room_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    r = get_or_404(db, models.BOQRoom, room_id, "room")
    parent_item_id = payload.get("parent_item_id")
    after_item_id = payload.get("after_item_id")
    # Where to insert: an explicit after_item_id wins; otherwise if this item belongs to a
    # parent product, it lands right after that product's existing block (the product
    # itself, or its last accessory so far - so a second/third accessory added later stacks
    # onto the end of the block instead of wedging in front of ones already there); with
    # neither, it's appended at the room's end same as before.
    after = None
    if after_item_id:
        after = get_or_404(db, models.BOQItem, after_item_id, "item")
    elif parent_item_id:
        parent = get_or_404(db, models.BOQItem, parent_item_id, "item")
        group = [parent] + [x for x in r.items if x.parent_item_id == parent_item_id]
        after = max(group, key=lambda x: x.sort_order)
    if after:
        for sib in r.items:
            if sib.sort_order > after.sort_order:
                sib.sort_order += 1
        sort_order = after.sort_order + 1
    else:
        sort_order = max([i.sort_order for i in r.items], default=-1) + 1
    i = models.BOQItem(
        room_id=room_id,
        specsheet_id=payload.get("specsheet_id"),
        parent_item_id=parent_item_id,
        sort_order=sort_order,
        item_code=payload.get("item_code", ""),
        reference_number=payload.get("reference_number", ""),
        model_no=payload.get("model_no", ""),
        description=payload.get("description", ""),
        dimensions=payload.get("dimensions", ""),
        show_line_diagram=bool(payload.get("show_line_diagram", False)),
        dimension_image=str(payload.get("dimension_image") or "").strip(),
        finish=payload.get("finish", ""),
        wattage=payload.get("wattage", ""),
        beam=payload.get("beam", ""),
        control=payload.get("control", ""),
        led_color=payload.get("led_color", ""),
        config=_clean_line_config(payload.get("config")),
        make=payload.get("make", ""),
        qty=str(payload.get("qty", "1")),
        unit=payload.get("unit", "Nos."),
        remarks=payload.get("remarks", ""),
        is_accessory=bool(payload.get("is_accessory", False)),
        is_driver=bool(payload.get("is_driver", False)),
        price=_coerce_price(payload.get("price")),
        discount_percent=_coerce_discount(payload.get("discount_percent")),
        area_ids=_clean_id_list(payload.get("area_ids"), _area_ids_of(r.floor.boq)),
        ref_image_ids=_clean_id_list(payload.get("ref_image_ids"), _ref_image_ids_of(r.floor.boq)),
    )
    db.add(i)
    note = None
    if parent_item_id and i.is_accessory and not i.is_driver:
        # An accessory ends its product's code, so the product's code line is brought up to
        # date in the same commit. Flushed first so the new row is among the product's own.
        db.flush()
        db.expire(r, ["items"])
        product = db.get(models.BOQItem, parent_item_id)
        if product is not None:
            note = _refresh_code_line(db, product)
    touch_boq(r.floor.boq)
    db.commit()
    db.refresh(r)
    out = room_dict(r)
    if note:
        out["code_note"] = note
    return out


@app.put("/api/items/{item_id}")
def update_item(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    i = get_or_404(db, models.BOQItem, item_id, "item")
    # specsheet_id included so "reconfigure via Configurator" can switch an existing row to a
    # different matching product, not just tweak its finish/wattage/beam/control text values
    for field in ("specsheet_id", "item_code", "reference_number", "model_no", "description",
                  "dimensions", "show_line_diagram", "dimension_image", "finish", "wattage",
                  "beam", "control", "led_color", "config", "make", "qty", "unit", "remarks",
                  "is_accessory", "is_driver", "sort_order"):
        if field in payload:
            setattr(i, field, payload[field])
    if "config" in payload:
        i.config = _clean_line_config(payload["config"])
    if "price" in payload:
        i.price = _coerce_price(payload["price"])
    if "discount_percent" in payload:
        i.discount_percent = _coerce_discount(payload["discount_percent"])
    # Quick BOQs: the areas this product goes into, and the pictures beside it. Checked
    # against this BOQ's own, so a stale page cannot attach another project's area.
    boq = _boq_of_item(i)
    if "area_ids" in payload:
        i.area_ids = _clean_id_list(payload["area_ids"], _area_ids_of(boq))
    if "ref_image_ids" in payload:
        i.ref_image_ids = _clean_id_list(payload["ref_image_ids"], _ref_image_ids_of(boq))
    touch_boq(i.room.floor.boq)
    db.commit()
    # Returns the parent room (not just this item) so a caller that needs to re-render more
    # than one field at once - e.g. "reconfigure via Configurator" switching specsheet_id -
    # can patch its local state and DOM for that whole room in one round trip, the same
    # contract as add/move/duplicate/delete below. The lightweight per-cell autosave path
    # (updateItem() in app.js) ignores this response entirely, so broadening it here is free.
    return room_dict(i.room)


@app.post("/api/items/{item_id}/move")
def move_item(item_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    i = get_or_404(db, models.BOQItem, item_id, "item")
    siblings = db.query(models.BOQItem).filter(models.BOQItem.room_id == i.room_id) \
        .order_by(models.BOQItem.sort_order).all()
    idx = siblings.index(i)
    direction = payload.get("direction", "up")
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap_idx < len(siblings):
        other = siblings[swap_idx]
        i.sort_order, other.sort_order = other.sort_order, i.sort_order
        touch_boq(i.room.floor.boq)
        db.commit()
    return room_dict(i.room)


@app.post("/api/items/{item_id}/duplicate")
def duplicate_item(item_id: int, db: Session = Depends(get_db)):
    i = get_or_404(db, models.BOQItem, item_id, "item")
    siblings = db.query(models.BOQItem).filter(models.BOQItem.room_id == i.room_id).all()
    for sib in siblings:
        if sib.sort_order > i.sort_order:
            sib.sort_order += 1
    new_item = models.BOQItem(
        room_id=i.room_id,
        specsheet_id=i.specsheet_id,
        parent_item_id=i.parent_item_id,  # a duplicated accessory stays grouped with the same parent
        sort_order=i.sort_order + 1,
        item_code=i.item_code,
        reference_number=i.reference_number,
        model_no=i.model_no,
        description=i.description,
        dimensions=i.dimensions or "",
        show_line_diagram=bool(i.show_line_diagram),
        dimension_image=i.dimension_image or "",
        finish=i.finish,
        wattage=i.wattage,
        beam=i.beam,
        control=i.control,
        led_color=i.led_color,
        make=i.make,
        qty=i.qty,
        unit=i.unit,
        remarks=i.remarks,
        is_accessory=i.is_accessory,
        is_driver=i.is_driver,
        price=i.price,
        discount_percent=i.discount_percent,
    )
    db.add(new_item)
    touch_boq(i.room.floor.boq)
    db.commit()
    db.refresh(new_item)
    return room_dict(new_item.room)


ITEM_COPY_FIELDS = ("specsheet_id", "item_code", "reference_number", "model_no",
                    "description", "dimensions", "show_line_diagram", "dimension_image", "finish", "wattage",
                    "beam", "control", "led_color", "make", "qty", "unit", "remarks",
                    "is_accessory", "is_driver", "price", "discount_percent")


def _copied_item(source, room_id, sort_order, parent_item_id=None):
    """A new BOQItem carrying everything the source row says, in a new place."""
    values = {name: getattr(source, name) for name in ITEM_COPY_FIELDS}
    values["dimensions"] = values["dimensions"] or ""
    values["show_line_diagram"] = bool(values["show_line_diagram"])
    return models.BOQItem(room_id=room_id, sort_order=sort_order,
                          parent_item_id=parent_item_id, **values)


@app.post("/api/items/transfer")
def transfer_items(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Copy or move any number of rows into one area, anywhere in the same BOQ.

    One endpoint for both because they differ in exactly one respect - whether the original
    row survives - and everything else about them is identical: the same validation, the same
    accessory handling, the same ordering, and the same all-or-nothing commit. Two endpoints
    would have been two places for that to drift.

    A room belongs to one floor and a floor to one BOQ, so "any floor" means any floor of this
    project; moving a row into another project would take it away from the specsheets, prices
    and numbering it was written against, and is refused.

    Accessories travel with the product they hang off. They are rows in their own right, but a
    power supply sitting under a downlight is not a thing anybody means to leave behind when
    they move that downlight to another area - and for a move, leaving it would strand a row
    pointing at a parent that is no longer in the room. Selecting an accessory on its own is
    still allowed: it arrives as a standalone row, and the reply says how many did that so the
    caller can say so rather than letting it pass silently.
    """
    mode = (payload.get("mode") or "").strip().lower()
    if mode not in ("copy", "move"):
        raise HTTPException(status_code=400, detail="mode must be 'copy' or 'move'")
    room_id = payload.get("room_id")
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")
    ids = [int(i) for i in (payload.get("item_ids") or [])]
    if not ids:
        raise HTTPException(status_code=400, detail="Pick at least one row first")

    target_room = get_or_404(db, models.BOQRoom, room_id, "room")
    target_boq_id = target_room.floor.boq_id

    chosen = db.query(models.BOQItem).filter(models.BOQItem.id.in_(ids)).all()
    if len(chosen) != len(set(ids)):
        raise HTTPException(status_code=404, detail="One of those rows no longer exists")
    for item in chosen:
        if item.room.floor.boq_id != target_boq_id:
            raise HTTPException(
                status_code=400,
                detail="Rows can only be copied or moved within the same BOQ")

    # A selected product brings its own accessories, and a row selected twice - directly and
    # as somebody's child - is still one row.
    by_id = {item.id: item for item in chosen}
    for item in chosen:
        for child in db.query(models.BOQItem).filter(
                models.BOQItem.parent_item_id == item.id).all():
            by_id.setdefault(child.id, child)

    # Their existing order is the order they arrive in, so a selection that read top to bottom
    # still does afterwards.
    ordered = sorted(by_id.values(), key=lambda i: (i.room_id, i.sort_order or 0, i.id))
    moving = set(by_id)
    source_room_ids = {i.room_id for i in ordered}

    next_sort = max((x.sort_order or 0 for x in target_room.items), default=-1) + 1
    detached = 0
    new_by_source = {}

    for item in ordered:
        keeps_parent = item.parent_item_id in moving
        if not keeps_parent and item.parent_item_id:
            detached += 1
        if mode == "copy":
            parent = new_by_source.get(item.parent_item_id) if keeps_parent else None
            fresh = _copied_item(item, target_room.id, next_sort,
                                 parent.id if parent is not None else None)
            db.add(fresh)
            db.flush()                       # its id is what a child of it will point at
            new_by_source[item.id] = fresh
        else:
            item.room_id = target_room.id
            item.sort_order = next_sort
            if not keeps_parent:
                item.parent_item_id = None
        next_sort += 1

    for boq in {target_room.floor.boq} | {
            db.query(models.BOQRoom).filter(models.BOQRoom.id == rid).first().floor.boq
            for rid in source_room_ids}:
        touch_boq(boq)
    db.commit()

    # Every room the change touched, so the caller can redraw both ends of a move at once.
    affected = {target_room.id} | (source_room_ids if mode == "move" else set())
    rooms = [room_dict(get_or_404(db, models.BOQRoom, rid, "room")) for rid in affected]
    return {"rooms": rooms, "mode": mode, "count": len(ordered),
            "accessories_included": len(ordered) - len(chosen),
            "detached": detached, "target_room_id": target_room.id}


def _copy_room_into(db, source: models.BOQRoom, target_floor: models.BOQFloor,
                    name: str, sort_order: int) -> models.BOQRoom:
    """A new area on `target_floor` holding everything `source` holds - the name it is given,
    the area remark, and every row in the same order, with each accessory still hanging off the
    same product it hung off before.

    Parent links are set in a SECOND pass rather than as each row is created. A child row can
    sort before its own parent, so at the moment a child is copied the parent's new id is not
    reliably known yet; doing the linking afterwards makes the order of the rows irrelevant
    instead of making it a thing that has to stay true.

    `sort_order` is passed in rather than worked out from target_floor.rooms, because the
    caller duplicating a whole floor adds several areas in a row and wants each to keep the
    position it had - and a relationship list is not reliably up to date mid-transaction
    anyway, which would quietly give every copied area the same position.
    """
    fresh_room = models.BOQRoom(floor_id=target_floor.id, name=name,
                                remark=source.remark, sort_order=sort_order)
    db.add(fresh_room)
    db.flush()                       # its id is what every row below points at

    rows = sorted(source.items, key=lambda i: (i.sort_order or 0, i.id))
    new_by_source = {}
    for row in rows:
        fresh = _copied_item(row, fresh_room.id, row.sort_order or 0)
        db.add(fresh)
        new_by_source[row.id] = fresh
    db.flush()                       # every new row now has an id
    for row in rows:
        if row.parent_item_id in new_by_source:
            new_by_source[row.id].parent_item_id = new_by_source[row.parent_item_id].id
    return fresh_room


@app.post("/api/rooms/{room_id}/transfer")
def transfer_room(room_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Copy or move one whole area - its name, its remark and every row in it - onto another
    floor of the same BOQ.

    The same shape as transfer_items above, and for the same reason: copy and move differ in
    exactly one respect, whether the original survives, so they share one endpoint rather than
    giving that single difference two places to drift apart in.

    A move REASSIGNS THE PARENT (`room.floor = target_floor`). It must never be done by taking
    the room out of one floor's `rooms` list and appending it to another's: that relationship
    is `delete-orphan`, so a room removed from its floor's list is a room marked for deletion,
    and every item in it would go with it. Reassigning is the one safe way to say "this area
    now belongs to that floor".
    """
    mode = (payload.get("mode") or "").strip().lower()
    if mode not in ("copy", "move"):
        raise HTTPException(status_code=400, detail="mode must be 'copy' or 'move'")
    floor_id = payload.get("floor_id")
    if not floor_id:
        raise HTTPException(status_code=400, detail="floor_id is required")

    room = get_or_404(db, models.BOQRoom, room_id, "room")
    target_floor = get_or_404(db, models.BOQFloor, floor_id, "floor")
    source_floor = room.floor
    # A floor belongs to one BOQ, so "another floor" means another floor of this project.
    # Moving an area into a different project would take it away from the specsheets, prices
    # and numbering it was written against - the same refusal transfer_items makes.
    if source_floor.boq_id != target_floor.boq_id:
        raise HTTPException(status_code=400,
                            detail="An area can only be copied or moved within the same BOQ")
    if mode == "move" and source_floor.id == target_floor.id:
        raise HTTPException(status_code=400,
                            detail="That area is already on this floor")

    next_sort = max((r.sort_order or 0 for r in target_floor.rooms), default=-1) + 1
    if mode == "copy":
        # Landing on its own floor, a copy needs a name that tells the two apart. Landing on
        # another floor it keeps its name exactly - which is the point of copying it as it is.
        name = ("%s (copy)" % room.name) if target_floor.id == source_floor.id else room.name
        result = _copy_room_into(db, room, target_floor, name, next_sort)
    else:
        room.sort_order = next_sort
        room.floor = target_floor
        result = room

    touch_boq(target_floor.boq)
    db.commit()
    db.refresh(result)
    return {"mode": mode, "room": room_dict(result), "floor_id": target_floor.id,
            "source_floor_id": source_floor.id, "items": len(result.items)}


@app.post("/api/floors/{floor_id}/duplicate")
def duplicate_floor(floor_id: int, db: Session = Depends(get_db)):
    """A second copy of a whole floor: every area on it, and every row in every area.

    Placed directly below the original rather than at the end of the BOQ. A duplicate is nearly
    always made to be edited into a variant of the thing it was copied from, so putting it
    anywhere else would mean scrolling past the rest of the project to find it every time -
    the same reasoning duplicate_item already follows for a single row.
    """
    source = get_or_404(db, models.BOQFloor, floor_id, "floor")
    boq = source.boq
    source_order = source.sort_order or 0
    for sibling in boq.floors:
        if (sibling.sort_order or 0) > source_order:
            sibling.sort_order = (sibling.sort_order or 0) + 1
    fresh = models.BOQFloor(boq_id=boq.id, name="%s (copy)" % source.name,
                            sort_order=source_order + 1)
    db.add(fresh)
    db.flush()                       # its id is what every area below points at
    # Each area keeps the position it had, so the copy reads exactly like the original.
    for room in sorted(source.rooms, key=lambda r: (r.sort_order or 0, r.id)):
        _copy_room_into(db, room, fresh, room.name, room.sort_order or 0)
    touch_boq(boq)
    db.commit()
    db.refresh(fresh)
    return floor_dict(fresh)


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    i = get_or_404(db, models.BOQItem, item_id, "item")
    room_id = i.room_id
    touch_boq(i.room.floor.boq)
    product = (db.get(models.BOQItem, i.parent_item_id)
               if i.parent_item_id and i.is_accessory and not i.is_driver else None)
    # Accessories only make sense alongside the product they're associated with - deleting
    # the product takes its accessory rows with it rather than leaving them orphaned in the room.
    db.query(models.BOQItem).filter(models.BOQItem.parent_item_id == item_id).delete()
    db.delete(i)
    note = None
    if product is not None:
        # An accessory taken away no longer ends its product's code (see _refresh_code_line).
        db.flush()
        db.expire(product.room, ["items"])
        note = _refresh_code_line(db, product)
    db.commit()
    # room_id captured before delete: `i` itself is a deleted instance post-commit, so its
    # room_id/relationship attrs are gone - re-fetching the (still-existing) parent room fresh
    # is what gives the same "here's the room after this change" contract the other item
    # mutation endpoints share.
    r = get_or_404(db, models.BOQRoom, room_id, "room")
    out = room_dict(r)
    if note:
        out["code_note"] = note
    return out


# ---------- export ----------

# Which column vocabulary each export format uses. A BOQ and a Quotation do not share one:
# the Quotation has REF NO./MAKE/LED COLOR/AREA and the price columns, the BOQ has none of
# them - so the picker has to be told which document is being exported, or it offers
# checkboxes for columns that document has never had.
#
# The summarised variants drop the columns their own exporter always drops, so the picker
# never offers to hide something that is not there: "remarks" means nothing once rooms are
# collapsed away, and "area" is the column a summary exists to remove.
_EXPORT_COLUMN_SETS = {
    "xlsx": (COLUMN_KEYS, COLUMN_LABELS),
    "pdf": (COLUMN_KEYS, COLUMN_LABELS),
    "boq-summary": ([k for k in COLUMN_KEYS if k not in ("remarks", "ref_images")], COLUMN_LABELS),
    "boq-summary-pdf": ([k for k in COLUMN_KEYS if k not in ("remarks", "ref_images")], COLUMN_LABELS),
    "quotation": (QUOTATION_COLUMN_KEYS, QUOTATION_COLUMN_LABELS),
    "quotation-pdf": (QUOTATION_COLUMN_KEYS, QUOTATION_COLUMN_LABELS),
    # The area is a banner above its rows in this layout, never a column, so it is not offered.
    "quotation-areas": ([k for k in QUOTATION_COLUMN_KEYS if k != "area"], QUOTATION_COLUMN_LABELS),
    "quotation-areas-pdf": ([k for k in QUOTATION_COLUMN_KEYS if k != "area"], QUOTATION_COLUMN_LABELS),
    "quotation-summary": (SUMMARY_QUOTATION_COLUMN_KEYS, QUOTATION_COLUMN_LABELS),
    "quotation-summary-pdf": (SUMMARY_QUOTATION_COLUMN_KEYS, QUOTATION_COLUMN_LABELS),
}

# Columns the picker should leave ticked but which are worth a second thought - shown with a
# hint rather than silently, because they are the ones people actually want to drop.
_COLUMN_HINTS = {
    "image": "Product photos",
    "ref_images": "The project's own reference pictures",
    "dimensions": "Dimension drawing / measurements",
    "area": "Which room each item belongs to",
    "remarks": "Per-item notes",
    "discount": "Printed only when products have different discounts",
    "discounted_price": "Printed only when the quotation shows its discount",
}


@app.get("/api/export-columns")
def list_export_columns(format: str = Query("xlsx")):
    """The column set + display labels for one export format - lets the frontend's "choose
    which columns to include" picker stay in sync with the exporters' own column vocabulary
    instead of hardcoding a second copy of the list in app.js.

    Unknown formats fall back to the BOQ set rather than erroring: a picker with slightly
    wrong checkboxes is recoverable, an export the user cannot start is not."""
    keys, labels = _EXPORT_COLUMN_SETS.get(format, (COLUMN_KEYS, COLUMN_LABELS))
    return [{"key": k, "label": labels[k], "hint": _COLUMN_HINTS.get(k, "")} for k in keys]


@app.get("/api/currencies")
def list_currencies():
    """The fixed currency set + displayed prefix - same reasoning as /api/export-columns:
    keeps the BOQ meta panel's currency dropdown in sync with
    quotation_export.CURRENCY_PREFIXES instead of hardcoding a second copy in app.js."""
    return [{"code": code, "prefix": prefix} for code, prefix in CURRENCY_PREFIXES.items()]


def _refuse_detailed_export(b):
    """A quick BOQ keeps its products in one floor and one area behind the scenes, so the
    floor-by-floor exports would print that scaffolding as if it were the project's own
    structure. They are refused rather than left to produce a misleading document; the
    consolidated pair is what a quick BOQ has."""
    if (b.mode or "detailed") == "quick":
        raise HTTPException(status_code=400, detail="This is a quick BOQ - download its "
                            "consolidated BOQ or quotation instead.")


# Characters Windows refuses in a file name. Everything else in a project name is kept as typed.
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _download_name(b, kind, extension):
    """The name a BOQ or quotation is saved under on the person's computer (2026-10-12):
    ILDL_<project>_<date>_<kind>, e.g. "ILDL_Sobha Villa_12-10-2026_Quotation.pdf", where
    `kind` is "BOQ" or "Quotation" and the date is the day of the export in India.

    Only the download is named this way. The copy kept in EXPORTS_DIR keeps its own
    timestamped name, because two exports of one project on the same day would otherwise
    overwrite each other there and the export history would point at the wrong file."""
    project = " ".join(_UNSAFE_FILENAME_CHARS.sub(" ", b.project_name or "").split()).strip(" .")
    parts = ["Studio", project, _today_ist().strftime("%d-%m-%Y"), kind]
    return "_".join(p for p in parts if p) + "." + extension


@app.get("/api/boqs/{boq_id}/export-consolidated")
def export_consolidated_route(boq_id: int, priced: bool = Query(False),
                              db: Session = Depends(get_db)):
    """A quick BOQ's own document - the consolidated layout, priced or not.

    `priced=false` is the working BOQ: the same table with no money column on it at all.
    `priced=true` is the quotation the client sees: prices, amounts, the Summary page.
    """
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    if (b.mode or "detailed") != "quick":
        raise HTTPException(status_code=400, detail="This is a detailed BOQ - use the normal "
                            "BOQ or Quotation export for it.")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    kind = "Quotation" if priced else "BOQ"
    out_path = EXPORTS_DIR / f"{safe_name}_Consolidated_{kind}_{ts}.xlsx"
    # Named now: once the session commits and closes, b's fields can no longer be read.
    download_name = _download_name(b, kind, "xlsx")
    if priced:
        export_consolidated_quotation(b, out_path)
    else:
        export_consolidated_boq(b, out_path)
    db.rollback()          # nothing this export worked out is meant to be saved
    db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
    db.commit()
    db.close()
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
    )


@app.get("/api/boqs/{boq_id}/export-consolidated-pdf")
def export_consolidated_pdf_route(boq_id: int, priced: bool = Query(False),
                                  preview: bool = Query(False),
                                  db: Session = Depends(get_db)):
    """The same two documents as .pdf - what the Preview button and the docked preview show,
    and what a client is sent when they should read it rather than edit it.

    `preview=true` displays it in the browser instead of downloading it, and is not recorded
    as an export: it is a look, not a document that went out.
    """
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    if (b.mode or "detailed") != "quick":
        raise HTTPException(status_code=400, detail="This is a detailed BOQ - use its own "
                            "BOQ or Quotation preview.")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    kind = "Quotation" if priced else "BOQ"
    out_path = EXPORTS_DIR / f"{safe_name}_Consolidated_{kind}_{ts}.pdf"
    download_name = _download_name(b, kind, "pdf")
    if priced:
        export_consolidated_quotation_pdf(b, out_path)
    else:
        export_consolidated_boq_pdf(b, out_path)
    db.rollback()
    if not preview:
        db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
        db.commit()
    db.close()
    return FileResponse(
        out_path, media_type="application/pdf", filename=download_name,
        content_disposition_type="inline" if preview else "attachment",
    )


@app.get("/api/boqs/{boq_id}/export")
def export_boq_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    _refuse_detailed_export(b)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    out_path = EXPORTS_DIR / f"{safe_name}_{ts}.xlsx"
    download_name = _download_name(b, "BOQ", "xlsx")   # before the commit below, which expires b
    export_boq(b, out_path, hidden_columns=hidden_columns)
    # export_boq() may have auto-filled blank S.No values on b's items in memory
    # (excel_export._autofill_sno) - deliberately never meant to be saved, so discard that
    # (and anything else uncommitted) before the one write this route actually intends: the
    # BOQExport history row added below. Without this, db.commit() would sweep those
    # in-memory item_code edits into the database as a silent side effect of exporting.
    db.rollback()
    db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
    db.commit()
    db.close()  # release the connection now, not after the file finishes streaming to the client
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
    )


@app.get("/api/boqs/{boq_id}/export-pdf")
def export_boq_pdf_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), preview: bool = Query(False), db: Session = Depends(get_db)):
    """A clean, print-ready .pdf alternative to the .xlsx export - same cover
    info, per-floor/room item tables (with product photos) and terms page, but
    laid out for a client to open and read directly rather than to keep
    editing in Excel. Tracked in the same `boq_exports` history as the .xlsx
    export (the file's own extension is enough to tell them apart - no schema
    change needed).

    `preview=true` (2026-08-12): renders the exact same PDF but tells the browser to
    display it inline (a new tab, the browser's native PDF viewer) instead of forcing a
    download - so checking whether a revision looks right doesn't mean downloading a new
    file to disk every time. Not recorded in boq_exports history (it's a look, not a real
    export)."""
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    _refuse_detailed_export(b)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    out_path = EXPORTS_DIR / f"{safe_name}_{ts}.pdf"
    download_name = _download_name(b, "BOQ", "pdf")
    export_boq_pdf(b, out_path, hidden_columns=hidden_columns)
    db.rollback()  # discard any in-memory-only S.No autofill (_autofill_sno) - see export_boq_route's own comment
    if not preview:
        db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
        db.commit()
    db.close()
    return FileResponse(
        out_path, media_type="application/pdf", filename=download_name,
        content_disposition_type="inline" if preview else "attachment",
    )


@app.get("/api/boqs/{boq_id}/export-quotation")
def export_quotation_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    """The pricing-facing Quotation .xlsx (app/quotation_export.py) - Cover, a Summary
    sheet with per-floor subtotals, item tables with Price/Discounted Price/Amount, and
    the same terms page - a different document from the plain BOQ export above, built to
    match SampleInputs/SampleQuotation's own format. No hidden_columns option: unlike the
    BOQ export's product-tracking columns, every column here (including price) is the
    point of a quotation, so there's nothing sensible to optionally hide yet."""
    return _quotation_xlsx_response(boq_id, hidden_columns, db, area_banners=False)


@app.get("/api/boqs/{boq_id}/export-quotation-pdf")
def export_quotation_pdf_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), preview: bool = Query(False), db: Session = Depends(get_db)):
    """Print-ready sibling of export_quotation_route above (app/quotation_pdf_export.py) -
    same Cover/Summary/pricing/terms content, landscape A4 to fit the Quotation's wider
    19-column item table (see that module's docstring for why landscape specifically).
    `preview=true`: see export_boq_pdf_route's own docstring - same inline-instead-of-
    download behavior, not recorded in boq_exports history."""
    return _quotation_pdf_response(boq_id, hidden_columns, preview, db, area_banners=False)


# "Quotation (detailed, area as a subheader)": the detailed quotation laid out the way the
# detailed BOQ is - each area a banner with its items beneath it - instead of an AREA column.
# Same exporters, same figures and formulas; only the layout switch differs.
@app.get("/api/boqs/{boq_id}/export-quotation-areas")
def export_quotation_areas_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    return _quotation_xlsx_response(boq_id, hidden_columns, db, area_banners=True)


@app.get("/api/boqs/{boq_id}/export-quotation-areas-pdf")
def export_quotation_areas_pdf_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), preview: bool = Query(False), db: Session = Depends(get_db)):
    return _quotation_pdf_response(boq_id, hidden_columns, preview, db, area_banners=True)


def _quotation_file_path(b, extension, area_banners):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    kind = "Quotation_by_Area" if area_banners else "Quotation"
    return EXPORTS_DIR / f"{safe_name}_{kind}_{ts}.{extension}"


def _quotation_xlsx_response(boq_id, hidden_columns, db, area_banners):
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    _refuse_detailed_export(b)
    out_path = _quotation_file_path(b, "xlsx", area_banners)
    download_name = _download_name(b, "Quotation", "xlsx")
    export_quotation(b, out_path, hidden_columns=hidden_columns, area_banners=area_banners)
    db.rollback()  # discard any in-memory-only S.No autofill (_autofill_sno) - see export_boq_route's own comment
    db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
    db.commit()
    db.close()
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
    )


def _quotation_pdf_response(boq_id, hidden_columns, preview, db, area_banners):
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    _refuse_detailed_export(b)
    out_path = _quotation_file_path(b, "pdf", area_banners)
    download_name = _download_name(b, "Quotation", "pdf")
    export_quotation_pdf(b, out_path, hidden_columns=hidden_columns, area_banners=area_banners)
    db.rollback()  # discard any in-memory-only S.No autofill (_autofill_sno) - see export_boq_route's own comment
    if not preview:
        db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
        db.commit()
    db.close()
    return FileResponse(
        out_path, media_type="application/pdf", filename=download_name,
        content_disposition_type="inline" if preview else "attachment",
    )


@app.get("/api/boqs/{boq_id}/export-boq-summary")
def export_boq_summary_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    """The plain BOQ export's product-list, flattened: no per-floor/per-room grouping and
    no Remarks column - every distinct product spec appears once, with its quantity summed
    across every floor/room it was actually used in (app/boq_summary.py). The case this
    exists for: the same fixture specified on both the ground floor and first floor should
    show up as one line with the combined quantity when handed to an architect, not once
    per room."""
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    _refuse_detailed_export(b)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    out_path = EXPORTS_DIR / f"{safe_name}_Summary_{ts}.xlsx"
    download_name = _download_name(b, "BOQ", "xlsx")
    export_boq_summary(b, out_path, hidden_columns=hidden_columns)
    db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
    db.commit()
    db.close()
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
    )


@app.get("/api/boqs/{boq_id}/export-boq-summary-pdf")
def export_boq_summary_pdf_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), preview: bool = Query(False), db: Session = Depends(get_db)):
    """Print-ready sibling of export_boq_summary_route above. `preview=true`: see
    export_boq_pdf_route's own docstring."""
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    _refuse_detailed_export(b)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    out_path = EXPORTS_DIR / f"{safe_name}_Summary_{ts}.pdf"
    download_name = _download_name(b, "BOQ", "pdf")
    export_boq_summary_pdf(b, out_path, hidden_columns=hidden_columns)
    if not preview:
        db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
        db.commit()
    db.close()
    return FileResponse(
        out_path, media_type="application/pdf", filename=download_name,
        content_disposition_type="inline" if preview else "attachment",
    )


@app.get("/api/boqs/{boq_id}/export-quotation-summary")
def export_quotation_summary_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    """The priced Quotation export's item list, flattened and deduplicated the same way as
    export_boq_summary_route above - one Grand Total row at the bottom instead of the full
    Quotation export's separate per-floor Summary page, since there are no floors left to
    subtotal by (app/boq_summary.py)."""
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    _refuse_detailed_export(b)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    out_path = EXPORTS_DIR / f"{safe_name}_Quotation_Summary_{ts}.xlsx"
    download_name = _download_name(b, "Quotation", "xlsx")
    export_quotation_summary(b, out_path, hidden_columns=hidden_columns)
    db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
    db.commit()
    db.close()
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=download_name,
    )


@app.get("/api/boqs/{boq_id}/export-quotation-summary-pdf")
def export_quotation_summary_pdf_route(boq_id: int, hidden_columns: list[str] = Query(default=[]), preview: bool = Query(False), db: Session = Depends(get_db)):
    """Print-ready sibling of export_quotation_summary_route above. `preview=true`: see
    export_boq_pdf_route's own docstring."""
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    _refuse_detailed_export(b)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in b.project_name).strip() or "BOQ"
    out_path = EXPORTS_DIR / f"{safe_name}_Quotation_Summary_{ts}.pdf"
    download_name = _download_name(b, "Quotation", "pdf")
    export_quotation_summary_pdf(b, out_path, hidden_columns=hidden_columns)
    if not preview:
        db.add(models.BOQExport(boq_id=boq_id, file_path=str(out_path)))
        db.commit()
    db.close()
    return FileResponse(
        out_path, media_type="application/pdf", filename=download_name,
        content_disposition_type="inline" if preview else "attachment",
    )


@app.get("/api/boqs/{boq_id}/exports")
def list_exports(boq_id: int, db: Session = Depends(get_db)):
    b = get_or_404(db, models.BOQ, boq_id, "boq")
    return [
        {"id": e.id, "file_path": e.file_path, "filename": Path(e.file_path).name, "exported_at": _utc_iso(e.exported_at)}
        for e in b.exports
    ]


@app.get("/api/exports/{export_id}/download")
def download_export(export_id: int, db: Session = Depends(get_db)):
    e = get_or_404(db, models.BOQExport, export_id, "export")
    if not Path(e.file_path).exists():
        raise HTTPException(status_code=404, detail="export file missing on disk")
    file_path = e.file_path
    db.close()
    return FileResponse(file_path, filename=Path(file_path).name)


# ---------------- New Product Development ----------------
#
# Authoring a product rather than reading one out of a PDF. The product is saved as a real
# Specsheet (origin="npd"), so the Library, Match, Configurator and every export accept it
# without knowing it was typed rather than parsed - and its price travels with it, as a base
# plus per-option adders, instead of being joined on by hand afterwards. See app/npd.py.

def _npd_template_dict(t):
    return {
        "id": t.id, "typology": t.typology, "group_name": t.group_name, "label": t.label,
        "field_key": t.field_key, "sort_order": t.sort_order,
        "is_configurable": bool(t.is_configurable), "user_added": bool(t.user_added),
    }


@app.get("/api/npd/typologies")
def npd_typologies(request: Request, db: Session = Depends(get_db)):
    """Typologies that have a parameter template - what the NPD form's first choice is made
    from.

    Only these, not every typology in the library. The library also holds Exenia's, DuraGreen's
    and Lumenpulse's own classifications, which describe how those manufacturers organise their
    catalogues; offering them here would invite someone to author a InHouse product under
    another company's idea of what it is. A kind of product not listed is added with
    POST /api/npd/typologies, which starts it from the InHouse sheet shape."""
    discipline = current_discipline(request)
    counts = dict(
        db.query(models.ParameterTemplate.typology, func.count(models.ParameterTemplate.id))
        .filter(models.ParameterTemplate.discipline == discipline)
        .group_by(models.ParameterTemplate.typology).all()
    )
    return JSONResponse([{"typology": t, "parameters": counts[t]} for t in sorted(counts)])


@app.get("/api/npd/parameters")
def npd_parameters(request: Request, typology: str = Query(...), db: Session = Depends(get_db)):
    """The parameter template for one typology, in print order."""
    rows = (db.query(models.ParameterTemplate)
            .filter(models.ParameterTemplate.typology == typology,
                    models.ParameterTemplate.discipline == current_discipline(request))
            .order_by(models.ParameterTemplate.sort_order, models.ParameterTemplate.id).all())
    # One block per group, by name, in the order each first appears. Until 18 Nov 2026 a block
    # ended wherever the next row belonged to another group, and a parameter added to an earlier
    # group (numbered after every row) opened a second "MECHANICAL PARAMETERS" at the foot.
    groups, by_name = [], {}
    for r in rows:
        name = _npd_group_key(r.group_name)
        if name not in by_name:
            by_name[name] = {"group_name": r.group_name, "parameters": []}
            groups.append(by_name[name])
        by_name[name]["parameters"].append(_npd_template_dict(r))
    return JSONResponse({"typology": typology, "groups": groups,
                         "count": len(rows)})


def _npd_group_key(name) -> str:
    """A group's name as compared: "Mechanical parameters" is "MECHANICAL PARAMETERS"."""
    return " ".join(str(name or "").split()).upper()


# What names a product, as opposed to what specifies it. Series and family have always been
# columns of their own; sub-typology and variant joined them; anything further is a template row
# under npd.IDENTITY_GROUP, added per typology by whoever needs it. All of them behave the same
# way here: a value, and a short code that value contributes to the product code.
IDENTITY_BUILTINS = [
    ("series", "Series"),
    ("family", "Family"),
    ("sub_typology", "Sub-typology"),
    ("variant", "Variant"),
]


def _identity_fields(db, typology, discipline):
    """[(field_key, label)] for one typology - the built-ins, then its own identity template."""
    out = list(IDENTITY_BUILTINS)
    if typology:
        rows = (db.query(models.ParameterTemplate)
                .filter(models.ParameterTemplate.discipline == discipline,
                        models.ParameterTemplate.typology == typology,
                        models.ParameterTemplate.group_name == npd.IDENTITY_GROUP)
                .order_by(models.ParameterTemplate.sort_order, models.ParameterTemplate.id).all())
        out += [(r.field_key, r.label) for r in rows]
    return out


@app.get("/api/npd/identity")
def npd_identity(request: Request, typology: str = Query(""), db: Session = Depends(get_db)):
    """The identity fields a product of this typology is named by, and every short code known
    for their values.

    The codes come from the same `field_codes` table the Configurator's generated code is
    assembled from - an identity field is not a second code system, it is the first segments of
    the one that already exists. Whoever names a new series is the only person who knows its
    abbreviation, so the form asks for it where the name is typed rather than sending them to
    another tab for it."""
    fields = _identity_fields(db, typology.strip(), current_discipline(request))
    keys = [k for k, _label in fields]
    codes = (db.query(models.FieldCode).filter(models.FieldCode.field.in_(keys)).all()
             if keys else [])
    return JSONResponse({
        "typology": typology,
        "fields": [{"field_key": k, "label": label,
                    "builtin": k in {b for b, _l in IDENTITY_BUILTINS}} for k, label in fields],
        "codes": [{"field": c.field, "option_value": c.option_value, "code": c.code or ""}
                  for c in codes],
    })


@app.post("/api/npd/identity-code")
def npd_set_identity_code(request: Request, payload: dict = Body(...),
                          db: Session = Depends(get_db)):
    """Set the short code one identity value contributes to a product code ("Midi" -> "M").

    Scoped to identity fields on purpose: this is reachable by whoever may author a product,
    which is not the same as being allowed to rewrite the whole Product Codes table in Config
    Rules. A blank code removes the row rather than storing an empty one, so "no code" stays one
    state rather than two."""
    field = (payload.get("field_key") or "").strip()
    value = " ".join(str(payload.get("value") or "").split())
    code = " ".join(str(payload.get("code") or "").split()).upper()
    typology = (payload.get("typology") or "").strip()
    allowed = {k for k, _label in _identity_fields(db, typology, current_discipline(request))}
    if field not in allowed:
        raise HTTPException(status_code=400,
                            detail="%s is not an identity field of %s" % (field or "?", typology or "this typology"))
    if not value:
        raise HTTPException(status_code=400, detail="Give the value a code belongs to.")
    row = (db.query(models.FieldCode)
           .filter(models.FieldCode.field == field,
                   func.lower(models.FieldCode.option_value) == value.lower()).first())
    if not code:
        if row:
            db.delete(row)
            db.commit()
        return JSONResponse({"field": field, "option_value": value, "code": ""})
    if row:
        row.code = code
        row.option_value = value
    else:
        row = models.FieldCode(field=field, option_value=value, code=code)
        db.add(row)
    # Logged before the commit, so it is saved with the change - written after it, it never was.
    log_activity(db, request, "npd.identity_code",
                 "Coded %s '%s' as '%s'" % (field, value, code),
                 details={"field": field, "value": value, "code": code})
    db.commit()
    return JSONResponse({"field": field, "option_value": value, "code": code})


# ---------- the studio's product code ----------
#
# What a product code is made of, per brand and typology, and the abbreviation for every value
# - all of it edited in NPD, because the code has to keep up with the catalogue without a
# developer in the loop. app/product_codes.py assembles a code; these endpoints only read and
# write what it is assembled from. Everything here is read by anyone in NPD and changed by
# whoever may author products ("npd.edit", the default for /api/npd/ writes in
# _required_permission); the one exception is the preview, which changes nothing.

def _code_attr_dict(a):
    return {"key": a.key, "label": a.label, "part": a.part or "identity",
            "rule": a.rule or "list", "multi": bool(a.multi), "note": a.note or "",
            "prefix": a.prefix or "", "suffix": a.suffix or "",
            "sort_order": a.sort_order or 0}


def _code_affix(raw):
    """A prefix or suffix as stored: letters and digits only, at most six of them."""
    text = product_codes.clean_code(raw)
    if text and not re.fullmatch(r"[A-Z0-9]{1,6}", text):
        raise HTTPException(status_code=400, detail="A prefix or suffix is up to six letters "
                            "and digits - W, K, CRI, IP.")
    return text


def _code_segments(raw):
    return [{"key": str(x.get("key") or ""), "include": x.get("include", True) is not False,
             "optional": bool(x.get("optional"))} for x in (raw or []) if x.get("key")]


def _code_scheme_dict(s):
    # getattr throughout: the preview hands this an unsaved structure, which has no author yet.
    return {"id": getattr(s, "id", None), "make": getattr(s, "make", ""),
            "typology": s.typology or "", "prefix": s.prefix or "",
            "separator": s.separator if s.separator is not None else "-",
            "segments": _code_segments(s.segments),
            "updated_by": getattr(s, "updated_by", "") or "",
            "updated_at": _utc_iso(getattr(s, "updated_at", None)),
            "retired_at": _utc_iso(getattr(s, "retired_at", None))}


def _code_attr_or_404(db, discipline, key):
    row = (db.query(models.CodeAttribute)
           .filter(models.CodeAttribute.discipline == discipline,
                   models.CodeAttribute.key == key).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No product-code attribute called %s" % key)
    return row


@app.get("/api/npd/codes")
def npd_codes(request: Request, make: str = Query(""), db: Session = Depends(get_db)):
    """Everything the Product codes panel shows for one brand, in one answer: the attributes,
    the brand's code structures, every value its products use with the code each gets, and
    the check that matters most - which products have a complete code, and which share one.

    Values are counted per typology as well as in total, so the panel can list only what the
    typology on screen actually uses; the abbreviation itself belongs to the attribute, not the
    typology, which is why "WHITE" is WH wherever it appears."""
    discipline = current_discipline(request)
    make = make.strip()
    # First, before anything else is read: the brand's products coded once, kept until one of
    # them - or an abbreviation, attribute or structure - changes (product_codes.brand_codes).
    # Everything below built only from those is kept with it, so opening the panel again does
    # not code 53,243 SKUs again.
    brand = product_codes.brand_codes(db, discipline, make) if make else None
    ctx = product_codes.load_context(db, discipline)
    attrs = list(ctx.attributes.values())
    every_scheme = (db.query(models.CodeScheme)
                    .filter(models.CodeScheme.discipline == discipline,
                            models.CodeScheme.make == make).all()) if make else []
    # A retired structure codes nothing; it is listed apart so the panel can offer it back.
    schemes = [s for s in every_scheme if s.retired_at is None]
    retired = [s for s in every_scheme if s.retired_at is not None]

    def build():
        sheets = (db.query(models.Specsheet)
                  .filter(models.Specsheet.discipline == discipline,
                          models.Specsheet.make == make)
                  .order_by(models.Specsheet.typology, models.Specsheet.product_name,
                            models.Specsheet.id).all()
                  ) if make else []

        by_typology = {}
        for s in sheets:
            by_typology.setdefault(s.typology or "", []).append(s)

        spellings = {}
        if attrs:
            for r in db.query(models.FieldCode).filter(
                    models.FieldCode.field.in_([a.key for a in attrs])).all():
                spellings.setdefault(r.field, {})[product_codes.norm(r.option_value)] = r.option_value
        # A library accessory's code is its own, set in NPD > Accessory library, and a product's code
        # uses it (product_codes.with_library_accessories). The value table shows those same codes,
        # so Nomenclature and NPD never disagree (18 Nov 2026): a U-shape profile is USP17X15 in
        # both, not "missing" here.
        library = {product_codes.norm(label): codes for label, codes in
                   accessory_parts.library_codes_by_label(db, [s.id for s in sheets]).items()}
        values = {}
        for a in attrs:
            merged = {}
            for typology, group in by_typology.items():
                for value, count in product_codes.values_in_use(group, a.key, a.part, a.multi, ctx.typed):
                    n = product_codes.norm(value)
                    entry = merged.setdefault(n, {"value": value, "count": 0, "typologies": {}})
                    entry["count"] += count
                    entry["typologies"][typology] = entry["typologies"].get(typology, 0) + count
            # An abbreviation typed ahead of any product using it is vocabulary too - a value the
            # team has agreed on, waiting for its first product.
            for n, code in ctx.codes.get(a.key, {}).items():
                merged.setdefault(n, {"value": None, "count": 0, "typologies": {}})
            spelled = spellings.get(a.key, {})
            out = []
            for n, entry in merged.items():
                value = entry["value"] or spelled.get(n) or n
                lib = library.get(n) if a.key == "accessories" else None
                if lib is not None:
                    coded = [product_codes.clean_code(c) for c in lib if c and product_codes.explicit(product_codes.clean_code(c))]
                    row = {"value": value, "count": entry["count"], "typologies": entry["typologies"],
                           "code": " / ".join(coded), "source": "library" if coded else "",
                           "library_missing": len(coded) < len(lib)}
                    if not coded:
                        row["reason"] = product_codes.REASON_LIBRARY     # given in NPD, not here
                    out.append(row)
                    continue
                code, source = ctx.code_for(a.key, value)
                row = {"value": value, "count": entry["count"], "typologies": entry["typologies"],
                       "code": code, "source": source if code else ""}
                if not code:
                    # Why it has none - nothing codes it, or it would be digits alone - and what to
                    # start from, so filling a gap is one keystroke rather than an invention.
                    row["reason"] = source or product_codes.REASON_NONE
                    row["suggestion"] = ctx.suggestion(a.key, value)
                out.append(row)
            out.sort(key=lambda e: (-e["count"], str(e["value"]).lower()))
            values[a.key] = out

        # The check the whole scheme exists for: one SKU, one code. Every configuration of every
        # product is coded - each combination of the options it offers - and no two may share a
        # code, within one product (two options coded alike) or between two. A product "needs a
        # decision" while some configuration of it cannot be coded at all.
        report = []
        for s in sheets:
            # A held product is a deliberate no - invisible to the Library, the Configurator and
            # every BOQ picker - so nothing is ever ordered by its codes: it can neither clash
            # with another product nor be missing a code. Its values still count above, because
            # holding is reversible and a value only a held product uses is still vocabulary,
            # not safe to delete.
            coded = brand.results.get(s.id) if brand else None
            if s.approval_status == "held" or coded is None:     # held, or no structure codes it
                continue
            r = coded[0]
            gaps = [x for x in r["segments"]
                    if x["status"] in (product_codes.NO_VALUE, product_codes.NO_CODE)
                    or (x["status"] == product_codes.CHOOSE and any(not c["code"] for c in x["choices"]))]
            report.append({"id": s.id, "product_name": s.product_name, "typology": s.typology or "",
                           "origin": s.origin or "parsed",
                           "identity_code": r["identity_code"], "identity_partial": r["identity_partial"],
                           "identity_complete": r["identity_complete"],
                           "partial": r["partial"],
                           "missing": [x["label"] for x in gaps],
                           # the same, as the fields they are - so Open can go straight to the one
                           # that needs filling instead of leaving you to find it
                           "missing_keys": [x["key"] for x in gaps],
                           **brand.per.get(s.id, {"skus": 0, "uncoded": 0}),
                           "complete": not gaps})
        # One entry per set of products that share codes, with an example and how many they
        # share, rather than one per code: two products written up twice share every code they have.
        grouped = {}
        for code, who in sorted((brand.clashes if brand else {}).items()):
            ids = sorted({pid for pid, _chosen in who})
            entry = grouped.setdefault(tuple(ids), {"code": code, "ids": ids, "count": 0,
                                                    "chosen": [c for _pid, c in who][:2]})
            entry["count"] += 1
        return {"typologies": [{"typology": t, "count": len(g)} for t, g in sorted(by_typology.items())],
                "values": values, "report": report, "duplicates": list(grouped.values())}

    built = brand.memo("nomenclature", build) if brand else build()

    templates = (db.query(models.ParameterTemplate)
                 .filter(models.ParameterTemplate.discipline == discipline)
                 .order_by(models.ParameterTemplate.sort_order, models.ParameterTemplate.id).all())
    in_use = {t["typology"] for t in built["typologies"]}
    parameters = {}
    for t in templates:
        if t.typology in in_use or any(t.typology == x.typology for x in every_scheme):
            parameters.setdefault(t.typology, []).append(
                {"key": t.field_key, "label": t.label, "group": t.group_name,
                 "configurable": bool(t.is_configurable)})
    return JSONResponse({
        "make": make,
        "attributes": [_code_attr_dict(a) for a in attrs],
        "schemes": [_code_scheme_dict(s) for s in schemes],
        "retired": [_code_scheme_dict(s) for s in retired],
        "typologies": built["typologies"],
        "values": built["values"],
        "report": built["report"],
        "duplicates": built["duplicates"],
        "parameters": parameters,
        "rules": [{"key": k, "label": v[0], "help": v[1], "example": v[2]}
                  for k, v in product_codes.RULES.items()],
        "separators": product_codes.SEPARATORS,
    })


@app.post("/api/npd/codes/attributes")
def npd_code_add_attribute(request: Request, payload: dict = Body(...),
                           db: Session = Depends(get_db)):
    """A new thing a product code can say - a new axis of the classification, or an existing
    parameter (cutout, lumen package) brought into the code. Its key is where its value is
    stored on a product; a parameter keeps its own key, so the code reads the value the
    specification already holds rather than a second copy of it."""
    discipline = current_discipline(request)
    label = " ".join(str(payload.get("label") or "").split())
    if not label:
        raise HTTPException(status_code=400, detail="Give the attribute a name.")
    key = npd.key_from_label(payload.get("key") or label)
    if not key:
        raise HTTPException(status_code=400, detail="That name has no letters or digits to key it by.")
    if key in ("product_name",):
        raise HTTPException(status_code=400, detail="The product name cannot be coded - it is free text.")
    if (db.query(models.CodeAttribute)
            .filter(models.CodeAttribute.discipline == discipline,
                    models.CodeAttribute.key == key).first()):
        raise HTTPException(status_code=409, detail="There is already an attribute keyed %s." % key)
    part = payload.get("part") if payload.get("part") in product_codes.PARTS else "identity"
    rule = payload.get("rule") if payload.get("rule") in product_codes.RULES else "list"
    last = (db.query(func.max(models.CodeAttribute.sort_order))
            .filter(models.CodeAttribute.discipline == discipline).scalar()) or 0
    row = models.CodeAttribute(discipline=discipline, key=key, label=label, part=part, rule=rule,
                               multi=bool(payload.get("multi")),
                               prefix=_code_affix(payload.get("prefix")),
                               suffix=_code_affix(payload.get("suffix")),
                               note=str(payload.get("note") or "").strip(), sort_order=last + 1)
    db.add(row)
    log_activity(db, request, "npd.code_attribute",
                 "Added the product-code attribute %s" % label,
                 details={"key": key, "part": part, "rule": rule})
    db.commit()
    db.refresh(row)
    return _code_attr_dict(row)


@app.put("/api/npd/codes/attributes/{key}")
def npd_code_edit_attribute(key: str, request: Request, payload: dict = Body(...),
                            db: Session = Depends(get_db)):
    """Rename an attribute, or change how its values are coded. The key stays: it is where the
    values already live on every product."""
    row = _code_attr_or_404(db, current_discipline(request), key)
    before = _code_attr_dict(row)
    if "label" in payload:
        label = " ".join(str(payload.get("label") or "").split())
        if not label:
            raise HTTPException(status_code=400, detail="An attribute needs a name.")
        row.label = label
    if payload.get("part") in product_codes.PARTS:
        row.part = payload["part"]
    if payload.get("rule") in product_codes.RULES:
        row.rule = payload["rule"]
    if "multi" in payload:
        row.multi = bool(payload.get("multi"))
    if "note" in payload:
        row.note = str(payload.get("note") or "").strip()
    if "prefix" in payload:
        row.prefix = _code_affix(payload.get("prefix"))
    if "suffix" in payload:
        row.suffix = _code_affix(payload.get("suffix"))
    after = _code_attr_dict(row)
    changed = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
    if changed:
        log_activity(db, request, "npd.code_attribute",
                     "Changed the product-code attribute %s: %s" % (
                         row.label, ", ".join("%s %s → %s" % (k, a, b)
                                              for k, (a, b) in changed.items())),
                     details={"key": key, "changed": changed})
    db.commit()
    return after


@app.delete("/api/npd/codes/attributes/{key}")
def npd_code_delete_attribute(key: str, request: Request, db: Session = Depends(get_db)):
    """Remove an attribute nobody's code uses any more. Refused while a structure still names
    it - taking it out of the structures first is a visible, deliberate change to what codes
    say, which removing it from underneath them would not be. Values already saved on products
    and the abbreviations typed for them are kept."""
    discipline = current_discipline(request)
    row = _code_attr_or_404(db, discipline, key)
    # A retired structure codes nothing, so it holds nothing back; restoring it later simply
    # leaves out an attribute that is gone.
    users = [s for s in db.query(models.CodeScheme)
             .filter(models.CodeScheme.discipline == discipline,
                     models.CodeScheme.retired_at.is_(None)).all()
             if any(x.get("key") == key for x in (s.segments or []))]
    if users:
        raise HTTPException(status_code=409, detail="%s is still in the code for %s. Take it out "
                            "of those first." % (row.label, ", ".join(
                                "%s %s" % (s.make, s.typology or "(default)") for s in users)))
    log_activity(db, request, "npd.code_attribute_delete",
                 "Removed the product-code attribute %s" % row.label, details={"key": key})
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.put("/api/npd/codes/schemes")
def npd_code_save_scheme(request: Request, payload: dict = Body(...),
                         db: Session = Depends(get_db)):
    """Save the structure of one brand's code for one typology (blank typology: the brand's
    default). Saved whole - the order of the segments is the grammar, so it is never patched
    one segment at a time."""
    discipline = current_discipline(request)
    make = " ".join(str(payload.get("make") or "").split())
    typology = str(payload.get("typology") or "").strip()
    if not make:
        raise HTTPException(status_code=400, detail="Which brand is this code for?")
    separator = payload.get("separator", "-")
    if separator not in product_codes.SEPARATORS:
        raise HTTPException(status_code=400, detail="Choose one of the offered separators.")
    prefix = product_codes.clean_code(payload.get("prefix"))
    if prefix and not re.fullmatch(r"[A-Z0-9]+", prefix):
        raise HTTPException(status_code=400, detail="A prefix is letters and digits only.")
    segments = _code_segments(payload.get("segments"))
    known = {a.key for a in db.query(models.CodeAttribute)
             .filter(models.CodeAttribute.discipline == discipline).all()}
    keys = [s["key"] for s in segments]
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise HTTPException(status_code=400, detail="Not an attribute: %s" % ", ".join(unknown))
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=400, detail="An attribute can only appear once in a code.")
    row = (db.query(models.CodeScheme)
           .filter(models.CodeScheme.discipline == discipline, models.CodeScheme.make == make,
                   models.CodeScheme.typology == typology).first())
    before = _code_scheme_dict(row) if row else None
    # One row per typology: a new structure for a typology on the brand default takes the place
    # of the one retired there. The log keeps what it replaced.
    was_retired = bool(row and row.retired_at)
    if row is None:
        row = models.CodeScheme(discipline=discipline, make=make, typology=typology)
        db.add(row)
    row.prefix, row.separator, row.segments = prefix, separator, segments
    row.retired_at = None
    row.updated_by = _email_of(request.session.get("user_id"))
    shape = lambda segs: " ".join(s["key"] + ("" if s["include"] else "(off)") +
                                  ("?" if s["optional"] else "") for s in segs)
    log_activity(db, request, "npd.code_scheme",
                 "%s the %s %s product code%s" % (
                     "Changed" if before and not was_retired else "Set up", make,
                     typology or "default",
                     " (replacing the structure it had before it went to the brand default)"
                     if was_retired else ""),
                 details={"make": make, "typology": typology,
                          "before": shape(before["segments"]) if before else None,
                          "replaced_retired": was_retired,
                          "after": shape(segments), "prefix": prefix, "separator": separator})
    db.commit()
    db.refresh(row)
    return _code_scheme_dict(row)


@app.delete("/api/npd/codes/schemes/{scheme_id}")
def npd_code_delete_scheme(scheme_id: int, request: Request, db: Session = Depends(get_db)):
    """Put one typology back on the brand's default. Its own structure is retired, not deleted,
    so a click made by mistake is undone by restoring it (below) rather than rebuilding it."""
    row = get_or_404(db, models.CodeScheme, scheme_id, "code structure")
    if row.retired_at is not None:
        # Refused rather than answered "ok", so the activity log never records a change that
        # did not happen (a double click, a second tab).
        raise HTTPException(status_code=409, detail="%s already follows the brand default." %
                            (row.typology or "The brand default"))
    row.retired_at = models.now()
    row.updated_by = _email_of(request.session.get("user_id"))
    log_activity(db, request, "npd.code_scheme_delete",
                 "Put the %s %s product code on the brand default (its own structure is kept "
                 "and can be restored)" % (row.make, row.typology or "default"),
                 details={"make": row.make, "typology": row.typology, "scheme_id": row.id,
                          "prefix": row.prefix, "separator": row.separator,
                          "segments": _code_segments(row.segments)})
    db.commit()
    return {"ok": True}


@app.post("/api/npd/codes/schemes/{scheme_id}/restore")
def npd_code_restore_scheme(scheme_id: int, request: Request, db: Session = Depends(get_db)):
    """Bring back a typology's own structure, exactly as it was when it was put on the brand
    default. A segment whose attribute has been removed since is left out, and said so."""
    discipline = current_discipline(request)
    row = get_or_404(db, models.CodeScheme, scheme_id, "code structure")
    if row.retired_at is None:
        raise HTTPException(status_code=409, detail="%s already has its own structure." %
                            (row.typology or "The brand default"))
    known = {a.key for a in db.query(models.CodeAttribute)
             .filter(models.CodeAttribute.discipline == discipline).all()}
    segments = _code_segments(row.segments)
    dropped = [s["key"] for s in segments if s["key"] not in known]
    row.segments = [s for s in segments if s["key"] in known]
    row.retired_at = None
    row.updated_by = _email_of(request.session.get("user_id"))
    log_activity(db, request, "npd.code_scheme_restore",
                 "Restored the %s %s product-code structure" % (row.make, row.typology or "default"),
                 details={"make": row.make, "typology": row.typology, "scheme_id": row.id,
                          "segments": row.segments, "dropped": dropped})
    db.commit()
    db.refresh(row)
    return {"scheme": _code_scheme_dict(row), "dropped": dropped}


@app.post("/api/npd/codes/abbreviations")
def npd_code_set_abbreviation(request: Request, payload: dict = Body(...),
                              db: Session = Depends(get_db)):
    """Set the abbreviation one value contributes to a code ("WITH TRIM" -> "WT"). A blank code
    removes it, so the value falls back to its attribute's rule.

    Two values may share a code on purpose - "0-10" and "0-10V" are one protocol - so a shared
    code is reported back rather than refused, and the panel shows it for someone to judge."""
    discipline = current_discipline(request)
    key = str(payload.get("field") or "").strip()
    attr = _code_attr_or_404(db, discipline, key)
    value = " ".join(str(payload.get("value") or "").split())
    code = product_codes.clean_code(payload.get("code"))
    if not value:
        raise HTTPException(status_code=400, detail="Which value is this the code for?")
    if code and not re.fullmatch(r"[A-Z0-9]+", code):
        raise HTTPException(status_code=400, detail="A code is letters and digits only - a "
                            "separator inside one would make the code impossible to read back.")
    if code and not product_codes.explicit(code):
        raise HTTPException(status_code=400, detail="A code needs at least one letter, so it "
                            "says what it is - 7W rather than 7, 1H rather than 1.")
    rows = db.query(models.FieldCode).filter(models.FieldCode.field == key).all()
    mine = [r for r in rows if product_codes.norm(r.option_value) == product_codes.norm(value)]
    before = mine[0].code if mine else ""
    if not code:
        for r in mine:
            db.delete(r)
    elif mine:
        mine[0].code = code
        for extra in mine[1:]:                  # one value, one row
            db.delete(extra)
    else:
        db.add(models.FieldCode(field=key, option_value=value, code=code))
    if (before or "") != code:
        log_activity(db, request, "npd.code_abbreviation",
                     "Coded %s '%s' as '%s'" % (attr.label, value, code) if code else
                     "Removed the code for %s '%s'" % (attr.label, value),
                     details={"field": key, "value": value, "before": before, "code": code})
    db.commit()
    clashes = [r.option_value for r in rows
               if code and product_codes.clean_code(r.code) == code
               and product_codes.norm(r.option_value) != product_codes.norm(value)]
    # What the value is coded as now that the typed code is set or gone: the rule's code with
    # the attribute's prefix and suffix, exactly as a product's code will carry it.
    ctx = product_codes.load_context(db, discipline)
    effective, source = ctx.code_for(key, value)
    return {"field": key, "value": value, "code": code, "clashes": clashes,
            "rule_code": effective if source == "rule" else "",
            "effective": effective, "source": source if effective else "",
            "suggestion": "" if effective else ctx.suggestion(key, value)}


@app.post("/api/npd/codes/preview")
def npd_code_preview(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """The code a product would get: a saved one by `sheet_id`, or the NPD form's unsaved draft
    by `draft`, configured by `chosen`. Nothing is written - this is what lets the form show the
    code being assembled while it is typed."""
    discipline = current_discipline(request)
    if payload.get("sheet_id"):
        sheet = get_or_404(db, models.Specsheet, int(payload["sheet_id"]), "specsheet")
    else:
        sheet = product_codes.draft_sheet(payload.get("draft") or {})
    scheme = product_codes.scheme_for(db, discipline, sheet.make, sheet.typology)
    # The structure editor previews a structure before it is saved - the whole point is to see
    # what a change does before anybody's codes change.
    if isinstance(payload.get("scheme"), dict):
        draft = payload["scheme"]
        scheme = SimpleNamespace(id=getattr(scheme, "id", None),
                                 typology=getattr(scheme, "typology", ""),
                                 prefix=draft.get("prefix", ""),
                                 separator=draft.get("separator", "-"),
                                 segments=_code_segments(draft.get("segments")))
    if scheme is None:
        return JSONResponse({"scheme": None, "result": None})
    ctx = product_codes.load_context(db, discipline)
    # A InHouse product's library accessories put their own codes into its code - the saved
    # product's, or the ones the form has picked so far (app/accessory_parts.py).
    ctx = product_codes.with_library_accessories(
        db, ctx, sheet=sheet if payload.get("sheet_id") else None,
        part_ids=(payload.get("draft") or {}).get("accessory_parts"))
    chosen = dict(payload.get("chosen") or {})
    result = product_codes.compose(ctx, scheme, sheet, chosen)
    if payload.get("sample"):
        # An example of a finished code: each open choice takes its first option.
        for seg in result["segments"]:
            if seg["status"] == product_codes.CHOOSE and seg["choices"]:
                chosen[seg["key"]] = seg["choices"][0]["value"]
        result = product_codes.compose(ctx, scheme, sheet, chosen)
        result["sample_chosen"] = chosen
    # Whether any configuration of this product would carry a code another product of the brand
    # already has - the one thing a new product's code must not do. Only asked once nothing is
    # missing, since a code with a gap in it is not a code anybody could order by.
    shared = []
    if not result["missing"]:
        sep = scheme.separator if scheme.separator is not None else "-"
        prefix = product_codes.clean_code(scheme.prefix or "")
        mine = {code for code, _c in product_codes.configurations(result, sep, prefix) if code}
        # The brand's other products, coded once and kept until one of them - or an abbreviation,
        # attribute or structure - changes (product_codes.brand_codes). Held products are not in
        # it: never ordered - see npd_codes.
        brand = product_codes.brand_codes(db, discipline, sheet.make)
        skip = {getattr(sheet, "id", None), payload.get("exclude_id")}
        hits = sorted({pid for code in mine for pid in brand.owners(code)} - skip)
        for pid in hits:
            shared.append({"id": pid, "product_name": brand.names.get(pid),
                           "code": brand.first_shared(pid, mine)})
    return JSONResponse({"scheme": _code_scheme_dict(scheme), "result": result,
                         "shared_with": shared})


@app.post("/api/npd/parameters")
def npd_add_parameter(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Add a parameter to a typology's template.

    It persists for the typology, not just this product: the second product of a kind should
    start from a better template than the first did, which is the whole reason the template is
    a table. Marked user_added so a later review can tell it from the seeded rows."""
    typology = (payload.get("typology") or "").strip()
    label = (payload.get("label") or "").strip()
    group_name = (payload.get("group_name") or "").strip()
    if not typology or not label or not group_name:
        raise HTTPException(status_code=400, detail="typology, group_name and label are required")
    discipline = current_discipline(request)
    rows = (db.query(models.ParameterTemplate)
            .filter(models.ParameterTemplate.discipline == discipline,
                    models.ParameterTemplate.typology == typology)
            .order_by(models.ParameterTemplate.sort_order, models.ParameterTemplate.id).all())
    # The group as the template already spells it, when it has it in another case or spacing.
    same_group = [r for r in rows if _npd_group_key(r.group_name) == _npd_group_key(group_name)]
    if same_group:
        group_name = same_group[0].group_name
    # A key the typology already uses would make this parameter share that one's value - the
    # form would type into both, and the product would store one answer for two questions (an
    # IP RATING added under POWER SUPPLY becoming the luminaire's MECHANICAL one). So the key
    # is always one the template does not have yet, even when the caller suggests one.
    taken = {k for (k,) in db.query(models.ParameterTemplate.field_key)
             .filter(models.ParameterTemplate.discipline == discipline,
                     models.ParameterTemplate.typology == typology)}
    suggested = (payload.get("field_key") or "").strip()
    key = (suggested if suggested and suggested not in taken
           else npd.distinct_field_key(group_name, suggested or label, taken))
    clash = (db.query(models.ParameterTemplate)
             .filter(models.ParameterTemplate.discipline == discipline,
                     models.ParameterTemplate.typology == typology,
                     models.ParameterTemplate.group_name == group_name,
                     models.ParameterTemplate.label == label).first())
    if clash:
        raise HTTPException(status_code=409,
                            detail="%s already has a %s parameter under %s"
                                   % (typology, label, group_name))
    row = models.ParameterTemplate(
        discipline=discipline, typology=typology, group_name=group_name, label=label,
        field_key=key, sort_order=len(rows),
        is_configurable=bool(payload.get("is_configurable")), user_added=True,
    )
    # Placed after the last parameter of its own group (a new group goes at the end), and the
    # template renumbered 0..n-1 as a move does - so the sheet prints it inside its group.
    at = (rows.index(same_group[-1]) + 1) if same_group else len(rows)
    rows.insert(at, row)
    for n, r in enumerate(rows):
        if r.sort_order != n:
            r.sort_order = n
    db.add(row)
    db.commit()
    db.refresh(row)
    return _npd_template_dict(row)


@app.delete("/api/npd/parameters/{param_id}")
def npd_delete_parameter(param_id: int, request: Request, db: Session = Depends(get_db)):
    """Remove a parameter from a typology - any parameter, the seeded ones included (owner, 18 Nov
    2026: "allow the user to delete any parameter from any group"). Until then only a parameter
    someone had added could go, and a grazer's template kept rows nobody wanted on its sheets.

    It goes from the template and from every product of the typology authored in NPD: the value it
    holds, its options, and the line its sheet prints under the parameter's group. That is one
    step, so no product is left printing a parameter its typology no longer has. A product read
    from a manufacturer's PDF is left as its sheet says.

    Refused, naming what is in the way, while the parameter still does a job elsewhere:
      * a product prices it - an adder on one of its options, or a price row keyed on it (a power
        supply priced per CONTROL): the Configurator would stop asking for it and quotes would
        silently lose that price, and prices already entered never vanish (CLAUDE.md rule 4);
      * the brand's product-code structure for this typology spells it (a segment that is not
        optional): every product's code would go blank;
      * an NPD rule depends on it or acts on it, which would be left pointing at nothing.
    A product deleted from NPD (retired, kept for the BOQ lines that use it) is not touched: its
    lines keep their sheet exactly as it was, and nobody could open it to clear a price that blocked
    the removal. The template row and what each product held go into the activity log, so a
    removal can be put back by hand."""
    row = get_or_404(db, models.ParameterTemplate, param_id, "parameter")
    key, label, group = row.field_key, row.label, row.group_name
    products = (db.query(models.Specsheet)
                .filter(models.Specsheet.discipline == row.discipline,
                        models.Specsheet.typology == row.typology,
                        models.Specsheet.origin == "npd",
                        or_(models.Specsheet.approval_status.is_(None),
                            models.Specsheet.approval_status != NPD_DELETED)).all())

    def named(items):
        names = sorted(set(items))
        return ", ".join(names[:5]) + (" and more" if len(names) > 5 else "")

    priced = [s for s in products if (s.price_adders or {}).get(key)
              or any(key in (p.config or {}) for p in (s.price_entries or []))]
    if priced:
        raise HTTPException(
            status_code=409,
            detail="%s affects the price of %d product%s (%s). Remove those prices in the product first, "
                   "then delete the parameter." % (label, len(priced), "" if len(priced) == 1 else "s",
                                                   named(s.product_name or "#%d" % s.id for s in priced)))
    coding = []
    for make in sorted({(s.make or "").strip() for s in products if (s.make or "").strip()}):
        scheme = product_codes.scheme_for(db, row.discipline, make, row.typology)
        if scheme is not None and any(seg.get("key") == key and seg.get("include", True) and not seg.get("optional")
                                      for seg in (scheme.segments or [])):
            coding.append("%s %s" % (make, scheme.typology or "default"))
    if coding:
        raise HTTPException(
            status_code=409,
            detail="%s is part of the product code (%s): every %s product's code would go blank. Take it out of "
                   "the code structure in Nomenclature first, then delete the parameter." % (
                       label, named(coding), row.typology))
    makes = {(s.make or "").strip() for s in products}
    ids = {s.id for s in products}

    def rule_applies(r):
        if r.specsheet_id is not None:
            return r.specsheet_id in ids
        brand = [t for t in (r.triggers or []) if t.get("field") == RULE_BRAND_FIELD]
        return not brand or any(v in makes for t in brand for v in (t.get("values") or []))
    rules = [r for r in db.query(models.ConfigRule).filter(models.ConfigRule.typology == row.typology)
             if rule_applies(r) and (key in (r.target_fields or [])
                                     or any(t.get("field") == key for t in (r.triggers or [])))]
    if rules:
        raise HTTPException(
            status_code=409,
            detail="%d NPD rule%s use%s %s (rule %s). Change or remove %s in the product's Rules first, then "
                   "delete the parameter." % (len(rules), "" if len(rules) == 1 else "s", "s" if len(rules) == 1 else "",
                                              label, named("#%d" % r.id for r in rules),
                                              "it" if len(rules) == 1 else "them"))
    # Another row of the same typology may print the same label under another group (IP RATING
    # under MECHANICAL and under POWER SUPPLY): only this group's line goes from a sheet.
    shared_key = any(t.id != row.id and t.field_key == key for t in
                     db.query(models.ParameterTemplate).filter(models.ParameterTemplate.discipline == row.discipline,
                                                               models.ParameterTemplate.typology == row.typology))
    removed = []
    for s in products:
        fields, options = dict(s.fields or {}), dict(s.options or {})
        params = {g: dict(v) for g, v in (s.params or {}).items()}
        held = {"product_id": s.id}
        if not shared_key and key in fields:
            held["field"] = fields.pop(key)
        if not shared_key and key in options:
            held["options"] = options.pop(key)
        if group in params and label in params[group]:
            held["printed"] = params[group].pop(label)
            if not params[group]:
                params.pop(group)
        if len(held) > 1:
            s.fields, s.options, s.params = fields, options, params
            removed.append(held)
    template_row = {c.name: _jsonable(getattr(row, c.name)) for c in models.ParameterTemplate.__table__.columns}
    db.delete(row)
    db.flush()
    log_activity(db, request, "npd.parameter_delete",
                 "Removed %s (%s) from %s and %d product%s" % (
                     label, group, row.typology, len(removed), "" if len(removed) == 1 else "s"),
                 details={"parameter": template_row, "products": removed})
    db.commit()
    return {"ok": True, "products": len(removed)}


@app.post("/api/npd/parameters/{param_id}/move")
def npd_move_parameter(param_id: int, request: Request, payload: dict = Body(...),
                       db: Session = Depends(get_db)):
    """Move a parameter one place up or down within its group - the order the sheet prints it in.

    Like adding a parameter, this changes the typology's template, so every product of the kind
    prints in the new order (an existing product takes it up the next time it is saved, since
    its printed parameters are composed on save).

    A parameter only swaps with a neighbour from its own group, never crossing into another
    banner. The neighbour is the nearest row of the same group, not simply the next row: a
    parameter added to an earlier group used to land at the very end of the template, after
    other groups, and this lets someone walk it back up to where it belongs.

    The whole template is renumbered 0..n-1 afterwards rather than swapping two sort_orders,
    so rows that happen to share a number can never make a move silently do nothing."""
    direction = (payload.get("direction") or "").strip().lower()
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")
    row = get_or_404(db, models.ParameterTemplate, param_id, "parameter")
    rows = (db.query(models.ParameterTemplate)
            .filter(models.ParameterTemplate.discipline == row.discipline,
                    models.ParameterTemplate.typology == row.typology)
            .order_by(models.ParameterTemplate.sort_order, models.ParameterTemplate.id).all())
    i = next(n for n, r in enumerate(rows) if r.id == row.id)
    step = -1 if direction == "up" else 1
    j = i + step
    while 0 <= j < len(rows) and rows[j].group_name != row.group_name:
        j += step
    if not 0 <= j < len(rows):
        raise HTTPException(status_code=400,
                            detail="%s is already the %s of %s"
                                   % (row.label, "first" if direction == "up" else "last",
                                      row.group_name))
    neighbour = rows[j]
    rows.pop(i)
    at = rows.index(neighbour)
    rows.insert(at if direction == "up" else at + 1, row)
    for n, r in enumerate(rows):
        if r.sort_order != n:
            r.sort_order = n
    log_activity(db, request, "npd.parameter_move",
                 "Moved %s %s past %s in %s" % (row.label, direction, neighbour.label, row.typology),
                 details={"typology": row.typology, "group": row.group_name,
                          "parameter": row.label, "direction": direction})
    db.commit()
    return {"ok": True}


@app.get("/api/npd/products")
def npd_list_products(request: Request, db: Session = Depends(get_db)):
    """Products authored here, newest first - the NPD tab's own list.

    `make` and `power_supply` are here for the classification panel: since this became the
    only place products are priced, the panel has to answer "what is priced and what is
    not" across the whole catalogue, drivers and power supplies included. A driver is an
    ordinary product with its own typology, so it classifies itself; a power supply is not
    - it is a component of a luminaire whose price depends on the control chosen, so it
    travels with the product it belongs to.
    """
    rows = (db.query(models.Specsheet)
            .filter(models.Specsheet.origin == "npd",
                    models.Specsheet.discipline == current_discipline(request),
                    or_(models.Specsheet.approval_status.is_(None),
                        models.Specsheet.approval_status != NPD_DELETED))
            .options(selectinload(models.Specsheet.price_entries))
            .order_by(models.Specsheet.id.desc()).all())

    def power_supply_prices(sheet):
        out = {}
        for entry in (sheet.price_entries or []):
            cfg = entry.config or {}
            accessory = str(cfg.get("accessory") or "")
            if "".join(c for c in accessory.upper() if c.isalnum()) == "POWERSUPPLY":
                out[cfg.get("control") or "Any control"] = entry.price
        return out

    # Each product's code as it stands, so the list shows which products are fully classified
    # without opening them. The identity part only - configuration is chosen when quoting.
    # From each brand's products coded once and kept (product_codes.brand_codes): composing all
    # 140 again was most of this list's time, on every visit to the NPD tab.
    discipline = current_discipline(request)
    brands, schemes, held_ctx = {}, {}, []

    def coded(sheet):
        make = sheet.make or ""
        if make not in brands:
            brands[make] = product_codes.brand_codes(db, discipline, make)
        got = brands[make].results.get(sheet.id)
        if got is not None:
            return got[0]
        # Not in it: no structure codes it - or it was saved between the list being read and
        # the brand being coded. Asked the way it always was.
        key = (make, sheet.typology or "")
        if key not in schemes:
            schemes[key] = product_codes.scheme_for(db, discipline, sheet.make, sheet.typology)
        if schemes[key] is None:
            return None
        if not held_ctx:
            held_ctx.append(product_codes.load_context(db, discipline))
        return product_codes.compose(held_ctx[0], schemes[key], sheet, {})

    def identity(sheet):
        r = coded(sheet)
        if r is None:
            return None
        # What names it, in words and in code order - the card shows these, so two products
        # sharing a catalogue name are told apart without opening either.
        named = [{"label": x["label"], "value": x["value"], "key": x["key"]}
                 for x in r["segments"] if x["part"] == "identity" and x["value"]
                 and x["key"] not in ("typology", "series", "family", "model_no")]
        # Complete means nothing is missing. A choice made when quoting - a grazer's fixed or
        # adjustable bracket - is not a gap, so long as each of its options has a code.
        gaps = [x for x in r["segments"] if x["part"] == "identity" and (
            x["status"] in (product_codes.NO_VALUE, product_codes.NO_CODE)
            or (x["status"] == product_codes.CHOOSE and any(not c["code"] for c in x["choices"])))]
        return {"code": r["identity_code"], "partial": r["identity_partial"],
                "complete": not gaps, "values": named}

    return JSONResponse([{
        "id": s.id, "product_name": s.product_name, "typology": s.typology,
        "identity_code": identity(s),
        "approval_status": s.approval_status or "pending",
        "make": s.make, "series": s.series, "family": s.family, "variant": s.variant,
        "model_no": (s.fields or {}).get("model_no", ""),
        "base_price": s.base_price,
        "adder_count": sum(len(v) for v in (s.price_adders or {}).values() if isinstance(v, dict)),
        "power_supply": power_supply_prices(s),
        "thumb_url": image_urls(s)[1],
        "created_at": s.created_at.isoformat() if s.created_at else None,
        # What the list can be searched by - "every 12W product", "IP65 in 3000K" - and the adder
        # each option carries, so a search for a price review shows the money beside the match.
        "params": _npd_search_params(s),
        "price_adders": s.price_adders or {},
    } for s in rows])


# The parameters the NPD list is searched by: what a person narrows a price review to. Not every
# field a product records - "5 YEARS" of warranty or an operating temperature would only make a
# plain word match things nobody meant.
NPD_SEARCH_PARAMS = ("wattage", "cct", "beam_angle", "cri", "ip_rating", "finish", "control",
                     "cutout_dimensions", "luminaire_length", "trim_details", "mounting",
                     "adjustability", "optics_type", "reflector_finish", "lumen_package",
                     "input_voltage")


def _npd_search_params(sheet):
    """{parameter: [values]} - the options a product offers where it keeps a list, otherwise its
    printed value split the way NPD splits one (npd.split_options: "10W/M" stays one value)."""
    fields, options = sheet.fields or {}, sheet.options or {}
    out = {}
    for key in NPD_SEARCH_PARAMS:
        listed = options.get(key)
        values = ([str(v).strip() for v in listed] if isinstance(listed, list)
                  else npd.split_options(fields.get(key) or ""))
        values = [v for v in values if v]
        if values:
            out[key] = values
    return out


def _money(v):
    return None if v in (None, "") else round(float(v), 2)


def _stored_accessory_prices(sheet, skip=()):
    """{accessory: price} as the edit form is shown it (see the NPD detail endpoint) - the
    product's own, leaving out the accessories whose keys are in `skip`."""
    return {(p.config or {}).get("accessory"): _money(p.price)
            for p in (sheet.price_entries or []) if (p.config or {}).get("accessory")
            and _acc_key((p.config or {}).get("accessory")) not in skip}


def _acc_key(name):
    """accessories.fold, under a name the NPD endpoints' local `accessories` lists cannot shadow."""
    return accessories.fold(name)


def _price_changes(before_base, before_adders, before_acc, base, adders, acc):
    """Each price that differs, as (what, before, after) - for the permission check and for
    the activity log, which records every price change with its old and new figure."""
    out = []
    if _money(before_base) != _money(base):
        out.append(("base price", _money(before_base), _money(base)))
    keys = set(before_adders or {}) | set(adders or {})
    for field in sorted(keys):
        old, new = (before_adders or {}).get(field) or {}, (adders or {}).get(field) or {}
        for value in sorted(set(old) | set(new)):
            if _money(old.get(value)) != _money(new.get(value)):
                out.append(("%s %s adder" % (field.replace("_", " "), value),
                            _money(old.get(value)), _money(new.get(value))))
    for name in sorted(set(before_acc) | set(acc)):
        if _money(before_acc.get(name)) != _money(acc.get(name)):
            out.append(("accessory %s" % name, _money(before_acc.get(name)), _money(acc.get(name))))
    return out


def _describe_price_changes(changes):
    def fmt(v):
        return "none" if v is None else "Rs %s" % format(v, ",g")
    parts = ["%s %s \u2192 %s" % (what, fmt(a), fmt(b)) for what, a, b in changes[:3]]
    more = len(changes) - 3
    return "; ".join(parts) + (" and %d more" % more if more > 0 else "")


@app.post("/api/npd/products")
async def npd_create_product(
    request: Request,
    payload: str = Form(...),
    product_image: Optional[UploadFile] = File(None),
    line_diagram: Optional[UploadFile] = File(None),
    more_diagrams: Optional[list[UploadFile]] = File(None),
    db: Session = Depends(get_db),
):
    """Create a product from the NPD form.

    `payload` is the JSON body (multipart, because the two pictures come with it):

        {"product_name", "typology", "series", "family", "variant", "model_no", "make",
         "values": {field_key: "printed value"},
         "options": {field_key: ["WHITE", "BLACK"]},
         "base_price": 1000.0,
         "price_adders": {"finish": {"BLACK": 100}}}

    The parameters are assembled against the typology's template, so what gets stored is the
    same params/fields/options shape the PDF parser produces - an authored product and a
    parsed one are the same kind of row from here on.
    """
    import json as _json
    try:
        body = _json.loads(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="payload is not valid JSON")

    name = (body.get("product_name") or "").strip()
    typology = (body.get("typology") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Product name is required")
    if not typology:
        raise HTTPException(status_code=400, detail="Typology is required")

    discipline = current_discipline(request)
    # One composer for create and update, so the two cannot produce different records for
    # the same product - see _npd_compose().
    params, fields, options, base_price, adders, acc_list, title = _npd_compose(
        db, body, discipline)
    # Accessories picked from the library (app/accessory_parts.py) - checked before anything is
    # written, so a stale pick refuses the save rather than leaving half a product behind.
    make = (body.get("make") or "InHouse").strip()
    picked = _npd_picked(db, body, make, discipline)
    if accessory_parts.is_part_brand(make):
        _npd_library_list(db, typology, discipline, picked, acc_list, fields, options, params, [], "")

    # A new product is a specification first. Its prices are a second permission, so someone
    # trusted to describe products but not to price them can still add one - unpriced.
    if not user_can(request, "npd.edit"):
        raise HTTPException(status_code=403, detail="Adding a product needs \u201cCreate and "
                            "edit products\u201d - ask an admin.")
    new_acc = {a["name"]: _money(a["price"]) for a in acc_list if a.get("price") is not None}
    if _price_changes(None, {}, {}, base_price, adders, new_acc) and \
            not user_can(request, "prices.edit"):
        raise HTTPException(status_code=403, detail="You can add products but not price them. "
                            "Leave the prices empty - an admin, or someone with \u201cChange "
                            "prices\u201d, can add them afterwards.")
    # Before anything is written, so a refused picture leaves no half-made product behind.
    for upload in (product_image, line_diagram, *(more_diagrams or [])):
        if upload is not None and upload.filename:
            _picture_suffix(upload.filename, ".png")

    sheet = models.Specsheet(
        discipline=discipline, origin="npd", approval_status="pending",
        filename="%s.pdf" % re.sub(r"[^A-Za-z0-9 _-]+", "", name).strip(),
        product_name=name, typology=typology,
        series=(body.get("series") or "").strip(),
        family=(body.get("family") or "").strip(),
        variant=(body.get("variant") or "").strip(),
        make=(body.get("make") or "InHouse").strip(),
        category=(body.get("category") or title or typology).strip(),
        environment=body.get("environment") or [],
        params=params, fields=fields, options=options,
        base_price=base_price, price_adders=adders,
        diagrams=[],
    )
    db.add(sheet)
    db.commit()
    db.refresh(sheet)

    # Pictures are saved under the specsheet's own id, so a re-upload replaces rather than
    # accumulates, and image_urls()' version token changes with the file's mtime.
    diagrams = _npd_store_pictures(sheet, [], product_image, line_diagram, more_diagrams)

    if diagrams:
        sheet.diagrams = diagrams
        sheet.image_path = diagrams[0]["path"]
        db.commit()
        db.refresh(sheet)

    # An accessory price is a real price for a real orderable thing, so it lands in the same
    # table a price read off a manufacturer's sheet does - scoped to this product, because the
    # same accessory legitimately costs different amounts under different parents. Written by
    # the same helper the update path uses.
    priced = _npd_write_accessory_prices(db, sheet, acc_list, discipline)
    if picked:
        accessory_parts.set_product_accessories(db, sheet, [part.id for part, _r in picked])
    db.commit()
    accessories.forget()          # the catalogue now has this product's accessories

    return {"id": sheet.id, "product_name": sheet.product_name,
            "accessories": len(acc_list) + len(picked), "accessory_prices": priced,
            "typology": sheet.typology, "base_price": sheet.base_price,
            "specsheet_url": "/api/specsheets/%d/generated-specsheet.pdf" % sheet.id}


@app.get("/api/specsheets/{sheet_id}/generated-specsheet.pdf")
def npd_generated_specsheet(sheet_id: int, preview: bool = Query(False),
                            db: Session = Depends(get_db)):
    """The InHouse-format specsheet for an authored product, generated from its definition.

    Regenerated on request rather than stored, so it can never disagree with the product it
    describes - editing the definition changes the sheet, with nothing to re-issue."""
    sheet = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    if sheet.origin != "npd":
        raise HTTPException(
            status_code=400,
            detail="This product was read from a manufacturer's PDF - its own specsheet is "
                   "the source. Only products authored in NPD have a generated sheet.")
    images = [(d.get("path"), d.get("label")) for d in (sheet.diagrams or []) if d.get("path")]
    safe = "".join(c if c.isalnum() or c in " -_" else "_"
                   for c in (sheet.product_name or "Product")).strip() or "Product"
    out_path = EXPORTS_DIR / ("%s_Specsheet_%d.pdf" % (safe, sheet.id))
    npd.generate_specsheet_pdf(sheet, out_path, images=images)
    db.close()
    return FileResponse(out_path, media_type="application/pdf", filename=out_path.name,
                        content_disposition_type="inline" if preview else "attachment")


# ---------- driver and power-supply prices ----------
#
# A driver is quoted as its own BOQ line, so its price is not a product's price and does not
# live on a specsheet. It used to live in static/app.js as four hand-edited tables, which made
# "a driver got dearer" a code change. It is data now, and these are what the driver form and
# the NPD classification tab read.

@app.get("/api/npd/driver-prices")
def npd_driver_prices(request: Request, db: Session = Depends(get_db)):
    """Every driver combination, and what it costs where that is known.

    Shaped for the driver form's own cascade - type, then control, then IP, then wattage -
    so the form does not have to know the shape of the table, only how to walk it. `price`
    is null for a combination that is real but has never been priced; the form leaves those
    at 0 for someone to type, exactly as it did when these were constants.
    """
    rows = (db.query(models.DriverPrice)
            .filter(models.DriverPrice.discipline == current_discipline(request))
            .order_by(models.DriverPrice.sort_order, models.DriverPrice.driver_type,
                      models.DriverPrice.id).all())
    types, order = {}, []
    for r in rows:
        if r.driver_type not in types:
            types[r.driver_type] = {}
            order.append(r.driver_type)
        by_control = types[r.driver_type].setdefault(r.control_type, {})
        by_control.setdefault(r.ip_rating, []).append(
            {"id": r.id, "wattage": r.wattage, "price": r.price,
             "source_note": r.source_note or ""})
    return JSONResponse({"driver_types": order, "rules": types})


@app.post("/api/npd/driver-prices")
def npd_create_driver_price(request: Request, body: dict = Body(...),
                            db: Session = Depends(get_db)):
    """Add a driver combination, or a whole new driver type - the same call either way, since
    a driver type is only the set of combinations that name it."""
    fields = {k: str(body.get(k) or "").strip()
              for k in ("driver_type", "control_type", "ip_rating", "wattage")}
    missing = [k for k, v in fields.items() if not v]
    if missing:
        raise HTTPException(status_code=400,
                            detail="Missing: %s" % ", ".join(m.replace("_", " ") for m in missing))
    price = body.get("price")
    try:
        price = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Price must be a number, or left blank")

    discipline = current_discipline(request)
    clash = (db.query(models.DriverPrice)
             .filter(models.DriverPrice.discipline == discipline,
                     models.DriverPrice.driver_type == fields["driver_type"],
                     models.DriverPrice.control_type == fields["control_type"],
                     models.DriverPrice.ip_rating == fields["ip_rating"],
                     models.DriverPrice.wattage == fields["wattage"]).first())
    if clash:
        raise HTTPException(
            status_code=400,
            detail="That combination already exists - edit its price instead of adding it again.")

    # A new type goes after the ones already there; an existing one keeps its place.
    sibling = (db.query(models.DriverPrice)
               .filter(models.DriverPrice.discipline == discipline,
                       models.DriverPrice.driver_type == fields["driver_type"]).first())
    if sibling:
        sort_order = sibling.sort_order
    else:
        highest = db.query(func.max(models.DriverPrice.sort_order)).filter(
            models.DriverPrice.discipline == discipline).scalar()
        sort_order = (highest or 0) + 1

    row = models.DriverPrice(
        discipline=discipline, price=price, sort_order=sort_order,
        source_note=str(body.get("source_note") or "").strip(), **fields)
    db.add(row)
    db.commit()
    return {"id": row.id, "ok": True}


@app.put("/api/npd/driver-prices/{row_id}")
def npd_update_driver_price(row_id: int, request: Request, body: dict = Body(...),
                            db: Session = Depends(get_db)):
    """Change what a combination costs. Sending an empty price means "real, but not priced" -
    which is a thing worth being able to say, so it is not treated as zero."""
    row = get_or_404(db, models.DriverPrice, row_id, "driver price")
    before = row.price
    if "price" in body:
        raw = body.get("price")
        try:
            row.price = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail="Price must be a number, or left blank")
    if "source_note" in body:
        row.source_note = str(body.get("source_note") or "").strip()

    def money(v):
        return "not priced" if v is None else "Rs %s" % format(float(v), ",.2f").rstrip("0").rstrip(".")

    log_activity(db, request, "price.driver",
                 "Changed driver rate %s / %s / %s / %s: %s → %s" % (
                     row.driver_type, row.control_type, row.ip_rating, row.wattage,
                     money(before), money(row.price)),
                 details={"driver_price_id": row.id, "before": before, "after": row.price})
    db.commit()
    return {"ok": True, "price": row.price}


@app.delete("/api/npd/driver-prices/{row_id}")
def npd_delete_driver_price(row_id: int, db: Session = Depends(get_db)):
    """Remove a combination the studio does not sell. Deleting every row of a driver type
    removes the type, since a type is only the combinations that name it."""
    row = get_or_404(db, models.DriverPrice, row_id, "driver price")
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.get("/api/npd/price")
def npd_price(request: Request, sheet_id: int = Query(...), db: Session = Depends(get_db)):
    """What one configuration of an authored product costs, itemised.

    Every query parameter other than sheet_id is read as part of the configuration, so the
    caller does not have to know which fields this particular product is configurable on:
        /api/npd/price?sheet_id=12&wattage=12W&finish=BLACK
    """
    sheet = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    config = {k: v for k, v in request.query_params.items() if k != "sheet_id"}
    return {"price": npd.price_for(sheet, config),
            "breakdown": npd.price_breakdown(sheet, config)}


@app.post("/api/npd/typologies")
def npd_create_typology(request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Create a typology and give it a starting parameter template.

    A new typology starts from npd.STARTER_TEMPLATE - the shape a InHouse sheet actually has -
    rather than from nothing. A blank sheet is not a useful place to begin describing a
    luminaire, and everything the studio issues should read the same way whether or not there
    were products of that kind before. Every parameter can then be renamed, removed or added
    to, so this is a starting point and not a constraint."""
    name = (payload.get("typology") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Typology name is required")
    discipline = current_discipline(request)
    existing = (db.query(models.ParameterTemplate)
                .filter(models.ParameterTemplate.discipline == discipline,
                        func.lower(models.ParameterTemplate.typology) == name.lower()).first())
    if existing:
        raise HTTPException(status_code=409,
                            detail="%s already has a parameter template" % existing.typology)
    for i, (group, label, key, configurable) in enumerate(npd.STARTER_TEMPLATE):
        db.add(models.ParameterTemplate(
            discipline=discipline, typology=name, group_name=group, label=label,
            field_key=key, sort_order=i, is_configurable=configurable, user_added=True))
    db.commit()
    return {"typology": name, "parameters": len(npd.STARTER_TEMPLATE)}


NPD_STATUSES = ("pending", "approved", "held")
# A product deleted from NPD while BOQ lines still use it (owner, 18 Nov 2026). Its row stays, so
# those BOQs keep their picture, sheet and options and export as they did; it is out of NPD and out
# of every picker (they all take approved products only), and its lines cannot be reconfigured.
# Never set through the status route: only deleting does it.
NPD_DELETED = "deleted"


def _npd_live_or_404(sheet):
    """A product deleted from NPD (NPD_DELETED) is not opened, edited or approved again."""
    if (sheet.approval_status or "") == NPD_DELETED:
        raise HTTPException(status_code=404, detail="This product was deleted from NPD. BOQs that use it keep it as it was.")


@app.put("/api/npd/products/{sheet_id}/status")
def npd_set_status(sheet_id: int, request: Request, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Approve a drafted product, or hold it.

    Approving is what puts it into the Specsheet Library and therefore into the Configurator,
    Match and the BOQ pickers - up to that point the definition exists only in the NPD tab,
    where the person writing it can see it. "held" is a deliberate no, kept distinct from
    "pending" so a product somebody looked at and parked is not mistaken for one nobody has
    read yet; both are equally invisible to everyone else."""
    status = (payload.get("approval_status") or "").strip().lower()
    if status not in NPD_STATUSES:
        raise HTTPException(status_code=400,
                            detail="status must be one of %s" % ", ".join(NPD_STATUSES))
    sheet = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    _npd_live_or_404(sheet)
    if sheet.origin != "npd":
        raise HTTPException(
            status_code=400,
            detail="Only products authored in NPD are approved here - everything read from a "
                   "manufacturer's PDF is in the library already.")
    if status == "approved" and sheet.base_price is None:
        raise HTTPException(
            status_code=400,
            detail="This product has no base price yet. Approving it would put a product into "
                   "the library that no BOQ can price.")
    sheet.approval_status = status
    log_activity(db, request, "npd.status", "%s '%s'" % (
        "Approved" if status == "approved" else "Put on hold",
        sheet.product_name or "untitled"), details={"specsheet_id": sheet.id, "status": status})
    db.commit()
    return {"id": sheet.id, "approval_status": sheet.approval_status}


def _jsonable(value):
    """A column's value as the activity log can store it."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


@app.delete("/api/npd/products/{sheet_id}")
def npd_delete_product(sheet_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete a product authored in NPD - one discontinued, or entered wrongly. Admins only
    (_required_permission).

    A product on BOQ lines is retired, not erased (owner, 18 Nov 2026: "the BOQ will be exported as
    is, just the user won't be able to further edit that using the configurator"). A line points at
    its product for its picture, its specification sheet and its options, so the row stays, marked
    NPD_DELETED: it leaves NPD and every picker, its lines export exactly as before and cannot be
    reconfigured, and nothing of it - prices, rules - is touched. Only its links to library
    accessories go, so the accessory library no longer lists it as a taker. A product on no BOQ is
    deleted outright, as below.

    What goes with it is kept where it is not the product's alone:
      * a price-sheet row mapped to it goes back to List Pricing's waiting list, unmapped, so a
        price somebody entered never vanishes with the product (CLAUDE.md rule 4);
      * a configuration rule written for this one product is deleted - it can apply to nothing
        else. Its contents are kept in the log.
      * its links to library accessories go with it (the database removes them); the
        accessories themselves stay in the library, and the log records which it took.
    The whole product, as it was, is written into the activity log's details, so a mistaken
    delete can be put back by hand."""
    sheet = get_or_404(db, models.Specsheet, sheet_id, "product")
    _npd_live_or_404(sheet)
    if sheet.origin != "npd":
        raise HTTPException(
            status_code=400,
            detail="Only products authored in NPD are deleted here - a manufacturer's PDF is "
                   "deleted from the Specsheet Library.")
    lines = db.query(models.BOQItem).filter(models.BOQItem.specsheet_id == sheet.id).all()
    if lines:
        boqs = {}
        for line in lines:
            boq = line.room.floor.boq if line.room and line.room.floor else None
            if boq is not None:
                boqs[boq.id] = _boq_display_name(boq)
        names = sorted(boqs.values())
        before = sheet.approval_status or ""
        links = accessory_parts.links_of(db, [sheet.id]).get(sheet.id, [])
        taken = [l.part_id for l in links]
        for link in links:
            db.delete(link)
        sheet.approval_status = NPD_DELETED
        db.flush()
        log_activity(db, request, "npd.delete", "Deleted product '%s' - kept on %d BOQ line%s" % (
            sheet.product_name or "untitled", len(lines), "" if len(lines) == 1 else "s"),
                     details={"specsheet_id": sheet.id, "retired": True, "status_before": before,
                              "boq_lines": [l.id for l in lines], "boqs": names, "accessory_parts": taken})
        db.commit()
        accessories.forget()
        return {"ok": True, "retired": True, "boq_lines": len(lines), "boqs": names,
                "prices_released": 0, "rules_deleted": 0}

    rules = db.query(models.ConfigRule).filter(models.ConfigRule.specsheet_id == sheet.id).all()
    prices = list(sheet.price_entries or [])
    snapshot = {c.name: _jsonable(getattr(sheet, c.name)) for c in models.Specsheet.__table__.columns}
    removed_rules = [{c.name: _jsonable(getattr(r, c.name)) for c in models.ConfigRule.__table__.columns}
                     for r in rules]
    for row in prices:
        row.specsheet_id = None
        row.status = "open"
    for rule in rules:
        db.delete(rule)
    db.flush()
    log_activity(db, request, "npd.delete", "Deleted product '%s'" % (sheet.product_name or "untitled"),
                 details={"specsheet_id": sheet.id, "make": sheet.make or "", "typology": sheet.typology or "",
                          "prices_released": [p.id for p in prices], "rules_deleted": removed_rules,
                          "accessory_parts": [l.part_id for l in accessory_parts.links_of(db, [sheet.id]).get(sheet.id, [])],
                          "product": snapshot})
    db.delete(sheet)
    db.commit()
    accessories.forget()          # the catalogue counts this product's accessories
    return {"ok": True, "prices_released": len(prices), "rules_deleted": len(rules)}



def _npd_compose(db, body, discipline, templates=None):
    """Turn an NPD payload into everything a Specsheet needs: the printed parameters, the
    flattened fields, the option lists, the pricing and the accessory list.

    Shared by create and update so the two cannot drift - an edit has to produce exactly what
    creating the same product from scratch would, or a product would mean different things
    depending on how it got there."""
    typology = (body.get("typology") or "").strip()
    if templates is None:
        templates = (db.query(models.ParameterTemplate)
                     .filter(models.ParameterTemplate.typology == typology,
                             models.ParameterTemplate.discipline == discipline)
                     .order_by(models.ParameterTemplate.sort_order,
                               models.ParameterTemplate.id).all())
    submitted = body.get("values") or {}
    params, fields, options = npd.assemble(
        templates, submitted, body.get("options") or {},
        configurable_keys=body.get("affects") or None)

    # assemble() only emits a field for a parameter the typology's template names, which is
    # right for what the sheet prints but wrong for what the product knows. A parsed product
    # carries more than the template does - product_code, luminaire_type, the detail the
    # Configurator matches on - and none of it is shown in the form, so it cannot have been
    # deliberately cleared. Dropping it turned saving a product with no edits into a way of
    # losing its manufacturer's code. Anything the form was given and did not ask about is
    # carried straight back.
    for key, value in submitted.items():
        if key not in fields and str(value or "").strip():
            fields[key] = value

    model_no = (body.get("model_no") or "").strip()
    if model_no:
        fields["model_no"] = model_no

    # Sub-typology is how the product is installed - Recessed, Surface, Pendant. Held as a
    # field of its own rather than buried in the description line, so it can be filtered on
    # and, once it has a FieldCode row, contribute its own segment to a product code.
    sub_typology = (body.get("sub_typology") or "").strip()
    if sub_typology:
        fields["sub_typology"] = sub_typology

    accessories = []
    for entry in (body.get("accessories") or []):
        # A library accessory is picked by id (_npd_picked) - its name is the library's, not typed.
        if isinstance(entry, dict) and entry.get("part_id") not in (None, ""):
            continue
        acc_name = str((entry or {}).get("name") or "").strip()
        if not acc_name:
            continue
        raw = (entry or {}).get("price")
        try:
            price = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        accessories.append({"name": acc_name, "price": price})
    if accessories:
        names = [a["name"] for a in accessories]
        options["accessories"] = names
        # The accessory list is what each accessory COSTS; the ACCESSORIES parameter is what
        # the sheet PRINTS, and the two are not the same text. The list drops "NIL" - the
        # datasheet's own way of saying a plain fixture is an option - because offering "NIL"
        # in the accessory picker is nonsense, and it gains any accessory that carries a price
        # without being listed. Rebuilding the printed line from it therefore rewrote the
        # sheet on every save. The printed line is an ordinary parameter value and arrives
        # with the others; it is only composed here when there is none, which is a product
        # being authored from nothing.
        if not str(fields.get("accessories") or "").strip():
            fields["accessories"] = npd.OPTION_SEPARATOR.join(names)
        for t in templates:
            if t.field_key == "accessories":
                params.setdefault(t.group_name, {})[t.label] = fields["accessories"]
                break

    base_price = body.get("base_price")
    try:
        base_price = float(base_price) if base_price not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Base price must be a number")

    adders = {}
    for key, per_value in (body.get("price_adders") or {}).items():
        if not isinstance(per_value, dict):
            continue
        kept = {}
        for value, amount in per_value.items():
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                continue
            if amount:
                kept[str(value)] = amount
        if kept:
            adders[key] = kept

    # The line the sheet prints in its first bar. Composed from the parts above, but only as
    # a starting point: a real InHouse description line carries wording no composition can
    # produce - "Flexible LED Strip (Standard)", "Inground LED Uplight - Fixed - 24VDC", the
    # rail voltage on a track light. Deriving it and writing the result back turned
    # "Semi Recessed LED Downlight - Adjustable - With Trim" into "LED Downlight - Adjustable
    # - With Trim" the moment anybody edited the product, on 127 of 135 of them. So the form
    # holds the line itself and sends it, and what it sends is what prints.
    typed = str(body.get("description_line") or "").strip()
    title = typed or npd.compose_title(sub_typology, typology, fields)
    return params, fields, options, base_price, adders, accessories, title


def _npd_picked(db, body, make, discipline):
    """The library accessories an NPD payload picks - entries {"part_id", "replaces"} - in order, as
    [(AccessoryPart, the datasheet name it replaces or "")]. Library accessories are picked, never
    typed (app/accessory_parts.py), so these carry no name or price of their own."""
    out, seen = [], set()
    for entry in body.get("accessories") or []:
        if not isinstance(entry, dict) or entry.get("part_id") in (None, ""):
            continue
        if not accessory_parts.is_part_brand(make):
            raise HTTPException(status_code=400, detail="The accessory library is for %s products." %
                                " and ".join(accessory_parts.PART_BRANDS))
        try:
            part_id = int(entry["part_id"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="An accessory is picked by its id.")
        if part_id in seen:
            continue
        part = db.get(models.AccessoryPart, part_id)
        if part is None or part.make != make or part.discipline != discipline:
            raise HTTPException(status_code=400, detail="An accessory picked for this product is no "
                                "longer in the %s library - reload the page." % make)
        seen.add(part_id)
        out.append((part, " ".join(str(entry.get("replaces") or "").split())))
    return out


def _npd_library_list(db, typology, discipline, picked, acc_list, fields, options, params,
                      previous_names, previous_printed):
    """For a brand whose accessories are the library: the product's accessory list is its library
    accessories (by the name it lists each under) followed by any datasheet names not in the
    library yet, and the printed ACCESSORIES line follows that list - but only when the list
    changed, so saving a product untouched never rewrites its sheet. A line that began with "NIL"
    (a plain fixture is also an option) keeps it."""
    labels = accessory_parts.labels_for_parts([part for part, _r in picked])
    names = labels + [a["name"] for a in acc_list if a["name"] not in labels]
    options["accessories"] = names
    if [_acc_key(n) for n in names] == [_acc_key(n) for n in previous_names] and previous_printed:
        printed = previous_printed
    else:
        nil = str(previous_printed or "").strip().upper().startswith("NIL")
        printed = npd.OPTION_SEPARATOR.join((["NIL"] if nil else []) + names)
    fields["accessories"] = printed
    for t in (db.query(models.ParameterTemplate)
              .filter(models.ParameterTemplate.typology == typology,
                      models.ParameterTemplate.discipline == discipline,
                      models.ParameterTemplate.field_key == "accessories").all()):
        params.setdefault(t.group_name, {})[t.label] = printed


def _npd_write_accessory_prices(db, sheet, accessories, discipline, keep=()):
    """Replace this product's accessory prices with the ones just entered.

    Replaced rather than merged: the accessory list on the form is the whole truth about what
    this product is sold with, so an accessory removed there must stop being priced here.

    `keep` - the keys of datasheet accessories just replaced by a library accessory - are left
    alone: their price has either moved onto the library accessory or, when the two disagreed,
    stays on the product for a person to settle (accessory_parts.take_over_price). A price somebody
    entered is never deleted by choosing an accessory."""
    for row in (db.query(models.PriceEntry)
                  .filter(models.PriceEntry.specsheet_id == sheet.id,
                          models.PriceEntry.source_file.like("NPD -%")).all()):
        if _acc_key((row.config or {}).get("accessory") or "") not in keep:
            db.delete(row)
    db.flush()
    n = 0
    for a in accessories:
        if a["price"] is None:
            continue
        db.add(models.PriceEntry(
            discipline=discipline, specsheet_id=sheet.id,
            source_file="NPD - %s" % sheet.product_name,
            source_label="Accessory price entered with the product",
            config={"accessory": a["name"]}, price=a["price"], status="locked"))
        n += 1
    return n


def _npd_store_pictures(sheet, diagrams, product_image, line_diagram, more_diagrams=(), remove_diagrams=()):
    """An NPD product's pictures after a save: the product image, the main dimension drawing (its
    sheet's "Additional Diagram 2" - what every export and BOQ row reads, LINE_DIAGRAM_LABEL), and
    any more dimension drawings ("Dimension 2", 3, ... - npd.dimension_order), which the generated
    sheet prints together and a BOQ row may choose (owner, 18 Nov 2026).

    A new main drawing replaces the old one - including an older upload's "Line Diagram", which sat
    beside it unprinted. More drawings are added after the last; one named in `remove_diagrams` is
    taken off the product (its file stays on disk). Profiles and accessory pictures are untouched."""
    diagrams = [dict(d) for d in (diagrams or [])]
    # Only a further drawing ("Dimension N") is removed this way - the same test _npd_removes_a_drawing
    # makes, so a removal always counts as a specification change. The main drawing is replaced, never removed.
    removing = {str(x).strip().lower() for x in (remove_diagrams or [])
                if (npd.dimension_order(x) or (0,))[0] == 1}
    diagrams = [d for d in diagrams if str(d.get("label") or "").strip().lower() not in removing]

    def save(upload, label, stem):
        suffix = _picture_suffix(upload.filename, ".png")
        dest = IMAGES_DIR / ("npd_%d_%s%s" % (sheet.id, stem, suffix))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        entry = {"path": str(dest), "label": label}
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(dest) as im:
                entry["width"], entry["height"] = im.size
        except Exception:
            pass
        return entry

    if product_image is not None and product_image.filename:
        diagrams = [d for d in diagrams if d.get("label") != "Product Image"]
        diagrams.append(save(product_image, "Product Image", "product"))
    if line_diagram is not None and line_diagram.filename:
        main = {LINE_DIAGRAM_LABEL.lower(), "line diagram"}
        diagrams = [d for d in diagrams if str(d.get("label") or "").strip().lower() not in main]
        diagrams.append(save(line_diagram, LINE_DIAGRAM_LABEL, "line"))
    numbers = [npd.dimension_order(d.get("label"))[1] for d in diagrams
               if (npd.dimension_order(d.get("label")) or (0,))[0] == 1]
    # and every number this product's files have ever had: a removed drawing's file stays on disk,
    # and a BOQ row that chose "Dimension 3" must never be handed a different picture
    for f in IMAGES_DIR.glob("npd_%d_dim*" % sheet.id):
        m = re.match(r"npd_%d_dim(\d+)\." % sheet.id, f.name)
        if m:
            numbers.append(int(m.group(1)))
    nxt = max([1] + numbers) + 1
    for upload in (more_diagrams or []):
        if upload is None or not upload.filename:
            continue
        diagrams.append(save(upload, "Dimension %d" % nxt, "dim%d" % nxt))
        nxt += 1

    # Product image first, then the dimension drawings in order, then everything else as it was:
    # npd.pick_images reads the first of each kind.
    def rank(d):
        label = d.get("label") or ""
        if label == "Product Image":
            return (0, 0)
        o = npd.dimension_order(label)
        return (1, o[0] * 1000 + o[1]) if o else (2, 0)
    return sorted(diagrams, key=rank)


def _npd_removes_a_drawing(sheet, remove):
    """Whether `remove` names a further dimension drawing this product has - a stale tab's request
    for one already gone changes nothing, and must not send an approved product back to pending."""
    if not isinstance(remove, list):
        return False
    have = {str(d.get("label") or "").strip().lower() for d in (sheet.diagrams or [])
            if (npd.dimension_order(d.get("label")) or (0,))[0] == 1}
    return any(str(x).strip().lower() in have for x in remove)


def _npd_more_dimension_drawings(sheet):
    """[{label, url}] - a product's dimension drawings after its main one, for the NPD form."""
    out = []
    for d in sorted(sheet.diagrams or [], key=lambda d: npd.dimension_order(d.get("label")) or (9, 0)):
        o = npd.dimension_order(d.get("label"))
        if o is None or o[0] == 0 or not d.get("path") or not Path(d["path"]).exists():
            continue
        out.append({"label": d.get("label"), "url": _image_url(d["path"])})
    return out


def _npd_picture_urls(sheet):
    """(product photo URL, line diagram URL) for one product, or None where it has none.

    npd.pick_images works on file paths, since that is what the PDF generator needs; the form
    needs URLs for the same two pictures, so the paths are mapped back afterwards.
    """
    usable = [(d.get("path"), d.get("label")) for d in (sheet.diagrams or [])
              if d.get("path") and Path(d["path"]).exists()]
    product_path, diagram_path = npd.pick_images(usable)
    return (_image_url(product_path) if product_path else None,
            _image_url(diagram_path) if diagram_path else None)


@app.get("/api/npd/products/{sheet_id}")
def npd_product_detail(sheet_id: int, db: Session = Depends(get_db)):
    """Everything the NPD form needs to reopen a product exactly as it was saved."""
    s = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    _npd_live_or_404(s)
    if s.origin != "npd":
        raise HTTPException(status_code=400,
                            detail="This product was read from a manufacturer's PDF, not "
                                   "authored here, so it has no NPD definition to edit.")
    # The full accessory list comes from the product's own options, not from its price rows:
    # an accessory with no price recorded is still sold with the product, and rebuilding the
    # list from prices alone would quietly drop it the first time the product was edited.
    priced = {(p.config or {}).get("accessory"): p.price
              for p in (s.price_entries or []) if (p.config or {}).get("accessory")}
    # `options` holds the list once a product has been saved through this form. A product
    # read from a manufacturer's PDF has never been through it, and keeps its accessories as
    # the one string the datasheet prints - "NIL / HONEYCOMB / SPREAD LENS / PIN HOLE". Read
    # only from `options`, the form showed no accessories at all for any of those products,
    # while their own specsheet plainly lists them. Split the same way the Configurator's own
    # accessory picker splits it, so the two offer the same list.
    names = [a for a in (s.options or {}).get("accessories") or [] if str(a).strip()]
    if not names:
        names = _split_field_options((s.fields or {}).get("accessories"))
    for extra in priced:                       # priced but no longer listed - still shown
        if extra not in names:
            names.append(extra)
    acc_list = [{"name": n, "price": priced.get(n)} for n in names]
    # A InHouse product's accessories are picked from the library (app/accessory_parts.py): those
    # come first, each with everything the library holds about it, then any name its datasheet
    # lists that is not in the library yet - marked, so the form can offer to choose it there.
    library = accessory_parts.is_part_brand(s.make)
    if library:
        pairs = accessory_parts.labelled(accessory_parts.links_of(db, [s.id]).get(s.id, []))
        in_library = {_acc_key(label) for _l, label in pairs}
        entries = {e["id"]: _library_dict(e) for e in accessory_parts.library(db, s.discipline)}
        acc_list = [{"part_id": l.part_id, "name": label, "part": entries.get(l.part_id)}
                    for l, label in pairs] + \
                   [dict(a, legacy=True) for a in acc_list if _acc_key(a["name"]) not in in_library]
    # Which parameters this product treats as a choice, recovered from its option lists, so
    # reopening the form restores the switches the way they were left.
    affects = {k: True for k in (s.options or {}) if k != "accessories"}
    return JSONResponse({
        "id": s.id, "product_name": s.product_name, "typology": s.typology,
        "series": s.series, "family": s.family, "variant": s.variant, "make": s.make,
        "model_no": (s.fields or {}).get("model_no", ""),
        "approval_status": s.approval_status or "pending",
        # Whether this product's accessories are picked from the accessory library.
        "accessory_library": library,
        "values": {k: v for k, v in (s.fields or {}).items() if k != "model_no"},
        "affects": affects,
        "base_price": s.base_price,
        "price_adders": s.price_adders or {},
        "accessories": acc_list,
        "params": s.params or {},
        "sub_typology": (s.fields or {}).get("sub_typology", ""),
        # What the sheet's first bar prints today, so the form edits the real line rather
        # than re-deriving one that may not match it.
        "category": s.category or "",
        "description_line": s.category or "",
        "title_parts": [{"label": l, "value": v} for l, v in
                        npd.title_parts((s.fields or {}).get("sub_typology", ""),
                                        s.typology, s.fields or {})],
        # with a URL each, so the edit form shows the pictures already attached rather than
        # claiming none were chosen
        "diagrams": [{"label": d.get("label"), "url": _image_url(d["path"])}
                     for d in (s.diagrams or []) if d.get("path") and Path(d["path"]).exists()],
        # Which of those two the form's own upload boxes stand for, decided by the same rule
        # the generated sheet uses (npd.pick_images) rather than by matching a label exactly -
        # a product read from a manufacturer's PDF calls its line diagram whatever the parser
        # called it, and the form used to show nothing for all of them.
        "product_image_url": _npd_picture_urls(s)[0],
        "line_diagram_url": _npd_picture_urls(s)[1],
        "more_diagrams": _npd_more_dimension_drawings(s),
        "image_url": image_urls(s)[1],
        "specsheet_url": "/api/specsheets/%d/generated-specsheet.pdf" % s.id,
    })


@app.put("/api/npd/products/{sheet_id}")
async def npd_update_product(
    sheet_id: int,
    request: Request,
    payload: str = Form(...),
    product_image: Optional[UploadFile] = File(None),
    line_diagram: Optional[UploadFile] = File(None),
    more_diagrams: Optional[list[UploadFile]] = File(None),
    db: Session = Depends(get_db),
):
    """Revise an authored product - its specification, its prices, or both.

    An approved product that is edited returns to pending. The approval said "this is right to
    quote from", and that was said about the version somebody read; a later edit is not
    covered by it, and keeping the tick would let a change reach the library that nobody
    agreed to."""
    import json as _json
    try:
        body = _json.loads(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="payload is not valid JSON")

    sheet = get_or_404(db, models.Specsheet, sheet_id, "specsheet")
    _npd_live_or_404(sheet)
    if sheet.origin != "npd":
        raise HTTPException(status_code=400,
                            detail="Only products authored in NPD can be edited here.")
    name = (body.get("product_name") or "").strip()
    typology = (body.get("typology") or "").strip()
    if not name or not typology:
        raise HTTPException(status_code=400, detail="Product name and typology are required")
    # Before anything is written: the pictures are saved to disk ahead of the commit, under
    # names this product already uses, so a refusal half-way through would replace one of them.
    for upload in (product_image, line_diagram, *(more_diagrams or [])):
        if upload is not None and upload.filename:
            _picture_suffix(upload.filename, ".png")

    discipline = current_discipline(request)
    params, fields, options, base_price, adders, acc_list, title = _npd_compose(
        db, body, discipline)
    # Accessories picked from the library (app/accessory_parts.py): the product's accessory list
    # and printed line are composed from them here, before anything is compared or written.
    make = (body.get("make") or "InHouse").strip()
    picked = _npd_picked(db, body, make, discipline)
    library = accessory_parts.is_part_brand(make)
    if library:
        _npd_library_list(db, typology, discipline, picked, acc_list, fields, options, params,
                          accessories._names_of(sheet), str((sheet.fields or {}).get("accessories") or ""))
    # A datasheet accessory replaced by a library one hands its price over (below), so it is not
    # this product's price going away.
    replaced = {_acc_key(r) for _p, r in picked if r}

    # What this save would change. Saving a product with no edits changes nothing (checked
    # across the whole catalogue when the form stopped losing data), so any difference here is
    # a real edit - and the specification and the prices are two permissions, checked apart.
    merged_preview = dict(sheet.options or {})
    merged_preview.update(options)
    import json as _j

    def spec_state(nm, ty, se, fa, va, mk, ca, pa, fi, op):
        # For a library brand the accessory list's own spelling is not compared - the printed
        # line in `fields` is, and it changes whenever the accessories do.
        if library:
            op = {k: v for k, v in (op or {}).items() if k != "accessories"}
        return _j.dumps([nm or "", ty or "", se or "", fa or "", va or "", mk or "", ca or "",
                         pa or {}, fi or {}, op or {}], sort_keys=True, default=str)
    spec_changed = bool(product_image and product_image.filename) or \
        bool(line_diagram and line_diagram.filename) or \
        any(u is not None and u.filename for u in (more_diagrams or [])) or \
        _npd_removes_a_drawing(sheet, body.get("remove_diagrams")) or \
        spec_state(sheet.product_name, sheet.typology, sheet.series, sheet.family,
                   sheet.variant, sheet.make, sheet.category, sheet.params, sheet.fields,
                   sheet.options) != \
        spec_state(name, typology, (body.get("series") or "").strip(),
                   (body.get("family") or "").strip(), (body.get("variant") or "").strip(),
                   make, title or sheet.category, params, fields, merged_preview)
    new_acc = {a["name"]: _money(a["price"]) for a in acc_list if a.get("price") is not None}
    price_changes = _price_changes(sheet.base_price, sheet.price_adders,
                                   _stored_accessory_prices(sheet, replaced), base_price, adders, new_acc)
    if spec_changed and not user_can(request, "npd.edit"):
        raise HTTPException(status_code=403, detail="You can change this product\u2019s prices "
                            "but not its specification - ask an admin for \u201cCreate and edit "
                            "products\u201d.")
    if price_changes and not user_can(request, "prices.edit"):
        raise HTTPException(status_code=403, detail="You can change this product\u2019s "
                            "specification but not its prices - ask an admin for \u201cChange "
                            "prices\u201d.")

    # Approval vouches for the specification - what the product is and what its sheet says.
    # A price change leaves that untouched, so it does not send an approved product back to
    # awaiting approval: otherwise someone updating fifty prices would pull fifty products out
    # of the library until each was approved again. Every price change is logged instead, with
    # the figure before and after.
    was_approved = sheet.approval_status == "approved" and spec_changed
    sheet.product_name = name
    sheet.typology = typology
    sheet.series = (body.get("series") or "").strip()
    sheet.family = (body.get("family") or "").strip()
    sheet.variant = (body.get("variant") or "").strip()
    sheet.make = (body.get("make") or "InHouse").strip()
    # A parameter the product offers exactly one of - IP65 and nothing else, 10W and nothing
    # else - has no option list rebuilt for it, because one option is not a choice to offer.
    # But the parser recorded those lists, the Configurator reads them, and the form never
    # asks about them, so saving a product with no edits was quietly emptying them: 134 of 135
    # products lost a dropdown that way. Anything the form did not speak to is kept.
    merged_options = dict(sheet.options or {})
    merged_options.update(options)
    sheet.params, sheet.fields, sheet.options = params, fields, merged_options
    sheet.category = title or sheet.category
    sheet.base_price, sheet.price_adders = base_price, adders
    if was_approved:
        sheet.approval_status = "pending"

    pictures_before = [(d.get("label"), d.get("path")) for d in (sheet.diagrams or [])]
    diagrams = _npd_store_pictures(sheet, sheet.diagrams, product_image, line_diagram, more_diagrams,
                                   body.get("remove_diagrams") if isinstance(body.get("remove_diagrams"), list) else [])
    pictures_after = [(d.get("label"), d.get("path")) for d in diagrams]
    # Recorded when the set of pictures changed, so a replaced or removed drawing can be put back
    # from the log (its file stays on disk).
    pictures_change = ({"before": pictures_before, "after": pictures_after}
                       if sorted(map(str, pictures_before)) != sorted(map(str, pictures_after)) else None)
    if diagrams:
        sheet.diagrams = diagrams
        sheet.image_path = diagrams[0]["path"]

    # A datasheet accessory replaced by a library one: its price moves onto the library accessory
    # when that can be done without losing or contradicting a figure - before the product's own
    # prices are rewritten below, which would otherwise take it with them.
    accessory_notes, links_changed = [], {}
    if library:
        may_price = user_can(request, "prices.edit")
        for part, replaces in picked:
            note = accessory_parts.take_over_price(db, sheet, replaces, part, may_price) if replaces else ""
            if note:
                accessory_notes.append(note)
        links_changed = accessory_parts.set_product_accessories(db, sheet, [part.id for part, _r in picked])
    priced = _npd_write_accessory_prices(db, sheet, acc_list, discipline, replaced)
    if links_changed and (links_changed.get("added") or links_changed.get("removed")) and \
            not (price_changes or spec_changed):
        log_activity(db, request, "npd.update", "Changed the accessories of %s" % sheet.product_name,
                     details={"product_id": sheet.id, "accessories": links_changed,
                              "notes": accessory_notes})
    if price_changes or spec_changed:
        what = []
        if spec_changed:
            what.append("specification")
        if price_changes:
            what.append("prices")
        log_activity(db, request, "npd.update",
                     "Changed the %s of %s%s" % (
                         " and ".join(what), sheet.product_name,
                         (": " + _describe_price_changes(price_changes)) if price_changes else ""),
                     details={"product_id": sheet.id, "specification_changed": spec_changed,
                              "price_changes": [{"what": w, "before": a, "after": b}
                                                for w, a, b in price_changes],
                              "accessories": links_changed, "notes": accessory_notes,
                              "pictures": pictures_change})
    db.commit()
    accessories.forget()          # its accessories may have changed
    db.refresh(sheet)
    return {"id": sheet.id, "product_name": sheet.product_name,
            "approval_status": sheet.approval_status,
            "returned_to_pending": was_approved,
            "accessory_prices": priced,
            # What happened to the prices of datasheet accessories replaced by library ones.
            "accessory_notes": accessory_notes}


# ---------------------------------------------------------------- NPD: DuraGreen
#
# DuraGreen's side of NPD. InHouse's NPD lists the products authored in it (origin "npd") and
# codes them with the studio's own product code; DuraGreen's products are DuraGreen's, read
# from its website and spec sheets (scripts/backfills/backfill_duragreen_npd.py), and coded with
# DuraGreen's own printed order codes. The two share no rows and no code tables - see
# app/duragreen.py. What they do share is the Specsheet itself, so the Library, Configurator and
# BOQ treat a DuraGreen product like any other, and the base price + adder pricing.

# DuraGreen's catalogue code tables (vendor_code_rules) change only when
# backfill_duragreen_code_rules.py reloads them, so they are read once and kept until the
# table's size or newest id moves - not 2,400 rows on every BOQ line's description.
_dg_rules_cache = {"key": None, "rules": []}


def _duragreen_rules(db):
    from sqlalchemy import func
    key = db.query(func.count(models.VendorCodeRule.id), func.max(models.VendorCodeRule.id)).filter(
        models.VendorCodeRule.make == duragreen.MAKE).one()
    if key != _dg_rules_cache["key"]:
        rows = (db.query(models.VendorCodeRule).filter(models.VendorCodeRule.make == duragreen.MAKE)
                .order_by(models.VendorCodeRule.scope, models.VendorCodeRule.position, models.VendorCodeRule.id).all())
        # plain copies: a cached ORM row would outlive the session it was read in
        _dg_rules_cache["rules"] = [SimpleNamespace(scope=r.scope, segment=r.segment, attribute=r.attribute,
                                                    position=r.position, dash_after=r.dash_after, meaning=r.meaning,
                                                    code=r.code, source=r.source) for r in rows]
        _dg_rules_cache["key"] = tuple(key)
    return _dg_rules_cache["rules"]


def _vendor_rows(db, sheet_id):
    """A DuraGreen product's printed order codes (with its catalogue example as code template),
    its option code parts, and DuraGreen's catalogue code rules - what duragreen.order_code reads."""
    codes = (db.query(models.VendorOrderCode)
             .filter(models.VendorOrderCode.specsheet_id == sheet_id,
                     models.VendorOrderCode.make == duragreen.MAKE)
             .order_by(models.VendorOrderCode.sort_order, models.VendorOrderCode.id).all())
    parts = (db.query(models.VendorCodePart)
             .filter(models.VendorCodePart.specsheet_id == sheet_id,
                     models.VendorCodePart.make == duragreen.MAKE)
             .order_by(models.VendorCodePart.attribute, models.VendorCodePart.id).all())
    return codes, parts, _duragreen_rules(db)


# ---- DuraGreen's naming structures (27 Oct 2026) - see app/duragreen.py, "naming structures" ----

def _dg_made_structure(db, name):
    return (db.query(models.VendorCodeStructure)
            .filter(models.VendorCodeStructure.make == duragreen.MAKE,
                    func.upper(models.VendorCodeStructure.name) == str(name or "").strip().upper()).first())


def _dg_structure_source(rules, name, made):
    """Where a structure's letters come from, in a few words, for the code's pieces and NPD."""
    if made is not None:
        return "made in NPD" + (" by " + made.created_by if made.created_by else "")
    return next((r.source for r in rules if r.scope == name and not duragreen.is_entered(r.source)), name)


def _dg_naming_of(name, modes, rules, made, entered_by=""):
    """A naming structure with how one product fills it, as duragreen.order_code takes it."""
    return {"structure": name, "made": made is not None,
            "segments": duragreen.structure_segments(name, rules, made.segments if made is not None else None),
            "modes": modes or {}, "entered_by": entered_by or "", "source": _dg_structure_source(rules, name, made)}


def _dg_naming(db, s, rules=None):
    """The naming structure a person put this DuraGreen product in, resolved for
    duragreen.order_code - or None for a product that follows its catalogue page or its sheet."""
    row = db.query(models.VendorProductNaming).filter(models.VendorProductNaming.specsheet_id == s.id).first()
    if row is None:
        return None
    rules = _duragreen_rules(db) if rules is None else rules
    return _dg_naming_of(row.structure, row.segments, rules, _dg_made_structure(db, row.structure), row.entered_by)


def _dg_structures(db, rules):
    """Every naming structure a DuraGreen product can be put in: DuraGreen's printed code tables
    (the newest edition of each range's) and the ones made in NPD - [{name, made, id, description,
    source, created_by, segments}]. `rules` with their ids give each letter its id (the
    Nomenclature tab removes a letter a person added by it)."""
    out = []
    scopes = {r.scope for r in rules}
    for name in duragreen.RANGE_PAGES:
        if name in scopes:
            out.append({"name": name, "made": False, "id": None, "description": "", "created_by": "",
                        "source": _dg_structure_source(rules, name, None),
                        "segments": duragreen.structure_segments(name, rules)})
    for m in (db.query(models.VendorCodeStructure).filter(models.VendorCodeStructure.make == duragreen.MAKE)
              .order_by(models.VendorCodeStructure.name)):
        out.append({"name": m.name, "made": True, "id": m.id, "description": m.description or "",
                    "created_by": m.created_by or "", "source": _dg_structure_source(rules, m.name, m),
                    "segments": duragreen.structure_segments(m.name, rules, m.segments or [])})
    return out


def _dg_structure_named(db, rules, name):
    """(name as filed, made row or None, segments) of the structure called `name`, or a 422."""
    made = _dg_made_structure(db, name)
    if made is not None:
        return made.name, made, duragreen.structure_segments(made.name, rules, made.segments or [])
    printed = next((n for n in duragreen.RANGE_PAGES if n.upper() == str(name or "").strip().upper()), None)
    if printed and any(r.scope == printed for r in rules):
        return printed, None, duragreen.structure_segments(printed, rules)
    raise HTTPException(status_code=422, detail="There is no naming structure called %s." % name)


def _dg_clean_modes(raw, segments):
    """How a product fills each segment of its structure, checked: {attribute: {"mode", "value"?}}.
    A detail that names the product itself (its type, its size) is fixed or left out, never quoted:
    the Configurator never asks a product's identity (configurator.IDENTITY_KEYS)."""
    out = {}
    for seg in segments:
        attr = seg["attribute"]
        want = (raw or {}).get(attr)
        if not isinstance(want, dict):
            continue
        mode = str(want.get("mode") or "").strip()
        if not mode:
            continue
        if mode not in duragreen.NAMING_MODES:
            raise HTTPException(status_code=422, detail="%s: %r is not fixed, quoted or out." % (seg["segment"], mode))
        if mode == "quoted" and attr in configurator.IDENTITY_KEYS:
            raise HTTPException(status_code=422, detail="%s names the product itself, so it is fixed or left out - "
                                "never chosen when quoting." % seg["segment"])
        if mode == "fixed":
            value = str(want.get("value") or "").strip()
            if not value:
                raise HTTPException(status_code=422, detail="%s is fixed: choose its value." % seg["segment"])
            out[attr] = {"mode": "fixed", "value": value}
        else:
            out[attr] = {"mode": mode}
    return out


def _dg_set_naming(db, request, s, raw, rules, codes):
    """Puts a product in a naming structure (or takes it out, `raw` {"structure": ""}): returns
    (before, after) as {"structure", "segments"} or None, without writing a log - the caller's own
    record says what changed. A product one of DuraGreen's catalogue pages names follows that page
    (always DuraGreen's naming), so it is never put in another structure."""
    row = db.query(models.VendorProductNaming).filter(models.VendorProductNaming.specsheet_id == s.id).first()
    before = {"structure": row.structure, "segments": row.segments or {}} if row is not None else None
    name = str((raw or {}).get("structure") or "").strip()
    if not name:
        if row is not None:
            db.delete(row)
        return before, None
    template = next((c for c in codes if duragreen.is_example(c)), None)
    if template is not None:
        scheme = duragreen.scheme_of(codes)
        raise HTTPException(status_code=409, detail="%s follows its page of DuraGreen's catalogue (p.%s%s) - "
                            "DuraGreen's own naming - so it is not put in another structure." % (
                                s.product_name, scheme["page"], ", the %s range" % scheme["range"] if scheme["range"] else ""))
    name, _made, segments = _dg_structure_named(db, rules, name)
    modes = _dg_clean_modes((raw or {}).get("modes"), segments)
    after = {"structure": name, "segments": modes}
    if after == before:
        return before, after
    who = _user_email(db, effective_user_id(request)) or ""
    if row is None:
        row = models.VendorProductNaming(make=duragreen.MAKE, discipline=s.discipline, specsheet_id=s.id)
        db.add(row)
    row.structure, row.segments, row.entered_by = name, modes, who
    return before, after


def _dg_option_keys(s, *segment_lists, extra=()):
    """The options a DuraGreen product may be quoted on: DuraGreen's own (CONFIG_FIELDS), every
    detail its naming structure names that is not the product's identity, any it holds, and
    `extra` (the details its typology is described by - _dg_details)."""
    keys = duragreen.quotable_keys(extra)
    for segments in segment_lists:
        for seg in segments or []:
            attr = seg["attribute"]
            if attr and attr not in keys and attr not in configurator.IDENTITY_KEYS:
                keys.append(attr)
    for k in (s.options or {}) if s is not None else ():
        if k not in keys:
            keys.append(k)
    return keys


def _duragreen_or_404(db, request, sheet_id):
    s = db.get(models.Specsheet, sheet_id)
    if s is None or not duragreen.is_duragreen(s) or \
            (s.discipline or models.DEFAULT_DISCIPLINE) != current_discipline(request):
        raise HTTPException(status_code=404, detail="no DuraGreen product %s" % sheet_id)
    return s


def _engineer_groups(s, labels=None):
    """The product's facts grouped as an engineer reads them, every known field in its place
    (empty ones included, so the form can fill them) - [{group, items: [{key, label, value}]}].
    A detail typed in from its typology's template that DuraGreen's groups have no place for
    follows under "More details", named by `labels`."""
    fields = s.fields or {}
    out = [{"group": group, "items": [{"key": k, "label": label, "value": str(fields.get(k) or "")}
                                      for k, label in items]}
           for group, items in duragreen.ENGINEER_GROUPS]
    more = [{"key": k, "label": name, "value": v} for k, name, v in duragreen.more_details(fields, labels)]
    if more:
        out.append({"group": duragreen.MORE_DETAILS_GROUP, "items": more})
    return out


def _regroup_params(s, labels=None):
    """params rebuilt from fields in the engineer's groups (duragreen.regroup_params)."""
    return duragreen.regroup_params(s.fields, s.params, labels)


def _dg_details(db, discipline, typology):
    """(duragreen.details_list for one typology, {key: name} from its template). The template is
    the typology's NPD template - by name, else the one whose typology_key matches - and the
    offered options are those DuraGreen products of this typology hold, DuraGreen's own order
    first."""
    typology = str(typology or "").strip()
    T = models.ParameterTemplate
    names = sorted(t for (t,) in db.query(T.typology).filter(T.discipline == discipline).distinct() if t)
    key = typology_key(typology) if typology else ""
    name = typology if typology in names else next((n for n in names if key and typology_key(n) == key), "")
    labels, rows = {}, []
    if name:
        labels = configurator.template_labels(db, discipline, name)
        rows = [(r.group_name or "", labels.get(r.field_key) or r.label, r.field_key)
                for r in db.query(T).filter(T.discipline == discipline, T.typology == name)
                .order_by(T.sort_order, T.id)
                if (r.group_name or "").strip().upper() != npd.IDENTITY_GROUP]
    offered = duragreen.offered_order(_options_held(db, discipline, duragreen.MAKE, typology))
    out = duragreen.details_list(rows, offered, configurator.IDENTITY_KEYS)
    out.update(typology=typology, template=name)
    for k, v in duragreen.SAME_DETAIL.items():
        if k in labels:
            labels.setdefault(v, labels[k])
    return out, labels


def _options_held(db, discipline, make, typology):
    """{option key: how many products of one make and typology are quoted on it} - only the
    options column is read."""
    counts = collections.Counter()
    if typology:
        for (opts,) in db.query(models.Specsheet.options).filter(
                models.Specsheet.make == make, models.Specsheet.discipline == discipline,
                models.Specsheet.typology == typology):
            counts.update(k for k, v in (opts or {}).items() if v)
    return counts


def _dg_detail_keys(details, s=None):
    """The details a DuraGreen product may hold: DuraGreen's own, those its typology is described
    by, and any it already holds (never one dropped because its typology changed)."""
    keys = set(duragreen.LABEL_OF) | {it["key"] for g in details["groups"] for it in g["items"]}
    if s is not None:
        keys |= set(s.fields or {}) | set(s.options or {})
    return keys


@app.get("/api/npd/duragreen/details")
def npd_duragreen_details(request: Request, typology: str = Query(""), db: Session = Depends(get_db)):
    """What "Type it in" asks for a product of one typology: its NPD template's parameters (as a
    InHouse product of that typology is described), then DuraGreen's own options the template
    leaves out - or DuraGreen's own groups when the typology has no template. Writes nothing."""
    return JSONResponse(_dg_details(db, current_discipline(request), typology)[0])


@app.get("/api/npd/duragreen/products")
def npd_duragreen_products(request: Request, db: Session = Depends(get_db)):
    """Every DuraGreen product, for NPD (DuraGreen)'s list - with how many order codes its sheet
    prints, so the products nobody can order by code yet stand out."""
    discipline = current_discipline(request)
    # Only the columns the list sends: a product's fields, parameters and diagrams are large JSON
    # it never reads, and loading them for 615 products was half this answer's time (27 Oct).
    sheets = (db.query(models.Specsheet)
              .options(load_only(*(getattr(models.Specsheet, c) for c in (
                  "id", "product_name", "series", "family", "variant", "typology", "approval_status",
                  "image_path", "base_price", "price_adders", "source_url", "source_pdf_path", "options"))))
              .filter(models.Specsheet.make == duragreen.MAKE,
                      models.Specsheet.discipline == discipline)
              .order_by(models.Specsheet.series, models.Specsheet.product_name).all())
    counts = dict(db.query(models.VendorOrderCode.specsheet_id, func.count(models.VendorOrderCode.id))
                  .filter(models.VendorOrderCode.make == duragreen.MAKE)
                  .group_by(models.VendorOrderCode.specsheet_id).all())
    unconfirmed = dict(db.query(models.VendorCodePart.specsheet_id, func.count(models.VendorCodePart.id))
                       .filter(models.VendorCodePart.make == duragreen.MAKE,
                               models.VendorCodePart.status != "stated")
                       .group_by(models.VendorCodePart.specsheet_id).all())
    named = dict(db.query(models.VendorProductNaming.specsheet_id, models.VendorProductNaming.structure)
                 .filter(models.VendorProductNaming.make == duragreen.MAKE).all())
    out = []
    for s in sheets:
        _image, thumb = image_urls(s)
        out.append({
            "id": s.id, "product_name": s.product_name or "", "series": s.series or "",
            "family": s.family or "", "variant": s.variant or "", "typology": s.typology or "",
            "approval_status": s.approval_status or "approved", "thumb_url": thumb,
            "order_codes": counts.get(s.id, 0), "unconfirmed_parts": unconfirmed.get(s.id, 0),
            "base_price": s.base_price,
            "adder_count": sum(len(v) for v in (s.price_adders or {}).values() if isinstance(v, dict)),
            "source_url": s.source_url or "", "has_pdf": bool(s.source_pdf_path),
            "options": {k: len(v) for k, v in (s.options or {}).items() if isinstance(v, list)},
            "key_series": duragreen.key_series_of(s.series, s.product_name, s.typology or ""),
            "named_by": named.get(s.id, ""),
        })
    return {"products": out, "config_fields": duragreen.CONFIG_FIELDS, "key_series": list(duragreen.KEY_SERIES)}


@app.get("/api/npd/duragreen/products/{sheet_id}")
def npd_duragreen_product(sheet_id: int, request: Request, db: Session = Depends(get_db)):
    s = _duragreen_or_404(db, request, sheet_id)
    codes, parts, rules = _vendor_rows(db, s.id)
    naming = _dg_naming(db, s, rules)
    template = next((c for c in codes if duragreen.is_example(c)), None)
    d = specsheet_detail(s)
    labels = dict(duragreen.LABEL_OF)
    structures = _dg_structures(db, rules)
    for st in structures:
        for seg in st["segments"]:
            labels.setdefault(seg["attribute"], seg["segment"])
    # A detail typed in from its typology's template is named as the template names it.
    template_names = _dg_details(db, s.discipline or models.DEFAULT_DISCIPLINE, s.typology)[1]
    for k in list(s.fields or {}) + list(s.options or {}):
        if k not in labels and k in template_names:
            labels[k] = template_names[k]
    d.update({
        "approval_status": s.approval_status or "approved",
        "groups": _engineer_groups(s, template_names),
        "config_fields": _dg_option_keys(s, (naming or {}).get("segments")),
        "labels": labels,
        # How it is named: the structure a person put it in (with how it fills each segment), and
        # the structures it could be put in - none for a product its catalogue page names.
        "naming": {"structure": naming["structure"], "made": naming["made"], "modes": naming["modes"],
                   "entered_by": naming["entered_by"]} if naming else None,
        "naming_structures": structures if (template is None or naming) else [],
        "naming_locked": "" if template is None else duragreen.scheme_of(codes)["text"],
        "identity_keys": sorted(configurator.IDENTITY_KEYS),
        "price_adders": s.price_adders or {},
        "order_codes": [{"id": c.id, "code": c.code, "config": c.config or {}, "printed": c.printed or {},
                         "vector": duragreen.vector_of(s, c.config), "has_parts": bool(c.parts),
                         "source": c.source or ""} for c in codes],
        "code_parts": [{"id": p.id, "attribute": p.attribute, "value": p.value, "code": p.code,
                        "status": p.status, "source": p.source or "", "note": p.note or ""} for p in parts],
        "status_text": duragreen.STATUS_TEXT,
        "code_rules": _code_rules_for(codes, rules),
        # Whose rules write its code (its range's table, or only its own page and sheet), and the
        # options its codes are told apart by - the columns of NPD's printed and every-combination tables.
        "scheme": duragreen.scheme_of(codes, naming),
        "vector_fields": duragreen.vector_fields(s, codes, naming),
    })
    return d


@app.get("/api/npd/duragreen/products/{sheet_id}/combinations")
def npd_duragreen_combinations(sheet_id: int, request: Request, limit: int = 600,
                               db: Session = Depends(get_db)):
    """Every combination of a DuraGreen product's options with the code each gets - printed (and
    which code DuraGreen prints for it), built and how surely, or none and why - so NPD shows
    which combinations DuraGreen prints before anyone picks one. From the saved product; writes
    nothing. At most `limit` combinations are worked out (the answer says how many there are)."""
    s = _duragreen_or_404(db, request, sheet_id)
    codes, parts, rules = _vendor_rows(db, s.id)
    return duragreen.combinations(s, codes, parts, rules, limit=max(1, min(limit, 2000)),
                                  naming=_dg_naming(db, s, rules))


@app.get("/api/npd/duragreen/products/{sheet_id}/printed")
def npd_duragreen_printed(sheet_id: int, request: Request, db: Session = Depends(get_db)):
    """The codes a DuraGreen product's sheet prints, each as one choice ({code, vector}) - what
    Add Product offers as "Printed by DuraGreen", so quoting one is a click, not five dropdowns.
    A read, so anyone may (the NPD rule in _required_permission). The code a line gets is still
    the server's answer for the options set (suggest-description), never this list's."""
    s = _duragreen_or_404(db, request, sheet_id)
    codes, parts, rules = _vendor_rows(db, s.id)
    naming = _dg_naming(db, s, rules)
    fields = duragreen.vector_fields(s, codes, naming)
    printed = duragreen.printed_choices(s, codes, parts, rules, naming=naming)
    shown = set(fields) | {k for row in printed for k in row["vector"]} | {k for row in printed for k in row["missing"]}
    named = {seg["attribute"]: seg["segment"] for seg in (naming or {}).get("segments") or []}
    return {"fields": fields,
            "labels": {k: duragreen.LABEL_OF.get(k) or named.get(k) or k.replace("_", " ").title() for k in sorted(shown)},
            "status_text": duragreen.STATUS_TEXT, "printed": printed}


@app.get("/api/npd/duragreen/products/{sheet_id}/nomenclature")
def npd_duragreen_nomenclature(sheet_id: int, request: Request, db: Session = Depends(get_db)):
    """How this product's order code is built, piece by piece, and where each option's code comes
    from - the section at the foot of the NPD (DuraGreen) product. A read, so anyone may (the NPD
    rule in _required_permission)."""
    s = _duragreen_or_404(db, request, sheet_id)
    codes, parts, rules = _vendor_rows(db, s.id)
    out = duragreen.nomenclature(s, codes, parts, rules, naming=_dg_naming(db, s, rules))
    out["status_text"] = duragreen.STATUS_TEXT
    return out


def _code_rules_for(codes, rules):
    """How DuraGreen builds this product's code, for NPD (DuraGreen) to show: its catalogue
    page's example and legend, and the code table of its range - or, for a product outside both
    ranges, ACME's table for reference only (nothing from it is more than unconfirmed for it)."""
    template = next((c for c in codes if duragreen.is_example(c)), None)
    if template is None:
        return {}
    printed = template.printed or {}
    range_name = duragreen.range_of(template)
    scope = range_name or duragreen.GENERAL_TABLE
    table = {}
    for r in rules:
        if r.scope == scope:
            table.setdefault(r.position, {"segment": r.segment, "field": r.attribute, "dash_after": r.dash_after,
                                          "entries": []})["entries"].append({"code": r.code, "meaning": r.meaning})
    return {"example": template.code, "page": printed.get("page"), "legend": printed.get("legend") or [],
            "left_out": printed.get("skipped") or [], "table": scope,
            "table_is_general": not range_name, "adapted": printed.get("adapted") or None,
            "table_source": next((r.source for r in rules if r.scope == scope), ""),
            "segments": [table[k] for k in sorted(table)]}


@app.post("/api/npd/duragreen/code")
def npd_duragreen_code(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """The order code one configuration gets - for NPD (DuraGreen)'s code tester. Writes nothing."""
    s = _duragreen_or_404(db, request, int(body.get("sheet_id") or 0))
    codes, parts, rules = _vendor_rows(db, s.id)
    return duragreen.order_code(s, body.get("chosen") or {}, codes, parts, rules, naming=_dg_naming(db, s, rules))


def _clean_options(raw, allowed=None):
    """The options a save sends, each a list of distinct values, for the keys a DuraGreen product
    may be quoted on (CONFIG_FIELDS, or _dg_option_keys for one in a naming structure)."""
    allowed = duragreen.CONFIG_FIELDS if allowed is None else allowed
    out = {}
    for key, values in (raw or {}).items():
        if key not in allowed or not isinstance(values, list):
            continue
        vals = [str(v).strip() for v in values if str(v).strip()]
        if vals:
            out[key] = list(dict.fromkeys(vals))
    return out


def _clean_adders(raw, allowed=None):
    allowed = duragreen.CONFIG_FIELDS if allowed is None else allowed
    out = {}
    for key, per in (raw or {}).items():
        if key not in allowed or not isinstance(per, dict):
            continue
        clean = {}
        for value, amount in per.items():
            try:
                m = _money(amount)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="%s %s: %r is not an amount." % (key, value, amount))
            if m is not None:
                if m < 0:
                    raise HTTPException(status_code=422, detail="An adder cannot be negative (%s %s)." % (key, value))
                clean[str(value)] = m
        if clean:
            out[key] = clean
    return out


@app.put("/api/npd/duragreen/products/{sheet_id}")
def npd_duragreen_update(sheet_id: int, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Saves a DuraGreen product's specification and/or its prices. The two are separate
    permissions, checked against what actually changed - as InHouse's NPD save does. How it is
    named (`naming`: the structure a person puts it in, and how it fills each segment) is part of
    its specification."""
    s = _duragreen_or_404(db, request, sheet_id)
    codes, _parts, rules = _vendor_rows(db, s.id)
    old_naming = _dg_naming(db, s, rules)
    naming_change, new_segments = None, (old_naming or {}).get("segments")
    if "naming" in body:
        before, after = _dg_set_naming(db, request, s, body.get("naming"), rules, codes)
        if before != after:
            naming_change = {"before": before, "after": after}
        new_segments = _dg_structure_named(db, rules, after["structure"])[2] if after else None
    typology = str(body.get("typology", s.typology) or "").strip()
    details, template_names = _dg_details(db, s.discipline or models.DEFAULT_DISCIPLINE, typology)
    detail_keys = _dg_detail_keys(details, s)
    allowed = _dg_option_keys(s, (old_naming or {}).get("segments"), new_segments, extra=sorted(detail_keys))
    fields = dict(s.fields or {})
    for key, value in (body.get("fields") or {}).items():
        if key in detail_keys:
            value = str(value or "").strip()
            if value:
                fields[key] = value
            else:
                fields.pop(key, None)
    options = dict(s.options or {})
    if "options" in body:
        # DuraGreen's own options are always all sent; another (a detail a naming structure names)
        # is replaced only when the save sends it - one left out is never dropped unseen.
        sent = body.get("options") or {}
        for key in list(duragreen.CONFIG_FIELDS) + [k for k in sent if k in allowed]:
            options.pop(key, None)
        options.update(_clean_options(sent, allowed))
        # A line added with no IP chosen prints this field, so it must not name a rating just removed.
        if options.get("ip_rating"):
            fields["ip_rating"] = " / ".join(options["ip_rating"])
        elif (s.options or {}).get("ip_rating") and fields.get("ip_rating") == (s.fields or {}).get("ip_rating"):
            fields.pop("ip_rating", None)
    try:
        base = _money(body["base_price"]) if "base_price" in body else s.base_price
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="The base price is not an amount.")
    if base is not None and base < 0:
        raise HTTPException(status_code=422, detail="A base price cannot be negative.")
    adders = _clean_adders(body["price_adders"], allowed) if "price_adders" in body else (s.price_adders or {})

    spec_changed = (fields != (s.fields or {}) or options != (s.options or {})
                    or typology != (s.typology or "") or naming_change is not None)
    price_changes = _price_changes(s.base_price, s.price_adders, {}, base, adders, {})
    if spec_changed and not user_can(request, "npd.edit"):
        raise HTTPException(status_code=403, detail="You can change this product’s prices but not "
                            "its specification - that needs “Create and edit products”.")
    if price_changes and not user_can(request, "prices.edit"):
        raise HTTPException(status_code=403, detail="You can change this product’s specification "
                            "but not its prices - that needs “Edit prices”.")
    if not spec_changed and not price_changes:
        return {"id": s.id, "changed": False}
    s.fields, s.options, s.typology = fields, options, typology
    s.params = _regroup_params(s, template_names)
    s.base_price, s.price_adders = base, adders
    what = [w for w, on in (("specification", spec_changed), ("prices", price_changes)) if on]
    named = ""
    if naming_change is not None:
        after = naming_change["after"]
        named = " (now named by the %s structure)" % after["structure"] if after else " (taken out of its naming structure)"
    log_activity(db, request, "npd.duragreen.update", "Changed the %s of DuraGreen %s%s%s" % (
        " and ".join(what), s.product_name, named,
        (": " + _describe_price_changes(price_changes)) if price_changes else ""),
        details={"product_id": s.id, "specification_changed": spec_changed, "naming": naming_change,
                 "price_changes": [{"what": w, "before": a, "after": b} for w, a, b in price_changes]})
    db.commit()
    return {"id": s.id, "changed": True}


@app.put("/api/npd/duragreen/products/{sheet_id}/status")
def npd_duragreen_status(sheet_id: int, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Releases a DuraGreen product to the Library and BOQ pickers, holds it, or returns it to
    pending - new products found on the website arrive pending, so nobody quotes one unseen."""
    s = _duragreen_or_404(db, request, sheet_id)
    status = str(body.get("status") or "")
    if status not in ("approved", "held", "pending"):
        raise HTTPException(status_code=422, detail="status must be approved, held or pending")
    before = s.approval_status or "approved"
    if status == before:
        return {"id": s.id, "approval_status": status, "changed": False}
    s.approval_status = status
    log_activity(db, request, "npd.duragreen.status", "Set DuraGreen %s from %s to %s" % (
        s.product_name, before, status), details={"product_id": s.id, "before": before, "after": status})
    db.commit()
    return {"id": s.id, "approval_status": status, "changed": True}


@app.post("/api/npd/duragreen/products")
def npd_duragreen_create(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """A DuraGreen product typed in by hand - one DuraGreen has sent details of that is not on
    its website yet. It starts pending, like a product found on the website.

    It may come with everything a person decides for it at once (owner, 27 Oct 2026): how it is
    named (`naming`: a structure, and per segment fixed / quoted / out), the options it is quoted
    on, and their prices - a base price and what each option adds. Prices need "Edit prices"."""
    name = str(body.get("product_name") or "").strip()
    series = str(body.get("series") or "").strip()
    if not name or not series:
        raise HTTPException(status_code=422, detail="A DuraGreen product needs a series and a name.")
    exists = db.query(models.Specsheet).filter(
        models.Specsheet.make == duragreen.MAKE, func.upper(models.Specsheet.series) == series.upper(),
        func.upper(models.Specsheet.product_name) == name.upper()).first()
    if exists:
        raise HTTPException(status_code=409, detail="DuraGreen %s / %s is already there (#%d)." % (
            series, name, exists.id))
    rules = _duragreen_rules(db)
    wanted = body.get("naming") or {}
    segments = _dg_structure_named(db, rules, wanted["structure"])[2] if str(wanted.get("structure") or "").strip() else None
    typology = str(body.get("typology") or "").strip()
    details, template_names = _dg_details(db, current_discipline(request), typology)
    detail_keys = _dg_detail_keys(details)
    allowed = _dg_option_keys(None, segments, extra=sorted(detail_keys))
    fields = {k: str(v).strip() for k, v in (body.get("fields") or {}).items()
              if k in detail_keys and str(v or "").strip()}
    try:
        base = _money(body.get("base_price"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="The base price is not an amount.")
    if base is not None and base < 0:
        raise HTTPException(status_code=422, detail="A base price cannot be negative.")
    options = _clean_options(body.get("options"), allowed)
    # A choice is also printed, as every DuraGreen product prints one: "8W / 10W / 12W".
    for k, vals in options.items():
        if k in detail_keys and not fields.get(k):
            fields[k] = " / ".join(vals)
    adders = {k: per for k, per in _clean_adders(body.get("price_adders"), allowed).items() if k in options}
    adders = {k: {v: m for v, m in per.items() if v in options[k]} for k, per in adders.items()}
    adders = {k: per for k, per in adders.items() if per}
    if (base is not None or adders) and not user_can(request, "prices.edit"):
        raise HTTPException(status_code=403, detail="Adding it with prices needs “Edit prices” - "
                            "leave the prices blank and someone who may price it can add them.")
    s = models.Specsheet(
        discipline=current_discipline(request), make=duragreen.MAKE, origin="parsed",
        approval_status="pending", filename="", product_name=name, series=series,
        family=str(body.get("family") or "").strip(), variant=str(body.get("variant") or "").strip(),
        typology=typology, category="",
        source_url=str(body.get("source_url") or "").strip(),
        fields=fields, options=options, params={}, base_price=base, price_adders=adders)
    s.params = _regroup_params(s, template_names)
    db.add(s)
    db.flush()
    named = None
    if segments is not None:
        named = _dg_set_naming(db, request, s, wanted, rules, [])[1]
    log_activity(db, request, "npd.duragreen.create", "Added DuraGreen %s / %s%s" % (
        series, name, " (named by the %s structure)" % named["structure"] if named else ""),
        details={"product_id": s.id, "naming": named, "options": options,
                 "base_price": base, "price_adders": adders})
    db.commit()
    return {"id": s.id}


@app.post("/api/npd/duragreen/naming/preview")
def npd_duragreen_naming_preview(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """The code a DuraGreen product not saved yet would get in a naming structure - for "Add a
    DuraGreen product: type it in", so a person sees the code and every option's letters while
    deciding. Built by the same duragreen.structure_code as a saved product's. Writes nothing."""
    rules = _duragreen_rules(db)
    name, made, segments = _dg_structure_named(db, rules, body.get("structure"))
    modes = _dg_clean_modes(body.get("modes"), segments)
    sheet = SimpleNamespace(options=_clean_options(body.get("options"), _dg_option_keys(None, segments)),
                            fields={}, typology=str(body.get("typology") or ""), make=duragreen.MAKE,
                            series="", product_name="")
    naming = _dg_naming_of(name, modes, rules, made)
    nomen = duragreen.structure_nomenclature(sheet, [], [], naming)
    combos = duragreen.combinations(sheet, [], [], rules, limit=600, naming=naming)
    first = combos["rows"][0] if combos["rows"] else \
        dict(duragreen.order_code(sheet, {}, [], [], rules, naming=naming), chosen={})
    return {"structure": name, "example": nomen["example"], "pieces": nomen["structure"],
            "options": nomen["options"], "total": combos["total"], "worked": len(combos["rows"]),
            "coded": sum(1 for r in combos["rows"] if r["code"]),
            "first": {"chosen": first.get("chosen") or {}, "code": first["code"], "status": first["status"],
                      "reason": first["reason"]}}


@app.get("/api/npd/duragreen/structures")
def npd_duragreen_structures(db: Session = Depends(get_db)):
    """The naming structures a DuraGreen product can be put in, with their segments and letters -
    for "Add a DuraGreen product: type it in". A read, so anyone may."""
    return {"structures": _dg_structures(db, _duragreen_rules(db)),
            "identity_keys": sorted(configurator.IDENTITY_KEYS)}


@app.get("/api/npd/duragreen/naming")
def npd_duragreen_naming(request: Request, db: Session = Depends(get_db)):
    """DuraGreen's naming, for the Nomenclature tab: every naming structure a product can follow -
    DuraGreen's printed code tables (with their earlier editions, for reference) and the ones made in
    NPD, each with its segments and letters - and every DuraGreen product filed under the naming it
    follows (duragreen.naming_class), with the code that shows it. A read, so anyone may."""
    discipline = current_discipline(request)
    rules = (db.query(models.VendorCodeRule).filter(models.VendorCodeRule.make == duragreen.MAKE)
             .order_by(models.VendorCodeRule.scope, models.VendorCodeRule.position, models.VendorCodeRule.id).all())
    structures = _dg_structures(db, rules)
    for st in structures:
        st["editions"] = [{"name": ed, "source": _dg_structure_source(rules, ed, None),
                           "segments": duragreen.structure_segments(ed, rules)}
                          for ed in duragreen.EARLIER_EDITIONS.get(st["name"], ())
                          if any(r.scope == ed for r in rules)]
    sheets = (db.query(models.Specsheet)
              .options(load_only(*(getattr(models.Specsheet, c) for c in (
                  "id", "product_name", "series", "family", "typology", "approval_status", "image_path"))))
              .filter(models.Specsheet.make == duragreen.MAKE, models.Specsheet.discipline == discipline)
              .order_by(models.Specsheet.series, models.Specsheet.product_name).all())
    codes_of = collections.defaultdict(list)
    for c in (db.query(models.VendorOrderCode)
              .options(load_only(*(getattr(models.VendorOrderCode, k) for k in (
                  "id", "specsheet_id", "code", "source", "parts", "printed", "sort_order"))))
              .filter(models.VendorOrderCode.make == duragreen.MAKE)
              .order_by(models.VendorOrderCode.sort_order, models.VendorOrderCode.id)):
        codes_of[c.specsheet_id].append(c)
    namings = {n.specsheet_id: n for n in db.query(models.VendorProductNaming)
               .filter(models.VendorProductNaming.make == duragreen.MAKE)}
    made = {m.name: m for m in db.query(models.VendorCodeStructure).filter(models.VendorCodeStructure.make == duragreen.MAKE)}
    products = []
    for s in sheets:
        codes = codes_of.get(s.id, [])
        n = namings.get(s.id)
        cls, structure = duragreen.naming_class(codes, {"structure": n.structure} if n else None)
        template = next((c for c in codes if duragreen.is_example(c)), None)
        printed = [c for c in codes if not duragreen.is_example(c)]
        example = template.code if template is not None else (printed[0].code if printed else "")
        if n is not None and not example:
            # a product a person named shows its code the way its structure builds it (a few products,
            # each worked out from what is already loaded - nothing is fetched per product)
            s_full = db.get(models.Specsheet, s.id)
            parts = db.query(models.VendorCodePart).filter(models.VendorCodePart.specsheet_id == s.id).all()
            example = duragreen.structure_nomenclature(
                s_full, codes, parts, _dg_naming_of(n.structure, n.segments, rules, made.get(n.structure)))["example"]
        _image, thumb = image_urls(s)
        products.append({
            "id": s.id, "product_name": s.product_name or "", "series": s.series or "", "family": s.family or "",
            "typology": s.typology or "", "approval_status": s.approval_status or "approved", "thumb_url": thumb,
            "class": cls, "structure": structure, "by_person": n is not None,
            "letters": duragreen.type_letters(codes), "printed": len(printed), "example": example,
            "page": duragreen.scheme_of(codes)["page"] if template is not None else 0,
            "key_series": duragreen.key_series_of(s.series, s.product_name, s.typology or ""),
        })
    # What DuraGreen's catalogue says a code's leading letters mean ("SF" - surface fixed), for the
    # classes no code table names.
    letters = collections.defaultdict(set)
    for r in rules:
        if r.scope == "LEGEND" and r.attribute == "type" and r.code:
            letters[r.code].add(r.meaning)
    attributes = [{"key": k, "label": duragreen.LABEL_OF.get(k, k)} for k in duragreen.structure_attributes()]
    return {"structures": structures, "products": products,
            "letters": {k: sorted(v)[:3] for k, v in letters.items()},
            "attributes": attributes, "identity_keys": sorted(configurator.IDENTITY_KEYS),
            "reserved": sorted(duragreen.RESERVED_NAMES)}


_DG_ATTR_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _dg_clean_code(code):
    return re.sub(r"\s+", "", str(code or "")).upper()


def _dg_clean_structure(db, body, current=None):
    """A naming structure as a person sent it, checked: (name, description, [segment dicts with
    their entries]). The name is new and none of DuraGreen's own; each segment names one detail;
    within a segment, no meaning and no code twice - DuraGreen's letters must read back one way."""
    name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="A naming structure needs a name.")
    if len(name) > 60:
        raise HTTPException(status_code=422, detail="Keep the name under 60 characters.")
    if name.upper() in {n.upper() for n in duragreen.RESERVED_NAMES}:
        raise HTTPException(status_code=409, detail="%s is DuraGreen's own name for a code table - choose another." % name)
    other = _dg_made_structure(db, name)
    if other is not None and (current is None or other.id != current.id):
        raise HTTPException(status_code=409, detail="There is already a naming structure called %s." % other.name)
    raw = body.get("segments")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(status_code=422, detail="A naming structure needs at least one segment.")
    if len(raw) > 20:
        raise HTTPException(status_code=422, detail="A naming structure has at most 20 segments.")
    segments, seen = [], set()
    for i, seg in enumerate(raw):
        seg = seg if isinstance(seg, dict) else {}
        label = re.sub(r"\s+", " ", str(seg.get("segment") or "")).strip()
        attr = str(seg.get("attribute") or "").strip().lower()
        if not label:
            raise HTTPException(status_code=422, detail="Segment %d needs a name." % (i + 1))
        refused = duragreen.segment_refused(attr)
        if refused:
            raise HTTPException(status_code=422, detail="Segment %d: %s" % (i + 1, refused))
        if not _DG_ATTR_KEY.match(attr):
            raise HTTPException(status_code=422, detail="%s: choose the detail it names." % label)
        if attr in seen:
            raise HTTPException(status_code=422, detail="Two segments name the same detail (%s)." % attr)
        seen.add(attr)
        entries, meanings, codes = [], {}, {}
        for e in seg.get("entries") or []:
            meaning = re.sub(r"\s+", " ", str((e or {}).get("meaning") or "")).strip()
            code = _dg_clean_code((e or {}).get("code"))
            if not meaning and not code:
                continue
            if not meaning or not code:
                raise HTTPException(status_code=422, detail="%s: every code needs what it means, and every meaning its code." % label)
            key = duragreen.canon(attr, meaning) or meaning.lower()
            if key in meanings:
                raise HTTPException(status_code=422, detail="%s: %s is listed twice." % (label, meaning))
            if code in codes:
                raise HTTPException(status_code=422, detail="%s: %s already means %s." % (label, code, codes[code]))
            meanings[key], codes[code] = code, meaning
            entries.append({"meaning": meaning, "code": code})
        segments.append({"segment": label, "attribute": attr, "dash_after": bool(seg.get("dash_after")),
                         "entries": entries})
    return name, str(body.get("description") or "").strip()[:500], segments


def _dg_write_structure_rules(db, name, segments, who, discipline):
    """A made structure's letters, filed in vendor_code_rules under its name, by segment position."""
    for pos, seg in enumerate(segments):
        for e in seg["entries"]:
            db.add(models.VendorCodeRule(make=duragreen.MAKE, discipline=discipline, scope=name,
                                         segment=seg["segment"], attribute=seg["attribute"], position=pos,
                                         dash_after=seg["dash_after"], meaning=e["meaning"], code=e["code"],
                                         source="entered by %s" % who))


@app.post("/api/npd/duragreen/structures")
def npd_duragreen_structure_create(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """A naming structure made in NPD, for products DuraGreen names by codes no printed table holds
    (owner, 27 Oct 2026: "if not, the user must be allowed to create a new structure and naming")."""
    name, description, segments = _dg_clean_structure(db, body)
    who = _user_email(db, effective_user_id(request)) or "someone"
    row = models.VendorCodeStructure(make=duragreen.MAKE, discipline=current_discipline(request), name=name,
                                     description=description, created_by=who,
                                     segments=[{k: seg[k] for k in ("segment", "attribute", "dash_after")} for seg in segments])
    db.add(row)
    _dg_write_structure_rules(db, name, segments, who, row.discipline)
    db.flush()
    log_activity(db, request, "npd.duragreen.structure", "Made the DuraGreen naming structure %s (%d segments)" % (
        name, len(segments)), details={"structure_id": row.id, "name": name, "segments": segments})
    db.commit()
    return {"id": row.id, "name": name}


@app.put("/api/npd/duragreen/structures/{structure_id}")
def npd_duragreen_structure_update(structure_id: int, request: Request, body: dict = Body(...),
                                   db: Session = Depends(get_db)):
    """Changes a naming structure made in NPD - its name, segments and letters - as one save. The
    products in it follow it at once (their codes are built from it, never stored); a renamed one
    keeps its products. DuraGreen's own tables are never changed here."""
    row = get_or_404(db, models.VendorCodeStructure, structure_id, "naming structure")
    if row.make != duragreen.MAKE:
        raise HTTPException(status_code=404, detail="no DuraGreen naming structure %s" % structure_id)
    old_rules = db.query(models.VendorCodeRule).filter(models.VendorCodeRule.make == duragreen.MAKE,
                                                       models.VendorCodeRule.scope == row.name).all()
    before = {"name": row.name, "description": row.description or "", "segments": duragreen.structure_segments(
        row.name, old_rules, row.segments or [])}
    name, description, segments = _dg_clean_structure(db, body, current=row)
    for r in old_rules:
        db.delete(r)
    db.flush()
    who = _user_email(db, effective_user_id(request)) or "someone"
    if name != row.name:
        (db.query(models.VendorProductNaming)
         .filter(models.VendorProductNaming.make == duragreen.MAKE, models.VendorProductNaming.structure == row.name)
         .update({models.VendorProductNaming.structure: name}, synchronize_session=False))
    row.name, row.description = name, description
    row.segments = [{k: seg[k] for k in ("segment", "attribute", "dash_after")} for seg in segments]
    _dg_write_structure_rules(db, name, segments, who, row.discipline)
    log_activity(db, request, "npd.duragreen.structure_edit", "Changed the DuraGreen naming structure %s" % name,
                 details={"structure_id": row.id, "before": before,
                          "after": {"name": name, "description": description, "segments": segments}})
    db.commit()
    return {"id": row.id, "name": name}


@app.delete("/api/npd/duragreen/structures/{structure_id}")
def npd_duragreen_structure_delete(structure_id: int, request: Request, db: Session = Depends(get_db)):
    """Removes a naming structure made in NPD - only one no product is named by, so no product's
    code can vanish with it."""
    row = get_or_404(db, models.VendorCodeStructure, structure_id, "naming structure")
    if row.make != duragreen.MAKE:
        raise HTTPException(status_code=404, detail="no DuraGreen naming structure %s" % structure_id)
    users = db.query(models.VendorProductNaming).filter(models.VendorProductNaming.make == duragreen.MAKE,
                                                        models.VendorProductNaming.structure == row.name).count()
    if users:
        raise HTTPException(status_code=409, detail="%d product%s %s named by %s - put %s in another structure first." % (
            users, "" if users == 1 else "s", "is" if users == 1 else "are", row.name, "it" if users == 1 else "them"))
    rules = db.query(models.VendorCodeRule).filter(models.VendorCodeRule.make == duragreen.MAKE,
                                                   models.VendorCodeRule.scope == row.name).all()
    log_activity(db, request, "npd.duragreen.structure_delete", "Removed the DuraGreen naming structure %s" % row.name,
                 details={"structure_id": row.id, "name": row.name, "description": row.description or "",
                          "segments": duragreen.structure_segments(row.name, rules, row.segments or [])})
    for r in rules:
        db.delete(r)
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.post("/api/npd/duragreen/structures/entries")
def npd_duragreen_structure_entry(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """One code DuraGreen sent, added to a segment of a naming structure - its own printed table
    (a colour DuraGreen has since given letters, a new size) or one made in NPD. Recorded as
    entered by the person, with where it came from; what DuraGreen printed is never changed."""
    rules = db.query(models.VendorCodeRule).filter(models.VendorCodeRule.make == duragreen.MAKE).all()
    name, made, segments = _dg_structure_named(db, rules, body.get("structure"))
    try:
        position = int(body.get("position"))
        seg = next(s for s in segments if s["position"] == position)
    except (TypeError, ValueError, StopIteration):
        raise HTTPException(status_code=422, detail="Choose the segment the code belongs to.")
    meaning = re.sub(r"\s+", " ", str(body.get("meaning") or "")).strip()
    code = _dg_clean_code(body.get("code"))
    if not meaning or not code:
        raise HTTPException(status_code=422, detail="A code needs the code itself and what it means.")
    for e in seg["entries"]:
        if duragreen.canon(seg["attribute"], e["meaning"]) == duragreen.canon(seg["attribute"], meaning):
            raise HTTPException(status_code=409, detail="%s already has %s as %s." % (seg["segment"], meaning, e["code"]))
        if e["code"] == code:
            raise HTTPException(status_code=409, detail="%s already uses %s for %s." % (seg["segment"], code, e["meaning"]))
    who = _user_email(db, effective_user_id(request)) or "someone"
    note = str(body.get("note") or "").strip()[:200]
    row = models.VendorCodeRule(make=duragreen.MAKE, discipline=current_discipline(request), scope=name,
                                segment=seg["segment"], attribute=seg["attribute"], position=seg["position"],
                                dash_after=seg["dash_after"], meaning=meaning, code=code,
                                source="entered by %s%s" % (who, (" - " + note) if note else ""))
    db.add(row)
    db.flush()
    log_activity(db, request, "npd.duragreen.structure_entry", "Added %s = %s to %s of the DuraGreen %s structure" % (
        code, meaning, seg["segment"], name), details={"rule_id": row.id, "structure": name,
                                                       "segment": seg["segment"], "note": note})
    db.commit()
    return {"id": row.id}


@app.delete("/api/npd/duragreen/rules/{rule_id}")
def npd_duragreen_structure_entry_delete(rule_id: int, request: Request, db: Session = Depends(get_db)):
    """Removes a code a person added to a naming structure. One DuraGreen printed is its own and
    stays: only a row entered in NPD can be removed here."""
    row = get_or_404(db, models.VendorCodeRule, rule_id, "code")
    if row.make != duragreen.MAKE:
        raise HTTPException(status_code=404, detail="no DuraGreen code %s" % rule_id)
    if not duragreen.is_entered(row.source):
        raise HTTPException(status_code=409, detail="DuraGreen printed %s = %s (%s) - it is kept as printed." % (
            row.code, row.meaning, row.source))
    log_activity(db, request, "npd.duragreen.structure_entry_delete", "Removed %s = %s from %s of the DuraGreen %s structure" % (
        row.code, row.meaning, row.segment, row.scope), details={"structure": row.scope, "segment": row.segment,
                                                                 "code": row.code, "meaning": row.meaning,
                                                                 "source": row.source})
    db.delete(row)
    db.commit()
    return {"ok": True}


def _code_config(body, allowed=None):
    """The configuration a typed-in code orders: any option the product is quoted on - a track's
    length and colour as much as a downlight's wattage, CCT, beam and driver, and a detail its
    naming structure names (`allowed`, _dg_option_keys)."""
    allowed = duragreen.CONFIG_FIELDS if allowed is None else allowed
    config = {}
    for key, value in (body.get("config") or {}).items():
        if key in allowed and str(value or "").strip():
            config[key] = str(value).strip()
    return config


@app.post("/api/npd/duragreen/products/{sheet_id}/codes")
def npd_duragreen_add_code(sheet_id: int, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """A printed order code added by hand - typically one DuraGreen sends in answer to a
    question (the CCT and control codes most ranges do not print). Stored whole: nothing is
    built from its parts until a person also records what each option's part is."""
    s = _duragreen_or_404(db, request, sheet_id)
    code = re.sub(r"\s+", "", str(body.get("code") or "")).upper()
    codes, _parts, rules = _vendor_rows(db, s.id)
    config = _code_config(body, _dg_option_keys(s, (_dg_naming(db, s, rules) or {}).get("segments")))
    if not code or not config:
        raise HTTPException(status_code=422, detail="A code needs the code itself and the options it orders.")
    if any(c.code == code for c in codes):
        raise HTTPException(status_code=409, detail="%s is already one of this product's codes." % code)
    who = _user_email(db, effective_user_id(request)) or "someone"
    # Where the person got it ("DuraGreen email, 22 Oct", "spec sheet p.2") - kept with the code,
    # since a typed-in code is used as DuraGreen's own from then on.
    note = str(body.get("note") or "").strip()[:200]
    row = models.VendorOrderCode(make=duragreen.MAKE, specsheet_id=s.id, code=code, config=config,
                                 printed={"note": note} if note else {}, parts=[], discipline=s.discipline,
                                 source="entered by %s" % who,
                                 sort_order=max([c.sort_order or 0 for c in codes] + [0]) + 1)
    db.add(row)
    db.flush()
    log_activity(db, request, "npd.duragreen.code", "Added DuraGreen order code %s to %s" % (code, s.product_name),
                 details={"product_id": s.id, "code_id": row.id, "config": config, "note": note})
    db.commit()
    return {"id": row.id}


@app.delete("/api/npd/duragreen/codes/{code_id}")
def npd_duragreen_delete_code(code_id: int, request: Request, db: Session = Depends(get_db)):
    row = get_or_404(db, models.VendorOrderCode, code_id, "order code")
    s = _duragreen_or_404(db, request, row.specsheet_id)
    log_activity(db, request, "npd.duragreen.code_delete", "Removed DuraGreen order code %s from %s" % (
        row.code, s.product_name), details={"product_id": s.id, "code": row.code, "config": row.config,
                                            "source": row.source})
    db.delete(row)
    db.commit()
    return {"ok": True}


@app.post("/api/npd/duragreen/products/{sheet_id}/parts")
def npd_duragreen_add_part(sheet_id: int, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """What one option contributes to the code ("2700K" -> "27"), entered by hand once DuraGreen
    has confirmed it. Recorded as stated: a person typed it from DuraGreen's answer."""
    s = _duragreen_or_404(db, request, sheet_id)
    attribute = str(body.get("attribute") or "").strip()
    value = str(body.get("value") or "").strip()
    code = re.sub(r"\s+", "", str(body.get("code") or "")).upper()
    if attribute not in duragreen.PART_FIELD or not value or not code:
        raise HTTPException(status_code=422, detail="A code part needs an option (%s), its value and its code."
                            % ", ".join(duragreen.PART_FIELD))
    _codes, parts, _rules = _vendor_rows(db, s.id)
    clash = [p for p in parts if p.attribute == attribute and duragreen.norm(p.value) == duragreen.norm(value)]
    if clash:
        raise HTTPException(status_code=409, detail="%s %s already has code %s - remove it first." % (
            attribute, value, clash[0].code))
    who = _user_email(db, effective_user_id(request)) or "someone"
    row = models.VendorCodePart(make=duragreen.MAKE, specsheet_id=s.id, attribute=attribute, value=value,
                                code=code, status="stated", source="entered by %s" % who,
                                note=str(body.get("note") or "").strip(), discipline=s.discipline)
    db.add(row)
    db.flush()
    log_activity(db, request, "npd.duragreen.part", "Set DuraGreen %s %s = %s on %s" % (
        attribute, value, code, s.product_name), details={"product_id": s.id, "part_id": row.id})
    db.commit()
    return {"id": row.id}


@app.delete("/api/npd/duragreen/parts/{part_id}")
def npd_duragreen_delete_part(part_id: int, request: Request, db: Session = Depends(get_db)):
    row = get_or_404(db, models.VendorCodePart, part_id, "code part")
    s = _duragreen_or_404(db, request, row.specsheet_id)
    log_activity(db, request, "npd.duragreen.part_delete", "Removed DuraGreen %s %s = %s from %s" % (
        row.attribute, row.value, row.code, s.product_name),
        details={"product_id": s.id, "attribute": row.attribute, "value": row.value, "code": row.code,
                 "status": row.status, "source": row.source})
    db.delete(row)
    db.commit()
    return {"ok": True}


# ---------- NPD (DuraGreen): a product brought in from DuraGreen's website or its spec sheet ----------
#
# A product DuraGreen launches after the 19 Oct catalogue run comes in from here, read by the same
# rules the scripts used (app/duragreen_import.py). Always two steps: a preview that reads and
# changes nothing in the database, then an import of the products the person ticked. New ones
# arrive pending - released by the same Approve as everything else in NPD (DuraGreen).

def _dg_find(db, discipline, series, name, url=""):
    """The DuraGreen product a reading is, if the app has it: by series and name (the identity the
    importer and the 19 Oct backfill give a product), else by its web page and name - DuraGreen
    renames families (MOOD ROUND and MOOD SQUARE became one MOOD page) but keeps the card's name."""
    q = db.query(models.Specsheet).filter(models.Specsheet.make == duragreen.MAKE,
                                          models.Specsheet.discipline == discipline)
    upper_name = func.upper(func.trim(models.Specsheet.product_name)) == name.strip().upper()
    hit = q.filter(func.upper(func.trim(models.Specsheet.series)) == series.strip().upper(),
                   upper_name).order_by(models.Specsheet.id).first()
    if hit is None and url:
        hit = q.filter(models.Specsheet.source_url == url, upper_name).order_by(models.Specsheet.id).first()
    return hit


def _dg_preview_item(db, key, row, built, warnings, existing):
    codes_now = (db.query(models.VendorOrderCode).filter(models.VendorOrderCode.specsheet_id == existing.id).all()
                 if existing is not None else [])
    _fields, options, _params = duragreen_import.merged(existing, built)
    adders_now = (existing.price_adders or {}) if existing is not None else {}
    return {
        # What the product costs now, and the options a price can vary by once it is brought in
        # (those with a choice, and any that already carry an adder) - for the preview's price boxes.
        "prices": {"base_price": existing.base_price if existing is not None else None,
                   "price_adders": adders_now},
        "price_options": {k: options[k] for k in duragreen.CONFIG_FIELDS
                          if isinstance(options.get(k), list) and (len(options[k]) > 1 or adders_now.get(k))},
        "key": key, "series": row["series"], "product_name": row["name"], "family": row["family"],
        "variant": row["variant"], "typology": row["typology"], "category": row["category"],
        "source_url": row["web"]["url"], "image_url": row["web"]["image_url"],
        "spec_sheet": built["pdf_name"] or "", "read_ok": built["read_ok"], "warnings": warnings,
        "existing": {"id": existing.id, "product_name": existing.product_name or "", "series": existing.series or "",
                     "typology": existing.typology or "", "approval_status": existing.approval_status or "approved"}
        if existing is not None else None,
        "field_count": len(built["fields"]), "options": built["options"],
        "codes": len(built["codes"]), "sample_codes": [c["code"] for c in built["codes"][:6]],
        "parts": len(built["parts"]),
        "changes": duragreen_import.changes(existing, built, codes_now, duragreen.LABEL_OF)
        if existing is not None else None,
    }


def _dg_build(db, request, row, pdf_path, sheet_name, typology=None):
    """(existing product or None, the reading) - the row read with the typology it will have."""
    existing = _dg_find(db, current_discipline(request), row["series"], row["name"], row["web"]["url"])
    if typology is not None:
        row["typology"], row["typology_set"] = typology, True
    elif existing is not None and existing.typology:
        row["typology"] = existing.typology
    built = duragreen_import.build(row, pdf_path, sheet_name)
    duragreen_import.name_unknown_configs(built, _duragreen_rules(db), *duragreen_import.merged(existing, built)[:2])
    return existing, built


@app.post("/api/npd/duragreen/import/preview")
def npd_duragreen_import_preview(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Reads a DuraGreen product page - every variant card on it, each with its spec sheet - and
    says what bringing each in would do. Writes nothing to the database."""
    token = duragreen_import.new_token()
    try:
        page, read = duragreen_import.read_from_url(body.get("url"), token)
    except duragreen_import.ImportProblem as e:
        shutil.rmtree(duragreen_import.PREVIEW_DIR / token, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(e))
    stored, items = [], []
    for n, (row, path, name, warnings) in enumerate(read):
        key = "c%d" % n
        stored.append({"key": key, "row": row, "pdf_path": path, "pdf_name": name, "pdf_url": row["spec_sheet"]})
        as_read = dict(row)                       # takes the existing product's typology, if any
        existing, built = _dg_build(db, request, as_read, path, name)
        items.append(_dg_preview_item(db, key, as_read, built, warnings + duragreen_import.reading_warnings(
            built, bool(path), name), existing))
    duragreen_import.save_preview(token, {"kind": "url", "url": page["url"], "items": stored})
    return {"token": token, "kind": "url", "page_title": page["title"], "category": (page["breadcrumb"] or [""])[0],
            "url": page["url"], "items": items}


@app.post("/api/npd/duragreen/import/preview-pdf")
def npd_duragreen_import_preview_pdf(request: Request, file: UploadFile = File(...), series: str = Form(""),
                                     product_name: str = Form(""), family: str = Form(""),
                                     typology: str = Form(""), source_url: str = Form(""),
                                     db: Session = Depends(get_db)):
    """Reads one DuraGreen spec sheet a person uploads, for a product the website does not show
    yet. Series and name are the person's; left blank, the sheet's own first lines are offered."""
    data = file.file.read(duragreen_import.MAX_PDF_BYTES + 1)
    if len(data) > duragreen_import.MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="That PDF is larger than 30 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="That file is not a PDF.")
    token = duragreen_import.new_token()
    path = duragreen_import.PREVIEW_DIR / token / "upload.pdf"
    path.write_bytes(data)
    name = Path(file.filename or "spec-sheet.pdf").name
    guess = duragreen_import.guess_identity(str(path))
    identity = {"series": series.strip() or guess.get("series") or "",
                "product_name": product_name.strip() or guess.get("product_name") or "",
                "family": family.strip(), "typology": typology.strip(), "source_url": source_url.strip()}
    row = duragreen_import.pdf_row(identity)
    as_read = dict(row)
    existing, built = _dg_build(db, request, as_read, str(path), name, typology=identity["typology"] or None)
    duragreen_import.save_preview(token, {"kind": "pdf", "items": [
        {"key": "p0", "row": row, "pdf_path": str(path), "pdf_name": name, "pdf_url": ""}]})
    item = _dg_preview_item(db, "p0", as_read, built, duragreen_import.reading_warnings(built, True, name), existing)
    item["guessed"] = not (series.strip() and product_name.strip())
    return {"token": token, "kind": "pdf", "items": [item]}


# ---------- NPD: which details the Configurator asks, per brand and typology ----------
#
# Decided in NPD by whoever looks after the brand; app/configurator.py turns the decision into the
# list both the Configure tab and the product form ask. A detail some product offers a choice of
# is always asked - the screen shows it locked and says why.

def _configurator_editor(db, request, make, typology):
    discipline = current_discipline(request)
    cands, n = configurator.candidates(db, discipline, make, typology)
    rows = configurator.saved(db, discipline, make, typology)
    if rows:
        shown = configurator.effective(cands, rows)
    else:
        by_key = {c["key"]: c for c in cands}
        shown = [dict(by_key[k]) for k in configurator.automatic_keys(cands)]
    sheets = (db.query(models.Specsheet.price_adders)
              .filter(models.Specsheet.discipline == discipline, models.Specsheet.make == make,
                      models.Specsheet.typology == typology).all())
    priced = {k for (adders,) in sheets for k, v in (adders or {}).items() if isinstance(v, dict) and v}
    scheme = product_codes.scheme_for(db, discipline, make, typology)
    coded = {seg.get("key") for seg in ((scheme.segments if scheme else None) or [])
             if seg.get("include", True) is not False}

    def why(c):
        if not c["choice"]:
            return ""
        n = c["choice_products"]
        out = ["%d product%s more than one, so a BOQ line must say which" % (n, " offers" if n == 1 else "s offer")]
        if c["key"] in coded:
            out.append("part of the product code")
        if c["key"] in priced:
            out.append("changes the price")
        return "; ".join(out)

    shown_keys = [c["key"] for c in shown]
    fields = ([dict(c, shown=True, locked=c["choice"], why=why(c)) for c in shown] +
              [dict(c, shown=False, locked=False, why="") for c in cands if c["key"] not in shown_keys])
    last = (db.query(models.ConfiguratorField)
            .filter(models.ConfiguratorField.discipline == discipline, models.ConfiguratorField.make == make,
                    models.ConfiguratorField.typology == typology)
            .order_by(models.ConfiguratorField.created_at.desc()).first())
    return {"make": make, "typology": typology, "products": n, "decided": bool(rows),
            "saved_by": last.updated_by if last else None,
            "saved_at": last.created_at.isoformat() if last and last.created_at else None,
            "fields": fields}


@app.get("/api/npd/configurator/decided")
def npd_configurator_decided(request: Request, make: str = Query(...), db: Session = Depends(get_db)):
    """The typologies of one brand whose Configurator has been decided in NPD - so the editor can
    mark them apart from the ones still on the automatic list."""
    rows = (db.query(models.ConfiguratorField.typology)
            .filter(models.ConfiguratorField.discipline == current_discipline(request),
                    models.ConfiguratorField.make == make.strip())
            .distinct().all())
    return {"typologies": sorted(t for (t,) in rows)}


@app.get("/api/npd/configurator")
def npd_configurator(request: Request, make: str = Query(...), typology: str = Query(...),
                     db: Session = Depends(get_db)):
    """What the Configurator asks for one brand's typology, every detail it could ask, and why
    each choice is always asked - for NPD's "Configurator questions" panel."""
    return _configurator_editor(db, request, make.strip(), typology.strip())


@app.put("/api/npd/configurator")
def npd_configurator_save(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Saves which details the Configurator asks for one brand's typology, in order:
    {make, typology, fields: [{key, label}]}. Every detail some product offers a choice of is kept
    (added at the end if the list leaves it out). A label equal to the NPD name is stored blank,
    so renaming the parameter in NPD later still reaches the Configurator."""
    make = str(body.get("make") or "").strip()
    typology = str(body.get("typology") or "").strip()
    if not make or not typology:
        raise HTTPException(status_code=422, detail="Say which brand and typology.")
    discipline = current_discipline(request)
    cands, _n = configurator.candidates(db, discipline, make, typology)
    by_key = {c["key"]: c for c in cands}
    wanted, seen = [], set()
    for f in body.get("fields") or []:
        key = str((f or {}).get("key") or "").strip()
        if key not in by_key:
            raise HTTPException(status_code=422, detail="No %s %s holds a detail called %r." % (make, typology, key))
        if key in seen:
            continue
        seen.add(key)
        label = " ".join(str(f.get("label") or "").split())[:60]
        wanted.append((key, "" if label == by_key[key]["npd_label"] else label))
    wanted += [(c["key"], "") for c in cands if c["choice"] and c["key"] not in seen]
    if not wanted:
        raise HTTPException(status_code=422, detail="Show at least one detail.")
    before = [c["label"] for c in _configurator_editor(db, request, make, typology)["fields"] if c["shown"]]
    q = db.query(models.ConfiguratorField).filter(models.ConfiguratorField.discipline == discipline,
                                                  models.ConfiguratorField.make == make,
                                                  models.ConfiguratorField.typology == typology)
    q.delete(synchronize_session=False)
    who = _user_email(db, effective_user_id(request)) or ""
    for i, (key, label) in enumerate(wanted):
        db.add(models.ConfiguratorField(discipline=discipline, make=make, typology=typology, field_key=key,
                                        label=label or None, sort_order=i, updated_by=who))
    db.flush()
    after = [c["label"] for c in _configurator_editor(db, request, make, typology)["fields"] if c["shown"]]
    log_activity(db, request, "npd.configurator", "Set what the Configurator asks for %s %s: %s" % (
        make, typology, ", ".join(after)), details={"make": make, "typology": typology,
                                                    "before": before, "after": after})
    db.commit()
    return _configurator_editor(db, request, make, typology)


@app.delete("/api/npd/configurator")
def npd_configurator_reset(request: Request, make: str = Query(...), typology: str = Query(...),
                           db: Session = Depends(get_db)):
    """Puts one brand's typology back on the Configurator's automatic list."""
    discipline = current_discipline(request)
    make, typology = make.strip(), typology.strip()
    q = db.query(models.ConfiguratorField).filter(models.ConfiguratorField.discipline == discipline,
                                                  models.ConfiguratorField.make == make,
                                                  models.ConfiguratorField.typology == typology)
    removed = q.count()
    if removed:
        before = [c["label"] for c in _configurator_editor(db, request, make, typology)["fields"] if c["shown"]]
        q.delete(synchronize_session=False)
        log_activity(db, request, "npd.configurator_reset", "Put the Configurator for %s %s back to automatic"
                     % (make, typology), details={"make": make, "typology": typology, "before": before})
        db.commit()
    return _configurator_editor(db, request, make, typology)


def _dg_typed_prices(chosen):
    """(base price or None, {field: {value: adder}}) typed into one preview row, blanks left out -
    or 422. Read for every row before anything is written, so a mistyped figure cannot leave a
    half-made import's files (a stored sheet, a picture) behind."""
    raw = chosen.get("base_price")
    base = None
    if raw not in (None, ""):
        try:
            base = _money(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="The base price %r is not an amount." % raw)
        if base < 0:
            raise HTTPException(status_code=422, detail="A base price cannot be negative.")
    return base, _clean_adders(chosen.get("price_adders"))


def _dg_import_prices(request, sheet, chosen):
    """The prices typed into a preview row, laid over what the product costs now:
    [(what, before, after)] for each that changed. A blank box leaves that price as it is - an
    import can set or change a price, never remove one (removing is a deliberate act in the
    product's own page). Changing a price asks for "Edit prices", as the product's page does."""
    typed_base, typed_adders = _dg_typed_prices(chosen)
    base = sheet.base_price if typed_base is None else typed_base
    adders = {k: dict(v) for k, v in (sheet.price_adders or {}).items() if isinstance(v, dict)}
    for key, per in typed_adders.items():
        adders.setdefault(key, {}).update(per)
    changes = _price_changes(sheet.base_price, sheet.price_adders, {}, base, adders, {})
    if changes and not user_can(request, "prices.edit"):
        raise HTTPException(status_code=403, detail="%s: you can bring the product in but not change its "
                            "prices - that needs “Edit prices”. Clear the price boxes and try again."
                            % sheet.product_name)
    if changes:
        sheet.base_price, sheet.price_adders = base, adders
    return changes


@app.post("/api/npd/duragreen/import")
def npd_duragreen_import(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Brings in the products ticked in a preview. Each is read again from the stored page's
    sheets (so what is written is exactly what the preview showed, with the typology chosen
    there) and written by duragreen_import.apply: new ones pending, existing ones updated with
    their approval and hand-entered codes left alone. A price changes only where the person typed
    one in the preview row (base_price, price_adders) - see _dg_import_prices. All or nothing."""
    import json
    try:
        preview = duragreen_import.load_preview(body.get("token"))
    except duragreen_import.ImportProblem as e:
        raise HTTPException(status_code=410, detail=str(e))
    by_key = {it["key"]: it for it in preview["items"]}
    chosen = [c for c in (body.get("items") or []) if c.get("key") in by_key]
    if not chosen:
        raise HTTPException(status_code=422, detail="Tick at least one product to bring in.")
    for c in chosen:
        _dg_typed_prices(c)                       # every figure checked before anything is written
    discipline = current_discipline(request)
    rules = _duragreen_rules(db)
    stored_upload = {}
    results = []
    for c in chosen:
        it = by_key[c["key"]]
        row = json.loads(json.dumps(it["row"]))
        if preview["kind"] == "pdf":
            for k_in, k_row in (("series", "series"), ("product_name", "name"), ("family", "family")):
                if str(c.get(k_in) or "").strip():
                    row[k_row] = str(c[k_in]).strip()
            row = dict(duragreen_import.pdf_row({"series": row["series"], "product_name": row["name"],
                                                 "family": row["family"], "typology": row["typology"],
                                                 "source_url": row["web"]["url"]}))
        if not row["series"].strip() or not row["name"].strip():
            raise HTTPException(status_code=422, detail="A DuraGreen product needs a series and a name.")
        typology = str(c["typology"]).strip() if c.get("typology") is not None else None
        existing, built = _dg_build(db, request, row, it["pdf_path"], it["pdf_name"], typology=typology)
        slug = row["series"] + "_" + row["name"]
        image_url = row["web"]["image_url"] if preview["kind"] == "url" else ""
        if preview["kind"] == "url":
            pdf_ref = it["pdf_url"]
        else:
            if it["pdf_path"] not in stored_upload:
                dest = _unique_upload_dest(it["pdf_name"])
                shutil.copy2(it["pdf_path"], dest)
                stored_upload[it["pdf_path"]] = str(dest)
            pdf_ref = stored_upload[it["pdf_path"]]
        # The website card's picture; failing that, one from the product's own sheet - the drawing
        # beside its name where the sheet shows several products, never the whole system's drawing
        # (duragreen_import.picture_for). Until 27 Oct a card with no picture gave none, and a PDF
        # gave its largest picture to every product on it.
        mates = [by_key[o["key"]]["row"]["name"] for o in chosen
                 if o["key"] != c["key"] and by_key[o["key"]]["pdf_path"] == it["pdf_path"]]

        def picture(u=image_url, p=it["pdf_path"], n=row["name"], m=mates, s=slug):
            got = duragreen_import.fetch_image(u, s) if u else ""
            return got or (duragreen_import.picture_for(p, n, m, s)[0] if p else "")
        sheet, action, written = duragreen_import.apply(db, models, existing, row, built, discipline=discipline,
                                                        rules=rules, pdf_ref=pdf_ref, picture=picture)
        prices = _dg_import_prices(request, sheet, c)
        results.append({"id": sheet.id, "action": action, "series": sheet.series, "product_name": sheet.product_name,
                        "approval_status": sheet.approval_status or "approved", "codes_written": written,
                        "price_changes": [{"what": w, "before": a, "after": b} for w, a, b in prices]})
    added = [r for r in results if r["action"] == "added"]
    updated = [r for r in results if r["action"] == "updated"]
    priced = [r for r in results if r["price_changes"]]
    source = preview.get("url") or "an uploaded spec sheet (%s)" % by_key[chosen[0]["key"]]["pdf_name"]
    log_activity(db, request, "npd.duragreen.import", "Brought in DuraGreen products from %s: %d added (awaiting "
                 "approval), %d updated%s" % (source, len(added), len(updated),
                                              "; prices changed on %s" % ", ".join(
                                                  "%s (%s)" % (r["product_name"], _describe_price_changes(
                                                      [(p["what"], p["before"], p["after"]) for p in r["price_changes"]]))
                                                  for r in priced) if priced else ""),
                 details={"source": source, "products": results})
    db.commit()
    shutil.rmtree(duragreen_import.PREVIEW_DIR / body["token"], ignore_errors=True)
    return {"results": results}
