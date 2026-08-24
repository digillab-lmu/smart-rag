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

# ─── The configuration is a template in the repository, like every other ────
# Garage was the one component whose configuration existed nowhere but inside
# a heredoc in templates.sh: no file to review in a diff, nothing to look at
# in a garage/ directory, and — the part that bit — no way to re-create it
# without walking the wizard again.
TEMPLATE="$REPO/garage/garage.toml.template"
[[ -f "$TEMPLATE" ]]
check "the configuration lives in the repository as a template" $? \
      "every other component's config does: weaviate/schema.json, nginx/…, neo4j/…"
for ph in __RPC_SECRET__ __ADMIN_TOKEN__ __S3_REGION__; do
    grep -q "$ph" "$TEMPLATE"
    check "the template carries $ph" $? ""
done
# The reason a template and not the rendered file: it must hold no secret.
grep -qE '^(rpc_secret|admin_token) *= *"[^_]' "$TEMPLATE"
check "the template contains no secret literal" $(( $? == 0 ? 1 : 0 )) \
      "$(grep -nE '^(rpc_secret|admin_token)' "$TEMPLATE")"

# ─── Rendering it, and the two ways it goes wrong ───────────────────────────
render() {   # $1 = target, rest = environment assignments
    env -i PATH="$PATH" HOME="$HOME" LANG_CHOICE=en REPO_ROOT="$REPO" "${@:2}" bash -c '
        source "'"$REPO"'/scripts/lib/messages.sh"
        source "'"$REPO"'/scripts/lib/common.sh"
        source "'"$REPO"'/scripts/lib/templates.sh"
        write_garage_config "'"$1"'" "'"$REPO"'"
    ' 2>&1
}

TMP="$(mktemp -d)"
out="$(render "$TMP/garage.toml" GARAGE_RPC_SECRET=rpc-xyz GARAGE_ADMIN_TOKEN=adm-xyz GARAGE_REGION=eu-west-9)"
check "it renders from .env values alone, without the wizard" $? "$out"
grep -q 'rpc_secret = "rpc-xyz"' "$TMP/garage.toml"
check "the rpc secret is substituted" $? "$(cat "$TMP/garage.toml" 2>/dev/null)"
grep -q 'admin_token = "adm-xyz"' "$TMP/garage.toml"
check "the admin token is substituted" $? ""
grep -q 's3_region = "eu-west-9"' "$TMP/garage.toml"
check "the region is substituted" $? ""
grep -q '__' "$TMP/garage.toml"
check "no placeholder is left behind" $(( $? == 0 ? 1 : 0 )) "$(grep -o '__[A-Z_]*__' "$TMP/garage.toml")"
[[ "$(stat -c %a "$TMP/garage.toml" 2>/dev/null || stat -f %Lp "$TMP/garage.toml")" == "600" ]]
check "it is written 600 — it holds the cluster key" $? ""

# The wizard's own variable names must work too, or a first install breaks.
out="$(render "$TMP/wizard.toml" SECRET_GARAGE_RPC_SECRET=w-rpc SECRET_GARAGE_ADMIN_TOKEN=w-adm CFG_GARAGE_REGION=eu-north-1)"
grep -q 'rpc_secret = "w-rpc"' "$TMP/wizard.toml"
check "the wizard's SECRET_/CFG_ names render too" $? "$out"

# An empty secret would start a Garage that rejects every client, and the
# failure surfaces later as a signature error against a working-looking store.
out="$(render "$TMP/nosecret.toml" GARAGE_ADMIN_TOKEN=adm GARAGE_REGION=eu-central-1)"
check "an empty rpc secret is refused" $(( $? == 0 ? 1 : 0 )) "$out"
[[ ! -f "$TMP/nosecret.toml" ]]
check "and nothing is written" $? ""

# The failure this whole section exists for: Docker creates a DIRECTORY where
# a file mount's host path is missing. Garage then reads a directory as its
# configuration and restart-loops with "IO error: Is a directory", which names
# neither the file nor the cause — and it repeats forever, because the
# directory stays. Observed on a real install, 2026-08-24.
mkdir -p "$TMP/asdir/garage.toml"
out="$(render "$TMP/asdir/garage.toml" GARAGE_RPC_SECRET=r GARAGE_ADMIN_TOKEN=a)"
check "a directory in the file's place is refused" $(( $? == 0 ? 1 : 0 )) "$out"
grep -qi 'directory' <<<"$out"
check "and the refusal says so, with the repair" $? "$out"
grep -qi 'rmdir' <<<"$out"
check "naming rmdir rather than rm -rf" $? "$out"
rm -rf "$TMP"

# ─── Nothing starts before that file is a file ──────────────────────────────
STARTER="$REPO/scripts/start-services.sh"
grep -q 'garage.toml' "$STARTER"
check "start-services checks the configuration before compose runs" $? \
      "without it, the first start is what creates the directory"
starter_body="$(sed -n '/GARAGE_CONFIG=/,/^fi/p' "$STARTER")"
grep -q 'write_garage_config' <<<"$starter_body"
check "a missing file is written rather than refused" $? \
      "every value in it comes from .env, so there is nobody to ask"
grep -q 'svc_garage_config_is_dir' <<<"$starter_body"
check "a directory is refused rather than removed" $? \
      "removing something is the operator's call"
# Order matters: the check has to precede `compose up`, not follow it.
cfg_line="$(grep -n 'GARAGE_CONFIG=' "$STARTER" | head -1 | cut -d: -f1)"
up_line="$(grep -n 'up -d --remove-orphans' "$STARTER" | head -1 | cut -d: -f1)"
[[ -n "$cfg_line" && -n "$up_line" ]] && (( cfg_line < up_line ))
check "and it runs before the services start" $? "check at $cfg_line, up at $up_line"

# Writing the file is not enough on a host that has already started this
# container once. A bind mount is resolved when the container is CREATED, and
# `compose up` only restarts an existing one — nothing in the compose config
# changed. So the container keeps failing against a path that is now a good
# file. Measured on a real install: config written at 13:34, same error at
# 13:34:56, 13:35:56, 13:36:56.
grep -q 'docker rm -f smartrag-garage' <<<"$starter_body"
check "an existing container is removed so compose rebuilds it" $? \
      "restarting it re-uses the mount it resolved against the directory"
rm_line="$(grep -n 'docker rm -f smartrag-garage' "$STARTER" | head -1 | cut -d: -f1)"
[[ -n "$rm_line" ]] && (( rm_line < up_line ))
check "and that happens before compose runs" $? "rm at $rm_line, up at $up_line"
# Only in the branch that just wrote the file — never as a blanket removal.
awk -v s="$cfg_line" -v e="$up_line" 'NR>s && NR<e' "$STARTER" | grep -q 'write_garage_config'
check "the removal sits in the branch that repaired the file" $? ""

# ─── No stale MinIO machinery is described ──────────────────────────────────
# Two sentences outlived MinIO by four months: that buckets are created on
# startup, and that the object store fires a webhook at n8n on upload. Garage
# has no bucket notifications; the Content Admin posts to n8n itself. Both
# sent readers looking for machinery that is not there.
grep -qiE 'fires a webhook|MinIO upload' "$COMPOSE"
check "no comment claims the object store triggers the ingest" $(( $? == 0 ? 1 : 0 )) \
      "$(grep -niE 'fires a webhook|MinIO upload' "$COMPOSE")"
grep -q 'Redis, MinIO, n8n' "$COMPOSE"
check "the core profile does not still list MinIO" $(( $? == 0 ? 1 : 0 )) ""

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
