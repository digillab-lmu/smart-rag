# SMART RAG

**Shared Memory Agent-Based Retrieval for Teaching**

An open-source, course-agnostic deployment of a multi-agent AI tutoring system.
Built for university and professional-education contexts where a single subject
benefits from several specialized AI agents — each covering one topic — with
persistent per-student memory, hybrid retrieval, and optional LMS integration.

> Developed by **Benjamin Götzinger** at the [DigiLLab of LMU München](https://www.lmu.de/digillab/de/) —
> [Chair of Empirical Education and Educational Psychology](https://www.psy.lmu.de/ffp/)
> (Prof. Frank Fischer) — and generalized for community use.

---

## What it gives you

- **Up to 10 specialized AI agents** (Flowise AgentFlows) with a unified
  memory model — students keep their progress across agents and sessions.
- **Hybrid retrieval** (Weaviate) with optional re-ranking (Cohere or custom).
- **Concept prerequisite graph** (Neo4j) for adaptive scaffolding.
- **Persistent user memory** (`UserMemory` + `ChatHistory` in Weaviate) refreshed
  by scheduled n8n pipelines.
- **LLM observability** (Langfuse + ClickHouse, optional).
- **LMS integration** via LTI 1.3 (Moodle, ILIAS, Canvas — optional).
- **Course scoping** — one installation can host several courses; every
  retrieved object carries a `course_id` and every agent filters on it.
- **One-command deployment** to Ubuntu LTS via an interactive wizard —
  with a public domain, or with no domain at all via Tailscale.

---

## Architecture

```
LMS (LTI 1.3, optional)
    │
    ▼
LTI Middleware (Flask)
    │  session token: user_id|name|agent_id|timestamp
    ▼
Flowise — up to 10 AgentFlows (1 per topic)
    ├──► Weaviate     — RAG over course materials
    ├──► Neo4j        — concept prerequisites
    ├──► Redis        — chat queue + LTI sessions
    └──► LLM API      — Anthropic | OpenAI | Google | Mistral | Cohere |
                        OpenRouter | any OpenAI-compatible endpoint

n8n — background pipelines
    ├──► ChatHistory sync     (Postgres → Weaviate, every 5 min)
    └──► UserMemory summary   (Weaviate + LLM)

PostgreSQL — Flowise + n8n + Langfuse state
Garage     — S3 object storage: uploaded documents + Langfuse blobs
```

Document **ingestion** (upload → Docling conversion → cleanup → chunking →
embedding → Weaviate) runs as two n8n workflows in this repo, driven from the
Content Admin GUI's upload page — see [`n8n/workflows-ingest/`](n8n/workflows-ingest/).

**Working on this system?** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
records the decisions that are load-bearing and counterintuitive — each with
what breaks if it is changed back. [`docs/RUNBOOK.md`](docs/RUNBOOK.md) covers
the failures that have actually occurred, by the message they produce.

---

## Quick start

**Requirements**: Ubuntu 24.04 LTS (the release everything is tested against
— another even-year LTS is accepted after a warning), Docker, Docker Compose
v2, and an LLM plus embedding API key. Full checklist, including hardware
sizing: [`docs/requirements.md`](docs/requirements.md). The wizard shows this
checklist interactively before it asks you anything.

**Two deployment modes**, chosen as the wizard's first question:

| | Domain mode | Tailscale mode |
|---|---|---|
| Needs | a public domain, DNS you control, ports 80/443 reachable | a [Tailscale](https://tailscale.com/) account — nothing else |
| Certificates | Let's Encrypt via nginx + certbot | issued by Tailscale (DNS-01, no inbound port) |
| Chat reachable by | anyone, at your domain | anyone, via Tailscale Funnel |
| Admin interfaces | public subdomains behind passwords | inside your tailnet only |
| LTI / LMS | supported | **not supported** — an LMS needs stable institutional URLs |
| Intended for | production | test and evaluation systems |

Tailscale mode needs no port forwarding and no DNS at all, which makes it the
short path to a working system on a spare machine behind a home router. Its
one non-obvious requirement: **the computer you administer from must also run
Tailscale, signed into the same account** — otherwise every admin URL fails
with a TLS error that looks like a firewall problem and isn't.

```bash
# On the server — clone into /srv/smart-rag (recommended)
sudo mkdir -p /srv && sudo chown $USER /srv
git clone https://github.com/digillab-lmu/smart-rag.git /srv/smart-rag
cd /srv/smart-rag

# Phase 1 — interactive wizard (≈ 5 min)
sudo bash scripts/bootstrap.sh
#   → asks first: domain mode or Tailscale mode
#   → answers: domain, LLM provider, embedding model, etc.
#   → generates: .env, credentials.txt, nginx config, Weaviate schema

# Domain mode only: set DNS A-record(s) for *.your-domain.example
#   (one wildcard *.your-domain.example, OR individual records per subdomain)
#   Tailscale mode: nothing to do — the wizard already joined the tailnet.

# Phase 2 — deploy (≈ 10 min)
sudo bash scripts/bootstrap.sh --continue
#   → domain mode:    nginx + certbot, Let's Encrypt SAN certificate
#     Tailscale mode: publishes each service on its own tailnet port
#   → starts Docker stack and waits for health
#   → deploys the Weaviate + Neo4j schema, generates LTI keys (if enabled)
#   → walks you through the three browser steps and verifies each one
```

When `--continue` finishes, your stack is running — in **domain mode** at:
- `https://smart-rag.your-domain.example` — Flowise (chat interface)
- `https://content.your-domain.example` — Content Admin GUI
- `https://n8n.your-domain.example` — n8n (automation)
- `https://langfuse.your-domain.example` — Langfuse (if observability profile)
- `https://lti.your-domain.example` — LTI middleware (if lti profile)

In **Tailscale mode** everything sits on one MagicDNS name, separated by port,
because a Tailscale certificate covers exactly one name and has no wildcards:
`https://<machine>.<tailnet>.ts.net` for the chat (public), `:8443` Content
Admin, `:8444` n8n, `:8445` Langfuse, `:8447` the S3 endpoint — all
tailnet-only. There is no storage console: Garage has none.

If another service already occupies a port or subdomain on the host, the
wizard resolves the conflict itself and the real URLs end up in `.env`; the
installer prints the ones this deployment actually answers on.

Initial admin credentials are in `credentials.txt` (chmod 600) — **except
Flowise and n8n**, which prompt you to create their own admin account on
first visit instead (their `FLOWISE_USERNAME`/`PASSWORD` env vars are
ignored by the version this project pins). The installer does not end there:
it walks you through those accounts plus the Flowise API key, then verifies
each one before declaring the system ready. Full first-login walkthrough for
every service: [`docs/operations-guide.md`](docs/operations-guide.md).

**Day-to-day admin** — `sudo bash scripts/admin.sh` (or, once installed,
just `sudo smartrag`) opens a raspi-config-style menu for the operations
you'll actually use after deployment: service status, tailing logs,
pulling updates, restarting a service, SSL certificate status/renewal, a
mail-relay test, a DNS check, a read-only secrets overview, and changing
a handful of live, safe-to-edit settings (mail relay, reranker API key,
LMS URL, admin email, timezone). It offers to install itself as the
global `smartrag` command the first time you run it. Runs entirely on the
host as root over SSH — no extra container, no new network exposure.
(Content authoring — agent prompts, RAG documents, the knowledge graph —
is intentionally not here; it lives in the [Content Admin
GUI](#content-admin-gui).)

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

## Installation wizard features

- **Bilingual** — English or German (auto-detected from `$LANG`, override
  with `--lang en|de`).
- **Deployment mode as the first question** — public domain, or Tailscale for
  a machine with no domain and no reachable ports. In Tailscale mode the
  wizard joins the tailnet with you, waits for MagicDNS and HTTPS to be
  enabled (naming which of the two is missing), and derives every URL in
  `.env` from the resulting MagicDNS name.
- **Ends by verifying, not by instructing** — three steps happen in a browser
  and cannot be scripted: the Flowise account and its API key, the n8n owner
  account, the Content Admin account. The installer stays open, checks each
  against the running services, stores the Flowise key only after Flowise has
  accepted it, and announces readiness only once everything passed. Stopping
  early says exactly what is left unproven.
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

## Content Admin GUI

The bootstrap deploys the *infrastructure* end to end, including the
Weaviate/Neo4j schema and LTI keys (phases 8 and 11) — but Flowise and n8n
start out empty. `content-admin/` (reachable at `https://content.your-domain.example`
once deployed) is where course-specific content gets filled in:

- Up to 10 agent slots, each backed by one of the 6 existing Flowise agent
  archetypes (`flowise/agents/`) — fill in a plain form for that archetype's
  content (concepts, persona, topic subtopics, …), import with one click.
  Everything already known from the CLI wizard (course name, embedding
  model, LLM provider, …) is filled in automatically — the form only asks
  for what's genuinely new.
- Uploading course documents for retrieval, with the bibliographic details
  read out of the PDF or looked up from a DOI/ISBN, and suggested keywords.
- A document list per agent with deletion, so a mistaken upload, a superseded
  edition, or the leftovers of a slot reused for a different topic can be
  removed from the index — chunks and all.
- A "System status" page that checks, live, what still has to be set up
  (API keys, Flowise connection, agents, the n8n ingest webhook, the
  conversion and search services) by asking each service at that moment.
- A guided (not automated) path to seed the Neo4j concept graph: an
  explanation of the data model, a ready-to-copy prompt for an AI of your
  choice, and a box to paste + run the resulting Cypher. A fully automated
  version (the GUI proposes the graph itself, with a review step) is
  planned for later.

First-time setup needs one manual step that can't be avoided: Flowise has no
supported way to hand out an API key non-interactively (same limitation as
n8n). So you create the Flowise admin account once and generate an API key
under Settings → API Keys — the installer tells you what to call it and which
permissions to tick, then takes the key, checks it against Flowise and stores
it, so nobody has to paste it into the GUI. The GUI's own Flowise page
remains available for replacing the key later.

Deliberately a *separate* app from `scripts/admin.sh`: infrastructure/root
operations stay in the TUI (SSH-only, no network exposure); the content GUI
only ever talks to Flowise/Neo4j over the internal Docker network, so a
compromised GUI can't escalate to host control.

The n8n ingest workflows and their credentials are imported by
`scripts/deploy-n8n-workflows.sh`, which the bootstrap runs for you —
except on a first install, where n8n has no owner account yet and the
import has to wait until you've created one in the browser. The bootstrap
says so explicitly and prints the one command to run afterwards; see the
[Operations Guide](docs/operations-guide.md#n8n-automation-httpsn8nyour-domainexample).

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
│   └── workflows-ingest/   # Document ingest: convert, clean, chunk, embed
├── lti-middleware/         # Flask app for LTI 1.3
├── content-admin/          # Flask app for course-content authoring
├── scripts/                # bootstrap.sh, admin.sh, uninstall.sh, compose.sh, lib/, standalone phase scripts
├── tests/                  # Regression suite — bash tests/run-tests.sh
├── CHANGELOG.md            # What changed, and what an upgrade needs
└── docs/                   # requirements, operations guide, architecture, runbook
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
  [Langfuse](https://langfuse.com/), and
  [Garage](https://garagehq.deuxfleurs.fr/) teams.
- Developed by [Benjamin Götzinger](https://www.psy.lmu.de/edu/persons/ag-fischer/goetzinger_benjamin/index.html)
  at the [DigiLLab of LMU München](https://www.lmu.de/digillab/de/) —
  [Chair of Empirical Education and Educational Psychology](https://www.psy.lmu.de/ffp/)
  (Prof. Frank Fischer).
- Pedagogical concept developed in collaboration with the DigiLLab team.
