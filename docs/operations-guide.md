# Operations Guide

What to do after `scripts/bootstrap.sh --continue` finishes: first login to
each service, and how to run the system day to day. For getting to that
point in the first place, see the [Quick start](../README.md#quick-start)
in the README and [`requirements.md`](requirements.md).

When something is broken rather than merely unfamiliar, start at
[`RUNBOOK.md`](RUNBOOK.md) — it is organised by the message you are seeing.
[`ARCHITECTURE.md`](ARCHITECTURE.md) explains why the system is built the
way it is, which matters most when you are about to change something.

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

Open the URL in another window, create the account, return and press Enter.
The wizard then performs the import. If the account does not exist yet, the
check fails and the prompt is repeated.

To postpone the step, answer `n`. Nothing else is affected; the install ends
with `Setup INCOMPLETE — 2 manual steps left`.

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

Either way, that imports the S3/SMTP credentials and both ingest workflows,
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

### Object storage (Garage) — no console

There is no web interface, and this is not a gap to be worked around: Garage
does not have one. Buckets and access keys are created by the installer
(`scripts/deploy-garage.sh`), so there is nothing routine to click.

The credentials in `credentials.txt` are two S3 access keys, each granted only
where it belongs — one for the ingest's document bucket, one for Langfuse's
three. Garage has no root user and no bucket policies, so a key with no grant
on a bucket is refused there even though it is perfectly valid.

To see what is stored:

```bash
docker exec smartrag-garage /garage bucket list
docker exec smartrag-garage /garage bucket info <bucket>
```

`bucket info` shows the object count, the size, and which keys may use it
(`RWO` = read, write, owner). To browse or download objects, any S3 client
works against the endpoint; `mc` needs no installation because the image is
small:

```bash
docker run --rm --network smart-rag-network \
  -e MC_HOST_s3="http://$GARAGE_ACCESS_KEY:$GARAGE_SECRET_KEY@smartrag-garage:3900" \
  --entrypoint mc minio/minio:RELEASE.2025-09-07T16-13-09Z ls --recursive s3/
```

**One failure mode is worth knowing before you meet it.** Garage stores
nothing until a layout has assigned capacity to a node. Without one it starts,
reports healthy, accepts connections and refuses every write — so "uploads
succeed but nothing is stored" is a layout problem, not a credential problem.
`docker exec smartrag-garage /garage layout show` answers it, and re-running
`deploy-garage.sh` fixes it; the script is idempotent.

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
| Mail service | Set it up, or send a test mail against what is configured. One question with four answers, each stating what it needs beforehand |
| DNS check | Re-verify every subdomain still resolves to this server |
| Secrets | Overview of which secrets are set — **values are never shown**, only set/not-set |
| Change configuration | Mail service, reranker API key, LMS URL, admin email, timezone — see below |
| Backup | Make one — the services stop for the duration — or read how to restore. Restoring is a command, not a menu pick: it replaces the installation, so the archive is named on the command line |
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
- **Course ID** — baked into live bucket names and the Weaviate
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

## Upgrading an existing deployment

Some changes need a step on an already-running installation, because
`.env` is generated once and `deploy-schemas.sh` never rewrites a live
Weaviate class. Both of those are deliberate — neither should silently
overwrite something in production — but it means an upgrade occasionally
has one manual action.

**Course scoping (`course_id`).** Everything the agents retrieve carries a
`course_id`, and every agent filters on it, so one installation hosts more
than one course. There is **no upgrade path from 1.x**: a 1.x installation's
data has neither the property nor the values, and a 2.x installation creates
its courses through the Content Admin. The script that used to tag existing
data with the installation's single `COURSE_ID` has been removed — on an
installation with several courses it would have stamped the wrong one, and
the case it was written for cannot arise where there is no in-place upgrade.

The empty-retrieval failure that scoping can produce is intentional. The
alternative — a filter that matched everything when no course was set — would
have served one course's material to another course's students, and nobody
would have noticed. See the RUNBOOK entry "An agent retrieves nothing from
its course" for the three things that actually cause it.

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
- **Knowledge Graph** — two ways to fill the Neo4j concept graph, both ending
  at the same review step. *Build the map from the course material* starts an
  n8n workflow that reads the documents of the agents ticked on the agent
  list, has the strong model extract concepts per document and then derive the
  prerequisites over the resulting list. It runs in the background, takes
  minutes to hours depending on the amount of material, and uses API calls.
  The result appears as a diagram, a table grouped by document and an editable
  list; nothing is written until it is submitted. The manual route needs no
  API key: the page explains the data model and provides a prompt to copy into
  any AI, and the answer is pasted back as JSON. The field does not accept
  Cypher. `neo4j/seed.example.cypher` shows the resulting data model.

  Every concept and prerequisite records the documents it was derived from.
  Three operations follow from that: a build can be taken back out again, the
  material of one agent can be removed from the map without affecting concepts
  that other material also supports, and deleting a document offers to remove
  its contribution.

- **Courses** — created here. Creating a course also creates its chunk
  collection, its object-storage bucket, the ingest key's grant on that
  bucket, and its ten agent slots. Deletion is a separate page that counts
  what the course consists of across six systems before asking for
  confirmation. Each course has a retention date with a note and a record of
  the expiry having been acted on.

- **People** — the data held about one learner across the four systems that
  identify them under different field names, and deletion of all of it.
  Systems holding nothing are listed with a count of zero. Without LTI the
  page states that a deletion covers the entered id only.

The interface is available in English and German (switch top right).
