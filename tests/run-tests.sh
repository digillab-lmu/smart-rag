#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — test suite
# ═════════════════════════════════════════════════════════════════════════════
#
#   bash tests/run-tests.sh              # everything
#   bash tests/run-tests.sh citation     # only suites whose name matches
#   bash tests/run-tests.sh --list       # show what exists without running it
#
# Runs on a laptop with nothing installed but Python 3 and bash: the Python
# suites get a throwaway virtualenv under tests/.venv (created once, reused
# afterwards), and nothing here needs Docker, a server, or network access to
# any of the services. Every suite is self-contained — it stubs whatever it
# talks to and asserts on what the real code does with the answer.
#
# Why plain scripts rather than pytest: each suite prints, in one sentence,
# what it actually verified. That sentence is the point — it survives being
# read by someone who wasn't here when the bug was found, which a green dot
# does not. Exit code 0 = everything passed; anything else = read the output.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
VENV="$TESTS_DIR/.venv"

if [[ -t 1 ]]; then
    RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BOLD=''; DIM=''; RESET=''
fi

FILTER=""
LIST_ONLY=0
case "${1:-}" in
    --list) LIST_ONLY=1 ;;
    --help|-h) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    "") : ;;
    *) FILTER="$1" ;;
esac

# -maxdepth is a global option and has to precede the tests, otherwise find
# warns that it applies to everything anyway — which it does, so the second
# copy was both noise and misleading about how find works.
mapfile -t SUITES < <(find "$TESTS_DIR" -maxdepth 1 \
                        \( -name 'test_*.py' -o -name 'test_*.sh' \) | sort)

if (( LIST_ONLY )); then
    printf '%s\n' "${SUITES[@]#$TESTS_DIR/}"
    exit 0
fi

# ─── Python environment ──────────────────────────────────────────────────────
# Only built if there is a Python suite to run. The dependencies are the
# content-admin ones — the suites import its modules directly rather than
# talking to a running container.
have_python_suite=0
for s in "${SUITES[@]}"; do
    [[ "$s" == *.py ]] && [[ -z "$FILTER" || "$s" == *"$FILTER"* ]] && have_python_suite=1
done

PYTHON="python3"
if (( have_python_suite )); then
    REQ="$REPO_ROOT/content-admin/requirements.txt"
    STAMP="$VENV/.requirements-stamp"
    if [[ ! -x "$VENV/bin/python" ]]; then
        echo "${DIM}Creating $VENV …${RESET}"
        python3 -m venv "$VENV" || { echo "${RED}Could not create a virtualenv.${RESET}"; exit 1; }
        "$VENV/bin/pip" install --quiet --upgrade pip
    fi
    # Reinstall when requirements change. The virtualenv used to be built
    # once and never revisited, so a dependency added to requirements.txt
    # never reached it — the suite that needed it failed on an import, which
    # reads as a broken test rather than a stale environment.
    if [[ ! -f "$STAMP" || "$REQ" -nt "$STAMP" ]]; then
        echo "${DIM}Installing test dependencies …${RESET}"
        "$VENV/bin/pip" install --quiet -r "$REQ" \
            || { echo "${RED}Could not install test dependencies.${RESET}"; exit 1; }
        touch "$STAMP"
    fi
    PYTHON="$VENV/bin/python"
fi

