"""
What a course consists of, counted before anything is deleted.

A deletion that asks "really delete Chemie?" is asking the wrong question. It
should ask "really delete 113 document chunks, 189 conversations, 15 learning
records, 51 concepts, 12 stored files and 3 agents?" — a name is easy to
confirm, a number is not.

Which makes the failure that matters here a specific one: **an inventory that
reports zero because a service is unreachable**. The operator then confirms a
deletion believing there is nothing to lose, and the deletion proceeds against
a system that is merely down and comes back later holding data nobody expects.
So an unknown count is None and says why, never 0 — and that is what most of
this file checks.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402

# Before the fixture: env_file binds its path at import time.
os.environ["SMARTRAG_ENV_PATH"] = str(dbfixture.tmp_env(
    'COMPOSE_PROFILES="core,observability"\nWEAVIATE_API_KEY="wv"\n'
    'GARAGE_ADMIN_TOKEN="tok"\nNEO4J_PASSWORD="pw"\n'))

db, course = dbfixture.require_database()

import courses as courses_service  # noqa: E402
import storage  # noqa: E402
from weaviate_client import WeaviateError  # noqa: E402
from garage_client import GarageError  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def item_for(inv, cls):
    """The shared-class line for one Weaviate class. The label is a key now,
    so the class name is in the arguments rather than in the text."""
    for item in inv["items"]:
        if item["label"] == "inv_shared_class" and cls in item["args"]:
            return item
    return None


def line(inv, system, fragment):
    """The first inventory line of a system whose label contains fragment."""
    for item in inv["items"]:
        if item["system"] == system and fragment in item["label"]:
            return item
    return None


# ─── Stubs that record what was asked of them ────────────────────────────────
class Weaviate:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def collection_exists(self, name):
        self.calls.append(("collection_exists", name))
        return True

    def count_chunks(self, collection, course_id):
        self.calls.append(("count_chunks", collection, course_id))
        if self.fail:
            raise WeaviateError("Weaviate is not reachable")
        return 113

    def count_by_course(self, cls, course_id):
        self.calls.append(("count_by_course", cls, course_id))
        if self.fail:
            raise WeaviateError("Weaviate is not reachable")
        return {"ChatHistory": 189, "UserMemory": 15, "TestResults": 0}[cls]


class Garage:
    def __init__(self, info=None, fail=False):
        self.info, self.fail, self.calls = info, fail, []

    def bucket_info(self, name):
        self.calls.append(("bucket_info", name))
        if self.fail:
            raise GarageError("Garage is not reachable")
        return self.info


class Neo4j:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def counts(self, course_id):
        self.calls.append(("counts", course_id))
        if self.fail:
            raise RuntimeError("Neo4j is not reachable")
        return {"concepts": 51, "edges": 40}


class Flowise:
    def __init__(self, ids):
        self.ids, self.calls = ids, []

    def list_chatflows(self):
        self.calls.append(("list_chatflows",))
        return [{"id": i} for i in self.ids]


BUCKET = {"objects": 12, "bytes": 34567, "unfinishedUploads": 0}
CID = course["id"]

# ─── 1. Everything that belongs to the course is counted ─────────────────────
dbfixture.clear_slots(db)
storage.save_slot(CID, 1, "agent-topic-template.json", {}, "Tutor")
storage.set_chatflow_id(CID, 1, "cf-live", "digest")
storage.save_slot(CID, 2, "agent-topic-template.json", {}, "Zweiter")
storage.set_chatflow_id(CID, 2, "cf-gone", "digest")
storage.save_slot(CID, 3, "agent-topic-template.json", {}, "Nie importiert")

w, g, n = Weaviate(), Garage(BUCKET), Neo4j()
f = Flowise(["cf-live"])
inv = courses_service.inventory(CID, weaviate=w, garage=g, neo4j=n, flowise=f)

check("the course itself is returned", inv["course"]["id"] == CID, inv["course"])
check("document chunks are counted",
      line(inv, "weaviate", "inv_chunks")["count"] == 113, inv["items"])
check("conversations are counted",
      item_for(inv, "ChatHistory")["count"] == 189, inv["items"])
check("learning records are counted",
      item_for(inv, "UserMemory")["count"] == 15, inv["items"])
check("a class with nothing in it still appears",
      item_for(inv, "TestResults")["count"] == 0, inv["items"])
check("stored files are counted", line(inv, "garage", "inv_objects")["count"] == 12,
      inv["items"])
check("concepts are counted", line(inv, "neo4j", "inv_concepts")["count"] == 51,
      inv["items"])
check("prerequisite links are counted",
      line(inv, "neo4j", "inv_links")["count"] == 40, inv["items"])
check("configured slots are counted",
      line(inv, "postgres", "inv_slots_configured")["count"] == 3, inv["items"])
check("imported agents are counted separately",
      line(inv, "postgres", "inv_slots_imported")["count"] == 2, inv["items"])

# Only Flowise knows whether a chatflow is still there, and an id that is
# already gone is not a failure at deletion time.
check("only the chatflows that still exist are listed for deletion",
      line(inv, "flowise", "inv_chatflows")["count"] == 1, inv["items"])
check("…and the ones already gone are named as such",
      line(inv, "flowise", "inv_chatflows_gone")["count"] == 1, inv["items"])

# ─── 2. Unknown is not zero ──────────────────────────────────────────────────
# The whole point. Each system is asked separately, so one being down leaves
# one line unknown rather than emptying the inventory or aborting it.
inv = courses_service.inventory(CID, weaviate=Weaviate(fail=True),
                                garage=Garage(fail=True), neo4j=Neo4j(fail=True),
                                flowise=Flowise([]))
for system, fragment in (("weaviate", "inv_chunks"), ("weaviate", "inv_shared_class"),
                         ("garage", "inv_object_storage"), ("neo4j", "inv_graph")):
    item = line(inv, system, fragment)
    check(f"{system}/{fragment} reads as unknown, not as none",
          item is not None and item["count"] is None, item)
    check(f"…and says why", item and item["error"], item)
check("one system being down does not empty the rest",
      line(inv, "postgres", "inv_slots_configured")["count"] == 3, inv["items"])

# ─── 3. Absent is zero, and says so ──────────────────────────────────────────
# A bucket that was never created and a collection that does not exist are
# nothing to delete. That is a fact, not an error, and reporting it as one
# would make a clean course look broken.
inv = courses_service.inventory(CID, weaviate=Weaviate(), garage=Garage(None),
                                neo4j=Neo4j(), flowise=Flowise([]))
absent = line(inv, "garage", "inv_bucket")
check("a bucket that does not exist counts zero", absent and absent["count"] == 0,
      absent)
check("…without an error", absent and not absent["error"], absent)

# ─── 4. Langfuse is named, not counted ───────────────────────────────────────
# Traces carry a learner id and Flowise's chat id, never a course. The route
# from a course to its traces runs through Flowise's chat records, which
# Flowise deletes together with the chatflow — so after a deletion the mapping
# is gone too. A zero here would read as "there are none".
lf = line(inv, "langfuse", "inv_traces")
check("Langfuse traces are reported as not attributable",
      lf is not None and lf["count"] is None and lf["error"], lf)
# Two different things both leave count at None, and they mean opposite
# things: a system that did not answer may hold data and is a reason to
# hesitate; a number that cannot exist is not. The page warned about both
# and sent the operator to fix a Langfuse that was working.
check("…and marked as a number that cannot exist, not as a failure",
      lf and lf["unknowable"] is True, lf)
for item in inv["items"]:
    if item["system"] != "langfuse":
        check(f"{item['system']}/{item['label']} is not marked unknowable",
              item["unknowable"] is False, item)

# ─── 5. The inventory only reads ─────────────────────────────────────────────
# It is offered as "what is in this course", not only as a step before
# deleting, and a page that answers that question must not change anything.
w, g, n = Weaviate(), Garage(BUCKET), Neo4j()
courses_service.inventory(CID, weaviate=w, garage=g, neo4j=n, flowise=Flowise([]))
for stub in (w, g, n):
    for call in stub.calls:
        check("the inventory changes nothing",
              not call[0].startswith(("delete", "create", "apply", "clear", "allow")),
              call)

# ─── 6. A maintainer who would be left with nothing ──────────────────────────
# Their account survives the course and can then reach nothing. Deleting a
# person because a course ended is not a decision a deletion routine should
# make on its own, so it is reported and left alone.
import accounts  # noqa: E402

solo = accounts.create_account("solo", "x" * 12, "maintainer")
accounts.assign(solo["id"], CID)
inv = courses_service.inventory(CID, weaviate=Weaviate(), garage=Garage(BUCKET),
                                neo4j=Neo4j(), flowise=Flowise([]))
stranded = line(inv, "postgres", "inv_maintainers_stranded")
check("a maintainer who would be left with no course is reported",
      stranded and stranded["count"] == 1, inv["items"])
check("…and the report says their account is not touched",
      stranded and stranded["note"] == "inv_maintainers_stranded_note", stranded)

# ─── 7. Every line exists in both languages ──────────────────────────────────
# This table is built in Python, not in a template, which is how it came to be
# the one page in the application that showed English text to a German
# operator: "agent slots configured", "shared with other courses — removed by
# filter". Labels and notes are i18n keys now, and this is what keeps them so.
import i18n  # noqa: E402

inv = courses_service.inventory(CID, weaviate=Weaviate(), garage=Garage(BUCKET),
                                neo4j=Neo4j(), flowise=Flowise(["cf-live"]))
failing = courses_service.inventory(CID, weaviate=Weaviate(fail=True),
                                    garage=Garage(fail=True), neo4j=Neo4j(fail=True),
                                    flowise=Flowise([]))
for source in (inv, failing):
    for item in source["items"]:
        for field in ("label", "note"):
            key = item[field]
            if not key:
                continue
            check(f"{key} is a key, not a sentence",
                  " " not in key and key.islower(),
                  f"{field} of {item['system']} reads as prose")
            check(f"{key} exists in English", key in i18n.MSG_EN, "")
            check(f"{key} exists in German", key in i18n.MSG_DE, "")
        # The technical error of a system that did not answer is its own
        # message and stays untranslated — but the explanation of a number
        # that cannot exist is text this page wrote, so it is a key.
        if item["unknowable"] and item["error"]:
            check("the explanation of an uncountable line is translated",
                  item["error"] in i18n.MSG_DE, item["error"])


if failures:
    print("FAILURES:")
    for f_ in failures:
        print("  -", f_)
    sys.exit(1)
print(
    "All course-inventory checks passed: every system a course touches is "
    "counted — chunks in its own collection, its records in the three shared "
    "learner classes, objects in its bucket, concepts and links in the graph, "
    "its slots and maintainers, and the chatflows that still exist in Flowise "
    "as opposed to the ids merely recorded here; a system that cannot be "
    "reached leaves its line unknown with the reason rather than reporting "
    "zero, and does not stop the other lines; a bucket or collection that was "
    "never created reads as zero without an error; Langfuse is named as not "
    "attributable to a course rather than counted as none; the inventory only "
    "reads; a maintainer who would be left with no course is reported while "
    "their account is left alone; and every label and note is an i18n key that "
    "exists in both languages — this table is built in Python rather than in a "
    "template, which is how it came to be the one page that answered a German "
    "operator in English."
)
