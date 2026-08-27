# System requirements

## Prerequisites

Required before running `scripts/bootstrap.sh`:

**Server**
- Ubuntu 24.04 or 26.04 LTS, the two releases this project has been installed
  and run on. Another Ubuntu LTS (22.04, 28.04, …) is accepted after the
  wizard states that it is untested and the operator confirms; interim
  releases and non-Ubuntu systems are refused.
- Root or sudo access.
- Docker Engine and the Docker Compose v2 plugin **already installed**. The
  bootstrap does not install Docker itself, see
  [docs.docker.com/engine/install/ubuntu](https://docs.docker.com/engine/install/ubuntu/).
- At least 20 GB free disk space; 40 GB or more once documents are being
  ingested.

**Network.** This section depends on the deployment mode, which is the
wizard's first question.

*Domain mode* (production, and the only mode that supports LTI):
- A registered domain with DNS control, meaning the ability to create A/AAAA
  records. One wildcard record (`*.yourdomain.example`) or individual records
  per subdomain both work.
- Ports 80 and 443 reachable from the public internet, required for the
  Let's Encrypt HTTP-01 challenge via certbot. A CDN or proxy in front of the
  server (for example Cloudflare in orange-cloud mode) blocks that challenge.
  Plain DNS pointing at the server's IP is what works.

*Tailscale mode* (test and evaluation systems) needs no domain, no DNS
records, no inbound ports and no port forwarding. Instead:
- A [Tailscale](https://login.tailscale.com/start) account with MagicDNS and
  HTTPS enabled for the tailnet (admin console → DNS). The wizard covers both
  if they are off.
- Tailscale installed on the administering computer, signed into the **same**
  account. Only the chat is public, via Funnel; every admin interface answers
  inside the tailnet and nowhere else.
- LTI does not work in this mode. An LMS integration needs stable,
  institutionally approved URLs rather than a `*.ts.net` name.

Both modes need outbound HTTPS to the chosen LLM, embedding and reranker
providers.

**API keys**
- An LLM provider key: Anthropic, OpenAI, Google, Mistral, Cohere,
  OpenRouter, or a reachable OpenAI-compatible endpoint such as a self-hosted
  vLLM or LM Studio.
- An embedding provider key: OpenAI, Cohere, Google, Mistral, or a reachable
  OpenAI-compatible embeddings endpoint. The LLM key can be reused if the
  provider is the same.

The wizard's prerequisites checklist and pre-flight phase check what can be
checked from the server. DNS control, tailnet membership of the administering
machine, and whether an API key is valid and funded cannot be verified from
there and have to be confirmed beforehand.

## Mail relay

Flowise, n8n and Langfuse send password-reset and invite mail through one
shared SMTP relay, configured in the wizard's mail section. Without it, a
locked-out account has no self-service way back in and credentials have to be
reset against the database by hand. The wizard runs without a relay, but the
gap becomes apparent at the first forgotten password.

Two ways to set it up, both offered by the wizard:

1. **Local Postfix relay.** `scripts/install-postfix.sh` installs Postfix as a
   satellite relay that forwards outbound mail through the real provider's
   credentials. Flowise, n8n and Langfuse then talk to Postfix unauthenticated
   over the internal Docker network, so none of them holds the relay password;
   only Postfix does. Postfix listens on all interfaces, which is standard for
   this setup, but relays only from localhost and the SMART RAG Docker subnet
   (`172.28.92.0/24`, `mynetworks` restriction). Connections from anywhere
   else are refused rather than relayed.
2. **Direct external relay.** The applications point at an existing SMTP relay
   (host, port, user, password).

Either way, two things are needed: a smarthost willing to relay the mail — an
institutional mail server, or a transactional provider such as SendGrid,
Mailgun or Postmark — and, usually, credentials for it. Port 587 (STARTTLS)
is the most common, 465 (implicit TLS) and 25 also occur. Some cloud and
hosting providers block outbound port 25 by default, while 587 and 465 are
almost always open.

## Optional, depending on what is enabled

- **Reranker (Cohere)** improves retrieval quality. The wizard's default is
  `none`; enabling it needs a Cohere API key.
- **LTI 1.3 profile** needs the LMS's base URL (Moodle, ILIAS, Canvas) for
  CORS. The LMS-side registration — client ID, key exchange — happens after
  deployment, not during bootstrap.

## The embedding model is chosen once

The embedding model defines the vector space. Once documents have been
ingested, changing `EMBEDDING_PROVIDER` or `EMBEDDING_MODEL` requires
re-ingesting all of them, because the vector dimensions must match between
ingestion and retrieval. The model has to be chosen before the first ingest.

## Data protection

The level of compliance depends on the choice of LLM, embedding and reranker
providers:

- API-based providers (Anthropic, OpenAI and others) are subject to that
  provider's terms and data-processing location.
- A self-hosted OpenAI-compatible endpoint (`*_PROVIDER=custom`) keeps data
  on the operator's own infrastructure.
- Mixed setups have to be assessed per component.

Compliance with the applicable data protection requirements, for example the
GDPR, is the responsibility of the operating institution.

## Minimum hardware

Requirements scale with the enabled Compose profiles (`COMPOSE_PROFILES` in
`.env`):

| Profile combination | CPU | RAM | Disk |
|---|---|---|---|
| `core` only | 2 cores (4 recommended) | 8 GB | 20 GB |
| `core,observability` (recommended) | 4 cores | 12 GB | 20 GB |
| `core,observability,lti` | 4 cores | 12 GB | 20 GB |

**Where the memory goes.** Measured with `docker stats` on a 4-core, 16 GB
test machine running two courses, once idle and once during a document
ingest:

| | idle | during an ingest |
|---|---|---|
| `core` (11 containers) | ~3.9 GB | ~5.3 GB |
| `observability` (3 more) | +2.5 GB | +2.5 GB |

Three services account for almost all of it:

* **Docling** holds 1.3 GB idle and 2.7 GB while converting a document. It is
  the largest single consumer and is idle most of the time.
* **Langfuse and its ClickHouse** hold 2.5 GB together, a third of an 8 GB
  machine. This is why `observability` is a separate profile. On 8 GB it
  belongs on a test machine rather than alongside a production ingest.
* **Neo4j** held 871 MB with its shipped settings, for a graph of a few
  hundred concepts. Its heap and page cache are now capped to match that size
  (256 MB initial heap, 512 MB maximum, 256 MB page cache), which brings it to
  roughly 600 MB. A graph intended to grow well beyond course concept maps
  needs both raised in `docker/docker-compose.yml`.

**On 8 GB.** `core` alone fits, including the ingest peak.
`core,observability` measured 7.8 GB at peak, which leaves too little for the
operating system, hence the 12 GB in the table. An 8 GB machine that needs
tracing can run it, but not while ingesting.

**Two things grow with use.** Weaviate held 133 MB for 113 chunks; its index
is proportional to the number of chunks and their embedding dimensions, so on
an installation with many courses it becomes the largest consumer. Disk grows
with ingested material, and 40 GB or more is realistic once several courses
have their documents.

**Ingest time depends on CPU.** Document conversion and embedding are
CPU-bound; two cores work and are slow. Nothing else in normal operation is
demanding, since answering a chat is mostly waiting for the LLM provider.
