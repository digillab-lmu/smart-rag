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
    selected="$(select_one "$msg_key" "${opts[@]}")"
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
ask_course_info() {
    header "$(t cfg_section_course)"

    CFG_COURSE_NAME="$(prompt cfg_course_name "My Course")"
    CFG_COURSE_ID="$(prompt cfg_course_id "my-course" validate_slug)"

    # Try to pre-fill domain from reverse DNS — user always confirms
    local domain_default="example.com"
    local detected_domain
    detected_domain="$(detect_base_domain 2>/dev/null || true)"
    if [[ -n "$detected_domain" ]]; then
        info "$(t cfg_domain_detected "$detected_domain")" >&2
        domain_default="$detected_domain"
    fi
    CFG_DOMAIN="$(prompt cfg_domain "$domain_default" validate_fqdn)"

    CFG_ADMIN_EMAIL="$(prompt cfg_admin_email "" validate_email)"
    CFG_BASE_DATA_PATH="$(prompt cfg_base_data_path "/srv/smart-rag/data")"
    CFG_TZ="$(prompt cfg_tz "Europe/Berlin")"

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
        CFG_ENABLE_OBSERVABILITY="no"
    fi

    if confirm cfg_enable_lti "n"; then
        CFG_ENABLE_LTI="yes"
        profiles="${profiles},lti"
        CFG_LMS_URL="$(prompt cfg_lms_url "https://lms.example.com" validate_url)"
    else
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
        anthropic openai google mistral cohere openrouter custom)"

    CFG_LLM_MODEL_STRONG="$(ask_model_choice "$CFG_LLM_PROVIDER" strong cfg_llm_model_strong)"
    CFG_LLM_MODEL_FAST="$(ask_model_choice "$CFG_LLM_PROVIDER" fast cfg_llm_model_fast)"

    CFG_LLM_API_KEY="$(prompt_password cfg_llm_api_key)"

    if [[ "$CFG_LLM_PROVIDER" == "custom" ]]; then
        CFG_LLM_BASE_URL="$(prompt cfg_llm_base_url "" validate_url)"
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
        openai cohere google mistral custom)"

    local d_model
    d_model="$(default_embedding_model "$CFG_EMBEDDING_PROVIDER")"
    CFG_EMBEDDING_MODEL="$(prompt cfg_embed_model "$d_model")"

    # Auto-suggest dimensions if model is known
    local known_dims
    known_dims="$(known_embedding_dimensions "$CFG_EMBEDDING_MODEL")"
    if [[ -n "$known_dims" ]]; then
        info "$(t cfg_embed_dims_known "$known_dims")"
        CFG_EMBEDDING_DIMENSIONS="$(prompt cfg_embed_dimensions "$known_dims" validate_positive_int)"
    else
        CFG_EMBEDDING_DIMENSIONS="$(prompt cfg_embed_dimensions "1536" validate_positive_int)"
    fi

    # If same provider as LLM, offer to reuse the API key
    if [[ "$CFG_EMBEDDING_PROVIDER" == "$CFG_LLM_PROVIDER" ]]; then
        CFG_EMBEDDING_API_KEY="$CFG_LLM_API_KEY"
        dim "Reusing LLM API key (same provider)"
    else
        CFG_EMBEDDING_API_KEY="$(prompt_password cfg_embed_api_key)"
    fi

    if [[ "$CFG_EMBEDDING_PROVIDER" == "custom" ]]; then
        CFG_EMBEDDING_BASE_URL="$(prompt cfg_embed_base_url "" validate_url)"
    else
        CFG_EMBEDDING_BASE_URL=""
    fi
}


# ─── Section: Reranker ───────────────────────────────────────────────────────
ask_reranker_config() {
    header "$(t cfg_section_reranker)"
    printf "  ${DIM}%s${RESET}\n\n" "$(t cfg_reranker_intro)"

    CFG_RERANKER_PROVIDER="$(select_one cfg_reranker_provider \
        cohere custom none)"

    case "$CFG_RERANKER_PROVIDER" in
        cohere)
            CFG_RERANKER_MODEL="$(prompt cfg_reranker_model "rerank-multilingual-v3.0")"
            CFG_RERANKER_API_KEY="$(prompt_password cfg_reranker_api_key)"
            CFG_RERANKER_BASE_URL=""
            ;;
        custom)
            CFG_RERANKER_MODEL="$(prompt cfg_reranker_model "")"
            CFG_RERANKER_BASE_URL="$(prompt cfg_reranker_base_url "" validate_url)"
            CFG_RERANKER_API_KEY="$(prompt_password cfg_reranker_api_key)"
            ;;
        none)
            CFG_RERANKER_MODEL=""
            CFG_RERANKER_API_KEY=""
            CFG_RERANKER_BASE_URL=""
            ;;
    esac
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
    if [[ "$CFG_ENABLE_LTI" == "yes" ]]; then
        echo "  LMS URL:          $CFG_LMS_URL"
    fi
    echo
}


# ─── Master wizard ───────────────────────────────────────────────────────────
run_config_wizard() {
    printf "%s\n\n" "$(t cfg_intro)"
    ask_course_info
    ask_profiles
    ask_llm_config
    ask_embedding_config
    ask_reranker_config

    show_config_summary
    if ! confirm cfg_review_confirm "y"; then
        die "$(t cfg_aborted)"
    fi
}
