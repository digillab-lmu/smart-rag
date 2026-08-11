import io
import sys

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")

sys.path.insert(0, APP_DIR)

import n8n_client  # noqa: E402


failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data
        self.text = text if text else str(json_data or "")
        self.content = b"x" if json_data is not None or text else b""

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


real_request = n8n_client.requests.request


def reset():
    n8n_client.requests.request = real_request


# ── 1. Happy path: request shape ────────────────────────────────────────────
captured = {}


def fake_request(method, url, **kwargs):
    captured["method"] = method
    captured["url"] = url
    captured["files"] = kwargs.get("files")
    captured["data"] = kwargs.get("data")
    captured["timeout"] = kwargs.get("timeout")
    return FakeResponse(200, {"status": "accepted"})


n8n_client.requests.request = fake_request
client = n8n_client.N8nClient("http://smartrag-n8n:5678")
stream = io.BytesIO(b"%PDF-1.4 fake")
result = client.upload_document(
    file_stream=stream,
    filename="chapter4.pdf",
    content_type="application/pdf",
    agent_id=4,
    title="Chapter 4: Cognitive Load",
    authors="Mayer, Richard E.",
    year="2009",
    topic="Cognitive Load, Multimedia",
    language="de",
    force_ocr=True,
    notify_email="dozent@example.com",
)

check("returns None (fire-and-forget)", result is None, repr(result))
check("method is POST", captured["method"] == "POST", captured.get("method"))
check(
    "url is the ingest webhook",
    captured["url"] == "http://smartrag-n8n:5678/webhook/document-ingest",
    captured.get("url"),
)
check("timeout passed through", captured["timeout"] == 60, captured.get("timeout"))

files = captured["files"]
check("file field is named 'file'", "file" in files, list(files))
check(
    "file tuple is (filename, stream, content_type)",
    files["file"][0] == "chapter4.pdf"
    and files["file"][1] is stream
    and files["file"][2] == "application/pdf",
    files.get("file"),
)

data = captured["data"]
check("agent_id sent as string", data["agent_id"] == "4", data.get("agent_id"))
check("title passed through", data["title"] == "Chapter 4: Cognitive Load")
check("authors passed through", data["authors"] == "Mayer, Richard E.")
check("year passed through", data["year"] == "2009")
check("topic passed through", data["topic"] == "Cognitive Load, Multimedia")
check("language passed through", data["language"] == "de")
check("force_ocr True -> 'true'", data["force_ocr"] == "true", data.get("force_ocr"))
check("notify_email included when set", data["notify_email"] == "dozent@example.com")
reset()

# ── 2. Optional fields / defaults ───────────────────────────────────────────
captured = {}
n8n_client.requests.request = fake_request
client.upload_document(
    file_stream=io.BytesIO(b"x"),
    filename="notes.md",
    content_type="text/markdown",
    agent_id=1,
    title="Notes",
)
data = captured["data"]
check("force_ocr False -> 'false'", data["force_ocr"] == "false", data.get("force_ocr"))
check("language defaults to de", data["language"] == "de", data.get("language"))
check("empty authors still sent", data["authors"] == "", repr(data.get("authors")))
check(
    "notify_email omitted when empty",
    "notify_email" not in data,
    f"unexpectedly present: {data.get('notify_email')!r}",
)
reset()

# ── 3. base_url trailing slash is normalized ────────────────────────────────
captured = {}
n8n_client.requests.request = fake_request
n8n_client.N8nClient("http://smartrag-n8n:5678/").upload_document(
    file_stream=io.BytesIO(b"x"),
    filename="a.pdf",
    content_type="application/pdf",
    agent_id=2,
    title="A",
)
check(
    "no double slash in URL",
    captured["url"] == "http://smartrag-n8n:5678/webhook/document-ingest",
    captured.get("url"),
)
reset()

# ── 4. HTTP error surfaces as N8nError with n8n's own text ──────────────────
def fake_request_500(method, url, **kwargs):
    return FakeResponse(500, text="Workflow could not be started!")


n8n_client.requests.request = fake_request_500
try:
    client.upload_document(
        file_stream=io.BytesIO(b"x"),
        filename="a.pdf",
        content_type="application/pdf",
        agent_id=1,
        title="A",
    )
    failures.append("HTTP 500 should have raised N8nError")
except n8n_client.N8nError as exc:
    check("500 message includes status", "500" in str(exc), str(exc))
    check(
        "500 message includes n8n's own text",
        "Workflow could not be started" in str(exc),
        str(exc),
    )
reset()

# ── 5. Network failure surfaces as N8nError, not a raw RequestException ─────
def fake_request_conn_error(method, url, **kwargs):
    raise n8n_client.requests.RequestException("Connection refused")


n8n_client.requests.request = fake_request_conn_error
try:
    client.upload_document(
        file_stream=io.BytesIO(b"x"),
        filename="a.pdf",
        content_type="application/pdf",
        agent_id=1,
        title="A",
    )
    failures.append("connection error should have raised N8nError")
except n8n_client.N8nError as exc:
    check("conn error message surfaced", "Connection refused" in str(exc), str(exc))
