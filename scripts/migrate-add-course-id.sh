#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Migration: add course_id to an existing Weaviate deployment
# ═════════════════════════════════════════════════════════════════════════════
#
# One installation can now host several courses. Every object that agents
# retrieve carries a `course_id`, and every agent's retrieval filters on it.
#
# A deployment created before that change has neither: the property does not
# exist on its classes, and its objects do not carry a value. Two things have
# to happen, and neither is done by deploy-schemas.sh — that script skips
# classes that already exist, on purpose, so it never rewrites a live schema.
#
#   1. Add the `course_id` property to each existing class.
#   2. Set it, on every existing object, to this installation's COURSE_ID.
#
# Both steps are idempotent: a property that exists is left alone, and an
# object that already carries a non-empty course_id is skipped. Running this
# twice changes nothing the second time.
#
# Usage:
#   sudo bash scripts/migrate-add-course-id.sh --dry-run   # show, change nothing
#   sudo bash scripts/migrate-add-course-id.sh             # apply
#   sudo bash scripts/migrate-add-course-id.sh --course-id other-course
#
# Until this has run, agents on an upgraded installation retrieve NOTHING:
# their filter asks for a course_id the stored objects don't have. That is
# deliberately visible rather than silent — a filter that quietly matched
# everything would leak one course's material into another's.
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

DRY_RUN=0
COURSE_ID_OVERRIDE=""
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        --dry-run) DRY_RUN=1 ;;
        --course-id) shift; COURSE_ID_OVERRIDE="${1:-}" ;;
        --course-id=*) COURSE_ID_OVERRIDE="${1#*=}" ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
LANG_CHOICE="${LANG_CHOICE:-$(detect_default_language)}"
export LANG_CHOICE

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "$(t pf_root_needed "$(basename "$0")")"
fi

[[ -f "$REPO_ROOT/.env" ]] || die "$(t orch_phase1_needed)"
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

require_command curl
require_command jq

header "$(t migrate_course_title)"

COURSE_ID="${COURSE_ID_OVERRIDE:-${COURSE_ID:-}}"
[[ -n "$COURSE_ID" ]] || die "$(t migrate_course_no_id)"

WEAVIATE_URL="http://127.0.0.1:${WEAVIATE_HTTP_PORT:-8080}"
AUTH=(-H "Authorization: Bearer ${WEAVIATE_API_KEY:-}")

# The retrieval collection is named per installation; the other three are
# fixed. Anything not present is skipped rather than treated as an error —
# TestResults only exists once the knowledge-test agent has been used.
CLASSES=("${WEAVIATE_COLLECTION_NAME:?WEAVIATE_COLLECTION_NAME is not set in .env}"
         "ChatHistory" "UserMemory" "TestResults")

info "$(t migrate_course_target "$COURSE_ID" "$WEAVIATE_URL")"
(( DRY_RUN )) && warn "$(t migrate_course_dry_run)"
echo

curl -sf --max-time 10 "${AUTH[@]}" "$WEAVIATE_URL/v1/.well-known/ready" >/dev/null \
    || die "$(t migrate_course_unreachable "$WEAVIATE_URL")"

# ─── 1. Schema ───────────────────────────────────────────────────────────────
for class in "${CLASSES[@]}"; do
    schema="$(curl -s --max-time 10 "${AUTH[@]}" "$WEAVIATE_URL/v1/schema/$class" 2>/dev/null || true)"
    if ! jq -e '.class' <<<"$schema" >/dev/null 2>&1; then
        dim "$(t migrate_course_class_absent "$class")"
        continue
    fi
    if jq -e '.properties[]? | select(.name == "course_id")' <<<"$schema" >/dev/null 2>&1; then
        dim "$(t migrate_course_prop_exists "$class")"
        continue
    fi
    if (( DRY_RUN )); then
        info "$(t migrate_course_prop_would_add "$class")"
        continue
    fi
    body='{"name":"course_id","dataType":["text"],"indexFilterable":true,"indexSearchable":false}'
    if curl -sf --max-time 15 -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
            -d "$body" "$WEAVIATE_URL/v1/schema/$class/properties" >/dev/null; then
        ok "$(t migrate_course_prop_added "$class")"
    else
        die "$(t migrate_course_prop_failed "$class")"
    fi
done

echo

# ─── 2. Existing objects ─────────────────────────────────────────────────────
# Paged with the cursor API rather than GraphQL: `after` is stable while
# objects are being written, which a limit/offset walk is not.
for class in "${CLASSES[@]}"; do
    curl -s --max-time 10 "${AUTH[@]}" "$WEAVIATE_URL/v1/schema/$class" 2>/dev/null \
        | jq -e '.class' >/dev/null 2>&1 || continue

    total=0; patched=0; skipped=0; after=""
    while :; do
        url="$WEAVIATE_URL/v1/objects?class=$class&limit=100&include=&"
        [[ -n "$after" ]] && url+="after=$after&"
        page="$(curl -s --max-time 30 "${AUTH[@]}" "${url%&}")" || break
        mapfile -t ids < <(jq -r '.objects[]?.id // empty' <<<"$page")
        (( ${#ids[@]} )) || break

        for id in "${ids[@]}"; do
            total=$((total + 1))
            existing="$(curl -s --max-time 10 "${AUTH[@]}" \
                "$WEAVIATE_URL/v1/objects/$class/$id" | jq -r '.properties.course_id // ""')"
            if [[ -n "$existing" ]]; then
                skipped=$((skipped + 1)); continue
            fi
            if (( DRY_RUN )); then
                patched=$((patched + 1)); continue
            fi
            # PATCH merges: every other property is left untouched, and the
            # vector is not recomputed — course_id is metadata, not content.
            if curl -sf --max-time 15 -X PATCH "${AUTH[@]}" -H "Content-Type: application/json" \
                    -d "$(jq -n --arg c "$COURSE_ID" '{properties:{course_id:$c}}')" \
                    "$WEAVIATE_URL/v1/objects/$class/$id" >/dev/null; then
                patched=$((patched + 1))
            else
                warn "$(t migrate_course_obj_failed "$class" "$id")"
            fi
        done
        after="${ids[-1]}"
    done

    if (( DRY_RUN )); then
        info "$(t migrate_course_would_patch "$class" "$patched" "$total")"
    else
        ok "$(t migrate_course_patched "$class" "$patched" "$total" "$skipped")"
    fi
done

echo
if (( DRY_RUN )); then
    warn "$(t migrate_course_dry_run_done)"
else
    ok "$(t migrate_course_done "$COURSE_ID")"
    dim "$(t migrate_course_next)"
fi
