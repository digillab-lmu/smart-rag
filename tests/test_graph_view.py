"""Showing a proposal as something other than JSON.

The operator, twice, looking at a filled review box: *"mal ehrlich: wie
realistisch ist es, dass man das als Mensch überprüfen kann?"* and then *"es
ist immer noch schwer, das zu verstehen. Kann man das irgendwie besser
visualisieren?"*

Both are fair. The thing under review is a **graph**, and JSON is the worst
possible rendering of one. So the same proposal is shown three ways — the
prerequisites drawn, the concepts tabled by the work they came from, and the
number of concepts nothing connects to said out loud.

Two properties matter more than the prettiness:

  * the picture is **read-only**. The textarea is still the only thing
    submitted, so there is one writing path and the drawing can never
    disagree with what is applied;
  * the picture must not flatter the proposal. An edge to a concept that is
    not in the list is left out of the drawing, because drawing it would
    show a connection that will not exist — and the count of unconnected
    concepts is stated rather than left to be noticed, since a map that is
    mostly unconnected is a vocabulary list and the operator should be told
    so.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "content-admin"
if not APP_DIR.is_dir() and Path("/app/db.py").exists():
    APP_DIR = Path("/app")
sys.path.insert(0, str(APP_DIR))

import graph_view  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def concept(name, **kw):
    base = {"name": name, "sources": ["agent_1/a.md"]}
    base.update(kw)
    return base


CHAIN = {
    "concepts": [concept("A", chapter="1"), concept("B", chapter="2"),
                 concept("C", chapter="3"),
                 concept("Allein", chapter="9", sources=["agent_2/b.md"])],
    "prerequisites": [{"before": "A", "after": "B", "sources": ["agent_1/a.md"]},
                      {"before": "B", "after": "C", "sources": ["agent_1/a.md"]}],
}

v = graph_view.view_of(CHAIN)

# ─── What is connected, and what is not ─────────────────────────────────────
check("concepts an arrow touches are known as connected",
      v["connected"] == ["A", "B", "C"], v["connected"])
check("and the rest are named, not just missing",
      v["orphans"] == ["Allein"], v["orphans"],)
check("every concept is still listed somewhere",
      sum(len(r) for r in v["groups"].values()) == 4, v["groups"])
check("grouped by the work it came from",
      set(v["groups"]) == {"agent_1/a.md", "agent_2/b.md"}, list(v["groups"]))

# ─── The drawing ────────────────────────────────────────────────────────────
svg = v["svg"]
check("there is a drawing", svg.startswith("<svg"), svg[:60])
check("with one box per connected concept", svg.count("<rect") == 3,
      svg.count("<rect"))
check("and no box for the unconnected one", "Allein" not in svg,
      "forty isolated boxes would be a picture of nothing that hides the "
      "picture of something")
check("one arrow per prerequisite", svg.count("<line") == 2, svg.count("<line"))
check("arrows have a head, so the direction is visible",
      "marker-end" in svg and "<marker" in svg, "")
check("the full name is available on hover even when the box truncates",
      "<title>" in svg, "")

# Teaching order, left to right: C depends on B depends on A, so the three
# must sit in three different columns and in that order.
import re  # noqa: E402
xs = {m.group(2): int(m.group(1)) for m in
      re.finditer(r'<rect x="(\d+)"[^>]*/><text[^>]*>([^<]+)</text>', svg)}
if len(xs) != 3:
    xs = {}
    rects = re.findall(r'<rect x="(\d+)" y="\d+"', svg)
    labels = re.findall(r'>([^<>]+)</text>', svg)
    xs = dict(zip(labels, [int(x) for x in rects]))
check("the chain is laid out in teaching order",
      xs.get("A", 0) < xs.get("B", 1) < xs.get("C", 2), xs)

# A longer path must not sit beside its own prerequisite: with A→B, A→C and
# B→C, C belongs after B, not next to it.
diamond = {
    "concepts": [concept("A"), concept("B"), concept("C")],
    "prerequisites": [{"before": "A", "after": "B"}, {"before": "A", "after": "C"},
                      {"before": "B", "after": "C"}],
}
dsvg = graph_view.view_of(diamond)["svg"]
rects = [int(x) for x in re.findall(r'<rect x="(\d+)"', dsvg)]
labels = re.findall(r'>([^<>]+)</text>', dsvg)
dxs = dict(zip(labels, rects))
check("depth is the longest path, not the shortest",
      dxs.get("C", 0) > dxs.get("B", 0) > dxs.get("A", 0), dxs)

# ─── The drawing must not show what will not happen ─────────────────────────
dangling = {
    "concepts": [concept("A"), concept("B")],
    "prerequisites": [{"before": "A", "after": "B"},
                      {"before": "A", "after": "Gibt es nicht"}],
}
d = graph_view.view_of(dangling)
check("an edge to a concept that is not in the list is not drawn",
      d["svg"].count("<line") == 1, d["svg"].count("<line"))
check("and not tabled either", len(d["edges"]) == 1, d["edges"])
check("nor does it invent a box for it", "Gibt es nicht" not in d["svg"], "")

# ─── A proposal with no relations says so, rather than showing an empty box ─
flat = {"concepts": [concept("A"), concept("B")], "prerequisites": []}
f = graph_view.view_of(flat)
check("no prerequisites means no drawing at all", f["svg"] == "", f["svg"][:80])
check("and both concepts count as unconnected",
      f["orphans"] == ["A", "B"], f["orphans"])

# ─── Names are escaped, because they come from a model ──────────────────────
nasty = {"concepts": [concept("<script>alert(1)</script>"), concept("B")],
         "prerequisites": [{"before": "<script>alert(1)</script>", "after": "B"}]}
n = graph_view.view_of(nasty)
check("a name is escaped into the drawing",
      "<script>" not in n["svg"] and "&lt;script&gt;" in n["svg"],
      "the names come from a model reading uploaded documents, and this SVG "
      "is inlined into the page")

# ─── It must terminate even on input it should never see ────────────────────
# parse_proposal rejects cycles before this is reached, but a drawing that
# hangs is worse than one that is slightly wrong.
cyclic = {"concepts": [concept("A"), concept("B")],
          "prerequisites": [{"before": "A", "after": "B"},
                            {"before": "B", "after": "A"}]}
c = graph_view.view_of(cyclic)
check("a cycle does not hang the layout", c["svg"].count("<rect") == 2,
      c["svg"].count("<rect"))

if failures:
    print("FAILURES:")
    for f_ in failures:
        print(f"  - {f_}")
    sys.exit(1)

print("All graph-view checks passed: a proposal is shown as a drawing of its")
print("prerequisites laid out in teaching order by longest path, as a table of")
print("concepts grouped by the work each came from, and as a count of the")
print("concepts nothing connects to; every concept appears somewhere, arrows")
print("carry a head so direction is visible and a title so a truncated name is")
print("still readable; an edge naming a concept that is not in the proposal is")
print("neither drawn nor tabled, because showing a connection that will not")
print("exist is worse than showing none; a proposal with no relations produces")
print("no drawing rather than an empty frame; names are escaped, since they")
print("come from a model reading uploaded files into an inlined SVG; and a")
print("cycle terminates instead of hanging the page.")
