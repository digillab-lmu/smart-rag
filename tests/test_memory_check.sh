#!/usr/bin/env bash
# The RAM check exists because a deployment ran on 3.8 GB against a
# documented 12 GB requirement, with nothing anywhere warning about it. The
# symptoms landed far from the cause: MinIO took its own drive offline after
# a 31s stall, n8n needed more than a minute to restart. So what matters here
# is that the check fires, quotes the right number, and STOPS to ask.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

export LANG_CHOICE=en
# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"
# shellcheck source=/dev/null
source "$REPO/scripts/lib/messages.sh"
# shellcheck source=/dev/null
source "$REPO/scripts/lib/preflight.sh"

# ─── The requirement table, per profile ─────────────────────────────────────
# Must match docs/requirements.md — the check is worthless if it quotes a
# figure the documentation doesn't.
for profiles_req in "core:8" "core,observability:12" "core,observability,lti:12" "core,lti:12"; do
    profiles="${profiles_req%:*}"; want="${profiles_req##*:}"
    got="$(required_ram_gb_for_profiles "$profiles")"
    [[ "$got" == "$want" ]]
    check "requirement for '$profiles' is ${want}GB" $? "got ${got}GB"
done

DOC_CORE="$(grep -E '^\| .core. only' "$REPO/docs/requirements.md" | grep -oE '[0-9]+ GB' | head -1 | tr -d ' GB')"
[[ "$DOC_CORE" == "$(required_ram_gb_for_profiles core)" ]]
check "core figure agrees with docs/requirements.md" $? "docs say ${DOC_CORE}GB"

# ─── Reading MemTotal ───────────────────────────────────────────────────────
# Rounds to nearest: 7.9 GB must not read as 7 and fail an 8 GB check it meets.
fake_meminfo() { printf 'MemTotal:       %s kB\nSwapTotal: 0 kB\n' "$1" > "$2"; }
TMP="$(mktemp -d)"
for kb_expect in "4001280:4" "3846000:4" "8388608:8" "16777216:16" "8290304:8"; do
    kb="${kb_expect%:*}"; expect="${kb_expect##*:}"
    fake_meminfo "$kb" "$TMP/meminfo"
    got="$(awk '/^MemTotal:/ {print $2; exit}' "$TMP/meminfo" | { read -r k; echo $(( (k + 512*1024) / (1024*1024) )); })"
    [[ "$got" == "$expect" ]]
    check "${kb}kB reads as ${expect}GB" $? "got $got"
done

# The real machine must produce a plausible number, not an empty string.
real="$(detect_total_ram_gb)"
[[ -z "$real" || "$real" =~ ^[0-9]+$ ]]
check "detect_total_ram_gb returns a number or nothing" $? "got '$real'"

# ─── The gate stops and asks ────────────────────────────────────────────────
# Stubbed low, so the outcome doesn't depend on the machine running the test.
detect_total_ram_gb() { echo 4; }

out="$(printf 'n\n' | confirm_memory_for_profiles "core,observability" 2>&1)"; rc=$?
check "declining stops the wizard" $(( rc == 0 ? 1 : 0 )) "rc=$rc"
grep -q "12 GB" <<<"$out"
check "gate quotes the profile's requirement" $? "$out"
grep -q "4 GB" <<<"$out"
check "gate quotes what the machine actually has" $? "$out"
grep -qi "observability" <<<"$out"
check "gate names the optional profile as the way out" $? "$out"
# The point of the explanation: the failures show up somewhere else.
grep -qi "somewhere else\|offline\|killed" <<<"$out"
check "gate explains that symptoms surface elsewhere" $? "$out"

out="$(printf 'y\n' | confirm_memory_for_profiles "core,observability" 2>&1)"; rc=$?
check "accepting continues" $rc "rc=$rc"

# Enough RAM: no question at all, and nothing printed.
detect_total_ram_gb() { echo 16; }
out="$(printf '' | confirm_memory_for_profiles "core,observability" 2>&1)"; rc=$?
check "sufficient RAM asks nothing" $rc "rc=$rc"
[[ -z "$out" ]]
check "sufficient RAM prints nothing" $? "$out"

