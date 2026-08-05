#!/usr/bin/env bash
# Bringing Tailscale up, and the two tailnet settings the deployment needs.
#
# Both came from the first real run. Joining worked, then the installer said
# "no MagicDNS name" and dropped the operator back at the mode question — a
# sentence about what they should have done instead of help doing it. And the
# name was read by grepping the whole `tailscale status --json` for the first
# "DNSName", which on a tailnet with other machines returns a PEER's name:
# the installation would then have published its URLs under a hostname
# belonging to somebody else's device.
#
# `tailscale`, `jq` and `confirm` are stubbed, so this runs anywhere.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

export LANG_CHOICE=en
# shellcheck source=/dev/null
source "$REPO/scripts/lib/messages.sh"

# $1 = Self.DNSName, $2 = MagicDNSEnabled (true|false), $3 = CertDomains
# (yes|no), $4 = confirm answers, $5 = extra shell appended to the harness
run_ts() {
    local self="$1" magic="$2" certs="$3" answers="${4:-y\ny\ny\n}" extra="${5:-}"
    local certdoms='[]'
    [[ "$certs" == "yes" ]] && certdoms='["'"${self%.}"'"]'
    local script; script="$(mktemp)"
    cat > "$script" <<EOF
#!/usr/bin/env bash
set -uo pipefail
export LANG_CHOICE=en
source "$REPO/scripts/lib/common.sh"
source "$REPO/scripts/lib/messages.sh"

# A tailnet with another machine in it — the peer's DNSName appears in the
# JSON before Self's, which is what the original parser tripped on.
tailscale() {
    case "\$1" in
        status) printf '%s' '{"Peer":{"n1":{"DNSName":"someone-else.tail99.ts.net."}},"BackendState":"Running","CertDomains":$certdoms,"CurrentTailnet":{"MagicDNSEnabled":$magic},"Self":{"DNSName":"$self"}}' ;;
        up)     return 0 ;;
        cert)   echo "CALLED_TAILSCALE_CERT" >&2; return 0 ;;
        *)      return 0 ;;
    esac
}
$extra
name="\$(tailscale_ensure_up)" && echo "RESULT:\$name" || echo "RESULT:FAILED"
EOF
    printf '%b' "$answers" | bash "$script" 2>&1
    rm -f "$script"
}

# ─── The name must be this machine's, not a peer's ──────────────────────────
out="$(run_ts 'i5.tail99.ts.net.' true yes)"
grep -q "RESULT:i5.tail99.ts.net" <<<"$out"
check "reads Self.DNSName" $? "$(tail -2 <<<"$out")"
grep -q "someone-else" <<<"$out"
check "never reports a peer's hostname" $(( $? == 0 ? 1 : 0 )) "$out"
# The trailing dot in the JSON is not part of a usable hostname.
grep -q "RESULT:i5.tail99.ts.net$" <<<"$out"
check "the trailing dot is stripped" $? "$(grep RESULT <<<"$out")"

# ─── MagicDNS genuinely off: guide, don't just report ───────────────────────
out="$(run_ts '' false no 'n\n')"
grep -qF "${MSG_EN[ts_magicdns_missing]}" <<<"$out"
check "MagicDNS switched off is named as the problem" $? "$(tail -3 <<<"$out")"
grep -q "login.tailscale.com/admin/dns" <<<"$out"
check "it links straight to the settings page" $? "$out"
grep -qF "${MSG_EN[ts_magicdns_howto]}" <<<"$out"
check "it says what to click, not just what is wrong" $? "$out"
grep -q "RESULT:FAILED" <<<"$out"
check "declining leaves the install unconfigured rather than half-set-up" $? "$(tail -2 <<<"$out")"

# ─── The name has not arrived yet — do NOT blame the setting ────────────────
# Reported from a real install: the first run said MagicDNS was disabled when
# it was not. `tailscale up` returns once the node is authenticated, but the
# name comes with the netmap moments later. The second run, minutes on,
# worked with nothing changed.
# The counter has to outlast more than one status call: ensure_up reads the
# backend state before it ever asks for the name, so a stub that flips after
# a single call never exercises the wait at all.
LATE='STATE=$(mktemp); echo 0 > "$STATE"
tailscale() {
    case "$1" in
        status) n=$(cat "$STATE"); echo $((n+1)) > "$STATE"
                if (( n < 2 )); then
                    printf "%s" "{\"BackendState\":\"Running\",\"CertDomains\":[\"i5.tail99.ts.net\"],\"CurrentTailnet\":{\"MagicDNSEnabled\":true},\"Self\":{\"DNSName\":\"\"}}"
                else
                    printf "%s" "{\"BackendState\":\"Running\",\"CertDomains\":[\"i5.tail99.ts.net\"],\"CurrentTailnet\":{\"MagicDNSEnabled\":true},\"Self\":{\"DNSName\":\"i5.tail99.ts.net.\"}}"
                fi ;;
        *) return 0 ;;
    esac
}'
out="$(run_ts '' true yes 'n\n' "$LATE")"
grep -q "RESULT:i5.tail99.ts.net" <<<"$out"
check "a late-arriving name is waited for, not misdiagnosed" $? "$(tail -3 <<<"$out")"
grep -qF "${MSG_EN[ts_magicdns_missing]}" <<<"$out"
check "and the operator is never told to fix a correct setting" $(( $? == 0 ? 1 : 0 )) "$out"
grep -qF "${MSG_EN[ts_awaiting_name]}" <<<"$out"
check "the wait is explained rather than looking like a hang" $? "$out"

