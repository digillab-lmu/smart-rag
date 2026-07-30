# Operations Guide

What to do after `scripts/bootstrap.sh --continue` finishes: first login to
each service, and how to run the system day to day. For getting to that
point in the first place, see the [Quick start](../README.md#quick-start)
in the README and [`requirements.md`](requirements.md).

---

## First login, service by service

Deployment gives you running *infrastructure* — the services themselves
still need a first-time setup step before they're usable. Do these in
order.

### Flowise (chat interface) — `https://smart-rag.your-domain.example`

**Important:** Flowise does **not** use the `FLOWISE_USERNAME` /
`FLOWISE_PASSWORD` values from `.env` or `credentials.txt` — those
variables were deprecated upstream in Flowise v3.0.1 and are silently
ignored by the version this project pins. Instead:

1. Open the URL above. Flowise detects there's no admin account yet and
   shows a **"Create Admin Account"** screen.
2. Choose your own email + password there. This is stored in Flowise's own
   database (Postgres) from that point on — not in `.env`, and not
   editable via `scripts/admin.sh`.
3. If you ever need to reset it, that has to go through Flowise's own
   account-recovery flow (or its API/database directly) — this project
   doesn't manage that credential.

Nothing to import here yet — Flowise starts empty. Agent import (the ~6
templates in `flowise/agents/`) is planned but not yet built (see
[What's NOT done](../README.md#whats-not-done-by-the-bootstrap-yet) in the
README) — for now, importing/configuring agents is a manual, one-off task
through the Flowise UI.

### n8n (automation) — `https://n8n.your-domain.example`

Same pattern as Flowise: first visit prompts you to create the owner
account. Stored in n8n's own database, not `.env`.

Workflow import (`n8n/workflows/`) is likewise not yet automated — see the
README's "What's NOT done" section.

### MinIO console — `https://minio.your-domain.example`

Logs in with `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` from
`credentials.txt` — this one **does** work as documented, MinIO reads
these directly from its container environment on every start.

### Langfuse (if `observability` profile enabled) — `https://langfuse.your-domain.example`

First visit prompts you to create an account, same pattern as
Flowise/n8n — its own database, not `.env`.

### Neo4j Browser (optional, direct access)

Not exposed via nginx/HTTPS by default (internal-network + localhost-bound
ports only, see `docker-compose.yml`). If you need to browse the graph
directly, `ssh -L 7474:localhost:7474 your-server` and connect to
`http://localhost:7474` with `neo4j` / the `NEO4J_PASSWORD` from
`credentials.txt` — that one **does** come from `.env`, Neo4j reads
`NEO4J_AUTH` on every start.

---

## Day-to-day operations

Once deployed, don't run the individual phase scripts by hand — use the
admin TUI:

```bash
sudo bash scripts/admin.sh
```

It offers to install itself as a global `smartrag` command the first time
you run it, so afterwards it's just:

```bash
sudo smartrag
```

What's in there:

| Menu item | What it does |
|---|---|
| Status | Health of every running container |
| Logs | Tail one service's logs (`Ctrl+C` to stop) |
| Update | `docker compose pull && up -d` — picks up new pinned image versions after a `git pull` |
| Restart a service | `docker restart` on one container |
| SSL | Certificate status/expiry, force-renew |
| Mail | Send a test email against the configured relay; view current relay config |
| DNS check | Re-verify every subdomain still resolves to this server |
| Secrets | Overview of which secrets are set — **values are never shown**, only set/not-set |
| Change configuration | Mail relay reconfigure, reranker API key, LMS URL, admin email, timezone — see below |
| Uninstall | Runs `scripts/uninstall.sh` |

### What "Change configuration" covers, and why some things aren't there

Only values that are (a) actually read live from the container environment
and (b) safe to change without migrating existing data are editable there.
Concretely excluded, on purpose:

- **LLM / embedding provider, model, API key** — not read by any running
  container at all right now (only relevant once the not-yet-built agent
  import step exists, and even then Flowise stores its own copy of a
  credential once imported — editing `.env` afterward wouldn't update it).
  Changing `EMBEDDING_MODEL` after documents have been ingested breaks
  vector compatibility regardless of how it's changed.
- **Course ID** — baked into live MinIO bucket names and the Weaviate
  collection name. Changing it doesn't migrate anything; it orphans the
  existing bucket/collection and starts empty new ones.
- **Domain, base data path, port overrides, enabled profiles
  (observability/lti)** — each of these cascades into nginx config,
  SSL certificate SAN entries, and/or which containers exist at all.
  Effectively a redeploy, not a config edit.
- **Flowise/n8n login** — see above, not stored in `.env` at all.

For any of the above, the safe path is re-running the relevant phase
script directly (`scripts/get-ssl-certs.sh`, etc.) with a clear
understanding of what else needs to change alongside it, not a quick menu
edit.

---

## Content authoring (course material, agent prompts, knowledge graph)

**Not yet available.** The 6 agent templates in `flowise/agents/` contain
~25 placeholders (`{{CONCEPT_LIST}}`, `{{PERSONA_NAME}}`, `{{TOPIC_NAME}}`,
etc.) that need real, course-specific teaching content — this is planned
as a dedicated web-based content admin (separate from `scripts/admin.sh`,
so it never needs host/Docker-level privileges), covering:

- Filling in the agent template placeholders and importing the resulting
  agents into Flowise
- Uploading and managing RAG source documents
- Editing the Neo4j concept-prerequisite graph (`neo4j/schema.cypher`'s
  data model: Topics, Concepts, `BELONGS_TO`, `PREREQUISITE_FOR`)

Until it ships, do these manually through the Flowise / n8n / Neo4j
Browser UIs, using the JSON templates in `flowise/agents/` and
`n8n/workflows/`, and `neo4j/seed.example.cypher` as a starting point for
your own graph data.
