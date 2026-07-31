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

from functools import wraps

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from env_file import read_env, set_env_var


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


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped
