"""
SMART RAG — Content Admin GUI
================================

Course-content authoring: fill in the Flowise agent templates' course-specific
placeholders and import them, plus a guided (non-automated) path to seed the
Neo4j concept graph. Deliberately app-to-app only (Flowise/Neo4j REST APIs) —
never Docker socket or host filesystem access; see the batch plan for why
that's a hard boundary versus scripts/admin.sh.

Configuration (all via .env, read through env_file.py):
  CONTENT_ADMIN_USERNAME / CONTENT_ADMIN_PASSWORD_HASH  — this GUI's own login
  CONTENT_ADMIN_SESSION_SECRET                           — Flask session signing
  FLOWISE_API_KEY                                        — set via /flowise-setup,
                                                            not the CLI wizard
  DOMAIN, WEAVIATE_*, NEO4J_*, LLM_*, EMBEDDING_*, COURSE_NAME — all already
                                                            written by bootstrap.sh
"""

import logging
import os

from flask import Flask, redirect, render_template, request, session, url_for

import agent_templates
import auth
import storage
from env_file import read_env, set_env_var
from flowise_client import FlowiseClient, FlowiseError
from neo4j_client import Neo4jClient, Neo4jError

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
logger = logging.getLogger("smartrag.content-admin")

app = Flask(__name__)
app.secret_key = os.getenv("CONTENT_ADMIN_SESSION_SECRET") or read_env().get(
    "CONTENT_ADMIN_SESSION_SECRET", ""
)
if not app.secret_key:
    raise RuntimeError(
        "CONTENT_ADMIN_SESSION_SECRET is not set in .env — refusing to start "
        "with an unsigned/insecure session."
    )


# Internal Docker hostname, not the public smart-rag.<domain> URL — this
# container is already on smart-rag-network, so going out through nginx/DNS/
# SSL would be a pointless detour (and a needless dependency on the cert
# being valid) for a purely internal service-to-service call.
FLOWISE_INTERNAL_URL = "http://smartrag-flowise:3000/api/v1"


def _flowise_client() -> FlowiseClient | None:
    env = read_env()
    api_key = env.get("FLOWISE_API_KEY")
    if not api_key:
        return None
    return FlowiseClient(FLOWISE_INTERNAL_URL, api_key)


def _neo4j_client() -> Neo4jClient:
    env = read_env()
    return Neo4jClient(
        base_url="http://smartrag-neo4j:7474",
        user="neo4j",
        password=env.get("NEO4J_PASSWORD", ""),
    )


