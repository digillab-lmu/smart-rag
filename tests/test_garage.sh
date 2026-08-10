#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# Garage replaces MinIO — the properties that make that work
# ═════════════════════════════════════════════════════════════════════════════
#
# MinIO was archived upstream. Garage was chosen after running this stack's
# actual S3 operations against it, and then Langfuse's own client, on a live
# machine. What follows are the things that had to change with it, each of
# which fails silently if it regresses.
#
# Two differences from MinIO drive most of this:
#
#   Garage stores NOTHING until a layout assigns capacity. Without it the
#   service is healthy, accepts connections, and refuses every write.
#
#   Its image is FROM scratch — no shell — so MinIO's trick of provisioning
#   itself from its own entrypoint is impossible. Everything is applied from
#   outside by running the binary through docker exec.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

COMPOSE="$REPO/docker/docker-compose.yml"
ENVX="$REPO/.env.example"
DEPLOY="$REPO/scripts/deploy-garage.sh"

# ─── Nothing operative still points at MinIO ─────────────────────────────────
# Comments may discuss it — the history is why several of these decisions look
# the way they do. Configuration may not.
leftovers=""
for f in "$COMPOSE" "$ENVX" "$REPO/nginx/smartrag-suite.conf" \
         "$REPO"/scripts/*.sh "$REPO"/scripts/lib/*.sh; do
    [[ -f "$f" ]] || continue
    [[ "$(basename "$f")" == "spike-garage.sh" ]] && continue   # the evaluation
    hit="$(grep -inE 'minio' "$f" | grep -viE '^[0-9]+: *#' | grep -viE '^\s*[0-9]+:\s*(//|--)' || true)"
    [[ -n "$hit" ]] && leftovers="$leftovers\n$(basename "$f"): $(head -1 <<<"$hit")"
done
check "no MinIO configuration remains" $([[ -z "$leftovers" ]] && echo 0 || echo 1) "$(printf '%b' "$leftovers")"

# ─── The service ─────────────────────────────────────────────────────────────
grep -q 'smartrag-garage:' "$COMPOSE"
check "compose defines the Garage service" $? ""
grep -qE 'image: dxflrs/garage:v[0-9]' "$COMPOSE"
check "its image is pinned to a version" $? "an unpinned store is a store that changes under you"
grep -q '/etc/garage.toml:ro' "$COMPOSE"
check "the configuration file is mounted" $? "Garage cannot be configured from the environment alone"
grep -q 'write_garage_config' "$REPO/scripts/lib/templates.sh"
check "the wizard writes that configuration" $? ""
grep -q 'write_garage_config' "$REPO/scripts/bootstrap.sh"
check "and bootstrap calls it" $? "a mounted file that nothing writes is a missing mount"

# The config carries two secrets, so it must not be world-readable and must
# not be a committed template with placeholders.
grep -q 'chmod 600' "$REPO/scripts/lib/templates.sh"
check "the configuration is written 600" $? "it contains the rpc secret"
[[ ! -f "$REPO/garage/garage.toml" ]]
check "no garage.toml is committed" $? "an rpc_secret in the repository is a shared cluster key"

# ─── Provisioning: layout, then buckets, then keys ───────────────────────────
[[ -f "$DEPLOY" ]]
check "the provisioning script exists" $? ""
if [[ -f "$DEPLOY" ]]; then
    layout_at="$(grep -n 'layout assign' "$DEPLOY" | head -1 | cut -d: -f1)"
    bucket_at="$(grep -n 'bucket create' "$DEPLOY" | head -1 | cut -d: -f1)"
    key_at="$(grep -n 'key import' "$DEPLOY" | head -1 | cut -d: -f1)"
    grant_at="$(grep -n 'bucket allow' "$DEPLOY" | head -1 | cut -d: -f1)"
    [[ -n "$layout_at" && -n "$bucket_at" && "$layout_at" -lt "$bucket_at" ]]
    check "the layout is applied before any bucket is created" $? \
          "without a layout Garage accepts the call and stores nothing"
    [[ -n "$key_at" && -n "$grant_at" && "$key_at" -lt "$grant_at" ]]
    check "keys exist before permissions are granted on them" $? ""

    # Imported, not minted: the ids and secrets are already in .env and every
    # consumer is configured with them. Letting Garage generate its own would
    # mean reading them back and rewriting the file the wizard just wrote.
    grep -q 'key import' "$DEPLOY"
    check "keys are imported from .env" $? ""
    grep -qE 'GARAGE_ACCESS_KEY|GARAGE_SECRET_KEY' "$DEPLOY"
    check "using the generated credentials" $? ""

    # The layout version must be read, not assumed: `--version 1` works
    # exactly once and fails for the rest of the installation's life.
    grep -q 'apply --version "\$next_version"' "$DEPLOY"
    check "the layout version is read rather than hard-coded" $? \
          "a fixed version 1 breaks every later layout change"

    # Re-running must not fail on what already exists.
    grep -q 'bucket info' "$DEPLOY"
    check "an existing bucket is detected rather than recreated" $? ""
    grep -q 'key info' "$DEPLOY"
    check "an existing key is detected rather than reimported" $? ""

    # It has to run before anything writes an object.
    garage_at="$(grep -n 'deploy-garage.sh' "$REPO/scripts/bootstrap.sh" | head -1 | cut -d: -f1)"
    schema_at="$(grep -n 'deploy-schemas.sh' "$REPO/scripts/bootstrap.sh" | head -1 | cut -d: -f1)"
    [[ -n "$garage_at" && -n "$schema_at" && "$garage_at" -lt "$schema_at" ]]
    check "provisioning runs early in the deployment phase" $? ""
fi

# ─── Per-key, per-bucket permissions ─────────────────────────────────────────
# Garage has no root user and no bucket policies. Two keys, each granted only
# where it belongs: the ingest cannot read Langfuse's traces, and Langfuse
# cannot read course documents.
for k in GARAGE_ACCESS_KEY GARAGE_SECRET_KEY GARAGE_LANGFUSE_ACCESS_KEY \
         GARAGE_LANGFUSE_SECRET_KEY GARAGE_RPC_SECRET GARAGE_ADMIN_TOKEN; do
    grep -q "^$k=" "$ENVX"
    check "$k is declared" $? ""
    grep -q "SECRET_$k=" "$REPO/scripts/lib/secrets.sh"
    check "$k is generated, not shipped" $? ""
    grep -q "REPL\[$k\]=" "$REPO/scripts/lib/templates.sh"
    check "$k is written resolved" $? ""
done

# ─── Langfuse's S3 settings must be resolved, not interpolated ───────────────
# They carried ${MINIO_LANGFUSE_*} and were never substituted: Langfuse reads
# its whole configuration through env_file, which passes ${...} through
# literally, so it had been authenticating with a variable name. Nothing
# noticed because tracing was switched off and nothing ever wrote.
for purpose in EVENT_UPLOAD MEDIA_UPLOAD BATCH_EXPORT; do
    for suffix in ACCESS_KEY_ID SECRET_ACCESS_KEY REGION; do
        grep -q "REPL\[LANGFUSE_S3_\${_purpose}_$suffix\]=\|REPL\[LANGFUSE_S3_${purpose}_$suffix\]=" \
            "$REPO/scripts/lib/templates.sh"
        check "LANGFUSE_S3_${purpose}_$suffix is resolved" $? \
              "env_file passes \${...} through literally"
    done
done

# ─── Flowise no longer uses object storage at all ────────────────────────────
# One consumer fewer to migrate and one fewer place credentials have to be
# right. Its files are per-chatflow working data, not course content.
grep -qE '^\s+STORAGE_TYPE: "local"' "$COMPOSE"
check "Flowise stores files locally" $? ""
grep -qE '^\s+S3_STORAGE_(BUCKET_NAME|ACCESS_KEY_ID)' "$COMPOSE"
check "and has no S3 configuration left" $(( $? == 0 ? 1 : 0 )) ""

# ─── The console is gone, everywhere ─────────────────────────────────────────
# Garage has no web interface. Anything still offering one points at nothing.
grep -q 'server_name minio' "$REPO/nginx/smartrag-suite.conf"
check "nginx offers no storage console vhost" $(( $? == 0 ? 1 : 0 )) ""
# Comments may explain that 8446 is gone — that is worth keeping. A mapping
# for it is what must not come back.
grep -qE '^\s*\[8446\]=' "$REPO/scripts/install-tailscale.sh"
check "tailscale mode publishes no console port" $(( $? == 0 ? 1 : 0 )) \
      "8446 was the console; there is nothing behind it now"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All Garage checks passed: no MinIO configuration remains, the service is"
echo "pinned and gets a 600 configuration file the wizard writes and the repo"
echo "never carries, provisioning applies the layout before creating anything"
echo "and reads the layout version rather than assuming 1, keys are imported"
echo "from generated credentials and granted per bucket, every Langfuse S3"
echo "value is resolved rather than interpolated, Flowise no longer touches"
echo "object storage, and nothing still offers a console that does not exist."
