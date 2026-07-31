#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Admin TUI (day-to-day operations)
# ═════════════════════════════════════════════════════════════════════════════
#
# A raspi-config-style whiptail menu for operating an already-deployed SMART
# RAG instance: status, logs, updates, restarts, SSL, mail test, DNS check,
# secrets overview, uninstall. Everything here runs directly on the host as
# root (via SSH) — no new container, no network exposure, no Docker-socket-
# in-a-container privilege question. Content-authoring (agent prompts, RAG
# documents, knowledge graph) is intentionally NOT here — see the project
# plan for why that's a separate, later, web-based piece instead.
#
# On first run, offers to install itself as a global `smartrag` command
# (symlink to /usr/local/bin/smartrag) so you don't need the full path
# again. Safe to keep calling directly instead.
#
# Usage:  sudo bash scripts/admin.sh [--lang en|de]
#     or, once installed:  sudo smartrag
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: bash >= 4 required" >&2; exit 1
fi

# Resolve the REAL location of this file, following symlinks — needed because
# this is normally invoked via the /usr/local/bin/smartrag symlink, and a
# plain "dirname ${BASH_SOURCE[0]}" would then resolve to /usr/local/bin
# instead of the actual scripts/ directory inside the repo.
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck source=lib/common.sh
source "$LIB_DIR/common.sh"
# shellcheck source=lib/messages.sh
source "$LIB_DIR/messages.sh"
# shellcheck source=lib/preflight.sh
source "$LIB_DIR/preflight.sh"
# shellcheck source=lib/config-wizard.sh
source "$LIB_DIR/config-wizard.sh"

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

require_command whiptail
require_command docker

# ─── Load .env ───────────────────────────────────────────────────────────────
[[ -f "$REPO_ROOT/.env" ]] || die "$(t admin_env_missing)"
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

# ─── Self-install as a global command ─────────────────────────────────────────
# Whiptail's own OK/Cancel button labels stay English regardless of
# LANG_CHOICE — a minor, accepted cosmetic wrinkle, not worth chasing here.
if [[ ! -e /usr/local/bin/smartrag ]]; then
    if confirm admin_install_offer "y"; then
        if ln -sf "$SELF" /usr/local/bin/smartrag 2>/dev/null; then
            ok "$(t admin_install_done)"
        else
            warn "$(t admin_install_failed)"
        fi
    fi
fi

# ─── Shared helpers ────────────────────────────────────────────────────────────

# Echoes the smartrag-* container names relevant to the currently-enabled
# COMPOSE_PROFILES — same set start-services.sh waits on.
_active_services() {
    local services=(
        smartrag-postgres smartrag-redis smartrag-minio smartrag-weaviate
        smartrag-neo4j smartrag-flowise smartrag-flowise-worker smartrag-n8n
        smartrag-content-admin
    )
    [[ "${COMPOSE_PROFILES:-core}" == *observability* ]] && services+=(
        smartrag-clickhouse smartrag-langfuse-web smartrag-langfuse-worker
    )
    [[ "${COMPOSE_PROFILES:-core}" == *lti* ]] && services+=(smartrag-lti)
    printf '%s\n' "${services[@]}"
}

# whiptail menu to pick one service. Echoes the chosen name, returns 1 on Cancel/ESC.
pick_service() {
    local svc menu_args=()
    while IFS= read -r svc; do
        menu_args+=("$svc" "$svc")
    done < <(_active_services)

    local choice
    if ! choice=$(whiptail --title "$(t admin_title)" --menu "$(t admin_pick_service)" \
        20 78 12 "${menu_args[@]}" 3>&1 1>&2 2>&3); then
        return 1
    fi
    printf '%s' "$choice"
}

press_enter() {
    echo
    dim "$(t admin_press_enter)"
    read -r _ || true
}

# ─── Actions ───────────────────────────────────────────────────────────────────

