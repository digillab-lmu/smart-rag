# ═════════════════════════════════════════════════════════════════════════════
# config-wizard.sh — interactive .env configuration
# ═════════════════════════════════════════════════════════════════════════════
#
# Collects all user-configurable values via prompts and stores them in global
# CFG_* variables. The templates.sh module then writes them to .env.
#
# Globals set by run_config_wizard():
#   CFG_DOMAIN, CFG_ADMIN_EMAIL, CFG_BASE_DATA_PATH, CFG_TZ
#   (no course: courses are created in the Content Admin — see
#    ask_install_info for why the installer stopped asking)
#   CFG_ENABLE_OBSERVABILITY (yes|no), CFG_ENABLE_LTI (yes|no), CFG_LMS_URL
#   CFG_LLM_PROVIDER, CFG_LLM_MODEL_STRONG, CFG_LLM_MODEL_FAST,
#   CFG_LLM_API_KEY, CFG_LLM_BASE_URL
#   CFG_EMBEDDING_PROVIDER, CFG_EMBEDDING_MODEL, CFG_EMBEDDING_DIMENSIONS,
#   CFG_EMBEDDING_API_KEY, CFG_EMBEDDING_BASE_URL
#   CFG_RERANKER_PROVIDER, CFG_RERANKER_MODEL,
#   CFG_RERANKER_API_KEY, CFG_RERANKER_BASE_URL
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
        anthropic)  echo "claude-sonnet-5" ;;
        openai)     echo "gpt-4.1" ;;
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
        openai)     echo "gpt-5.4-mini" ;;
        google)     echo "gemini-2.0-flash-lite" ;;
        mistral)    echo "mistral-small-latest" ;;
        cohere)     echo "command-r" ;;
        openrouter) echo "anthropic/claude-haiku-4.5" ;;
        custom)     echo "llama-3.1-8b" ;;
        *)          echo "" ;;
    esac
}
# Curated model choices per provider ("|"-separated). Empty = no curated list
# (falls straight through to free-text entry). A convenience shortlist and a
# fallback for when the provider's own list cannot be fetched — not a
# catalogue. It goes stale by definition, which is why the wizard shows the
# live list when it can reach the provider.
#
# The Anthropic entries are the current ids as of 2026-08-26 and carry no date
# suffix, which the API rejects. The other providers' entries are not verified
# from here; they are corrected when somebody has the provider's list in front
# of them.
#
# The OpenAI strong entries are deliberately not the newest models. Every
# active gpt-5.x model reasons, and /v1/chat/completions rejects a request
# that carries function tools together with a reasoning effort — including
# the effort the model applies by default when the caller sends none. The
# agent archetypes all retrieve from the course material, which Flowise sends
# as a function tool, so an agent on gpt-5.6 answers with a 400 and nothing
# in the wizard can prevent it: the way out is reasoning_effort "none", which
# the Flowise 3.1.3 ChatOpenAI node does not offer, or the /v1/responses
# endpoint, which it does not use. gpt-4.1 and gpt-4o do not reason, take
# function tools, and are in the API with no shutdown date. Measured against
# a real install on 2026-08-26; revisit when Flowise routes tool calls to
# /v1/responses (langchain-ai/langchain#35584).
#
# The fast entries stay on gpt-5.x on purpose: that node extracts a topic and
# carries no tools, so the restriction does not reach it.
llm_model_choices_strong() {
    case "$1" in
        anthropic)  echo "claude-sonnet-5|claude-opus-5" ;;
        openai)     echo "gpt-4.1|gpt-4o" ;;
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
        openai)     echo "gpt-5.4-mini|gpt-5.4-nano" ;;
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
# The provider's current models, one id per line. Returns 2 when the list
# cannot be fetched, which is not an error — an installation behind a proxy,
# or a provider having a bad minute, must not stop a setup.
#
# **The first line of the output is the ordering** — "recent" or "alpha" — and
# the models follow, one per line, as `id` and an optional note separated by a
# tab. The note is whatever the provider itself publishes about that model:
# context length, price, a one-line description. Nothing is inferred and
# nothing is scored — OpenAI and Anthropic publish neither on this endpoint,
# so their models get a bare id rather than a guess dressed as advice. That is deliberate and worth the oddity: the order
# is a claim ("these are the newest") that only some endpoints support, and a
# global would not survive the trip. Every caller reads this through a
# subshell — mapfile from a process substitution, or a command substitution —
# so a variable set in here is discarded on the way out. The first version did
# exactly that, and the list came out correctly sorted underneath a line
# saying it was alphabetical.
#
# Returns 2 when the list cannot be fetched, which is not an error: an
# installation behind a proxy, or a provider having a bad minute, must not
# stop a setup. jq does the sorting — it is already a hard requirement here,
# and doing it in bash would mean parsing timestamps.
# $3 selects what the caller is asking for: chat or embedding.
#
# **Three of the six providers say which a model is**, and where they do, that
# is used instead of guessing from the name — verified against each one's own
# specification rather than remembered:
#
#   google   supportedGenerationMethods[]   generateContent / embedContent
#   mistral  capabilities.completion_chat   and a deprecation timestamp
#   cohere   endpoints[]                    chat / embed / rerank, is_deprecated
#
# OpenAI, Anthropic and OpenRouter publish no capability field on this
# endpoint, so those three fall back to the name filter in show_model_list.
# Mistral and Cohere also say when a model is deprecated, and a deprecated
# model is dropped: offering one is offering something scheduled to stop
# working.
#
# A model that does not carry the field at all is kept, not dropped. These
# are filters over somebody else's API: if a provider stops sending the
# field, the failure should be a list that is too long, not one that is
# silently empty.
fetch_model_ids() {   # $1 = provider, $2 = api key, $3 = chat|embedding
    local provider="$1" api_key="$2" kind="${3:-chat}" resp
    # One place strips a note that came out empty, rather than six jq
    # expressions each having to decide whether to add the separator.
    {
        case "$provider" in
            anthropic)
                resp="$(curl -sf --max-time 8 -H "x-api-key: $api_key" -H "anthropic-version: 2023-06-01" \
                    "https://api.anthropic.com/v1/models?limit=1000" 2>/dev/null)" || return 2
                echo recent
                jq -r '[.data[]?] | sort_by(.created_at // "") | reverse | .[]
                       | .id + "\t" + (.display_name // "")' <<<"$resp" 2>/dev/null
                ;;
            openai)
                resp="$(curl -sf --max-time 8 -H "Authorization: Bearer $api_key" \
                    "https://api.openai.com/v1/models" 2>/dev/null)" || return 2
                echo recent
                jq -r '[.data[]?] | sort_by(.created // 0) | reverse | .[].id' <<<"$resp" 2>/dev/null
                ;;
            google)
                resp="$(curl -sf --max-time 8 -H "x-goog-api-key: $api_key" \
                    "https://generativelanguage.googleapis.com/v1beta/models" 2>/dev/null)" || return 2
                echo alpha
                local g_want='generateContent'
                [[ "$kind" == "embedding" ]] && g_want='embedContent'
                jq -r --arg want "$g_want" '
                    [.models[]?
                     | select(.supportedGenerationMethods == null
                              or ((.supportedGenerationMethods | index($want)) != null))
                     | (.name | sub("^models/"; ""))
                       + "\t"
                       + ((.inputTokenLimit // empty | tostring | . + " tokens in") // "")
                       + (if .inputTokenLimit and .description then " · " else "" end)
                       + ((.description // "") | split(". ")[0])]
                    | sort | .[]' <<<"$resp" 2>/dev/null
                ;;
            mistral)
                resp="$(curl -sf --max-time 8 -H "Authorization: Bearer $api_key" \
                    "https://api.mistral.ai/v1/models" 2>/dev/null)" || return 2
                echo recent
                jq -r --arg kind "$kind" '
                    [.data[]?
                     | select(.deprecation == null)
                     | select(.capabilities == null
                              or (if $kind == "embedding"
                                  then (.capabilities.completion_chat // false) | not
                                  else (.capabilities.completion_chat // false) end))]
                    | sort_by(.created // 0) | reverse | .[]
                    | .id + "\t"
                      + ((.max_context_length // empty | tostring | . + " tokens") // "")
                      + (if .max_context_length and .description then " · " else "" end)
                      + ((.description // "") | split(". ")[0])' <<<"$resp" 2>/dev/null
                ;;
            cohere)
                resp="$(curl -sf --max-time 8 -H "Authorization: Bearer $api_key" \
                    "https://api.cohere.com/v1/models" 2>/dev/null)" || return 2
                echo alpha
                local c_want='chat'
                [[ "$kind" == "embedding" ]] && c_want='embed'
                jq -r --arg want "$c_want" '
                    [.models[]?
                     | select(.is_deprecated != true)
                     | select(.endpoints == null or ((.endpoints | index($want)) != null))
                     | .name + "\t"
                       + ((.context_length // empty | tostring | . + " tokens") // "")]
                    | sort | .[]' <<<"$resp" 2>/dev/null
                ;;
            openrouter)
                resp="$(curl -sf --max-time 8 "https://openrouter.ai/api/v1/models" 2>/dev/null)" || return 2
                echo recent
                jq -r --arg kind "$kind" '
                    [.data[]?
                     | select((.architecture.output_modalities // ["text"]) | index("text"))]
                    | sort_by(.created // 0) | reverse | .[]
                    | .id + "\t"
                      + ((.context_length // empty | tostring | . + " tokens") // "")
                      + (if .pricing.prompt then
                           " · $" + (((.pricing.prompt | tonumber) * 1000000 * 100 | round) / 100 | tostring)
                           + "/M in"
                         else "" end)' <<<"$resp" 2>/dev/null
                ;;
            *) return 2 ;;
        esac
    # POSIX '*', not GNU '\+': BSD sed does not know the latter and left every
    # trailing tab in place, which is the kind of difference that shows up as
    # a test failure on a laptop and never on the server.
    } | sed $'s/\t*$//'
}

# Print the provider's models so the operator can pick from what exists rather
# than from a list written into this repository months ago. Capped, because a
# provider with two hundred entries would push the question itself off the
# screen; the cap is stated rather than silent.
MODEL_LIST_CAP=24

# Which families are not what this question is asking for. Matched on the id,
# because none of the six endpoints says what a model is *for* — OpenAI's list
# returns transcription, image, realtime and embedding models beside the chat
# ones, undistinguished.
#
# A name-based filter is a heuristic and will be wrong eventually, which is
# why it only ever hides things from a *display*: the count of what was hidden
# is printed, and any name at all can still be typed at the prompt. That is a
# different bet from the curated suggestion lists, where being wrong means
# recommending a model that does not work.
# The providers that answer the question themselves, so the name filter must
# not run a second time over their output: a Google chat model called
# gemini-…-image supports generateContent and would be thrown away by a filter
# looking for "image" in the name.
provider_filters_itself() {
    case "$1" in google|mistral|cohere) return 0 ;; *) return 1 ;; esac
}

MODEL_FILTER_CHAT='embed|image|dall-e|sora|tts|audio|transcribe|whisper|realtime|moderation|guard|rerank'
MODEL_FILTER_EMBEDDING='embed'

show_model_list() {   # $1 = provider, $2 = api key, $3 = chat|embedding
    local provider="$1" api_key="$2" kind="${3:-chat}"
    [[ "$provider" == "custom" || -z "$api_key" ]] && return 0
    command -v jq >/dev/null 2>&1 || return 0

    local lines=() rc=0
    mapfile -t lines < <(fetch_model_ids "$provider" "$api_key" "$kind") || rc=$?
    if (( rc != 0 )) || (( ${#lines[@]} < 2 )); then
        dim "$(t cfg_model_list_unavailable)" >&2
        return 0
    fi
    local order="${lines[0]}"
    local all=("${lines[@]:1}")

    # Keep what this question is about. An embedding question showing 132
    # chat, image and speech models is a list nobody can use, and a chat
    # question whose first ten entries are transcription models buries the
    # ones being asked about.
    local models=() m mid
    if provider_filters_itself "$provider"; then
        models=("${all[@]}")
    else
        for m in "${all[@]}"; do
            # The id, not the line: a note mentioning "image" would otherwise
            # filter out the model it describes.
            mid="${m%%$'\t'*}"
            if [[ "$kind" == "embedding" ]]; then
                [[ "$mid" =~ $MODEL_FILTER_EMBEDDING ]] && models+=("$m")
            else
                [[ "$mid" =~ $MODEL_FILTER_CHAT ]] || models+=("$m")
            fi
        done
    fi
    # A filter that removes everything has misjudged this provider's naming.
    # Better the unfiltered list than none — and say which is being shown.
    local filtered=1
    if (( ${#models[@]} == 0 )); then
        models=("${all[@]}")
        filtered=0
        dim "$(t cfg_model_list_unfiltered)" >&2
    fi
    local hidden=$(( ${#all[@]} - ${#models[@]} ))

    # The two numbers, because one of them alone misleads: "5 models" reads
    # as the provider offering five, and it offers 132.
    if [[ "$order" == "recent" ]]; then
        info "$(t cfg_model_list_recent "${#models[@]}" "${#all[@]}")" >&2
    else
        info "$(t cfg_model_list_alpha "${#models[@]}" "${#all[@]}")" >&2
    fi
    # Aligned to the longest id being shown, so the notes form a column
    # instead of ragged text after names of different lengths.
    local shown=0 m id note width=0
    for m in "${models[@]:0:$MODEL_LIST_CAP}"; do
        id="${m%%$'\t'*}"
        (( ${#id} > width )) && width=${#id}
    done
    for m in "${models[@]}"; do
        (( shown >= MODEL_LIST_CAP )) && break
        id="${m%%$'\t'*}"
        note="${m#*$'\t'}"
        [[ "$note" == "$m" ]] && note=""
        if [[ -n "$note" ]]; then
            printf "    %-*s  ${DIM}%s${RESET}\n" "$width" "$id" "$note" >&2
        else
            printf "    %s\n" "$id" >&2
        fi
        shown=$(( shown + 1 ))
    done
    (( ${#models[@]} > shown )) && dim "$(t cfg_model_list_more "$(( ${#models[@]} - shown ))")" >&2
    (( filtered && hidden > 0 )) && dim "$(t cfg_model_list_hidden "$hidden")" >&2
    echo >&2
    return 0
}

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
    local curated=($choices)
    unset IFS

    # A recommendation the provider has retired is worse than no
    # recommendation: it is the one entry an operator trusts, and choosing it
    # produces an installation whose agents answer with an API error. The
    # names below are written into this repository and go stale by definition
    # — gpt-4o was still being offered as the strong model long after it had
    # been superseded twice.
    #
    # So they are checked against the provider's own list, which was fetched a
    # moment ago for the display. Anything the provider no longer offers is
    # dropped, and the drop is stated: a suggestion vanishing without a word
    # would look like this installer forgetting the provider.
    local opts=() live=() gone=()
    mapfile -t live < <(fetch_model_ids "$provider" "$api_key" chat 2>/dev/null | tail -n +2 | cut -f1)
    if (( ${#live[@]} > 0 )); then
        local c
        for c in "${curated[@]}"; do
            if printf '%s\n' "${live[@]}" | grep -qxF "$c"; then
                opts+=("$c")
            else
                gone+=("$c")
            fi
        done
        (( ${#gone[@]} )) && warn "$(t cfg_model_retired "${gone[*]}")" >&2
    else
        # No list to check against — offer them all rather than nothing.
        opts=("${curated[@]}")
    fi

    # Every suggestion retired at once leaves the free-text entry, which is
    # the honest state: this repository no longer knows what to recommend for
    # this provider, and the live list is on screen above.
    if (( ${#opts[@]} == 0 )); then
        prompt_and_validate_model "$msg_key" "${live[0]:-$default_model}" "$provider" "$api_key"
        return
    fi
    # The default follows the suggestions: one pointing at a model that was
    # just dropped would be offered as the pre-filled answer.
    printf '%s\n' "${opts[@]}" | grep -qxF "$default_model" || default_model="${opts[0]}"
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
# No course is asked for here, and that is the point of this section's name.
# A course is created in the Content Admin, where creating one also makes its
# chunk collection, its bucket, the grant on that bucket and its ten agent
# slots — and, crucially, a row in the courses table that everything else
# joins against.
#
# The installer used to ask, and then built a collection and a bucket for a
# course that existed in no table. The Content Admin showed zero courses; the
# first one created there made a *second* collection and a *second* bucket,
# and the installer's pair sat on disk with nothing pointing at them. Which is
# the whole reason this system stopped being single-course: the answer to
# "which course is this installation for" is now "as many as you like", and an
# installer that insists on one at setup time is answering a question nobody
# asked any more.
ask_install_info() {
    header "$(t cfg_section_install)"

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

    # Shown once, before either question: the two tiers are picked from the
    # same list, and printing it twice would push the first question off the
    # screen. The curated suggestions below stay — they say which model is a
    # reasonable *choice*, which a bare list cannot — but they are no longer
    # the only thing the operator sees, and a model released last week is now
    # visible instead of being something you have to already know about.
    show_model_list "$CFG_LLM_PROVIDER" "$CFG_LLM_API_KEY" chat

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
    show_model_list "$CFG_EMBEDDING_PROVIDER" "$CFG_EMBEDDING_API_KEY" embedding
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


# ─── Section: Mail service (SMTP) ────────────────────────────────────────────
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
    local _intro_line
    while IFS= read -r _intro_line; do
        printf "  ${DIM}%s${RESET}\n" "$_intro_line"
    done < <(wrap_lines "$(t cfg_mail_intro)" $(( $(term_width) - 2 )))
    printf "\n"

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
    # A bounded loop, and both halves of that matter. Not a recursive call:
    # the first version re-entered ask_mail_config when the operator declined
    # the warning below, which on an exhausted stdin recursed until the stack
    # gave out. And bounded, because a plain loop has the same defect in a
    # different shape — at EOF select_one_index returns its default and
    # confirm returns its own, so the pair would spin forever agreeing with
    # each other. After three passes the question stops being asked and the
    # answer is "no mail", which is the one outcome that is always true and
    # always fixable later.
    local choice attempts=0
    while true; do
    if (( attempts >= 3 )); then
        warn "$(t cfg_mail_gave_up)"
        _reset_mail_config_disabled
        CFG_INSTALL_POSTFIX="false"
        return 0
    fi
    attempts=$(( attempts + 1 ))
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
            #
            # But only if something *is* running. The detection above already
            # knew; it just was not consulted, so on a machine with no MTA at
            # all this option was accepted in silence and the installation
            # finished believing it could send mail. It cannot, and the first
            # sign is a password reset that never arrives.
            if [[ "$mta" == "none" && "$port_listening" != "1" ]]; then
                warn "$(t cfg_mail_none_detected)"
                # Back to the question rather than onwards with a setting the
                # operator has just been told is wrong.
                confirm cfg_mail_none_detected_anyway "n" || continue
            fi
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
    break
    done

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

EOF
    if [[ "$CFG_INSTALL_POSTFIX" == "true" ]]; then
        echo "  Mail service:     local Postfix → $CFG_SMTP_RELAY_HOST:$CFG_SMTP_RELAY_PORT"
    elif [[ -n "$CFG_SMTP_HOST" ]]; then
        echo "  Mail service:     direct → $CFG_SMTP_HOST:$CFG_SMTP_PORT"
    else
        echo "  Mail service:     none (nothing this system sends can leave it)"
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
    local steps=(ask_deployment_mode ask_install_info ask_profiles ask_llm_config ask_embedding_config ask_reranker_config ask_mail_config)
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
            _wizard_step_loop 6   # re-enter at the last section (mail service)
        else
            die "$(t cfg_aborted)"
        fi
    done
}
