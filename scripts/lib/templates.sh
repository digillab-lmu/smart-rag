# ═════════════════════════════════════════════════════════════════════════════
# templates.sh — write .env and substitute template files
# ═════════════════════════════════════════════════════════════════════════════
#
# Depends on globals from config-wizard.sh (CFG_*) and secrets.sh (SECRET_*).
# All file writes are idempotent and create backups before overwriting.
# ═════════════════════════════════════════════════════════════════════════════

# ─── .env writer ─────────────────────────────────────────────────────────────
# Substitutes all configured values into .env.example and writes to repo/.env.
# Uses awk for line-by-line key=value replacement — only modifies known keys,
# preserves all comments and structure from .env.example.
write_env_file() {
    local repo="$1"
    local src="$repo/.env.example"
    local dst="$repo/.env"

    [[ -f "$src" ]] || die ".env.example not found at $src"
    info "$(t tpl_writing_env "$dst")"

    backup_file "$dst"

    # Build a key→value associative array (string values are NOT yet quoted)
    declare -A REPL
    # Course & deployment
    REPL[COURSE_NAME]="$CFG_COURSE_NAME"
    REPL[COURSE_ID]="$CFG_COURSE_ID"
    REPL[DOMAIN]="$CFG_DOMAIN"
    REPL[BASE_DATA_PATH]="$CFG_BASE_DATA_PATH"
    REPL[ADMIN_EMAIL]="$CFG_ADMIN_EMAIL"
    REPL[ADMIN_PASSWORD]="$SECRET_ADMIN_PASSWORD"
    REPL[TZ]="$CFG_TZ"

    # Subdomain prefix (set by resolve_subdomain_prefix() in preflight.sh,
    # only non-empty if the plain names collided with something already in
    # nginx). Every URL below that embeds a subdomain is computed fully
    # resolved here — not left as `${DOMAIN}`-style interpolation in .env —
    # so it doesn't depend on Docker Compose's env_file interpolation
    # supporting the conditional prefix logic.
    REPL[DEPLOYMENT_MODE]="${CFG_DEPLOYMENT_MODE:-domain}"
    REPL[SUBDOMAIN_PREFIX]="${CFG_SUBDOMAIN_PREFIX:-}"
    # In tailscale mode there are no subdomains: one certificate covers one
    # MagicDNS name, so services are separated by port. The name is known by
    # now — the wizard joined the tailnet before writing this file, precisely
    # so these can be correct here rather than patched in later.
    if [[ "${CFG_DEPLOYMENT_MODE:-domain}" == "tailscale" ]]; then
        local ts="${CFG_TAILSCALE_HOSTNAME:-}"
        REPL[TAILSCALE_HOSTNAME]="$ts"
        REPL[FLOWISE_PUBLIC_URL]="https://$ts"
        REPL[CONTENT_ADMIN_PUBLIC_URL]="https://$ts:8443"
        REPL[N8N_WEBHOOK_URL]="https://$ts:8444"
        REPL[N8N_HOSTNAME]="$ts"
        REPL[NEXTAUTH_URL]="https://$ts:8445"
        REPL[GARAGE_S3_PUBLIC_URL]="https://$ts:8447"
        REPL[LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT]="https://$ts:8447"
    else
        REPL[TAILSCALE_HOSTNAME]=""
        REPL[N8N_WEBHOOK_URL]="https://$(subdomain_host n8n "$CFG_DOMAIN" "${CFG_SUBDOMAIN_PREFIX:-}")"
        REPL[N8N_HOSTNAME]="$(subdomain_host n8n "$CFG_DOMAIN" "${CFG_SUBDOMAIN_PREFIX:-}")"
        REPL[NEXTAUTH_URL]="https://$(subdomain_host langfuse "$CFG_DOMAIN" "${CFG_SUBDOMAIN_PREFIX:-}")"
        REPL[LANGFUSE_S3_BATCH_EXPORT_EXTERNAL_ENDPOINT]="https://$(subdomain_host s3 "$CFG_DOMAIN" "${CFG_SUBDOMAIN_PREFIX:-}")"
        # These four were once built inline in docker-compose.yml as
        # "https://s3.${DOMAIN}", which silently dropped the prefix and
        # pointed services at hostnames with no DNS record, vhost or
        # certificate. APP_URL was wrong even without a prefix — it named
        # the bare domain, while Flowise lives on the smart-rag subdomain.
        REPL[GARAGE_S3_PUBLIC_URL]="https://$(subdomain_host s3 "$CFG_DOMAIN" "${CFG_SUBDOMAIN_PREFIX:-}")"
        REPL[FLOWISE_PUBLIC_URL]="https://$(subdomain_host smart-rag "$CFG_DOMAIN" "${CFG_SUBDOMAIN_PREFIX:-}")"
        REPL[CONTENT_ADMIN_PUBLIC_URL]="https://$(subdomain_host content "$CFG_DOMAIN" "${CFG_SUBDOMAIN_PREFIX:-}")"
    fi

    # Compose profiles
    # Langfuse wants REDIS_AUTH; only Flowise gets it from compose.
    REPL[REDIS_AUTH]="$SECRET_REDIS_PASSWORD"
    # Written resolved for the same reason. .env.example carries
    # "noreply@${DOMAIN}", which bash expands when sourcing but env_file does
    # not — and smartrag-n8n takes its whole environment from env_file, while
    # the ingest workflow reads $env.SMTP_SENDER_EMAIL. Left interpolated, the
    # "your document is ready" mail goes out with a literal ${DOMAIN} in the
    # sender address. Python's read_env() does not expand it either.
    REPL[SMTP_SENDER_EMAIL]="noreply@$CFG_DOMAIN"
    # Written resolved for the same reason: Langfuse reads these through
    # env_file, which does not interpolate. Left as ${COURSE_NAME} they would
    # reach it literally, and it would create a project named "${COURSE_NAME}"
    # with a user whose address is "${ADMIN_EMAIL}" — accepted, and wrong in a
    # way only visible in the dashboard.
    REPL[LANGFUSE_INIT_PROJECT_NAME]="$CFG_COURSE_NAME"
    REPL[LANGFUSE_INIT_USER_EMAIL]="$CFG_ADMIN_EMAIL"
    REPL[LANGFUSE_INIT_USER_PASSWORD]="$SECRET_ADMIN_PASSWORD"
    REPL[LANGFUSE_INIT_PROJECT_PUBLIC_KEY]="$SECRET_LANGFUSE_PUBLIC_KEY"
    REPL[LANGFUSE_INIT_PROJECT_SECRET_KEY]="$SECRET_LANGFUSE_SECRET_KEY"

    # ── Object storage ──────────────────────────────────────────────────────
    REPL[GARAGE_ACCESS_KEY]="$SECRET_GARAGE_ACCESS_KEY"
    REPL[GARAGE_SECRET_KEY]="$SECRET_GARAGE_SECRET_KEY"
    REPL[GARAGE_LANGFUSE_ACCESS_KEY]="$SECRET_GARAGE_LANGFUSE_ACCESS_KEY"
    REPL[GARAGE_LANGFUSE_SECRET_KEY]="$SECRET_GARAGE_LANGFUSE_SECRET_KEY"
    REPL[GARAGE_RPC_SECRET]="$SECRET_GARAGE_RPC_SECRET"
    REPL[GARAGE_ADMIN_TOKEN]="$SECRET_GARAGE_ADMIN_TOKEN"

    # Langfuse's own S3 settings, resolved. These carried ${MINIO_LANGFUSE_*}
    # before and were never substituted — Langfuse reads its whole
    # configuration through env_file, which passes ${...} through literally,
    # so it had been authenticating with the nine characters of a variable
    # name. Nobody noticed because nothing ever wrote a trace; the same
    # investigation that found tracing switched off found this.
    local _lf_region="${CFG_GARAGE_REGION:-eu-central-1}"
    local _purpose
    for _purpose in EVENT_UPLOAD MEDIA_UPLOAD BATCH_EXPORT; do
        REPL[LANGFUSE_S3_${_purpose}_ACCESS_KEY_ID]="$SECRET_GARAGE_LANGFUSE_ACCESS_KEY"
        REPL[LANGFUSE_S3_${_purpose}_SECRET_ACCESS_KEY]="$SECRET_GARAGE_LANGFUSE_SECRET_KEY"
        REPL[LANGFUSE_S3_${_purpose}_REGION]="$_lf_region"
    done
    REPL[COMPOSE_PROFILES]="$CFG_COMPOSE_PROFILES"

    # LLM
    REPL[LLM_PROVIDER]="$CFG_LLM_PROVIDER"
    REPL[LLM_MODEL_STRONG]="$CFG_LLM_MODEL_STRONG"
    REPL[LLM_MODEL_FAST]="$CFG_LLM_MODEL_FAST"
    REPL[LLM_API_KEY]="$CFG_LLM_API_KEY"
    REPL[LLM_BASE_URL]="$CFG_LLM_BASE_URL"

    # Embedding
    REPL[EMBEDDING_PROVIDER]="$CFG_EMBEDDING_PROVIDER"
    REPL[EMBEDDING_MODEL]="$CFG_EMBEDDING_MODEL"
    REPL[EMBEDDING_DIMENSIONS]="$CFG_EMBEDDING_DIMENSIONS"
    REPL[EMBEDDING_API_KEY]="$CFG_EMBEDDING_API_KEY"
    REPL[EMBEDDING_BASE_URL]="$CFG_EMBEDDING_BASE_URL"

    # Reranker
    REPL[RERANKER_PROVIDER]="$CFG_RERANKER_PROVIDER"
    REPL[RERANKER_MODEL]="$CFG_RERANKER_MODEL"
    REPL[RERANKER_API_KEY]="$CFG_RERANKER_API_KEY"
    REPL[RERANKER_BASE_URL]="$CFG_RERANKER_BASE_URL"

    # Mail relay (SMTP) — what Flowise/n8n/Langfuse actually connect to, plus
    # (if the wizard chose local Postfix) the upstream smarthost Postfix relays
    # through. See ask_mail_config() in config-wizard.sh.
    REPL[INSTALL_POSTFIX_RELAY]="$CFG_INSTALL_POSTFIX"
    REPL[SMTP_RELAY_HOST]="${CFG_SMTP_RELAY_HOST:-}"
    REPL[SMTP_RELAY_PORT]="${CFG_SMTP_RELAY_PORT:-587}"
    REPL[SMTP_RELAY_USER]="${CFG_SMTP_RELAY_USER:-}"
    REPL[SMTP_RELAY_PASSWORD]="${CFG_SMTP_RELAY_PASSWORD:-}"
    REPL[N8N_EMAIL_MODE]="$CFG_N8N_EMAIL_MODE"
    REPL[SMTP_HOST]="$CFG_SMTP_HOST"
    REPL[SMTP_PORT]="$CFG_SMTP_PORT"
    REPL[SMTP_USER]="$CFG_SMTP_USER"
    REPL[SMTP_PASSWORD]="$CFG_SMTP_PASSWORD"
    REPL[SMTP_SECURE]="$CFG_SMTP_SECURE"
    REPL[SMTP_CONNECTION_URL]="$CFG_SMTP_CONNECTION_URL"

    # Weaviate
    REPL[WEAVIATE_COLLECTION_NAME]="$CFG_WEAVIATE_COLLECTION_NAME"
    REPL[WEAVIATE_API_KEY]="$SECRET_WEAVIATE_API_KEY"

    # LMS (LTI)
    REPL[LMS_URL]="$CFG_LMS_URL"
    REPL[LTI_SESSION_SECRET]="$SECRET_LTI_SESSION_SECRET"
    REPL[CONTENT_ADMIN_SESSION_SECRET]="$SECRET_CONTENT_ADMIN_SESSION_SECRET"

    # Database secrets
    REPL[POSTGRES_PASSWORD]="$SECRET_POSTGRES_PASSWORD"
    REPL[NEO4J_PASSWORD]="$SECRET_NEO4J_PASSWORD"
    REPL[REDIS_PASSWORD]="$SECRET_REDIS_PASSWORD"
    REPL[CLICKHOUSE_PASSWORD]="$SECRET_CLICKHOUSE_PASSWORD"

    # Flowise / n8n / Langfuse secrets
    REPL[FLOWISE_PASSWORD]="$SECRET_FLOWISE_PASSWORD"
    REPL[SALT]="$SECRET_SALT"
    REPL[ENCRYPTION_KEY]="$SECRET_ENCRYPTION_KEY"
    REPL[JWT_AUTH_TOKEN_SECRET]="$SECRET_JWT_AUTH_TOKEN_SECRET"
    REPL[JWT_REFRESH_TOKEN_SECRET]="$SECRET_JWT_REFRESH_TOKEN_SECRET"
    REPL[EXPRESS_SESSION_SECRET]="$SECRET_EXPRESS_SESSION_SECRET"
    REPL[TOKEN_HASH_SECRET]="$SECRET_TOKEN_HASH_SECRET"

    REPL[N8N_ENCRYPTION_KEY]="$SECRET_N8N_ENCRYPTION_KEY"
    REPL[N8N_USER_MANAGEMENT_JWT_SECRET]="$SECRET_N8N_USER_MANAGEMENT_JWT_SECRET"

    REPL[NEXTAUTH_SECRET]="$SECRET_NEXTAUTH_SECRET"

    # ─── Host port resolutions (from preflight resolve_ports) ───────────────
    # If RESOLVED_PORTS is set (preflight ran), use those values. Otherwise
    # leave the defaults from .env.example untouched.
    if declare -p RESOLVED_PORTS >/dev/null 2>&1; then
        local pvar
        for pvar in "${!RESOLVED_PORTS[@]}"; do
            REPL[$pvar]="${RESOLVED_PORTS[$pvar]}"
        done
    fi

    # Walk .env.example line by line. For lines starting with KEY= where KEY
    # is in REPL, replace the value. Everything else passes through verbatim
    # (preserves comments, blank lines, and variables we don't manage like
    # POSTGRES_USER, NEO4J_AUTH expressions, port numbers, etc).
    local line key val
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)= ]]; then
            key="${BASH_REMATCH[1]}"
            if [[ -v "REPL[$key]" ]]; then
                val="${REPL[$key]}"
                # Numeric values: no quotes; everything else: quoted
                if [[ "$val" =~ ^[0-9]+$ ]]; then
                    printf '%s=%s\n' "$key" "$val"
                else
                    # Escape everything that's special inside a double-quoted
                    # shell string — in this exact order (backslash first, or
                    # the escapes added for the other three get re-escaped).
                    # Without this, a value containing e.g. $(...) would be
                    # EXECUTED the next time something does `source .env`.
                    val="${val//\\/\\\\}"
                    val="${val//\$/\\\$}"
                    val="${val//\`/\\\`}"
                    val="${val//\"/\\\"}"
                    printf '%s="%s"\n' "$key" "$val"
                fi
                continue
            fi
        fi
        printf '%s\n' "$line"
    done < "$src" > "$dst"

    chmod 600 "$dst"
    ok ".env written ($(wc -l < "$dst") lines)"
}


