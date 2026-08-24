#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Deploy Weaviate + Neo4j schemas (Phase 8)
# ═════════════════════════════════════════════════════════════════════════════
#
# Weaviate: pushes each class from the staged, already-substituted schema
#   ($BASE_DATA_PATH/staging/weaviate-schema.json, written by Phase 4's
#   write_weaviate_schema()) via the REST API. Idempotent — classes that
#   already exist are skipped.
# Neo4j: applies neo4j/schema.cypher's uniqueness constraints via cypher-shell
#   (idempotent, IF NOT EXISTS). Optionally loads the example seed data.
#
# Requires smartrag-weaviate and smartrag-neo4j already running and healthy
# (normal case: called right after start-services.sh).
#
# Usage:  sudo bash scripts/deploy-schemas.sh [--lang en|de]
# Re-runnable any time — safe to use to re-push the schema after an edit.
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: bash >= 4 required" >&2; exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck source=lib/templates.sh — for write_weaviate_schema, used
# when the staged file is missing (see below).
# shellcheck source=lib/common.sh
source "$LIB_DIR/common.sh"
# shellcheck source=lib/messages.sh
source "$LIB_DIR/messages.sh"
# shellcheck source=lib/templates.sh
source "$LIB_DIR/templates.sh"

# ─── Arg parsing ─────────────────────────────────────────────────────────────
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
LANG_CHOICE="${LANG_CHOICE:-$(detect_default_language)}"
export LANG_CHOICE

# ─── Root check ──────────────────────────────────────────────────────────────
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "$(t pf_root_needed "$(basename "$0")")"
fi

# ─── Load .env ───────────────────────────────────────────────────────────────
[[ -f "$REPO_ROOT/.env" ]] || die "$(t orch_phase1_needed)"
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

require_command jq
require_command curl
require_command docker

header "$(t phase_schemas)"

# ─── Helper: is a container running+healthy? ──────────────────────────────────
_container_ready() { container_ready "$1"; }

# ─── Weaviate ─────────────────────────────────────────────────────────────────
_container_ready smartrag-weaviate || die "$(t schema_container_not_healthy "smartrag-weaviate")"

# Phase 4 stages this file, and it lives under BASE_DATA_PATH — the one
# directory an operator deletes on purpose when starting over. The .env sits in
# the repository and survives that, so "continue the deployment" then looks
# like a reasonable answer and dies here instead.
#
# Written rather than refused, because since courses stopped being an
# installation-wide setting this file is derived from weaviate/schema.json
# alone: it is the shared classes with the per-course template removed. There
# is nothing to ask anybody. Seen on a real install, 2026-08-24, one phase
# after the same shape of failure in Garage.
STAGED_SCHEMA="${BASE_DATA_PATH}/staging/weaviate-schema.json"
if [[ ! -f "$STAGED_SCHEMA" ]]; then
    warn "$(t schema_weaviate_restaging "$STAGED_SCHEMA")"
    write_weaviate_schema "$REPO_ROOT" "$STAGED_SCHEMA"
fi

WEAVIATE_URL="http://127.0.0.1:${WEAVIATE_HTTP_PORT}"
info "$(t schema_weaviate_checking)"

class_count="$(jq '.classes | length' "$STAGED_SCHEMA")"
for (( i=0; i<class_count; i++ )); do
    class_name="$(jq -r ".classes[$i].class" "$STAGED_SCHEMA")"
    http_code="$(curl -s -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer ${WEAVIATE_API_KEY}" \
        "$WEAVIATE_URL/v1/schema/$class_name")"

    if [[ "$http_code" == "200" ]]; then
        dim "$(t schema_weaviate_exists "$class_name")"
        continue
    fi

    info "$(t schema_weaviate_creating "$class_name")"
    class_body="$(jq -c ".classes[$i]" "$STAGED_SCHEMA")"
    create_code="$(curl -s -o /tmp/smartrag-weaviate-create-response.json -w '%{http_code}' \
        -X POST \
        -H "Authorization: Bearer ${WEAVIATE_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$class_body" \
        "$WEAVIATE_URL/v1/schema")"

    if [[ "$create_code" == "200" ]]; then
        ok "$(t schema_weaviate_created "$class_name")"
    else
        cat /tmp/smartrag-weaviate-create-response.json >&2 || true
        die "$(t schema_weaviate_failed "$class_name")"
    fi
    rm -f /tmp/smartrag-weaviate-create-response.json
done

# ─── Neo4j ─────────────────────────────────────────────────────────────────────
_container_ready smartrag-neo4j || die "$(t schema_container_not_healthy "smartrag-neo4j")"

info "$(t schema_neo4j_constraints)"
if docker exec -i smartrag-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" --format plain \
    < "$REPO_ROOT/neo4j/schema.cypher" >/dev/null; then
    ok "$(t schema_neo4j_constraints_done)"
else
    die "$(t schema_neo4j_constraints_failed)"
fi

if confirm schema_neo4j_seed_confirm "n"; then
    info "$(t schema_neo4j_seed_loading)"
    if docker exec -i smartrag-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" --format plain \
        < "$REPO_ROOT/neo4j/seed.example.cypher" >/dev/null; then
        ok "$(t schema_neo4j_seed_done)"
    else
        warn "$(t schema_neo4j_seed_failed)"
    fi
else
    dim "$(t schema_neo4j_seed_skipped)"
fi

ok "$(t schema_done)"
