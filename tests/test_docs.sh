#!/usr/bin/env bash
# What the documentation claims about the repository, checked against it.
#
# Documentation goes stale silently: a feature is rebuilt, the page that
# describes it is not, and the next reader is told something that used to be
# true. Twice already in this repository — the graph page was described as
# "guided (not automated)" months after the automated build existed, and the
# same paragraph asked for Cypher to be pasted into a field that had taken
# JSON since the Cypher route was removed.
#
# Only checkable claims are checked here. Prose is held to its register by
# tests/test_text_style.py; this file is about facts that can be compared with
# the tree.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

DOCS=(README.md docs/*.md)

# ─── Claims that were true once ─────────────────────────────────────────────
# Each of these described a state the code has since left. They are listed by
# the wording that was wrong, so the same sentence cannot come back.
! grep -rn "guided (not automated)" "${DOCS[@]}" >/dev/null
check "no page still calls the graph route 'guided (not automated)'" $? \
      "the automated build exists; saying otherwise sends a reader looking for a feature they already have"

! grep -rniE "paste \+? ?run the resulting cypher|paste the .{0,12}cypher" "${DOCS[@]}" >/dev/null
check "no page still asks for Cypher to be pasted" $? \
      "that field has taken JSON, and only JSON, since the Cypher route was removed"

! grep -rn "is planned for later" README.md >/dev/null
check "the README does not announce the graph build as planned" $? \
      "it was built; a feature described as forthcoming while it runs is worse than no description"

# ─── Files the documentation points at have to exist ────────────────────────
# Every markdown link into the repository, resolved. A moved file leaves a
# link that looks fine and goes nowhere.
# Resolved against the directory of the file that contains the link, not
# against the repository root: a link written in docs/ points at its
# neighbours by bare name. The first version of this check resolved
# everything from the root and reported every one of them as missing.
missing=()
for doc in "${DOCS[@]}"; do
    dir="$(dirname "$doc")"
    while read -r target; do
        [[ -z "$target" ]] && continue
        [[ "$target" =~ ^https?:// ]] && continue
        [[ "$target" == \#* ]] && continue
        [[ "$target" == docs/img/*.png ]] && continue   # checked separately
        path="${target%%#*}"
        [[ -z "$path" ]] && continue
        [[ -e "$dir/$path" || -e "$REPO/$path" ]] || missing+=("$doc -> $path")
    done < <(grep -ohE '\]\(([^)]+)\)' "$doc" | sed -E 's/^\]\(//; s/\)$//' | sort -u)
done
(( ${#missing[@]} == 0 ))
check "every path the documentation links to exists" $? "${missing[*]:-}"

# ─── Screenshots: a typo must fail, a not-yet-taken picture must not ────────
# The images are supplied by whoever runs an installation; until they are
# here, the references stand. What must not stand is a reference to a name
# nobody plans to supply, so an image path has to be either present on disk
# or listed in the table in docs/img/README.md.
declared="$(grep -oE '`[a-z0-9-]+\.png`' docs/img/README.md | tr -d '`' | sort -u)"
unknown=()
while read -r img; do
    [[ -z "$img" ]] && continue
    name="$(basename "$img")"
    [[ -e "$REPO/$img" ]] && continue
    grep -qx "$name" <<<"$declared" || unknown+=("$img")
done < <(grep -rhoE 'docs/img/[a-z0-9-]+\.png' "${DOCS[@]}" | sort -u)
(( ${#unknown[@]} == 0 ))
check "every screenshot reference is either present or declared" $? \
      "${unknown[*]:-} — add the file, or list it in docs/img/README.md"

# And the other way round: a declared screenshot nobody shows is a picture
# somebody will take for nothing.
unused=()
while read -r name; do
    [[ -z "$name" ]] && continue
    grep -rq "docs/img/$name" "${DOCS[@]}" || unused+=("$name")
done <<<"$declared"
(( ${#unused[@]} == 0 ))
check "every declared screenshot is shown somewhere" $? "${unused[*]:-}"

# An image nobody shows. Neither of the two checks above sees it: one walks
# the references, the other the declarations, and a file that is in neither
# falls between them. That happened — a screenshot was uploaded and stayed
# invisible, which is the whole cost of taking it.
orphans=()
for img in docs/img/*.png; do
    [[ -e "$img" ]] || continue
    grep -rq "$(basename "$img")" "${DOCS[@]}" docs/img/README.md || orphans+=("$img")
done
(( ${#orphans[@]} == 0 ))
check "every image in docs/img is shown somewhere" $? \
      "${orphans[*]:-} — reference it, or delete it"

# ─── Buttons the documentation tells the reader to press ────────────────────
# A guide that names a control which no longer exists is the commonest way for
# documentation to go quietly wrong, and it is checkable: the label has to be
# in the message catalogue.
python3 - <<'PY' || exit 1
import pathlib, re, sys
sys.path.insert(0, "content-admin")
import i18n
docs = "\n".join(pathlib.Path(f).read_text()
                 for f in ["README.md", "docs/operations-guide.md"])
labels = set(i18n.MSG_EN.values())
missing = []
# Italicised names in these two files are button labels by convention.
for name in set(re.findall(r"\*([A-Z][^*\n]{6,60})\*", docs)):
    if name in labels:
        continue
    # Only complain about phrases that look like a control, not about a
    # title or an emphasised sentence.
    if name.rstrip(".").split()[0] in ("Build", "Take", "Propose", "Write",
                                       "Delete", "Publish", "Import", "Apply",
                                       "Discard", "Show", "Save"):
        missing.append(name)
if missing:
    print("FAILURES:")
    for m in missing:
        print(f"  - the documentation names a control that is not in the "
              f"message catalogue: {m!r}")
    sys.exit(1)
PY

# ─── Counts the documentation states ────────────────────────────────────────
ingest_workflows="$(ls n8n/workflows-ingest/*.json 2>/dev/null | wc -l | tr -d ' ')"
if grep -rqn "runs as two n8n workflows" README.md; then
    [[ "$ingest_workflows" == "2" ]]
    check "the README's count of ingest workflows matches the directory" $? \
          "README says two, the directory holds $ingest_workflows"
fi

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All documentation checks passed: no page still describes the knowledge"
echo "graph as a manual-only route or asks for Cypher, the README does not"
echo "announce a built feature as planned, every path linked from the"
echo "documentation exists, every screenshot reference is either present or"
echo "declared in docs/img/README.md and every declared one is actually shown,"
echo "and the stated number of ingest workflows matches the directory."