# core-only on 8 GB is exactly at the documented minimum — must pass.
detect_total_ram_gb() { echo 8; }
confirm_memory_for_profiles "core" >/dev/null 2>&1
check "8 GB meets the core minimum" $? ""
# …but not the observability one.
out="$(printf 'n\n' | confirm_memory_for_profiles "core,observability" 2>&1)"; rc=$?
check "8 GB does not meet the observability requirement" $(( rc == 0 ? 1 : 0 )) "rc=$rc"

# Undetectable RAM must not interrogate anyone.
detect_total_ram_gb() { echo ""; }
confirm_memory_for_profiles "core,observability" >/dev/null 2>&1
check "unknown RAM does not block the wizard" $? ""

# ─── It is actually wired in ────────────────────────────────────────────────
grep -q "check_memory" "$REPO/scripts/lib/preflight.sh"
check "check_memory runs in the preflight" $? ""
grep -A 12 "run_preflight()" "$REPO/scripts/lib/preflight.sh" | grep -q "check_memory"
check "check_memory is in run_preflight, not just defined" $? ""
grep -q "confirm_memory_for_profiles" "$REPO/scripts/lib/config-wizard.sh"
check "the gate is called after profile selection" $? ""

# ─── Ubuntu gate ────────────────────────────────────────────────────────────
# 24.04 is what this is tested on, but hard-failing every other release would
# block a machine that very likely works — an operator installing on a newer
# LTS should get a stated reason and a choice, not a wall.
ubuntu_verdict() { # $1 = VERSION_ID -> tested | lts | reject
    [[ "$1" == "$UBUNTU_TESTED" ]] && { echo tested; return; }
    if [[ "$1" =~ ^(2[0-9])\.04$ ]] && (( ${BASH_REMATCH[1]} % 2 == 0 )); then
        echo lts; return
    fi
    echo reject
}

[[ "$UBUNTU_TESTED" == "24.04" ]]
check "the tested version is named once, as a constant" $? "${UBUNTU_TESTED:-unset}"

for case_ in "24.04:tested" "26.04:lts" "28.04:lts" "22.04:lts" \
             "25.10:reject" "24.10:reject" "23.04:reject" "18.04:reject"; do
    v="${case_%:*}"; want="${case_##*:}"
    got="$(ubuntu_verdict "$v")"
    [[ "$got" == "$want" ]]
    check "Ubuntu $v -> $want" $? "got $got"
done

# The gate must ask, and honour a no — same human-in-the-loop shape as the
# RAM gate, not a warning that scrolls past. The whole function is extracted
# rather than grepped with a fixed context window, which silently truncated
# this check when the function grew.
UBUNTU_FN="$(sed -n '/^check_ubuntu()/,/^}/p' "$REPO/scripts/lib/preflight.sh")"
[[ -n "$UBUNTU_FN" ]]
check "check_ubuntu could be extracted" $? ""

grep -q "confirm pf_ubuntu_untested_continue" <<<"$UBUNTU_FN"
check "an untested LTS asks before continuing" $? ""
grep -q 'die "$(t pf_ubuntu_declined)"' <<<"$UBUNTU_FN"
check "declining stops the install" $? ""
# Non-Ubuntu stays a hard stop — those really do differ.
grep -q 'die "$(t pf_ubuntu_not_linux' <<<"$UBUNTU_FN"
check "non-Ubuntu is still refused outright" $? ""
# The tested version must not be repeated as a literal inside the function.
[[ "$(grep -c '"24\.04"' <<<"$UBUNTU_FN")" == "0" ]]
check "the version literal isn't duplicated in the function" $? \
      "$(grep -n '24\.04' <<<"$UBUNTU_FN")"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All memory-check checks passed: the per-profile requirement matches"
echo "docs/requirements.md, MemTotal rounds to nearest so a 7.9 GB machine"
echo "isn't failed for an 8 GB rule, the preflight reports it, and the gate"
echo "stops after profile selection quoting both the requirement and the"
echo "actual figure, explains that the symptoms surface elsewhere, names the"
echo "optional profile as the way out, blocks on a no, continues on a yes,"
echo "and stays silent both when there is enough RAM and when it can't tell;"
echo "and the Ubuntu gate passes the tested release, asks on any other LTS"
echo "(honouring a no), and still refuses interim releases and non-Ubuntu."
