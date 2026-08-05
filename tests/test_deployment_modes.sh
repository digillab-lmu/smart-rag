#!/usr/bin/env bash
# The deployment-mode switch.
#
# Mode "domain" is what existed before: one subdomain per service, nginx,
# Let's Encrypt. Mode "tailscale" has no domain of ours at all — Tailscale
# terminates TLS with its own certificate and proxies to the containers, so
# nginx and certbot are not involved.
#
# What this guards: the two modes must not leak into each other. In tailscale
# mode nothing may write an nginx vhost, request a certificate, or invent a
# hostname before Tailscale has assigned one — a URL that looks authoritative
# and is wrong is worse than an empty value. In domain mode nothing may skip
# the checks that mode depends on.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

export LANG_CHOICE=en
# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"
# shellcheck source=/dev/null
source "$REPO/scripts/lib/messages.sh"
# shellcheck source=/dev/null
source "$REPO/scripts/lib/templates.sh"

# ─── The wizard asks first, and offers exactly two modes ────────────────────
grep -q "ask_deployment_mode" "$REPO/scripts/lib/config-wizard.sh"
check "the mode question exists" $? ""
grep -qE 'steps=\(ask_deployment_mode ' "$REPO/scripts/lib/config-wizard.sh"
check "it is the FIRST wizard step" $? \
      "$(grep -oE 'steps=\([a-z_ ]*\)' "$REPO/scripts/lib/config-wizard.sh")"
# Going back from the summary must land on the last section, not past it.
# The first token is "steps=(ask_deployment_mode", which does not begin with
# "ask_" — counting naively lost it and made this assertion off by one.
last_idx=$(( $(grep -oE 'steps=\(([a-z_ ]*)\)' "$REPO/scripts/lib/config-wizard.sh" \
    | sed 's/^steps=(//; s/)$//' | tr ' ' '\n' | grep -c '^ask_') - 1 ))
grep -qE "_wizard_step_loop $last_idx " "$REPO/scripts/lib/config-wizard.sh"
check "the back-from-summary index matches the step count" $? \
      "expected $last_idx; $(grep -oE '_wizard_step_loop [0-9]+ ' "$REPO/scripts/lib/config-wizard.sh" | tail -1)"

# The LTI limitation must be stated at the point of choosing, not buried.
# Joining must happen in the mode section, so the MagicDNS name is known
# before .env is written and the URLs are right on the first write.
MODE_FN="$(sed -n '/^ask_deployment_mode()/,/^}/p' "$REPO/scripts/lib/config-wizard.sh")"
grep -q "tailscale_ensure_up" <<<"$MODE_FN"
check "the wizard joins the tailnet while asking, not later" $? ""
grep -q "CFG_TAILSCALE_HOSTNAME=" <<<"$MODE_FN"
check "and records the MagicDNS name for .env" $? ""
# A failure there must return to the question, not abort the install —
# domain mode is still a valid answer.
grep -q "return 1" <<<"$MODE_FN"
check "a Tailscale failure returns to the mode question" $? ""

# And the deployment phase must NOT restart everything afterwards: that
# restart only existed because the name arrived too late.
PHASES_TMP="$(sed -n '/^run_deployment_phases()/,/^}/p' "$REPO/scripts/bootstrap.sh")"
sed -n '/== "tailscale"/,/^    else$/p' <<<"$PHASES_TMP" | grep -q "compose.sh\" up -d\|compose.sh up -d"
check "no container restart round after tailscale setup" $(( $? == 0 ? 1 : 0 )) \
      "$(sed -n '/== "tailscale"/,/^    else$/p' <<<"$PHASES_TMP")"

grep -q "cfg_mode_tailscale_lti" "$REPO/scripts/lib/config-wizard.sh"
check "the LTI limitation is stated when choosing" $? ""
grep -qi "LTI" <<<"${MSG_EN[cfg_mode_tailscale_lti]}"
check "and that message actually mentions LTI" $? "${MSG_EN[cfg_mode_tailscale_lti]:0:60}"
grep -qi "Tailscale-Konto\|tailscale.com\|login.tailscale" <<<"${MSG_DE[cfg_mode_tailscale_prereq_1]}${MSG_EN[cfg_mode_tailscale_prereq_1]}"
check "the account prerequisite links somewhere usable" $? ""

