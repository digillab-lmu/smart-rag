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

import hmac
import json
import logging
import os
import secrets
from datetime import date

from functools import wraps

from flask import (Flask, g, jsonify, redirect, render_template, request,
                   session, url_for)

import accounts
import agent_templates
import auth
import citation
import courses as courses_service
import db
import i18n
import ingest_status
import llm_client
import learners
import mailer
import setup_checks
import storage
from env_file import read_env, set_env_var
from flowise_client import FlowiseClient, FlowiseError
from llm_client import LLMError, optimize_field, suggest_keywords
from n8n_client import N8nClient, N8nError
from weaviate_client import WeaviateClient, WeaviateError
import graph_builds
import neo4j_client
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

# Bring the schema up to date before serving the first request.
#
# db.py used to say this was deliberately not wired in, "because nothing reads
# these tables in this phase". That stopped being true two phases later:
# courses, accounts and agent slots all live here now, and there was still no
# path that applied a migration. The admin menu's "Upgrade — apply pending
# migrations" checks .env keys and stale state and never touches the SQL, so
# migration 001 reached this installation only because somebody ran the CLI by
# hand. The next one would have arrived as a missing column at request time.
#
# Safe at startup because the runner takes an advisory lock: two containers
# coming up together do not race, the second waits and then finds nothing to
# do. Fatal on failure, because every page needs this schema — a container
# that serves with the wrong shape fails later and less clearly.
try:
    _applied = db.migrate()
    if _applied:
        logger.info("Applied database migration(s): %s",
                    ", ".join(str(v) for v in _applied))
except db.DatabaseError as exc:
    raise RuntimeError(
        f"The database schema could not be brought up to date: {exc}"
    ) from exc


# Internal Docker hostname, not the public smart-rag.<domain> URL — this
# container is already on smart-rag-network, so going out through nginx/DNS/
# SSL would be a pointless detour (and a needless dependency on the cert
# being valid) for a purely internal service-to-service call.
FLOWISE_INTERNAL_URL = "http://smartrag-flowise:3000/api/v1"
# Same reasoning — n8n's own container port (see docker-compose.yml's
# comment on why N8N_PORT is pinned to 5678 internally regardless of the
# host-side binding).
N8N_INTERNAL_URL = "http://smartrag-n8n:5678"
# Weaviate's HTTP port inside the network is always 8080; WEAVIATE_HTTP_PORT
# in .env is the host-side binding, and using it here would repeat the
# host-vs-container port mix-up that once pointed MinIO at a dead port.
WEAVIATE_INTERNAL_URL = "http://smartrag-weaviate:8080"

# Formats Docling accepts, mirrored from the upload form's accept attribute
# so a file rejected here is never one the pipeline could have handled.
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm", ".md",
    ".adoc", ".asciidoc", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
}
# Guards against a stray multi-GB upload wedging the single gunicorn
# worker while it streams. Docling itself gets much longer to work.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB


def conversion_limit_minutes(env: dict | None = None) -> int:
    """How long a conversion may take, in whole minutes, for the upload page.

    Read from the same DOCLING_MAX_SYNC_WAIT the container is started with
    rather than written here as a second number — a limit stated on a page and
    a limit enforced in a container that disagree is worse than not stating
    it, because the page is what somebody plans around.
    """
    raw = (env if env is not None else read_env()).get("DOCLING_MAX_SYNC_WAIT", "")
    try:
        seconds = int(str(raw).strip() or 0)
    except ValueError:
        seconds = 0
    # The image's own default, which is what applies when the variable is
    # missing — an installation whose .env predates this key.
    return max(1, round((seconds or 120) / 60))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def _subdomain_host(service: str, env: dict) -> str:
    """Mirrors subdomain_host() in scripts/lib/common.sh — the same rule the
    bootstrap wizard used to name the vhost and request the certificate.

    Only valid in domain mode. Tailscale mode has no subdomains at all: a
    Tailscale certificate covers exactly one MagicDNS name and has no
    wildcards, which is why services are separated by port there. Kept as a
    last-resort fallback for an .env written before FLOWISE_PUBLIC_URL
    existed; new code should read the resolved URL instead."""
    domain = env.get("DOMAIN", "").strip()
    if not domain:
        return ""
    prefix = env.get("SUBDOMAIN_PREFIX", "").strip()
    return f"{prefix}-{service}.{domain}" if prefix else f"{service}.{domain}"


def _public_chat_url(chatflow_id: str, env: dict) -> str:
    """The student-facing URL of a published agent.

    Read from FLOWISE_PUBLIC_URL, never assembled. The wizard resolves that
    value for whichever deployment mode is in use — a subdomain in domain
    mode, the bare MagicDNS name in tailscale mode — and assembling it here
    from DOMAIN reproduced the domain-mode rule everywhere. On a tailscale
    install that produced https://smart-rag.<machine>.<tailnet>.ts.net, a
    host with no certificate and no DNS record, handed to students as the
    address of their chat.

    This is the same rule as ARCHITECTURE decision 2 — public URLs are
    resolved in .env, never assembled — which had been applied to the compose
    file and missed here.
    """
    if not chatflow_id:
        return ""
    base = (env.get("FLOWISE_PUBLIC_URL") or "").strip().rstrip("/")
    if not base:
        # Pre-dates FLOWISE_PUBLIC_URL: fall back to the domain-mode rule,
        # which is what such an installation was using anyway.
        host = _subdomain_host("smart-rag", env)
        if not host:
            return ""
        base = f"https://{host}"
    return f"{base}/chatbot/{chatflow_id}"


def _flowise_client() -> FlowiseClient | None:
    env = read_env()
    api_key = env.get("FLOWISE_API_KEY")
    if not api_key:
        return None
    return FlowiseClient(FLOWISE_INTERNAL_URL, api_key)


def _n8n_client() -> N8nClient:
    return N8nClient(N8N_INTERNAL_URL)


def _weaviate_client() -> WeaviateClient:
    env = read_env()
    return WeaviateClient(WEAVIATE_INTERNAL_URL, env.get("WEAVIATE_API_KEY", ""))


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
            try:
                user = accounts.create_account(username, password,
                                               role=accounts.ROLE_ADMIN)
            except accounts.AccountError as exc:
                error = str(exc)
            else:
                auth.log_in(user)
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
        user = accounts.verify_login(username, password)
        if user:
            auth.log_in(user)
            return redirect(url_for("dashboard"))
        error = _t("login_err_invalid")
    return render_template(
        "login.html",
        error=error,
        # The link is only shown when a reset could actually be delivered.
        # Offering "forgot password?" on an installation with no mail relay
        # sends someone down a path that ends in a page telling them it was
        # never going to work.
        mail_available=mailer.mail_configured(),
    )


