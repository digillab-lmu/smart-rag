# ═════════════════════════════════════════════════════════════════════════════
# config-wizard.sh — interactive .env configuration
# ═════════════════════════════════════════════════════════════════════════════
#
# Collects all user-configurable values via prompts and stores them in global
# CFG_* variables. The templates.sh module then writes them to .env.
#
# Globals set by run_config_wizard():
#   CFG_COURSE_NAME, CFG_COURSE_ID, CFG_DOMAIN, CFG_ADMIN_EMAIL,
#   CFG_BASE_DATA_PATH, CFG_TZ
#   CFG_ENABLE_OBSERVABILITY (yes|no), CFG_ENABLE_LTI (yes|no), CFG_LMS_URL
#   CFG_LLM_PROVIDER, CFG_LLM_MODEL_STRONG, CFG_LLM_MODEL_FAST,
#   CFG_LLM_API_KEY, CFG_LLM_BASE_URL
#   CFG_EMBEDDING_PROVIDER, CFG_EMBEDDING_MODEL, CFG_EMBEDDING_DIMENSIONS,
#   CFG_EMBEDDING_API_KEY, CFG_EMBEDDING_BASE_URL
#   CFG_RERANKER_PROVIDER, CFG_RERANKER_MODEL,
#   CFG_RERANKER_API_KEY, CFG_RERANKER_BASE_URL
#   CFG_WEAVIATE_COLLECTION_NAME (derived)
#   CFG_COMPOSE_PROFILES (derived)
# ═════════════════════════════════════════════════════════════════════════════

# ─── Validators ──────────────────────────────────────────────────────────────

_is_slug() {
    [[ "$1" =~ ^[a-z0-9][a-z0-9-]*[a-z0-9]$ ]]
}

validate_slug() {
    local s="$1"
    if _is_slug "$s"; then
        return 0
    fi
    err "$(t cfg_course_id_invalid)"
    return 1
}

# German-friendly transliteration for slugs: ä/ö/ü/ß, lowercase, spaces and
# anything else non-slug-safe collapsed to single hyphens.
transliterate_slug() {
    local s="$1"
    s="${s//ä/ae}"; s="${s//ö/oe}"; s="${s//ü/ue}"; s="${s//ß/ss}"
    s="${s//Ä/Ae}"; s="${s//Ö/Oe}"; s="${s//Ü/Ue}"
    s="${s,,}"
    s="$(printf '%s' "$s" | tr -c 'a-z0-9' '-')"
    while [[ "$s" == *--* ]]; do s="${s//--/-}"; done
    s="${s#-}"; s="${s%-}"
    printf '%s' "$s"
}

# Prompts for a slug value (course-id). If the raw input isn't already a
# valid slug, offers an auto-transliterated suggestion (ä→ae etc.) instead
# of just failing — most invalid input here is German Umlaute, not typos.
prompt_slug() {
    local key="$1" default="$2"
    local input suggestion
    while true; do
        input="$(prompt "$key" "$default")" || return 1
        if _is_slug "$input"; then
            printf '%s' "$input"
            return 0
        fi
        suggestion="$(transliterate_slug "$input")"
        if [[ -n "$suggestion" ]] && _is_slug "$suggestion"; then
            info "$(t cfg_course_id_suggest "$suggestion")" >&2
            if confirm cfg_course_id_use_suggestion "y"; then
                printf '%s' "$suggestion"
                return 0
            fi
            (( WIZARD_BACK )) && return 1
        else
            err "$(t cfg_course_id_invalid)"
        fi
    done
}

validate_fqdn() {
    local d="$1"
    # Must contain at least one dot, lowercase + digits + hyphens + dots
    if [[ "$d" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]]; then
        return 0
    fi
    err "$(t cfg_domain_invalid)"
    return 1
}

validate_email() {
    local e="$1"
    if [[ "$e" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
        return 0
    fi
    err "$(t cfg_email_invalid)"
    return 1
}

validate_positive_int() {
    local n="$1"
    if [[ "$n" =~ ^[0-9]+$ ]] && (( n > 0 )); then
        return 0
    fi
    err "$(t val_dimensions_num)"
    return 1
}

validate_url() {
    local u="$1"
    if [[ "$u" =~ ^https?:// ]]; then
        return 0
    fi
    err "URL must start with http:// or https://"
    return 1
}


# ─── Derivation helpers ──────────────────────────────────────────────────────

# course-id → PascalCase + "Chunks" suffix
# "intro-research-methods" → "IntroResearchMethodsChunks"
derive_collection_name() {
    local slug="$1"
    local IFS='-'
    local parts=($slug)
    local out=""
    for p in "${parts[@]}"; do
        out="${out}${p^}"   # capitalize first letter
    done
    echo "${out}Chunks"
}

# Auto-fill embedding dimensions for known models
known_embedding_dimensions() {
    local model="$1"
    case "$model" in
        text-embedding-3-small)      echo 1536 ;;
        text-embedding-3-large)      echo 3072 ;;
        text-embedding-ada-002)      echo 1536 ;;
        embed-multilingual-v3.0)     echo 1024 ;;
        embed-english-v3.0)          echo 1024 ;;
        text-embedding-004)          echo 768  ;;
        mistral-embed)               echo 1024 ;;
        *)                           echo ""   ;;
    esac
}

