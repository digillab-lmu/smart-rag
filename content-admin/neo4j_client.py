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

# What a concept may carry. "topic" is deliberately absent: it was accepted
# here and then never written, so a model that used it would have its output
# silently dropped — accepting a field is a promise to store it.
CONCEPT_KEYS = {"name", "chapter", "section_id", "description", "sources"}
EDGE_KEYS = {"before", "after", "prerequisite", "concept", "sources"}


def _source_list(value) -> list[str]:
    """Normalise whatever a proposal offered as provenance into a list.

    A single string, a list, or nothing at all — the workflow, a pasted
    answer and an old caller all reach this. Empty strings are dropped: a
    source of "" would make a concept look supported by a document that does
    not exist, which is worse than one with no provenance at all, because the
    first survives a cleanup and the second is visibly incomplete.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    seen: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def concept_key(course_id: str, name: str) -> str:
    """The one property the database can enforce uniqueness on.

    Neo4j Community has no composite uniqueness — a node key is Enterprise —
    so the pair (course, name) is folded into one value. The separator is two
    colons because a course id cannot contain one (see the courses table's
    CHECK), which makes the encoding unambiguous.
    """
    return f"{course_id}::{name}"


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
                       edges: list[dict], build_id: str = "") -> dict:
        """Write a validated proposal into one course's graph.

        MERGE on (name, course_id), so the same proposal applied twice
        changes nothing the second time — an LLM asked again produces almost
        the same list, and duplicating the graph on every re-run would be the
        normal case rather than the exception.

        **Provenance is written here or never.** Each concept and each edge
        records the documents it came from and the builds that asserted it.
        A course is a container several people put agents into; when one of
        them turns out to hold unrelated material, "take that back out" is
        answerable only if every node remembers what supports it. A concept
        two agents' documents support must survive the departure of one of
        them, so `sources` is a list and stays one — and appending to it is
        how a second build strengthens a concept rather than replacing it.
        """
        statements = []
        for c in concepts:
            statements.append({
                "statement": (
                    # MERGE on the synthetic key, not on (name, course_id):
                    # the uniqueness constraint can only cover one property in
                    # Neo4j Community, so the pair lives in c.key and that is
                    # what the database enforces. Merging on anything else
                    # would let a concurrent write create a second node the
                    # constraint then rejects.
                    "MERGE (c:Concept {key: $key}) "
                    "SET c.name = $name, c.course_id = $course, "
                    "    c.chapter = coalesce($chapter, c.chapter), "
                    "    c.section_id = coalesce($section_id, c.section_id), "
                    # A description a person wrote is not overwritten by a
                    # model's. coalesce only guards the empty case, so a
                    # rebuild used to silently replace curated text with
                    # whatever the model said this time.
                    "    c.description = CASE WHEN c.curated_at IS NULL "
                    "        THEN coalesce($description, c.description) "
                    "        ELSE c.description END, "
                    # Unique append in plain Cypher: drop what is about to be
                    # added, then add it. APOC is not installed here and a
                    # graph feature that needs a plugin is a graph feature
                    # that breaks on the next image bump.
                    "    c.sources = [x IN coalesce(c.sources, []) "
                    "                 WHERE NOT x IN $sources] + $sources, "
                    "    c.builds  = [x IN coalesce(c.builds, []) "
                    "                 WHERE NOT x IN $builds] + $builds"),
                "parameters": {
                    "key": concept_key(course_id, c["name"]),
                    "name": c["name"], "course": course_id,
                    "chapter": c.get("chapter"), "section_id": c.get("section_id"),
                    "description": c.get("description"),
                    "sources": _source_list(c.get("sources")),
                    "builds": [build_id] if build_id else [],
                },
            })
        for e in edges:
            statements.append({
                "statement": (
                    "MATCH (a:Concept {name: $before, course_id: $course}) "
                    "MATCH (b:Concept {name: $after, course_id: $course}) "
                    "MERGE (a)-[r:PREREQUISITE_FOR]->(b) "
                    "SET r.sources = [x IN coalesce(r.sources, []) "
                    "                 WHERE NOT x IN $sources] + $sources, "
                    "    r.builds  = [x IN coalesce(r.builds, []) "
                    "                 WHERE NOT x IN $builds] + $builds"),
                "parameters": {"before": e["before"], "after": e["after"],
                               "course": course_id,
                               "sources": _source_list(e.get("sources")),
                               "builds": [build_id] if build_id else []},
            })
        if not statements:
            raise GraphInputError("The proposal contains nothing to write.")
        # One transaction: a proposal that fails half-way would leave concepts
        # without the edges that give them meaning.
        self._run(statements)
        return {"concepts": len(concepts), "edges": len(edges)}

    def by_source(self, course_id: str) -> dict:
        """How much of the map each document is holding up.

        One query rather than a preview per document: the documents page shows
        this for every row, and forty round trips to render a list is how a
        page becomes something people stop opening.

        `only` is the number that matters when somebody is about to delete.
        "This document supports 12 concepts, 5 of which nothing else
        supports" is a different decision from "12 concepts, all of them also
        in three other works", and the difference is invisible without it.
        """
        results = self._run([{
            "statement": (
                "MATCH (c:Concept {course_id: $course}) "
                "UNWIND coalesce(c.sources, []) AS src "
                "RETURN src AS source, count(c) AS concepts, "
                "       count(CASE WHEN size(c.sources) = 1 THEN 1 END) AS only"),
            "parameters": {"course": course_id},
        }])
        return {r["source"]: {"concepts": r["concepts"], "only": r["only"]}
                for r in (_rows(results[0]) if results else [])}

    def stale_sources(self, course_id: str, present: list[str]) -> dict:
        """Citations naming material this course no longer holds.

        A document can leave the course by paths that never touch this file —
        a bulk clean-up, a course-wide operation, a future feature nobody has
        written yet. Rather than trusting each of them to remember the graph,
        the graph page asks this question every time it is opened, so a
        dangling citation is something the operator is told about rather than
        something they find out when a proposal cites a work that is gone.
        """
        have = {str(x).strip() for x in present if str(x).strip()}
        counts = self.by_source(course_id)
        stale = {src: n for src, n in counts.items() if src not in have}
        return {
            "sources": sorted(stale),
            "concepts": sum(n["concepts"] for n in stale.values()),
            "orphaned": sum(n["only"] for n in stale.values()),
        }

    def contribution_of(self, course_id: str, documents: list[str]) -> dict:
        """What would go, and what would only shrink, if these documents left.

        Asked before anything is deleted, and shown to the operator as
        numbers. Unticking an agent is not a small decision when its material
        has been in the map for a term, and "42 concepts, 17 of them supported
        by nothing else" is the difference between a decision and a click.
        """
        docs = _source_list(documents)
        if not docs:
            return {"concepts_removed": 0, "concepts_kept": 0,
                    "edges_removed": 0, "edges_kept": 0, "unprovenanced": 0}
        results = self._run([{
            "statement": (
                "MATCH (c:Concept {course_id: $course}) "
                "WHERE any(x IN coalesce(c.sources, []) WHERE x IN $docs) "
                "RETURN "
                "  count(CASE WHEN size([x IN c.sources WHERE NOT x IN $docs]) = 0 "
                "             THEN 1 END) AS concepts_removed, "
                "  count(CASE WHEN size([x IN c.sources WHERE NOT x IN $docs]) > 0 "
                "             THEN 1 END) AS concepts_kept"),
            "parameters": {"course": course_id, "docs": docs},
        }, {
            "statement": (
                "MATCH (:Concept {course_id: $course})-[r:PREREQUISITE_FOR]->"
                "(:Concept {course_id: $course}) "
                "WHERE any(x IN coalesce(r.sources, []) WHERE x IN $docs) "
                "RETURN "
                "  count(CASE WHEN size([x IN r.sources WHERE NOT x IN $docs]) = 0 "
                "             THEN 1 END) AS edges_removed, "
                "  count(CASE WHEN size([x IN r.sources WHERE NOT x IN $docs]) > 0 "
                "             THEN 1 END) AS edges_kept"),
            "parameters": {"course": course_id, "docs": docs},
        }, {
            # Concepts written before provenance existed. They cannot be
            # attributed to anyone, so no removal will ever touch them — and
            # saying so is better than letting somebody conclude their
            # colleague's material is gone when part of it is not.
            "statement": (
                "MATCH (c:Concept {course_id: $course}) "
                "WHERE c.sources IS NULL OR size(c.sources) = 0 "
                "RETURN count(c) AS unprovenanced"),
            "parameters": {"course": course_id},
        }])
        out = {}
        for result in results:
            rows = _rows(result)
            if rows:
                out.update(rows[0])
        return out

    def remove_documents(self, course_id: str, documents: list[str]) -> dict:
        """Take these documents' contribution back out of one course's graph.

        No model, no queue, no workflow: everything this needs is already in
        the database. It runs in one transaction for the same reason
        apply_proposal does — a graph whose concepts are gone but whose edges
        remain is worse than either clean state, and a removal spread over
        several steps of a workflow engine cannot promise that.

        A concept another document still supports is kept and loses only that
        document from its sources. That is the whole reason provenance is a
        list, and the case that makes an agent's departure survivable.
        """
        docs = _source_list(documents)
        if not docs:
            raise GraphInputError("No documents named, so there is nothing to remove.")
        before = self.contribution_of(course_id, docs)
        self._run([{
            # Shrink first: after the delete below, the surviving concepts are
            # the ones this needs to find, and doing it the other way round
            # would leave the deleted ones' names in the way.
            "statement": (
                "MATCH (c:Concept {course_id: $course}) "
                "WHERE any(x IN coalesce(c.sources, []) WHERE x IN $docs) "
                "  AND size([x IN c.sources WHERE NOT x IN $docs]) > 0 "
                "SET c.sources = [x IN c.sources WHERE NOT x IN $docs]"),
            "parameters": {"course": course_id, "docs": docs},
        }, {
            "statement": (
                "MATCH (:Concept {course_id: $course})-[r:PREREQUISITE_FOR]->"
                "(:Concept {course_id: $course}) "
                "WHERE any(x IN coalesce(r.sources, []) WHERE x IN $docs) "
                "  AND size([x IN r.sources WHERE NOT x IN $docs]) > 0 "
                "SET r.sources = [x IN r.sources WHERE NOT x IN $docs]"),
            "parameters": {"course": course_id, "docs": docs},
        }, {
            "statement": (
                "MATCH (:Concept {course_id: $course})-[r:PREREQUISITE_FOR]->"
                "(:Concept {course_id: $course}) "
                "WHERE size(coalesce(r.sources, [])) > 0 "
                "  AND size([x IN r.sources WHERE NOT x IN $docs]) = 0 "
                "DELETE r"),
            "parameters": {"course": course_id, "docs": docs},
        }, {
            "statement": (
                "MATCH (c:Concept {course_id: $course}) "
                "WHERE size(coalesce(c.sources, [])) > 0 "
                "  AND size([x IN c.sources WHERE NOT x IN $docs]) = 0 "
                "DETACH DELETE c"),
            "parameters": {"course": course_id, "docs": docs},
        }])
        return before

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
                          # The key comes with the course: a concept adopted
                          # without one is invisible to every later MERGE,
                          # which would then create a second node beside it.
                          "SET c.course_id = $course, "
                          "    c.key = $course + '::' + c.name "
                          "RETURN count(c) AS moved"),
            "parameters": {"course": course_id},
        }])
        rows = _rows(results[0]) if results else []
        return rows[0]["moved"] if rows else 0


# ─── The proposal format ─────────────────────────────────────────────────────

def _check_sources(sources: list[str], known: list[str] | None,
                   what: str) -> list[str]:
    """Refuse provenance naming material this course does not have."""
    if known is None:
        return sources
    allowed = {str(k).strip() for k in known}
    strangers = [s for s in sources if s not in allowed]
    if strangers:
        raise GraphInputError(
            f"{what} cites {', '.join(repr(s) for s in strangers[:3])}, which "
            "is not among this course's documents. Every citation has to name "
            "material the course actually holds.")
    return sources


def parse_proposal(text: str, known_sources: list[str] | None = None
                   ) -> tuple[list[dict], list[dict]]:
    """Turn what the model returned into concepts and edges, or say why not.

    Every message here names the thing to fix, because the person reading it
    did not write the input — a model did, and they are deciding whether to
    trust it.

    `known_sources`, when given, is the documents the corpus actually holds.
    A citation naming anything else is refused. That is not a formality: a
    course can hold two unrelated bodies of material, and the edges a model
    invents between them are the ones no document supports. Requiring the
    citation to be real does not make an edge correct — it does remove the
    most damaging class of invention, and it gives the reviewer something
    they can check instead of a claim they can only believe.
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
            "sources": _check_sources(_source_list(c.get("sources")),
                                      known_sources, f"Concept {i}"),
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
        unknown = set(e) - EDGE_KEYS
        if unknown:
            raise GraphInputError(
                f"Prerequisite {i} has field(s) this graph does not store: "
                f"{', '.join(sorted(unknown))}.")
        edges.append({"before": before, "after": after,
                      "sources": _check_sources(_source_list(e.get("sources")),
                                                known_sources, f"Prerequisite {i}")})

    _reject_cycles(concepts, edges)
    return concepts, edges


