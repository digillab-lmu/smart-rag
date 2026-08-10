#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# SMART RAG — the hand-over message to the Content Admin
# ═════════════════════════════════════════════════════════════════════════════
#
# Shared by bootstrap.sh, which offers this once at the end of an install, and
# admin.sh, which offers it at any time afterwards — for a second person, or
# after a handover to someone new. One implementation, because the awkward
# parts (which of the two account sentences is true, whether Tailscale has to
# be explained first, whether there is a relay at all) are the same in both
# places and would drift apart in two.
#
# Expects: common.sh and messages.sh sourced, REPO_ROOT set, and a .env there.
# ═════════════════════════════════════════════════════════════════════════════

# ─── The message the Content Admin actually receives ─────────────────────────
#
# Everything the installer prints is read by the system administrator, in a
# terminal, on the server. The person who will use the system daily is
# somewhere else entirely and gets none of it — which is how an installation
# ends up finished and unused, or how its first question arrives as "what is
# the address again?".
#
# So the installer writes that message itself: what this is, where to go,
# what to do in which order, and who to ask. It sends it when there is a
# relay, and otherwise prints it between two lines to be copied out — the
# Tailscale deployments, which are the ones most likely to have no relay, are
# also the ones where the address is impossible to guess.
#
# Sending is never automatic. The address is asked for, the message is shown
# in full first, and the send is confirmed: this leaves the server and names
# a colleague, and neither of those should happen because the operator
# pressed Enter through a wizard.
_handover_body() {
    set -a; source "$REPO_ROOT/.env"; set +a

    local content="${CONTENT_ADMIN_PUBLIC_URL:-}"
    if [[ -z "$content" && "${DEPLOYMENT_MODE:-domain}" == "domain" ]]; then
        content="https://$(subdomain_host content "$DOMAIN" "${SUBDOMAIN_PREFIX:-}")"
    fi
    local flowise="${FLOWISE_PUBLIC_URL:-}"
    local course="${COURSE_NAME:-${COURSE_ID:-}}"

    printf '%s\n\n' "$(t handover_greeting)"
    printf '%s\n\n' "$(t handover_body_intro "$course")"
    printf '%s\n'    "$(t handover_body_where)"
    printf '  %s\n\n' "$content"

    # Which of the two account sentences is true depends on whether step 3
    # left an account behind. Presence, not emptiness: an .env that has the
    # key with no value means no account, same as no key at all.
    if [[ -n "${CONTENT_ADMIN_USERNAME:-}" ]]; then
        printf '%s\n\n' "$(t handover_body_login_exists "$CONTENT_ADMIN_USERNAME")"
    else
        printf '%s\n\n' "$(t handover_body_login_new)"
    fi

    printf '%s\n\n' "$(t handover_body_steps_title)"
    printf '%s\n\n' "$(t handover_body_step1)"
    printf '%s\n\n' "$(t handover_body_step2)"
    printf '%s\n\n' "$(t handover_body_step3)"
    printf '%s\n\n' "$(t handover_body_check)"

    if [[ -n "$flowise" ]]; then
        printf '%s\n'    "$(t handover_body_flowise)"
        printf '  %s\n\n' "$flowise"
    fi
    if [[ "${DEPLOYMENT_MODE:-domain}" == "tailscale" ]]; then
        printf '%s\n\n' "$(t handover_body_tailscale)"
    fi

    printf '%s\n\n' "$(t handover_body_ask "${ADMIN_EMAIL:-}")"
    printf '%s\n'    "$(t handover_body_close)"
}

_handover_mail() {
    set -a; source "$REPO_ROOT/.env"; set +a

    local course="${COURSE_NAME:-${COURSE_ID:-}}"
    local subject; subject="$(t handover_subject "$course")"
    local body;    body="$(_handover_body)"

    echo
    printf "  ${BOLD}%s${RESET}\n" "$(t handover_title)"
    printf "  %s\n\n"             "$(t handover_intro)"

    # Printed before anything is sent, and printed again in the paths where
    # nothing is sent — the operator should never have to take the wizard's
    # word for what went out under their name.
    printf "  ${DIM}%s${RESET}\n" "$(t handover_copy_start)"
    printf "%s\n" "$(t handover_subject "$course")"
    echo
    printf "%s\n" "$body"
    printf "  ${DIM}%s${RESET}\n" "$(t handover_copy_end)"
    echo

    if [[ -z "${SMTP_HOST:-}" ]]; then
        info "$(t handover_mail_norelay)"
        return 0
    fi

    local to
    # An empty answer is a legitimate choice here, so this cannot use
    # prompt(), which insists on a value.
    printf "  %s: " "$(t handover_mail_ask)" >&2
    IFS= read -r to || to=""
    to="${to//[[:space:]]/}"
    [[ -n "$to" ]] || { info "$(t handover_mail_notsent)"; return 0; }
    validate_email "$to" || { info "$(t handover_mail_notsent)"; return 0; }

    confirm handover_mail_confirm "y" || { info "$(t handover_mail_notsent)"; return 0; }

    info "$(t handover_mail_sending)"
    local out
    if out="$(printf '%s\n' "$body" | docker exec -i smartrag-content-admin \
              python3 /app/send_handover.py "$to" "$subject" 2>&1)"; then
        ok "$(t handover_mail_sent "$to")"
    else
        err "$(t handover_mail_failed "$out")"
        printf "  %s\n" "$(t handover_mail_failed_how)"
    fi
}