# Suggest default model names per provider
default_llm_model_strong() {
    case "$1" in
        anthropic)  echo "claude-sonnet-4-5" ;;
        openai)     echo "gpt-4o" ;;
        google)     echo "gemini-2.5-pro" ;;
        mistral)    echo "mistral-large-latest" ;;
        cohere)     echo "command-r-plus" ;;
        openrouter) echo "anthropic/claude-sonnet-4.5" ;;
        custom)     echo "llama-3.1-70b" ;;
        *)          echo "" ;;
    esac
}
default_llm_model_fast() {
    case "$1" in
        anthropic)  echo "claude-haiku-4-5" ;;
        openai)     echo "gpt-4o-mini" ;;
        google)     echo "gemini-2.0-flash-lite" ;;
        mistral)    echo "mistral-small-latest" ;;
        cohere)     echo "command-r" ;;
        openrouter) echo "anthropic/claude-haiku-4.5" ;;
        custom)     echo "llama-3.1-8b" ;;
        *)          echo "" ;;
    esac
}
# Curated model choices per provider ("|"-separated). Empty = no curated list
# (falls straight through to free-text entry). Update periodically — this is
# a convenience shortlist, not an exhaustive/live-fetched catalog (see
# cfg_model_custom for manual override, always available).
llm_model_choices_strong() {
    case "$1" in
        anthropic)  echo "claude-sonnet-4-5|claude-opus-4-8" ;;
        openai)     echo "gpt-4o|gpt-4.1|o3" ;;
        google)     echo "gemini-2.5-pro|gemini-2.5-flash" ;;
        mistral)    echo "mistral-large-latest|mistral-medium-latest" ;;
        cohere)     echo "command-r-plus|command-r" ;;
        openrouter) echo "anthropic/claude-sonnet-4.5|openai/gpt-4o|google/gemini-2.5-pro" ;;
        *)          echo "" ;;
    esac
}
llm_model_choices_fast() {
    case "$1" in
        anthropic)  echo "claude-haiku-4-5" ;;
        openai)     echo "gpt-4o-mini|gpt-4.1-mini" ;;
        google)     echo "gemini-2.0-flash-lite|gemini-2.5-flash" ;;
        mistral)    echo "mistral-small-latest" ;;
        cohere)     echo "command-r" ;;
        openrouter) echo "anthropic/claude-haiku-4.5|openai/gpt-4o-mini" ;;
        *)          echo "" ;;
    esac
}

# Checks whether model_id appears in the provider's live /models list.
# Returns: 0 = found, 1 = not found (genuine mismatch), 2 = couldn't check
# (network/API error, missing jq, unsupported provider — never block on this,
# just skip validation). Never prints anything — pure exit-code contract, safe
# to call from functions whose stdout is captured via command substitution.
# The API key travels via header/Authorization everywhere, never a URL query
# param, so it never ends up visible in `ps aux` output.
validate_model_id() {
    local provider="$1" api_key="$2" model_id="$3"
    local resp found=1

    # NOTE: every jq call below is the condition of an `if` — bootstrap.sh
    # runs under `set -e`, and a bare `jq -e ...` statement returning 1 (the
    # normal, expected outcome for "model not found") would otherwise abort
    # the entire script. `if jq ...; then` is exempt from errexit regardless
    # of the command's exit status, which is exactly what we need here.
    case "$provider" in
        anthropic)
            resp="$(curl -sf --max-time 8 -H "x-api-key: $api_key" -H "anthropic-version: 2023-06-01" \
                "https://api.anthropic.com/v1/models?limit=1000" 2>/dev/null)" || return 2
            if jq -e --arg m "$model_id" 'any(.data[]?; .id == $m)' >/dev/null 2>&1 <<<"$resp"; then
                found=0
            fi
            ;;
        openai)
            resp="$(curl -sf --max-time 8 -H "Authorization: Bearer $api_key" \
                "https://api.openai.com/v1/models" 2>/dev/null)" || return 2
            if jq -e --arg m "$model_id" 'any(.data[]?; .id == $m)' >/dev/null 2>&1 <<<"$resp"; then
                found=0
            fi
            ;;
        google)
            resp="$(curl -sf --max-time 8 -H "x-goog-api-key: $api_key" \
                "https://generativelanguage.googleapis.com/v1beta/models" 2>/dev/null)" || return 2
            if jq -e --arg m "models/$model_id" 'any(.models[]?; .name == $m)' >/dev/null 2>&1 <<<"$resp"; then
                found=0
            fi
            ;;
        mistral)
            resp="$(curl -sf --max-time 8 -H "Authorization: Bearer $api_key" \
                "https://api.mistral.ai/v1/models" 2>/dev/null)" || return 2
            if jq -e --arg m "$model_id" 'any(.data[]?; .id == $m)' >/dev/null 2>&1 <<<"$resp"; then
                found=0
            fi
            ;;
        cohere)
            resp="$(curl -sf --max-time 8 -H "Authorization: Bearer $api_key" \
                "https://api.cohere.com/v1/models" 2>/dev/null)" || return 2
            if jq -e --arg m "$model_id" 'any(.models[]?; .name == $m)' >/dev/null 2>&1 <<<"$resp"; then
                found=0
            fi
            ;;
        openrouter)
            # Public endpoint, no auth needed.
            resp="$(curl -sf --max-time 8 "https://openrouter.ai/api/v1/models" 2>/dev/null)" || return 2
            if jq -e --arg m "$model_id" 'any(.data[]?; .id == $m)' >/dev/null 2>&1 <<<"$resp"; then
                found=0
            fi
            ;;
        *)
            return 2   # custom / unknown provider — cannot validate
            ;;
    esac

    (( found == 0 )) && return 0
    return 1
}

