#!/usr/bin/env bash
# A menu option long enough to say what it requires must still read as one
# option.
#
# The mail section asks one question with four answers, and each answer now
# states what the operator needs to have ready before choosing it — because
# "install Postfix" reads like the self-contained option and is not: it needs
# an account with a mail provider exactly as the direct option does, the only
# difference being who holds the password. Saying so takes a sentence, and a
# sentence is wider than a terminal.
#
# Left to the terminal, the overflow restarts at column 0, directly under the
# "[2]" of the next option — four options in a row and it stops being visible
# where one ends and the next begins. So select_one_index wraps them itself,
# with the continuation indented to the text column. This file holds that
# behaviour, and holds the requirement statements in place.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

export LANG_CHOICE=en
# shellcheck source=/dev/null
source "$REPO/scripts/lib/messages.sh"
# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"

# ─── wrap_lines ──────────────────────────────────────────────────────────────

out="$(wrap_lines "one two three four five six" 20)"
[[ "$out" == $'one two three four\nfive six' ]]
check "wrap_lines fills to the width and breaks at spaces" $? "$out"

# Below 20 columns the width is clamped rather than going negative, so a
# narrow terminal still gets whole words instead of an endless loop.
[[ "$(wrap_lines "one two three four five six" 4)" == "$out" ]]
check "an absurdly narrow width is clamped, not obeyed" $? ""

long="$(wrap_lines "supercalifragilisticexpialidocious and" 20)"
# A word wider than the column cannot be broken without inventing a hyphen —
# it goes out whole rather than being cut.
[[ "$long" == $'supercalifragilisticexpialidocious\nand' ]]
check "a word longer than the width is not truncated" $? "$long"

[[ "$(wrap_lines "short" 40)" == "short" ]]
check "text that fits comes back unchanged" $? ""

[[ -z "$(wrap_lines "" 40)" ]]
check "empty text produces no line" $? ""

# The first implementation split on an unquoted expansion, which globs: a
# message containing * would have been replaced by the working directory.
star="$(cd "$REPO" && wrap_lines "pick * now" 40)"
[[ "$star" == "pick * now" ]]
check "a * in a message stays a *" $? "$star"

# ─── select_one_index ────────────────────────────────────────────────────────

render() {   # $1 = COLUMNS; rest = options. Answers "1" so it returns.
    COLUMNS="$1" bash -c '
        set -uo pipefail
        export LANG_CHOICE=en
        source "'"$REPO"'/scripts/lib/messages.sh"
        source "'"$REPO"'/scripts/lib/common.sh"
        echo 1 | select_one_index cfg_mail_how "$@" >/dev/null
    ' _ "${@:2}" 2>&1 | sed 's/\x1b\[[0-9;]*m//g'
}

opt_a="Alpha $(printf 'word %.0s' {1..30})end"
opt_b="Bravo is short"
out="$(render 80 "$opt_a" "$opt_b")"

# Nothing overflows the terminal.
overlong="$(awk 'length > 80' <<<"$out")"
[[ -z "$overlong" ]]
check "no rendered line exceeds the terminal width" $? "$overlong"

# Continuations sit under the text, not under the marker.
mapfile -t cont < <(grep -n '^ \{9\}[^ ]' <<<"$out")
(( ${#cont[@]} >= 2 ))
check "a long option is continued at the text column" $? "$out"

# And every option still starts exactly one line with its own marker, which
# is what the wrapping is for.
for marker in '[1]' '[2]'; do
    n="$(grep -c -- "^    \\$marker" <<<"$out")"
    [[ "$n" == "1" ]]
    check "option $marker begins exactly one line" $? "found $n in: $out"
done

# The short option is not touched.
grep -q '^    \[2\]  Bravo is short$' <<<"$out"
check "a short option is printed on one line as before" $? "$out"

# A narrow terminal must still produce something, not divide by a negative.
narrow="$(render 30 "$opt_a" "$opt_b")"
[[ -n "$narrow" ]] && ! grep -q 'error\|command not found' <<<"$narrow"
check "a 30-column terminal renders without error" $? "$narrow"

# ─── The mail options say what they require ──────────────────────────────────
# The point of the exercise: an operator choosing "install Postfix" learns
# before choosing that it needs a provider account, not afterwards when the
# wizard asks for a password they do not have.

for lang in EN DE; do
    declare -n CAT="MSG_$lang"
    for key in cfg_mail_how_existing cfg_mail_how_postfix cfg_mail_how_direct cfg_mail_how_none; do
        text="${CAT[$key]}"
        [[ "$text" == *"Needs:"* || "$text" == *"Braucht:"* ]]
        check "$lang $key states what it requires" $? "$text"
    done

    # Specifically: Postfix and direct both need the same account. The whole
    # reason the user asked for this was that only the direct option looked
    # like it needed one.
    for key in cfg_mail_how_postfix cfg_mail_how_direct; do
        text="${CAT[$key]}"
        wants=(port password); [[ "$lang" == DE ]] && wants=(Port Passwort)
        for want in "${wants[@]}"; do
            grep -qi -- "$want" <<<"$text"
            check "$lang $key names the $want the operator must have" $? "$text"
        done
    done
    unset -n CAT
done

# "Relay" is a term for a machine that forwards mail. What the operator is
# choosing here is whether this system can send mail at all, so the dialogue
# says mail service. The variable names (SMTP_RELAY_*) and Postfix's own
# mechanics keep the accurate term; these operator-facing keys must not.
for key in cfg_section_mail cfg_mail_intro cfg_mail_how cfg_mail_how_existing \
           cfg_mail_how_postfix cfg_mail_how_direct cfg_mail_how_none \
           admin_menu_mail admin_mail_title admin_cfg_mail phase_postfix; do
    for lang in EN DE; do
        declare -n CAT="MSG_$lang"
        text="${CAT[$key]:-}"
        [[ -n "$text" ]]
        check "$lang $key exists" $? ""
        grep -qiE 'relay|relais' <<<"$text"
        check "$lang $key avoids the word relay" $(( $? == 0 ? 1 : 0 )) "$text"
        unset -n CAT
    done
done

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All menu-wrapping checks passed: wrap_lines fills to the given width,"
echo "breaks only at spaces, leaves an over-long word whole and does not glob;"
echo "select_one_index wraps each option under its own text column so that a"
echo "four-line answer cannot be mistaken for the next one, keeps short options"
echo "on one line and survives a 30-column terminal; and each of the four mail"
echo "answers states what the operator must have ready — including that Postfix"
echo "needs the same provider account, port and password as the direct route,"
echo "in operator-facing wording that no longer says 'relay'."
