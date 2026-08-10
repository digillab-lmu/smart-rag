#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# The hand-over message to the Content Admin
# ═════════════════════════════════════════════════════════════════════════════
#
# Everything else the installer prints is read by the system administrator, on
# the server. The person who will use the system daily reads none of it, and
# the address they need is one they cannot guess — least of all in Tailscale
# mode, where it is a machine name on a private network.
#
# So the installer composes that message. Three things about it are worth a
# test rather than a reading:
#
#   1. It is assembled from .env, so every way a value can be missing shows up
#      as a broken sentence in somebody's inbox: an unexpanded ${DOMAIN}, an
#      unfilled %s, a URL that is just "https://".
#   2. Two of its sentences are conditional — whether an account already
#      exists, and whether the reader has to be told about Tailscale first.
#      The wrong one is worse than neither.
#   3. It leaves the machine. It must not carry a password, and it must not be
#      sent without the operator being shown it and asked.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"
# shellcheck source=/dev/null
source "$REPO/scripts/lib/messages.sh"
# shellcheck source=/dev/null
source "$REPO/scripts/lib/config-wizard.sh"

# bootstrap.sh runs its work at source time, so the functions are lifted out
# rather than sourced. Lifting them means this test reads the shipped code and
# not a copy of it.
REPO_ROOT="$SANDBOX"
eval "$(sed -n '/^_handover_body() {/,/^}/p' "$REPO/scripts/bootstrap.sh")"
eval "$(sed -n '/^_handover_mail() {/,/^}/p' "$REPO/scripts/bootstrap.sh")"
declare -F _handover_body >/dev/null && declare -F _handover_mail >/dev/null
check "both hand-over functions were found in bootstrap.sh" $? \
      "the sed extraction found nothing — renamed or reindented?"

write_env() {   # write_env KEY=VALUE ...
    : > "$SANDBOX/.env"
    printf '%s\n' "$@" >> "$SANDBOX/.env"
}

BASE_ENV=(
    'DOMAIN="lmu.de"'
    'SUBDOMAIN_PREFIX=""'
    'DEPLOYMENT_MODE="domain"'
    'COURSE_NAME="Didaktik der Chemie"'
    'COURSE_ID="chemie"'
    'ADMIN_EMAIL="admin@lmu.de"'
    'FLOWISE_PUBLIC_URL="https://smart-rag.lmu.de"'
    'CONTENT_ADMIN_PUBLIC_URL=""'
    'CONTENT_ADMIN_USERNAME=""'
    'CONTENT_ADMIN_PASSWORD_HASH=""'
    'SMTP_HOST=""'
)

