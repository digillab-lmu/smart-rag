#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# The chat-history chain — driver
# ═════════════════════════════════════════════════════════════════════════════
#
# The suite itself is JavaScript, because what it runs is JavaScript: the Code
# nodes out of n8n/workflows/chathistory-sync.json, executed in order with the
# transport stubbed. Reimplementing them in Python would test the copy.
#
# n8n's own runtime is Node, so a laptop that can develop these workflows has
# one. Where there is none the suite reports "could not run" (exit 10) rather
# than passing, so the area is visibly untested instead of invisibly so.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v node >/dev/null 2>&1; then
    echo "Node is not installed, so the workflow's Code nodes cannot be run."
    echo "Install Node (the same runtime n8n uses) and run this suite again."
    exit 10
fi

exec node "$TESTS_DIR/chathistory_chain.js"
