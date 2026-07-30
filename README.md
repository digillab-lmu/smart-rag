# 🚧 UNDER CONSTRUCTION - USE AT YOUR OWN RISK 🚧

# SMART RAG

**Shared Memory Agent-Based Retrieval for Teaching**

An open-source, course-agnostic deployment of a multi-agent AI tutoring system.
Built for university and professional-education contexts where a single subject
benefits from several specialized AI agents — each covering one topic — with
persistent per-student memory, hybrid retrieval, and optional LMS integration.

> Developed by **Benjamin Götzinger** at [DigiLLab, LMU München](https://edpsych.psy.lmu.de/) —
> Chair of Empirical Educational Research and Educational Psychology
> (Prof. Frank Fischer) — and generalized for community use.

---

## What it gives you

- **Up to 14 specialized AI agents** (Flowise AgentFlows) with a unified
  memory model — students keep their progress across agents and sessions.
- **Hybrid retrieval** (Weaviate) with optional re-ranking (Cohere or custom).
- **Concept prerequisite graph** (Neo4j) for adaptive scaffolding.
- **Persistent user memory** (`UserMemory` + `ChatHistory` in Weaviate) refreshed
  by scheduled n8n pipelines.
- **LLM observability** (Langfuse + ClickHouse, optional).
- **LMS integration** via LTI 1.3 (Moodle, ILIAS, Canvas — optional).
- **One-command deployment** to Ubuntu 24.04 LTS via interactive wizard.

---

## Architecture

```
LMS (LTI 1.3, optional)
    │
    ▼
LTI Middleware (Flask)
    │  session token: user_id|name|agent_id|timestamp
    ▼
Flowise — 14 AgentFlows (1 per topic)
    ├──► Weaviate     — RAG over course materials
    ├──► Neo4j        — concept prerequisites
    ├──► Redis        — chat queue + LTI sessions
    └──► LLM API      — Anthropic | OpenAI | Google | Mistral | Cohere |
                        OpenRouter | any OpenAI-compatible endpoint

n8n — background pipelines
    ├──► ChatHistory sync     (Postgres → Weaviate, every 5 min)
    ├──► UserMemory summary   (Weaviate + LLM)
    └──► Langfuse userId patch (Postgres → Langfuse)

PostgreSQL — Flowise + n8n + Langfuse state
MinIO      — document store + Langfuse blob backend
```

Document **ingestion** (PDF/audio/video → chunks → Weaviate) lives in a
separate repository: [`smart-rag-ingest`](https://github.com/) (planned).

---

## Quick start

**Requirements**: Ubuntu 24.04 LTS, Docker, Docker Compose v2, a public domain
with DNS pointing to your server, ports 80/443 reachable. Full checklist
(including API keys and hardware sizing): [`docs/requirements.md`](docs/requirements.md).
The wizard also shows this checklist interactively before it asks you anything.

```bash
# On the server — clone into /srv/smart-rag (recommended)
sudo mkdir -p /srv && sudo chown $USER /srv
git clone https://github.com/digillab-lmu/smart-rag.git /srv/smart-rag
cd /srv/smart-rag

# Phase 1 — interactive wizard (≈ 5 min)
sudo bash scripts/bootstrap.sh
#   → answers: domain, LLM provider, embedding model, etc.
#   → generates: .env, credentials.txt, nginx config, Weaviate schema

# Set DNS A-record(s) for *.your-domain.example
#   (one wildcard *.your-domain.example, OR individual records per subdomain)

# Phase 2 — deploy (≈ 10 min)
sudo bash scripts/bootstrap.sh --continue
#   → installs nginx + certbot (if missing)
#   → obtains Let's Encrypt SAN certificate
#   → starts Docker stack and waits for health
#   → deploys the Weaviate + Neo4j schema, generates LTI keys (if enabled)
```

When `--continue` finishes, your stack is running at:
- `https://smart-rag.your-domain.example` — Flowise (chat interface)
- `https://n8n.your-domain.example` — n8n (automation)
- `https://minio.your-domain.example` — MinIO console
- `https://langfuse.your-domain.example` — Langfuse (if observability profile)
- `https://lti.your-domain.example` — LTI middleware (if lti profile)

Initial admin credentials are in `credentials.txt` (chmod 600).

**Day-to-day admin** — `sudo bash scripts/admin.sh` (or, once installed,
just `sudo smartrag`) opens a raspi-config-style menu for the operations
you'll actually use after deployment: service status, tailing logs,
pulling updates, restarting a service, SSL certificate status/renewal, a
mail-relay test, a DNS check, and a read-only secrets overview. It offers
to install itself as the global `smartrag` command the first time you run
it. Runs entirely on the host as root over SSH — no extra container, no
new network exposure. (Content authoring — agent prompts, RAG documents,
the knowledge graph — is intentionally not here; see "What's NOT done"
below.)

**Day-2 Docker operations** (pulling updated images, viewing logs, restarting):
use `scripts/compose.sh` instead of calling `docker compose` directly — it's
a thin wrapper that always points at the right compose file and `.env`, no
matter which directory you run it from (plain `docker compose` silently
breaks if run from `docker/` without `--env-file`, see the header comment in
[`docker/docker-compose.yml`](docker/docker-compose.yml) for why):

```bash
bash scripts/compose.sh pull && bash scripts/compose.sh up -d   # apply new image versions
bash scripts/compose.sh logs -f smartrag-n8n                    # tail one service's logs
bash scripts/compose.sh ps                                      # status
```

**Starting over / uninstalling**: `sudo bash scripts/uninstall.sh` removes SMART
RAG's own footprint (containers, network, nginx configs) and leaves nginx,
certbot, Docker, and Postfix themselves untouched (they may be shared with
other services on this host). Data, secrets, and the SSL certificate are kept
by default — add `--purge-data`, `--purge-secrets`, `--purge-certs` to also
remove those (data deletion needs typing `DELETE` to confirm). Try `--dry-run`
first to see exactly what it would do.

---

## Wizard features

- **Bilingual** — English or German (auto-detected from `$LANG`, override
  with `--lang en|de`).
- **Prerequisites checklist** — shown before any question is asked, so you
  find out you're missing an API key or DNS control up front, not halfway
  through. Declining exits cleanly with a pointer to
  [`docs/requirements.md`](docs/requirements.md) instead of leaving a
  half-configured `.env`.
- **Back-navigation** — type `back` (or `zurück`) at any prompt to return to
  the previous section and fix an earlier answer. Everything you already
  entered is kept as the new default.
- **Curated model shortlists with live validation** — after picking an LLM
  provider, choose from a short list of current models, or type your own —
  custom entries are checked against the provider's live `/models` API
  (needs the API key, which is why it's now asked before model selection)
  so a typo like `GPT-5.2` gets caught immediately instead of failing later
  inside Flowise.
- **Domain auto-detection with round-trip check** — pre-fills the
  base-domain prompt from the server's reverse DNS, but only if that
  domain's own DNS record actually points back to this server. Cloud
  providers (IONOS, AWS, ...) set generic PTR records unrelated to your
  actual domain — a naive reverse-DNS guess would suggest those; the
  round-trip check filters them out. Always shown as an editable default,
  never applied silently.
- **Mail relay setup with existing-relay detection** — Flowise/n8n/Langfuse
  all need SMTP for password-reset and invite emails. Before offering to
  set anything up, the wizard checks whether Postfix/Exim/Sendmail/msmtp is
  already installed or something's already listening on port 25, and lets
  you keep it, reconfigure, or skip. Otherwise it can install and configure
  a local Postfix relay for you (`scripts/install-postfix.sh`) — apps talk
  to it unauthenticated over the internal Docker network, so your real mail
  provider's password only ever lives in Postfix's config — followed by an
  actual test email to confirm the relay works end to end, not just that it
  installed.
- **Coexistence-safe** — designed to deploy on a server that already runs
  other web services. See [`docs/COEXISTENCE.md`](docs/COEXISTENCE.md) for
  the explicit contract of what we touch and (mostly) don't touch.
- **Auto-resolves port *and* subdomain conflicts** — if port 9000 or a
  subdomain like `n8n.yourdomain.example` is already taken on the host
  (e.g. a standalone n8n running separately), the wizard proposes a free
  port alternative or a shared subdomain prefix (`smartrag-n8n.…`) and
  writes the resolution into `.env`. No manual intervention needed.
- **Pre-flight checks** — Ubuntu version, Docker, disk space, DNS resolution,
  nginx server-name collisions, existing certificates, base data path.
- **Safety snapshot** — before any destructive action, the script captures
  `/etc/nginx`, current Docker state, and listening ports to
  `/var/backups/smartrag-pre-bootstrap-<timestamp>/` for easy rollback.

---

## What's NOT done by the bootstrap (yet)

The bootstrap deploys the *infrastructure* end to end, including the
Weaviate/Neo4j schema and LTI keys (phases 8 and 11). What's still missing is
*content*: after `--continue` succeeds, Flowise and n8n are running but
empty — no agents or workflows imported yet. The next release will add:

- **Phase 9** — set up Flowise credentials/variables and import the 6 agent
  templates from `flowise/agents/`. Non-trivial: the templates contain ~25
  Mustache placeholders (`{{CONCEPT_LIST}}`, `{{PERSONA_NAME}}`,
  `{{EXPERT_DOMAIN}}`, …) that are genuine per-course teaching content, not
  config values — this needs an actual content-authoring UI, not another
  wizard prompt.
- **Phase 10** — import the 3 n8n core workflows and create credentials.

For now you can do these steps manually through the Flowise / n8n UIs using
the JSON templates in `flowise/agents/` and `n8n/workflows/`.

Planned alongside phases 9/10: a **web-based content admin** (course
content, agent system prompts, RAG documents, the Neo4j knowledge graph) —
deliberately a *separate* piece from `scripts/admin.sh`. Infrastructure/root
operations stay in the TUI (SSH-only, no network exposure); content editing
only ever needs to talk to Weaviate/Neo4j/Flowise over the internal Docker
network, so a compromised content GUI can't escalate to host control.

---

## Configuration

Everything lives in `.env`. The wizard writes a complete one, but here are the
key knobs:

| Variable | Purpose |
|----------|---------|
| `COMPOSE_PROFILES` | `core` always; add `observability` for Langfuse, `lti` for LTI |
| `LLM_PROVIDER` / `LLM_API_KEY` | LLM provider + key (Anthropic, OpenAI, …) |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | Embedding model (**don't change after first ingest!**) |
| `WEAVIATE_COLLECTION_NAME` | Derived from `COURSE_ID` |
| `FLOWISE_PORT`, `N8N_PORT`, … | Host port bindings (override if conflicts) |

See [`.env.example`](.env.example) for the complete annotated list.

---

## Repository layout

```
smart-rag/
├── docker/                 # docker-compose.yml (profiles: core, observability, lti)
├── nginx/                  # smartrag-suite.conf template
├── weaviate/               # schema.json (with __COLLECTION_NAME__ placeholder)
├── neo4j/                  # schema.cypher (constraints) + seed.example.cypher
├── flowise/agents/         # 6 generic agent JSON templates
├── n8n/
│   ├── workflows/          # 3 core workflows (sync, summary, observability)
│   └── workflows-ingest/   # Document ingest workflows (→ moving to smart-rag-ingest)
├── lti-middleware/         # Flask app for LTI 1.3
├── scripts/                # bootstrap.sh, admin.sh, uninstall.sh, compose.sh, lib/, standalone phase scripts
└── docs/                   # COEXISTENCE.md, requirements.md
```

---

## License

This entire repository (code and documentation) is licensed under the
**[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)**.

- **Free for any noncommercial use** — personal projects, research, and
  use by charitable organizations, educational institutions, public
  research organizations, and government institutions is explicitly
  permitted regardless of funding source (this covers university/school
  deployments like this project's own origin at LMU München).
- **Commercial use requires a separate license from the licensor.**
  Reach out if you want to use SMART RAG commercially.

See [`LICENSE`](LICENSE) for the full legal text.

---

## Contributing

This is an early public release. Issues, pull requests, and discussion welcome.
The most useful contributions right now:

- **Real deployment testing** — run the bootstrap on a fresh server and report
  what does or doesn't work.
- **Documentation gaps** — anything unclear in this README or `docs/`.
- **Translations** — German + English are first-class; other languages welcome
  via `scripts/lib/messages.sh`.

---

## Acknowledgements

- Built on top of the excellent open-source work of the
  [Flowise](https://flowiseai.com/), [n8n](https://n8n.io/),
  [Weaviate](https://weaviate.io/), [Neo4j](https://neo4j.com/),
  [Langfuse](https://langfuse.com/), and [MinIO](https://min.io/) teams.
- Developed by [Benjamin Götzinger](https://www.psy.lmu.de/edu/persons/ag-fischer/goetzinger_benjamin/index.html)
  at [DigiLLab, LMU München](https://edpsych.psy.lmu.de/) — Chair of
  Empirical Educational Research and Educational Psychology (Prof. Frank Fischer).
- Pedagogical concept developed in collaboration with the DigiLLab team.