for lang in en de; do
    LANG_CHOICE="$lang"
    write_env "${BASE_ENV[@]}"
    body="$(_handover_body)"

    # ─── 1. Nothing unresolved may reach a reader ────────────────────────────
    # Each of these has actually shipped somewhere in this project: a literal
    # ${DOMAIN} in a sender address, a %s left standing by a missing argument.
    grep -q '\${' <<<"$body"
    check "[$lang] no unexpanded shell interpolation" $(( $? == 0 ? 1 : 0 )) \
          "$(grep -o '\${[A-Z_]*}' <<<"$body" | head -1)"
    grep -q '%s' <<<"$body"
    check "[$lang] no unfilled placeholder" $(( $? == 0 ? 1 : 0 )) \
          "a message key was called with too few arguments"
    grep -qE 'https?://[[:space:]]*$' <<<"$body"
    check "[$lang] no bare scheme with no host" $(( $? == 0 ? 1 : 0 )) "$body"
    # A missing key renders as the key name — the docs_col_action bug.
    grep -qE '^[a-z_]*handover[a-z_]*$' <<<"$body"
    check "[$lang] every key resolved to text" $(( $? == 0 ? 1 : 0 )) \
          "$(grep -E '^[a-z_]*handover[a-z_]*$' <<<"$body" | head -1)"

    # ─── 2. The things the reader came for ───────────────────────────────────
    grep -q 'https://content.lmu.de' <<<"$body"
    check "[$lang] the Content Admin URL is in the message" $? "$body"
    grep -q 'Didaktik der Chemie' <<<"$body"
    check "[$lang] the course is named" $? ""
    grep -q 'admin@lmu.de' <<<"$body"
    check "[$lang] somebody to ask is named" $? ""
    for n in 1. 2. 3.; do
        grep -q "^$n " <<<"$body"
        check "[$lang] step $n is present" $? "$body"
    done

    # ─── 3. It must not carry a secret ───────────────────────────────────────
    # The mail goes to a mailbox this installation does not control, so the
    # password belongs in the conversation that follows it, not in it.
    # No dollar signs in the fake hash: .env is read with `source`, which
    # would expand them away and quietly make this check pass on nothing.
    write_env "${BASE_ENV[@]/CONTENT_ADMIN_PASSWORD_HASH=\"\"/CONTENT_ADMIN_PASSWORD_HASH=\"scrypt-32768-deadbeef\"}"
    body_hash="$(_handover_body)"
    grep -q 'deadbeef' <<<"$body_hash"
    check "[$lang] no credential material is included" $(( $? == 0 ? 1 : 0 )) \
          "a hash from .env reached the message body"

    # ─── 4. The two conditional sentences ────────────────────────────────────
    # No account yet: the reader creates it, and is told to hurry, because
    # whoever opens the page first owns the account.
    write_env "${BASE_ENV[@]}"
    new_body="$(_handover_body)"
    write_env "${BASE_ENV[@]/CONTENT_ADMIN_USERNAME=\"\"/CONTENT_ADMIN_USERNAME=\"kursleitung\"}"
    old_body="$(_handover_body)"
    grep -q 'kursleitung' <<<"$old_body"
    check "[$lang] an existing account is named by user name" $? "$old_body"
    grep -q 'kursleitung' <<<"$new_body"
    check "[$lang] no account is invented when none exists" $(( $? == 0 ? 1 : 0 )) ""
    # The two sentences must exclude each other — printing both would tell the
    # reader to create an account that is already there.
    [[ "$new_body" != "$old_body" ]]
    check "[$lang] the account sentence actually differs between the two" $? ""

    # ─── 5. Tailscale, only in Tailscale mode ────────────────────────────────
    write_env "${BASE_ENV[@]/DEPLOYMENT_MODE=\"domain\"/DEPLOYMENT_MODE=\"tailscale\"}" \
              'CONTENT_ADMIN_PUBLIC_URL="https://hp-i5-ubuntu.tail1234.ts.net"'
    ts_body="$(_handover_body)"
    grep -qi 'tailscale' <<<"$ts_body"
    check "[$lang] tailscale mode warns about the network" $? "$ts_body"
    grep -q 'hp-i5-ubuntu.tail1234.ts.net' <<<"$ts_body"
    check "[$lang] tailscale mode uses the MagicDNS address" $? "$ts_body"
    grep -qi 'tailscale' <<<"$new_body"
    check "[$lang] domain mode does not mention tailscale" $(( $? == 0 ? 1 : 0 )) ""
done

# ─── 6. Sending is deliberate, never a side effect ──────────────────────────
# A canary docker on PATH: if a path that must not send reaches for it, the
# test says so instead of the operator finding out from a colleague's inbox.
#
# The canary leaves a FILE rather than printing. Printing does not work here
# and the first version of this test proved it: the send happens inside a
# command substitution with 2>&1, so the canary's output was captured as the
# send's own output and never reached the text being grepped — the test went
# green against a version with the confirmation deleted. A file is outside
# every redirection there is.
mkdir -p "$SANDBOX/bin"
cat > "$SANDBOX/bin/docker" <<CANARY
#!/usr/bin/env bash
echo called >> "$SANDBOX/canary"
exit 0
CANARY
chmod +x "$SANDBOX/bin/docker"

# sent_nothing → 0 when the canary was not triggered since the last reset.
reset_canary() { rm -f "$SANDBOX/canary"; }
sent_nothing() { [[ ! -e "$SANDBOX/canary" ]]; }

LANG_CHOICE=en
write_env "${BASE_ENV[@]}"          # SMTP_HOST empty
reset_canary
out="$(PATH="$SANDBOX/bin:$PATH" _handover_mail </dev/null 2>&1)"
sent_nothing
check "with no relay, nothing is sent" $? "$out"
grep -q 'copy from here' <<<"$out"
check "with no relay, the message is printed to be copied" $? "$out"

