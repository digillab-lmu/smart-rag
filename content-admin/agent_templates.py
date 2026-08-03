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
import os
import re
from pathlib import Path
from typing import Any

# Default assumes running from source (content-admin/ next to flowise/, as
# in this repo checkout) — correct for local dev/tests, but WRONG inside the
# container: the Dockerfile's `COPY *.py ./` flattens everything into /app
# with no parent repo structure, so Path(__file__).parent.parent silently
# resolves to "/" instead of the repo root. Bug caught live (every archetype
# choice 500'd — load_template() raised because the path didn't exist, with
# nothing catching it). Fixed the same way env_file.py/storage.py already
# handle this exact class of local-vs-container path mismatch: an env var
# override, with docker-compose.yml bind-mounting the real directory in at
# that exact path (see SMARTRAG_TEMPLATES_DIR there).
TEMPLATES_DIR = Path(
    os.getenv("SMARTRAG_TEMPLATES_DIR", str(Path(__file__).parent.parent / "flowise" / "agents"))
)

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

# Shown next to each archetype in the picker — purpose, typical use case,
# whether it needs RAG (course documents retrieved from Weaviate). All but
# Backup Assistant do; document upload/ingestion isn't in this GUI yet
# (see docs/operations-guide.md) so those agents have nothing to retrieve
# until documents exist in the course's Weaviate collection some other way.
ARCHETYPE_DESCRIPTIONS: dict[str, str] = {
    "agent-01-universal.json": (
        "General-purpose course assistant — answers questions across the "
        "whole course. Uses RAG (course documents). Good as a default or "
        "your only agent for a small course."
    ),
    "agent-10-persona.json": (
        "Simulates a specific character or role (e.g. a struggling student, "
        "a stakeholder in a case study) for perspective-taking exercises. "
        "Uses RAG, scoped to what that persona would plausibly know."
    ),
    "agent-11-expert-feedback.json": (
        "Simulates a domain expert giving feedback on student work within "
        "one specific field. Uses RAG, drawing on materials relevant to "
        "that expert domain."
    ),
    "agent-13-knowledge-test.json": (
        "Adaptive, scenario-based knowledge assessment — poses practice "
        "tasks and adjusts difficulty from the student's answers. Uses RAG "
        "across the whole course. Typically one per course, not one per "
        "topic — it also references your other configured agents "
        "automatically."
    ),
    "agent-14-backup.json": (
        "Plain fallback chat agent — no RAG, no course-document retrieval. "
        "Use for general conversation when a materials-grounded answer "
        "isn't needed, or as a safety-net slot."
    ),
    "agent-topic-template.json": (
        "Focused on one course chapter/topic — meant to be reused, one per "
        "chapter. Uses RAG, scoped to that chapter's material. Most of "
        "your 10 slots will likely be this type."
    ),
}

# Fields whose value is derived from other slots, not asked in that slot's own
# form — see derive_translation_tables(). Only relevant for agent-13.
DERIVED_FIELDS = {"AGENT_TRANSLATION_TABLE", "CONTENT_TRANSLATION_TABLE"}

# Fields auto_fill_from_env() (or _do_import()'s slot-number substitution)
# already fills in from .env / context — must NOT also show up as a content
# form field. Bug caught live: COURSE_NAME/EMBEDDING_MODEL/AGENT_NUMBER were
# rendering as free-text boxes the operator's input for was silently
# discarded (auto-fill runs first and overwrites those exact keys before the
# content pass ever sees them) — confusing, not just cosmetic.
AUTO_FILLED_FIELDS = {"COURSE_NAME", "WEAVIATE_COLLECTION_NAME", "EMBEDDING_MODEL", "AGENT_NUMBER"}