# ─── MagicDNS fine, HTTPS not ───────────────────────────────────────────────
out="$(run_ts 'i5.tail99.ts.net.' true no 'n\n')"
grep -qF "${MSG_EN[ts_https_missing]}" <<<"$out"
check "a missing certificate is reported as HTTPS, not as a name problem" $? "$(tail -3 <<<"$out")"
grep -qF "${MSG_EN[ts_magicdns_missing]}" <<<"$out"
check "and is not blamed on MagicDNS, which works" $(( $? == 0 ? 1 : 0 )) "$out"

# `tailscale cert` PROVISIONS a certificate and is rate-limited by Let's
# Encrypt — using it as a probe inside a retry loop can lock the tailnet out
# for hours. CertDomains answers the same question by reading.
out="$(run_ts 'i5.tail99.ts.net.' true yes 'y\n')"
grep -q "CALLED_TAILSCALE_CERT" <<<"$out"
check "HTTPS is checked by reading, not by requesting a certificate" $(( $? == 0 ? 1 : 0 )) "$out"

# ─── Bounded, so a non-interactive stdin cannot hang the installer ──────────
out="$(timeout 25 bash -c "$(declare -f run_ts); REPO='$REPO'; run_ts '' false no ''" 2>&1)"
rc=$?
check "it terminates on closed stdin instead of looping" $(( rc == 124 ? 1 : 0 )) "timed out"
grep -q "RESULT:FAILED" <<<"$out"
check "and reports failure rather than a bogus name" $? "$(tail -2 <<<"$out")"

# ─── Wiring ─────────────────────────────────────────────────────────────────
FN="$(sed -n '/^tailscale_magicdns_name()/,/^}/p' "$REPO/scripts/lib/common.sh")"
grep -q '\.Self\.DNSName' <<<"$FN"
check "Self is addressed explicitly, not by first match" $? "$FN"
# The jq-less fallback must narrow to the Self object BEFORE matching, or it
# reintroduces the peer bug on a machine without jq.
if grep -q 'head -1' <<<"$FN"; then
    grep -q '"Self":{' <<<"$FN"
    check "the jq-less fallback scopes to Self before matching" $? "$FN"
fi

# jq's `//` fires on false as well as null, so a boolean read through it
# reports "unknown" for a setting that is genuinely off — and the caller
# then waits for something that will never arrive.
ENABLED_FN="$(sed -n '/^tailscale_magicdns_enabled()/,/^}/p' "$REPO/scripts/lib/common.sh")"
grep -q 'MagicDNSEnabled // ' <<<"$ENABLED_FN"
check "the boolean is not read through jq's // operator" $(( $? == 0 ? 1 : 0 )) "$ENABLED_FN"

# Behavioural version of the same: off must read as off.
out="$(bash -c "
    export LANG_CHOICE=en
    source '$REPO/scripts/lib/common.sh'; source '$REPO/scripts/lib/messages.sh'
    tailscale() { printf '%s' '{\"CurrentTailnet\":{\"MagicDNSEnabled\":false}}'; }
    tailscale_magicdns_enabled")"
[[ "$out" == "no" ]]
check "MagicDNSEnabled:false reads as 'no', not 'unknown'" $? "got '$out'"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All Tailscale-setup checks passed: the MagicDNS name is read from Self"
echo "rather than the first match in the JSON, so a peer's hostname can never"
echo "be published as this machine's, and its trailing dot is stripped; a"
echo "tailnet missing MagicDNS or HTTPS is walked through — naming which of"
echo "the two is missing, linking the settings page and saying what to click,"
echo "then re-checking — instead of being reported and abandoned; a name that"
echo "has merely not arrived yet is waited for rather than misdiagnosed as a"
echo "disabled setting, with the wait explained; HTTPS is read from"
echo "CertDomains instead of provisioning a rate-limited certificate as a"
echo "probe; declining fails cleanly; and the retry loop is bounded, so a"
echo "closed stdin ends the installer instead of spinning."
