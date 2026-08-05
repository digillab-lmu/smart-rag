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

# $1 = Self.DNSName, $2 = cert succeeds (yes|no), $3 = confirm answers,
# $4 = extra shell appended to the harness
run_ts() {
    local self="$1" cert="$2" answers="${3:-y\ny\ny\n}" extra="${4:-}"
    local script; script="$(mktemp)"
    cat > "$script" <<EOF
#!/usr/bin/env bash
set -uo pipefail
export LANG_CHOICE=en
source "$REPO/scripts/lib/common.sh"
source "$REPO/scripts/lib/messages.sh"

# A tailnet with another machine in it — the peer's DNSName appears in the
# JSON before Self's, which is exactly what the original parser tripped on.
tailscale() {
    case "\$1" in
        status) printf '%s' '{"Peer":{"n1":{"DNSName":"someone-else.tail99.ts.net."}},"BackendState":"Running","Self":{"DNSName":"$self"}}' ;;
        cert)   [[ "$cert" == "yes" ]] ;;
        up)     return 0 ;;
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
out="$(run_ts 'i5.tail99.ts.net.' yes)"
grep -q "RESULT:i5.tail99.ts.net" <<<"$out"
check "reads Self.DNSName" $? "$(tail -2 <<<"$out")"
grep -q "someone-else" <<<"$out"
check "never reports a peer's hostname" $(( $? == 0 ? 1 : 0 )) "$out"
# The trailing dot in the JSON is not part of a usable hostname.
grep -q "RESULT:i5.tail99.ts.net$" <<<"$out"
check "the trailing dot is stripped" $? "$(grep RESULT <<<"$out")"

# ─── Missing MagicDNS: guide, don't just report ─────────────────────────────
# The state flips to "configured" after the first confirmation, standing in
# for the operator actually changing it in the admin console.
FLIP='STATE=$(mktemp); echo 0 > "$STATE"
tailscale() {
    case "$1" in
        status) if [[ "$(cat "$STATE")" == 0 ]]; then
                    printf "%s" "{\"Peer\":{\"n1\":{\"DNSName\":\"someone-else.tail99.ts.net.\"}},\"BackendState\":\"Running\",\"Self\":{\"DNSName\":\"\"}}"
                else
                    printf "%s" "{\"Peer\":{\"n1\":{\"DNSName\":\"someone-else.tail99.ts.net.\"}},\"BackendState\":\"Running\",\"Self\":{\"DNSName\":\"i5.tail99.ts.net.\"}}"
                fi ;;
        cert)   [[ "$(cat "$STATE")" != 0 ]] ;;
        *)      return 0 ;;
    esac
}
confirm() { echo 1 > "$STATE"; return 0; }'

out="$(run_ts '' no 'y\n' "$FLIP")"
grep -q "RESULT:i5.tail99.ts.net" <<<"$out"
check "a fixed tailnet is picked up on re-check" $? "$(tail -3 <<<"$out")"
grep -qF "${MSG_EN[ts_magicdns_missing]}" <<<"$out"
check "it names MagicDNS as what is missing" $? "$(tail -3 <<<"$out")"
grep -q "login.tailscale.com/admin/dns" <<<"$out"
check "it links straight to the settings page" $? "$out"
grep -qF "${MSG_EN[ts_magicdns_howto]}" <<<"$out"
check "it says what to click, not just what is wrong" $? "$out"

# ─── MagicDNS fine, HTTPS not ───────────────────────────────────────────────
out="$(run_ts 'i5.tail99.ts.net.' no 'n\n')"
grep -qF "${MSG_EN[ts_https_missing]}" <<<"$out"
check "a missing certificate is reported as HTTPS, not as a name problem" $? "$(tail -3 <<<"$out")"
grep -qF "${MSG_EN[ts_magicdns_missing]}" <<<"$out"
check "and is not blamed on MagicDNS, which works" $(( $? == 0 ? 1 : 0 )) "$out"
grep -q "RESULT:FAILED" <<<"$out"
check "declining the re-check fails rather than continuing" $? "$(tail -2 <<<"$out")"

# ─── Bounded, so a non-interactive stdin cannot hang the installer ──────────
# `confirm` returns its default on an empty read, which is what EOF looks
# like — an unbounded retry loop would spin forever.
out="$(timeout 20 bash -c "$(declare -f run_ts); REPO='$REPO'; run_ts 'i5.tail99.ts.net.' no ''" 2>&1)"
rc=$?
check "it terminates on closed stdin instead of looping" $(( rc == 124 ? 1 : 0 )) "timed out"
grep -q "RESULT:FAILED" <<<"$out"
check "and reports failure rather than a bogus name" $? "$(tail -2 <<<"$out")"

# ─── Wiring ─────────────────────────────────────────────────────────────────
grep -q 'jq -r ..Self.DNSName' "$REPO/scripts/lib/common.sh"
check "Self is addressed explicitly, not by first match" $? ""
FN="$(sed -n '/^tailscale_magicdns_name()/,/^}/p' "$REPO/scripts/lib/common.sh")"
grep -q 'head -1' <<<"$FN" && grep -q 'Self' <<<"$FN"
check "any fallback still scopes to Self before matching" $? "$FN"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All Tailscale-setup checks passed: the MagicDNS name is read from Self"
echo "rather than the first match in the JSON, so a peer's hostname can never"
echo "be published as this machine's, and its trailing dot is stripped; a"
echo "tailnet missing MagicDNS or HTTPS is walked through — naming which of"
echo "the two is missing, linking the settings page and saying what to click,"
echo "then re-checking — instead of being reported and abandoned; declining"
echo "fails cleanly; and the retry loop is bounded, so a closed stdin ends the"
echo "installer instead of spinning."
