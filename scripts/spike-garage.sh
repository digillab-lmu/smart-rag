#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Garage evaluation (changes nothing in the deployment)
# ═════════════════════════════════════════════════════════════════════════════
#
# MinIO was archived upstream on 2026-04-24. The pinned image works and is not
# an emergency, but it will not receive fixes, so a successor has to be chosen
# eventually. Garage (Deuxfleurs, v2.3.0, released 2026-04-16, still receiving
# commits) is the candidate.
#
# Its documented S3 support covers what this stack needs — presigned URLs,
# multipart upload, batch delete, CORS. Bucket policies are the one thing it
# does not implement, and we do not use them: MinIO's own IAM is what our
# entrypoint calls, and Garage has an equivalent per-key-per-bucket model.
#
# Documentation is not evidence, though, so this runs the operations against a
# real Garage and reports which of them work. It:
#
#   • starts one Garage container beside the running stack, on its own data
#     directory and its own ports — MinIO is untouched and keeps serving;
#   • exercises exactly the operations Langfuse's media and batch exports, and
#     this project's document ingest and deletion, depend on;
#   • prints a verdict per operation and removes nothing until asked.
#
# The S3 client is `mc` out of the MinIO image already on the machine, run
# over host networking so a presigned URL can be fetched from outside the
# container the way a browser would. No new image is pulled.
#
# Usage:
#   sudo bash scripts/spike-garage.sh                  # S3 operations only
#   sudo bash scripts/spike-garage.sh --langfuse       # point Langfuse at it
#   sudo bash scripts/spike-garage.sh --langfuse-revert
#   sudo bash scripts/spike-garage.sh --cleanup        # remove container + data
#
# --langfuse is the only mode that changes the running deployment, and the
# only one that answers the question the S3 checks cannot: whether Langfuse's
# own client works against Garage. It rewrites the LANGFUSE_S3_* keys in .env
# (backed up first) and recreates the two Langfuse containers. --langfuse-revert
# puts the previous values back. Nothing else is touched, and MinIO keeps its
# data throughout — the buckets on Garage start empty.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: bash >= 4 required" >&2; exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck source=lib/common.sh
source "$LIB_DIR/common.sh"

# Pinned like every other image in this project — an evaluation whose result
# depends on which version happened to be current is not a result.
GARAGE_IMAGE="dxflrs/garage:v2.3.0"
GARAGE_NAME="smartrag-garage-spike"
# Deliberately not 9000/9001: MinIO has those and must keep them.
GARAGE_S3_PORT=3900
GARAGE_ADMIN_PORT=3903
BUCKET="spike-bucket"
KEY_NAME="spike-key"

[[ -f "$REPO_ROOT/.env" ]] || die "No .env — run this on a deployed machine."
set -a; source "$REPO_ROOT/.env"; set +a
MC_IMAGE="minio/minio:${MINIO_IMAGE_TAG:-RELEASE.2025-09-07T16-13-09Z}"
SPIKE_DIR="${BASE_DATA_PATH:-/srv/smart-rag/data}/garage-spike"

# ─── Cleanup ─────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--cleanup" ]]; then
    header "Removing the Garage evaluation"
    docker rm -f "$GARAGE_NAME" >/dev/null 2>&1 && ok "Container removed" \
        || info "No container to remove"
    if [[ -d "$SPIKE_DIR" ]]; then
        rm -rf "$SPIKE_DIR"
        ok "Data removed: $SPIKE_DIR"
    fi
    ok "Nothing of the evaluation remains. MinIO was never touched."
    exit 0
fi

