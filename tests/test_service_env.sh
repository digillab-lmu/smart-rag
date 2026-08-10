#!/usr/bin/env bash
# Every service must find the variables it actually reads.
#
# From a real install: Langfuse would not start, logging
# "Redis error WRONGPASS invalid username-password pair". The credential was
# correct — the NAME was not. Langfuse reads REDIS_AUTH (its self-hosting
# docs, "cache"), our .env only carried REDIS_PASSWORD, and REDIS_AUTH
# existed solely inside docker-compose.yml's Flowise block. Langfuse takes
# its whole configuration from .env via env_file, so it connected with no
# password at all — and Redis answers WRONGPASS to an empty one, which reads
# like a wrong credential rather than a missing variable.
#
# The general rule this encodes: a service configured through `env_file`
# can only see what is IN that file. Anything set under another service's
# `environment:` key is invisible to it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

ENV_EXAMPLE="$REPO/.env.example"
COMPOSE="$REPO/docker/docker-compose.yml"

env_has() { grep -qE "^$1=" "$ENV_EXAMPLE"; }

# ─── Services that take their whole config from .env ────────────────────────
# Langfuse has no `environment:` block of its own beyond PORT, so every one
# of these has to be in .env.
LANGFUSE_NEEDS=(
    DATABASE_URL NEXTAUTH_SECRET NEXTAUTH_URL SALT ENCRYPTION_KEY
    CLICKHOUSE_URL CLICKHOUSE_MIGRATION_URL CLICKHOUSE_USER CLICKHOUSE_PASSWORD
    REDIS_HOST REDIS_PORT REDIS_AUTH
)
for v in "${LANGFUSE_NEEDS[@]}"; do
    env_has "$v"
    check "Langfuse: $v is in .env" $? "only reachable if it is in .env"
done

# The specific trap: present under another service, absent from .env.
for v in "${LANGFUSE_NEEDS[@]}"; do
    if ! env_has "$v" && grep -q "$v" "$COMPOSE"; then
        FAILURES+=("$v exists only in docker-compose.yml — invisible to env_file services")
    fi
done
check "no variable is compose-only where a service needs it from .env" 0 ""

# ─── REDIS_AUTH must carry the same value Redis requires ────────────────────
# Redis is started with --requirepass ${REDIS_PASSWORD}; a REDIS_AUTH that
# says anything else fails in exactly the way that started this.
grep -q 'requirepass ${REDIS_PASSWORD}' "$COMPOSE"
check "Redis requires REDIS_PASSWORD" $? ""
grep -qE '^REDIS_AUTH="\$\{REDIS_PASSWORD\}"' "$ENV_EXAMPLE"
check "REDIS_AUTH mirrors REDIS_PASSWORD in .env.example" $? \
      "$(grep -n '^REDIS_AUTH=' "$ENV_EXAMPLE")"

# And the wizard must write it resolved, like every other secret.
grep -q 'REPL\[REDIS_AUTH\]' "$REPO/scripts/lib/templates.sh"
check "bootstrap writes REDIS_AUTH into .env" $? ""
grep -q 'REPL\[REDIS_AUTH\]="\$SECRET_REDIS_PASSWORD"' "$REPO/scripts/lib/templates.sh"
check "and writes the same secret Redis is started with" $? \
      "$(grep -n 'REPL\[REDIS_AUTH\]' "$REPO/scripts/lib/templates.sh")"

# Existing installs get it through the Upgrade entry rather than by hand.
grep -q 'REDIS_AUTH)' "$REPO/scripts/admin.sh"
check "the upgrade entry knows how to fill REDIS_AUTH" $? ""

# ─── The same check for the other env_file-only services ────────────────────
# n8n also configures itself largely from .env.
for v in N8N_ENCRYPTION_KEY N8N_HOST N8N_PORT; do
    env_has "$v" || grep -qE "^ *$v:" "$COMPOSE"
    check "n8n: $v is available" $? "neither in .env nor in its compose block"
done

# ─── The same trap, second instance: SMTP_SENDER_EMAIL ──────────────────────
# .env.example carries "noreply@${DOMAIN}". Bash expands that when sourcing,
# and Compose expands it when substituting into an `environment:` block — but
# NOT for values handed to a container through `env_file`, and not for
# Python's read_env(), which deliberately does not implement shell semantics.
#
# smartrag-n8n takes its whole environment from env_file, and the ingest
# workflow reads $env.SMTP_SENDER_EMAIL — so left interpolated, every "your
# document is ready" mail goes out with a literal ${DOMAIN} in the sender
# address. N8N_SMTP_SENDER being correct does not help: the workflow reads
# the other name.
grep -q 'env_file: ../.env' "$COMPOSE"
check "some services are configured through env_file" $? ""

grep -q 'SMTP_SENDER_EMAIL' "$REPO/n8n/workflows-ingest/ingest-document.json"
check "the ingest workflow reads SMTP_SENDER_EMAIL" $? ""

