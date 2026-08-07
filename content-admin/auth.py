"""
Single-admin-account auth. Deliberately simple: this is a low-traffic
internal tool with exactly one operator account, not a multi-user system —
Flask's built-in signed-cookie session (itsdangerous, keyed by
CONTENT_ADMIN_SESSION_SECRET) is sufficient and needs no server-side store.
No Redis dependency for this reason (see commit message for the reasoning).

Credentials live in .env (CONTENT_ADMIN_USERNAME / CONTENT_ADMIN_PASSWORD_HASH)
via env_file.py's set_env_var() — the one canonical secrets location this
whole project already uses, so scripts/admin.sh's Secrets overview picks
this up automatically too.
"""

import hashlib
import hmac
import secrets
import time
from functools import wraps

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from env_file import read_env, set_env_var

# How long a reset link stays usable. Long enough to survive a mail relay
# that queues for a few minutes, short enough that a link left in an inbox
# is not a standing key to the system.
RESET_TTL_SECONDS = 3600

MIN_PASSWORD_LENGTH = 12


def is_configured() -> bool:
    env = read_env()
    return bool(env.get("CONTENT_ADMIN_USERNAME")) and bool(
        env.get("CONTENT_ADMIN_PASSWORD_HASH")
    )


def create_admin_account(username: str, password: str) -> None:
    set_env_var("CONTENT_ADMIN_USERNAME", username)
    set_env_var("CONTENT_ADMIN_PASSWORD_HASH", generate_password_hash(password))


def verify_login(username: str, password: str) -> bool:
    env = read_env()
    if username != env.get("CONTENT_ADMIN_USERNAME"):
        return False
    stored_hash = env.get("CONTENT_ADMIN_PASSWORD_HASH", "")
    return bool(stored_hash) and check_password_hash(stored_hash, password)


def set_password(password: str) -> None:
    """Replace the password, keeping the username."""
    set_env_var("CONTENT_ADMIN_PASSWORD_HASH", generate_password_hash(password))


# ─── Password reset by email ─────────────────────────────────────────────────
# The token is stored hashed, for the same reason the password is: .env is
# readable by root and appears in the admin TUI's secrets overview, and a
# token in there is a usable key until it expires. SHA-256 without a salt is
# right here and would be wrong for a password — the token is 32 bytes of
# CSPRNG output, so there is nothing to guess and nothing to precompute.

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset_token() -> str:
    """Generate a reset token, store its hash and expiry, return the token."""
    token = secrets.token_urlsafe(32)
    set_env_var("CONTENT_ADMIN_RESET_TOKEN_HASH", _hash_token(token))
    set_env_var("CONTENT_ADMIN_RESET_EXPIRES", str(int(time.time()) + RESET_TTL_SECONDS))
    return token


def clear_reset_token() -> None:
    set_env_var("CONTENT_ADMIN_RESET_TOKEN_HASH", "")
    set_env_var("CONTENT_ADMIN_RESET_EXPIRES", "")


def verify_reset_token(token: str) -> bool:
    """True only for the current, unexpired token."""
    env = read_env()
    stored = env.get("CONTENT_ADMIN_RESET_TOKEN_HASH", "").strip()
    expires_raw = env.get("CONTENT_ADMIN_RESET_EXPIRES", "").strip()
    if not stored or not expires_raw or not token:
        return False
    try:
        expires = int(expires_raw)
    except ValueError:
        # An unreadable expiry is not a reason to accept the token.
        return False
    if time.time() > expires:
        return False
    # compare_digest, not ==: the comparison is against a secret, and this
    # endpoint is reachable by anyone who can reach the login page.
    return hmac.compare_digest(stored, _hash_token(token))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped
