#!/usr/bin/env bash
# n8n_webhook_state() is shared by deploy-n8n-workflows.sh (verifying its
# own work) and admin.sh's status view, so a wrong reading here would show
# up as two tools disagreeing about whether uploads work.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

BIN="$(mktemp -d)"
stub_curl() { # $1 = body to return, or "fail"
    if [[ "$1" == "fail" ]]; then
        printf '#!/usr/bin/env bash\nexit 7\n' > "$BIN/curl"
    else
        printf '#!/usr/bin/env bash\nprintf "%%s" %q\n' "$1" > "$BIN/curl"
    fi
    chmod +x "$BIN/curl"
}

# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"

# Wording verified in n8n's webhook-not-found.error.ts at tag n8n@1.123.0.
REGISTERED='{"code":404,"message":"This webhook is not registered for GET requests. Did you mean to make a POST request?"}'
NOT_REG='{"code":404,"message":"The requested webhook \"GET document-ingest\" is not registered.","hint":"The workflow must be active"}'

probe() { # $1 = stubbed body, $2 = expected state, $3 = label
    stub_curl "$1"
    local got; got="$(PATH="$BIN:$PATH" n8n_webhook_state)"
    [[ "$got" == "$2" ]]
    check "$3 -> $2" $? "got '$got'"
}

probe "$REGISTERED" registered   "method-mismatch 404"
probe "$NOT_REG"    unregistered "not-registered 404"
probe ""            unreachable  "empty reply"
# Not an n8n answer at all (a proxy error page, say): must not be guessed
# either way — "unreachable" is the honest reading.
probe "<html>502 Bad Gateway</html>" unreachable "non-n8n reply"

stub_curl fail
got="$(PATH="$BIN:$PATH" n8n_webhook_state)"
[[ "$got" == "unreachable" ]]
check "curl failure -> unreachable" $? "got '$got'"

# The base URL must be respected and not double-slashed.
URL_LOG="$BIN/url.log"
cat > "$BIN/curl" <<STUB
#!/usr/bin/env bash
echo "\${!#}" >> "$URL_LOG"
printf '%s' 'This webhook is not registered for GET requests.'
STUB
chmod +x "$BIN/curl"

: > "$URL_LOG"
PATH="$BIN:$PATH" n8n_webhook_state "http://example:1234/" >/dev/null
grep -qx "http://example:1234/webhook/document-ingest" "$URL_LOG"
check "trailing slash in the base URL is handled" $? "$(cat "$URL_LOG")"

: > "$URL_LOG"
PATH="$BIN:$PATH" n8n_webhook_state "http://example:1234" >/dev/null
grep -qx "http://example:1234/webhook/document-ingest" "$URL_LOG"
check "base URL without trailing slash works too" $? "$(cat "$URL_LOG")"

# Default: n8n's own container port from .env, on loopback.
: > "$URL_LOG"
PATH="$BIN:$PATH" N8N_PORT=5678 n8n_webhook_state >/dev/null
grep -qx "http://127.0.0.1:5678/webhook/document-ingest" "$URL_LOG"
check "defaults to 127.0.0.1 and N8N_PORT" $? "$(cat "$URL_LOG")"

# ─── Internal URLs must not be built from host-side ports ───────────────────
# docker-compose.yml pins n8n's container-side port to 5678 and documents
# that N8N_PORT in .env is the HOST binding, which the wizard moves on a
# port conflict. A container-to-container URL built from it dials a port
# nothing listens on inside the network — seen in the field as MinIO
# logging "connect: connection refused" against smartrag-n8n:5778.
while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *"smartrag-"*'${N8N_PORT}'* ]] || continue
    FAILURES+=("internal URL built from the host-side N8N_PORT: $line")
done < "$REPO/.env.example"

grep -q 'smartrag-n8n:5678/webhook/minio-notify' "$REPO/.env.example"
check "the MinIO notify endpoint uses n8n's container port" $? \
      "$(grep MINIO_NOTIFY_WEBHOOK_ENDPOINT "$REPO/.env.example")"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All n8n_webhook_state checks passed: n8n's method-mismatch 404 reads as"
echo "registered, its not-registered 404 as unregistered, and an empty reply, a"
echo "non-n8n page or a curl failure all read as unreachable rather than being"
echo "guessed either way; the base URL is honoured without double slashes; and no"
echo "container-to-container URL in .env.example is built from a host-side port."
