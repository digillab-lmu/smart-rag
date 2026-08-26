#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — Admin TUI (day-to-day operations)
# ═════════════════════════════════════════════════════════════════════════════
#
# A raspi-config-style whiptail menu for operating an already-deployed SMART
# RAG instance: status, logs, updates, restarts, SSL, mail test, DNS check,
# secrets overview, uninstall. Everything here runs directly on the host as
# root (via SSH) — no new container, no network exposure, no Docker-socket-
# in-a-container privilege question. Content-authoring (agent prompts, RAG
# documents, knowledge graph) is intentionally NOT here — see the project
# plan for why that's a separate, later, web-based piece instead.
#
# On first run it installs itself as a global `smartrag` command (a symlink
# to /usr/local/bin/smartrag), without asking, so the `sudo smartrag` that
# the installer and the docs both point at actually exists. An existing file
# at that path is left alone. Calling this script directly keeps working.
#
# Usage:  sudo bash scripts/admin.sh [--lang en|de]
#     or, once installed:  sudo smartrag
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    echo "ERROR: bash >= 4 required" >&2; exit 1
fi

# Resolve the REAL location of this file, following symlinks — needed because
# this is normally invoked via the /usr/local/bin/smartrag symlink, and a
# plain "dirname ${BASH_SOURCE[0]}" would then resolve to /usr/local/bin
# instead of the actual scripts/ directory inside the repo.
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LIB_DIR="$SCRIPT_DIR/lib"

# shellcheck source=lib/common.sh
source "$LIB_DIR/common.sh"
# shellcheck source=lib/messages.sh
source "$LIB_DIR/messages.sh"
# shellcheck source=lib/preflight.sh
source "$LIB_DIR/preflight.sh"
# shellcheck source=lib/config-wizard.sh
source "$LIB_DIR/config-wizard.sh"
# shellcheck source=lib/handover.sh
source "$LIB_DIR/handover.sh"

# ─── Arg parsing ─────────────────────────────────────────────────────────────
while (( $# > 0 )); do
    case "$1" in
        --lang) shift; LANG_CHOICE="${1:-en}" ;;
        --lang=*) LANG_CHOICE="${1#*=}" ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
LANG_CHOICE="${LANG_CHOICE:-$(detect_default_language)}"
export LANG_CHOICE

# ─── Root check ──────────────────────────────────────────────────────────────
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "$(t pf_root_needed "$(basename "$0")")"
fi

require_command whiptail
require_command docker

# ─── Load .env ───────────────────────────────────────────────────────────────
[[ -f "$REPO_ROOT/.env" ]] || die "$(t admin_env_missing)"
set -a
# shellcheck source=/dev/null
source "$REPO_ROOT/.env"
set +a

# ─── Self-install as a global command ─────────────────────────────────────────
# Done without asking. It is a symlink to this file — nothing is copied,
# nothing existing is replaced (the guard below), and removing it is one
# `rm`. A prompt whose answer is effectively always yes is friction, and the
# cost of it being missed is real: the docs and the installer's closing steps
# all say `sudo smartrag`, and that command then does not exist.
if [[ ! -e /usr/local/bin/smartrag ]]; then
    # Shared with bootstrap, which now makes the link at the end of an
    # install — this file used to be the only place that did, and it is the
    # one place reachable only without the command it creates.
    if install_smartrag_command "$SELF"; then
        ok "$(t admin_install_done)"
    else
        # A read-only /usr/local/bin or a missing directory: worth saying,
        # not worth stopping for — the script itself runs fine either way.
        warn "$(t admin_install_failed)"
    fi
fi

# ─── Shared helpers ────────────────────────────────────────────────────────────

# Echoes the smartrag-* container names relevant to the currently-enabled
# COMPOSE_PROFILES — same set start-services.sh waits on.
_active_services() {
    local services=(
        smartrag-postgres smartrag-redis smartrag-garage smartrag-weaviate
        smartrag-neo4j smartrag-flowise smartrag-flowise-worker smartrag-n8n
        smartrag-content-admin
    )
    [[ "${COMPOSE_PROFILES:-core}" == *observability* ]] && services+=(
        smartrag-clickhouse smartrag-langfuse-web smartrag-langfuse-worker
    )
    [[ "${COMPOSE_PROFILES:-core}" == *lti* ]] && services+=(smartrag-lti)
    printf '%s\n' "${services[@]}"
}

# whiptail menu to pick one service. Echoes the chosen name, returns 1 on Cancel/ESC.
pick_service() {
    local svc menu_args=()
    while IFS= read -r svc; do
        menu_args+=("$svc" "$svc")
    done < <(_active_services)

    local choice
    # Sized like the main menu: with the lti profile enabled this list is
    # thirteen entries, and a service hidden below a scroll fold is a service
    # the operator concludes is not running.
    _drain_stdin
    if ! choice=$(whiptail --title "$(t admin_title)" --menu "$(t admin_pick_service)" \
        22 78 14 "${menu_args[@]}" 3>&1 1>&2 2>&3); then
        return 1
    fi
    printf '%s' "$choice"
}

press_enter() {
    echo
    dim "$(t admin_press_enter)"
    read -r _ || true
}

# Throws away anything typed while something else was on screen.
#
# Keystrokes made while a plain-text action was running (or while the script
# was still starting) sit in the terminal's input buffer. The terminal echoes
# them at the cursor — which is where whiptail is about to draw its first
# menu row, so an arrow key shows up as a literal ^[[A over the top entry —
# and whiptail then consumes them as navigation in a menu the operator has
# not seen yet. Both are avoided by draining the buffer first.
#
# read returns non-zero on both timeout and EOF, so this terminates whether
# stdin is an idle terminal or a closed pipe.
_drain_stdin() {
    local junk
    while read -r -t 0.05 -n 4096 junk 2>/dev/null; do :; done
    return 0
}

# ─── Actions ───────────────────────────────────────────────────────────────────

action_status() {
    clear
    header "$(t admin_status_title)"
    local svc status container_status
    while IFS= read -r svc; do
        status="$(container_health "$svc")"
        [[ "$status" == none || "$status" == absent ]] && status=""
        if [[ -n "$status" ]]; then
            case "$status" in
                healthy) ok "$svc: $status" ;;
                *)       warn "$svc: $status" ;;
            esac
            continue
        fi
        container_status="$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "")"
        if [[ -n "$container_status" ]]; then
            info "$svc: $container_status ($(t admin_status_no_healthcheck))"
        else
            err "$svc: $(t admin_status_not_found)"
        fi
    done < <(_active_services)

    # Container health alone says nothing about whether documents can
    # actually be ingested: a perfectly healthy n8n with no active workflow
    # looks green here, while every upload 404s. Ask the webhook itself.
    echo
    case "$(n8n_webhook_state)" in
        registered)   ok "$(t admin_status_ingest_ok)" ;;
        unregistered) err "$(t admin_status_ingest_missing)"
                      dim "$(t admin_status_ingest_fix)" ;;
        *)            warn "$(t admin_status_ingest_unknown)" ;;
    esac
    press_enter
}