# Prompts for a model name and validates it against the provider's live model
# list (skips validation entirely for provider=custom, or if no API key is
# available yet). Loops until the user either enters a model the provider
# actually recognizes, or explicitly confirms using an unrecognized one
# (covers brand-new/preview models not yet reflected in the list endpoint).
prompt_and_validate_model() {
    local msg_key="$1" default_model="$2" provider="$3" api_key="$4"
    local model_id vstatus
    while true; do
        model_id="$(prompt "$msg_key" "$default_model")" || return 1
        if [[ "$provider" == "custom" || -z "$api_key" ]]; then
            printf '%s' "$model_id"
            return 0
        fi
        # `if fn; then vstatus=0; else vstatus=$?; fi` (not a bare call) so
        # set -e never triggers on the expected "not found" (1) outcome.
        if validate_model_id "$provider" "$api_key" "$model_id"; then
            vstatus=0
        else
            vstatus=$?
        fi
        if (( vstatus == 0 )); then
            ok "$(t cfg_model_validated "$model_id")" >&2
            printf '%s' "$model_id"
            return 0
        elif (( vstatus == 2 )); then
            warn "$(t cfg_model_validate_unreachable)" >&2
            printf '%s' "$model_id"
            return 0
        else
            warn "$(t cfg_model_validate_notfound "$model_id")" >&2
            if confirm cfg_model_validate_use_anyway "n"; then
                printf '%s' "$model_id"
                return 0
            fi
            (( WIZARD_BACK )) && return 1
            # loop — re-prompt
        fi
    done
}

# Ask for a model name: curated select-list (+ custom escape hatch) when we
# have one for this provider/tier, otherwise straight free-text entry (which
# then goes through prompt_and_validate_model). Curated entries are never
# validated — they're our own hardcoded strings, no typo risk from the user.
# ─── Section: Deployment mode ────────────────────────────────────────────────
# Asked first, because everything after it depends on the answer: whether a
# domain is needed at all, whether DNS and certificates are checked, whether
# nginx is deployed.
ask_deployment_mode() {
    header "$(t cfg_section_mode)"
    info "$(t cfg_mode_intro)"
    echo

    local choice
    choice="$(select_one_index cfg_mode_choice \
        "$(t cfg_mode_domain)" \
        "$(t cfg_mode_tailscale)")" || return 1

    case "$choice" in
        1) CFG_DEPLOYMENT_MODE="domain" ;;
        2)
            CFG_DEPLOYMENT_MODE="tailscale"
            echo
            warn "$(t cfg_mode_tailscale_warning)"
            dim "$(t cfg_mode_tailscale_lti)"
            echo
            info "$(t cfg_mode_tailscale_prereq)"
            # Not dimmed: these are conditions, not asides. Prerequisite 4 in
            # particular has to land before the mode is chosen — an operator
            # who learns only afterwards that their own machine needs
            # Tailscale sees "Secure Connection Failed" on the admin URLs and
            # reasonably concludes it is a firewall or router problem.
            printf "    %s\n" "$(t cfg_mode_tailscale_prereq_1)"
            printf "    %s\n" "$(t cfg_mode_tailscale_prereq_2)"
            printf "    %s\n" "$(t cfg_mode_tailscale_prereq_3)"
            printf "    ${BOLD}%s${RESET}\n" "$(t cfg_mode_tailscale_prereq_4)"
            echo
            confirm cfg_mode_tailscale_ready "y" || return 1

            # Join now, not later. Two reasons, and the second matters more:
            # this is the moment the operator has a browser open because we
            # just asked them to — and every public URL in .env is derived
            # from the MagicDNS name, so knowing it before .env is written
            # means the file is correct the first time instead of being
            # patched afterwards with a restart of every container.
            #
            # It is also the earliest point at which a tailnet without
            # MagicDNS or HTTPS fails — before twenty more questions have
            # been answered into a configuration that cannot work.
            echo
            local ts_name
            if ! ts_name="$(tailscale_ensure_up)"; then
                echo
                err "$(t cfg_mode_tailscale_failed)"
                return 1   # back to the mode question; domain mode still works
            fi
            CFG_TAILSCALE_HOSTNAME="$ts_name"
            CFG_DOMAIN="$ts_name"
            ok "$(t cfg_mode_tailscale_host "$ts_name")"
            ;;
    esac
    dim "$(t cfg_mode_chosen "$CFG_DEPLOYMENT_MODE")"
}


