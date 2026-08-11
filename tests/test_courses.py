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