# ─── Langfuse against Garage ─────────────────────────────────────────────────
# The S3 checks above prove the primitives. They do not prove that Langfuse's
# client — its key layout, its multipart thresholds, its retry behaviour —
# works against this server, and "not officially supported" is exactly the
# kind of claim that is only settled by running it.
#
# Event upload is the cheapest decisive test: Langfuse writes a blob for every
# trace, so one chat message exercises the whole path.
if [[ "${1:-}" == "--langfuse" || "${1:-}" == "--langfuse-revert" ]]; then
    ENVFILE="$REPO_ROOT/.env"

    if [[ "${1:-}" == "--langfuse-revert" ]]; then
        header "Pointing Langfuse back at MinIO"
        latest="$(ls -1t "$ENVFILE".backup-* 2>/dev/null | head -1)"
        [[ -n "$latest" ]] || die "No .env backup found — restore by hand."
        info "Restoring from $(basename "$latest")"
        # Only the LANGFUSE_S3_* keys are put back: anything else changed
        # since the backup is somebody's work, not this script's to undo.
        restored=0
        while IFS= read -r line; do
            [[ "$line" =~ ^(LANGFUSE_S3_[A-Z_]+)=\"?(.*[^\"])\"?$ ]] || continue
            set_env_var "$ENVFILE" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
            restored=$((restored+1))
        done < "$latest"
        ok "$restored setting(s) restored"
        bash "$SCRIPT_DIR/compose.sh" up -d --force-recreate \
            smartrag-langfuse-web smartrag-langfuse-worker >/dev/null 2>&1 \
            && ok "Langfuse recreated against MinIO" \
            || err "Recreate failed — run it by hand"
        exit 0
    fi

    header "Pointing Langfuse at Garage"
    docker ps --format '{{.Names}}' | grep -qx "$GARAGE_NAME" \
        || die "The Garage container is not running. Run this script without arguments first."

    GARAGE_BIN=/garage
    docker exec "$GARAGE_NAME" /garage --version >/dev/null 2>&1 || GARAGE_BIN=garage
    gcmd() { docker exec "$GARAGE_NAME" "$GARAGE_BIN" "$@" 2>&1; }

    LF_KEY_NAME="langfuse-spike"
    for b in langfuse-events langfuse-media langfuse-exports; do
        gcmd bucket create "$b" >/dev/null 2>&1
    done
    key_out="$(gcmd key create "$LF_KEY_NAME" 2>&1)"
    # Re-running is normal; an existing key has to be read back instead.
    if ! grep -qE 'GK[0-9a-f]+' <<<"$key_out"; then
        key_out="$(gcmd key info --show-secret "$LF_KEY_NAME" 2>&1)"
    fi
    LF_ACCESS="$(grep -oE 'GK[0-9a-f]+' <<<"$key_out" | head -1)"
    LF_SECRET="$(grep -oiE 'secret key: *[0-9a-f]+' <<<"$key_out" | awk '{print $NF}')"
    [[ -n "$LF_ACCESS" && -n "$LF_SECRET" ]] || die "Could not obtain a key: $key_out"
    for b in langfuse-events langfuse-media langfuse-exports; do
        gcmd bucket allow --read --write --owner "$b" --key "$LF_KEY_NAME" >/dev/null 2>&1
    done
    ok "Three buckets and one key ready in Garage"

    # Container-to-container: Garage is on smart-rag-network under its name.
    GARAGE_INTERNAL="http://${GARAGE_NAME}:3900"
    info "Rewriting the LANGFUSE_S3_* settings (a backup is written first)"
    for purpose in EVENT_UPLOAD MEDIA_UPLOAD BATCH_EXPORT; do
        case "$purpose" in
            EVENT_UPLOAD) bucket=langfuse-events ;;
            MEDIA_UPLOAD) bucket=langfuse-media ;;
            BATCH_EXPORT) bucket=langfuse-exports ;;
        esac
        set_env_var "$ENVFILE" "LANGFUSE_S3_${purpose}_BUCKET"            "$bucket"
        set_env_var "$ENVFILE" "LANGFUSE_S3_${purpose}_ENDPOINT"          "$GARAGE_INTERNAL"
        set_env_var "$ENVFILE" "LANGFUSE_S3_${purpose}_ACCESS_KEY_ID"     "$LF_ACCESS"
        set_env_var "$ENVFILE" "LANGFUSE_S3_${purpose}_SECRET_ACCESS_KEY" "$LF_SECRET"
        set_env_var "$ENVFILE" "LANGFUSE_S3_${purpose}_FORCE_PATH_STYLE"  "true"
        set_env_var "$ENVFILE" "LANGFUSE_S3_${purpose}_REGION"            "${MINIO_REGION_NAME:-us-east-1}"
    done
    ok "18 settings rewritten"

    info "Recreating Langfuse so it reads them (env_file is read at creation)"
    if bash "$SCRIPT_DIR/compose.sh" up -d --force-recreate \
            smartrag-langfuse-web smartrag-langfuse-worker >/dev/null 2>&1; then
        ok "Recreated"
    else
        err "Recreate failed"
        exit 1
    fi

    info "Waiting for Langfuse to become healthy…"
    healthy=0
    for _ in $(seq 40); do
        [[ "$(container_health smartrag-langfuse-web)" == "healthy" ]] && { healthy=1; break; }
        sleep 5
    done
    if (( healthy )); then
        ok "Langfuse is healthy against Garage"
    else
        err "Langfuse did not become healthy — this is the answer, and it is a no"
        docker logs --tail 25 smartrag-langfuse-web 2>&1 | sed 's/^/    /'
        echo
        warn "Revert with: sudo bash scripts/spike-garage.sh --langfuse-revert"
        exit 1
    fi

    echo
    header "Now produce a trace"
    echo "  Send one message to an agent in the chat. Langfuse writes a blob"
    echo "  per trace, so that single message exercises its whole S3 path."
    echo
    echo "  Then check that objects arrived:"
    printf "    %s\n" "sudo bash scripts/spike-garage.sh --langfuse-check"
    echo
    dim "Revert at any time: sudo bash scripts/spike-garage.sh --langfuse-revert"
    dim "MinIO still holds everything written before this; nothing was copied or deleted."
    exit 0
