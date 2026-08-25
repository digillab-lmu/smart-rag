"""Turning a proposal into something a person can actually judge.

The review box held JSON, and the operator asked the question that answers
itself once it is asked: *how realistic is it that a person can check this?*
Forty-three concepts and nine prerequisites in a text field is not a review,
it is a formality. And the object being reviewed is a **graph** — the one
shape that JSON is worst at showing.

So this renders the same proposal three ways, all read-only. The textarea
stays the thing that is submitted: one writing path, no second door into the
graph. What changes is that the reader can see what they are approving.

  * the prerequisites as a drawing, laid out left to right in teaching order;
  * the concepts as a table, grouped by the document they came from;
  * the count of concepts nothing connects to, stated rather than left to be
    noticed.

That last number is usually the most informative thing on the page. A
proposal with forty-three concepts and nine edges is mostly a vocabulary
list, and whether that is right or a sign the model was too cautious is a
judgement only the operator can make — but they can only make it if somebody
says the number out loud.
"""

from __future__ import annotations

import html
from typing import Any

# Layout constants. Deliberately generous: this is read on a laptop by
# somebody deciding whether to trust it, not printed.
_COL_W = 210
_ROW_H = 62
_BOX_W = 176
_BOX_H = 38
_PAD = 20


def view_of(proposal: dict[str, Any]) -> dict:
    """A proposal, rearranged for reading rather than for storage."""
    concepts = list(proposal.get("concepts") or [])
    edges = list(proposal.get("prerequisites") or proposal.get("edges") or [])

    by_name = {str(c.get("name", "")): c for c in concepts if c.get("name")}
    connected: set[str] = set()
    clean_edges = []
    for e in edges:
        before, after = str(e.get("before", "")), str(e.get("after", ""))
        if before in by_name and after in by_name:
            clean_edges.append({"before": before, "after": after,
                                "sources": list(e.get("sources") or [])})
            connected.update((before, after))

    # Grouped by the work each concept came from. A document that contributed
    # nonsense shows up as a block, which is a decision you can take in one
    # go instead of forty.
    groups: dict[str, list[dict]] = {}
    for c in concepts:
        for src in (c.get("sources") or ["(ohne Herkunft)"]):
            groups.setdefault(str(src), []).append(c)
    for rows in groups.values():
        rows.sort(key=lambda c: (str(c.get("chapter") or ""),
                                 str(c.get("section_id") or ""),
                                 str(c.get("name") or "")))

    return {
        "concepts": concepts,
        "edges": clean_edges,
        "groups": dict(sorted(groups.items())),
        "connected": sorted(connected),
        "orphans": sorted(n for n in by_name if n not in connected),
        "svg": _svg(by_name, clean_edges),
    }


def _levels(names: set[str], edges: list[dict]) -> dict[str, int]:
    """How far into the course each concept sits, by longest path.

    Longest rather than shortest: a concept that depends on something three
    steps in belongs after those three steps, not beside the first of them.
    The proposal is checked for cycles before it ever reaches here, so this
    terminates — but it counts iterations anyway rather than trusting that,
    because a drawing that hangs is worse than one that is slightly wrong.
    """
    level = {n: 0 for n in names}
    for _ in range(len(names) + 1):
        changed = False
        for e in edges:
            want = level[e["before"]] + 1
            if level[e["after"]] < want:
                level[e["after"]] = want
                changed = True
        if not changed:
            break
    return level


def _svg(by_name: dict, edges: list[dict]) -> str:
    """The prerequisite structure, drawn. Empty string if there is none.

    Only the concepts an edge touches. The others are in the table, and
    forty isolated boxes would be a picture of nothing that hid the picture
    of something.
    """
    if not edges:
        return ""
    names = {e["before"] for e in edges} | {e["after"] for e in edges}
    level = _levels(names, edges)

    columns: dict[int, list[str]] = {}
    for name in sorted(names):
        columns.setdefault(level[name], []).append(name)

    pos = {}
    for col, members in columns.items():
        for row, name in enumerate(members):
            pos[name] = (_PAD + col * _COL_W, _PAD + row * _ROW_H)

    width = _PAD * 2 + (max(columns) + 1) * _COL_W
    height = _PAD * 2 + max(len(m) for m in columns.values()) * _ROW_H

    out = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px;height:auto" role="img" '
        'xmlns="http://www.w3.org/2000/svg">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#555"/></marker></defs>',
    ]
    for e in edges:
        x1, y1 = pos[e["before"]]
        x2, y2 = pos[e["after"]]
        out.append(
            f'<line x1="{x1 + _BOX_W}" y1="{y1 + _BOX_H // 2}" '
            f'x2="{x2 - 4}" y2="{y2 + _BOX_H // 2}" stroke="#555" '
            'stroke-width="1.5" marker-end="url(#arrow)"/>')
    for name, (x, y) in pos.items():
        label = name if len(name) <= 26 else name[:25] + "…"
        out.append(
            f'<rect x="{x}" y="{y}" width="{_BOX_W}" height="{_BOX_H}" rx="5" '
            'fill="#eef4ee" stroke="#4a7a4a" stroke-width="1"/>')
        out.append(
            f'<text x="{x + _BOX_W // 2}" y="{y + _BOX_H // 2 + 4}" '
            'text-anchor="middle" font-size="12" fill="#1d3a1d">'
            f'{html.escape(label)}</text>')
        out.append(f'<title>{html.escape(name)}</title>')
    out.append("</svg>")
    return "".join(out)
