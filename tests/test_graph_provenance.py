"""Where a concept came from, and what happens when that material leaves.

The concept map covers a whole course, across every agent in it — that is the
point of it. A course is also a container several people put agents into, and
the operator put the problem plainly: *"I know my colleagues."* Somebody adds
an agent whose material has nothing to do with the rest, somebody presses
rebuild, and the map now mixes two subjects.

Reading the code first changed what the danger is. apply_proposal writes with
MERGE, so a rebuild **adds** — no curated work is destroyed. What was
impossible was separating the two again: a concept recorded its name, course,
chapter, section and description, and nothing about where it came from, so
"take that agent's material back out" had exactly two answers — delete
concepts one at a time by name, or clear_course and lose everything.

Hence provenance, and hence these checks. The one that matters most is the
middle case: a concept **two** agents' documents support must survive the
departure of one of them. That is the whole reason sources is a list.
"""

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


class Recorder:
    """A Neo4j that records statements instead of running them."""

    def __init__(self, answers=None):
        self.sent = []
        self.answers = answers or []

    def _run(self, statements):
        self.sent.extend(statements)
        return self.answers


def client(answers=None):
    c = nc.Neo4jClient.__new__(nc.Neo4jClient)
    rec = Recorder(answers)
    c._run = rec._run
    return c, rec


def statements_of(rec):
    return [s["statement"] for s in rec.sent]


# ─── Provenance is written, or it can never be recovered ────────────────────
c, rec = client()
c.apply_proposal(
    "kurs-1",
    [{"name": "A", "sources": ["a.md"]}, {"name": "B", "sources": ["a.md", "b.md"]}],
    [{"before": "A", "after": "B", "sources": ["a.md"]}],
    build_id="build-7")

# Whitespace-normalised: these statements are written across several source
# lines, and a check that matched the formatting would fail on a re-wrap
# while passing on a real change.
joined = re.sub(r"\s+", " ", " ".join(statements_of(rec)))
check("a concept records the documents it came from",
      "c.sources" in joined, joined[:200])
check("an edge does too",
      "r.sources" in joined,
      "an edge with no citation cannot be removed with the material that "
      "justified it, and cannot be checked by a reviewer either")
check("both record the build that asserted them",
      "c.builds" in joined and "r.builds" in joined, "")

params = [s["parameters"] for s in rec.sent]
check("the sources reach the query as parameters",
      any(p.get("sources") == ["a.md", "b.md"] for p in params), params)
check("the build id travels with them",
      all(p.get("builds") == ["build-7"] for p in params if "builds" in p), params)

# Appending, not replacing: a second build that finds the same concept in a
# new document must strengthen it, not rewrite where it came from.
check("sources are appended without duplicating",
      "[x IN coalesce(c.sources, []) WHERE NOT x IN $sources] + $sources" in joined,
      "a rebuild would otherwise either lose the earlier documents or list "
      "the same one twice")
check("and this needs no database plugin",
      "apoc." not in joined.lower(),
      "a graph feature that needs APOC breaks on the next image bump")

# ─── A description a person wrote is not a description a model may replace ──
check("curated text is protected from a rebuild",
      "c.curated_at IS NULL" in joined,
      "coalesce guards only the empty case, so a model-supplied description "
      "used to overwrite one somebody had written by hand")

# ─── The three cases of taking material away ────────────────────────────────
c, rec = client()
c.remove_documents("kurs-1", ["b.md"])
stmts = statements_of(rec)
removal = re.sub(r"\s+", " ", " ".join(stmts))

# 1. supported by nothing else → gone.
check("a concept only this material supported is deleted",
      any("DETACH DELETE c" in s for s in stmts), stmts)
# 2. supported by something else → kept, and only loses that source.
norm = [re.sub(r"\s+", " ", s) for s in stmts]
kept = [s for s in norm
        if "SET c.sources = [x IN c.sources WHERE NOT x IN $docs]" in s]
check("a concept other material also supports is kept",
      bool(kept),
      "this is the case the whole design turns on — losing it means one "
      "agent leaving takes another agent's concepts with it")
check("and it loses exactly the departing documents",
      any("size([x IN c.sources WHERE NOT x IN $docs]) > 0" in s for s in kept), kept)
# 3. edges follow the same rule, separately.
check("an edge citing only this material is deleted",
      any("DELETE r" in s and "DETACH" not in s for s in stmts), stmts)
check("an edge with another citation is kept and shortened",
      any("SET r.sources" in s for s in stmts), stmts)

# Order matters: shrink the survivors before deleting the rest.
first_shrink = next((i for i, s in enumerate(stmts) if "SET c.sources" in s), -1)
first_delete = next((i for i, s in enumerate(stmts) if "DETACH DELETE c" in s), -1)
check("survivors are shrunk before the rest are deleted",
      -1 < first_shrink < first_delete, (first_shrink, first_delete))

