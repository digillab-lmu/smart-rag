#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Install required system packages (Phase 5)
# ═════════════════════════════════════════════════════════════════════════════
#
# Installs nginx, certbot, jq, dnsutils, openssl, curl on Ubuntu 24.04.
# Idempotent — packages already installed are skipped.
#
# Usage:  sudo bash scripts/install-system-packages.sh [--lang en|de]
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

# ─── Required packages ───────────────────────────────────────────────────────
PACKAGES=(
    nginx
    certbot
    python3-certbot-nginx
    jq
    dnsutils       # for `dig` (DNS checks)
    openssl
    curl
    ca-certificates
)

header "$(t phase_packages)"

# ─── Determine which are missing ─────────────────────────────────────────────
to_install=()
for pkg in "${PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
        dim "$(t pkg_already "$pkg")"
    else
        to_install+=("$pkg")
    fi
done

if (( ${#to_install[@]} == 0 )); then
    ok "$(t pkg_done)"
    exit 0
fi

# ─── Install ─────────────────────────────────────────────────────────────────
info "$(t pkg_updating)"
DEBIAN_FRONTEND=noninteractive apt-get update -q >/dev/null

info "$(t pkg_installing "${to_install[*]}")"
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${to_install[@]}"

ok "$(t pkg_done)"