# ─── First-run setup ───────────────────────────────────────────────────────────
@app.route("/setup", methods=["GET", "POST"])
def setup():
    if auth.is_configured():
        return redirect(url_for("login"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not username or not password:
            error = "Username and password are required."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 12:
            error = "Password must be at least 12 characters."
        else:
            auth.create_admin_account(username, password)
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("flowise_setup"))
    return render_template("setup.html", error=error)


# ─── Login / logout ─────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth.is_configured():
        return redirect(url_for("setup"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if auth.verify_login(username, password):
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Flowise connection setup ───────────────────────────────────────────────────
@app.route("/flowise-setup", methods=["GET", "POST"])
@auth.login_required
def flowise_setup():
    env = read_env()
    error = None
    success = None
    if request.method == "POST":
        api_key = request.form.get("api_key", "").strip()
        if not api_key:
            error = "API key is required."
        else:
            try:
                FlowiseClient(FLOWISE_INTERNAL_URL, api_key).check_connection()
            except FlowiseError as exc:
                error = f"Could not connect to Flowise with this key: {exc}"
            else:
                set_env_var("FLOWISE_API_KEY", api_key)
                success = "Connected — Flowise API key saved."
                env = read_env()
    return render_template(
        "flowise_setup.html",
        error=error,
        success=success,
        already_set=bool(env.get("FLOWISE_API_KEY")),
    )


# ─── Dashboard ───────────────────────────────────────────────────────────────────
@app.route("/")
@auth.login_required
def dashboard():
    env = read_env()
    slots = storage.all_slots()
    return render_template(
        "dashboard.html",
        slots=slots,
        archetypes=agent_templates.ARCHETYPES,
        flowise_configured=bool(env.get("FLOWISE_API_KEY")),
    )


# ─── Agent slot: choose archetype, fill content, import ────────────────────────
@app.route("/slot/<int:slot>", methods=["GET", "POST"])
@auth.login_required
def slot_view(slot: int):
    if not (1 <= slot <= storage.MAX_SLOTS):
        return "Invalid slot", 404

    existing = storage.get_slot(slot)
    error = None
    success = None

    try:
        if request.method == "POST":
            action = request.form.get("action")
            archetype = request.form.get("archetype", existing.get("archetype", ""))

            if action == "choose_archetype":
                existing = {"archetype": archetype, "name": "", "content": {}, "chatflow_id": None}

            elif action in ("save", "import"):
                fields = agent_templates.placeholders_for(archetype)
                content = {f: request.form.get(f, "") for f in fields}
                name = request.form.get("name", "").strip()

                if not name:
                    error = "Please give this agent a name."
                elif storage.name_taken(name, exclude_slot=slot):
                    error = (
                        f'The name "{name}" is already used by another agent — '
                        "each agent needs a unique name."
                    )

                if error:
                    # Redisplay what the user typed instead of discarding it
                    # in favor of the last-saved state.
                    existing = {
                        "archetype": archetype,
                        "name": name,
                        "content": content,
                        "chatflow_id": existing.get("chatflow_id"),
                    }
                else:
                    storage.save_slot(slot, archetype, content, name)
                    existing = storage.get_slot(slot)

                    if action == "import":
                        client = _flowise_client()
                        if client is None:
                            error = "Flowise isn't connected yet — set it up first."
                        else:
                            try:
                                error = _do_import(slot, archetype, client)
                                if not error:
                                    success = "Imported into Flowise."
                                    existing = storage.get_slot(slot)
                            except FlowiseError as exc:
                                error = f"Flowise import failed: {exc}"

        fields = agent_templates.placeholders_for(existing.get("archetype", "")) if existing.get(
            "archetype"
        ) else []
    except agent_templates.TemplateError as exc:
        # Surfaces cleanly instead of a bare 500 — this is exactly the class
        # of error that hit in practice (SMARTRAG_TEMPLATES_DIR pointing at
        # a path that doesn't exist in this container), and a plain
        # "Internal Server Error" page gives the operator nothing to act on.
        logger.error("Template load failed for slot %s: %s", slot, exc)
        error = f"Could not load agent template: {exc}"
        fields = []

    return render_template(
        "slot.html",
        slot=slot,
        archetypes=agent_templates.ARCHETYPES,
        descriptions=agent_templates.ARCHETYPE_DESCRIPTIONS,
        field_help=agent_templates.FIELD_HELP,
        field_examples=agent_templates.FIELD_EXAMPLES,
        existing=existing,
        fields=fields,
        error=error,
        success=success,
    )


def _do_import(slot: int, archetype: str, client: FlowiseClient) -> str | None:
    """Returns an error message, or None on success."""
    env = read_env()
    all_slots = storage.all_slots()
    slot_data = all_slots[str(slot)]
    content = dict(slot_data.get("content") or {})

    # agent-13 only: fill the two derived fields from the other slots.
    content.update(
        {
            k: v
            for k, v in agent_templates.derive_translation_tables(all_slots).items()
            if k in agent_templates.DERIVED_FIELDS
        }
    )

    flow = agent_templates.load_template(archetype)
    agent_templates.auto_fill_from_env(flow, env, slot=slot)
    missing = agent_templates.substitute_content(flow, content)
    if missing:
        return f"Missing content for: {', '.join(missing)} — fill in the form and save first."

    llm_map = agent_templates.LLM_PROVIDER_MAP.get(
        env.get("LLM_PROVIDER", "anthropic"), agent_templates.LLM_PROVIDER_MAP["anthropic"]
    )
    embed_map = agent_templates.EMBEDDING_PROVIDER_MAP.get(
        env.get("EMBEDDING_PROVIDER", "openai"), agent_templates.EMBEDDING_PROVIDER_MAP["openai"]
    )

    llm_cred_id = client.get_or_create_credential(
        f"smartrag-llm-{env.get('LLM_PROVIDER', 'anthropic')}",
        llm_map["credential_name"],
        {llm_map["credential_key"]: env.get("LLM_API_KEY", "")},
    )
    embed_cred_id = client.get_or_create_credential(
        f"smartrag-embedding-{env.get('EMBEDDING_PROVIDER', 'openai')}",
        embed_map["credential_name"],
        {embed_map["credential_key"]: env.get("EMBEDDING_API_KEY", "")},
    )
    agent_templates.set_credential_ids(flow, llm_cred_id, embed_cred_id)

    # The 5 secrets the custom-function nodes read via Flowise "Variables"
    # ($vars?.X) rather than as node inputs — see flowise/agents/*.json's
    # custom-function code (Load UserMemory, Load neo4j Prerequisites, etc.)
    for name, value in (
        ("EMBEDDING_API_KEY", env.get("EMBEDDING_API_KEY", "")),
        ("EMBEDDING_BASE_URL", env.get("EMBEDDING_BASE_URL", "")),
        ("EMBEDDING_MODEL", env.get("EMBEDDING_MODEL", "")),
        ("WEAVIATE_API_KEY", env.get("WEAVIATE_API_KEY", "")),
        ("NEO4J_PASSWORD", env.get("NEO4J_PASSWORD", "")),
    ):
        client.get_or_create_variable(name, value)

    agent_name = slot_data.get("name") or f"Agent {slot:02d}"
    chatflow_name = f"SMART RAG — {agent_name}"
    flow_data_json = __import__("json").dumps(flow)
    chatflow_id, _created = client.upsert_chatflow(chatflow_name, flow_data_json)
    storage.set_chatflow_id(slot, chatflow_id)
    set_env_var(f"CHATFLOW_AGENT{slot:02d}", chatflow_id)
    return None


# ─── Knowledge-graph guidance ────────────────────────────────────────────────────
@app.route("/graph-guidance", methods=["GET", "POST"])
@auth.login_required
def graph_guidance():
    error = None
    success = None
    if request.method == "POST":
        cypher = request.form.get("cypher", "")
        try:
            results = _neo4j_client().run_script(cypher)
            success = f"Executed {len(results)} statement(s) successfully."
        except Neo4jError as exc:
            error = str(exc)
    return render_template("graph_guidance.html", error=error, success=success)
