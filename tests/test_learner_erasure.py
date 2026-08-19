"""
Erasing one person, across the systems that each know them by a different name.

Three things decide whether this is safe, and none of them is visible by
reading the code once.

**Who the learner is.** Flowise stores a `sessionId` of the form
"<learner>|<something>" and a `chatId` that is a conversation, not a person.
Matching the wrong one erases either nothing or everybody: `chatId` belongs to
no learner, and a prefix match on the session id would take every learner whose
id begins with the same characters. The rule is the one the agents themselves
use — the part before the first "|", the whole string when there is none.

**The order.** A Langfuse trace carries Flowise's chatId as its own sessionId
and carries no learner at all (Flowise 3.1.3 sets no userId on the trace). So
the only route from a person to their traces runs through Flowise's chat
records, and those have to be read before anything in Flowise is deleted.

**A failure must not read as an absence.** If the conversations cannot be
read, the traces they point at are unknown — which is not the same as there
being none, and must not be reported as a clean skip.

The stubs record what they were asked and in which order, because that order
is the thing under test.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402

os.environ["SMARTRAG_ENV_PATH"] = str(dbfixture.tmp_env(
    'COMPOSE_PROFILES="core,observability"\nWEAVIATE_API_KEY="wv"\n'
    'LANGFUSE_INIT_PROJECT_PUBLIC_KEY="pk"\nLANGFUSE_INIT_PROJECT_SECRET_KEY="sk"\n'))

db, course = dbfixture.require_database()

import learners  # noqa: E402
import storage  # noqa: E402
from flowise_client import FlowiseError  # noqa: E402
from weaviate_client import WeaviateError  # noqa: E402

CID = course["id"]
LEARNER = "lti-7f3a"
OTHER = "lti-7f3ab"          # begins with LEARNER — a prefix match takes it too
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
    """Two chatflows, three learners, one of whom is a prefix of another."""

    def __init__(self, log, fail_read=False, fail_delete=()):
        self.log = log
        self.fail_read = fail_read
        self.fail_delete = set(fail_delete)
        self.records = {
            "cf-1": [
                {"session_id": f"{LEARNER}|mathe", "chat_id": "chat-a"},
                {"session_id": f"{LEARNER}|mathe", "chat_id": "chat-b"},
                {"session_id": f"{OTHER}|mathe", "chat_id": "chat-c"},
                {"session_id": "someone-else|mathe", "chat_id": "chat-d"},
            ],
            "cf-2": [
                # No separator at all: a chat opened outside the LMS. The
                # agents treat the whole string as the learner, so this is
                # theirs and must go.
                {"session_id": LEARNER, "chat_id": "chat-e"},
            ],
        }

    def chat_records(self, chatflow_id):
        self.log.append(("flowise.read", chatflow_id))
        if self.fail_read:
            raise FlowiseError("Flowise is not reachable")
        return list(self.records.get(chatflow_id, []))

    def delete_chat_session(self, chatflow_id, session_id):
        self.log.append(("flowise.delete", chatflow_id, session_id))
        if not session_id:
            raise FlowiseError("no session id")
        if session_id in self.fail_delete:
            raise FlowiseError("Flowise said no")


class Langfuse:
    def __init__(self, log, fail=False):
        self.log, self.fail, self.deleted = log, fail, []

    def trace_ids_for_session(self, session_id):
        self.log.append(("langfuse.list", session_id))
        return [f"trace-{session_id}"]

    def delete_traces(self, ids):
        self.log.append(("langfuse.delete", tuple(sorted(ids))))
        if self.fail:
            from langfuse_client import LangfuseError
            raise LangfuseError("Langfuse is not reachable")
        self.deleted.extend(ids)
        return len(ids)


class Weaviate:
    SHARED_LEARNER_CLASSES = ("ChatHistory", "UserMemory", "TestResults")

    def __init__(self, log, fail_class=""):
        self.log, self.fail_class = log, fail_class

    def count_by_learner(self, cls, user_id, course_id=None):
        self.log.append(("weaviate.count", cls, user_id, course_id))
        if cls == self.fail_class:
            raise WeaviateError("Weaviate is not reachable")
        return 3

    def delete_by_learner(self, cls, user_id, course_id=None):
        self.log.append(("weaviate.delete", cls, user_id, course_id))
        if cls == self.fail_class:
            raise WeaviateError("Weaviate is not reachable")
        return 3


def fresh_course():
    dbfixture._ensure_course(db)
    dbfixture.clear_slots(db)
    storage.save_slot(CID, 1, "agent-topic-template.json", {}, "Tutor")
    storage.set_chatflow_id(CID, 1, "cf-1", "digest")
    storage.save_slot(CID, 2, "agent-topic-template.json", {}, "Zweiter")
    storage.set_chatflow_id(CID, 2, "cf-2", "digest")


def run(user_id=LEARNER, course_id=None, **overrides):
    fresh_course()
    log = Log()
    flowise = overrides.pop("flowise", None) or Flowise(log)
    langfuse = overrides.pop("langfuse", None) or Langfuse(log)
    weaviate = overrides.pop("weaviate", None) or Weaviate(log)
    result = learners.erase(user_id, course_id, weaviate=weaviate,
                            flowise=flowise, langfuse=langfuse)
    return result, log, flowise, langfuse, weaviate


# ─── Who the learner is ──────────────────────────────────────────────────────

check("the id before the separator is the learner",
      learners.learner_of("lti-7f3a|mathe|2") == "lti-7f3a",
      learners.learner_of("lti-7f3a|mathe|2"))
check("a session id with no separator is the whole learner",
      learners.learner_of("lti-7f3a") == "lti-7f3a")
check("an empty session id has no learner",
      learners.learner_of("") == "" and learners.learner_of(None) == "")

# ─── The happy path ──────────────────────────────────────────────────────────

result, log, flowise, langfuse, weaviate = run()
check("the erasure succeeded", result["erased"],
      [s for s in result["steps"] if not s["ok"]])

deletes = [e for e in log if e[0] == "flowise.delete"]
deleted_sessions = {(e[1], e[2]) for e in deletes}
check("both of the learner's sessions were deleted",
      deleted_sessions == {("cf-1", f"{LEARNER}|mathe"), ("cf-2", LEARNER)},
      deleted_sessions)

# The point of the OTHER learner: their id begins with this one's.
check("a learner whose id merely begins the same is untouched",
      not any(e[2].startswith(OTHER) for e in deletes), deleted_sessions)
check("another learner's session is untouched",
      not any("someone-else" in e[2] for e in deletes), deleted_sessions)

# ─── The order ───────────────────────────────────────────────────────────────

kinds = [e[0] for e in log]


def at(kind):
    """Where a call happened, or None. Not list.index: a mutation that makes
    the call disappear entirely should be reported as a missing call, not
    crash this file before it can print anything."""
    return kinds.index(kind) if kind in kinds else None


read, del_flowise, del_langfuse = at("flowise.read"), at("flowise.delete"), at("langfuse.delete")
check("all three kinds of call happened at all",
      None not in (read, del_flowise, del_langfuse), kinds)
check("the conversations are read before anything is deleted",
      None not in (read, del_flowise, del_langfuse)
      and read < min(del_flowise, del_langfuse), kinds)
check("Langfuse is asked before Flowise loses the records",
      None not in (del_flowise, del_langfuse) and del_langfuse < del_flowise, kinds)

traces = ([e[1] for e in log if e[0] == "langfuse.delete"] or [()])[0]
check("every chat of the learner's sessions became a trace deletion",
      set(traces) == {"trace-chat-a", "trace-chat-b", "trace-chat-e"}, traces)
check("no other learner's chat was included",
      "trace-chat-c" not in traces and "trace-chat-d" not in traces, traces)

# Langfuse deletes asynchronously and confirms nothing, so the wording must
# not claim the traces are gone.
lf = step(result, "Langfuse")
check("the Langfuse step says it asked, not that it deleted",
      lf is not None and "asked" in lf["action"], lf)

# ─── Weaviate, all three classes, scoped or not ──────────────────────────────

classes = [e[1] for e in log if e[0] == "weaviate.delete"]
check("all three learner classes are cleared",
      classes == ["ChatHistory", "UserMemory", "TestResults"], classes)
scopes = {e[3] for e in log if e[0] == "weaviate.delete"}
check("without a course the erasure is not scoped to one", scopes == {None}, scopes)

result, log, *_ = run(course_id=CID)
scopes = {e[3] for e in log if e[0] == "weaviate.delete"}
check("with a course every Weaviate deletion carries it", scopes == {CID}, scopes)

# ─── A failure must not read as an absence ───────────────────────────────────

log = Log()
result = learners.erase(LEARNER, None, weaviate=Weaviate(log),
                        flowise=Flowise(log, fail_read=True),
                        langfuse=Langfuse(log))
check("an unreadable Flowise fails the erasure", not result["erased"])
lf = step(result, "delete the traces")
check("the traces are reported as unknown, not as none",
      lf is not None and not lf["ok"] and "could not be" in lf["error"], lf)
check("nothing was deleted in Flowise after the read failed",
      not any(e[0] == "flowise.delete" for e in log), log)
check("Weaviate is still cleared — it does not depend on the bridge",
      any(e[0] == "weaviate.delete" for e in log), log)

# One failing class must not stop the other two, and must not be reported
# as an erasure that worked.
log = Log()
result = learners.erase(LEARNER, None, weaviate=Weaviate(log, fail_class="UserMemory"),
                        flowise=Flowise(log), langfuse=Langfuse(log))
check("one failing class does not stop the others",
      [e[1] for e in log if e[0] == "weaviate.delete"]
      == ["ChatHistory", "UserMemory", "TestResults"])
check("a failing class makes the whole erasure unsuccessful", not result["erased"])

log = Log()
result = learners.erase(LEARNER, None, weaviate=Weaviate(log),
                        flowise=Flowise(log, fail_delete={f"{LEARNER}|mathe"}),
                        langfuse=Langfuse(log))
check("a failed session deletion is reported as failed", not result["erased"])
fs = step(result, "delete the conversations")
check("and says how many did go", fs is not None and "1 deleted" in fs["detail"], fs)

log = Log()
result = learners.erase(LEARNER, None, weaviate=Weaviate(log),
                        flowise=Flowise(log), langfuse=Langfuse(log, fail=True))
check("a Langfuse failure does not stop Flowise or Weaviate",
      any(e[0] == "flowise.delete" for e in log)
      and any(e[0] == "weaviate.delete" for e in log))
check("but it does make the erasure unsuccessful", not result["erased"])

# ─── Refusals ────────────────────────────────────────────────────────────────

for label, call in (
    ("erase", lambda: learners.erase("")),
    ("inventory", lambda: learners.inventory("")),
    ("sessions_of", lambda: learners.sessions_of("")),
):
    try:
        call()
        check(f"{label} refuses an empty learner id", False, "it did not raise")
    except learners.LearnerError:
        check(f"{label} refuses an empty learner id", True)
    except Exception as exc:  # noqa: BLE001
        check(f"{label} refuses an empty learner id", False, f"raised {exc!r}")

try:
    learners.erase(LEARNER, "no-such-course")
    check("erase refuses a course that does not exist", False, "it did not raise")
except learners.LearnerError:
    check("erase refuses a course that does not exist", True)

# ─── The inventory ───────────────────────────────────────────────────────────

log = Log()
inv = learners.inventory(LEARNER, None, weaviate=Weaviate(log), flowise=Flowise(log))
check("the inventory deletes nothing",
      not any(e[0].endswith("delete") for e in log), log)
systems = {i["system"] for i in inv["items"]}
check("every system is named, including those holding nothing",
      systems == {"weaviate", "flowise", "langfuse", "neo4j", "garage", "postgres"},
      systems)
sessions = [i for i in inv["items"] if i["label"] == "inv_learner_sessions"][0]
check("the session count is the learner's own", sessions["count"] == 3, sessions)

log = Log()
inv = learners.inventory(LEARNER, None, weaviate=Weaviate(log, fail_class="UserMemory"),
                         flowise=Flowise(log))
bad = [i for i in inv["items"] if i["label"] == "inv_learner_class" and i["error"]]
check("an unreachable class is one unknown line, not a raised error",
      len(bad) == 1 and bad[0]["count"] is None, bad)
check("and the other lines still have their numbers",
      len([i for i in inv["items"]
           if i["label"] == "inv_learner_class" and i["count"] == 3]) == 2)

# ─── Report ──────────────────────────────────────────────────────────────────

if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("All learner-erasure checks passed: the learner is the part of Flowise's")
print("session id before the first '|' and the whole id when there is none, so")
print("a chat opened outside the LMS is included while a learner whose id merely")
print("begins the same is not; the conversations are read before anything is")
print("deleted and Langfuse is asked before Flowise loses the records that point")
print("at them, with every chat of the learner's own sessions and no other's;")
print("the Langfuse step says it asked rather than that it deleted, because")
print("Langfuse confirms nothing; all three Weaviate classes are cleared, scoped")
print("to one course when one is given and to none when not; a Flowise read that")
print("fails reports the traces as unknown rather than as absent and deletes")
print("nothing there, while Weaviate is still cleared; one failing class, one")
print("failing session and a failing Langfuse each leave the rest done and the")
print("erasure marked unsuccessful; an empty learner id and an unknown course are")
print("refused; and the inventory deletes nothing, names all six systems")
print("including the three that hold nothing, and turns one unreachable class")
print("into a single unknown line.")
