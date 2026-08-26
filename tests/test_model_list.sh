#!/usr/bin/env bash
# The installer shows the provider's own model list, not one written here.
#
# The wizard already fetched that list — to validate what was typed — and then
# threw it away, so the operator chose from three names hard-coded in this
# repository. Those go stale by definition: a model released last week is one
# you have to already know the name of. Reported from a real install, where
# the curated list offered gpt-4o and the answer was a model two generations
# newer.
#
# Two things this holds. **The order is a claim**: "newest first" is only true
# for the endpoints that say when a model appeared, and four of the six do not
# — those are alphabetical and say so. **A list that cannot be fetched is not
# an error**: an installation behind a proxy must still finish, with the
# curated suggestions and free text, which is exactly what it had before.
#
# curl is stubbed, so this asks what the code does with an answer rather than
# what a provider returned today.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

command -v jq >/dev/null 2>&1 || { echo "jq is required for these checks"; exit 10; }

# Runs one snippet with curl replaced by a stub that prints $STUB_BODY and
# exits $STUB_RC. Nothing here reaches a network.
run() {   # $1 = snippet, $2 = body, $3 = rc
    STUB_BODY="$2" STUB_RC="${3:-0}" bash -c '
        set -uo pipefail
        export LANG_CHOICE=en
        source "'"$REPO"'/scripts/lib/messages.sh"
        source "'"$REPO"'/scripts/lib/common.sh"
        source "'"$REPO"'/scripts/lib/config-wizard.sh"
        curl() { [[ "$STUB_RC" == "0" ]] || return "$STUB_RC"; printf "%s" "$STUB_BODY"; }
        '"$1"'
    ' 2>&1
}

OPENAI='{"data":[{"id":"gpt-4o","created":100},{"id":"gpt-5.4","created":900},{"id":"ancient","created":1}]}'
# No supportedGenerationMethods on these two on purpose: a provider that stops
# sending the field must produce a list that is too long, never a silently
# empty one.
GOOGLE='{"models":[{"name":"models/gemini-b"},{"name":"models/gemini-a"}]}'

# ─── Newest first, where the API knows ───────────────────────────────────────
out="$(run 'fetch_model_ids openai key123' "$OPENAI")"
# First line is the ordering, ids follow — see the contract in the function.
[[ "$(head -1 <<<"$out")" == "recent" ]]
check "the ordering is reported as by date" $? "$out"
[[ "$(sed -n 2p <<<"$out")" == "gpt-5.4" ]]
check "an API that dates its models is sorted newest first" $? "$out"
[[ "$(tail -1 <<<"$out")" == "ancient" ]]
check "…and the oldest is last" $? "$out"

# ─── Alphabetical, where it does not — and said so ───────────────────────────
out="$(run 'fetch_model_ids google key123' "$GOOGLE")"
[[ "$(head -1 <<<"$out")" == "alpha" ]]
check "the ordering is not claimed to be by date" $? "$out"
[[ "$(sed -n 2p <<<"$out")" == "gemini-a" ]]
check "an API without dates is sorted alphabetically" $? "$out"
grep -q 'models/' <<<"$out"
check "and the models/ prefix is stripped" $(( $? == 0 ? 1 : 0 )) "$out"

out="$(run 'show_model_list google key123 chat' "$GOOGLE")"
grep -qi 'alphabetical' <<<"$out"
check "the display says why the order is alphabetical" $? "$out"
out="$(run 'show_model_list openai key123 chat' "$OPENAI")"
grep -qi 'newest first' <<<"$out"
check "and says newest first when that is true" $? "$out"

# The first line is a marker, not a model — it must never be printed as one.
out="$(run 'show_model_list openai key123' "$OPENAI")"
grep -qE '^    (recent|alpha)$' <<<"$out"
check "the ordering marker is not shown as a model" $(( $? == 0 ? 1 : 0 )) "$out"
grep -qE '^    gpt-5\.4$' <<<"$out"
check "and the models themselves are" $? "$out"