# ─── Password reset ────────────────────────────────────────────────────────────
# Two properties this must have, both of which shape the code below:
#
# The reply is identical whether or not the entered username exists. This
# page is reachable by anyone who can reach the login page, and a different
# answer per username turns it into a way to enumerate the account.
#
# The mail always goes to ADMIN_EMAIL, never to an address supplied in the
# form. Otherwise anyone who can load this page could have a valid reset link
# delivered to themselves.
@app.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if not auth.is_configured():
        return redirect(url_for("setup"))

    env = read_env()
    if not mailer.mail_configured(env):
        # Honest dead end rather than a form that silently does nothing:
        # this installation has no relay, and the fix is a different one.
        return render_template("forgot.html", unavailable=True)

    sent = False
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        # Always report success — see the note above.
        sent = True
        user = accounts.get_by_username(username) if username else None
        # The link goes to the address stored on the account, and to
        # ADMIN_EMAIL only for an account that has none. Never to an address
        # from the form: that would let anyone have a working reset link
        # delivered to themselves.
        target = (user or {}).get("email") or env.get("ADMIN_EMAIL", "")
        if user and target:
            token = accounts.create_reset_token(user["id"])
            base = (env.get("CONTENT_ADMIN_PUBLIC_URL") or "").rstrip("/")
            link = f"{base}{url_for('reset_password', token=token)}"
            try:
                mailer.send_mail(
                    target,
                    _t("reset_mail_subject"),
                    _t("reset_mail_body", link, accounts.RESET_TTL_SECONDS // 60),
                )
            except mailer.MailError as exc:
                # The operator sees the same page either way; the log is
                # where the real reason belongs, and the admin TUI remains
                # the way in if mail is broken.
                logger.error("Could not send password-reset mail: %s", exc)
                accounts.clear_reset_token(user["id"])

    return render_template(
        "forgot.html",
        sent=sent,
        admin_email=env.get("ADMIN_EMAIL", ""),
    )


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    if not auth.is_configured():
        return redirect(url_for("setup"))

    user = accounts.user_for_reset_token(token)
    if not user:
        return render_template("reset.html", invalid=True), 400

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not password:
            error = _t("setup_err_required")
        elif password != confirm:
            error = _t("setup_err_mismatch")
        elif len(password) < auth.MIN_PASSWORD_LENGTH:
            error = _t("setup_err_too_short")
        else:
            # set_password clears the token itself — single use, so the link
            # dies with the password it replaced whether or not its hour is
            # up.
            accounts.set_password(user["id"], password)
            return render_template("reset.html", done=True)

    return render_template("reset.html", token=token, error=error)


@app.route("/logout")
def logout():
    auth.log_out()
    return redirect(url_for("login"))


# ─── Flowise connection setup ───────────────────────────────────────────────────
@app.route("/flowise-setup", methods=["GET", "POST"])
@auth.admin_required
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
        # The address to click, not one to retype. FLOWISE_PUBLIC_URL is what
        # the wizard resolved for this deployment — domain or MagicDNS name —
        # so the link works from wherever the operator is reading this.
        flowise_url=(env.get("FLOWISE_PUBLIC_URL") or "").strip().rstrip("/"),
        already_set=bool(env.get("FLOWISE_API_KEY")),
    )


# ─── The account that used to live in .env ───────────────────────────────────
# Done on the first request rather than at import: the database may not be
# reachable when the container starts, and refusing to start over an account
# migration would take the whole GUI down for a problem it could report.
_adoption_done = False


@app.before_request
def _adopt_once():
    global _adoption_done
    if _adoption_done:
        return
    _adoption_done = True
    try:
        auth.adopt_legacy_account()
    except db.DatabaseError as exc:
        # Not fatal: the pages that need the database will say so themselves,
        # and the ones that do not still work.
        logger.warning("Could not check for a legacy account: %s", exc)


# ─── Accounts ────────────────────────────────────────────────────────────────
@app.route("/accounts", methods=["GET", "POST"])
@auth.admin_required
def accounts_page():
    """Who exists, what they may reach, and how that changes.

    Administrator-only, and the reason is worth stating: this page hands out
    access to course material. A maintainer who could assign themselves a
    course would make the whole authorisation model decorative.
    """
    error = None
    success = None
    try:
        courses_available = [c for c in courses_service.all_courses() if c["ready"]]
    except db.DatabaseError as exc:
        return render_template("accounts.html", accounts=[], courses=[],
                               error=str(exc), success=None)

    if request.method == "POST":
        action = request.form.get("action", "")
        try:
            if action == "create":
                user = accounts.create_account(
                    request.form.get("username", ""),
                    request.form.get("password", ""),
                    role=request.form.get("role", accounts.ROLE_MAINTAINER),
                    email=request.form.get("email", ""))
                success = _t("accounts_created", user["username"])
            elif action == "assign":
                accounts.assign(int(request.form.get("user_id", "0")),
                                request.form.get("course_id", ""))
            elif action == "unassign":
                accounts.unassign(int(request.form.get("user_id", "0")),
                                  request.form.get("course_id", ""))
            elif action == "role":
                accounts.set_role(int(request.form.get("user_id", "0")),
                                  request.form.get("role", ""))
            elif action == "delete":
                accounts.delete_account(int(request.form.get("user_id", "0")))
                success = _t("accounts_deleted")
        except accounts.AccountError as exc:
            # These messages already explain themselves — the last-administrator
            # one in particular says what to do instead.
            error = str(exc)
        except (ValueError, db.DatabaseError) as exc:
            error = str(exc)

    return render_template("accounts.html",
                           accounts=accounts.all_accounts(),
                           courses=courses_available,
                           current_id=(auth.current_user() or {}).get("id"),
                           error=error, success=success)


# ─── The active course ───────────────────────────────────────────────────────
# Every page that touches agents, documents or uploads works inside exactly
# one course. Which one is resolved here, in one place.
#
# This is where phase 5 will also assert that the logged-in account may work
# on that course. Checked in fifteen routes is forgotten in one, and the
# omission is invisible until somebody sees another course's material — so
# the shape is a single decorator now, even though today it only resolves.
def _resolve_course() -> dict | None:
    """The course the operator is working in, or None if that is not settled.

    A course chosen explicitly wins. With exactly one ready course and no
    choice made, that one is used — asking someone to pick from a list of one
    is a step that teaches people to click without reading. With several and
    no choice made, nothing is guessed: the wrong course silently selected is
    how one course's documents end up in another.
    """
    user = auth.current_user()
    chosen = session.get("course_id")
    if chosen:
        course = courses_service.get_course(chosen)
        # Membership is checked here, on every request, and not once at the
        # moment of choosing: an assignment can be withdrawn while somebody
        # is logged in, and a cookie written before that would otherwise keep
        # working for as long as the session lives.
        if course and course["ready"] and accounts.may_access(user, chosen):
            return course
        # A course that was deleted, or whose provisioning never finished,
        # must not stay selected — every page would then fail somewhere
        # deeper, with a message about a missing collection.
        session.pop("course_id", None)

    ready = [c for c in courses_service.all_courses()
             if c["ready"] and accounts.may_access(user, c["id"])]
    if len(ready) == 1:
        return ready[0]
    return None


