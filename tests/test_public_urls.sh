#!/usr/bin/env bash
# Public hostnames must be built with subdomain_host(), which applies
# SUBDOMAIN_PREFIX — the prefix the wizard sets when the plain names collide
# with something already in nginx.
#
# docker-compose.yml built four of them inline as "https://s3.${DOMAIN}",
# silently dropping the prefix, so MinIO's console and the LTI middleware
# pointed at hostnames with no DNS record, no vhost and no certificate. One
# of them (APP_URL) was wrong even without a prefix: the bare domain, while
# Flowise is served on the smart-rag subdomain.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

# ─── No compose value may build a subdomain from ${DOMAIN} ──────────────────
while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    # A value that puts anything in front of ${DOMAIN} is a subdomain being
    # assembled by hand — exactly what has to come from .env instead.
    [[ "$line" =~ [/.@a-zA-Z0-9-]\$\{DOMAIN\} ]] || continue
    FAILURES+=("compose builds a subdomain from \${DOMAIN}: ${line# }")
done < "$REPO/docker/docker-compose.yml"

# ─── The keys compose now expects must exist in .env.example ────────────────
for key in GARAGE_S3_PUBLIC_URL FLOWISE_PUBLIC_URL; do
    grep -q "^${key}=" "$REPO/.env.example"
    check "$key is declared in .env.example" $? ""
done
# FLOWISE_PUBLIC_URL reaches its container through compose. The S3 public URL
# does not: nothing in compose needs it. It exists so the wizard can resolve
# LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT — the address a browser is given
# for a batch-export download, which a container name cannot serve.
grep -q '\${FLOWISE_PUBLIC_URL}' "$REPO/docker/docker-compose.yml"
check "FLOWISE_PUBLIC_URL is used by compose" $? ""
grep -q 'REPL\[LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT\]' "$REPO/scripts/lib/templates.sh"
check "the S3 public URL is what resolves the external export endpoint" $? ""

# ─── …and bootstrap must fill them, prefix-aware ────────────────────────────
# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"

for key in GARAGE_S3_PUBLIC_URL FLOWISE_PUBLIC_URL \
           N8N_WEBHOOK_URL N8N_HOSTNAME NEXTAUTH_URL \
           LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT; do
    grep -q "REPL\[$key\]=" "$REPO/scripts/lib/templates.sh"
    check "bootstrap resolves $key" $? "not set in templates.sh"
    # Each must go through subdomain_host, not string-concatenate the domain.
    grep "REPL\[$key\]=" "$REPO/scripts/lib/templates.sh" | grep -q "subdomain_host"
    check "$key goes through subdomain_host" $? \
          "$(grep "REPL\[$key\]=" "$REPO/scripts/lib/templates.sh")"
done

# ─── The naming rule itself, both ways ──────────────────────────────────────
[[ "$(subdomain_host s3 "lmu.de" "smartrag")" == "smartrag-s3.lmu.de" ]]
check "prefixed host is prefix-service.domain" $? "$(subdomain_host s3 lmu.de smartrag)"
[[ "$(subdomain_host s3 "lmu.de" "")" == "s3.lmu.de" ]]
check "unprefixed host is service.domain" $? "$(subdomain_host s3 lmu.de '')"

# ─── Every hostname compose/env relies on must be one nginx serves ──────────
# A URL nobody has a vhost or certificate for is the failure mode this is
# all about, so check the two lists agree.
mapfile -t VHOSTS < <(sed -n 's/^[[:space:]]*server_name \([a-z0-9-]*\)\.YOUR_DOMAIN;.*/\1/p' \
    "$REPO/nginx/smartrag-suite.conf" | sort -u)
for svc in s3 smart-rag; do
    printf '%s\n' "${VHOSTS[@]}" | grep -qx "$svc"
    check "nginx serves a vhost for '$svc'" $? "vhosts: ${VHOSTS[*]}"
done

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All public-URL checks passed: docker-compose.yml no longer assembles any"
echo "subdomain from \${DOMAIN}; every public URL it consumes is declared in"
echo ".env.example, used by compose, and resolved by bootstrap through"
echo "subdomain_host() so SUBDOMAIN_PREFIX is applied; and s3 and"
echo "smart-rag each have a vhost nginx actually serves."
