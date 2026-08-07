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

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All message-catalogue safety checks passed: no backticks and no \$( ) in"
echo "any message, no unescaped backtick in any string under scripts/, and"
echo "sourcing the catalogue runs no command at all — verified with executable"
echo "canaries named sudo, smartrag, docker and systemctl on PATH."
