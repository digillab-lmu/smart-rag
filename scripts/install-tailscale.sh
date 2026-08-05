#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Tailscale deployment (Phase 6, tailscale mode only)
# ═════════════════════════════════════════════════════════════════════════════
#
# Replaces nginx + Let's Encrypt entirely. Tailscale terminates TLS itself,
# with real Let's Encrypt certificates obtained over a DNS-01 challenge
# against `*.ts.net` — so no inbound port 80, no DNS records of your own, and
# no certificate renewal to arrange. Every service is reached at this
# machine's MagicDNS name on its own port:
#
#     https://<machine>.<tailnet>.ts.net        → Flowise      (chat)
#     https://<machine>.<tailnet>.ts.net:8443   → Content Admin
#     https://<machine>.<tailnet>.ts.net:8444   → n8n
#     https://<machine>.<tailnet>.ts.net:8445   → Langfuse     (if enabled)
#     https://<machine>.<tailnet>.ts.net:8446   → MinIO console
#     https://<machine>.<tailnet>.ts.net:8447   → MinIO S3 API
#
# A certificate covers exactly one MagicDNS name — there are no wildcards and
# no additional names — which is why services are separated by port here
# rather than by subdomain as in domain mode.
#
# Flowise additionally goes through Funnel, which makes it reachable from the
# public internet without the visitor having Tailscale. Funnel is limited to
# ports 443, 8443 and 10000, so Flowise takes 443 and everything else stays
# inside the tailnet — the administration interfaces are then not exposed at
# all, which is stricter than domain mode, where they sit behind a password
# on a public vhost.
#
# Three steps need a browser and cannot be scripted: joining a tailnet,
# approving this machine, and approving Funnel. Each is a wait-and-confirm,
# the same shape as the n8n owner-account step.
#
# Usage:  sudo bash scripts/install-tailscale.sh [--lang en|de]
# Re-runnable: `tailscale serve` is declarative, so re-running re-applies the
# same configuration rather than stacking duplicates.
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

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "$(t pf_root_needed "$(basename "$0")")"
fi

[[ -f "$REPO_ROOT/.env" ]] || die "$(t orch_phase1_needed)"
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

header "$(t phase_tailscale)"

# ─── 1. Install ──────────────────────────────────────────────────────────────
if command -v tailscale >/dev/null 2>&1; then
    ok "$(t ts_already_installed "$(tailscale version | head -1)")"
else
    info "$(t ts_installing)"
    # Tailscale's official installer. Preferred over a hand-written apt repo
    # stanza because it resolves the distribution itself — which matters on a
    # release that may not have its own repository path yet.
    if curl -fsSL https://tailscale.com/install.sh | sh; then
        ok "$(t ts_installed)"
    else
        die "$(t ts_install_failed)"
    fi
fi

# ─── 2. Join a tailnet ───────────────────────────────────────────────────────
# `tailscale up` prints a URL to approve in a browser. Deliberately NOT an
# auth key: a key would have to live in .env, and its reach is the whole
# tailnet — a much larger credential than anything else in that file.
ts_backend_state() {
    tailscale status --json 2>/dev/null | grep -o '"BackendState":"[^"]*"' | cut -d'"' -f4
}

if [[ "$(ts_backend_state)" == "Running" ]]; then
    ok "$(t ts_already_up)"
else
    info "$(t ts_up_intro)"
    echo
    # Streams to the terminal so the operator sees the login URL as it
    # appears; `up` blocks until the browser approval happens.
    if tailscale up --accept-dns=false; then
        ok "$(t ts_up_done)"
    else
        die "$(t ts_up_failed)"
    fi
fi

