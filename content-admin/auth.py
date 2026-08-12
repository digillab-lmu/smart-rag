"""
Sessions: who is logged in, and the two decorators that guard a route.

The accounts themselves live in the database now (accounts.py). This module
is only the part that belongs to a request: the signed cookie, resolving it
back to an account, and refusing when it does not.

Why the session holds an id and not the account. A cookie is written once and
read for as long as it lives; a role copied into it at login would survive
the account being demoted or deleted. The id is looked up per request, which
costs one small query and means "no longer an administrator" takes effect
immediately rather than at next login.

The account that used to live in .env is adopted on first use — see
adopt_legacy_account(). Without it the first start after this change would
find no accounts, offer its first-run setup page, and let anybody who reaches
the address claim the installation.
"""

import logging
from functools import wraps

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash

import accounts
from env_file import read_env, set_env_var

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = accounts.MIN_PASSWORD_LENGTH


def current_user() -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return accounts.get(user_id)


def log_in(user: dict) -> None:
    session["user_id"] = user["id"]
    session["logged_in"] = True          # what the layout checks
    session["username"] = user["username"]


def log_out() -> None:
    session.clear()


def is_configured() -> bool:
    """Whether this installation has any account at all."""
    return accounts.any_account_exists()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    wrapped.__login_required__ = True
    return wrapped


def admin_required(view):
    """For the pages that manage the installation rather than a course.

    Separate from login_required rather than folded into it: a maintainer
    reaching an admin page is a different situation from a stranger reaching
    any page, and answering both with the login form would send a
    legitimately logged-in person round in a circle.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if user["role"] != accounts.ROLE_ADMIN:
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    # An explicit marker, because functools.wraps makes a wrapper
    # indistinguishable from the function it wraps — a test that tried to
    # recognise the decoration by name saw only the view's own name and
    # would have passed for an unguarded route.
    wrapped.__admin_only__ = True
    return wrapped


def adopt_legacy_account() -> bool:
    """Move the single .env account into the database, once.

    Before accounts existed, there was one, in CONTENT_ADMIN_USERNAME and
    CONTENT_ADMIN_PASSWORD_HASH. Leaving it behind would mean the first start
    after this change finds no accounts, shows the first-run setup page, and
    hands the installation to whoever opens it first.

    The hash is carried across as it is, so the existing password keeps
    working and nobody has to be told a new one. The .env values are cleared
    afterwards: two places holding a credential is one place too many, and
    the stale one is the one somebody will later "fix" the login with.
    """
    if accounts.any_account_exists():
        return False
    env = read_env()
    username = env.get("CONTENT_ADMIN_USERNAME", "").strip()
    password_hash = env.get("CONTENT_ADMIN_PASSWORD_HASH", "").strip()
    if not username or not password_hash:
        return False

    with accounts.db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, email, password_hash, role) "
                "VALUES (%s, %s, %s, %s)",
                (username, env.get("ADMIN_EMAIL", "").strip() or None,
                 password_hash, accounts.ROLE_ADMIN))
        conn.commit()
    set_env_var("CONTENT_ADMIN_USERNAME", "")
    set_env_var("CONTENT_ADMIN_PASSWORD_HASH", "")
    logger.info("Adopted the .env account %r as the first administrator", username)
    return True


def verify_password(user: dict, password: str) -> bool:
    """Used where an action needs the current password again."""
    with accounts.db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],))
            row = cur.fetchone()
        conn.commit()
    return bool(row) and check_password_hash(row[0], password or "")