# Args: $1=provider  $2=strong|fast  $3=message key  $4=api_key
ask_model_choice() {
    local provider="$1" tier="$2" msg_key="$3" api_key="$4"
    local choices default_model

    if [[ "$tier" == "strong" ]]; then
        choices="$(llm_model_choices_strong "$provider")"
        default_model="$(default_llm_model_strong "$provider")"
    else
        choices="$(llm_model_choices_fast "$provider")"
        default_model="$(default_llm_model_fast "$provider")"
    fi

    if [[ -z "$choices" ]]; then
        prompt_and_validate_model "$msg_key" "$default_model" "$provider" "$api_key"
        return
    fi

    local IFS='|'
    local opts=($choices)
    unset IFS
    opts+=("$(t cfg_model_custom)")

    local selected
    selected="$(select_one "$msg_key" "${opts[@]}")" || return 1
    if [[ "$selected" == "$(t cfg_model_custom)" ]]; then
        prompt_and_validate_model "$msg_key" "$default_model" "$provider" "$api_key"
    else
        printf '%s' "$selected"
    fi
}

default_embedding_model() {
    case "$1" in
        openai)   echo "text-embedding-3-small" ;;
        cohere)   echo "embed-multilingual-v3.0" ;;
        google)   echo "text-embedding-004" ;;
        mistral)  echo "mistral-embed" ;;
        custom)   echo "" ;;
        *)        echo "" ;;
    esac
}


# ─── Section: Course & deployment ────────────────────────────────────────────
# Each field uses `|| return 1` so a "back" request (WIZARD_BACK, see common.sh)
# aborts the section immediately and bubbles up to run_config_wizard's step
# loop. Defaults read from previously-entered CFG_* values so re-entering a
# section after going back doesn't lose earlier answers.
ask_course_info() {
    header "$(t cfg_section_course)"

    CFG_COURSE_NAME="$(prompt cfg_course_name "${CFG_COURSE_NAME:-My Course}")" || return 1
    CFG_COURSE_ID="$(prompt_slug cfg_course_id "${CFG_COURSE_ID:-my-course}")" || return 1

    # In tailscale mode there is no domain to ask for: the hostname is the
    # machine's MagicDNS name, which Tailscale assigns once it is up. It is
    # filled in by install-tailscale.sh, not here.
    if [[ "${CFG_DEPLOYMENT_MODE:-domain}" == "tailscale" ]]; then
        # Already set from the MagicDNS name in ask_deployment_mode.
        CFG_DOMAIN="${CFG_TAILSCALE_HOSTNAME:-}"
        dim "$(t cfg_domain_from_tailscale "$CFG_DOMAIN")"
    else
        # Try to pre-fill domain from reverse DNS — user always confirms
        local domain_default="${CFG_DOMAIN:-example.com}"
        if [[ -z "${CFG_DOMAIN:-}" ]]; then
            local detected_domain
            detected_domain="$(detect_base_domain 2>/dev/null || true)"
            if [[ -n "$detected_domain" ]]; then
                info "$(t cfg_domain_detected "$detected_domain")" >&2
                domain_default="$detected_domain"
            fi
        fi
        CFG_DOMAIN="$(prompt cfg_domain "$domain_default" validate_fqdn)" || return 1
    fi

    CFG_ADMIN_EMAIL="$(prompt cfg_admin_email "${CFG_ADMIN_EMAIL:-}" validate_email)" || return 1
    info "$(t cfg_base_data_path_explain)"
    CFG_BASE_DATA_PATH="$(prompt cfg_base_data_path "${CFG_BASE_DATA_PATH:-/srv/smart-rag/data}")" || return 1
    CFG_TZ="$(prompt cfg_tz "${CFG_TZ:-Europe/Berlin}")" || return 1

    CFG_WEAVIATE_COLLECTION_NAME="$(derive_collection_name "$CFG_COURSE_ID")"
    dim "Weaviate collection name: $CFG_WEAVIATE_COLLECTION_NAME"
}