# ─── nginx template ──────────────────────────────────────────────────────────
# Reads nginx/smart-rag.conf (template with YOUR_DOMAIN and YOUR_LMS_DOMAIN
# placeholders) and writes the substituted result to the staging path.
# Args: $1 = repo root, $2 = output path (e.g. /etc/nginx/sites-available/smart-rag.conf)
write_nginx_config() {
    local repo="$1"
    local out="$2"
    local src="$repo/nginx/smartrag-suite.conf"

    [[ -f "$src" ]] || die "nginx template not found at $src"
    info "$(t tpl_writing_nginx "$CFG_DOMAIN")"

    backup_file "$out"

    # Extract just the hostname from CFG_LMS_URL (strip protocol)
    local lms_domain="${CFG_LMS_URL#https://}"
    lms_domain="${lms_domain#http://}"
    lms_domain="${lms_domain%%/*}"

    # Each of the template's 6 fixed "<service>.YOUR_DOMAIN" patterns is
    # replaced with its fully-resolved (prefix-aware) hostname FIRST, before
    # the generic YOUR_DOMAIN substitution runs — otherwise the later pass
    # would just re-append the bare domain to whatever these left behind.
    local prefix="${CFG_SUBDOMAIN_PREFIX:-}"
    local n8n_host; n8n_host="$(subdomain_host n8n "$CFG_DOMAIN" "$prefix")"
    # The CORS map's regex alternation escapes its dot ("n8n\.YOUR_DOMAIN")
    # on purpose — an unescaped dot in that context matches any character,
    # not just ".". Preserve the escape in the substituted value too. Needs a
    # DOUBLED backslash here: sed's own replacement-string parsing consumes
    # one level of backslash-escaping, so "\\." survives as "\." in the file.
    local n8n_host_escaped="${n8n_host/./\\\\.}"

    # __XXX_PORT__ placeholders must resolve to the ACTUAL host ports Docker
    # will bind — i.e. RESOLVED_PORTS from preflight.sh's resolve_ports(),
    # which may differ from the .env.example defaults if the wizard moved a
    # port to avoid a conflict with something already on this host. Getting
    # this wrong means nginx silently proxies to the wrong port (or to
    # whatever unrelated thing already occupied the default one) — exactly
    # the port-conflict-resolution feature failing at the one layer that's
    # actually internet-facing. Falls back to defaults if RESOLVED_PORTS
    # isn't set (e.g. this function called outside the normal wizard flow).
    local flowise_port=3000 n8n_port=5678 langfuse_port=3001
    local garage_s3_port=3900 lti_port=10088
    local content_admin_port=3002
    if declare -p RESOLVED_PORTS >/dev/null 2>&1; then
        [[ -n "${RESOLVED_PORTS[FLOWISE_PORT]:-}" ]]        && flowise_port="${RESOLVED_PORTS[FLOWISE_PORT]}"
        [[ -n "${RESOLVED_PORTS[N8N_PORT]:-}" ]]             && n8n_port="${RESOLVED_PORTS[N8N_PORT]}"
        [[ -n "${RESOLVED_PORTS[LANGFUSE_PORT]:-}" ]]        && langfuse_port="${RESOLVED_PORTS[LANGFUSE_PORT]}"
        [[ -n "${RESOLVED_PORTS[GARAGE_S3_PORT]:-}" ]]       && garage_s3_port="${RESOLVED_PORTS[GARAGE_S3_PORT]}"
        [[ -n "${RESOLVED_PORTS[LTI_PORT]:-}" ]]             && lti_port="${RESOLVED_PORTS[LTI_PORT]}"
        [[ -n "${RESOLVED_PORTS[CONTENT_ADMIN_PORT]:-}" ]]   && content_admin_port="${RESOLVED_PORTS[CONTENT_ADMIN_PORT]}"
    fi

    sed -e "s|smart-rag\.YOUR_DOMAIN|$(subdomain_host smart-rag "$CFG_DOMAIN" "$prefix")|g" \
        -e "s|n8n\.YOUR_DOMAIN|$n8n_host|g" \
        -e "s|n8n\\\\\.YOUR_DOMAIN|$n8n_host_escaped|g" \
        -e "s|langfuse\.YOUR_DOMAIN|$(subdomain_host langfuse "$CFG_DOMAIN" "$prefix")|g" \
        -e "s|s3\.YOUR_DOMAIN|$(subdomain_host s3 "$CFG_DOMAIN" "$prefix")|g" \
        -e "s|lti\.YOUR_DOMAIN|$(subdomain_host lti "$CFG_DOMAIN" "$prefix")|g" \
        -e "s|content\.YOUR_DOMAIN|$(subdomain_host content "$CFG_DOMAIN" "$prefix")|g" \
        -e "s|YOUR_DOMAIN|$CFG_DOMAIN|g" \
        -e "s|YOUR_LMS_DOMAIN|$lms_domain|g" \
        -e "s|__FLOWISE_PORT__|$flowise_port|g" \
        -e "s|__N8N_PORT__|$n8n_port|g" \
        -e "s|__LANGFUSE_PORT__|$langfuse_port|g" \
        -e "s|__GARAGE_S3_PORT__|$garage_s3_port|g" \
        -e "s|__LTI_PORT__|$lti_port|g" \
        -e "s|__CONTENT_ADMIN_PORT__|$content_admin_port|g" \
        "$src" > "$out"

    ok "nginx config written to $out"
}


