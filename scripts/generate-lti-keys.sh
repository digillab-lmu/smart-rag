#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Generate LTI 1.3 RSA signing keys (Phase 11)
# ═════════════════════════════════════════════════════════════════════════════
#
# Thin wrapper around lti-middleware/generate_keys.sh (already handles the
# actual openssl key generation + its own overwrite protection) — adds the
# bilingual messaging/idempotency-skip conventions the rest of the pipeline
# uses, and no-ops entirely when the lti profile isn't enabled.
#
# Usage:  sudo bash scripts/generate-lti-keys.sh [--lang en|de]
# Re-runnable — skips if keys already exist.
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

header "$(t phase_lti_keys)"

if [[ "${COMPOSE_PROFILES:-core}" != *lti* ]]; then
    dim "$(t lti_keys_skip_no_profile)"
    exit 0
fi

require_command openssl

if [[ -f "$REPO_ROOT/lti-middleware/config/private.key" ]]; then
    ok "$(t lti_keys_exist)"
    exit 0
fi

info "$(t lti_keys_generating)"
if (cd "$REPO_ROOT/lti-middleware" && bash ./generate_keys.sh); then
    ok "$(t lti_keys_done)"
else
    die "$(t lti_keys_failed)"
fi