# All of it in one transaction. A graph whose concepts are gone but whose
# edges remain is worse than either clean state.
check("the whole removal is one transaction",
      len(rec.sent) > 1 and len([1 for s in stmts if "DETACH DELETE" in s]) == 1,
      "several _run calls would leave a half-removed graph on a failure")

# A concept written before provenance existed belongs to nobody, so nothing
# may sweep it up.
check("nothing removes a concept with no sources at all",
      all("size(coalesce(c.sources, [])) > 0" in s
          for s in stmts if "DETACH DELETE c" in s),
      "concepts from before provenance would vanish without being anybody's")

# ─── Taking one build back out ──────────────────────────────────────────────
# The reason this exists is not tidiness. The operator asked the honest
# question about the review — *"mal ehrlich: wie realistisch ist es, dass man
# das als Mensch überprüfen kann?"* — and for a proposal of hundreds of
# concepts the answer is that it is not. A review that cannot be done is a
# review that gets clicked through. An exact undo changes what the reading has
# to achieve: a map you can remove in one action is one you may apply and then
# check against something real.
#
# Exact means three things survive that a blunt "delete everything from that
# run" would take: a concept an earlier build also found, one a later build
# also found, and one a person has edited by hand.
c, rec = client()
c.undo_build("kurs-1", "build-7")
stmts = [re.sub(r"\s+", " ", s) for s in statements_of(rec)]
undo = " ".join(stmts)

# "Only this build" as an exact list, not "this build is among them". The
# difference is a mutation that passed: `$build IN coalesce(c.builds, [])`
# deletes concepts a second build also found, which undoes two runs at once.
deletes = [s for s in stmts if "DETACH DELETE c" in s]
check("a concept only this build asserted is deleted", bool(deletes), stmts)
check("and 'only' means exactly that",
      all("coalesce(c.builds, []) = [$build]" in s for s in deletes),
      "matching membership instead of the whole list would take concepts an "
      "earlier or later build also found")
edge_deletes = [s for s in stmts if "DELETE r" in s and "DETACH" not in s]
check("the same is true for edges",
      all("coalesce(r.builds, []) = [$build]" in s for s in edge_deletes),
      edge_deletes)
check("but only if nobody has edited it",
      all("c.curated_at IS NULL" in s for s in stmts if "DETACH DELETE c" in s),
      "undoing a machine's work must not throw away a person's")
check("a concept another build also asserted is kept",
      any("SET c.builds = [x IN c.builds WHERE x <> $build]" in s for s in stmts),
      "it was not this run's doing, and removing it would undo two builds")
check("and loses only this build from its record",
      any("size(c.builds) > 1" in s for s in stmts), stmts)
check("edges follow the same rule",
      any("DELETE r" in s and "DETACH" not in s for s in stmts)
      and any("SET r.builds" in s for s in stmts), stmts)
check("a kept concept stops claiming the undone build",
      stmts[-1].count("SET c.builds") == 1,
      "otherwise undoing a second build later would find it and think it was "
      "that build's")
check("the whole undo is one transaction", len(rec.sent) > 1 and
      len([1 for s in stmts if "DETACH DELETE" in s]) == 1, "")

c, rec = client()
try:
    c.undo_build("kurs-1", "")
    check("undoing without naming a build is refused", False, "it ran")
except nc.GraphInputError:
    check("undoing without naming a build is refused", True)
check("and sent nothing", not rec.sent,
      "an empty build id would match coalesce(builds, []) = [''] — or worse")

c, rec = client()
c.build_contribution("kurs-1", "build-7")
preview = re.sub(r"\s+", " ", " ".join(statements_of(rec)))
check("the preview counts what would go and what would stay",
      "concepts_removed" in preview and "concepts_shared" in preview
      and "concepts_curated" in preview, preview[:200])
check("and writes nothing",
      not any(w in preview for w in ("DELETE", "SET ", "MERGE", "CREATE")),
      preview[:200])

# ─── Naming nothing is refused, rather than removing everything ─────────────
c, rec = client()
try:
    c.remove_documents("kurs-1", [])
    check("removing nothing is refused", False, "it ran with an empty list")
except nc.GraphInputError:
    check("removing nothing is refused", True)
check("and it sent no statement", not rec.sent,
      "an empty list matching 'sources all in []' would delete the course")

# ─── The preview, before anybody clicks ─────────────────────────────────────
c, rec = client(answers=[])
out = c.contribution_of("kurs-1", [])
check("a preview for nothing is zeroes, not a query",
      out["concepts_removed"] == 0 and not rec.sent, out)