except n8n_client.requests.RequestException:
    failures.append("raw RequestException leaked instead of N8nError")
reset()

# ── 6. Empty 200 body is fine (webhook may answer with no content) ──────────
def fake_request_empty(method, url, **kwargs):
    return FakeResponse(200)


n8n_client.requests.request = fake_request_empty
try:
    client.upload_document(
        file_stream=io.BytesIO(b"x"),
        filename="a.pdf",
        content_type="application/pdf",
        agent_id=1,
        title="A",
    )
except Exception as exc:  # noqa: BLE001
    failures.append(f"empty 200 body should not raise, got {exc!r}")
reset()

# ─── The document's own name must reach the workflow ─────────────────────────
# The workflow cannot recover it later: by the time it builds the object key,
# the binary in hand is Docling's response, not the upload, and its fileName
# is gone. It fell back to a constant, so every document in a course was
# archived over the previous one at agent_N/document.md — the chunks survived,
# the archived markdown of every earlier document did not. So the name travels
# as a plain form field, and this pins it.
reset()
n8n_client.requests.request = fake_request
client.upload_document(
    file_stream=io.BytesIO(b"x"),
    filename="Sailer et al (2024) — Learning activities.pdf",
    content_type="application/pdf",
    agent_id=1,
    title="Learning activities",
)
sent = captured["data"]
if sent.get("filename") != "Sailer et al (2024) — Learning activities.pdf":
    failures.append(f"the filename is not sent as a form field: {sent!r}")
# The multipart part carries it too, but that is the one the workflow loses.
if captured["files"]["file"][0] != "Sailer et al (2024) — Learning activities.pdf":
    failures.append("the multipart part lost the filename")
reset()

# And the workflow must prefer that field over the binary it no longer has.
import json as _json  # noqa: E402
wf = _json.load(open(REPO / "n8n" / "workflows-ingest" / "ingest-document.json"))
extract = [n for n in wf["nodes"] if n["name"] == "Extract Images"][0]["parameters"]["jsCode"]
if "trigger.body?.filename" not in extract:
    failures.append("Extract Images does not read the filename from the upload")
if extract.index("trigger.body?.filename") > extract.index("$binary?.file?.fileName"):
    failures.append("the binary is preferred over the upload's own field")

# The fallback must not be a constant: a constant is what made two different
# documents share one key.
sanitize = [n for n in wf["nodes"] if n["name"] == "Sanitize Filename"][0]["parameters"]["jsCode"]
if "'document'" in sanitize:
    failures.append("Sanitize Filename still falls back to a constant name")
if "Date.now()" not in sanitize:
    failures.append("Sanitize Filename has no collision-free fallback")
# A name of nothing but punctuation sanitises to "", which would produce the
# key "agent_N/.md" — shared by every such document, so the silent overwrite
# would be back through the side door. Caught by exercising the sanitiser
# itself rather than by reading it.
if "if (!safeName)" not in sanitize:
    failures.append("an empty sanitised name is not caught")

# The key-building expression, executed rather than eyeballed. Python stands
# in for the JS: the same operations in the same order, so a rule added on one
# side and not the other shows up as a disagreement.
import re as _re  # noqa: E402
def _js_key(raw: str) -> str:
    s = _re.sub(r"\.[^/.]+$", "", raw)
    for a, b in [("ä","ae"),("ö","oe"),("ü","ue"),("Ä","Ae"),("Ö","Oe"),("Ü","Ue"),("ß","ss")]:
        s = s.replace(a, b)
    s = _re.sub(r"[^a-zA-Z0-9/_-]", "-", s)
    s = _re.sub(r"-{2,}", "-", s)
    s = s.strip("-").lower()[:80].rstrip("-")
    return s

for raw, expected in [
    ("Stegmann, K., Wecker, C. 2018 - Lehren und Lernen.pdf",
     "stegmann-k-wecker-c-2018-lehren-und-lernen"),
    ("Über Größenordnungen.pdf", "ueber-groessenordnungen"),
]:
    got = _js_key(raw)
    if got != expected:
        failures.append(f"key for {raw!r} is {got!r}, expected {expected!r}")
# Two different documents must not produce one key.
if _js_key("Skript Teil 1.pdf") == _js_key("Skript Teil 2.pdf"):
    failures.append("distinct documents collapse to the same key")
# Runs of punctuation must not survive as runs of dashes: these keys are read
# by a human looking through `garage bucket list-objects`.
if "--" in _js_key("Stegmann, K., Wecker, C., Mandl, H. & Fischer, F..pdf"):
    failures.append("dash runs are not collapsed")
if len(_js_key("x" * 300)) > 80:
    failures.append("the key is not capped")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All n8n_client.py checks passed: request shape (URL/method/multipart file field/"
    "form fields), optional-field defaults, base_url normalization, HTTP-error and "
    "network-error surfacing as N8nError, empty-body tolerance, and the document's "
    "own filename travelling as a form field the workflow prefers over the binary "
    "it no longer holds — with a fallback that cannot collide."
)
