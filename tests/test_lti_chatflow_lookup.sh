#!/usr/bin/env bash
# Where an LTI launch finds its chatflow.
#
# Until 0.3.0 it came from the environment. `agents.json` named a variable per
# agent — CHATFLOW_AGENT01 and so on — and the Content Admin wrote those
# variables on import. Two things followed, and neither was written down where
# an operator would meet it:
#
#   * The variables were written for the oldest course only, because the name
#     carries a slot and has no room for a course. A second course could
#     import its agents and this service would never learn their chatflows.
#     LTI therefore worked for exactly one course per installation.
#
#   * The mapping was built once at module import, so a newly imported agent
#     needed the container recreated. A launch before that resolved to an
#     empty chatflow id and produced a chat page pointed at nothing.
#
# The ids live in agent_slots, which the Content Admin writes on every import.
# These checks hold the two ends of the replacement: nothing reads the
# variables any more, and nothing writes them either.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

main="$(cat "$REPO/lti-middleware/main.py")"
slots="$(cat "$REPO/lti-middleware/slots.py")"
compose="$(cat "$REPO/docker/docker-compose.yml")"

# ─── The reader ──────────────────────────────────────────────────────────────
grep -q "import slots" <<<"$main"
check "the middleware has a slot lookup" $? "it would still read the environment"

grep -q "slots.chatflow_for" <<<"$main"
check "and resolves the chatflow through it" $? ""

if grep -qE 'CHATFLOW_AGENT|chatflow_env' <<<"$main"; then
    check "no chatflow comes from the environment any more" 1 \
          "$(grep -noE 'CHATFLOW_AGENT[0-9]*|chatflow_env' <<<"$main" | head -3 | tr '\n' ' ')"
else
    check "no chatflow comes from the environment any more" 0
fi

# Resolved per request. A module-level lookup would reintroduce the restart.
if grep -qE '^AGENT_CONFIG *=.*chatflow' <<<"$main"; then
    check "the chatflow is not resolved at import time" 1 \
          "a re-imported agent would need the container recreated again"
else
    check "the chatflow is not resolved at import time" 0
fi

# An empty slot and an unreachable database are different answers.
grep -q "error_agent_not_imported" <<<"$main"
check "a configured agent with no chatflow says so" $? \
      "it would look like an unknown agent"
grep -q "error_chatflow_unavailable" <<<"$main"
check "and a database that cannot be reached says that instead" $? \
      "a lookup failure reported as a missing agent sends the operator to agents.json"

grep -q "def chatflow_for" <<<"$slots"
check "the lookup takes a course and a slot" $? ""
grep -q "course_id = %s AND slot = %s" <<<"$slots"
check "and queries agent_slots by both" $? \
      "a query on slot alone would cross courses"

# ─── The writer ──────────────────────────────────────────────────────────────
if grep -q 'set_env_var(f"CHATFLOW_AGENT' "$REPO/content-admin/app.py"; then
    check "the Content Admin no longer writes the variables" 1 \
          "they would be written for a reader that no longer exists"
else
    check "the Content Admin no longer writes the variables" 0
fi

if grep -qE '^ *CHATFLOW_AGENT[0-9]+:' <<<"$compose"; then
    check "compose no longer passes them to the container" 1 "$(grep -cE '^ *CHATFLOW_AGENT[0-9]+:' <<<"$compose") still there"
else
    check "compose no longer passes them to the container" 0
fi

grep -qE 'CHATFLOW_AGENT' "$REPO/.env.example"
check "and .env.example no longer offers them" $(( $? == 0 ? 1 : 0 )) \
      "the Upgrade step reads that file for defaults of missing keys"

# ─── The database the lookup needs ───────────────────────────────────────────
awk '/^  smartrag-lti:/{f=1;next} /^  [a-z]/{f=0} f' "$REPO/docker/docker-compose.yml" \
  | grep -q "smartrag-postgres"
check "the lti service waits for Postgres" $? \
      "the first launch after a restart would race the database"

grep -q "psycopg" "$REPO/lti-middleware/requirements.txt"
check "and its image has a driver" $? "the import would fail at start-up"

# ─── The configuration an operator edits ─────────────────────────────────────
example="$REPO/lti-middleware/config/agents.json.example"
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$example"
check "agents.json.example is valid JSON" $? ""

out="$(python3 - "$example" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
agents = d.get("agents", [])
bad = [a for a in agents if not isinstance(a.get("slot"), int) or not 1 <= a["slot"] <= 10]
dead = [k for a in agents for k in ("chatflow_env", "label", "block") if k in a]
print("SLOTS_OK" if agents and not bad else f"SLOTS_BAD {bad}")
print("NO_DEAD_FIELDS" if not dead else f"DEAD {sorted(set(dead))}")
PY
)"
grep -q "SLOTS_OK" <<<"$out"
check "every agent names a slot the Content Admin allows" $? "$out"
grep -q "NO_DEAD_FIELDS" <<<"$out"
check "and carries no field nothing reads" $? "$out"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All LTI chatflow-lookup checks passed: a launch resolves its chatflow"
echo "from agent_slots at request time rather than from an environment"
echo "variable fixed at container start, so an agent re-imported in the"
echo "Content Admin is reachable at once and a second course is reachable at"
echo "all; an empty slot and an unreachable database are told apart; the"
echo "variables are gone from the writer, from compose and from .env.example;"
echo "the service waits for Postgres and its image carries a driver; and the"
echo "example configuration names slots in the range the Content Admin allows,"
echo "with no field left in it that nothing reads."
