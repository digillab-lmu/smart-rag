#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Bootstrap orchestrator
# ═════════════════════════════════════════════════════════════════════════════
#
# Default mode runs phases 1–4 (the configuration half):
#   1. Pre-flight checks (Ubuntu, Docker, internet)
#   2. Interactive configuration wizard      →  .env
#   3. Generate cryptographically secure secrets
#   4. Substitute templates (nginx, Weaviate schema, LTI config)
#
# Then set DNS A-records for all subdomains, then re-run with --continue:
#
# --continue mode runs phases 5–7 (the deployment half):
#   5. Install system packages (nginx, certbot, jq, ...)
#   6. Obtain SSL certificates via Let's Encrypt
#   7. Start Docker services and wait for them to be healthy
#
# Usage:
#   sudo bash scripts/bootstrap.sh             # phases 1–4 (interactive)
#   sudo bash scripts/bootstrap.sh --continue  # phases 5–7 (deployment)
#   sudo bash scripts/bootstrap.sh --lang en   # force English (or 'de')
#   sudo bash scripts/bootstrap.sh --help      # show this help
#
# Each of phases 5–7 can also be run standalone (idempotent):
#   sudo bash scripts/install-system-packages.sh
#   sudo bash scripts/get-ssl-certs.sh
#   sudo bash scripts/start-services.sh
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Bash version guard ──────────────────────────────────────────────────────
# We use associative arrays (declare -A) which require bash >= 4.
# Ubuntu 24.04 ships bash 5.2. macOS default bash is 3.2 — install via brew.
if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: bash >= 4 required (you have $BASH_VERSION)" >&2
    echo "       On macOS:   brew install bash  &&  /opt/homebrew/bin/bash $0" >&2
    echo "       On Ubuntu:  your bash should already be 5.x — try /bin/bash $0" >&2
    exit 1
fi

# ─── Locate ourselves + source libs ──────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# Log file (set BEFORE sourcing common.sh so log_to_file works from the start)
LOG_FILE="$REPO_ROOT/bootstrap.log"

# shellcheck source=lib/common.sh
source "$LIB_DIR/common.sh"
# shellcheck source=lib/messages.sh
source "$LIB_DIR/messages.sh"
# shellcheck source=lib/preflight.sh
source "$LIB_DIR/preflight.sh"
# shellcheck source=lib/secrets.sh
source "$LIB_DIR/secrets.sh"
# shellcheck source=lib/config-wizard.sh
source "$LIB_DIR/config-wizard.sh"
# shellcheck source=lib/templates.sh
source "$LIB_DIR/templates.sh"

# ─── Argument parsing ────────────────────────────────────────────────────────
SHOW_HELP=0
DRY_RUN=0
SKIP_PREFLIGHT=0
MODE="phase1"   # phase1 | continue

while (( $# > 0 )); do
    case "$1" in
        --lang)
            shift
            case "${1:-}" in
                en|de) LANG_CHOICE="$1" ;;
                *) die "Unknown language: $1 (use 'en' or 'de')" ;;
            esac
            ;;
        --lang=*)
            LANG_CHOICE="${1#*=}"
            ;;
        --help|-h)
            SHOW_HELP=1
            ;;
        --dry-run)
            DRY_RUN=1
            ;;
        --skip-preflight)
            SKIP_PREFLIGHT=1
            ;;
        --continue)
            MODE="continue"
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
    shift
done

if (( SHOW_HELP )); then
    # Extract the Usage block from the header comment.
    # Print all lines from "# Usage:" up to (but not including) the next "# ═══" line.
    awk '/^# Usage:/{f=1} f && /^# ════/{exit} f{sub(/^# ?/, ""); print}' "${BASH_SOURCE[0]}"
    exit 0
fi

