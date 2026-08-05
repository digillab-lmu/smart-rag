import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
env_path.write_text(
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
    'DOMAIN="example.com"\n'
    'LLM_PROVIDER="anthropic"\n'
    'LLM_MODEL_STRONG="claude-sonnet-4-5"\n'
    'LLM_MODEL_FAST="claude-haiku-4-5"\n'
    'LLM_API_KEY="sk-test"\n'
    'EMBEDDING_PROVIDER="openai"\n'
    'EMBEDDING_MODEL="text-embedding-3-small"\n'
    'EMBEDDING_API_KEY="sk-embed-test"\n'
    'COURSE_NAME="Testkurs"\n'
    'WEAVIATE_COLLECTION_NAME="TestChunks"\n'
    'WEAVIATE_API_KEY="wv-test"\n'
    'NEO4J_PASSWORD="neo4j-test"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

import app as flask_app_module  # noqa: E402
import storage  # noqa: E402

client = flask_app_module.app.test_client()
failures = []

def check(name, resp, expected_status, contains=None, not_contains=None):
    if resp.status_code != expected_status:
        failures.append(f"{name}: status {resp.status_code}, expected {expected_status}")
        return
    body = resp.get_data(as_text=True)
    if contains:
        for c in contains:
            if c not in body:
                failures.append(f"{name}: expected text {c!r} not found")
    if not_contains:
        for c in not_contains:
            if c in body:
                failures.append(f"{name}: unexpected text {c!r} found")

# 1. Not configured yet -> /setup should render, /login should redirect to /setup
check("GET /setup (unconfigured)", client.get("/setup"), 200, ["create your admin account"])
check("GET /login (unconfigured) redirects", client.get("/login", follow_redirects=False), 302)
check("GET / (unconfigured) redirects to login->setup", client.get("/", follow_redirects=True), 200, ["create your admin account"])

# 2. Create the admin account
resp = client.post("/setup", data={
    "username": "admin", "password": "a-strong-test-password", "confirm": "a-strong-test-password",
}, follow_redirects=True)
check("POST /setup creates account", resp, 200, ["Connect to Flowise"])

# 3. Now logged in (session set on setup) -> dashboard reachable
check("GET / (logged in)", client.get("/"), 200, ["Agents (up to 10)"])

# 4. Log out, log back in
client.get("/logout")
check("GET / after logout redirects", client.get("/", follow_redirects=True), 200, ["Content Admin"])
resp = client.post("/login", data={"username": "admin", "password": "a-strong-test-password"}, follow_redirects=True)
check("POST /login works", resp, 200, ["Agents (up to 10)"])

resp = client.post("/login", data={"username": "admin", "password": "WRONG"}, follow_redirects=True)
check("POST /login wrong password rejected", resp, 200, ["Invalid username or password"])
# must still be logged out
check("GET / still redirects after failed login", client.get("/", follow_redirects=True), 200, ["Content Admin"])
# log back in for the rest of the test
client.post("/login", data={"username": "admin", "password": "a-strong-test-password"})

# 5. Flowise setup page renders (no network call yet since we don't submit)
check("GET /flowise-setup", client.get("/flowise-setup"), 200, ["Connect to Flowise"])

# 6. Slot pages for all 10 slots: archetype picker renders
for slot in range(1, 11):
    check(f"GET /slot/{slot}", client.get(f"/slot/{slot}"), 200, ["Choose an archetype"])
check("GET /slot/11 (invalid)", client.get("/slot/11"), 404)
check("GET /slot/0 (invalid)", client.get("/slot/0"), 404)

# 7. Choose archetype for slot 4 (topic template), then check the content form renders
# with every expected placeholder field, the Agent Name field, field-help text, and
# WITHOUT the auto-filled fields (Agent Number, Course Name, Weaviate Collection Name,
# Embedding Model) that placeholders_for() must now exclude.
resp = client.post("/slot/4", data={"action": "choose_archetype", "archetype": "agent-topic-template.json"})
check("POST choose_archetype shows real content fields + help + name field", resp, 200, [
    "Topic Name", "Topic Subtopics", "Concept List", "Student Role",
    "Agent Name",
    "4.1 The Three-Store Model of Memory",  # FIELD_EXAMPLES placeholder text for TOPIC_SUBTOPICS
], not_contains=[
    "Agent Number", "Course Name", "Weaviate Collection Name", "Embedding Model",
])

# 8. Save content without a name -> must be rejected, form re-shows what was typed
resp = client.post("/slot/4", data={
    "archetype": "agent-topic-template.json",
    "action": "save",
    "TOPIC_NAME": "Kognitive Lernvoraussetzungen",
    "TOPIC_SUBTOPICS": "4.1 Drei-Speicher-Modell",
    "TOPIC_KNOWLEDGE_DESCRIPTION": "desc",
    "STUDENT_ROLE": "Lehramtsstudierende/r",
    "CONCEPT_LIST": "Drei-Speicher-Modell",
    "RESPONSE_LANGUAGE_RULE": "Antworte auf Deutsch",
}, follow_redirects=True)
check("POST save without name rejected, content preserved", resp, 200, [
    "Please give this agent a name", "Kognitive Lernvoraussetzungen",
])

# 9. Save content with a name -> succeeds
resp = client.post("/slot/4", data={
    "archetype": "agent-topic-template.json",
    "action": "save",
    "name": "Chapter 4 Tutor",
    "TOPIC_NAME": "Kognitive Lernvoraussetzungen",
    "TOPIC_SUBTOPICS": "4.1 Drei-Speicher-Modell",
    "TOPIC_KNOWLEDGE_DESCRIPTION": "desc",
    "STUDENT_ROLE": "Lehramtsstudierende/r",
    "CONCEPT_LIST": "Drei-Speicher-Modell",
    "RESPONSE_LANGUAGE_RULE": "Antworte auf Deutsch",
}, follow_redirects=True)
check("POST save with name succeeds", resp, 200, ["Kognitive Lernvoraussetzungen", "Chapter 4 Tutor"])

# 10. Dashboard now shows slot 4 as "Content saved, not imported" with its name
resp = client.get("/")
body = resp.get_data(as_text=True)
if "Content saved, not imported" not in body:
    failures.append("dashboard doesn't show slot 4 as content-saved")
if "Chapter 4 Tutor" not in body:
    failures.append("dashboard doesn't show slot 4's agent name")

# 11. Choose archetype for slot 5, try to save with the SAME name as slot 4 -> rejected
client.post("/slot/5", data={"action": "choose_archetype", "archetype": "agent-01-universal.json"})
resp = client.post("/slot/5", data={
    "archetype": "agent-01-universal.json",
    "action": "save",
    "name": "chapter 4 tutor",  # different case, must still collide
    "COURSE_KNOWLEDGE_DESCRIPTION": "desc",
    "RESPONSE_LANGUAGE_RULE": "Antworte auf Deutsch",
}, follow_redirects=True)
check("POST save with duplicate name (case-insensitive) rejected", resp, 200, [
    "already used by another agent",
])

# 12. Same slot, different unique name -> succeeds
resp = client.post("/slot/5", data={
    "archetype": "agent-01-universal.json",
    "action": "save",
    "name": "General Helper",
    "COURSE_KNOWLEDGE_DESCRIPTION": "desc",
    "RESPONSE_LANGUAGE_RULE": "Antworte auf Deutsch",
}, follow_redirects=True)
check("POST save with unique name succeeds", resp, 200, ["General Helper"])

# 13. Re-saving slot 4 with its OWN existing name must not be rejected as a duplicate.
resp = client.post("/slot/4", data={
    "archetype": "agent-topic-template.json",
    "action": "save",
    "name": "Chapter 4 Tutor",
    "TOPIC_NAME": "Kognitive Lernvoraussetzungen (updated)",
    "TOPIC_SUBTOPICS": "4.1 Drei-Speicher-Modell",
    "TOPIC_KNOWLEDGE_DESCRIPTION": "desc",
    "STUDENT_ROLE": "Lehramtsstudierende/r",
    "CONCEPT_LIST": "Drei-Speicher-Modell",
    "RESPONSE_LANGUAGE_RULE": "Antworte auf Deutsch",
}, follow_redirects=True)
check("POST re-save slot 4 with its own unchanged name is not rejected", resp, 200, [
    "Kognitive Lernvoraussetzungen (updated)",
], not_contains=["already used by another agent"])

# 14. storage.name_taken() direct checks
if not storage.name_taken("Chapter 4 Tutor", exclude_slot=5):
    failures.append("name_taken() should report True for slot 5 checking slot 4's name")
if storage.name_taken("Chapter 4 Tutor", exclude_slot=4):
    failures.append("name_taken() should report False when excluding the slot that owns the name")
if storage.name_taken("Some Totally Unused Name", exclude_slot=1):
    failures.append("name_taken() should report False for an unused name")

# 15. Graph guidance page renders with the prompt template visible
check("GET /graph-guidance", client.get("/graph-guidance"), 200, [
    "PREREQUISITE_FOR", "MERGE (t:Topic", "Run against Neo4j",
])

# 16. /slot/<n>/optimize route: unknown field rejected without calling the LLM
resp = client.post("/slot/4/optimize", json={"field": "NOT_A_REAL_FIELD", "text": "x"})
check("POST optimize with unknown field rejected", resp, 400, ["Unknown field"])

# 17. Known field: mock optimize_field so no real network/API call happens,
# and verify the route wires args through correctly and returns its result.
captured_args = {}


def fake_optimize_field(field, field_purpose, text, env, language="en"):
    captured_args["field"] = field
    captured_args["field_purpose"] = field_purpose
    captured_args["text"] = text
    captured_args["env_has_llm_provider"] = "LLM_PROVIDER" in env
    captured_args["language"] = language
    return {"suggestion": "A much better topic name.", "rationale": "Made it more specific."}


flask_app_module.optimize_field = fake_optimize_field
resp = client.post("/slot/4/optimize", json={"field": "TOPIC_NAME", "text": "draft topic name"})
check("POST optimize with known field succeeds", resp, 200, ["A much better topic name.", "Made it more specific."])
if captured_args.get("field") != "TOPIC_NAME":
    failures.append(f"optimize route passed wrong field to optimize_field: {captured_args.get('field')!r}")
if captured_args.get("text") != "draft topic name":
    failures.append(f"optimize route passed wrong text to optimize_field: {captured_args.get('text')!r}")
if not captured_args.get("env_has_llm_provider"):
    failures.append("optimize route did not pass the full env dict to optimize_field")

# 18. LLMError from optimize_field surfaces as a 502 with the error message
from llm_client import LLMError  # noqa: E402


def failing_optimize_field(field, field_purpose, text, env, language="en"):
    raise LLMError("No LLM API key configured (LLM_API_KEY is empty in .env).")


flask_app_module.optimize_field = failing_optimize_field
resp = client.post("/slot/4/optimize", json={"field": "TOPIC_NAME", "text": "draft"})
check("POST optimize surfaces LLMError as 502", resp, 502, ["No LLM API key configured"])

# 19. Route requires login
client.get("/logout")
resp = client.post("/slot/4/optimize", json={"field": "TOPIC_NAME", "text": "draft"}, follow_redirects=False)
check("POST optimize while logged out redirects to login", resp, 302)
client.post("/login", data={"username": "admin", "password": "a-strong-test-password"})

# ── Document upload (/upload) ──────────────────────────────────────────────
import io  # noqa: E402
from n8n_client import N8nError  # noqa: E402

# 20. GET renders, and only lists slots that actually have an agent configured
# (slots 4 and 5 were configured earlier in this test; the other 8 were not).
resp = client.get("/upload")
check("GET /upload renders", resp, 200, [
    "Upload Course Documents", "Chapter 4 Tutor", "General Helper",
])
body = resp.get_data(as_text=True)
for empty_slot in ("Slot 2 —", "Slot 7 —"):
    if empty_slot in body:
        failures.append(f"/upload offers unconfigured {empty_slot!r} as a target")

# 21. Validation: no file chosen
resp = client.post("/upload", data={"slot": "4", "title": "T"}, follow_redirects=True)
check("POST /upload without a file rejected", resp, 200, ["Please choose a file to upload"])

# 22. Validation: no slot chosen
resp = client.post("/upload", data={
    "title": "T",
    "document": (io.BytesIO(b"%PDF-1.4"), "a.pdf"),
}, content_type="multipart/form-data", follow_redirects=True)
check("POST /upload without a slot rejected", resp, 200, ["choose which agent"])

# 23. Validation: unconfigured slot rejected (slot 7 has no archetype)
resp = client.post("/upload", data={
    "slot": "7",
    "title": "T",
    "document": (io.BytesIO(b"%PDF-1.4"), "a.pdf"),
}, content_type="multipart/form-data", follow_redirects=True)
check("POST /upload to an unconfigured slot rejected", resp, 200, ["choose which agent"])

# 24. Validation: unsupported file type
resp = client.post("/upload", data={
    "slot": "4",
    "title": "T",
    "document": (io.BytesIO(b"MZ\x90"), "malware.exe"),
}, content_type="multipart/form-data", follow_redirects=True)
check("POST /upload with unsupported extension rejected", resp, 200, ["a supported format"])

# 25. Validation: non-numeric year
resp = client.post("/upload", data={
    "slot": "4",
    "title": "T",
    "year": "zweitausendneun",
    "document": (io.BytesIO(b"%PDF-1.4"), "a.pdf"),
}, content_type="multipart/form-data", follow_redirects=True)
check("POST /upload with non-numeric year rejected", resp, 200, ["Year must be a number"])

# 26. Happy path — n8n client mocked out, no real network call.
captured_upload = {}


class FakeN8nClient:
    def upload_document(self, **kwargs):
        captured_upload.update(kwargs)


flask_app_module._n8n_client = lambda: FakeN8nClient()
resp = client.post("/upload", data={
    "slot": "4",
    "title": "Chapter 4: Cognitive Load",
    "authors": "Mayer, Richard E.",
    "year": "2009",
    "topic": "Cognitive Load, Multimedia",
    "language": "en",
    "force_ocr": "on",
    "notify_email": "dozent@example.com",
    "document": (io.BytesIO(b"%PDF-1.4 fake"), "chapter4.pdf"),
}, content_type="multipart/form-data", follow_redirects=True)
check("POST /upload happy path succeeds", resp, 200, [
    "chapter4.pdf", "Chapter 4 Tutor", "email when it&#39;s searchable",
])
if captured_upload.get("agent_id") != 4:
    failures.append(f"upload passed wrong agent_id: {captured_upload.get('agent_id')!r} (want int 4)")
if captured_upload.get("filename") != "chapter4.pdf":
    failures.append(f"upload passed wrong filename: {captured_upload.get('filename')!r}")
if captured_upload.get("title") != "Chapter 4: Cognitive Load":
    failures.append(f"upload passed wrong title: {captured_upload.get('title')!r}")
if captured_upload.get("force_ocr") is not True:
    failures.append(f"force_ocr checkbox not mapped to True: {captured_upload.get('force_ocr')!r}")
if captured_upload.get("language") != "en":
    failures.append(f"upload passed wrong language: {captured_upload.get('language')!r}")
if captured_upload.get("notify_email") != "dozent@example.com":
    failures.append(f"upload passed wrong notify_email: {captured_upload.get('notify_email')!r}")

# 27. Title falls back to the filename stem when left empty
captured_upload.clear()
client.post("/upload", data={
    "slot": "4",
    "document": (io.BytesIO(b"%PDF-1.4"), "some-lecture-notes.pdf"),
}, content_type="multipart/form-data", follow_redirects=True)
if captured_upload.get("title") != "some-lecture-notes":
    failures.append(f"empty title didn't fall back to filename stem: {captured_upload.get('title')!r}")

# 28. N8nError surfaces to the operator instead of a 500
class FailingN8nClient:
    def upload_document(self, **kwargs):
        raise N8nError("POST /webhook/document-ingest → HTTP 500: Workflow could not be started!")


flask_app_module._n8n_client = lambda: FailingN8nClient()
resp = client.post("/upload", data={
    "slot": "4",
    "title": "T",
    "document": (io.BytesIO(b"%PDF-1.4"), "a.pdf"),
}, content_type="multipart/form-data", follow_redirects=True)
check("POST /upload surfaces N8nError", resp, 200, [
    "Upload failed", "Workflow could not be started",
])

# 29. Route requires login
client.get("/logout")
check("GET /upload while logged out redirects", client.get("/upload", follow_redirects=False), 302)
client.post("/login", data={"username": "admin", "password": "a-strong-test-password"})

# ── Citation lookup (/upload/lookup) ───────────────────────────────────────
import citation as citation_mod  # noqa: E402

# 30. No identifier at all -> 400
resp = client.post("/upload/lookup", json={})
check("lookup without identifier rejected", resp, 400, ["DOI"])

# 31. DOI path: citation.lookup_doi stubbed, no network touched
lookup_calls = {}


def fake_lookup_doi(value):
    lookup_calls["doi"] = value
    return {"title": "Stub Title", "authors": "Mayer, Richard E.", "year": "2001",
            "topic": "Education", "citation": "Mayer, Richard E. (2001). Stub Title.",
            "source": "Crossref"}


def boom_isbn(value):
    raise AssertionError("ISBN lookup must not run for a DOI")


citation_mod.lookup_doi = fake_lookup_doi
citation_mod.lookup_isbn = boom_isbn
resp = client.post("/upload/lookup", json={"identifier": "10.1037/0022-0663.93.1.187"})
check("DOI lookup succeeds", resp, 200, ["Stub Title", "Crossref"])
if lookup_calls.get("doi") != "10.1037/0022-0663.93.1.187":
    failures.append(f"DOI not passed through: {lookup_calls.get('doi')!r}")

# 32. ISBN path: anything not DOI-shaped routes to the ISBN lookup
def fake_lookup_isbn(value):
    lookup_calls["isbn"] = value
    return {"title": "Stub Book", "authors": "", "year": "2014", "topic": "",
            "citation": "…", "source": "Open Library"}


citation_mod.lookup_doi = lambda v: (_ for _ in ()).throw(AssertionError("DOI lookup must not run"))
citation_mod.lookup_isbn = fake_lookup_isbn
resp = client.post("/upload/lookup", json={"identifier": "978-1-107-03520-1"})
check("ISBN lookup succeeds", resp, 200, ["Stub Book", "Open Library"])
if lookup_calls.get("isbn") != "978-1-107-03520-1":
    failures.append(f"ISBN not passed through: {lookup_calls.get('isbn')!r}")

# 33. Not-found surfaces as 404 with the service's own wording
citation_mod.lookup_isbn = lambda v: (_ for _ in ()).throw(
    citation_mod.CitationNotFound("No record found for ISBN 9781107035201")
)
resp = client.post("/upload/lookup", json={"identifier": "9781107035201"})
check("lookup not-found -> 404", resp, 404, ["No record found"])

# 34. Service failure surfaces as 502, distinct from not-found
citation_mod.lookup_isbn = lambda v: (_ for _ in ()).throw(
    citation_mod.CitationError("Lookup service unreachable: timeout")
)
resp = client.post("/upload/lookup", json={"identifier": "9781107035201"})
check("lookup service error -> 502", resp, 502, ["unreachable"])

# 35. PDF scan path: a file with no identifier in it -> 404, not a crash
citation_mod.scan_pdf = lambda stream, **kw: {}
resp = client.post("/upload/lookup", data={
    "document": (io.BytesIO(b"%PDF-1.4 no identifiers here"), "notes.pdf"),
}, content_type="multipart/form-data")
check("PDF scan with no identifier -> 404", resp, 404, ["No DOI or ISBN found"])

# 36. PDF scan that finds a DOI feeds it straight into the DOI lookup
citation_mod.scan_pdf = lambda stream, **kw: {"doi": "10.3389/fpsyg.2019.02364"}
citation_mod.lookup_doi = fake_lookup_doi
resp = client.post("/upload/lookup", data={
    "document": (io.BytesIO(b"%PDF-1.4 fake"), "paper.pdf"),
}, content_type="multipart/form-data")
check("PDF scan -> DOI lookup", resp, 200, ["Stub Title"])
if lookup_calls.get("doi") != "10.3389/fpsyg.2019.02364":
    failures.append(f"scanned DOI not used: {lookup_calls.get('doi')!r}")

# 37. A scanned ISBN must route to the ISBN lookup even though the shape
# check alone couldn't tell (guards the found_via branch).
citation_mod.scan_pdf = lambda stream, **kw: {"isbn": "9781107035201"}
citation_mod.lookup_isbn = fake_lookup_isbn
citation_mod.lookup_doi = lambda v: (_ for _ in ()).throw(AssertionError("must use ISBN lookup"))
resp = client.post("/upload/lookup", data={
    "document": (io.BytesIO(b"%PDF-1.4 fake"), "book.pdf"),
}, content_type="multipart/form-data")
check("PDF scan -> ISBN lookup", resp, 200, ["Stub Book"])

# 38. Route requires login
client.get("/logout")
check("lookup while logged out redirects", client.post("/upload/lookup", json={"identifier": "x"},
      follow_redirects=False), 302)
client.post("/login", data={"username": "admin", "password": "a-strong-test-password"})

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All app.py smoke tests passed: setup, login/logout, all 10 slots, auto-filled-field "
      "exclusion, field help text, agent-name requirement + uniqueness (incl. case-insensitive "
      "and self-exclusion), content save, dashboard status, graph guidance page, "
      "/slot/<n>/optimize route (unknown-field rejection, arg wiring, LLMError->502, login "
      "requirement), /upload route (configured-slots-only listing, missing-file/missing-slot/"
      "unconfigured-slot/bad-extension/bad-year validation, happy-path arg wiring, title "
      "fallback, N8nError surfacing, login requirement), /upload/lookup route (missing "
      "identifier, DOI vs ISBN routing, PDF-scan paths for both identifier kinds, "
      "not-found vs service-error status codes, login requirement).")
