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
    check("…in the pattern, not as an afterthought",
          "course_id: $course" in st["statement"], st["statement"])
# MERGE everywhere, CREATE nowhere. A model asked twice returns almost the
# same list, so re-applying a proposal is the normal case — with CREATE it
# would double the graph each time, and the duplicates are only visible as
# concepts with no edges.
check("nothing is created blindly",
      not any("CREATE (" in st["statement"] for st in r.sent),
      [st["statement"][:60] for st in r.sent if "CREATE (" in st["statement"]])
check("concepts are merged", any(st["statement"].startswith("MERGE (c:Concept")
                                 for st in r.sent), "")
check("edges are merged onto existing concepts",
      any("MERGE (a)-[:PREREQUISITE_FOR]->(b)" in st["statement"] for st in r.sent), "")

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