action_status() {
    clear
    header "$(t admin_status_title)"
    local svc status container_status
    while IFS= read -r svc; do
        status="$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "")"
        if [[ -n "$status" ]]; then
            case "$status" in
                healthy) ok "$svc: $status" ;;
                *)       warn "$svc: $status" ;;
            esac
            continue
        fi
        container_status="$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "")"
        if [[ -n "$container_status" ]]; then
            info "$svc: $container_status ($(t admin_status_no_healthcheck))"
        else
            err "$svc: $(t admin_status_not_found)"
        fi
    done < <(_active_services)
    press_enter
}

action_logs() {
    local svc
    svc="$(pick_service)" || return 0
    clear
    header "$(t admin_logs_title "$svc")"
    dim "$(t admin_logs_hint)"
    docker logs -f --tail 50 "$svc" || true
    press_enter
}

action_update() {
    clear
    header "$(t admin_menu_update)"
    if bash "$SCRIPT_DIR/compose.sh" pull && bash "$SCRIPT_DIR/compose.sh" up -d; then
        ok "$(t admin_update_done)"
    else
        err "$(t admin_update_failed)"
    fi
    press_enter
}

action_restart() {
    local svc
    svc="$(pick_service)" || return 0
    clear
    header "$(t admin_menu_restart)"
    if docker restart "$svc" >/dev/null; then
        ok "$(t admin_restart_done "$svc")"
    else
        err "$(t admin_restart_failed "$svc")"
    fi
    press_enter
}

action_ssl() {
    clear
    header "$(t admin_ssl_title)"
    certbot certificates 2>/dev/null | grep -A5 "smartrag-" || dim "$(t admin_ssl_none)"
    echo
    if confirm admin_ssl_renew_confirm "n"; then
        bash "$SCRIPT_DIR/get-ssl-certs.sh" --lang "$LANG_CHOICE" || err "$(t admin_ssl_renew_failed)"
    fi
    press_enter
}

action_mail() {
    clear
    header "$(t admin_mail_title)"
    if [[ -z "${SMTP_HOST:-}" ]]; then
        warn "$(t admin_mail_no_relay)"
    else
        info "$(t admin_mail_current "$SMTP_HOST" "${SMTP_PORT:-25}")"
        if [[ -n "${ADMIN_EMAIL:-}" ]]; then
            info "$(t admin_mail_target "$ADMIN_EMAIL")"
            if confirm admin_mail_test_confirm "y"; then
                bash "$SCRIPT_DIR/install-postfix.sh" --lang "$LANG_CHOICE" --test-only \
                    || err "$(t admin_mail_test_failed)"
            fi
        fi
    fi
    press_enter
}

action_dns() {
    clear
    header "$(t admin_dns_title)"
    local prefix="${SUBDOMAIN_PREFIX:-}"
    check_dns "$(subdomain_host smart-rag "$DOMAIN" "$prefix")"
    check_dns "$(subdomain_host n8n       "$DOMAIN" "$prefix")"
    check_dns "$(subdomain_host minio     "$DOMAIN" "$prefix")"
    check_dns "$(subdomain_host content   "$DOMAIN" "$prefix")"
    [[ "${COMPOSE_PROFILES:-core}" == *observability* ]] && check_dns "$(subdomain_host langfuse "$DOMAIN" "$prefix")"
    [[ "${COMPOSE_PROFILES:-core}" == *lti* ]]           && check_dns "$(subdomain_host lti       "$DOMAIN" "$prefix")"
    press_enter
}

action_secrets() {
    clear
    header "$(t admin_secrets_title)"
    local creds="$REPO_ROOT/credentials.txt" perms
    if [[ -f "$creds" ]]; then
        perms="$(stat -c '%a' "$creds" 2>/dev/null || echo "?")"
        info "$(t admin_secrets_path "$creds" "$perms")"
    else
        warn "$(t admin_secrets_no_file)"
    fi
    echo
    local keys=(
        POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ROOT_PASSWORD NEO4J_PASSWORD
        WEAVIATE_API_KEY ENCRYPTION_KEY JWT_AUTH_TOKEN_SECRET
        N8N_ENCRYPTION_KEY LLM_API_KEY EMBEDDING_API_KEY SMTP_PASSWORD
    )
    local k
    for k in "${keys[@]}"; do
        if [[ -n "${!k:-}" ]]; then
            ok "$k: $(t admin_secrets_set)"
        else
            dim "$k: $(t admin_secrets_unset)"
        fi
    done
    press_enter
}

