#!/usr/bin/env bash
# The LTI session id is not private, and it must not read like it is.
#
# It looks like an internal handle and is not one. Flowise stores it on every
# chat message; chathistory-sync copies it into Weaviate's ChatHistory; and
# because the launch page sends it as the Langfuse sessionId, it is stamped on
# every trace. Whatever it contains exists in three systems, for as long as
# those records do.
#
# It used to contain the learner's given name and full name, in fields 2 and
# 5, beside the LTI pseudonym that exists so those systems would not have to
# hold a name at all. Nothing read them: the agents take field 1, the sync
# workflow takes field 3, and the only consumer of the names was a workflow
# that is switched off.
#
# Field 2 stays empty rather than closing the gap: renumbering would make
# every conversation recorded before the change look like a different agent.
# That empty field is the load-bearing part of this file — it is exactly what
# a later tidy-up would want to remove.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAIN="$REPO/lti-middleware/main.py"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

line="$(grep -n 'session_id = f"' "$MAIN" | head -1)"
[[ -n "$line" ]]
check "the session id is built in one place" $? "not found in $MAIN"
fmt="${line#*session_id = f\"}"; fmt="${fmt%\"*}"

[[ "$fmt" == '{user_id}||{agent_id}|{ts}' ]]
check "the session id is pseudonym, empty, agent, timestamp" $? "$fmt"

for name in given_name full_name '{name}'; do
    grep -q -- "$name" <<<"$fmt"
    check "no $name in the session id" $(( $? == 0 ? 1 : 0 )) "$fmt"
done

# Positions, checked by splitting a rendered example the way the consumers do.
rendered="lti-sub-9||agent-03|20260819T101500"
IFS='|' read -ra parts <<< "$rendered"
[[ "${parts[0]}" == "lti-sub-9" ]]
check "field 1 is the learner, as every agent reads it" $? "${parts[0]}"
[[ -z "${parts[1]}" ]]
check "field 2 is empty, holding the position open" $? "${parts[1]}"
[[ "${parts[2]}" == "agent-03" ]]
check "field 3 is the agent, as chathistory-sync reads it" $? "${parts[2]}"

# The middleware must not hold or log the full name either — it was kept only
# to be pasted into the id above.
grep -q 'full_name' "$MAIN"
check "the middleware no longer keeps a full name" $(( $? == 0 ? 1 : 0 )) \
      "$(grep -n 'full_name' "$MAIN" | head -3)"

launch_log="$(grep -n 'LTI launch —' "$MAIN" || true)"
[[ -n "$launch_log" ]]
check "the launch is still logged" $? ""
grep -qE "given_name|data\.get\('name'\)" <<<"$launch_log"
check "the launch log names nobody" $(( $? == 0 ? 1 : 0 )) "$launch_log"

# The learner still has to be greeted by name — that travels separately, and
# removing it here would have been a different change than the one intended.
grep -q 'student_name' "$MAIN"
check "the given name still reaches the agent as flowState" $? \
      "student_name is gone from $MAIN"

# What the agents and the sync workflow actually read, so this file fails if
# either side moves without the other.
for agent in "$REPO"/flowise/agents/*.json; do
    grep -q "split('|')\[0\]" "$agent" || continue
    # The status has to be captured before the check line: expanding
    # $(basename …) in an earlier argument overwrites $?, and the first
    # version of this loop measured basename instead of grep — reporting all
    # six agents as broken while every one of them was fine.
    grep -q "split('|')\[[1-9]\]" "$agent"; rc=$?
    name="$(basename "$agent")"
    check "$name reads only field 1" $(( rc == 0 ? 1 : 0 )) ""
done

result="$(python3 - "$REPO" <<'PY'
import json, re, sys, pathlib
repo = pathlib.Path(sys.argv[1])
w = json.loads((repo / "n8n/workflows/chathistory-sync.json").read_text())
code = "\n".join((n.get("parameters") or {}).get("jsCode", "") for n in w["nodes"])
print(",".join(sorted(set(re.findall(r"parts\[(\d)\]", code)))))
PY
)"
[[ "$result" == "0,2" ]]
check "chathistory-sync reads fields 1 and 3 and no others" $? "parts used: $result"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All LTI session-id checks passed: the id is the LTI pseudonym, an empty"
echo "field held open so older conversations keep their agent, the agent and a"
echo "timestamp — and carries no given name and no full name, which three"
echo "systems used to store beside the pseudonym that exists to replace them;"
echo "the middleware no longer keeps a full name at all and the launch log"
echo "names nobody, while the given name still reaches the agent separately as"
echo "flowState.student_name; and both consumers still read the fields they"
echo "read before — the agents field 1 only, chathistory-sync fields 1 and 3."
