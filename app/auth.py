"""Password hashing for login accounts (see app/models.py's User, deployment.md
section 3.3). Uses the stdlib's PBKDF2-HMAC-SHA256 rather than pulling in passlib/
bcrypt - one fewer dependency, and PBKDF2 at a high iteration count is a NIST-approved,
well-understood choice for a 20-30 person internal tool (not a public-facing service
under credential-stuffing load, where bcrypt/argon2's extra cost would matter more).
"""
import hashlib
import hmac
import secrets

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f"{PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        iterations_s, salt, digest = stored.split("$")
        iterations = int(iterations_s)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations).hex()
    # constant-time compare - a timing difference here would leak how many leading
    # hex characters of the hash matched, an actual side channel for password guessing
    return hmac.compare_digest(candidate, digest)