# Recreates any container whose effective config changed (Compose diffs
# against the current .env automatically — we don't need to know which
# container is affected by which key).
_apply_config_change() {
    info "$(t admin_cfg_applying)"
    set -a
    # shellcheck source=/dev/null
    source "$REPO_ROOT/.env"
    set +a
    if bash "$SCRIPT_DIR/compose.sh" up -d; then
        ok "$(t admin_cfg_done)"
    else
        err "$(t admin_cfg_apply_failed)"
    fi
    press_enter
}

# Plain single-value edit: show current value as the default, prompt for a
# new one, patch .env, apply. Only for NON-secret values — the current value
# is shown on screen.
# Args: $1=ENV_KEY  $2=message-key for the question  $3=validator function (may be empty)
_cfg_simple() {
    local env_key="$1" msg_key="$2" validator="${3:-}"
    clear
    header "$(t admin_config_title)"
    local new
    new="$(prompt "$msg_key" "${!env_key:-}" "$validator")" || { press_enter; return 0; }
    set_env_var "$REPO_ROOT/.env" "$env_key" "$new"
    _apply_config_change
}

# Same, but for secrets: never displays the current value, only overwrites
# if the operator actually typed something (blank = keep unchanged).
# Args: $1=ENV_KEY  $2=message-key for the question  $3=optional message-key
#       for an extra note printed above the prompt (menu labels must stay
#       short — whiptail doesn't wrap them — so caveats go here instead)
_cfg_secret() {
    local env_key="$1" msg_key="$2" note_key="${3:-}"
    clear
    header "$(t admin_config_title)"
    [[ -n "$note_key" ]] && info "$(t "$note_key")"
    [[ -n "${!env_key:-}" ]] && info "$(t admin_cfg_secret_already_set)"
    local new
    new="$(prompt_password "$msg_key" "")" || { press_enter; return 0; }
    if [[ -z "$new" ]]; then
        info "$(t admin_cfg_secret_unchanged)"
        press_enter
        return 0
    fi
    set_env_var "$REPO_ROOT/.env" "$env_key" "$new"
    _apply_config_change
}

# Reuses the wizard's own mail-relay section (existing-relay detection,
# Postfix-vs-direct choice, etc.) instead of duplicating that logic —
# pre-populated from the current .env so its prompts show real defaults.
_cfg_mail() {
    clear
    header "$(t admin_config_title)"
    CFG_INSTALL_POSTFIX="${INSTALL_POSTFIX_RELAY:-false}"
    CFG_SMTP_RELAY_HOST="${SMTP_RELAY_HOST:-}"
    CFG_SMTP_RELAY_PORT="${SMTP_RELAY_PORT:-587}"
    CFG_SMTP_RELAY_USER="${SMTP_RELAY_USER:-}"
    CFG_SMTP_RELAY_PASSWORD="${SMTP_RELAY_PASSWORD:-}"
    CFG_SMTP_HOST="${SMTP_HOST:-}"
    CFG_SMTP_PORT="${SMTP_PORT:-587}"
    CFG_SMTP_SECURE="${SMTP_SECURE:-false}"
    CFG_SMTP_USER="${SMTP_USER:-}"
    CFG_SMTP_PASSWORD="${SMTP_PASSWORD:-}"

    if ! ask_mail_config; then
        info "$(t admin_cfg_cancelled)"
        press_enter
        return 0
    fi

    local env_file="$REPO_ROOT/.env"
    set_env_var "$env_file" INSTALL_POSTFIX_RELAY "$CFG_INSTALL_POSTFIX"
    set_env_var "$env_file" SMTP_RELAY_HOST "$CFG_SMTP_RELAY_HOST"
    set_env_var "$env_file" SMTP_RELAY_PORT "$CFG_SMTP_RELAY_PORT"
    set_env_var "$env_file" SMTP_RELAY_USER "$CFG_SMTP_RELAY_USER"
    set_env_var "$env_file" SMTP_RELAY_PASSWORD "$CFG_SMTP_RELAY_PASSWORD"
    set_env_var "$env_file" N8N_EMAIL_MODE "$CFG_N8N_EMAIL_MODE"
    set_env_var "$env_file" SMTP_HOST "$CFG_SMTP_HOST"
    set_env_var "$env_file" SMTP_PORT "$CFG_SMTP_PORT"
    set_env_var "$env_file" SMTP_USER "$CFG_SMTP_USER"
    set_env_var "$env_file" SMTP_PASSWORD "$CFG_SMTP_PASSWORD"
    set_env_var "$env_file" SMTP_SECURE "$CFG_SMTP_SECURE"
    set_env_var "$env_file" SMTP_CONNECTION_URL "$CFG_SMTP_CONNECTION_URL"

    if [[ "$CFG_INSTALL_POSTFIX" == "true" ]]; then
        info "$(t admin_cfg_mail_postfix_note)"
        bash "$SCRIPT_DIR/install-postfix.sh" --lang "$LANG_CHOICE" || warn "$(t admin_cfg_mail_postfix_failed)"
    fi

    _apply_config_change
}

