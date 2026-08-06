#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# The installer must not end by *claiming* the system works
# ═════════════════════════════════════════════════════════════════════════════
#
# Three of the setup steps happen in a browser and cannot be scripted: the
# Flowise account plus its API key, the n8n owner account, and the Content
# Admin account. Printing instructions and exiting leaves an operator reading
# a finished-looking run while the first upload will answer 404.
#
# So the installer now stays open and probes what the next person actually
# depends on. These tests pin down the part that matters: readiness is only
# ever announced after the checks have passed, and a key is only stored after
# Flowise itself has accepted it.
#
# curl is stubbed, so nothing here needs a running stack.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()

check() { # name, condition-result(0/1), detail
    if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi
}

# ─── Sandbox ─────────────────────────────────────────────────────────────────
# $1 = "allgood" | "noflowise" | "non8n"   (what the stubbed world looks like)
# $2 = value for FLOWISE_API_KEY in .env ("" = unset)
setup_sandbox() {
    # SANDBOX is created by the caller, in the parent shell: run_bootstrap is
    # invoked inside $( ), and anything assigned in there dies with the
    # subshell — including the path the assertions afterwards need.
    mkdir -p "$SANDBOX/scripts/lib" "$SANDBOX/bin"
    cp "$REPO/scripts/bootstrap.sh" "$SANDBOX/scripts/"
    cp "$REPO"/scripts/lib/*.sh "$SANDBOX/scripts/lib/"

    for s in install-system-packages install-postfix get-ssl-certs \
             start-services deploy-schemas generate-lti-keys install-tailscale \
             deploy-n8n-workflows; do
        printf '#!/usr/bin/env bash\necho "STUB %s"\nexit 0\n' "$s" \
            > "$SANDBOX/scripts/$s.sh"
        chmod +x "$SANDBOX/scripts/$s.sh"
    done

    {
        echo 'DOMAIN="example.com"'
        echo 'SUBDOMAIN_PREFIX=""'
        echo 'FLOWISE_PORT=3000'
        echo 'N8N_PORT=5678'
        echo 'CONTENT_ADMIN_PORT=3002'
        [[ -n "$2" ]] && printf 'FLOWISE_API_KEY="%s"\n' "$2"
    } > "$SANDBOX/.env"

    # ── Stub curl ────────────────────────────────────────────────────────────
    # Mirrors the three real endpoints closely enough to exercise every
    # branch: the Flowise key check (which reads an HTTP code), the n8n
    # webhook probe (which reads the response body), and the Content Admin
    # reachability check.
    #
    # GOOD_KEY is the only key Flowise accepts — that is what makes "was the
    # key verified before being written" a testable claim rather than a hope.
    cat > "$SANDBOX/bin/curl" <<STUB
#!/usr/bin/env bash
world="$1"
url=""; key=""; want_code=0
prev=""
for a in "\$@"; do
    case "\$prev" in
        -H) [[ "\$a" == Authorization:* ]] && key="\${a##*Bearer }" ;;
        -w) [[ "\$a" == *http_code* ]] && want_code=1 ;;
    esac
    [[ "\$a" == http://* ]] && url="\$a"
    prev="\$a"
done

if [[ "\$url" == *"/api/v1/chatflows"* ]]; then
    if [[ "\$world" == "noflowise" ]]; then echo -n "000"; exit 0; fi
    case "\$key" in
        GOOD_KEY)     echo -n "200" ;;
        NOPERMS_KEY)  echo -n "403" ;;
        *)            echo -n "401" ;;
    esac
    exit 0
fi

if [[ "\$url" == *"/webhook/document-ingest"* ]]; then
    if [[ "\$world" == "non8n" ]]; then exit 7; fi
    echo 'This webhook is not registered for GET requests. Did you mean to make a POST request?'
    exit 0
fi

# Content Admin reachability
if (( want_code )); then echo -n "200"; fi
exit 0
STUB
    sed -i.bak "s/^world=.*/world=\"$1\"/" "$SANDBOX/bin/curl" && rm -f "$SANDBOX/bin/curl.bak"
    chmod +x "$SANDBOX/bin/curl"

    cat >> "$SANDBOX/scripts/lib/common.sh" <<'OVERRIDE'
create_system_snapshot() { :; }
OVERRIDE
}

new_sandbox() { SANDBOX="$(mktemp -d)"; }

run_bootstrap() { # $1 = world, $2 = key in .env, $3 = stdin answers
    setup_sandbox "$1" "$2"
    ( cd "$SANDBOX" && PATH="$SANDBOX/bin:$PATH" printf '%b' "${3:-}" \
        | PATH="$SANDBOX/bin:$PATH" bash scripts/bootstrap.sh --continue --lang en 2>&1 )
    BOOT_RC=$?
}

