#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Install & configure Postfix as a local mail relay
# ═════════════════════════════════════════════════════════════════════════════
#
# Installs Postfix in "Internet with smarthost" (satellite) mode: it does NOT
# accept or deliver mail for the outside world, it only relays outbound mail
# from Flowise/n8n/Langfuse through your real mail provider's credentials.
# Apps talk to Postfix unauthenticated over the internal Docker network —
# no app ever sees the actual relay password.
#
# Reads from .env:
#   INSTALL_POSTFIX_RELAY  — "true" to run, anything else skips silently
#   SMTP_RELAY_HOST/PORT   — the real upstream smarthost (your provider)
#   SMTP_RELAY_USER/PASSWORD — smarthost credentials (empty = no auth)
#   DOMAIN                 — used as Postfix's mailname
#
# Security model:
#   - inet_interfaces = all (Postfix listens on every interface, including
#     the public one — this is standard practice for a satellite relay)
#   - mynetworks restricts actual relaying to localhost + the Docker subnet
#     pinned in docker-compose.yml (172.28.92.0/24). Postfix's own default
#     (reject_unauth_destination) refuses relay attempts from anyone else —
#     an internet host hitting port 25 gets a protocol handshake and then
#     "554 5.7.1 Relay access denied", nothing more.
#
# Idempotent. If Postfix is already installed by something else, asks for
# confirmation before touching its config (coexistence safety).
#
# Usage:  sudo bash scripts/install-postfix.sh [--lang en|de]
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

header "$(t phase_postfix)"

if [[ "${INSTALL_POSTFIX_RELAY:-false}" != "true" ]]; then
    info "$(t postfix_skip_disabled)"
    exit 0
fi

if [[ -z "${SMTP_RELAY_HOST:-}" ]]; then
    die "$(t postfix_missing_relay_host)"
fi

# Must match the pinned subnet/gateway in docker/docker-compose.yml exactly —
# see the comment on the `smart-rag-network` block there.
DOCKER_GATEWAY="172.28.92.1"
DOCKER_SUBNET="172.28.92.0/24"

RELAY_HOST="$SMTP_RELAY_HOST"
RELAY_PORT="${SMTP_RELAY_PORT:-587}"
RELAY_USER="${SMTP_RELAY_USER:-}"
RELAY_PASSWORD="${SMTP_RELAY_PASSWORD:-}"
MAILNAME="${DOMAIN:-localhost}"

