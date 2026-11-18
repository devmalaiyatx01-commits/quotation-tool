"""One-off/ad-hoc CLI to create or update a login account - the only way to provision
users in this minimal auth setup (no public self-registration, matching deployment.md's
"admin-provisioned accounts" choice for a confidential internal tool - see section 3.3).

Safe to re-run: an existing email gets its password/role updated in place rather than
erroring or creating a duplicate account, so this doubles as "reset someone's password"
and "promote/demote an admin."

Usage: python create_user.py
"""
from app.database import SessionLocal, Base, engine
from app import models
from app.auth import hash_password


def prompt_password() -> str:
    """Deliberately plain input(), not getpass.getpass() - getpass reads the real
    Windows console device directly (via msvcrt), which several common terminals on
    this platform don't actually provide (Git Bash/MSYS in particular, also some IDE
    integrated terminals): instead of erroring, it just hangs forever with no way to
    tell what's wrong. Confirmed directly - not assumed - that it hangs rather than
    raises here, so a try/except around it wouldn't have helped either. Password
    visible while typing is a real but acceptable tradeoff for a script one admin
    runs occasionally to provision accounts, versus a script that unpredictably
    freezes depending on which terminal happens to be open."""
    print("(Typed password will be visible - this script doesn't hide input, see the comment in create_user.py for why.)")
    return input("Password: ")


def main():
    Base.metadata.create_all(bind=engine)  # ensures the users table exists even on a brand-new db
    db = SessionLocal()
    try:
        email = input("Email: ").strip().lower()
        if not email:
            print("Email is required.")
            return
        # The same rule the app enforces at sign-in: an account for any other address could
        # never be used, so refusing it here saves making one by mistake.
        if not email.endswith("@example.com"):
            print("Only @example.com addresses can use the app.")
            return
        password = prompt_password()
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            return
        role = (input("Role (admin/member) [member]: ").strip().lower() or "member")
        if role not in ("admin", "member"):
            print(f"Unknown role {role!r} - must be 'admin' or 'member'.")
            return

        user = db.query(models.User).filter(models.User.email == email).first()
        if user:
            user.password_hash = hash_password(password)
            user.role = role
            db.commit()
            print(f"Updated existing user {email} (role={role}).")
        else:
            db.add(models.User(email=email, password_hash=hash_password(password), role=role))
            db.commit()
            print(f"Created new user {email} (role={role}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
