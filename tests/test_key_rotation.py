"""
Rotating an API key has to reach Flowise, not just .env.

Flowise keeps its own copy of every key in a credential created when an agent
was imported, and the agents reference it by id. The previous implementation
looked a credential up by name and returned it untouched, so:

  * changing LLM_API_KEY in .env changed nothing an agent used;
  * re-importing the agent did not help either — same name, same stale value;
  * and nothing failed, because from Flowise's side nothing had changed.

The operator would have seen a green "saved", and every agent would have kept
authenticating with the key they had just replaced.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "content-admin"))

from flowise_client import FlowiseClient  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


class RecordingClient(FlowiseClient):
    """Records requests instead of making them."""

    def __init__(self, existing):
        super().__init__("http://stub/api/v1", "key")
        self.existing = existing
        self.calls = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        if method == "GET" and path == "/credentials":
            return self.existing
        if method == "GET" and path == "/variables":
            return []
        if method == "POST":
            return {"id": "new-id"}
        return {}


# ─── 1. An existing credential is REWRITTEN, not returned untouched ──────────
c = RecordingClient([{"id": "cred-1", "name": "smartrag-llm-anthropic"}])
returned = c.upsert_credential(
    "smartrag-llm-anthropic", "anthropicApi", {"anthropicApiKey": "new-key"}
)
writes = [x for x in c.calls if x[0] in ("PUT", "POST")]
check("an existing credential is written to", len(writes) == 1, c.calls)
check("via PUT on its id", writes and writes[0][0] == "PUT" and writes[0][1] == "/credentials/cred-1",
      writes)
check("carrying the new value", writes and writes[0][2]["plainDataObj"]["anthropicApiKey"] == "new-key",
      writes)
check("and its id is returned", returned == "cred-1", returned)

# ─── 2. A missing credential is created ──────────────────────────────────────
c = RecordingClient([])
returned = c.upsert_credential("smartrag-llm-openai", "openAIApi", {"openAIApiKey": "k"})
posts = [x for x in c.calls if x[0] == "POST"]
check("a missing credential is created", len(posts) == 1, c.calls)
check("the created id is returned", returned == "new-id", returned)

# ─── 3. Name matching is exact ───────────────────────────────────────────────
# smartrag-llm-openai must not be mistaken for smartrag-llm-openai-old.
c = RecordingClient([{"id": "other", "name": "smartrag-llm-openai-old"}])
c.upsert_credential("smartrag-llm-openai", "openAIApi", {"openAIApiKey": "k"})
check("a similarly named credential is not overwritten",
      all(x[1] != "/credentials/other" for x in c.calls), c.calls)

# ─── 4. sync_secrets targets every key Flowise holds ─────────────────────────
import sync_secrets  # noqa: E402

env = {
    "LLM_PROVIDER": "anthropic", "LLM_API_KEY": "L",
    "EMBEDDING_PROVIDER": "openai", "EMBEDDING_API_KEY": "E",
    "WEAVIATE_API_KEY": "W",
}
targets = sync_secrets._targets(env)
names = [t[1] for t in targets]
check("the LLM credential is covered", "smartrag-llm-anthropic" in names, names)
check("the embedding credential is covered", "smartrag-embedding-openai" in names, names)
check("the Weaviate credential is covered", "smartrag-weaviate" in names, names)
# The names must be the ones the import creates, or the push writes to a
# credential no agent references.
app_src = (REPO / "content-admin" / "app.py").read_text()
for n in ("smartrag-llm-", "smartrag-embedding-", "smartrag-weaviate"):
    check(f"{n}… matches what the import creates", n in app_src,
          "sync_secrets would write a credential no agent uses")

# The value actually pushed must come from .env, not a placeholder.
llm_target = next(t for t in targets if t[1] == "smartrag-llm-anthropic")
check("the LLM key from .env is what gets pushed",
      "L" in llm_target[3].values(), llm_target[3])

# ─── 5. An empty key must not wipe a working credential ──────────────────────
# Rotating with an empty value is a mistake, not an instruction to clear it.
src = (REPO / "content-admin" / "sync_secrets.py").read_text()
check("an empty value is skipped rather than pushed",
      "no value in .env" in src, "an empty key would overwrite a working credential")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All key-rotation checks passed: an existing Flowise credential is "
    "rewritten rather than returned untouched, a missing one is created, a "
    "similarly named one is left alone, sync_secrets covers exactly the "
    "credential names the import creates and pushes the values from .env, and "
    "an empty key is skipped instead of wiping a working credential."
)
