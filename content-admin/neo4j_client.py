"""
The knowledge graph, one course at a time.

Two things changed here at once, and they are the same change. Concepts used
to carry no course, and this page used to run whatever Cypher was pasted into
it. With one course that was merely rough; with several it means one course's
prerequisites reach another course's agents, and a maintainer who should see
one course can rewrite the graph of all of them.

The boundary cannot be enforced inside pasted Cypher. Checking a statement
before running it means parsing Cypher, and a parser that is nearly right is
the kind of half-measure that looks like a safeguard. So the interchange
format changed instead: the model returns **JSON** — concepts and
prerequisite edges — this module validates it, and the writing is done here,
by parameterised statements that stamp the course. Nothing a person pastes
becomes a query.

Everything reads and writes with `course_id` in the pattern, so a query
cannot accidentally span courses: it is not a filter that can be forgotten,
it is part of every statement in this file.

HTTP rather than `docker exec cypher-shell`, as before — this GUI has no
Docker access by design.
"""

import re

import requests

MAX_NAME = 120
MAX_CONCEPTS = 500

CONCEPT_KEYS = {"name", "chapter", "section_id", "description", "topic"}


class Neo4jError(RuntimeError):
    pass


class GraphInputError(ValueError):
    """The pasted proposal is not usable — said in terms of what to fix."""


class Neo4jClient:
    def __init__(self, base_url: str, user: str, password: str, database: str = "neo4j"):
        self.base = base_url.rstrip("/")
        self.auth = (user, password)
        self.database = database

    # ─── transport ──────────────────────────────────────────────────────────
    def _run(self, statements: list[dict]) -> list[dict]:
        url = f"{self.base}/db/{self.database}/tx/commit"
        try:
            resp = requests.post(url, json={"statements": statements},
                                 auth=self.auth, timeout=30)
        except requests.RequestException as exc:
            raise Neo4jError(f"Neo4j is not reachable: {exc}") from exc
        if resp.status_code == 401:
            raise Neo4jError("Neo4j refused the password from .env (NEO4J_PASSWORD).")
        if not resp.ok:
            raise Neo4jError(f"Neo4j answered HTTP {resp.status_code}: {resp.text[:300]}")

        payload = resp.json()
        errors = payload.get("errors") or []
        if errors:
            first = errors[0]
            raise Neo4jError(f"{first.get('code', 'error')}: {first.get('message', '')}")
        return payload.get("results", [])

    def check_connection(self) -> None:
        self._run([{"statement": "RETURN 1"}])

    # ─── reading ────────────────────────────────────────────────────────────
    def concepts(self, course_id: str, limit: int = 500) -> list[dict]:
        """Every concept of one course, with how many concepts it is a
        prerequisite for and how many it depends on. Those two numbers are
        what make an accidental duplicate visible: the copy has no edges."""
        results = self._run([{
            "statement": (
                "MATCH (c:Concept {course_id: $course}) "
                "OPTIONAL MATCH (c)-[:PREREQUISITE_FOR]->(after:Concept {course_id: $course}) "
                "OPTIONAL MATCH (before:Concept {course_id: $course})-[:PREREQUISITE_FOR]->(c) "
                "RETURN c.name AS name, c.chapter AS chapter, "
                "       c.description AS description, "
                "       count(DISTINCT after) AS leads_to, "
                "       count(DISTINCT before) AS depends_on "
                "ORDER BY coalesce(c.chapter, ''), c.name LIMIT $limit"),
            "parameters": {"course": course_id, "limit": limit},
        }])
        return _rows(results[0]) if results else []

    def edges(self, course_id: str) -> list[dict]:
        results = self._run([{
            "statement": (
                "MATCH (a:Concept {course_id: $course})-[:PREREQUISITE_FOR]->"
                "(b:Concept {course_id: $course}) "
                "RETURN a.name AS before, b.name AS after ORDER BY a.name, b.name"),
            "parameters": {"course": course_id},
        }])
        return _rows(results[0]) if results else []

    def counts(self, course_id: str) -> dict:
        results = self._run([{
            "statement": (
                "MATCH (c:Concept {course_id: $course}) "
                "OPTIONAL MATCH (:Concept {course_id: $course})-[r:PREREQUISITE_FOR]->"
                "(:Concept {course_id: $course}) "
                "RETURN count(DISTINCT c) AS concepts, count(r) AS edges"),
            "parameters": {"course": course_id},
        }])
        rows = _rows(results[0]) if results else []
        return rows[0] if rows else {"concepts": 0, "edges": 0}

    def unassigned_count(self) -> int:
        """Concepts from before the graph knew about courses.

        Shown rather than migrated silently: they belong to whichever course
        the installation had at the time, and only a person knows which.
        """
        results = self._run([{
            "statement": "MATCH (c:Concept) WHERE c.course_id IS NULL "
                         "RETURN count(c) AS n"}])
        rows = _rows(results[0]) if results else []
        return rows[0]["n"] if rows else 0

    # ─── writing ────────────────────────────────────────────────────────────
    def apply_proposal(self, course_id: str, concepts: list[dict],
                       edges: list[dict]) -> dict:
        """Write a validated proposal into one course's graph.

        MERGE on (name, course_id), so the same proposal applied twice
        changes nothing the second time — an LLM asked again produces almost
        the same list, and duplicating the graph on every re-run would be the
        normal case rather than the exception.
        """
        statements = []
        for c in concepts:
            statements.append({
                "statement": (
                    "MERGE (c:Concept {name: $name, course_id: $course}) "
                    "SET c.chapter = coalesce($chapter, c.chapter), "
                    "    c.section_id = coalesce($section_id, c.section_id), "
                    "    c.description = coalesce($description, c.description)"),
                "parameters": {
                    "name": c["name"], "course": course_id,
                    "chapter": c.get("chapter"), "section_id": c.get("section_id"),
                    "description": c.get("description"),
                },
            })
        for e in edges:
            statements.append({
                "statement": (
                    "MATCH (a:Concept {name: $before, course_id: $course}) "
                    "MATCH (b:Concept {name: $after, course_id: $course}) "
                    "MERGE (a)-[:PREREQUISITE_FOR]->(b)"),
                "parameters": {"before": e["before"], "after": e["after"],
                               "course": course_id},
            })
        if not statements:
            raise GraphInputError("The proposal contains nothing to write.")
        # One transaction: a proposal that fails half-way would leave concepts
        # without the edges that give them meaning.
        self._run(statements)
        return {"concepts": len(concepts), "edges": len(edges)}

    def delete_concept(self, course_id: str, name: str) -> int:
        results = self._run([{
            "statement": ("MATCH (c:Concept {name: $name, course_id: $course}) "
                          "DETACH DELETE c RETURN count(c) AS removed"),
            "parameters": {"name": name, "course": course_id},
        }])
        rows = _rows(results[0]) if results else []
        return rows[0]["removed"] if rows else 0

    def clear_course(self, course_id: str) -> int:
        results = self._run([{
            "statement": ("MATCH (c:Concept {course_id: $course}) "
                          "WITH count(c) AS n "
                          "MATCH (c:Concept {course_id: $course}) DETACH DELETE c "
                          "RETURN n AS removed"),
            "parameters": {"course": course_id},
        }])
        rows = _rows(results[0]) if results else []
        return rows[0]["removed"] if rows else 0

    def adopt_unassigned(self, course_id: str) -> int:
        """Claim the concepts that predate course scoping for one course."""
        results = self._run([{
            "statement": ("MATCH (c:Concept) WHERE c.course_id IS NULL "
                          "SET c.course_id = $course RETURN count(c) AS moved"),
            "parameters": {"course": course_id},
        }])
        rows = _rows(results[0]) if results else []
        return rows[0]["moved"] if rows else 0