# ─── Initialise log ──────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LOG_FILE")"
: > "$LOG_FILE"
chmod 600 "$LOG_FILE"
_log_to_file "─── bootstrap.sh started ───"
_log_to_file "argv: $*"
_log_to_file "user: $(id -un)  euid: ${EUID:-?}"
_log_to_file "pwd: $(pwd)"

# ─── Banner + language ───────────────────────────────────────────────────────
banner
select_language
check_translations >/dev/null 2>&1 || true

# ─── Detect a previous run ────────────────────────────────────────────────────
# Only relevant if --continue wasn't explicitly passed — if it was, the user
# already knows what they want and we respect that as before. Otherwise,
# running plain `bootstrap.sh` a second time on an already-configured (or
# already-running) deployment used to silently restart the full wizard —
# ask instead.
if [[ "$MODE" == "phase1" ]]; then
    PREV_STATE="$(detect_bootstrap_state "$REPO_ROOT")"
    if [[ "$PREV_STATE" != "none" ]]; then
        header "$(t prevrun_title)"
        case "$PREV_STATE" in
            configured) info "$(t prevrun_configured)" ;;
            running)    info "$(t prevrun_running)" ;;
        esac
        prevrun_choice="$(select_one_index prevrun_choice \
            "$(t prevrun_continue)" \
            "$(t prevrun_fresh)" \
            "$(t prevrun_abort)")" || exit 0
        case "$prevrun_choice" in
            1) MODE="continue" ;;
            2) info "$(t prevrun_fresh_note)" ;;   # falls through to the normal phase1 flow below
            3) exit 0 ;;
        esac
    fi
fi

# ─── Phases 5–7 (deployment half) — reusable, called from two places ────────
# 1. The --continue CLI flag (explicit, always respected as-is).
# 2. The end of phase 1 below, when DNS for every subdomain ALREADY resolves
#    correctly — no reason to make the user type a second command and wait.
run_deployment_phases() {
    info "$(t orch_continue_intro)"

    # Phase 1 must have run — .env is required
    if [[ ! -f "$REPO_ROOT/.env" ]]; then
        die "$(t orch_phase1_needed)"
    fi

    # Load .env so we can echo subdomain URLs at the end
    set -a; source "$REPO_ROOT/.env"; set +a

    # Safety snapshot of current system state — BEFORE any modifications.
    # Saves /etc/nginx + Docker state + open ports to /var/backups/smartrag-*.
    # If anything goes wrong below, the operator can diff or restore.
    create_system_snapshot

    bash "$SCRIPT_DIR/install-system-packages.sh" --lang "$LANG_CHOICE"
    bash "$SCRIPT_DIR/install-postfix.sh"         --lang "$LANG_CHOICE"
    bash "$SCRIPT_DIR/get-ssl-certs.sh"           --lang "$LANG_CHOICE"
    bash "$SCRIPT_DIR/start-services.sh"          --lang "$LANG_CHOICE"
    bash "$SCRIPT_DIR/deploy-schemas.sh"          --lang "$LANG_CHOICE"
    # Exits EXIT_SKIPPED if n8n has no owner account yet — expected on a
    # first run, since creating that account is a manual browser step. The
    # rest of the install is unaffected, so we carry on and report it at the
    # end instead of aborting. `|| rc=$?` is required under `set -e`.
    # The guided version lives in common.sh so admin.sh's menu entry walks
    # the admin through exactly the same owner-account step — nothing that
    # belongs to a standard setup should need a hand-typed command.
    local n8n_rc=0
    run_n8n_import_guided "$SCRIPT_DIR" "$LANG_CHOICE" || n8n_rc=$?
    if (( n8n_rc != 0 && n8n_rc != EXIT_SKIPPED && n8n_rc != EXIT_UNVERIFIED )); then
        die "$(t orch_n8n_failed)"
    fi

    bash "$SCRIPT_DIR/generate-lti-keys.sh"       --lang "$LANG_CHOICE"

    # A run that skipped a required phase is not a completed install, and
    # must not look like one. The old version printed the same "Complete"
    # banner either way and left the follow-up as one line of prose in a
    # long wall of text — which is exactly how an install reaches the point
    # where the first sign of trouble is a 404 in the Content Admin GUI.
    # Ran, but n8n never came back to confirm it. Not a completed install
    # either — say precisely that, instead of picking whichever of the two
    # existing banners is less wrong.
    if (( n8n_rc == EXIT_UNVERIFIED )); then
        header "$(t orch_incomplete)"
        echo "  $(t orch_n8n_unverified)"
        echo
        echo "  $(t orch_next_visit "$(subdomain_host smart-rag "$DOMAIN" "${SUBDOMAIN_PREFIX:-}")")"
        echo "  $(t orch_next_login)"
        return 0
    fi

    if (( n8n_rc == EXIT_SKIPPED )); then
        header "$(t orch_incomplete)"
        echo "  $(t orch_incomplete_intro)"
        echo
        echo "  $(t orch_incomplete_step1 "$(subdomain_host n8n "$DOMAIN" "${SUBDOMAIN_PREFIX:-}")")"
        echo "  $(t orch_incomplete_step2)"
        echo
        printf "      ${BOLD}sudo bash %s/deploy-n8n-workflows.sh${RESET}\n" "$SCRIPT_DIR"
        echo
        echo "  $(t orch_incomplete_then)"
        echo
        echo "  $(t orch_next_visit "$(subdomain_host smart-rag "$DOMAIN" "${SUBDOMAIN_PREFIX:-}")")"
        echo "  $(t orch_next_login)"
        return 0
    fi

    header "$(t orch_complete)"
    echo "  $(t orch_next_visit "$(subdomain_host smart-rag "$DOMAIN" "${SUBDOMAIN_PREFIX:-}")")"
    echo "  $(t orch_next_login)"
    echo
    echo "  $(t orch_next_finalize)"
}

