#!/usr/bin/env bash
# The way back in when the browser cannot help.
#
# The menu entry called "forgotten password" cleared CONTENT_ADMIN_USERNAME
# and CONTENT_ADMIN_PASSWORD_HASH in .env, so that is_configured() turned
# false and the Content Admin offered its first-run page again. That stopped
# working when accounts moved into Postgres: is_configured() asks
# accounts.any_account_exists(), and auth.py blanks those two variables itself
# the first time it migrates them. From then on the entry read an empty
# variable, reported "no account configured" and returned — doing nothing, on
# precisely the installations that have an account to recover.
#
# Found by an operator locked out of a restored installation, whose accounts
# had come from the archive and whose own credentials.txt no longer applied.
#
# What the checks hold: the entry reaches the accounts table, sets the
# password through the application rather than writing a hash of its own, and
# never puts the password where another process can read it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

fn="$(awk '/^action_reset_content_admin\(\) \{/,/^\}/' "$REPO/scripts/admin.sh")"

[[ -n "$fn" ]]
check "the menu entry exists" $? "action_reset_content_admin was not found"

grep -q "accounts.all_accounts()" <<<"$fn"
check "it reads the accounts from the database" $? \
      "reading .env is what made this entry do nothing"

grep -q "accounts.set_password(" <<<"$fn"
check "and sets the password through the application" $? \
      "the stored value is a werkzeug hash; anything else fails at the next login"

# The old behaviour must not be the whole of the new one.
if grep -q "accounts.set_password(" <<<"$fn"; then
    check "clearing .env is no longer the mechanism" 0
else
    check "clearing .env is no longer the mechanism" 1 \
          "is_configured() no longer looks at those variables"
fi

grep -q "read -rsp" <<<"$fn"
check "the password is not echoed" $? "it would stay in the scrollback"

[[ "$(grep -c 'read -rsp' <<<"$fn")" -ge 2 ]]
check "and is asked for twice" $? \
      "a typo here locks the account this entry exists to unlock"

grep -q 'printf .%s. "\$pw1" | docker exec -i' <<<"$fn"
check "the password goes in on stdin, not as an argument" $? \
      "an argument is visible in ps to everyone on the machine"

# An installation from before the migration still has the variables filled,
# and leaving them beside the account just changed is a second, stale answer
# to "who is the administrator".
grep -q "CONTENT_ADMIN_USERNAME" <<<"$fn"
check "a pre-migration .env is still tidied up" $? \
      "two definitions of the account would remain"

# Nothing here should need the operator to know an id.
grep -qE "admin_reset_pick(_invalid)?" <<<"$fn"
check "the account is chosen from a list" $? \
      "an id typed from memory is the wrong account half the time"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All password-reset checks passed: the menu entry reads the accounts from"
echo "the database and sets the password through the application, so the value"
echo "stored is one the login can verify; the account is picked from a list"
echo "rather than typed; the password is asked for twice, never echoed and"
echo "passed on stdin rather than as an argument; and an .env left over from"
echo "before accounts moved into Postgres is cleared instead of standing"
echo "beside the account that was just changed."