# ─── 1. Everything already in place ──────────────────────────────────────────
new_sandbox
out="$(run_bootstrap allgood GOOD_KEY "")"

grep -q "System ready for use" <<<"$out"
check "announces readiness once every check passes" $? "$(tail -6 <<<"$out")"

grep -q "Handing over to the Content Admin" <<<"$out"
check "hands over to the Content Admin" $? "$(tail -6 <<<"$out")"

grep -q "NOT confirmed working" <<<"$out"
check "does not also warn when everything passed" $(( $? == 0 ? 1 : 0 )) "$(tail -6 <<<"$out")"

# ─── 2. No API key yet: must not claim readiness ─────────────────────────────
# stdin closed → the loop has to end rather than spin.
new_sandbox
out="$(run_bootstrap allgood "" "")"

grep -q "System ready for use" <<<"$out"
check "no readiness claim without a Flowise key" $(( $? == 0 ? 1 : 0 )) "$(tail -6 <<<"$out")"

grep -q "no API key stored yet" <<<"$out"
check "names the missing key as the blocker" $? "$(tail -6 <<<"$out")"

grep -q "NOT confirmed working" <<<"$out"
check "says plainly that nothing was confirmed" $? "$(tail -6 <<<"$out")"

# ─── 3. Pasting a key: only a working one gets stored ────────────────────────
# Choose [2], paste a key Flowise rejects, then stop.
new_sandbox
out="$(run_bootstrap allgood "" "2\nWRONG_KEY\n3\n")"

grep -q "rejected this key" <<<"$out"
check "a rejected key is reported as rejected" $? "$(tail -8 <<<"$out")"

grep -q 'FLOWISE_API_KEY' "$SANDBOX/.env"
check "a rejected key is NOT written to .env" $(( $? == 0 ? 1 : 0 )) "$(grep FLOWISE "$SANDBOX/.env" || echo '(absent)')"

# Now the same flow with a key Flowise accepts.
new_sandbox
out="$(run_bootstrap allgood "" "2\nGOOD_KEY\n3\n")"

grep -q "verified against Flowise and saved" <<<"$out"
check "an accepted key is saved" $? "$(tail -8 <<<"$out")"

grep -q 'FLOWISE_API_KEY="GOOD_KEY"' "$SANDBOX/.env"
check "the accepted key reaches .env for the Content Admin" $? "$(grep FLOWISE "$SANDBOX/.env" || echo '(absent)')"

# ─── 4. A key without permissions is its own diagnosis ───────────────────────
new_sandbox
out="$(run_bootstrap allgood NOPERMS_KEY "")"

grep -q "lacks permissions" <<<"$out"
check "403 is reported as missing permissions, not a bad key" $? "$(tail -6 <<<"$out")"

grep -q "System ready for use" <<<"$out"
check "no readiness claim on a permissionless key" $(( $? == 0 ? 1 : 0 )) "$(tail -6 <<<"$out")"

# ─── 5. n8n down ─────────────────────────────────────────────────────────────
new_sandbox
out="$(run_bootstrap non8n GOOD_KEY "")"

grep -q "System ready for use" <<<"$out"
check "no readiness claim while n8n is unreachable" $(( $? == 0 ? 1 : 0 )) "$(tail -6 <<<"$out")"

# ─── 6. Mandatory instructions are not dimmed ────────────────────────────────
# Dim text reads as a footnote. Everything in the three numbered steps is
# required, so none of it may be printed dim — an operator skimming past a
# grey line ends up with an installation that answers 404.
src="$REPO/scripts/bootstrap.sh"
dimmed="$(sed -n '/^_print_next_steps()/,/^}/p' "$src" | grep -n '\${DIM}' || true)"
check "no dimmed text among the mandatory steps" $([[ -z "$dimmed" ]] && echo 0 || echo 1) "$dimmed"

# ─── 7. The Flowise key instructions name a key name and its permissions ─────
msgs="$REPO/scripts/lib/messages.sh"
for lang_marker in "smart-rag-content-admin" "Chatflows" "Credentials" "Variables"; do
    (( $(grep -c "$lang_marker" "$msgs") >= 2 ))
    check "step 1.2 names '$lang_marker' in both languages" $? \
        "$(grep -c "$lang_marker" "$msgs") occurrence(s)"
done

# ─── Result ──────────────────────────────────────────────────────────────────
if (( ${#FAILURES[@]} == 0 )); then
    cat <<'SUMMARY'
All final-verification checks passed: readiness is announced only after every
check succeeds, a Flowise key is stored only once Flowise itself accepted it,
403 is told apart from 401, a closed stdin ends the loop instead of spinning,
and the mandatory steps are neither dimmed nor vague about the key's name and
permissions.
SUMMARY
    exit 0
fi
echo "FAILURES:"
printf '  - %s\n' "${FAILURES[@]}"
exit 1
