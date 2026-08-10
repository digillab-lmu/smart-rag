# Ingest Workflows

Document ingest pipeline for a course's RAG documents: upload → Docling
conversion (+ AI image/diagram description) → object storage → chunking →
embedding → Weaviate write.

| File | Purpose |
|------|---------|
| `ingest-document.json`          | Entry point. Webhook-triggered (`POST /webhook/document-ingest`), converts an uploaded file via Docling, describes any embedded images/diagrams using the course's configured `LLM_PROVIDER`, archives the result to object storage, then calls the chunk+embed sub-workflow. |
| `ingest-chunk-and-embed.json`   | Sub-workflow (`Execute Workflow Trigger`, not directly reachable via HTTP). Chunks the converted markdown, embeds each chunk via the course's configured `EMBEDDING_PROVIDER`, writes to Weaviate. |

Audio/video transcription (WhisperX) used to live here as a third
workflow. It was removed rather than generalized: it hard-coded the
original deployment's hostnames, an internal IP and a personal email
address, and it depended on a GPU-bound WhisperX instance that no fresh
deployment has. Recovering it means `git log -- n8n/workflows-ingest/` —
but generalizing it properly would mean choosing a transcription service
first, which is a separate decision.

## What triggers `ingest-document.json`

The content-admin GUI's upload page (see `content-admin/n8n_client.py`) POSTs
a file plus form fields (`agent_id`, `title`, `authors`, `year`, `topic`,
`language`, `force_ocr`, optional `notify_email`) as `multipart/form-data`
to this workflow's webhook. The webhook responds immediately
(`responseMode: onReceived`) — conversion, image description, and embedding
happen asynchronously afterward and can take anywhere from under a minute
to tens of minutes for a large scanned document with many diagrams. The
uploader gets an email when it's done (or on failure), rather than waiting
on the HTTP request.

## Dependencies

Both workflows only depend on services already part of this repo's own
`docker-compose.yml` (profile `core`, always deployed): `smartrag-docling`
(self-hosted, CPU-only Docling), `smartrag-markdowncleaner` (self-hosted
wrapper around the `markdowncleaner` PyPI package, see `markdowncleaner/`
at the repo root — strips references/footnotes/copyright-notice sections
and fixes PDF-conversion artifacts before chunking), `smartrag-garage`,
`smartrag-weaviate`, and whichever `LLM_PROVIDER`/`EMBEDDING_PROVIDER` is
configured in `.env` (no self-hosted LLM/embedding service required — see
the batch's commit history for why the earlier VHB-derived versions of
these workflows depended on an external, GPU-bound Ollama instance and
why that dependency was removed).

## Import

Automated — `scripts/deploy-n8n-workflows.sh` (Phase 10) imports both
workflows *and* the S3/SMTP credentials they need, activates the
webhook workflow, and restarts n8n so the activation takes effect. It runs
as part of `bootstrap.sh`, and is also available from `scripts/admin.sh`
(→ *Ingest — (re-)import n8n credentials + workflows*) for re-running
after a `git pull` that changed these files.

**One-time precondition:** n8n needs an owner account before anything can
be imported (n8n's CLI has no user to assign objects to otherwise). That's
the "set up owner" screen on first login — a manual browser step, same as
Flowise's API key. On a first bootstrap run the script detects this,
skips itself with a message, and leaves the rest of the bootstrap intact;
just re-run it (or `bootstrap.sh --continue`) once the owner exists.

Credentials are staged as plain JSON and imported via
`n8n import:credentials`, which encrypts them with the instance's own
`N8N_ENCRYPTION_KEY` — deliberately not via n8n's public REST API, which
would need an API key that itself can only be created manually in the UI.
The staged plaintext files live under `$BASE_DATA_PATH/n8n/data/staging`
only for the duration of the import and are removed afterwards, including
when the script fails partway.
