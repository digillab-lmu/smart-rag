#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# The chat-history repair, and the objects it must not touch
# ═════════════════════════════════════════════════════════════════════════════
#
# The script rewrites the course of stored conversations. Getting that wrong
# in the safe direction costs a message nobody can retrieve; getting it wrong
# in the other direction puts one course's conversations into another course's
# agent, and nothing in any status output would say so.
#
# So the interesting assertions are the negative ones: an object whose
# chatflow is in no slot, one whose Flowise message has been deleted, and one
# with no trace_id at all must come out of the run exactly as they went in —
# even though all three look like "no course, please fill in".
#
# Run against stubbed `docker` and `curl`, with only the root gate patched
# out of the sandbox copy. Everything under test runs as written.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

command -v jq >/dev/null 2>&1 || {
    echo "jq is not installed, and the script under test requires it."
    exit 10
}

SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
mkdir -p "$SANDBOX/scripts/lib" "$SANDBOX/bin"
cp "$REPO"/scripts/lib/*.sh "$SANDBOX/scripts/lib/"

sed 's|^if \[\[ "\${EUID:-\$(id -u)}" -ne 0 \]\]; then|if false; then|' \
    "$REPO/scripts/repair-chathistory-course.sh" \
    > "$SANDBOX/scripts/repair-chathistory-course.sh"

cat > "$SANDBOX/.env" <<'ENV'
POSTGRES_USER="smartrag"
POSTGRES_PASSWORD="test-postgres-password"
WEAVIATE_HTTP_PORT=8080
WEAVIATE_API_KEY="test-weaviate-key"
ENV

PATCH_LOG="$SANDBOX/patches.log"
: > "$PATCH_LOG"

# ─── The fixture ─────────────────────────────────────────────────────────────
# Two slots have a chatflow. CF-OLD does not — it is the shape an older
# installation has: conversations from an agent nobody assigned to a slot.
cat > "$SANDBOX/bin/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "ps" ]]; then echo "smartrag-postgres"; exit 0; fi
if [[ "$1" == "exec" ]]; then
    case "$*" in
      *"-d contentadmin"*)
          echo "CF-MATHE|mathe-1"
          echo "CF-CHEM|chemie-1"
          exit 0 ;;
      *"-d flowise"*)
          echo "m-mathe-1|CF-MATHE"
          echo "m-mathe-2|CF-MATHE"
          echo "m-chem-1|CF-CHEM"
          echo "m-old-1|CF-OLD"
          exit 0 ;;
    esac
    exit 1
fi
exit 0
STUB
chmod +x "$SANDBOX/bin/docker"

# Six objects, one of each situation the script has to tell apart.
cat > "$SANDBOX/objects.json" <<'JSON'
{"objects":[
 {"id":"o1","properties":{"trace_id":"m-mathe-1","course_id":""}},
 {"id":"o2","properties":{"trace_id":"m-mathe-2","course_id":"testkurs2"}},
 {"id":"o3","properties":{"trace_id":"m-chem-1","course_id":"chemie-1"}},
 {"id":"o4","properties":{"trace_id":"m-old-1","course_id":"testkurs2"}},
 {"id":"o5","properties":{"course_id":"testkurs2"}},
 {"id":"o6","properties":{"trace_id":"m-deleted","course_id":""}}
]}
JSON

cat > "$SANDBOX/bin/curl" <<STUB
#!/usr/bin/env bash
url="\${!#}"
if [[ "\$*" == *"-X PATCH"* ]]; then
    body=""
    prev=""
    for a in "\$@"; do
        [[ "\$prev" == "-d" ]] && body="\$a"
        prev="\$a"
    done
    printf '%s %s\n' "\$url" "\$body" >> "$PATCH_LOG"
    exit 0
fi
case "\$url" in
  *.well-known/ready) exit 0 ;;
  *v1/schema/ChatHistory) echo '{"class":"ChatHistory"}'; exit 0 ;;
  *after=*) echo '{"objects":[]}'; exit 0 ;;
  *v1/objects?class=ChatHistory*) cat "$SANDBOX/objects.json"; exit 0 ;;
esac
exit 1
STUB
chmod +x "$SANDBOX/bin/curl"

run() {   # $@ = arguments to the script
    PATH="$SANDBOX/bin:$PATH" bash "$SANDBOX/scripts/repair-chathistory-course.sh" \
        --lang en "$@" 2>&1
}

# ─── 1. Without --apply, nothing is written ──────────────────────────────────
out="$(run)"; rc=$?
check "the dry run succeeds" $rc "$out"
check "it says it will not write" \
      "$(grep -qi 'Nothing is written without --apply' <<<"$out" && echo 0 || echo 1)" "$out"
check "it counts the two that are wrong" \
      "$(grep -q '2 object(s) would be rewritten' <<<"$out" && echo 0 || echo 1)" "$out"
check "the dry run writes nothing at all" \
      "$([[ ! -s "$PATCH_LOG" ]] && echo 0 || echo 1)" "$(cat "$PATCH_LOG")"

# The grouped report is what an operator decides on, so it has to name both
# directions: filling in a blank and correcting a wrong course.
check "it reports the objects with no course" \
      "$(grep -q 'no course → mathe-1: 1 object(s)' <<<"$out" && echo 0 || echo 1)" "$out"
check "it reports the ones filed under the wrong course" \
      "$(grep -q 'testkurs2 → mathe-1: 1 object(s)' <<<"$out" && echo 0 || echo 1)" "$out"

# ─── 2. The three that must be left alone ────────────────────────────────────
check "an object without a trace_id is counted, not guessed at" \
      "$(grep -q '1 without a trace_id' <<<"$out" && echo 0 || echo 1)" "$out"
check "an object whose chat was deleted is counted, not guessed at" \
      "$(grep -q '1 whose Flowise message no longer exists' <<<"$out" && echo 0 || echo 1)" "$out"
check "an object from an unassigned chatflow is counted, not guessed at" \
      "$(grep -q '1 from a chatflow that is in no slot' <<<"$out" && echo 0 || echo 1)" "$out"
check "the one already correct is not counted as work" \
      "$(grep -q '6 object(s) examined, 1 already correct' <<<"$out" && echo 0 || echo 1)" "$out"

# ─── 3. With --apply, exactly those two ──────────────────────────────────────
out="$(run --apply)"; rc=$?
check "the apply run succeeds" $rc "$out"
check "two objects were rewritten" \
      "$(grep -q '2 object(s) now carry the course' <<<"$out" && echo 0 || echo 1)" "$out"
check "exactly two PATCHes were sent" \
      "$([[ "$(wc -l <"$PATCH_LOG")" -eq 2 ]] && echo 0 || echo 1)" "$(cat "$PATCH_LOG")"
check "the one with no course was filled in" \
      "$(grep -q '/o1 .*mathe-1' <"$PATCH_LOG" && echo 0 || echo 1)" "$(cat "$PATCH_LOG")"
check "the one under the wrong course was moved" \
      "$(grep -q '/o2 .*mathe-1' <"$PATCH_LOG" && echo 0 || echo 1)" "$(cat "$PATCH_LOG")"
for id in o3 o4 o5 o6; do
    check "$id was not touched" \
          "$(grep -q "/$id " <"$PATCH_LOG" && echo 1 || echo 0)" "$(cat "$PATCH_LOG")"
done
# Only the course is sent. A PATCH carrying anything else would overwrite
# whatever the sync has written in the meantime.
check "nothing but the course is written" \
      "$(cut -d' ' -f2- <"$PATCH_LOG" | jq -e -s 'all(.properties | keys == ["course_id"])' \
         >/dev/null && echo 0 || echo 1)" "$(cat "$PATCH_LOG")"

# ─── 4. A second run has nothing left to do ──────────────────────────────────
# Re-running is the normal case — an operator repeats it after fixing whatever
# blocked the first attempt — so it has to be a no-op, not a second rewrite.
python3 - "$SANDBOX/objects.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
for o in d["objects"]:
    if o["id"] in ("o1", "o2"):
        o["properties"]["course_id"] = "mathe-1"
json.dump(d, open(p, "w"))
PY
: > "$PATCH_LOG"
out="$(run --apply)"; rc=$?
check "the second run succeeds" $rc "$out"
check "the second run changes nothing" \
      "$([[ ! -s "$PATCH_LOG" ]] && echo 0 || echo 1)" "$(cat "$PATCH_LOG")"
check "…and says so" \
      "$(grep -q 'Nothing to repair' <<<"$out" && echo 0 || echo 1)" "$out"

# ─── 5. Refusing to run beats running blind ──────────────────────────────────
# With no chatflow assigned to a slot, every object would look unplaceable and
# the run would report a clean "nothing to repair" — a false all-clear.
cat > "$SANDBOX/bin/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "ps" ]]; then echo "smartrag-postgres"; exit 0; fi
if [[ "$1" == "exec" ]]; then
    case "$*" in
      *"-d contentadmin"*) exit 0 ;;
      *"-d flowise"*) echo "m-mathe-1|CF-MATHE"; exit 0 ;;
    esac
fi
exit 0
STUB
chmod +x "$SANDBOX/bin/docker"
out="$(run)"; rc=$?
check "an empty slot table stops the run" "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "$out"
check "…with the reason and what to do" \
      "$(grep -q 'No chatflow is assigned to a slot' <<<"$out" && echo 0 || echo 1)" "$out"

# And the same for the other lookup: no messages at all means the query
# failed or Flowise is empty, and either way every object would be reported
# as "its chat is gone".
cat > "$SANDBOX/bin/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "ps" ]]; then echo "smartrag-postgres"; exit 0; fi
if [[ "$1" == "exec" ]]; then
    case "$*" in
      *"-d contentadmin"*) echo "CF-MATHE|mathe-1"; exit 0 ;;
      *"-d flowise"*) exit 0 ;;
    esac
fi
exit 0
STUB
chmod +x "$SANDBOX/bin/docker"
out="$(run)"; rc=$?
check "an empty message table stops the run" "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "$out"
check "…with the reason" \
      "$(grep -qi 'chat_message table returned nothing' <<<"$out" && echo 0 || echo 1)" "$out"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All chat-history repair checks passed: the script reports before it"
echo "writes and writes nothing without --apply; it fills in a missing course"
echo "and corrects a wrong one, both named in the report; an object with no"
echo "trace_id, one whose Flowise message is gone and one from a chatflow in"
echo "no slot are counted and left exactly as they were; only course_id is"
echo "sent; a second run is a no-op; and an empty slot or message table stops"
echo "the run instead of reporting a clean nothing-to-repair."
