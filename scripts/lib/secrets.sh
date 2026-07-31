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
#   SECRET_MINIO_ROOT_PASSWORD
#   SECRET_MINIO_LANGFUSE_SECRET_KEY
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
    SECRET_MINIO_ROOT_PASSWORD="$(gen_alnum 32)"
    SECRET_MINIO_LANGFUSE_SECRET_KEY="$(gen_alnum 32)"

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
MinIO:       https://minio.$DOMAIN
LTI:         https://lti.$DOMAIN         (only if 'lti' profile)

─── Database credentials (internal — usually not needed) ───────────────────
PostgreSQL:  smartrag / $SECRET_POSTGRES_PASSWORD
Neo4j:       neo4j    / $SECRET_NEO4J_PASSWORD
Redis:       (no user) / $SECRET_REDIS_PASSWORD
ClickHouse:  ch_admin / $SECRET_CLICKHOUSE_PASSWORD
MinIO:       smartrag-admin / $SECRET_MINIO_ROOT_PASSWORD

─── API keys (internal — set in .env, used by services) ────────────────────
Weaviate API key:           $SECRET_WEAVIATE_API_KEY
MinIO Langfuse service key: $SECRET_MINIO_LANGFUSE_SECRET_KEY

═══════════════════════════════════════════════════════════════════════════
EOF

    chmod 600 "$out"
}
