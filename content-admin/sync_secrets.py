"""
Push the API keys from .env into Flowise.

Changing a key in .env is not enough. Flowise keeps its own copy in a
credential, created when an agent was imported, and the agents reference it
by id. Until that credential is rewritten, every agent keeps authenticating
with the key that was replaced — and nothing reports a problem, because from
Flowise's point of view nothing changed.

Run from the admin TUI after a key is changed:

    docker exec smartrag-content-admin python -m sync_secrets

Deliberately lives here rather than in the TUI's bash: the mapping from a
provider name to Flowise's credential type ("anthropic" → "anthropicApi",
field "anthropicApiKey") already exists in agent_templates, and a second copy
in shell would be one more pair of things that must agree and eventually
would not.

Exit codes: 0 everything applied, 1 nothing could be applied (Flowise
unreachable or the key rejected), 2 applied in part.
"""

import sys

import agent_templates
from env_file import read_env
from flowise_client import FlowiseClient, FlowiseError

FLOWISE_INTERNAL_URL = "http://smartrag-flowise:3000/api/v1"


def _targets(env: dict) -> list[tuple[str, str, str, dict]]:
    """(label, credential name, credential type, plain data) for each key.

    Only credentials appear here. The five Flowise *variables* are handled by
    get_or_create_variable(), which already rewrites a changed value.
    """
    llm_provider = env.get("LLM_PROVIDER", "anthropic")
    embed_provider = env.get("EMBEDDING_PROVIDER", "openai")
    llm_map = agent_templates.LLM_PROVIDER_MAP.get(
        llm_provider, agent_templates.LLM_PROVIDER_MAP["anthropic"]
    )
    embed_map = agent_templates.EMBEDDING_PROVIDER_MAP.get(
        embed_provider, agent_templates.EMBEDDING_PROVIDER_MAP["openai"]
    )
    return [
        (
            f"LLM ({llm_provider})",
            f"smartrag-llm-{llm_provider}",
            llm_map["credential_name"],
            {llm_map["credential_key"]: env.get("LLM_API_KEY", "")},
        ),
        (
            f"Embedding ({embed_provider})",
            f"smartrag-embedding-{embed_provider}",
            embed_map["credential_name"],
            {embed_map["credential_key"]: env.get("EMBEDDING_API_KEY", "")},
        ),
        (
            "Weaviate",
            "smartrag-weaviate",
            "weaviateApi",
            {"weaviateApiKey": env.get("WEAVIATE_API_KEY", "")},
        ),
    ]


def main() -> int:
    env = read_env()
    api_key = env.get("FLOWISE_API_KEY", "").strip()
    if not api_key:
        print("No FLOWISE_API_KEY in .env — nothing to push to.", file=sys.stderr)
        return 1

    client = FlowiseClient(FLOWISE_INTERNAL_URL, api_key)
    try:
        client.check_connection()
    except FlowiseError as exc:
        print(f"Flowise is not accepting the stored API key: {exc}", file=sys.stderr)
        return 1

    applied, failed = 0, 0
    for label, name, cred_type, data in _targets(env):
        # An empty key would overwrite a working credential with nothing.
        if not next(iter(data.values()), ""):
            print(f"  skipped {label}: no value in .env")
            continue
        try:
            client.upsert_credential(name, cred_type, data)
        except FlowiseError as exc:
            print(f"  FAILED  {label}: {exc}", file=sys.stderr)
            failed += 1
        else:
            print(f"  updated {label} → {name}")
            applied += 1

    # The variables the custom-function nodes read. get_or_create_variable
    # rewrites a changed value, so this is a plain re-push.
    for var in ("EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
                "WEAVIATE_API_KEY", "NEO4J_PASSWORD"):
        value = env.get(var, "")
        if not value:
            continue
        try:
            client.get_or_create_variable(var, value)
        except FlowiseError as exc:
            print(f"  FAILED  variable {var}: {exc}", file=sys.stderr)
            failed += 1
        else:
            applied += 1

    if failed and applied:
        return 2
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