# With a relay but no address given, still nothing goes out.
reset_canary
write_env "${BASE_ENV[@]/SMTP_HOST=\"\"/SMTP_HOST=\"172.28.92.1\"}"
out="$(PATH="$SANDBOX/bin:$PATH" _handover_mail </dev/null 2>&1)"
sent_nothing
check "an empty address sends nothing" $? "$out"

# An address, but the confirmation declined.
reset_canary
out="$(printf 'kurs@lmu.de\nn\n' | PATH="$SANDBOX/bin:$PATH" _handover_mail 2>&1)"
sent_nothing
check "declining the confirmation sends nothing" $? "$out"

# A malformed address must not reach the relay either.
reset_canary
out="$(printf 'kurs-at-lmu\ny\n' | PATH="$SANDBOX/bin:$PATH" _handover_mail 2>&1)"
sent_nothing
check "an invalid address sends nothing" $? "$out"

# And the message must be on screen BEFORE the address is asked for — being
# asked to send something unseen is how a wizard gets clicked through.
out="$(printf '\n' | PATH="$SANDBOX/bin:$PATH" _handover_mail 2>&1)"
copy_line="$(grep -n 'copy from here' <<<"$out" | head -1 | cut -d: -f1)"
ask_line="$(grep -n 'Email address of the Content Admin' <<<"$out" | head -1 | cut -d: -f1)"
[[ -n "$copy_line" && -n "$ask_line" ]] && (( copy_line < ask_line ))
check "the message is shown before the address is asked for" $? \
      "message at line ${copy_line:-none}, question at line ${ask_line:-none}"

# ─── 7. Both catalogues, and the sender script ──────────────────────────────
while read -r key; do
    (( $(grep -c "\[$key\]=" "$REPO/scripts/lib/messages.sh") == 2 ))
    check "$key is defined in both languages" $? \
          "$(grep -c "\[$key\]=" "$REPO/scripts/lib/messages.sh") definition(s)"
done < <(grep -oE '\bt handover_[a-z0-9_]+' "$REPO/scripts/bootstrap.sh" \
         | awk '{print $2}' | sort -u)

# The script is only reachable if the image actually contains it.
grep -qE '^COPY \*\.py' "$REPO/content-admin/Dockerfile"
check "the image copies the sender script in" $? \
      "send_handover.py must be inside smartrag-content-admin to be exec'd"
python3 -c "import ast, sys; ast.parse(open('$REPO/content-admin/send_handover.py').read())"
check "send_handover.py parses" $? ""

# Its exit codes are the installer's fallback signal, so they are part of the
# contract: 1 = would not try, 2 = tried and failed. Both must leave the
# installer able to print the message instead.
pushd "$REPO/content-admin" >/dev/null || exit 1
python3 send_handover.py </dev/null >/dev/null 2>&1
check "no arguments is refused with 1" $(( $? == 1 ? 0 : 1 )) ""
echo "body" | python3 send_handover.py "" "subject" >/dev/null 2>&1
check "an empty recipient is refused with 1" $(( $? == 1 ? 0 : 1 )) ""
printf '' | python3 send_handover.py "a@b.de" "subject" >/dev/null 2>&1
check "an empty body is refused with 1" $(( $? == 1 ? 0 : 1 )) ""
# No relay configured: a failure to send, not a crash and not a silent success.
rc=0
echo "body" | SMARTRAG_ENV_FILE="$SANDBOX/.env.norelay" python3 send_handover.py \
    "a@b.de" "subject" >/dev/null 2>&1 || rc=$?
check "a relay failure exits 2, not 0" $(( rc == 2 ? 0 : 1 )) "exit code was $rc"
popd >/dev/null || exit 1

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All hand-over checks passed: the message is assembled from .env with no"
echo "unexpanded variable, unfilled placeholder or unresolved key; it names the"
echo "course, the Content Admin address and someone to ask; it carries no"
echo "credential; the account and Tailscale sentences each appear only when"
echo "true; nothing is sent without a relay, an address, a valid address and a"
echo "confirmation, and the message is on screen before the question is asked."