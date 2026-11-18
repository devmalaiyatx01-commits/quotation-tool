"""Grants or revokes an account's access to a tool (architectural / decorative).

Access is per account because the two tools are run by different people - an account with only
["decorative"] cannot reach architectural products or BOQs at all, and the chooser after login
shows it only what it may open.

    python scripts/maintenance/grant_discipline.py list
    python scripts/maintenance/grant_discipline.py grant  someone@example.com decorative
    python scripts/maintenance/grant_discipline.py revoke someone@example.com architectural
    python scripts/maintenance/grant_discipline.py set    someone@example.com decorative architectural

`set` replaces the whole list; grant/revoke change one entry. An account is never left with an
empty list - that would lock it out of everything with no way back short of this script - so
revoking the last one is refused.
"""
# --- repo-root shim ------------------------------------------------------------------
# Running a script file puts ITS OWN directory on sys.path, not the repo root, so `app`
# would not import and a `Path(__file__).parent` would no longer be the project root.
# Both are fixed here by walking up to the directory that contains app/ - the same rule
# app/database.py uses. Run this script from anywhere; paths below stay repo-relative.
import pathlib as _pathlib, sys as _sys
_REPO_ROOT = next((p for p in _pathlib.Path(__file__).resolve().parents
                   if (p / "app").is_dir()), None)
if _REPO_ROOT is None:
    raise SystemExit("Cannot find the repo root: no parent directory contains app/.")
_sys.path.insert(0, str(_REPO_ROOT))
# --- end shim ------------------------------------------------------------------------
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app import models


def show(db):
    for u in db.query(models.User).order_by(models.User.email).all():
        print("   %-38s %-8s %s" % (u.email, u.role, list(u.disciplines or ["architectural"])))


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("list", "grant", "revoke", "set"):
        print(__doc__)
        return 1

    db = SessionLocal()
    action = args[0]

    if action == "list":
        show(db)
        return 0

    if len(args) < 3:
        print(__doc__)
        return 1

    email = args[1].strip().lower()
    wanted = [d.strip().lower() for d in args[2:]]
    bad = [d for d in wanted if d not in models.DISCIPLINES]
    if bad:
        print("Not a discipline: %s (valid: %s)" % (", ".join(bad), ", ".join(models.DISCIPLINES)))
        return 1

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        print("No account with that email: %s" % email)
        return 1

    current = [d for d in (user.disciplines or ["architectural"]) if d in models.DISCIPLINES]
    if action == "grant":
        new = current + [d for d in wanted if d not in current]
    elif action == "revoke":
        new = [d for d in current if d not in wanted]
    else:
        new = list(dict.fromkeys(wanted))

    if not new:
        print("Refusing to leave %s with no tools - it could not sign in to anything." % email)
        return 1

    # Assigned as a new list, not mutated in place: SQLAlchemy does not see an in-place change
    # to a JSON column and would save nothing at all.
    user.disciplines = new
    db.commit()
    print("%s: %s -> %s" % (email, current, new))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
