"""
Langfuse, for the one thing deleting a course has to do there.

A trace carries Flowise's chat id, as its own `sessionId`, and nothing else
that identifies anybody. It carries no course, and — despite what this
paragraph claimed until 2026-08-19 — **no learner either**: Flowise 3.1.3's
AnalyticHandler builds `langfuse.trace({ name, sessionId: this.options.chatId
})` and never sets a userId. Read in the source, after the earlier sentence
here had been believed long enough to be built on.

So the only route from a course to its traces runs

    course → its chatflows → Flowise's chat records → chatId → sessionId

and Flowise deletes those chat records together with the chatflow. Which
fixes the order of a course deletion: the session ids have to be collected
**before** the chatflows go, or the traces become unreachable in the same
moment the course does.

The route from a *person* to their traces runs through the same records —
see learners.py — for the same reason: there is no query by learner here.

Everything here is read from Langfuse's OpenAPI specification rather than
remembered:

  * `GET /api/public/traces` filters by `sessionId` and pages with `page` and
    `limit`, answering `{data: [...], meta: {page, limit, totalItems,
    totalPages}}`.
  * `DELETE /api/public/traces` — operationId `trace_deleteMultiple` — takes
    `{"traceIds": [...]}`.
  * Basic auth, public key as the user name and secret key as the password.

**Deletion is asynchronous.** Langfuse's own documentation says trace data is
removed within about fifteen minutes and gives no confirmation. So this
module reports what it asked for, never what happened, and every caller has
to phrase it that way too.
"""

import logging
import os

import requests

from env_file import read_env

logger = logging.getLogger(__name__)

# Langfuse advises against very large delete batches. Fifty is the upper end
# of what its documentation suggests.
BATCH = 50
# A trace list page. Larger pages are allowed but the endpoint's own note says
# to reduce this if requests fail, so it starts where it is comfortable.
PAGE = 100


class LangfuseError(RuntimeError):
    """Said in terms of what could not be done to which project."""


class LangfuseClient:
    def __init__(self, base_url: str = "", public_key: str = "",
                 secret_key: str = "", timeout: int = 30):
        env = read_env()
        port = env.get("LANGFUSE_PORT", "3001")
        # The internal name: this container is on the same network, and going
        # out through nginx would add a certificate to the list of things that
        # can break a deletion.
        self.base = (base_url or os.getenv(
            "SMARTRAG_LANGFUSE_URL",
            f"http://smartrag-langfuse-web:{port}")).rstrip("/")
        self.public_key = public_key or env.get(
            "LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "").strip()
        self.secret_key = secret_key or env.get(
            "LANGFUSE_INIT_PROJECT_SECRET_KEY", "").strip()
        self.timeout = timeout

    @staticmethod
    def configured(env: dict | None = None) -> bool:
        """Whether this installation runs Langfuse at all.

        The observability profile is optional. A deletion on an installation
        without it must report the step as skipped — not as done, which would
        claim traces were removed that never existed, and not as failed.
        """
        env = env if env is not None else read_env()
        return ("observability" in (env.get("COMPOSE_PROFILES") or "")
                and bool(env.get("LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "").strip()))

    def _request(self, method: str, path: str, **kwargs):
        if not self.public_key or not self.secret_key:
            raise LangfuseError(
                "The Langfuse project keys are missing from .env "
                "(LANGFUSE_INIT_PROJECT_PUBLIC_KEY / _SECRET_KEY), so its "
                "traces cannot be reached.")
        try:
            resp = requests.request(
                method, f"{self.base}{path}",
                auth=(self.public_key, self.secret_key),
                timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise LangfuseError(f"Langfuse is not reachable: {exc}") from exc
        if resp.status_code in (401, 403):
            raise LangfuseError(
                "Langfuse refused the project keys from .env. They are the "
                "LANGFUSE_INIT_PROJECT_* pair, not the Garage ones next to "
                "them.")
        if not resp.ok:
            raise LangfuseError(
                f"Langfuse answered HTTP {resp.status_code}: {resp.text[:200]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise LangfuseError(
                f"Langfuse answered something that is not JSON: {resp.text[:120]}"
            ) from exc

    def _trace_ids(self, key: str, value: str, cap: int) -> list[str]:
        """Every trace matching one filter, following the page count.

        A client that reads the first page only would leave most of a long
        conversation's traces behind while reporting it done — the same shape
        as a bucket listing that ignores its continuation token.
        """
        if not value:
            return []
        ids: list[str] = []
        page = 1
        while True:
            answer = self._request(
                "GET", "/api/public/traces",
                params={key: value, "page": page, "limit": PAGE})
            for trace in (answer or {}).get("data") or []:
                if trace.get("id"):
                    ids.append(trace["id"])
            meta = (answer or {}).get("meta") or {}
            total_pages = int(meta.get("totalPages") or 1)
            if page >= total_pages or len(ids) >= cap:
                return ids
            page += 1

    def trace_ids_for_session(self, session_id: str, cap: int = 10_000) -> list[str]:
        """Every trace of one session id.

        **Which string is the session id depends on how the chat was
        opened**, which is why callers here pass more than one:

          * Opened through the LTI middleware, Flowise receives
            `overrideConfig.analytics.langFuse = {userId, sessionId}`, and
            both handler sites in Flowise 3.1.3 spread that object *over*
            their defaults. So the trace's sessionId is the middleware's
            string, `<sub>|<given name>|<agent>|<timestamp>|<full name>`.
          * Opened without it, nothing overrides anything and the default
            stands: `sessionId = options.chatId`, Flowise's own conversation
            id.

        Both are stored in Flowise's chat records — as `sessionId` and
        `chatId` respectively — so asking for both covers either deployment
        without having to detect which one this is.
        """
        return self._trace_ids("sessionId", session_id, cap)

    def trace_ids_for_user(self, user_id: str, cap: int = 10_000) -> list[str]:
        """Every trace of one learner, where the learner is on the trace.

        Only true on an installation that launches its chats through the LTI
        middleware: that is what sends a `userId` for Flowise to spread into
        the Langfuse options. Without it a trace carries no learner at all,
        and this returns nothing — which is correct, and is why it is never
        the only route an erasure takes.

        `userId` is a documented filter of `GET /api/public/traces`, read from
        Langfuse's own OpenAPI specification. The endpoint is deprecated on
        Langfuse Cloud from November 2026; self-hosted deployments — which is
        every installation of this system — keep it until they move to
        Langfuse v4.
        """
        return self._trace_ids("userId", user_id, cap)

    def delete_traces(self, trace_ids: list[str]) -> int:
        """Ask for these traces to be deleted. Returns how many were asked for.

        Asked for, not deleted: Langfuse removes trace data within about
        fifteen minutes and returns no confirmation, so a return value that
        claimed otherwise would be a promise this code cannot keep. Scores and
        observations go with them — that is Langfuse's behaviour, and it is
        what an erasure request needs.
        """
        unique = list(dict.fromkeys(i for i in trace_ids if i))
        for start in range(0, len(unique), BATCH):
            batch = unique[start:start + BATCH]
            self._request("DELETE", "/api/public/traces",
                          json={"traceIds": batch})
            logger.info("Asked Langfuse to delete %s trace(s)", len(batch))
        return len(unique)
