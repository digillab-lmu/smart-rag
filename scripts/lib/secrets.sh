# ═════════════════════════════════════════════════════════════════════════════
# secrets.sh — cryptographically secure secret generation
# ═════════════════════════════════════════════════════════════════════════════
#
# All secrets are generated from /dev/urandom (kernel CSPRNG).
# Three formats are used:
#   - hex(N)    → N*2 hex characters     (use for: encryption keys, JWT secrets)
#   - alnum(N)  → N alphanumeric chars   (use for: passwords — readable in logs)
#   - base64url(N) → N*4/3 url-safe chars(use for: session tokens, salts)
#
# After generate_all_secrets() runs, the following globals are set:
#   SECRET_POSTGRES_PASSWORD
#   SECRET_WEAVIATE_API_KEY
#   SECRET_NEO4J_PASSWORD
#   SECRET_REDIS_PASSWORD
#   SECRET_GARAGE_ACCESS_KEY / SECRET_GARAGE_SECRET_KEY
#   SECRET_GARAGE_LANGFUSE_ACCESS_KEY / SECRET_GARAGE_LANGFUSE_SECRET_KEY
#   SECRET_GARAGE_RPC_SECRET / SECRET_GARAGE_ADMIN_TOKEN
#   SECRET_INGEST_STATUS_TOKEN
#   SECRET_CLICKHOUSE_PASSWORD
#   SECRET_FLOWISE_PASSWORD
#   SECRET_SALT
#   SECRET_ENCRYPTION_KEY
#   SECRET_JWT_AUTH_TOKEN_SECRET
#   SECRET_JWT_REFRESH_TOKEN_SECRET
#   SECRET_EXPRESS_SESSION_SECRET
#   SECRET_TOKEN_HASH_SECRET
#   SECRET_N8N_ENCRYPTION_KEY
#   SECRET_N8N_USER_MANAGEMENT_JWT_SECRET
#   SECRET_NEXTAUTH_SECRET
#   SECRET_LTI_SESSION_SECRET
#   SECRET_CONTENT_ADMIN_SESSION_SECRET
#   SECRET_ADMIN_PASSWORD     (shared across Flowise/n8n/Langfuse for first login)
# ═════════════════════════════════════════════════════════════════════════════

# ─── Primitive generators ────────────────────────────────────────────────────

# hex(bytes) → 2*bytes hex chars
gen_hex() {
    local bytes="${1:-32}"
    openssl rand -hex "$bytes"
}

# alnum(length) → length [A-Za-z0-9] chars, no ambiguous chars (no 0/O/1/l/I)
gen_alnum() {
    local length="${1:-24}"
    # Read more bytes than needed and filter
    LC_ALL=C tr -dc 'A-HJ-NP-Za-km-z2-9' < /dev/urandom 2>/dev/null | head -c "$length"
    echo
}

# base64url(bytes) → URL-safe base64 (no /, +, =)
gen_base64url() {
    local bytes="${1:-32}"
    openssl rand -base64 "$bytes" | tr '/+' '_-' | tr -d '='
}

# ─── Master generator ────────────────────────────────────────────────────────

