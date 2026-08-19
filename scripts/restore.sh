#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Restore an installation from a backup (Phase 8)
# ═════════════════════════════════════════════════════════════════════════════
#
# A restore onto a second machine *is* a move, which is why there is one
# command for both. What makes it non-trivial is not the copying:
#
#   * **A data directory is not portable across Postgres major versions.**
#     Not "mostly works" — Postgres refuses to start. So the archive's own
#     PG_VERSION is compared against the major this installation will run,
#     and a mismatch stops here rather than at a container that restart-loops.
#
#   * **The .env and the data directories are one thing.** Postgres, Neo4j and
#     ClickHouse read their password once, at initdb; N8N_ENCRYPTION_KEY
#     decrypts every credential n8n holds. An archive whose halves were taken
#     at different times produces databases nobody can open, and the failure
#     appears as an authentication error days later. The manifest carries
#     fingerprints of those secrets, and they are checked.
#
#   * **The address is baked into .env**, and a machine with a different
#     address needs a rename, not a copy. This never happens silently: the
#     restore states the archive's address, and renaming is a separate flag
#     that first prints what it will and will not reach.
#
# This refuses rather than guesses. Every check that fails stops the restore
# before anything on the target is touched.
#
# Usage:
#   sudo bash scripts/restore.sh ARCHIVE.tar.gz [--lang en|de]
#                                [--rename NEW_DOMAIN] [--force] [--dry-run]
#
#   --rename D   the installation will answer at D instead of the archive's
#                address. Prints what it rewrites before doing it.
#   --force      proceed although the target already holds data. The existing
#                data directory is moved aside, never deleted.
#   --dry-run    run every check and print what would happen. Touches nothing.
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

ARCHIVE=""
RENAME=""
FORCE=0
DRY_RUN=0
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        --rename) shift; RENAME="${1:-}" ;;
        --rename=*) RENAME="${1#*=}" ;;
        --force) FORCE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -*) die "Unknown argument: $1" ;;
        *) ARCHIVE="$1" ;;
    esac
    shift
done
LANG_CHOICE="${LANG_CHOICE:-$(detect_default_language)}"
export LANG_CHOICE

[[ -n "$ARCHIVE" ]] || die "$(t restore_no_archive)"
[[ -f "$ARCHIVE" ]] || die "$(t restore_archive_missing "$ARCHIVE")"

header "$(t restore_title)"
(( DRY_RUN )) && warn "$(t restore_dry_run)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ─── 1. The archive is what it says it is ────────────────────────────────────
if [[ -f "$ARCHIVE.sha256" ]]; then
    info "$(t restore_checking_sum)"
    expected="$(cat "$ARCHIVE.sha256")"
    actual="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
    [[ "$expected" == "$actual" ]] || die "$(t restore_sum_mismatch)"
    ok "$(t restore_sum_ok)"
else
    # Not fatal: an archive copied by hand may arrive without it. But the
    # difference between "verified" and "not verified" has to be visible.
    warn "$(t restore_no_sum)"
fi

# Only the two small members first. Unpacking 40 GB of data to discover the
# Postgres version does not match is the wrong order of operations.
tar -C "$WORK" -xzf "$ARCHIVE" manifest.toml env 2>/dev/null \
    || die "$(t restore_not_an_archive)"
[[ -f "$WORK/manifest.toml" && -f "$WORK/env" ]] || die "$(t restore_not_an_archive)"

manifest_value() {   # $1 = key
    local line
    line="$(grep -m1 "^$1 = " "$WORK/manifest.toml" || true)"
    line="${line#*= }"
    line="${line%\"}"; line="${line#\"}"
    printf '%s' "$line"
}

archive_env_value() {   # $1 = key
    local line
    line="$(grep -m1 "^$1=" "$WORK/env" || true)"
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    printf '%s' "$line"
}

A_CREATED="$(manifest_value created_at)"
A_CONSISTENT="$(manifest_value consistent)"
A_PG="$(manifest_value postgres_major)"
A_DOMAIN="$(manifest_value domain)"
A_TAILSCALE="$(manifest_value tailscale_hostname)"
A_BASE="$(manifest_value base_data_path)"

