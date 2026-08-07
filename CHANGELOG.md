# Changelog

Notable changes, newest first. Dates are release dates.

This project follows [semantic versioning](https://semver.org/) loosely: the
minor version moves when something an operator would notice changes, and
entries marked **Upgrade required** need an explicit step on an existing
installation — `sudo smartrag` → *Upgrade* applies most of them.

---

## 0.9.0 — 2026-08-05

First tagged release. Everything before this was an untagged `main`.

Not called 1.0 for one honest reason: the document-ingest pipeline has never
been run end to end on a live deployment. Every stage is verified in
isolation and the whole chain is covered by tests, but "upload a PDF and have
an agent answer from it" has not yet happened on a real server. Until it has,
a 1.0 would be claiming something that hasn't been demonstrated.

### Added

- **Course scoping.** One installation can host several courses. Every
  object the agents retrieve carries a `course_id`, and every agent filters
  on it. **Upgrade required** — see below.
- **Document list with deletion** in the Content Admin GUI. Until now
  nothing could be removed from the index: a mistaken upload was permanent,
  a revised edition became a duplicate, and — the case that produces wrong
  answers rather than clutter — an agent slot reused for a new topic
  silently inherited the previous agent's documents.
- **System status page** in the GUI: API keys, the Flowise connection,
  agents, the n8n ingest webhook and the conversion services, each checked
  live by asking the service rather than reading a stored flag.
- **Publishing an agent** as a public chat link, with an explicit warning
  that a published agent answers anyone, without knowing who is asking, on
  the operator's LLM budget.
- **Upgrade entry** in `sudo smartrag`: finds `.env` keys a newer version
  expects, and applies the course-scoping migration after a dry run.
- **RAM check** in the preflight, with a stop-and-confirm when the machine
  is below the documented minimum for the chosen profiles. Memory
  exhaustion disguises itself as a disk fault, a slow restart and a dying
  container all at once; nothing used to warn about it.
- **Regression test suite** — `bash tests/run-tests.sh`, 21 suites, about
  15 seconds, needing only Python 3 and bash.
- **`docs/ARCHITECTURE.md`** (the decisions that are load-bearing and
  counterintuitive, each with what breaks if changed back) and
  **`docs/RUNBOOK.md`** (the failures that have actually occurred, indexed
  by the message they produce).
- Document upload can read bibliographic details from a PDF or look them up
  by DOI/ISBN, and suggest keywords.
- Agent system prompts are viewable, editable and resettable in the GUI.
- German translation of the whole GUI, switchable per user.

### Fixed

- **Agents could not authenticate at all.** Credential ids were written to
  `credential`; Flowise's runtime reads `FLOWISE_CREDENTIAL_ID`. Every agent
  answered `Missing credentials … set the OPENAI_API_KEY environment
  variable` on its first message, regardless of the configured provider.
- **Agents threw on their first message.** Template code read
  `process.env`, which Flowise's sandbox sets to `undefined`, so the
  fallback chain threw instead of reaching its default — on every standard
  install, because `EMBEDDING_BASE_URL` is empty there.
- **Retrieval had no Weaviate credential**, which would have failed as soon
  as an agent actually queried the index.
- **Public URLs ignored `SUBDOMAIN_PREFIX`.** Four were assembled from
  `${DOMAIN}` in the compose file, pointing services at hostnames with no
  DNS record, vhost or certificate. `APP_URL` was wrong even without a
  prefix. **Upgrade required.**
- **MinIO's n8n notification** used the host-side port for a
  container-to-container URL, logging `connection refused` on every object.
  **Upgrade required.**
- **The installer reported success it had not verified.** A skipped phase
  printed the same completion banner as a finished install, and the n8n
  import claimed the ingest was ready without checking the webhook.
- The installer now waits while the admin creates the n8n owner account and
  finishes the import itself, instead of printing instructions and exiting.
- Uploads of up to 200 MB no longer fail with a 413 at nginx.
- The GUI shows the server's actual error instead of a browser
  `SyntaxError` when a response is not JSON.

### Changed

- Ubuntu 24.04 remains the tested release; another LTS is accepted after a
  stated caveat and a confirmation, rather than refused outright.
- Importing the n8n workflows is reachable from the admin menu, and the
  Content Admin GUI asks the operator to forward a message to whoever
  administers the server rather than telling them to run a shell command
  they have no access to.
- "Getting started" is now "System status" — the page is consulted long
  after setup, whenever something stops working.

### Removed

- The WhisperX audio-transcription workflow. It carried the original
  deployment's hostnames, an internal IP and a personal email address, and
  depended on a GPU-bound service no fresh installation has.

### Upgrading from an untagged `main`

```bash
cd /srv/smart-rag && git pull
sudo smartrag        # → Upgrade — apply pending migrations
bash scripts/compose.sh up -d --build
```

Then re-import your agents in the Content Admin GUI. Two of the fixes above
only take effect at import time — the flows already in Flowise keep their
old, broken configuration until then.

**Until the course-scoping migration has run, the agents retrieve nothing.**
That is deliberate: the alternative, a filter matching everything when no
course is set, would serve one course's material to another course's
students without anyone noticing.

### Known limitations

- The ingest pipeline has not been run end to end on a live deployment.
- MinIO is archived upstream; the pinned image is the last one published to
  Docker Hub. A migration to an alternative is a decision that will have to
  be made, not an emergency.
- Several courses can share one installation, but the Content Admin GUI
  still has a single account and a single course selection. The design that
  resolves this is recorded in `docs/ARCHITECTURE.md` (6a, 6b): one chunk
  collection per course, courses created in the GUI rather than answered
  during installation, and maintainers per course. Decided before a second
  course exists anywhere, because the migration is a rename today and an
  export-and-re-embed later.
- `content-admin/env_file.py` is now covered: adversarial values are sourced
  by real bash with canaries on PATH to prove nothing executes, and Python
  and bash are checked to read every value identically.