# ─── A database for the suites that need one ─────────────────────────────────
# Agent slots and courses live in Postgres, so the suites covering them need
# one. Rather than making every developer export a DSN by hand, a local
# Postgres is looked for and used — and when there is none, those suites say
# "could not run" instead of passing.
#
# 127.0.0.1 and not a socket: psycopg's DSN takes a host, and the socket path
# differs between Homebrew, Debian and the container.
if [[ -z "${SMARTRAG_TEST_DSN:-}" ]]; then
    for pg_bin in /opt/homebrew/opt/postgresql@17/bin /usr/local/opt/postgresql@17/bin ""; do
        [[ -n "$pg_bin" && ! -x "$pg_bin/pg_isready" ]] && continue
        PATH_WITH_PG="${pg_bin:+$pg_bin:}$PATH"
        if PATH="$PATH_WITH_PG" command -v pg_isready >/dev/null 2>&1 \
           && PATH="$PATH_WITH_PG" pg_isready -q -h 127.0.0.1 -p 5432 2>/dev/null; then
            export SMARTRAG_TEST_DSN="postgresql://$(id -un)@127.0.0.1:5432/smartrag_test"
            echo "${DIM}Using the local Postgres for the database-backed suites.${RESET}"
            break
        fi
    done
fi

# ─── Run ─────────────────────────────────────────────────────────────────────
passed=0; failed=0; skipped=0; unrunnable=0
FAILED_NAMES=()
started=$SECONDS

for suite in "${SUITES[@]}"; do
    name="${suite##*/}"
    if [[ -n "$FILTER" && "$name" != *"$FILTER"* ]]; then
        skipped=$((skipped + 1)); continue
    fi

    printf '  %-32s ' "$name"
    if [[ "$suite" == *.py ]]; then
        # PYTHONDONTWRITEBYTECODE: a stale .pyc can make a suite pass against
        # code that no longer says what it did. Python invalidates the cache
        # on the source's mtime in WHOLE SECONDS, so a file rewritten within
        # the same second as the cache — an edit-and-rerun loop, or a restore
        # during a red-test check — keeps executing the old bytecode. That
        # happened here: a counter-proof appeared to pass because the module
        # under test was never recompiled.
        output="$(PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$suite" 2>&1)"; rc=$?
    else
        output="$(bash "$suite" 2>&1)"; rc=$?
    fi

    # Exit code 10 means the suite could not run — a precondition it does not
    # control is missing, typically a database. Reported as its own outcome:
    # counting it as a pass would let a whole area go untested behind a green
    # summary, and counting it as a failure would make a normal laptop look
    # broken.
    if (( rc == 10 )); then
        printf '%sskipped%s\n' "$YELLOW" "$RESET"
        printf '      %s\n' "$(head -3 <<<"$output" | sed 's/^/  /')"
        unrunnable=$((unrunnable + 1))
        continue
    fi

    if (( rc == 0 )); then
        passed=$((passed + 1))
        printf '%sok%s\n' "$GREEN" "$RESET"
        # The closing sentence of a passing suite says what it verified.
        # Worth showing: it is the readable half of the safety net.
        printf '%s' "$DIM"
        grep -v '^\s*$' <<<"$output" | tail -n +1 | grep -iE '^All .* (passed|round-tripped)' \
            | fold -s -w 76 | sed 's/^/      /'
        printf '%s' "$RESET"
    else
        failed=$((failed + 1)); FAILED_NAMES+=("$name")
        printf '%sFAILED%s\n' "$RED" "$RESET"
        sed 's/^/      /' <<<"$output" | tail -25
    fi
done

# ─── Summary ─────────────────────────────────────────────────────────────────
echo
elapsed=$((SECONDS - started))
if (( failed == 0 )); then
    printf '%s%s%d suite(s) passed%s' "$BOLD" "$GREEN" "$passed" "$RESET"
else
    printf '%s%s%d passed, %d FAILED%s' "$BOLD" "$RED" "$passed" "$failed" "$RESET"
fi
(( skipped > 0 )) && printf ' %s(%d skipped by filter)%s' "$YELLOW" "$skipped" "$RESET"
(( unrunnable > 0 )) && printf ' %s(%d could not run)%s' "$YELLOW" "$unrunnable" "$RESET"
printf ' %sin %ds%s\n' "$DIM" "$elapsed" "$RESET"

if (( failed > 0 )); then
    printf '  %s\n' "${FAILED_NAMES[@]}"
    exit 1
fi
