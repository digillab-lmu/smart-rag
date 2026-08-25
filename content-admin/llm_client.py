"""
One-shot LLM calls for the content form's "Optimize with AI" button.

Reuses whichever LLM_PROVIDER / LLM_API_KEY / LLM_MODEL_STRONG the operator
already configured via the CLI wizard — the same provider the agents
themselves use — via plain REST calls with `requests`, no vendor SDK
(consistent with flowise_client.py/neo4j_client.py already doing the same
for their APIs; requirements.txt deliberately stays minimal).

Endpoint URLs, auth header formats, and response JSON shapes below were
verified against each provider's current docs (not assumed from training
knowledge — API shapes drift and a wrong path here fails silently as a
KeyError, not a helpful error message):
  - Anthropic Messages API:        https://docs.claude.com (Messages API)
  - OpenAI Chat Completions API:   https://platform.openai.com/docs/api-reference/chat
  - Google Gemini generateContent: https://ai.google.dev/api/generate-content
  - Mistral Chat Completions API:  https://docs.mistral.ai/api/
  - Cohere Chat API v2:            https://docs.cohere.com/v2/reference/chat
  - OpenRouter (OpenAI-compatible): https://openrouter.ai/docs/api_reference/overview
"""

import json

import requests

_TIMEOUT = 30
_MAX_TOKENS = 1024

# A concept map is a different size of answer from a keyword list, and a
# reasoning model spends part of any budget before it writes a character.
# Measured: with the 1024 above, the strong model returned an empty string
# after six seconds — the whole budget had gone into reasoning tokens, and the
# empty answer then surfaced as "Nothing was pasted", which is not what
# happened and not something an operator can act on.
_MAX_TOKENS_LONG = 16384
# The same call sends the course outline, so it is a slow one both ways.
#
# **Must stay below gunicorn's --timeout in content-admin/Dockerfile (120).**
# The innermost limit has to be the shortest, or the component that knows why
# the request stopped never gets to say so — the same ordering the docling and
# n8n timeouts are held to in tests/test_ingest_limits.sh. Above 120 the
# worker is killed mid-request and the operator gets a dead connection instead
# of a sentence. Note also that this image runs a single sync worker, so the
# whole Content Admin is unresponsive for the duration: this is a ceiling for
# a pathological case, not a target.
_TIMEOUT_LONG = 110


class LLMError(Exception):
    pass


