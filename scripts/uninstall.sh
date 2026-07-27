#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Uninstall (coexistence-safe)
# ═════════════════════════════════════════════════════════════════════════════
#
# Removes what SMART RAG itself created — and ONLY that. Follows the same
# philosophy as the rest of the bootstrap: never touch anything that might be
# shared with other services on this host.
#
# ALWAYS removed (safe, reversible — nothing here is unique/hard to recreate):
#   - smartrag-* Docker containers and the smart-rag-network
#   - /etc/nginx/sites-{available,enabled}/smartrag-{suite,acme}.conf
#
# NEVER touched, regardless of flags (shared infrastructure):
#   - nginx, certbot, Postfix, Docker themselves (the packages/services)
#   - proxy-network (external, documented as managed independently)
#   - any other site's nginx config
#
# OPTIONAL, each requires its own flag or an explicit interactive confirm:
#   --purge-certs    delete the Let's Encrypt certificate (via certbot delete)
#   --purge-secrets  delete .env + credentials.txt (backed up first, cheap to keep)
#   --purge-data     delete BASE_DATA_PATH — ALL course data, chat history,
#                    vector embeddings, everything. Real `rm -rf`, not backed
#                    up (can be many GB) — requires typing DELETE to confirm,
#                    even with --yes on the other flags.
#
# Usage:
#   sudo bash scripts/uninstall.sh [--lang en|de] [--dry-run] [--yes]
#                                  [--purge-certs] [--purge-secrets] [--purge-data]
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
DRY_RUN=0
ASSUME_YES=0
PURGE_CERTS=0
PURGE_SECRETS=0
PURGE_DATA=0
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        --dry-run) DRY_RUN=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --purge-certs) PURGE_CERTS=1 ;;
        --purge-secrets) PURGE_SECRETS=1 ;;
        --purge-data) PURGE_DATA=1 ;;
        --help|-h)
            awk '/^# Usage:/{f=1} f && /^# ════/{exit} f{sub(/^# ?/, ""); print}' "${BASH_SOURCE[0]}"
            exit 0
            ;;
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

banner
header "$(t uninstall_title)"

if (( DRY_RUN )); then
    warn "$(t uninstall_dry_run)"
fi

# ─── Load .env if present (some steps need DOMAIN/BASE_DATA_PATH) ────────────
HAVE_ENV=0
if [[ -f "$REPO_ROOT/.env" ]]; then
    HAVE_ENV=1
    set -a
    # shellcheck source=/dev/null
    source "$REPO_ROOT/.env"
    set +a
fi

# ─── Summary of what will happen ──────────────────────────────────────────────
printf "  ${BOLD}%s${RESET}\n" "$(t uninstall_always_title)"
printf "    • %s\n" "$(t uninstall_always_containers)"
printf "    • %s\n" "$(t uninstall_always_nginx)"
echo
printf "  ${BOLD}%s${RESET}\n" "$(t uninstall_never_title)"
printf "    • %s\n" "$(t uninstall_never_list)"
echo
printf "  ${BOLD}%s${RESET}\n" "$(t uninstall_optional_title)"
[[ $PURGE_CERTS   -eq 1 ]] && printf "    • %s\n" "$(t uninstall_optional_certs_on)"   || printf "    • %s\n" "$(t uninstall_optional_certs_off)"
[[ $PURGE_SECRETS -eq 1 ]] && printf "    • %s\n" "$(t uninstall_optional_secrets_on)" || printf "    • %s\n" "$(t uninstall_optional_secrets_off)"
[[ $PURGE_DATA    -eq 1 ]] && printf "    • %s\n" "$(t uninstall_optional_data_on)"    || printf "    • %s\n" "$(t uninstall_optional_data_off)"
echo

if (( ! ASSUME_YES )); then
    confirm uninstall_confirm_proceed "n" || die "$(t uninstall_aborted)"
fi

# ─── Safety snapshot — same mechanism used before any destructive bootstrap
# step. Cheap, and the one thing here that's actually hard to redo by hand. ──
if (( ! DRY_RUN )); then
    create_system_snapshot
fi

# ─── 1. Stop and remove containers + our own network ──────────────────────────
info "$(t uninstall_stopping_containers)"
COMPOSE_FILE="$REPO_ROOT/docker/docker-compose.yml"
if (( HAVE_ENV )) && [[ -f "$COMPOSE_FILE" ]]; then
    if (( DRY_RUN )); then
        dim "docker compose -f $COMPOSE_FILE --env-file $REPO_ROOT/.env down"
    else
        docker compose -f "$COMPOSE_FILE" --env-file "$REPO_ROOT/.env" down --remove-orphans 2>&1 \
            | sed 's/^/    /' || warn "$(t uninstall_compose_down_failed)"
    fi
else
    dim "$(t uninstall_no_env_skip_compose)"
fi