# ─── --continue branch ────────────────────────────────────────────────────────
if [[ "$MODE" == "continue" ]]; then
    run_deployment_phases
    exit 0
fi

# ─── Intro ───────────────────────────────────────────────────────────────────
printf "%s\n\n" "$(t intro_welcome)"
printf "${BOLD}%s${RESET}\n" "$(t intro_what_happens)"
printf "%s\n" "$(t intro_step1)"
printf "%s\n" "$(t intro_step2)"
printf "%s\n" "$(t intro_step3)"
printf "%s\n" "$(t intro_step4)"
printf "%s\n\n" "$(t intro_step5)"

printf "${BOLD}%s${RESET}\n" "$(t intro_how_it_works)"
printf "%s\n" "$(t intro_how1)"
printf "%s\n" "$(t intro_how2)"
printf "%s\n" "$(t intro_how3)"
printf "%s\n\n" "$(t intro_how4)"

if ! confirm intro_continue "y"; then
    exit 0
fi

# ─── Phase 0: Prerequisites checklist ────────────────────────────────────────
if (( SKIP_PREFLIGHT == 0 )); then
    show_prerequisites_checklist
fi

# ─── Phase 1: Pre-flight ─────────────────────────────────────────────────────
if (( SKIP_PREFLIGHT == 0 )); then
    header "$(t phase_preflight)"
    run_preflight
fi

# ─── Phase 2: Configuration wizard ───────────────────────────────────────────
header "$(t phase_config)"

# Handle existing .env
if [[ -f "$REPO_ROOT/.env" ]]; then
    # Use _index variant — language-neutral 1/2/3 instead of translated text
    choice="$(select_one_index cfg_env_exists_prompt \
        "$(t cfg_env_keep)" \
        "$(t cfg_env_backup_new)" \
        "$(t cfg_env_overwrite)")"
    case "$choice" in
        1)
            warn "Using existing .env — skipping wizard, secrets, templates."
            warn "If you need to regenerate secrets, delete .env first."
            printf "\n${GREEN}${BOLD}Done.${RESET}\n"
            exit 0
            ;;
        2) backup_file "$REPO_ROOT/.env" ;;
        3) warn "Overwriting existing .env without backup" ;;
    esac
