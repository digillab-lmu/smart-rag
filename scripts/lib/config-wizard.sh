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

validate_slug() {
    local s="$1"
    if [[ "$s" =~ ^[a-z0-9][a-z0-9-]*[a-z0-9]$ ]]; then
        return 0
    fi
    err "$(t cfg_course_id_invalid)"
    return 1
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

# Ask for a model name: curated select-list (+ custom escape hatch) when we
# have one for this provider/tier, otherwise straight free-text entry.
# Args: $1=provider  $2=strong|fast  $3=message key (for prompt/header text)
ask_model_choice() {
    local provider="$1" tier="$2" msg_key="$3"
    local choices default_model

    if [[ "$tier" == "strong" ]]; then
        choices="$(llm_model_choices_strong "$provider")"
        default_model="$(default_llm_model_strong "$provider")"
    else
        choices="$(llm_model_choices_fast "$provider")"
        default_model="$(default_llm_model_fast "$provider")"
    fi

    if [[ -z "$choices" ]]; then
        prompt "$msg_key" "$default_model"
        return
    fi

    local IFS='|'
    local opts=($choices)
    unset IFS
    opts+=("$(t cfg_model_custom)")

    local selected
    selected="$(select_one "$msg_key" "${opts[@]}")" || return 1
    if [[ "$selected" == "$(t cfg_model_custom)" ]]; then
        prompt "$msg_key" "$default_model"
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
    CFG_COURSE_ID="$(prompt cfg_course_id "${CFG_COURSE_ID:-my-course}" validate_slug)" || return 1

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

    CFG_ADMIN_EMAIL="$(prompt cfg_admin_email "${CFG_ADMIN_EMAIL:-}" validate_email)" || return 1
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
}


# ─── Section: LLM ────────────────────────────────────────────────────────────
ask_llm_config() {
    header "$(t cfg_section_llm)"

    CFG_LLM_PROVIDER="$(select_one cfg_llm_provider \
        anthropic openai google mistral cohere openrouter custom)" || return 1

    CFG_LLM_MODEL_STRONG="$(ask_model_choice "$CFG_LLM_PROVIDER" strong cfg_llm_model_strong)" || return 1
    CFG_LLM_MODEL_FAST="$(ask_model_choice "$CFG_LLM_PROVIDER" fast cfg_llm_model_fast)" || return 1

    CFG_LLM_API_KEY="$(prompt_password cfg_llm_api_key "${CFG_LLM_API_KEY:-}")" || return 1

    if [[ "$CFG_LLM_PROVIDER" == "custom" ]]; then
        CFG_LLM_BASE_URL="$(prompt cfg_llm_base_url "${CFG_LLM_BASE_URL:-}" validate_url)" || return 1
    else
        CFG_LLM_BASE_URL=""
    fi
}


# ─── Section: Embedding ──────────────────────────────────────────────────────
ask_embedding_config() {
    header "$(t cfg_section_embedding)"
    printf "  ${YELLOW}${BOLD}%s${RESET}\n" "$(t cfg_embed_warning_bold)"
    printf "  ${DIM}%s${RESET}\n\n" "$(t cfg_embed_warning)"

    CFG_EMBEDDING_PROVIDER="$(select_one cfg_embed_provider \
        openai cohere google mistral custom)" || return 1

    local d_model
    d_model="$(default_embedding_model "$CFG_EMBEDDING_PROVIDER")"
    CFG_EMBEDDING_MODEL="$(prompt cfg_embed_model "${CFG_EMBEDDING_MODEL:-$d_model}")" || return 1

    # Auto-suggest dimensions if model is known
    local known_dims
    known_dims="$(known_embedding_dimensions "$CFG_EMBEDDING_MODEL")"
    if [[ -n "$known_dims" ]]; then
        info "$(t cfg_embed_dims_known "$known_dims")"
        CFG_EMBEDDING_DIMENSIONS="$(prompt cfg_embed_dimensions "$known_dims" validate_positive_int)" || return 1
    else
        CFG_EMBEDDING_DIMENSIONS="$(prompt cfg_embed_dimensions "${CFG_EMBEDDING_DIMENSIONS:-1536}" validate_positive_int)" || return 1
    fi

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

ask_mail_config() {
    header "$(t cfg_section_mail)"
    printf "  ${YELLOW}${BOLD}%s${RESET}\n" "$(t cfg_mail_warning_bold)"
    printf "  ${DIM}%s${RESET}\n\n" "$(t cfg_mail_intro)"

    if ! confirm cfg_mail_enable "y"; then
        (( WIZARD_BACK )) && return 1
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
        return 0
    fi

    if confirm cfg_mail_use_postfix "y"; then
        CFG_INSTALL_POSTFIX="true"
        CFG_SMTP_RELAY_HOST="$(prompt cfg_mail_relay_host "${CFG_SMTP_RELAY_HOST:-}")" || return 1
        CFG_SMTP_RELAY_PORT="$(prompt cfg_mail_relay_port "${CFG_SMTP_RELAY_PORT:-587}" validate_positive_int)" || return 1
        if confirm cfg_mail_relay_auth "y"; then
            CFG_SMTP_RELAY_USER="$(prompt cfg_mail_relay_user "${CFG_SMTP_RELAY_USER:-}")" || return 1
            CFG_SMTP_RELAY_PASSWORD="$(prompt_password cfg_mail_relay_password "${CFG_SMTP_RELAY_PASSWORD:-}")" || return 1
        else
            (( WIZARD_BACK )) && return 1
            CFG_SMTP_RELAY_USER=""
            CFG_SMTP_RELAY_PASSWORD=""
        fi
        # Apps talk to local Postfix, unauthenticated, on the pinned gateway.
        CFG_SMTP_HOST="$SMARTRAG_DOCKER_GATEWAY"
        CFG_SMTP_PORT="25"
        CFG_SMTP_SECURE="false"
        CFG_SMTP_USER=""
        CFG_SMTP_PASSWORD=""
    else
        (( WIZARD_BACK )) && return 1
        CFG_INSTALL_POSTFIX="false"
        CFG_SMTP_RELAY_HOST=""
        CFG_SMTP_RELAY_PORT="587"
        CFG_SMTP_RELAY_USER=""
        CFG_SMTP_RELAY_PASSWORD=""

        CFG_SMTP_HOST="$(prompt cfg_mail_host "${CFG_SMTP_HOST:-}")" || return 1
        CFG_SMTP_PORT="$(prompt cfg_mail_port "${CFG_SMTP_PORT:-587}" validate_positive_int)" || return 1
        if confirm cfg_mail_secure "n"; then
            CFG_SMTP_SECURE="true"
        else
            (( WIZARD_BACK )) && return 1
            CFG_SMTP_SECURE="false"
        fi
        CFG_SMTP_USER="$(prompt cfg_mail_user "${CFG_SMTP_USER:-}")" || return 1
        if [[ -n "$CFG_SMTP_USER" ]]; then
            CFG_SMTP_PASSWORD="$(prompt_password cfg_mail_password "${CFG_SMTP_PASSWORD:-}")" || return 1
        else
            CFG_SMTP_PASSWORD=""
        fi
    fi

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
    local steps=(ask_course_info ask_profiles ask_llm_config ask_embedding_config ask_reranker_config ask_mail_config)
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
            _wizard_step_loop 5   # re-enter at the last section (mail relay)
        else
            die "$(t cfg_aborted)"
        fi
    done
}