# Defensive sweep — catches any smartrag-* container compose didn't know
# about (e.g. a stale one from a broken previous run, or .env missing above).
mapfile -t stray_containers < <(docker ps -a --format '{{.Names}}' 2>/dev/null | grep '^smartrag-' || true)
if (( ${#stray_containers[@]} > 0 )); then
    info "$(t uninstall_removing_stray "${#stray_containers[@]}")"
    for c in "${stray_containers[@]}"; do
        if (( DRY_RUN )); then
            dim "docker rm -f $c"
        else
            docker rm -f "$c" >/dev/null 2>&1 || true
        fi
    done
fi

if docker network inspect smart-rag-network >/dev/null 2>&1; then
    if (( DRY_RUN )); then
        dim "docker network rm smart-rag-network"
    else
        docker network rm smart-rag-network >/dev/null 2>&1 || true
    fi
fi
ok "$(t uninstall_containers_done)"

# ─── 2. Remove OUR nginx configs only (never touch anything else) ────────────
info "$(t uninstall_removing_nginx)"
NGINX_FILES=(
    /etc/nginx/sites-enabled/smartrag-suite.conf
    /etc/nginx/sites-available/smartrag-suite.conf
    /etc/nginx/sites-enabled/smartrag-acme.conf
    /etc/nginx/sites-available/smartrag-acme.conf
)
for f in "${NGINX_FILES[@]}"; do
    if [[ -e "$f" || -L "$f" ]]; then
        if (( DRY_RUN )); then
            dim "rm -f $f"
        else
            rm -f "$f"
        fi
    fi
done
if (( ! DRY_RUN )) && command -v nginx >/dev/null 2>&1; then
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || true
    else
        warn "$(t uninstall_nginx_test_failed)"
    fi
fi
ok "$(t uninstall_nginx_done)"

# ─── 3. Optional: Let's Encrypt certificate ───────────────────────────────────
if (( PURGE_CERTS )) && (( HAVE_ENV )) && [[ -n "${DOMAIN:-}" ]]; then
    CERT_NAME="smartrag-$DOMAIN"
    if command -v certbot >/dev/null 2>&1 && certbot certificates 2>/dev/null | grep -q "$CERT_NAME"; then
        if (( DRY_RUN )); then
            dim "certbot delete --cert-name $CERT_NAME --non-interactive"
        else
            certbot delete --cert-name "$CERT_NAME" --non-interactive 2>&1 | sed 's/^/    /' || true
        fi
        ok "$(t uninstall_certs_done "$CERT_NAME")"
    else
        dim "$(t uninstall_certs_none)"
    fi
elif (( PURGE_CERTS )); then
    warn "$(t uninstall_certs_no_domain)"
fi

# ─── 4. Optional: .env + credentials.txt (backed up, not hard-deleted) ───────
if (( PURGE_SECRETS )); then
    BACKUP_DIR="/var/backups/smartrag-uninstall-$(date +%Y%m%dT%H%M%S)"
    found_secrets=0
    for f in "$REPO_ROOT/.env" "$REPO_ROOT/credentials.txt"; do
        [[ -f "$f" ]] || continue
        found_secrets=1
        if (( DRY_RUN )); then
            dim "mv $f $BACKUP_DIR/"
        else
            mkdir -p "$BACKUP_DIR"
            chmod 700 "$BACKUP_DIR"
            mv "$f" "$BACKUP_DIR/"
        fi
    done
    if (( found_secrets )); then
        ok "$(t uninstall_secrets_done "$BACKUP_DIR")"
    else
        dim "$(t uninstall_secrets_none)"
    fi
fi

# ─── 5. Optional: BASE_DATA_PATH — real deletion, strong confirmation ────────
if (( PURGE_DATA )); then
    if (( ! HAVE_ENV )) || [[ -z "${BASE_DATA_PATH:-}" ]]; then
        warn "$(t uninstall_data_no_path)"
    elif [[ ! -d "$BASE_DATA_PATH" ]]; then
        dim "$(t uninstall_data_none "$BASE_DATA_PATH")"
    else
        warn "$(t uninstall_data_warning "$BASE_DATA_PATH")"
        proceed_purge=1
        if (( ! ASSUME_YES )); then
            proceed_purge=0
            # Deliberately NOT using prompt() here — it rejects empty input
            # as "value required" and loops, which would trap someone who
            # just wants to bail via a blank Enter. Any single read, ANY
            # input other than the exact phrase cancels, no retry loop.
            printf "  %s: " "$(t uninstall_data_confirm_phrase)" >&2
            IFS= read -r typed || typed=""
            [[ "$typed" == "DELETE" ]] && proceed_purge=1
        fi
        if (( proceed_purge )); then
            if (( DRY_RUN )); then
                dim "rm -rf $BASE_DATA_PATH"
            else
                rm -rf "${BASE_DATA_PATH:?}"
                ok "$(t uninstall_data_done "$BASE_DATA_PATH")"
            fi
        else
            warn "$(t uninstall_data_skipped)"
        fi
    fi
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
header "$(t uninstall_complete)"
if (( DRY_RUN )); then
    info "$(t uninstall_dry_run_done)"
else
    ok "$(t uninstall_done_summary)"
fi
echo "  $(t uninstall_kept_note)"
