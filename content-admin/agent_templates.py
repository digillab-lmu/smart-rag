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

import hashlib
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

class ProviderNotConfigured(ValueError):
    """LLM_PROVIDER or EMBEDDING_PROVIDER is empty or names something unknown."""


def _resolve_provider(env: dict, key: str, table: dict) -> tuple[str, dict]:
    """The provider and its Flowise mapping, or a refusal.

    Deliberately no default. Falling back to a *different* provider than the
    one configured is the worst of the three options: the request then goes
    to the wrong vendor with the wrong credential shape, and the error comes
    back as "invalid x-api-key" from a service nobody meant to call — which
    points the reader away from the actual problem, an empty variable.
    """
    provider = (env.get(key) or "").strip()
    if not provider:
        raise ProviderNotConfigured(f"{key} is empty in .env.")
    if provider not in table:
        known = ", ".join(sorted(table))
        raise ProviderNotConfigured(f"{key} is {provider!r}; known values: {known}.")
    return provider, table[provider]


def resolve_llm_provider(env: dict) -> tuple[str, dict]:
    return _resolve_provider(env, "LLM_PROVIDER", LLM_PROVIDER_MAP)


def resolve_embedding_provider(env: dict) -> tuple[str, dict]:
    return _resolve_provider(env, "EMBEDDING_PROVIDER", EMBEDDING_PROVIDER_MAP)


PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

# Everything below is operator-facing copy, so it exists per language.
# Structure mirrors i18n.py's MSG_EN/MSG_DE (English is the fallback when a
# language is missing an entry) — but lives here rather than there because
# it describes these specific templates and their placeholders, and has to
# stay in step with the JSON files next to it.

# Display name shown in the archetype picker, and which template file backs it.
ARCHETYPES_BY_LANG: dict[str, dict[str, str]] = {
    "en": {
        "agent-01-universal.json": "Universal Assistant",
        "agent-10-persona.json": "Persona Agent",
        "agent-11-expert-feedback.json": "Expert Feedback Agent",
        "agent-13-knowledge-test.json": "Knowledge Test Agent",
        "agent-14-backup.json": "Backup Assistant",
        "agent-topic-template.json": "Topic Agent",
    },
    "de": {
        "agent-01-universal.json": "Universal-Assistent",
        "agent-10-persona.json": "Persona-Agent",
        "agent-11-expert-feedback.json": "Experten-Feedback-Agent",
        "agent-13-knowledge-test.json": "Wissenstest-Agent",
        "agent-14-backup.json": "Ausweich-Assistent",
        "agent-topic-template.json": "Themen-Agent",
    },
}

# Canonical (English) names — used where a stable, language-independent
# label is needed, e.g. the chatflow name written into Flowise.
ARCHETYPES: dict[str, str] = ARCHETYPES_BY_LANG["en"]

# Shown next to each archetype in the picker — purpose, typical use case,
# whether it needs RAG (course documents retrieved from Weaviate). All but
# Backup Assistant do; those agents have nothing to retrieve until documents
# are uploaded for them on the Documents page (/upload).
ARCHETYPE_DESCRIPTIONS_BY_LANG: dict[str, dict[str, str]] = {
    "en": {
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
    },
    "de": {
        "agent-01-universal.json": (
            "Allgemeiner Kursassistent — beantwortet Fragen zum gesamten Kurs. "
            "Nutzt RAG (Kursdokumente). Gut geeignet als Standardagent oder als "
            "einziger Agent eines kleinen Kurses."
        ),
        "agent-10-persona.json": (
            "Verkörpert eine bestimmte Rolle oder Figur (z. B. eine Lernende mit "
            "Schwierigkeiten, eine beteiligte Person in einer Fallstudie) für "
            "Übungen zum Perspektivwechsel. Nutzt RAG, beschränkt auf das, was "
            "diese Person plausibel wissen kann."
        ),
        "agent-11-expert-feedback.json": (
            "Verkörpert eine Fachexpertin bzw. einen Fachexperten und gibt "
            "Rückmeldung zu studentischen Arbeiten in einem bestimmten "
            "Fachgebiet. Nutzt RAG mit den für dieses Fachgebiet relevanten "
            "Materialien."
        ),
        "agent-13-knowledge-test.json": (
            "Adaptive, szenariobasierte Wissensüberprüfung — stellt Übungsaufgaben "
            "und passt den Schwierigkeitsgrad an die Antworten der Lernenden an. "
            "Nutzt RAG über den gesamten Kurs. Üblicherweise einmal pro Kurs, "
            "nicht pro Thema — er bezieht sich zudem automatisch auf deine "
            "übrigen eingerichteten Agenten."
        ),
        "agent-14-backup.json": (
            "Einfacher Ausweich-Chatagent — ohne RAG, ohne Zugriff auf "
            "Kursdokumente. Für allgemeine Gespräche, bei denen keine auf "
            "Materialien gestützte Antwort nötig ist, oder als Reserveplatz."
        ),
        "agent-topic-template.json": (
            "Auf ein einzelnes Kapitel bzw. Thema des Kurses ausgerichtet — "
            "gedacht zur Mehrfachverwendung, einmal pro Kapitel. Nutzt RAG, "
            "beschränkt auf das Material dieses Kapitels. Die meisten deiner "
            "10 Plätze werden vermutlich von diesem Typ sein."
        ),
    },
}