# ─── Each question shows what it is asking about ─────────────────────────────
# The provider's list mixes chat, embedding, image, speech and moderation
# models and marks none of them. Unfiltered, the embedding question offered
# 132 entries of which two were embeddings, and the chat question opened with
# four transcription models — reported from a real install, both.
MIXED='{"data":[{"id":"gpt-9-chat","created":9},{"id":"gpt-image-2","created":8},
                {"id":"text-embedding-9","created":7},{"id":"gpt-transcribe","created":6},
                {"id":"omni-moderation-latest","created":5},{"id":"dall-e-3","created":4}]}'

out="$(run 'show_model_list openai key123 chat' "$MIXED")"
grep -qE '^    gpt-9-chat$' <<<"$out"
check "the chat question shows a chat model" $? "$out"
for noise in gpt-image-2 text-embedding-9 gpt-transcribe omni-moderation-latest dall-e-3; do
    grep -qE "^    $noise\$" <<<"$out"
    check "the chat question hides $noise" $(( $? == 0 ? 1 : 0 )) "$out"
done

out="$(run 'show_model_list openai key123 embedding' "$MIXED")"
grep -qE '^    text-embedding-9$' <<<"$out"
check "the embedding question shows the embedding model" $? "$out"
grep -qE '^    gpt-9-chat$' <<<"$out"
check "the embedding question hides the chat model" $(( $? == 0 ? 1 : 0 )) "$out"

# Hiding is never silent, and the heading never passes the filtered count off
# as the provider's offering.
grep -qiE '(not shown|nicht gezeigt)' <<<"$out"
check "what was hidden is counted" $? "$out"
grep -qE '1 of the provider.s 6 models' <<<"$out"
check "the heading gives both numbers" $? "$out"

# A filter that matches nothing has misjudged the provider's naming — then the
# whole list is better than an empty one, and it says so.
NONE='{"data":[{"id":"weird-one","created":2},{"id":"weird-two","created":1}]}'
out="$(run 'show_model_list openai key123 embedding' "$NONE")"
grep -qE '^    weird-one$' <<<"$out"
check "a filter matching nothing falls back to the whole list" $? "$out"
grep -qi 'whole list' <<<"$out"
check "and says that is what is happening" $? "$out"

# ─── The cap is stated, never silent ─────────────────────────────────────────
MANY="$(jq -nc '{data: [range(40) | {id: ("m" + (tostring)), created: .}]}')"
out="$(run 'show_model_list openai key123' "$MANY")"
shown="$(grep -cE '^    m[0-9]+$' <<<"$out")"
(( shown > 0 && shown <= 24 ))
check "a long list is capped" $? "$shown lines shown"
grep -qiE 'and [0-9]+ more' <<<"$out"
check "and the rest is counted rather than dropped in silence" $? "$out"

# ─── Where the provider says what a model is, that is used ───────────────────
# Three of the six publish it, and each was read from its own specification
# rather than remembered: Google's supportedGenerationMethods, Mistral's
# capabilities.completion_chat, Cohere's endpoints. Guessing from the name
# where the answer is published is how a chat model called gemini-…-image gets
# thrown away for having "image" in it.
# Two chat models, one of which has a filtered word in its name. The second is
# load-bearing: with only the first, the name filter would empty the list and
# the "show everything rather than nothing" fallback would hand it back —
# hiding the very mistake this is checking for.
GOOGLE_CAP='{"models":[
  {"name":"models/gemini-3-image","supportedGenerationMethods":["generateContent"]},
  {"name":"models/gemini-3-pro","supportedGenerationMethods":["generateContent"]},
  {"name":"models/text-embedding-5","supportedGenerationMethods":["embedContent"]}]}'
out="$(run 'fetch_model_ids google key123 chat' "$GOOGLE_CAP")"
grep -qx 'gemini-3-image' <<<"$out"
check "Google: a chat model is kept despite its name" $? "$out"
grep -qx 'text-embedding-5' <<<"$out"
check "Google: an embedding model is not offered for chat" $(( $? == 0 ? 1 : 0 )) "$out"
out="$(run 'fetch_model_ids google key123 embedding' "$GOOGLE_CAP")"
grep -qx 'text-embedding-5' <<<"$out"
check "Google: the embedding question gets the embedding model" $? "$out"

