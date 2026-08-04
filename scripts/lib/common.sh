# ═════════════════════════════════════════════════════════════════════════════
# common.sh — shared utilities (logging, prompts, helpers)
# ═════════════════════════════════════════════════════════════════════════════
#
# Sourced by bootstrap.sh and other scripts. Do not run directly.
# Conventions:
#   - All user-facing strings go through t() from messages.sh
#   - All output to terminal AND to $LOG_FILE (if set)
#   - Functions return non-zero on failure; bootstrap.sh decides whether to die
# ═════════════════════════════════════════════════════════════════════════════

# ─── Colors ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'
    YELLOW=$'\033[1;33m'
    GREEN=$'\033[0;32m'
    BLUE=$'\033[0;34m'
    CYAN=$'\033[0;36m'
    BOLD=$'\033[1m'
    DIM=$'\033[2m'
    RESET=$'\033[0m'
else
    RED='' YELLOW='' GREEN='' BLUE='' CYAN='' BOLD='' DIM='' RESET=''
fi

# ─── Exit codes ──────────────────────────────────────────────────────────────
# A phase that could not run yet — not a failure, but not a success either.
# Distinct from 0 so an orchestrating script can report an incomplete setup
# instead of a clean finish, and from 1 so `set -e` handling stays honest
# about what actually broke. Well outside the shell's own reserved range
# (126/127/128+n).
readonly EXIT_SKIPPED=10

# ─── Log file (set by bootstrap.sh; may be empty) ─────────────────────────────
LOG_FILE="${LOG_FILE:-}"

