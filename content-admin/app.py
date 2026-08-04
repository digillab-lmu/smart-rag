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
import citation
import i18n
import storage
from env_file import read_env, set_env_var
from flowise_client import FlowiseClient, FlowiseError
from llm_client import LLMError, optimize_field
from n8n_client import N8nClient, N8nError
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
# Same reasoning — n8n's own container port (see docker-compose.yml's
# comment on why N8N_PORT is pinned to 5678 internally regardless of the
# host-side binding).
N8N_INTERNAL_URL = "http://smartrag-n8n:5678"

# Formats Docling accepts, mirrored from the upload form's accept attribute
# so a file rejected here is never one the pipeline could have handled.
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm", ".md",
    ".adoc", ".asciidoc", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
}
# Guards against a stray multi-GB upload wedging the single gunicorn
# worker while it streams. Docling itself gets much longer to work.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def _flowise_client() -> FlowiseClient | None:
    env = read_env()
    api_key = env.get("FLOWISE_API_KEY")
    if not api_key:
        return None
    return FlowiseClient(FLOWISE_INTERNAL_URL, api_key)


def _n8n_client() -> N8nClient:
    return N8nClient(N8N_INTERNAL_URL)


# ─── Language ───────────────────────────────────────────────────────────────────
def current_language() -> str:
    """Cookie first (an explicit choice), browser preference otherwise."""
    cookie = request.cookies.get(i18n.LANGUAGE_COOKIE)
    if cookie:
        return i18n.normalize_language(cookie)
    return i18n.language_from_accept_header(request.headers.get("Accept-Language"))


def _t(key: str, *args) -> str:
    """Request-scoped translate — resolves the language itself so call sites
    (and templates) never have to pass it around."""
    return i18n.t(key, *args, lang=current_language())


@app.context_processor
def inject_i18n():
    """Makes t(), the active language, and the language list available to
    every template without each route having to pass them in."""
    return {
        "t": _t,
        "lang": current_language(),
        "languages": i18n.LANGUAGES,
    }


@app.route("/language/<lang>")
def set_language(lang: str):
    """Sets the language cookie, then returns the operator to where they
    were — the switch appears on every page, so bouncing to the dashboard
    would lose their place. Only same-site relative paths are honoured, so
    the Referer header can't be used to redirect somewhere else."""
    target = request.referrer or url_for("dashboard")
    if "://" in target:
        from urllib.parse import urlparse

        parsed = urlparse(target)
        if parsed.netloc and parsed.netloc != request.host:
            target = url_for("dashboard")
        else:
            target = parsed.path or url_for("dashboard")

    resp = redirect(target)
    resp.set_cookie(
        i18n.LANGUAGE_COOKIE,
        i18n.normalize_language(lang),
        max_age=i18n.LANGUAGE_COOKIE_MAX_AGE,
        samesite="Lax",
        httponly=False,
    )
    return resp


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
            error = _t("setup_err_required")
        elif password != confirm:
            error = _t("setup_err_mismatch")
        elif len(password) < 12:
            error = _t("setup_err_too_short")
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
        error = _t("login_err_invalid")
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
            error = _t("flowise_err_required")
        else:
            try:
                FlowiseClient(FLOWISE_INTERNAL_URL, api_key).check_connection()
            except FlowiseError as exc:
                error = _t("flowise_err_connect", exc)
            else:
                set_env_var("FLOWISE_API_KEY", api_key)
                success = _t("flowise_saved")
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
        archetypes=agent_templates.archetypes_for(current_language()),
        flowise_configured=bool(env.get("FLOWISE_API_KEY")),
    )