# ─── Section: Compose profiles ───────────────────────────────────────────────
ask_profiles() {
    header "$(t cfg_section_profiles)"

    local profiles="core"

    if confirm cfg_enable_observability "y"; then
        CFG_ENABLE_OBSERVABILITY="yes"
        profiles="${profiles},observability"
    else
        (( WIZARD_BACK )) && return 1
        CFG_ENABLE_OBSERVABILITY="no"
    fi

    if confirm cfg_enable_lti "n"; then
        CFG_ENABLE_LTI="yes"
        profiles="${profiles},lti"
        CFG_LMS_URL="$(prompt cfg_lms_url "${CFG_LMS_URL:-https://lms.example.com}" validate_url)" || return 1
    else
        (( WIZARD_BACK )) && return 1
        CFG_ENABLE_LTI="no"
        CFG_LMS_URL="https://lms.example.com"
    fi

    CFG_COMPOSE_PROFILES="$profiles"
    dim "Compose profiles: $profiles"

    # Now that the profiles are known, the RAM requirement is a concrete
    # number rather than a range — so this is the first point where the
    # question can actually be put. Declining returns 1, which the wizard
    # treats as "go back", letting the operator pick a lighter profile set
    # instead of being dropped out of the installer.
    confirm_memory_for_profiles "$profiles" || return 1
}


# ─── Section: LLM ────────────────────────────────────────────────────────────
# API key is asked BEFORE the model name (not after, as it used to be) — model
# selection now validates custom-typed entries against the provider's live
# /models list, which needs the key. Curated shortlist entries skip
# validation (they're our own strings, no typo risk).
ask_llm_config() {
    header "$(t cfg_section_llm)"

    CFG_LLM_PROVIDER="$(select_one cfg_llm_provider \
        anthropic openai google mistral cohere openrouter custom)" || return 1

    CFG_LLM_API_KEY="$(prompt_password cfg_llm_api_key "${CFG_LLM_API_KEY:-}")" || return 1

    if [[ "$CFG_LLM_PROVIDER" == "custom" ]]; then
        CFG_LLM_BASE_URL="$(prompt cfg_llm_base_url "${CFG_LLM_BASE_URL:-}" validate_url)" || return 1
    else
        CFG_LLM_BASE_URL=""
    fi

    printf "  ${BOLD}%s${RESET}\n" "$(t cfg_llm_tiers_explain)"
    CFG_LLM_MODEL_STRONG="$(ask_model_choice "$CFG_LLM_PROVIDER" strong cfg_llm_model_strong "$CFG_LLM_API_KEY")" || return 1
    CFG_LLM_MODEL_FAST="$(ask_model_choice "$CFG_LLM_PROVIDER" fast cfg_llm_model_fast "$CFG_LLM_API_KEY")" || return 1
}


# ─── Section: Embedding ──────────────────────────────────────────────────────
# API key now comes before the model name for the same reason as in
# ask_llm_config(): the model name gets validated against the provider's live
# /models list, which needs the key already in hand.
ask_embedding_config() {
    header "$(t cfg_section_embedding)"
    printf "  ${YELLOW}${BOLD}%s${RESET}\n" "$(t cfg_embed_warning_bold)"
    printf "  ${DIM}%s${RESET}\n\n" "$(t cfg_embed_warning)"

    CFG_EMBEDDING_PROVIDER="$(select_one cfg_embed_provider \
        openai cohere google mistral custom)" || return 1

    # If same provider as LLM, offer to reuse the API key
    if [[ "$CFG_EMBEDDING_PROVIDER" == "$CFG_LLM_PROVIDER" ]]; then
        CFG_EMBEDDING_API_KEY="$CFG_LLM_API_KEY"
        dim "Reusing LLM API key (same provider)"
    else
        CFG_EMBEDDING_API_KEY="$(prompt_password cfg_embed_api_key "${CFG_EMBEDDING_API_KEY:-}")" || return 1
    fi

    if [[ "$CFG_EMBEDDING_PROVIDER" == "custom" ]]; then
        CFG_EMBEDDING_BASE_URL="$(prompt cfg_embed_base_url "${CFG_EMBEDDING_BASE_URL:-}" validate_url)" || return 1
    else
        CFG_EMBEDDING_BASE_URL=""
    fi

    local d_model
    d_model="$(default_embedding_model "$CFG_EMBEDDING_PROVIDER")"
    CFG_EMBEDDING_MODEL="$(prompt_and_validate_model cfg_embed_model "${CFG_EMBEDDING_MODEL:-$d_model}" \
        "$CFG_EMBEDDING_PROVIDER" "$CFG_EMBEDDING_API_KEY")" || return 1

    # Auto-suggest dimensions if model is known
    local known_dims
    known_dims="$(known_embedding_dimensions "$CFG_EMBEDDING_MODEL")"
    if [[ -n "$known_dims" ]]; then
        info "$(t cfg_embed_dims_known "$known_dims")"
        CFG_EMBEDDING_DIMENSIONS="$(prompt cfg_embed_dimensions "$known_dims" validate_positive_int)" || return 1
    else
        CFG_EMBEDDING_DIMENSIONS="$(prompt cfg_embed_dimensions "${CFG_EMBEDDING_DIMENSIONS:-1536}" validate_positive_int)" || return 1
    fi
}


# ─── Section: Reranker ───────────────────────────────────────────────────────
# "none" is the default (first option) — most users testing the wizard won't
# have a reranker API key on hand yet, and cohere requires one immediately
# after selection. Cohere remains recommended in the intro text for whoever
# does have a key.
ask_reranker_config() {
    header "$(t cfg_section_reranker)"
    printf "  ${DIM}%s${RESET}\n\n" "$(t cfg_reranker_intro)"

    CFG_RERANKER_PROVIDER="$(select_one cfg_reranker_provider \
        none cohere custom)" || return 1

    case "$CFG_RERANKER_PROVIDER" in
        cohere)
            CFG_RERANKER_MODEL="$(prompt cfg_reranker_model "${CFG_RERANKER_MODEL:-rerank-multilingual-v3.0}")" || return 1
            CFG_RERANKER_API_KEY="$(prompt_password cfg_reranker_api_key "${CFG_RERANKER_API_KEY:-}")" || return 1
            CFG_RERANKER_BASE_URL=""
            ;;
        custom)
            CFG_RERANKER_MODEL="$(prompt cfg_reranker_model "${CFG_RERANKER_MODEL:-}")" || return 1
            CFG_RERANKER_BASE_URL="$(prompt cfg_reranker_base_url "${CFG_RERANKER_BASE_URL:-}" validate_url)" || return 1
            CFG_RERANKER_API_KEY="$(prompt_password cfg_reranker_api_key "${CFG_RERANKER_API_KEY:-}")" || return 1
            ;;
        none)
            CFG_RERANKER_MODEL=""
            CFG_RERANKER_API_KEY=""
            CFG_RERANKER_BASE_URL=""
            ;;
    esac
}


