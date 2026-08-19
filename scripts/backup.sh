#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Back up the whole installation (Phase 8)
# ═════════════════════════════════════════════════════════════════════════════
#
# Everything that matters lives in two places: BASE_DATA_PATH and .env. Nothing
# else on the host is irreplaceable — nginx configuration and certificates are
# regenerated, images are pinned and pulled. So this archives those two, and
# the whole difficulty is in three details that have nothing to do with
# copying:
#
#   1. **.env is not optional and not separable.** Postgres, Neo4j and
#      ClickHouse each read their password once, when their data directory is
#      first created. A data directory restored beside a different .env is
#      unreadable — not "wrong password", unreadable. The same is true of
#      N8N_ENCRYPTION_KEY: without the original, every credential stored in
#      n8n is ciphertext nobody can decrypt. The two therefore travel in one
#      archive or not at all, and this script refuses to make an archive of
#      only one of them.
#
#   2. **A copy taken while the services run is a torn copy.** Postgres and
#      ClickHouse write pages continuously; a tar of a running data directory
#      is a snapshot of no consistent moment. They are stopped first. This
#      system tolerates a few minutes of downtime, and a backup that needs
#      explaining is worse than one that costs an outage.
#
#   3. **The address is baked in.** That matters at restore time, not here —
#      but it is recorded in the manifest, so the restore can say the target
#      differs rather than discovering it afterwards.
#
# Usage:
#   sudo bash scripts/backup.sh [--lang en|de] [--to DIR] [--keep N] [--running]
#
#   --to DIR    where the archive goes (default: BASE_DATA_PATH/../backups)
#   --keep N    delete older archives, keeping the newest N (default: keep all)
#   --running   do NOT stop the services. Produces a torn copy of the
#               databases and says so in the manifest. For a quick copy of an
#               installation that is not being written to; never for one that
#               has to be restorable.
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

DEST=""
KEEP=0
STOP_SERVICES=1
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        --to) shift; DEST="${1:-}" ;;
        --to=*) DEST="${1#*=}" ;;
        --keep) shift; KEEP="${1:-0}" ;;
        --keep=*) KEEP="${1#*=}" ;;
        --running) STOP_SERVICES=0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
LANG_CHOICE="${LANG_CHOICE:-$(detect_default_language)}"
export LANG_CHOICE

ENV_FILE="$REPO_ROOT/.env"
[[ -f "$ENV_FILE" ]] || die "$(t backup_no_env "$ENV_FILE")"

# Read, not source: .env is data. Sourcing it would run whatever a value
# happens to contain, and this script is run as root.
read_env_value() {   # $1 = key
    local line
    line="$(grep -m1 "^$1=" "$ENV_FILE" || true)"
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    printf '%s' "$line"
}

BASE_DATA_PATH="$(read_env_value BASE_DATA_PATH)"
[[ -n "$BASE_DATA_PATH" ]] || die "$(t backup_no_base_path)"
[[ -d "$BASE_DATA_PATH" ]] || die "$(t backup_base_path_missing "$BASE_DATA_PATH")"

DEST="${DEST:-$(dirname "$BASE_DATA_PATH")/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="smartrag-$STAMP"
WORK="$DEST/.$NAME.partial"
ARCHIVE="$DEST/$NAME.tar.gz"

header "$(t backup_title)"

# ─── What will be copied, and how big ────────────────────────────────────────
# Said before anything is stopped. An operator who learns the size after the
# outage has started cannot use the number for anything.
info "$(t backup_source "$BASE_DATA_PATH")"
SIZE_KB="$(du -sk "$BASE_DATA_PATH" 2>/dev/null | cut -f1 || echo 0)"
FREE_KB="$(df -Pk "$DEST" 2>/dev/null | awk 'NR==2{print $4}' \
           || df -Pk "$(dirname "$DEST")" | awk 'NR==2{print $4}')"
info "$(t backup_size "$(( SIZE_KB / 1024 ))" "$(( FREE_KB / 1024 ))")"

# The archive compresses, but by how much depends on the data — chunk vectors
# barely compress at all. Requiring the full uncompressed size is the estimate
# that cannot be wrong in the direction that matters.
if (( FREE_KB < SIZE_KB )); then
    die "$(t backup_no_space "$(( SIZE_KB / 1024 ))" "$(( FREE_KB / 1024 ))")"
fi

mkdir -p "$DEST"
chmod 700 "$DEST"

# ─── Stop the services ───────────────────────────────────────────────────────
RESTART_AFTER=0
if (( STOP_SERVICES )); then
    info "$(t backup_stopping)"
    if bash "$SCRIPT_DIR/compose.sh" stop >/dev/null 2>&1; then
        RESTART_AFTER=1
        ok "$(t backup_stopped)"
    else
        # Not fatal by itself — but the archive must not claim to be
        # consistent when the stop did not happen.
        warn "$(t backup_stop_failed)"
        STOP_SERVICES=0
    fi
else
    warn "$(t backup_running_warning)"
fi

