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

exec docker compose -f "$REPO_ROOT/docker/docker-compose.yml" --env-file "$REPO_ROOT/.env" "$@"
