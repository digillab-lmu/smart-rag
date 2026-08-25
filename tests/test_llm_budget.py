"""An answer the model never wrote, and the message that described it wrongly.

Live, on the test installation: "Karte aus dem Kursmaterial vorschlagen" ran
for six seconds and reported **"Nothing was pasted."** Nobody had pasted
anything — the operator had pressed a button.

What happened: LLM_MODEL_STRONG there is a reasoning model, and
`max_completion_tokens` counts reasoning tokens. The shared ceiling was 1024,
sized for keyword suggestions; the model spent all of it thinking and returned
`content: ""` with `finish_reason: "length"`, which the API reports as a
perfectly successful call. The empty string travelled on to parse_proposal,
whose first refusal is for an empty box.

Two defects, and they are independent — either alone would come back:

  * a budget belonging to one feature was applied to a much larger one;
  * an empty answer was not treated as a failure where it happened, so it was
    named by whatever tried to use it several layers away.

So the checks below cover every provider, not the one that failed.
"""

import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "content-admin"
if not APP_DIR.is_dir() and Path("/app/db.py").exists():
    APP_DIR = Path("/app")
sys.path.insert(0, str(APP_DIR))

import llm_client as L  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


class _Resp:
    ok = True

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _stub(payload, capture=None):
    def post(url, **kw):
        if capture is not None:
            capture.clear()
            capture.update(body=kw["json"], timeout=kw["timeout"], url=url)
        return _Resp(payload)
    L.requests = types.SimpleNamespace(post=post)


ENV = {"LLM_PROVIDER": "openai", "LLM_API_KEY": "k", "LLM_MODEL_STRONG": "m"}

# ─── An empty answer is a failure, in every provider ────────────────────────
# Each payload is the shape that provider really returns when the budget went
# on reasoning: an empty content field, a candidate with no parts, a thinking
# block with no text, an empty content list.
EMPTY = {
    "openai":    {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
    "mistral":   {"choices": [{"message": {"content": "  "}, "finish_reason": "length"}]},
    "openrouter": {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
    "google":    {"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]},
    "anthropic": {"content": [{"type": "thinking", "thinking": "…"}],
                  "stop_reason": "max_tokens"},
    "cohere":    {"message": {"content": []}, "finish_reason": "MAX_TOKENS"},
}
for provider, payload in EMPTY.items():
    _stub(payload)
    try:
        got = L._complete("s", "u", dict(ENV, LLM_PROVIDER=provider))
        check(f"{provider}: an empty answer raises", False,
              f"it returned {got!r} for something to trip over later")
    except L.LLMError as exc:
        msg = str(exc)
        check(f"{provider}: an empty answer raises", True)
        # The message must describe what happened. "Nothing was pasted" is the
        # counter-example this test exists for.
        check(f"{provider}: and says the model returned nothing",
              "empty answer" in msg, msg)
        check(f"{provider}: and names the reason the API gave",
              any(w in msg.lower() for w in ("length", "max_token")), msg)
        check(f"{provider}: and names the ceiling that was hit",
              "1024" in msg, msg)
        check(f"{provider}: and says nothing was written",
              "Nothing was written" in msg, msg)
        check(f"{provider}: and does not blame the operator",
              "paste" not in msg.lower(), msg)

# A model that simply had nothing to say still fails, but must not be blamed
# on the token limit.
_stub({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})
try:
    L._complete("s", "u", ENV)
    check("an empty answer with a normal stop still raises", False, "")
except L.LLMError as exc:
    check("an empty answer with a normal stop still raises", True)
    check("and is not blamed on the token limit",
          "token limit" not in str(exc), str(exc))

# ─── The budget follows the feature, not the other way round ────────────────
GOOD = {"choices": [{"message": {"content": '{"concepts":[{"name":"A"}],'
                                            '"prerequisites":[]}'},
                     "finish_reason": "stop"}]}
seen = {}
_stub(GOOD, seen)

answer = L.propose_graph("Kurs", "Material genug", "Anweisung", ENV)
long_tokens = seen["body"].get("max_completion_tokens")
long_timeout = seen["timeout"]
check("the concept map is asked for with a large budget",
      isinstance(long_tokens, int) and long_tokens >= 8192, long_tokens)
check("and a timeout that fits a large answer",
      long_timeout >= 120, long_timeout)
check("the model's answer is returned unchanged",
      answer.startswith('{"concepts"'), answer[:40])

L._complete("s", "u", ENV)
short_tokens = seen["body"].get("max_completion_tokens")
check("a short assist keeps the small budget",
      short_tokens == 1024, short_tokens)
check("so the two are not one number", long_tokens > short_tokens,
      (long_tokens, short_tokens))
check("and the short call keeps the short timeout", seen["timeout"] == 30,
      seen["timeout"])

# The ceiling must reach the wire for every provider, not only the one whose
# request body happens to be checked above.
for provider, field in (("anthropic", "max_tokens"), ("mistral", "max_tokens"),
                        ("openrouter", "max_tokens"), ("cohere", "max_tokens")):
    payload = {"content": [{"type": "text", "text": "x"}]} if provider == "anthropic" \
        else {"message": {"content": [{"text": "x"}]}} if provider == "cohere" \
        else GOOD
    _stub(payload, seen)
    L._complete("s", "u", dict(ENV, LLM_PROVIDER=provider), max_tokens=4242)
    check(f"{provider} sends the budget it was given",
          seen["body"].get(field) == 4242, seen["body"].get(field))

_stub({"candidates": [{"content": {"parts": [{"text": "x"}]}}]}, seen)
L._complete("s", "u", dict(ENV, LLM_PROVIDER="google"), max_tokens=4242)
check("google sends the budget it was given",
      (seen["body"].get("generationConfig") or {}).get("maxOutputTokens") == 4242,
      seen["body"].get("generationConfig"))

# ─── No material is refused before a request is made ────────────────────────
called = []
L.requests = types.SimpleNamespace(post=lambda *a, **k: called.append(1))
try:
    L.propose_graph("Kurs", "   ", "Anweisung", ENV)
    check("an empty course is refused", False, "it asked anyway")
except L.LLMError as exc:
    check("an empty course is refused", True)
    check("without spending a request", not called, "the API was called")
    check("and the refusal explains why",
          "general knowledge" in str(exc), str(exc))

if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("All LLM budget checks passed: an answer with no text is a failure where")
print("it happens rather than an empty string handed on — for all six provider")
print("shapes, naming the finish reason, the ceiling that was hit and the fact")
print("that nothing was written, and never telling the operator they pasted")
print("nothing when they pressed a button; an empty answer that stopped")
print("normally is not blamed on the limit; the concept map is asked for with a")
print("budget and a timeout of its own while the short assists keep theirs, and")
print("every provider puts the budget it was given into the request it sends;")
print("and a course with no material is refused before a request is spent.")
