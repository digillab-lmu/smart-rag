"""Listing and removing indexed documents.

Deleting is the point of this page. Without it a mistaken upload is
permanent, a revised edition sits next to its predecessor and both get
retrieved, and — the case that produces wrong answers rather than clutter —
a repurposed agent slot inherits the previous agent's corpus, because the
chunks still carry that agent_id.

The delete filter is where this can go wrong quietly, in two directions:

* Too narrow: nothing is deleted. Annoying, and immediately visible.
* Too wide: another course's or another agent's documents disappear, or
  worse, a request without a course scope removes everything. Nobody sees
  that until it is too late, so the checks below are weighted towards it.

And the two filter formats in this project are easy to confuse. Weaviate's
REST API — what deletion uses — wants the classic where filter, verified in
weaviate/weaviate's openapi-specs/schema.json:

    operands / path (a list) / a TYPED value field (valueText, valueInt)

Flowise's vector-store node goes through the gRPC TypeScript client, whose
filters are the other shape entirely:

    filters / target.property / one untyped value

Swapping them fails silently — the request is accepted and matches nothing.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
Path(tmpdir, ".env").write_text(
    'CONTENT_ADMIN_SESSION_SECRET="t"\nDOMAIN="example.com"\n'
    'COURSE_ID="medienerziehung"\nWEAVIATE_COLLECTION_NAME="Chunks"\n'
    'WEAVIATE_API_KEY="wv-test"\nLLM_PROVIDER="anthropic"\nLLM_API_KEY="sk-t"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(Path(tmpdir, ".env"))
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir, "slots.json"))
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(REPO / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "t"

from markupsafe import escape  # noqa: E402

# ─── A database, because agent slots live in one now ─────────────────────────
# Slots moved out of slots.json into Postgres, so this suite needs a database
# and a course for the slots to belong to. dbfixture arranges both, or exits
# 10 — "could not run" rather than a pass that covered nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402
_db, COURSE = dbfixture.require_database()
dbfixture.clear_slots(_db)
COURSE_ID = COURSE["id"]

import app as m  # noqa: E402
import i18n  # noqa: E402
import storage  # noqa: E402
from weaviate_client import WeaviateClient, WeaviateError  # noqa: E402

failures = []
COURSE = "medienerziehung"
COLL = "Chunks"


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


def ok_response(payload):
    return mock.Mock(ok=True, status_code=200, content=json.dumps(payload).encode(),
                     json=lambda: payload, text=json.dumps(payload))


# ── The delete request's shape ──────────────────────────────────────────────
client = WeaviateClient("http://weaviate:8080", "wv-test")

with mock.patch("weaviate_client.requests.request",
                return_value=ok_response({"results": {"successful": 42, "failed": 0}})) as req:
    removed = client.delete_document(COLL, COURSE, "Kastorff et al. (2022)", 3)
check("returns how many chunks went", removed == 42, removed)

args, kwargs = req.call_args
check("delete uses DELETE", args[0] == "DELETE", args)
check("delete hits the batch endpoint", args[1].endswith("/v1/batch/objects"), args)

body = kwargs["json"]
match = body.get("match", {})
check("the collection is named", match.get("class") == COLL, match)

where = match.get("where", {})
check("REST filters use `operands`, not `filters`",
      "operands" in where and "filters" not in where, sorted(where))
check("the operator is And", where.get("operator") == "And", where.get("operator"))

by_path = {tuple(o.get("path", [])): o for o in where.get("operands", [])}
check("scoped by course", ("course_id",) in by_path, sorted(by_path))
check("scoped by title", ("source_title",) in by_path, sorted(by_path))
check("scoped by agent", ("agent_id",) in by_path, sorted(by_path))

# The typed value fields: text properties want valueText, ints valueInt.
# Getting this wrong is accepted by the API and matches nothing.
course_op = by_path.get(("course_id",), {})
check("course_id compares with valueText",
      course_op.get("valueText") == COURSE, course_op)
agent_op = by_path.get(("agent_id",), {})
check("agent_id compares with valueInt, not valueText",
      agent_op.get("valueInt") == 3 and "valueText" not in agent_op, agent_op)
check("paths are lists, not strings",
      all(isinstance(o.get("path"), list) for o in where.get("operands", [])),
      where.get("operands"))
# The other project's filter vocabulary must not leak in here.
serialized = json.dumps(body)
check("no `target.property` in a REST filter", "target" not in serialized, serialized[:200])

# Without an agent, the filter must still be scoped by course and title —
# and must not silently become "everything with this title".
with mock.patch("weaviate_client.requests.request",
                return_value=ok_response({"results": {"successful": 1, "failed": 0}})) as req:
    client.delete_document(COLL, COURSE, "Some Title", None)
ops = req.call_args[1]["json"]["match"]["where"]["operands"]
paths = {tuple(o["path"]) for o in ops}
check("no agent given: still scoped by course and title",
      paths == {("course_id",), ("source_title",)}, sorted(paths))

# ── Refusals: the wide-blast-radius cases ───────────────────────────────────
for label, call in (
    ("no course id", lambda: client.delete_document(COLL, "", "T", 1)),
    ("no title", lambda: client.delete_document(COLL, COURSE, "", 1)),
    ("no course id for a whole agent", lambda: client.delete_agent_documents(COLL, "", 1)),
):
    with mock.patch("weaviate_client.requests.request") as req:
        try:
            call()
            failures.append(f"{label}: should have refused")
        except WeaviateError:
            check(f"{label}: refuses before sending anything", not req.called,
                  "a request was sent anyway")

# A partial failure reported by Weaviate must surface, not read as success.
with mock.patch("weaviate_client.requests.request",
                return_value=ok_response({"results": {"successful": 3, "failed": 2}})):
    try:
        client.delete_document(COLL, COURSE, "T", 1)
        failures.append("a reported failure count should raise")
    except WeaviateError as exc:
        check("a failed deletion says how many", "2" in str(exc), str(exc))

# Clearing one slot: scoped by course AND agent, never agent alone.
with mock.patch("weaviate_client.requests.request",
                return_value=ok_response({"results": {"successful": 9, "failed": 0}})) as req:
    client.delete_agent_documents(COLL, COURSE, 7)
ops = req.call_args[1]["json"]["match"]["where"]["operands"]
paths = {tuple(o["path"]) for o in ops}
check("clearing a slot stays inside the course", paths == {("course_id",), ("agent_id",)},
      sorted(paths))

# ── Listing ─────────────────────────────────────────────────────────────────
ROWS = [
    {"source_title": "A", "source_file": "a.pdf", "authors": "X", "year": 2022,
     "agent_id": 3, "doc_type": "paper", "ingest_date": "2026-08-01T10:00:00Z"},
    {"source_title": "A", "source_file": "a.pdf", "authors": "X", "year": 2022,
     "agent_id": 3, "doc_type": "paper", "ingest_date": "2026-08-01T10:00:05Z"},
    {"source_title": "B", "source_file": "b.pdf", "authors": "", "year": None,
     "agent_id": 7, "doc_type": "course-material", "ingest_date": "2025-11-02T08:00:00Z"},
]
with mock.patch("weaviate_client.requests.request",
                return_value=ok_response({"data": {"Get": {COLL: ROWS}}})) as req:
    docs = client.list_documents(COLL, COURSE)

check("chunks are grouped into documents", len(docs) == 2, docs)
by_title = {d["source_title"]: d for d in docs}
check("chunk counts are per document", by_title["A"]["chunks"] == 2, by_title["A"])
check("the earliest ingest date wins",
      by_title["A"]["ingest_date"] == "2026-08-01T10:00:00Z", by_title["A"]["ingest_date"])

query = req.call_args[1]["json"]["query"]
check("the listing is scoped to the course", "course_id" in query and COURSE in query, query[:200])

# A quote in the course id would end the GraphQL string literal early.
with mock.patch("weaviate_client.requests.request",
                return_value=ok_response({"data": {"Get": {COLL: []}}})) as req:
    client.list_documents(COLL, 'we"ird\\one')
q = req.call_args[1]["json"]["query"]
check("the course id is escaped into the query", '\\"' in q or '\\\\' in q, q[:200])

# GraphQL answers 200 with an errors array. Treating that as an empty result
# would show "no documents" for a broken query.
with mock.patch("weaviate_client.requests.request",
                return_value=ok_response({"errors": [{"message": "no such property"}]})):
    try:
        client.list_documents(COLL, COURSE)
        failures.append("a GraphQL error should raise, not read as empty")
    except WeaviateError as exc:
        check("a GraphQL error names the reason", "no such property" in str(exc), str(exc))

# ── The page ────────────────────────────────────────────────────────────────
client_http = m.app.test_client()
resp = client_http.get("/documents")
check("the page requires login", resp.status_code in (302, 401), resp.status_code)

client_http.post("/setup", data={"username": "admin", "password": "a-strong-test-password",
                                 "confirm": "a-strong-test-password"}, follow_redirects=True)
storage.save_slot(COURSE_ID, 3, "agent-11-expert-feedback.json", {"EXPERT_DOMAIN": "x"}, "Statistik-Tutor", None)

DOCS = [
    {"source_title": "Kastorff et al. (2022)", "source_file": "k.pdf", "authors": "Kastorff",
     "year": 2022, "agent_id": 3, "doc_type": "paper", "ingest_date": "2026-08-01T10:00:00Z",
     "chunks": 42},
    {"source_title": "Altes Skript", "source_file": "alt.pdf", "authors": "", "year": None,
     "agent_id": 7, "doc_type": "course-material", "ingest_date": "2025-11-02T08:00:00Z",
     "chunks": 9},
]


def fake_weaviate(docs=DOCS, total=51, raises=None):
    fake = mock.Mock()
    if raises:
        fake.list_documents.side_effect = raises
        fake.count_chunks.side_effect = raises
        fake.delete_document.side_effect = raises
    else:
        fake.list_documents.return_value = docs
        fake.count_chunks.return_value = total
        fake.delete_document.return_value = 42
    return fake


with mock.patch.object(m, "_weaviate_client", return_value=fake_weaviate()):
    body = client_http.get("/documents").get_data(as_text=True)
check("documents are listed", "Kastorff" in body, body[:200])
check("a configured slot shows its name", "Statistik-Tutor" in body)
# The row an operator is actually hunting for: documents left behind by a
# slot that was cleared or repurposed.
check("an orphaned agent id is called out",
      str(escape(i18n.t("docs_agent_unknown", 7))) in body, i18n.t("docs_agent_unknown", 7))
check("chunk counts are shown", ">42<" in body.replace(" ", ""), "")
check("deleting asks first", "confirm(" in body)

# Deleting passes exactly the identifying pair through.
fake = fake_weaviate()
with mock.patch.object(m, "_weaviate_client", return_value=fake):
    body = client_http.post("/documents", data={"source_title": "Kastorff et al. (2022)",
                                                "agent_id": "3"},
                            follow_redirects=True).get_data(as_text=True)
args = fake.delete_document.call_args[0]
# Not from the form, and — since courses became runtime objects — not from
# .env either: from the course selected in the header. Reading .env here made
# this page list one fixed collection whichever course was chosen, which is
# how it was noticed: documents from another course, under a course that
# never had them.
check("the collection comes from the selected course",
      args[0] == "TestkursChunks", args)
check("the course id comes from the selected course, not the form",
      args[1] == dbfixture.COURSE_ID, args)
check("the title is passed through", args[2] == "Kastorff et al. (2022)", args)
check("the agent is passed as an int", args[3] == 3, repr(args[3]))
# Jinja escapes the quotes around the title, so compare the escaped form.
check("the result says how many chunks went",
      str(escape(i18n.t("docs_deleted", 42, "Kastorff et al. (2022)"))) in body, body[:300])

# A POST without a title must not turn into a course-wide delete.
fake = fake_weaviate()
with mock.patch.object(m, "_weaviate_client", return_value=fake):
    body = client_http.post("/documents", data={"agent_id": "3"},
                            follow_redirects=True).get_data(as_text=True)
check("a delete without a title is refused", not fake.delete_document.called,
      "a delete was attempted anyway")
check("and says so", str(escape(i18n.t("docs_err_no_title"))) in body, body[:300])

# Failures render the page with the reason rather than a 500.
with mock.patch.object(m, "_weaviate_client",
                       return_value=fake_weaviate(raises=WeaviateError("connection refused"))):
    resp = client_http.get("/documents")
check("an unreachable Weaviate still renders", resp.status_code == 200, resp.status_code)
check("and shows the reason", "connection refused" in resp.get_data(as_text=True))

# A truncated read must be stated, not shown as if it were the whole list.
with mock.patch.object(m, "_weaviate_client", return_value=fake_weaviate(total=5000)):
    body = client_http.get("/documents").get_data(as_text=True)
check("a truncated listing says so", str(escape(i18n.t("docs_truncated"))) in body, "")

with mock.patch.object(m, "_weaviate_client", return_value=fake_weaviate(docs=[], total=0)):
    body = client_http.get("/documents").get_data(as_text=True)
check("an empty course says so", str(escape(i18n.t("docs_empty"))) in body)
check("an empty course isn't called truncated",
      str(escape(i18n.t("docs_truncated"))) not in body)

# Both languages.
for lang in ("en", "de"):
    client_http.get(f"/language/{lang}")
    with mock.patch.object(m, "_weaviate_client", return_value=fake_weaviate()):
        body = client_http.get("/documents").get_data(as_text=True)
    for key in ("docs_heading", "docs_intro", "docs_why_delete", "docs_delete"):
        # Escaped: several of these contain apostrophes or quotes.
        check(f"[{lang}] {key} localised",
              str(escape(i18n.t(key, lang=lang))) in body, i18n.t(key, lang=lang)[:50])
client_http.get("/language/en")

# The internal URL must be the container port, not the host binding — the
# same mix-up that once pointed MinIO's notify webhook at a dead port.
check("Weaviate is reached on its container port",
      m.WEAVIATE_INTERNAL_URL == "http://smartrag-weaviate:8080", m.WEAVIATE_INTERNAL_URL)

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All document-management checks passed: deletion sends Weaviate's REST where filter — "
    "operands, path lists and typed values, with agent_id as valueInt and no gRPC-style "
    "target/filters vocabulary leaking in — always scoped by course as well as title, "
    "refusing before it sends anything when the course or title is missing, and surfacing "
    "a reported failure count instead of reading it as success; clearing a whole slot stays "
    "inside its course; listing groups chunks into documents with counts and the earliest "
    "ingest date, escapes the course id into the GraphQL literal, and raises on a GraphQL "
    "error rather than showing it as an empty index; and the page requires login, names "
    "documents orphaned by a repurposed slot, confirms before deleting, passes only the "
    "identifying pair through with the course taken from .env, refuses a delete with no "
    "title, states a truncated read, renders with an unreachable Weaviate, and is localised "
    "in both languages."
)
