"""
Flowise agent template loading + two-pass substitution.

Pass 1 (auto-fill): values already known from the CLI wizard (.env) — the
operator never re-enters these in the GUI. Covers a real, systematically
verified gap: all 6 templates hardcode Anthropic + OpenAI-Embeddings
literals (agentModel/llmModel node type, modelName, embeddingModel node
type, and two values — EMBEDDING_MODEL and WEAVIATE_COLLECTION_NAME — that
were written as BARE strings instead of {{...}} placeholders, meaning
nothing would ever have substituted them).

Pass 2 (content): the genuinely new, course-specific {{PLACEHOLDER}} values
collected via the GUI forms (CONCEPT_LIST, PERSONA_NAME, TOPIC_NAME, ...).

Provider → Flowise node-type/credential mappings below were read directly
from the Flowise source (packages/components/{nodes,credentials}/...), not
guessed — a wrong node-type name silently produces an agent that can't be
configured in the Flowise UI at all.
"""

import json
import re
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).parent.parent / "flowise" / "agents"

# ─── Provider → Flowise node-type / credential mapping ───────────────────────
# (chat_node, credential_name, credential_key) — credential_name/key are
# Flowise's own identifiers (this.name in the credential class, and the
# `name` of its API-key input field), needed to create a matching credential
# via POST /credentials.

LLM_PROVIDER_MAP: dict[str, dict[str, str]] = {
    "anthropic": {
        "node": "chatAnthropic",
        "credential_name": "anthropicApi",
        "credential_key": "anthropicApiKey",
    },
    "openai": {
        "node": "chatOpenAI",
        "credential_name": "openAIApi",
        "credential_key": "openAIApiKey",
    },
    "google": {
        "node": "chatGoogleGenerativeAI",
        "credential_name": "googleGenerativeAI",
        "credential_key": "googleGenerativeAPIKey",
    },
    "mistral": {
        "node": "chatMistralAI",
        "credential_name": "mistralAIApi",
        "credential_key": "mistralAIAPIKey",
    },
    "cohere": {
        "node": "chatCohere",
        "credential_name": "cohereApi",
        "credential_key": "cohereApiKey",
    },
    "openrouter": {
        "node": "chatOpenRouter",
        "credential_name": "openRouterApi",
        "credential_key": "openRouterApiKey",
    },
    # OpenAI-compatible custom endpoint — reuses the OpenAI node, pointed at
    # a different base URL via the `basepath` input Flowise's ChatOpenAI node
    # already exposes for exactly this purpose.
    "custom": {
        "node": "chatOpenAI",
        "credential_name": "openAIApi",
        "credential_key": "openAIApiKey",
        "extra_config_key": "basepath",
        "extra_config_env": "LLM_BASE_URL",
    },
}

EMBEDDING_PROVIDER_MAP: dict[str, dict[str, str]] = {
    "openai": {
        "node": "openAIEmbeddings",
        "credential_name": "openAIApi",
        "credential_key": "openAIApiKey",
    },
    "cohere": {
        "node": "cohereEmbeddings",
        "credential_name": "cohereApi",
        "credential_key": "cohereApiKey",
    },
    "google": {
        "node": "googleGenerativeAiEmbeddings",
        "credential_name": "googleGenerativeAI",
        "credential_key": "googleGenerativeAPIKey",
    },
    "mistral": {
        "node": "mistralAIEmbeddings",
        "credential_name": "mistralAIApi",
        "credential_key": "mistralAIAPIKey",
    },
    "custom": {
        "node": "openAIEmbeddingsCustom",
        "credential_name": "openAIApi",
        "credential_key": "openAIApiKey",
        "extra_config_key": "basepath",
        "extra_config_env": "EMBEDDING_BASE_URL",
    },
}

PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