# ─── Section: Mail relay (SMTP) ──────────────────────────────────────────────
# Strongly recommended, not required — a user can decline and fix it later by
# editing .env directly. Two paths:
#   a) Local Postfix (recommended): collects the *upstream* smarthost's
#      credentials into SMTP_RELAY_* (consumed by install-postfix.sh later);
#      the apps themselves are pointed at the pinned Docker gateway IP,
#      unauthenticated (Postfix holds the real credentials, not the apps).
#   b) Direct: apps connect straight to an external relay with SMTP_*.
# Either way we also derive SMTP_CONNECTION_URL for Langfuse, which wants a
# single URL rather than discrete host/port/user/pass fields.
#
# Must match the pinned subnet/gateway in docker/docker-compose.yml exactly.
readonly SMARTRAG_DOCKER_GATEWAY="172.28.92.1"

_reset_mail_config_disabled() {
    CFG_INSTALL_POSTFIX="false"
    CFG_SMTP_RELAY_HOST=""
    CFG_SMTP_RELAY_PORT="587"
    CFG_SMTP_RELAY_USER=""
    CFG_SMTP_RELAY_PASSWORD=""
    CFG_SMTP_HOST=""
    CFG_SMTP_PORT="25"
    CFG_SMTP_SECURE="false"
    CFG_SMTP_USER=""
    CFG_SMTP_PASSWORD=""
    CFG_N8N_EMAIL_MODE=""
    CFG_SMTP_CONNECTION_URL=""
}

