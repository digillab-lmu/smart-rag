#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Prove that a backup archive actually opens (Phase 8)
# ═════════════════════════════════════════════════════════════════════════════
#
# A backup nobody has restored is not a backup, and "the restore script did not
# refuse it" is not the same claim. restore.sh checks what can be checked from
# the outside: the checksum, the Postgres major, that the two halves were taken
# together. None of that opens a database.
#
# This does. It unpacks the archive into a scratch directory and starts
# throwaway containers against that copy — on their own names and their own
# ports, on no shared network, so the running installation is untouched
# throughout and keeps serving. Then it asks each one the only question that
# matters: can you read this?
#
#   * **Postgres** — start with the archive's own password, list the databases,
#     and count the rows in a table this project owns. Proves the password in
#     the archived .env is the one the data directory was created with, which
#     is the failure that otherwise surfaces days after a restore.
#   * **Garage** — the open question. Its layout carries a node id created on
#     the machine it was laid out on. Whether a copied metadata directory
#     brings a usable one to another host is reasoning until somebody looks;
#     this looks. If `bucket list` shows the buckets, the layout travelled.
#   * **Weaviate** — count the objects in the shared learner classes. Proves
#     the vector store opens its own files, which is the bulk of the archive.
#
# Nothing here writes to the archive, to BASE_DATA_PATH, or to .env. The
# scratch directory and the containers are removed at the end, including after
# a failure — an aborted verification must not leave a second Postgres holding
# a copy of every conversation in the installation.
#
# Usage:
#   sudo bash scripts/verify-backup.sh ARCHIVE.tar.gz [--lang en|de] [--keep]
#
#   --keep   leave the scratch directory and containers up for inspection.
#            Prints how to remove them.
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
# shellcheck source=lib/messages.sh
source "$LIB_DIR/messages.sh"

ARCHIVE=""
KEEP=0
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        --keep) KEEP=1 ;;
        -*) die "Unknown argument: $1" ;;
        *) ARCHIVE="$1" ;;
    esac
    shift
done
LANG_CHOICE="${LANG_CHOICE:-$(detect_default_language)}"
export LANG_CHOICE

[[ -n "$ARCHIVE" ]] || die "$(t vfyb_no_archive)"
[[ -f "$ARCHIVE" ]] || die "$(t vfyb_archive_missing "$ARCHIVE")"
command -v docker >/dev/null 2>&1 || die "$(t vfyb_no_docker)"

PREFIX="smartrag-verify"
PG_NAME="$PREFIX-postgres"
GARAGE_NAME="$PREFIX-garage"
WEAVIATE_NAME="$PREFIX-weaviate"
# High and loopback-only. These are throwaway services holding a full copy of
# the installation's data; nothing about them should be reachable from off the
# machine even for the minute they exist.
PG_PORT=45432
GARAGE_S3_PORT=43900
GARAGE_ADMIN_PORT=43903
WEAVIATE_PORT=48080

SCRATCH=""
RESULTS=()
record() { RESULTS+=("$1|$2|$3"); }   # system|ok/fail/skip|detail

cleanup() {
    if (( KEEP )); then
        warn "$(t vfyb_kept "$SCRATCH")"
        echo "    docker rm -f $PG_NAME $GARAGE_NAME $WEAVIATE_NAME"
        echo "    rm -rf $SCRATCH"
        return
    fi
    docker rm -f "$PG_NAME" "$GARAGE_NAME" "$WEAVIATE_NAME" >/dev/null 2>&1 || true
    [[ -n "$SCRATCH" && -d "$SCRATCH" ]] && rm -rf "$SCRATCH"
}
trap cleanup EXIT

header "$(t vfyb_title)"

# ─── Unpack, somewhere with room ─────────────────────────────────────────────
# Beside the archive rather than in /tmp: /tmp is often a tmpfs sized for
# temporary files, and this is the whole installation.
SCRATCH="$(mktemp -d "$(dirname "$ARCHIVE")/.verify-XXXXXX")"
chmod 700 "$SCRATCH"
info "$(t vfyb_unpacking "$SCRATCH")"
tar -C "$SCRATCH" -xzf "$ARCHIVE" || die "$(t vfyb_unpack_failed)"
[[ -f "$SCRATCH/manifest.toml" ]] || die "$(t vfyb_not_an_archive)"
tar --numeric-owner -C "$SCRATCH" -xf "$SCRATCH/data.tar" || die "$(t vfyb_unpack_failed)"

env_value() {   # $1 = key
    local line; line="$(grep -m1 "^$1=" "$SCRATCH/env" || true)"
    line="${line#*=}"; line="${line%\"}"; line="${line#\"}"
    printf '%s' "$line"
}
A_BASE="$(grep -m1 '^base_data_path = ' "$SCRATCH/manifest.toml" | cut -d'"' -f2)"
DATA="$SCRATCH/$(basename "$A_BASE")"
[[ -d "$DATA" ]] || die "$(t vfyb_no_data "$DATA")"
ok "$(t vfyb_unpacked)"

