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

# Layout. Boxes are sized to their label rather than the other way round:
# the first version truncated at 26 characters, and "Kompetenzorientierte
# Leistungsmessung" became "Kompetenzorientierte Leis…", which is exactly the
# information somebody needs to judge whether the concept belongs.
_CHAR_W = 6.6          # measured for 12px in the page's font stack
_LINE_H = 15
_BOX_PAD_X = 14
_BOX_PAD_Y = 10
_MIN_BOX_W = 120
_MAX_BOX_W = 260
_COL_GAP = 56
_ROW_GAP = 18
_PAD = 24


def _wrap(text: str, width_chars: int, max_lines: int = 3) -> list[str]:
    """Break a name over a few lines, on spaces, without inventing hyphens.

    Three lines at most, and the last one is elided if it still does not fit —
    but the full name is in a <title> and in the table, so nothing is only
    ever visible in truncated form.
    """
    words = str(text).split()
    if not words:
        return [""]
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines:
        rest = " ".join(words)
        shown = " ".join(lines)
        if len(shown) < len(rest):
            lines[-1] = lines[-1][: max(1, width_chars - 1)].rstrip() + "\u2026"
    return lines


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
    """The prerequisite structure, drawn, clickable, and honest about size.

    Only the concepts an edge touches. The others are in the table, and forty
    isolated boxes would be a picture of nothing that hid the picture of
    something.

    Every box is a link target rather than an image: clicking one dims
    everything it has nothing to do with, which is the question a reader
    actually has — *what does this one depend on, and what depends on it* —
    and cannot be answered by staring at a full graph.
    """
    if not edges:
        return ""
    names = {e["before"] for e in edges} | {e["after"] for e in edges}
    level = _levels(names, edges)

    # Wrap first, because the column width follows from the widest label in it.
    wrapped, box_w, box_h = {}, {}, {}
    for name in names:
        chars = int(_MAX_BOX_W / _CHAR_W)
        lines = _wrap(name, chars)
        width = max(_MIN_BOX_W,
                    min(_MAX_BOX_W,
                        int(max(len(x) for x in lines) * _CHAR_W) + _BOX_PAD_X * 2))
        wrapped[name] = lines
        box_w[name] = width
        box_h[name] = len(lines) * _LINE_H + _BOX_PAD_Y * 2

    columns: dict[int, list[str]] = {}
    for name in sorted(names):
        columns.setdefault(level[name], []).append(name)

    col_w = {c: max(box_w[n] for n in members) for c, members in columns.items()}
    col_x, x = {}, _PAD
    for c in sorted(columns):
        col_x[c] = x
        x += col_w[c] + _COL_GAP
    width = x - _COL_GAP + _PAD

    pos, height = {}, 0
    for c, members in columns.items():
        y = _PAD
        for name in members:
            pos[name] = (col_x[c], y)
            y += box_h[name] + _ROW_GAP
        height = max(height, y - _ROW_GAP + _PAD)

    def ident(name: str) -> str:
        return "n" + str(abs(hash(name)) % (10 ** 12))

    out = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'style="max-width:{width}px;height:auto" role="img" '
        'class="cmap" xmlns="http://www.w3.org/2000/svg">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#555"/></marker></defs>',
    ]
    for e in edges:
        x1, y1 = pos[e["before"]]
        x2, y2 = pos[e["after"]]
        out.append(
            f'<line class="cedge" data-a="{ident(e["before"])}" '
            f'data-b="{ident(e["after"])}" '
            f'x1="{x1 + box_w[e["before"]]}" y1="{y1 + box_h[e["before"]] // 2}" '
            f'x2="{x2 - 5}" y2="{y2 + box_h[e["after"]] // 2}" '
            'stroke="#555" stroke-width="1.5" marker-end="url(#arrow)"/>')
    for name, (x, y) in pos.items():
        lines = wrapped[name]
        out.append(f'<g class="cnode" id="{ident(name)}" '
                   f'data-name="{html.escape(name, quote=True)}" tabindex="0">')
        out.append(
            f'<rect x="{x}" y="{y}" width="{box_w[name]}" height="{box_h[name]}" '
            'rx="6" fill="#eef4ee" stroke="#4a7a4a" stroke-width="1"/>')
        for i, line in enumerate(lines):
            ty = y + _BOX_PAD_Y + _LINE_H * i + 11
            out.append(
                f'<text x="{x + box_w[name] // 2}" y="{ty}" text-anchor="middle" '
                f'font-size="12" fill="#1d3a1d">{html.escape(line)}</text>')
        out.append(f'<title>{html.escape(name)}</title>')
        out.append("</g>")
    out.append("</svg>")
    return "".join(out)


def from_form(form) -> dict:
    """Rebuild a proposal from the edited rows.

    The alternative to a JSON textarea, and the reason it exists: a person
    asked to approve a graph should be able to strike out a concept, correct a
    name and drop a wrong prerequisite without editing punctuation. What comes
    out of here goes through exactly the same parse_proposal and
    apply_proposal as a pasted answer — the form is another way of writing the
    proposal, not another way into the graph.

    Renaming is the part with a trap in it. Edges refer to concepts by name,
    so correcting "Medienkompetenzen" to "Medienkompetenz" would silently
    orphan every edge that touched it — and the proposal would then be
    rejected wholesale for naming a concept that is not in the list. The
    original name travels in a hidden field so the edges can be carried
    across.
    """
    rename: dict[str, str] = {}
    concepts = []
    kept = set(form.getlist("c_keep"))
    index = 0
    while f"c_name_{index}" in form:
        i = index
        index += 1
        original = form.get(f"c_orig_{i}", "").strip()
        name = form.get(f"c_name_{i}", "").strip()
        if str(i) not in kept or not name:
            continue
        if original and original != name:
            rename[original] = name
        concept = {"name": name}
        for field, key in (("c_chapter", "chapter"), ("c_section", "section_id"),
                           ("c_desc", "description")):
            value = form.get(f"{field}_{i}", "").strip()
            if value:
                concept[key] = value
        sources = [x for x in form.getlist(f"c_src_{i}") if x.strip()]
        if sources:
            concept["sources"] = sources
        concepts.append(concept)

    names = {c["name"] for c in concepts}
    edges = []
    kept_edges = set(form.getlist("e_keep"))
    index = 0
    while f"e_before_{index}" in form:
        i = index
        index += 1
        if str(i) not in kept_edges:
            continue
        before = form.get(f"e_before_{i}", "").strip()
        after = form.get(f"e_after_{i}", "").strip()
        before, after = rename.get(before, before), rename.get(after, after)
        # An edge whose endpoint was struck out goes with it, rather than
        # failing the whole proposal on submit.
        if before not in names or after not in names or before == after:
            continue
        sources = [x for x in form.getlist(f"e_src_{i}") if x.strip()]
        edges.append({"before": before, "after": after, "sources": sources})

    # One new edge per submit. Adding twenty by hand is not what this is for;
    # being able to add the one the model missed is.
    new_before = form.get("new_before", "").strip()
    new_after = form.get("new_after", "").strip()
    if new_before and new_after and new_before != new_after \
            and new_before in names and new_after in names:
        pair = {(e["before"], e["after"]) for e in edges}
        if (new_before, new_after) not in pair:
            # No citation: a person asserting a dependency is the citation,
            # and inventing a document here would be worse than an empty list.
            edges.append({"before": new_before, "after": new_after, "sources": []})

    return {"concepts": concepts, "prerequisites": edges}
