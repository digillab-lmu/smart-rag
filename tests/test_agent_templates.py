import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "content-admin"))
from agent_templates import load_template, auto_fill_from_env, substitute_content, set_credential_ids  # noqa: E402

failures = []

# Simulate a real .env, non-default providers to actually exercise the mapping
env = {
    "LLM_PROVIDER": "openai",
    "LLM_MODEL_STRONG": "gpt-5.2",
    "LLM_MODEL_FAST": "gpt-5.2-mini",
    "EMBEDDING_PROVIDER": "mistral",
    "EMBEDDING_MODEL": "mistral-embed",
    "COURSE_NAME": "Testkurs Mediendidaktik",
    "WEAVIATE_COLLECTION_NAME": "TestkursChunks",
}

content = {
    "AGENT_NUMBER": "4",
    "TOPIC_NAME": "Kognitive Lernvoraussetzungen",
    "TOPIC_SUBTOPICS": "- 4.1 Drei-Speicher-Modell\n- 4.2 CLT",
    "TOPIC_KNOWLEDGE_DESCRIPTION": "Vertiefungsmaterial zu Kapitel 4",
    "STUDENT_ROLE": "Lehramtsstudierende/r",
    "CONCEPT_LIST": "Drei-Speicher-Modell, CLT, CTML",
    "RESPONSE_LANGUAGE_RULE": "Antworte ausschließlich auf Deutsch",
}

flow = load_template("agent-topic-template.json")
# The course is passed, not read from env: COURSE_NAME, COURSE_ID and
# WEAVIATE_COLLECTION_NAME left .env when the installer stopped configuring a
# course, and an agent's collection and filter are properties of the course it
# belongs to rather than of the installation.
COURSE = {"id": "testkurs", "name": "Testkurs Mediendidaktik",
          "collection": "TestkursChunks"}
auto_fill_from_env(flow, env, course=COURSE)
missing = substitute_content(flow, content)
set_credential_ids(flow, "llm-cred-id-123", "embed-cred-id-456")

dump = json.dumps(flow, ensure_ascii=False)

# 1. No missing placeholders
if missing:
    failures.append(f"Missing placeholders not covered by test content: {missing}")

# 2. No literal {{...}} left anywhere
import re
leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", dump)
if leftover:
    failures.append(f"Leftover unsubstituted placeholders: {leftover}")

# 3. The two previously-bare, never-substituted bugs are now fixed
if '"WEAVIATE_COLLECTION_NAME"' in dump:
    failures.append("BUG STILL PRESENT: literal 'WEAVIATE_COLLECTION_NAME' string found")
if '"EMBEDDING_MODEL"' in dump:
    failures.append("BUG STILL PRESENT: literal 'EMBEDDING_MODEL' string found")
if "TestkursChunks" not in dump:
    failures.append("weaviateIndex was not actually set to the real collection name")
if "mistral-embed" not in dump:
    failures.append("embedding modelName was not set from env")

# 4. Provider mapping actually applied on the actual VALUE fields (agentModel/
#    llmModel node-type selector + the two modelConfig copies). NOTE: the
#    template legitimately still contains "chatAnthropic"/"chatGoogleGenerativeAI"
#    etc. elsewhere — as `show: {agentModel: "chatXxx"}` conditions on OTHER
#    provider-specific optional fields (e.g. agentToolsBuiltInAnthropic), which
#    is static UI schema metadata, not a configured value, and must NOT change.
agent_node = next(n for n in flow["nodes"] if "agentModel" in n["data"]["inputs"])
extract_node = next(n for n in flow["nodes"] if "llmModel" in n["data"]["inputs"])
if agent_node["data"]["inputs"]["agentModel"] != "chatOpenAI":
    failures.append("agentModel (top-level) was not remapped to chatOpenAI")
if agent_node["data"]["inputs"]["agentModelConfig"]["agentModel"] != "chatOpenAI":
    failures.append("agentModelConfig.agentModel was not remapped to chatOpenAI")
if extract_node["data"]["inputs"]["llmModel"] != "chatOpenAI":
    failures.append("llmModel (top-level) was not remapped to chatOpenAI")
vs = agent_node["data"]["inputs"]["agentKnowledgeVSEmbeddings"][0]
if vs["embeddingModel"] != "mistralAIEmbeddings":
    failures.append("embeddingModel node type was not remapped to mistralAIEmbeddings")
if vs["embeddingModelConfig"]["embeddingModel"] != "mistralAIEmbeddings":
    failures.append("embeddingModelConfig.embeddingModel was not remapped")

# 5. Credential IDs wired in
if agent_node["data"]["inputs"]["agentModelConfig"]["credential"] != "llm-cred-id-123":
    failures.append("LLM credential id was not set")
if vs["embeddingModelConfig"].get("FLOWISE_CREDENTIAL_ID") != "embed-cred-id-456":
    failures.append("embedding credential id (FLOWISE_CREDENTIAL_ID) was not set")