action_logs() {
    local svc
    svc="$(pick_service)" || return 0
    clear
    header "$(t admin_logs_title "$svc")"
    dim "$(t admin_logs_hint)"
    docker logs -f --tail 50 "$svc" || true
    press_enter
}

action_update() {
    clear
    header "$(t admin_menu_update)"
    if bash "$SCRIPT_DIR/compose.sh" pull && bash "$SCRIPT_DIR/compose.sh" up -d; then
        ok "$(t admin_update_done)"
    else
        err "$(t admin_update_failed)"
    fi
    press_enter
}

action_restart() {
    local svc
    svc="$(pick_service)" || return 0
    clear
    header "$(t admin_menu_restart)"
    if docker restart "$svc" >/dev/null; then
        ok "$(t admin_restart_done "$svc")"
    else
        err "$(t admin_restart_failed "$svc")"
        press_enter
        return 0
    fi

    # Whatever depends on this service is still holding a connection to the
    # instance that just went away. `depends_on` orders startup; it does not
    # propagate a restart. Offered rather than done automatically: restarting
    # Postgres would otherwise sweep half the stack along without being asked.
    local dependents=()
    mapfile -t dependents < <(compose_dependents "$svc" "$SCRIPT_DIR/compose.sh")
    if (( ${#dependents[@]} > 0 )); then
        echo
        info "$(t admin_restart_dependents "$svc" "${#dependents[@]}")"
        printf '      %s\n' "${dependents[@]}"
        dim "$(t admin_restart_dependents_why)"
        echo
        if confirm admin_restart_dependents_confirm "y"; then
            local dep
            for dep in "${dependents[@]}"; do
                if docker restart "$dep" >/dev/null 2>&1; then
                    ok "$(t admin_restart_done "$dep")"
                else
                    err "$(t admin_restart_failed "$dep")"
                fi
            done
        else
            warn "$(t admin_restart_dependents_skipped)"
        fi
    fi
    press_enter
}

action_ssl() {
    clear
    header "$(t admin_ssl_title)"
    certbot certificates 2>/dev/null | grep -A5 "smartrag-" || dim "$(t admin_ssl_none)"
    echo
    if confirm admin_ssl_regen_nginx_confirm "n"; then
        bash "$SCRIPT_DIR/deploy-nginx-config.sh" --lang "$LANG_CHOICE" || err "$(t admin_ssl_regen_nginx_failed)"
        echo
    fi
    if confirm admin_ssl_renew_confirm "n"; then
        bash "$SCRIPT_DIR/get-ssl-certs.sh" --lang "$LANG_CHOICE" || err "$(t admin_ssl_renew_failed)"
    fi
    press_enter
}

action_n8n_workflows() {
    clear
    header "$(t admin_n8n_title)"
    info "$(t admin_n8n_intro)"
    echo
    if confirm admin_n8n_confirm "n"; then
        # Same guided flow the installer uses: if n8n has no owner account
        # yet, walk the admin through creating one and then finish the
        # import here, rather than sending them to a shell command. This
        # menu entry is meant to be the whole path — importing the ingest
        # workflows belongs to a standard setup, and nothing in a standard
        # setup should require typing a command by hand.
        rc=0
        run_n8n_import_guided "$SCRIPT_DIR" "$LANG_CHOICE" || rc=$?
        # EXIT_SKIPPED means the admin chose "not now"; the guided flow has
        # already said so on screen. Calling that "failed" would send them
        # looking for a broken import instead of the browser step they
        # deferred.
        # EXIT_UNVERIFIED: the import ran but n8n hadn't restarted in time
        # to confirm it. The script has already said so; the Status entry
        # shows the real answer once n8n is back.
        if (( rc != 0 && rc != EXIT_SKIPPED && rc != EXIT_UNVERIFIED )); then
            err "$(t admin_n8n_failed)"
        fi
    fi
    press_enter
}

# ─── Upgrade / migrations ─────────────────────────────────────────────────────
# .env is generated once, and deploy-schemas.sh never rewrites a live Weaviate
# class. Both are deliberate — neither should silently overwrite something in
# production — but together they mean an upgraded deployment can be missing
# things a fresh one gets for free. Twice now that has been discovered by an
# operator hitting the resulting failure rather than by anything telling them.
# This entry closes that gap: it says what a `git pull` expects and this
# installation doesn't have, and offers to fix it.

# Echoes the keys present in .env.example but absent from .env, one per line.
# Keys the wizard writes with a resolved value. templates.sh is the single
# source of truth — anything it assigns through REPL[...] is computed at
# install time, so the same key still carrying a ${...} in a live .env was
# copied from .env.example and never resolved.
_wizard_resolved_keys() {
    # sed, not `tr -d 'REPL[]'` — tr deletes those CHARACTERS wherever they
    # occur, so SMTP_SENDER_EMAIL came back as SMT_SNDR_MAI and matched
    # nothing.
    grep -oE 'REPL\[[A-Z][A-Z0-9_]*\]' "$LIB_DIR/templates.sh" 2>/dev/null \
        | sed -E 's/^REPL\[(.+)\]$/\1/' | sort -u
}

# Present, but still holding an unexpanded ${...} that should have been
# resolved. Invisible to _missing_env_keys, which only looks for absent keys
# — that is why SMTP_SENDER_EMAIL survived an Upgrade run as
# "noreply@${DOMAIN}", and why the ingest's completion mail would have gone
# out with that in the From address.
#
# Keys NOT written by the wizard are deliberately left alone: DATABASE_URL and
# NEO4J_AUTH legitimately keep their ${...}, because they reach their
# containers through compose's `environment:` block, where Compose does
# interpolate. Only env_file consumers need the resolved form.
_stale_env_keys() {
    local envfile="$REPO_ROOT/.env" key value
    [[ -f "$envfile" ]] || return 0
    while IFS= read -r key; do
        [[ -n "$key" ]] || continue
        value="$(grep -m1 "^${key}=" "$envfile" || true)"
        [[ -n "$value" ]] || continue
        [[ "$value" == *'${'* ]] && echo "$key"
    done < <(_wizard_resolved_keys)

    # Separately, and for EVERY key rather than only the wizard-written ones:
    # the placeholder itself. It is a fixed string published in this
    # repository, and all twenty-two keys that carry it in .env.example are
    # secrets, so finding it in a live file means that secret is public.
    grep -oE '^[A-Z][A-Z0-9_]*="generate-with-bootstrap"' "$envfile" 2>/dev/null \
        | cut -d= -f1
}

# A key that appears more than once. Both bash and read_env() take the last
# occurrence, so the earlier ones are dead weight that silently disagrees
# with what is actually in effect.
_duplicate_env_keys() {
    local envfile="$REPO_ROOT/.env"
    [[ -f "$envfile" ]] || return 0
    grep -oE '^[A-Z][A-Z0-9_]*=' "$envfile" | tr -d '=' | sort | uniq -d
}

_missing_env_keys() {
    local example="$REPO_ROOT/.env.example" envfile="$REPO_ROOT/.env" key
    [[ -f "$example" && -f "$envfile" ]] || return 0
    while IFS= read -r key; do
        grep -q "^${key}=" "$envfile" || echo "$key"
    done < <(grep -oE '^[A-Z][A-Z0-9_]*=' "$example" | tr -d '=' | sort -u)
}

# The value a missing key should get. Anything whose value embeds a subdomain
# is computed here through subdomain_host() — copying .env.example's literal
# would drop SUBDOMAIN_PREFIX and point the service at a hostname with no
# DNS record, which is exactly the bug that made this necessary.
_default_for_env_key() {
    local key="$1" prefix="${SUBDOMAIN_PREFIX:-}"
    case "$key" in
        GARAGE_S3_PUBLIC_URL)       echo "https://$(subdomain_host s3        "$DOMAIN" "$prefix")" ;;
        FLOWISE_PUBLIC_URL)         echo "https://$(subdomain_host smart-rag "$DOMAIN" "$prefix")" ;;
        # Langfuse reads REDIS_AUTH, not REDIS_PASSWORD.
        REDIS_AUTH)                 echo "${REDIS_PASSWORD:-}" ;;
        # Must be resolved: env_file does not expand ${DOMAIN}.
        SMTP_SENDER_EMAIL)          echo "noreply@${DOMAIN}" ;;
        # Langfuse validates these on startup and refuses to initialise on a
        # bad one — a literal ${ADMIN_EMAIL} is rejected as "Invalid input",
        # which is what happened when the fallback copied .env.example's
        # literal instead of resolving it. The two project keys are generated
        # here for the same reason they are generated in the wizard: an
        # installation whose keys are predictable is one with no keys.
        LANGFUSE_INIT_USER_EMAIL)         echo "${ADMIN_EMAIL:-}" ;;
        LANGFUSE_INIT_USER_PASSWORD)      echo "${ADMIN_PASSWORD:-}" ;;
        LANGFUSE_INIT_PROJECT_NAME)       echo "${COURSE_NAME:-SMART RAG}" ;;
        INGEST_STATUS_TOKEN)              echo "$(openssl rand -hex 32)" ;;
        LANGFUSE_INIT_PROJECT_PUBLIC_KEY) echo "pk-lf-$(openssl rand -hex 16)" ;;
        LANGFUSE_INIT_PROJECT_SECRET_KEY) echo "sk-lf-$(openssl rand -hex 16)" ;;
        *)
            # Unknown new key: fall back to whatever .env.example carries,
            # and let the caller flag it for review rather than pretending
            # the value is necessarily right for this installation.
            local raw
            raw="$(grep -m1 "^${key}=" "$REPO_ROOT/.env.example" || true)"
            raw="${raw#*=}"          # strip KEY=
            raw="${raw%\"}"          # strip the surrounding quotes, if any
            raw="${raw#\"}"
            # …except when that literal is the placeholder. Twenty-two keys in
            # .env.example say "generate-with-bootstrap", and every one of
            # them is a secret. Copying it into a live .env produces a
            # credential that is published in this repository and identical on
            # every installation — which is what happened to the Langfuse
            # project keys: Langfuse accepted the placeholder as a key,
            # because as a string there is nothing wrong with it.
            if [[ "$raw" == "generate-with-bootstrap" ]]; then
                raw="$(openssl rand -hex 24)"
            fi
            printf '%s' "$raw"
            ;;
    esac
}