# ─── URL resolution per mode ────────────────────────────────────────────────
# write_env_file() is called for real against a sandbox copy of .env.example,
# and the resulting .env is read back. Extracting the function and eval'ing
# it looked simpler and was wrong: its `declare -A REPL` is function-local,
# so an extracted copy silently reports nothing.
render_env() {   # $1 = mode -> echoes the path of the generated .env
    local mode="$1"
    local box; box="$(mktemp -d)"
    cp "$REPO/.env.example" "$box/.env.example"
    : > "$box/.env"

    CFG_DEPLOYMENT_MODE="$mode"
    CFG_DOMAIN="example.com"; CFG_SUBDOMAIN_PREFIX="kurs"
    CFG_COURSE_NAME="C"; CFG_COURSE_ID="c"; CFG_BASE_DATA_PATH="$box/data"
    CFG_ADMIN_EMAIL="a@example.com"; CFG_TZ="Europe/Berlin"
    CFG_COMPOSE_PROFILES="core"; CFG_ENABLE_OBSERVABILITY="no"; CFG_ENABLE_LTI="no"
    CFG_LMS_URL="https://lms.example.com"
    CFG_LLM_PROVIDER="anthropic"; CFG_LLM_MODEL_STRONG="m"; CFG_LLM_MODEL_FAST="m"
    CFG_LLM_API_KEY="k"; CFG_LLM_BASE_URL=""
    CFG_EMBEDDING_PROVIDER="openai"; CFG_EMBEDDING_MODEL="e"
    CFG_EMBEDDING_DIMENSIONS="1536"; CFG_EMBEDDING_API_KEY="k"; CFG_EMBEDDING_BASE_URL=""
    CFG_RERANKER_PROVIDER="none"; CFG_RERANKER_MODEL=""; CFG_RERANKER_API_KEY=""
    CFG_RERANKER_BASE_URL=""; CFG_WEAVIATE_COLLECTION_NAME="X"
    CFG_INSTALL_POSTFIX="false"; CFG_SMTP_RELAY_HOST=""; CFG_SMTP_RELAY_PORT="587"
    CFG_SMTP_RELAY_USER=""; CFG_SMTP_RELAY_PASSWORD=""; CFG_SMTP_HOST=""
    CFG_SMTP_PORT="25"; CFG_SMTP_SECURE="false"; CFG_SMTP_USER=""
    CFG_SMTP_PASSWORD=""; CFG_SMTP_CONNECTION_URL=""; CFG_N8N_EMAIL_MODE=""
    # write_env_file substitutes every generated secret; the wizard normally
    # sets these in phase 3.
    local v
    for v in $(grep -oE '^\s*SECRET_[A-Z0-9_]+' "$REPO/scripts/lib/secrets.sh" | tr -d ' ' | sort -u); do
        printf -v "$v" '%s' "test-value"
    done
    write_env_file "$box" >/dev/null 2>&1
    echo "$box/.env"
}

env_value() { grep -m1 "^$2=" "$1" | cut -d= -f2- | sed 's/^"//; s/"$//'; }

DOMAIN_ENV="$(render_env domain)"
[[ "$(env_value "$DOMAIN_ENV" FLOWISE_PUBLIC_URL)" == "https://kurs-smart-rag.example.com" ]]
check "domain mode resolves the prefixed hostname" $? "$(env_value "$DOMAIN_ENV" FLOWISE_PUBLIC_URL)"
[[ "$(env_value "$DOMAIN_ENV" DEPLOYMENT_MODE)" == "domain" ]]
check "domain mode is recorded in .env" $? "$(env_value "$DOMAIN_ENV" DEPLOYMENT_MODE)"

TS_ENV="$(render_env tailscale)"
[[ "$(env_value "$TS_ENV" DEPLOYMENT_MODE)" == "tailscale" ]]
check "tailscale mode is recorded in .env" $? "$(env_value "$TS_ENV" DEPLOYMENT_MODE)"

# The wizard joins the tailnet BEFORE writing .env, so the URLs are correct
# on the first write. Getting the name later meant patching .env afterwards
# and restarting every container to pick it up — a restart round in the
# middle of an install, caused only by asking too late.
CFG_TAILSCALE_HOSTNAME="i5.tail1234.ts.net"
TS_ENV="$(render_env tailscale)"
declare -A WANT=(
    [FLOWISE_PUBLIC_URL]="https://i5.tail1234.ts.net"
    [CONTENT_ADMIN_PUBLIC_URL]="https://i5.tail1234.ts.net:8443"
    [N8N_WEBHOOK_URL]="https://i5.tail1234.ts.net:8444"
    [MINIO_BROWSER_REDIRECT_URL]="https://i5.tail1234.ts.net:8446"
    [MINIO_SERVER_URL]="https://i5.tail1234.ts.net:8447"
    [TAILSCALE_HOSTNAME]="i5.tail1234.ts.net"
    [N8N_HOSTNAME]="i5.tail1234.ts.net"
)
for key in "${!WANT[@]}"; do
    val="$(env_value "$TS_ENV" "$key")"
    [[ "$val" == "${WANT[$key]}" ]]
    check "tailscale mode resolves $key at write time" $? "got '$val', wanted '${WANT[$key]}'"
    # One certificate covers one name, so services differ by port, never by
    # subdomain — a subdomain here would have no certificate at all.
    [[ "$val" != *"smart-rag."* && "$val" != *example.com* ]]
    check "$key uses no subdomain and no leftover domain" $? "$val"
done