# Whatever happens below, the services come back. A backup that leaves the
# installation down because tar ran out of disk is a worse outage than the one
# it was taking.
restart_services() {
    if (( RESTART_AFTER )); then
        RESTART_AFTER=0
        info "$(t backup_restarting)"
        bash "$SCRIPT_DIR/compose.sh" start >/dev/null 2>&1 \
            && ok "$(t backup_restarted)" \
            || warn "$(t backup_restart_failed)"
    fi
}
trap 'restart_services; rm -rf "$WORK"' EXIT

mkdir -p "$WORK"

# ─── The manifest ────────────────────────────────────────────────────────────
# What a restore has to know before it touches anything, written before the
# copy so the copy can be verified against it.
#
# The Postgres major version comes from the data directory's own PG_VERSION
# file rather than from the image tag in docker-compose.yml: the tag says what
# would run, the file says what actually wrote these pages, and a restore onto
# a different major version does not work no matter which of the two is newer.
PG_VERSION="unknown"
[[ -f "$BASE_DATA_PATH/postgres/data/PG_VERSION" ]] \
    && PG_VERSION="$(tr -d '[:space:]' < "$BASE_DATA_PATH/postgres/data/PG_VERSION")"

# An identity for the installation, so a restore can tell "this archive's .env
# belongs with this archive's data" from "somebody assembled these". Hashes of
# the three secrets that a data directory is unreadable without — never the
# secrets themselves, because this manifest is stored in plaintext next to the
# archive and read by whoever looks at the backup directory.
secret_fingerprint() {   # $1 = key
    local value; value="$(read_env_value "$1")"
    if [[ -z "$value" ]]; then printf 'absent'; return; fi
    printf '%s' "$value" | sha256sum | cut -c1-16
}

DOMAIN="$(read_env_value DOMAIN)"
TAILSCALE_HOSTNAME="$(read_env_value TAILSCALE_HOSTNAME)"

{
    echo "schema = 1"
    echo "created_at = \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
    echo "created_by = \"scripts/backup.sh\""
    echo "consistent = $( ((STOP_SERVICES)) && echo true || echo false )"
    echo "base_data_path = \"$BASE_DATA_PATH\""
    echo "postgres_major = \"$PG_VERSION\""
    echo "domain = \"$DOMAIN\""
    echo "tailscale_hostname = \"$TAILSCALE_HOSTNAME\""
    echo "compose_profiles = \"$(read_env_value COMPOSE_PROFILES)\""
    echo "size_kb = $SIZE_KB"
    echo ""
    echo "# Fingerprints, not secrets. A restore compares these against the"
    echo "# .env inside the archive: if they differ, the two halves were not"
    echo "# taken together, and the data directories would be unreadable."
    echo "fp_postgres_password = \"$(secret_fingerprint POSTGRES_PASSWORD)\""
    echo "fp_n8n_encryption_key = \"$(secret_fingerprint N8N_ENCRYPTION_KEY)\""
    echo "fp_neo4j_password = \"$(secret_fingerprint NEO4J_PASSWORD)\""
    echo "fp_clickhouse_password = \"$(secret_fingerprint CLICKHOUSE_PASSWORD)\""
    echo "fp_encryption_key = \"$(secret_fingerprint ENCRYPTION_KEY)\""
} > "$WORK/manifest.toml"

# ─── The copy ────────────────────────────────────────────────────────────────
info "$(t backup_copying)"
cp "$ENV_FILE" "$WORK/env"
chmod 600 "$WORK/env"

# --numeric-owner: the uids inside the data directories are the container
# users', and they must survive a restore onto a host whose /etc/passwd
# assigns those numbers to somebody else. Restoring by name would hand
# Postgres's data directory to whoever happens to be called "postgres" there.
tar --numeric-owner -C "$(dirname "$BASE_DATA_PATH")" \
    -cf "$WORK/data.tar" "$(basename "$BASE_DATA_PATH")" \
    || die "$(t backup_tar_failed)"

# The archive is one file, so it cannot be half-copied to another machine
# without that being obvious.
tar -C "$WORK" -czf "$ARCHIVE.partial" manifest.toml env data.tar \
    || die "$(t backup_tar_failed)"
mv "$ARCHIVE.partial" "$ARCHIVE"
chmod 600 "$ARCHIVE"

sha256sum "$ARCHIVE" | awk '{print $1}' > "$ARCHIVE.sha256"

restart_services

ARCHIVE_MB="$(( $(stat -c%s "$ARCHIVE" 2>/dev/null || stat -f%z "$ARCHIVE") / 1048576 ))"
ok "$(t backup_done "$ARCHIVE" "$ARCHIVE_MB")"
(( STOP_SERVICES )) || warn "$(t backup_torn_note)"

# ─── Retention ───────────────────────────────────────────────────────────────
if (( KEEP > 0 )); then
    mapfile -t OLD < <(ls -1t "$DEST"/smartrag-*.tar.gz 2>/dev/null | tail -n +$(( KEEP + 1 )))
    for old in "${OLD[@]:-}"; do
        [[ -n "$old" ]] || continue
        rm -f "$old" "$old.sha256"
        dim "$(t backup_pruned "$(basename "$old")")"
    done
fi

dim "$(t backup_restore_hint "$ARCHIVE")"
