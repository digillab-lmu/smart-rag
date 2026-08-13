#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Repair: file chat history under the course it came from
# ═════════════════════════════════════════════════════════════════════════════
#
# Two versions of chathistory-sync wrote the wrong course, and both left their
# traces in Weaviate:
#
#   * Before the course lookup existed, every message was stamped with
#     $env.COURSE_ID — the installation's one course. On an installation that
#     has since gained a second course, the first course's name sits on the
#     second course's conversations.
#   * After the lookup was added, "Prepare messages" looked the course up,
#     used it to decide whether to skip the message, and then built its output
#     object without it. Those messages were written with no course at all,
#     and no agent can see them.
#
# One rule repairs both, and it reads the truth rather than assuming it: a
# ChatHistory object carries the id of the Flowise message it came from
# (trace_id), that message names its chatflow, and a chatflow belongs to
# exactly one slot of one course. Where that chain is complete, course_id is
# set from it.
#
# Where it is not — the Flowise message has been deleted, or its chatflow is
# in no slot — the object is left exactly as it is and counted. That is the
# important half: an installation's older conversations usually come from
# chatflows nobody has assigned to a slot, and guessing a course for them
# would move one course's conversations into another. Untouched and counted
# is the honest outcome.
#
# Nothing but course_id is written: PATCH merges, so every other property
# stays, and the vector is not recomputed — the course is metadata, not
# content.
#
# Usage:
#   sudo bash scripts/repair-chathistory-course.sh            # show, change nothing
#   sudo bash scripts/repair-chathistory-course.sh --apply    # write
#
# Nothing happens without --apply. This script can overwrite a course that is
# already set, and a wrong overwrite is not visible in any status output — it
# is found by someone reading another course's conversations.
#
# ChatHistory only. UserMemory is written by a different workflow that
# summarises per learner, and it needs its own answer.
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

APPLY=0
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        --apply) APPLY=1 ;;
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
require_command docker

CLASS="ChatHistory"
PG_CONTAINER="smartrag-postgres"
WEAVIATE_URL="http://127.0.0.1:${WEAVIATE_HTTP_PORT:-8080}"
AUTH=(-H "Authorization: Bearer ${WEAVIATE_API_KEY:-}")

header "$(t repair_chat_title)"
echo "$(t repair_chat_intro)"
echo
(( APPLY )) || warn "$(t repair_chat_dry_run)"
echo

curl -sf --max-time 10 "${AUTH[@]}" "$WEAVIATE_URL/v1/.well-known/ready" >/dev/null \
    || die "$(t repair_chat_unreachable "$WEAVIATE_URL")"

curl -s --max-time 10 "${AUTH[@]}" "$WEAVIATE_URL/v1/schema/$CLASS" \
    | jq -e '.class' >/dev/null 2>&1 \
    || die "$(t repair_chat_class_absent "$CLASS")"

# ─── The two lookups, read from the systems that own them ────────────────────
# psql's own errors are left on stderr rather than swallowed: a query that
# fails and one that returns nothing look identical once the output is
# discarded, and the difference is the whole diagnosis.
psql_query() {   # $1 = database, $2 = SQL
    docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD:-}" "$PG_CONTAINER" \
        psql -U "${POSTGRES_USER:?POSTGRES_USER is not set in .env}" \
             -d "$1" -At -F '|' -c "$2"
}

docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER" \
    || die "$(t repair_chat_no_postgres "$PG_CONTAINER")"

declare -A COURSE_OF_CHATFLOW=()
while IFS='|' read -r chatflow course; do
    [[ -n "$chatflow" && -n "$course" ]] && COURSE_OF_CHATFLOW["$chatflow"]="$course"
done < <(psql_query contentadmin \
    "SELECT chatflow_id, course_id FROM agent_slots WHERE chatflow_id IS NOT NULL")