# Everything NOT mode-specific must still be written — a mode branch that
# returned early would silently blank the rest of the file.
[[ -n "$(env_value "$TS_ENV" COURSE_ID)" && -n "$(env_value "$TS_ENV" LLM_PROVIDER)" ]]
check "the mode branch does not cut the rest of .env short" $? \
      "COURSE_ID='$(env_value "$TS_ENV" COURSE_ID)' LLM_PROVIDER='$(env_value "$TS_ENV" LLM_PROVIDER)'"
(( $(grep -cE '^[A-Z][A-Z0-9_]*=' "$TS_ENV") > 100 ))
check "tailscale .env has the full set of keys" $? "$(grep -cE '^[A-Z][A-Z0-9_]*=' "$TS_ENV") keys"

# ─── bootstrap skips the right phases ───────────────────────────────────────
PHASES="$(sed -n '/^run_deployment_phases()/,/^}/p' "$REPO/scripts/bootstrap.sh")"
grep -q 'install-tailscale.sh' <<<"$PHASES"
check "tailscale mode runs install-tailscale.sh" $? ""
grep -q 'DEPLOYMENT_MODE:-domain.*== "tailscale"' <<<"$PHASES"
check "the phase list branches on the mode" $? ""
# get-ssl-certs must be inside the domain branch, never unconditional.
awk '/== "tailscale"/{ts=1} /^    else$/{ts=0} /get-ssl-certs/{ if (ts) print "IN_TAILSCALE_BRANCH" }' <<<"$PHASES" | grep -q .
check "certificates are not requested in tailscale mode" $(( $? == 0 ? 1 : 0 )) ""

grep -q 'orch_tailscale_skip_nginx' "$REPO/scripts/bootstrap.sh"
check "no nginx vhost is written in tailscale mode" $? ""
grep -q 'orch_tailscale_skip_coexist' "$REPO/scripts/bootstrap.sh"
check "coexistence checks are skipped in tailscale mode" $? ""

# ─── install-tailscale.sh ───────────────────────────────────────────────────
TS="$REPO/scripts/install-tailscale.sh"
[[ -x "$TS" ]]
check "the script is executable" $? ""
bash -n "$TS"
check "the script parses" $? ""

# An auth key would have to live in .env, where its reach is the whole
# tailnet — a much larger credential than anything else in that file.
# Comments explaining WHY no auth key is used must not trip this — only
# actual code may be inspected.
grep -vE '^\s*#' "$TS" | grep -qiE "auth.?key|tskey-|--authkey"
check "no auth key is used or stored" $(( $? == 0 ? 1 : 0 )) \
      "$(grep -vE '^\s*#' "$TS" | grep -niE 'auth.?key|tskey-' | head -2)"

grep -q "tailscale serve reset" "$TS"
check "serve is reset before applying, so re-runs don't stack" $? ""
grep -q -- "--https=443" "$TS"
check "Flowise takes 443, the port Funnel allows" $? ""
grep -q "tailscale funnel" "$TS"
check "the chat is exposed via Funnel" $? ""
# Funnel failing must not kill an otherwise working install.
sed -n '/tailscale funnel --bg/,/^fi/p' "$TS" | grep -q "warn"
check "a Funnel failure warns rather than aborts" $? ""

# Every serve port must map to a variable, not a hardcoded container port.
grep -qE '\[8444\]="\$\{N8N_PORT' "$TS"
check "serve ports read the host bindings from .env" $? "$(grep -n '\[8444\]' "$TS")"

# The MagicDNS name has to reach .env, or nothing downstream knows the URLs.
for key in FLOWISE_PUBLIC_URL MINIO_SERVER_URL N8N_WEBHOOK_URL TAILSCALE_HOSTNAME; do
    grep -q "set_env_var \"\$ENV_FILE\" $key" "$TS"
    check "install-tailscale.sh writes $key" $? ""
done

# ─── .env.example documents both modes ──────────────────────────────────────
grep -q '^DEPLOYMENT_MODE=' "$REPO/.env.example"
check "DEPLOYMENT_MODE is declared" $? ""
grep -q '^TAILSCALE_HOSTNAME=' "$REPO/.env.example"
check "TAILSCALE_HOSTNAME is declared" $? ""
grep -qi "LTI does NOT work" "$REPO/.env.example"
check ".env.example states the LTI limitation" $? ""

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All deployment-mode checks passed: the mode is the first wizard question"
echo "with the back-from-summary index matching the step count, and it states"
echo "the LTI limitation where the choice is made and joins the tailnet right"
echo "there, so every URL is resolved at write time instead of being patched"
echo "in afterwards with a restart round; domain mode resolves prefixed"
echo "hostnames while tailscale mode separates services by port, never by a"
echo "subdomain no certificate would cover, without cutting the rest of .env"
echo "short; bootstrap requests no certificate and writes no"
echo "nginx vhost in tailscale mode; and install-tailscale.sh uses no auth"
echo "key, resets serve before applying, puts Flowise on a Funnel-capable"
echo "port, treats a Funnel failure as a warning, reads its host bindings"
echo "from .env, and writes the resolved URLs back."
