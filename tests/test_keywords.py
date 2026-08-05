"""Covers the keyword-suggestion feature end to end: llm_client.suggest_keywords
(prompt building, parsing, dedup, refusal to guess), the /upload/keywords route,
the session stash that feeds it, and the button's presence in both languages."""
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
env_path = Path(tmpdir) / ".env"
env_path.write_text(
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
    'DOMAIN="example.com"\n'
    'LLM_PROVIDER="anthropic"\nLLM_MODEL_STRONG="claude-sonnet-5"\n'
    'LLM_MODEL_FAST="claude-haiku-4-5"\nLLM_API_KEY="sk-test"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_MODEL="text-embedding-3-small"\n'
    'EMBEDDING_API_KEY="sk-embed-test"\nCOURSE_NAME="Testkurs"\n'
    'WEAVIATE_COLLECTION_NAME="TestChunks"\nWEAVIATE_API_KEY="wv-test"\n'
    'NEO4J_PASSWORD="neo4j-test"\nN8N_INGEST_WEBHOOK_URL="http://n8n/webhook/x"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

import app as flask_app_module  # noqa: E402
import llm_client  # noqa: E402
import storage  # noqa: E402
from llm_client import LLMError, suggest_keywords  # noqa: E402

failures = []
ENV = {
    "LLM_PROVIDER": "anthropic",
    "LLM_MODEL_FAST": "claude-haiku-4-5",
    "LLM_API_KEY": "sk-test",
}


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


def fake_complete(reply):
    """Stands in for the provider dispatch, capturing what it was asked."""
    seen = {}

    def _fake(system_prompt, user_prompt, env):
        seen["system"] = system_prompt
        seen["user"] = user_prompt
        seen["env"] = env
        return reply

    return _fake, seen


# ── suggest_keywords: prompt building ───────────────────────────────────────
fake, seen = fake_complete('{"keywords": ["Cognitive Load", "Instructional Design"]}')
with mock.patch.object(llm_client, "_complete", fake):
    kws = suggest_keywords("Cognitive Load Theory", "Sweller, J.", "Abstract: ...", ENV)
check("keywords parsed", kws == ["Cognitive Load", "Instructional Design"], kws)
check("title reaches the prompt", "Cognitive Load Theory" in seen["user"], seen["user"][:120])
check("authors reach the prompt", "Sweller, J." in seen["user"])
check("excerpt reaches the prompt", "Abstract: ..." in seen["user"])
check("default language is English", "English" in seen["system"], seen["system"][:200])

# German GUI must ask for German keywords — they are content, not chrome.
fake, seen = fake_complete('{"keywords": ["Lernpsychologie"]}')
with mock.patch.object(llm_client, "_complete", fake):
    suggest_keywords("Titel", "", "", ENV, language="de")
check("German requested for de", "German" in seen["system"], seen["system"][:200])

# A long scan must not be sent wholesale.
fake, seen = fake_complete('{"keywords": ["x"]}')
with mock.patch.object(llm_client, "_complete", fake):
    suggest_keywords("T", "", "y" * 20000, ENV)
check("excerpt is capped", seen["user"].count("y") <= 6000, seen["user"].count("y"))

# Missing fields must be labelled, not silently empty.
fake, seen = fake_complete('{"keywords": ["x"]}')
with mock.patch.object(llm_client, "_complete", fake):
    suggest_keywords("Only a title", "", "", ENV)
check("unknown authors labelled", "(unknown)" in seen["user"], seen["user"][:200])

# ── suggest_keywords: parsing and hygiene ───────────────────────────────────
cases = [
    ('{"keywords": ["A", "B"]}', ["A", "B"], "plain JSON"),
    ('```json\n{"keywords": ["A"]}\n```', ["A"], "markdown fence"),
    ('{"keywords": ["A", "a", "A "]}', ["A"], "case/whitespace dedup"),
    ('{"keywords": ["A", "", "  ", "B"]}', ["A", "B"], "blank entries dropped"),
]
for reply, expected, label in cases:
    fake, _ = fake_complete(reply)
    with mock.patch.object(llm_client, "_complete", fake):
        try:
            got = suggest_keywords("T", "", "", ENV)
        except LLMError as exc:
            got = f"LLMError: {exc}"
    check(f"parse: {label}", got == expected, f"{got!r} != {expected!r}")

# Unparseable or empty replies must fail loudly rather than return nothing.
# A chatty preamble counts as unparseable on purpose: the system prompt says
# "output nothing before or after the JSON object", and _parse_suggestion
# (the optimize/citation path) is equally strict. Salvaging a brace-delimited
# substring here would make the two parsers disagree about what a valid reply
# is, which is worse than one loud, visible failure.
for reply, label in [
    ("not json at all", "garbage"),
    ('Sure!\n{"keywords": ["A"]}', "chatty preamble"),
    ('{"keywords": []}', "empty list"),
]:
    fake, _ = fake_complete(reply)
    with mock.patch.object(llm_client, "_complete", fake):
        try:
            suggest_keywords("T", "", "", ENV)
            failures.append(f"{label} reply should raise LLMError")
        except LLMError:
            pass

# Nothing to work from: refuse rather than invent keywords out of thin air.
called = {"n": 0}


def _must_not_run(*a, **kw):
    called["n"] += 1
    return '{"keywords": []}'


with mock.patch.object(llm_client, "_complete", _must_not_run):
    try:
        suggest_keywords("   ", "Sweller, J.", "  ", ENV)
        failures.append("empty title+excerpt should raise LLMError")
    except LLMError:
        pass
check("no LLM call when there is nothing to work from", called["n"] == 0, called["n"])

# ── Route behaviour ─────────────────────────────────────────────────────────
client = flask_app_module.app.test_client()

# Login is required — keyword suggestion spends the operator's LLM budget.
resp = client.post("/upload/keywords", json={"title": "T"})
check("route requires login", resp.status_code in (302, 401), resp.status_code)

client.post("/setup", data={
    "username": "admin", "password": "a-strong-test-password",
    "confirm": "a-strong-test-password",
}, follow_redirects=True)

with mock.patch.object(flask_app_module, "suggest_keywords",
                       return_value=["Alpha", "Beta"]) as m:
    resp = client.post("/upload/keywords", json={"title": "T", "authors": "A"})
check("happy path is 200", resp.status_code == 200, resp.status_code)
check("keywords returned", resp.get_json() == {"keywords": ["Alpha", "Beta"]},
      resp.get_data(as_text=True))
args, kwargs = m.call_args
check("title passed through", args[0] == "T", args)
check("authors passed through", args[1] == "A", args)
check("language passed through", kwargs.get("language") == "en", kwargs)

# LLMError must surface as 502 with the reason, not a blank 500.
with mock.patch.object(flask_app_module, "suggest_keywords",
                       side_effect=LLMError("no API key")):
    resp = client.post("/upload/keywords", json={"title": "T"})
check("LLMError -> 502", resp.status_code == 502, resp.status_code)
check("reason surfaced", "no API key" in resp.get_json().get("error", ""),
      resp.get_data(as_text=True))

# A body that isn't JSON at all must not blow up.
with mock.patch.object(flask_app_module, "suggest_keywords", return_value=["X"]) as m:
    resp = client.post("/upload/keywords", data="not json",
                       content_type="text/plain")
check("non-JSON body tolerated", resp.status_code == 200, resp.status_code)
check("empty strings passed on non-JSON body", m.call_args[0][:2] == ("", ""),
      m.call_args)

# ── The scan stash feeds the suggestion ─────────────────────────────────────
with mock.patch.object(flask_app_module, "suggest_keywords", return_value=["X"]) as m:
    resp = client.post("/upload/keywords", json={"title": "T"})
check("no excerpt before any scan", m.call_args[0][2] == "", repr(m.call_args[0][2])[:80])

with client.session_transaction() as sess:
    sess["last_scan_text"] = "Front matter from the scanned PDF."
with mock.patch.object(flask_app_module, "suggest_keywords", return_value=["X"]) as m:
    client.post("/upload/keywords", json={"title": "T"})
check("stashed scan text is reused",
      m.call_args[0][2] == "Front matter from the scanned PDF.", m.call_args)

# The stash must actually be filled by a real scan, and capped.
storage.save_slot(1, "agent-11-expert-feedback.json", {"EXPERT_DOMAIN": "x"}, "A", None)
with client.session_transaction() as sess:
    sess.pop("last_scan_text", None)
with mock.patch.object(flask_app_module.citation, "scan_pdf",
                       return_value={"doi": "10.1/x", "text": "z" * 50000}), \
     mock.patch.object(flask_app_module.citation, "lookup_doi",
                       return_value={"title": "T"}):
    client.post("/upload/lookup", data={
        "document": (Path(APP_DIR, "app.py").open("rb"), "doc.pdf"),
    }, content_type="multipart/form-data")
with client.session_transaction() as sess:
    stashed = sess.get("last_scan_text", "")
check("scan fills the stash", stashed.startswith("zzz"), repr(stashed)[:60])
check("stash is capped at 20k", len(stashed) == 20000, len(stashed))

# ── Template wiring, both languages ─────────────────────────────────────────
import i18n  # noqa: E402

for lang in ("en", "de"):
    client.get(f"/language/{lang}")
    body = client.get("/upload").get_data(as_text=True)
    check(f"[{lang}] suggest button rendered", 'id="topic-suggest"' in body)
    check(f"[{lang}] result box rendered", 'id="topic-result"' in body)
    check(f"[{lang}] posts to the right route", "/upload/keywords" in body)
    check(f"[{lang}] button label localised",
          i18n.t("upload_topic_suggest", lang=lang) in body,
          i18n.t("upload_topic_suggest", lang=lang))
    # The JS labels are injected via |tojson — they must be valid JSON
    # literals, or the whole script block dies silently.
    for key in ("upload_topic_suggesting", "slot_close", "lookup_dismiss"):
        check(f"[{lang}] {key} injected as JSON",
              json.dumps(i18n.t(key, lang=lang)) in body,
              json.dumps(i18n.t(key, lang=lang)))
    # The error label is a %s template; the page substitutes '@@' at runtime.
    check(f"[{lang}] error label injected with the @@ marker",
          json.dumps(i18n.t("upload_topic_err", "@@", lang=lang)) in body)
client.get("/language/en")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All keyword-suggestion checks passed: suggest_keywords builds the prompt from "
    "title/authors/excerpt with unknowns labelled, caps the excerpt, asks for the GUI "
    "language, parses plain and fenced JSON, dedups case- and whitespace-variants, "
    "drops blanks, raises loudly on garbage, chatty preambles and empty results (matching "
    "_parse_suggestion's strictness), and refuses to call the LLM at "
    "all when there is nothing to work from; the /upload/keywords route requires login, "
    "passes title/authors/language through, tolerates a non-JSON body, surfaces LLMError "
    "as 502 with its reason, and reuses the front matter that /upload/lookup stashes "
    "(capped at 20k); and the button, result box and every localised JS label render "
    "correctly on the upload page in both English and German."
)
