import sys

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")

sys.path.insert(0, APP_DIR)

import llm_client  # noqa: E402


failures = []


class FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._json = json_data
        self.text = text if text else str(json_data)

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def patch_post(monkeypatch_target, fn):
    llm_client.requests.post = fn


real_post = llm_client.requests.post


def reset_post():
    llm_client.requests.post = real_post


GOOD_JSON_REPLY = '{"suggestion": "Improved text.", "rationale": "Made it clearer."}'


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


# ── 1. Anthropic ──────────────────────────────────────────────────────────
captured = {}


def fake_post_anthropic(url, headers=None, json=None, timeout=None):
    captured["url"] = url
    captured["headers"] = headers
    captured["json"] = json
    return FakeResponse(200, {"content": [{"type": "text", "text": GOOD_JSON_REPLY}]})


llm_client.requests.post = fake_post_anthropic
result = llm_client.optimize_field(
    "TOPIC_NAME", "chapter name", "draft", {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "k", "LLM_MODEL_STRONG": "claude-sonnet-5"}
)
check("anthropic url", captured["url"] == "https://api.anthropic.com/v1/messages", captured.get("url"))
check("anthropic auth header", captured["headers"]["x-api-key"] == "k")
check("anthropic version header", captured["headers"]["anthropic-version"] == "2023-06-01")
check("anthropic model in body", captured["json"]["model"] == "claude-sonnet-5")
check("anthropic max_tokens present", captured["json"]["max_tokens"] == 1024)
check("anthropic system prompt present", "TOPIC_NAME" not in captured["json"]["system"] and len(captured["json"]["system"]) > 0)
check("anthropic user prompt contains field context", "TOPIC_NAME" in captured["json"]["messages"][0]["content"])
check("anthropic result parsed", result == {"suggestion": "Improved text.", "rationale": "Made it clearer."}, result)
reset_post()

# ── 2. OpenAI ──────────────────────────────────────────────────────────────
captured = {}


def fake_post_openai(url, headers=None, json=None, timeout=None):
    captured["url"] = url
    captured["headers"] = headers
    captured["json"] = json
    return FakeResponse(200, {"choices": [{"message": {"content": GOOD_JSON_REPLY}}]})


llm_client.requests.post = fake_post_openai
result = llm_client.optimize_field(
    "STUDENT_ROLE", "who the students are", "", {"LLM_PROVIDER": "openai", "LLM_API_KEY": "k2", "LLM_MODEL_STRONG": "gpt-5.2"}
)
check("openai url", captured["url"] == "https://api.openai.com/v1/chat/completions", captured.get("url"))
check("openai auth header", captured["headers"]["Authorization"] == "Bearer k2")
check("openai model", captured["json"]["model"] == "gpt-5.2")
check("openai messages shape", captured["json"]["messages"][0]["role"] == "system")
check("openai uses max_completion_tokens, not max_tokens", "max_completion_tokens" in captured["json"] and "max_tokens" not in captured["json"], captured["json"])
check("openai result parsed", result["suggestion"] == "Improved text.")
reset_post()

# ── 3. Google ──────────────────────────────────────────────────────────────
captured = {}


def fake_post_google(url, headers=None, json=None, timeout=None):
    captured["url"] = url
    captured["headers"] = headers
    captured["json"] = json
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": GOOD_JSON_REPLY}]}}]})


llm_client.requests.post = fake_post_google
result = llm_client.optimize_field(
    "TOPIC_NAME", "chapter name", "draft", {"LLM_PROVIDER": "google", "LLM_API_KEY": "k3", "LLM_MODEL_STRONG": "gemini-3.5-flash"}
)
check("google url has model", "gemini-3.5-flash:generateContent" in captured["url"], captured.get("url"))
check("google auth header", captured["headers"]["x-goog-api-key"] == "k3")
check("google systemInstruction present", "systemInstruction" in captured["json"])
check("google result parsed", result["suggestion"] == "Improved text.")
reset_post()

# Google: empty candidates -> LLMError with blockReason surfaced
def fake_post_google_blocked(url, headers=None, json=None, timeout=None):
    return FakeResponse(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})