MAGIC_DNS_NAME="$(tailscale status --json 2>/dev/null \
    | grep -o '"DNSName":"[^"]*"' | head -1 | cut -d'"' -f4 | sed 's/\.$//')"

if [[ -z "$MAGIC_DNS_NAME" ]]; then
    die "$(t ts_no_magicdns)"
fi
ok "$(t ts_hostname "$MAGIC_DNS_NAME")"

# ─── 3. Certificate ──────────────────────────────────────────────────────────
# `tailscale serve` provisions and renews the certificate itself; this is a
# pre-flight so a tailnet without HTTPS enabled fails here, with a clear
# message, rather than later inside serve.
info "$(t ts_cert_check)"
if tailscale cert "$MAGIC_DNS_NAME" >/dev/null 2>&1; then
    ok "$(t ts_cert_ok)"
else
    warn "$(t ts_cert_failed "$MAGIC_DNS_NAME")"
    dim "$(t ts_cert_hint)"
    confirm ts_cert_continue "n" || die "$(t ts_aborted)"
fi

# ─── 3b. Write the resolved URLs back to .env ────────────────────────────────
# Same principle as domain mode: every public URL is stored fully resolved,
# so nothing downstream has to know which deployment mode this is or how to
# assemble a hostname. The MagicDNS name is only knowable once Tailscale is
# up, which is why this happens here and not in the wizard.
ENV_FILE="$REPO_ROOT/.env"
info "$(t ts_writing_env)"

set_env_var "$ENV_FILE" TAILSCALE_HOSTNAME        "$MAGIC_DNS_NAME"
set_env_var "$ENV_FILE" DOMAIN                    "$MAGIC_DNS_NAME"
set_env_var "$ENV_FILE" FLOWISE_PUBLIC_URL        "https://$MAGIC_DNS_NAME"
set_env_var "$ENV_FILE" MINIO_BROWSER_REDIRECT_URL "https://$MAGIC_DNS_NAME:8446"
set_env_var "$ENV_FILE" MINIO_SERVER_URL          "https://$MAGIC_DNS_NAME:8447"
set_env_var "$ENV_FILE" N8N_HOSTNAME              "$MAGIC_DNS_NAME"
set_env_var "$ENV_FILE" N8N_WEBHOOK_URL           "https://$MAGIC_DNS_NAME:8444"
set_env_var "$ENV_FILE" CONTENT_ADMIN_PUBLIC_URL  "https://$MAGIC_DNS_NAME:8443"
if [[ "${COMPOSE_PROFILES:-core}" == *observability* ]]; then
    set_env_var "$ENV_FILE" NEXTAUTH_URL "https://$MAGIC_DNS_NAME:8445"
    set_env_var "$ENV_FILE" LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT "https://$MAGIC_DNS_NAME:8447"
fi
ok "$(t ts_env_written)"

# ─── 4. Serve every service on its own port ──────────────────────────────────
# The containers already bind to 127.0.0.1:<host port>, so serve proxies
# straight to them — nginx is not involved in this mode at all.
declare -A SERVE_PORTS=(
    [8443]="${CONTENT_ADMIN_PORT:-3002}"
    [8444]="${N8N_PORT:-5678}"
    [8446]="${MINIO_CONSOLE_PORT:-9001}"
    [8447]="${MINIO_API_PORT:-9000}"
)
[[ "${COMPOSE_PROFILES:-core}" == *observability* ]] && SERVE_PORTS[8445]="${LANGFUSE_PORT:-3001}"

info "$(t ts_serve_intro)"
# Reset first: serve is declarative, and a stale mapping from an earlier run
# with different ports would otherwise linger.
tailscale serve reset >/dev/null 2>&1 || true

for public_port in "${!SERVE_PORTS[@]}"; do
    local_port="${SERVE_PORTS[$public_port]}"
    if tailscale serve --bg --https="$public_port" "http://127.0.0.1:${local_port}" >/dev/null 2>&1; then
        ok "$(t ts_serve_ok "$public_port" "$local_port")"
    else
        die "$(t ts_serve_failed "$public_port" "$local_port")"
    fi
done

# ─── 5. Flowise, publicly, via Funnel ────────────────────────────────────────
# The first `tailscale funnel` opens a browser approval, after which
# Tailscale writes the funnel node attribute into the tailnet policy itself.
info "$(t ts_funnel_intro)"
echo
if tailscale funnel --bg --https=443 "http://127.0.0.1:${FLOWISE_PORT:-3000}"; then
    ok "$(t ts_funnel_ok)"
else
    warn "$(t ts_funnel_failed)"
    dim "$(t ts_funnel_hint)"
    # Not fatal: everything else is reachable inside the tailnet, and Funnel
    # can be enabled later. Saying it plainly beats leaving an install that
    # looks finished but has no student-facing URL.
fi

# ─── 6. Report ───────────────────────────────────────────────────────────────
echo
header "$(t ts_urls_title)"
echo "  $(t ts_url_flowise "https://$MAGIC_DNS_NAME")"
echo "  $(t ts_url_content "https://$MAGIC_DNS_NAME:8443")"
echo "  $(t ts_url_n8n     "https://$MAGIC_DNS_NAME:8444")"
[[ -n "${SERVE_PORTS[8445]:-}" ]] && echo "  $(t ts_url_langfuse "https://$MAGIC_DNS_NAME:8445")"
echo "  $(t ts_url_minio   "https://$MAGIC_DNS_NAME:8446")"
echo
dim "$(t ts_urls_note)"

ok "$(t ts_done)"
