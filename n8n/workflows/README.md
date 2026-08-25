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
| `chathistory-sync.json`     | Schedule (every 5 min) | Polls Postgres for new Flowise messages, generates embeddings + metadata, writes to Weaviate `ChatHistory` for cross-agent semantic recall. **Handles personal data:** it copies what learners wrote, keyed to their pseudonymous id. Erasing one person, or one course's retention period running out, has to reach these records — see the Content Admin's *People* page. |
| `usermemory-summary.json`   | Schedule              | Periodically condenses each user's recent sessions into the Weaviate `UserMemory` record. **Handles personal data**, for the same reason and with the same consequence as the row above. Calls `$env.LLM_BASE_URL` with `$env.LLM_MODEL_FAST`, like every other LLM call here — it used to be an Anthropic node with a hard-coded model, which could not run on an installation configured for any other provider. |
| `graph-build.json`          | Webhook `graph-build` | Builds the course's concept map. Started from the Content Admin's graph page, reads the documents of the agents ticked on the agent list, extracts concepts per slice of one document with `$env.LLM_MODEL_STRONG`, merges them into one vocabulary and asks once for the prerequisites over that list — the corpus never has to fit in a prompt, and the global step costs the same for forty documents as for four. Reports back to `/api/graph-build`. **Writes nothing to Neo4j:** the proposal lands in the review box and reaches the graph only when a person submits it. |

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

`bootstrap.sh` creates these credentials automatically.

## LLM provider note

`chathistory-sync` uses an **OpenAI-compatible HTTP request** for metadata
extraction (it sets `response_format: { type: "json_object" }`). It works
out-of-the-box with OpenAI, OpenRouter, vLLM, LM Studio, and most local
servers.

If you set `LLM_PROVIDER=anthropic`, the metadata-extractor node needs to be
swapped for the Anthropic LangChain node (see `usermemory-summary` for the
pattern). The setup wizard can do this for you.

## Why the concept map is a workflow at all

Because it is long. A course of ten agents is dozens of documents; each slice
is a model call, and the whole run is minutes to hours. The Content Admin runs
one synchronous worker behind a 120-second gunicorn timeout, so it can start
the build and receive the result, but it cannot be where the work happens.

Two consequences worth knowing before editing this file. `Extract concepts`
deliberately handles **one slice per execution** rather than looping over all
of them inside a single Code node: `N8N_RUNNERS_TASK_TIMEOUT` is 1800 seconds,
and a node that read forty documents would be killed halfway with nothing to
show. And the workflow's `executionTimeout` is set to three hours, well above
n8n's default, because the default would cut a large run off with the money
already spent.

The logic in the Code nodes is exercised without n8n by
`tests/test_graph_workflow.sh`, which lifts them out of this JSON and runs
them against a stubbed Weaviate and a stubbed model. That covers the slicing,
merging, citation resolution and cycle breaking. It does not cover the wiring
between nodes or n8n's runtime — only running it does.