# 6. modelName actually updated
if '"modelName": "gpt-5.2"' not in dump:
    failures.append("agentModelConfig.modelName was not set from LLM_MODEL_STRONG")
if '"modelName": "gpt-5.2-mini"' not in dump:
    failures.append("llmModelConfig.modelName was not set from LLM_MODEL_FAST")

# 7. Content substitution worked
if "Kognitive Lernvoraussetzungen" not in dump:
    failures.append("TOPIC_NAME content was not substituted")
if "Testkurs Mediendidaktik" not in dump:
    failures.append("COURSE_NAME (auto-fill) was not substituted")

# 8. Test the "missing content" validation path too
flow2 = load_template("agent-topic-template.json")
auto_fill_from_env(flow2, env)
missing2 = substitute_content(flow2, {})  # no content supplied at all
expected_missing = {
    # AGENT_NUMBER is not here: auto_fill_from_env() always substitutes it
    # (with "" when no slot is passed, as here), so it never shows up as
    # "missing" at the raw-template level — it's excluded from the
    # user-facing content form entirely via AUTO_FILLED_FIELDS instead.
    "TOPIC_NAME", "TOPIC_SUBTOPICS", "TOPIC_KNOWLEDGE_DESCRIPTION",
    "STUDENT_ROLE", "CONCEPT_LIST", "RESPONSE_LANGUAGE_RULE",
}
if set(missing2) != expected_missing:
    failures.append(f"missing-placeholder detection off: got {sorted(missing2)}, expected {sorted(expected_missing)}")

# ─── The token cap survives the provider swap ────────────────────────────────
# Flowise reads a node's inputs by name and ignores what it does not know, so
# swapping chatAnthropic for chatOpenAI left the template's maxTokensToSample
# sitting in the config where nothing reads it, and maxTokens unset. The cap
# then silently did not apply — on the topic-extraction node, which runs on
# every question a learner asks, writes its answer into the agent's state and
# appends it to the conversation. Forty tokens is what keeps a concept name
# from becoming a paragraph in both places.
#
# The input names are the ones Flowise 3.1.3 declares, read from the running
# container rather than recalled.
_cap_env = {"LLM_API_KEY": "k", "EMBEDDING_PROVIDER": "openai",
            "EMBEDDING_API_KEY": "k", "EMBEDDING_MODEL": "m",
            "LLM_MODEL_STRONG": "s", "LLM_MODEL_FAST": "f"}
_expected_cap = {
    "anthropic":  ("chatAnthropic",           "maxTokensToSample"),
    "openai":     ("chatOpenAI",              "maxTokens"),
    "google":     ("chatGoogleGenerativeAI",  "maxOutputTokens"),
    "mistral":    ("chatMistralAI",           "maxOutputTokens"),
    "openrouter": ("chatOpenRouter",          "maxTokens"),
    # ChatCohere declares no token-limit input; the cap cannot be expressed.
    "cohere":     ("chatCohere",              None),
}
for _prov, (_node, _key) in _expected_cap.items():
    _flow = load_template("agent-10-persona.json")
    auto_fill_from_env(_flow, dict(_cap_env, LLM_PROVIDER=_prov), slot=1,
                          course={"id": "c", "name": "n", "collection": "Col"})
    _cfgs = [n["data"]["inputs"]["llmModelConfig"] for n in _flow["nodes"]
             if isinstance(n.get("data", {}).get("inputs", {}).get("llmModelConfig"), dict)]
    if not _cfgs:
        failures.append(f"{_prov}: no llmModelConfig in the template")
        continue
    _cfg = _cfgs[0]
    # Both model nodes go through the swap. The agent's own cap is empty in
    # the templates, so nothing is written under the new name — but the
    # Anthropic key must not stay behind there either, and a check that looks
    # only at the fast node does not notice when that call is dropped.
    _agent_cfgs = [n["data"]["inputs"]["agentModelConfig"] for n in _flow["nodes"]
                   if isinstance(n.get("data", {}).get("inputs", {}).get("agentModelConfig"), dict)]
    for _ac in _agent_cfgs:
        if _key != "maxTokensToSample" and "maxTokensToSample" in _ac:
            failures.append(f"{_prov}: the agent node kept the Anthropic cap key")

    if _key is None:
        if "maxTokensToSample" in _cfg:
            failures.append(f"{_prov}: the Anthropic cap key was left behind, "
                            "where it looks effective and is not")
    else:
        if _cfg.get(_key) != "40":
            failures.append(f"{_prov}: cap not under {_key} — got "
                            f"{ {k: v for k, v in _cfg.items() if 'ax' in k} }")
        if _key != "maxTokensToSample" and "maxTokensToSample" in _cfg:
            failures.append(f"{_prov}: the Anthropic cap key was left beside "
                            f"{_key}; two caps, one of them dead")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All agent_templates.py checks passed: auto-fill, provider mapping, content substitution, credential wiring, missing-placeholder detection.")
