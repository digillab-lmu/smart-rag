"""
Thin wrapper around the Flowise REST API.

Only the handful of calls the content GUI needs. Every create is idempotent
(list/lookup by name first) so re-importing an agent doesn't pile up duplicate
credentials/variables.

Auth: Bearer token — the operator generates it once in Flowise
(Settings → API Keys → Create Key) and pastes it into this GUI's first-run
setup. Flowise has no supported way to pre-provision one via env var; this was
researched and the DB-seeding workaround deliberately rejected (undocumented,
multi-table, breaks on upgrades).
"""

import requests


class FlowiseError(RuntimeError):
    """Raised with Flowise's own error text — never swallowed silently, the
    GUI shows it to the operator verbatim so a failed import is diagnosable."""


class FlowiseClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # ─── internals ──────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise FlowiseError(f"{method} {url} failed: {exc}") from exc

        if not resp.ok:
            raise FlowiseError(
                f"{method} {path} → HTTP {resp.status_code}: {resp.text[:500]}"
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # ─── connectivity ───────────────────────────────────────────────────────
    def check_connection(self) -> None:
        """Cheap auth/reachability probe — used by first-run setup to validate
        the pasted API key before storing it, instead of accepting a bad key
        and failing later mid-import."""
        self._request("GET", "/chatflows")

    # ─── chatflows ──────────────────────────────────────────────────────────
    def list_chatflows(self) -> list[dict]:
        return self._request("GET", "/chatflows") or []

    def find_chatflow_by_name(self, name: str) -> dict | None:
        for cf in self.list_chatflows():
            if cf.get("name") == name:
                return cf
        return None

    def create_chatflow(
        self, name: str, flow_data: str, deployed: bool = True, analytic: str | None = None
    ) -> dict:
        payload = {
            "name": name,
            "flowData": flow_data,
            "deployed": deployed,
            "isPublic": False,
            "type": "AGENTFLOW",
        }
        if analytic is not None:
            payload["analytic"] = analytic
        return self._request("POST", "/chatflows", json=payload)

    def update_chatflow(
        self, chatflow_id: str, name: str, flow_data: str, analytic: str | None = None
    ) -> dict:
        payload = {"name": name, "flowData": flow_data}
        # Only sent when given: updateChatflow merges the body into the stored
        # entity, so omitting it preserves whatever tracing is configured
        # rather than clearing it.
        if analytic is not None:
            payload["analytic"] = analytic
        return self._request("PUT", f"/chatflows/{chatflow_id}", json=payload)

    def get_chatflow(self, chatflow_id: str) -> dict | None:
        """Returns the chatflow, or None if it no longer exists in Flowise —
        which happens whenever someone deletes it there while slots.json still
        remembers its id. Callers treat that as "not imported" rather than as
        an error."""
        try:
            return self._request("GET", f"/chatflows/{chatflow_id}")
        except FlowiseError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def chat_session_ids(self, chatflow_id: str) -> list[str]:
        """The distinct chat ids of one chatflow's conversations.

        Named for what it is used for and not for what it returns, which is
        confusing enough to write down: these are Flowise `chatId` values,
        because that is what a Langfuse trace carries as its own `sessionId`.
        Flowise's `sessionId` is a different string and is not what Langfuse
        stores — see chat_records.

        Needed before the chatflow is deleted, and only then: Flowise removes
        these records together with the chatflow, which closes the only
        bridge from a course to its traces at the same moment.

        An empty list for a chatflow nobody ever talked to, and for one that
        no longer exists: both mean "no sessions to carry over", and neither
        is a reason to stop a deletion.
        """
        seen: dict[str, None] = {}
        for record in self.chat_records(chatflow_id):
            if record["chat_id"]:
                seen[record["chat_id"]] = None
        return list(seen)

    def chat_records(self, chatflow_id: str) -> list[dict]:
        """Every conversation record of one chatflow, as {session_id, chat_id}.

        Two different ids, and the difference is the whole point:

          * `sessionId` is what the embedding sets, and in this system it is
            `<learner>|<something>` — every agent derives the learner from it
            with `$flow.sessionId?.split('|')[0]`, and chathistory-sync stores
            the same split as `user_id`. So this is the only field that says
            *who*.
          * `chatId` is Flowise's own conversation id, and it is what a
            Langfuse trace carries as its `sessionId`. So this is the only
            field that reaches the traces.

        One learner therefore needs both: the session ids to delete from
        Flowise, and the chat ids of those sessions to delete from Langfuse.
        """
        try:
            messages = self._request("GET", f"/chatmessage/{chatflow_id}") or []
        except FlowiseError as exc:
            if "HTTP 404" in str(exc):
                return []
            raise
        seen: dict[tuple[str, str], None] = {}
        for message in messages:
            message = message or {}
            session_id = message.get("sessionId") or ""
            chat_id = message.get("chatId") or ""
            if session_id or chat_id:
                seen[(session_id, chat_id)] = None
        return [{"session_id": s, "chat_id": c} for s, c in seen]

    def delete_chat_session(self, chatflow_id: str, session_id: str) -> None:
        """Delete one conversation of one chatflow, by its session id.

        `DELETE /chatmessage/{chatflowId}?sessionId=…`, which in 3.1.3 passes
        the value to utilGetChatMessage as a TypeORM equality — an exact
        match, not a prefix. Read in the source: a prefix match would be the
        difference between erasing one learner and erasing everyone whose id
        begins with the same characters.

        An empty session id is refused rather than sent. Flowise treats an
        absent sessionId as "no filter" and would delete every conversation
        of the chatflow.
        """
        if not session_id:
            raise FlowiseError(
                "Refusing to delete a chat session without a session id — "
                "Flowise would read that as every session of the chatflow."
            )
        self._request("DELETE", f"/chatmessage/{chatflow_id}",
                      params={"sessionId": session_id})

    def delete_chatflow(self, chatflow_id: str) -> bool:
        """Remove a chatflow. True when it was there, False when it was not.

        Flowise deletes more than the flow: its own deleteChatflow removes the
        ChatMessage, ChatMessageFeedback and UpsertHistory rows and the
        uploaded files belonging to it — read in the 3.1.3 source rather than
        inferred from the API documentation, which does not say. That is why
        the Content Admin never needs to reach into Flowise's database, and
        why anything that needs those records must read them first.
        """
        try:
            self._request("DELETE", f"/chatflows/{chatflow_id}")
            return True
        except FlowiseError as exc:
            # Already gone is the desired state, not a failure: a deletion is
            # re-runnable after a partial one, and the second run must not
            # stop on the half that succeeded.
            if "HTTP 404" in str(exc):
                return False
            raise

    def set_chatflow_public(self, chatflow_id: str, is_public: bool) -> None:
        """Toggles public access for one chatflow.

        Only isPublic is sent. Flowise's updateChatflow merges the request
        body into the stored entity (repository.merge), so everything not
        named here — flowData, name, deployed, category — is preserved;
        verified in the 3.1.3 source, not assumed. Sending flowData too would
        mean re-uploading a flow this route never read, and any drift between
        Flowise and slots.json would silently overwrite the live agent.
        """
        self._request("PUT", f"/chatflows/{chatflow_id}", json={"isPublic": is_public})

    def upsert_chatflow(
        self, name: str, flow_data: str, analytic: str | None = None
    ) -> tuple[str, bool]:
        """Returns (chatflow_id, created). Re-importing an edited agent updates
        the existing flow in place rather than creating a second one with the
        same name — otherwise the CHATFLOW_AGENT0X id in .env would point at a
        stale copy.

        `analytic` is Flowise's per-chatflow tracing configuration, a JSON
        string. Tracing cannot be switched on globally for Langfuse: Flowise's
        env-based tracing (packages/components/src/tracingEnv.ts) covers only
        LangSmith, so every chatflow carries its own setting. Left unset, the
        agent runs and reports nothing — which is how an installation ends up
        with Langfuse and ClickHouse running and an empty dashboard.
        """
        payload_extra = {"analytic": analytic} if analytic is not None else {}
        existing = self.find_chatflow_by_name(name)
        if existing:
            self.update_chatflow(existing["id"], name, flow_data, **payload_extra)
            return existing["id"], False
        created = self.create_chatflow(name, flow_data, **payload_extra)
        return created["id"], True

    @staticmethod
    def langfuse_analytic(credential_id: str) -> str:
        """The shape Flowise's handler reads: analytic[provider].status and
        .credentialId (packages/components/src/handler.ts). The provider key
        is "langFuse", capital F — spelled otherwise the block is simply
        skipped, with no error anywhere."""
        import json as _json

        return _json.dumps(
            {"langFuse": {"credentialId": credential_id, "status": True}}
        )

    # ─── credentials ────────────────────────────────────────────────────────
    def list_credentials(self) -> list[dict]:
        return self._request("GET", "/credentials") or []

    def upsert_credential(
        self, name: str, credential_name: str, plain_data: dict
    ) -> str:
        """
        credential_name is Flowise's *type* identifier (e.g. "anthropicApi",
        "openAIApi"), name is the human label we look it up by.

        Writes the value every time, including when the credential already
        exists. The previous version returned an existing credential
        untouched, which made a rotated API key impossible to apply: changing
        it in .env did not reach Flowise, and re-importing the agent found the
        credential by name and reused the old value. Nothing reported a
        problem — the agents simply kept authenticating with a key that had
        been replaced.

        Flowise's own routes allow this: PUT /credentials/:id needs
        credentials:create OR credentials:update (packages/server/src/routes/
        credentials/index.ts), and credentials:create is already required to
        import an agent at all.

        Note: POST /credentials is not in Flowise's published API reference,
        but is the real, working endpoint (verified against community usage).
        If a future Flowise release changes it, this call is where it breaks —
        the error surfaces to the operator rather than failing silently.
        """
        payload = {
            "name": name,
            "credentialName": credential_name,
            "plainDataObj": plain_data,
        }
        for cred in self.list_credentials():
            if cred.get("name") == name:
                self._request("PUT", f"/credentials/{cred['id']}", json=payload)
                return cred["id"]
        created = self._request("POST", "/credentials", json=payload)
        return created["id"]

    # ─── variables ──────────────────────────────────────────────────────────
    def list_variables(self) -> list[dict]:
        return self._request("GET", "/variables") or []

    def get_or_create_variable(self, name: str, value: str) -> str:
        for var in self.list_variables():
            if var.get("name") == name:
                # Value may have changed in .env since it was first created
                if var.get("value") != value:
                    self._request(
                        "PUT",
                        f"/variables/{var['id']}",
                        json={"name": name, "value": value, "type": "static"},
                    )
                return var["id"]
        created = self._request(
            "POST", "/variables", json={"name": name, "value": value, "type": "static"}
        )
        return created["id"]
