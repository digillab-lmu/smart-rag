"""
Deleting a course, across six systems that do not know about each other.

Two properties decide whether this is safe, and neither is visible by reading
the code once.

**The order.** A Langfuse trace carries a learner id and Flowise's chat id,
never a course. The only route from a course to its traces runs through
Flowise's chat records — and Flowise deletes those together with the chatflow.
So the session ids must be collected before anything is removed. Get that
wrong and the deletion still reports success, while a course's traces stay
behind with nothing left that could ever find them again.

**The record goes last.** While the course row exists the course is listed,
its slots are there, and the whole operation can be run again. Delete it after
a step that failed and the leftovers are orphans: data in five systems that
nothing points at, findable only by someone who knows all of them.

The stubs below record what they were asked and in which order, because that
order is the thing under test.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402

os.environ["SMARTRAG_ENV_PATH"] = str(dbfixture.tmp_env(
    'COMPOSE_PROFILES="core,observability"\nWEAVIATE_API_KEY="wv"\n'
    'LANGFUSE_INIT_PROJECT_PUBLIC_KEY="pk"\nLANGFUSE_INIT_PROJECT_SECRET_KEY="sk"\n'
    'GARAGE_ACCESS_KEY="GK1"\nGARAGE_SECRET_KEY="s1"\nNEO4J_PASSWORD="pw"\n'))

db, course = dbfixture.require_database()

import courses as courses_service  # noqa: E402
import storage  # noqa: E402
from weaviate_client import WeaviateError  # noqa: E402

CID = course["id"]
failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def step(result, fragment):
    for s in result["steps"]:
        if fragment in s["action"]:
            return s
    return None


class Log(list):
    """Shared call log, so the order across systems is one sequence."""


class Flowise:
    def __init__(self, log, present=("cf-1",), fail=False):
        self.log, self.present, self.fail = log, set(present), fail

    def chat_records(self, chatflow_id):
        # Two ids per conversation, as Flowise really stores them. Which one a
        # Langfuse trace is keyed by depends on how the chat was opened, so a
        # deletion that collects only one of them misses every trace on the
        # other kind of installation.
        self.log.append(("flowise.sessions", chatflow_id))
        return [{"session_id": "lti-9|Ada|1|t|Ada L.",
                 "chat_id": f"chat-{chatflow_id}"}]

    def delete_chatflow(self, chatflow_id):
        self.log.append(("flowise.delete", chatflow_id))
        if self.fail:
            raise RuntimeError("Flowise said no")
        return chatflow_id in self.present


class Langfuse:
    def __init__(self, log):
        self.log, self.deleted = log, []

    def trace_ids_for_session(self, session_id):
        self.log.append(("langfuse.list", session_id))
        return [f"trace-{session_id}-a", f"trace-{session_id}-b"]

    def delete_traces(self, ids):
        self.log.append(("langfuse.delete", tuple(ids)))
        self.deleted.extend(ids)
        return len(ids)


class Weaviate:
    SHARED_LEARNER_CLASSES = ("ChatHistory", "UserMemory", "TestResults")

    def __init__(self, log, fail_class=""):
        self.log, self.fail_class = log, fail_class

    def collection_exists(self, name):
        return True

    def delete_collection(self, name):
        self.log.append(("weaviate.collection", name))

    def delete_by_course(self, cls, course_id):
        self.log.append(("weaviate.class", cls))
        if cls == self.fail_class:
            raise WeaviateError("Weaviate is not reachable")
        return 7


class Garage:
    def __init__(self, log, exists=True):
        self.log, self.exists = log, exists

    def bucket_info(self, name):
        return {"id": "b1"} if self.exists else None

    def delete_bucket(self, name):
        self.log.append(("garage.delete_bucket", name))
        return True


class S3:
    def __init__(self, log):
        self.log = log

    def empty_bucket(self, name):
        self.log.append(("s3.empty", name))
        return 4


class Neo4j:
    def __init__(self, log):
        self.log = log

    def clear_course(self, course_id):
        self.log.append(("neo4j.clear", course_id))
        return 51


def fresh_course():
    """A course with two imported agents, recreated for each scenario."""
    dbfixture._ensure_course(db)
    dbfixture.clear_slots(db)
    storage.save_slot(CID, 1, "agent-topic-template.json", {}, "Tutor")
    storage.set_chatflow_id(CID, 1, "cf-1", "digest")
    storage.save_slot(CID, 2, "agent-topic-template.json", {}, "Zweiter")
    storage.set_chatflow_id(CID, 2, "cf-2", "digest")


def run(**overrides):
    log = Log()
    kwargs = dict(weaviate=Weaviate(log), garage=Garage(log), neo4j=Neo4j(log),
                  flowise=Flowise(log), langfuse=Langfuse(log), s3=S3(log))
    kwargs.update({k: v(log) if callable(v) else v for k, v in overrides.items()})
    return courses_service.delete_course(CID, **kwargs), log


# ─── 1. The order, which is the whole point ──────────────────────────────────
fresh_course()
result, log = run()
names = [c[0] for c in log]

check("the deletion completes", result["deleted"], result["steps"])
check("chat sessions are read before any chatflow is deleted",
      names.index("flowise.sessions") < names.index("flowise.delete"), names)
check("traces are asked for before the chatflows go",
      names.index("langfuse.delete") < names.index("flowise.delete"), names)
check("every chatflow's sessions are read",
      names.count("flowise.sessions") == 2, names)

# Both ids reach Langfuse, not just one. Through the LTI middleware a trace's
# sessionId is the middleware's own string, because Flowise spreads
# overrideConfig.analytics.langFuse over its defaults; without it the trace's
# sessionId is Flowise's chatId. Collecting only chatIds — which this did
# until 2026-08-19 — matched no trace at all on an LTI installation and
# reported that as "0 traces", successfully.
asked_for = {e[1] for e in log if e[0] == "langfuse.list"}
check("the LTI session id is looked up in Langfuse",
      "lti-9|Ada|1|t|Ada L." in asked_for, asked_for)
check("Flowise's own chat id is looked up too",
      "chat-cf-1" in asked_for and "chat-cf-2" in asked_for, asked_for)
# The bucket cannot be deleted while it holds objects, and Garage's admin API
# cannot empty one — so the order here is forced too.
check("the bucket is emptied before it is deleted",
      names.index("s3.empty") < names.index("garage.delete_bucket"), names)

# ─── 2. What was actually asked of each system ───────────────────────────────
check("the collection goes as a whole",
      ("weaviate.collection", course["collection"]) in log, list(log))
for cls in ("ChatHistory", "UserMemory", "TestResults"):
    check(f"{cls} is filtered by course", ("weaviate.class", cls) in log, list(log))
check("the graph is cleared", ("neo4j.clear", CID) in log, list(log))
# In slot order, not in whatever order the rows happen to sit in. This
# assertion was already here and passed for months; it began failing on the
# second run of the suite once another test updated a slot, because an UPDATE
# moves the row and the SELECT had no ORDER BY. The order reaches the operator
# in what the deletion reports, so it is worth pinning rather than relaxing.
check("both chatflows are deleted",
      [c for c in log if c[0] == "flowise.delete"] ==
      [("flowise.delete", "cf-1"), ("flowise.delete", "cf-2")], list(log))

# Langfuse deletes on its own time, so the report must not claim otherwise.
lf = step(result, "traces")
check("the Langfuse step says it asked rather than did",
      lf and "asked" in lf["action"], lf)
check("…and says the deletion is not immediate",
      lf and "15 minutes" in lf["detail"], lf)
# Three distinct ids across two chatflows — the one LTI session id, shared by
# both conversations and counted once, and each chatflow's own chat id — with
# two traces apiece.
check("every trace of every id was asked for",
      lf and "6 trace(s)" in lf["detail"], lf)

# ─── 3. The record goes last, and the course is really gone ──────────────────
actions = [s["action"] for s in result["steps"]]
check("the course record is the last thing deleted",
      actions[-1].startswith("deleted the course record"), actions)
check("the course is gone from the list",
      courses_service.get_course(CID) is None, "")
with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM agent_slots WHERE course_id = %s", (CID,))
        (left,) = cur.fetchone()
    conn.commit()
check("its slots went with it, by cascade", left == 0, left)

# ─── 4. One failed step keeps the course ─────────────────────────────────────
# The important half. A deletion that removes the record anyway turns a
# recoverable half-deletion into orphaned data in five systems.
fresh_course()
result, log = run(weaviate=lambda log: Weaviate(log, fail_class="UserMemory"))
check("a failed step means the course is not deleted", not result["deleted"],
      result["steps"])
check("the course is still there", courses_service.get_course(CID) is not None, "")
kept = step(result, "kept the course record")
check("…and the report says why it was kept", kept and kept["error"], kept)
# Everything else still ran: stopping at the first failure would leave more
# behind and tell the operator less.
check("the other systems were still cleared",
      ("neo4j.clear", CID) in log and ("s3.empty", course["bucket"]) in log,
      list(log))
check("the failure names the class", step(result, "delete from UserMemory") is not None,
      [s["action"] for s in result["steps"]])

# ─── 5. Already gone is not a failure ────────────────────────────────────────
fresh_course()
result, log = run(flowise=lambda log: Flowise(log, present=()),
                  garage=lambda log: Garage(log, exists=False))
check("a chatflow that no longer exists does not fail the deletion",
      result["deleted"], [s for s in result["steps"] if not s["ok"]])
check("…and is counted as already gone",
      "2 already gone" in (step(result, "deleted the chatflows") or {}).get("detail", ""),
      step(result, "deleted the chatflows"))
check("a bucket that no longer exists is not emptied",
      not any(c[0] == "s3.empty" for c in log), list(log))

# ─── 6. Without Langfuse, the step is skipped and says so ────────────────────
# Not "done": claiming traces were removed on an installation that never had
# any is the kind of report that makes the next one unbelievable.
# Driven through the configuration, not through the argument: whether this
# installation has Langfuse is a property of the deployment, and a caller that
# could switch it off by passing None would be able to skip the step by
# accident.
fresh_course()
Path(os.environ["SMARTRAG_ENV_PATH"]).write_text(
    'CONTENT_ADMIN_SESSION_SECRET="t"\nPOSTGRES_USER="u"\nPOSTGRES_PASSWORD="p"\n'
    'COMPOSE_PROFILES="core"\nWEAVIATE_API_KEY="wv"\n'
    'GARAGE_ACCESS_KEY="GK1"\nGARAGE_SECRET_KEY="s1"\nNEO4J_PASSWORD="pw"\n')
log = Log()
result = courses_service.delete_course(
    CID, weaviate=Weaviate(log), garage=Garage(log), neo4j=Neo4j(log),
    flowise=Flowise(log), s3=S3(log), langfuse=None)
check("with no Langfuse nothing tries to reach it",
      not any(c[0].startswith("langfuse") for c in log), list(log))
skipped = step(result, "skipped")
check("with no Langfuse the step is skipped", skipped is not None,
      [s["action"] for s in result["steps"]])
check("…and the deletion still completes", result["deleted"], result["steps"])

# ─── 7. The page asks for the id, and a near miss changes nothing ────────────
# The one confirmation that cannot be given by muscle memory. Everything else
# on this page is a button, and a button next to a course name is one careless
# click away from removing six systems' worth of data.
import accounts  # noqa: E402
import app as flask_app  # noqa: E402

fresh_course()
accounts.create_account("chefin", "a-strong-test-password", accounts.ROLE_ADMIN)
client = flask_app.app.test_client()
client.post("/login", data={"username": "chefin",
                            "password": "a-strong-test-password"})

page = client.get(f"/courses/{CID}/delete")
check("the page renders for an administrator", page.status_code == 200,
      page.status_code)
body = page.get_data(as_text=True)
check("…and counts before it asks", CID in body and "Wie viel" in body or "How much" in body,
      body[:200])

before = courses_service.get_course(CID)
answer = client.post(f"/courses/{CID}/delete", data={"confirm": CID + "x"})
check("a mistyped id deletes nothing",
      courses_service.get_course(CID) is not None, "the course is gone")
check("…and says so", "confirm" in answer.get_data(as_text=True).lower()
      or answer.status_code == 200, answer.status_code)
check("nothing about the course changed", courses_service.get_course(CID) == before,
      "")


# ─── 8. A failed step must say why, on the page ──────────────────────────────
# The report rendered "detail or error", so a step that failed with a count
# showed the count and swallowed the reason: "0 deleted, 0 already gone" with
# no word about what stopped it — on the one row the operator has to read.
report = (Path(flask_app.__file__).parent / "templates" / "course_delete.html").read_text()
check("the report does not hide the error behind the detail",
      "s.detail or s.error" not in report,
      "a failed step would show its count and not its reason")
check("…it renders both", "s.error" in report and "s.detail" in report, "")

# And it is written to the log as well, because the page is gone as soon as
# somebody navigates away from it.
src = Path(courses_service.__file__).read_text()
check("a failed step is logged", "logger.error(\"Deleting %s" in src, "")

# ─── Order that reaches a person must be asked for ──────────────────────────
# Checked in the source, deliberately. A missing ORDER BY is only wrong
# sometimes — it follows the physical row order, so it is right until an
# unrelated UPDATE moves a row. Re-running the suite cannot catch that
# reliably; reading the query can.
import re as _re  # noqa: E402

_APP = Path(__file__).resolve().parent.parent / "content-admin"
if not _APP.is_dir() and Path("/app/db.py").exists():
    _APP = Path("/app")

for module in ("courses.py", "learners.py"):
    text = (_APP / module).read_text()
    # A window after each SELECT, not "up to the next cur.execute" — in this
    # codebase cur.execute comes *before* the query string, so the first
    # version of this check looked at the wrong side of it and passed on the
    # very query it was written for.
    ok = True
    for match in _re.finditer(r"SELECT chatflow_id FROM agent_slots", text):
        window = text[match.end(): match.end() + 260]
        if "ORDER BY" not in window:
            ok = False
    check(f"{module}: chatflow ids are read in a defined order", ok,
          "without ORDER BY the order changes after any update to a slot, and "
          "it reaches the operator in what deletion reports")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All course-deletion checks passed: the chat sessions are read and the "
    "traces asked for before any chatflow is deleted, because Flowise removes "
    "the records that are the only bridge from a course to its traces; the "
    "bucket is emptied before Garage is asked to delete it, which Garage "
    "refuses otherwise; the collection goes whole while the three shared "
    "learner classes are filtered by course; the graph is cleared; the course "
    "record is the last thing removed and takes its slots with it by cascade; "
    "a single failed step leaves the record in place with the reason, while "
    "every other system is still cleared; a chatflow or bucket that is "
    "already gone is counted rather than treated as a failure; and on an "
    "installation without Langfuse the trace step is reported as skipped, not "
    "as done; and the page that offers all this asks for the course id to be typed, so a near miss changes nothing."
)
