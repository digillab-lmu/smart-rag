"""
An unconfigured provider must be refused, never substituted.

Nine places resolved LLM_PROVIDER/EMBEDDING_PROVIDER with a default of
"anthropic"/"openai". With the variable empty or misspelled, the request then
went to a vendor nobody had configured, carrying a credential shaped for a
different one — and came back as "invalid x-api-key" from a service the
operator had never chosen. That error points away from the actual problem.

Refusing costs nothing here: the wizard always writes the variable, so the
only installations affected are already broken, and they now say how.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "content-admin"))

import agent_templates as at  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def refuses(fn, env):
    try:
        fn(env)
    except at.ProviderNotConfigured as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"WRONG EXCEPTION: {type(exc).__name__}: {exc}"
    return ""


# ─── 1. Empty, missing and unknown are all refused ───────────────────────────
for label, env in [
    ("missing key", {}),
    ("empty value", {"LLM_PROVIDER": ""}),
    ("whitespace only", {"LLM_PROVIDER": "   "}),
    ("unknown provider", {"LLM_PROVIDER": "gpt5-inc"}),
]:
    msg = refuses(at.resolve_llm_provider, env)
    check(f"LLM: {label} is refused", msg and "WRONG" not in msg, msg or "returned a default")
    check(f"LLM: {label} names the variable", "LLM_PROVIDER" in msg, msg)

for label, env in [
    ("missing key", {}),
    ("empty value", {"EMBEDDING_PROVIDER": ""}),
    ("unknown provider", {"EMBEDDING_PROVIDER": "nope"}),
]:
    msg = refuses(at.resolve_embedding_provider, env)
    check(f"Embedding: {label} is refused", msg and "WRONG" not in msg, msg or "returned a default")
    check(f"Embedding: {label} names the variable", "EMBEDDING_PROVIDER" in msg, msg)

# An unknown value must list what IS accepted — otherwise the operator has to
# read the source to find out how to spell it.
msg = refuses(at.resolve_llm_provider, {"LLM_PROVIDER": "gpt5-inc"})
check("an unknown provider lists the known ones", "anthropic" in msg and "openai" in msg, msg)

# ─── 2. A configured provider still resolves, and to ITS OWN mapping ─────────
for provider in at.LLM_PROVIDER_MAP:
    got_name, got_map = at.resolve_llm_provider({"LLM_PROVIDER": provider})
    check(f"LLM {provider} resolves to itself", got_name == provider, got_name)
    check(f"LLM {provider} gets its own credential type",
          got_map is at.LLM_PROVIDER_MAP[provider], got_map)
for provider in at.EMBEDDING_PROVIDER_MAP:
    got_name, got_map = at.resolve_embedding_provider({"EMBEDDING_PROVIDER": provider})
    check(f"Embedding {provider} resolves to itself", got_name == provider, got_name)

# Surrounding whitespace in .env must not make a valid provider unknown.
got_name, _ = at.resolve_llm_provider({"LLM_PROVIDER": "  openai  "})
check("a padded value still resolves", got_name == "openai", got_name)

# ─── 3. No silent fallback survives anywhere ─────────────────────────────────
import re  # noqa: E402

pattern = re.compile(
    r"""(LLM_PROVIDER|EMBEDDING_PROVIDER)["']?\s*,\s*["'](anthropic|openai)["']"""
)
for path in sorted((REPO / "content-admin").glob("*.py")):
    body = "\n".join(
        l for l in path.read_text().splitlines() if not l.strip().startswith("#")
    )
    hit = pattern.search(body)
    check(f"{path.name} has no defaulted provider lookup", not hit,
          hit.group(0) if hit else "")

wf = (REPO / "n8n" / "workflows-ingest" / "ingest-document.json").read_text()
check("the workflow has no `|| 'anthropic'` fallback",
      "LLM_PROVIDER || 'anthropic'" not in wf, "still falls back to another vendor")
check("the workflow refuses instead", "LLM_PROVIDER is empty" in wf,
      "no explicit refusal found")

# ─── 4. The GUI turns the refusal into a message, not a traceback ────────────
app_src = (REPO / "content-admin" / "app.py").read_text()
check("the import catches the refusal", "ProviderNotConfigured" in app_src, "")
check("and reports it through i18n", "import_err_provider" in app_src, "")
import i18n  # noqa: E402
for lang in ("en", "de"):
    msg = i18n.t("import_err_provider", "LLM_PROVIDER is empty in .env.", lang=lang)
    check(f"[{lang}] the message carries the detail", "LLM_PROVIDER" in msg, msg)
    check(f"[{lang}] and says nothing was changed",
          "no agent" in msg.lower() or "kein agent" in msg.lower(), msg)

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All provider-resolution checks passed: a missing, empty, whitespace-only "
    "or unknown provider is refused with a message naming the variable and "
    "listing the accepted values; every configured provider still resolves to "
    "its own credential mapping; no defaulted lookup remains in any "
    "content-admin module or in the ingest workflow; and the GUI turns the "
    "refusal into a localised message that states no agent was changed."
)