c, rec = client()
c.contribution_of("kurs-1", ["b.md"])
preview = re.sub(r"\s+", " ", " ".join(statements_of(rec)))
check("the preview separates what goes from what shrinks",
      "concepts_removed" in preview and "concepts_kept" in preview, preview[:200])
check("and counts the edges too",
      "edges_removed" in preview and "edges_kept" in preview, preview[:200])
check("and says how much is unattributable",
      "unprovenanced" in preview,
      "concepts from before provenance are touched by no removal, and an "
      "operator who is not told that concludes the material is gone")
check("the preview writes nothing",
      not any(w in preview for w in ("DELETE", "SET ", "MERGE", "CREATE")),
      preview[:200])

# ─── A citation has to name material the course really holds ────────────────
good = ('{"concepts": [{"name": "A", "sources": ["a.md"]},'
        ' {"name": "B", "sources": ["a.md"]}],'
        ' "prerequisites": [{"before": "A", "after": "B", "sources": ["a.md"]}]}')
concepts, edges = nc.parse_proposal(good, known_sources=["a.md", "b.md"])
check("a proposal keeps its concept citations",
      concepts[0]["sources"] == ["a.md"], concepts[0])
check("and its edge citations", edges[0]["sources"] == ["a.md"], edges[0])

bad = ('{"concepts": [{"name": "A", "sources": ["a.md"]},'
       ' {"name": "B", "sources": ["a.md"]}],'
       ' "prerequisites": [{"before": "A", "after": "B",'
       ' "sources": ["erfunden.pdf"]}]}')
try:
    nc.parse_proposal(bad, known_sources=["a.md"])
    check("an edge citing material that does not exist is refused", False, "")
except nc.GraphInputError as exc:
    check("an edge citing material that does not exist is refused", True)
    check("and the message names the invention",
          "erfunden.pdf" in str(exc), str(exc))

# Without a corpus to check against, nothing is invented and nothing is
# refused — the pasted route has no document list to offer.
plain = nc.parse_proposal('{"concepts": [{"name": "A"}], "prerequisites": []}')
check("a proposal with no provenance still parses", plain[0][0]["name"] == "A", plain)
check("and carries an empty source list rather than None",
      plain[0][0]["sources"] == [], plain[0][0])

# ─── Odd shapes of provenance ───────────────────────────────────────────────
check("a single string becomes a list", nc._source_list("a.md") == ["a.md"], "")
check("blanks are dropped", nc._source_list(["", "  ", "a.md"]) == ["a.md"],
      "a source of '' claims support from a document that does not exist, and "
      "survives every cleanup because nothing matches it")
check("duplicates collapse", nc._source_list(["a.md", "a.md"]) == ["a.md"], "")
check("nothing is an empty list", nc._source_list(None) == [], "")

# ─── The scope lives on the agent, and the migration says so ────────────────
migration = (APP_DIR / "migrations" / "004_graph_scope_and_builds.sql").read_text()
check("an agent carries whether its material is included",
      re.search(r"agent_slots ADD COLUMN IF NOT EXISTS in_graph boolean", migration),
      "")
check("and it defaults to included",
      "NOT NULL DEFAULT true" in migration,
      "a default of false makes the feature look broken — nothing ever "
      "enters the map until somebody finds a checkbox")
check("a build is recorded", "CREATE TABLE IF NOT EXISTS graph_builds" in migration, "")
check("a proposed build is not an applied one",
      "'proposed'" in migration and "'applied'" in migration,
      "the safety of this feature is that nothing reaches Neo4j until a "
      "person submits the review")
check("only one build may run per course at a time",
      "graph_builds_one_active_idx" in migration,
      "an impatient second click is a second run over the same corpus at "
      "full price, and two proposals racing to be the one that is seen")
check("the scope a build used is kept with it",
      "scope" in migration,
      "the question about an old proposal is what it was built from, and "
      "today's checkboxes are not that")

if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("All graph provenance checks passed: every concept and edge records the")
print("documents behind it and the build that asserted them, appended without")
print("duplicates and without needing APOC, and a description somebody wrote by")
print("hand is no longer overwritten by a model's; taking material away deletes")
print("what only it supported, keeps what other material also supports and")
print("shortens its citations instead, does both for edges, shrinks survivors")
print("before deleting the rest, runs as one transaction, refuses an empty list")
print("rather than emptying the course, and never touches a concept that has no")
print("provenance at all; the preview counts what would go, what would shrink")
print("and what cannot be attributed, and writes nothing; a citation naming")
print("material the course does not hold is refused by name, while a pasted")
print("proposal with no provenance still parses; and the scope lives on the")
print("agent, included by default, with one build per course at a time and the")
print("scope it ran with kept beside it.")