def _complete(system_prompt: str, user_prompt: str, env: dict,
              max_tokens: int = _MAX_TOKENS, timeout: int = _TIMEOUT) -> str:
    """
    One chat completion against whichever provider is configured, returning
    the assistant's raw text. Extracted so every AI-assist feature shares a
    single provider dispatch — a new one (keyword suggestions, prompt
    translation) doesn't mean another copy of this ladder to keep in step.
    """
    # No default: an absent LLM_PROVIDER is a misconfiguration, and
    # answering it by calling a different vendor produces an auth error
    # that points at the wrong thing. An empty value already fell
    # through to the "Unknown LLM_PROVIDER" below; a missing one now
    # does too.
    provider = (env.get("LLM_PROVIDER") or "").strip()
    api_key = env.get("LLM_API_KEY", "")
    model = env.get("LLM_MODEL_STRONG", "")
    if not api_key:
        raise LLMError("No LLM API key configured (LLM_API_KEY is empty in .env).")
    if not model:
        raise LLMError("No LLM model configured (LLM_MODEL_STRONG is empty in .env).")

    if provider == "anthropic":
        return _call_anthropic(api_key, model, system_prompt, user_prompt,
                               max_tokens, timeout)
    if provider == "openai":
        # OpenAI's newer models (o-series/reasoning, and now some current
        # chat models too) reject the classic "max_tokens" field outright —
        # live error: "Unsupported parameter: 'max_tokens' is not supported
        # with this model. Use 'max_completion_tokens' instead." OpenAI's own
        # docs mark max_completion_tokens as the current field for all
        # models, so use it unconditionally here rather than guessing per
        # model name.
        return _call_openai_compatible(
            "https://api.openai.com/v1/chat/completions", api_key, model, system_prompt, user_prompt,
            tokens_param="max_completion_tokens", max_tokens=max_tokens, timeout=timeout,
        )
    if provider == "google":
        return _call_google(api_key, model, system_prompt, user_prompt,
                            max_tokens, timeout)
    if provider == "mistral":
        return _call_openai_compatible(
            "https://api.mistral.ai/v1/chat/completions", api_key, model, system_prompt, user_prompt,
            max_tokens=max_tokens, timeout=timeout,
        )
    if provider == "cohere":
        return _call_cohere(api_key, model, system_prompt, user_prompt,
                            max_tokens, timeout)
    if provider == "openrouter":
        # config-wizard.sh already stores openrouter model strings with the
        # required provider/ prefix (e.g. "anthropic/claude-sonnet-5") —
        # nothing extra to do here.
        return _call_openai_compatible(
            "https://openrouter.ai/api/v1/chat/completions", api_key, model, system_prompt, user_prompt,
            max_tokens=max_tokens, timeout=timeout,
        )
    if provider == "custom":
        base_url = env.get("LLM_BASE_URL", "").rstrip("/")
        if not base_url:
            raise LLMError("LLM_PROVIDER is 'custom' but LLM_BASE_URL is empty in .env.")
        return _call_openai_compatible(
            f"{base_url}/chat/completions", api_key, model, system_prompt, user_prompt,
            max_tokens=max_tokens, timeout=timeout,
        )
    raise LLMError(f"Unknown LLM_PROVIDER: {provider!r}")


_LANGUAGE_NAMES = {"en": "English", "de": "German"}


def optimize_field(
    field_name: str,
    field_purpose: str,
    current_text: str,
    env: dict,
    language: str = "en",
) -> dict:
    """
    Returns {"suggestion": str, "rationale": str}. Raises LLMError on any
    failure (missing config, network error, unexpected response shape) —
    the caller surfaces the message directly to the operator.

    `language` is the GUI language the operator is working in. It decides
    the language of the suggestion when the field is still empty, and the
    language of the rationale always — an operator using the German GUI
    shouldn't get an English explanation of what was changed. Existing
    text keeps its own language regardless, so improving a German draft
    never silently translates it.
    """
    language_name = _LANGUAGE_NAMES.get(language, "English")
    system_prompt = (
        "You help course creators write content for an AI teaching assistant. "
        "You will be given the name and purpose of one form field, and what "
        "the course creator has written for it so far (it may be empty, a "
        "rough note, or already good). Expand or improve it into complete, "
        "well-written content suitable for that field. Keep the same "
        f"language the input is written in; if the input is empty, write in "
        f"{language_name}. Respond with ONLY a JSON object with exactly two "
        'keys: "suggestion" (the improved field content, nothing else — no '
        'quotes or markdown around it) and "rationale" (1-2 sentences, '
        f"written in {language_name}, explaining what you changed and why). "
        "Output nothing before or after the JSON object."
    )
    user_prompt = (
        f"Field: {field_name}\n"
        f"What this field is for: {field_purpose}\n"
        f"Current content (may be empty):\n{current_text or '(empty)'}"
    )

    return _parse_suggestion(_complete(system_prompt, user_prompt, env))


