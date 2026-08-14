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

    def chat_session_ids(self, chatflow_id):
        self.log.append(("flowise.sessions", chatflow_id))
        return [f"chat-{chatflow_id}"]

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
check("both chatflows are deleted",
      [c for c in log if c[0] == "flowise.delete"] ==
      [("flowise.delete", "cf-1"), ("flowise.delete", "cf-2")], list(log))

# Langfuse deletes on its own time, so the report must not claim otherwise.
lf = step(result, "traces")
check("the Langfuse step says it asked rather than did",
      lf and "asked" in lf["action"], lf)
check("…and says the deletion is not immediate",
      lf and "15 minutes" in lf["detail"], lf)
check("all four traces were asked for", lf and "4 trace(s)" in lf["detail"], lf)

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
    "as done."
)