fi

run_config_wizard

# ─── Coexistence pre-flight (now that we know domain + ports) ────────────────
# Export wizard values as env vars so run_coexistence_preflight() can read them
# via the same names it would see when loading .env.
DOMAIN="$CFG_DOMAIN"
BASE_DATA_PATH="$CFG_BASE_DATA_PATH"
COMPOSE_PROFILES="$CFG_COMPOSE_PROFILES"
export DOMAIN BASE_DATA_PATH COMPOSE_PROFILES
# Host ports come from .env.example defaults (the wizard doesn't ask for them
# — user overrides in .env if needed). We pre-read them from .env.example so
# the port-collision check uses the real values.
if [[ -f "$REPO_ROOT/.env.example" ]]; then
    while IFS='=' read -r k v; do
        case "$k" in
            FLOWISE_PORT|N8N_PORT|WEAVIATE_HTTP_PORT|WEAVIATE_GRPC_PORT| \
            NEO4J_HTTP_PORT|NEO4J_BOLT_PORT|LANGFUSE_PORT|MINIO_API_PORT| \
            MINIO_CONSOLE_PORT|LTI_PORT)
                v="${v#\"}"; v="${v%\"}"
                export "$k=$v"
                ;;
        esac
    done < <(grep -E '^[A-Z_]+_PORT=' "$REPO_ROOT/.env.example")
fi

run_coexistence_preflight

# Re-run DNS check now that we have a domain (warning only).
# We check the actual subdomains, not the base domain (which we don't host).
# Snapshot PREFLIGHT_WARN before/after: if it didn't move, every check_dns()
# call below found a correct match — used further down to offer skipping
# straight into --continue instead of making the user run a second command.
DNS_ALL_OK=0
if command -v dig >/dev/null 2>&1; then
    info "Checking DNS for required subdomains of $CFG_DOMAIN..."
    _warn_before_dns=$PREFLIGHT_WARN
    check_dns "$(subdomain_host smart-rag "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")"
    check_dns "$(subdomain_host n8n       "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")"
    check_dns "$(subdomain_host minio     "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")"
    [[ "$CFG_ENABLE_OBSERVABILITY" == "yes" ]] && check_dns "$(subdomain_host langfuse "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")"
    [[ "$CFG_ENABLE_LTI" == "yes" ]]           && check_dns "$(subdomain_host lti       "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")"
    [[ "$PREFLIGHT_WARN" -eq "$_warn_before_dns" ]] && DNS_ALL_OK=1
fi

# ─── Phase 3: Generate secrets ───────────────────────────────────────────────
header "$(t phase_secrets)"
info "$(t secrets_intro)"
generate_all_secrets
ok "$(t secrets_done 20)"
printf "  ${BOLD}%s${RESET}  %s\n\n" \
    "$(t secrets_admin_pw_note)" "$SECRET_ADMIN_PASSWORD"

# ─── Phase 4: Write templates ────────────────────────────────────────────────
header "$(t phase_templates)"

# Export config so future phases (called as separate scripts) can re-read it
DOMAIN="$CFG_DOMAIN"
COURSE_ID="$CFG_COURSE_ID"
ADMIN_EMAIL="$CFG_ADMIN_EMAIL"
export DOMAIN COURSE_ID ADMIN_EMAIL

# Decide where to stage generated files
STAGING_DIR="$CFG_BASE_DATA_PATH/staging"
mkdir -p "$STAGING_DIR"

write_env_file "$REPO_ROOT"
write_weaviate_schema "$REPO_ROOT" "$STAGING_DIR/weaviate-schema.json"