# ─── Weaviate schema ─────────────────────────────────────────────────────────
# Substitutes __COLLECTION_NAME__ in weaviate/schema.json and writes the
# result to a staging path that deploy-weaviate-schema.sh will POST.
# Args: $1 = repo root, $2 = output path
# Garage needs a configuration FILE — unlike MinIO, it cannot be configured
# from the environment alone. Written here rather than shipped as a template
# with placeholders because two of its values are secrets: an rpc_secret in a
# file committed to a public repository would be a shared cluster key.
#
# db_engine sqlite and replication_factor 1 are the single-node settings.
# Garage refuses to start with a replication factor it cannot satisfy, so a
# multi-node deployment is a deliberate change here and not something that
# happens by accident.
#
# Args: $1 = output path
write_garage_config() {
    local out="$1"
    info "$(t tpl_writing_garage)"
    mkdir -p "$(dirname "$out")"
    cat > "$out" <<TOML
# Written by scripts/lib/templates.sh — regenerated on every wizard run.
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"
replication_factor = 1

rpc_bind_addr = "[::]:3901"
rpc_public_addr = "127.0.0.1:3901"
rpc_secret = "$SECRET_GARAGE_RPC_SECRET"

[s3_api]
# Must match what every client sends: clients sign requests for a region, and
# a mismatch is rejected as a signature error rather than as a wrong region.
s3_region = "${CFG_GARAGE_REGION:-eu-central-1}"
api_bind_addr = "[::]:3900"
root_domain = ".s3.garage"

[admin]
api_bind_addr = "[::]:3903"
admin_token = "$SECRET_GARAGE_ADMIN_TOKEN"
TOML
    chmod 600 "$out"
    ok "Garage configuration written to $out"
}

