#!/usr/bin/env bash
# Typing `exit` at a prompt must end the installer — from every prompt.
#
# It did not, and the way it failed is worth recording. prompt, select_one,
# select_one_index and prompt_slug are all called as "$(prompt ...)", and
# inside a command substitution `die` exits only the subshell. The wizard saw
# a non-zero return, read it as "go back", and re-asked the very question it
# had just been told to abandon: the exit message appeared and nothing
# happened. confirm was the one exception — it is not called in a
# substitution — which is exactly why this survived unnoticed.
#
# So each prompt type is exercised through a real command substitution here.
# Asserting that `wizard_quit` is called somewhere would not have caught it:
# the old code called `die`, which looks just as final.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

# Compare against the catalogue rather than a remembered phrase — the first
# version of this test grepped for wording that did not exist.
export LANG_CHOICE=en
# shellcheck source=/dev/null
source "$REPO/scripts/lib/messages.sh"
EXIT_MSG="${MSG_EN[wizard_exit]}"
[[ -n "$EXIT_MSG" ]]
check "the exit message exists in the catalogue" $? ""

# Runs one prompt inside a harness that prints a sentinel afterwards. If the
# sentinel appears, the script did NOT exit.
run_prompt() {   # $1 = shell snippet, $2 = stdin
    local script; script="$(mktemp)"
    cat > "$script" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export LANG_CHOICE=en
source "$REPO/scripts/lib/common.sh"
source "$REPO/scripts/lib/messages.sh"
# prompt_slug lives in the wizard lib, not common.sh.
source "$REPO/scripts/lib/config-wizard.sh"
$1
echo "SENTINEL_REACHED"
EOF
    printf '%b' "$2" | bash "$script" 2>&1
    rm -f "$script"
}

# ─── exit ends the installer, from every prompt type ────────────────────────
declare -A PROMPTS=(
    [prompt]='v="$(prompt cfg_course_name "d")" || true'
    [prompt_slug]='v="$(prompt_slug cfg_course_id "my-course")" || true'
    [select_one]='v="$(select_one cfg_mode_choice "A" "B")" || true'
    [select_one_index]='v="$(select_one_index cfg_mode_choice "A" "B")" || true'
    [confirm]='confirm cfg_enable_observability "y" || true'
)
for name in "${!PROMPTS[@]}"; do
    for word in exit quit; do
        out="$(run_prompt "${PROMPTS[$name]}" "$word\n")"
        grep -q "SENTINEL_REACHED" <<<"$out"
        check "'$word' at $name ends the script" $(( $? == 0 ? 1 : 0 )) \
              "execution continued past the prompt"
        grep -qF "$EXIT_MSG" <<<"$out"
        check "'$word' at $name says why it stopped" $? "$(tail -2 <<<"$out")"
    done
done

# ─── and everything else still behaves ──────────────────────────────────────
out="$(run_prompt 'v="$(prompt cfg_course_name "d")" || true; echo "GOT:$v"' "Mein Kurs\n")"
grep -q "GOT:Mein Kurs" <<<"$out"
check "a normal answer is returned unchanged" $? "$out"
grep -q "SENTINEL_REACHED" <<<"$out"
check "a normal answer does not end the script" $? "$out"

# `back` must stay a non-zero return the wizard reads as "previous section",
# NOT an exit — collapsing the two would make going back quit.
out="$(run_prompt 'if v="$(prompt cfg_course_name "d")"; then echo "FORWARD"; else echo "BACK:$?"; fi' "back\n")"
grep -q "BACK:1" <<<"$out"
check "'back' returns 1 rather than exiting" $? "$out"
grep -q "SENTINEL_REACHED" <<<"$out"
check "'back' does not end the script" $? "$out"

out="$(run_prompt 'confirm cfg_enable_observability "y" && echo "YES" || echo "NO/BACK"' "n\n")"
grep -q "NO/BACK" <<<"$out"
check "confirm still distinguishes a plain no" $? "$out"

# An invalid answer must re-ask, not fall through with an empty value.
out="$(run_prompt 'v="$(select_one_index cfg_mode_choice "A" "B")" || true; echo "GOT:$v"' "nonsense\n2\n")"
grep -q "GOT:2" <<<"$out"
check "an invalid choice re-asks rather than accepting it" $? "$out"

# ─── the mechanism ──────────────────────────────────────────────────────────
grep -q 'trap .* TERM' "$REPO/scripts/lib/common.sh"
check "a TERM trap turns the signal into a clean exit" $? ""
# Without the trap, bash prints "Terminated" — noise in the operator's face
# at the exact moment they asked to stop.
out="$(run_prompt 'v="$(prompt cfg_course_name "d")" || true' "exit\n")"
grep -qi "terminated" <<<"$out"
check "no raw 'Terminated' reaches the operator" $(( $? == 0 ? 1 : 0 )) "$out"

# Every prompt that reads input must route exit through wizard_quit; `die`
# there is the bug this file exists for.
while IFS= read -r line; do
    [[ "$line" == *"_is_exit_input"*"die "* ]] || continue
    FAILURES+=("a prompt still exits via die, which cannot leave a subshell: $line")
done < "$REPO/scripts/lib/common.sh"
check "no prompt exits via die" 0 ""

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All wizard-quit checks passed: typing exit or quit ends the installer"
echo "from every prompt type — including the four that run inside a command"
echo "substitution, where the previous implementation exited only the subshell"
echo "and left the wizard re-asking the question it had been told to abandon —"
echo "with a stated reason and without a raw 'Terminated'; while a normal"
echo "answer is returned unchanged, an invalid one re-asks, and 'back' still"
echo "returns 1 for the step loop rather than quitting."
