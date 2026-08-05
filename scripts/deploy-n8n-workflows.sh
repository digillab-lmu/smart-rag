#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Import n8n credentials + workflows (Phase 10)
# ═════════════════════════════════════════════════════════════════════════════
#
# Provisions everything n8n needs to actually run the ingest pipeline:
#   1. Credentials (MinIO/S3 + SMTP) — staged as plain JSON, imported via
#      `n8n import:credentials`, which encrypts them with the instance's own
#      N8N_ENCRYPTION_KEY. Deliberately NOT via n8n's public REST API: that
#      needs an API key which only exists once a human creates one in the UI
#      (same chicken-and-egg as Flowise). The CLI has no such requirement.
#   2. Workflows — `n8n import:workflow`, then activated via
#      `n8n update:workflow --active=true`.
#
# Everything runs through `docker exec` against the already-running n8n
# container, so no host-side n8n installation is needed. Staging files are
# written under $BASE_DATA_PATH/n8n/data/staging (which is the container's
# /home/node/.n8n/staging via the existing bind mount) and deleted again
# afterwards — they contain plaintext secrets while they exist.
#
# PRECONDITION: n8n's owner account must already exist (the one-time
# "set up owner" screen on first login). Without it the CLI has no user to
# assign imported objects to and fails with a clear message. Same one-time
# manual bridge as Flowise's API key — documented in docs/operations-guide.md.
#
# Usage:  sudo bash scripts/deploy-n8n-workflows.sh [--lang en|de]
# Re-runnable any time — imports are keyed by fixed IDs, so a re-run updates
# in place rather than creating duplicates.
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

require_command jq
require_command docker
# Used by the final verification step. Declared here so a missing curl says
# so plainly instead of surfacing later as "n8n could not be reached".
require_command curl

header "$(t phase_n8n_workflows)"

# ─── Preconditions ───────────────────────────────────────────────────────────
_container_ready() {
    local container="$1"
    local status
    status="$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "missing")"
    case "$status" in
        healthy) return 0 ;;
        missing)
            docker inspect --format='{{.State.Status}}' "$container" 2>/dev/null | grep -qx running
            ;;
        *) return 1 ;;
    esac
}

_container_ready smartrag-n8n || die "$(t schema_container_not_healthy "smartrag-n8n")"

WORKFLOW_DIR="$REPO_ROOT/n8n/workflows-ingest"
[[ -d "$WORKFLOW_DIR" ]] || die "$(t n8n_workflow_dir_missing "$WORKFLOW_DIR")"

# The two ingest workflows. This list is the whole set — a third
# (WhisperX audio transcription) was removed from the repo rather than
# generalized; see that directory's README.
WORKFLOWS=(
    "ingest-chunk-and-embed.json"
    "ingest-document.json"
)
# Only workflows with a real trigger need activating. The chunk+embed
# sub-workflow is reached via an Execute Workflow node, which doesn't
# require it to be active.
ACTIVATE_IDS=("smartrag-ingest-document")

# ─── Staging dir (inside the container's bind-mounted volume) ────────────────
STAGING_HOST="${BASE_DATA_PATH}/n8n/data/staging"
STAGING_CONTAINER="/home/node/.n8n/staging"

# Secrets land here in plaintext for the duration of the import — make sure
# they're gone afterwards even if something below fails.
cleanup_staging() {
    rm -rf "$STAGING_HOST"
}
trap cleanup_staging EXIT

rm -rf "$STAGING_HOST"
mkdir -p "$STAGING_HOST"
chmod 700 "$STAGING_HOST"
# n8n's container runs as uid 1000 ("node") and must be able to read these.
chown -R 1000:1000 "$STAGING_HOST"

# ─── 1. Credentials ──────────────────────────────────────────────────────────
# IDs must match what the workflow JSONs reference in their `credentials`
# blocks — that's how an imported workflow finds its credential.
info "$(t n8n_creds_staging)"

CREDS_FILE="$STAGING_HOST/credentials.json"