# nginx target — only write if /etc/nginx/sites-available exists (otherwise
# we're not on a server where nginx is installed yet; the user will rerun
# us later or copy manually).
NGINX_TARGET="/etc/nginx/sites-available/smartrag-suite.conf"
if [[ -d /etc/nginx/sites-available ]]; then
    write_nginx_config "$REPO_ROOT" "$NGINX_TARGET"
else
    warn "nginx not yet installed — writing staged config to $STAGING_DIR/smartrag-suite.conf"
    write_nginx_config "$REPO_ROOT" "$STAGING_DIR/smartrag-suite.conf"
fi

# LTI configs — only if profile enabled
if [[ "$CFG_ENABLE_LTI" == "yes" ]]; then
    copy_lti_configs "$REPO_ROOT"
fi

ok "$(t tpl_done)"

# ─── Save credentials.txt ────────────────────────────────────────────────────
CREDS_FILE="$REPO_ROOT/credentials.txt"
write_credentials_file "$CREDS_FILE"
info "$(t secrets_creds_file "$CREDS_FILE")"

# ─── Summary ─────────────────────────────────────────────────────────────────
header "$(t phase_complete)"

printf "${BOLD}%s${RESET}\n" "$(t summary_files)"
echo "  • $REPO_ROOT/.env"
echo "  • $CREDS_FILE"
echo "  • $STAGING_DIR/weaviate-schema.json"
if [[ -d /etc/nginx/sites-available ]]; then
    echo "  • $NGINX_TARGET"
else
    echo "  • $STAGING_DIR/smartrag-suite.conf  (staged — copy to /etc/nginx after installing nginx)"
fi
if [[ "$CFG_ENABLE_LTI" == "yes" ]]; then
    echo "  • $REPO_ROOT/lti-middleware/config/lti.json"
    echo "  • $REPO_ROOT/lti-middleware/config/agents.json"
    echo "  • $REPO_ROOT/lti-middleware/config/branding.json"
fi
echo

# Compose list of subdomains for DNS hint
SUBDOMAINS="$(subdomain_host smart-rag "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")  $(subdomain_host n8n "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")  $(subdomain_host minio "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")  $(subdomain_host s3 "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")"
[[ "$CFG_ENABLE_OBSERVABILITY" == "yes" ]] && SUBDOMAINS="$SUBDOMAINS  $(subdomain_host langfuse "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")"
[[ "$CFG_ENABLE_LTI" == "yes" ]] && SUBDOMAINS="$SUBDOMAINS  $(subdomain_host lti "$CFG_DOMAIN" "$CFG_SUBDOMAIN_PREFIX")"

# Shown before the DNS/auto-continue branch below so it's never missed —
# some branches exit straight into deployment and never reach the old
# end-of-script position this used to have.
printf "${YELLOW}${BOLD}%s${RESET}\n" "$(t summary_creds_warn "$CREDS_FILE")"
printf "${DIM}%s${RESET}\n\n" "$(t summary_creds_chmod)"

printf "${BOLD}%s${RESET}\n" "$(t summary_next)"
echo "$(t summary_next_review)"

if (( DNS_ALL_OK )); then
    echo
    ok "$(t dns_all_ok_title)"
    if confirm dns_auto_continue_confirm "y"; then
        echo
        _log_to_file "─── bootstrap.sh finished (phase 1) — auto-continuing into phases 5–7 ───"
        run_deployment_phases
        exit 0
    else
        echo "$(t dns_auto_continue_declined)"
    fi
else
    echo "$(t summary_next_dns "$CFG_DOMAIN")"
    echo "      Subdomains used: $SUBDOMAINS"
    echo "$(t summary_next_dns_ip "$(detect_public_ip)")"
    echo "$(t summary_next_dns_howto "$CFG_DOMAIN")"
    echo "$(t summary_next_start)"
fi
echo

_log_to_file "─── bootstrap.sh finished (phase 1) ───"
