#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# Two ways a running deployment can silently disagree with its own .env
# ═════════════════════════════════════════════════════════════════════════════
#
# Both were observed on the same live install, hours apart, and both presented
# as an authentication error from a service that had been working.
#
# 1. Re-running the wizard over initialised data. Postgres, Neo4j, ClickHouse
#    and MinIO read their password once, when their data directory is first
#    created. New secrets never reach them, so .env and the databases part
#    ways. Recovering cost a full wipe, because nothing warned.
#
# 2. Containers older than .env. Compose recreates a container when the
#    `environment:` it computed changes, but values passed through `env_file`
#    go straight to the container and a change there does not reliably count
#    as a reason to recreate. Containers created 19 hours before an .env
#    rewrite kept running with the old values.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"

# ─── 1. initialised_data_stores ──────────────────────────────────────────────
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

out="$(initialised_data_stores "$SANDBOX")"
check "an empty data path reports nothing" $([[ -z "$out" ]] && echo 0 || echo 1) "$out"

out="$(initialised_data_stores "$SANDBOX/does-not-exist")"
check "a missing data path reports nothing" $([[ -z "$out" ]] && echo 0 || echo 1) "$out"

# An existing but EMPTY directory is not initialised — compose creates these
# before anything has been written, and warning then would train the operator
# to click past the warning that matters.
mkdir -p "$SANDBOX/postgres" "$SANDBOX/neo4j"
out="$(initialised_data_stores "$SANDBOX")"
check "empty directories are not counted as initialised" $([[ -z "$out" ]] && echo 0 || echo 1) "$out"

touch "$SANDBOX/postgres/PG_VERSION"
out="$(initialised_data_stores "$SANDBOX")"
grep -qx postgres <<<"$out"
check "a populated store is found" $? "$out"
grep -qx neo4j <<<"$out"
check "a still-empty store beside it is not" $(( $? == 0 ? 1 : 0 )) "$out"

touch "$SANDBOX/minio/.keep" 2>/dev/null || { mkdir -p "$SANDBOX/minio"; touch "$SANDBOX/minio/.keep"; }
touch "$SANDBOX/clickhouse/x" 2>/dev/null || { mkdir -p "$SANDBOX/clickhouse"; touch "$SANDBOX/clickhouse/x"; }
out="$(initialised_data_stores "$SANDBOX")"
(( $(grep -c . <<<"$out") == 3 ))
check "every store that reads its password at init is checked" $? "$out"

# ─── 2. The wizard must warn before regenerating over data ───────────────────
block="$(sed -n '/prevrun_choice" in/,/^    fi$/p' "$REPO/scripts/bootstrap.sh")"
grep -q 'initialised_data_stores' <<<"$block"
check "the fresh-setup path checks for existing data" $? \
      "choosing 'set up afresh' regenerates secrets with no warning"
grep -q 'confirm prevrun_data_confirm' <<<"$block"
check "and requires an explicit confirmation" $? ""
# Default must be no: this is the destructive answer.
grep -q 'confirm prevrun_data_confirm "n"' <<<"$block"
check "the confirmation defaults to no" $? "a stray Enter must not regenerate secrets"

# The message has to name the mechanism, not just say "careful" — an operator
# who does not know that the password is read once cannot judge the choice.
for phrase in prevrun_data_why prevrun_data_option_keep prevrun_data_option_wipe; do
    (( $(grep -c "\[$phrase\]=" "$REPO/scripts/lib/messages.sh") == 2 ))
    check "$phrase exists in both languages" $? \
          "$(grep -c "\[$phrase\]=" "$REPO/scripts/lib/messages.sh") definition(s)"
done

# ─── 3. start-services must recreate when .env is newer ──────────────────────
src="$REPO/scripts/start-services.sh"
grep -q 'force-recreate' "$src"
check "start-services can force a recreation" $? ""
grep -q 'stat -c %Y' "$src"
check "it compares .env's modification time" $? ""
# The comparison must be against the containers, not a fixed age.
grep -q "docker inspect --format='{{.Created}}'" "$src"
check "it compares against when the containers were created" $? ""
# And it must still be a plain `up -d` when nothing changed — forcing a
# recreation on every run would restart the whole stack for no reason.
grep -qE 'RECREATE=\(\)' "$src"
check "recreation is conditional, not unconditional" $? \
      "every run would restart every container"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All stale-state checks passed: an initialised data store is told apart"
echo "from an empty directory, the wizard refuses to regenerate secrets over"
echo "one without an explicit no-by-default confirmation that names why the"
echo "password cannot be changed after initdb, and start-services recreates"
echo "containers when .env is newer than they are — but only then."
