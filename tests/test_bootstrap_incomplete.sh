#!/usr/bin/env bash
# Runs bootstrap.sh --continue for real, against stubbed phase scripts, to
# check what it reports when the n8n workflow import could not run yet.
# Static grepping would not catch the thing that actually went wrong here:
# a skipped phase that still ends with a "Complete" banner.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()

check() { # name, condition-result(0/1), detail
    if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi
}

# ─── Build a throwaway copy of the repo's script tree ────────────────────────
setup_sandbox() { # $1 = exit code the n8n phase should return
    SANDBOX="$(mktemp -d)"
    mkdir -p "$SANDBOX/scripts/lib"
    cp "$REPO/scripts/bootstrap.sh" "$SANDBOX/scripts/"
    cp "$REPO"/scripts/lib/*.sh "$SANDBOX/scripts/lib/"

    # Every phase script becomes a stub that just says it ran. The n8n one
    # returns whatever this test is exercising.
    for s in install-system-packages install-postfix get-ssl-certs \
             start-services deploy-garage deploy-schemas generate-lti-keys \
             install-tailscale; do
        printf '#!/usr/bin/env bash\necho "STUB %s"\nexit 0\n' "$s" \
            > "$SANDBOX/scripts/$s.sh"
        chmod +x "$SANDBOX/scripts/$s.sh"
    done
    if [[ "$1" == "10-then-0" ]]; then
        # First call: no owner yet. Second call (after the admin says they
        # created the account): succeeds.
        cat > "$SANDBOX/scripts/deploy-n8n-workflows.sh" <<'STATEFUL'
#!/usr/bin/env bash
marker="$(dirname "$0")/../.n8n-called"
echo "STUB deploy-n8n-workflows"
if [[ -e "$marker" ]]; then exit 0; fi
touch "$marker"; exit 10
STATEFUL
        chmod +x "$SANDBOX/scripts/deploy-n8n-workflows.sh"
    else
        printf '#!/usr/bin/env bash\necho "STUB deploy-n8n-workflows"\nexit %s\n' "$1" \
            > "$SANDBOX/scripts/deploy-n8n-workflows.sh"
    fi
    chmod +x "$SANDBOX/scripts/deploy-n8n-workflows.sh"

    cat > "$SANDBOX/.env" <<'ENV'
DOMAIN="example.com"
SUBDOMAIN_PREFIX=""
ENV

    # create_system_snapshot writes to /var/backups and needs root; the
    # deployment phases are what this test is about, so it's neutralised.
    cat >> "$SANDBOX/scripts/lib/common.sh" <<'OVERRIDE'
create_system_snapshot() { :; }
OVERRIDE
}

run_bootstrap() { # $1 = n8n exit code, $2 = lang, $3 = answers on stdin
    setup_sandbox "$1"
    ( cd "$SANDBOX" && printf '%b' "${3:-n\n}" \
        | bash scripts/bootstrap.sh --continue --lang "${2:-en}" 2>&1 )
    BOOT_RC=$?
}

# ─── 1. n8n phase skipped (fresh install, no n8n owner yet) ──────────────────
out="$(run_bootstrap 10 en "n\n"; echo "RC=$BOOT_RC")"

grep -q "Setup INCOMPLETE" <<<"$out"
check "skipped run says INCOMPLETE" $? "$(tail -5 <<<"$out")"

# The exact regression this change is about: a skipped phase used to print
# the same success banner as a finished install.
grep -q "Bootstrap complete" <<<"$out"
check "skipped run does NOT claim completion" $(( $? == 0 ? 1 : 0 )) "$(tail -5 <<<"$out")"

grep -q "deploy-n8n-workflows.sh" <<<"$out"
check "names the command to run" $? ""

grep -q "n8n.example.com" <<<"$out"
check "names the n8n URL to open" $? "$(grep -i n8n <<<"$out" | head -3)"

grep -q "404" <<<"$out"
check "warns about the 404 that follows if ignored" $? ""

# The later phases must still have run — a skipped n8n import doesn't stop
# the install, it only changes what gets reported at the end.
grep -q "STUB generate-lti-keys" <<<"$out"
check "later phases still ran" $? ""

grep -q "RC=0" <<<"$out"
check "skipped run still exits 0" $? "$(tail -2 <<<"$out")"

# ─── 2. Everything fine ──────────────────────────────────────────────────────
out="$(run_bootstrap 0 en; echo "RC=$BOOT_RC")"

grep -q "Setup INCOMPLETE" <<<"$out"
check "clean run shows no incomplete block" $(( $? == 0 ? 1 : 0 )) "$(tail -5 <<<"$out")"

# All phases ran, so the three manual browser steps must be spelled out.
grep -q "Open Flowise" <<<"$out"
check "clean run states the manual steps" $? "$(tail -5 <<<"$out")"

# The new contract: phases finishing is not the same as a working system.
# Nothing may claim readiness before the checks have actually passed — and
# here they cannot, because stdin is closed and Flowise is not running.
grep -q "System ready for use" <<<"$out"
check "clean run does NOT claim readiness unverified" $(( $? == 0 ? 1 : 0 )) "$(tail -5 <<<"$out")"

grep -q "NOT confirmed working" <<<"$out"
check "clean run says plainly what is unproven" $? "$(tail -5 <<<"$out")"

grep -q "RC=0" <<<"$out"
check "clean run exits 0" $? ""

# ─── 3. The n8n phase genuinely failed ───────────────────────────────────────
# A real failure must not be quietly folded into "incomplete, do it later".
out="$(run_bootstrap 1 en; echo "RC=$BOOT_RC")"

grep -q "Setup INCOMPLETE" <<<"$out"
check "hard failure is not reported as merely incomplete" $(( $? == 0 ? 1 : 0 )) "$(tail -5 <<<"$out")"

grep -q "Bootstrap complete" <<<"$out"
check "hard failure is not reported as complete" $(( $? == 0 ? 1 : 0 )) "$(tail -5 <<<"$out")"

grep -qi "failed" <<<"$out"
check "hard failure says it failed" $? "$(tail -5 <<<"$out")"

grep -q "RC=0" <<<"$out"
check "hard failure exits non-zero" $(( $? == 0 ? 1 : 0 )) "$(tail -3 <<<"$out")"

# ─── 4. German ───────────────────────────────────────────────────────────────
out="$(run_bootstrap 10 de "n\n"; echo "RC=$BOOT_RC")"

grep -q "UNVOLLSTÄNDIG" <<<"$out"
check "[de] incomplete block is translated" $? "$(tail -5 <<<"$out")"

grep -q "Bootstrap abgeschlossen" <<<"$out"
check "[de] does not claim completion" $(( $? == 0 ? 1 : 0 )) "$(tail -5 <<<"$out")"

# ─── 4b. The wizard waits for the browser step and then finishes ─────────────
# The point of this: the admin is at the keyboard right now, and the missing
# piece is one browser step. Printing instructions and exiting is what left
# installs half-finished.
out="$(run_bootstrap 10-then-0 en "y\n"; echo "RC=$BOOT_RC")"

grep -qi "will wait" <<<"$out"
check "wizard offers to wait for the owner setup" $? "$(tail -8 <<<"$out")"

grep -q "n8n.example.com" <<<"$out"
check "wizard names the n8n URL to open" $? ""

# Two invocations: the initial one, and the retry after "yes".
(( $(grep -c "STUB deploy-n8n-workflows" <<<"$out") == 2 ))
check "wizard retries the import after confirmation" $? "$(grep -c 'STUB deploy' <<<"$out") call(s)"

grep -q "Setup INCOMPLETE" <<<"$out"
check "no incomplete block once it succeeded" $(( $? == 0 ? 1 : 0 )) "$(tail -8 <<<"$out")"

# Same contract as the clean run: the import succeeding says nothing about
# whether the browser steps were done, so readiness still has to be earned.
grep -q "Open Flowise" <<<"$out"
check "successful import still states the manual steps" $? "$(tail -8 <<<"$out")"

# Answering yes too early: n8n still has no owner. Must say so and ask
# again rather than silently accepting a still-broken state.
out="$(run_bootstrap 10 en "y\ny\nn\n"; echo "RC=$BOOT_RC")"
grep -q "still reports no owner" <<<"$out"
check "premature yes is called out" $? "$(tail -8 <<<"$out")"
grep -q "Setup INCOMPLETE" <<<"$out"
check "giving up after retries still reports INCOMPLETE" $? "$(tail -8 <<<"$out")"

# Declining must not be treated as failure.
out="$(run_bootstrap 10 en "n\n"; echo "RC=$BOOT_RC")"
grep -qi "skipping for now" <<<"$out"
check "declining is acknowledged, not punished" $? "$(tail -8 <<<"$out")"
grep -q "RC=0" <<<"$out"
check "declining still exits 0" $? ""

# ─── 4c. The import ran but could not be confirmed (EXIT_UNVERIFIED) ─────────
# n8n was still restarting when the webhook check gave up. Nothing broke, so
# the install must not abort — but nothing was confirmed either, so it must
# not claim completion.
out="$(run_bootstrap 11 en; echo "RC=$BOOT_RC")"

grep -q "Bootstrap complete" <<<"$out"
check "unverified run does not claim completion" $(( $? == 0 ? 1 : 0 )) "$(tail -6 <<<"$out")"

grep -qi "did not finish restarting" <<<"$out"
check "unverified run says what is actually unknown" $? "$(tail -6 <<<"$out")"

# It also isn't the owner-account situation, so that block would be wrong.
grep -qi "owner setup" <<<"$out"
check "unverified run doesn't blame the owner account" $(( $? == 0 ? 1 : 0 )) "$(tail -6 <<<"$out")"

grep -q "RC=0" <<<"$out"
check "unverified run does not abort the install" $? "$(tail -3 <<<"$out")"

grep -q "STUB generate-lti-keys" <<<"$out"
check "later phases still ran after an unverified import" $? ""

# ─── 4d. The ending says what to do next, with real URLs ────────────────────
# The deployment is not usable when bootstrap finishes: Flowise, n8n and the
# Content Admin each still want an account created in a browser. That used to
# be one line of prose at the end of a long run — which is how an install
# reaches the point where the first sign of trouble is a 404 much later.
setup_sandbox 0
cat >> "$SANDBOX/.env" <<'ENVX'
DEPLOYMENT_MODE="tailscale"
FLOWISE_PUBLIC_URL="https://i5.tail99.ts.net"
CONTENT_ADMIN_PUBLIC_URL="https://i5.tail99.ts.net:8443"
N8N_WEBHOOK_URL="https://i5.tail99.ts.net:8444"
ENVX
out="$( cd "$SANDBOX" && printf 'n\n' | bash scripts/bootstrap.sh --continue --lang en 2>&1 )"

for url in "https://i5.tail99.ts.net" "https://i5.tail99.ts.net:8443" "https://i5.tail99.ts.net:8444"; do
    grep -qF "$url" <<<"$out"
    check "the ending shows $url" $? "$(tail -12 <<<"$out")"
done
grep -qi "flowise" <<<"$out"
check "Flowise is named as a step" $? ""
grep -qi "n8n" <<<"$out"
check "n8n is named as a step" $? ""
grep -qi "API key" <<<"$out"
check "the Flowise API key step is spelled out" $? ""
grep -q "credentials.txt" <<<"$out"
check "it points at credentials.txt" $? ""
# The URLs must come from .env, never be reassembled — that is how the
# subdomain-prefix bug got in.
grep -q "smart-rag.i5.tail99" <<<"$out"
check "no URL is reassembled from the hostname" $(( $? == 0 ? 1 : 0 )) "$out"

# ─── 5. EXIT_SKIPPED is a real, distinct constant ────────────────────────────
# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"
[[ "${EXIT_SKIPPED:-}" == "10" ]]
check "EXIT_SKIPPED is defined as 10" $? "${EXIT_SKIPPED:-unset}"
(( EXIT_SKIPPED != 0 && EXIT_SKIPPED != 1 ))
check "EXIT_SKIPPED is distinct from success and failure" $? "$EXIT_SKIPPED"
[[ "${EXIT_UNVERIFIED:-}" == "11" ]]
check "EXIT_UNVERIFIED is defined as 11" $? "${EXIT_UNVERIFIED:-unset}"
(( EXIT_UNVERIFIED != EXIT_SKIPPED ))
check "'could not run' and 'could not confirm' are different states" $? ""

# ─── Report ──────────────────────────────────────────────────────────────────
if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"
    printf '  - %s\n' "${FAILURES[@]}"
    exit 1
fi
echo "All bootstrap-ending checks passed: the wizard waits for the n8n owner"
echo "step and finishes the import itself, calls out a premature yes, treats"
echo "declining as a choice rather than a failure, and never loops forever on"
echo "an EOF stdin; a run whose n8n phase could not"
echo "execute yet reports INCOMPLETE (never 'complete'), names the n8n URL,"
echo "the exact command and the 404 that follows if ignored, still runs the"
echo "later phases and still exits 0; a clean run reports completion with no"
echo "incomplete block; a genuine n8n failure is reported as a failure with a"
echo "non-zero exit rather than folded into 'do it later'; the block is"
echo "translated; the ending lists the three accounts still to create with the"
echo "URLs read from .env rather than reassembled; and EXIT_SKIPPED is a"
echo "distinct constant."
