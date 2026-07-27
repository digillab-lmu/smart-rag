# ═════════════════════════════════════════════════════════════════════════════
# preflight.sh — system checks before configuration
# ═════════════════════════════════════════════════════════════════════════════
#
# Sets global PREFLIGHT_OK / PREFLIGHT_WARN counts.
# Critical checks call die(); warnings only set the WARN counter.
# ═════════════════════════════════════════════════════════════════════════════

PREFLIGHT_WARN=0

# ─── Previous-run detection ──────────────────────────────────────────────────
# Called from bootstrap.sh before deciding whether to show the full wizard or
# offer to continue/redo an existing deployment. Echoes one of:
#   none       — no .env, this is genuinely the first run
#   configured — .env exists, but no smartrag-* containers are currently up
#   running    — .env exists AND smartrag-* containers are running
detect_bootstrap_state() {
    local repo_root="$1"
    if [[ ! -f "$repo_root/.env" ]]; then
        echo "none"
        return 0
    fi
    if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^smartrag-'; then
        echo "running"
    else
        echo "configured"
    fi
}

# ─── 0. Prerequisites checklist (human, not automated) ──────────────────────
# Shown once, right after the welcome intro and before any system checks.
# Purpose: let the user bail out cleanly BEFORE investing time in the wizard
# if they're missing something they can't check automatically (an API key,
# DNS control, an already-installed Docker). A declined checklist exits 0 —
# it's not an error, just "come back when ready".
show_prerequisites_checklist() {
    header "$(t prereq_title)"
    printf "  %s\n\n" "$(t prereq_intro)"

    printf "  ${BOLD}%s${RESET}\n" "$(t prereq_mandatory)"
    printf "    • %s\n" "$(t prereq_m1)"
    printf "    • %s\n" "$(t prereq_m2)"
    printf "    • %s\n" "$(t prereq_m3)"
    printf "    • %s\n" "$(t prereq_m4)"
    printf "    • %s\n" "$(t prereq_m5)"
    printf "    • %s\n" "$(t prereq_m6)"
    printf "    • %s\n\n" "$(t prereq_m7)"

    printf "  ${BOLD}%s${RESET}\n" "$(t prereq_optional)"
    printf "    • %s\n" "$(t prereq_o1)"
    printf "    • %s\n" "$(t prereq_o2)"
    printf "    • %s\n\n" "$(t prereq_o3)"

    if ! confirm prereq_confirm "y"; then
        info "$(t prereq_declined)"
        exit 0
    fi
}

# ─── 1. Operating System ─────────────────────────────────────────────────────
check_ubuntu() {
    local id="" version=""
    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        source /etc/os-release
        id="${ID:-}"
        version="${VERSION_ID:-}"
    fi

    if [[ "$id" != "ubuntu" ]]; then
        die "$(t pf_ubuntu_not_linux "${id:-unknown}")"
    fi

    if [[ "$version" != "24.04" ]]; then
        die "$(t pf_ubuntu_wrong "$version")"
    fi

    ok "$(t pf_ubuntu_ok "$version")"
}

# ─── 2. Root privileges ──────────────────────────────────────────────────────
check_root() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        die "$(t pf_root_needed "$(basename "${BASH_SOURCE[0]}")")"
    fi
    ok "$(t pf_root_ok)"
}

# ─── 3. Internet connectivity ────────────────────────────────────────────────
# We don't care which endpoint responds — just that *any* of these well-known
# liveness URLs is reachable. Docker Hub's registry root returns 401 without
# auth headers, which would trip `curl -f`. So we use clear "I'm alive" endpoints
# instead, and check connection success (not HTTP status).
check_internet() {
    local targets=(
        "https://www.google.com/generate_204"     # → 204 No Content
        "https://cloudflare.com/cdn-cgi/trace"    # → 200 plain text
        "https://1.1.1.1/"                        # → 200 (Cloudflare DNS site)
    )
    local t
    for t in "${targets[@]}"; do
        # -s = silent, -o /dev/null = drop body, --max-time 5 = give up fast.
        # No -f: any HTTP response (even 4xx/5xx) proves the network works.
        if curl -sS -o /dev/null --max-time 5 "$t" 2>/dev/null; then
            ok "$(t pf_internet_ok)"
            return 0
        fi
    done
    die "$(t pf_internet_fail "${targets[*]}")"
}

