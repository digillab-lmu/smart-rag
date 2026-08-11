"""
Live status checks for the onboarding guide.

Every check answers one question an operator actually has while setting the
system up ("is n8n ready to receive documents?"), by asking the service
itself over the internal Docker network — never by reading a flag we wrote
down earlier. A stored "yes" that has since become false is worse than no
answer at all, because it sends the operator looking in the wrong place.

Same architectural boundary as the rest of this GUI: HTTP to other
containers only. Nothing here shells out, touches the Docker socket or
reads the host filesystem, which is also why the n8n workflow import can
only ever be *shown* as a command to run, not run from here.

Checks never raise. A check that cannot determine its answer says so
(State.UNKNOWN) rather than guessing or blowing up the page — an
unreachable service during setup is the normal case, not an exception.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import requests

logger = logging.getLogger(__name__)

# Health endpoints, each taken from that service's own healthcheck in
# docker/docker-compose.yml so this agrees with what Docker considers
# healthy instead of inventing a second opinion.
WEAVIATE_HEALTH = "http://smartrag-weaviate:{port}/v1/.well-known/ready"
DOCLING_HEALTH = "http://smartrag-docling:5001/health"
MARKDOWNCLEANER_HEALTH = "http://smartrag-markdowncleaner:8000/health"

# Short: these are same-network calls to neighbouring containers, and the
# page waits for all of them in series. A service that needs longer than
# this to answer a health check is not ready either way.
TIMEOUT = 5


class State(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass
class Check:
    """One line in the guide.

    `key` names the i18n strings (title/how-to). `detail` is free text
    from the service itself — an error message, a version — and is shown
    verbatim, because during setup the raw text is usually the fix.
    `command` is a shell command the operator must run themselves.
    """

    key: str
    state: State
    detail: str = ""
    command: str = ""
    link: str = ""
    link_key: str = ""
    # The service's own web address, for opening it in a new tab. Distinct
    # from `link`, which names a route inside this GUI: this one leaves it.
    # Shown in every state, not only on failure — "open Flowise" is a normal
    # thing to want, and having to hunt for the address in an email from the
    # installation day is how people end up asking an administrator for a
    # URL that is written in .env.
    external_url: str = ""
    # Blocks the steps after it: no point telling someone to import agents
    # while Flowise is unreachable.
    blocking: bool = False
    extra: dict = field(default_factory=dict)


def _probe(url: str) -> tuple[bool, str]:
    """GET a health endpoint. Returns (reachable, detail)."""
    try:
        resp = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, str(exc)
    if not resp.ok:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return True, ""


# ─── individual checks ───────────────────────────────────────────────────────
def check_llm_keys(env: dict) -> Check:
    """The two API keys nothing else works without. Not validated against
    the provider here — that costs a request per page load and would fail
    for reasons (rate limit, network) that say nothing about the key."""
    missing = [
        name for name in ("LLM_API_KEY", "EMBEDDING_API_KEY")
        if not env.get(name, "").strip()
        # bootstrap writes literal placeholders; an untouched one is
        # "not configured", not "configured with a bad value".
        or env.get(name, "").strip().startswith("your-")
    ]
    if missing:
        return Check("llm_keys", State.FAIL, ", ".join(missing), blocking=True)
    return Check("llm_keys", State.OK, env.get("LLM_PROVIDER", ""))


def check_flowise(env: dict, client) -> Check:
    """Reachable *and* the stored key is accepted. Those are different
    failures with different fixes, so they get different messages."""
    url = env.get("FLOWISE_PUBLIC_URL", "").strip()
    if client is None:
        return Check("flowise", State.FAIL, "", link_key="flowise_link",
                     link="flowise_setup", external_url=url, blocking=True)
    try:
        client.check_connection()
    except Exception as exc:  # noqa: BLE001 — FlowiseError or a transport error
        return Check("flowise", State.FAIL, str(exc), link_key="flowise_link",
                     link="flowise_setup", external_url=url, blocking=True)
    return Check("flowise", State.OK, external_url=url)


def check_agents(slots: dict, client) -> Check:
    """Counts what is filled in versus what actually reached Flowise."""
    configured = [s for s in slots.values() if s.get("archetype")]
    imported = [s for s in configured if s.get("chatflow_id")]

    if not configured:
        return Check("agents", State.FAIL, link_key="agents_link", link="dashboard",
                     blocking=True)
    if not imported:
        return Check("agents", State.WARN, link_key="agents_link", link="dashboard",
                     extra={"configured": len(configured), "imported": 0},
                     blocking=True)

    # An id in slots.json that Flowise no longer knows means someone
    # deleted the chatflow there; the agent is gone even though this page
    # would otherwise report success.
    stale = 0
    if client is not None:
        try:
            live = {cf.get("id") for cf in client.list_chatflows()}
        except Exception as exc:  # noqa: BLE001
            logger.info("Could not cross-check chatflows: %s", exc)
        else:
            stale = sum(1 for s in imported if s.get("chatflow_id") not in live)

    extra = {"configured": len(configured), "imported": len(imported), "stale": stale}
    if stale:
        return Check("agents", State.WARN, link_key="agents_link", link="dashboard",
                     extra=extra)
    return Check("agents", State.OK, link_key="agents_link", link="dashboard",
                 extra=extra)


def check_n8n_webhook(base_url: str, deploy_command: str, env: dict | None = None) -> Check:
    """Is the ingest workflow imported AND active?

    Probed with a GET on the production webhook path, which has no side
    effects — a POST would start a real ingest run. n8n distinguishes the
    two failure modes for us in the response body (verified in
    packages/cli/src/errors/response-errors/webhook-not-found.error.ts):

      * registered for POST, asked with GET
            "This webhook is not registered for GET requests.
             Did you mean to make a POST request?"
      * not registered at all (missing or inactive workflow)
            'The requested webhook "GET document-ingest" is not registered.'

    So the *rejection we want* is the first one — a 404 that proves the
    POST route exists. Treating any 404 as failure would report a working
    system as broken.
    """
    # The address a person opens is not the one this check probes: the probe
    # goes to the container over the Docker network, the link has to work in
    # a browser.
    public = (env or {}).get("N8N_WEBHOOK_URL", "").strip()
    url = f"{base_url.rstrip('/')}/webhook/document-ingest"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return Check("n8n", State.FAIL, str(exc), command=deploy_command,
                     external_url=public, blocking=True)

    body = resp.text or ""
    if "not registered for GET requests" in body:
        return Check("n8n", State.OK, external_url=public)
    if "is not registered" in body:
        return Check("n8n", State.FAIL, body[:200], command=deploy_command,
                     external_url=public, blocking=True)
    # n8n answered something else entirely. Say what, rather than
    # inventing an interpretation.
    return Check("n8n", State.UNKNOWN, f"HTTP {resp.status_code}: {body[:200]}",
                 command=deploy_command, external_url=public)


def check_ingest_services(env: dict) -> Check:
    """Docling, markdowncleaner and Weaviate — the three the ingest
    pipeline calls in turn. Reported as one line because to the operator
    they are one capability ("can this system take a document?"), with the
    individual results underneath."""
    port = env.get("WEAVIATE_HTTP_PORT", "8080").strip() or "8080"
    targets = {
        "docling": DOCLING_HEALTH,
        "markdowncleaner": MARKDOWNCLEANER_HEALTH,
        "weaviate": WEAVIATE_HEALTH.format(port=port),
    }
    results = {}
    for name, url in targets.items():
        ok, detail = _probe(url)
        results[name] = {"ok": ok, "detail": detail}

    down = [n for n, r in results.items() if not r["ok"]]
    if not down:
        return Check("ingest_services", State.OK, extra=results)
    return Check("ingest_services", State.FAIL, ", ".join(down), extra=results,
                 blocking=True)


def run_all(env: dict, slots: dict, flowise_client, n8n_base_url: str,
            deploy_command: str) -> list[Check]:
    """In the order an operator works through them: keys, then Flowise,
    then agents, then the ingest side. Each returns whatever it found —
    one failing check never stops the others, because seeing every
    problem at once is the point of this page."""
    return [
        check_llm_keys(env),
        check_flowise(env, flowise_client),
        check_agents(slots, flowise_client),
        check_n8n_webhook(n8n_base_url, deploy_command, env),
        check_ingest_services(env),
    ]