def with_course(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            course = _resolve_course()
        except db.DatabaseError as exc:
            logger.warning("Could not resolve the active course: %s", exc)
            course = None
        if course is None:
            # Say why. Bouncing to the course list without a word looks like
            # a broken link — which is exactly how it was reported.
            return redirect(url_for("courses", pick="1"))
        g.course = course
        return view(*args, **kwargs)
    wrapped.__course_bound__ = True
    return wrapped


@app.context_processor
def _inject_course():
    """So the layout can show which course is active without every view
    passing it. A page that acts on a course while not naming it is how an
    edit lands in the wrong one."""
    # Bound before the try: db.DatabaseError can fire on the very first
    # line below (_resolve_course() itself calls auth.current_user()), which
    # would otherwise leave `user` unassigned when the except branch's
    # `return` references it below — UnboundLocalError instead of the
    # intended graceful fallback (6e-11).
    user = None
    try:
        active = getattr(g, "course", None) or _resolve_course()
        user = auth.current_user()
        available = [c for c in courses_service.all_courses()
                     if c["ready"] and accounts.may_access(user, c["id"])]
    except db.DatabaseError as exc:
        logger.warning("Could not load the course list for the layout: %s", exc)
        active, available = None, []
    return {"active_course": active, "available_courses": available,
            "is_admin": bool(user and user["role"] == accounts.ROLE_ADMIN),
            "current_account": user}


# ─── Database unavailable ──────────────────────────────────────────────────
# Safety net for every route and context processor that does not catch
# db.DatabaseError itself (6e-10): with_course, _inject_course and a few
# routes already handle it locally and never reach here (Flask only invokes
# a registered errorhandler when the exception propagates unhandled out of
# view/context-processor code) — this is purely additive for everything
# else, including login_required's own current_user() call.
#
# TODO(6e-10/6e-02): under a sustained outage this logs once per request,
# same open point as _inject_course's own logging (6e-02) — not throttled.
@app.errorhandler(db.DatabaseError)
def db_unavailable(exc):
    logger.warning("Unhandled database error: %s", exc)
    try:
        return render_template("db_unavailable.html"), 503
    except Exception as render_exc:
        # The template's own render_template() call still runs every
        # context processor app-wide (6e-11 is exactly this kind of
        # failure) -- if a second, unrelated one ever raises here, a
        # template-free reply is the one thing that can't be dragged down
        # by the same outage a second time.
        logger.warning("Could not render the database-unavailable page "
                        "itself: %s", render_exc)
        body = f"{_t('db_unavailable_heading')}\n\n{_t('db_unavailable_message')}"
        return body, 503, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/courses/<course_id>/use")
@auth.login_required
def use_course(course_id):
    course = courses_service.get_course(course_id)
    if course and course["ready"] and accounts.may_access(auth.current_user(), course_id):
        session["course_id"] = course_id
    return redirect(request.referrer or url_for("dashboard"))


def _sources_by_agent(course: dict) -> dict[str, list[str]]:
    """This course's documents, grouped by the agent that owns them.

    By source_file, which is the key provenance uses: a title can be edited,
    and two agents may upload the same filename, while this path carries the
    agent.
    """
    out: dict[str, list[str]] = {}
    client = _weaviate_client()
    for doc in client.list_documents(course["collection"], course["id"]):
        agent = doc.get("agent_id")
        path = doc.get("source_file") or ""
        if agent is None or not path:
            continue
        out.setdefault(str(agent), []).append(path)
    return out


def _agent_sources(course: dict, slot: str) -> list[str]:
    """The documents of one agent, or an empty list if it has none."""
    try:
        return _sources_by_agent(course).get(str(slot), [])
    except Exception as exc:  # noqa: BLE001
        # Guessing here would remove the wrong thing, or nothing while
        # reporting success.
        raise Neo4jError(
            "Could not read this course's documents, so the agent's "
            f"contribution cannot be identified: {exc}") from exc


# ─── Dashboard ───────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
@auth.login_required
@with_course
def dashboard():
    env = read_env()
    error = None
    success = None

    # Re-import every agent of this course. One at a time is fine for two; the
    # plan is fifty courses with up to ten slots each, and after a change to a
    # template that is five hundred manual imports with no way to tell which
    # ones were done.
    # Which agents' material the concept map is built from. Saving this is
    # scope only — it changes what the next build reads and removes nothing,
    # because a concept two agents' documents support has to survive one of
    # them leaving. Taking a contribution out is the action below it, with
    # its own numbers.
    if request.method == "POST" and request.form.get("action") == "graph_scope":
        included = set(request.form.getlist("in_graph"))
        changed = []
        for num, data in storage.all_slots(g.course["id"]).items():
            if not data:
                continue
            want = num in included
            if bool(data.get("in_graph", True)) != want:
                storage.set_in_graph(g.course["id"], int(num), want)
                changed.append((num, want))
        success = (_t("dash_graph_scope_saved", len(changed)) if changed
                   else _t("dash_graph_scope_unchanged"))

    elif request.method == "POST" and request.form.get("action") == "graph_drop_agent":
        slot_num = request.form.get("slot", "")
        try:
            docs = _agent_sources(g.course, slot_num)
        except Neo4jError as exc:
            docs, error = [], str(exc)
        if not docs and not error:
            error = _t("dash_graph_drop_nothing")
        elif docs:
            try:
                gone = _neo4j_client().remove_documents(g.course["id"], docs)
            except (Neo4jError, neo4j_client.GraphInputError) as exc:
                logger.error("Removing agent %s from the graph failed: %s",
                             slot_num, exc)
                error = _t("dash_graph_drop_failed", exc)
            else:
                success = _t("dash_graph_dropped", slot_num,
                             gone.get("concepts_removed", 0),
                             gone.get("concepts_kept", 0),
                             gone.get("edges_removed", 0))

    if request.method == "POST" and request.form.get("action") == "reimport":
        client = _flowise_client()
        if client is None:
            error = _t("dash_reimport_no_flowise")
        else:
            done, failed = 0, []
            for num, data in sorted(storage.all_slots(g.course["id"]).items(),
                                    key=lambda kv: int(kv[0])):
                # Never imported is not the same as behind, and re-importing
                # is not what an empty slot needs.
                if not data.get("archetype") or not data.get("chatflow_id"):
                    continue
                failure = _do_import(g.course, int(num), data["archetype"], client)
                if failure:
                    failed.append(f"{num}: {failure}")
                else:
                    done += 1
            if failed:
                # Partial success is reported as such. "3 done, 2 failed" with
                # the reasons beats one red line that hides the three that
                # worked.
                error = _t("dash_reimport_partial", done, len(failed),
                           " · ".join(failed))
            else:
                success = _t("dash_reimport_done", done)

    slots = storage.all_slots(g.course["id"])

    # One list call covers all ten slots — asking Flowise per slot would be
    # ten round trips for a table. Ids missing from the answer were deleted
    # in Flowise, so they simply aren't in the map and render as not public.
    public_ids: set[str] = set()
    client = _flowise_client()
    # "Connected" used to mean "a key is stored in .env" — which stays true
    # after the key is deleted in Flowise, after its permissions are reduced,
    # and while Flowise is down. The banner then reassures the operator right
    # up to the moment an import fails for reasons the banner just denied.
    #
    # The call below already tells us the truth, so it costs nothing extra to
    # use it: if listing chatflows works, the key is currently valid and
    # carries at least chatflows:view. What it cannot prove are the create
    # permissions — those show first at import, which is why the import path
    # reports permission errors verbatim rather than as "connection failed".
    flowise_live = False
    if client is not None:
        try:
            public_ids = {
                cf["id"] for cf in client.list_chatflows()
                if cf.get("isPublic") and cf.get("id")
            }
            flowise_live = True
        except FlowiseError as exc:
            # The dashboard is the page an operator lands on; a Flowise
            # outage must not replace it with an error.
            logger.error("Could not read published states: %s", exc)

    # What each agent is holding up in the concept map. Shown for every agent,
    # not only unticked ones: the number is what makes unticking a decision
    # rather than a click, and it has to be there before the click.
    graph_weight: dict = {}
    try:
        by_source = _neo4j_client().by_source(g.course["id"])
        for num, files in _sources_by_agent(g.course).items():
            concepts = sum(by_source.get(f, {}).get("concepts", 0) for f in files)
            only = sum(by_source.get(f, {}).get("only", 0) for f in files)
            if concepts:
                graph_weight[num] = {"concepts": concepts, "only": only}
    except Exception as exc:  # noqa: BLE001 — a column, never a blocker
        logger.info("Graph weights unavailable for the agent list: %s", exc)

    import_state = _import_state(g.course, slots, env)
    return render_template(
        "dashboard.html",
        graph_weight=graph_weight,
        slots=slots,
        error=error,
        success=success,
        import_state=import_state,
        behind_count=sum(1 for v in import_state.values() if v == "behind"),
        archetypes=agent_templates.archetypes_for(current_language()),
        flowise_configured=flowise_live,
        # Distinguishes "never set up" from "set up, but not working now" —
        # the same banner for both would send someone to paste a new key when
        # the real problem is that Flowise is down.
        flowise_key_stored=bool(env.get("FLOWISE_API_KEY")),
        public_ids=public_ids,
        public_urls={
            num: _public_chat_url(data.get("chatflow_id") or "", env)
            for num, data in slots.items()
        },
    )


# ─── Agent slot: choose archetype, fill content, import ────────────────────────
@app.route("/slot/<int:slot>", methods=["GET", "POST"])
@auth.login_required
@with_course
def slot_view(slot: int):
    if not (1 <= slot <= storage.MAX_SLOTS):
        return _t("slot_err_invalid"), 404

    existing = storage.get_slot(g.course["id"], slot)
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

            elif action in ("publish", "unpublish"):
                # Deliberately separate from save/import: publishing changes
                # who can reach the agent, not what it says, and must never
                # ride along on a content save the operator didn't connect
                # with going public.
                chatflow_id = existing.get("chatflow_id")
                client = _flowise_client()
                if not chatflow_id:
                    error = _t("publish_err_not_imported")
                elif client is None:
                    error = _t("slot_err_not_connected")
                else:
                    try:
                        client.set_chatflow_public(chatflow_id, action == "publish")
                    except FlowiseError as exc:
                        logger.error("Publish toggle failed for slot %s: %s", slot, exc)
                        error = _t("publish_err_failed", exc)
                    else:
                        success = _t(
                            "publish_ok" if action == "publish" else "unpublish_ok"
                        )

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
                elif storage.name_taken(g.course["id"], name, exclude_slot=slot):
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
                    storage.save_slot(g.course["id"], slot, archetype, content, name,
                                      system_prompt)
                    existing = storage.get_slot(g.course["id"], slot)
                    if action == "reset_prompt":
                        success = _t("slot_prompt_reset_ok")

                    if action == "import":
                        client = _flowise_client()
                        if client is None:
                            error = _t("slot_err_not_connected")
                        else:
                            try:
                                error = _do_import(g.course, slot, archetype, client)
                                if not error:
                                    success = _t("slot_imported_ok")
                                    existing = storage.get_slot(g.course["id"], slot)
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

    # Read the published state back from Flowise rather than caching it in
    # slots.json: it can also be toggled in Flowise's own UI, and a stale
    # "not published" badge next to a live public URL would be worse than
    # one extra internal call. A Flowise outage must not take this page
    # down, so failure degrades to "unknown" instead of raising.
    env = read_env()
    is_public = None
    chatflow_gone = False
    publish_client = _flowise_client()
    if existing.get("chatflow_id") and publish_client is not None:
        try:
            chatflow = publish_client.get_chatflow(existing["chatflow_id"])
        except FlowiseError as exc:
            logger.error("Could not read published state for slot %s: %s", slot, exc)
        else:
            if chatflow is None:
                # Deleted in Flowise while slots.json still remembers the id.
                # Said plainly rather than shown as "unknown": the operator
                # needs to re-import, and "unknown" would send them looking
                # for a connection problem that isn't there.
                logger.info("Slot %s references chatflow %s, which no longer "
                            "exists in Flowise", slot, existing["chatflow_id"])
                chatflow_gone = True
            else:
                is_public = bool(chatflow.get("isPublic"))

    return render_template(
        "slot.html",
        slot=slot,
        is_public=is_public,
        chatflow_gone=chatflow_gone,
        public_url=_public_chat_url(existing.get("chatflow_id") or "", env),
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
@with_course
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


def _first_course_id() -> str | None:
    """The oldest course. Used only to decide which course owns the
    single-course-era CHATFLOW_AGENTnn variables in .env."""
    all_of_them = courses_service.all_courses()
    if not all_of_them:
        return None
    return min(all_of_them, key=lambda c: c["created_at"])["id"]


def _build_flow(course: dict, slot: int, archetype: str,
                all_slots: dict, env: dict) -> tuple[dict, list[str]]:
    """The flow as the course, the slot's content and .env determine it —
    everything except the credential ids, which only exist once Flowise has
    been asked for them.

    Split out of the import so the same construction can be repeated without
    importing: that is how a slot can be told it is behind the repository
    without a round trip per slot, and it guarantees the comparison is against
    what an import would actually produce rather than against a copy of the
    logic that could drift from it.
    """
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
    agent_templates.auto_fill_from_env(flow, env, slot=slot, course=course)
    missing = agent_templates.substitute_content(flow, content)
    return flow, missing


def _import_state(course: dict, all_slots: dict, env: dict) -> dict[str, str]:
    """Per slot: "current", "behind" or "unknown" — for the slots that have
    been imported at all. Slots that were never imported are absent.

    "unknown" is its own answer and not folded into "behind": a slot imported
    before the digest existed, or one whose template no longer loads, has not
    been shown to differ. Saying "behind" there would send someone to
    re-import for no reason, which is how a warning stops being read.
    """
    state: dict[str, str] = {}
    for num, data in all_slots.items():
        if not data.get("chatflow_id"):
            continue
        stored = data.get("imported_digest")
        if not stored:
            state[num] = "unknown"
            continue
        try:
            flow, missing = _build_flow(course, int(num), data["archetype"],
                                        all_slots, env)
        except (agent_templates.TemplateError, KeyError, ValueError) as exc:
            logger.warning("Slot %s of %s cannot be rebuilt for comparison: %s",
                           num, course["id"], exc)
            state[num] = "unknown"
            continue
        if missing:
            # Content was removed after the import. The agent in Flowise still
            # works; re-importing it now would fail. "Behind" is right.
            state[num] = "behind"
            continue
        state[num] = "current" if agent_templates.flow_digest(flow) == stored \
            else "behind"
    return state


def _do_import(course: dict, slot: int, archetype: str,
               client: FlowiseClient) -> str | None:
    """Returns an error message, or None on success.

    The course is a parameter, not something read from the request context:
    this function decides which collection an agent will search, and a
    wrong-course import produces an agent that answers plausibly from
    somebody else's material.
    """
    env = read_env()
    all_slots = storage.all_slots(course["id"])
    flow, missing = _build_flow(course, slot, archetype, all_slots, env)
    if missing:
        return _t("slot_err_missing_content", ", ".join(missing))
    # Taken here, before the credential ids are stamped in, because this is
    # the part that can be recomputed later without asking Flowise anything.
    digest = agent_templates.flow_digest(flow)
    slot_data = all_slots[str(slot)]

    # Refuses rather than substituting a different provider — see
    # agent_templates._resolve_provider. The credential name is built from the
    # resolved value, so it can never disagree with the credential's type.
    try:
        llm_provider, llm_map = agent_templates.resolve_llm_provider(env)
        embed_provider, embed_map = agent_templates.resolve_embedding_provider(env)
    except agent_templates.ProviderNotConfigured as exc:
        return _t("import_err_provider", str(exc))

    llm_cred_id = client.upsert_credential(
        f"smartrag-llm-{llm_provider}",
        llm_map["credential_name"],
        {llm_map["credential_key"]: env.get("LLM_API_KEY", "")},
    )
    embed_cred_id = client.upsert_credential(
        f"smartrag-embedding-{embed_provider}",
        embed_map["credential_name"],
        {embed_map["credential_key"]: env.get("EMBEDDING_API_KEY", "")},
    )
    # Weaviate is started with AUTHENTICATION_APIKEY_ENABLED=true, so the
    # vector-store node needs its own credential. Names verified against
    # Flowise's WeaviateApi.credential.ts (flowise@3.1.3): "weaviateApi"
    # with a single "weaviateApiKey" input.
    weaviate_cred_id = client.upsert_credential(
        "smartrag-weaviate",
        "weaviateApi",
        {"weaviateApiKey": env.get("WEAVIATE_API_KEY", "")},
    )
    agent_templates.set_credential_ids(
        flow, llm_cred_id, embed_cred_id, weaviate_cred_id
    )

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

    # Tracing. Without this the observability profile runs Langfuse and
    # ClickHouse — well over a gigabyte of memory — and receives nothing,
    # while an n8n workflow patches traces that do not exist every 30 minutes.
    # Flowise has no global switch for Langfuse: its env-based tracing covers
    # LangSmith only, so the setting belongs on each chatflow.
    #
    # Skipped, not failed, when Langfuse is not part of this deployment: the
    # profile is optional and an agent must still import without it.
    analytic = None
    if "observability" in env.get("COMPOSE_PROFILES", "") \
            and env.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "").strip():
        try:
            lf_cred_id = client.upsert_credential(
                "smartrag-langfuse",
                "langfuseApi",
                {
                    "langFusePublicKey": env.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", ""),
                    "langFuseSecretKey": env.get("LANGFUSE_INIT_PROJECT_SECRET_KEY", ""),
                    # The internal name: Flowise reports from inside the
                    # network, so the public URL would be a detour through
                    # nginx or Funnel for a container-to-container call.
                    "langFuseEndpoint": "http://smartrag-langfuse-web:3001",
                },
            )
            analytic = FlowiseClient.langfuse_analytic(lf_cred_id)
        except FlowiseError as exc:
            # An agent that works without tracing beats no agent.
            logger.error("Could not configure Langfuse tracing: %s", exc)

    agent_name = slot_data.get("name") or f"Agent {slot:02d}"
    # The course is in the name because Flowise's names are global and
    # upsert_chatflow finds an existing flow by name. Two courses with an
    # agent called "Tutor" would otherwise be one chatflow, each import
    # overwriting the other — and it would look like a successful import
    # both times.
    chatflow_name = f"SMART RAG — {course['id']} — {agent_name}"
    flow_data_json = __import__("json").dumps(flow)
    chatflow_id, _created = client.upsert_chatflow(
        chatflow_name, flow_data_json, analytic=analytic
    )
    storage.set_chatflow_id(course["id"], slot, chatflow_id, digest)
    # CHATFLOW_AGENTnn in .env is from the single-course era: one variable per
    # slot, with no room for a course. Kept for the LTI middleware, which
    # still reads it, and only written for the first course so it cannot be
    # rewritten by whichever course was imported last.
    if course["id"] == _first_course_id():
        set_env_var(f"CHATFLOW_AGENT{slot:02d}", chatflow_id)
    return None


# ─── Document upload (RAG ingest) ───────────────────────────────────────────────
@app.route("/upload", methods=["GET", "POST"])
@auth.login_required
@with_course
def upload():
    slots = storage.all_slots(g.course["id"])
    # Only slots that actually have an agent configured — uploading to an
    # empty slot would tag chunks with an agent_id nothing retrieves.
    configured = {
        num: data for num, data in slots.items() if data.get("archetype")
    }

    error = None
    success = None
    warning = None
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
        elif not form["title"]:
            # The one metadata field that is genuinely required. It is handed
            # to the agent with every retrieved chunk (source_title is in each
            # agent's weaviateMetadataKeys), so it is what an answer cites
            # with. Left empty, the citation falls back to a filename slug —
            # "agent_1/digitale-bildung-an-bayerischen-schulen-…md" is what a
            # student then sees as the source. The field is pre-filled from
            # the PDF, so requiring it costs a glance, not typing.
            error = _t("upload_err_no_title_field")
        elif os.path.splitext(upload_file.filename)[1].lower() not in ALLOWED_UPLOAD_EXTENSIONS:
            error = _t(
                "upload_err_bad_type",
                upload_file.filename,
                ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS)),
            )
        elif form["year"] and not form["year"].isdigit():
            error = _t("upload_err_bad_year")
        else:
            job_id = secrets.token_hex(8)
            try:
                _n8n_client().upload_document(
                    job_id=job_id,
                    course_id=g.course["id"],
                    collection=g.course["collection"],
                    bucket=g.course["bucket"],
                    file_stream=upload_file.stream,
                    filename=upload_file.filename,
                    content_type=upload_file.mimetype or "application/octet-stream",
                    agent_id=int(form["slot"]),
                    # Falling back to the filename matches what the ingest
                    # workflow does anyway if title is empty — doing it here
                    # too just makes the value visible to the operator.
                    # No fallback to the file name. There used to be one, and
                    # it is why documents reached the index titled after their
                    # slug — which is then what an answer cites. The title is
                    # required above, so this can only be a real one.
                    title=form["title"],
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
                # The row is created here, by the side that knows the upload
                # was accepted — not by the first callback. A pipeline that
                # reports nothing at all is precisely the case worth showing,
                # and a row that only appears once n8n speaks would show
                # nothing in exactly that case.
                try:
                    ingest_status.start(job_id, upload_file.filename,
                                        int(form["slot"]), g.course["id"])
                except OSError as exc:
                    # The document is already with n8n at this point. Failing
                    # the request now would report a failure that did not
                    # happen and invite a second upload of the same file — so
                    # the progress row is lost, the ingest is not, and the log
                    # says which.
                    logger.warning("Could not record ingest progress for %s: %s",
                                   upload_file.filename, exc)
                agent_label = configured[form["slot"]].get("name") or f"Agent {form['slot']}"
                success = _t("upload_ok", upload_file.filename, agent_label)
                # Authors and year are not required, and deliberately so: a
                # ministry framework or a curriculum has no personal author,
                # and a mandatory field there buys invented entries. An
                # invented source is worse than a missing one. But both ride
                # along on every chunk and are what an answer cites with, so
                # their absence is said once, after the upload has succeeded,
                # rather than blocking it.
                thin = [name for name, value in (("authors", form["authors"]),
                                                 ("year", form["year"]))
                        if not value]
                if thin:
                    warning = _t("upload_warn_thin_metadata",
                                 _t("upload_field_" + thin[0])
                                 if len(thin) == 1
                                 else _t("upload_fields_authors_and_year"))
                form = {}

    return render_template(
        "upload.html",
        max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
        conversion_minutes=conversion_limit_minutes(),
        warning=warning,
        configured=configured,
        form=form,
        error=error,
        success=success,
    )


# ─── Ingest progress, reported by the pipeline itself ────────────────────────
@app.route("/api/ingest-status", methods=["POST"])
def api_ingest_status():
    """Called by ingest-document.json as it works, never by a browser.

    No session: n8n has none. A shared token instead, compared in constant
    time — this endpoint is reachable from anywhere on the Docker network,
    and without it any container could rewrite what the operator is told
    about their documents.

    It answers 200 to anything it understood, including a callback for a job
    it does not know. That is deliberate: n8n retries on an error status, and
    a retry cannot make an unknown job known. The pipeline must never be
    disturbed by the progress display failing — the display is the optional
    part, the ingest is not.
    """
    expected = read_env().get("INGEST_STATUS_TOKEN", "").strip()
    supplied = request.headers.get("X-Ingest-Token", "")
    # An unset token would otherwise make every caller authorised, which is
    # the failure mode that looks like it works.
    if not expected or not hmac.compare_digest(expected, supplied):
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    job_id = str(payload.get("job_id", "")).strip()
    stage = str(payload.get("stage", "")).strip()
    detail = str(payload.get("detail", ""))
    if not job_id or not stage:
        return jsonify({"error": "job_id and stage are required"}), 400

    applied = ingest_status.update(job_id, stage, detail)
    if not applied:
        logger.info("Ingest callback ignored (job=%s, stage=%s)", job_id, stage)
    return jsonify({"applied": applied}), 200


# ─── The concept-map build, reported by the workflow itself ──────────────────
@app.route("/api/graph-build", methods=["POST"])
def api_graph_build():
    """Called by the concept-map workflow as it works, never by a browser.

    Shares INGEST_STATUS_TOKEN with the ingest callback rather than inventing
    a second secret: it is the same trust boundary — the n8n container talking
    to this one over the Docker network — and a second token would mean a
    wizard question, a line in .env.example and a step for every existing
    installation, in exchange for nothing.

    Unlike the ingest callback, this one answers 4xx to what it does not
    understand. There the display was the optional part and the pipeline was
    not; here the payload *is* the product, and a proposal quietly dropped
    because a field was misspelled would leave a build running for ever with
    nobody able to say why.
    """
    expected = read_env().get("INGEST_STATUS_TOKEN", "").strip()
    supplied = request.headers.get("X-Ingest-Token", "")
    if not expected or not hmac.compare_digest(expected, supplied):
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    build_id = str(payload.get("build_id", "")).strip()
    state = str(payload.get("state", "")).strip()
    if not build_id or state not in ("running", "proposed", "failed"):
        return jsonify({"error": "build_id and a state of running, proposed "
                                 "or failed are required"}), 400

    build = graph_builds.get(build_id)
    if build is None:
        return jsonify({"error": "unknown build"}), 404

    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else None

    if state == "running":
        return jsonify({"applied": graph_builds.running(build_id, stats)}), 200

    if state == "failed":
        message = str(payload.get("error", "")).strip() or "no reason given"
        return jsonify({"applied": graph_builds.fail(build_id, message)}), 200

    # A proposal is validated here, before it is stored — not when somebody
    # opens the review. A build that reports success and produces something
    # unusable should fail while the workflow is still there to say why, and
    # the reviewer should never be handed a box of nonsense to approve.
    proposal = payload.get("proposal")
    if not isinstance(proposal, dict):
        graph_builds.fail(build_id, "the proposal was not an object")
        return jsonify({"error": "proposal must be an object"}), 400
    try:
        known = _present_sources_for(build["course_id"])
        concepts, edges = neo4j_client.parse_proposal(
            json.dumps(proposal), known_sources=known)
    except neo4j_client.GraphInputError as exc:
        graph_builds.fail(build_id, f"the proposal was rejected: {exc}")
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not check build %s: %s", build_id, exc)
        return jsonify({"error": "could not be checked"}), 503

    stored = graph_builds.propose(
        build_id, {"concepts": concepts, "prerequisites": edges}, stats)
    return jsonify({"applied": stored, "concepts": len(concepts),
                    "edges": len(edges)}), 200


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
        # Stash the extracted front matter so "suggest keywords" can work
        # from it without a second upload of the same file. Session-scoped
        # and capped: it's a convenience cache, not storage.
        if identifiers.get("text"):
            session["last_scan_text"] = identifiers["text"][:20000]
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


@app.route("/upload/keywords", methods=["POST"])
@auth.login_required
def upload_keywords():
    """
    Proposes subject keywords from what the form already knows plus, when
    the document was scanned earlier in this session, its front matter.
    Same suggest-then-confirm contract as everything else here: this only
    returns a list, the page decides what to do with it.
    """
    payload = request.get_json(silent=True) or {}
    title = (payload.get("title") or "").strip()
    authors = (payload.get("authors") or "").strip()
    excerpt = session.get("last_scan_text", "")

    try:
        keywords = suggest_keywords(
            title, authors, excerpt, read_env(), language=current_language()
        )
    except LLMError as exc:
        logger.error("Keyword suggestion failed: %s", exc)
        return {"error": str(exc)}, 502
    return {"keywords": keywords}


@app.errorhandler(413)
def upload_too_large(_exc):
    """Flask aborts with 413 before the route ever runs when a request
    exceeds MAX_CONTENT_LENGTH — without this the operator would get a bare
    error page with no hint about what the limit is."""
    return (
        render_template(
            "upload.html",
            max_upload_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
            conversion_minutes=conversion_limit_minutes(),
            # No warning here: this handler runs instead of the route, so
            # nothing was uploaded and there is nothing to have been thin
            # about. Passing the name unbound would be a NameError raised
            # from an error handler, which is the worst place for one.
            warning=None,
            configured={
                num: data
                for num, data in storage.all_slots(g.course["id"]).items()
                if data.get("archetype")
            },
            form={},
            error=_t("upload_err_too_large", MAX_UPLOAD_BYTES // (1024 * 1024)),
            success=None,
        ),
        413,
    )


# ─── Onboarding guide ────────────────────────────────────────────────────────────
# The command an operator has to run on the host to import the n8n workflows
# and credentials. It cannot be run from here by design: this container has
# no Docker socket and no host filesystem (see the module docstring), and
# handing a web GUI the ability to exec into other containers would trade
# that boundary away for one saved copy-paste.
DEPLOY_WORKFLOWS_COMMAND = "sudo bash scripts/deploy-n8n-workflows.sh"


@app.route("/getting-started")
@auth.login_required
@with_course
def getting_started():
    checks = setup_checks.run_all(
        env=read_env(),
        slots=storage.all_slots(g.course["id"]),
        flowise_client=_flowise_client(),
        n8n_base_url=N8N_INTERNAL_URL,
        deploy_command=DEPLOY_WORKFLOWS_COMMAND,
    )
    # "Ready" means every check passed — a WARN is not a pass. Someone whose
    # agents are saved but never imported does not have a working system, and
    # a green banner would tell them they do.
    ready = all(c.state == setup_checks.State.OK for c in checks)
    return render_template(
        "getting_started.html",
        checks=checks,
        ready=ready,
        State=setup_checks.State,
        first_blocker=next(
            (c for c in checks if c.blocking and c.state != setup_checks.State.OK),
            None,
        ),
    )


# ─── Courses ─────────────────────────────────────────────────────────────────
@app.route("/courses", methods=["GET", "POST"])
@auth.login_required
def courses():
    """List courses and create one.

    Behind the existing single account for now. Who may create a course, and
    who may see which, is phase 5 — putting a half-built authorisation model
    here would have to be unpicked, and one that looks finished is worse than
    none.
    """
    error = None
    success = None
    form = {"id": "", "name": "", "retention_until": "", "retention_note": ""}

    user = auth.current_user()
    is_admin = bool(user and user["role"] == accounts.ROLE_ADMIN)

    if request.method == "POST":
        action = request.form.get("action", "create")
        if not is_admin:
            # Creating a course creates a collection, a bucket and a storage
            # grant. That is an installation-level act, not a course-level
            # one, so it belongs to the role that manages the installation.
            return redirect(url_for("courses"))
        try:
            if action in ("retention", "retention_handled"):
                cid = request.form.get("course_id", "")
                if action == "retention_handled":
                    courses_service.mark_retention_applied(cid)
                    success = _t("retention_marked", cid)
                else:
                    until = _parse_date(request.form.get("until", ""))
                    courses_service.set_retention(
                        cid, until, request.form.get("note", ""))
                    success = (_t("retention_saved", cid, until.isoformat())
                               if until else _t("retention_cleared", cid))
            elif action == "provision":
                # Finishing a course whose creation failed part-way. Same
                # code path as creating one, because the failure could have
                # been at any step and "resume" that only handles the last
                # one is a resume that works until it is needed.
                cid = request.form.get("course_id", "")
                done = courses_service.provision(cid)
                success = _t("courses_provisioned", done["name"])
            else:
                form = {"id": request.form.get("id", "").strip(),
                        "name": request.form.get("name", "").strip(),
                        "retention_until": request.form.get("retention_until", "").strip(),
                        "retention_note": request.form.get("retention_note", "").strip()}
                # Parsed before the course is created, so a mistyped date
                # fails the form rather than leaving a provisioned course
                # behind with no retention period and an error message.
                until = _parse_date(form["retention_until"])
                created = courses_service.create_course(form["id"], form["name"])
                if until or form["retention_note"]:
                    courses_service.set_retention(created["id"], until,
                                                  form["retention_note"])
                success = _t("courses_created", created["name"],
                             created["collection"], created["bucket"])
                form = {"id": "", "name": "", "retention_until": "",
                        "retention_note": ""}
        except courses_service.CourseError as exc:
            # The message already says which step failed and what is left
            # behind; repeating it in the operator's words would lose that.
            error = str(exc)
        except db.DatabaseError as exc:
            error = str(exc)

    # Arrived here because a page needed a course and none was settled.
    needs_pick = request.args.get("pick") == "1"

    try:
        visible = courses_service.all_courses()
        if not is_admin:
            # A maintainer sees the courses they are assigned to and no
            # others — including no unfinished ones, which are somebody
            # else's problem to finish.
            visible = [c for c in visible if accounts.may_access(user, c["id"])]
        all_of_them = visible
        unfinished = [c for c in all_of_them if not c["ready"]]
    except db.DatabaseError as exc:
        return render_template("courses.html", courses=[], unfinished=[],
                               error=error or str(exc), success=success,
                               form=form, needs_pick=needs_pick,
                               is_admin=is_admin, retention=None)

    # Only over the courses this person may see. An expiry warning about a
    # course somebody does not maintain is a warning they cannot act on.
    visible_ids = {c["id"] for c in all_of_them}
    overview = courses_service.retention_overview()
    retention = {k: [c for c in v if c["id"] in visible_ids]
                 for k, v in overview.items()}

    return render_template("courses.html", courses=all_of_them,
                           unfinished=unfinished, error=error, success=success,
                           form=form, needs_pick=needs_pick, is_admin=is_admin,
                           retention=retention)


# ─── Deleting a course ───────────────────────────────────────────────────────
# Two pages, not one. The first counts what a course consists of and asks; the
# second does it. A single confirm dialog on a list row would be one careless
# click away from removing a collection, a bucket, a graph and every
# conversation ever held in a course — and the dialog could not say how much
# that is, because counting it takes six requests.
def _parse_date(raw: str):
    """An <input type="date"> value, or None for an empty one.

    Empty means "clear it", which is a real answer. Anything else that is not
    a date is refused with a sentence rather than silently becoming None —
    that would look like the operator had cleared the field on purpose.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise courses_service.CourseError(
            _t("retention_bad_date", raw)) from exc


@app.route("/courses/<course_id>/delete", methods=["GET", "POST"])
@auth.admin_required
def delete_course_view(course_id: str):
    course = courses_service.get_course(course_id)
    if course is None:
        return _t("courses_delete_unknown", course_id), 404

    client = _flowise_client()
    error = None
    result = None

    if request.method == "POST":
        # The course id has to be typed. Not because a mistyped id is likely,
        # but because it is the one confirmation that cannot be given by
        # muscle memory — and this is irreversible in six systems at once.
        typed = (request.form.get("confirm") or "").strip()
        if typed != course_id:
            error = _t("courses_delete_mistyped", course_id)
        else:
            try:
                result = courses_service.delete_course(course_id, flowise=client)
            except courses_service.CourseError as exc:
                error = str(exc)
            except db.DatabaseError as exc:
                error = str(exc)
            if result and result["deleted"]:
                # The selected course may be the one that just went.
                if session.get("course_id") == course_id:
                    session.pop("course_id", None)

    inventory = None
    if result is None:
        try:
            inventory = courses_service.inventory(course_id, flowise=client)
        except (courses_service.CourseError, db.DatabaseError) as exc:
            error = error or str(exc)

    return render_template("course_delete.html", course=course,
                           inventory=inventory, result=result, error=error)


# ─── One person, across every system ─────────────────────────────────────────
# Two pages in one, and never a single click: the form counts what is held
# about a learner, and only then offers to erase it — with the id typed back.
# The same route serves both obligations the DPO's approval brings with it: an
# erasure request, which is scoped to nobody's course in particular, and a
# retention period running out, which is scoped to one.
@app.route("/learners", methods=["GET", "POST"])
@auth.admin_required
def learners_page():
    env = read_env()
    error = None
    inventory = None
    result = None

    user_id = (request.values.get("user_id") or "").strip()
    course_id = (request.values.get("course_id") or "").strip()
    if course_id and courses_service.get_course(course_id) is None:
        course_id = ""

    client = _flowise_client()
    action = request.form.get("action", "")

    if request.method == "POST" and user_id:
        try:
            if action == "erase":
                # Typed back, as a course deletion asks for its id. This
                # cannot be undone in four systems at once, and a learner id
                # is a string nobody recognises by sight — which is exactly
                # why the confirmation has to be a deliberate act rather than
                # a second click in the same place.
                typed = (request.form.get("confirm") or "").strip()
                if typed != user_id:
                    error = _t("learners_mistyped")
                else:
                    result = learners.erase(user_id, course_id or None,
                                            flowise=client)
                    if result["erased"] and course_id:
                        # An erasure done because a period ran out is the
                        # thing that period was waiting for.
                        courses_service.mark_retention_applied(course_id)
            else:
                inventory = learners.inventory(user_id, course_id or None,
                                               flowise=client)
        except (learners.LearnerError, courses_service.CourseError,
                db.DatabaseError) as exc:
            error = str(exc)

    return render_template(
        "learners.html", user_id=user_id, course_id=course_id,
        courses=courses_service.all_courses(), inventory=inventory,
        result=result, error=error, lti=learners.lti_configured(env))


# ─── Documents: what is indexed, and removing it ─────────────────────────────────
@app.route("/documents", methods=["GET", "POST"])
@auth.login_required
@with_course
def documents():
    """
    Lists what is actually in the index for this course, and lets the
    operator remove a document.

    Deletion matters more than it sounds. Without it a mistaken upload is
    permanent, a revised edition sits alongside its predecessor and both get
    retrieved, and — the one that produces wrong answers rather than clutter
    — repurposing an agent slot hands the new agent the old one's documents,
    because the chunks still carry that agent_id.
    """
    # From the selected course, not from .env. Those two variables are the
    # single-course era's, and reading them here made this page show one
    # fixed collection whichever course was selected — the list looked
    # authoritative and belonged to somebody else.
    course_id = g.course["id"]
    collection = g.course["collection"]
    slots = storage.all_slots(course_id)
    error = None
    success = None

    # Independent of Weaviate on purpose: a document being processed has no
    # chunks yet, so if this were read from the index it would show nothing
    # during the exact window it exists for. It is also why it is fetched
    # before the not-configured bail-out — an upload in flight is worth
    # showing even when the index cannot be listed.
    jobs = ingest_status.active(g.course["id"])

    if not course_id or not collection:
        return render_template(
            "documents.html", documents=[], slots=slots, truncated=False,
            jobs=jobs, error=_t("docs_err_not_configured"), success=None, total=0)

    client = _weaviate_client()

    if request.method == "POST" and request.form.get("action") == "dismiss_job":
        # Clearing a progress row, which is not deleting a document. The two
        # tables sit one above the other and the wording has to keep them
        # apart: this removes a line that has been read, the one below removes
        # what an agent can find.
        job_id = request.form.get("job_id", "").strip()
        if ingest_status.forget(job_id, course_id):
            success = _t("docs_job_dismissed")
            jobs = ingest_status.active(course_id)
        else:
            error = _t("docs_job_dismiss_failed")
    elif request.method == "POST":
        title = request.form.get("source_title", "").strip()
        source_file = request.form.get("source_file", "").strip()
        also_graph = request.form.get("also_graph") == "1"
        raw_agent = request.form.get("agent_id", "")
        agent_id = int(raw_agent) if raw_agent.isdigit() else None
        if not title:
            error = _t("docs_err_no_title")
        else:
            try:
                removed = client.delete_document(collection, course_id, title, agent_id)
            except WeaviateError as exc:
                logger.error("Deleting %r failed: %s", title, exc)
                error = _t("docs_err_delete_failed", exc)
            else:
                logger.info("Deleted %s chunk(s) of %r (course=%s, agent=%s)",
                            removed, title, course_id, agent_id)
                success = _t("docs_deleted", removed, title)
                # The concept map is built from these documents, so deleting
                # one changes it. Doing that silently leaves concepts citing a
                # work the course no longer has — and the operator finds out
                # when a proposal cites something that is gone.
                #
                # Deliberately after the chunks are gone, and deliberately not
                # in the same breath: if this fails, the document is still
                # deleted and the graph is merely stale, which the graph page
                # reports. The reverse order could remove a document's
                # concepts and then fail to remove the document.
                if also_graph and source_file:
                    try:
                        gone = _neo4j_client().remove_documents(
                            course_id, [source_file])
                    except (Neo4jError, neo4j_client.GraphInputError) as exc:
                        logger.error("Graph clean-up after deleting %r failed: %s",
                                     source_file, exc)
                        success = _t("docs_deleted_graph_failed", removed, title)
                    else:
                        success = _t("docs_deleted_with_graph", removed, title,
                                     gone.get("concepts_removed", 0),
                                     gone.get("concepts_kept", 0),
                                     gone.get("edges_removed", 0))

    documents: list[dict] = []
    total = 0
    truncated = False
    try:
        documents = client.list_documents(collection, course_id)
        total = client.count_chunks(collection, course_id)
    except WeaviateError as exc:
        logger.error("Could not list documents: %s", exc)
        error = error or _t("docs_err_list_failed", exc)
    else:
        # The list is built from a capped read of chunks. Say so rather than
        # showing a short list as if it were complete.
        truncated = sum(d["chunks"] for d in documents) < total

    # What each document is holding up in the concept map, so the weight of a
    # deletion is on the page before the click rather than in the aftermath.
    # A graph that cannot be reached is not an error here: the documents page
    # has its own job, and it still does it.
    graph_weight: dict = {}
    try:
        graph_weight = _neo4j_client().by_source(course_id)
    except Exception as exc:  # noqa: BLE001 — a display column, never a blocker
        logger.info("Graph weights unavailable for the document list: %s", exc)

    return render_template(
        "documents.html", documents=documents, slots=slots, truncated=truncated,
        jobs=jobs, total=total, error=error, success=success,
        graph_weight=graph_weight)


# ─── Knowledge-graph guidance ────────────────────────────────────────────────────
def _present_sources_for(course_id: str) -> list[str]:
    """The documents a course holds, by source_file, without needing g.course.

    The workflow's callback carries a build id, not a session, so the
    request-scoped course of _present_sources is not available to it.
    """
    course = courses_service.get_course(course_id)
    if course is None:
        raise Neo4jError(f"No such course: {course_id}")
    client = _weaviate_client()
    return [d.get("source_file", "") for d
            in client.list_documents(course["collection"], course_id)
            if d.get("source_file")]


def _present_sources(course_id: str) -> list[str]:
    """The documents this course currently holds, by source_file.

    source_file rather than the title: provenance has to survive a document
    being re-titled, and two agents may legitimately upload the same filename
    — the agent is part of this path, the title is not.
    """
    try:
        client = _weaviate_client()
        return [d.get("source_file", "") for d
                in client.list_documents(g.course["collection"], course_id)
                if d.get("source_file")]
    except Exception as exc:  # noqa: BLE001
        # Refusing to guess: an empty list here would look like "no documents
        # at all", and every citation in the graph would be reported stale.
        raise Neo4jError(
            "Could not read the course's documents, so which citations are "
            f"stale cannot be decided: {exc}") from exc


@app.route("/graph-guidance", methods=["GET", "POST"])
@auth.login_required
@with_course
def graph_guidance():
    """The course's concept graph: what is in it, and adding to it.

    The page no longer runs pasted Cypher. It takes the model's answer as
    JSON, validates it here, and writes with parameterised statements that
    carry the course — a boundary cannot be enforced inside a statement
    somebody else wrote, and checking Cypher before running it would mean
    parsing Cypher, which is the kind of nearly-right safeguard that is worse
    than none.
    """
    course_id = g.course["id"]
    client = _neo4j_client()
    error = None
    success = None
    warning = None
    material_note = None
    proposal = ""

    if request.method == "POST":
        action = request.form.get("action", "apply")
        try:
            if action == "delete":
                name = request.form.get("name", "")
                removed = client.delete_concept(course_id, name)
                success = _t("graph_deleted", name) if removed else _t("graph_not_found", name)
            elif action == "build":
                # Start the workflow and return. The scope is read now and
                # stored with the build: the question asked about a proposal
                # later is what it was built from, and today's checkboxes are
                # not that answer.
                scope = [int(num) for num, data
                         in storage.all_slots(course_id).items()
                         if data and data.get("in_graph", True)]
                if not scope:
                    error = _t("graph_build_no_scope")
                else:
                    build = graph_builds.start(
                        course_id, scope, session.get("username", ""))
                    try:
                        _n8n_client().start_graph_build({
                            "build_id": build["id"],
                            "course_id": course_id,
                            "course_name": g.course["name"],
                            "collection": g.course["collection"],
                            "agents": scope,
                            "language": current_language(),
                        })
                    except Exception as exc:  # noqa: BLE001
                        # A build nobody is working on must not sit in the
                        # table as "queued" for ever, blocking every later
                        # attempt through the one-active index.
                        graph_builds.fail(build["id"], f"could not be started: {exc}")
                        logger.error("Could not start the graph build: %s", exc)
                        error = _t("graph_build_start_failed", exc)
                    else:
                        success = _t("graph_build_started", len(scope))
            elif action == "abandon_build":
                # The way out of a run that is going nowhere. Without it the
                # one-active index turns a stuck build into a course that can
                # never be built again.
                stuck = graph_builds.active(course_id)
                if stuck is None:
                    error = _t("graph_build_none_active")
                else:
                    graph_builds.abandon(stuck["id"])
                    success = _t("graph_build_abandoned")
            elif action == "clean_stale":
                # Removing what only vanished material supported. Same
                # machinery as unticking an agent, reached from the other
                # end: no model, no workflow, one transaction.
                present = _present_sources(course_id)
                stale = client.stale_sources(course_id, present)
                if not stale["sources"]:
                    success = _t("graph_stale_none")
                else:
                    gone = client.remove_documents(course_id, stale["sources"])
                    success = _t("graph_stale_cleaned",
                                 gone.get("concepts_removed", 0),
                                 gone.get("concepts_kept", 0),
                                 gone.get("edges_removed", 0))
            elif action == "adopt":
                moved = client.adopt_unassigned(course_id)
                success = _t("graph_adopted", moved, g.course["name"])
            elif action == "clear":
                removed = client.clear_course(course_id)
                success = _t("graph_cleared", removed, g.course["name"])
            elif action == "propose":
                # Reads the course's own material and asks the strong model
                # for the same JSON the page asks a human to fetch by hand.
                # The answer is validated here and written into the review box
                # below — never applied. A concept map decides what an agent
                # treats as a prerequisite for what, and a model that
                # misreads a chapter heading would reorder a course without
                # anyone having looked.
                env = read_env()
                wv = _weaviate_client()
                outline = wv.outline(g.course["collection"], course_id)
                material = wv.outline_as_text(outline)
                answer = llm_client.propose_graph(
                    g.course["name"], material,
                    _t("graph_prompt_text", g.course["name"]), env)
                # Through the same parser a pasted answer goes through, and
                # written back canonically: reviewing text that differs from
                # what would be applied is worse than not reviewing.
                concepts_p, edges_p = neo4j_client.parse_proposal(answer)
                # Empty optional fields are dropped rather than written as
                # null: this text is read by a person deciding whether to
                # trust it, and three nulls per concept is noise between them
                # and the names they are checking.
                proposal = json.dumps(
                    {"concepts": [{k: v for k, v in c.items() if v is not None}
                                  for c in concepts_p],
                     "prerequisites": edges_p},
                    indent=2, ensure_ascii=False)
                material_note = _t(
                    "graph_proposed_from",
                    len(outline["documents"]), outline["sections"])
                if outline["truncated"]:
                    warning = _t("graph_proposed_truncated")
            else:
                proposal = request.form.get("proposal", "")
                # The build this proposal came from, if it came from one. It
                # gives every concept and edge a build id, which is what makes
                # a build undoable later — and marks the build as reviewed, so
                # a finished proposal stops reappearing in the box after it
                # has been acted on.
                pending = graph_builds.awaiting_review(course_id)
                build_id = pending["id"] if pending else ""
                concepts, edges = neo4j_client.parse_proposal(proposal)
                written = client.apply_proposal(course_id, concepts, edges,
                                                build_id=build_id)
                if build_id:
                    graph_builds.applied(build_id)
                success = _t("graph_applied", written["concepts"], written["edges"])
                proposal = ""
        except graph_builds.BuildError as exc:
            # A refusal with a sentence in it — an already-running build, most
            # often. Uncaught this was a 500, so the one guard against paying
            # twice for the same corpus reached nobody who needed it.
            error = str(exc)
        except neo4j_client.GraphInputError as exc:
            # The reader did not write this input — a model did — so the
            # message says which part to fix rather than "invalid".
            error = str(exc)
        except LLMError as exc:
            # No key, no material, a provider having a bad minute. None of
            # that is a graph problem, and the manual path on this same page
            # still works — so it is said here rather than raised.
            error = _t("graph_propose_failed", exc)
        except (WeaviateError, Neo4jError) as exc:
            error = str(exc)

    try:
        concepts = client.concepts(course_id)
        edges = client.edges(course_id)
        counts = client.counts(course_id)
        unassigned = client.unassigned_count()
    except Neo4jError as exc:
        return render_template("graph_guidance.html", error=error or str(exc),
                               success=success, warning=warning,
                               material_note=material_note,
                               concepts=[], edges=[],
                               counts={"concepts": 0, "edges": 0}, unassigned=0,
                               proposal=proposal, stale=None, build=None)

    # Asked on every load rather than trusted to whoever deleted something.
    # A document can leave the course by paths that never touch this page,
    # and a citation to material that is gone should be something the
    # operator is told about, not something a later proposal reveals.
    stale = None
    try:
        found = client.stale_sources(course_id, _present_sources(course_id))
        if found["concepts"]:
            stale = found
    except (Neo4jError, Exception) as exc:  # noqa: BLE001 — a notice, not the page
        logger.info("Could not check for stale citations: %s", exc)

    # A finished build fills the review box, unless this request already put
    # something there — a pasted answer being edited must not be replaced by
    # a stored proposal on every reload.
    build = graph_builds.latest(course_id)
    # The proposal to show is the newest one *awaiting review*, which is not
    # necessarily the newest build: starting a second run must not hide a
    # finished proposal nobody has read yet.
    ready = graph_builds.awaiting_review(course_id)
    if ready and not proposal:
        proposal = json.dumps(ready["proposal"], indent=2, ensure_ascii=False)
        stats = ready.get("stats") or {}
        material_note = material_note or _t(
            "graph_build_from", len(ready.get("scope") or []),
            stats.get("documents", 0))
        if build and build["active"] and build["id"] != ready["id"]:
            # Both things are true and the page has to say so, or the running
            # one reads as the reason the box is full.
            warning = warning or _t("graph_build_older_proposal")

    return render_template("graph_guidance.html", error=error, success=success,
                           warning=warning, material_note=material_note,
                           concepts=concepts, edges=edges, counts=counts,
                           unassigned=unassigned, proposal=proposal,
                           stale=stale, build=build)