# ─── 4. Docker ───────────────────────────────────────────────────────────────
check_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        die "$(t pf_docker_missing)"
    fi
    local version
    version="$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
    ok "$(t pf_docker_ok "${version:-?}")"
}

# ─── 5. Docker Compose v2 plugin ─────────────────────────────────────────────
check_docker_compose() {
    if ! docker compose version >/dev/null 2>&1; then
        die "$(t pf_compose_missing)"
    fi
    ok "$(t pf_compose_ok)"
}

# ─── 5b. jq (needed early — the wizard validates model names against
#          provider APIs before install-system-packages.sh would install it) ──
ensure_jq_installed() {
    if command -v jq >/dev/null 2>&1; then
        ok "$(t pf_jq_ok)"
        return 0
    fi
    info "$(t pf_jq_installing)"
    DEBIAN_FRONTEND=noninteractive apt-get update -q >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y jq >/dev/null 2>&1
    if command -v jq >/dev/null 2>&1; then
        ok "$(t pf_jq_ok)"
    else
        warn "$(t pf_jq_failed)"
        PREFLIGHT_WARN=$((PREFLIGHT_WARN+1))
    fi
}

# ─── 6. Disk space (warning only) ────────────────────────────────────────────
# Args: $1 = path to check (e.g. /srv). If empty: defaults to /.
check_disk_space() {
    local path="${1:-/}"
    # df -BG reports GiB; strip the trailing G
    local free_gb
    free_gb="$(df -BG --output=avail "$path" 2>/dev/null | tail -1 | tr -d ' G')"
    if [[ -z "$free_gb" || ! "$free_gb" =~ ^[0-9]+$ ]]; then
        warn "Could not determine disk space for $path"
        PREFLIGHT_WARN=$((PREFLIGHT_WARN+1))
        return 0
    fi
    if (( free_gb < 20 )); then
        warn "$(t pf_disk_low "$path" "$free_gb")"
        PREFLIGHT_WARN=$((PREFLIGHT_WARN+1))
    else
        ok "$(t pf_disk_ok "$free_gb" "$path")"
    fi
}

# Forward-resolve a domain's A record to an IP. Tries dig → host → python3,
# same fallback chain as the PTR lookup below, for consistency.
_forward_resolve_a() {
    local domain="$1"
    if command -v dig &>/dev/null; then
        dig +short A "$domain" 2>/dev/null | head -1
    elif command -v host &>/dev/null; then
        host -t A "$domain" 2>/dev/null | awk '/has address/ {print $NF; exit}'
    elif command -v python3 &>/dev/null; then
        python3 -c "import socket; print(socket.gethostbyname('$domain'))" 2>/dev/null || true
    fi
}