# Asks the provider's host, port, encryption and credentials — the same four
# questions whether they end up in Postfix's configuration or in .env, so the
# two paths below share them. Known providers prefill what they document,
# because the usual failure is not a wrong password but a wrong port with the
# wrong encryption.
#
# Results land in _MP_*; the caller copies them where they belong.
_ask_mail_provider() {
    _MP_HOST=""; _MP_PORT="587"; _MP_SECURE="false"; _MP_USER=""; _MP_PASSWORD=""
    local hint=""

    local which
    which="$(select_one_index cfg_mail_provider_choice \
        "$(t cfg_mail_provider_brevo)" \
        "$(t cfg_mail_provider_manual)")" || return 1
    if [[ "$which" == "1" ]]; then
        # Verified against Brevo's own documentation: STARTTLS on 587, the
        # user name is the login address, and the password is an SMTP key
        # generated in the dashboard — not the API key and not the account
        # password, which is the mistake that costs an afternoon.
        _MP_HOST="smtp-relay.brevo.com"
        _MP_PORT="587"
        _MP_SECURE="false"
        hint="cfg_mail_brevo_hint"
    fi

    [[ -n "$hint" ]] && info "$(t "$hint")"
    _MP_HOST="$(prompt cfg_mail_host "$_MP_HOST")" || return 1
    _MP_PORT="$(prompt cfg_mail_port "$_MP_PORT" validate_positive_int)" || return 1
    # 465 is implicit TLS from the first byte; 587 and 25 start in the clear
    # and upgrade. Asking rather than deriving, because a provider may differ
    # — but the default follows the port, which is right almost always.
    local secure_default="n"
    [[ "$_MP_PORT" == "465" ]] && secure_default="y"
    if confirm cfg_mail_secure "$secure_default"; then
        _MP_SECURE="true"
    else
        (( WIZARD_BACK )) && return 1
        _MP_SECURE="false"
    fi
    _MP_USER="$(prompt cfg_mail_user "$_MP_USER")" || return 1
    if [[ -n "$_MP_USER" ]]; then
        _MP_PASSWORD="$(prompt_password cfg_mail_password "")" || return 1
    fi
    return 0
}

ask_mail_config() {
    header "$(t cfg_section_mail)"
    printf "  ${DIM}%s${RESET}\n\n" "$(t cfg_mail_intro)"

    # Detection first, so the menu can say "we found one" rather than making
    # the operator remember. See detect_existing_mail_relay in preflight.sh.
    local detection mta port_listening
    detection="$(detect_existing_mail_relay)"
    mta="${detection%%:*}"
    port_listening="${detection##*:}"
    if [[ "$mta" != "none" ]]; then
        info "$(t cfg_mail_detected_mta "$mta")"
    elif [[ "$port_listening" == "1" ]]; then
        info "$(t cfg_mail_detected_port25)"
    fi

    # One question with four answers, instead of three yes/no questions whose
    # "no" meant something different each time. The old sequence asked
    # "configure a relay?", then "install Postfix?", and a no to the second
    # silently meant "then an external server" — after which a bare "SMTP
    # host:" appeared with nothing saying whose host it wanted.
    local choice
    choice="$(select_one_index cfg_mail_how \
        "$(t cfg_mail_how_existing)" \
        "$(t cfg_mail_how_postfix)" \
        "$(t cfg_mail_how_direct)" \
        "$(t cfg_mail_how_none)")" || return 1

    case "$choice" in
        1)
            # Whatever is already running — Postfix, OpenSMTPD, Exim, the
            # institution's own. We install nothing and point the containers
            # at the Docker gateway, because from inside a container
            # "localhost" is the container.
            _reset_mail_config_disabled
            CFG_INSTALL_POSTFIX="false"
            CFG_SMTP_HOST="$SMARTRAG_DOCKER_GATEWAY"
            CFG_SMTP_PORT="25"
            CFG_SMTP_SECURE="false"
            CFG_SMTP_USER=""
            CFG_SMTP_PASSWORD=""
            info "$(t cfg_mail_existing_pointed "$SMARTRAG_DOCKER_GATEWAY")"
            warn "$(t cfg_mail_existing_requirement)"
            ;;
        2)
            CFG_INSTALL_POSTFIX="true"
            _ask_mail_provider || return 1
            CFG_SMTP_RELAY_HOST="$_MP_HOST"
            CFG_SMTP_RELAY_PORT="$_MP_PORT"
            CFG_SMTP_RELAY_USER="$_MP_USER"
            CFG_SMTP_RELAY_PASSWORD="$_MP_PASSWORD"
            # The apps talk to local Postfix, unauthenticated, on the pinned
            # gateway. Postfix holds the provider's credentials; no
            # application ever sees them.
            CFG_SMTP_HOST="$SMARTRAG_DOCKER_GATEWAY"
            CFG_SMTP_PORT="25"
            CFG_SMTP_SECURE="false"
            CFG_SMTP_USER=""
            CFG_SMTP_PASSWORD=""
            ;;
        3)
            CFG_INSTALL_POSTFIX="false"
            CFG_SMTP_RELAY_HOST=""; CFG_SMTP_RELAY_PORT="587"
            CFG_SMTP_RELAY_USER=""; CFG_SMTP_RELAY_PASSWORD=""
            _ask_mail_provider || return 1
            CFG_SMTP_HOST="$_MP_HOST"
            CFG_SMTP_PORT="$_MP_PORT"
            CFG_SMTP_SECURE="$_MP_SECURE"
            CFG_SMTP_USER="$_MP_USER"
            CFG_SMTP_PASSWORD="$_MP_PASSWORD"
            warn "$(t cfg_mail_direct_note)"
            ;;
        *)
            _reset_mail_config_disabled
            info "$(t cfg_mail_none_note)"
            return 0
            ;;
    esac

    CFG_N8N_EMAIL_MODE="smtp"

    # Derive Langfuse's SMTP_CONNECTION_URL (smtp://[user:pass@]host:port).
    # Credentials must be URL-encoded — a raw ':' or '@' in a password would
    # otherwise break URL parsing.
    local scheme="smtp"
    [[ "$CFG_SMTP_SECURE" == "true" ]] && scheme="smtps"
    if [[ -n "$CFG_SMTP_USER" ]]; then
        CFG_SMTP_CONNECTION_URL="${scheme}://$(url_encode "$CFG_SMTP_USER"):$(url_encode "$CFG_SMTP_PASSWORD")@${CFG_SMTP_HOST}:${CFG_SMTP_PORT}"
    else
        CFG_SMTP_CONNECTION_URL="${scheme}://${CFG_SMTP_HOST}:${CFG_SMTP_PORT}"
    fi
}

