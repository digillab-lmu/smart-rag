import json
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
    'LLM_PROVIDER="anthropic"\nLLM_MODEL_STRONG="claude-sonnet-5"\n'
    'LLM_MODEL_FAST="claude-haiku-4-5"\nLLM_API_KEY="sk-test"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_MODEL="text-embedding-3-small"\n'
    'EMBEDDING_API_KEY="sk-embed-test"\nCOURSE_NAME="Testkurs"\n'
    'WEAVIATE_COLLECTION_NAME="TestChunks"\nWEAVIATE_API_KEY="wv-test"\n'
    'NEO4J_PASSWORD="neo4j-test"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

import agent_templates as at  # noqa: E402
# ─── A database, because agent slots live in one now ─────────────────────────
# Slots moved out of slots.json into Postgres, so this suite needs a database
# and a course for the slots to belong to. dbfixture arranges both, or exits
# 10 — "could not run" rather than a pass that covered nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402
_db, COURSE = dbfixture.require_database()
dbfixture.clear_slots(_db)
COURSE_ID = COURSE["id"]

import app as flask_app_module  # noqa: E402
import storage  # noqa: E402

failures = []
ARCH = "agent-11-expert-feedback.json"


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


# ── Prompt access helpers ───────────────────────────────────────────────────
for arch in at.ARCHETYPES:
    prompt = at.default_prompt_for(arch)
    check(f"{arch}: has a default prompt", len(prompt) > 500, f"{len(prompt)} chars")

# Every archetype must ship a *distinct* prompt — a copy-paste slip here
# would silently give two agent types identical behaviour.
prompts = {a: at.default_prompt_for(a) for a in at.ARCHETYPES}
dupes = [
    (a, b) for a in prompts for b in prompts
    if a < b and prompts[a] == prompts[b]
]
check("archetype prompts are all distinct", not dupes, f"{dupes}")

# set_prompt must find the holder regardless of node order
flow = at.load_template(ARCH)
check("set_prompt succeeds", at.set_prompt(flow, "REPLACED") is True)
check("set_prompt actually replaced", "REPLACED" in json.dumps(flow))
check("old prompt gone", "# Identity" not in json.dumps(flow))

# A flow with no agentMessages must report failure rather than pretend
check("set_prompt on empty flow returns False", at.set_prompt({"nodes": []}, "x") is False)

# ── Fields follow the edited prompt ─────────────────────────────────────────
base_fields = at.placeholders_for(ARCH)
check("baseline fields", "EXPERT_DOMAIN" in base_fields, base_fields)

added = at.placeholders_for(ARCH, at.default_prompt_for(ARCH) + "\n{{MY_NEW_FIELD}}")
check("added placeholder yields a field", "MY_NEW_FIELD" in added, added)

stripped = at.placeholders_for(ARCH, "A prompt with no placeholders at all.")
# RESPONSE_LANGUAGE_RULE and STUDENT_ROLE exist only inside the prompt, so
# emptying it must retire them. EXPERT_DOMAIN and the knowledge-description
# fields also appear in the vector-store node's knowledgeName/Description,
# so they legitimately survive — verified against the template itself.
check("prompt-only placeholders retire",
      "RESPONSE_LANGUAGE_RULE" not in stripped and "STUDENT_ROLE" not in stripped, stripped)
check("placeholders used elsewhere survive",
      "EXPERT_DOMAIN" in stripped and "CONCEPT_LIST" in stripped, stripped)

# Auto-filled/derived names must never surface as fields, even if the
# operator writes them into the prompt by hand.
sneaky = at.placeholders_for(ARCH, "{{COURSE_NAME}} {{AGENT_NUMBER}} {{EXPERT_DOMAIN}}")
check("auto-filled names stay out of the form",
      "COURSE_NAME" not in sneaky and "AGENT_NUMBER" not in sneaky, sneaky)

# ── Storage round-trip ──────────────────────────────────────────────────────
storage.save_slot(COURSE_ID, 1, ARCH, {"EXPERT_DOMAIN": "x"}, "Agent A", "MY CUSTOM PROMPT")
check("prompt persisted", storage.get_slot(COURSE_ID, 1).get("system_prompt") == "MY CUSTOM PROMPT")
storage.save_slot(COURSE_ID, 1, ARCH, {"EXPERT_DOMAIN": "x"}, "Agent A", None)
check("None means default", storage.get_slot(COURSE_ID, 1).get("system_prompt") is None)

# ── Live request behaviour ──────────────────────────────────────────────────
client = flask_app_module.app.test_client()
client.post("/setup", data={
    "username": "admin", "password": "a-strong-test-password", "confirm": "a-strong-test-password",
}, follow_redirects=True)

resp = client.post("/slot/2", data={"action": "choose_archetype", "archetype": ARCH})
body = resp.get_data(as_text=True)
check("prompt shown on the form", "System prompt" in body, body[:200])
check("default prompt text rendered", "# Identity" in body)
check("reset button present", "reset_prompt" in body)

DEFAULT = at.default_prompt_for(ARCH)

