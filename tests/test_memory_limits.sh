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
# The variable NAME is the whole risk here, not its presence. Neo4j maps
# NEO4J_<name> by turning "_" into "." and "__" into a literal underscore, so
# the underscore count encodes which setting is meant — and Neo4j refuses to
# start on a name it does not recognise rather than ignoring it.
#
# Written with two underscores first, this produced server.memory.pagecache_
# size, which does not exist, and the next install crash-looped. The previous
# version of this test asserted the setting was present and passed against
# exactly that. So: assert the spellings, and assert the wrong one is absent.
declare -A NEO4J_EXPECTED=(
    # real setting                    → expected variable
    ["server.memory.heap.initial_size"]="NEO4J_server_memory_heap_initial__size"
    ["server.memory.heap.max_size"]="NEO4J_server_memory_heap_max__size"
    ["server.memory.pagecache.size"]="NEO4J_server_memory_pagecache_size"
)
for setting in "${!NEO4J_EXPECTED[@]}"; do
    var="${NEO4J_EXPECTED[$setting]}"
    grep -qE "^\s+$var:" "$COMPOSE"
    check "$setting is set as $var" $? "Neo4j will not start on an unknown setting name"
done
# The spelling that broke it must not come back.
grep -qE '^\s+NEO4J_server_memory_pagecache__size:' "$COMPOSE"
check "the two-underscore pagecache spelling is gone" $(( $? == 0 ? 1 : 0 )) \
      "that maps to server.memory.pagecache_size, which does not exist"

# Every NEO4J_ variable must be one this test knows about — a new one added
# without checking its underscore count is the same mistake again.
while read -r var; do
    known=0
    for setting in "${!NEO4J_EXPECTED[@]}"; do
        [[ "${NEO4J_EXPECTED[$setting]}" == "$var" ]] && known=1
    done
    # AUTH is not a setting mapping, it is the image's own bootstrap variable.
    [[ "$var" == "NEO4J_AUTH" ]] && known=1
    (( known ))
    check "$var is a verified spelling" $? "add it to NEO4J_EXPECTED after checking the real setting name"
done < <(grep -oE '^\s+NEO4J_[A-Za-z_]+' "$COMPOSE" | tr -d ' ' | sort -u)

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
