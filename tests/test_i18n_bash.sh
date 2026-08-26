#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# No message may contain command substitution
# ═════════════════════════════════════════════════════════════════════════════
#
# messages.sh assigns its catalogue inside double quotes. In bash, `...` and
# $(...) are command substitution there — they RUN, at the moment the file is
# sourced, which every script in this project does at startup.
#
# This is not hypothetical. A message written as
#
#     "... and `sudo smartrag` → Secrets shows it again ..."
#
# with the backticks meant as markdown emphasis executed `sudo smartrag` on
# every source. That command is this project's admin tool, which sources
# messages.sh itself — so it recursed about four thousand levels deep and
# left roughly twelve thousand sleeping processes holding six gigabytes on a
# sixteen-gigabyte machine, with no error message anywhere.
#
# It survived the whole test suite because the suites source messages.sh too,
# and in a sandbox `sudo` simply fails and returns — the recursion needs a
# host where the command actually exists. A runtime test cannot be trusted to
# catch this; a static one can.
#
# Placeholders are %s (printf), never interpolation, so there is no legitimate
# use of either form in a message value.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

MSGS="$REPO/scripts/lib/messages.sh"

# Backticks, anywhere in the file.
backticks="$(grep -n '`' "$MSGS" || true)"
check "no backticks in messages.sh" $([[ -z "$backticks" ]] && echo 0 || echo 1) \
      "$(head -3 <<<"$backticks")"

