#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# Memory limits: on the services where a runaway is possible, and nowhere else
# ═════════════════════════════════════════════════════════════════════════════
#
# No container had a limit, so any single service could push a 16 GB machine
# into swap and let the kernel pick a victim — not necessarily the culprit.
#
# The fix is deliberately not "a limit on everything". A limit is a kill
# threshold: set one below what a service legitimately needs and the kernel
# stops it, which is worse than no limit at all. So limits go where the peak
# is driven by input rather than by accumulated data, and the stateful stores
# are left alone on purpose — that choice is asserted here so it stays a
# decision rather than an omission someone "fixes" later.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$REPO/docker/docker-compose.yml"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

svc_block() { sed -n "/^  $1:/,/^  smartrag-[a-z]/p" "$COMPOSE"; }

# ─── Limited: input-driven peaks, restart-safe ───────────────────────────────
for svc in smartrag-docling smartrag-clickhouse; do
    grep -qE '^\s+mem_limit:' <<<"$(svc_block "$svc")"
    check "$svc has a memory limit" $? "an unbounded peak here takes the host with it"
done

# Docling's must be generous — it is the one that converts arbitrary uploads,
# and killing a conversion that would have finished is its own failure.
dl="$(svc_block smartrag-docling | grep -oE 'mem_limit: *[0-9]+[gm]' | grep -oE '[0-9]+[gm]')"
[[ "$dl" == *g ]] && (( ${dl%g} >= 3 ))
check "docling's limit is generous, not tight" $? "got '$dl' — a large scan needs room"

# ─── Neo4j: the page cache is configuration, not a property of the host ──────
grep -q 'NEO4J_server_memory_pagecache__size' "$COMPOSE"
check "neo4j's page cache is capped" $? \
      "uncapped, Neo4j sizes it from whatever RAM it detects"
grep -q 'NEO4J_server_memory_heap_max__size' "$COMPOSE"
check "neo4j's heap is still capped" $? ""

# ─── Deliberately unlimited: memory grows with the data ──────────────────────
# Postgres, Weaviate and MinIO grow with what the course contains. A limit
# would not prevent a runaway, it would schedule an outage for whenever the
# index outgrew the number someone picked today. Redis is separate: it is
# Flowise's job queue, and a container limit without an eviction policy means
# the kernel kills it instead of Redis dropping keys — losing queued work.
for svc in smartrag-postgres smartrag-weaviate smartrag-minio smartrag-redis; do
    grep -qE '^\s+mem_limit:' <<<"$(svc_block "$svc")"
    check "$svc is deliberately left unlimited" $(( $? == 0 ? 1 : 0 )) \
          "a limit here fails when the data grows, not when something runs away"
done

# ─── The limits must fit the documented minimum ──────────────────────────────
# Limits are ceilings, not reservations, so their sum may exceed RAM — but if
# the two capped services alone could exceed the documented 8 GB minimum, the
# numbers are not defensible.
total=0
while read -r v; do
    n="${v%[gm]}"
    [[ "$v" == *g ]] && n=$(( n * 1024 ))
    total=$(( total + n ))
done < <(grep -oE 'mem_limit: *[0-9]+[gm]' "$COMPOSE" | grep -oE '[0-9]+[gm]')
(( total <= 6144 ))
check "the capped services fit inside the documented minimum" $? "${total} MB of limits"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All memory-limit checks passed: the two services whose peak is driven by"
echo "input are capped and docling's cap is generous rather than tight, Neo4j's"
echo "page cache is configuration instead of a function of host RAM, the four"
echo "stores whose memory grows with the data are left unlimited on purpose,"
echo "and the caps together fit the documented 8 GB minimum."
