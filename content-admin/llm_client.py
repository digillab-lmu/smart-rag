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


class LLMError(Exception):
    pass


def _complete(system_prompt: str, user_prompt: str, env: dict) -> str:
    """
    One chat completion against whichever provider is configured, returning
    the assistant's raw text. Extracted so every AI-assist feature shares a
    single provider dispatch — a new one (keyword suggestions, prompt
    translation) doesn't mean another copy of this ladder to keep in step.
    """
    provider = env.get("LLM_PROVIDER", "anthropic")
    api_key = env.get("LLM_API_KEY", "")
    model = env.get("LLM_MODEL_STRONG", "")
    if not api_key:
        raise LLMError("No LLM API key configured (LLM_API_KEY is empty in .env).")
    if not model:
        raise LLMError("No LLM model configured (LLM_MODEL_STRONG is empty in .env).")

    if provider == "anthropic":
        return _call_anthropic(api_key, model, system_prompt, user_prompt)
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
            tokens_param="max_completion_tokens",
        )
    if provider == "google":
        return _call_google(api_key, model, system_prompt, user_prompt)
    if provider == "mistral":
        return _call_openai_compatible(
            "https://api.mistral.ai/v1/chat/completions", api_key, model, system_prompt, user_prompt
        )
    if provider == "cohere":
        return _call_cohere(api_key, model, system_prompt, user_prompt)
    if provider == "openrouter":
        # config-wizard.sh already stores openrouter model strings with the
        # required provider/ prefix (e.g. "anthropic/claude-sonnet-5") —
        # nothing extra to do here.
        return _call_openai_compatible(
            "https://openrouter.ai/api/v1/chat/completions", api_key, model, system_prompt, user_prompt
        )
    if provider == "custom":
        base_url = env.get("LLM_BASE_URL", "").rstrip("/")
        if not base_url:
            raise LLMError("LLM_PROVIDER is 'custom' but LLM_BASE_URL is empty in .env.")
        return _call_openai_compatible(
            f"{base_url}/chat/completions", api_key, model, system_prompt, user_prompt
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


def _call_anthropic(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        raise LLMError(f"Anthropic API error: {_extract_error(resp, ('error', 'message'))}")
    data = resp.json()
    for block in data.get("content", []):
        if block.get("type") == "text":
            return block["text"]
    raise LLMError("Anthropic response had no text content block.")


def _call_openai_compatible(
    url: str, api_key: str, model: str, system_prompt: str, user_prompt: str,
    tokens_param: str = "max_tokens",
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
            tokens_param: _MAX_TOKENS,
        },
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        raise LLMError(f"API error: {_extract_error(resp, ('error', 'message'))}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from exc


def _call_google(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": _MAX_TOKENS},
        },
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        raise LLMError(f"Google API error: {_extract_error(resp, ('error', 'message'))}")
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "no candidates returned")
        raise LLMError(f"Google API returned no content ({reason}).")
    try:
        return candidates[0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from exc


def _call_cohere(api_key: str, model: str, system_prompt: str, user_prompt: str) -> str:
    resp = requests.post(
        "https://api.cohere.com/v2/chat",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": _MAX_TOKENS,
            "stream": False,
        },
        timeout=_TIMEOUT,
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
    try:
        return data["message"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from exc


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
