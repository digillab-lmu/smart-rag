#!/usr/bin/env bash
# run_n8n_import_guided() is the single path both the installer and the
# admin TUI take, so importing the ingest workflows never requires typing a
# command by hand. Tested directly, plus a static check that both callers
# really go through it rather than invoking the deploy script themselves.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"
# shellcheck source=/dev/null
source "$REPO/scripts/lib/messages.sh"
LANG_CHOICE=en
DOMAIN="example.com"
SUBDOMAIN_PREFIX=""

make_scripts() { # $1 = "0" | "10" | "1" | "10-then-0"
    SBOX="$(mktemp -d)"
    case "$1" in
        10-then-0)
            cat > "$SBOX/deploy-n8n-workflows.sh" <<'STATEFUL'
#!/usr/bin/env bash
marker="$(dirname "$0")/.called"
echo "DEPLOY-RUN"
[[ -e "$marker" ]] && exit 0
touch "$marker"; exit 10
STATEFUL
            ;;
        *)  printf '#!/usr/bin/env bash\necho DEPLOY-RUN\nexit %s\n' "$1" \
                > "$SBOX/deploy-n8n-workflows.sh" ;;
    esac
    chmod +x "$SBOX/deploy-n8n-workflows.sh"
}

guided() { # $1 = deploy behaviour, $2 = answers
    make_scripts "$1"
    OUT="$(printf '%b' "$2" | run_n8n_import_guided "$SBOX" en 2>&1)"
    RC=$?
}

# ─── Nothing to guide: the import just works ─────────────────────────────────
guided 0 ""
check "clean import returns 0" $(( RC == 0 ? 0 : 1 )) "rc=$RC"
(( $(grep -c DEPLOY-RUN <<<"$OUT") == 1 ))
check "clean import runs the deploy script once" $? "$OUT"
grep -qi "owner account" <<<"$OUT"
check "clean import asks nothing" $(( $? == 0 ? 1 : 0 )) "$OUT"

# ─── A real failure passes straight through ──────────────────────────────────
guided 1 ""
check "hard failure is returned as-is" $(( RC == 1 ? 0 : 1 )) "rc=$RC"
grep -qi "owner account" <<<"$OUT"
check "hard failure is not mistaken for a missing owner" $(( $? == 0 ? 1 : 0 )) "$OUT"

# ─── The guided case: wait, then finish it ───────────────────────────────────
guided 10-then-0 "y\n"
check "guided import ends successfully" $(( RC == 0 ? 0 : 1 )) "rc=$RC / $OUT"
(( $(grep -c DEPLOY-RUN <<<"$OUT") == 2 ))
check "the import is retried after confirmation" $? "$(grep -c DEPLOY-RUN <<<"$OUT") run(s)"
grep -q "n8n.example.com" <<<"$OUT"
check "the n8n URL is named" $? "$OUT"
# The promise that matters is that the run does not abort while the
# operator is in a browser. Matched on either phrasing rather than one
# literal, so a reworded message fails only if it stops promising it.
grep -qiE "(will wait|wizard waits|waits and)" <<<"$OUT"
check "it says it will wait" $? "$OUT"

# ─── Declining is a choice, not a failure ────────────────────────────────────
guided 10 "n\n"
check "declining returns EXIT_SKIPPED" $(( RC == EXIT_SKIPPED ? 0 : 1 )) "rc=$RC"
grep -qi "skipping for now" <<<"$OUT"
check "declining is acknowledged" $? "$OUT"

# ─── Answering yes too early ─────────────────────────────────────────────────
guided 10 "y\ny\ny\n"
check "still-missing owner keeps EXIT_SKIPPED" $(( RC == EXIT_SKIPPED ? 0 : 1 )) "rc=$RC"
grep -q "still reports no owner" <<<"$OUT"
check "premature yes is called out" $? "$OUT"
# Bounded: three attempts, not an endless loop.
(( $(grep -c DEPLOY-RUN <<<"$OUT") <= 4 ))
check "retries are bounded" $? "$(grep -c DEPLOY-RUN <<<"$OUT") run(s)"

# ─── EOF on stdin must not hang (the bug this loop already had once) ─────────
make_scripts 10
timeout 20 bash -c "
  source '$REPO/scripts/lib/common.sh'; source '$REPO/scripts/lib/messages.sh'
  LANG_CHOICE=en DOMAIN=example.com
  run_n8n_import_guided '$SBOX' en < /dev/null
" >/dev/null 2>&1
check "closed stdin terminates instead of looping" $(( $? == 124 ? 1 : 0 )) "timed out"

# ─── Both callers go through it ──────────────────────────────────────────────
grep -q "run_n8n_import_guided" "$REPO/scripts/bootstrap.sh"
check "bootstrap.sh uses the guided path" $? ""
grep -q "run_n8n_import_guided" "$REPO/scripts/admin.sh"
check "admin.sh uses the guided path" $? ""

# Neither may call the deploy script directly any more — that is what let
# the TUI skip the owner-account guidance the installer gave.
for f in bootstrap.sh admin.sh; do
    grep -E 'bash "\$SCRIPT_DIR/deploy-n8n-workflows\.sh"' "$REPO/scripts/$f" >/dev/null
    check "$f no longer calls the deploy script directly" $(( $? == 0 ? 1 : 0 )) \
          "$(grep -n 'deploy-n8n-workflows' "$REPO/scripts/$f" | head -2)"
done

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All guided-import checks passed: a clean import runs once and asks nothing,"
echo "a genuine failure passes through untouched, a missing n8n owner is walked"
echo "through and the import then finished automatically, declining returns"
echo "EXIT_SKIPPED without being treated as an error, answering yes too early is"
echo "called out with bounded retries, a closed stdin terminates instead of"
echo "looping — and both the installer and the admin TUI go through this one"
echo "path, neither invoking the deploy script directly any more."
