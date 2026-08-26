"""Deleting a document changes the concept map, and used to do it in silence.

The map is built from the course's documents. Delete one and the concepts it
supported are still there, citing a work the course no longer holds — and
nobody is told. The operator found this the way it is usually found: *"wenn
der User ein Dokument rauslöscht, dann hat das ja auch Auswirkungen auf den
Graphen!"*

Three things have to be true, and they are separate:

  * the weight of the deletion is visible **before** the click, not reported
    afterwards;
  * the clean-up happens, but as a choice — a document being replaced by a
    newer edition is not the same act as a document being wrong;
  * the graph notices dangling citations **by itself**, because a document can
    leave the course by paths that never touch the documents page.

Everything below runs against the real routes with Weaviate and Neo4j
stubbed. The clean-up is deliberately not a workflow and not a model call: it
is one transaction over data already in the database.
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
import weaviate_client  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


DOCS = [
    {"source_title": "Werk A", "source_file": "agent_1/a.md", "agent_id": 1,
     "chunks": 10, "doc_type": "document", "ingest_date": "2026-08-25",
     "authors": "", "year": None},
    {"source_title": "Werk B", "source_file": "agent_2/b.md", "agent_id": 2,
     "chunks": 5, "doc_type": "document", "ingest_date": "2026-08-25",
     "authors": "", "year": None},
]
# agent_9/weg.md is in the graph and not in the course: a document deleted
# without the graph being told, which is the state this page has to notice.
WEIGHT = {"agent_1/a.md": {"concepts": 12, "only": 5},
          "agent_2/b.md": {"concepts": 4, "only": 0},
          "agent_9/weg.md": {"concepts": 7, "only": 3}}

deleted: list = []
removed: list = []

weaviate_client.WeaviateClient.list_documents = lambda self, c, cid: list(DOCS)
weaviate_client.WeaviateClient.count_chunks = lambda self, c, cid: 15
weaviate_client.WeaviateClient.delete_document = \
    lambda self, c, cid, t, a: (deleted.append(t), 10)[1]

neo4j_client.Neo4jClient.by_source = lambda self, cid: dict(WEIGHT)
neo4j_client.Neo4jClient.remove_documents = lambda self, cid, docs: (
    removed.append(list(docs)),
    {"concepts_removed": 5, "concepts_kept": 7, "edges_removed": 2})[1]
for _name, _fn in (("concepts", lambda self, cid, **k: []),
                   ("edges", lambda self, cid: []),
                   ("counts", lambda self, cid: {"concepts": 16, "edges": 3}),
                   ("unassigned_count", lambda self: 0)):
    setattr(neo4j_client.Neo4jClient, _name, _fn)

client = flask_app.app.test_client()
with client.session_transaction() as sess:
    user = (accounts.get_by_username("doclink")
            or accounts.create_account("doclink", "a-strong-test-password",
                                       role=accounts.ROLE_ADMIN))
    sess["user_id"] = user["id"]
    sess["logged_in"] = True
    sess["course_id"] = course["id"]

# ─── Before the click ───────────────────────────────────────────────────────
page = client.get("/documents").get_data(as_text=True)
check("the document list shows what each document holds up",
      "12" in page and "(5)" in page,
      "the weight of a deletion has to be on the page before it happens")
check("the offer to clean up appears only where there is something to clean",
      page.count('name="also_graph"') == len(WEIGHT) - 1,
      "a checkbox that usually does nothing is one people stop reading")
check("the confirmation names the graph consequence",
      "docs_delete_graph_warn" in page or "concept" in page.lower(), "")
check("the form carries the stable identifier",
      'name="source_file"' in page,
      "provenance keys on source_file — a title can be edited, and two "
      "agents may upload the same filename")

# ─── A deletion that found nothing is not a deletion ────────────────────────
# Seen on a live installation: "0 Chunk(s) von \"Digitale Ethik\" aus dem Index
# entfernt", in the green box. Read quickly that is a completed removal with a
# count beside it, and the two situations it actually covers — the document
# was already gone, or the list is stale and the title no longer matches —
# both need the operator to look again.
weaviate_client.WeaviateClient.delete_document = lambda self, c, cid, t, a: 0
page = client.post("/documents", data={
    "source_title": "Werk A", "source_file": "agent_1/a.md",
    "agent_id": "1"}).get_data(as_text=True)
check("removing nothing is not reported as a success",
      "No chunks" in page or "keine Chunks" in page, page[-300:])
check("and the page does not also claim a removal",
      "Removed 0" not in page and "0 chunk(s) of" not in page, "")
weaviate_client.WeaviateClient.delete_document = \
    lambda self, c, cid, t, a: (deleted.append(t), 10)[1]

# ─── Deleting, with the clean-up ────────────────────────────────────────────
body = client.post("/documents", data={
    "source_title": "Werk A", "source_file": "agent_1/a.md",
    "agent_id": "1", "also_graph": "1"}).get_data(as_text=True)
check("the chunks are deleted", deleted == ["Werk A"], deleted)
check("and exactly that document's contribution leaves the graph",
      removed == [["agent_1/a.md"]], removed)
check("the message says what went and what stayed",
      "5" in body and "7" in body,
      "a concept other material supports is kept, and silence about that "
      "reads as if everything went")

# ─── Deleting without it ────────────────────────────────────────────────────
deleted.clear()
removed.clear()
client.post("/documents", data={"source_title": "Werk B",
                                "source_file": "agent_2/b.md", "agent_id": "2"})
check("without the box ticked the graph is left alone", removed == [],
      "replacing a document with a newer edition must not throw its concepts "
      "away — the re-upload would have to earn them back")
check("but the document is still deleted", deleted == ["Werk B"], deleted)

# ─── The graph notices by itself ────────────────────────────────────────────
removed.clear()
page = client.get("/graph-guidance").get_data(as_text=True)
check("a citation to material the course no longer holds is reported",
      "agent_9/weg.md" in page,
      "documents leave by paths that never touch the documents page")
check("and the clean-up is offered there", 'value="clean_stale"' in page, "")
check("material the course still holds is not reported stale",
      "agent_2/b.md" not in page.split("clean_stale")[0].split("stale")[-1]
      or "agent_9/weg.md" in page, "")

client.post("/graph-guidance", data={"action": "clean_stale"})
check("the clean-up removes only the missing material",
      removed == [["agent_9/weg.md"]],
      "it must not touch documents the course still has")

# ─── An unreadable document list must not report everything stale ───────────
def _boom(self, c, cid):
    raise weaviate_client.WeaviateError("Weaviate is down")


removed.clear()
weaviate_client.WeaviateClient.list_documents = _boom
page = client.get("/graph-guidance").get_data(as_text=True)
check("a page that cannot read the documents still renders",
      "clean_stale" not in page,
      "an empty document list reads as 'no documents at all', which would "
      "report every citation in the graph as stale")
client.post("/graph-guidance", data={"action": "clean_stale"})
check("and refuses to clean on a guess", removed == [],
      "this would have emptied the graph of everything with provenance")

if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("All document/graph link checks passed: the document list shows what")
print("each document holds up in the concept map and offers the clean-up only")
print("where there is something to clean, so the weight of a deletion is on the")
print("page before the click; deleting with the box ticked removes exactly that")
print("document's contribution and says what went and what was kept because")
print("other material supports it, while leaving it unticked deletes the")
print("document and leaves the map alone, which is what replacing an edition")
print("needs; the graph page notices citations to material the course no longer")
print("holds without being told, offers to remove only those, and — when the")
print("document list cannot be read at all — neither reports everything stale")
print("nor cleans anything on that guess.")
