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

    def create_chatflow(self, name: str, flow_data: str, deployed: bool = True) -> dict:
        payload = {
            "name": name,
            "flowData": flow_data,
            "deployed": deployed,
            "isPublic": False,
            "type": "AGENTFLOW",
        }
        return self._request("POST", "/chatflows", json=payload)

    def update_chatflow(self, chatflow_id: str, name: str, flow_data: str) -> dict:
        payload = {"name": name, "flowData": flow_data}
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

    def upsert_chatflow(self, name: str, flow_data: str) -> tuple[str, bool]:
        """Returns (chatflow_id, created). Re-importing an edited agent updates
        the existing flow in place rather than creating a second one with the
        same name — otherwise the CHATFLOW_AGENT0X id in .env would point at a
        stale copy."""
        existing = self.find_chatflow_by_name(name)
        if existing:
            self.update_chatflow(existing["id"], name, flow_data)
            return existing["id"], False
        created = self.create_chatflow(name, flow_data)
        return created["id"], True

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