(( ${#COURSE_OF_CHATFLOW[@]} )) || die "$(t repair_chat_no_slots)"

declare -A CHATFLOW_OF_MESSAGE=()
while IFS='|' read -r message_id chatflow; do
    [[ -n "$message_id" && -n "$chatflow" ]] && CHATFLOW_OF_MESSAGE["$message_id"]="$chatflow"
done < <(psql_query flowise "SELECT id, chatflowid FROM chat_message")

# An empty result and a query that could not run look the same from here, and
# the difference matters: with no messages every object would be counted as
# "its chat is gone" and the run would report a clean nothing-to-do.
(( ${#CHATFLOW_OF_MESSAGE[@]} )) || die "$(t repair_chat_no_messages)"

info "$(t repair_chat_lookups "${#COURSE_OF_CHATFLOW[@]}" "${#CHATFLOW_OF_MESSAGE[@]}")"
echo

# ─── Walk every object ───────────────────────────────────────────────────────
# The cursor API rather than limit/offset: `after` stays stable while the
# sync writes new objects underneath the walk, which an offset does not.
total=0; unchanged=0; no_trace=0; gone=0; unmapped=0; would=0; patched=0; failed=0
declare -A MOVES=()
after=""

while :; do
    url="$WEAVIATE_URL/v1/objects?class=$CLASS&limit=100"
    [[ -n "$after" ]] && url+="&after=$after"
    page="$(curl -s --max-time 30 "${AUTH[@]}" "$url")" \
        || die "$(t repair_chat_read_failed)"

    # Joined with SOH, not a tab. Tab is IFS whitespace, so bash collapses a
    # run of them into one separator and drops empty fields: an object with
    # no trace_id but a course arrived as trace=<the course>, was looked up
    # as a message id, found nothing, and was reported as "its chat was
    # deleted" instead of "it has no trace_id". Two different situations,
    # both left untouched — but the operator was told the wrong one.
    mapfile -t rows < <(jq -r '.objects[]? | [.id,
                                              (.properties.trace_id   // ""),
                                              (.properties.course_id  // "")]
                               | join("\u0001")' <<<"$page")
    (( ${#rows[@]} )) || break

    last_id=""
    for row in "${rows[@]}"; do
        IFS=$'\001' read -r id trace current <<<"$row"
        last_id="$id"
        total=$((total + 1))

        # No trace_id: written by something other than the sync, or by a
        # version that predates the field. Nothing links it to a chat.
        if [[ -z "$trace" ]]; then
            no_trace=$((no_trace + 1)); continue
        fi
        chatflow="${CHATFLOW_OF_MESSAGE[$trace]:-}"
        if [[ -z "$chatflow" ]]; then
            gone=$((gone + 1)); continue
        fi
        target="${COURSE_OF_CHATFLOW[$chatflow]:-}"
        if [[ -z "$target" ]]; then
            unmapped=$((unmapped + 1)); continue
        fi
        if [[ "$current" == "$target" ]]; then
            unchanged=$((unchanged + 1)); continue
        fi

        # Keyed with the raw value, empty included: putting a translated
        # "(no course)" in the key would break the split below the moment a
        # translation contains the separator.
        MOVES["$current|$target"]=$(( ${MOVES["$current|$target"]:-0} + 1 ))

        if (( ! APPLY )); then
            would=$((would + 1)); continue
        fi
        if curl -sf --max-time 15 -X PATCH "${AUTH[@]}" \
                -H "Content-Type: application/json" \
                -d "$(jq -nc --arg c "$target" '{properties:{course_id:$c}}')" \
                "$WEAVIATE_URL/v1/objects/$CLASS/$id" >/dev/null; then
            patched=$((patched + 1))
        else
            failed=$((failed + 1))
            warn "$(t repair_chat_obj_failed "$id")"
        fi
    done

    [[ -n "$last_id" ]] || break
    after="$last_id"
done

# ─── What it found ───────────────────────────────────────────────────────────
echo
if (( ${#MOVES[@]} )); then
    echo "$(t repair_chat_moves_heading)"
    for key in "${!MOVES[@]}"; do
        IFS='|' read -r from to <<<"$key"
        [[ -n "$from" ]] || from="$(t repair_chat_no_course)"
        info "$(t repair_chat_move "$from" "$to" "${MOVES[$key]}")"
    done
    echo
fi

dim "$(t repair_chat_scanned "$total" "$unchanged")"
(( no_trace )) && dim "$(t repair_chat_no_trace "$no_trace")"
(( gone ))     && dim "$(t repair_chat_gone "$gone")"
(( unmapped )) && dim "$(t repair_chat_unmapped "$unmapped")"

echo
if (( ! APPLY )); then
    if (( would )); then
        warn "$(t repair_chat_would_change "$would")"
    else
        ok "$(t repair_chat_nothing_to_do)"
    fi
    exit 0
fi

if (( failed )); then
    die "$(t repair_chat_partly_failed "$patched" "$failed")"
fi
if (( patched )); then
    ok "$(t repair_chat_changed "$patched")"
else
    ok "$(t repair_chat_nothing_to_do)"
fi
