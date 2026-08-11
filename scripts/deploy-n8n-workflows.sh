#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Import n8n credentials + workflows (Phase 10)
# ═════════════════════════════════════════════════════════════════════════════
#
# Provisions everything n8n needs to actually run the ingest pipeline:
#   1. Credentials (Garage/S3 + SMTP) — staged as plain JSON, imported via
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

# The values the credentials are built from. Under `set -u` a missing one
# aborts with a bare "unbound variable" from the middle of a jq invocation,
# which says nothing about what to fix — and it happened, in this script's
# own test sandbox, the moment the Postgres credential was added. Named
# checks instead, so the message points at the .env key.
for _required in POSTGRES_USER POSTGRES_PASSWORD GARAGE_ACCESS_KEY GARAGE_SECRET_KEY; do
    [[ -n "${!_required:-}" ]] || die "$(t n8n_env_missing "$_required")"
done
require_command docker
# Used by the final verification step. Declared here so a missing curl says
# so plainly instead of surfacing later as "n8n could not be reached".
require_command curl

header "$(t phase_n8n_workflows)"

# ─── Preconditions ───────────────────────────────────────────────────────────
_container_ready() { container_ready "$1"; }

_container_ready smartrag-n8n || die "$(t schema_container_not_healthy "smartrag-n8n")"

WORKFLOW_DIR="$REPO_ROOT/n8n/workflows-ingest"
[[ -d "$WORKFLOW_DIR" ]] || die "$(t n8n_workflow_dir_missing "$WORKFLOW_DIR")"

CORE_DIR="$REPO_ROOT/n8n/workflows"
[[ -d "$CORE_DIR" ]] || die "$(t n8n_workflow_dir_missing "$CORE_DIR")"

# Every workflow this installation runs, as directory:file. Both directories
# are deployed — n8n/workflows/ used to be imported by nothing at all while
# its README claimed bootstrap did it, so the memory and observability
# pipelines were documented, present, and never running.
#
# A third ingest workflow (WhisperX audio transcription) was removed from the
# repo rather than generalized; see that directory's README.
WORKFLOWS=(
    "$WORKFLOW_DIR/ingest-chunk-and-embed.json"
    "$WORKFLOW_DIR/ingest-document.json"
    "$CORE_DIR/chathistory-sync.json"
    "$CORE_DIR/usermemory-summary.json"
)
# Only workflows with a real trigger need activating. The chunk+embed
# sub-workflow is reached via an Execute Workflow node, which doesn't
# require it to be active.
ACTIVATE_IDS=(
    "smartrag-ingest-document"
    "smartrag-chathistory-sync"
    "smartrag-usermemory-summary"
)

# The Langfuse trace patcher only makes sense where Langfuse runs. Deployed
# and activated with the observability profile, left out otherwise — an
# always-failing scheduled workflow every 30 minutes is noise that teaches
# people to ignore the execution list.
LANGFUSE_ENABLED=0
if [[ ",${COMPOSE_PROFILES:-}," == *",observability,"* ]]; then
    LANGFUSE_ENABLED=1
    WORKFLOWS+=("$CORE_DIR/langfuse-userid-patch.json")
    ACTIVATE_IDS+=("smartrag-langfuse-userid-patch")
fi

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

# Garage speaks S3. forcePathStyle is required: it serves buckets as
# path segments (host/bucket), not as virtual-host subdomains.
jq -n \
    --arg endpoint "http://smartrag-garage:3900" \
    --arg region "${GARAGE_REGION}" \
    --arg access "${GARAGE_ACCESS_KEY}" \
    --arg secret "${GARAGE_SECRET_KEY}" \
    --arg pg_user "${POSTGRES_USER}" \
    --arg pg_pass "${POSTGRES_PASSWORD}" \
    --arg lf_public "${LANGFUSE_INIT_PROJECT_PUBLIC_KEY:-}" \
    --arg lf_secret "${LANGFUSE_INIT_PROJECT_SECRET_KEY:-}" \
    --arg smtp_host "${SMTP_HOST:-}" \
    --arg smtp_user "${SMTP_USER:-}" \
    --arg smtp_pass "${SMTP_PASSWORD:-}" \
    --argjson smtp_port "${SMTP_PORT:-25}" \
    --argjson smtp_secure "$([[ "${SMTP_SECURE:-false}" == "true" ]] && echo true || echo false)" \
    '[
      {
        "id": "smartrag-s3-credential",
        "name": "smartrag-s3",
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
        "id": "smartrag-postgres-credential",
        "name": "smartrag-postgres",
        "type": "postgres",
        "data": {
          "host": "smartrag-postgres",
          "port": 5432,
          "database": "flowise",
          "user": $pg_user,
          "password": $pg_pass,
          "ssl": "disable",
          "allowUnauthorizedCerts": false
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

if (( LANGFUSE_ENABLED )); then
    # Langfuse's public API authenticates with the project key pair as HTTP
    # basic auth — public key as the user, secret key as the password. Those
    # are the LANGFUSE_INIT_PROJECT_* values, not the Garage ones next to
    # them in .env, and not a variable called LANGFUSE_PUBLIC_KEY, which does
    # not exist.
    jq --arg lf_public "${LANGFUSE_INIT_PROJECT_PUBLIC_KEY:-}" \
       --arg lf_secret "${LANGFUSE_INIT_PROJECT_SECRET_KEY:-}" \
       '. + [{
          "id": "smartrag-langfuse-credential",
          "name": "smartrag-langfuse",
          "type": "httpBasicAuth",
          "data": { "user": $lf_public, "password": $lf_secret }
        }]' "$CREDS_FILE" > "$CREDS_FILE.tmp" && mv "$CREDS_FILE.tmp" "$CREDS_FILE"