# A response without the capability field is kept rather than filtered away.
out="$(run 'fetch_model_ids google key123 chat' "$GOOGLE")"
[[ "$(tail -n +2 <<<"$out" | tr '\n' ' ')" == "gemini-a gemini-b " ]]
check "a model that carries no capability field is kept" $? "$out"

out="$(run 'show_model_list google key123 chat' "$GOOGLE_CAP")"
grep -qE '^    gemini-3-image$' <<<"$out"
check "and the name filter does not run a second time over it" $? "$out"

# Deprecated is not offered: it is scheduled to stop working.
MISTRAL='{"data":[
  {"id":"mistral-large","created":9,"capabilities":{"completion_chat":true}},
  {"id":"mistral-embed","created":8,"capabilities":{"completion_chat":false}},
  {"id":"mistral-old","created":7,"capabilities":{"completion_chat":true},"deprecation":"2026-01-01T00:00:00Z"}]}'
out="$(run 'fetch_model_ids mistral key123 chat' "$MISTRAL")"
grep -qx 'mistral-large' <<<"$out"
check "Mistral: a current chat model is offered" $? "$out"
grep -qx 'mistral-old' <<<"$out"
check "Mistral: a deprecated model is not" $(( $? == 0 ? 1 : 0 )) "$out"
grep -qx 'mistral-embed' <<<"$out"
check "Mistral: an embedding model is not offered for chat" $(( $? == 0 ? 1 : 0 )) "$out"

COHERE='{"models":[
  {"name":"command-a","endpoints":["chat"]},
  {"name":"embed-v4","endpoints":["embed"]},
  {"name":"rerank-v4","endpoints":["rerank"]},
  {"name":"command-old","endpoints":["chat"],"is_deprecated":true}]}'
out="$(run 'fetch_model_ids cohere key123 chat' "$COHERE")"
[[ "$(tail -n +2 <<<"$out" | tr '\n' ' ')" == "command-a " ]]
check "Cohere: only the current chat model" $? "$out"
out="$(run 'fetch_model_ids cohere key123 embedding' "$COHERE")"
[[ "$(tail -n +2 <<<"$out" | tr '\n' ' ')" == "embed-v4 " ]]
check "Cohere: only the embedding model" $? "$out"

# ─── What the provider publishes is shown beside the model ───────────────────
# Facts from the provider, never a judgement of ours: context length, price,
# the first sentence of its own description. OpenAI and Anthropic publish
# neither on this endpoint, and their models get a bare id rather than an
# invented recommendation — the entry an operator would trust most is the one
# that must not be made up.
WITHNOTES='{"data":[
  {"id":"a/one","created":9,"context_length":128000,
   "pricing":{"prompt":"0.0000014"},"architecture":{"output_modalities":["text"]}}]}'
out="$(run 'show_model_list openrouter key123 chat' "$WITHNOTES")"
grep -q '128000 tokens' <<<"$out"
check "the context length the provider publishes is shown" $? "$out"
grep -q '1.4/M in' <<<"$out"
check "and its price per million tokens" $? "$out"

# A note must never decide whether a model is shown.
NOTEBAIT='{"data":[
  {"id":"good/chat","created":9,"context_length":1,
   "pricing":{"prompt":"0"},"architecture":{"output_modalities":["text"],"modality":"image->text"}},
  {"id":"good/other","created":8,"context_length":2,
   "pricing":{"prompt":"0"},"architecture":{"output_modalities":["text"]}}]}'
out="$(run 'show_model_list openrouter key123 chat' "$NOTEBAIT")"
grep -q 'good/chat' <<<"$out"
check "a model is not hidden because its note mentions a filtered word" $? "$out"

out="$(run 'fetch_model_ids openai key123 chat' "$OPENAI")"
[[ "$(sed -n 2p <<<"$out")" == "gpt-5.4" ]]
check "a provider that publishes nothing yields a bare id" $? "$out"