def _reject_cycles(concepts: list[dict], edges: list[dict]) -> None:
    """A prerequisite chain that comes back to where it started.

    "A before B before C before A" says every one of the three must be
    understood first, which is not a statement about a course, it is a
    contradiction. Nothing downstream would complain: the agent would fetch
    prerequisites for ever or teach in an arbitrary order, and the map would
    look plausible. Self-loops are the one-element case and are caught
    earlier with a clearer message.
    """
    after: dict[str, list[str]] = {}
    for e in edges:
        after.setdefault(e["before"].casefold(), []).append(e["after"].casefold())

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str):
        colour[node] = GREY
        path.append(node)
        for nxt in after.get(node, []):
            state = colour.get(nxt, WHITE)
            if state == GREY:
                loop = path[path.index(nxt):] + [nxt]
                raise GraphInputError(
                    "These prerequisites form a circle, so none of them could "
                    "ever be learned first: " + " → ".join(loop))
            if state == WHITE:
                visit(nxt)
        path.pop()
        colour[node] = BLACK

    for c in concepts:
        if colour.get(c["name"].casefold(), WHITE) == WHITE:
            visit(c["name"].casefold())


def _opt(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _rows(result: dict) -> list[dict]:
    columns = result.get("columns", [])
    return [dict(zip(columns, row.get("row", []))) for row in result.get("data", [])]
