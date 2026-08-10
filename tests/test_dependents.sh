#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# Restarting a backend leaves its dependents talking to what used to be there
# ═════════════════════════════════════════════════════════════════════════════
#
# Observed on a live install: Redis was recreated after Langfuse, and Langfuse
# then reported `connect ETIMEDOUT` against an address nothing answered on.
# The name resolved once, at startup, to a container that no longer existed.
#
# `depends_on` does not help — it orders startup and does not propagate a
# restart. So the admin tool has to know the reverse graph. It reads it from
# Compose's own normalised configuration rather than parsing the YAML, which
# is what this stubs.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"

SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

# A stub compose.sh emitting a graph with a two-level chain:
#   redis  <- flowise <- flowise-worker
#   postgres <- flowise, content-admin
make_stub() {   # $1 = what `config --format json` prints ("" = fail)
    cat > "$SANDBOX/compose.sh" <<STUB
#!/usr/bin/env bash
[[ "\$*" == *"config"* ]] || exit 1
$1
STUB
    chmod +x "$SANDBOX/compose.sh"
}

GRAPH='cat <<JSON
{"services":{
  "smartrag-redis":{},
  "smartrag-postgres":{},
  "smartrag-flowise":{"depends_on":{"smartrag-redis":{},"smartrag-postgres":{}}},
  "smartrag-flowise-worker":{"depends_on":{"smartrag-flowise":{}}},
  "smartrag-content-admin":{"depends_on":{"smartrag-postgres":{}}},
  "smartrag-weaviate":{}
}}
JSON'

make_stub "$GRAPH"

# ─── Direct and transitive dependents ────────────────────────────────────────
out="$(compose_dependents smartrag-redis "$SANDBOX/compose.sh" | sort | tr '\n' ' ')"
grep -q 'smartrag-flowise ' <<<"$out "
check "a direct dependent is found" $? "$out"
grep -q 'smartrag-flowise-worker' <<<"$out"
check "a dependent of the dependent is found too" $? \
      "the worker talks to Flowise, which was just replaced: $out"

out="$(compose_dependents smartrag-postgres "$SANDBOX/compose.sh" | sort | tr '\n' ' ')"
for svc in smartrag-flowise smartrag-flowise-worker smartrag-content-admin; do
    grep -q "$svc" <<<"$out"
    check "postgres: $svc is included" $? "$out"
done

# ─── A leaf has no dependents, and nothing invents any ───────────────────────
out="$(compose_dependents smartrag-weaviate "$SANDBOX/compose.sh")"
check "a service nobody depends on yields nothing" $([[ -z "$out" ]] && echo 0 || echo 1) "$out"

# The service itself must never appear in its own list — restarting it twice
# is harmless but reads as a bug, and a cycle must not hang the sweep.
out="$(compose_dependents smartrag-redis "$SANDBOX/compose.sh")"
grep -qx 'smartrag-redis' <<<"$out"
check "the service is not listed as its own dependent" $(( $? == 0 ? 1 : 0 )) "$out"

CYCLE='cat <<JSON
{"services":{"a":{"depends_on":{"b":{}}},"b":{"depends_on":{"a":{}}}}}
JSON'
make_stub "$CYCLE"
timeout 20 bash -c "source '$REPO/scripts/lib/common.sh'; compose_dependents a '$SANDBOX/compose.sh'" >/dev/null 2>&1
check "a dependency cycle terminates instead of hanging" $? "timed out"

# ─── Degrading, not guessing ─────────────────────────────────────────────────
# If Compose cannot be asked, the answer is "none known" and the caller
# restarts only what it was told to — never a guess at the graph.
make_stub ""
out="$(compose_dependents smartrag-redis "$SANDBOX/compose.sh")"
check "an unavailable config yields nothing rather than a guess" \
      $([[ -z "$out" ]] && echo 0 || echo 1) "$out"

# ─── Wiring: offered, not silently done ──────────────────────────────────────
block="$(sed -n '/^action_restart()/,/^}/p' "$REPO/scripts/admin.sh")"
grep -q 'compose_dependents' <<<"$block"
check "the restart action looks the dependents up" $? ""
grep -q 'confirm admin_restart_dependents_confirm' <<<"$block"
check "and asks before restarting them" $? \
      "restarting Postgres would otherwise sweep half the stack along unasked"
grep -q 'admin_restart_dependents_skipped' <<<"$block"
check "declining says what the consequence is" $? ""

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All dependent-restart checks passed: direct and transitive dependents are"
echo "found from Compose's own normalised config, a leaf yields none, a service"
echo "never lists itself, a cycle terminates rather than hanging, an"
echo "unavailable config degrades to 'none known' instead of a guess, and the"
echo "restart is offered with its consequence rather than performed unasked."