ARCHETYPE_DESCRIPTIONS: dict[str, str] = ARCHETYPE_DESCRIPTIONS_BY_LANG["en"]

# Fields whose value is derived from other slots, not asked in that slot's own
# form — see derive_translation_tables(). Only relevant for agent-13.
DERIVED_FIELDS = {"AGENT_TRANSLATION_TABLE", "CONTENT_TRANSLATION_TABLE"}

# Fields auto_fill_from_env() (or _do_import()'s slot-number substitution)
# already fills in from .env / context — must NOT also show up as a content
# form field. Bug caught live: COURSE_NAME/EMBEDDING_MODEL/AGENT_NUMBER were
# rendering as free-text boxes the operator's input for was silently
# discarded (auto-fill runs first and overwrites those exact keys before the
# content pass ever sees them) — confusing, not just cosmetic.
AUTO_FILLED_FIELDS = {
    "COURSE_NAME", "WEAVIATE_COLLECTION_NAME", "EMBEDDING_MODEL", "AGENT_NUMBER",
    # Which course this agent belongs to. One installation can host several,
    # and every retrieval filters on it — but it comes from .env, so the
    # operator is never asked for it in the content form.
    "COURSE_ID",
}

# Shown under each content-form field — every remaining field is genuine,
# course-specific prose the operator has to write themselves, so a first-
# time non-technical user needs to know what's actually expected, not just
# a title-cased field name. One entry per field NAME (not per archetype):
# the same field means the same thing everywhere it appears. Kept short —
# the worked example in FIELD_EXAMPLES (shown as the field's placeholder
# text) is what actually shows the operator what a good answer looks like.
FIELD_HELP_BY_LANG: dict[str, dict[str, str]] = {
    "en": {
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
    },
    "de": {
        "CONCEPT_LIST": "Die konkreten Konzepte, die dieser Agent kennen soll — eines pro Zeile oder durch Komma getrennt.",
        "CONCEPT_EXAMPLE": "Ein konkretes Konzept, das diese Person ansprechen könnte oder mit dem sie Schwierigkeiten hat — macht den Dialog greifbarer.",
        "COURSE_KNOWLEDGE_DESCRIPTION": "Ein kurzer Absatz dazu, was der gesamte Kurs behandelt — gibt dem Agenten den Rahmen für seine Recherche.",
        "EXPERT_DOMAIN": "Das Fachgebiet, in dem dieser Agent als Expertin bzw. Experte antworten soll.",
        "EXPERT_KNOWLEDGE_DESCRIPTION": "Worüber diese Fachperson Bescheid weiß und wozu sie Rückmeldung geben kann.",
        "PERSONA_CONCEPTS": "Mit welchen Kurskonzepten diese Person vertraut ist (oder womit sie sich schwertut) — bestimmt, worüber sie sinnvoll sprechen kann.",
        "PERSONA_CONTEXT": "Die Ausgangslage — wer diese Person ist und warum sie in diesem Gespräch ist.",
        "PERSONA_DESCRIPTION": "Eine kurze Charakterbeschreibung — Persönlichkeit, Tonfall, Sprechweise.",
        "PERSONA_KNOWLEDGE_DESCRIPTION": "Welchen fachlichen Hintergrund diese Person mitbringt, soweit für ihre Rolle relevant.",
        "PERSONA_KNOWLEDGE_NAME": "Eine kurze Bezeichnung für diesen Hintergrund, auf die sich der Agent beziehen kann.",
        "PERSONA_NAME": "Der Name der Person, so wie die Lernenden ihn sehen.",
        "PERSONA_SITUATION": "Die konkrete Situation, in der diese Person gerade steckt — gibt dem Rollenspiel einen greifbaren Ausgangspunkt.",
        "PERSONA_STYLE_DESCRIPTION": "Wie diese Person kommuniziert — förmlich oder locker, kurze oder ausführliche Antworten usw.",
        "RESPONSE_LANGUAGE_RULE": "Ein Satz, der dem Agenten vorgibt, in welcher Sprache er antworten soll.",
        "STUDENT_ROLE": "Wer die Lernenden sind, die diesen Agenten nutzen — bestimmt, wie er sie anspricht.",
        "STUDENT_ROLE_CONTEXT": "Eine etwas ausführlichere Fassung der Lernendenrolle, aus der realistische Übungsszenarien erzeugt werden.",
        "TOPIC_KNOWLEDGE_DESCRIPTION": "Ein kurzer Absatz dazu, was dieses Kapitel behandelt — grenzt die Recherche des Agenten darauf ein.",
        "TOPIC_NAME": "Der Name dieses Kapitels bzw. Themas, so wie er in deinem Kurs vorkommt.",
        "TOPIC_SUBTOPICS": "Die Unterabschnitte innerhalb dieses Kapitels, einer pro Zeile.",
    },
}

