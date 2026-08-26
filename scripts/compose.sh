#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — docker compose wrapper
# ═════════════════════════════════════════════════════════════════════════════
#
# Thin passthrough for `docker compose` that always supplies the two flags
# Compose needs to find this project's files correctly, no matter which
# directory you run it from:
#   -f docker/docker-compose.yml   (the compose file doesn't live in the repo root)
#   --env-file .env                (Compose only auto-loads .env from your
#                                    CURRENT directory, never relative to -f —
#                                    running `cd docker && docker compose up`
#                                    without this silently blanks every
#                                    variable, which then fails in confusing
#                                    ways like "no port specified: 127.0.0.1::")
#
# Usage:  bash scripts/compose.sh <any docker compose subcommand and args>
# Examples:
#   bash scripts/compose.sh pull
#   bash scripts/compose.sh up -d
#   bash scripts/compose.sh logs -f smartrag-n8n
#   bash scripts/compose.sh ps
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$REPO_ROOT/.env" ]]; then
    echo "ERROR: $REPO_ROOT/.env not found — run scripts/bootstrap.sh first." >&2
    exit 1
fi

# Compose resolves ${VAR} in the compose file from the ambient environment
# FIRST and only falls back to --env-file, so an exported value silently beats
# the file this wrapper just went to the trouble of naming. The admin TUI
# sources .env into its own environment at startup; after a restore replaced
# that file, every ${VAR} in the compose file still resolved to the secrets of
# the installation that had just been moved aside. Measured on a restore: n8n
# received POSTGRES_PASSWORD from env_file (the new one) and DATABASE_PASSWORD
# from ${POSTGRES_PASSWORD} (the old one) in the same container, and refused to
# start with "Mismatching encryption keys".
#
# Clearing the keys .env defines makes --env-file authoritative, which is what
# every caller already assumes. The handful of variables below are excluded
# because unsetting them would break this process rather than the containers.
while IFS= read -r _key; do
    case "$_key" in
        PATH|HOME|SHELL|PWD|USER|TERM) continue ;;
    esac
    unset "$_key" 2>/dev/null || true
done < <(sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$REPO_ROOT/.env")

exec docker compose -f "$REPO_ROOT/docker/docker-compose.yml" --env-file "$REPO_ROOT/.env" "$@"
