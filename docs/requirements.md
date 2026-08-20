# System Requirements

## Prerequisites

Before running `scripts/bootstrap.sh`, make sure you have:

**Server**
- Ubuntu 24.04 or 26.04 LTS — the two releases this project has actually been
  installed and run on, rather than the ones it is expected to work on.
  Another Ubuntu LTS (22.04, 28.04, …) is accepted after the wizard says it is
  untested and you confirm; interim releases and non-Ubuntu systems are
  refused outright
- Root or sudo access
- Docker Engine + Docker Compose v2 plugin **already installed**. The
  bootstrap does not install Docker itself — see
  [docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- At least 20 GB free disk space (40+ GB recommended once you start
  ingesting documents)

**Network** — this section depends on which deployment mode you pick; the
wizard asks that first.

*Domain mode* (production, and the only mode that supports LTI):
- A registered domain (any public TLD) with DNS control — you need to be
  able to create A/AAAA records. One wildcard record (`*.yourdomain.example`)
  or individual records per subdomain both work.
- Ports 80 and 443 reachable from the public internet (required for the
  Let's Encrypt HTTP-01 challenge via certbot). No CDN/proxy (e.g.
  Cloudflare orange-cloud mode) in front of the server — it blocks the
  challenge. Plain DNS pointing straight at the server's IP is what works.

*Tailscale mode* (test and evaluation systems) needs none of the above — no
domain, no DNS records, no inbound ports, no port forwarding. Instead:
- A [Tailscale](https://login.tailscale.com/start) account, with MagicDNS and
  HTTPS enabled for the tailnet (admin console → DNS). The wizard walks you
  through both if they are off.
- Tailscale installed on the computer you administer from, signed into the
  **same** account. Only the chat is public (via Funnel); every admin
  interface answers inside the tailnet and nowhere else.
- LTI does not work in this mode — an LMS integration needs stable,
  institutionally approved URLs, not a `*.ts.net` name.

Both modes:
- Outbound HTTPS to your chosen LLM/embedding/reranker provider(s).

**API keys**
- An LLM provider API key — Anthropic, OpenAI, Google, Mistral, Cohere,
  OpenRouter, or a reachable OpenAI-compatible endpoint (self-hosted vLLM,
  LM Studio, etc.)
- An embedding provider API key — OpenAI, Cohere, Google, Mistral, or a
  reachable OpenAI-compatible embeddings endpoint. Can reuse the LLM key if
  it's the same provider.

Everything above is checked by the wizard's prerequisites checklist and
pre-flight phase, but DNS control, tailnet membership on your own machine,
and having a funded/valid API key can't be verified from the server —
confirm these yourself before starting.

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
| `core` only | 2 cores (4 recommended) | 8 GB | 20 GB |
| `core,observability` (recommended) | 4 cores | 12 GB | 20 GB |
| `core,observability,lti` | 4 cores | 12 GB | 20 GB |

**Where the memory actually goes.** These are not estimates. Measured with
`docker stats` on a 4-core/16 GB test machine running two courses, once idle
and once during a document ingest:

| | idle | during an ingest |
|---|---|---|
| `core` (11 containers) | ~3.9 GB | ~5.3 GB |
| `observability` (3 more) | +2.5 GB | +2.5 GB |

Three services account for almost all of it, and it is worth knowing which:

* **Docling** — 1.3 GB idle, **2.7 GB while converting a document**. It is the
  single largest consumer and it is idle most of the time.
* **Langfuse and its ClickHouse** — 2.5 GB together, a third of an 8 GB
  machine. This is why `observability` is a separate profile: tracing is
  genuinely useful and genuinely expensive. On 8 GB, run it on a test machine
  rather than alongside a production ingest.
* **Neo4j** — held 871 MB with its shipped settings, for a graph of a few
  hundred concepts. Both its heap and its page cache are now capped at a size
  matched to that (256 MB initial heap, 512 MB maximum, 256 MB page cache),
  which brings it to roughly 600 MB. If you intend to grow the graph well
  beyond course concept maps, raise both in `docker/docker-compose.yml`.

**What this means for 8 GB.** `core` alone fits, with room for the ingest
peak. `core,observability` measured 7.8 GB at peak, which does not leave
enough for the operating system — hence 12 GB in the table. An 8 GB machine
that must have tracing can run it, but not while ingesting.

**Two things that grow.** Weaviate held 133 MB for 113 chunks; its index is
proportional to the number of chunks and their embedding dimensions, so plan
for it to be the largest consumer on an installation with many courses. Disk
grows with ingested material, and 40+ GB is realistic once several courses
have their documents.

**CPU is what ingest time depends on.** Document conversion and embedding are
CPU-bound; two cores work and are slow. Nothing else in normal operation is
demanding — answering a chat is mostly waiting for the LLM provider.