# ─── 7a. Detect base domain from reverse DNS ─────────────────────────────────
# Returns the PTR record for the server's public IP, stripped to the registrable
# base domain (last two labels) — but ONLY if that base domain's own A record
# round-trips back to this server's IP. Cloud/VPS providers almost always set
# a generic PTR (e.g. "pbiaas.com" for IONOS, "compute.amazonaws.com" for AWS)
# that has nothing to do with the domain actually being used for this
# deployment — suggesting it unconditionally is actively misleading. Falls
# back to empty string (no suggestion) whenever detection or verification
# fails; callers should always let the user confirm/override the result.
detect_base_domain() {
    local pub_ip ptr_record base_domain forward_ip
    pub_ip="$(curl -sf --max-time 5 https://api.ipify.org 2>/dev/null || true)"
    [[ -z "$pub_ip" ]] && return 0

    if command -v dig &>/dev/null; then
        ptr_record="$(dig +short -x "$pub_ip" 2>/dev/null | sed 's/\.$//' | head -1)"
    elif command -v host &>/dev/null; then
        ptr_record="$(host "$pub_ip" 2>/dev/null \
            | awk '/domain name pointer/ {sub(/\.$/, "", $NF); print $NF; exit}')"
    elif command -v python3 &>/dev/null; then
        ptr_record="$(python3 -c \
            "import socket; print(socket.gethostbyaddr('$pub_ip')[0])" 2>/dev/null || true)"
    fi

    [[ -z "$ptr_record" ]] && return 0
    # Validate: must look like a proper FQDN (has at least one dot, all valid chars)
    if [[ "$ptr_record" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]+\.[a-zA-Z]{2,}$ ]]; then
        # Strip to last two labels — e.g. "static.123.duenn-mit-pfiff.de" → "duenn-mit-pfiff.de"
        base_domain="$(echo "$ptr_record" | awk -F'.' '{print $(NF-1)"."$NF}')"

        # Round-trip check: only suggest it if the base domain's own A record
        # actually points back at this server's public IP.
        forward_ip="$(_forward_resolve_a "$base_domain")"
        if [[ -n "$forward_ip" && "$forward_ip" == "$pub_ip" ]]; then
            echo "$base_domain"
        fi
    fi
}

# ─── 7b. DNS check (warning only) ────────────────────────────────────────────
# Args: $1 = domain (e.g. smart-rag.example.com)
# Checks: does the domain resolve, and to this server?
check_dns() {
    local domain="${1:-}"
    if [[ -z "$domain" ]]; then
        info "$(t pf_dns_skip)"
        return 0
    fi
    # Skip if dig isn't available — we'll re-check in get-ssl-certs.sh
    if ! command -v dig >/dev/null 2>&1; then
        warn "dig not installed — skipping DNS check. Install with: apt install dnsutils"
        PREFLIGHT_WARN=$((PREFLIGHT_WARN+1))
        return 0
    fi

    local dns_ip server_ip
    dns_ip="$(dig +short A "$domain" 2>/dev/null | head -1)"
    server_ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo "")"

    if [[ -z "$dns_ip" ]]; then
        warn "$(t pf_dns_nores "$domain")"
        PREFLIGHT_WARN=$((PREFLIGHT_WARN+1))
        return 0
    fi
    if [[ -n "$server_ip" && "$dns_ip" != "$server_ip" ]]; then
        warn "$(t pf_dns_mismatch "$domain" "$dns_ip" "$server_ip")"
        PREFLIGHT_WARN=$((PREFLIGHT_WARN+1))
        return 0
    fi
    ok "$(t pf_dns_ok "$domain" "$dns_ip")"
}

# ─── 8a. Low-level port helpers ──────────────────────────────────────────────

# is_port_free PORT → exit 0 if no listener, 1 otherwise.
#
# Three detection paths in order of preference:
#   1. ss   (part of iproute2, always on Ubuntu — gives best info)
#   2. lsof (commonly available)
#   3. bash's /dev/tcp pseudo-device — actually OPENS a TCP connection to
#      127.0.0.1:PORT. If the connection succeeds, something is listening
#      (port taken). If it's refused, port is free. This is a bash built-in
#      and is ALWAYS available — no external tool required.
#
# We never "assume free" — blind-flight is too risky.
is_port_free() {
    local port="$1"

    if command -v ss >/dev/null 2>&1; then
        ! ss -tlnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$port\$"
        return $?
    fi

    if command -v lsof >/dev/null 2>&1; then
        ! lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1
        return $?
    fi

    # Fallback — pure bash, no external tools needed.
    # `timeout 1` guards against the (rare) case of a firewall that drops
    # SYN packets on localhost without RST'ing them.
    if timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null; then
        return 1   # connection succeeded → port is in use
    fi
    return 0       # connection refused → port is free
}