fi

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
for src in "${WORKFLOWS[@]}"; do
    wf="$(basename "$src")"
    [[ -f "$src" ]] || die "$(t n8n_workflow_missing "$src")"
    jq empty "$src" 2>/dev/null || die "$(t n8n_workflow_invalid_json "$src")"
    # A workflow with no id imports as a new object every time, so a re-run
    # would leave duplicates behind — and a duplicate of a scheduled workflow
    # runs twice. All three core workflows shipped without one.
    [[ "$(jq -r '.id // empty' "$src")" != "" ]] \
        || die "$(t n8n_workflow_no_id "$src")"

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
# ─── Nobody's ingest gets cut off ────────────────────────────────────────────
# Restarting n8n kills whatever it is running. That happened here: an upload
# started at 20:12:43, this script restarted n8n eleven seconds later, and the
# execution was recorded as "crashed" — with n8n's own hint blaming memory,
# which sent the diagnosis in the wrong direction for a while. A document
# takes minutes to convert, so the window is wide open on a real course.
_running_executions() {
    docker exec smartrag-postgres psql -U "${POSTGRES_USER}" -d n8n -t -A -c \
        "SELECT count(*) FROM execution_entity WHERE status IN ('running','new')" \
        2>/dev/null | tr -d '[:space:]'
}

running="$(_running_executions)"
if [[ "$running" =~ ^[0-9]+$ ]] && (( running > 0 )); then
    warn "$(t n8n_restart_busy "$running")"
    waited=0
    while (( waited < ${N8N_DRAIN_WAIT:-300} )); do
        sleep 10; waited=$(( waited + 10 ))
        running="$(_running_executions)"
        [[ "$running" =~ ^[0-9]+$ ]] || break
        (( running == 0 )) && break
        info "$(t n8n_restart_waiting "$running" "$waited")"
    done
    if [[ "$running" =~ ^[0-9]+$ ]] && (( running > 0 )); then
        # Still busy. The operator decides — but the default is not to
        # destroy work in progress.
        if ! confirm n8n_restart_anyway "n"; then
            warn "$(t n8n_restart_declined)"
            exit "$EXIT_UNVERIFIED"
        fi
    else
        ok "$(t n8n_restart_drained)"
    fi
fi

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

# Two separate waits, because they are two separate questions.
#
# The first is "is n8n back at all", answered by its own healthz. The second
# is "is the webhook registered", and the answer right after a restart is
# routinely "not yet" for a few seconds — n8n serves healthz before it has
# finished registering webhooks.
#
# Conflating them produced a warning that was simply untrue: the loop broke
# the moment healthz answered, took whatever the webhook said, and if that
# was anything unexpected reported "n8n did not come back within 180s" — a
# timeout that had not elapsed and a service that was up. An operator who
# checks and finds n8n running then has to decide whether to believe the
# installer, which is the worst position to put them in.
WEBHOOK_SETTLE="${N8N_WEBHOOK_SETTLE:-30}"

verify_state="unreachable"
n8n_back=0
verify_waited=0
while (( verify_waited < VERIFY_TIMEOUT )); do
    if curl -sf --max-time 3 "$N8N_LOCAL_URL/healthz" >/dev/null 2>&1; then
        n8n_back=1
        break
    fi
    sleep 3
    verify_waited=$(( verify_waited + 3 ))
done

if (( n8n_back )); then
    # Only "registered" ends the wait. The other two answers are both
    # legitimate transients right after a restart: n8n serves healthz before
    # it has registered any webhook, and until it has, the probe gets either
    # nothing at all or n8n's own "is not registered" — which is also what a
    # genuinely inactive workflow returns. The two cannot be told apart in a
    # single reading, only by waiting: this aborted an install whose webhook
    # answered correctly moments later, because the first version of this
    # loop treated "unregistered" as final.
    settle_waited=0
    settle_announced=0
    while true; do
        verify_state="$(n8n_webhook_state "$N8N_LOCAL_URL")"
        [[ "$verify_state" == "registered" ]] && break
        (( settle_waited >= WEBHOOK_SETTLE )) && break
        # Said once, when it turns out there is something to wait for. A
        # silent pause of half a minute after "n8n restarted" reads as a
        # hang, and the operator's next move is Ctrl-C — in the middle of
        # the one step that decides whether uploads work.
        if (( ! settle_announced )); then
            info "$(t n8n_verify_settling "$WEBHOOK_SETTLE")"
            settle_announced=1
        fi
        sleep 3
        settle_waited=$(( settle_waited + 3 ))
    done
fi

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
        # Which of the two failures this is decides what the operator does
        # next, so it decides what they are told.
        if (( n8n_back )); then
            # n8n is up. The webhook answered something this installer does
            # not recognise, and the raw answer is the only useful thing to
            # show — paraphrasing it is what produced a message claiming a
            # timeout that never happened.
            warn "$(t n8n_verify_odd_reply "$WEBHOOK_SETTLE" \
                    "$(curl -s --max-time 5 "$N8N_LOCAL_URL/webhook/document-ingest" | head -c 200)")"
        else
            warn "$(t n8n_verify_unreachable "$N8N_LOCAL_URL" "$VERIFY_TIMEOUT")"
            warn "$(t n8n_verify_container "$(container_health smartrag-n8n)")"
        fi
        warn "$(t n8n_verify_recheck)"
        # Not exit 1: nothing observably broke, and aborting a whole
        # install because n8n was still restarting would be wrong. But not
        # exit 0 either — the caller must not print a success banner over
        # an outcome nobody confirmed.
        exit "$EXIT_UNVERIFIED"
        ;;
esac
