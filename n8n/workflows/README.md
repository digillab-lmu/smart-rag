# n8n workflows: core

These workflows hold the runtime memory of the system and report when part of
it stops working. They are imported and activated by
`scripts/deploy-n8n-workflows.sh`, which bootstrap runs as phase 10 and which
can be re-run at any time.

Until 2026-08-11 this file stated that bootstrap imported them automatically
through the n8n REST API. Nothing did: the deployer read `workflows-ingest/`
only. The workflows were documented, present and inactive, which is a worse
state than being absent, because documentation of something that does not run
is relied upon.

| File | Trigger | Purpose |
|------|---------|---------|
| `chathistory-sync.json`     | Schedule (every 5 min) | Polls Postgres for new Flowise messages, generates embeddings + metadata, writes to Weaviate `ChatHistory` for cross-agent semantic recall. **Handles personal data:** it copies what learners wrote, keyed to their pseudonymous id. Erasing one person, or one course's retention period running out, has to reach these records — see the Content Admin's *People* page. |
| `usermemory-summary.json`   | Schedule              | Periodically condenses each user's recent sessions into the Weaviate `UserMemory` record. **Handles personal data**, for the same reason and with the same consequence as the row above. Calls `$env.LLM_BASE_URL` with `$env.LLM_MODEL_FAST`, like every other LLM call here — it used to be an Anthropic node with a hard-coded model, which could not run on an installation configured for any other provider. |
| `graph-build.json`          | Webhook `graph-build` | Builds the course's concept map. Started from the Content Admin's graph page, reads the documents of the agents ticked on the agent list, extracts concepts per slice of one document with `$env.LLM_MODEL_STRONG`, merges them into one vocabulary and asks once for the prerequisites over that list — the corpus never has to fit in a prompt, and the global step costs the same for forty documents as for four. Reports back to `/api/graph-build`. **Writes nothing to Neo4j:** the proposal lands in the review box and reaches the graph only when a person submits it. |
| `error-handler.json`        | Error Trigger         | Sends mail when another workflow fails, with the failure described and repeated alerts throttled. |
| `watchdog.json`             | Schedule (hourly)     | Reports a workflow that has stopped running, and one that runs without producing anything: it reads the last success per workflow and the count of settled messages from Postgres. Repeated reports are throttled. |

## Required environment variables

These workflows read configuration from the following n8n env vars (set by
`docker/docker-compose.yml`):

- `WEAVIATE_API_KEY`              — auth for Weaviate writes/queries
- `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_FAST` — for metadata extraction
- `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL` — for chat-history embedding

## n8n credentials

`deploy-n8n-workflows.sh` imports the credentials these workflows use, so
nothing has to be attached by hand after a standard install. The Postgres
credential (`smartrag-postgres`) is the one used here, by
`chathistory-sync`'s *Fetch new messages* and by `watchdog`'s two Postgres
nodes. The LLM calls are plain HTTP requests and carry their key from the
environment rather than from a credential.

## LLM provider

`chathistory-sync` extracts metadata through an OpenAI-compatible HTTP request
against `${LLM_BASE_URL}/chat/completions`, setting
`response_format: { type: "json_object" }`. That works with OpenAI,
OpenRouter, vLLM, LM Studio and most local servers.

Anthropic does not serve `/chat/completions`, so on an installation configured
for it this node needs `LLM_BASE_URL` pointed at an OpenAI-compatible gateway,
or the node replaced. Nothing in the installer does this automatically.
`graph-build.json` shows the alternative: its Code node dispatches on
`$env.LLM_PROVIDER` and speaks each vendor's own API.

## Why the concept map runs as a workflow

A course of ten agents is dozens of documents. Each slice is a model call and
a whole run takes minutes to hours, while the Content Admin runs one
synchronous worker behind a 120-second gunicorn timeout. It can therefore
start the build and receive the result, but the work cannot happen there.

Two properties matter before this file is edited. `Extract concepts` handles
one slice per execution rather than looping over all of them inside a single
Code node, because `N8N_RUNNERS_TASK_TIMEOUT` is 1800 seconds and a node
reading forty documents would be terminated part-way with nothing to show.
The workflow's `executionTimeout` is set to three hours, above n8n's default,
which would otherwise end a large run after the API calls had been paid
for.

The logic in the Code nodes is exercised without n8n by
`tests/test_graph_workflow.sh`, which lifts them out of this JSON and runs
them against a stubbed Weaviate and a stubbed model. That covers the slicing,
merging, citation resolution and cycle breaking. It does not cover the wiring
between nodes or n8n's runtime — only running it does.