_log_to_file() {
    [[ -n "$LOG_FILE" ]] || return 0
    local ts; ts="$(date -Iseconds 2>/dev/null || date)"
    printf "%s  %s\n" "$ts" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

# ─── Log primitives ──────────────────────────────────────────────────────────
ok()    { printf "  ${GREEN}✓${RESET} %s\n" "$*"; _log_to_file "OK  $*"; }
warn()  { printf "  ${YELLOW}⚠${RESET}  %s\n" "$*"; _log_to_file "WARN $*"; }
err()   { printf "  ${RED}✗${RESET} %s\n" "$*" >&2; _log_to_file "ERR  $*"; }
info()  { printf "  ${CYAN}ℹ${RESET}  %s\n" "$*"; _log_to_file "INFO $*"; }
dim()   { printf "  ${DIM}%s${RESET}\n" "$*"; _log_to_file "DIM  $*"; }

die() {
    err "$@"
    printf "\n${RED}${BOLD}Bootstrap aborted.${RESET}\n"
    [[ -n "$LOG_FILE" ]] && printf "Full log: %s\n" "$LOG_FILE"
    exit 1
}

# ─── Headers ─────────────────────────────────────────────────────────────────
header() {
    printf "\n${BOLD}${BLUE}━━━ %s ━━━${RESET}\n" "$*"
    _log_to_file "HEADER $*"
}

# Box width adapts to content so long lines (e.g. the license line) never
# get clipped or need manual re-counting of ASCII art characters.
banner() {
    local lines=(
        "S M A R T   R A G"
        "Shared Memory Agent-Based Retrieval for Teaching"
        ""
        "Entwicklung: DigiLLab LMU München 2026"
        "Lizenz: PolyForm Noncommercial 1.0.0"
    )

    local width=0 l
    for l in "${lines[@]}"; do
        (( ${#l} > width )) && width=${#l}
    done
    width=$((width + 4))   # 2 spaces padding on each side

    local border; border="$(printf '─%.0s' $(seq 1 "$width"))"

    printf "\n${BOLD}${CYAN}"
    printf "┌%s┐\n" "$border"
    local pad_total pad_left pad_right
    for l in "${lines[@]}"; do
        pad_total=$((width - ${#l}))
        pad_left=$((pad_total / 2))
        pad_right=$((pad_total - pad_left))
        printf "│%*s%s%*s│\n" "$pad_left" "" "$l" "$pad_right" ""
    done
    printf "└%s┘\n" "$border"
    printf "${RESET}\n"
}

# ─── Interactive prompts ─────────────────────────────────────────────────────

# Back-navigation: any prompt/select_one/confirm/prompt_password accepts the
# literal input "back", "b", "zurück", or "z" (case-insensitive) instead of a
# value. When detected, the primitive sets WIZARD_BACK=1 and returns 1 with
# no output. Callers chain `|| return 1` to bubble the signal up to the
# section function, which run_config_wizard's step loop catches to step back
# one section. WIZARD_BACK is reset at the start of every primitive call so
# a stale flag can never leak into an unrelated later prompt.
WIZARD_BACK=0
_is_back_input() {
    case "${1,,}" in
        back|b|zurück|zurueck|z) return 0 ;;
        *) return 1 ;;
    esac
}

# Clean-exit-from-anywhere: "exit"/"quit"/"beenden"/"abbrechen" at any prompt
# terminates the whole script immediately via die() — unlike back-navigation,
# this needs no propagation through callers (die() never returns), so it's
# checked and handled entirely inside the 4 primitives below, no other file
# needs to know about it.
_is_exit_input() {
    case "${1,,}" in
        exit|quit|beenden|abbrechen) return 0 ;;
        *) return 1 ;;
    esac
}

# prompt KEY DEFAULT [VALIDATOR]  →  echoes user input
# KEY:        i18n message key (the question)
# DEFAULT:    default value if user just hits enter (may be empty)
# VALIDATOR:  optional function name. If set, called with $1=value; must return 0 to accept.
prompt() {
    local key="$1" default="${2:-}" validator="${3:-}"
    local question; question="$(t "$key")"
    local input
    WIZARD_BACK=0

    # IMPORTANT: All prompt output goes to stderr so that command substitution
    # ($(prompt KEY)) captures ONLY the user's input, not the question text.
    while true; do
        if [[ -n "$default" ]]; then
            printf "  %s ${DIM}[%s]${RESET}: " "$question" "$default" >&2
        else
            printf "  %s: " "$question" >&2
        fi
        IFS= read -r input
        _is_exit_input "$input" && die "$(t wizard_exit)"
        if _is_back_input "$input"; then
            WIZARD_BACK=1
            return 1
        fi
        input="${input:-$default}"
        if [[ -z "$input" ]]; then
            err "$(t value_required)"
            continue
        fi
        if [[ -n "$validator" ]] && ! "$validator" "$input"; then
            continue
        fi
        printf '%s' "$input"
        return 0
    done
}

# prompt_password KEY [DEFAULT]  →  hides input, no validation
prompt_password() {
    local key="$1" default="${2:-}"
    local question; question="$(t "$key")"
    local input
    WIZARD_BACK=0
    if [[ -n "$default" ]]; then
        printf "  %s ${DIM}[%s]${RESET}: " "$question" "$default" >&2
    else
        printf "  %s: " "$question" >&2
    fi
    IFS= read -rs input
    printf "\n" >&2
    _is_exit_input "$input" && die "$(t wizard_exit)"
    if _is_back_input "$input"; then
        WIZARD_BACK=1
        return 1
    fi
    printf '%s' "${input:-$default}"
}

# confirm KEY [DEFAULT]  →  returns 0=yes, 1=no (or back — check WIZARD_BACK)
# DEFAULT: "y" or "n" (default "n")
confirm() {
    local key="$1" default="${2:-n}"
    local question; question="$(t "$key")"
    local suffix
    if [[ "$default" == "y" ]]; then suffix="${BOLD}Y${RESET}/n"; else suffix="y/${BOLD}N${RESET}"; fi
    local input
    WIZARD_BACK=0
    while true; do
        printf "  %s [%s]: " "$question" "$suffix" >&2
        IFS= read -r input
        _is_exit_input "$input" && die "$(t wizard_exit)"
        if _is_back_input "$input"; then
            WIZARD_BACK=1
            return 1
        fi
        input="${input:-$default}"
        case "${input,,}" in
            y|yes|j|ja)  return 0 ;;
            n|no|nein)   return 1 ;;
            *) err "$(t invalid_yn)" ;;
        esac
    done
}

