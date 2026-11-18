"""Google Sign-In, restricted to @example.com accounts - see GoogleAuthSetup.md for the
one-time Google Cloud Console steps this depends on (creating the OAuth client there is a
human-only step, not something this code can do).

Uses Authlib's Starlette integration rather than hand-rolling the OAuth2/OIDC flow: it
correctly handles the parts that are easy to get subtly wrong by hand - CSRF `state`,
replay-resistant `nonce`, and verifying the ID token's signature against Google's published
keys (`.well-known/openid-configuration`) before trusting anything in it. A hand-rolled
version that "worked" in casual testing but skipped nonce/signature checks would still be a
real vulnerability, not just untidy code - not worth risking on an auth feature.

Domain restriction lives here, in application code, not left to Google Cloud Console's own
OAuth consent screen settings alone: even if the consent screen is ever set to "External"
(or misconfigured), `is_allowed_email()` is the actual gate. If the studio's Google
Workspace lets the consent screen be set to "Internal" (see GoogleAuthSetup.md), that's a
second, Google-enforced layer on top of this - not a replacement for it.
"""
import os

from authlib.integrations.starlette_client import OAuth

ALLOWED_EMAIL_DOMAIN = "example.com"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_CONFIGURED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

oauth = OAuth()
if GOOGLE_CONFIGURED:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def is_allowed_email(claims: dict) -> bool:
    """`claims` is the verified ID token payload Authlib hands back from
    `authorize_access_token()` - already signature-checked, so `email`/`email_verified` here
    are Google's own claims about the account that just completed the OAuth flow, not
    anything the browser could have supplied directly.

    `email_verified` matters as much as the domain check: Google allows an account to *set*
    an email-like value that isn't actually verified in some flows, so checking the domain
    suffix alone without also requiring verification would be checking a string, not an
    actual identity guarantee."""
    if not claims.get("email_verified"):
        return False
    email = (claims.get("email") or "").strip().lower()
    return email.endswith(f"@{ALLOWED_EMAIL_DOMAIN}")