# ─── Coexistence check — is Postfix already here for another reason? ─────────
ALREADY_INSTALLED=0
if dpkg -s postfix >/dev/null 2>&1; then
    ALREADY_INSTALLED=1
    if ! grep -q "# Managed by SMART RAG bootstrap" /etc/postfix/main.cf 2>/dev/null; then
        warn "$(t postfix_already_other)"
        if ! confirm postfix_confirm_reconfigure "n"; then
            info "$(t postfix_declined)"
            exit 0
        fi
        backup_ts="$(date +%Y%m%dT%H%M%S)"
        backup_dir="/var/backups/smartrag-postfix-pre-bootstrap-$backup_ts"
        mkdir -p "$backup_dir"
        chmod 700 "$backup_dir"
        tar czf "$backup_dir/postfix.tar.gz" -C / etc/postfix 2>/dev/null || true
        chmod 600 "$backup_dir"/*.tar.gz 2>/dev/null || true
        ok "$(t postfix_backup "$backup_dir/postfix.tar.gz")"
    fi
fi

# ─── Install (non-interactive) ────────────────────────────────────────────────
if (( ALREADY_INSTALLED )); then
    info "$(t postfix_already_installed)"
else
    info "$(t postfix_installing)"
    debconf-set-selections <<EOF
postfix postfix/main_mailer_type select Internet with smarthost
postfix postfix/mailname string ${MAILNAME}
postfix postfix/relayhost string [${RELAY_HOST}]:${RELAY_PORT}
EOF
    DEBIAN_FRONTEND=noninteractive apt-get update -q >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y postfix mailutils libsasl2-modules >/dev/null
    ok "$(t postfix_installed)"
fi

# ─── Configure relay + access restrictions ────────────────────────────────────
info "$(t postfix_configuring "$RELAY_HOST")"

postconf -e "myhostname = ${MAILNAME}"
postconf -e "inet_interfaces = all"
postconf -e "mynetworks = 127.0.0.0/8 [::1]/128 ${DOCKER_SUBNET}"
postconf -e "relayhost = [${RELAY_HOST}]:${RELAY_PORT}"
postconf -e "smtp_tls_security_level = encrypt"

if [[ -n "$RELAY_USER" ]]; then
    printf '[%s]:%s\t%s:%s\n' "$RELAY_HOST" "$RELAY_PORT" "$RELAY_USER" "$RELAY_PASSWORD" \
        > /etc/postfix/sasl_passwd
    chmod 600 /etc/postfix/sasl_passwd
    postmap /etc/postfix/sasl_passwd
    postconf -e "smtp_sasl_auth_enable = yes"
    postconf -e "smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd"
    postconf -e "smtp_sasl_security_options = noanonymous"
else
    postconf -e "smtp_sasl_auth_enable = no"
fi

# Marker so future runs know this Postfix is ours (see coexistence check above)
if ! grep -q "# Managed by SMART RAG bootstrap" /etc/postfix/main.cf 2>/dev/null; then
    printf '\n# Managed by SMART RAG bootstrap — see scripts/install-postfix.sh\n' \
        >> /etc/postfix/main.cf
fi

systemctl restart postfix
systemctl enable postfix >/dev/null 2>&1 || true

if systemctl is-active --quiet postfix; then
    ok "$(t postfix_configured "$RELAY_HOST" "$DOCKER_SUBNET")"
    dim "Docker containers reach this relay at: ${DOCKER_GATEWAY}:25"
else
    die "$(t postfix_restart_failed)"
fi

# ─── Test email — actually prove the relay works end to end ──────────────────
# Prefer /var/log/mail.log (standard on Ubuntu with rsyslog); fall back to
# journald in case rsyslog isn't installed on this box.
_mail_log_tail() {
    if [[ -f /var/log/mail.log ]]; then
        tail -n 200 /var/log/mail.log 2>/dev/null
    else
        journalctl -u postfix --no-pager -n 200 2>/dev/null
    fi
}

if ! command -v mail >/dev/null 2>&1; then
    warn "$(t postfix_test_no_mailutils)"
elif [[ -z "${ADMIN_EMAIL:-}" ]]; then
    warn "$(t postfix_test_no_admin_email)"
else
    info "$(t postfix_test_sending "$ADMIN_EMAIL")"
    marker="smartrag-postfix-test-$(date +%s)-$$"
    {
        echo "This is an automated test message from scripts/install-postfix.sh."
        echo "If you received this, the SMART RAG mail relay is working end to end."
        echo "Marker: $marker"
    } | mail -s "SMART RAG mail relay test ($marker)" "$ADMIN_EMAIL"

    # Find the Postfix queue ID just assigned to our message, then poll the
    # log for its final delivery status (sent/bounced/deferred).
    sleep 2
    qid="$(_mail_log_tail | grep 'postfix/pickup' | tail -1 | awk -F': ' '{print $2}')"

    if [[ -z "$qid" ]]; then
        warn "$(t postfix_test_unknown)"
    else
        status_line=""
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            status_line="$(_mail_log_tail | grep "$qid" | grep -E 'status=(sent|bounced|deferred)' | tail -1)"
            [[ -n "$status_line" ]] && break
            sleep 2
        done

        if [[ -z "$status_line" ]]; then
            warn "$(t postfix_test_timeout)"
        elif [[ "$status_line" == *"status=sent"* ]]; then
            ok "$(t postfix_test_success "$ADMIN_EMAIL")"
        else
            warn "$(t postfix_test_failed)"
            printf '    %s\n' "$status_line"
        fi
    fi
fi