# Display name shown in the archetype picker, and which template file backs it.
ARCHETYPES: dict[str, str] = {
    "agent-01-universal.json": "Universal Assistant",
    "agent-10-persona.json": "Persona Agent",
    "agent-11-expert-feedback.json": "Expert Feedback Agent",
    "agent-13-knowledge-test.json": "Knowledge Test Agent",
    "agent-14-backup.json": "Backup Assistant",
    "agent-topic-template.json": "Topic Agent",
}

# Fields whose value is derived from other slots, not asked in that slot's own
# form — see derive_translation_tables(). Only relevant for agent-13.
DERIVED_FIELDS = {"AGENT_TRANSLATION_TABLE", "CONTENT_TRANSLATION_TABLE"}


def placeholders_for(archetype_file: str) -> list[str]:
    """All {{PLACEHOLDER}} names a template references, minus the derived
    ones — used to render the content form for a slot."""
    flow = load_template(archetype_file)
    found = set(PLACEHOLDER_RE.findall(json.dumps(flow)))
    return sorted(found - DERIVED_FIELDS)


def derive_translation_tables(all_slots: dict[str, dict]) -> dict[str, str]:
    """
    Builds AGENT_TRANSLATION_TABLE / CONTENT_TRANSLATION_TABLE from whatever
    the OTHER 9 slots already have filled in, instead of asking the operator
    to re-type the same information a third time. Best-effort, not asked to
    be perfect: the knowledge-test agent (agent-13) is the only consumer, and
    it's an internal reference table, not shown to students.
    """
    agent_lines = []
    content_lines = []
    for slot_num, slot in sorted(all_slots.items(), key=lambda kv: int(kv[0])):
        content = slot.get("content") or {}
        label = (
            content.get("TOPIC_NAME")
            or content.get("PERSONA_NAME")
            or content.get("EXPERT_DOMAIN")
            or ARCHETYPES.get(slot.get("archetype", ""), "")
        )
        if not label:
            continue
        agent_lines.append(f"Agent {slot_num} = {label}")
        subtopics = content.get("TOPIC_SUBTOPICS")
        if subtopics:
            content_lines.append(subtopics)

    return {
        "AGENT_TRANSLATION_TABLE": "\n".join(agent_lines) or "(no other agents configured yet)",
        "CONTENT_TRANSLATION_TABLE": "\n".join(content_lines) or "(no topic agents configured yet)",
    }


class TemplateError(ValueError):
    pass


def load_template(archetype_file: str) -> dict[str, Any]:
    path = TEMPLATES_DIR / archetype_file
    if not path.is_file():
        raise TemplateError(f"Unknown agent template: {archetype_file}")
    return json.loads(path.read_text())