# ─── Agent slot: choose archetype, fill content, import ────────────────────────
@app.route("/slot/<int:slot>", methods=["GET", "POST"])
@auth.login_required
def slot_view(slot: int):
    if not (1 <= slot <= storage.MAX_SLOTS):
        return _t("slot_err_invalid"), 404

    existing = storage.get_slot(slot)
    error = None
    success = None

    try:
        if request.method == "POST":
            action = request.form.get("action")
            archetype = request.form.get("archetype", existing.get("archetype", ""))

            if action == "choose_archetype":
                existing = {
                    "archetype": archetype,
                    "name": "",
                    "content": {},
                    "system_prompt": None,
                    "chatflow_id": None,
                }

            elif action in ("save", "import", "reset_prompt"):
                submitted_prompt = request.form.get("system_prompt", "")
                default_prompt = agent_templates.default_prompt_for(archetype)

                if action == "reset_prompt":
                    # Discard the edit and go back to the shipped wording.
                    # Stored as None rather than a copy of the default, so
                    # the slot resumes tracking the template.
                    submitted_prompt = default_prompt
                    system_prompt = None
                else:
                    # Only an actual change is stored; an untouched prompt
                    # stays None so template updates keep reaching this slot.
                    system_prompt = (
                        submitted_prompt.strip()
                        if submitted_prompt.strip()
                        and submitted_prompt.strip() != default_prompt.strip()
                        else None
                    )

                # Fields come from the prompt as edited, so a placeholder
                # added here immediately gets an input of its own.
                fields = agent_templates.placeholders_for(archetype, submitted_prompt)
                content = {f: request.form.get(f, "") for f in fields}
                name = request.form.get("name", "").strip()

                if not name:
                    error = _t("slot_err_name_required")
                elif storage.name_taken(name, exclude_slot=slot):
                    error = _t("slot_err_name_taken", name)

                if error:
                    # Redisplay what the user typed instead of discarding it
                    # in favor of the last-saved state.
                    existing = {
                        "archetype": archetype,
                        "name": name,
                        "content": content,
                        "system_prompt": system_prompt,
                        "chatflow_id": existing.get("chatflow_id"),
                    }
                else:
                    storage.save_slot(slot, archetype, content, name, system_prompt)
                    existing = storage.get_slot(slot)
                    if action == "reset_prompt":
                        success = _t("slot_prompt_reset_ok")

                    if action == "import":
                        client = _flowise_client()
                        if client is None:
                            error = _t("slot_err_not_connected")
                        else:
                            try:
                                error = _do_import(slot, archetype, client)
                                if not error:
                                    success = _t("slot_imported_ok")
                                    existing = storage.get_slot(slot)
                            except FlowiseError as exc:
                                error = _t("slot_err_import_failed", exc)

        fields = (
            agent_templates.placeholders_for(
                existing.get("archetype", ""), existing.get("system_prompt")
            )
            if existing.get("archetype")
            else []
        )
    except agent_templates.TemplateError as exc:
        # Surfaces cleanly instead of a bare 500 — this is exactly the class
        # of error that hit in practice (SMARTRAG_TEMPLATES_DIR pointing at
        # a path that doesn't exist in this container), and a plain
        # "Internal Server Error" page gives the operator nothing to act on.
        logger.error("Template load failed for slot %s: %s", slot, exc)
        error = _t("slot_err_template", exc)
        fields = []

    return render_template(
        "slot.html",
        slot=slot,
        archetypes=agent_templates.archetypes_for(current_language()),
        descriptions=agent_templates.archetype_descriptions_for(current_language()),
        field_help=agent_templates.field_help_for(current_language()),
        field_examples=agent_templates.field_examples_for(current_language()),
        field_labels=agent_templates.field_labels_for(current_language()),
        existing=existing,
        fields=fields,
        system_prompt=(
            existing.get("system_prompt")
            or (
                agent_templates.default_prompt_for(existing["archetype"])
                if existing.get("archetype")
                else ""
            )
        ),
        prompt_is_customised=bool(existing.get("system_prompt")),
        error=error,
        success=success,
    )


# ─── "Optimize with AI" — one-shot, per-field content suggestion ────────────────
@app.route("/slot/<int:slot>/optimize", methods=["POST"])
@auth.login_required
def slot_optimize(slot: int):
    if not (1 <= slot <= storage.MAX_SLOTS):
        return {"error": _t("slot_err_invalid")}, 404

    payload = request.get_json(silent=True) or {}
    field = payload.get("field", "")
    text = payload.get("text", "")

    # Scoped to known content fields only — refuses to spend an LLM call on
    # arbitrary field names the client might send.
    if field not in agent_templates.FIELD_HELP:
        return {"error": _t("slot_err_unknown_field")}, 400

    env = read_env()
    try:
        lang = current_language()
        result = optimize_field(
            field,
            agent_templates.field_help_for(lang)[field],
            text,
            env,
            language=lang,
        )
    except LLMError as exc:
        logger.error("Optimize failed for slot %s field %s: %s", slot, field, exc)
        return {"error": str(exc)}, 502
    return result


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
    # An edited system prompt replaces the shipped one before any
    # substitution runs, so its placeholders are filled like the
    # template's own.
    custom_prompt = slot_data.get("system_prompt")
    if custom_prompt:
        agent_templates.set_prompt(flow, custom_prompt)
    agent_templates.auto_fill_from_env(flow, env, slot=slot)
    missing = agent_templates.substitute_content(flow, content)
    if missing:
        return _t("slot_err_missing_content", ", ".join(missing))

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