# Shown under each content-form field — every remaining field is genuine,
# course-specific prose the operator has to write themselves, so a first-
# time non-technical user needs to know what's actually expected, not just
# a title-cased field name. One entry per field NAME (not per archetype):
# the same field means the same thing everywhere it appears. Kept short —
# the worked example in FIELD_EXAMPLES (shown as the field's placeholder
# text) is what actually shows the operator what a good answer looks like.
FIELD_HELP: dict[str, str] = {
    "CONCEPT_LIST": "The specific concepts this agent should know about, one per line or comma-separated.",
    "CONCEPT_EXAMPLE": "One concrete concept this persona might mention or struggle with, to make their dialogue feel grounded.",
    "COURSE_KNOWLEDGE_DESCRIPTION": "A short paragraph summarizing what the whole course covers — gives the agent context for what it's retrieving.",
    "EXPERT_DOMAIN": "The specific field of expertise this agent should respond as an expert in.",
    "EXPERT_KNOWLEDGE_DESCRIPTION": "What this expert knows and can give feedback on.",
    "PERSONA_CONCEPTS": "Which course concepts this persona is familiar with (or struggling with) — shapes what they can meaningfully discuss.",
    "PERSONA_CONTEXT": "Background situation — who this persona is, why they're in this conversation.",
    "PERSONA_DESCRIPTION": "A short character description — personality, tone, how they talk.",
    "PERSONA_KNOWLEDGE_DESCRIPTION": "What this persona's own background/expertise consists of, if relevant to their role.",
    "PERSONA_KNOWLEDGE_NAME": "A short label for that background, used when the agent refers back to it.",
    "PERSONA_NAME": "The persona's name, as students will see it.",
    "PERSONA_SITUATION": "The specific scenario this persona is currently facing — gives the roleplay a concrete starting point.",
    "PERSONA_STYLE_DESCRIPTION": "How this persona communicates — formal or casual, short or long answers, etc.",
    "RESPONSE_LANGUAGE_RULE": "One sentence telling the agent which language to answer in.",
    "STUDENT_ROLE": "Who the students using this agent are — shapes how it addresses them.",
    "STUDENT_ROLE_CONTEXT": "A slightly longer version of Student Role, used to generate realistic practice scenarios.",
    "TOPIC_KNOWLEDGE_DESCRIPTION": "A short paragraph summarizing what this specific chapter covers — scopes the agent's retrieval to it.",
    "TOPIC_NAME": "The name of this chapter/topic, as it appears in your course.",
    "TOPIC_SUBTOPICS": "The subtopics/sections within this chapter, one per line.",
}