FIELD_HELP: dict[str, str] = FIELD_HELP_BY_LANG["en"]

# Shown as the field's placeholder text (grayed-out, inside the box) — a
# complete, realistic worked example the operator can read, then overwrite
# with their own course content ("nachbauen" — rebuild the same shape with
# their own ideas). Within each language, all examples share one fictional
# course (a teacher-training module on cognitive load theory) so they read
# as one coherent, copyable pattern rather than disconnected one-liners.
# The German set is written natively rather than translated — a translated
# example reads like a translation, which is exactly the wrong model for
# someone about to write their own.
FIELD_EXAMPLES_BY_LANG: dict[str, dict[str, str]] = {
    "en": {
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
    },
    "de": {
        "CONCEPT_LIST": (
            "Arbeitsgedächtnis\nCognitive Load Theory\nIntrinsische, extrinsische "
            "und lernbezogene Belastung\nPrinzipien multimedialen Lernens"
        ),
        "CONCEPT_EXAMPLE": (
            "Verwechslung von intrinsischer Belastung (der Schwierigkeit des "
            "Stoffes selbst) mit extrinsischer Belastung (schlechter didaktischer "
            "Aufbereitung)"
        ),
        "COURSE_KNOWLEDGE_DESCRIPTION": (
            "Dieser Kurs führt angehende Lehrkräfte in die Pädagogische "
            "Psychologie ein. Behandelt werden die zentralen Lerntheorien "
            "(Behaviorismus, Kognitivismus, Konstruktivismus), Motivation und "
            "Selbstregulation, die Cognitive Load Theory sowie die Gestaltung "
            "und Auswertung von Leistungsüberprüfungen. Die Studierenden wenden "
            "diese Theorien über das Semester hinweg auf reale "
            "Unterrichtssituationen an."
        ),
        "EXPERT_DOMAIN": "Cognitive Load Theory in der Gestaltung multimedialer Lernangebote",
        "EXPERT_KNOWLEDGE_DESCRIPTION": (
            "Prüft Unterrichtsmaterialien auf übermäßige extrinsische kognitive "
            "Belastung und schlägt konkrete Überarbeitungen vor — etwa eine "
            "überladene Folie in aufeinander aufbauende Schritte zu zerlegen, "
            "redundanten Bildschirmtext durch gesprochene Erläuterung zu "
            "ersetzen oder rein dekorative Elemente ohne Lernwert zu entfernen."
        ),
        "PERSONA_CONCEPTS": (
            "Mit den grundlegenden Lerntheorien (Behaviorismus, Kognitivismus) "
            "aus dem Studium vertraut, hat sich aber nie systematisch mit der "
            "Cognitive Load Theory oder mit Gestaltungsprinzipien multimedialen "
            "Lernens befasst."
        ),
        "PERSONA_CONTEXT": (
            "Eine Lehrerin im ersten Berufsjahr, die ihre erste "
            "medienbasierte Unterrichtsstunde zum Wasserkreislauf für eine "
            "7. Klasse vorbereitet."
        ),
        "PERSONA_DESCRIPTION": (
            "Begeistert davon, digitale Medien im Unterricht einzusetzen, aber "
            "schnell überfordert, wenn es zu theoretisch wird. Fragt viel nach "
            "und will vor allem wissen: „Was heißt das jetzt konkret für meinen "
            "Unterricht?“"
        ),
        "PERSONA_KNOWLEDGE_DESCRIPTION": (
            "Drei Jahre Unterrichtserfahrung und ein abgeschlossenes "
            "Lehramtsstudium, aber keine fachliche Vorbildung in "
            "Instruktionsdesign oder Medienpsychologie."
        ),
        "PERSONA_KNOWLEDGE_NAME": "Unterrichtserfahrung",
        "PERSONA_NAME": "Sarah, Lehrerin im ersten Berufsjahr",
        "PERSONA_SITUATION": (
            "Sarah hat gerade eine 20-seitige Präsentation zum Wasserkreislauf "
            "erstellt, voll mit Text, Grafiken und Animationen, und wundert "
            "sich, warum ihre Klasse eher verwirrt als interessiert wirkt."
        ),
        "PERSONA_STYLE_DESCRIPTION": (
            "Lockerer, gesprächiger Ton. Kurze Nachrichten, gelegentlich etwas "
            "Smalltalk aus dem Lehrerzimmer. Vermeidet Fachjargon, solange ihr "
            "Gegenüber ihn nicht selbst einbringt."
        ),
        "RESPONSE_LANGUAGE_RULE": (
            "Antworte immer auf Deutsch, unabhängig davon, in welcher Sprache "
            "die Lernenden schreiben."
        ),
        "STUDENT_ROLE": "Lehramtsstudierende im zweiten Studienjahr",
        "STUDENT_ROLE_CONTEXT": (
            "Lehramtsstudierende, die gerade ihr Praxissemester absolvieren und "
            "echte Unterrichtsmaterialien entwerfen müssen, die ihre "
            "Mentorinnen und Mentoren anschließend begutachten."
        ),
        "TOPIC_KNOWLEDGE_DESCRIPTION": (
            "Behandelt Baddeleys Arbeitsgedächtnismodell, Swellers Cognitive "
            "Load Theory (intrinsische, extrinsische und lernbezogene "
            "Belastung) sowie Mayers Prinzipien multimedialen Lernens "
            "(Kohärenz, Signalgebung, Redundanz, räumliche und zeitliche Nähe)."
        ),
        "TOPIC_NAME": "Kapitel 4: Kognitive Lernvoraussetzungen",
        "TOPIC_SUBTOPICS": (
            "4.1 Das Drei-Speicher-Modell des Gedächtnisses\n"
            "4.2 Cognitive Load Theory\n"
            "4.3 Prinzipien multimedialen Lernens"
        ),
    },
}