# ─── Document upload (RAG ingest) ───────────────────────────────────────────────
@app.route("/upload", methods=["GET", "POST"])
@auth.login_required
def upload():
    slots = storage.all_slots()
    # Only slots that actually have an agent configured — uploading to an
    # empty slot would tag chunks with an agent_id nothing retrieves.
    configured = {
        num: data for num, data in slots.items() if data.get("archetype")
    }

    error = None
    success = None
    form = {}

    if request.method == "POST":
        form = {
            "slot": request.form.get("slot", ""),
            "title": request.form.get("title", "").strip(),
            "authors": request.form.get("authors", "").strip(),
            "year": request.form.get("year", "").strip(),
            "topic": request.form.get("topic", "").strip(),
            "language": request.form.get("language", "de"),
            "force_ocr": request.form.get("force_ocr") == "on",
            "notify_email": request.form.get("notify_email", "").strip(),
        }
        upload_file = request.files.get("document")

        if not form["slot"] or form["slot"] not in configured:
            error = _t("upload_err_no_slot")
        elif not upload_file or not upload_file.filename:
            error = _t("upload_err_no_file")
        elif os.path.splitext(upload_file.filename)[1].lower() not in ALLOWED_UPLOAD_EXTENSIONS:
            error = _t(
                "upload_err_bad_type",
                upload_file.filename,
                ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS)),
            )
        elif form["year"] and not form["year"].isdigit():
            error = _t("upload_err_bad_year")
        else:
            try:
                _n8n_client().upload_document(
                    file_stream=upload_file.stream,
                    filename=upload_file.filename,
                    content_type=upload_file.mimetype or "application/octet-stream",
                    agent_id=int(form["slot"]),
                    # Falling back to the filename matches what the ingest
                    # workflow does anyway if title is empty — doing it here
                    # too just makes the value visible to the operator.
                    title=form["title"] or os.path.splitext(upload_file.filename)[0],
                    authors=form["authors"],
                    year=form["year"],
                    topic=form["topic"],
                    language=form["language"],
                    force_ocr=form["force_ocr"],
                    notify_email=form["notify_email"],
                )
            except N8nError as exc:
                logger.error("Upload failed for slot %s: %s", form["slot"], exc)
                error = _t("upload_err_failed", exc)
            else:
                agent_label = configured[form["slot"]].get("name") or f"Agent {form['slot']}"
                success = _t("upload_ok", upload_file.filename, agent_label)
                form = {}

    return render_template(
        "upload.html",
        configured=configured,
        form=form,
        error=error,
        success=success,
    )


@app.route("/upload/lookup", methods=["POST"])
@auth.login_required
def upload_lookup():
    """
    Resolves bibliographic metadata so the operator doesn't retype it.

    Two modes, both answering with the same shape so the page handles them
    identically:
      - JSON body {"identifier": "..."} — a DOI or ISBN typed/pasted in.
      - multipart with a file — scan the PDF's front matter for a DOI or
        ISBN, then look that up.

    Nothing is written anywhere: the answer is a suggestion the operator
    confirms before it touches a single form field.
    """
    identifier = ""
    found_via = ""

    upload_file = request.files.get("document")
    if upload_file and upload_file.filename:
        identifiers = citation.scan_pdf(upload_file.stream)
        # Rewind: this same stream is not reused here, but leaving a
        # consumed file object behind is a trap for any later handler.
        try:
            upload_file.stream.seek(0)
        except (OSError, ValueError):
            pass
        identifier = identifiers.get("doi") or identifiers.get("isbn") or ""
        if not identifier:
            return {"error": _t("lookup_err_nothing_in_pdf")}, 404
        found_via = "doi" if identifiers.get("doi") else "isbn"
    else:
        payload = request.get_json(silent=True) or {}
        identifier = (payload.get("identifier") or "").strip()
        if not identifier:
            return {"error": _t("lookup_err_no_identifier")}, 400

    try:
        # A DOI always starts "10." — anything else is treated as an ISBN,
        # which then has to survive its own checksum check.
        if found_via == "isbn" or (
            not found_via and not citation.DOI_RE.search(identifier)
        ):
            result = citation.lookup_isbn(identifier)
        else:
            result = citation.lookup_doi(identifier)
    except citation.CitationNotFound as exc:
        logger.info("Citation lookup found nothing for %r: %s", identifier, exc)
        return {"error": str(exc)}, 404
    except citation.CitationError as exc:
        logger.error("Citation lookup failed for %r: %s", identifier, exc)
        return {"error": str(exc)}, 502

    result["identifier"] = identifier
    return result


@app.errorhandler(413)
def upload_too_large(_exc):
    """Flask aborts with 413 before the route ever runs when a request
    exceeds MAX_CONTENT_LENGTH — without this the operator would get a bare
    error page with no hint about what the limit is."""
    return (
        render_template(
            "upload.html",
            configured={
                num: data
                for num, data in storage.all_slots().items()
                if data.get("archetype")
            },
            form={},
            error=_t("upload_err_too_large", MAX_UPLOAD_BYTES // (1024 * 1024)),
            success=None,
        ),
        413,
    )


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
            success = _t("graph_ok", len(results))
        except Neo4jError as exc:
            error = str(exc)
    return render_template("graph_guidance.html", error=error, success=success)
