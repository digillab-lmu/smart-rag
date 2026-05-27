# Ingest Workflows — staged for `smart-rag-ingest`

This directory contains the original document-ingestion workflows from the
production system. They are **not yet generalized** and will be migrated to a
**separate repository** (`smart-rag-ingest`) so this core repo stays focused
on the agent runtime + memory pipelines.

Until that split is done, these files live here as a reference snapshot.

| File | Purpose |
|------|---------|
| `ingest-audio-transcription-(whisperx).json`            | Audio/video transcription via WhisperX |
| `ingest-document-ingest-(docling2minio-+-vl).json`      | PDF/docx → MinIO → chunks via Docling + VL |
| `ingest-minio-→-weaviate-ingest.json`                    | MinIO triggers → embedding → Weaviate write |

## Why a separate repo?

The ingest pipeline depends on heavy services (WhisperX with GPU, Docling, VL
model, Tesseract, language-specific spaCy models) that most deployments do
not need. Splitting them out keeps:

- **Core repo lightweight** — runtime services only, fast clone & boot.
- **Ingest repo opinionated** — can ship its own Docker Compose with GPU
  profile, language packs, and offline document tooling.

## Do not import these into a fresh deployment

They still reference VHB hostnames, hardcoded LMU credentials, and the
DGX-Spark Ollama endpoint. They will be cleaned up at migration time.
