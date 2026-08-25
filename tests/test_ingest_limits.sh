#!/usr/bin/env bash
# An upload passes through four limits, and only the innermost one can explain
# itself.
#
#   Content Admin  MAX_UPLOAD_BYTES          200 MB
#   n8n            N8N_PAYLOAD_SIZE_MAX      256 MB
#   n8n            "Docling Conversion" node 1800 s
#   docling        DOCLING_SERVE_MAX_SYNC_WAIT
#
# **The innermost limit must be the shortest**, or the component that knows
# why a conversion stopped never gets to say so. Measured on a real install:
# docling's own default of 120 s cut off a 1.2 MB scanned PDF while n8n sat
# waiting patiently for thirty minutes, and the operator got a 504 whose text
# named an environment variable. Raising docling's wait past n8n's would swap
# that for an n8n timeout, which says nothing at all.
#
# The size limits run the other way round: the Content Admin's must be the
# smaller, so a file too large is refused by the page the person is looking at
# rather than by a webhook they never see.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

COMPOSE="$REPO/docker/docker-compose.yml"
ENVEX="$REPO/.env.example"
INGEST="$REPO/n8n/workflows-ingest/ingest-document.json"

# ─── Every number, read from where it actually lives ────────────────────────
docling_wait="$(grep -m1 '^DOCLING_MAX_SYNC_WAIT=' "$ENVEX" | cut -d= -f2- | tr -d '"')"
[[ "$docling_wait" =~ ^[0-9]+$ ]]
check "DOCLING_MAX_SYNC_WAIT is set in .env.example" $? "got '$docling_wait'"

grep -q 'DOCLING_SERVE_MAX_SYNC_WAIT: "\${DOCLING_MAX_SYNC_WAIT}"' "$COMPOSE"
check "the container is started with it" $? \
      "without this the image's own 120 s applies and nothing in .env changes that"

node_timeout="$(python3 -c "
import json
w = json.load(open('$INGEST'))
for n in w['nodes']:
    if 'docling' in str((n.get('parameters') or {}).get('url','')):
        print(((n['parameters'].get('options') or {}).get('timeout') or 0) // 1000)
        break
")"
[[ "$node_timeout" =~ ^[0-9]+$ ]] && (( node_timeout > 0 ))
check "the ingest node has a timeout" $? "got '$node_timeout'"

# ─── The ordering that makes the error legible ──────────────────────────────
(( docling_wait < node_timeout ))
check "docling gives up before n8n does" $? \
      "docling ${docling_wait}s vs n8n ${node_timeout}s — the outer one would win and say nothing useful"

# Not so far below that the margin is pointless either: a conversion that ends
# at 25 minutes and a workflow that waits 30 leaves room for the upload and
# the response, which is the point of the gap.
(( node_timeout - docling_wait >= 60 ))
check "with at least a minute of headroom" $? \
      "docling ${docling_wait}s, n8n ${node_timeout}s"

# And the default must be past the image's 120 s, which is the failure that
# started this.
(( docling_wait > 120 ))
check "and past the image default that was too short" $? "${docling_wait}s"

# ─── Sizes run the other way ────────────────────────────────────────────────
admin_mb="$(grep -m1 'MAX_UPLOAD_BYTES = ' "$REPO/content-admin/app.py" \
            | grep -oE '[0-9]+ \* 1024 \* 1024' | grep -oE '^[0-9]+')"
# The quoted value, not "any number on the line": the key's own name contains
# an 8, and matching bare digits turned 256 into "8 256".
n8n_mb="$(grep -m1 'N8N_PAYLOAD_SIZE_MAX:' "$COMPOSE" | grep -oE '"[0-9]+"' | tr -d '"')"
[[ -n "$admin_mb" && -n "$n8n_mb" ]]
check "both size limits were found" $? "admin=$admin_mb n8n=$n8n_mb"
(( admin_mb <= n8n_mb ))
check "the Content Admin refuses a too-large file before n8n would" $? \
      "admin ${admin_mb}MB vs n8n ${n8n_mb}MB — the person would get a webhook error instead of a page"

# ─── Both limits are stated before the upload, not after it ─────────────────
grep -q 'upload_limits' "$REPO/content-admin/templates/upload.html"
check "the upload page states its limits" $? \
      "the size was only ever mentioned in the 413 error, i.e. after failing"
grep -q 'conversion_minutes' "$REPO/content-admin/app.py"
check "and the time limit comes from the configured value" $? \
      "a second number written into the page would drift from the one enforced"
# The stated minutes must be derived, never a literal: this is the check that
# fails if somebody hard-codes 25 into the message.
grep -qE 'conversion_limit_minutes\(' "$REPO/content-admin/app.py"
check "…through a function that reads DOCLING_MAX_SYNC_WAIT" $? ""

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All ingest-limit checks passed: docling's conversion wait is configured"
echo "rather than left at the image's 120 seconds, it is shorter than the n8n"
echo "node that calls it — so the component that knows why a conversion"
echo "stopped is the one that answers — with a minute of headroom between"
echo "them; the Content Admin's size limit is at or below n8n's payload limit,"
echo "so an oversized file is refused by the page rather than by a webhook;"
echo "and both limits are stated before the upload, with the time taken from"
echo "the value the container is actually started with instead of a second"
echo "number written into the text."