# On a Tailscale deployment DOMAIN *is* the MagicDNS name, so the two values
# are the same string and printing both reads as a stutter — which is exactly
# how it came out on the first real run.
A_ADDRESS="$A_DOMAIN"
[[ -n "$A_TAILSCALE" && "$A_TAILSCALE" != "$A_DOMAIN" ]] \
    && A_ADDRESS="${A_ADDRESS:+$A_ADDRESS }$A_TAILSCALE"
info "$(t restore_archive_from "$A_CREATED" "${A_ADDRESS:-—}")"

# A backup taken with --running is a torn copy of the databases. It may
# restore perfectly and it may not, and nobody finds out until Postgres tries
# to replay it. Saying so is the whole reason the flag records itself.
if [[ "$A_CONSISTENT" != "true" ]]; then
    warn "$(t restore_torn_archive)"
    if (( ! FORCE )) && ! confirm restore_torn_continue "n"; then
        die "$(t restore_aborted)"
    fi
fi

# ─── 2. The two halves belong together ───────────────────────────────────────
# The failure this prevents does not look like a failure. A data directory
# beside the wrong .env starts, refuses the password, and reads as a
# configuration mistake — for as long as it takes somebody to conclude the
# backup was fine and the restore was botched.
info "$(t restore_checking_identity)"
fingerprint() {   # $1 = key
    local value; value="$(archive_env_value "$1")"
    if [[ -z "$value" ]]; then printf 'absent'; return; fi
    printf '%s' "$value" | sha256sum | cut -c1-16
}
IDENTITY_OK=1
for pair in "fp_postgres_password:POSTGRES_PASSWORD" \
            "fp_n8n_encryption_key:N8N_ENCRYPTION_KEY" \
            "fp_neo4j_password:NEO4J_PASSWORD" \
            "fp_clickhouse_password:CLICKHOUSE_PASSWORD" \
            "fp_encryption_key:ENCRYPTION_KEY"; do
    key="${pair%%:*}"; env_key="${pair#*:}"
    recorded="$(manifest_value "$key")"
    [[ -n "$recorded" ]] || continue     # manifest from an older schema
    if [[ "$recorded" != "$(fingerprint "$env_key")" ]]; then
        err "$(t restore_identity_mismatch "$env_key")"
        IDENTITY_OK=0
    fi
done
(( IDENTITY_OK )) || die "$(t restore_identity_failed)"
ok "$(t restore_identity_ok)"

# ─── 3. Postgres major version ───────────────────────────────────────────────
# From the compose file, because that is what will actually start. Reading it
# from the image tag rather than a variable: the tag is the pinned truth, and
# an installation whose tag moved is exactly the case this must catch.
TARGET_PG="$(grep -m1 -oE 'image: postgres:[0-9]+' "$REPO_ROOT/docker/docker-compose.yml" \
             | grep -oE '[0-9]+$' || true)"
info "$(t restore_checking_postgres "$A_PG" "${TARGET_PG:-?}")"
if [[ -n "$TARGET_PG" && -n "$A_PG" && "$A_PG" != "unknown" && "$A_PG" != "$TARGET_PG" ]]; then
    die "$(t restore_postgres_mismatch "$A_PG" "$TARGET_PG")"
fi
ok "$(t restore_postgres_ok)"

# ─── 4. The address ──────────────────────────────────────────────────────────
# Never silent, in either direction: keeping the archive's address is stated,
# and changing it prints what the change reaches and what it does not.
TARGET_ENV="$REPO_ROOT/.env"
NEW_DOMAIN="$A_DOMAIN"
if [[ -n "$RENAME" ]]; then
    NEW_DOMAIN="$RENAME"
    header "$(t restore_rename_heading "$A_DOMAIN" "$RENAME")"
    echo "$(t restore_rename_rewrites)"
    echo "$(t restore_rename_derived)"
    echo ""
    warn "$(t restore_rename_not_reached)"
    if (( ! FORCE )) && ! confirm restore_rename_confirm "n"; then
        die "$(t restore_aborted)"
    fi
else
    info "$(t restore_address_kept "${A_DOMAIN:-${A_TAILSCALE:-—}}")"
fi

