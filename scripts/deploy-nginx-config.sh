#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Regenerate and deploy the nginx config from the current .env
# ═════════════════════════════════════════════════════════════════════════════
#
# templates.sh::write_nginx_config() is normally only called once, inline,
# during bootstrap.sh's Phase 4. That's a real gap: a `git pull` that adds a
# new subdomain/service (see the content-admin GUI's content.<domain> block)
# updates the REPO's nginx/smartrag-suite.conf template, but never touches
# the already-materialized /etc/nginx/sites-available/smartrag-suite.conf
# on an existing deployment — nginx keeps serving whatever was last written,
# silently 404ing (or falling through to nginx's own default page) for
# anything added since. This script re-runs that same template write
# standalone, against whatever's already in .env, so picking up a new
# subdomain after an update doesn't need re-running the whole wizard.
#
# Does NOT touch SSL — a newly-added subdomain also needs its hostname in
# the certificate's SAN list, which only get-ssl-certs.sh (run afterward)
# handles. This script's job ends at "nginx knows about the route".
#
# Usage:  sudo bash scripts/deploy-nginx-config.sh [--lang en|de]
# Re-runnable — always safe, backs up the previous config first.
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

require_command nginx

# ─── Load .env ───────────────────────────────────────────────────────────────
[[ -f "$REPO_ROOT/.env" ]] || die "$(t orch_phase1_needed)"
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

header "$(t phase_nginx_redeploy)"

# write_nginx_config() reads CFG_-prefixed names (the wizard's own internal
# variables) — alias them from the plain .env values a redeploy actually has.
CFG_DOMAIN="$DOMAIN"
CFG_SUBDOMAIN_PREFIX="${SUBDOMAIN_PREFIX:-}"
CFG_LMS_URL="${LMS_URL:-https://lms.example.com}"

# Ports are already resolved and in active use on an existing deployment —
# no need to re-run resolve_ports()'s conflict detection, just carry
# forward whatever .env already has (falls back to defaults for anything
# unset, same as write_nginx_config() already does on its own).
declare -A RESOLVED_PORTS=(
    [FLOWISE_PORT]="${FLOWISE_PORT:-3000}"
    [N8N_PORT]="${N8N_PORT:-5678}"
    [LANGFUSE_PORT]="${LANGFUSE_PORT:-3001}"
    [GARAGE_S3_PORT]="${GARAGE_S3_PORT:-3900}"
    [LTI_PORT]="${LTI_PORT:-10088}"
    [CONTENT_ADMIN_PORT]="${CONTENT_ADMIN_PORT:-3002}"
)

NGINX_TARGET="/etc/nginx/sites-available/smartrag-suite.conf"
write_nginx_config "$REPO_ROOT" "$NGINX_TARGET"

info "$(t nginx_redeploy_testing)"
if ! nginx -t 2>&1 | tail -10; then
    die "$(t nginx_redeploy_test_failed "$NGINX_TARGET")"
fi

systemctl reload nginx
ok "$(t nginx_redeploy_done)"
dim "$(t nginx_redeploy_ssl_hint)"
