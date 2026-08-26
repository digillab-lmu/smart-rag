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
# ─── …and one derivation fills them, prefix-aware ───────────────────────────
# These used to be written out in templates.sh. They moved into address_vars()
# in lib/common.sh when the restore's rename needed the same set: two copies
# of the arithmetic is how a rename ends up rewriting some of the eight and
# leaving the rest pointing at the address the installation moved away from.
#
# So the check follows the derivation rather than the file it used to live in,
# and asserts the values it produces instead of the lines that produce them.
# shellcheck source=/dev/null
source "$REPO/scripts/lib/common.sh"

grep -q 'LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT' "$REPO/scripts/lib/common.sh"
check "the S3 public URL is what resolves the external export endpoint" $? ""

# The call, not the name: the comment above it also says "address_vars", and
# a mutation that replaced the call with `true` passed a check for the name.
grep -qF '< <(address_vars' "$REPO/scripts/lib/templates.sh"
check "the installer fills them from that one derivation" $? \
      "templates.sh has its own copy again"

DERIVED="$(address_vars domain "lmu.de" "smartrag" "")"
for key in GARAGE_S3_PUBLIC_URL FLOWISE_PUBLIC_URL CONTENT_ADMIN_PUBLIC_URL \
           N8N_WEBHOOK_URL N8N_HOSTNAME NEXTAUTH_URL \
           LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT SMTP_SENDER_EMAIL; do
    grep -q "^$key=" <<<"$DERIVED"
    check "the derivation produces $key" $? "$DERIVED"
    # Prefix-aware: string-concatenating the domain silently drops the prefix
    # and points a service at a host with no DNS record and no certificate.
    value="$(grep "^$key=" <<<"$DERIVED" | cut -d= -f2-)"
    case "$key" in
        SMTP_SENDER_EMAIL) [[ "$value" == *"@lmu.de" ]] ;;
        N8N_HOSTNAME)      [[ "$value" == "smartrag-"* ]] ;;
        *)                 [[ "$value" == *"//smartrag-"* ]] ;;
    esac
    check "$key respects the subdomain prefix" $? "$key=$value"
done

# Tailscale mode separates services by port on one name, and must not invent
# subdomains that no certificate covers.
TS="$(address_vars tailscale "" "" "host.tailnet.ts.net")"
grep -q "^N8N_WEBHOOK_URL=https://host.tailnet.ts.net:8444$" <<<"$TS"
check "tailscale mode separates by port, not by subdomain" $? "$TS"
! grep -qE "^[A-Z_]+=https://[a-z0-9-]+\.host\.tailnet" <<<"$TS"
check "and invents no subdomain of the tailnet name" $? "$TS"

# A mode with nothing to derive from is a refusal, not an empty address.
address_vars domain "" "" "" >/dev/null 2>&1
check "domain mode without a domain is refused" $(( $? == 0 ? 1 : 0 )) ""
address_vars tailscale "" "" "" >/dev/null 2>&1
check "tailscale mode without a name is refused" $(( $? == 0 ? 1 : 0 )) ""

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
