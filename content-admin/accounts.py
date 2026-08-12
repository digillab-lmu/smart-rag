"""
Accounts and who may work on which course.

Replaces the single account that lived in .env. Two roles and nothing more:

  * `admin` — creates courses and accounts, and may work in every course.
    There is at least one, always: the last one cannot be removed or demoted,
    because an installation with no administrator can only be repaired from a
    shell.
  * `maintainer` — works in the courses they are assigned to, and sees no
    others. Assigned to several, because the same person routinely looks
    after a lecture and its seminar (ARCHITECTURE 6c).

The rule that matters is one sentence: **a maintainer may act on a course
only if there is a row in `user_courses` for the pair.** It is enforced in
one place — `may_access()`, called from the one decorator every course-bound
route carries — because a check repeated per route is a check that will be
missing from the next route somebody adds, and the omission is invisible
until a maintainer sees another course's material.

Passwords are hashed with werkzeug's default (scrypt). Reset tokens are
stored as a sha256 of the token: the row is readable by anyone with database
access, and a usable token sitting there would be a standing key.
"""

import hashlib
import hmac
import logging
import secrets
import time

from werkzeug.security import check_password_hash, generate_password_hash

import db

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
RESET_TTL_SECONDS = 3600

ROLE_ADMIN = "admin"
ROLE_MAINTAINER = "maintainer"
ROLES = (ROLE_ADMIN, ROLE_MAINTAINER)


class AccountError(RuntimeError):
    """Phrased for whoever triggered it, not for a log."""


def _row(r) -> dict:
    return {"id": r[0], "username": r[1], "email": r[2], "role": r[3],
            "created_at": r[4], "last_login_at": r[5]}


_COLS = "id, username, email, role, created_at, last_login_at"


# ─── Reading ─────────────────────────────────────────────────────────────────

def any_account_exists() -> bool:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users LIMIT 1")
            found = cur.fetchone() is not None
        conn.commit()
    return found


def get_by_username(username: str) -> dict | None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM users WHERE lower(username) = lower(%s)",
                        (username.strip(),))
            row = cur.fetchone()
        conn.commit()
    return _row(row) if row else None


def get(user_id: int) -> dict | None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        conn.commit()
    return _row(row) if row else None


def all_accounts() -> list[dict]:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM users ORDER BY role, lower(username)")
            rows = [_row(r) for r in cur.fetchall()]
            cur.execute("SELECT user_id, course_id FROM user_courses")
            pairs = cur.fetchall()
        conn.commit()
    by_user: dict[int, list[str]] = {}
    for user_id, course_id in pairs:
        by_user.setdefault(user_id, []).append(course_id)
    for row in rows:
        row["courses"] = sorted(by_user.get(row["id"], []))
    return rows


def courses_of(user_id: int) -> list[str]:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT course_id FROM user_courses WHERE user_id = %s "
                        "ORDER BY course_id", (user_id,))
            rows = [r[0] for r in cur.fetchall()]
        conn.commit()
    return rows


def may_access(user: dict | None, course_id: str) -> bool:
    """The single authorisation question in this system.

    Everything that acts on a course goes through here. An administrator may
    work anywhere; a maintainer needs the assignment. A missing user is not
    an administrator by accident — the None case answers no.
    """
    if not user or not course_id:
        return False
    if user.get("role") == ROLE_ADMIN:
        return True
    return course_id in courses_of(user["id"])


# ─── Writing ─────────────────────────────────────────────────────────────────

def _check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(
            f"A password needs at least {MIN_PASSWORD_LENGTH} characters.")


def create_account(username: str, password: str, role: str = ROLE_MAINTAINER,
                   email: str = "") -> dict:
    username = (username or "").strip()
    if not username:
        raise AccountError("An account needs a user name.")
    if role not in ROLES:
        raise AccountError(f"{role!r} is not a role. Use one of {', '.join(ROLES)}.")
    _check_password(password)
    if get_by_username(username):
        raise AccountError(f"There is already an account called {username!r}.")

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, email, password_hash, role) "
                "VALUES (%s, %s, %s, %s) RETURNING " + _COLS,
                (username, email.strip() or None,
                 generate_password_hash(password), role))
            row = _row(cur.fetchone())
        conn.commit()
    logger.info("Account %s created with role %s", username, role)
    return row


def set_password(user_id: int, password: str) -> None:
    _check_password(password)
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash = %s, "
                        "reset_token_hash = NULL, reset_expires_at = NULL "
                        "WHERE id = %s", (generate_password_hash(password), user_id))
        conn.commit()


def verify_login(username: str, password: str) -> dict | None:
    """The account on success, None on failure — never a reason.

    Same answer for an unknown user and a wrong password, and the hash of a
    non-existent user is still checked, so the two do not differ in timing
    either. A login page that distinguishes them is a way to enumerate
    accounts.
    """
    dummy = generate_password_hash("not-a-real-password")
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS}, password_hash FROM users "
                        "WHERE lower(username) = lower(%s)", ((username or "").strip(),))
            row = cur.fetchone()
        conn.commit()

    stored = row[-1] if row else dummy
    if not check_password_hash(stored, password or "") or not row:
        return None

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (row[0],))
        conn.commit()
    return _row(row[:-1])


def set_role(user_id: int, role: str) -> None:
    if role not in ROLES:
        raise AccountError(f"{role!r} is not a role.")
    if role != ROLE_ADMIN and _is_last_admin(user_id):
        raise AccountError(
            "This is the last administrator. Demoting it would leave the "
            "installation with nobody who can create courses or accounts, "
            "and that can only be repaired from a shell.")
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET role = %s WHERE id = %s", (role, user_id))
        conn.commit()


def delete_account(user_id: int) -> None:
    if _is_last_admin(user_id):
        raise AccountError(
            "This is the last administrator and cannot be removed. Make "
            "somebody else an administrator first.")
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()


def _is_last_admin(user_id: int) -> bool:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM users WHERE role = %s", (ROLE_ADMIN,))
            admins = cur.fetchone()[0]
            cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        conn.commit()
    return bool(row) and row[0] == ROLE_ADMIN and admins <= 1


def assign(user_id: int, course_id: str) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_courses (user_id, course_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING", (user_id, course_id))
        conn.commit()


def unassign(user_id: int, course_id: str) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_courses WHERE user_id = %s AND course_id = %s",
                        (user_id, course_id))
        conn.commit()


# ─── Password reset ──────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET reset_token_hash = %s, "
                "reset_expires_at = now() + make_interval(secs => %s) WHERE id = %s",
                (_hash_token(token), RESET_TTL_SECONDS, user_id))
        conn.commit()
    return token


def user_for_reset_token(token: str) -> dict | None:
    """The account a token belongs to, or None. Expiry counts as invalid, and
    an unparseable stored value counts as invalid too — a reset path that
    fails open is worse than one that fails."""
    if not token:
        return None
    wanted = _hash_token(token)
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS}, reset_token_hash FROM users "
                "WHERE reset_token_hash IS NOT NULL AND reset_expires_at > now()")
            rows = cur.fetchall()
        conn.commit()
    for row in rows:
        # compare_digest over every candidate rather than a WHERE on the
        # hash: the number of accounts with a live token is tiny, and this
        # keeps the comparison constant-time.
        if hmac.compare_digest(row[-1], wanted):
            return _row(row[:-1])
    return None


def clear_reset_token(user_id: int) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET reset_token_hash = NULL, "
                        "reset_expires_at = NULL WHERE id = %s", (user_id,))
        conn.commit()