# MinIO speaks S3. forcePathStyle is required: MinIO serves buckets as
# path segments (host/bucket), not as virtual-host subdomains.
jq -n \
    --arg endpoint "http://smartrag-minio:9000" \
    --arg region "${MINIO_REGION_NAME}" \
    --arg access "${MINIO_ROOT_USER}" \
    --arg secret "${MINIO_ROOT_PASSWORD}" \
    --arg smtp_host "${SMTP_HOST:-}" \
    --arg smtp_user "${SMTP_USER:-}" \
    --arg smtp_pass "${SMTP_PASSWORD:-}" \
    --argjson smtp_port "${SMTP_PORT:-25}" \
    --argjson smtp_secure "$([[ "${SMTP_SECURE:-false}" == "true" ]] && echo true || echo false)" \
    '[
      {
        "id": "smartrag-minio-credential",
        "name": "smartrag-minio",
        "type": "s3",
        "data": {
          "endpoint": $endpoint,
          "region": $region,
          "accessKeyId": $access,
          "secretAccessKey": $secret,
          "forcePathStyle": true,
          "ignoreSSLIssues": false
        }
      },
      {
        "id": "smartrag-smtp-credential",
        "name": "smartrag-smtp",
        "type": "smtp",
        "data": {
          "host": $smtp_host,
          "port": $smtp_port,
          "user": $smtp_user,
          "password": $smtp_pass,
          "secure": $smtp_secure,
          "disableStartTls": false
        }
      }
    ]' > "$CREDS_FILE"

chmod 600 "$CREDS_FILE"
chown 1000:1000 "$CREDS_FILE"

info "$(t n8n_creds_importing)"
# Output is captured (not streamed) so a missing owner account can be told
# apart from a genuine failure. n8n's own wording for it — verified in
# n8n@1.123.67's import commands — is "Failed to find owner."; both
# import:credentials and import:workflow raise it identically.
set +e
creds_output="$(docker exec smartrag-n8n n8n import:credentials \
    --input="$STAGING_CONTAINER/credentials.json" 2>&1)"
creds_rc=$?
set -e

if (( creds_rc != 0 )); then
    if grep -q "Failed to find owner" <<<"$creds_output"; then
        # Expected on a fresh install: nobody has completed n8n's one-time
        # owner-setup screen yet. Not an error — this phase simply can't run
        # yet, so bootstrap must not abort at the very end over a step that
        # is meant to come after a human logs in.
        #
        # But it must not read as success either: exiting 0 here is what let
        # a first install finish with a "Complete" banner while the ingest
        # webhook was never registered, so the first symptom was a 404 in
        # the Content Admin GUI, far away from this cause. EXIT_SKIPPED
        # lets the caller tell "done" from "couldn't run yet" and say so.
        warn "$(t n8n_owner_missing)"
        exit "$EXIT_SKIPPED"
    fi
    echo "$creds_output" >&2
    die "$(t n8n_creds_failed)"
fi
ok "$(t n8n_creds_done)"

# Plaintext secrets are in n8n's encrypted store now — drop the staged copy
# immediately rather than waiting for the EXIT trap.
rm -f "$CREDS_FILE"

if [[ -z "${SMTP_HOST:-}" ]]; then
    warn "$(t n8n_smtp_not_configured)"
fi

# ─── 2. Workflows ────────────────────────────────────────────────────────────
for wf in "${WORKFLOWS[@]}"; do
    src="$WORKFLOW_DIR/$wf"
    [[ -f "$src" ]] || die "$(t n8n_workflow_missing "$src")"
    jq empty "$src" 2>/dev/null || die "$(t n8n_workflow_invalid_json "$src")"

    cp "$src" "$STAGING_HOST/$wf"
    chown 1000:1000 "$STAGING_HOST/$wf"

    info "$(t n8n_workflow_importing "$wf")"
    if docker exec smartrag-n8n n8n import:workflow \
            --input="$STAGING_CONTAINER/$wf" >/dev/null 2>&1; then
        ok "$(t n8n_workflow_imported "$wf")"
    else
        docker exec smartrag-n8n n8n import:workflow \
            --input="$STAGING_CONTAINER/$wf" >&2 || true
        die "$(t n8n_workflow_failed "$wf")"
    fi
    rm -f "$STAGING_HOST/$wf"