# port_owner PORT → echoes a short description of the process holding the port
# (e.g. "users:((\"docker-proxy\",pid=1234,fd=8))"), or empty if unknown.
port_owner() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -tlnpH 2>/dev/null \
            | awk -v p="$port" '$4 ~ ":"p"$" {print $NF; exit}' \
            | sed -E 's/users:\(\("([^"]+)".*/\1/'
    fi
}

# find_free_port START [TAKEN ...] → echoes first free port >= START,
# also avoiding any in TAKEN (our already-assigned ports for this run).
find_free_port() {
    local start="$1"; shift
    local taken=("$@")
    local p t
    for (( p=start; p<65000; p++ )); do
        local skip=0
        for t in "${taken[@]}"; do
            [[ "$t" == "$p" ]] && { skip=1; break; }
        done
        (( skip )) && continue
        if is_port_free "$p"; then
            printf '%d' "$p"
            return 0
        fi
    done
    return 1
}


# ─── 8b. Port resolution (replaces old check_host_ports) ─────────────────────
#
# For each requested port, try the default. If it's taken, find the next free
# alternative and remember it. Doesn't die on conflict — just records the
# resolutions. The caller can then ask the user to confirm.
#
# Args: $@ = list of "VARNAME=PORT:label" entries
#       e.g.  "FLOWISE_PORT=3000:Flowise"
#
# Sets these global associative arrays:
#   RESOLVED_PORTS[VARNAME] = final port (the default OR the alternative)
#   PORT_CONFLICTS[VARNAME] = "original:alternative:owner:label"  (only for conflicts)
resolve_ports() {
    declare -gA RESOLVED_PORTS=()
    declare -gA PORT_CONFLICTS=()
    local entries=("$@")

    info "$(t pf_port_check)"

    # Track ports we've already assigned this run so we don't double-allocate
    local assigned=()
    local entry varname rest default label owner alt

    for entry in "${entries[@]}"; do
        varname="${entry%%=*}"; rest="${entry#*=}"
        default="${rest%%:*}"
        label="${rest#*:}"; [[ "$label" == "$default" ]] && label=""

        if is_port_free "$default" && ! printf '%s\n' "${assigned[@]}" | grep -qx "$default"; then
            ok "$(t pf_port_ok "$default" "$label")"
            RESOLVED_PORTS[$varname]="$default"
            assigned+=("$default")
            continue
        fi

        # Default is taken — find an alternative
        owner="$(port_owner "$default")"
        [[ -z "$owner" ]] && owner="unknown"
        alt="$(find_free_port $((default + 100)) "${assigned[@]}")" || true
        if [[ -z "$alt" ]]; then
            die "$(t pf_port_no_alternative "$default" "65000" "$label")"
        fi
        warn "$(t pf_port_conflict "$default" "$label" "$owner" "$alt")"
        RESOLVED_PORTS[$varname]="$alt"
        PORT_CONFLICTS[$varname]="$default:$alt:$owner:$label"
        assigned+=("$alt")
    done
}