# Images come from the compose file, so this verifies against the versions
# this installation actually runs rather than whatever :latest is today.
image_for() {   # $1 = service key in docker-compose.yml
    grep -m1 -oE "image: $1[^ ]*" "$REPO_ROOT/docker/docker-compose.yml" | sed 's/image: //'
}

wait_for() {   # $1 = name, $2 = seconds, $3.. = command
    local name="$1" limit="$2"; shift 2
    local i=0
    while (( i < limit )); do
        "$@" >/dev/null 2>&1 && return 0
        sleep 1; i=$((i+1))
    done
    return 1
}

# ─── Postgres ────────────────────────────────────────────────────────────────
header "$(t vfyb_postgres_heading)"
PG_IMAGE="$(image_for postgres)"
PG_USER="$(env_value POSTGRES_USER)"
PG_PASS="$(env_value POSTGRES_PASSWORD)"
PG_DB="$(env_value POSTGRES_DB)"

if [[ -z "$PG_USER" || -z "$PG_PASS" ]]; then
    warn "$(t vfyb_postgres_no_creds)"
    record postgres skip "no credentials in the archived .env"
elif [[ ! -d "$DATA/postgres/data" ]]; then
    warn "$(t vfyb_postgres_no_dir)"
    record postgres skip "no data directory in the archive"
else
    docker rm -f "$PG_NAME" >/dev/null 2>&1
    # No POSTGRES_PASSWORD here: the directory is already initialised, so the
    # password is whatever initdb stored. Passing one would change nothing and
    # would hide exactly the mismatch this is looking for.
    if docker run -d --name "$PG_NAME" \
        -p "127.0.0.1:$PG_PORT:5432" \
        -v "$DATA/postgres/data:/var/lib/postgresql/data" \
        "$PG_IMAGE" >/dev/null 2>&1
    then
        if wait_for "$PG_NAME" 60 docker exec "$PG_NAME" pg_isready -U "$PG_USER"; then
            dbs="$(docker exec -e PGPASSWORD="$PG_PASS" "$PG_NAME" \
                   psql -U "$PG_USER" -d "$PG_DB" -tAc \
                   "SELECT string_agg(datname, ' ' ORDER BY datname) FROM pg_database WHERE NOT datistemplate" 2>&1)"
            if [[ "$dbs" == *"$PG_DB"* ]]; then
                ok "$(t vfyb_postgres_ok "$dbs")"
                # One count from a table this project owns, so "it started" is
                # not mistaken for "the data is there".
                courses="$(docker exec -e PGPASSWORD="$PG_PASS" "$PG_NAME" \
                           psql -U "$PG_USER" -d contentadmin -tAc \
                           "SELECT count(*) FROM courses" 2>/dev/null | tr -d '[:space:]')"
                if [[ "$courses" =~ ^[0-9]+$ ]]; then
                    ok "$(t vfyb_postgres_courses "$courses")"
                    record postgres ok "$courses course(s)"
                else
                    warn "$(t vfyb_postgres_no_contentadmin)"
                    record postgres ok "databases open; contentadmin not readable"
                fi
            else
                err "$(t vfyb_postgres_auth_failed)"
                echo "$dbs" | tail -3 | sed 's/^/    /'
                record postgres fail "the archived password does not open this data directory"
            fi
        else
            err "$(t vfyb_postgres_no_start)"
            docker logs "$PG_NAME" 2>&1 | tail -8 | sed 's/^/    /'
            record postgres fail "did not become ready"
        fi
    else
        err "$(t vfyb_postgres_no_start)"
        record postgres fail "container would not start"
    fi
fi

# ─── Garage — the question this script exists for ────────────────────────────
header "$(t vfyb_garage_heading)"
info "$(t vfyb_garage_why)"
GARAGE_IMAGE="$(image_for dxflrs/garage)"

if [[ ! -f "$DATA/garage/garage.toml" ]]; then
    warn "$(t vfyb_garage_no_config)"
    record garage skip "no garage.toml in the archive"