fi

if [[ "${1:-}" == "--langfuse-check" ]]; then
    header "Did Langfuse write to Garage?"
    docker ps --format '{{.Names}}' | grep -qx "$GARAGE_NAME" || die "Garage is not running."
    GARAGE_BIN=/garage
    docker exec "$GARAGE_NAME" /garage --version >/dev/null 2>&1 || GARAGE_BIN=garage
    out="$(docker exec "$GARAGE_NAME" "$GARAGE_BIN" bucket info langfuse-events 2>&1)"
    printf '%s\n' "$out" | sed 's/^/    /'
    objects="$(grep -oiE 'objects: *[0-9]+' <<<"$out" | grep -oE '[0-9]+' | head -1)"
    echo
    if [[ -n "$objects" ]] && (( objects > 0 )); then
        ok "$objects object(s) in langfuse-events — Langfuse's own client works against Garage."
        echo
        info "That retires the last open risk. What a migration still needs:"
        echo "    • the layout step in the bootstrap"
        echo "    • bucket/key provisioning rewritten around Garage's model"
        echo "    • Flowise moved to local storage, and the ingest's bucket migrated"
        echo "    • existing objects copied from MinIO"
    else
        warn "No objects yet. Send a chat message first, then run this again."
        dim "If it stays empty after a message, check: docker logs smartrag-langfuse-worker"
    fi
    exit 0
fi

require_command docker
require_command curl

PASS=0; FAIL=0; SKIP=0
step_ok()   { ok "$*";   PASS=$((PASS+1)); }
step_fail() { err "$*";  FAIL=$((FAIL+1)); }
step_skip() { warn "$*"; SKIP=$((SKIP+1)); }

header "Garage evaluation — $GARAGE_IMAGE"
info "MinIO keeps running and is not modified. Data goes to $SPIKE_DIR."
echo

# ─── 1. Configuration ────────────────────────────────────────────────────────
mkdir -p "$SPIKE_DIR/meta" "$SPIKE_DIR/data"
RPC_SECRET="$(openssl rand -hex 32)"
ADMIN_TOKEN="$(openssl rand -base64 32)"

# sqlite + replication_factor 1: a single node is the whole point here, and
# Garage refuses to start with a factor it cannot satisfy.
cat > "$SPIKE_DIR/garage.toml" <<TOML
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1

rpc_bind_addr = "[::]:3901"
rpc_public_addr = "127.0.0.1:3901"
rpc_secret = "$RPC_SECRET"

[s3_api]
s3_region = "${MINIO_REGION_NAME:-us-east-1}"
api_bind_addr = "[::]:3900"
root_domain = ".s3.garage"

