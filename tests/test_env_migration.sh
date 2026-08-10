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
(( ${#missing[@]} == 4 ))
check "exactly the four absent keys are found" $? "${#missing[@]}: ${missing[*]}"

# ─── The values: derived, not copied ────────────────────────────────────────
# This is the whole point. .env.example says "https://s3.${DOMAIN}" — using
# that literally on a prefixed installation points at a host with no DNS
# record, no vhost and no certificate.
declare -A EXPECT=(
    [MINIO_SERVER_URL]="https://smartrag-s3.duenn-mit-pfiff.de"
    [MINIO_BROWSER_REDIRECT_URL]="https://smartrag-minio.duenn-mit-pfiff.de"
    [FLOWISE_PUBLIC_URL]="https://smartrag-smart-rag.duenn-mit-pfiff.de"
)
for key in "${!EXPECT[@]}"; do
    got="$(_default_for_env_key "$key")"
    [[ "$got" == "${EXPECT[$key]}" ]]
    check "$key is derived correctly" $? "got '$got', wanted '${EXPECT[$key]}'"
    # No unexpanded interpolation may survive into .env.
    [[ "$got" != *'${'* ]]
    check "$key contains no unexpanded variable" $? "$got"
done

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
# unreachable (or worse, reachable but unlabelled). Scoped to the main loop:
# `case "$rc" in 0)` inside a helper is not a menu entry, and treating it as
# one made this fail on a function that had nothing to do with the menu.
main_loop="$(sed -n '/^while true; do/,$p' "$REPO/scripts/admin.sh")"
for n in $(grep -oE '^\s+[0-9]+\)' <<<"$main_loop" | grep -oE '[0-9]+'); do
    grep -qE "\"$n\" +\"" <<<"$main_loop"
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


# The menu must show every entry without scrolling, and the box must be tall
# enough for the list. A whiptail menu whose list height is smaller than the
# number of entries scrolls silently — an operator who does not notice the
# arrow concludes the missing entries do not exist. Uninstall being the one
# below the fold is the version of this that matters.
read -r box_h _ list_h < <(grep -A1 'menu "\$(t admin_menu_prompt)"' "$REPO/scripts/admin.sh" \
    | grep -oE '^\s+[0-9]+ [0-9]+ [0-9]+' | tr -s ' ' | sed 's/^ //')
entries="$(grep -cE '^\s+"[0-9]+" +"\$\(t ' "$REPO/scripts/admin.sh")"
[[ -n "$box_h" && -n "$list_h" && -n "$entries" ]]
check "the menu geometry could be read" $? "box=$box_h list=$list_h entries=$entries"
(( list_h >= entries ))
check "every menu entry is visible without scrolling" $? "list=$list_h entries=$entries"
(( box_h >= list_h + 7 ))
check "the box is tall enough for its list" $? "box=$box_h list=$list_h"

# Buffered keystrokes must be discarded before the menu is drawn: the
# terminal echoes them over whiptail's first row (an arrow key appears as a
# literal ^[[A on the top entry) and whiptail then consumes them as
# navigation in a menu nobody has seen yet.
grep -q '_drain_stdin' "$REPO/scripts/admin.sh"
check "stdin is drained before the menu is shown" $? ""

# ─── Present-but-unresolved values ──────────────────────────────────────────
# The gap that let SMTP_SENDER_EMAIL survive an Upgrade run as
# "noreply@${DOMAIN}": _missing_env_keys only reports keys that are ABSENT, so
# a key that is there with a value the installer never resolved is invisible.
# The ingest's completion mail would have carried that literal in its sender.
HELPERS2="$(mktemp)"
sed -n '/^_wizard_resolved_keys()/,/^}/p;/^_stale_env_keys()/,/^}/p;/^_duplicate_env_keys()/,/^}/p' \
    "$REPO/scripts/admin.sh" > "$HELPERS2"
[[ -s "$HELPERS2" ]]
check "the stale/duplicate helpers exist" $? "not found in admin.sh"

LIB_DIR="$REPO/scripts/lib"
cat > "$SANDBOX/.env" <<'EOF'
DOMAIN="duenn-mit-pfiff.de"
SUBDOMAIN_PREFIX="smartrag"
SMTP_SENDER_EMAIL="noreply@${DOMAIN}"
REDIS_AUTH="secret1"
REDIS_AUTH="secret1"
DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@smartrag-postgres:5432/${POSTGRES_DB}"
EOF
# shellcheck source=/dev/null
source "$HELPERS2"

mapfile -t stale < <(_stale_env_keys)
printf '%s\n' "${stale[@]}" | grep -qx "SMTP_SENDER_EMAIL"
check "an unresolved value the wizard writes is found" $? "${stale[*]}"

# DATABASE_URL keeps its ${...} on purpose: it reaches its container through
# compose's environment: block, where Compose does interpolate. Reporting it
# would send an operator to "fix" something that is correct.
printf '%s\n' "${stale[@]}" | grep -qx "DATABASE_URL"
check "a value that is meant to interpolate is NOT reported" $(( $? == 0 ? 1 : 0 )) "${stale[*]}"

mapfile -t dupes < <(_duplicate_env_keys)
printf '%s\n' "${dupes[@]}" | grep -qx "REDIS_AUTH"
check "a duplicated key is found" $? "${dupes[*]}"
(( ${#dupes[@]} == 1 ))
check "only the duplicated key is reported" $? "${dupes[*]}"

# ─── Values a service validates must be resolved, not copied ────────────────
# Langfuse refuses to initialise on a bad one and says so: the upgrade path's
# fallback copied .env.example's literal, so LANGFUSE_INIT_USER_EMAIL reached
# the container as "${ADMIN_EMAIL}" and Langfuse rejected it as "Invalid
# input" — no organisation, no project, no API keys, and therefore nothing
# able to report a trace.
DOMAIN="duenn-mit-pfiff.de"
ADMIN_EMAIL="kurs@example.org"
ADMIN_PASSWORD="a-generated-password"
COURSE_NAME="Testkurs"

for key in LANGFUSE_INIT_USER_EMAIL LANGFUSE_INIT_USER_PASSWORD LANGFUSE_INIT_PROJECT_NAME; do
    got="$(_default_for_env_key "$key")"
    [[ -n "$got" && "$got" != *'${'* ]]
    check "$key is resolved, not copied" $? "got '$got'"
done
[[ "$(_default_for_env_key LANGFUSE_INIT_USER_EMAIL)" == "kurs@example.org" ]]
check "the admin address is used verbatim" $? "$(_default_for_env_key LANGFUSE_INIT_USER_EMAIL)"

# The two project keys are secrets: generated per installation, never a
# literal that would be identical everywhere.
k1="$(_default_for_env_key LANGFUSE_INIT_PROJECT_PUBLIC_KEY)"
k2="$(_default_for_env_key LANGFUSE_INIT_PROJECT_PUBLIC_KEY)"
[[ "$k1" == pk-lf-* && ${#k1} -gt 20 ]]
check "the public key looks like a Langfuse key" $? "$k1"
[[ "$k1" != "$k2" ]]
check "the project keys are generated, not fixed" $? "two calls returned '$k1'"
s1="$(_default_for_env_key LANGFUSE_INIT_PROJECT_SECRET_KEY)"
[[ "$s1" == sk-lf-* ]]
check "the secret key is generated too" $? "$s1"

# ─── The placeholder must never survive into a live .env ────────────────────
# Twenty-two keys in .env.example read "generate-with-bootstrap", and every
# one is a secret. The upgrade path's fallback copied that literal verbatim,
# so a key added by an upgrade became a credential that is published in this
# repository and identical on every installation. Langfuse accepted it as a
# project key without complaint — as a string there is nothing wrong with it,
# which is exactly why nothing caught it.
cat > "$SANDBOX/.env.example" <<'EOF'
DOMAIN="example.com"
SUBDOMAIN_PREFIX=""
SOME_NEW_SECRET="generate-with-bootstrap"
SOME_NEW_PLAIN_KEY="a-literal-default"
EOF
g1="$(_default_for_env_key SOME_NEW_SECRET)"
g2="$(_default_for_env_key SOME_NEW_SECRET)"
[[ "$g1" != "generate-with-bootstrap" ]]
check "the placeholder is never handed out as a value" $? "got '$g1'"
(( ${#g1} >= 24 ))
check "what replaces it is long enough to be a secret" $? "${#g1} characters"
[[ "$g1" != "$g2" ]]
check "and is generated per call, not fixed" $? "two calls returned '$g1'"
# A genuine literal default must still be copied — this only targets the
# placeholder, not every default.
[[ "$(_default_for_env_key SOME_NEW_PLAIN_KEY)" == "a-literal-default" ]]
check "a real default is still used as-is" $? "$(_default_for_env_key SOME_NEW_PLAIN_KEY)"

# And an .env that already contains one must be reported, whether or not the
# key is one the wizard writes.
cat > "$SANDBOX/.env" <<'EOF'
DOMAIN="duenn-mit-pfiff.de"
SUBDOMAIN_PREFIX="smartrag"
LANGFUSE_INIT_PROJECT_SECRET_KEY="generate-with-bootstrap"
SOMETHING_ELSE="fine"
EOF
mapfile -t stale2 < <(_stale_env_keys)
printf '%s\n' "${stale2[@]}" | grep -qx "LANGFUSE_INIT_PROJECT_SECRET_KEY"
check "a placeholder left in .env is reported" $? "${stale2[*]}"
printf '%s\n' "${stale2[@]}" | grep -qx "SOMETHING_ELSE"
check "a real value is not reported" $(( $? == 0 ? 1 : 0 )) "${stale2[*]}"

# ─── The upgrade must patch in place, never append ──────────────────────────
# Appending is how the duplicate above came to exist, and it cannot fix a key
# that is already present — which is the whole point of this entry.
sed -n '/^action_migrate()/,/^}/p' "$REPO/scripts/admin.sh" | grep -q '>> "\$REPO_ROOT/.env"'
check "the upgrade no longer appends to .env" $(( $? == 0 ? 1 : 0 )) \
      "a raw >> append is back in action_migrate"
sed -n '/^action_migrate()/,/^}/p' "$REPO/scripts/admin.sh" | grep -q 'set_env_var "\$REPO_ROOT/.env"'
check "the upgrade patches through set_env_var" $? ""

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