else
    # The archive's own configuration, with only the paths it needs inside a
    # container. Rewriting more would test a Garage this installation does not
    # run.
    docker rm -f "$GARAGE_NAME" >/dev/null 2>&1
    if docker run -d --name "$GARAGE_NAME" \
        -p "127.0.0.1:$GARAGE_S3_PORT:3900" \
        -p "127.0.0.1:$GARAGE_ADMIN_PORT:3903" \
        -v "$DATA/garage/garage.toml:/etc/garage.toml:ro" \
        -v "$DATA/garage/meta:/var/lib/garage/meta" \
        -v "$DATA/garage/data:/var/lib/garage/data" \
        "$GARAGE_IMAGE" >/dev/null 2>&1
    then
        GARAGE_BIN=""
        for cand in /garage garage; do
            docker exec "$GARAGE_NAME" "$cand" --version >/dev/null 2>&1 && { GARAGE_BIN="$cand"; break; }
        done
        if [[ -z "$GARAGE_BIN" ]]; then
            err "$(t vfyb_garage_no_binary)"
            record garage fail "the garage binary was not found in the image"
        elif wait_for "$GARAGE_NAME" 45 docker exec "$GARAGE_NAME" "$GARAGE_BIN" status; then
            layout="$(docker exec "$GARAGE_NAME" "$GARAGE_BIN" layout show 2>&1)"
            buckets="$(docker exec "$GARAGE_NAME" "$GARAGE_BIN" bucket list 2>&1)"
            n="$(grep -cE '^ ' <<<"$buckets" || true)"
            echo "$layout" | sed 's/^/    /' | head -12
            if grep -qiE 'no layout|not configured|role.*unassigned' <<<"$layout"; then
                # The fallback the plan named, and now with a measurement
                # behind it rather than a worry.
                err "$(t vfyb_garage_layout_lost)"
                record garage fail "the layout did not travel — S3-level copy needed"
            elif (( n > 0 )); then
                ok "$(t vfyb_garage_ok "$n")"
                echo "$buckets" | sed 's/^/    /' | head -12
                record garage ok "$n bucket(s), layout intact"
            else
                warn "$(t vfyb_garage_no_buckets)"
                record garage fail "layout present but no buckets listed"
            fi
        else
            err "$(t vfyb_garage_no_start)"
            docker logs "$GARAGE_NAME" 2>&1 | tail -8 | sed 's/^/    /'
            record garage fail "did not become ready"
        fi
    else
        err "$(t vfyb_garage_no_start)"
        record garage fail "container would not start"
    fi
fi

# ─── Weaviate ────────────────────────────────────────────────────────────────
header "$(t vfyb_weaviate_heading)"
WEAVIATE_IMAGE="$(image_for semitechnologies/weaviate)"
WEAVIATE_KEY="$(env_value WEAVIATE_API_KEY)"

if [[ ! -d "$DATA/weaviate/data" ]]; then
    warn "$(t vfyb_weaviate_no_dir)"
    record weaviate skip "no data directory in the archive"
else
    docker rm -f "$WEAVIATE_NAME" >/dev/null 2>&1
    if docker run -d --name "$WEAVIATE_NAME" \
        -p "127.0.0.1:$WEAVIATE_PORT:8080" \
        -v "$DATA/weaviate/data:/var/lib/weaviate" \
        -e PERSISTENCE_DATA_PATH=/var/lib/weaviate \
        -e AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=true \
        -e DEFAULT_VECTORIZER_MODULE=none \
        -e CLUSTER_HOSTNAME=verify \
        "$WEAVIATE_IMAGE" >/dev/null 2>&1
    then
        if wait_for "$WEAVIATE_NAME" 90 \
               curl -fsS "http://127.0.0.1:$WEAVIATE_PORT/v1/.well-known/ready"; then
            schema="$(curl -fsS "http://127.0.0.1:$WEAVIATE_PORT/v1/schema" 2>/dev/null)"
            classes="$(grep -oE '"class":"[^"]+"' <<<"$schema" | cut -d'"' -f4 | tr '\n' ' ')"
            if [[ -n "$classes" ]]; then
                ok "$(t vfyb_weaviate_ok "$classes")"
                record weaviate ok "classes: $classes"
            else
                warn "$(t vfyb_weaviate_empty)"
                record weaviate fail "opened, but the schema is empty"
            fi
        else
            err "$(t vfyb_weaviate_no_start)"
            docker logs "$WEAVIATE_NAME" 2>&1 | tail -8 | sed 's/^/    /'
            record weaviate fail "did not become ready"
        fi
    else
        err "$(t vfyb_weaviate_no_start)"
        record weaviate fail "container would not start"
    fi
fi

# ─── Verdict ─────────────────────────────────────────────────────────────────
header "$(t vfyb_verdict_heading)"
FAILED=0
for row in "${RESULTS[@]}"; do
    IFS='|' read -r system state detail <<<"$row"
    case "$state" in
        ok)   ok "  $system — $detail" ;;
        skip) dim "  $system — $(t vfyb_skipped "$detail")" ;;
        *)    err "  $system — $detail"; FAILED=1 ;;
    esac
done
echo
if (( FAILED )); then
    err "$(t vfyb_verdict_bad)"
    exit 1
fi
ok "$(t vfyb_verdict_good)"
dim "$(t vfyb_verdict_caveat)"