def _walk_strings(obj: Any, fn):
    """Recursively apply fn() to every string value in a JSON-like structure,
    in place. Used for the {{PLACEHOLDER}} content pass."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = _walk_strings(v, fn)
        return obj
    if isinstance(obj, list):
        return [_walk_strings(v, fn) for v in obj]
    if isinstance(obj, str):
        return fn(obj)
    return obj


def substitute_content(flow_data: dict[str, Any], values: dict[str, str]) -> list[str]:
    """
    Pass 2: replace {{PLACEHOLDER}} with GUI-collected content values.
    Returns the list of placeholder names that were found but had no value
    supplied — the caller shows these as a validation error rather than
    silently importing an agent with a literal "{{X}}" in its prompt.
    """
    missing: set[str] = set()

    def replace(text: str) -> str:
        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key not in values:
                missing.add(key)
                return m.group(0)
            return values[key]

        return PLACEHOLDER_RE.sub(sub, text)

    _walk_strings(flow_data, replace)
    return sorted(missing)


def auto_fill_from_env(flow_data: dict[str, Any], env: dict[str, str]) -> None:
    """
    Pass 1: fill in everything already known from the CLI wizard. Mutates
    flow_data in place. Must run BEFORE substitute_content(), since
    {{COURSE_NAME}} is handled here (env-known), not as a content field.
    """
    llm_provider = env.get("LLM_PROVIDER", "anthropic")
    embed_provider = env.get("EMBEDDING_PROVIDER", "openai")
    llm_map = LLM_PROVIDER_MAP.get(llm_provider, LLM_PROVIDER_MAP["anthropic"])
    embed_map = EMBEDDING_PROVIDER_MAP.get(embed_provider, EMBEDDING_PROVIDER_MAP["openai"])

    course_name = env.get("COURSE_NAME", "")
    weaviate_collection = env.get("WEAVIATE_COLLECTION_NAME", "")
    embedding_model = env.get("EMBEDDING_MODEL", "")

    def replace_course_name(text: str) -> str:
        return PLACEHOLDER_RE.sub(
            lambda m: course_name if m.group(1) == "COURSE_NAME" else m.group(0), text
        )

    _walk_strings(flow_data, replace_course_name)

    for node in flow_data.get("nodes", []):
        inputs = node.get("data", {}).get("inputs", {})

        # Main agent LLM (strong model) + "Thema extrahieren" LLM (fast model)
        if "agentModel" in inputs:
            inputs["agentModel"] = llm_map["node"]
            cfg = inputs.get("agentModelConfig")
            if isinstance(cfg, dict):
                cfg["agentModel"] = llm_map["node"]
                cfg["modelName"] = env.get("LLM_MODEL_STRONG", cfg.get("modelName", ""))
                if "extra_config_key" in llm_map:
                    cfg[llm_map["extra_config_key"]] = env.get(
                        llm_map["extra_config_env"], ""
                    )
        if "llmModel" in inputs:
            inputs["llmModel"] = llm_map["node"]
            cfg = inputs.get("llmModelConfig")
            if isinstance(cfg, dict):
                cfg["llmModel"] = llm_map["node"]
                cfg["modelName"] = env.get("LLM_MODEL_FAST", cfg.get("modelName", ""))
                if "extra_config_key" in llm_map:
                    cfg[llm_map["extra_config_key"]] = env.get(
                        llm_map["extra_config_env"], ""
                    )

        # Knowledge / vector store block
        vs_list = inputs.get("agentKnowledgeVSEmbeddings")
        if isinstance(vs_list, list):
            for vs in vs_list:
                if not isinstance(vs, dict):
                    continue
                vs["embeddingModel"] = embed_map["node"]
                emc = vs.get("embeddingModelConfig")
                if isinstance(emc, dict):
                    emc["embeddingModel"] = embed_map["node"]
                    emc["modelName"] = embedding_model
                    if "extra_config_key" in embed_map:
                        emc[embed_map["extra_config_key"]] = env.get(
                            embed_map["extra_config_env"], ""
                        )
                vsc = vs.get("vectorStoreConfig")
                if isinstance(vsc, dict):
                    vsc["weaviateIndex"] = weaviate_collection


def set_credential_ids(
    flow_data: dict[str, Any], llm_credential_id: str, embed_credential_id: str
) -> None:
    """Wire the Flowise credential IDs (created via flowise_client) into every
    node that references one — the templates ship with credential: ""."""
    for node in flow_data.get("nodes", []):
        inputs = node.get("data", {}).get("inputs", {})
        for cfg_key in ("agentModelConfig", "llmModelConfig"):
            cfg = inputs.get(cfg_key)
            if isinstance(cfg, dict) and "credential" in cfg:
                cfg["credential"] = llm_credential_id
        vs_list = inputs.get("agentKnowledgeVSEmbeddings")
        if isinstance(vs_list, list):
            for vs in vs_list:
                emc = vs.get("embeddingModelConfig") if isinstance(vs, dict) else None
                if isinstance(emc, dict):
                    # Not "credential" like agentModelConfig/llmModelConfig —
                    # verified against Flowise's own Agent.ts source, which
                    # reads embeddingModelConfig['FLOWISE_CREDENTIAL_ID'].
                    # Always set (not conditional on the key pre-existing):
                    # our templates don't declare it at all yet, since
                    # credential:"" was only ever copied onto the LLM configs.
                    emc["FLOWISE_CREDENTIAL_ID"] = embed_credential_id