generate_all_secrets() {
    # Database passwords — readable alphanumeric (no special chars to avoid escaping pain)
    SECRET_POSTGRES_PASSWORD="$(gen_alnum 32)"
    SECRET_NEO4J_PASSWORD="$(gen_alnum 32)"
    SECRET_REDIS_PASSWORD="$(gen_alnum 32)"
    SECRET_CLICKHOUSE_PASSWORD="$(gen_alnum 32)"
    # Garage object storage. Two access keys, mirroring the separation MinIO
    # had: one for this project's own bucket, one for Langfuse's three.
    # Garage's model is per-key-per-bucket, so each is granted only where it
    # belongs rather than being an administrator that happens to be used.
    #
    # The shapes match what Garage generates itself — GK + 24 hex for an id,
    # 64 hex for a secret — because these are handed to `garage key import`,
    # and a key that does not look like a Garage key is a bet on how strictly
    # it validates. The id is not a secret; the secret is.
    SECRET_GARAGE_ACCESS_KEY="GK$(gen_hex 12)"
    SECRET_GARAGE_SECRET_KEY="$(gen_hex 32)"
    SECRET_GARAGE_LANGFUSE_ACCESS_KEY="GK$(gen_hex 12)"
    SECRET_GARAGE_LANGFUSE_SECRET_KEY="$(gen_hex 32)"
    # Node-to-node authentication and the admin API. A single-node deployment
    # still requires both to be set — Garage refuses to start without an
    # rpc_secret.
    SECRET_INGEST_STATUS_TOKEN="$(gen_hex 32)"
    SECRET_GARAGE_RPC_SECRET="$(gen_hex 32)"
    SECRET_GARAGE_ADMIN_TOKEN="$(gen_hex 32)"

    # API keys — alphanumeric (used in URL-safe contexts)
    SECRET_WEAVIATE_API_KEY="$(gen_alnum 40)"

    # Application secrets — hex (used as crypto keys / signing secrets)
    SECRET_SALT="$(gen_hex 16)"
    SECRET_ENCRYPTION_KEY="$(gen_hex 32)"
    SECRET_JWT_AUTH_TOKEN_SECRET="$(gen_hex 32)"
    SECRET_JWT_REFRESH_TOKEN_SECRET="$(gen_hex 32)"
    SECRET_EXPRESS_SESSION_SECRET="$(gen_hex 32)"
    SECRET_TOKEN_HASH_SECRET="$(gen_hex 32)"
    SECRET_N8N_ENCRYPTION_KEY="$(gen_hex 32)"
    SECRET_N8N_USER_MANAGEMENT_JWT_SECRET="$(gen_hex 32)"
    SECRET_NEXTAUTH_SECRET="$(gen_hex 32)"
    SECRET_LTI_SESSION_SECRET="$(gen_hex 32)"
    SECRET_CONTENT_ADMIN_SESSION_SECRET="$(gen_hex 32)"

    # Langfuse project keys. Langfuse initialises its organisation, project,
    # user and API keys from LANGFUSE_INIT_* on first start — so these have to
    # exist before it comes up, and they are what Flowise later authenticates
    # with when reporting traces. The pk-lf-/sk-lf- prefixes are Langfuse's own
    # convention; nothing enforces them, but a key that looks like a Langfuse
    # key is recognisable in a log or a credential list.
    SECRET_LANGFUSE_PUBLIC_KEY="pk-lf-$(gen_hex 16)"
    SECRET_LANGFUSE_SECRET_KEY="sk-lf-$(gen_hex 16)"

    # Admin-facing passwords — alphanumeric, more memorable
    SECRET_ADMIN_PASSWORD="$(gen_alnum 24)"
    SECRET_FLOWISE_PASSWORD="$SECRET_ADMIN_PASSWORD"   # same admin pw across services
}


# ─── Write credentials.txt ───────────────────────────────────────────────────
# Called after .env is written. Creates a human-readable summary of all
# admin credentials, with 0600 permissions (owner read/write only).
#
# Args: $1 = path to credentials.txt
write_credentials_file() {
    local out="$1"
    local now; now="$(date -Iseconds 2>/dev/null || date)"

    cat > "$out" <<EOF
═══════════════════════════════════════════════════════════════════════════
SMART RAG — Initial Credentials
═══════════════════════════════════════════════════════════════════════════
Generated:   $now
Domain:      $DOMAIN
Course ID:   $COURSE_ID

⚠  KEEP THIS FILE SAFE. Permissions are 600 (owner-only).
⚠  These are the credentials needed for initial login. Once you've logged in
   you can rotate them in the respective UIs.

─── Admin login (Flowise / n8n / Langfuse) ──────────────────────────────────
Email:       $ADMIN_EMAIL
Username:    admin
Password:    $SECRET_ADMIN_PASSWORD

─── Service URLs ────────────────────────────────────────────────────────────
Flowise:     https://smart-rag.$DOMAIN
n8n:         https://n8n.$DOMAIN
Langfuse:    https://langfuse.$DOMAIN   (only if 'observability' profile)
LTI:         https://lti.$DOMAIN         (only if 'lti' profile)

─── Database credentials (internal — usually not needed) ───────────────────
PostgreSQL:  smartrag / $SECRET_POSTGRES_PASSWORD
Neo4j:       neo4j    / $SECRET_NEO4J_PASSWORD
Redis:       (no user) / $SECRET_REDIS_PASSWORD
ClickHouse:  ch_admin / $SECRET_CLICKHOUSE_PASSWORD
S3 (Garage): $SECRET_GARAGE_ACCESS_KEY / $SECRET_GARAGE_SECRET_KEY
             (no web console — Garage has none)

─── API keys (internal — set in .env, used by services) ────────────────────
Weaviate API key:           $SECRET_WEAVIATE_API_KEY
S3 key for Langfuse: $SECRET_GARAGE_LANGFUSE_ACCESS_KEY / $SECRET_GARAGE_LANGFUSE_SECRET_KEY

═══════════════════════════════════════════════════════════════════════════
EOF

    chmod 600 "$out"
}