def suggest_keywords(
    title: str,
    authors: str,
    excerpt: str,
    env: dict,
    language: str = "en",
    count: int = 8,
) -> list[str]:
    """
    Proposes subject keywords for an uploaded document, from whatever is
    known about it: its title, its authors, and — when the PDF was scanned
    for a DOI/ISBN — the front-matter text that scan already extracted.
    Reusing that text is deliberate: the operator uploaded the file once,
    and asking them to do it again just to get keywords would be silly.

    Returns a list of terms. Keywords are content, so they follow the GUI
    language rather than the document's — an operator filing a German
    course wants German keywords even for an English paper.
    """
    if not (title.strip() or excerpt.strip()):
        raise LLMError("Nothing to work from — enter a title or scan the document first.")

    language_name = _LANGUAGE_NAMES.get(language, "English")
    system_prompt = (
        "You extract subject keywords for a course document library. Given "
        "what is known about one document, propose the terms a student or "
        "lecturer would plausibly search for to find it. Prefer established "
        "subject terminology over phrases lifted verbatim from the text, and "
        "avoid near-duplicates. Respond with ONLY a JSON object with one key "
        '"keywords": an array of at most '
        f"{count} short strings, written in {language_name}. Output nothing "
        "before or after the JSON object."
    )
    user_prompt = (
        f"Title: {title or '(unknown)'}\n"
        f"Authors: {authors or '(unknown)'}\n"
        # Front matter is usually title page + abstract, which is exactly
        # the useful part; the cap keeps a long scan from dominating the
        # request.
        f"Opening pages of the document:\n{excerpt[:6000] or '(not available)'}"
    )

    raw = _complete(system_prompt, user_prompt, env).strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Could not parse the AI's response as JSON: {exc}") from exc

    keywords = data.get("keywords") if isinstance(data, dict) else None
    if not isinstance(keywords, list):
        raise LLMError("The AI's response was missing a 'keywords' list.")

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in keywords:
        term = str(item).strip().strip(",;")
        if term and term.casefold() not in seen:
            seen.add(term.casefold())
            cleaned.append(term)
    if not cleaned:
        raise LLMError("The AI returned no usable keywords.")
    return cleaned[:count]


def propose_graph(course_name: str, material: str, instruction: str,
                  env: dict) -> str:
    """Ask the strong model for a concept map, and return its raw answer.

    Raw on purpose. The answer goes through neo4j_client.parse_proposal like
    a pasted one — same validation, same refusals for a cycle or a
    prerequisite naming an unknown concept — and then into the same review
    box the guided path fills by hand. One path from here on, so a proposal
    cannot reach the graph through a door the manual one does not have.

    `instruction` is the very prompt the page offers for copying into an AI
    of one's own. Passing it in rather than writing a second one here is the
    point: the two routes must ask for the same thing, or the automated one
    quietly becomes a different feature that happens to share a page.
    """
    if not material.strip():
        raise LLMError(
            "There is no material to read yet — upload course documents first. "
            "A concept map proposed from nothing would be the model's general "
            "knowledge of the subject, not this course.")

    system_prompt = (
        "You build concept maps for university courses. You answer with JSON "
        "and nothing else: no explanation, no commentary, no code fence."
    )
    user_prompt = (
        f"{instruction}\n\n"
        f"--- Course material for \"{course_name}\" ---\n{material}\n--- end ---"
    )
    return _complete(system_prompt, user_prompt, env,
                     max_tokens=_MAX_TOKENS_LONG, timeout=_TIMEOUT_LONG)


