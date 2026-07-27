#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Start Docker services and wait for health (Phase 7)
# ═════════════════════════════════════════════════════════════════════════════
#
# Pulls images, starts services according to COMPOSE_PROFILES, waits for the
# critical ones to report healthy.
#
# Usage:  sudo bash scripts/start-services.sh [--lang en|de] [--no-pull]
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: bash >= 4 required" >&2; exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck source=lib/common.sh
source "$LIB_DIR/common.sh"
# shellcheck source=lib/messages.sh
source "$LIB_DIR/messages.sh"

# ─── Arg parsing ─────────────────────────────────────────────────────────────
DO_PULL=1
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        --no-pull) DO_PULL=0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
LANG_CHOICE="${LANG_CHOICE:-$(detect_default_language)}"
export LANG_CHOICE

# ─── Load .env ───────────────────────────────────────────────────────────────
[[ -f "$REPO_ROOT/.env" ]] || die "$(t orch_phase1_needed)"
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

require_command docker

# ─── Determine which containers must be healthy ──────────────────────────────
# Always required (profile core):
CORE_SERVICES=(
    smartrag-postgres
    smartrag-redis
    smartrag-minio
    smartrag-weaviate
    smartrag-neo4j
    smartrag-flowise
    smartrag-n8n
)
EXTRA_SERVICES=()
[[ "${COMPOSE_PROFILES:-core}" == *observability* ]] && EXTRA_SERVICES+=(
    smartrag-clickhouse
    smartrag-langfuse-web
)
[[ "${COMPOSE_PROFILES:-core}" == *lti* ]] && EXTRA_SERVICES+=(smartrag-lti)

ALL_SERVICES=("${CORE_SERVICES[@]}" "${EXTRA_SERVICES[@]}")

header "$(t phase_services)"

# ─── Ensure external networks exist ──────────────────────────────────────────
# proxy-network is declared external in docker-compose so the user has full
# control over its lifecycle. We create it if it doesn't exist (idempotent —
# `docker network create` will fail loudly if there's already a network with
# that name but different settings, which we then surface).
if ! docker network inspect proxy-network >/dev/null 2>&1; then
    info "Creating external Docker network: proxy-network"
    docker network create proxy-network >/dev/null
fi

# ─── Ensure data directories exist with correct ownership ────────────────────
# `docker compose up` auto-creates missing bind-mount host directories, but
# as root:root — fine for images that self-chown on startup (postgres,
# clickhouse, ...) when they're first run as root, but images that run
# internally as a fixed non-root user out of the box (no root-then-chown
# entrypoint dance) can't write into a root-owned directory and crash-loop
# with an EACCES deep inside their own startup, invisible to any check we
# run beforehand. Pre-create + chown before compose ever touches them.
info "$(t svc_preparing_data_dirs)"
mkdir -p "${BASE_DATA_PATH}/n8n/data"
chown -R 1000:1000 "${BASE_DATA_PATH}/n8n/data"   # n8n image runs as user "node" (uid 1000)

# ─── Pull images (skip with --no-pull) ───────────────────────────────────────
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"
if (( DO_PULL )); then
    info "$(t svc_pulling)"
    docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" pull --quiet
fi

# ─── Start services ──────────────────────────────────────────────────────────
info "$(t svc_starting "${COMPOSE_PROFILES:-core}")"
docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" up -d --remove-orphans

# ─── Wait for health ─────────────────────────────────────────────────────────
# Polls `docker inspect` for each container's health status.
# Timeout per service: 180s. Health checks run every 30s in compose,
# so 180s = up to 6 ticks of grace.
wait_for_healthy() {
    local container="$1"
    local timeout="${2:-180}"
    local elapsed=0
    while (( elapsed < timeout )); do
        local status
        status="$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")"
        case "$status" in
            healthy)
                ok "$(t svc_healthy "$container")"
                return 0
                ;;
            starting|"")
                # still booting — keep waiting
                ;;
            unhealthy)
                err "$(t svc_unhealthy "$container" "$elapsed")"
                docker logs --tail 20 "$container" 2>&1 | sed 's/^/    /'
                return 1
                ;;
            missing)
                # Container has no healthcheck — just verify it's running
                if docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null | grep -qx running; then
                    ok "$(t svc_healthy "$container") (no healthcheck — running)"
                    return 0
                fi
                ;;
        esac
        sleep 5
        elapsed=$((elapsed+5))
    done
    err "$(t svc_unhealthy "$container" "$timeout")"
    docker logs --tail 20 "$container" 2>&1 | sed 's/^/    /'
    return 1
}

info "$(t svc_waiting)"
all_ok=1
for svc in "${ALL_SERVICES[@]}"; do
    wait_for_healthy "$svc" 180 || all_ok=0
done

# ─── Summary ─────────────────────────────────────────────────────────────────
header "$(t svc_status)"
docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" ps

if (( all_ok )); then
    ok "$(t svc_all_healthy)"
    ok "$(t svc_done)"
    exit 0
else
    die "One or more services did not become healthy."
fi
