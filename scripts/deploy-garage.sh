#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Garage layout, buckets and keys (deployment phase)
# ═════════════════════════════════════════════════════════════════════════════
#
# MinIO provisioned itself from its own entrypoint. Garage cannot: its image is
# FROM scratch and contains only the binary, so there is no shell to run a
# setup script in. Everything here therefore runs the binary directly through
# `docker exec`, from outside.
#
# Three things have to happen, in this order, and only the first has no MinIO
# equivalent at all:
#
#   1. A layout. Garage stores NOTHING until capacity is assigned to a node
#      and the layout is applied. A running, healthy Garage with no layout
#      accepts a connection and rejects every write.
#   2. Buckets.
#   3. Keys — imported with the ids and secrets this installation generated,
#      rather than letting Garage mint its own and reading them back. That
#      keeps .env the source of truth and the bootstrap declarative.
#
# Idempotent throughout: re-running finds the layout applied, the buckets
# present and the keys known, and says so instead of failing.
#
# Usage:  sudo bash scripts/deploy-garage.sh [--lang en|de]
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail

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

[[ -f "$REPO_ROOT/.env" ]] || die "$(t orch_phase1_needed)"
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

require_command docker

CONTAINER="smartrag-garage"
header "$(t phase_garage)"

g() { docker exec "$CONTAINER" /garage "$@" 2>&1; }

# ─── 1. Wait for the node ────────────────────────────────────────────────────
info "$(t garage_waiting)"
NODE_ID=""
for _ in $(seq 40); do
    out="$(g node id -q 2>/dev/null || true)"
    if [[ "$out" =~ ^[0-9a-f]{16,} ]]; then
        NODE_ID="${out%%@*}"
        break
    fi
    sleep 3
done
[[ -n "$NODE_ID" ]] || {
    err "$(t garage_no_node)"
    docker logs --tail 20 "$CONTAINER" 2>&1 | sed 's/^/    /'
    exit 1
}
ok "$(t garage_node_up "${NODE_ID:0:16}")"

# ─── 2. Layout ───────────────────────────────────────────────────────────────
# The step with no MinIO equivalent, and the one whose absence is invisible:
# without it Garage is healthy and refuses every write.
if g layout show 2>/dev/null | grep -qi "$NODE_ID"; then
    ok "$(t garage_layout_present)"
else
    info "$(t garage_layout_applying)"
    # Capacity is what this node contributes, not a quota — with one node and
    # a replication factor of 1 the number only has to be non-zero and
    # plausible for the disk.
    if ! g layout assign -z "${GARAGE_ZONE:-dc1}" -c "${GARAGE_CAPACITY:-100G}" "$NODE_ID" >/dev/null 2>&1; then
        err "$(t garage_layout_failed)"
        g layout show | sed 's/^/    /'
        exit 1
    fi
    # The version to apply is whatever the pending layout has become — hard-
    # coding 1 works exactly once and then fails for the rest of the
    # installation's life.
    next_version="$(g layout show 2>/dev/null | grep -oiE 'version [0-9]+' | grep -oE '[0-9]+' | tail -1)"
    next_version="${next_version:-1}"
    if g layout apply --version "$next_version" >/dev/null 2>&1; then
        ok "$(t garage_layout_applied "$next_version")"
    else
        err "$(t garage_layout_failed)"
        g layout show | sed 's/^/    /'
        exit 1
    fi
fi

# ─── 3. Buckets ──────────────────────────────────────────────────────────────
DOC_BUCKET="${COURSE_ID}-rag"
BUCKETS=("$DOC_BUCKET" langfuse-events langfuse-media langfuse-exports)
for b in "${BUCKETS[@]}"; do
    if g bucket info "$b" >/dev/null 2>&1; then
        ok "$(t garage_bucket_present "$b")"
    elif g bucket create "$b" >/dev/null 2>&1; then
        ok "$(t garage_bucket_created "$b")"
    else
        err "$(t garage_bucket_failed "$b")"
        exit 1
    fi
done

# ─── 4. Keys ─────────────────────────────────────────────────────────────────
# Imported, not generated: the ids and secrets are already in .env, every
# consumer is configured with them, and letting Garage mint its own would mean
# reading them back and rewriting the file the wizard just wrote.
import_key() {   # $1 = name, $2 = id, $3 = secret
    local name="$1" id="$2" secret="$3"
    [[ -n "$id" && -n "$secret" ]] || { err "$(t garage_key_missing "$name")"; return 1; }
    if g key info "$id" >/dev/null 2>&1; then
        ok "$(t garage_key_present "$name")"
        return 0
    fi
    if g key import "$id" "$secret" -n "$name" --yes >/dev/null 2>&1; then
        ok "$(t garage_key_imported "$name")"
        return 0
    fi
    err "$(t garage_key_failed "$name")"
    g key import "$id" "$secret" -n "$name" --yes | sed 's/^/    /'
    return 1
}

import_key smartrag  "${GARAGE_ACCESS_KEY:-}"          "${GARAGE_SECRET_KEY:-}"          || exit 1
import_key langfuse  "${GARAGE_LANGFUSE_ACCESS_KEY:-}" "${GARAGE_LANGFUSE_SECRET_KEY:-}" || exit 1

# ─── 5. Permissions ──────────────────────────────────────────────────────────
# Per key and per bucket — Garage has no root user and no bucket policies, so
# each key is granted only where it belongs. The ingest key cannot read
# Langfuse's traces and Langfuse cannot read course documents.
grant() {   # $1 = bucket, $2 = key id
    if g bucket allow --read --write --owner "$1" --key "$2" >/dev/null 2>&1; then
        ok "$(t garage_granted "$2" "$1")"
    else
        err "$(t garage_grant_failed "$2" "$1")"
        return 1
    fi
}
grant "$DOC_BUCKET" "$GARAGE_ACCESS_KEY" || exit 1
for b in langfuse-events langfuse-media langfuse-exports; do
    grant "$b" "$GARAGE_LANGFUSE_ACCESS_KEY" || exit 1
done

echo
ok "$(t garage_done)"