FIELD_EXAMPLES: dict[str, str] = FIELD_EXAMPLES_BY_LANG["en"]

# The form's visible label for each field. Without this the label is derived
# from the placeholder name (TOPIC_NAME -> "Topic Name"), which is fine in
# English and nonsense in German.
FIELD_LABELS_BY_LANG: dict[str, dict[str, str]] = {
    # English labels stay derived from the field name — listing them here
    # would just be the same strings twice, and any field added to a
    # template later then works without a code change.
    "en": {},
    "de": {
        "CONCEPT_LIST": "Konzeptliste",
        "CONCEPT_EXAMPLE": "Beispielkonzept",
        "COURSE_KNOWLEDGE_DESCRIPTION": "Beschreibung des Kursinhalts",
        "EXPERT_DOMAIN": "Fachgebiet",
        "EXPERT_KNOWLEDGE_DESCRIPTION": "Beschreibung des Expertenwissens",
        "PERSONA_CONCEPTS": "Konzepte der Person",
        "PERSONA_CONTEXT": "Hintergrund der Person",
        "PERSONA_DESCRIPTION": "Beschreibung der Person",
        "PERSONA_KNOWLEDGE_DESCRIPTION": "Fachlicher Hintergrund der Person",
        "PERSONA_KNOWLEDGE_NAME": "Bezeichnung des Hintergrunds",
        "PERSONA_NAME": "Name der Person",
        "PERSONA_SITUATION": "Situation der Person",
        "PERSONA_STYLE_DESCRIPTION": "Kommunikationsstil der Person",
        "RESPONSE_LANGUAGE_RULE": "Sprachregel für Antworten",
        "STUDENT_ROLE": "Rolle der Lernenden",
        "STUDENT_ROLE_CONTEXT": "Kontext der Lernendenrolle",
        "TOPIC_KNOWLEDGE_DESCRIPTION": "Beschreibung des Kapitelinhalts",
        "TOPIC_NAME": "Name des Kapitels",
        "TOPIC_SUBTOPICS": "Unterabschnitte",
    },
}


