"""
Creating a course: four side effects across three systems, and what happens
when the third one fails.

The interesting cases are all failures. A course that creates cleanly is one
path; a course whose bucket could not be created, or whose key was never
granted, is where the design either holds or leaves something behind that
nobody has a record of.

Weaviate and Garage are stubbed here — deliberately, because what is under
test is the ordering and the recovery, and a stub can fail on demand where a
real service cannot. The stubs are strict about the calls they receive, so a
change that stops granting the key, or grants it before the bucket exists,
shows up as a wrong call rather than as a silent pass. The clients themselves
are exercised against the real services on the server.

The database half needs a real Postgres for the same reason test_db.py does:
the states this file is about — recorded but unfinished, finished, absent —
are rows, and their constraints are the thing being relied on. Without
SMARTRAG_TEST_DSN it exits 10 and the runner reports "could not run".
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "content-admin"
if not APP_DIR.is_dir() and Path("/app/db.py").exists():
    APP_DIR = Path("/app")
sys.path.insert(0, str(APP_DIR))

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
env_path.write_text(
    'POSTGRES_USER="u"\nPOSTGRES_PASSWORD="p"\n'
    'GARAGE_ADMIN_TOKEN="admin-token-not-real"\n'
    'GARAGE_ACCESS_KEY="GKtestingestkey"\n'
    'WEAVIATE_API_KEY="weaviate-key"\nWEAVIATE_HTTP_PORT="8080"\n'
    # The import path resolves a provider from .env before it substitutes
    # anything, so the agent half of this suite needs one.
    'LLM_PROVIDER="openai"\nLLM_MODEL_STRONG="gpt-4o"\nLLM_MODEL_FAST="gpt-4o-mini"\n'
    'LLM_API_KEY="sk-test"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_MODEL="text-embedding-3-small"\n'
    'EMBEDDING_API_KEY="sk-embed-test"\n'
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)

# The template a course collection is created from. The real one when it is
# visible, a minimal stand-in when running from inside the container.
schema_src = REPO / "weaviate" / "schema.json"
schema_path = Path(tmpdir) / "schema.json"
if schema_src.exists():
    schema_path.write_text(schema_src.read_text())
else:
    schema_path.write_text(
        '{"classes":[{"class":"__COLLECTION_NAME__","vectorizer":"none",'
        '"properties":[{"name":"course_id","dataType":["text"]}]}]}')
os.environ["SMARTRAG_SCHEMA_PATH"] = str(schema_path)

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def done_early():
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)


try:
    import courses
    import db
except ModuleNotFoundError as exc:
    missing = getattr(exc, "name", "") or str(exc)
    print(f"{missing} is not importable from {APP_DIR}. On the host run "
          "tests/run-tests.sh; inside the container rebuild the image.")
    sys.exit(10)

import atexit
atexit.register(lambda: db.close_pool())


# ─── Stubs that are strict about being called correctly ──────────────────────
class FakeWeaviate:
    def __init__(self, fail_on_create=False):
        self.collections: set[str] = set()
        self.fail_on_create = fail_on_create
        self.calls: list[str] = []

    def create_collection(self, name, template_path):
        self.calls.append(f"create_collection:{name}")
        if self.fail_on_create:
            from weaviate_client import WeaviateError
            raise WeaviateError("weaviate is down")
        # The template must be readable and must be the real shape — passing
        # a path nobody reads would make this stub prove nothing about it.
        import json
        data = json.loads(Path(template_path).read_text())
        assert any(c.get("class") == "__COLLECTION_NAME__"
                   for c in data.get("classes", [])), "template not used"
        if name in self.collections:
            return False
        self.collections.add(name)
        return True

    def collection_exists(self, name):
        return name in self.collections

    def delete_collection(self, name):
        self.calls.append(f"delete_collection:{name}")
        self.collections.discard(name)


class FakeGarage:
    def __init__(self, fail_on_create=False, fail_on_allow=False,
                 grant_silently_lost=False):
        self.buckets: dict[str, dict] = {}
        self.grants: dict[str, set[str]] = {}
        self.fail_on_create = fail_on_create
        self.fail_on_allow = fail_on_allow
        self.grant_silently_lost = grant_silently_lost
        self.calls: list[str] = []

    def bucket_info(self, name):
        if name not in self.buckets:
            return None
        return {"id": self.buckets[name]["id"],
                "keys": [{"accessKeyId": k,
                          "permissions": {"read": True, "write": True, "owner": True}}
                         for k in self.grants.get(name, set())]}

    def create_bucket(self, name):
        self.calls.append(f"create_bucket:{name}")
        if self.fail_on_create:
            from garage_client import GarageError
            raise GarageError("garage refused")
        self.buckets.setdefault(name, {"id": f"id-{name}"})
        return {"id": self.buckets[name]["id"]}

    def allow_key(self, bucket_id, access_key_id, **kwargs):
        self.calls.append(f"allow_key:{bucket_id}:{access_key_id}")
        if self.fail_on_allow:
            from garage_client import GarageError
            raise GarageError("grant refused")
        name = next((n for n, b in self.buckets.items() if b["id"] == bucket_id), None)
        assert name, "granted a key on a bucket that was never created"
        if not self.grant_silently_lost:
            self.grants.setdefault(name, set()).add(access_key_id)

    def key_can_write(self, name, access_key_id):
        return access_key_id in self.grants.get(name, set())


# ─── 1. Naming, without a database ───────────────────────────────────────────
check("a course id becomes a valid Weaviate class",
      courses.collection_for("didaktik-2026") == "Chunks_didaktik_2026",
      courses.collection_for("didaktik-2026"))
check("a course id becomes the bucket name the ingest already used",
      courses.bucket_for("didaktik-2026") == "didaktik-2026-rag",
      courses.bucket_for("didaktik-2026"))

# ─── 2. With a database ──────────────────────────────────────────────────────
TEST_DSN = os.getenv("SMARTRAG_TEST_DSN", "").strip()
if not TEST_DSN:
    done_early()
    print("No SMARTRAG_TEST_DSN set. Course creation, its failure paths and "
          "the recorded-but-unfinished state are unverified.\n"
          "  docker cp tests/test_courses.py smartrag-content-admin:/tmp/ && "
          "docker exec -e SMARTRAG_TEST_DSN=... smartrag-content-admin "
          "python3 /tmp/test_courses.py")
    sys.exit(10)

os.environ["SMARTRAG_DB_DSN"] = TEST_DSN
db.close_pool()
try:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
except db.DatabaseError as exc:
    print(f"SMARTRAG_TEST_DSN is set but unusable: {exc}")
    sys.exit(10)


def reset():
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS agent_slots, user_courses, users, "
                        "courses, schema_version CASCADE")
        conn.commit()
    db.migrate()


reset()

# ─── The clean path ──────────────────────────────────────────────────────────
w, g = FakeWeaviate(), FakeGarage()
course = courses.create_course("didaktik", "Didaktik der Chemie", weaviate=w, garage=g)
check("a created course is ready", course["ready"], course)
check("its collection exists", "Chunks_didaktik" in w.collections, w.collections)
check("its bucket exists", "didaktik-rag" in g.buckets, list(g.buckets))
check("the ingest key may write to it",
      g.key_can_write("didaktik-rag", "GKtestingestkey"), g.grants)

with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_slots WHERE course_id='didaktik'")
        slots = cur.fetchone()[0]
    conn.commit()
check("ten empty slots come with it", slots == 10, slots)

# The bucket must be created before the key is granted on it — the stub
# asserts that, so this checks the order actually happened.
check("the bucket was created before the grant",
      g.calls.index("create_bucket:didaktik-rag")
      < next(i for i, c in enumerate(g.calls) if c.startswith("allow_key")), g.calls)

def raises(fn, fragment=""):
    try:
        fn()
    except courses.CourseError as exc:
        return fragment in str(exc)
    except Exception:  # noqa: BLE001
        return False
    return False


check("a second course with the same id is refused",
      raises(lambda: courses.create_course("didaktik", "Nochmal", weaviate=w, garage=g),
             "already exists"))
check("a malformed id is refused before anything is created",
      raises(lambda: courses.create_course("Didaktik 2026!", "x", weaviate=w, garage=g),
             "not a usable course id"))
check("…and left nothing behind", "Chunks_Didaktik 2026!" not in w.collections)
check("a course with no name is refused",
      raises(lambda: courses.create_course("kurs-b", "  ", weaviate=w, garage=g),
             "needs a name"))

# ─── Partial failure: the whole point ────────────────────────────────────────
# Bucket fails after the collection was created. The course must exist as
# recorded-but-unfinished, not as absent and not as ready.
w2, g2 = FakeWeaviate(), FakeGarage(fail_on_create=True)
check("a failing bucket fails the creation",
      raises(lambda: courses.create_course("kurs-c", "Kurs C", weaviate=w2, garage=g2),
             "recorded but not finished"))
c = courses.get_course("kurs-c")
check("…the course is recorded", c is not None, c)
check("…and is not ready", c and not c["ready"], c)
check("…and shows up as unfinished",
      "kurs-c" in [x["id"] for x in courses.unfinished()], courses.unfinished())
check("…while the collection that did succeed is kept",
      "Chunks_kurs_c" in w2.collections, w2.collections)

# Resuming: same course, a Garage that now works. The collection already
# exists, so this proves the second run tolerates its own leftovers.
g2b = FakeGarage()
resumed = courses.provision("kurs-c", weaviate=w2, garage=g2b)
check("provisioning again finishes it", resumed["ready"], resumed)
check("…without creating the collection twice",
      w2.calls.count("create_collection:Chunks_kurs_c") == 2, w2.calls)
check("…and the bucket is there now", "kurs-c-rag" in g2b.buckets, list(g2b.buckets))

# The grant failing on its own.
w3, g3 = FakeWeaviate(), FakeGarage(fail_on_allow=True)
check("a failing grant fails the creation",
      raises(lambda: courses.create_course("kurs-d", "Kurs D", weaviate=w3, garage=g3),
             "recorded but not finished"))
check("…and the course stays unfinished",
      "kurs-d" in [x["id"] for x in courses.unfinished()])

# The grant that reports success and did nothing. This is the failure with no
# symptom until an upload stores nothing, which is why provisioning verifies
# rather than trusts.
w4, g4 = FakeWeaviate(), FakeGarage(grant_silently_lost=True)
check("a grant that silently did nothing is caught",
      raises(lambda: courses.create_course("kurs-e", "Kurs E", weaviate=w4, garage=g4),
             "still not allowed to write"))
check("…and that course is not ready",
      not courses.get_course("kurs-e")["ready"])

# ─── Two courses do not share anything ───────────────────────────────────────
w5, g5 = FakeWeaviate(), FakeGarage()
a = courses.create_course("kurs-x", "Kurs X", weaviate=w5, garage=g5)
b = courses.create_course("kurs-y", "Kurs Y", weaviate=w5, garage=g5)
check("two courses get different collections", a["collection"] != b["collection"])
check("two courses get different buckets", a["bucket"] != b["bucket"])
check("both are ready", a["ready"] and b["ready"])

# ─── The page ────────────────────────────────────────────────────────────────
# The route is what an operator touches, and the state it must not hide is
# "unfinished". Everything below runs against the same real database.
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"
os.environ.setdefault("SMARTRAG_SLOTS_PATH", str(Path(tmpdir) / "slots.json"))
os.environ.setdefault("SMARTRAG_TEMPLATES_DIR",
                      str(Path(APP_DIR).parent / "flowise" / "agents"))
import app as flask_app  # noqa: E402
import auth  # noqa: E402

auth.create_admin_account("kursadmin", "a-strong-test-password")
client = flask_app.app.test_client()
client.post("/login", data={"username": "kursadmin",
                            "password": "a-strong-test-password"})

reset()
w6, g6 = FakeWeaviate(), FakeGarage()
courses.create_course("kurs-fertig", "Fertiger Kurs", weaviate=w6, garage=g6)
w7, g7 = FakeWeaviate(), FakeGarage(fail_on_create=True)
try:
    courses.create_course("kurs-halb", "Halber Kurs", weaviate=w7, garage=g7)
except courses.CourseError:
    pass

page = client.get("/courses")
body = page.get_data(as_text=True)
check("the courses page renders", page.status_code == 200, page.status_code)
check("a ready course is listed", "Fertiger Kurs" in body)
check("an unfinished course is listed", "Halber Kurs" in body)
# The distinction is the whole point: a page that lists both the same way
# tells an operator their half-made course is fine.
# Compared inside the page's own content: the course switcher in the layout
# names every ready course before the body starts, so comparing positions in
# the whole document measures the navigation, not the list.
main = body[body.index("</nav>"):] if "</nav>" in body else body
check("…and is marked as unfinished",
      main.index("Halber Kurs") < main.index("Fertiger Kurs"),
      "unfinished courses must come first, where they are seen")
check("the collection and bucket are shown",
      "Chunks_kurs_fertig" in body and "kurs-fertig-rag" in body)

# Creating through the form reaches the service.
page = client.post("/courses", data={"name": "Neu", "id": "kurs-neu"},
                   follow_redirects=True)
check("a course created from the form is refused without a working store",
      page.status_code == 200, page.status_code)

# A malformed id must come back as a message, not a 500.
page = client.post("/courses", data={"name": "Kaputt", "id": "Nicht Erlaubt!"})
check("a malformed id is reported, not raised", page.status_code == 200,
      page.status_code)
check("…and says what is wrong",
      "usable course id" in page.get_data(as_text=True)
      or "Kurskennung" in page.get_data(as_text=True),
      page.get_data(as_text=True)[-300:])

# ─── With several courses, the operator must be able to choose ───────────────
# Reported from the live system as "the links are corrupt": every page
# redirected to the course list, because with two courses none is picked
# automatically — and the switcher existed in the view layer but was never
# rendered. A redirect nobody can satisfy is a dead end.
reset()
wf2, gf2 = FakeWeaviate(), FakeGarage()
courses.create_course("kurs-eins", "Kurs Eins", weaviate=wf2, garage=gf2)
courses.create_course("kurs-zwei", "Kurs Zwei", weaviate=wf2, garage=gf2)

fresh = flask_app.app.test_client()
fresh.post("/login", data={"username": "kursadmin",
                           "password": "a-strong-test-password"})
page = fresh.get("/", follow_redirects=True)
body = page.get_data(as_text=True)
check("with two courses a page asks which one", "/courses" in page.request.path
      or "Kurs Eins" in body, page.request.path)
check("…and says why rather than just bouncing",
      "arbeitest" in body or "work in" in body, body[-400:])
check("…and offers both courses to choose from",
      "Kurs Eins" in body and "Kurs Zwei" in body, "")
check("…with a link that actually selects one",
      "/courses/kurs-eins/use" in body, "")

fresh.get("/courses/kurs-eins/use")
page = fresh.get("/")
check("after choosing, the page opens", page.status_code == 200, page.status_code)
check("…and the layout names the active course",
      "Kurs Eins" in page.get_data(as_text=True), "")

# A single course needs no choosing — asking someone to pick from a list of
# one teaches clicking without reading.
reset()
wf3, gf3 = FakeWeaviate(), FakeGarage()
courses.create_course("nur-einer", "Nur Einer", weaviate=wf3, garage=gf3)
solo = flask_app.app.test_client()
solo.post("/login", data={"username": "kursadmin",
                          "password": "a-strong-test-password"})
check("a single course is used without asking",
      solo.get("/").status_code == 200, "")

check("the page requires a login",
      flask_app.app.test_client().get("/courses").status_code in (302, 401),
      "anyone could list every course")

# ─── Two courses, one agent name ─────────────────────────────────────────────
# The failure this whole phase is about. Flowise's chatflow names are global
# and upsert_chatflow finds an existing flow by name, so two courses with an
# agent called "Tutor" would be one chatflow — each import overwriting the
# other, and reporting success both times.
import storage  # noqa: E402
import agent_templates  # noqa: E402

reset()
wf, gf = FakeWeaviate(), FakeGarage()
a = courses.create_course("kurs-a", "Kurs A", weaviate=wf, garage=gf)
b = courses.create_course("kurs-b", "Kurs B", weaviate=wf, garage=gf)

ARCH = "agent-11-expert-feedback.json"
CONTENT = {"EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
           "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r",
           "STUDENT_ROLE": "s"}
for cid in ("kurs-a", "kurs-b"):
    storage.save_slot(cid, 1, ARCH, CONTENT, "Tutor", None)


class RecordingFlowise:
    def __init__(self):
        self.flows: dict[str, str] = {}

    def upsert_credential(self, *a, **kw):
        return "cred-id"

    def get_or_create_variable(self, *a, **kw):
        return "var-id"

    def upsert_chatflow(self, name, flow_data, analytic=None):
        self.flows[name] = flow_data
        return f"cf-{len(self.flows)}", True


fl = RecordingFlowise()
# A request context because the import path formats its messages with t(),
# which reads the language from the request.
with flask_app.app.test_request_context("/"):
    for course in (a, b):
        err = flask_app._do_import(course, 1, ARCH, fl)
        check(f"importing into {course['id']} succeeds", err is None, str(err))

check("two courses produce two chatflows", len(fl.flows) == 2, list(fl.flows))
check("…and each name carries its course",
      all(cid in n for cid, n in zip(("kurs-a", "kurs-b"), sorted(fl.flows))),
      sorted(fl.flows))

# The point of separate collections: each agent must search its own.
for course in (a, b):
    name = next(n for n in fl.flows if course["id"] in n)
    body = fl.flows[name]
    check(f"{course['id']}'s agent searches its own collection",
          course["collection"] in body, course["collection"])
    other = b if course is a else a
    check(f"…and not {other['id']}'s",
          other["collection"] not in body, other["collection"])
    # The retrieval filter is JSON inside a JSON string, so the quotes around
    # the value arrive escaped. Normalising is the difference between
    # checking the value and checking the escaping.
    flat = body.replace("\\", "")
    check(f"{course['id']}'s agent filters on its own course id",
          f'"property": "course_id"' in flat and f'"value": "{course["id"]}"' in flat,
          [x for x in flat.split(",") if "course_id" in x][:1])
    check(f"…and not on {other['id']}'s",
          f'"value": "{other["id"]}"' not in flat, other["id"])

# And the slots did not bleed into each other.
check("each course keeps its own chatflow id",
      storage.get_slot("kurs-a", 1)["chatflow_id"]
      != storage.get_slot("kurs-b", 1)["chatflow_id"],
      storage.get_slot("kurs-a", 1)["chatflow_id"])
check("the chatflow can be traced back to its course",
      storage.course_of_chatflow(storage.get_slot("kurs-b", 1)["chatflow_id"]) == "kurs-b")

reset()
db.close_pool()

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All course checks passed: creating one records the intent first and sets "
    "provisioned_at only after the collection, the bucket, the grant and ten "
    "empty slots are all in place; the bucket is created before the key is "
    "granted on it; a malformed id or an empty name is refused before "
    "anything is created; a failure at any step leaves the course recorded "
    "and listed as unfinished rather than absent or ready; provisioning again "
    "tolerates the parts that already succeeded and finishes it; a grant that "
    "reports success without taking effect is caught by verifying it; and two "
    "courses share neither collection nor bucket."
)