# ─── Review & confirm ────────────────────────────────────────────────────────
show_config_summary() {
    header "$(t cfg_review_title)"
    cat <<EOF
  Course:           $CFG_COURSE_NAME ($CFG_COURSE_ID)
  Domain:           $CFG_DOMAIN
  Admin email:      $CFG_ADMIN_EMAIL
  Data path:        $CFG_BASE_DATA_PATH
  Timezone:         $CFG_TZ
  Profiles:         $CFG_COMPOSE_PROFILES

  LLM provider:     $CFG_LLM_PROVIDER
  LLM strong:       $CFG_LLM_MODEL_STRONG
  LLM fast:         $CFG_LLM_MODEL_FAST
  LLM key:          ${CFG_LLM_API_KEY:0:8}…  (${#CFG_LLM_API_KEY} chars)

  Embedding:        $CFG_EMBEDDING_PROVIDER / $CFG_EMBEDDING_MODEL
  Embed dims:       $CFG_EMBEDDING_DIMENSIONS
  Embed key:        ${CFG_EMBEDDING_API_KEY:0:8}…  (${#CFG_EMBEDDING_API_KEY} chars)

  Reranker:         $CFG_RERANKER_PROVIDER${CFG_RERANKER_MODEL:+ / $CFG_RERANKER_MODEL}

  Weaviate coll:    $CFG_WEAVIATE_COLLECTION_NAME
EOF
    if [[ "$CFG_INSTALL_POSTFIX" == "true" ]]; then
        echo "  Mail relay:       local Postfix → $CFG_SMTP_RELAY_HOST:$CFG_SMTP_RELAY_PORT"
    elif [[ -n "$CFG_SMTP_HOST" ]]; then
        echo "  Mail relay:       direct → $CFG_SMTP_HOST:$CFG_SMTP_PORT"
    else
        echo "  Mail relay:       disabled (no password-reset emails)"
    fi
    if [[ "$CFG_ENABLE_LTI" == "yes" ]]; then
        echo "  LMS URL:          $CFG_LMS_URL"
    fi
    echo
}


# Runs the ordered wizard sections starting at step index $1. Each ask_*
# function returns 1 when the user typed "back"/"zurück" on one of its fields
# (see common.sh WIZARD_BACK) — in that case we re-run the previous section
# instead of advancing. Sections read their own CFG_* globals as defaults, so
# nothing entered earlier is lost when stepping back and forward again.
_wizard_step_loop() {
    local i="$1"
    local steps=(ask_deployment_mode ask_course_info ask_profiles ask_llm_config ask_embedding_config ask_reranker_config ask_mail_config)
    local n=${#steps[@]}
    while (( i < n )); do
        if "${steps[$i]}"; then
            i=$((i+1))
        else
            (( i > 0 )) && i=$((i-1))
        fi
    done
}

# ─── Master wizard ───────────────────────────────────────────────────────────
run_config_wizard() {
    printf "%s\n\n" "$(t cfg_intro)"
    info "$(t cfg_back_hint)"

    _wizard_step_loop 0

    while true; do
        show_config_summary
        if confirm cfg_review_confirm "y"; then
            return 0
        elif (( WIZARD_BACK )); then
            _wizard_step_loop 6   # re-enter at the last section (mail relay)
        else
            die "$(t cfg_aborted)"
        fi
    done
}