action_config() {
    local items=(mail "$(t admin_cfg_mail)")
    items+=(reranker "$(t admin_cfg_reranker)")
    [[ "${COMPOSE_PROFILES:-core}" == *lti* ]] && items+=(lms "$(t admin_cfg_lms)")
    items+=(admin_email "$(t admin_cfg_admin_email)")
    items+=(tz "$(t admin_cfg_tz)")

    local choice
    if ! choice=$(whiptail --title "$(t admin_title)" --menu "$(t admin_cfg_menu_prompt)" \
        20 78 12 "${items[@]}" 3>&1 1>&2 2>&3); then
        return 0
    fi

    case "$choice" in
        mail)        _cfg_mail ;;
        reranker)    _cfg_secret RERANKER_API_KEY cfg_reranker_api_key admin_cfg_reranker_note ;;
        lms)         _cfg_simple LMS_URL cfg_lms_url validate_url ;;
        admin_email) _cfg_simple ADMIN_EMAIL cfg_admin_email validate_email ;;
        tz)          _cfg_simple TZ cfg_tz "" ;;
    esac
}

action_uninstall() {
    clear
    header "$(t admin_menu_uninstall)"
    bash "$SCRIPT_DIR/uninstall.sh" --lang "$LANG_CHOICE" || true
    press_enter
}

# ─── Main loop ─────────────────────────────────────────────────────────────────
while true; do
    choice=""
    if ! choice=$(whiptail --title "$(t admin_title)" --menu "$(t admin_menu_prompt)" \
        20 78 10 \
        "1"  "$(t admin_menu_status)" \
        "2"  "$(t admin_menu_logs)" \
        "3"  "$(t admin_menu_update)" \
        "4"  "$(t admin_menu_restart)" \
        "5"  "$(t admin_menu_ssl)" \
        "6"  "$(t admin_menu_mail)" \
        "7"  "$(t admin_menu_dns)" \
        "8"  "$(t admin_menu_secrets)" \
        "9"  "$(t admin_menu_config)" \
        "10" "$(t admin_menu_uninstall)" \
        "11" "$(t admin_menu_exit)" \
        3>&1 1>&2 2>&3); then
        clear
        break
    fi

    case "$choice" in
        1)  action_status ;;
        2)  action_logs ;;
        3)  action_update ;;
        4)  action_restart ;;
        5)  action_ssl ;;
        6)  action_mail ;;
        7)  action_dns ;;
        8)  action_secrets ;;
        9)  action_config ;;
        10) action_uninstall ;;
        11) clear; break ;;
    esac
done