def _parse_suggestion(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Could not parse the AI's response as JSON: {exc}") from exc
    if not isinstance(data, dict) or "suggestion" not in data or "rationale" not in data:
        raise LLMError("The AI's response was missing 'suggestion' or 'rationale'.")
    return {"suggestion": str(data["suggestion"]), "rationale": str(data["rationale"])}


def _empty_answer(reason: str, max_tokens: int) -> "LLMError":
    """The model was reached, answered, and said nothing.

    Worth its own message. An empty answer used to travel onward as an empty
    string and was reported by whatever tried to use it — for the concept map
    that was "Nothing was pasted", which describes neither what happened nor
    anything the reader can do. The usual cause is the budget: a reasoning
    model can spend every token thinking and return an empty content field,
    which the API reports as a perfectly successful call.
    """
    hint = ""
    # openai/mistral/openrouter say "length", google says "MAX_TOKENS",
    # anthropic "max_tokens", cohere "MAX_TOKENS".
    if "len" in (reason or "").lower() or "max_token" in (reason or "").lower():
        hint = (f" The answer was cut off at the token limit ({max_tokens}), so "
                "the model most likely spent the whole budget before writing "
                "anything — a reasoning model will do that.")
    return LLMError(
        f"The model returned an empty answer (finish reason: {reason or 'not stated'})."
        f"{hint} Nothing was written to the graph.")


def _call_anthropic(api_key: str, model: str, system_prompt: str, user_prompt: str,
                    max_tokens: int = _MAX_TOKENS, timeout: int = _TIMEOUT) -> str:
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=timeout,
    )
    if not resp.ok:
        raise LLMError(f"Anthropic API error: {_extract_error(resp, ('error', 'message'))}")
    data = resp.json()
    for block in data.get("content", []):
        if block.get("type") == "text" and block.get("text", "").strip():
            return block["text"]
    # A thinking block and no text is the same situation as an empty content
    # field elsewhere: the budget went on reasoning. Say so, rather than
    # "no text content block", which reads like a protocol error.
    raise _empty_answer(str(data.get("stop_reason") or ""), max_tokens)


def _call_openai_compatible(
    url: str, api_key: str, model: str, system_prompt: str, user_prompt: str,
    tokens_param: str = "max_tokens",
    max_tokens: int = _MAX_TOKENS, timeout: int = _TIMEOUT,
) -> str:
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tokens_param: max_tokens,
        },
        timeout=timeout,
    )
    if not resp.ok:
        raise LLMError(f"API error: {_extract_error(resp, ('error', 'message'))}")
    data = resp.json()
    try:
        choice = data["choices"][0]
        text = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from exc
    if not (text or "").strip():
        raise _empty_answer(str(choice.get("finish_reason") or ""), max_tokens)
    return text


def _call_google(api_key: str, model: str, system_prompt: str, user_prompt: str,
                 max_tokens: int = _MAX_TOKENS, timeout: int = _TIMEOUT) -> str:
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
        timeout=timeout,
    )
    if not resp.ok:
        raise LLMError(f"Google API error: {_extract_error(resp, ('error', 'message'))}")
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "no candidates returned")
        raise LLMError(f"Google API returned no content ({reason}).")
    # A candidate that ran out of budget has no "parts" at all, so the shape
    # error below would otherwise be what an operator is told.
    if not (candidates[0].get("content") or {}).get("parts"):
        raise _empty_answer(str(candidates[0].get("finishReason") or ""), max_tokens)
    try:
        text = candidates[0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from exc
    if not text.strip():
        raise _empty_answer(str(candidates[0].get("finishReason") or ""), max_tokens)
    return text


def _call_cohere(api_key: str, model: str, system_prompt: str, user_prompt: str,
                 max_tokens: int = _MAX_TOKENS, timeout: int = _TIMEOUT) -> str:
    resp = requests.post(
        "https://api.cohere.com/v2/chat",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "stream": False,
        },
        timeout=timeout,
    )
    if not resp.ok:
        # Cohere nests its error message directly under "message", not
        # under an "error" key like every other provider here.
        try:
            message = resp.json().get("message", resp.text[:300])
        except ValueError:
            message = resp.text[:300]
        raise LLMError(f"Cohere API error: {message}")
    data = resp.json()
    if not (data.get("message") or {}).get("content"):
        raise _empty_answer(str(data.get("finish_reason") or ""), max_tokens)
    try:
        text = data["message"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from exc
    if not text.strip():
        raise _empty_answer(str(data.get("finish_reason") or ""), max_tokens)
    return text


def _extract_error(resp: requests.Response, path: tuple[str, ...]) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    node = body
    try:
        for key in path:
            node = node[key]
        return str(node)
    except (KeyError, TypeError):
        return str(body)[:300]
