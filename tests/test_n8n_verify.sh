#!/usr/bin/env bash
# Exercises deploy-n8n-workflows.sh against stubbed `docker` and `curl`, to
# check the two things that decide whether an operator is told the truth:
# the owner-missing skip (must not read as success) and the final webhook
# verification (must not claim success without asking).
#
# The script's root check is the one thing patched out in the sandbox copy —
# everything below it, including all the logic under test, runs as written.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()

check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

# n8n's real wording, verified in packages/cli/src/errors/response-errors/
# webhook-not-found.error.ts at tag n8n@1.123.0. A GET on a registered POST
# webhook produces the first one — which is the SUCCESS signal here.
REGISTERED='{"code":404,"message":"This webhook is not registered for GET requests. Did you mean to make a POST request?"}'
NOT_REGISTERED='{"code":404,"message":"The requested webhook \"GET document-ingest\" is not registered.","hint":"The workflow must be active"}'

setup() { # $1 = docker-exec behaviour, $2 = webhook body, $3 = healthz (ok|down)
    SANDBOX="$(mktemp -d)"
    # Both workflow directories: the deployer imports the ingest pipeline and
    # the memory/observability workflows, and a missing directory is a hard
    # abort — correctly, but it made this fixture look like a product failure.
    mkdir -p "$SANDBOX/scripts/lib" "$SANDBOX/n8n/workflows-ingest" \
             "$SANDBOX/n8n/workflows" "$SANDBOX/bin" \
             "$SANDBOX/data/n8n/data"
    cp "$REPO"/scripts/lib/*.sh "$SANDBOX/scripts/lib/"
    cp "$REPO"/n8n/workflows-ingest/*.json "$SANDBOX/n8n/workflows-ingest/"
    cp "$REPO"/n8n/workflows/*.json "$SANDBOX/n8n/workflows/"

    # Only the root gate is removed; the rest of the script is verbatim.
    sed 's|^if \[\[ "\${EUID:-\$(id -u)}" -ne 0 \]\]; then|if false; then|' \
        "$REPO/scripts/deploy-n8n-workflows.sh" > "$SANDBOX/scripts/deploy-n8n-workflows.sh"

    cat > "$SANDBOX/.env" <<ENV
BASE_DATA_PATH="$SANDBOX/data"
GARAGE_REGION="us-east-1"
GARAGE_ACCESS_KEY="GKtestaccesskey"
GARAGE_SECRET_KEY="test-secret-key"
POSTGRES_USER="smartrag"
POSTGRES_PASSWORD="test-postgres-password"
COMPOSE_PROFILES="core"
SMTP_HOST="mail.example.com"
SMTP_PORT=25
SMTP_USER="smtp-test-user"
SMTP_PASSWORD="smtp-test-password"
SMTP_SECURE="false"
N8N_PORT=5678
ENV

    # ── stub: docker ────────────────────────────────────────────────────────
    cat > "$SANDBOX/bin/docker" <<STUB
#!/usr/bin/env bash
case "\$1 \$2" in
  "inspect --format="*) echo healthy; exit 0 ;;
esac
if [[ "\$1" == "inspect" ]]; then echo healthy; exit 0; fi
if [[ "\$1" == "restart" ]]; then exit 0; fi
if [[ "\$1" == "exec" ]]; then
    case "\$*" in
      *import:credentials*) $1 ;;
      *) exit 0 ;;
    esac
fi
exit 0
STUB
    chmod +x "$SANDBOX/bin/docker"

    # ── stub: chown ─────────────────────────────────────────────────────────
    # The real script chowns the staging dir to uid 1000 so n8n's container
    # user can read it. That needs root, which this test deliberately does
    # not have; ownership is irrelevant to the logic under test.
    printf '#!/usr/bin/env bash\nexit 0\n' > "$SANDBOX/bin/chown"
    chmod +x "$SANDBOX/bin/chown"

    # ── stub: curl (healthz + the webhook probe) ────────────────────────────
    cat > "$SANDBOX/bin/curl" <<STUB
#!/usr/bin/env bash
url="\${!#}"
case "\$url" in
  *healthz*) [[ "$3" == "ok" ]] && exit 0 || exit 7 ;;
  *webhook/document-ingest*) printf '%s' '$2'; exit 0 ;;
esac
exit 0
STUB
    chmod +x "$SANDBOX/bin/curl"
}

run() { # $1 docker-exec-creds behaviour, $2 webhook body, $3 healthz
    setup "$1" "$2" "$3"
    # Short timeout: the wait loop's duration isn't what's under test, and
    # the default 180s would make this suite unusable.
    ( cd "$SANDBOX" && PATH="$SANDBOX/bin:$PATH" N8N_VERIFY_TIMEOUT=6 \
        bash scripts/deploy-n8n-workflows.sh --lang en 2>&1 )
    RC=$?
}

CREDS_OK='exit 0'
CREDS_NO_OWNER='echo "Failed to find owner." >&2; exit 1'
CREDS_BROKEN='echo "database is locked" >&2; exit 1'

# ─── 1. No n8n owner yet ─────────────────────────────────────────────────────
out="$(run "$CREDS_NO_OWNER" "$REGISTERED" ok; echo "RC=$RC")"
grep -q "RC=10" <<<"$out"
check "missing owner exits EXIT_SKIPPED, not 0" $? "$(tail -3 <<<"$out")"
grep -qi "owner" <<<"$out"
check "missing owner is explained" $? ""
grep -qi "no owner account yet\|Skipped" <<<"$out"
check "missing owner reads as skipped, not failed" $? "$(tail -3 <<<"$out")"

# ─── 2. Happy path: webhook really is registered ─────────────────────────────
out="$(run "$CREDS_OK" "$REGISTERED" ok; echo "RC=$RC")"
grep -q "RC=0" <<<"$out"
check "verified run exits 0" $? "$(tail -5 <<<"$out")"
grep -q "Ingest webhook is registered" <<<"$out"
check "verification confirms the webhook" $? "$(tail -5 <<<"$out")"
grep -q "workflows are in place" <<<"$out"
check "final success still reported" $? ""

# ─── 3. Import "succeeded" but the webhook is dead ───────────────────────────
# The regression that matters: every command reported success and the script
# used to end with "done" while an upload would 404. It must fail instead.
out="$(run "$CREDS_OK" "$NOT_REGISTERED" ok; echo "RC=$RC")"
grep -q "RC=0" <<<"$out"
check "unregistered webhook does NOT exit 0" $(( $? == 0 ? 1 : 0 )) "$(tail -5 <<<"$out")"
grep -q "NOT registered" <<<"$out"
check "unregistered webhook is named" $? "$(tail -5 <<<"$out")"
grep -q "workflows are in place" <<<"$out"
check "no success claim when the webhook is dead" $(( $? == 0 ? 1 : 0 )) "$(tail -5 <<<"$out")"
grep -qi "inactive\|id differs" <<<"$out"
check "says what to suspect (inactive or wrong id)" $? ""

# ─── 4. n8n never comes back after the restart ───────────────────────────────
# Observed in the field: every import step reported success, n8n was still
# restarting when the check gave up, and the script then printed BOTH "could
# not be verified" AND "documents can now be uploaded" — the exact
# unverified success claim this whole step exists to prevent.
out="$(run "$CREDS_OK" "$REGISTERED" down; echo "RC=$RC")"
grep -qi "UNKNOWN\|not come back up" <<<"$out"
check "unverifiable state is stated, not assumed" $? "$(tail -5 <<<"$out")"
grep -q "docker logs smartrag-n8n" <<<"$out"
check "points at the logs" $? ""
grep -q "workflows are in place" <<<"$out"
check "no success claim when nothing could be verified" $(( $? == 0 ? 1 : 0 )) "$(tail -6 <<<"$out")"
grep -q "RC=11" <<<"$out"
check "unverifiable exits EXIT_UNVERIFIED, not 0" $? "$(tail -3 <<<"$out")"
grep -qi "sudo smartrag" <<<"$out"
check "points at the TUI to re-check, not a shell command" $? "$(tail -5 <<<"$out")"

# ─── 5. A real credential failure is still a failure ─────────────────────────
out="$(run "$CREDS_BROKEN" "$REGISTERED" ok; echo "RC=$RC")"
grep -q "RC=0\|RC=10" <<<"$out"
check "genuine import failure is neither success nor skip" $(( $? == 0 ? 1 : 0 )) "$(tail -3 <<<"$out")"

# ─── 6. Staged plaintext secrets are gone afterwards ─────────────────────────
# The staging dir holds MinIO and SMTP passwords in clear text while the
# import runs; the EXIT trap must remove it in every one of these paths.
leftovers=0
for body in "$REGISTERED" "$NOT_REGISTERED"; do
    run "$CREDS_OK" "$body" ok >/dev/null 2>&1
    [[ -e "$SANDBOX/data/n8n/data/staging" ]] && leftovers=1
done
run "$CREDS_NO_OWNER" "$REGISTERED" ok >/dev/null 2>&1
[[ -e "$SANDBOX/data/n8n/data/staging" ]] && leftovers=1
check "no plaintext staging dir left behind on any path" $leftovers "staging survived"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"
    printf '  - %s\n' "${FAILURES[@]}"
    exit 1
fi
echo "All n8n deploy checks passed: a missing n8n owner exits EXIT_SKIPPED and"
echo "reads as skipped rather than done; a registered webhook is confirmed by"
echo "asking n8n (method-mismatch 404 = registered) before success is claimed;"
echo "an import that reports success while the webhook is dead now fails loudly"
echo "and names the likely cause; an unverifiable state exits EXIT_UNVERIFIED"
echo "with no success line and points at the TUI rather than being assumed"
echo "either way; a genuine import failure stays a failure; and"
echo "the plaintext staging dir is removed on every path."
