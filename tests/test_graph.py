"""
The knowledge graph, and the boundary it did not have.

Concepts carried no course, and the page ran whatever Cypher was pasted into
it. With one course that was rough; with two it means the map of one course
answers for the other — the same silent cross-course leak we closed in the
vector database, the object store and the progress table, still open here.

The boundary cannot live inside pasted Cypher. Checking a statement before
running it means parsing Cypher, and a parser that is nearly right is worse
than none, because it reads as a safeguard. So the format changed: the model
answers with JSON, this code validates it, and the writing is done with
parameterised statements that carry the course.

What is tested here is therefore mostly the validator and the shape of the
statements — with the client's transport stubbed, because what matters is
which query is sent, not that Neo4j answers it. The queries themselves run
against the real Neo4j on the server.
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "content-admin"
if not APP_DIR.is_dir() and Path("/app/db.py").exists():
    APP_DIR = Path("/app")
sys.path.insert(0, str(APP_DIR))

import neo4j_client as nc  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def rejects(text, fragment=""):
    try:
        nc.parse_proposal(text)
    except nc.GraphInputError as exc:
        return fragment.lower() in str(exc).lower()
    return False


GOOD = json.dumps({
    "concepts": [
        {"name": "Arbeitsgedächtnis", "chapter": "1", "description": "kurz"},
        {"name": "Cognitive Load", "chapter": "2"},
    ],
    "prerequisites": [{"before": "Arbeitsgedächtnis", "after": "Cognitive Load"}],
})

# ─── 1. The validator ────────────────────────────────────────────────────────
concepts, edges = nc.parse_proposal(GOOD)
check("a well-formed proposal parses", len(concepts) == 2 and len(edges) == 1,
      (concepts, edges))
check("optional fields survive", concepts[0]["chapter"] == "1", concepts[0])
check("a missing optional field becomes None", concepts[1]["description"] is None,
      concepts[1])

# Models wrap JSON in a fenced block often enough that refusing it would make
# the page feel broken for a reason that has nothing to do with the content.
fenced, _ = nc.parse_proposal("```json\n" + GOOD + "\n```")
check("a fenced code block is accepted", len(fenced) == 2, fenced)

check("nothing pasted is refused", rejects("", "nothing"))
check("prose instead of JSON is refused", rejects("Hier sind die Konzepte:", "valid JSON"))
check("a bare list is refused", rejects('["a", "b"]', "top level"))
check("no concepts is refused", rejects('{"concepts": []}', "missing or empty"))
check("a concept without a name is refused",
      rejects('{"concepts": [{"chapter": "1"}]}', "no name"))
check("a duplicate name is refused",
      rejects('{"concepts": [{"name": "A"}, {"name": "A"}]}', "twice"))

# An edge to a concept that is not in the proposal writes nothing and reports
# success — the graph then looks complete and answers nothing.
check("an edge into thin air is refused",
      rejects(json.dumps({"concepts": [{"name": "A"}],
                          "prerequisites": [{"before": "A", "after": "B"}]}),
              "not among the concepts"))
check("a self-referencing edge is refused",
      rejects(json.dumps({"concepts": [{"name": "A"}],
                          "prerequisites": [{"before": "A", "after": "A"}]}),
              "itself"))
# A field the graph does not store is a sign the model answered a different
# question — accepting it silently would drop the content.
check("an unknown field is refused",
      rejects('{"concepts": [{"name": "A", "difficulty": "hard"}]}',
              "does not store"))
check("an oversized proposal is refused",
      rejects(json.dumps({"concepts": [{"name": f"C{i}"} for i in range(nc.MAX_CONCEPTS + 1)]}),
              "split it up"))

# A field that is accepted and then not written is worse than one that is
# refused: the model's output disappears and nobody is told. "topic" was in
# the accepted set and never stored.
check("a field that is not stored is refused rather than dropped",
      rejects('{"concepts": [{"name": "A", "topic": "Kapitel 1"}]}', "does not store"))

# Circles. "A before B before C before A" is a contradiction, and nothing
# downstream would object — the agent would fetch prerequisites for ever or
# teach in an arbitrary order, and the map would look plausible.
check("a prerequisite circle is refused",
      rejects(json.dumps({
          "concepts": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
          "prerequisites": [{"before": "A", "after": "B"},
                            {"before": "B", "after": "C"},
                            {"before": "C", "after": "A"}]}), "circle"))
long_chain = json.dumps({
    "concepts": [{"name": n} for n in "ABCDE"],
    "prerequisites": [{"before": a, "after": b}
                      for a, b in zip("ABCD", "BCDE")]})
check("a long chain is not mistaken for a circle",
      len(nc.parse_proposal(long_chain)[1]) == 4, "")
diamond = json.dumps({
    "concepts": [{"name": n} for n in "ABCD"],
    "prerequisites": [{"before": "A", "after": "B"}, {"before": "A", "after": "C"},
                      {"before": "B", "after": "D"}, {"before": "C", "after": "D"}]})
check("two paths to the same concept are allowed",
      len(nc.parse_proposal(diamond)[1]) == 4, "a diamond is not a circle")

# ─── 2. Every statement carries the course ───────────────────────────────────
class Recorder(nc.Neo4jClient):
    def __init__(self):
        super().__init__("http://neo4j:7474", "neo4j", "pw")
        self.sent = []

    def _run(self, statements):
        self.sent.extend(statements)
        # Answer with whatever column the caller asked for, so the stub does
        # not decide which methods can be exercised.
        wanted = re.findall(r"AS (\w+)", statements[-1]["statement"])
        cols = wanted or ["n"]
        return [{"columns": cols, "data": [{"row": [0] * len(cols)}]}]


r = Recorder()
r.apply_proposal("mathe-1", concepts, edges)
check("something was written", r.sent, "")
for st in r.sent:
    check("every write names the course",
          st["parameters"].get("course") == "mathe-1", st["parameters"])
    # Two shapes, both carrying the course into the match itself. A concept
    # is merged on its key, which begins with the course; an edge matches
    # both ends inside the course. What must never appear is a statement
    # that finds a node by name alone and then sets the course afterwards —
    # that one would attach to another course's node.
    if st["statement"].startswith("MERGE (c:Concept"):
        check("a concept is keyed by its course",
              st["parameters"]["key"].startswith("mathe-1::"), st["parameters"])
    else:
        check("an edge matches both ends inside the course",
              st["statement"].count("course_id: $course") == 2, st["statement"])
# MERGE everywhere, CREATE nowhere. A model asked twice returns almost the
# same list, so re-applying a proposal is the normal case — with CREATE it
# would double the graph each time, and the duplicates are only visible as
# concepts with no edges.
check("nothing is created blindly",
      not any("CREATE (" in st["statement"] for st in r.sent),
      [st["statement"][:60] for st in r.sent if "CREATE (" in st["statement"]])
check("concepts are merged", any(st["statement"].startswith("MERGE (c:Concept")
                                 for st in r.sent), "")

# The uniqueness the database can actually enforce. Neo4j Community has no
# composite uniqueness — a node key is Enterprise — and the constraint that
# shipped was on c.name alone, which is global: two courses could not both
# have a concept called "Cognitive Load", and the second one failed on MERGE.
# The pair is folded into c.key, and that is what is merged on.
check("a concept is keyed by course and name",
      nc.concept_key("mathe-1", "Cognitive Load") == "mathe-1::Cognitive Load",
      nc.concept_key("mathe-1", "Cognitive Load"))
check("the same name in two courses gives two keys",
      nc.concept_key("mathe-1", "X") != nc.concept_key("chemie-1", "X"), "")
check("the write merges on that key",
      any("MERGE (c:Concept {key: $key})" in st["statement"] for st in r.sent),
      [st["statement"][:50] for st in r.sent])

schema = (REPO / "neo4j" / "schema.cypher").read_text()
check("the global uniqueness on the name is dropped",
      "DROP CONSTRAINT constraint_concept_name IF EXISTS" in schema,
      "two courses cannot both have a concept of the same name")
check("…and replaced by one on the key",
      "REQUIRE c.key IS UNIQUE" in schema, "")
check("adopting old concepts gives them a key",
      "c.key = $course + '::' + c.name" in
      (APP_DIR / "neo4j_client.py").read_text(),
      "an adopted concept without a key is invisible to the next MERGE, "
      "which then creates a second node beside it")
# Matched on both ends and merged in the middle. Written as intent rather
# than as one literal string: the relationship acquired a variable when it
# started carrying provenance, and a check on the exact text failed for a
# change that kept every property it was guarding.
edge_stmts = [st["statement"] for st in r.sent if "PREREQUISITE_FOR" in st["statement"]]
check("an edge statement is sent", bool(edge_stmts), r.sent)
check("edges are merged onto existing concepts",
      any(re.search(r"MERGE \(a\)-\[\w*:PREREQUISITE_FOR\]->\(b\)", st)
          for st in edge_stmts), edge_stmts)
check("and both ends are matched, never created",
      all(st.count("MATCH") >= 2 and "MERGE (a:Concept" not in st
          for st in edge_stmts),
      "an edge that creates its endpoints invents concepts nobody proposed")

# Counting concepts and edges in one statement multiplies them: the edge
# match stands in no relation to the concept matched beside it, so every
# concept is paired with every edge. A live course with 43 concepts and 5
# prerequisites announced 215 — plausible enough that it took an operator
# asking about it.
r = Recorder()
r.counts("mathe-1")
count_stmts = [st["statement"] for st in r.sent]
check("concepts and edges are counted separately", len(count_stmts) == 2,
      count_stmts)
check("and no statement counts across both",
      not any("count(c)" in st and "count(r)" in st for st in count_stmts),
      "one statement over both is a cartesian product")
check("no count query pairs a concept match with an unrelated edge match",
      not any(st.count("MATCH") > 1 and "PREREQUISITE_FOR" in st
              and "(c:Concept" in st for st in count_stmts),
      count_stmts)

for method, args in (("concepts", ("mathe-1",)), ("edges", ("mathe-1",)),
                     ("counts", ("mathe-1",)),
                     ("delete_concept", ("mathe-1", "A")),
                     ("clear_course", ("mathe-1",))):
    r = Recorder()
    getattr(r, method)(*args)
    for st in r.sent:
        check(f"{method} scopes its query to the course",
              "course_id: $course" in st["statement"], st["statement"])

        check(f"{method} passes the course as a parameter",
              st["parameters"].get("course") == "mathe-1", st.get("parameters"))

# The one query that deliberately looks across courses is the one that finds
# what predates them — and it only reads.
r = Recorder()
r.unassigned_count()
check("the unassigned count is a read", all("DELETE" not in s["statement"]
                                            for s in r.sent), r.sent)

# ─── 3. No pasted Cypher reaches the database ────────────────────────────────
app_src = (APP_DIR / "app.py").read_text()
check("the page no longer runs pasted Cypher",
      "run_script" not in app_src, "arbitrary Cypher is still executed")
check("…and the client no longer offers it",
      not hasattr(nc.Neo4jClient, "run_script"),
      "run_script is still there for the next caller to find")

# ─── 4. The agents ask within their course ───────────────────────────────────
for path in sorted((REPO / "flowise" / "agents").glob("*.json")):
    data = json.loads(path.read_text())
    for node in data.get("nodes", []):
        code = (node.get("data", {}).get("inputs") or {}).get(
            "customFunctionJavascriptFunction") or ""
        if "PREREQUISITE_FOR" not in code:
            continue
        label = f"{path.name}/{node.get('data', {}).get('label', '?')}"
        check(f"{label} matches only its own course",
              "course_id: $courseId" in code,
              "the query matches a concept of that name in any course")
        check(f"{label} passes the course as a parameter",
              "courseId" in code and "parameters: { topic, courseId }" in code,
              "interpolating it into the statement would be an injection")
        check(f"{label} takes the course from the import substitution",
              "{{COURSE_ID}}" in code, "")

# ─── Proposing a map from the course's own material ─────────────────────────
# The automated route must not become a second, laxer door into the graph.
# Everything below is about that: it asks the same question the page hands a
# human, it goes through the same parser, and it writes nothing.
app_src = (APP_DIR / "app.py").read_text()
llm_src = (APP_DIR / "llm_client.py").read_text()
wv_src = (APP_DIR / "weaviate_client.py").read_text()
tpl = (REPO / "content-admin" / "templates" / "graph_guidance.html").read_text()

check("there is a propose action", 'action == "propose"' in app_src, "")
# The same prompt the page offers for copying — not a second one written for
# the automated path, which would quietly make it a different feature.
check("it sends the page's own prompt",
      '_t("graph_prompt_text"' in app_src.split('action == "propose"')[1][:1400],
      "a separate prompt here means the two routes stop agreeing")
check("the material comes from the course's documents",
      ".outline(" in app_src and "outline_as_text" in app_src, "")

# The safety property. A proposal reaches the graph only through the review
# box, and only when that box is submitted.
propose_block = app_src.split('action == "propose"')[1].split('else:')[0]
check("the proposal is parsed before it is shown",
      "parse_proposal(" in propose_block,
      "an unvalidated proposal in the review box is one a cycle could survive")
check("and nothing is applied by proposing",
      "apply_proposal" not in propose_block,
      "proposing must never write — the operator has not read it yet")
check("what is shown is what would be applied",
      "json.dumps(" in propose_block,
      "reviewing the model's raw text while applying something else is a trap")

# No material, no proposal: a map invented from an empty course would be the
# model's general knowledge of the subject, presented as this course's.
check("an empty course is refused rather than guessed at",
      "There is no material to read yet" in llm_src, "")

# The outline is bounded, and says when it did not fit. Run for real against a
# stubbed Weaviate: a budget that is only *named* in the source is one a later
# edit can stop honouring without any test noticing.
import weaviate_client as wc  # noqa: E402

rows = [{"source_title": "Skript", "chapter_title": f"Kapitel {i}",
         "section": f"{i}.1", "section_id": f"{i}.1", "chunk_index": i,
         "text": "Lorem ipsum " * 60} for i in range(40)]
wc.WeaviateClient._graphql = lambda self, q: {"data": {"Get": {"C": rows}}}
client = wc.WeaviateClient.__new__(wc.WeaviateClient)

whole = client.outline("C", "course-1", char_budget=10 ** 7)
check("all sections are listed when they fit",
      whole["sections"] == 40 and not whole["truncated"], whole["sections"])

cut = client.outline("C", "course-1", char_budget=2000)
check("a budget that is exceeded stops the outline",
      cut["characters"] <= 2000, cut["characters"])
check("and it is fewer sections than the whole course",
      0 < cut["sections"] < 40, cut["sections"])
check("and the caller is told it was cut",
      cut["truncated"] is True, cut)
check("a truncated outline counts only the sections it kept",
      cut["sections"] == sum(len(d["sections"]) for d in cut["documents"]),
      (cut["sections"], [len(d["sections"]) for d in cut["documents"]]))
check("the page reports truncation to the operator",
      "graph_proposed_truncated" in app_src,
      "a map built from half a course is a different map")

# One entry per section, not per chunk: the excerpt list is for orientation,
# and repeating a section forty times would spend the budget on nothing.
dupes = [dict(rows[0], chunk_index=i) for i in range(20)]
wc.WeaviateClient._graphql = lambda self, q: {"data": {"Get": {"C": dupes}}}
one = client.outline("C", "course-1")
check("repeated chunks of one section collapse to one entry",
      sum(len(d["sections"]) for d in one["documents"]) == 1,
      [len(d["sections"]) for d in one["documents"]])
check("and the reported count agrees with what is listed",
      one["sections"] == 1, one["sections"])

check("the page offers the button", 'value="propose"' in tpl, "")
check("and keeps the manual route", 'graph-prompt' in tpl,
      "the automated one needs a key and a reachable provider; the other does not")
# The review box is the only writer, and it is a separate submission.
check("applying is still its own form",
      'value="apply"' in tpl, "")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All knowledge-graph checks passed: a proposal is JSON that is validated "
    "before anything is written — prose, a bare list, a nameless or duplicated "
    "concept, an unknown field, an edge to a concept that is not there and a "
    "self-referencing edge are each refused with the reason; every read and "
    "every write names the course inside the pattern rather than as a filter "
    "that can be dropped; pasted Cypher no longer reaches the database and "
    "run_script is gone rather than left for the next caller; and every agent "
    "that reads the graph matches concepts of its own course only."
)
