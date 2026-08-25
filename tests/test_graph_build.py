"""Starting a concept-map build, and everything that must not happen meanwhile.

The build reads a course's whole corpus and has a strong model draft the
concepts and their prerequisites. It takes minutes to hours, so it cannot
happen inside the request that starts it: the Content Admin runs one
synchronous worker behind a 120-second gunicorn timeout. It runs as an n8n
workflow — that is where long, retrying, many-step work lives in this stack —
and reports back through a token-protected endpoint.

Which means the interesting parts are not the happy path but the seams:

  * a second click must not buy a second pass over the same corpus;
  * a workflow that cannot be reached must not leave a build sitting in the
    table for ever, blocking every later attempt;
  * a proposal is checked when it arrives, not when somebody opens the
    review — a build that "succeeded" and produced nonsense should fail while
    the workflow is still there to say why;
  * and after all of it, nothing reaches Neo4j until a person submits the
    review. That gap is the feature.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402

os.environ["SMARTRAG_ENV_PATH"] = str(dbfixture.tmp_env('INGEST_STATUS_TOKEN="tok"\n'))
os.environ["SMARTRAG_INGEST_STATUS_PATH"] = tempfile.mkstemp()[1]
os.environ.setdefault("CONTENT_ADMIN_SESSION_SECRET", "test-secret")
os.environ.setdefault("SMARTRAG_TEMPLATES_DIR", "flowise/agents")

db, course = dbfixture.require_database()

import accounts  # noqa: E402
import app as flask_app  # noqa: E402
import graph_builds as gb  # noqa: E402
import n8n_client  # noqa: E402
import neo4j_client  # noqa: E402
import storage  # noqa: E402
import weaviate_client  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


CID = course["id"]
HEAD = {"X-Ingest-Token": "tok"}
DOCS = [{"source_title": "A", "source_file": "agent_1/a.md", "agent_id": 1, "chunks": 3}]
GOOD = {"concepts": [{"name": "A", "sources": ["agent_1/a.md"]},
                     {"name": "B", "sources": ["agent_1/a.md"]}],
        "prerequisites": [{"before": "A", "after": "B",
                           "sources": ["agent_1/a.md"]}]}

sent: list = []
written: list = []

weaviate_client.WeaviateClient.list_documents = lambda self, c, cid: list(DOCS)
neo4j_client.Neo4jClient.by_source = lambda self, cid: {}
for _n, _f in (("concepts", lambda self, cid, **k: []),
               ("edges", lambda self, cid: []),
               ("counts", lambda self, cid: {"concepts": 0, "edges": 0}),
               ("unassigned_count", lambda self: 0)):
    setattr(neo4j_client.Neo4jClient, _n, _f)
neo4j_client.Neo4jClient.apply_proposal = lambda self, cid, c, e, build_id="": (
    written.append((len(c), len(e), build_id)),
    {"concepts": len(c), "edges": len(e)})[1]
n8n_client.N8nClient.start_graph_build = lambda self, p: sent.append(p)


def reset():
    """Back to a known scope: agent 1 in, everything else out.

    Every slot, not only the two this file creates. The course fixture is
    shared, another suite leaves a third configured agent behind, and the
    first version of this reset left it ticked — so the scope assertions
    passed alone and failed in a full run, which is the worst way for a test
    to be wrong.
    """
    gb.forget_course(CID)
    sent.clear()
    written.clear()
    storage.save_slot(CID, 1, "tutor", {}, "Eins", "p")
    storage.save_slot(CID, 2, "tutor", {}, "Zwei", "p")
    for num, data in storage.all_slots(CID).items():
        if data:
            storage.set_in_graph(CID, int(num), num == "1")
    n8n_client.N8nClient.start_graph_build = lambda self, p: sent.append(p)


client = flask_app.app.test_client()
with client.session_transaction() as sess:
    user = (accounts.get_by_username("buildtest")
            or accounts.create_account("buildtest", "a-strong-test-password",
                                       role=accounts.ROLE_ADMIN))
    sess["user_id"] = user["id"]
    sess["logged_in"] = True
    sess["course_id"] = CID

# ─── Starting ───────────────────────────────────────────────────────────────
reset()
page = client.post("/graph-guidance", data={"action": "build"}).get_data(as_text=True)
check("the workflow is asked to start", len(sent) == 1, sent)
check("and only the ticked agents are in scope",
      sent and sent[0]["agents"] == [1], sent)
check("the build carries what it needs to report back",
      sent and {"build_id", "course_id", "collection"} <= set(sent[0]), sent)

build = gb.active(CID)
check("a build is recorded before the workflow is called",
      build is not None and build["state"] == "queued", build)
check("with the scope it was started with",
      build and build["scope"] == [1],
      "the question asked about a proposal later is what it was built from, "
      "and by then the checkboxes may say something else")

# ─── A second click ─────────────────────────────────────────────────────────
page = client.post("/graph-guidance", data={"action": "build"}).get_data(as_text=True)
check("a second click does not start a second run", len(sent) == 1, sent)
check("and is refused in a sentence rather than a 500",
      "already running" in page.lower(),
      "the guard against paying twice reaches nobody if it raises")

# ─── A workflow that cannot be reached ──────────────────────────────────────
reset()


def _unreachable(self, payload):
    raise RuntimeError("connection refused")


n8n_client.N8nClient.start_graph_build = _unreachable
page = client.post("/graph-guidance", data={"action": "build"}).get_data(as_text=True)
check("an unreachable workflow is reported", "could not be started" in page.lower()
      or "konnte nicht" in page.lower(), page[-400:])
check("and leaves no build blocking the next attempt", gb.active(CID) is None,
      "a queued build nobody is working on would block this course for ever "
      "through the one-active index")

# ─── Nothing ticked ─────────────────────────────────────────────────────────
reset()
storage.set_in_graph(CID, 1, False)
page = client.post("/graph-guidance", data={"action": "build"}).get_data(as_text=True)
check("a course with no agent ticked is refused", not sent, sent)
check("and told why", "no material" in page.lower() or "no agent" in page.lower(),
      page[-300:])

# ─── Reporting back ─────────────────────────────────────────────────────────
reset()
client.post("/graph-guidance", data={"action": "build"})
build = gb.active(CID)

check("an unauthenticated callback is refused",
      client.post("/api/graph-build",
                  json={"build_id": build["id"], "state": "running"}).status_code == 401,
      "this endpoint is reachable from anywhere on the Docker network")
check("an unknown build is a 404",
      client.post("/api/graph-build", json={"build_id": "gb-nope", "state": "running"},
                  headers=HEAD).status_code == 404, "")
check("a state nobody defined is a 400",
      client.post("/api/graph-build", json={"build_id": build["id"], "state": "x"},
                  headers=HEAD).status_code == 400,
      "a mistyped field must not be swallowed — the build would run for ever")

client.post("/api/graph-build",
            json={"build_id": build["id"], "state": "running",
                  "stats": {"documents": 1}}, headers=HEAD)
page = client.get("/graph-guidance").get_data(as_text=True)
check("a running build is visible on the page",
      "build is running" in page.lower(), "")
check("and the start button is gone while it runs",
      'value="build"' not in page, "")

# ─── The proposal is checked on arrival ─────────────────────────────────────
BAD = {"concepts": [{"name": "A", "sources": ["agent_1/a.md"]},
                    {"name": "B", "sources": ["agent_1/a.md"]}],
       "prerequisites": [{"before": "A", "after": "B",
                          "sources": ["nie-hochgeladen.pdf"]}]}
resp = client.post("/api/graph-build",
                   json={"build_id": build["id"], "state": "proposed",
                         "proposal": BAD}, headers=HEAD)
check("a proposal citing material that does not exist is refused",
      resp.status_code == 422, resp.status_code)
after = gb.get(build["id"])
check("the build fails rather than sitting there",
      after["state"] == "failed", after["state"])
check("and the reason is kept for the operator",
      "nie-hochgeladen.pdf" in (after["error"] or ""), after["error"])
check("nothing was written", written == [], written)

# ─── The good path, and the gap that matters ────────────────────────────────
reset()
client.post("/graph-guidance", data={"action": "build"})
build = gb.active(CID)
resp = client.post("/api/graph-build",
                   json={"build_id": build["id"], "state": "proposed",
                         "proposal": GOOD}, headers=HEAD)
check("a good proposal is accepted", resp.status_code == 200, resp.get_json())
check("and counted back to the workflow",
      resp.get_json().get("concepts") == 2, resp.get_json())
check("storing a proposal writes nothing to the graph", written == [],
      "this gap is the whole safety of the feature")

page = client.get("/graph-guidance").get_data(as_text=True)
unescaped = page.replace("&#34;", '"')
check("the proposal is waiting in the review box",
      '"name": "A"' in unescaped, "")
check("with its provenance intact for the reviewer to check",
      "agent_1/a.md" in unescaped,
      "a citation the reviewer cannot see is one they cannot check")
check("and the page says what it was drafted from",
      "drafted from" in page.lower() or "entworfen aus" in page.lower(), "")

# A late duplicate delivery must not overwrite what is being read.
client.post("/api/graph-build",
            json={"build_id": build["id"], "state": "proposed",
                  "proposal": {"concepts": [{"name": "SPAETER",
                                             "sources": ["agent_1/a.md"]}],
                               "prerequisites": []}}, headers=HEAD)
stored = gb.get(build["id"])["proposal"]
check("a second delivery replaces the first while it is still under review",
      stored["concepts"][0]["name"] in ("A", "SPAETER"),
      "either is defensible; what must not happen is a crash or a mix")

# ─── Applying ───────────────────────────────────────────────────────────────
client.post("/graph-guidance", data={"action": "apply",
                                     "proposal": json.dumps(GOOD)})
check("applying writes the proposal", written and written[0][:2] == (2, 1), written)
check("and stamps it with the build that produced it",
      written and written[0][2] == build["id"],
      "without the build id on every concept and edge, a build cannot be "
      "undone afterwards")
check("the build is recorded as applied",
      gb.get(build["id"])["state"] == "applied", "")

page = client.get("/graph-guidance").get_data(as_text=True)
check("and the review box is empty again",
      '"name": "A"' not in page.replace("&#34;", '"'),
      "a proposal that keeps reappearing after it was applied gets applied "
      "twice")

# A callback arriving after the review was acted on must not undo it. n8n
# retries, and a delivery duplicated after the operator applied the proposal
# would otherwise reopen a finished build and offer the same concepts again.
client.post("/api/graph-build",
            json={"build_id": build["id"], "state": "proposed",
                  "proposal": {"concepts": [{"name": "ZU-SPAET",
                                             "sources": ["agent_1/a.md"]}],
                               "prerequisites": []}}, headers=HEAD)
done = gb.get(build["id"])
check("a callback after applying does not reopen the build",
      done["state"] == "applied", done["state"])
check("and does not replace what was applied",
      done["proposal"]["concepts"][0]["name"] != "ZU-SPAET",
      done["proposal"])
client.post("/api/graph-build",
            json={"build_id": build["id"], "state": "failed",
                  "error": "spaeter Fehlschlag"}, headers=HEAD)
check("nor can a late failure undo it",
      gb.get(build["id"])["state"] == "applied", "")

# The one-active guard is an index, not a check-then-insert: two clicks a
# second apart both pass a check. The database has to be the one refusing,
# and only the migration says so.
migration = (Path(flask_app.__file__).resolve().parent / "migrations"
             / "004_graph_scope_and_builds.sql").read_text()
check("one build per course is enforced by a unique index",
      "CREATE UNIQUE INDEX IF NOT EXISTS graph_builds_one_active_idx" in migration,
      "a plain index enforces nothing, and the refusal would depend on "
      "timing")
check("and only for builds that are still going",
      "WHERE state IN ('queued', 'running')" in migration,
      "without the predicate a course could never be built a second time")

gb.forget_course(CID)

if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("All graph-build checks passed: starting records the build with the")
print("scope it ran with before the workflow is called and sends only the")
print("ticked agents; a second click is refused in a sentence rather than")
print("buying a second pass; an unreachable workflow is reported and leaves no")
print("build blocking the course; a course with nothing ticked is refused with")
print("a reason. The callback rejects an unauthenticated caller, an unknown")
print("build and an undefined state, shows a running build and hides the start")
print("button, and checks a proposal on arrival — one citing material the")
print("course does not hold fails the build with the reason kept, rather than")
print("waiting to surprise the reviewer. A good proposal is stored and")
print("displayed with its provenance, and writes nothing; only submitting the")
print("review writes, stamped with the build id that makes it undoable, after")
print("which the build counts as applied and the box no longer offers it again.")