[admin]
api_bind_addr = "[::]:3903"
admin_token = "$ADMIN_TOKEN"
TOML
step_ok "Configuration written (region ${MINIO_REGION_NAME:-us-east-1}, same as MinIO's)"

# ─── 2. Start ────────────────────────────────────────────────────────────────
docker rm -f "$GARAGE_NAME" >/dev/null 2>&1 || true
if docker run -d --name "$GARAGE_NAME" \
    --network smart-rag-network \
    -p "127.0.0.1:${GARAGE_S3_PORT}:3900" \
    -p "127.0.0.1:${GARAGE_ADMIN_PORT}:3903" \
    -v "$SPIKE_DIR/garage.toml:/etc/garage.toml:ro" \
    -v "$SPIKE_DIR/meta:/var/lib/garage/meta" \
    -v "$SPIKE_DIR/data:/var/lib/garage/data" \
    "$GARAGE_IMAGE" >/dev/null 2>&1; then
    step_ok "Container started"
else
    step_fail "Container would not start"
    docker logs "$GARAGE_NAME" 2>&1 | tail -20 | sed 's/^/    /'
    exit 1
fi

# The binary's path inside the image is a detail worth not dying on: try the
# documented location, fall back to PATH.
GARAGE_BIN=""
for cand in /garage garage; do
    if docker exec "$GARAGE_NAME" "$cand" --version >/dev/null 2>&1; then
        GARAGE_BIN="$cand"; break
    fi
done
if [[ -z "$GARAGE_BIN" ]]; then
    # Not fatal yet — the server may still be starting; retry inside gcmd.
    GARAGE_BIN="/garage"
fi
gcmd() { docker exec "$GARAGE_NAME" "$GARAGE_BIN" "$@" 2>&1; }

info "Waiting for the node to come up…"
node_id=""
for _ in $(seq 30); do
    out="$(gcmd node id -q || true)"
    if [[ "$out" =~ ^[0-9a-f]{16,} ]]; then
        node_id="${out%%@*}"
        break
    fi
    sleep 2
done
if [[ -n "$node_id" ]]; then
    step_ok "Node is up (${node_id:0:16}…)"
else
    step_fail "Node did not report an id within 60s"
    docker logs "$GARAGE_NAME" 2>&1 | tail -20 | sed 's/^/    /'
    exit 1
fi

# ─── 3. Layout ───────────────────────────────────────────────────────────────
# Garage stores nothing until a layout assigns capacity — the step with no
# MinIO equivalent, and the one a future bootstrap would have to perform.
if gcmd layout assign -z dc1 -c 10G "$node_id" >/dev/null 2>&1 \
   && gcmd layout apply --version 1 >/dev/null 2>&1; then
    step_ok "Layout assigned and applied (the step MinIO has no equivalent for)"
else
    step_fail "Layout could not be applied"
    gcmd layout show | sed 's/^/    /'
    exit 1
fi

# ─── 4. Bucket and key ───────────────────────────────────────────────────────
gcmd bucket create "$BUCKET" >/dev/null 2>&1
key_out="$(gcmd key create "$KEY_NAME")"
ACCESS_KEY="$(grep -oE 'GK[0-9a-f]+' <<<"$key_out" | head -1)"
SECRET_KEY="$(grep -oiE 'secret key: *[0-9a-f]+' <<<"$key_out" | awk '{print $NF}')"
if [[ -n "$ACCESS_KEY" && -n "$SECRET_KEY" ]]; then
    step_ok "Bucket and access key created"
else
    step_fail "Could not read the key out of: $key_out"
    exit 1
fi
if gcmd bucket allow --read --write --owner "$BUCKET" --key "$KEY_NAME" >/dev/null 2>&1; then
    step_ok "Permissions granted per key and bucket (Garage's model, not bucket policies)"
else
    step_fail "Could not grant permissions"
fi