# Saving without touching the prompt must NOT store an override — the slot
# should keep tracking the template so later template fixes reach it.
client.post("/slot/2", data={
    "archetype": ARCH, "action": "save", "name": "Prompt Agent",
    "system_prompt": DEFAULT,
    "EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
    "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s",
}, follow_redirects=True)
check("untouched prompt stores no override",
      storage.get_slot(COURSE_ID, 2).get("system_prompt") is None,
      repr(storage.get_slot(COURSE_ID, 2).get("system_prompt"))[:80])

# Whitespace-only difference must also count as untouched.
client.post("/slot/2", data={
    "archetype": ARCH, "action": "save", "name": "Prompt Agent",
    "system_prompt": "\n  " + DEFAULT + "  \n",
    "EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
    "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s",
}, follow_redirects=True)
check("whitespace-only change is not an override",
      storage.get_slot(COURSE_ID, 2).get("system_prompt") is None)

# A genuine edit is stored and shown as customised.
EDITED = DEFAULT + "\n\n# Extra rule\nAlways answer in exactly three sentences."
resp = client.post("/slot/2", data={
    "archetype": ARCH, "action": "save", "name": "Prompt Agent",
    "system_prompt": EDITED,
    "EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
    "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s",
}, follow_redirects=True)
check("edit is stored", storage.get_slot(COURSE_ID, 2).get("system_prompt") == EDITED)
body = resp.get_data(as_text=True)
check("edited prompt renders back", "exactly three sentences" in body)
check("customised state shown", "no longer follows the default" in body, body[:200])

# Adding a placeholder while editing must produce a field on the next render.
WITH_FIELD = DEFAULT + "\n{{TONE_OF_VOICE}}"
resp = client.post("/slot/2", data={
    "archetype": ARCH, "action": "save", "name": "Prompt Agent",
    "system_prompt": WITH_FIELD,
    "EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
    "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s",
}, follow_redirects=True)
check("new placeholder becomes a form field",
      'name="TONE_OF_VOICE"' in resp.get_data(as_text=True))

# Reset restores the default and clears the override.
resp = client.post("/slot/2", data={
    "archetype": ARCH, "action": "reset_prompt", "name": "Prompt Agent",
    "system_prompt": EDITED,
    "EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
    "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s",
}, follow_redirects=True)
check("reset clears the override", storage.get_slot(COURSE_ID, 2).get("system_prompt") is None,
      repr(storage.get_slot(COURSE_ID, 2).get("system_prompt"))[:80])
body = resp.get_data(as_text=True)
check("reset confirms", "reset to the default" in body, body[:200])
check("reset removes the edit from the box", "exactly three sentences" not in body)

# ── The edited prompt actually reaches the imported flow ────────────────────
storage.save_slot(COURSE_ID, 3, ARCH, {
    "EXPERT_DOMAIN": "Cognitive Load", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
    "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s",
}, "Import Agent", "CUSTOM PROMPT for {{EXPERT_DOMAIN}}.")

captured = {}


class FakeFlowise:
    def upsert_credential(self, *a, **kw):
        return "cred-id"

    def get_or_create_variable(self, *a, **kw):
        return "var-id"

    def upsert_chatflow(self, name, flow_data, analytic=None):
        captured["flow"] = flow_data
        captured["name"] = name
        # A distinct id per chatflow, as Flowise gives. The fake used to
        # return one constant, which two slots then shared — the schema
        # refuses that now, and rightly: two slots on one chatflow means each
        # import silently overwrites the other's agent.
        return f"chatflow-{abs(hash(name)) % 100000}", True


err = flask_app_module._do_import(COURSE, 3, ARCH, FakeFlowise())
check("import succeeded", err is None, str(err))
flow_json = captured.get("flow", "")
check("custom prompt is in the imported flow", "CUSTOM PROMPT for Cognitive Load." in flow_json,
      flow_json[:200])
check("shipped prompt is gone from the imported flow", "# Identity" not in flow_json)
# Only OUR placeholders must be gone. {{question}} / {{output}} are
# Flowise's own runtime variables — lowercase, so PLACEHOLDER_RE never
# touches them, and they must survive into the imported flow.
import re as _re
leftover_ours = sorted(set(_re.findall(r"\{\{([A-Z0-9_]+)\}\}", flow_json)))
check("no unsubstituted SMART RAG placeholders left", not leftover_ours, leftover_ours)
check("Flowise's own runtime vars are preserved", "{{question}}" in flow_json)

# A slot with no override must still import the shipped prompt.
storage.save_slot(COURSE_ID, 4, ARCH, {
    "EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
    "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s",
}, "Default Agent", None)
captured.clear()
flask_app_module._do_import(COURSE, 4, ARCH, FakeFlowise())
check("default prompt used when no override", "# Identity" in captured.get("flow", ""))

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All system-prompt editing checks passed: every archetype ships a distinct non-trivial "
    "prompt; set_prompt locates the holder by structure and reports failure when absent; "
    "form fields follow the edited prompt (added placeholder appears, removed one retires, "
    "placeholders used elsewhere survive, auto-filled names stay out); storage round-trip; "
    "untouched and whitespace-only-changed prompts store no override so the slot keeps "
    "tracking the template; a real edit persists, renders back and is flagged as customised; "
    "reset clears the override and confirms; and the edited prompt — with its placeholders "
    "substituted — is what actually reaches Flowise, while an unedited slot still imports "
    "the shipped default."
)
