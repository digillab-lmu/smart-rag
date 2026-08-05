"""
Thin Weaviate client for listing and removing ingested documents.

The GUI needs two things the ingest pipeline never provided: seeing what is
actually in the index, and taking something out again. Without the second,
a wrong upload is permanent, a revised edition sits next to its predecessor,
and repurposing an agent slot silently hands the new agent the old one's
corpus — the chunks still carry that agent_id.

Same architectural boundary as the rest of this GUI: HTTP to Weaviate over
the internal Docker network. No Docker socket, no host filesystem.

─── A warning about filter formats ──────────────────────────────────────────
There are TWO filter shapes in this project, and they are not interchangeable.

Weaviate's REST API (what this module speaks) uses the classic `where`
filter, verified against weaviate/weaviate's own openapi-specs/schema.json:

    {"operator": "And",
     "operands": [{"path": ["course_id"], "operator": "Equal",
                   "valueText": "…"}]}

    → `operands`, `path` as a list, and a TYPED value field: valueText for
      text properties, valueInt for int ones.

Flowise's vector-store node speaks to Weaviate through the gRPC-based
TypeScript client, whose filters look different (see the agent templates):

    {"operator": "And",
     "filters": [{"operator": "Equal", "target": {"property": "course_id"},
                  "value": "…"}]}

    → `filters`, `target.property`, and one untyped `value` whose JSON type
      decides the encoding.

Using one shape where the other belongs fails quietly: the request is
accepted and simply matches nothing. Keep them apart.
"""

from __future__ import annotations

import json
import logging

import requests

logger = logging.getLogger(__name__)


class WeaviateError(RuntimeError):
    """Raised with Weaviate's own message where there is one — a failed
    delete must be visible, not swallowed into an empty result."""


class WeaviateClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # ─── internals ──────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise WeaviateError(f"{method} {url} failed: {exc}") from exc

        if not resp.ok:
            raise WeaviateError(f"{method} {path} → HTTP {resp.status_code}: {resp.text[:500]}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def _graphql(self, query: str):
        data = self._request("POST", "/v1/graphql", json={"query": query})
        # GraphQL answers 200 with an `errors` array — treating that as
        # success would show an empty document list for a broken query,
        # which reads exactly like "no documents".
        if isinstance(data, dict) and data.get("errors"):
            messages = "; ".join(
                e.get("message", str(e)) for e in data["errors"] if isinstance(e, dict)
            )
            raise WeaviateError(f"GraphQL: {messages or data['errors']}")
        return data

    # ─── connectivity ───────────────────────────────────────────────────────
    def check_connection(self) -> None:
        self._request("GET", "/v1/.well-known/ready")

    # ─── listing ────────────────────────────────────────────────────────────
    def list_documents(self, collection: str, course_id: str, limit: int = 2000) -> list[dict]:
        """
        One row per ingested document, grouped from its chunks.

        Weaviate has no notion of a "document" — only chunks — so the
        grouping happens here. `limit` caps how many chunks are read to
        build the list; a course past that many gets a truncated view,
        which the caller reports rather than hides.
        """
        # GraphQL string literals: a quote or backslash in the course id
        # would otherwise end the literal early. json.dumps escapes both.
        course_literal = json.dumps(course_id)
        query = f"""
        {{
          Get {{
            {collection}(
              limit: {int(limit)}
              where: {{ path: ["course_id"], operator: Equal, valueText: {course_literal} }}
            ) {{
              source_title
              source_file
              authors
              year
              agent_id
              doc_type
              ingest_date
            }}
          }}
        }}
        """
        data = self._graphql(query)
        rows = (((data or {}).get("data") or {}).get("Get") or {}).get(collection) or []

        # Group by the pair the delete path also keys on, so what the
        # operator sees and what gets removed cannot drift apart.
        grouped: dict[tuple, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = (row.get("source_title") or "").strip()
            agent_id = row.get("agent_id")
            key = (title, agent_id)
            entry = grouped.get(key)
            if entry is None:
                entry = {
                    "source_title": title,
                    "source_file": row.get("source_file") or "",
                    "authors": row.get("authors") or "",
                    "year": row.get("year"),
                    "agent_id": agent_id,
                    "doc_type": row.get("doc_type") or "",
                    "ingest_date": row.get("ingest_date") or "",
                    "chunks": 0,
                }
                grouped[key] = entry
            entry["chunks"] += 1
            # Keep the earliest ingest date seen, so a re-ingested document
            # doesn't look newer than it is.
            if row.get("ingest_date") and (
                not entry["ingest_date"] or row["ingest_date"] < entry["ingest_date"]
            ):
                entry["ingest_date"] = row["ingest_date"]

        return sorted(
            grouped.values(),
            key=lambda d: (d["agent_id"] if d["agent_id"] is not None else -1, d["source_title"]),
        )

    def count_chunks(self, collection: str, course_id: str) -> int:
        """Total chunks for a course — used to tell "no documents" apart from
        "the list was truncated"."""
        query = f"""
        {{
          Aggregate {{
            {collection}(
              where: {{ path: ["course_id"], operator: Equal, valueText: {json.dumps(course_id)} }}
            ) {{ meta {{ count }} }}
          }}
        }}
        """
        data = self._graphql(query)
        agg = (((data or {}).get("data") or {}).get("Aggregate") or {}).get(collection) or []
        if not agg:
            return 0
        return int(((agg[0] or {}).get("meta") or {}).get("count") or 0)

    # ─── deletion ───────────────────────────────────────────────────────────
    def delete_document(
        self, collection: str, course_id: str, source_title: str, agent_id: int | None
    ) -> int:
        """
        Removes every chunk of one document. Returns how many were deleted.

        Always scoped by course_id as well as title: two courses on the same
        installation can legitimately use the same text, and deleting one
        course's copy must not touch the other's.
        """
        if not source_title:
            raise WeaviateError("Refusing to delete without a document title.")
        if not course_id:
            # Without it the filter would match across every course.
            raise WeaviateError("Refusing to delete without a course id.")

        operands = [
            {"path": ["course_id"], "operator": "Equal", "valueText": course_id},
            {"path": ["source_title"], "operator": "Equal", "valueText": source_title},
        ]
        if agent_id is not None:
            # agent_id is an int property — valueInt, not valueText.
            operands.append(
                {"path": ["agent_id"], "operator": "Equal", "valueInt": int(agent_id)}
            )

        payload = {
            "match": {"class": collection, "where": {"operator": "And", "operands": operands}},
            "output": "minimal",
        }
        result = self._request("DELETE", "/v1/batch/objects", json=payload)
        results = (result or {}).get("results") or {}
        failed = int(results.get("failed") or 0)
        if failed:
            raise WeaviateError(
                f"Weaviate reported {failed} failed deletion(s) for {source_title!r}."
            )
        return int(results.get("successful") or 0)

    def delete_agent_documents(self, collection: str, course_id: str, agent_id: int) -> int:
        """Everything belonging to one agent slot, within one course. Used
        when a slot is repurposed: the chunks keep the old agent_id, so the
        new agent would otherwise inherit the previous one's corpus."""
        if not course_id:
            raise WeaviateError("Refusing to delete without a course id.")
        payload = {
            "match": {
                "class": collection,
                "where": {
                    "operator": "And",
                    "operands": [
                        {"path": ["course_id"], "operator": "Equal", "valueText": course_id},
                        {"path": ["agent_id"], "operator": "Equal", "valueInt": int(agent_id)},
                    ],
                },
            },
            "output": "minimal",
        }
        result = self._request("DELETE", "/v1/batch/objects", json=payload)
        results = (result or {}).get("results") or {}
        failed = int(results.get("failed") or 0)
        if failed:
            raise WeaviateError(f"Weaviate reported {failed} failed deletion(s).")
        return int(results.get("successful") or 0)