def _for_lang(catalog: dict[str, dict[str, str]], lang: str) -> dict[str, str]:
    """English entries merged under the requested language, so a key only
    present in English still resolves instead of vanishing from the form."""
    merged = dict(catalog.get("en", {}))
    merged.update(catalog.get(lang, {}))
    return merged


def archetypes_for(lang: str = "en") -> dict[str, str]:
    return _for_lang(ARCHETYPES_BY_LANG, lang)


def archetype_descriptions_for(lang: str = "en") -> dict[str, str]:
    return _for_lang(ARCHETYPE_DESCRIPTIONS_BY_LANG, lang)


def field_help_for(lang: str = "en") -> dict[str, str]:
    return _for_lang(FIELD_HELP_BY_LANG, lang)


def field_examples_for(lang: str = "en") -> dict[str, str]:
    return _for_lang(FIELD_EXAMPLES_BY_LANG, lang)


def field_labels_for(lang: str = "en") -> dict[str, str]:
    return _for_lang(FIELD_LABELS_BY_LANG, lang)


def _find_prompt_holder(flow_data: dict[str, Any]) -> dict | None:
    """
    Locates the message object holding the agent's system prompt.

    Every shipped template happens to keep it at nodes[1], but that's
    incidental — searching for the node that actually declares
    agentMessages survives someone reordering nodes in the Flowise editor
    and re-exporting.
    """
    for node in flow_data.get("nodes", []):
        messages = node.get("data", {}).get("inputs", {}).get("agentMessages")
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict) and "content" in first:
                return first
    return None


def default_prompt_for(archetype_file: str) -> str:
    """The system prompt as it ships in the template, placeholders intact."""
    holder = _find_prompt_holder(load_template(archetype_file))
    return holder.get("content", "") if holder else ""


def set_prompt(flow_data: dict[str, Any], prompt: str) -> bool:
    """Replaces the system prompt in an already-loaded flow. Returns False
    if this template has no prompt node to replace (agent-14 has one, but
    a future template might not)."""
    holder = _find_prompt_holder(flow_data)
    if holder is None:
        return False
    holder["content"] = prompt
    return True


def placeholders_for(archetype_file: str, prompt_override: str | None = None) -> list[str]:
    """
    All {{PLACEHOLDER}} names the operator actually needs to fill in —
    i.e. minus the derived and auto-filled ones. Drives the content form.

    `prompt_override` is the slot's edited system prompt, when it has one.
    The placeholder set is then read from the prompt the import will
    actually use, not from the shipped template: adding {{MY_FIELD}} while
    editing makes a matching input appear, and deleting a placeholder
    retires the field that fed it. Without this the form would keep asking
    for values the prompt no longer mentions — and silently fail at import
    on ones it newly does.
    """
    flow = load_template(archetype_file)
    if prompt_override is not None:
        set_prompt(flow, prompt_override)
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


