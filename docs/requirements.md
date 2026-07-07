# System Requirements

## Prerequisites

Before running `scripts/bootstrap.sh`, make sure you have:

**Server**
- Ubuntu 24.04 LTS (hard requirement — the bootstrap wizard refuses to run
  on any other OS or version)
- Root or sudo access
- Docker Engine + Docker Compose v2 plugin **already installed**. The
  bootstrap does not install Docker itself — see
  [docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- At least 20 GB free disk space (40+ GB recommended once you start
  ingesting documents)

**Network**
- A registered domain (any public TLD) with DNS control — you need to be
  able to create A/AAAA records. One wildcard record (`*.yourdomain.example`)
  or individual records per subdomain both work.
- Ports 80 and 443 reachable from the public internet (required for the
  Let's Encrypt HTTP-01 challenge via certbot). No CDN/proxy (e.g.
  Cloudflare orange-cloud mode) in front of the server — it blocks the
  challenge. Plain DNS pointing straight at the server's IP is what works.
- Outbound HTTPS to your chosen LLM/embedding/reranker provider(s).

**API keys**
- An LLM provider API key — Anthropic, OpenAI, Google, Mistral, Cohere,
  OpenRouter, or a reachable OpenAI-compatible endpoint (self-hosted vLLM,
  LM Studio, etc.)
- An embedding provider API key — OpenAI, Cohere, Google, Mistral, or a
  reachable OpenAI-compatible embeddings endpoint. Can reuse the LLM key if
  it's the same provider.

Everything above is checked by the wizard's prerequisites checklist and
pre-flight phase, but DNS control and having a funded/valid API key can't be
verified automatically — confirm these yourself before starting.

## Strongly recommended: a mail relay

Flowise, n8n, and Langfuse all send password-reset and invite emails through
one shared SMTP relay, configured in the wizard's "Mail relay" section. Without
it, a locked-out user (including you, the admin) has no self-service way back
into their account — you'd have to reset credentials manually via the
database. It's not a hard requirement to *start* the wizard, but skipping it
creates an operational gap you'll likely regret later.

Two ways to set it up, both asked for in the wizard:

1. **Local Postfix relay (recommended)** — `scripts/install-postfix.sh`
   installs Postfix as a "satellite" relay that forwards outbound mail
   through your real provider's credentials. Flowise/n8n/Langfuse then talk
   to Postfix *unauthenticated* over the internal Docker network — none of
   them ever sees the actual relay password, only Postfix does. Postfix
   listens on all interfaces (standard for this setup) but only relays mail
   from localhost and the SMART RAG Docker subnet (`172.28.92.0/24`,
   `mynetworks` restriction) — connections from anywhere else get refused
   outright, never actually relayed.
2. **Direct external relay** — point the apps straight at any existing SMTP
   relay (host/port/user/password) you already run or have access to.

Either way, you need: a **smarthost** (an SMTP server willing to relay your
mail — your institution's mail server, or a transactional provider like
SendGrid/Mailgun/Postmark) and, usually, **credentials** for it. Check with
your provider whether it's reachable on port 587 (STARTTLS, most common),
465 (implicit TLS), or 25 — some cloud/hosting providers block outbound
port 25 by default, but 587/465 are almost always open.

## Optional, depending on what you enable

- **Reranker (Cohere)** — improves retrieval quality. Default is `none`
  (safe to skip); provide a Cohere API key if you want it.
- **LTI 1.3 profile** — requires your LMS's base URL (Moodle / ILIAS /
  Canvas) for CORS. Full LMS-side registration (client ID, key exchange)
  happens after deployment, not during bootstrap.

## ⚠️ Embedding model — choose once

The embedding model defines your vector space. Once you've ingested
documents, changing `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` requires
re-ingesting everything from scratch — the vector dimensions must match
exactly between ingestion and retrieval. Pick your model before the first
ingestion run.

## Data Privacy

The level of data privacy compliance depends entirely on your choice of
LLM/embedding/reranker providers:
- API-based providers (Anthropic, OpenAI, etc.): subject to that provider's
  terms and data-processing location
- A self-hosted OpenAI-compatible endpoint (`*_PROVIDER=custom`): no data
  leaves your own infrastructure
- Mixed setups: assess each component individually

Users are responsible for ensuring compliance with applicable data
protection requirements (e.g. GDPR) for their context and institution.

## Minimum Hardware

Requirements scale with which Compose profiles you enable
(`COMPOSE_PROFILES` in `.env`):

| Profile combination | CPU | RAM | Disk |
|---|---|---|---|
| `core` only | 2 cores | 8 GB | 20 GB |
| `core,observability` (recommended) | 4 cores | 12 GB | 20 GB |
| `core,observability,lti` | 4 cores | 12 GB | 20 GB |

These are baseline figures for a small-to-medium course. Scale up disk
space with the volume of ingested course material, and RAM if you expect
many concurrent users.