# ─── The proposal format ─────────────────────────────────────────────────────

def parse_proposal(text: str) -> tuple[list[dict], list[dict]]:
    """Turn what the model returned into concepts and edges, or say why not.

    Every message here names the thing to fix, because the person reading it
    did not write the input — a model did, and they are deciding whether to
    trust it.
    """
    import json

    raw = (text or "").strip()
    if not raw:
        raise GraphInputError("Nothing was pasted.")
    # Models like to wrap JSON in a fenced block. Stripping that is not
    # leniency about the format, it is leniency about the wrapper.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.S)
    if fence:
        raw = fence.group(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GraphInputError(
            f"That is not valid JSON ({exc.msg}, line {exc.lineno}). Paste the "
            "model's answer unchanged, or ask it again for JSON only.") from exc

    if not isinstance(data, dict):
        raise GraphInputError("The top level must be an object with "
                              '"concepts" and "prerequisites".')

    concepts_in = data.get("concepts")
    edges_in = data.get("prerequisites", data.get("edges", []))
    if not isinstance(concepts_in, list) or not concepts_in:
        raise GraphInputError('"concepts" is missing or empty.')
    if not isinstance(edges_in, list):
        raise GraphInputError('"prerequisites" must be a list.')
    if len(concepts_in) > MAX_CONCEPTS:
        raise GraphInputError(
            f"{len(concepts_in)} concepts is more than one course's graph "
            f"should hold at once (limit {MAX_CONCEPTS}). Split it up.")

    concepts: list[dict] = []
    names: set[str] = set()
    for i, c in enumerate(concepts_in, 1):
        if isinstance(c, str):
            c = {"name": c}
        if not isinstance(c, dict):
            raise GraphInputError(f"Concept {i} is not an object.")
        unknown = set(c) - CONCEPT_KEYS
        if unknown:
            raise GraphInputError(
                f"Concept {i} has field(s) this graph does not store: "
                f"{', '.join(sorted(unknown))}.")
        name = str(c.get("name", "")).strip()
        if not name:
            raise GraphInputError(f"Concept {i} has no name.")
        if len(name) > MAX_NAME:
            raise GraphInputError(f"Concept {i}'s name is longer than {MAX_NAME} characters.")
        if name.casefold() in names:
            raise GraphInputError(f"{name!r} appears twice in the proposal.")
        names.add(name.casefold())
        concepts.append({
            "name": name,
            "chapter": _opt(c.get("chapter")),
            "section_id": _opt(c.get("section_id")),
            "description": _opt(c.get("description")),
        })

    edges: list[dict] = []
    for i, e in enumerate(edges_in, 1):
        if not isinstance(e, dict):
            raise GraphInputError(f"Prerequisite {i} is not an object.")
        before = str(e.get("before", e.get("prerequisite", ""))).strip()
        after = str(e.get("after", e.get("concept", ""))).strip()
        if not before or not after:
            raise GraphInputError(
                f'Prerequisite {i} needs "before" and "after".')
        # Both ends must be in the same proposal. An edge to a concept that
        # does not exist writes nothing and reports success, which is how a
        # graph ends up looking complete and answering nothing.
        for end in (before, after):
            if end.casefold() not in names:
                raise GraphInputError(
                    f"Prerequisite {i} refers to {end!r}, which is not among "
                    "the concepts. Every edge must connect two of them.")
        if before.casefold() == after.casefold():
            raise GraphInputError(f"Prerequisite {i} points {before!r} at itself.")
        edges.append({"before": before, "after": after})

    return concepts, edges


def _opt(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _rows(result: dict) -> list[dict]:
    columns = result.get("columns", [])
    return [dict(zip(columns, row.get("row", []))) for row in result.get("data", [])]