# Show a summary of any port reassignments and ask the user to confirm.
# Returns 0 = approved, 1 = user cancelled (caller decides what to do).
confirm_port_resolutions() {
    if (( ${#PORT_CONFLICTS[@]} == 0 )); then
        ok "$(t pf_port_all_free)"
        return 0
    fi

    header "$(t pf_port_section_resolutions)"
    printf "%s\n\n" "$(t pf_port_summary_intro)"

    local varname rest original alternative owner label
    # Direct iteration over keys — no process substitution (avoids subshell
    # corner-cases when bootstrap.sh is fed via piped stdin in tests).
    local sorted_keys=()
    mapfile -t sorted_keys < <(printf '%s\n' "${!PORT_CONFLICTS[@]}" | sort)
    for varname in "${sorted_keys[@]}"; do
        rest="${PORT_CONFLICTS[$varname]}"
        original="${rest%%:*}"; rest="${rest#*:}"
        alternative="${rest%%:*}"; rest="${rest#*:}"
        owner="${rest%%:*}"; rest="${rest#*:}"
        label="$rest"
        # Print directly — the format string is fixed and doesn't need
        # printf-on-printf through t().
        printf "  %-25s  %5d → %5d   (was: %s)\n" \
            "$varname" "$original" "$alternative" "$owner"
    done
    echo

    if confirm pf_port_confirm "y"; then
        return 0
    fi
    die "$(t pf_port_cancelled)"
}

# ─── 9. nginx subdomain collision (non-fatal — caller decides) ──────────────
# Scans all enabled nginx sites for `server_name` directives that overlap with
# the subdomains we plan to use. Returns 1 on conflict instead of dying —
# resolve_subdomain_prefix() below is the caller that decides what to do
# about it (offer a shared prefix and retry, or give up and abort).
# Args: $@ = list of FQDNs we plan to claim (e.g. smart-rag.example.com)
check_nginx_subdomains() {
    local subdomains=("$@")
    local sites_dir="${NGINX_SITES_DIR:-/etc/nginx/sites-enabled}"

    if [[ ! -d "$sites_dir" ]]; then
        # nginx not yet installed → no conflict possible
        return 0
    fi

    local conflicts=0
    local site sub
    # Collect all server_name lines once
    declare -A SERVER_NAMES   # site_path → "name1 name2 ..."
    for site in "$sites_dir"/*; do
        # Skip if no files (glob remained literal)
        [[ -e "$site" ]] || continue
        # Skip our own files
        case "$(basename "$site")" in
            smartrag-*) continue ;;
        esac
        # Extract server_name directives (ignore comments + multi-line)
        local names
        names="$(awk '
            /^[[:space:]]*server_name[[:space:]]/{
                $1=""; sub(/;.*/, ""); print
            }' "$site" 2>/dev/null | tr -s '[:space:]' ' ')"
        SERVER_NAMES["$site"]="$names"
    done

    for sub in "${subdomains[@]}"; do
        for site in "${!SERVER_NAMES[@]}"; do
            # word-boundary match: " sub " in " name1 name2 sub name3 "
            if [[ " ${SERVER_NAMES[$site]} " == *" $sub "* ]]; then
                err "$(t pf_subdom_conflict "$(basename "$site")" "$sub")"
                conflicts=$((conflicts+1))
            fi
        done
    done

    (( conflicts > 0 )) && return 1
    return 0
}

# Determines the final subdomain prefix (possibly empty) for this deployment.
# Tries with no prefix first — the common case on a dedicated/clean server,
# and backward-compatible with existing deployments. If any of our subdomains
# collide with something already configured in nginx (e.g. a standalone n8n
# already running on this host), offers ONE shared prefix applied to every
# one of our subdomains — mirrors resolve_ports()'s auto-resolve-on-conflict
# approach — and retries, up to a few attempts before giving up.
# Args: $1=domain  $2...=service labels (e.g. smart-rag n8n minio s3 [langfuse] [lti])
# Echoes the final prefix (possibly "").
resolve_subdomain_prefix() {
    local domain="$1"; shift
    local services=("$@")
    local prefix="" attempt subdomains s

    # This function's stdout IS the return value (captured by the caller via
    # command substitution) — every status message must go to stderr, or it
    # ends up concatenated into CFG_SUBDOMAIN_PREFIX.
    info "$(t pf_subdom_check)" >&2
    for attempt in 1 2 3; do
        subdomains=()
        for s in "${services[@]}"; do
            subdomains+=("$(subdomain_host "$s" "$domain" "$prefix")")
        done

        if check_nginx_subdomains "${subdomains[@]}"; then
            ok "$(t pf_subdom_ok)" >&2
            printf '%s' "$prefix"
            return 0
        fi

        if (( attempt == 3 )); then
            die "$(t pf_subdom_abort)"
        fi

        warn "$(t pf_subdom_prefix_needed)" >&2
        prefix="$(prompt pf_subdom_prefix_prompt "${prefix:-smartrag}" validate_slug)" \
            || die "$(t cfg_aborted)"
    done
}

# ─── 9b. Existing mail relay detection ───────────────────────────────────────
# Called from ask_mail_config() (config-wizard.sh) BEFORE offering to set up
# a new relay — a server may already run Postfix/Exim/etc. for unrelated
# reasons, and blindly offering a fresh Postfix install ignores that.
# Echoes "MTA_PACKAGE:PORT25_LISTENING" — MTA_PACKAGE is "none" if no known
# MTA package is installed, PORT25_LISTENING is 1/0.
detect_existing_mail_relay() {
    local mta="none" pkg
    for pkg in postfix exim4 sendmail msmtp ssmtp; do
        if dpkg -s "$pkg" >/dev/null 2>&1; then
            mta="$pkg"
            break
        fi
    done

    local port_listening=0
    if ! is_port_free 25 2>/dev/null; then
        port_listening=1
    fi

    printf '%s:%s' "$mta" "$port_listening"
}

# ─── 10. Let's Encrypt existing-cert check (warning only) ────────────────────
# Args: $1 = our cert name (e.g. smartrag-example.com)
check_existing_certs() {
    local cert_name="$1"
    if [[ ! -d /etc/letsencrypt/live ]]; then
        return 0
    fi
    info "$(t pf_cert_check)"
    if [[ -d "/etc/letsencrypt/live/$cert_name" ]]; then
        warn "$(t pf_cert_exists "$cert_name")"
    else
        ok "$(t pf_cert_clean)"
    fi
}

# ─── 11. BASE_DATA_PATH non-empty check (interactive) ────────────────────────
# If the data path already contains files that aren't from a previous SMART RAG
# run, ask the user before proceeding.
# Args: $1 = path
check_base_data_path() {
    local path="$1"
    info "$(t pf_data_path_check)"
    if [[ ! -d "$path" ]] || [[ -z "$(ls -A "$path" 2>/dev/null)" ]]; then
        ok "$(t pf_data_path_empty "$path")"
        return 0
    fi

    # Detect SMART RAG markers (subdirs we create)
    local known_subdirs=(postgres redis neo4j weaviate minio clickhouse flowise n8n langfuse staging)
    local foreign_items=()
    local item base
    for item in "$path"/*; do
        [[ -e "$item" ]] || continue
        base="$(basename "$item")"
        local known=0
        local s
        for s in "${known_subdirs[@]}"; do
            [[ "$base" == "$s" ]] && { known=1; break; }
        done
        (( known )) || foreign_items+=("$base")
    done

    if (( ${#foreign_items[@]} == 0 )); then
        ok "$(t pf_data_path_smartrag "$path")"
        return 0
    fi

    warn "$(t pf_data_path_foreign "$path" "${foreign_items[*]:0:5}")"
    if ! confirm pf_data_path_confirm "n"; then
        die "Aborted by user."
    fi
}

# ─── 12. nginx config validity (warning only) ────────────────────────────────
# Runs `nginx -t` against the existing config. If it's broken, we should know
# BEFORE we touch anything, because our reload would also fail.
check_nginx_config_valid() {
    if ! command -v nginx >/dev/null 2>&1; then
        return 0   # nginx not installed yet, will be installed in phase 5
    fi
    if nginx -t >/dev/null 2>&1; then
        if systemctl is-active --quiet nginx; then
            ok "$(t pf_nginx_running)"
        else
            info "$(t pf_nginx_not_running)"
        fi
    else
        nginx -t 2>&1 | sed 's/^/    /'
        die "$(t pf_nginx_config_test)"
    fi
}


# ─── Master runner — phase 1 (basic system checks only) ──────────────────────
# Critical checks come first (will die() on failure).
# Disk + DNS are warnings only.
# Coexistence checks (ports, subdomains, certs) are done in run_coexistence_preflight()
# *after* the wizard has gathered domain + port info.
run_preflight() {
    check_ubuntu
    check_root
    check_internet
    check_docker
    check_docker_compose
    check_disk_space "/"
    ensure_jq_installed
}

# ─── Master runner — phase 2 (coexistence checks) ────────────────────────────
# Called after the config wizard, when we know which domain and ports to check.
# Expects globals from .env to be sourced:
#   DOMAIN, BASE_DATA_PATH, COMPOSE_PROFILES,
#   FLOWISE_PORT, N8N_PORT, LANGFUSE_PORT, MINIO_API_PORT, MINIO_CONSOLE_PORT, LTI_PORT,
#   NEO4J_HTTP_PORT, NEO4J_BOLT_PORT, WEAVIATE_HTTP_PORT, WEAVIATE_GRPC_PORT
#
# After this runs, RESOLVED_PORTS[VARNAME] contains the final port for each
# (= default if free, = an alternative if the default was taken).
run_coexistence_preflight() {
    header "$(t pf_section_coexist)"

    # Build the list of SERVICE LABELS (not yet combined with domain/prefix —
    # resolve_subdomain_prefix() does that, retrying with a shared prefix if
    # the unprefixed names collide with something already on this host).
    local services=(smart-rag n8n minio s3)
    [[ "${COMPOSE_PROFILES:-core}" == *observability* ]] && services+=(langfuse)
    [[ "${COMPOSE_PROFILES:-core}" == *lti*           ]] && services+=(lti)

    # Build host-port list — only the ones we actually bind to the host.
    # Format: VARNAME=DEFAULT_PORT:LABEL
    local port_entries=(
        "FLOWISE_PORT=${FLOWISE_PORT:-3000}:Flowise"
        "N8N_PORT=${N8N_PORT:-5678}:n8n"
        "WEAVIATE_HTTP_PORT=${WEAVIATE_HTTP_PORT:-8080}:Weaviate HTTP"
        "WEAVIATE_GRPC_PORT=${WEAVIATE_GRPC_PORT:-50051}:Weaviate gRPC"
        "NEO4J_HTTP_PORT=${NEO4J_HTTP_PORT:-7474}:Neo4j HTTP"
        "NEO4J_BOLT_PORT=${NEO4J_BOLT_PORT:-7687}:Neo4j Bolt"
        "MINIO_API_PORT=${MINIO_API_PORT:-9000}:MinIO API"
        "MINIO_CONSOLE_PORT=${MINIO_CONSOLE_PORT:-9001}:MinIO Console"
    )
    [[ "${COMPOSE_PROFILES:-core}" == *observability* ]] && \
        port_entries+=("LANGFUSE_PORT=${LANGFUSE_PORT:-3001}:Langfuse")
    [[ "${COMPOSE_PROFILES:-core}" == *lti*           ]] && \
        port_entries+=("LTI_PORT=${LTI_PORT:-10088}:LTI middleware")

    # Auto-resolve any port conflicts, then ask user to confirm
    resolve_ports "${port_entries[@]}"
    confirm_port_resolutions

    check_nginx_config_valid
    CFG_SUBDOMAIN_PREFIX="$(resolve_subdomain_prefix "$DOMAIN" "${services[@]}")"
    export CFG_SUBDOMAIN_PREFIX
    check_existing_certs    "smartrag-$DOMAIN"
    check_base_data_path    "${BASE_DATA_PATH:-/srv/smart-rag}"
}