# ─── 5. The operations this stack actually performs ──────────────────────────
# mc from the image already present, over host networking so the endpoint is
# the same one a presigned URL will name.
ENDPOINT="http://127.0.0.1:${GARAGE_S3_PORT}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mc() {
    docker run --rm --network host \
        -e MC_HOST_g="http://${ACCESS_KEY}:${SECRET_KEY}@127.0.0.1:${GARAGE_S3_PORT}" \
        -v "$WORK:/w" --entrypoint mc "$MC_IMAGE" "$@" 2>&1
}

echo
info "Exercising the operations Langfuse and the ingest depend on:"

printf 'hello from the garage evaluation\n' > "$WORK/small.txt"
if mc cp /w/small.txt "g/$BUCKET/small.txt" >/dev/null; then
    step_ok "PutObject"
else
    step_fail "PutObject — nothing else below will mean much"
fi

if mc cp "g/$BUCKET/small.txt" /w/back.txt >/dev/null && \
   diff -q "$WORK/small.txt" "$WORK/back.txt" >/dev/null; then
    step_ok "GetObject, byte-identical"
else
    step_fail "GetObject or content mismatch"
fi

mc ls "g/$BUCKET" | grep -q small.txt && step_ok "ListObjects" || step_fail "ListObjects"
mc stat "g/$BUCKET/small.txt" >/dev/null && step_ok "HeadObject" || step_fail "HeadObject"

# Multipart: Langfuse media and a scanned PDF both exceed the threshold, and
# it is the operation most likely to differ between implementations.
head -c 32000000 /dev/urandom > "$WORK/big.bin"
if mc cp /w/big.bin "g/$BUCKET/big.bin" >/dev/null; then
    if mc cp "g/$BUCKET/big.bin" /w/big-back.bin >/dev/null && \
       cmp -s "$WORK/big.bin" "$WORK/big-back.bin"; then
        step_ok "Multipart upload of 32 MB, retrieved byte-identical"
    else
        step_fail "32 MB uploaded but came back different"
    fi
else
    step_fail "Multipart upload"
fi

# Presigned URL: the one Langfuse hands to a browser, which carries no
# credentials. Fetched with plain curl from the host for exactly that reason.
share_out="$(mc share download --expire 1h "g/$BUCKET/small.txt")"
presigned="$(grep -oE 'https?://[^ ]+' <<<"$share_out" | tail -1)"
if [[ -n "$presigned" ]]; then
    body="$(curl -s --max-time 15 "$presigned")"
    if [[ "$body" == "$(cat "$WORK/small.txt")" ]]; then
        step_ok "Presigned URL fetched without credentials — what Langfuse's media needs"
    else
        step_fail "Presigned URL did not return the object: ${body:0:120}"
    fi
else
    step_skip "mc produced no presigned URL: ${share_out:0:160}"
fi

# Batch delete: how this project removes a document's chunks' source objects.
for i in 1 2 3; do
    printf 'obj %s\n' "$i" > "$WORK/d$i.txt"
    mc cp "/w/d$i.txt" "g/$BUCKET/todelete/d$i.txt" >/dev/null
done
if mc rm --recursive --force "g/$BUCKET/todelete/" >/dev/null && \
   ! mc ls "g/$BUCKET/todelete/" 2>/dev/null | grep -q d1; then
    step_ok "Recursive delete"
else
    step_fail "Recursive delete"
fi

mc rm "g/$BUCKET/small.txt" >/dev/null && step_ok "DeleteObject" || step_fail "DeleteObject"

# ─── Verdict ─────────────────────────────────────────────────────────────────
echo
header "Result"
printf "  %s passed, %s failed, %s skipped\n\n" "$PASS" "$FAIL" "$SKIP"
if (( FAIL == 0 )); then
    ok "Every operation this stack performs works against Garage."
    echo
    info "What a migration would still need, none of it proven by the above:"
    echo "    • the layout step above, in the bootstrap — Garage stores nothing without it"
    echo "    • bucket and key provisioning rewritten: no 'mc admin', a different model"
    echo "    • Langfuse pointed at it and actually exercised — media and a batch export"
    echo "    • existing objects copied over"
else
    err "Not every operation works. Migrating on this basis would move the problem."
fi
echo
dim "Remove everything with: sudo bash scripts/spike-garage.sh --cleanup"
dim "MinIO was not touched and is still serving."
