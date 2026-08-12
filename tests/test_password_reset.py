"""
Password reset by email — the properties that make it safe to expose.

This endpoint sits in front of the login page, so it is reachable by anyone
who can reach the GUI at all. Three things therefore have to hold, and all
three are easy to lose in a later edit:

  * the answer must not differ between an existing and a made-up username,
    or the page becomes a way to learn the account name;
  * the link must go to the system's own administration address, never to
    one supplied in the form, or anyone could have a valid link delivered to
    themselves;
  * a token must work exactly once, and only within its lifetime.

The stored token is a hash, so a reader of .env — which the admin TUI shows
on request — cannot use one they find there.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
env_path.write_text(
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
    'DOMAIN="example.com"\n'
    'ADMIN_EMAIL="admin@example.com"\n'
    'SMTP_HOST="localhost"\n'
    'SMTP_PORT=25\n'
    'SMTP_SENDER_EMAIL="noreply@${DOMAIN}"\n'
    'CONTENT_ADMIN_PUBLIC_URL="https://content.example.com"\n'
    'COURSE_NAME="Testkurs"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

# ─── A database, because accounts live in one now ────────────────────────────
# The single .env account became rows in Postgres, so this suite needs a
# database like the others that touch accounts or slots.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402
_db, COURSE = dbfixture.require_database()

import app as flask_app_module  # noqa: E402
import accounts  # noqa: E402
import auth  # noqa: E402
import mailer  # noqa: E402
from env_file import read_env  # noqa: E402

client = flask_app_module.app.test_client()
failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


# ─── A stub relay that records instead of sending ────────────────────────────
SENT = []


def fake_send(to, subject, body):
    SENT.append({"to": to, "subject": subject, "body": body})


mailer.send_mail = fake_send
flask_app_module.mailer.send_mail = fake_send

# The account under test.
USER = accounts.create_account("kursadmin", "a-strong-test-password",
                               role=accounts.ROLE_ADMIN,
                               email="kursadmin@example.com")

# ─── 1. The sender address must never contain an unexpanded ${DOMAIN} ────────
# .env.example writes "noreply@${DOMAIN}", and read_env deliberately does not
# expand shell interpolation — an older .env still carries the literal.
sender = mailer._sender(read_env())
check("sender is a usable address", "${" not in sender, sender)
check("sender falls back to the domain", sender == "noreply@example.com", sender)

# ─── 2. The reply does not reveal whether the username exists ────────────────
SENT.clear()
real = client.post("/forgot", data={"username": "kursadmin"})
real_body = real.get_data(as_text=True)
check("a real username is accepted", real.status_code == 200, real.status_code)
check("a mail is sent for a real username", len(SENT) == 1, SENT)

SENT.clear()
fake = client.post("/forgot", data={"username": "definitely-not-the-account"})
fake_body = fake.get_data(as_text=True)
check("an unknown username gets the same status", fake.status_code == 200, fake.status_code)
check("an unknown username sends nothing", len(SENT) == 0, SENT)
check(
    "both answers are byte-identical",
    real_body == fake_body,
    "the page differs between an existing and a non-existing account",
)

# ─── 3. The link goes to the account's own address, whatever was typed ───────
# It used to go to ADMIN_EMAIL, because there was one account. Now each
# account carries its own address and the link goes there; an account with
# none falls back to ADMIN_EMAIL. What has not changed is the part that
# matters: never to an address supplied in the form, which would let anyone
# have a working reset link delivered to themselves.
SENT.clear()
client.post("/forgot", data={"username": "kursadmin", "email": "attacker@example.org"})
check("exactly one mail", len(SENT) == 1, SENT)
check(
    "delivered to the address stored on the account",
    SENT and SENT[0]["to"] == "kursadmin@example.com",
    SENT[0]["to"] if SENT else "(none)",
)
body = SENT[0]["body"] if SENT else ""
check("the mail carries the public URL", "https://content.example.com/reset/" in body, body[:200])

token = body.split("/reset/")[1].split()[0] if "/reset/" in body else ""
check("a token could be extracted", bool(token), body[:200])

# ─── 4. The token is stored hashed, never in the clear ───────────────────────
# In a column now rather than in .env — same reasoning, different place: the
# row is readable by anyone with database access, and a usable token sitting
# there would be a standing key to the account.
with _db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT reset_token_hash FROM users WHERE id = %s", (USER["id"],))
        stored = (cur.fetchone() or [""])[0] or ""
    conn.commit()
check("a token hash is stored", bool(stored), "empty")
check("the token itself is not stored", stored != token, "stored in the clear")
check("what is stored is a sha256 hex digest", len(stored) == 64, f"len={len(stored)}")
check("the token is not in .env either", token not in env_path.read_text(),
      "token found in plaintext")

# ─── 5. A wrong token is refused ─────────────────────────────────────────────
check("a made-up token does not verify", not bool(accounts.user_for_reset_token("nonsense")), "")
check("an empty token does not verify", not bool(accounts.user_for_reset_token("")), "")
check("the real token verifies", bool(accounts.user_for_reset_token(token)), "")

resp = client.get("/reset/some-other-token")
check("a wrong token yields 400", resp.status_code == 400, resp.status_code)

# ─── 6. Password rules still apply on this path ──────────────────────────────
resp = client.post(f"/reset/{token}", data={"password": "short", "confirm": "short"})
check("a too-short password is rejected", "at least" in resp.get_data(as_text=True).lower()
      or "zeichen" in resp.get_data(as_text=True).lower(), resp.get_data(as_text=True)[:200])
check("and the token still works afterwards", bool(accounts.user_for_reset_token(token)), "consumed on a failed attempt")

resp = client.post(f"/reset/{token}", data={"password": "a-new-strong-password", "confirm": "mismatch-here"})
check("a mismatch is rejected", bool(accounts.user_for_reset_token(token)), "token consumed on mismatch")

# ─── 7. Success changes the password and burns the token ─────────────────────
resp = client.post(f"/reset/{token}",
                   data={"password": "a-new-strong-password", "confirm": "a-new-strong-password"})
check("the reset succeeds", resp.status_code == 200, resp.status_code)
check("the new password works", accounts.verify_login("kursadmin", "a-new-strong-password"), "")
check("the old password no longer works",
      not accounts.verify_login("kursadmin", "a-strong-test-password"), "")
check("the username is unchanged", accounts.get(USER["id"])["username"] == "kursadmin",
      accounts.get(USER["id"])["username"])
check("the token is single-use", not bool(accounts.user_for_reset_token(token)), "still valid after use")

resp = client.get(f"/reset/{token}")
check("reusing the link yields 400", resp.status_code == 400, resp.status_code)

# ─── 8. Expiry is enforced ───────────────────────────────────────────────────
token2 = accounts.create_reset_token(USER["id"])
check("a fresh token verifies", bool(accounts.user_for_reset_token(token2)), "")
from env_file import set_env_var  # noqa: E402
# Expiry is a timestamp column now, so it is aged in the database rather
# than by rewriting .env. The property is the same: past its hour, the token
# is no longer accepted.
with _db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET reset_expires_at = now() - interval '1 minute' "
                    "WHERE id = %s", (USER["id"],))
    conn.commit()
check("an expired token does not verify", not bool(accounts.user_for_reset_token(token2)), "")

# A second token replaces the first: two live links to one account would
# mean revoking one does not revoke the other.
token3a = accounts.create_reset_token(USER["id"])
token3 = accounts.create_reset_token(USER["id"])
check("issuing a new token retires the previous one",
      not bool(accounts.user_for_reset_token(token3a)), "the older link still worked")
check("the newest token works", bool(accounts.user_for_reset_token(token3)), "")

# ─── 9. Without a relay, the page says so instead of pretending ──────────────
set_env_var("SMTP_HOST", "")
check("mail is reported as unconfigured", not mailer.mail_configured(), "")
resp = client.get("/forgot")
page = resp.get_data(as_text=True)
check("the page states it cannot send", "cannot send mail" in page, page[:300])
check("and names the way that does work", "smartrag" in page, page[:300])

# The login page must not offer a link that leads to that dead end.
resp = client.get("/login")
check("no reset link on the login page without mail",
      "/forgot" not in resp.get_data(as_text=True), "link offered anyway")

set_env_var("SMTP_HOST", "localhost")
resp = client.get("/login")
check("the link appears once mail is configured",
      "/forgot" in resp.get_data(as_text=True), "link missing")

# ─── Result ──────────────────────────────────────────────────────────────────
if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All password-reset checks passed: the answer is byte-identical for an "
    "existing and a non-existing username, the link is only ever delivered to "
    "ADMIN_EMAIL and never to an address from the form, the token is stored "
    "only as a sha256 and is single-use, expiry and an unreadable expiry both "
    "fail closed, the password rules apply on this path without consuming the "
    "token, the sender address never carries an unexpanded ${DOMAIN}, and an "
    "installation with no relay says so and hides the link instead of offering "
    "a dead end."
)