done

# ─── 3. Activate ─────────────────────────────────────────────────────────────
# `import:workflow` always imports inactive in a non-queue deployment
# (--activeState=fromJson is rejected outside queue/multi-main mode), so
# activation is a separate, explicit step.
for wf_id in "${ACTIVATE_IDS[@]}"; do
    info "$(t n8n_workflow_activating "$wf_id")"
    if docker exec smartrag-n8n n8n update:workflow \
            --id="$wf_id" --active=true >/dev/null 2>&1; then
        ok "$(t n8n_workflow_activated "$wf_id")"
    else
        docker exec smartrag-n8n n8n update:workflow \
            --id="$wf_id" --active=true >&2 || true
        die "$(t n8n_workflow_activate_failed "$wf_id")"
    fi
done

# ─── 4. Restart n8n ──────────────────────────────────────────────────────────
# n8n's own CLI says so verbatim: "Activation or deactivation will not take
# effect if n8n is running. Please restart n8n for changes to take effect."
# Without this the webhook stays unregistered and an upload from the GUI
# fails with a 404 that looks like a bug in the GUI.
info "$(t n8n_restarting)"
if docker restart smartrag-n8n >/dev/null; then
    ok "$(t n8n_restarted)"
else
    die "$(t n8n_restart_failed)"
fi

# ─── 5. Verify ───────────────────────────────────────────────────────────────
# Everything above can report success while the webhook still isn't live:
# activation is keyed by the fixed ACTIVATE_IDS, and an id that doesn't match
# what the import actually created would leave the workflow inactive without
# any command here failing. So don't claim it works — ask.
#
# The probe itself lives in common.sh as n8n_webhook_state(), shared with
# admin.sh's status view so the two can never disagree about whether ingest
# is live (it also matches the Content Admin's System status page).
info "$(t n8n_verifying)"

N8N_LOCAL_URL="http://127.0.0.1:${N8N_PORT:-5678}"

# How long to wait for n8n to come back after the restart. 60s was too
# tight in practice — an n8n restarting against a busy Postgres regularly
# needs longer, and reporting "could not verify" for a system that was
# merely still booting sends the operator debugging a non-problem.
# Overridable for two honest reasons: a slow or heavily loaded server may
# need longer, and the test suite must not sit here for three minutes.
VERIFY_TIMEOUT="${N8N_VERIFY_TIMEOUT:-180}"

# The restart above means n8n is still booting; wait for its own healthz
# before drawing any conclusion from the webhook's answer.
verify_state="unreachable"
verify_waited=0
while (( verify_waited < VERIFY_TIMEOUT )); do
    if curl -sf --max-time 3 "$N8N_LOCAL_URL/healthz" >/dev/null 2>&1; then
        verify_state="$(n8n_webhook_state "$N8N_LOCAL_URL")"
        break
    fi
    sleep 3
    verify_waited=$(( verify_waited + 3 ))
done

case "$verify_state" in
    registered)
        ok "$(t n8n_verify_ok)"
        ok "$(t n8n_workflows_done)"
        ;;
    unregistered)
        die "$(t n8n_verify_not_registered)"
        ;;
    *)
        # n8n never came back up, or answered something unexpected. The
        # import itself may well have worked — but saying so here is
        # exactly the claim this verification exists to avoid. Report what
        # is actually known and where to see the real answer, and exit
        # non-zero so a caller can't treat this as a finished job.
        warn "$(t n8n_verify_unreachable "$N8N_LOCAL_URL" "$VERIFY_TIMEOUT")"
        warn "$(t n8n_verify_recheck)"
        # Not exit 1: nothing observably broke, and aborting a whole
        # install because n8n was still restarting would be wrong. But not
        # exit 0 either — the caller must not print a success banner over
        # an outcome nobody confirmed.
        exit "$EXIT_UNVERIFIED"
        ;;
esac