llm_client.requests.post = fake_post_google_blocked
try:
    llm_client.optimize_field("TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "google", "LLM_API_KEY": "k", "LLM_MODEL_STRONG": "m"})
    failures.append("google blocked response should have raised LLMError")
except llm_client.LLMError as exc:
    check("google blockReason surfaced", "SAFETY" in str(exc), str(exc))
reset_post()

# ── 4. Mistral ─────────────────────────────────────────────────────────────
captured = {}


def fake_post_mistral(url, headers=None, json=None, timeout=None):
    captured["url"] = url
    captured["headers"] = headers
    captured["json"] = json
    return FakeResponse(200, {"choices": [{"message": {"content": GOOD_JSON_REPLY}}]})


llm_client.requests.post = fake_post_mistral
result = llm_client.optimize_field(
    "TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "mistral", "LLM_API_KEY": "k4", "LLM_MODEL_STRONG": "mistral-large-latest"}
)
check("mistral url", captured["url"] == "https://api.mistral.ai/v1/chat/completions", captured.get("url"))
check("mistral still uses max_tokens (not OpenAI's max_completion_tokens)", "max_tokens" in captured["json"] and "max_completion_tokens" not in captured["json"], captured["json"])
check("mistral result parsed", result["suggestion"] == "Improved text.")
reset_post()

# ── 5. Cohere ──────────────────────────────────────────────────────────────
captured = {}


def fake_post_cohere(url, headers=None, json=None, timeout=None):
    captured["url"] = url
    captured["headers"] = headers
    captured["json"] = json
    return FakeResponse(200, {"message": {"content": [{"text": GOOD_JSON_REPLY}]}})


llm_client.requests.post = fake_post_cohere
result = llm_client.optimize_field(
    "TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "cohere", "LLM_API_KEY": "k5", "LLM_MODEL_STRONG": "command-a"}
)
check("cohere url", captured["url"] == "https://api.cohere.com/v2/chat", captured.get("url"))
check("cohere stream false", captured["json"]["stream"] is False)
check("cohere result parsed", result["suggestion"] == "Improved text.")
reset_post()

# Cohere error shape: flat "message" key, not nested under "error"
def fake_post_cohere_error(url, headers=None, json=None, timeout=None):
    return FakeResponse(401, {"message": "invalid api token"})


llm_client.requests.post = fake_post_cohere_error
try:
    llm_client.optimize_field("TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "cohere", "LLM_API_KEY": "bad", "LLM_MODEL_STRONG": "m"})
    failures.append("cohere error response should have raised LLMError")
except llm_client.LLMError as exc:
    check("cohere flat error message surfaced", "invalid api token" in str(exc), str(exc))
reset_post()

# ── 6. OpenRouter ────────────────────────────────────────────────────────
captured = {}


def fake_post_openrouter(url, headers=None, json=None, timeout=None):
    captured["url"] = url
    captured["json"] = json
    return FakeResponse(200, {"choices": [{"message": {"content": GOOD_JSON_REPLY}}]})


llm_client.requests.post = fake_post_openrouter
result = llm_client.optimize_field(
    "TOPIC_NAME", "x", "y",
    {"LLM_PROVIDER": "openrouter", "LLM_API_KEY": "k6", "LLM_MODEL_STRONG": "anthropic/claude-sonnet-5"},
)
check("openrouter url", captured["url"] == "https://openrouter.ai/api/v1/chat/completions", captured.get("url"))
check("openrouter model passed through with prefix", captured["json"]["model"] == "anthropic/claude-sonnet-5")
reset_post()

# ── 7. custom (OpenAI-compatible) ───────────────────────────────────────────
captured = {}


def fake_post_custom(url, headers=None, json=None, timeout=None):
    captured["url"] = url
    return FakeResponse(200, {"choices": [{"message": {"content": GOOD_JSON_REPLY}}]})


llm_client.requests.post = fake_post_custom
result = llm_client.optimize_field(
    "TOPIC_NAME", "x", "y",
    {"LLM_PROVIDER": "custom", "LLM_API_KEY": "k7", "LLM_MODEL_STRONG": "local-model", "LLM_BASE_URL": "http://localhost:8080/v1"},
)
check("custom url built from LLM_BASE_URL", captured["url"] == "http://localhost:8080/v1/chat/completions", captured.get("url"))
reset_post()

try:
    llm_client.optimize_field(
        "TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "custom", "LLM_API_KEY": "k7", "LLM_MODEL_STRONG": "m", "LLM_BASE_URL": ""}
    )
    failures.append("custom provider with empty LLM_BASE_URL should have raised LLMError")
except llm_client.LLMError:
    pass

# ── 8. Config validation ────────────────────────────────────────────────────
try:
    llm_client.optimize_field("TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "", "LLM_MODEL_STRONG": "m"})
    failures.append("missing LLM_API_KEY should have raised LLMError")
except llm_client.LLMError as exc:
    check("missing api key message", "LLM_API_KEY" in str(exc))

try:
    llm_client.optimize_field("TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "k", "LLM_MODEL_STRONG": ""})
    failures.append("missing LLM_MODEL_STRONG should have raised LLMError")
except llm_client.LLMError as exc:
    check("missing model message", "LLM_MODEL_STRONG" in str(exc))

try:
    llm_client.optimize_field("TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "made-up", "LLM_API_KEY": "k", "LLM_MODEL_STRONG": "m"})
    failures.append("unknown provider should have raised LLMError")
except llm_client.LLMError as exc:
    check("unknown provider message", "made-up" in str(exc), str(exc))

# ── 9. Response parsing edge cases ──────────────────────────────────────────
check(
    "parse plain JSON",
    llm_client._parse_suggestion('{"suggestion": "a", "rationale": "b"}') == {"suggestion": "a", "rationale": "b"},
)
check(
    "parse markdown-fenced JSON",
    llm_client._parse_suggestion('```json\n{"suggestion": "a", "rationale": "b"}\n```') == {"suggestion": "a", "rationale": "b"},
)
try:
    llm_client._parse_suggestion("not json at all")
    failures.append("garbage response should have raised LLMError")
except llm_client.LLMError:
    pass
try:
    llm_client._parse_suggestion('{"suggestion": "a"}')
    failures.append("response missing rationale should have raised LLMError")
except llm_client.LLMError:
    pass

# ── 10. HTTP error surfacing (nested error.message shape used by most providers) ──
def fake_post_openai_error(url, headers=None, json=None, timeout=None):
    return FakeResponse(400, {"error": {"message": "invalid model", "type": "invalid_request_error"}})


llm_client.requests.post = fake_post_openai_error
try:
    llm_client.optimize_field("TOPIC_NAME", "x", "y", {"LLM_PROVIDER": "openai", "LLM_API_KEY": "k", "LLM_MODEL_STRONG": "bad-model"})
    failures.append("openai error response should have raised LLMError")
except llm_client.LLMError as exc:
    check("openai nested error.message surfaced", "invalid model" in str(exc), str(exc))
reset_post()

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("All llm_client.py checks passed: request building + response parsing for all 6 providers "
      "+ custom, config validation, JSON/markdown-fence parsing, error-shape extraction "
      "(incl. Google's blocked-response and Cohere's flat error-message gotchas).")
