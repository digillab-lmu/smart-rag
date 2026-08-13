#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# The learning-record chain — driver
# ═════════════════════════════════════════════════════════════════════════════
#
# Like tests/test_chathistory_chain.sh: the suite is JavaScript because what
# it runs is JavaScript — the Code nodes out of
# n8n/workflows/usermemory-summary.json, executed in order against stubbed
# transport. This workflow failed on every scheduled run for weeks while its
# node text read correctly, which is the argument for running it.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail
TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v node >/dev/null 2>&1; then
    echo "Node is not installed, so the workflow's Code nodes cannot be run."
    echo "Install Node (the same runtime n8n uses) and run this suite again."
    exit 10
fi

exec node "$TESTS_DIR/usermemory_chain.js"
