#!/usr/bin/env bash
# The admin TUI's upgrade entry.
#
# Twice now, an upgraded deployment was missing .env keys a newer version
# expected, and both times it was discovered by the operator running into the
# resulting failure — once as a MinIO console that would not log in, once as
# MinIO dialling a port nothing listened on. Neither .env nor a live Weaviate
# class is ever rewritten automatically, on purpose; this closes the gap that
# leaves.
#
# The important property: keys whose value embeds a subdomain must be DERIVED
# through subdomain_host(), not copied from .env.example. Copying is what
# introduced the bug — the literal there has no SUBDOMAIN_PREFIX in it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"

# The two helpers are extracted and sourced rather than reimplemented, so
# this tests the real thing. admin.sh itself can't be sourced: it demands
# root, whiptail and a live .env at load time.
HELPERS="$(mktemp)"
sed -n '/^_missing_env_keys()/,/^}/p;/^_default_for_env_key()/,/^}/p' \
    "$REPO/scripts/admin.sh" > "$HELPERS"
[[ -s "$HELPERS" ]]
check "the helpers could be extracted from admin.sh" $? "nothing matched"

SANDBOX="$(mktemp -d)"
REPO_ROOT="$SANDBOX"
cat > "$SANDBOX/.env.example" <<'EOF'
DOMAIN="example.com"
SUBDOMAIN_PREFIX=""
EXISTING_KEY="kept"
MINIO_SERVER_URL="https://s3.${DOMAIN}"
MINIO_BROWSER_REDIRECT_URL="https://minio.${DOMAIN}"
FLOWISE_PUBLIC_URL="https://smart-rag.${DOMAIN}"
MINIO_NOTIFY_WEBHOOK_ENDPOINT="http://smartrag-n8n:5678/webhook/minio-notify"
SOME_NEW_PLAIN_KEY="a-literal-default"
EOF
cat > "$SANDBOX/.env" <<'EOF'
DOMAIN="duenn-mit-pfiff.de"
SUBDOMAIN_PREFIX="smartrag"
EXISTING_KEY="kept"
EOF

DOMAIN="duenn-mit-pfiff.de"
SUBDOMAIN_PREFIX="smartrag"
# shellcheck source=/dev/null
source "$HELPERS"

