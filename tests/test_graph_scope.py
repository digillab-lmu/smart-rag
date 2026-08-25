"""Which agents' material the concept map is built from, and leaving again.

The map covers a whole course, across every agent — that is what makes it a
shared vocabulary rather than ten private ones. It also means a course is a
container several people put agents into, and the operator named the failure
mode from experience: *"Ich kenne meine Kollegen."* Someone sets up agents
with entirely unrelated material, someone presses rebuild, and two subjects
are now one map.

The checkbox is the coarse control: does this agent's material take part at
all. Everything below is about one distinction that is easy to collapse and
expensive to get wrong —

    "no longer read"  is not  "no longer in the map"

Unticking is scope. It changes what the next build reads and removes nothing,
because a concept two agents' documents support has to survive one of them
leaving. Taking a contribution out is a second, explicit act with its own
numbers. A checkbox that silently did both would be a delete button wearing a
checkbox's clothes; one that did neither would be a lie the first time
somebody trusted it.
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
import neo4j_client  # noqa: E402
import db  # noqa: E402
import storage  # noqa: E402
import weaviate_client  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


CID = course["id"]
storage.save_slot(CID, 1, "tutor", {}, "Eins", "p")
storage.save_slot(CID, 2, "tutor", {}, "Zwei", "p")
storage.save_slot(CID, 3, "tutor", {}, "Drei", "p")
# What a newly created agent gets, asked of the database rather than of
# whatever the last run left behind: slots persist, the course fixture is
# shared, and another suite ticks and unticks the same numbers. DEFAULT is
# the column's own answer, which is the thing being checked.
with db.connect() as _conn:
    with _conn.cursor() as _cur:
        _cur.execute("UPDATE agent_slots SET in_graph = DEFAULT "
                     "WHERE course_id = %s AND slot = 3", (CID,))
    _conn.commit()
storage.set_in_graph(CID, 1, True)
storage.set_in_graph(CID, 2, True)

DOCS = [{"source_title": "A", "source_file": "agent_1/a.md", "agent_id": 1, "chunks": 3},
        {"source_title": "B", "source_file": "agent_2/b.md", "agent_id": 2, "chunks": 3}]
removed: list = []

weaviate_client.WeaviateClient.list_documents = lambda self, c, cid: list(DOCS)
neo4j_client.Neo4jClient.by_source = lambda self, cid: {
    "agent_1/a.md": {"concepts": 12, "only": 5},
    "agent_2/b.md": {"concepts": 4, "only": 0}}
neo4j_client.Neo4jClient.remove_documents = lambda self, cid, docs: (
    removed.append(sorted(docs)),
    {"concepts_removed": 5, "concepts_kept": 7, "edges_removed": 2})[1]

client = flask_app.app.test_client()
with client.session_transaction() as sess:
    user = (accounts.get_by_username("scopetest")
            or accounts.create_account("scopetest", "a-strong-test-password",
                                       role=accounts.ROLE_ADMIN))
    sess["user_id"] = user["id"]
    sess["logged_in"] = True
    sess["course_id"] = CID

# ─── The default, and what is on the page before any decision ───────────────
check("a new agent takes part by default",
      storage.all_slots(CID)["3"]["in_graph"] is True,
      "a default of off makes the feature look broken — nothing would ever "
      "enter the map until somebody found a checkbox")

page = client.get("/").get_data(as_text=True)
check("the agent list has the column", "In the map" in page, "")
check("every configured agent has a checkbox",
      page.count('name="in_graph"') == 3, page.count('name="in_graph"'))
check("with what that agent holds up beside it",
      "12" in page and "(5)" in page,
      "the number is what makes unticking a decision rather than a click")
# A phrase from this sentence alone, not words that also occur in
# "Re-import every agent of this course" — which is why the first version of
# this check stayed green with the explanation deleted.
check("and the page says what the map covers",
      "covers this whole course" in page,
      "somebody has to be told the map is course-wide before they can be "
      "expected to think about scope")

# ─── Unticking is scope, and only scope ─────────────────────────────────────
page = client.post("/", data={"action": "graph_scope",
                              "in_graph": ["2", "3"]}).get_data(as_text=True)
check("the unticked agent is recorded as out",
      storage.all_slots(CID)["1"]["in_graph"] is False, "")
check("the other one is untouched",
      storage.all_slots(CID)["2"]["in_graph"] is True, "")
check("and nothing was removed from the map", removed == [],
      "unticking must not delete — a concept two agents support has to "
      "survive one of them leaving, and only a person can decide that")
check("the page says the map is unchanged",
      "nothing in the map has changed" in page.lower()
      or "takes effect at the next build" in page.lower(), "")
check("an agent that is out but still present says so",
      "still in the map" in page,
      "without this line an unticked agent reads as removed, which is the "
      "one misunderstanding this whole design exists to prevent")
check("and the removal is offered next to it",
      'value="graph_drop_agent"' in page, "")

# Re-ticking is just as quiet.
client.post("/", data={"action": "graph_scope",
                       "in_graph": ["1", "2", "3"]})
check("re-ticking restores the scope",
      storage.all_slots(CID)["1"]["in_graph"] is True, "")
check("and still writes nothing to the map", removed == [],
      "ticking adds nothing until the next build, and must not pretend to")

client.post("/", data={"action": "graph_scope", "in_graph": ["2", "3"]})

# ─── Removal is the second, explicit act ────────────────────────────────────
page = client.post("/", data={"action": "graph_drop_agent",
                              "slot": "1"}).get_data(as_text=True)
check("removing takes exactly that agent's documents",
      removed == [["agent_1/a.md"]],
      "the other agent's material must not travel with it")
check("and reports what went and what stayed",
      "5" in page and "7" in page,
      "a concept other material supports is kept, and silence about that "
      "reads as if everything went")

# ─── Refusals ───────────────────────────────────────────────────────────────
removed.clear()
page = client.post("/", data={"action": "graph_drop_agent",
                              "slot": "9"}).get_data(as_text=True)
check("an agent with no documents removes nothing",
      removed == [] and "nothing to remove" in page,
      "an empty document list would otherwise mean 'match everything'")


def _boom(self, c, cid):
    raise weaviate_client.WeaviateError("Weaviate is down")


removed.clear()
weaviate_client.WeaviateClient.list_documents = _boom
page = client.post("/", data={"action": "graph_drop_agent",
                              "slot": "1"}).get_data(as_text=True)
check("an unreadable document list removes nothing", removed == [],
      "guessing here removes the wrong thing, or nothing while reporting "
      "success")
# And it must not be reported as "this agent has no documents". The two are
# opposite situations: one needs nothing done, the other needs somebody to
# look at why the index is unreachable.
check("and does not claim the agent has none",
      "nothing to remove" not in page,
      "an unreadable index reported as an empty agent sends the operator "
      "away satisfied while the material is still in the map")
check("and the agent list still renders",
      client.get("/").status_code == 200,
      "the dashboard is the page an operator lands on")

if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("All graph-scope checks passed: every agent takes part by default and")
print("carries a checkbox on the agent list with what its material holds up")
print("beside it, under a sentence saying the map spans the whole course;")
print("unticking records the scope and removes nothing, re-ticking adds")
print("nothing, and an agent that is out while its concepts are still in the")
print("map says exactly that instead of reading as removed; taking a")
print("contribution out is a separate act that touches only that agent's")
print("documents and reports what went and what was kept because other")
print("material supports it; and an agent with no documents, or a document")
print("list that cannot be read at all, removes nothing rather than treating")
print("an empty list as 'everything'.")