# ─── 5. The target ───────────────────────────────────────────────────────────
TARGET_BASE="$A_BASE"
if [[ -f "$TARGET_ENV" ]]; then
    existing_base="$(grep -m1 '^BASE_DATA_PATH=' "$TARGET_ENV" | cut -d= -f2- | tr -d '"' || true)"
    [[ -n "$existing_base" ]] && TARGET_BASE="$existing_base"
fi
info "$(t restore_target "$TARGET_BASE")"

OCCUPIED=0
[[ -d "$TARGET_BASE" ]] && [[ -n "$(ls -A "$TARGET_BASE" 2>/dev/null)" ]] && OCCUPIED=1
if (( OCCUPIED )); then
    warn "$(t restore_target_occupied "$TARGET_BASE")"
    if (( ! FORCE )); then
        die "$(t restore_target_occupied_refuse)"
    fi
fi

FREE_KB="$(df -Pk "$(dirname "$TARGET_BASE")" | awk 'NR==2{print $4}')"
NEED_KB="$(manifest_value size_kb)"; NEED_KB="${NEED_KB:-0}"
if (( NEED_KB > 0 && FREE_KB < NEED_KB )); then
    die "$(t restore_no_space "$(( NEED_KB / 1024 ))" "$(( FREE_KB / 1024 ))")"
fi

if (( DRY_RUN )); then
    # A dry run that says "this would work" while leaving out that it would
    # move the live installation aside is not a dry run, it is a reassurance.
    (( OCCUPIED )) && warn "$(t restore_dry_run_would_replace "$TARGET_BASE")"
    [[ -n "$RENAME" ]] && warn "$(t restore_dry_run_would_rename "$A_DOMAIN" "$RENAME")"
    ok "$(t restore_dry_run_done)"
    exit 0
fi

# ─── 6. Do it ────────────────────────────────────────────────────────────────
info "$(t restore_stopping)"
bash "$SCRIPT_DIR/compose.sh" down >/dev/null 2>&1 || warn "$(t restore_stop_failed)"

# Moved aside, never deleted. A restore that turns out to be the wrong archive
# has to be survivable, and the operator is the one who decides when the old
# data is no longer needed.
if (( OCCUPIED )); then
    ASIDE="$TARGET_BASE.replaced-$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$TARGET_BASE" "$ASIDE"
    warn "$(t restore_moved_aside "$ASIDE")"
fi

info "$(t restore_unpacking)"
tar -C "$WORK" -xzf "$ARCHIVE" data.tar || die "$(t restore_unpack_failed)"
mkdir -p "$(dirname "$TARGET_BASE")"
tar --numeric-owner -C "$(dirname "$TARGET_BASE")" -xf "$WORK/data.tar" \
    || die "$(t restore_unpack_failed)"

# The archive's directory name may differ from this host's BASE_DATA_PATH.
UNPACKED="$(dirname "$TARGET_BASE")/$(basename "$A_BASE")"
if [[ "$UNPACKED" != "$TARGET_BASE" && -d "$UNPACKED" ]]; then
    mv "$UNPACKED" "$TARGET_BASE"
fi

# ─── 7. The .env, with the rename applied ────────────────────────────────────
if [[ -f "$TARGET_ENV" ]]; then
    cp "$TARGET_ENV" "$TARGET_ENV.replaced-$(date -u +%Y%m%dT%H%M%SZ)"
fi
cp "$WORK/env" "$TARGET_ENV"
chmod 600 "$TARGET_ENV"

if [[ -n "$RENAME" ]]; then
    set_env_var "$TARGET_ENV" DOMAIN "$RENAME"
    # BASE_DATA_PATH follows this host, not the archive's.
    set_env_var "$TARGET_ENV" BASE_DATA_PATH "$TARGET_BASE"
    ok "$(t restore_renamed "$RENAME")"
fi
set_env_var "$TARGET_ENV" BASE_DATA_PATH "$TARGET_BASE"

ok "$(t restore_unpacked)"

# ─── 8. What the operator has to check, because a restore that starts every
#        container is not a restore that works ───────────────────────────────
header "$(t restore_next_heading)"
echo "$(t restore_next_start)"
echo "$(t restore_next_verify)"
[[ -n "$RENAME" ]] && echo "$(t restore_next_rename)"
echo "$(t restore_next_garage)"
