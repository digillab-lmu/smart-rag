"""Regression test for the credential wiring.

The imported agents answered "Missing credentials. Please pass an apiKey, or
set the OPENAI_API_KEY environment variable" on the first message, whatever
provider was configured. Cause: set_credential_ids wrote the id to the
`credential` key, but every agentflow node reads FLOWISE_CREDENTIAL_ID —
verified in flowise@3.1.3's own source:

    Agent.ts:909   credential: modelConfig['FLOWISE_CREDENTIAL_ID']
    Agent.ts:830   credential: selectedEmbeddingModelConfig['FLOWISE_CREDENTIAL_ID']
    Agent.ts:845   credential: selectedVectorStoreConfig['FLOWISE_CREDENTIAL_ID']
    LLM.ts:376     credential: modelConfig['FLOWISE_CREDENTIAL_ID']

So the check here is deliberately keyed on FLOWISE_CREDENTIAL_ID, not on
"some credential field is set" — the old code would pass that weaker test.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
env_path.write_text(
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\nDOMAIN="example.com"\n'
    'LLM_PROVIDER="openai"\nLLM_MODEL_STRONG="gpt-5"\nLLM_MODEL_FAST="gpt-5-mini"\n'
    'LLM_API_KEY="sk-llm-test"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_MODEL="text-embedding-3-small"\n'
    'EMBEDDING_API_KEY="sk-embed-test"\nCOURSE_NAME="Testkurs"\n'
    'WEAVIATE_COLLECTION_NAME="TestChunks"\nWEAVIATE_API_KEY="wv-test-key"\n'
    'NEO4J_PASSWORD="neo4j-test"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

import agent_templates as at  # noqa: E402
import app as m  # noqa: E402
import storage  # noqa: E402

failures = []
RUNTIME_KEY = "FLOWISE_CREDENTIAL_ID"


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


def config_blocks(flow):
    """Every node config block that Flowise resolves a credential from."""
    found = []
    for node in flow.get("nodes", []):
        inputs = node.get("data", {}).get("inputs", {})
        for key in ("agentModelConfig", "llmModelConfig"):
            if isinstance(inputs.get(key), dict):
                found.append((key, inputs[key]))
        for vs in inputs.get("agentKnowledgeVSEmbeddings") or []:
            if not isinstance(vs, dict):
                continue
            for key in ("embeddingModelConfig", "vectorStoreConfig"):
                if isinstance(vs.get(key), dict):
                    found.append((key, vs[key]))
    return found


# ── Every archetype, every credential-bearing block ─────────────────────────
for arch in at.ARCHETYPES:
    flow = at.load_template(arch)
    at.set_credential_ids(flow, "llm-cred", "embed-cred", "weaviate-cred")

    blocks = config_blocks(flow)
    check(f"{arch}: has credential-bearing config blocks", blocks, "none found")

    expected = {
        "agentModelConfig": "llm-cred",
        "llmModelConfig": "llm-cred",
        "embeddingModelConfig": "embed-cred",
        "vectorStoreConfig": "weaviate-cred",
    }
    for key, cfg in blocks:
        # The whole bug in one assertion: the runtime key must carry the id.
        check(f"{arch}: {key} has {RUNTIME_KEY}", cfg.get(RUNTIME_KEY) == expected[key],
              f"{cfg.get(RUNTIME_KEY)!r} != {expected[key]!r}")
        # Kept in sync so the canvas doesn't show the agent as unconfigured.
        check(f"{arch}: {key} keeps `credential` in sync",
              cfg.get("credential") == expected[key], cfg.get("credential"))

    # Nothing may be left empty anywhere in the flow.
    leftover = [
        (k, c) for k, c in config_blocks(flow)
        if not c.get(RUNTIME_KEY)
    ]
    check(f"{arch}: no config block left without a credential", not leftover,
          [k for k, _ in leftover])

# A vector-store credential is optional in the signature; omitting it must
# not blank out a value or crash, it just leaves the store untouched.
flow = at.load_template(next(iter(at.ARCHETYPES)))
at.set_credential_ids(flow, "llm-cred", "embed-cred")
vs_blocks = [c for k, c in config_blocks(flow) if k == "vectorStoreConfig"]
check("omitted vectorstore credential leaves the block alone",
      all(not c.get(RUNTIME_KEY) for c in vs_blocks), vs_blocks[:1])
llm_blocks = [c for k, c in config_blocks(flow) if k == "agentModelConfig"]
check("omitted vectorstore credential doesn't affect the LLM",
      all(c.get(RUNTIME_KEY) == "llm-cred" for c in llm_blocks), llm_blocks[:1])

# Malformed nodes must not raise — a template with a missing block is a
# template problem, not a reason to 500 the import.
at.set_credential_ids({"nodes": [{}, {"data": {}}, {"data": {"inputs": None}}]}, "a", "b", "c")
at.set_credential_ids({}, "a", "b", "c")
at.set_credential_ids(
    {"nodes": [{"data": {"inputs": {"agentKnowledgeVSEmbeddings": [None, "x"]}}}]},
    "a", "b", "c")

# ── The import path creates all three credentials ───────────────────────────
ARCH = "agent-11-expert-feedback.json"
storage.save_slot(1, ARCH, {
    "EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d", "CONCEPT_LIST": "c",
    "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s",
}, "Cred Agent", None)

created = []
captured = {}


class FakeFlowise:
    def upsert_credential(self, name, cred_type, data):
        created.append({"name": name, "type": cred_type, "data": data})
        return f"id-of-{cred_type}"

    def get_or_create_variable(self, *a, **kw):
        return "var-id"

    def upsert_chatflow(self, name, flow_data, analytic=None):
        captured["flow"] = flow_data
        return "chatflow-id", True


err = m._do_import(1, ARCH, FakeFlowise())
check("import succeeded", err is None, str(err))

types = [c["type"] for c in created]
check("LLM credential created", "openAIApi" in types, types)
check("Weaviate credential created", "weaviateApi" in types, types)
check("exactly three credentials created", len(created) == 3, types)

wv = next((c for c in created if c["type"] == "weaviateApi"), None)
check("weaviate credential carries the key from .env",
      wv and wv["data"] == {"weaviateApiKey": "wv-test-key"}, wv)
check("weaviate credential is named for this project",
      wv and wv["name"] == "smartrag-weaviate", wv)

# And the ids actually reach the flow that gets uploaded.
flow_json = captured.get("flow", "")
parsed = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
for key, cfg in config_blocks(parsed):
    check(f"imported flow: {key} carries a credential id",
          str(cfg.get(RUNTIME_KEY, "")).startswith("id-of-"),
          f"{key}={cfg.get(RUNTIME_KEY)!r}")

# The exact string that used to reach Flowise: an empty credential.
check("no empty credential survives into the imported flow",
      '"FLOWISE_CREDENTIAL_ID": ""' not in flow_json
      and '"credential": ""' not in flow_json,
      "an empty credential is still in the uploaded flow")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All credential-wiring checks passed: every credential-bearing config block in every "
    "archetype (agent model, LLM model, embeddings, vector store) carries the id under "
    "FLOWISE_CREDENTIAL_ID — the key Flowise's runtime actually reads — with `credential` "
    "kept in sync for the canvas; omitting the optional vector-store credential leaves that "
    "block alone without affecting the others; malformed nodes don't raise; and the import "
    "path creates all three credentials (LLM, embeddings, and the Weaviate one Weaviate's "
    "API-key auth requires) and gets their ids into the uploaded flow with none left empty."
)
