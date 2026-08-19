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

    def _request(self, method: str, path: str, allow_404: bool = False, **kwargs):
        url = f"{self.base}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise WeaviateError(f"{method} {url} failed: {exc}") from exc

        # "Not there" is an answer, not a failure, for the caller asking
        # whether a collection exists — and only for that caller, which has
        # to say so. Everywhere else a 404 stays an error.
        if resp.status_code == 404 and allow_404:
            return None
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

    # ─── collections ────────────────────────────────────────────────────────
    # One chunk collection per course (ARCHITECTURE 6a). The alternative was
    # one shared collection filtered by course_id, and the failure modes are
    # not symmetric: a missing filter answers from every course, plausibly,
    # and nobody notices until a student sees another course's material.
    # A wrong collection name finds nothing, and the first test shows it.
    def collection_exists(self, name: str) -> bool:
        return self._request("GET", f"/v1/schema/{name}", allow_404=True) is not None

    def create_collection(self, name: str, template_path: str) -> bool:
        """Create a course's chunk collection from the shipped template.

        Returns True when it was created, False when it already existed —
        the caller re-runs provisioning after a partial failure and needs to
        know which half it just did.

        The template is `weaviate/schema.json`'s __COLLECTION_NAME__ class,
        read at call time rather than baked in, so a change to the properties
        reaches new courses without rebuilding this image.
        """
        import json as _json
        from pathlib import Path as _Path

        if self.collection_exists(name):
            return False

        raw = _Path(template_path).read_text()
        schema = _json.loads(raw)
        template = next((c for c in schema.get("classes", [])
                         if c.get("class") == "__COLLECTION_NAME__"), None)
        if template is None:
            raise WeaviateError(
                f"{template_path} has no __COLLECTION_NAME__ class, so there "
                "is no template to create a course collection from."
            )
        body = _json.loads(_json.dumps(template).replace("__COLLECTION_NAME__", name))
        body["class"] = name
        self._request("POST", "/v1/schema", json=body)
        return True

    def delete_collection(self, name: str) -> None:
        """Used when provisioning fails after the collection was created, so a
        retry does not trip over its own leftovers."""
        self._request("DELETE", f"/v1/schema/{name}")

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

    # The three classes every course shares. A course's chunks live in a
    # collection of their own and go with it; these do not, so they are the
    # ones a deletion has to filter rather than drop.
    SHARED_LEARNER_CLASSES = ("ChatHistory", "UserMemory", "TestResults")

    def count_by_course(self, class_name: str, course_id: str) -> int:
        """How many objects of a shared class belong to one course.

        Returns 0 for a class that does not exist: TestResults is only created
        once the knowledge-test agent has been used, and an installation that
        never used it should read as "none", not as an error in the middle of
        an inventory.
        """
        if not course_id:
            raise WeaviateError("Refusing to count without a course id.")
        query = f"""
        {{
          Aggregate {{
            {class_name}(
              where: {{ path: ["course_id"], operator: Equal, valueText: {json.dumps(course_id)} }}
            ) {{ meta {{ count }} }}
          }}
        }}
        """
        try:
            data = self._graphql(query)
        except WeaviateError:
            if not self.collection_exists(class_name):
                return 0
            raise
        agg = (((data or {}).get("data") or {}).get("Aggregate") or {}).get(class_name) or []
        if not agg:
            return 0
        return int(((agg[0] or {}).get("meta") or {}).get("count") or 0)

    def _learner_where(self, user_id: str, course_id: str | None) -> dict:
        """The filter for one learner, optionally inside one course.

        `user_id` is the learner's pseudonymous id — the part of Flowise's
        session id before the first "|", which is how every agent derives it
        (`$flow.sessionId?.split('|')[0]`) and how chathistory-sync stores it.

        A course narrows an erasure to one course's records, which is what a
        retention period expiring does. Without one it is everything the
        learner has anywhere, which is what an erasure request is.
        """
        if not user_id:
            raise WeaviateError("Refusing to match without a learner id.")
        learner = {"path": ["user_id"], "operator": "Equal", "valueText": user_id}
        if not course_id:
            return learner
        return {"operator": "And", "operands": [
            learner,
            {"path": ["course_id"], "operator": "Equal", "valueText": course_id},
        ]}

    @staticmethod
    def _graphql_equal(path: str, value: str) -> str:
        """One `path == value` clause, as GraphQL rather than JSON.

        A GraphQL where-clause looks like JSON and is not: the keys are bare
        identifiers and the operator is an enum literal, not a string. The
        first version of this built the JSON and rewrote it with two regular
        expressions, which worked and was a trap — the escaping of a value
        then decides whether a key inside it is mistaken for a key of the
        clause. Only the value is JSON here, which is exactly what json.dumps
        is for.
        """
        return f'{{ path: ["{path}"], operator: Equal, valueText: {json.dumps(value)} }}'

    def _learner_graphql_where(self, user_id: str, course_id: str | None) -> str:
        if not user_id:
            raise WeaviateError("Refusing to match without a learner id.")
        learner = self._graphql_equal("user_id", user_id)
        if not course_id:
            return learner
        return ("{ operator: And, operands: ["
                + learner + ", " + self._graphql_equal("course_id", course_id) + "] }")

    def count_by_learner(self, class_name: str, user_id: str,
                         course_id: str | None = None) -> int:
        """How many objects of a shared class belong to one learner.

        Zero for a class that does not exist, as count_by_course does: an
        installation that never used the knowledge-test agent has no
        TestResults, and that is not an error to report to somebody asking
        what is held about a person.
        """
        where = self._learner_graphql_where(user_id, course_id)
        query = f"""
        {{
          Aggregate {{
            {class_name}(where: {where}) {{ meta {{ count }} }}
          }}
        }}
        """
        try:
            data = self._graphql(query)
        except WeaviateError:
            if not self.collection_exists(class_name):
                return 0
            raise
        agg = (((data or {}).get("data") or {}).get("Aggregate") or {}).get(class_name) or []
        if not agg:
            return 0
        return int(((agg[0] or {}).get("meta") or {}).get("count") or 0)

    # ─── deletion ───────────────────────────────────────────────────────────
    def delete_by_learner(self, class_name: str, user_id: str,
                          course_id: str | None = None) -> int:
        """Every object of a shared class belonging to one learner.

        The learner id is required and checked. An empty one would build a
        filter matching every learner, and a batch delete does not ask twice
        — the same reason delete_by_course refuses an empty course.
        """
        if not self.collection_exists(class_name):
            return 0
        payload = {
            "match": {"class": class_name,
                      "where": self._learner_where(user_id, course_id)},
            "output": "minimal",
        }
        result = self._request("DELETE", "/v1/batch/objects", json=payload)
        results = (result or {}).get("results") or {}
        failed = int(results.get("failed") or 0)
        if failed:
            raise WeaviateError(
                f"Weaviate reported {failed} failed deletion(s) in {class_name}."
            )
        return int(results.get("successful") or 0)

    def delete_by_course(self, class_name: str, course_id: str) -> int:
        """Every object of a shared class belonging to one course.

        The course id is required and checked, not defaulted: an empty one
        here would produce a filter that matches every course, and a batch
        delete does not ask twice.
        """
        if not course_id:
            raise WeaviateError("Refusing to delete without a course id.")
        if not self.collection_exists(class_name):
            return 0
        payload = {
            "match": {
                "class": class_name,
                "where": {"path": ["course_id"], "operator": "Equal",
                          "valueText": course_id},
            },
            "output": "minimal",
        }
        result = self._request("DELETE", "/v1/batch/objects", json=payload)
        results = (result or {}).get("results") or {}
        failed = int(results.get("failed") or 0)
        if failed:
            raise WeaviateError(
                f"Weaviate reported {failed} failed deletion(s) in {class_name}."
            )
        return int(results.get("successful") or 0)

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