write_weaviate_schema() {
    local repo="$1"
    local out="$2"
    local src="$repo/weaviate/schema.json"

    [[ -f "$src" ]] || die "Weaviate schema not found at $src"
    info "$(t tpl_writing_weaviate "$CFG_WEAVIATE_COLLECTION_NAME")"

    mkdir -p "$(dirname "$out")"
    sed "s|__COLLECTION_NAME__|$CFG_WEAVIATE_COLLECTION_NAME|g" "$src" > "$out"
    ok "Weaviate schema written to $out"
}


# ─── LTI config files ────────────────────────────────────────────────────────
# Copies *.json.example → *.json if not already present.
# Args: $1 = repo root
copy_lti_configs() {
    local repo="$1"
    local config_dir="$repo/lti-middleware/config"

    info "$(t tpl_copying_lti)"

    local copied=0
    local f
    for f in "$config_dir"/*.json.example; do
        [[ -e "$f" ]] || continue
        local target="${f%.example}"
        if [[ -f "$target" ]]; then
            dim "exists, skipped: $(basename "$target")"
        else
            cp "$f" "$target"
            ok "copied: $(basename "$f") → $(basename "$target")"
            copied=$((copied+1))
        fi
    done

    if (( copied == 0 )); then
        dim "All LTI config files already present"
    fi
}
