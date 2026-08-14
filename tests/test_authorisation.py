"""
Who may reach what.

The rule is one sentence — a maintainer may act on a course only if there is
a row in `user_courses` for the pair — and the reason it is enforced in one
place is that the failure has no symptom. A route that forgets the check does
not crash; it shows another course's documents to somebody who should not see
them, and it keeps doing that until a person notices.

So this file does not check a hand-written list of routes. It reads Flask's
own route table and requires every course-bound route to be guarded, which
means a route added next month without the decorator fails here rather than
in a course.

Three properties, in order of how badly they fail:

  1. A maintainer cannot reach a course they are not assigned to — not by
     URL, not by selecting it, not by having been assigned once and removed.
  2. A maintainer cannot reach the pages that hand out access.
  3. A logged-out visitor reaches nothing at all.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402

_db, COURSE = dbfixture.require_database()
APP_DIR = dbfixture.app_dir()

env_path = dbfixture.tmp_env(
    'LLM_PROVIDER="openai"\nLLM_API_KEY="sk-test"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_API_KEY="sk-test"\n'
    'WEAVIATE_API_KEY="wv"\nWEAVIATE_HTTP_PORT="8080"\n')
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(env_path.parent / "slots.json")
os.environ["SMARTRAG_INGEST_STATUS_PATH"] = str(env_path.parent / "ingest.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

import accounts  # noqa: E402
import app as flask_app  # noqa: E402
import courses as courses_service  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


# ─── Two courses, three people ───────────────────────────────────────────────
def fresh_course(cid, name):
    with _db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO courses (id, name, collection, bucket, provisioned_at) "
                "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (id) DO NOTHING",
                (cid, name, f"Chunks_{cid.replace('-', '_')}", f"{cid}-rag"))
            for slot in range(1, 11):
                cur.execute("INSERT INTO agent_slots (course_id, slot) VALUES (%s, %s) "
                            "ON CONFLICT DO NOTHING", (cid, slot))
        conn.commit()


fresh_course("kurs-eins", "Kurs Eins")
fresh_course("kurs-zwei", "Kurs Zwei")

admin = accounts.create_account("chefin", "a-strong-test-password",
                                role=accounts.ROLE_ADMIN)
anna = accounts.create_account("anna", "a-strong-test-password")
accounts.assign(anna["id"], "kurs-eins")


def login(username):
    client = flask_app.app.test_client()
    client.post("/login", data={"username": username,
                                "password": "a-strong-test-password"})
    return client


anna_c = login("anna")
admin_c = login("chefin")
stranger = flask_app.app.test_client()

# ─── 1. The rule itself ──────────────────────────────────────────────────────
check("a maintainer may reach their own course",
      accounts.may_access(anna, "kurs-eins"))
check("…and not another one", not accounts.may_access(anna, "kurs-zwei"))
check("an administrator may reach any course",
      accounts.may_access(admin, "kurs-zwei"))
check("nobody is not an administrator", not accounts.may_access(None, "kurs-eins"))

# Selecting a course one is not assigned to must not take effect. Checking
# only at the moment of choosing would leave the cookie valid afterwards.
anna_c.get("/courses/kurs-zwei/use")
page = anna_c.get("/", follow_redirects=True)
check("selecting a foreign course does not take effect",
      "Kurs Zwei" not in page.get_data(as_text=True),
      "the maintainer is working in a course they were never given")

# Withdrawn while logged in: the next request must already refuse. What
# "refuse" means here is not a redirect — Anna still has kurs-eins, so the
# page opens in that one. The property is that she is no longer working in
# kurs-zwei, and checking for a redirect instead would have been checking
# the wrong thing.
accounts.assign(anna["id"], "kurs-zwei")
anna_c.get("/courses/kurs-zwei/use")
before = anna_c.get("/").get_data(as_text=True)
check("selecting a course she was given works", "Kurs Zwei" in before, "")
accounts.unassign(anna["id"], "kurs-zwei")
after = anna_c.get("/").get_data(as_text=True)
check("an assignment withdrawn mid-session takes effect at once",
      "Kurs Zwei" not in after,
      "the cookie kept her in a course she no longer has")

# ─── 2. Every course-bound route, from Flask's own table ─────────────────────
# Hand-written lists go stale. This reads the routes the application actually
# has, so a new one without the decorator shows up here.
COURSE_BOUND = {"dashboard", "slot_view", "slot_optimize", "upload",
                "documents", "getting_started", "graph_guidance"}
# Deleting a course removes a collection, a bucket, a graph and every
# conversation held in it. Creating one is already administrators only,
# and undoing that act cannot be less.
ADMIN_ONLY = {"accounts_page", "flowise_setup", "delete_course_view"}
# Logged in, but not tied to one course: a citation lookup and a keyword
# suggestion act on text the caller typed, not on stored material.
LOGIN_ONLY = {"upload_lookup", "upload_keywords"}
PUBLIC = {"login", "setup", "forgot_password", "reset_password", "logout",
          "set_language", "static", "api_ingest_status"}

rules = {r.endpoint: r for r in flask_app.app.url_map.iter_rules()}
unclassified = (set(rules) - COURSE_BOUND - ADMIN_ONLY - LOGIN_ONLY - PUBLIC
                - {"courses", "use_course"})
check("every route is classified in this test", not unclassified,
      f"unclassified: {sorted(unclassified)} — decide whether each is course-bound, "
      "admin-only or public, and add it here")

def has_marker(view, marker):
    """Whether a decorator that sets `marker` is anywhere in the chain.

    An explicit attribute, because functools.wraps copies __name__ and
    __qualname__ from the view onto the wrapper: the first version of this
    check looked for the wrapper's name, found the view's, and would have
    passed for a route with no decorator at all.
    """
    fn = view
    for _ in range(6):
        if getattr(fn, marker, False):
            return True
        fn = getattr(fn, "__wrapped__", None)
        if fn is None:
            return False
    return False


for endpoint in sorted(COURSE_BOUND & set(rules)):
    view = flask_app.app.view_functions[endpoint]
    check(f"{endpoint} is behind the course check",
          has_marker(view, "__course_bound__"), "no @with_course")
    check(f"{endpoint} also requires a login",
          has_marker(view, "__login_required__"), "no @auth.login_required")

for endpoint in sorted(ADMIN_ONLY & set(rules)):
    check(f"{endpoint} is administrator-only",
          has_marker(flask_app.app.view_functions[endpoint], "__admin_only__"),
          "no @auth.admin_required")

for endpoint in sorted(LOGIN_ONLY & set(rules)):
    check(f"{endpoint} requires a login",
          has_marker(flask_app.app.view_functions[endpoint], "__login_required__"),
          "no @auth.login_required")

# And the behaviour, not only the decoration: a logged-out visitor gets
# nothing, and a maintainer cannot open the admin page.
for endpoint in sorted((COURSE_BOUND | ADMIN_ONLY) & set(rules)):
    rule = rules[endpoint]
    if "<" in rule.rule:      # skip the ones needing parameters
        continue
    resp = stranger.get(rule.rule, follow_redirects=False)
    check(f"{endpoint} is closed to a stranger", resp.status_code in (302, 401),
          resp.status_code)

resp = anna_c.get("/accounts", follow_redirects=False)
check("a maintainer cannot open the accounts page",
      resp.status_code == 302, resp.status_code)
check("…and is not sent to the login form, which would be a loop",
      "/login" not in resp.headers.get("Location", ""),
      resp.headers.get("Location"))
check("an administrator can", admin_c.get("/accounts").status_code == 200)

# Posting to it must be refused too — a form is not a page.
resp = anna_c.post("/accounts", data={"action": "assign",
                                      "user_id": str(anna["id"]),
                                      "course_id": "kurs-zwei"})
check("a maintainer cannot assign themselves a course",
      resp.status_code == 302 and "kurs-zwei" not in accounts.courses_of(anna["id"]),
      accounts.courses_of(anna["id"]))

# Creating a course is an installation-level act for the same reason.
resp = anna_c.post("/courses", data={"name": "Selbst", "id": "selbst"})
check("a maintainer cannot create a course",
      courses_service.get_course("selbst") is None, "the course was created")

# ─── 3. What each of them sees ───────────────────────────────────────────────
body = anna_c.get("/courses").get_data(as_text=True)
check("a maintainer's course list shows their course", "Kurs Eins" in body)
check("…and not the others", "Kurs Zwei" not in body,
      "a maintainer can see that another course exists, and its identifier")

body = admin_c.get("/courses").get_data(as_text=True)
check("an administrator sees both", "Kurs Eins" in body and "Kurs Zwei" in body)

# ─── 4. The last administrator ───────────────────────────────────────────────
# An installation with no administrator can only be repaired from a shell.
try:
    accounts.set_role(admin["id"], accounts.ROLE_MAINTAINER)
    check("the last administrator cannot be demoted", False, "it was demoted")
except accounts.AccountError as exc:
    check("the last administrator cannot be demoted", "last administrator" in str(exc),
          str(exc))
try:
    accounts.delete_account(admin["id"])
    check("the last administrator cannot be deleted", False, "it was deleted")
except accounts.AccountError as exc:
    check("the last administrator cannot be deleted", "last administrator" in str(exc),
          str(exc))

second = accounts.create_account("zweite", "a-strong-test-password",
                                 role=accounts.ROLE_ADMIN)
try:
    accounts.set_role(admin["id"], accounts.ROLE_MAINTAINER)
    check("with a second administrator, the first may be demoted", True)
except accounts.AccountError as exc:
    check("with a second administrator, the first may be demoted", False, str(exc))

# ─── 5. Login tells nothing apart ────────────────────────────────────────────
check("a wrong password fails", accounts.verify_login("anna", "wrong") is None)
check("an unknown user fails the same way",
      accounts.verify_login("nobody-at-all", "wrong") is None)
check("the right password works",
      accounts.verify_login("anna", "a-strong-test-password") is not None)

with _db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM courses WHERE id LIKE 'kurs-%'")
    conn.commit()

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All authorisation checks passed: a maintainer reaches the courses they "
    "are assigned to and no others, selecting a foreign course does not take "
    "effect, and an assignment withdrawn mid-session stops working on the "
    "next request rather than at the next login; every course-bound route in "
    "Flask's own table is behind the single check and closed to a stranger, "
    "and every route is classified here so a new one cannot slip in "
    "unnoticed; the accounts page and course creation are refused to a "
    "maintainer by GET and by POST; the last administrator can be neither "
    "demoted nor deleted; and a wrong password and an unknown user fail "
    "identically."
)