action_migrate() {
    clear
    header "$(t admin_migrate_title)"
    info "$(t admin_migrate_intro)"
    echo

    # ── 1. Missing .env keys ────────────────────────────────────────────────
    local missing=()
    mapfile -t missing < <(_missing_env_keys)

    if (( ${#missing[@]} == 0 )); then
        ok "$(t admin_migrate_env_ok)"
    else
        warn "$(t admin_migrate_env_missing "${#missing[@]}")"
        local key value derived=()
        for key in "${missing[@]}"; do
            value="$(_default_for_env_key "$key")"
            derived+=("$key=$value")
            printf '      %s=%s\n' "$key" "$value"
        done
        echo
        if confirm admin_migrate_env_confirm "y"; then
            cp "$REPO_ROOT/.env" "$REPO_ROOT/.env.backup-$(date +%F-%H%M%S)"
            for key in "${missing[@]}"; do
                value="$(_default_for_env_key "$key")"
                # set_env_var, not >>: appending cannot fix a key that is
                # already there, and it is how this .env ended up with two
                # REDIS_AUTH lines. set_env_var patches in place, escapes the
                # value, and collapses duplicates.
                set_env_var "$REPO_ROOT/.env" "$key" "$value"
            done
            ok "$(t admin_migrate_env_added "${#missing[@]}")"
            dim "$(t admin_migrate_env_restart)"
        else
            info "$(t admin_migrate_env_skipped)"
        fi
    fi

    # ── 1b. Present, but never resolved ─────────────────────────────────────
    echo
    local stale=()
    mapfile -t stale < <(_stale_env_keys)
    if (( ${#stale[@]} == 0 )); then
        ok "$(t admin_migrate_stale_ok)"
    else
        warn "$(t admin_migrate_stale_found "${#stale[@]}")"
        local skey svalue
        for skey in "${stale[@]}"; do
            svalue="$(_default_for_env_key "$skey")"
            printf '      %s\n' "$(grep -m1 "^${skey}=" "$REPO_ROOT/.env")"
            printf '      → %s="%s"\n' "$skey" "$svalue"
        done
        echo
        if confirm admin_migrate_stale_confirm "y"; then
            for skey in "${stale[@]}"; do
                set_env_var "$REPO_ROOT/.env" "$skey" "$(_default_for_env_key "$skey")"
            done
            ok "$(t admin_migrate_stale_fixed "${#stale[@]}")"
            dim "$(t admin_migrate_env_restart)"
        else
            info "$(t admin_migrate_env_skipped)"
        fi
    fi

    # ── 1c. Duplicated keys ─────────────────────────────────────────────────
    echo
    local dupes=()
    mapfile -t dupes < <(_duplicate_env_keys)
    if (( ${#dupes[@]} > 0 )); then
        warn "$(t admin_migrate_dupes_found "${#dupes[@]}")"
        printf '      %s\n' "${dupes[@]}"
        dim "$(t admin_migrate_dupes_why)"
        echo
        if confirm admin_migrate_dupes_confirm "y"; then
            local dkey dval
            for dkey in "${dupes[@]}"; do
                # The value in effect is the LAST one, so that is what is kept.
                dval="$(grep "^${dkey}=" "$REPO_ROOT/.env" | tail -1)"
                dval="${dval#*=}"; dval="${dval%\"}"; dval="${dval#\"}"
                set_env_var "$REPO_ROOT/.env" "$dkey" "$dval"
            done
            ok "$(t admin_migrate_dupes_fixed "${#dupes[@]}")"
        else
            info "$(t admin_migrate_env_skipped)"
        fi
    fi

    echo
    # ── 1d. The database schema ─────────────────────────────────────────────
    # This menu entry is called "apply pending migrations" and, until now,
    # applied none: it checked .env keys and stale state and never touched the
    # SQL. The application applies them at startup, so this is the path for
    # looking without restarting — and for the case where the container is
    # down because of the very schema it needs.
    if docker ps --format '{{.Names}}' | grep -qx smartrag-content-admin; then
        local schema_state
        schema_state="$(docker exec smartrag-content-admin \
            python3 /app/db.py status 2>&1 || true)"
        if grep -q "pending: \[\]" <<<"$schema_state"; then
            ok "$(t admin_migrate_schema_ok)"
        else
            warn "$(t admin_migrate_schema_pending)"
            printf '      %s\n' "$schema_state"
            if confirm admin_migrate_schema_confirm "y"; then
                if docker exec smartrag-content-admin \
                       python3 /app/db.py migrate; then
                    ok "$(t admin_migrate_schema_done)"
                else
                    warn "$(t admin_migrate_schema_failed)"
                fi
            else
                info "$(t admin_migrate_env_skipped)"
            fi
        fi
    else
        dim "$(t admin_migrate_schema_no_container)"
    fi

    press_enter
}

# Sends through the Content Admin's own mailer rather than Postfix's.
#
# The previous test called install-postfix.sh --test-only, which exercises a
# local Postfix — so on an installation pointed straight at an external relay
# there was no way to test the thing that actually sends. The mailer reads the
# same SMTP_* values every other part of the stack uses, so a mail that
# arrives through it is evidence for all of them.
_mail_send_test() {
    local to="$1"
    docker exec smartrag-content-admin python3 -c "
import sys; sys.path.insert(0, '/app')
import mailer
mailer.send_mail(${to@Q}, 'SMART RAG — test', 'This is the test mail from sudo smartrag.')
print('sent')
" 2>&1
}

action_mail() {
    clear
    header "$(t admin_mail_title)"

    if [[ -z "${SMTP_HOST:-}" ]]; then
        warn "$(t admin_mail_no_relay)"
        info "$(t admin_mail_why)"
        echo
        # The obvious place to fix it is the place that reports it. This used
        # to be reachable only through the configuration menu, so the page
        # that said "no relay" was also the page that could not do anything
        # about it.
        if confirm admin_mail_configure_now "y"; then
            _cfg_mail
            set -a
            # shellcheck source=/dev/null
            source "$REPO_ROOT/.env"
            set +a
        else
            press_enter
            return 0
        fi
        [[ -z "${SMTP_HOST:-}" ]] && { press_enter; return 0; }
    else
        info "$(t admin_mail_current "$SMTP_HOST" "${SMTP_PORT:-25}")"
    fi

    # n8n keeps its own copy. The credential is built from .env when the
    # workflows are deployed, so changing the relay here reaches the Content
    # Admin immediately and n8n not at all — the ingest mails and both alert
    # workflows would go on using the old settings, without saying so.
    if confirm admin_mail_refresh_n8n "y"; then
        # Through the guided path, not the deploy script directly. That rule
        # exists because the import needs n8n's owner account, which can only
        # be created in a browser — a direct call skips the guidance the
        # installer gives and fails with a message about a missing user.
        local rc=0
        run_n8n_import_guided "$SCRIPT_DIR" "$LANG_CHOICE" || rc=$?
        if (( rc != 0 && rc != EXIT_SKIPPED && rc != EXIT_UNVERIFIED )); then
            warn "$(t admin_mail_refresh_failed)"
        fi
    fi

    if [[ -n "${ADMIN_EMAIL:-}" ]]; then
        info "$(t admin_mail_target "$ADMIN_EMAIL")"
        if confirm admin_mail_test_confirm "y"; then
            local out
            out="$(_mail_send_test "$ADMIN_EMAIL")"
            if grep -q "^sent$" <<<"$out"; then
                ok "$(t admin_mail_test_sent "$ADMIN_EMAIL")"
                dim "$(t admin_mail_test_hint)"
            else
                err "$(t admin_mail_test_failed)"
                printf '      %s\n' "$(tail -3 <<<"$out")"
            fi
        fi
    else
        warn "$(t admin_mail_no_admin_email)"
    fi
    press_enter
}

action_dns() {
    clear
    header "$(t admin_dns_title)"
    local prefix="${SUBDOMAIN_PREFIX:-}"
    check_dns "$(subdomain_host smart-rag "$DOMAIN" "$prefix")"
    check_dns "$(subdomain_host n8n       "$DOMAIN" "$prefix")"
    check_dns "$(subdomain_host content   "$DOMAIN" "$prefix")"
    [[ "${COMPOSE_PROFILES:-core}" == *observability* ]] && check_dns "$(subdomain_host langfuse "$DOMAIN" "$prefix")"
    [[ "${COMPOSE_PROFILES:-core}" == *lti* ]]           && check_dns "$(subdomain_host lti       "$DOMAIN" "$prefix")"
    press_enter
}

action_secrets() {
    clear
    header "$(t admin_secrets_title)"
    local creds="$REPO_ROOT/credentials.txt" perms
    if [[ -f "$creds" ]]; then
        perms="$(stat -c '%a' "$creds" 2>/dev/null || echo "?")"
        info "$(t admin_secrets_path "$creds" "$perms")"
    else
        warn "$(t admin_secrets_no_file)"
    fi
    echo
    local keys=(
        POSTGRES_PASSWORD REDIS_PASSWORD GARAGE_SECRET_KEY NEO4J_PASSWORD
        WEAVIATE_API_KEY ENCRYPTION_KEY JWT_AUTH_TOKEN_SECRET
        N8N_ENCRYPTION_KEY LLM_API_KEY EMBEDDING_API_KEY SMTP_PASSWORD
    )
    local k
    for k in "${keys[@]}"; do
        if [[ -n "${!k:-}" ]]; then
            ok "$k: $(t admin_secrets_set)"
        else
            dim "$k: $(t admin_secrets_unset)"
        fi
    done
    press_enter
}

# Recreates any container whose effective config changed (Compose diffs
# against the current .env automatically — we don't need to know which
# container is affected by which key).
_apply_config_change() {
    info "$(t admin_cfg_applying)"
    set -a
    # shellcheck source=/dev/null
    source "$REPO_ROOT/.env"
    set +a
    if bash "$SCRIPT_DIR/compose.sh" up -d; then
        ok "$(t admin_cfg_done)"
    else
        err "$(t admin_cfg_apply_failed)"
    fi
    press_enter
}

# Plain single-value edit: show current value as the default, prompt for a
# new one, patch .env, apply. Only for NON-secret values — the current value
# is shown on screen.
# Args: $1=ENV_KEY  $2=message-key for the question  $3=validator function (may be empty)
_cfg_simple() {
    local env_key="$1" msg_key="$2" validator="${3:-}"
    clear
    header "$(t admin_config_title)"
    local new
    new="$(prompt "$msg_key" "${!env_key:-}" "$validator")" || { press_enter; return 0; }
    set_env_var "$REPO_ROOT/.env" "$env_key" "$new"
    _apply_config_change
}

# Same, but for secrets: never displays the current value, only overwrites
# if the operator actually typed something (blank = keep unchanged).
# Args: $1=ENV_KEY  $2=message-key for the question  $3=optional message-key
#       for an extra note printed above the prompt (menu labels must stay
#       short — whiptail doesn't wrap them — so caveats go here instead)
# Like _cfg_secret, but for the two keys Flowise also holds a copy of.
# Writing .env is only half the job — see _push_keys_to_flowise.
_cfg_provider_key() {
    local env_key="$1" msg_key="$2" note_key="${3:-}"
    clear
    header "$(t admin_config_title)"
    [[ -n "$note_key" ]] && info "$(t "$note_key")"
    local provider=""
    case "$env_key" in
        LLM_API_KEY)       provider="${LLM_PROVIDER:-}" ;;
        EMBEDDING_API_KEY) provider="${EMBEDDING_PROVIDER:-}" ;;
    esac
    [[ -n "$provider" ]] && info "$(t admin_cfg_key_provider "$provider")"
    [[ -n "${!env_key:-}" ]] && info "$(t admin_cfg_secret_already_set)"

    local new
    new="$(prompt_password "$msg_key" "")" || { press_enter; return 0; }
    if [[ -z "$new" ]]; then
        info "$(t admin_cfg_secret_unchanged)"
        press_enter
        return 0
    fi

    set_env_var "$REPO_ROOT/.env" "$env_key" "$new"
    ok "$(t admin_cfg_key_written)"
    # Reload so the push reads the new value rather than the one this shell
    # sourced at startup.
    set -a; source "$REPO_ROOT/.env"; set +a

    if ! _push_keys_to_flowise; then
        echo
        warn "$(t admin_cfg_push_manual)"
    fi
    _apply_config_change
}

_cfg_secret() {
    local env_key="$1" msg_key="$2" note_key="${3:-}"
    clear
    header "$(t admin_config_title)"
    [[ -n "$note_key" ]] && info "$(t "$note_key")"
    [[ -n "${!env_key:-}" ]] && info "$(t admin_cfg_secret_already_set)"
    local new
    new="$(prompt_password "$msg_key" "")" || { press_enter; return 0; }
    if [[ -z "$new" ]]; then
        info "$(t admin_cfg_secret_unchanged)"
        press_enter
        return 0
    fi
    set_env_var "$REPO_ROOT/.env" "$env_key" "$new"
    _apply_config_change
}

# Reuses the wizard's own mail-relay section (existing-relay detection,
# Postfix-vs-direct choice, etc.) instead of duplicating that logic —
# pre-populated from the current .env so its prompts show real defaults.
_cfg_mail() {
    clear
    header "$(t admin_config_title)"
    CFG_INSTALL_POSTFIX="${INSTALL_POSTFIX_RELAY:-false}"
    CFG_SMTP_RELAY_HOST="${SMTP_RELAY_HOST:-}"
    CFG_SMTP_RELAY_PORT="${SMTP_RELAY_PORT:-587}"
    CFG_SMTP_RELAY_USER="${SMTP_RELAY_USER:-}"
    CFG_SMTP_RELAY_PASSWORD="${SMTP_RELAY_PASSWORD:-}"
    CFG_SMTP_HOST="${SMTP_HOST:-}"
    CFG_SMTP_PORT="${SMTP_PORT:-587}"
    CFG_SMTP_SECURE="${SMTP_SECURE:-false}"
    CFG_SMTP_USER="${SMTP_USER:-}"
    CFG_SMTP_PASSWORD="${SMTP_PASSWORD:-}"

    if ! ask_mail_config; then
        info "$(t admin_cfg_cancelled)"
        press_enter
        return 0
    fi

    local env_file="$REPO_ROOT/.env"
    set_env_var "$env_file" INSTALL_POSTFIX_RELAY "$CFG_INSTALL_POSTFIX"
    set_env_var "$env_file" SMTP_RELAY_HOST "$CFG_SMTP_RELAY_HOST"
    set_env_var "$env_file" SMTP_RELAY_PORT "$CFG_SMTP_RELAY_PORT"
    set_env_var "$env_file" SMTP_RELAY_USER "$CFG_SMTP_RELAY_USER"
    set_env_var "$env_file" SMTP_RELAY_PASSWORD "$CFG_SMTP_RELAY_PASSWORD"
    set_env_var "$env_file" N8N_EMAIL_MODE "$CFG_N8N_EMAIL_MODE"
    set_env_var "$env_file" SMTP_HOST "$CFG_SMTP_HOST"
    set_env_var "$env_file" SMTP_PORT "$CFG_SMTP_PORT"
    set_env_var "$env_file" SMTP_USER "$CFG_SMTP_USER"
    set_env_var "$env_file" SMTP_PASSWORD "$CFG_SMTP_PASSWORD"
    set_env_var "$env_file" SMTP_SECURE "$CFG_SMTP_SECURE"
    set_env_var "$env_file" SMTP_CONNECTION_URL "$CFG_SMTP_CONNECTION_URL"

    if [[ "$CFG_INSTALL_POSTFIX" == "true" ]]; then
        info "$(t admin_cfg_mail_postfix_note)"
        bash "$SCRIPT_DIR/install-postfix.sh" --lang "$LANG_CHOICE" || warn "$(t admin_cfg_mail_postfix_failed)"
    fi

    _apply_config_change
}

# Pushes the keys in .env into Flowise, which keeps its own copy.
#
# Without this, changing a key here changes nothing an agent uses: Flowise
# stores the value in a credential created at import time, and the agents
# reference it by id. Re-importing does not help either — the credential is
# found by name and reused. So a rotated key would leave every agent
# authenticating with the old one, and nothing would report a problem.
#
# The work happens inside the content-admin container rather than here,
# because the mapping from a provider to Flowise's credential type already
# exists there. Two copies of that mapping would be one copy too many.
_push_keys_to_flowise() {
    echo
    info "$(t admin_cfg_push_intro)"
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx smartrag-content-admin; then
        warn "$(t admin_cfg_push_no_container)"
        return 1
    fi
    local out rc=0
    out="$(docker exec smartrag-content-admin python -m sync_secrets 2>&1)" || rc=$?
    printf '%s\n' "$out" | sed 's/^/    /'
    case "$rc" in
        0) ok   "$(t admin_cfg_push_ok)" ;;
        2) warn "$(t admin_cfg_push_partial)" ;;
        *) err  "$(t admin_cfg_push_failed)" ;;
    esac
    return "$rc"
}

action_config() {
    local items=(mail "$(t admin_cfg_mail)")
    items+=(llm_key "$(t admin_cfg_llm_key)")
    items+=(embed_key "$(t admin_cfg_embed_key)")
    items+=(reranker "$(t admin_cfg_reranker)")
    [[ "${COMPOSE_PROFILES:-core}" == *lti* ]] && items+=(lms "$(t admin_cfg_lms)")
    items+=(admin_email "$(t admin_cfg_admin_email)")
    items+=(tz "$(t admin_cfg_tz)")

    local choice
    if ! choice=$(whiptail --title "$(t admin_title)" --menu "$(t admin_cfg_menu_prompt)" \
        20 78 12 "${items[@]}" 3>&1 1>&2 2>&3); then
        return 0
    fi

    case "$choice" in
        mail)        _cfg_mail ;;
        llm_key)     _cfg_provider_key LLM_API_KEY cfg_llm_api_key admin_cfg_llm_key_note ;;
        embed_key)   _cfg_provider_key EMBEDDING_API_KEY cfg_embed_api_key admin_cfg_embed_key_note ;;
        reranker)    _cfg_secret RERANKER_API_KEY cfg_reranker_api_key admin_cfg_reranker_note ;;
        lms)         _cfg_simple LMS_URL cfg_lms_url validate_url ;;
        admin_email) _cfg_simple ADMIN_EMAIL cfg_admin_email validate_email ;;
        tz)          _cfg_simple TZ cfg_tz "" ;;
    esac
}

# ─── Copy a backup to a removable medium ─────────────────────────────────────
# Two archives, not one: the .sha256 beside the tar.gz is what makes a restore
# able to tell a complete copy from a truncated one. Copied by hand it is the
# file people forget, and restore.sh then only warns — so the transfer that
# needs the check most is the one most likely to arrive without it.
#
# What this does not do is encrypt. The archive contains .env, and .env holds
# every password of the installation in clear. On a stick that leaves the
# building that is the whole deployment, which is why the warning below is a
# confirmation and not a note.
# ─── Restore, from here rather than from a remembered command line ───────────
# This used to print the two commands and leave. That was a defensible answer
# while a restore was a rare, deliberate act performed by whoever wrote the
# backup — but the case it is actually needed for is a move onto a machine
# where the archive arrived on a stick, and sending somebody to the command
# line at that point is where the wrong archive name gets typed.
#
# The safety is not in making it awkward. It is in the dry run, which prints
# what the archive is, what address it carries and what would be overwritten,
# and in a confirmation that has to be typed rather than accepted.
action_restore() {
    clear
    header "$(t admin_restore_title)"
    info "$(t admin_restore_intro)"
    echo

    local archive
    archive="$(choose_backup_archive)" || return 0
    [[ -n "$archive" ]] || return 0

    echo
    info "$(t admin_restore_dryrun "$(basename "$archive")")"
    echo
    # Every refusal in restore.sh happens before anything is touched, so the
    # dry run is the same set of checks without the writing.
    # The same flags as the real run, --replace included. A dry run with a
    # different set does not predict the run it precedes: without it the
    # check stopped at "refusing to restore over an existing installation",
    # which is the one thing this menu entry is for.
    bash "$REPO_ROOT/scripts/restore.sh" "$archive" --replace --dry-run \
         --lang "$LANG_CHOICE" || {
        echo
        err "$(t admin_restore_dryrun_failed)"
        echo
        read -rp "$(t admin_press_enter)" _ || true
        return 0
    }

    echo
    warn "$(t admin_restore_replaces)"
    local typed
    typed="$(prompt admin_restore_type_word)" || return 0
    if [[ "$typed" != "$(t admin_restore_word)" ]]; then
        info "$(t admin_restore_not_confirmed)"
        echo
        read -rp "$(t admin_press_enter)" _ || true
        return 0
    fi

    echo
    # No address question here. restore.sh reads what this machine answers at
    # and offers it — asking in three places meant three chances to ask it
    # somewhere the answer could not be known. --replace because replacing
    # this installation is what this menu entry means; the typed confirmation
    # above is the gate, not a flag that would also silence unrelated warnings.
    bash "$REPO_ROOT/scripts/restore.sh" "$archive" --replace --lang "$LANG_CHOICE" || true

    # This menu sourced .env into its own environment at startup, and the
    # restore has just replaced that file. Without re-reading it, every later
    # action in this session works from the secrets of the installation that
    # was moved aside — and the values shown on screen are that one's too.
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a

    echo
    read -rp "$(t admin_press_enter)" _ || true
}

# ─── Preparing a medium ──────────────────────────────────────────────────────
# A new stick is normally FAT32, and FAT32 cannot hold a file larger than 4 GB.
# The archive here is 284 MB, but a course with real material passes that, and
# the copy then fails partway — which is the kind of failure that is only
# noticed when the backup is needed. ext4 also keeps ownership and permissions,
# which matters for a restore onto another Linux machine.
#
# This erases a disk, so the guards are the feature:
#   * only devices the kernel reports as removable (RM=1). The system disk is
#     not removable and therefore cannot appear in the list at all — that is
#     the guard, not a name filter that a different disk layout would defeat;
#   * nothing mounted is offered, and anything mounted from the device is
#     refused rather than unmounted for the operator;
#   * the device node has to be typed out, not selected, because selecting is
#     what makes the wrong row easy to take.
# ─── Checking an archive ─────────────────────────────────────────────────────
# The question this answers is "can this be restored", not "does this unpack" —
# it opens the archive in throwaway containers and has each system read its own
# data back. That was reachable only from the command line, which is the wrong
# place for it: the moment it is most worth running is on the machine a medium
# has just been carried to, and there the operator has a stick, not a
# remembered path. The chooser offers removable media for that reason.
action_verify_backup() {
    clear
    header "$(t admin_verify_title)"
    info "$(t admin_verify_intro)"
    echo

    local archive
    archive="$(choose_backup_archive)" || return 0
    [[ -n "$archive" ]] || return 0

    echo
    info "$(t admin_verify_running "$(basename "$archive")")"
    dim "$(t admin_verify_takes_a_while)"
    echo
    bash "$REPO_ROOT/scripts/verify-backup.sh" "$archive" --lang "$LANG_CHOICE" || true
    echo
    read -rp "$(t admin_press_enter)" _ || true
}

action_format_medium() {
    clear
    header "$(t admin_format_title)"
    info "$(t admin_format_intro)"
    echo

    local -a devices=() labels=()
    local dev size model
    while IFS=$'\t' read -r dev size model; do
        [[ -n "$dev" ]] || continue
        devices+=("$dev")
        labels+=("$dev  $size  $model")
    done < <(removable_disks)

    if (( ${#devices[@]} == 0 )); then
        warn "$(t admin_format_none)"
        echo
        read -rp "$(t admin_press_enter)" _ || true
        return 0
    fi

    local choice
    choice="$(select_one_index admin_format_which "${labels[@]}")" || return 0
    local device="${devices[$((choice-1))]}"

    # What is on it now, so the operator can recognise the wrong disk before
    # rather than after.
    echo
    info "$(t admin_format_contents "$device")"
    lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT "$device" 2>/dev/null | sed 's/^/    /'
    echo

    # Anything mounted from this device stops it. Unmounting on the operator's
    # behalf here would be this tool deciding that whatever is using the disk
    # does not matter.
    if lsblk -nr -o MOUNTPOINT "$device" 2>/dev/null | grep -q .; then
        err "$(t admin_format_mounted "$device")"
        echo
        read -rp "$(t admin_press_enter)" _ || true
        return 0
    fi

    warn "$(t admin_format_erases "$device")"
    local typed
    typed="$(prompt admin_format_type_device)" || return 0
    if [[ "$typed" != "$device" ]]; then
        info "$(t admin_format_not_confirmed)"
        echo
        read -rp "$(t admin_press_enter)" _ || true
        return 0
    fi

    info "$(t admin_format_running "$device")"
    if ! wipefs -a "$device" >/dev/null 2>&1; then
        err "$(t admin_format_failed)"; echo
        read -rp "$(t admin_press_enter)" _ || true; return 0
    fi
    # One partition spanning the disk, GPT, ext4. Nothing here needs a layout
    # more interesting than that, and a simpler one is easier to recognise.
    if ! parted -s "$device" mklabel gpt mkpart primary ext4 1MiB 100% >/dev/null 2>&1; then
        err "$(t admin_format_failed)"; echo
        read -rp "$(t admin_press_enter)" _ || true; return 0
    fi
    sync; sleep 1
    local part
    part="$(lsblk -nr -o NAME "$device" | sed -n '2p')"
    part="/dev/${part}"
    if ! mkfs.ext4 -q -L SMARTRAG "$part" >/dev/null 2>&1; then
        err "$(t admin_format_failed)"; echo
        read -rp "$(t admin_press_enter)" _ || true; return 0
    fi
    sync
    ok "$(t admin_format_done "$part")"
    echo
    read -rp "$(t admin_press_enter)" _ || true
}

action_backup_copy() {
    local dest archives=() archive target
    local MOUNTED_HERE=""
    dest="$(dirname "${BASE_DATA_PATH:-/srv/smart-rag-data}")/backups"
    mapfile -t archives < <(ls -1t "$dest"/smartrag-*.tar.gz 2>/dev/null | head -10)
    if (( ${#archives[@]} == 0 )); then
        warn "$(t admin_copy_none "$dest")"
        return 0
    fi

    local labels=() a
    for a in "${archives[@]}"; do
        labels+=("$(basename "$a")  ($(( $(stat -c%s "$a" 2>/dev/null || echo 0) / 1048576 )) MB)")
    done
    local pick
    pick="$(select_one_index admin_copy_which "${labels[@]}")" || return 0
    archive="${archives[$((pick-1))]}"

    if [[ ! -f "$archive.sha256" ]]; then
        # Without it the copy cannot be checked at the far end, and a restore
        # from it would proceed on trust.
        warn "$(t admin_copy_no_sum "$(basename "$archive")")"
        confirm admin_copy_no_sum_go "n" || return 0
    fi

    # Every removable partition, mounted or not. A server has no desktop to
    # mount a stick for it, so on the machine this exists for the medium is
    # almost always unmounted — and offering only a path to type is the
    # interface giving up exactly where it should help.
    local -a targets=() labels=()
    local dev size fstype label mp
    while IFS=$'\t' read -r dev size fstype label mp; do
        [[ -n "$dev" ]] || continue
        if [[ -n "$mp" ]]; then
            targets+=("mounted:$mp")
            labels+=("$(t admin_copy_target_mounted "$label" "$size" "$fstype" "$mp")")
        else
            targets+=("mount:$dev")
            labels+=("$(t admin_copy_target_unmounted "$label" "$size" "$fstype" "$dev")")
        fi
    done < <(removable_partitions)

    targets+=("other"); labels+=("$(t admin_copy_other)")
    local choice
    choice="$(select_one_index admin_copy_where "${labels[@]}")" || return 0
    local picked="${targets[$((choice-1))]}"

    case "$picked" in
        mounted:*) target="${picked#mounted:}" ;;
        mount:*)
            local device="${picked#mount:}"
            info "$(t admin_copy_mounting "$device")"
            target="$(mount_removable "$device")" || {
                err "$(t admin_copy_mount_failed "$device")"
                return 0
            }
            MOUNTED_HERE="$target"
            ok "$(t admin_copy_mounted "$target")"
            ;;
        *) target="$(prompt admin_copy_path)" || return 0 ;;
    esac

    [[ -d "$target" ]] || { err "$(t admin_copy_no_dir "$target")"; return 0; }
    [[ -w "$target" ]] || { err "$(t admin_copy_not_writable "$target")"; return 0; }

    echo
    warn "$(t admin_copy_secrets_warning)"
    confirm admin_copy_confirm "n" || return 0

    info "$(t admin_copy_running "$(basename "$archive")" "$target")"
    cp -- "$archive" "$target/" || { err "$(t admin_copy_failed)"; return 0; }
    [[ -f "$archive.sha256" ]] && cp -- "$archive.sha256" "$target/"
    # Written, not merely handed to the page cache. A stick pulled before this
    # returns holds a file that looks complete and is not.
    sync

    # Read back from the medium rather than trusting the copy: this is what
    # catches a full disk, a failing stick and a short write, and it is the
    # only reason to do the copy here rather than by hand.
    local expected actual
    if [[ -f "$target/$(basename "$archive").sha256" ]]; then
        expected="$(cat "$target/$(basename "$archive").sha256")"
        actual="$(sha256sum "$target/$(basename "$archive")" | awk '{print $1}')"
        if [[ "$expected" == "$actual" ]]; then
            ok "$(t admin_copy_verified)"
        else
            err "$(t admin_copy_mismatch)"
            return 0
        fi
    else
        warn "$(t admin_copy_unverified)"
    fi

    ok "$(t admin_copy_done "$target")"
    # Unmounted again only if this mounted it. A medium the operator had
    # already mounted may be in use for something else.
    if [[ -n "${MOUNTED_HERE:-}" ]]; then
        if umount "$MOUNTED_HERE" 2>/dev/null; then
            ok "$(t admin_copy_unmounted)"
        else
            warn "$(t admin_copy_unmount_failed "$MOUNTED_HERE")"
        fi
        MOUNTED_HERE=""
    else
        dim "$(t admin_copy_eject)"
    fi
}

action_backup() {
    clear
    header "$(t admin_backup_title)"
    info "$(t admin_backup_intro)"
    echo

    local existing=() dest
    dest="$(dirname "${BASE_DATA_PATH:-/srv/smart-rag-data}")/backups"
    mapfile -t existing < <(ls -1t "$dest"/smartrag-*.tar.gz 2>/dev/null | head -5)
    if (( ${#existing[@]} )); then
        echo "$(t admin_backup_existing "$dest")"
        local a
        for a in "${existing[@]}"; do
            dim "  $(basename "$a")  ($(( $(stat -c%s "$a" 2>/dev/null || echo 0) / 1048576 )) MB)"
        done
        echo
    else
        dim "$(t admin_backup_none "$dest")"
        echo
    fi

    local choice
    choice="$(select_one_index admin_backup_what         "$(t admin_backup_do)"         "$(t admin_backup_verify)"         "$(t admin_backup_copy)"         "$(t admin_backup_format)"         "$(t admin_backup_restore)"         "$(t admin_backup_back)")" || return 0

    case "$choice" in
        1)
            # Says the outage out loud before it starts one. The stop is what
            # makes the copy restorable, so it is not optional — but an
            # operator who learns about it afterwards is right to be annoyed.
            confirm admin_backup_confirm "y" || return 0
            bash "$REPO_ROOT/scripts/backup.sh" --lang "$LANG_CHOICE" || true
            ;;
        2)
            action_verify_backup
            ;;
        3)
            action_backup_copy
            ;;
        4)
            action_format_medium
            ;;
        5)
            action_restore
            ;;
        *) return 0 ;;
    esac
    echo
    read -rp "$(t admin_press_enter)" _ || true
}

action_handover() {
    clear
    header "$(t admin_menu_handover)"
    # The installer offers this once, at the end of a run nobody repeats. It
    # is needed again more often than that: a second person joins, the first
    # one leaves, or the mail was written down and lost. Same message, same
    # data, so it is also still correct — the account sentence follows what
    # .env says today, not what it said on installation day.
    _handover_mail
    press_enter
}

action_uninstall() {
    clear
    header "$(t admin_menu_uninstall)"
    bash "$SCRIPT_DIR/uninstall.sh" --lang "$LANG_CHOICE" || true
    press_enter
}

action_reset_content_admin() {
    clear
    header "$(t admin_reset_title)"
    # The way back in when the GUI's own reset cannot help: no mail relay,
    # a mailbox nobody can read any more, or a handover where the previous
    # holder is gone. Clearing the two keys makes is_configured() false, so
    # the GUI offers its first-run setup page again.
    #
    # Everything the account is *for* lives elsewhere — agent slots in
    # slots.json, documents in Weaviate, the Flowise key in its own variable
    # — so this loses nothing but the login itself. Saying that plainly
    # matters: "reset the admin account" sounds far more destructive than it
    # is, and an operator who fears data loss will look for a worse way.
    info "$(t admin_reset_explain)"
    echo
    local user
    user="$(grep -m1 '^CONTENT_ADMIN_USERNAME=' "$REPO_ROOT/.env" | cut -d= -f2- | tr -d '"')"
    if [[ -n "$user" ]]; then
        info "$(t admin_reset_current "$user")"
    else
        warn "$(t admin_reset_none)"
        press_enter
        return 0
    fi
    echo
    if ! confirm admin_reset_confirm "n"; then
        info "$(t admin_reset_cancelled)"
        press_enter
        return 0
    fi

    backup_file "$REPO_ROOT/.env"
    set_env_var "$REPO_ROOT/.env" CONTENT_ADMIN_USERNAME          ""
    set_env_var "$REPO_ROOT/.env" CONTENT_ADMIN_PASSWORD_HASH     ""
    # A reset link issued before this would still be valid otherwise.
    set_env_var "$REPO_ROOT/.env" CONTENT_ADMIN_RESET_TOKEN_HASH  ""
    set_env_var "$REPO_ROOT/.env" CONTENT_ADMIN_RESET_EXPIRES     ""
    ok "$(t admin_reset_done)"
    echo
    local url
    url="$(grep -m1 '^CONTENT_ADMIN_PUBLIC_URL=' "$REPO_ROOT/.env" | cut -d= -f2- | tr -d '"')"
    [[ -z "$url" ]] && url="https://$(subdomain_host content "$DOMAIN" "${SUBDOMAIN_PREFIX:-}")"
    info "$(t admin_reset_next "$url")"
    # No container restart: the GUI reads .env on every request.
    press_enter
}

# ─── Main loop ─────────────────────────────────────────────────────────────────
while true; do
    choice=""
    _drain_stdin
    # Box height, width, visible list rows. The list must be tall enough for
    # every entry — a scrolling menu hides the destructive one below the fold
    # — and the box needs roughly seven rows more than the list for its
    # title, prompt, buttons and borders. 16 entries therefore need a box of
    # 23, which still fits an 80x24 terminal with a row to spare. That is the
    # ceiling: a seventeenth entry needs the list split into groups, not the
    # box grown, because 24 leaves whiptail nothing to draw its border on.
    if ! choice=$(whiptail --title "$(t admin_title)" --menu "$(t admin_menu_prompt)" \
        23 78 16 \
        "1"  "$(t admin_menu_status)" \
        "2"  "$(t admin_menu_logs)" \
        "3"  "$(t admin_menu_update)" \
        "4"  "$(t admin_menu_restart)" \
        "5"  "$(t admin_menu_ssl)" \
        "6"  "$(t admin_menu_n8n)" \
        "7"  "$(t admin_menu_mail)" \
        "8"  "$(t admin_menu_dns)" \
        "9"  "$(t admin_menu_secrets)" \
        "10" "$(t admin_menu_config)" \
        "11" "$(t admin_menu_migrate)" \
        "12" "$(t admin_menu_backup)" \
        "13" "$(t admin_menu_reset_ca)" \
        "14" "$(t admin_menu_handover)" \
        "15" "$(t admin_menu_uninstall)" \
        "16" "$(t admin_menu_exit)" \
        3>&1 1>&2 2>&3); then
        clear
        break
    fi

    case "$choice" in
        1)  action_status ;;
        2)  action_logs ;;
        3)  action_update ;;
        4)  action_restart ;;
        5)  action_ssl ;;
        6)  action_n8n_workflows ;;
        7)  action_mail ;;
        8)  action_dns ;;
        9)  action_secrets ;;
        10) action_config ;;
        11) action_migrate ;;
        12) action_backup ;;
        13) action_reset_content_admin ;;
        14) action_handover ;;
        15) action_uninstall ;;
        16) clear; break ;;
    esac
done
