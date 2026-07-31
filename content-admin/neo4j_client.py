"""
Minimal Neo4j client for the knowledge-graph guidance page's "paste Cypher,
run it" box. Uses the HTTP transactional Cypher endpoint (Basic Auth) —
NOT `docker exec cypher-shell` like scripts/deploy-schemas.sh, because this
GUI deliberately has no Docker access at all (see the batch's architecture
note: content GUI stays app-to-app only, never host/Docker level).

Same endpoint shape already used elsewhere in this project — the Flowise
agent templates' "Load neo4j Prerequisites" custom-function node talks to
http://smartrag-neo4j:7474/db/neo4j/tx/commit the same way.
"""

import requests


class Neo4jError(RuntimeError):
    pass


class Neo4jClient:
    def __init__(self, base_url: str, user: str, password: str, database: str = "neo4j"):
        self.base = base_url.rstrip("/")
        self.auth = (user, password)
        self.database = database

    def run_script(self, cypher_script: str) -> list[dict]:
        """
        Splits on ';' (naive, but adequate for the guidance page's use case:
        an LLM-generated block of MERGE statements, one per line/semicolon —
        the same style as neo4j/seed.example.cypher). Runs all statements in
        ONE transaction, so a bad statement rolls back the whole batch rather
        than leaving a half-applied graph.
        """
        statements = [
            {"statement": s.strip()}
            for s in cypher_script.split(";")
            if s.strip() and not s.strip().startswith("//")
        ]
        if not statements:
            raise Neo4jError("No statements found in the pasted Cypher.")

        url = f"{self.base}/db/{self.database}/tx/commit"
        try:
            resp = requests.post(
                url, json={"statements": statements}, auth=self.auth, timeout=30
            )
        except requests.RequestException as exc:
            raise Neo4jError(f"Could not reach Neo4j: {exc}") from exc

        if not resp.ok:
            raise Neo4jError(f"Neo4j HTTP {resp.status_code}: {resp.text[:500]}")

        body = resp.json()
        errors = body.get("errors") or []
        if errors:
            msgs = "; ".join(e.get("message", str(e)) for e in errors)
            raise Neo4jError(f"Cypher error(s): {msgs}")
        return body.get("results", [])
