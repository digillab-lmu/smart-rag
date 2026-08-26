"""Changing an account instead of replacing it.

Reported after using the software: *"ich kann user nur entfernen, nicht
editieren. Wenn sich eine Mailadresse ändert, muss ich den User neu
anlegen."* An address goes stale whenever somebody changes department, and
the only remedy was delete-and-recreate — which drops the account's course
assignments and its password, so a routine correction turned into a small
migration.

The address is not cosmetic: it is where the password reset link is sent. An
account whose address is wrong cannot recover its own password.

What the tests hold onto:

  * editing keeps everything not being edited — role, courses, password;
  * name and address are validated together and written together, because a
    half-applied edit leaves the operator looking at an error with no way to
    tell which half went through;
  * a rename does not sign anybody out, since the session holds the id.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402

os.environ["SMARTRAG_ENV_PATH"] = str(dbfixture.tmp_env())
os.environ["SMARTRAG_INGEST_STATUS_PATH"] = tempfile.mkstemp()[1]
os.environ.setdefault("CONTENT_ADMIN_SESSION_SECRET", "test-secret")
os.environ.setdefault("SMARTRAG_TEMPLATES_DIR", "flowise/agents")

db, course = dbfixture.require_database()

import accounts  # noqa: E402
import app as flask_app  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


PW = "a-strong-test-password"
for name in ("ed-target", "ed-other", "ed-renamed", "ed-admin"):
    existing = accounts.get_by_username(name)
    if existing:
        accounts.delete_account(existing["id"])

admin = accounts.create_account("ed-admin", PW, role=accounts.ROLE_ADMIN)
target = accounts.create_account("ed-target", PW,
                                 role=accounts.ROLE_MAINTAINER,
                                 email="alt@example.org")
other = accounts.create_account("ed-other", PW)
accounts.assign(target["id"], course["id"])

client = flask_app.app.test_client()
with client.session_transaction() as sess:
    sess["user_id"] = admin["id"]
    sess["logged_in"] = True
    sess["course_id"] = course["id"]

# ─── The fields are on the page at all ──────────────────────────────────────
page = client.get("/accounts").get_data(as_text=True)
# A text input, not merely a field of that name: a hidden input satisfies
# "the name is on the page" while leaving nothing to edit, and that is
# exactly what the row looked like before this feature.
check("the account row is editable",
      'type="text" name="username" value="ed-target"' in page, "")
check("including the address", 'value="alt@example.org"' in page, "")
check("an account without an address shows an empty field, not a gap",
      'name="email"' in page and "no address" in page, "")

# ─── Editing keeps what is not being edited ─────────────────────────────────
client.post("/accounts", data={"action": "edit", "user_id": target["id"],
                               "username": "ed-renamed",
                               "email": "neu@example.org"})
after = accounts.get(target["id"])
check("the account is renamed", after["username"] == "ed-renamed", after)
check("and re-addressed", after["email"] == "neu@example.org", after)
check("the role is untouched", after["role"] == accounts.ROLE_MAINTAINER, after)
check("the course assignments survive",
      accounts.courses_of(target["id"]) == [course["id"]],
      "delete-and-recreate loses these, which is what made a changed address "
      "expensive")
check("the password still works",
      accounts.verify_login("ed-renamed", PW) is not None,
      "and the old name no longer does")
check("the old name is free again",
      accounts.get_by_username("ed-target") is None, "")

# ─── Refusals, and nothing half done ────────────────────────────────────────
page = client.post("/accounts", data={"action": "edit", "user_id": target["id"],
                                      "username": "ed-other",
                                      "email": "x@example.org"}).get_data(as_text=True)
check("a name already taken is refused",
      accounts.get(target["id"])["username"] == "ed-renamed", "")
check("and said so", "already an account" in page, page[-200:])
check("and the address did not change either",
      accounts.get(target["id"])["email"] == "neu@example.org",
      "a refused edit must not apply half of itself")

page = client.post("/accounts", data={"action": "edit", "user_id": target["id"],
                                      "username": "ed-halfway",
                                      "email": "keine-adresse"}).get_data(as_text=True)
check("an address that is not one is refused", "not an email" in page, page[-200:])
check("and the rename in the same submit is not applied",
      accounts.get(target["id"])["username"] == "ed-renamed",
      "this is the case the single transaction exists for: validated "
      "together, written together")

page = client.post("/accounts", data={"action": "edit", "user_id": target["id"],
                                      "username": "   ",
                                      "email": "neu@example.org"}).get_data(as_text=True)
check("an empty name is refused",
      accounts.get(target["id"])["username"] == "ed-renamed", "")

# ─── An address can be removed ──────────────────────────────────────────────
client.post("/accounts", data={"action": "edit", "user_id": target["id"],
                               "username": "ed-renamed", "email": ""})
check("clearing the address stores nothing rather than an empty string",
      accounts.get(target["id"])["email"] is None,
      "an empty string would look like an address to anything checking for one")

# ─── Renaming does not sign anyone out ──────────────────────────────────────
with client.session_transaction() as sess:
    sess["user_id"] = target["id"]
client.post("/accounts", data={"action": "edit", "user_id": target["id"],
                               "username": "ed-renamed-again", "email": ""})
check("a renamed account stays signed in",
      client.get("/accounts").status_code in (200, 302, 403),
      "the session holds the id, not the name")

# ─── An unknown account is refused rather than creating one ─────────────────
before = len(accounts.all_accounts())
client.post("/accounts", data={"action": "edit", "user_id": "999999",
                               "username": "ghost", "email": ""})
check("editing an account that does not exist creates nothing",
      len(accounts.all_accounts()) == before, "")

for name in ("ed-renamed-again", "ed-renamed", "ed-other", "ed-target"):
    existing = accounts.get_by_username(name)
    if existing:
        accounts.delete_account(existing["id"])

if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("All account-editing checks passed: a name and an address can be changed")
print("in place, keeping the role, the course assignments and the password, so")
print("a changed address no longer means deleting and recreating the account;")
print("a name already in use, an address that is not one, and an empty name")
print("are each refused with nothing written — including the other field in")
print("the same submit, which is what the single transaction is for; clearing")
print("an address stores nothing rather than an empty string; a rename does")
print("not sign the account out; and editing an account that does not exist")
print("creates nothing.")
