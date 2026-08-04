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

Flowise starts empty, and you don't import agents here by hand: you fill
in a course-content template in the [Content Admin GUI](#content-authoring-course-material-agent-prompts-knowledge-graph)
and press "Save and import to Flowise". The one manual step is giving that
GUI an API key — its **Flowise Connection** page walks you through it
(Flowise has no supported way to hand out a key automatically, same
chicken-and-egg as the admin account above).

### n8n (automation) — `https://n8n.your-domain.example`

Same pattern as Flowise: the first visit prompts you to create the owner
account, stored in n8n's own database, not `.env`.

**This one is required.** The ingest workflows are imported by a script,
but that script needs an existing n8n owner to assign the imported
credentials and workflows to — and that account can only be created in a
browser. So `bootstrap.sh` stops at this point and asks you to do it now:

```
ℹ  The ingest workflows still need to be imported, and that needs an n8n
   owner account — which only you can create, in a browser. Open
   https://n8n.your-domain.example now and complete its one-time owner
   setup (email + password). This wizard will wait, and finish the import
   for you afterwards.

   Done — n8n owner account created? [Y/n]:
```

Open the URL in another window, create the account, come back and press
Enter — the wizard then runs the import itself and you're done. If you
answer too early, it says so and offers another attempt.

If you'd rather not do it right now, answer `n`. Nothing else is affected,
but the install then ends with `Setup INCOMPLETE — 2 manual steps left`.

To finish it later, use the admin menu — not a hand-typed command:

```bash
sudo smartrag
```

→ *Ingest — (re-)import n8n credentials + workflows*. That menu entry runs
the exact same guided flow as the installer: if n8n still has no owner
account it names the URL, waits while you create one, and then does the
import. Importing the ingest workflows is part of a standard setup, so it
is meant to be reachable from the menu without touching the command line.
(`sudo bash scripts/deploy-n8n-workflows.sh` does the same thing directly,
if you prefer.)

Either way, that imports the MinIO/SMTP credentials and both ingest workflows,
activates the document-ingest workflow, restarts n8n, and then **verifies
that the webhook is actually registered** before reporting success. It is
re-runnable at any time — imports are keyed by fixed ids, so a re-run
updates in place rather than creating duplicates.

**Until you do this, document upload will fail with a 404.** That 404 is
the single most common symptom of a half-finished install: the Content
Admin GUI is working correctly and n8n simply has no webhook listening.
The GUI's **System status** page checks this — along with the API keys,
the Flowise connection, your agents, and the Docling/markdowncleaner/
Weaviate services — and tells you which step is missing, at any time.

### MinIO console — `https://minio.your-domain.example`

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` from `credentials.txt` are the
real credentials — MinIO reads them from its container environment on
every start, and everything that talks to MinIO over the S3 and admin
APIs (the bucket setup, n8n, Langfuse) authenticates with them.

**The web console itself is a different story, and may simply refuse to
log you in.** In 2025 MinIO stripped the Community Edition console down to
an object browser, removing roughly 110,000 lines of management UI; the
management features moved to the paid edition, and the pinned image here
(`RELEASE.2025-09-07`, the last one ever published to Docker Hub) is from
after that change. A console login returning 401 while `mc` works with the
same credentials is therefore not a sign that your credentials are wrong.

Nothing in SMART RAG needs that console. If you want to look at what is
actually in the buckets, use `mc` — the client is already on the host as a
container image:

```bash
docker run --rm --network smart-rag-network -it minio/mc:latest sh -c \
  'mc alias set s3 http://smartrag-minio:9000 "$USER" "$PASS" && mc ls --recursive s3/'
```

(substituting your own values for `$USER` / `$PASS`, or exporting them
first). `mc ls`, `mc cp` and `mc du` cover browsing, downloading and size
checks.

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
| Status | Health of every running container, plus whether the n8n ingest webhook is actually registered — a healthy n8n container with no active workflow looks fine but 404s every upload |
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

This is done in the **Content Admin GUI** at
`https://content.your-domain.example` — a separate app from
`scripts/admin.sh` on purpose: it only ever talks to Flowise, n8n and
Neo4j over the internal Docker network, and never touches Docker or the
host filesystem, so a compromised content GUI cannot escalate to host
control. (That boundary is also why it can show you the
`deploy-n8n-workflows.sh` command but cannot run it for you.)

On first visit it asks you to create its own admin account — separate
from the Flowise and n8n accounts above, and unrelated to `.env`.

What you do there:

- **System status** — a live checklist of everything that has to be in
  place: API keys, the Flowise connection, your agents, the n8n ingest
  webhook, and the Docling/markdowncleaner/Weaviate services. Each line is
  checked by asking the service itself at that moment, so it is also the
  fastest way to answer "why isn't this working?" later on.
- **Agents** — up to 10 slots, each based on one of the agent archetypes
  in `flowise/agents/`. Fill in a plain form for that archetype's course
  content and import it into Flowise with one click; anything already
  known from the install (course name, LLM provider, embedding model, …)
  is filled in for you. Each agent's system prompt can be viewed and
  edited, and reset to the shipped default. An imported agent can also be
  published as a public chat link — read the warning on that page first.
- **Documents** — upload course material for retrieval. The GUI can read
  the bibliographic details out of the PDF or look them up from a DOI or
  ISBN, and suggest keywords. Processing runs asynchronously in n8n;
  a large scanned PDF can take tens of minutes, and you get an email when
  it's done. **This needs the n8n step above to be finished.**
- **Knowledge Graph** — a guided (not automated) path to seed the Neo4j
  concept graph: the data model explained, a ready-to-copy prompt for an
  AI of your choice, and a box to paste and run the resulting Cypher.
  `neo4j/seed.example.cypher` is a starting point for doing it by hand.

The interface is available in English and German (switch top right).