# Shown as the field's placeholder text (grayed-out, inside the box) — a
# complete, realistic worked example the operator can read, then overwrite
# with their own course content ("nachbauen" — rebuild the same shape with
# their own ideas). All examples share one fictional course (a teacher-
# training module on cognitive load theory) so they read as one coherent,
# copyable pattern rather than disconnected one-liners.
FIELD_EXAMPLES: dict[str, str] = {
    "CONCEPT_LIST": (
        "Working Memory\nCognitive Load Theory\nIntrinsic, Extraneous, and "
        "Germane Load\nMultimedia Learning Principles"
    ),
    "CONCEPT_EXAMPLE": (
        "Confusing intrinsic load (the inherent difficulty of the material) "
        "with extraneous load (poor instructional design)"
    ),
    "COURSE_KNOWLEDGE_DESCRIPTION": (
        "This course introduces educational psychology for future teachers. "
        "It covers major learning theories (behaviorism, cognitivism, "
        "constructivism), motivation and self-regulation, cognitive load "
        "theory, and how to design and evaluate assessments. Students apply "
        "these theories to real classroom scenarios throughout the "
        "semester."
    ),
    "EXPERT_DOMAIN": "Cognitive load theory in multimedia learning design",
    "EXPERT_KNOWLEDGE_DESCRIPTION": (
        "Reviews instructional materials for excessive extraneous cognitive "
        "load and suggests concrete redesigns — e.g. splitting a dense "
        "slide into sequential steps, replacing redundant on-screen text "
        "with narration, or removing decorative elements that add no "
        "learning value."
    ),
    "PERSONA_CONCEPTS": (
        "Comfortable with basic learning theories (behaviorism, "
        "cognitivism) from her teacher training, but has never formally "
        "studied cognitive load theory or multimedia design principles."
    ),
    "PERSONA_CONTEXT": (
        "A first-year secondary school teacher preparing her first "
        "multimedia-based lesson on the water cycle for a 7th-grade class."
    ),
    "PERSONA_DESCRIPTION": (
        "Enthusiastic and eager to use technology in her teaching, but "
        "easily overwhelmed by too much theoretical jargon. Asks a lot of "
        "clarifying, practical \"so what should I actually do\" questions."
    ),
    "PERSONA_KNOWLEDGE_DESCRIPTION": (
        "Three years of classroom teaching experience and a completed "
        "teacher-training degree, but no formal background in "
        "instructional design or media psychology."
    ),
    "PERSONA_KNOWLEDGE_NAME": "classroom teaching experience",
    "PERSONA_NAME": "Sarah, a first-year secondary school teacher",
    "PERSONA_SITUATION": (
        "Sarah has just built a 20-slide presentation on the water cycle, "
        "packed with text, diagrams, and animations, and isn't sure why her "
        "students seem confused instead of engaged."
    ),
    "PERSONA_STYLE_DESCRIPTION": (
        "Casual, conversational tone. Short messages, occasional "
        "teacher-lounge small talk. Avoids academic jargon unless the "
        "student introduces it first."
    ),
    "RESPONSE_LANGUAGE_RULE": (
        "Always respond in German, regardless of the language the student "
        "writes in."
    ),
    "STUDENT_ROLE": "trainee secondary school teachers in their second year of teacher training",
    "STUDENT_ROLE_CONTEXT": (
        "Trainee secondary school teachers who are currently completing "
        "their practical teaching placement and need to design real lesson "
        "materials for their mentor teacher to review."
    ),
    "TOPIC_KNOWLEDGE_DESCRIPTION": (
        "Covers Baddeley's working memory model, Sweller's cognitive load "
        "theory (intrinsic, extraneous, germane load), and Mayer's "
        "multimedia learning principles (coherence, signaling, redundancy, "
        "spatial and temporal contiguity)."
    ),
    "TOPIC_NAME": "Chapter 4: Cognitive Prerequisites for Learning",
    "TOPIC_SUBTOPICS": (
        "4.1 The Three-Store Model of Memory\n4.2 Cognitive Load Theory\n"
        "4.3 Principles of Multimedia Learning"
    ),
}


def placeholders_for(archetype_file: str) -> list[str]:
    """All {{PLACEHOLDER}} names a template references that the operator
    actually needs to fill in — i.e. minus the derived and auto-filled
    ones. Used to render the content form for a slot."""
    flow = load_template(archetype_file)
    found = set(PLACEHOLDER_RE.findall(json.dumps(flow)))
    return sorted(found - DERIVED_FIELDS - AUTO_FILLED_FIELDS)


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


def auto_fill_from_env(
    flow_data: dict[str, Any], env: dict[str, str], slot: int | None = None
) -> None:
    """
    Pass 1: fill in everything already known from the CLI wizard (or, for
    AGENT_NUMBER, from which slot this is) — the operator is never asked for
    these in the content form (see AUTO_FILLED_FIELDS). Mutates flow_data in
    place. Must run BEFORE substitute_content(), since {{COURSE_NAME}} and
    {{AGENT_NUMBER}} are both handled here, not as content fields.
    """
    llm_provider = env.get("LLM_PROVIDER", "anthropic")
    embed_provider = env.get("EMBEDDING_PROVIDER", "openai")
    llm_map = LLM_PROVIDER_MAP.get(llm_provider, LLM_PROVIDER_MAP["anthropic"])
    embed_map = EMBEDDING_PROVIDER_MAP.get(embed_provider, EMBEDDING_PROVIDER_MAP["openai"])

    course_name = env.get("COURSE_NAME", "")
    weaviate_collection = env.get("WEAVIATE_COLLECTION_NAME", "")
    embedding_model = env.get("EMBEDDING_MODEL", "")
    agent_number = str(slot) if slot is not None else ""

    def replace_context_fields(text: str) -> str:
        def sub(m: re.Match) -> str:
            if m.group(1) == "COURSE_NAME":
                return course_name
            if m.group(1) == "AGENT_NUMBER":
                return agent_number
            return m.group(0)

        return PLACEHOLDER_RE.sub(sub, text)

    _walk_strings(flow_data, replace_context_fields)

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