def flow_digest(flow_data: dict[str, Any]) -> str:
    """A stable fingerprint of a built flow.

    An agent in Flowise is a copy of this structure, not a reference to it, so
    "was this imported" is not the useful question — "was *this version*
    imported" is. Taken before credential ids are stamped in, so it can be
    recomputed at any time without asking Flowise anything, which is what lets
    a page say "behind" for ten slots without ten round trips.

    sort_keys, because Python preserves insertion order and a template edit
    that only moves a key would otherwise read as a change.
    """
    canonical = json.dumps(flow_data, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    flow_data: dict[str, Any], env: dict[str, str], slot: int | None = None,
    course: dict | None = None,
) -> None:
    """
    Pass 1: fill in everything already known from the CLI wizard (or, for
    AGENT_NUMBER, from which slot this is) — the operator is never asked for
    these in the content form (see AUTO_FILLED_FIELDS). Mutates flow_data in
    place. Must run BEFORE substitute_content(), since {{COURSE_NAME}},
    {{COURSE_ID}} and {{AGENT_NUMBER}} are all handled here, not as content
    fields.
    """
    llm_provider, llm_map = resolve_llm_provider(env)
    embed_provider, embed_map = resolve_embedding_provider(env)

    # The course comes from the caller. Those three values were once
    # installation-wide in .env, which is exactly what stopped there being
    # more than one course: an agent's retrieval filter and its collection are
    # properties of the course it belongs to.
    #
    # The .env fallback is gone with the keys themselves — the installer no
    # longer writes COURSE_NAME, COURSE_ID or WEAVIATE_COLLECTION_NAME,
    # because an installation no longer has a course. A caller with no course
    # is the template preview, and it gets empty strings: a preview showing
    # "{{COURSE_NAME}}" unfilled is honest, while one showing some other
    # course's collection name would not be.
    if course:
        course_name = course.get("name", "")
        course_id = course.get("id", "")
        weaviate_collection = course.get("collection", "")
    else:
        course_name = ""
        course_id = ""
        weaviate_collection = ""
    embedding_model = env.get("EMBEDDING_MODEL", "")
    agent_number = str(slot) if slot is not None else ""

    def replace_context_fields(text: str) -> str:
        def sub(m: re.Match) -> str:
            if m.group(1) == "COURSE_NAME":
                return course_name
            if m.group(1) == "AGENT_NUMBER":
                return agent_number
            if m.group(1) == "COURSE_ID":
                return course_id
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


def _set_config_credential(cfg: Any, credential_id: str) -> None:
    """Attach a credential to one node config block.

    Flowise's agentflow nodes read the credential from FLOWISE_CREDENTIAL_ID,
    NOT from the `credential` key the templates ship with. Verified in the
    pinned version's own source (flowise@3.1.3):

        Agent.ts:909   credential: modelConfig['FLOWISE_CREDENTIAL_ID']
        Agent.ts:830   credential: selectedEmbeddingModelConfig['FLOWISE_CREDENTIAL_ID']
        Agent.ts:845   credential: selectedVectorStoreConfig['FLOWISE_CREDENTIAL_ID']
        LLM.ts:376     credential: modelConfig['FLOWISE_CREDENTIAL_ID']

    Setting only `credential` — which is what this function used to do for
    the LLM configs — means the node starts with no credential at all and
    falls back to the provider SDK's own environment variable, which in a
    container that has none produces "Missing credentials. Please pass an
    apiKey, or set the OPENAI_API_KEY environment variable" at the first
    message, regardless of which provider was configured.

    Both keys are written: FLOWISE_CREDENTIAL_ID is what the runtime reads,
    and `credential` is what the templates declare and the canvas UI shows,
    so keeping them in sync avoids an imported agent that works but looks
    unconfigured when opened in Flowise.
    """
    if not isinstance(cfg, dict):
        return
    cfg["FLOWISE_CREDENTIAL_ID"] = credential_id
    cfg["credential"] = credential_id


def set_credential_ids(
    flow_data: dict[str, Any],
    llm_credential_id: str,
    embed_credential_id: str,
    vectorstore_credential_id: str = "",
) -> None:
    """Wire the Flowise credential IDs (created via flowise_client) into every
    node that references one — the templates ship with credential: ""."""
    for node in flow_data.get("nodes", []):
        inputs = node.get("data", {}).get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            # A node without inputs is a note or a malformed template entry —
            # skip it rather than failing the whole import over it.
            continue
        for cfg_key in ("agentModelConfig", "llmModelConfig"):
            _set_config_credential(inputs.get(cfg_key), llm_credential_id)

        vs_list = inputs.get("agentKnowledgeVSEmbeddings")
        if isinstance(vs_list, list):
            for vs in vs_list:
                if not isinstance(vs, dict):
                    continue
                _set_config_credential(vs.get("embeddingModelConfig"), embed_credential_id)
                # Weaviate runs with AUTHENTICATION_APIKEY_ENABLED=true (see
                # docker-compose.yml), so retrieval needs a credential too —
                # without one the agent answers, then fails to retrieve.
                if vectorstore_credential_id:
                    _set_config_credential(
                        vs.get("vectorStoreConfig"), vectorstore_credential_id
                    )