# ─── Which keys are missing ─────────────────────────────────────────────────
mapfile -t missing < <(_missing_env_keys)
printf '%s\n' "${missing[@]}" | grep -qx "MINIO_SERVER_URL"
check "a genuinely missing key is found" $? "${missing[*]}"
printf '%s\n' "${missing[@]}" | grep -qx "EXISTING_KEY"
check "a key already present is NOT reported" $(( $? == 0 ? 1 : 0 )) "${missing[*]}"
printf '%s\n' "${missing[@]}" | grep -qx "DOMAIN"
check "DOMAIN is not reported as missing" $(( $? == 0 ? 1 : 0 )) "${missing[*]}"
(( ${#missing[@]} == 5 ))
check "exactly the five absent keys are found" $? "${#missing[@]}: ${missing[*]}"

# ─── The values: derived, not copied ────────────────────────────────────────
# This is the whole point. .env.example says "https://s3.${DOMAIN}" — using
# that literally on a prefixed installation points at a host with no DNS
# record, no vhost and no certificate.
declare -A EXPECT=(
    [MINIO_SERVER_URL]="https://smartrag-s3.duenn-mit-pfiff.de"
    [MINIO_BROWSER_REDIRECT_URL]="https://smartrag-minio.duenn-mit-pfiff.de"
    [FLOWISE_PUBLIC_URL]="https://smartrag-smart-rag.duenn-mit-pfiff.de"
    [MINIO_NOTIFY_WEBHOOK_ENDPOINT]="http://smartrag-n8n:5678/webhook/minio-notify"
)
for key in "${!EXPECT[@]}"; do
    got="$(_default_for_env_key "$key")"
    [[ "$got" == "${EXPECT[$key]}" ]]
    check "$key is derived correctly" $? "got '$got', wanted '${EXPECT[$key]}'"
    # No unexpanded interpolation may survive into .env.
    [[ "$got" != *'${'* ]]
    check "$key contains no unexpanded variable" $? "$got"
done

# The notify endpoint is a container-to-container URL: it must use n8n's
# container port, never the host-side N8N_PORT the wizard may have moved.
[[ "$(_default_for_env_key MINIO_NOTIFY_WEBHOOK_ENDPOINT)" == *":5678/"* ]]
check "the notify endpoint uses n8n's container port" $? ""

# Without a prefix, the same keys come out unprefixed.
SUBDOMAIN_PREFIX=""
[[ "$(_default_for_env_key MINIO_SERVER_URL)" == "https://s3.duenn-mit-pfiff.de" ]]
check "no prefix yields the plain hostname" $? "$(_default_for_env_key MINIO_SERVER_URL)"
SUBDOMAIN_PREFIX="smartrag"

# An unknown new key falls back to .env.example's literal — acceptable, but
# it must at least produce something rather than an empty value.
got="$(_default_for_env_key SOME_NEW_PLAIN_KEY)"
[[ -n "$got" ]]
check "an unknown key still gets a value from .env.example" $? "empty"

# ─── Wiring ─────────────────────────────────────────────────────────────────
grep -q "action_migrate" "$REPO/scripts/admin.sh"
check "the action exists" $? ""
grep -q '"\$(t admin_menu_migrate)"' "$REPO/scripts/admin.sh"
check "it has a menu entry" $? ""
grep -qE '^\s+11\) action_migrate ;;' "$REPO/scripts/admin.sh"
check "the menu entry is dispatched" $? "$(grep -n 'action_migrate ;;' "$REPO/scripts/admin.sh")"
# Uninstall must stay at the end, immediately before Exit: a mis-key should
# never land on the destructive entry. Pinned as a rule rather than a fixed
# number, so adding a menu item does not need this rewritten — only moving
# uninstall does, which is exactly when someone should have to think.
uninstall_key="$(grep -oE '^\s+([0-9]+)\) action_uninstall ;;' "$REPO/scripts/admin.sh" | grep -oE '[0-9]+')"
exit_key="$(grep -oE '^\s+([0-9]+)\) clear; break ;;' "$REPO/scripts/admin.sh" | grep -oE '[0-9]+')"
[[ -n "$uninstall_key" && -n "$exit_key" && $(( exit_key - uninstall_key )) -eq 1 ]]
check "uninstall sits immediately before exit, at the end of the menu" $? \
      "uninstall=$uninstall_key exit=$exit_key"

# And every dispatched number must have a menu label, or an entry becomes
# unreachable (or worse, reachable but unlabelled).
for n in $(grep -oE '^\s+[0-9]+\)' "$REPO/scripts/admin.sh" | grep -oE '[0-9]+'); do
    grep -qE "\"$n\" +\"" "$REPO/scripts/admin.sh"
    check "menu entry $n has a label" $? "dispatched but not listed"
done

# The global command installs itself without asking. Every closing message
# and every doc says `sudo smartrag`; a prompt that can be declined leaves
# those instructions pointing at a command that does not exist.
sed -n '/Self-install as a global command/,/^fi$/p' "$REPO/scripts/admin.sh" | grep -q 'confirm '
check "the symlink is created without a prompt" $(( $? == 0 ? 1 : 0 )) \
      "a confirm() reappeared in the self-install block"
sed -n '/Self-install as a global command/,/^fi$/p' "$REPO/scripts/admin.sh" | grep -q 'ln -sf'
check "the symlink is actually created" $? ""
# It must not clobber an existing file at that path.
sed -n '/Self-install as a global command/,/^fi$/p' "$REPO/scripts/admin.sh" | grep -q '! -e /usr/local/bin/smartrag'
check "an existing /usr/local/bin/smartrag is left alone" $? ""


# It must back the file up before appending.
sed -n '/^action_migrate()/,/^}/p' "$REPO/scripts/admin.sh" | grep -q 'cp "\$REPO_ROOT/.env"'
check "it backs .env up before writing" $? ""
# And offer the course migration as a dry run first.
sed -n '/^action_migrate()/,/^}/p' "$REPO/scripts/admin.sh" | grep -q -- "--dry-run"
check "the course migration is offered as a dry run first" $? ""

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All upgrade-migration checks passed: missing .env keys are found without"
echo "reporting ones already present; every key whose value embeds a subdomain"
echo "is derived through subdomain_host() with SUBDOMAIN_PREFIX applied rather"
echo "than copied from .env.example, leaves no unexpanded interpolation, and"
echo "the MinIO notify endpoint uses n8n's container port; an unknown new key"
echo "still gets a value; and the action is wired into the menu ahead of"
echo "uninstall, backs .env up before appending, and offers the course_id"
echo "migration as a dry run first."