# ─── A list that cannot be fetched is not a failure ──────────────────────────
out="$(run 'show_model_list openai key123; echo "REACHED-THE-END"' "" 22)"
grep -q 'REACHED-THE-END' <<<"$out"
check "an unreachable provider does not stop the wizard" $? "$out"
grep -qi 'could not be fetched' <<<"$out"
check "…and says so instead of showing nothing" $? "$out"

out="$(run 'show_model_list custom "" ; echo "REACHED-THE-END"' "$OPENAI")"
grep -q 'REACHED-THE-END' <<<"$out"
check "a custom provider is skipped rather than guessed at" $? "$out"
out="$(run 'show_model_list openai "" ; echo "REACHED-THE-END"' "$OPENAI")"
grep -q 'REACHED-THE-END' <<<"$out"
check "and so is a provider with no key yet" $? "$out"

# ─── The list is actually shown where the models are chosen ──────────────────
grep -q 'show_model_list "$CFG_LLM_PROVIDER"' "$REPO/scripts/lib/config-wizard.sh"
check "the LLM section shows it" $? ""
grep -q 'show_model_list "$CFG_EMBEDDING_PROVIDER"' "$REPO/scripts/lib/config-wizard.sh"
check "the embedding section shows it" $? ""

# Every provider the wizard offers must be fetchable, or the list silently
# never appears for it. Checked against the menu rather than a list here.
providers="$(grep -m1 -A1 'select_one cfg_llm_provider' "$REPO/scripts/lib/config-wizard.sh" | tr -d '\\\\')"
for prov in anthropic openai google mistral cohere openrouter; do
    grep -q "$prov" <<<"$providers"
    check "the wizard still offers $prov" $? "$providers"
    grep -qE "^ *$prov\)" <<<"$(sed -n '/^fetch_model_ids()/,/^}/p' "$REPO/scripts/lib/config-wizard.sh")"
    check "fetch_model_ids handles $prov" $? ""
done

# ─── The suggested strong model must be able to take a function tool ────────
# Every agent archetype retrieves from the course material, and Flowise sends
# that retrieval as a function tool. OpenAI's /v1/chat/completions refuses a
# request that carries function tools together with a reasoning effort, and a
# gpt-5.x model applies one by default even when the caller sends none — so
# the wizard offering gpt-5.6-sol produced installations whose agents answered
# with a 400 as soon as they touched the material. The Flowise 3.1.3 node
# cannot send reasoning_effort "none" and does not use /v1/responses, so the
# only lever left is which model is suggested.
#
# The fast list is not checked: that node extracts a topic, carries no tools,
# and the restriction does not reach it.
strong_openai="$(bash -c "source '$REPO/scripts/lib/config-wizard.sh' 2>/dev/null; llm_model_choices_strong openai" 2>/dev/null)"
default_openai="$(bash -c "source '$REPO/scripts/lib/config-wizard.sh' 2>/dev/null; default_llm_model_strong openai" 2>/dev/null)"

[[ -n "$strong_openai" ]]
check "the OpenAI strong list is readable" $? "sourcing the wizard produced nothing"

if grep -q "gpt-5" <<<"$strong_openai"; then
    check "no reasoning model is suggested as the strong model" 1 \
          "offered: $strong_openai — these 400 with function tools"
else
    check "no reasoning model is suggested as the strong model" 0
fi

[[ "$default_openai" != gpt-5* ]]
check "and the default is not one either" $? "default: $default_openai"

grep -q "gpt-4" <<<"$strong_openai"
check "a tool-capable OpenAI model is still offered" $? \
      "removing the reasoning models must not leave the list empty"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All model-list checks passed: the installer shows the provider's own"
echo "models rather than three names written into this repository — newest"
echo "first for the endpoints that date them, alphabetical for those that do"
echo "not and saying which, with the models/ prefix stripped where Google adds"
echo "it; a long list is capped at 24 with the remainder counted rather than"
echo "quietly dropped; a provider that cannot be reached, one with no key yet,"
echo "and the custom provider each leave the wizard running with its own"
echo "suggestions instead of failing; and every provider the menu offers is one"
echo "the fetcher handles, so the list cannot silently never appear."
