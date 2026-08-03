# Ingest Workflows

Document ingest pipeline for a course's RAG documents: upload → Docling
conversion (+ AI image/diagram description) → MinIO archival → chunking →
embedding → Weaviate write.

| File | Purpose |
|------|---------|
| `ingest-document.json`          | Entry point. Webhook-triggered (`POST /webhook/document-ingest`), converts an uploaded file via Docling, describes any embedded images/diagrams using the course's configured `LLM_PROVIDER`, archives the result to MinIO, then calls the chunk+embed sub-workflow. |
| `ingest-chunk-and-embed.json`   | Sub-workflow (`Execute Workflow Trigger`, not directly reachable via HTTP). Chunks the converted markdown, embeds each chunk via the course's configured `EMBEDDING_PROVIDER`, writes to Weaviate. |
| `ingest-audio-transcription-(whisperx).json` | Audio/video transcription via WhisperX. Not yet generalized — still references a specific VHB deployment (GPU-bound WhisperX instance). Out of scope for the document-ingest generalization; do not import into a fresh deployment. |

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
and fixes PDF-conversion artifacts before chunking), `smartrag-minio`,
`smartrag-weaviate`, and whichever `LLM_PROVIDER`/`EMBEDDING_PROVIDER` is
configured in `.env` (no self-hosted LLM/embedding service required — see
the batch's commit history for why the earlier VHB-derived versions of
these workflows depended on an external, GPU-bound Ollama instance and
why that dependency was removed).

## Import

Not yet automated (see Phase 10 / `scripts/deploy-n8n-workflows.sh` in the
project's batch plan) — import both JSON files manually via n8n's UI for
now (**Workflows → Import from File**), and create an S3-type credential
in n8n pointing at `smartrag-minio` (host `smartrag-minio:9000`, using
`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` from `.env`) named to match what
`ingest-document.json`'s "Upload to MinIO" node references.
