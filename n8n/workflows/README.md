# n8n Workflows — Core

These workflows are the runtime memory/observability pipelines that keep the
multi-agent system stateful and traceable. They are imported and activated by
`scripts/deploy-n8n-workflows.sh`, which bootstrap runs as phase 10 and the
admin tool can re-run at any time.

Until 2026-08-11 this file claimed bootstrap imported them "automatically via
the n8n REST API". Nothing did — the deployer read `workflows-ingest/` only.
They were documented, present and dead, which is worse than absent: absent
invites building, documented-and-dead invites relying.

| File | Trigger | Purpose |
|------|---------|---------|
| `chathistory-sync.json`     | Schedule (every 5 min) | Polls Postgres for new Flowise messages, generates embeddings + metadata, writes to Weaviate `ChatHistory` for cross-agent semantic recall. |
| `usermemory-summary.json`   | Schedule              | Periodically condenses each user's recent sessions into the Weaviate `UserMemory` record. Calls `$env.LLM_BASE_URL` with `$env.LLM_MODEL_FAST`, like every other LLM call here — it used to be an Anthropic node with a hard-coded model, which could not run on an installation configured for any other provider. |
| `langfuse-userid-patch.json`| Schedule (every 30 min) | Looks up `userId` from Flowise's Postgres for Langfuse traces that came in without one, then patches the trace. Deployed only with the `observability` profile. **Handles personal data:** it parses the LTI session id (`userId|givenName|agentId|ts|fullName`) and writes the learner's name into Langfuse. It therefore does nothing without the LTI middleware, and where LTI is in use the legal basis for identifying learners has to be settled first. |

## Required environment variables

These workflows read configuration from the following n8n env vars (set by
`docker/docker-compose.yml`):

- `WEAVIATE_API_KEY`              — auth for Weaviate writes/queries
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_FAST` — for metadata extraction
- `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL` — for chat-history embedding

## Required n8n credentials

After import, attach matching credentials in the n8n UI to these nodes:

| Workflow | Node | Credential type | Suggested name |
|----------|------|-----------------|----------------|
| `chathistory-sync` | Fetch new messages           | Postgres                | `smartrag-postgres` |
| `usermemory-summary` | LLM: summarise session     | Anthropic API           | `smartrag-anthropic` |
| `langfuse-userid-patch` | Langfuse: fetch/patch   | HTTP Basic Auth (LF keys) | `smartrag-langfuse` |
| `langfuse-userid-patch` | Flowise DB: lookup      | Postgres                | `smartrag-postgres` |

`bootstrap.sh` creates these credentials automatically.

## LLM provider note

`chathistory-sync` uses an **OpenAI-compatible HTTP request** for metadata
extraction (it sets `response_format: { type: "json_object" }`). It works
out-of-the-box with OpenAI, OpenRouter, vLLM, LM Studio, and most local
servers.

If you set `LLM_PROVIDER=anthropic`, the metadata-extractor node needs to be
swapped for the Anthropic LangChain node (see `usermemory-summary` for the
pattern). The setup wizard can do this for you.