# $( ... ) inside a message value. The keys themselves are plain, so any
# occurrence on an assignment line is in the text.
cmdsub="$(grep -nE '^\s*\[[a-z0-9_]+\]=".*\$\(' "$MSGS" || true)"
check "no \$( ) substitution in a message" $([[ -z "$cmdsub" ]] && echo 0 || echo 1) \
      "$(head -3 <<<"$cmdsub")"

# The same trap anywhere else. A backtick in a comment is harmless; one in a
# double-quoted value is a command. Escaped backticks are exempt on purpose:
# common.sh and templates.sh escape them when writing .env, which is the
# defence against the same class of bug in values an operator supplies.
for f in "$REPO"/scripts/*.sh "$REPO"/scripts/lib/*.sh; do
    [[ -f "$f" ]] || continue
    bt="$(grep -nE '^[^#]*="[^"]*[^\\]`' "$f" || true)"
    check "no unescaped backtick in a string in $(basename "$f")" \
          $([[ -z "$bt" ]] && echo 0 || echo 1) "$(head -2 <<<"$bt")"
done

# And the belt-and-braces check: sourcing the catalogue must not run anything.
# A canary on PATH would be executed by exactly the bug above.
TMPBIN="$(mktemp -d)"
CANARY="$TMPBIN/canary-flag"
for name in sudo smartrag docker systemctl; do
    printf '#!/usr/bin/env bash\ntouch %s\n' "$CANARY" > "$TMPBIN/$name"
    chmod +x "$TMPBIN/$name"
done
(
    PATH="$TMPBIN:$PATH"
    # shellcheck source=/dev/null
    source "$MSGS" >/dev/null 2>&1
)
[[ ! -e "$CANARY" ]]
check "sourcing messages.sh executes no command" $? "a canary on PATH was run"
rm -rf "$TMPBIN"

# ─── No message nobody uses ──────────────────────────────────────────────────
# A key left behind when its dialogue was rewritten is the same kind of thing
# as a script that no longer exists: it reads like part of the system, and the
# next person changing that dialogue reads it as a requirement. Thirteen
# accumulated when the mail section was reworked, and thirty-eight more had
# outlived the migration script they belonged to.
#
# Only cfg_*, admin_*, next_* and repair_* are checked: those are dialogue
# text with one call site each. Keys used through a computed name — the
# inventory's inv_* among them — cannot be found by grep and would be
# reported as dead while being in daily use.
unused=()
while read -r key; do
    grep -rq "$key" "$REPO"/scripts/*.sh "$REPO"/scripts/lib/*.sh \
        --exclude=messages.sh || unused+=("$key")
done < <(grep -oE '^\s*\[(cfg|admin|next|repair)_[a-z0-9_]+\]' "$MSGS" \
         | tr -d '[] ' | sort -u)
check "no message is left without a caller" ${#unused[@]} \
      "$(printf '%s ' "${unused[@]:0:6}")"

# ─── And the inverse, which is the one that reaches the operator ─────────────
# A message without a caller is dead weight. A caller without a message prints
# "MISSING:the_key" on the screen, in the middle of a run — which is how
# vfyb_garage_layout_ok was found: by an operator verifying a backup, not by
# this file, because only one direction was ever checked.
#
# Literal keys only: t "$var" and computed names cannot be resolved here, and
# reporting them would make the check unusable.
missing=()
while read -r key; do
    [[ -n "$key" ]] || continue
    grep -qE "^\s*\[$key\]=" "$MSGS" || missing+=("$key")
# Anchored on the call form. A bare "\bt +word" also matches prose — "don't
# archives" among them — and reported five words from comments as missing
# keys.
done < <(grep -rhoE '\$\(t +[a-z][a-z0-9_]+' "$REPO"/scripts/*.sh "$REPO"/scripts/lib/*.sh \
         --exclude=messages.sh | sed -E 's/^\$\(t +//' | sort -u)
check "every key a script asks for exists in the catalogue" ${#missing[@]} \
      "$(printf '%s ' "${missing[@]:0:6}") — these print MISSING:<key> to the operator"

# ─── No key assigned twice in the same catalogue ─────────────────────────────
# bash keeps the last assignment silently, so a duplicate is not an error at
# load time — it is a message that reads correctly in the file and wrong on
# screen. Found once for real: next_flowise_perm_chatflows_why was pasted into
# MSG_EN in German as well as English, and the English installer showed the
# German sentence while the English one sat two lines above it, untouched.
dupes="$(awk '
    /^declare -A MSG_EN=\(/ { cat="EN"; next }
    /^declare -A MSG_DE=\(/ { cat="DE"; next }
    cat != "" && match($0, /^[ \t]*\[[a-z0-9_]+\]=/) {
        key = $0
        sub(/^[ \t]*\[/, "", key); sub(/\]=.*$/, "", key)
        if (seen[cat "/" key]++) print cat " " key " (line " NR ")"
    }
' "$REPO/scripts/lib/messages.sh")"
[[ -z "$dupes" ]]
check "no key is assigned twice within one catalogue" $? "$dupes"

# ─── The mail dialogue does not accept a mail server that is not there ───────
# Option 1 says "a mail server already runs on this machine". The detection
# that could tell ran two lines above and was not consulted, so on a machine
# with no MTA the option was accepted in silence — and the installation
# finished believing it could send mail. Reported from a real install.
out="$(timeout 30 bash -c '
    source "'"$REPO"'/scripts/lib/messages.sh"
    source "'"$REPO"'/scripts/lib/common.sh"
    source "'"$REPO"'/scripts/lib/config-wizard.sh"
    LANG_CHOICE=en
    detect_existing_mail_relay() { echo "none:0"; }
    printf "1\nn\n" | ask_mail_config' 2>&1)"
rc=$?
# Two answers and then EOF, which is the case both wrong shapes fail: a
# recursive call runs out of stack, and an unbounded loop spins because at EOF
# the menu returns its default and the confirmation returns its own, forever.
check "the mail dialogue terminates on an exhausted stdin" $(( rc == 124 ? 1 : 0 )) \
      "it timed out — the decline path recursed, or looped without a bound"
grep -qi 'No mail server was found' <<<"$out"
check "choosing the existing-server option with no server warns" $? "$out"
grep -qi 'nothing is configured' <<<"$out"
check "and declining ends in no mail rather than in a false setting" $? "$out"

out="$(timeout 30 bash -c '
    source "'"$REPO"'/scripts/lib/messages.sh"
    source "'"$REPO"'/scripts/lib/common.sh"
    source "'"$REPO"'/scripts/lib/config-wizard.sh"
    LANG_CHOICE=en
    detect_existing_mail_relay() { echo "postfix:1"; }
    printf "1\n" | ask_mail_config' 2>&1)"
grep -qi 'No mail server was found' <<<"$out"
check "a machine that does have one is not warned" $(( $? == 0 ? 1 : 0 )) "$out"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All message-catalogue safety checks passed: no backticks and no \$( ) in"
echo "any message, no unescaped backtick in any string under scripts/, and"
echo "sourcing the catalogue runs no command at all — verified with executable"
echo "canaries named sudo, smartrag, docker and systemctl on PATH — and no"
echo "cfg/admin/next/repair message is left in the catalogue without a caller,"
echo "nor any key assigned twice in one catalogue, where bash keeps the last"
echo "one silently and the wrong language reaches the screen."