# select_one_index KEY OPTION1 OPTION2 ... → echoes 1-based index, returns 0
# Use this when you need language-neutral identity (case-switch on a number).
select_one_index() {
    local key="$1"; shift
    local options=("$@")
    local prompt_text; prompt_text="$(t "$key")"
    local i input
    WIZARD_BACK=0
    printf "  ${BOLD}%s${RESET}\n" "$prompt_text" >&2
    for i in "${!options[@]}"; do
        printf "    ${BOLD}[%d]${RESET}  %s\n" $((i+1)) "${options[$i]}" >&2
    done
    while true; do
        printf "  ${DIM}%s${RESET} ${BOLD}[1]${RESET}: " "$(t enter_choice)" >&2
        IFS= read -r input
        _is_exit_input "$input" && die "$(t wizard_exit)"
        if _is_back_input "$input"; then
            WIZARD_BACK=1
            return 1
        fi
        input="${input:-1}"
        if [[ "$input" =~ ^[0-9]+$ ]] && (( input >= 1 && input <= ${#options[@]} )); then
            printf '%s' "$input"
            return 0
        fi
        err "$(t invalid_choice)"
    done
}

# select_one KEY OPTION1 OPTION2 ... → echoes selected option text, returns 0
# Use this when the option text IS the value you want (e.g. "anthropic", "openai").
select_one() {
    local key="$1"; shift
    local options=("$@")
    local idx; idx="$(select_one_index "$key" "${options[@]}")" || return 1
    printf '%s' "${options[$((idx-1))]}"
}

# ─── Path helpers ────────────────────────────────────────────────────────────

# Returns the repo root (parent of scripts/)
repo_root() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
    # walk up until we find .env.example
    local dir="$script_dir"
    while [[ "$dir" != "/" ]]; do
        if [[ -f "$dir/.env.example" ]]; then echo "$dir"; return 0; fi
        dir="$(dirname "$dir")"
    done
    die "Could not locate repo root (no .env.example found)"
}

# Backup a file if it exists, with timestamp
backup_file() {
    local f="$1"
    [[ -f "$f" ]] || return 0
    local ts; ts="$(date +%Y%m%dT%H%M%S)"
    local backup="${f}.backup-${ts}"
    cp -p "$f" "$backup"
    dim "Backed up: $(basename "$f") → $(basename "$backup")"
}

# Require a command to exist on PATH
require_command() {
    local cmd="$1"
    command -v "$cmd" >/dev/null 2>&1 || die "Required command not found: $cmd"
}

# Safely patches a single KEY="VALUE" line in-place inside an .env file,
# preserving every other line's position — some values interpolate earlier
# ones (e.g. NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}"), so moving lines around
# would break sourcing. Appends a new KEY="VALUE" line at the end if the key
# isn't present yet. Backs up the file first (see backup_file() above).
#
# Escaping matches templates.sh::write_env_file() exactly, in the same
# order (backslash first, or the escapes added for the other three get
# re-escaped) — without this, a value containing e.g. $(...) would be
# EXECUTED the next time something does `source .env`.
#
# Args: $1 = path to .env  $2 = KEY  $3 = new value (unescaped, as typed)
set_env_var() {
    local env_file="$1" key="$2" val="$3"
    [[ -f "$env_file" ]] || die "set_env_var: $env_file not found"

    val="${val//\\/\\\\}"
    val="${val//\$/\\\$}"
    val="${val//\`/\\\`}"
    val="${val//\"/\\\"}"

    backup_file "$env_file"

    local tmp found=0 line
    tmp="$(mktemp)"
    chmod 600 "$tmp"
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" == "${key}="* ]]; then
            printf '%s="%s"\n' "$key" "$val" >> "$tmp"
            found=1
        else
            printf '%s\n' "$line" >> "$tmp"
        fi
    done < "$env_file"
    (( found )) || printf '%s="%s"\n' "$key" "$val" >> "$tmp"

    mv "$tmp" "$env_file"
}

# Computes the actual hostname for one of our services, honoring an optional
# shared subdomain prefix (see resolve_subdomain_prefix() in preflight.sh —
# only set to non-empty when the default unprefixed names collided with
# something already on the host, e.g. an existing standalone n8n).
# Args: $1=service label (e.g. "smart-rag", "n8n")  $2=base domain  $3=prefix (may be empty)
subdomain_host() {
    local service="$1" domain="$2" prefix="$3"
    if [[ -n "$prefix" ]]; then
        printf '%s-%s.%s' "$prefix" "$service" "$domain"
    else
        printf '%s.%s' "$service" "$domain"
    fi
}

# Detects this machine's public IP via api.ipify.org. Echoes the IP, or
# nothing on failure (network down, service unreachable) — callers should
# treat an empty result as "couldn't detect", not as an error to die() on
# except where the IP is genuinely required (e.g. get-ssl-certs.sh).
detect_public_ip() {
    curl -sf --max-time 5 https://api.ipify.org 2>/dev/null || true
}

# Percent-encode a string for safe embedding in a URL (e.g. an SMTP password
# that may contain ':', '@', '/'). ASCII-only — sufficient for SMTP passwords.
url_encode() {
    local s="$1" out="" c i hex
    for (( i=0; i<${#s}; i++ )); do
        c="${s:i:1}"
        case "$c" in
            [a-zA-Z0-9.~_-]) out+="$c" ;;
            *) printf -v hex '%%%02X' "'$c"; out+="$hex" ;;
        esac
    done
    printf '%s' "$out"
}

# ─── System snapshot ─────────────────────────────────────────────────────────
# Captures the current state of nginx config, running Docker containers, and
# listening ports — BEFORE we make any destructive changes. Stored at:
#   /var/backups/smartrag-pre-bootstrap-<timestamp>/
#
# Cheap insurance: lets the operator diff against the snapshot or quickly
# restore nginx config if something goes wrong.
#
# Returns the snapshot directory path via SNAPSHOT_DIR global.
create_system_snapshot() {
    info "$(t snap_creating)"
    local ts; ts="$(date +%Y%m%dT%H%M%S)"
    SNAPSHOT_DIR="/var/backups/smartrag-pre-bootstrap-$ts"
    mkdir -p "$SNAPSHOT_DIR"
    chmod 700 "$SNAPSHOT_DIR"

    # 1. nginx config — only if nginx is installed (otherwise nothing to snapshot)
    if [[ -d /etc/nginx ]]; then
        tar czf "$SNAPSHOT_DIR/nginx.tar.gz" -C / etc/nginx 2>/dev/null
        dim "$(t snap_nginx)"
    fi

    # 2. Docker container list (names + images + status) — only if docker present
    if command -v docker >/dev/null 2>&1; then
        docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' \
            > "$SNAPSHOT_DIR/docker-ps.txt" 2>/dev/null || true
        dim "$(t snap_docker)"
    fi

    # 3. Listening ports — gives us a clean reference point
    if command -v ss >/dev/null 2>&1; then
        ss -tlnp > "$SNAPSHOT_DIR/listening-ports.txt" 2>/dev/null || true
        dim "$(t snap_ports)"
    fi

    # 4. Let's Encrypt cert metadata (NOT the certs themselves — those are
    #    already in /etc/letsencrypt and we never touch foreign ones)
    if command -v certbot >/dev/null 2>&1; then
        certbot certificates > "$SNAPSHOT_DIR/letsencrypt-certs.txt" 2>/dev/null || true
        dim "$(t snap_letsencrypt)"
    fi

    chmod 600 "$SNAPSHOT_DIR"/*
    ok "$(t snap_done "$SNAPSHOT_DIR")"
    dim "$(t snap_restore_hint "$SNAPSHOT_DIR")"

    export SNAPSHOT_DIR
}