grep -q 'REPL\[SMTP_SENDER_EMAIL\]' "$REPO/scripts/lib/templates.sh"
check "bootstrap writes SMTP_SENDER_EMAIL resolved" $? \
      "otherwise the container sees a literal \${DOMAIN}"

grep -q 'SMTP_SENDER_EMAIL)' "$REPO/scripts/admin.sh"
check "the upgrade path can add SMTP_SENDER_EMAIL too" $? ""

# The general form: no value that an env_file service must read may be left
# with an unexpanded ${...} by the wizard. DATABASE_URL and NEO4J_AUTH are
# fine — they reach their containers through `environment:`, where Compose
# does interpolate.
ENVFILE_MUST_RESOLVE=(SMTP_SENDER_EMAIL REDIS_AUTH)
for v in "${ENVFILE_MUST_RESOLVE[@]}"; do
    grep -q "REPL\[$v\]" "$REPO/scripts/lib/templates.sh"
    check "$v is written resolved, not interpolated" $? \
          "env_file passes values through verbatim"
done


# ─── n8n's database settings carry no N8N_ prefix ───────────────────────────
# Found on a live install: an execution query answered "relation
# execution_entity does not exist". n8n reads DB_TYPE and DB_POSTGRESDB_*
# (packages/@n8n/config/src/configs/database.config.ts), and DB_TYPE defaults
# to 'sqlite'. We were setting N8N_DB_TYPE and N8N_DB_POSTGRESDB_*, which n8n
# ignores — so it ran on SQLite while the Postgres database stayed empty, and
# a backup of Postgres contained none of n8n's workflows, credentials or
# history. Nothing ever failed, which is exactly why it survived this long.
n8n_block="$(sed -n '/^  smartrag-n8n:/,/^  smartrag-[a-z]/p' "$COMPOSE")"

for v in DB_TYPE DB_POSTGRESDB_HOST DB_POSTGRESDB_USER DB_POSTGRESDB_PASSWORD DB_POSTGRESDB_DATABASE; do
    grep -qE "^\s+$v:" <<<"$n8n_block"
    check "n8n receives $v under the name it reads" $? \
          "n8n's config decorators use no N8N_ prefix for database settings"
done

# And the prefixed spellings must not be what the container is given: they
# look configured and do nothing.
grep -qE '^\s+N8N_DB_(TYPE|POSTGRESDB)' <<<"$n8n_block"
check "no N8N_DB_* is passed as if it configured something" $(( $? == 0 ? 1 : 0 )) \
      "$(grep -nE '^\s+N8N_DB_' <<<"$n8n_block" | head -2)"

# The .env keys keep their namespaced names — renaming them would be a
# migration for every existing installation, and the container-side name is
# the only one that has to match.
grep -qE '^N8N_DB_TYPE=' "$ENV_EXAMPLE"
check ".env keeps its namespaced key" $? "renaming it would break existing installs"

# ─── Every $env. a workflow reads must exist in .env ────────────────────────
# The workflows reach into the container's environment 24 times — collection
# name, embedding key and model, course id, LLM credentials. A reference to a
# variable nobody sets resolves to undefined and fails somewhere downstream,
# far from the cause.
missing_env=""
for v in $(grep -ohE '\$env\.[A-Z][A-Z0-9_]*' "$REPO"/n8n/workflows*/*.json \
           | sed 's/\$env\.//' | sort -u); do
    grep -qE "^$v=" "$ENV_EXAMPLE" || missing_env="$missing_env $v"
done
check "every \$env a workflow reads is defined in .env.example" \
      $([[ -z "$missing_env" ]] && echo 0 || echo 1) "missing:$missing_env"

# And that access must stay switched on. n8n warns that
# N8N_BLOCK_ENV_ACCESS_IN_NODE flips from false to true in a future release;
# unpinned, a plain image bump would break the whole ingest with nothing
# changed on our side.
grep -q 'N8N_BLOCK_ENV_ACCESS_IN_NODE: "false"' "$COMPOSE"
check "env access from nodes is pinned on" $? \
      "an image bump would silently break every \$env reference"

# Nothing should be allowlisted that is neither installed nor used: it only
# produces a warning at every start, which trains people to ignore warnings.
if grep -q 'NODE_FUNCTION_ALLOW_EXTERNAL' "$COMPOSE"; then
    for m in $(grep -oE 'NODE_FUNCTION_ALLOW_EXTERNAL: "[^"]*"' "$COMPOSE" \
               | sed 's/.*"\(.*\)"/\1/' | tr ',' ' '); do
        [[ -z "$m" ]] && continue
        grep -q "$m" "$REPO"/n8n/workflows*/*.json
        check "allowlisted module $m is actually used" $? "allowlisted but unused"
    done
fi

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All service-environment checks passed: every variable Langfuse reads is"
echo "in .env rather than only inside another service's compose block — which"
echo "is invisible to a service configured through env_file — REDIS_AUTH"
echo "mirrors the password Redis is actually started with, bootstrap writes it"
echo "resolved, and the upgrade entry can add it to an existing installation."
