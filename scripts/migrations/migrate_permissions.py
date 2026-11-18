"""Give every existing account the permissions that match what it could already do.

Access used to be two fixed levels: an admin could do everything, and every other account
could see and edit BOQs and add specsheets. It is now a list of named permissions an admin
gives per person on the People tab. So that nobody loses anything on the day that changes,
each existing member is given exactly what they could already do - "See BOQs", "Build and edit
BOQs", "Add and edit specsheets" - and nothing more. Admins need no rows; they hold everything.

Runs once. It records that it has run, so running it again (on a later deploy, by mistake)
cannot hand back access an admin has since taken away.

    python scripts/migrations/migrate_permissions.py --dry-run
    python scripts/migrations/migrate_permissions.py
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
import argparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import SessionLocal
from app import models
from app.main import DEFAULT_NEW_PERMISSIONS

DONE = "permissions_migrated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    if db.get(models.AppSetting, DONE):
        print("Already run on this database - nothing to do.")
        db.close()
        return

    given = 0
    for user in db.query(models.User).order_by(models.User.email).all():
        if user.role == "admin":
            print("   %-40s admin - holds everything already" % user.email)
            continue
        have = {g.permission for g in db.query(models.UserPermission)
                .filter(models.UserPermission.user_id == user.id).all()}
        missing = [k for k in DEFAULT_NEW_PERMISSIONS if k not in have]
        print("   %-40s member - given %s" % (user.email, ", ".join(missing) or "nothing new"))
        for key in missing:
            db.add(models.UserPermission(user_id=user.id, permission=key,
                                         granted_by="access-change migration"))
            given += 1

    print()
    print("permissions given: %d" % given)
    if args.dry_run:
        db.rollback()
        print("--dry-run: nothing written.")
    else:
        db.add(models.AppSetting(key=DONE, value=True, updated_by="migrate_permissions.py"))
        db.commit()
        print("done.")
    db.close()


if __name__ == "__main__":
    main()
