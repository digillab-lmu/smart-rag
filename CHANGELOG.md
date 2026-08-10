# Changelog

Notable changes, newest first. Dates are release dates.

This project follows [semantic versioning](https://semver.org/) loosely: the
minor version moves when something an operator would notice changes, and
entries marked **Upgrade required** need an explicit step on an existing
installation — `sudo smartrag` → *Upgrade* applies most of them.

---

## Unreleased

### Fixed

- **The Start node in every agent template was three versions behind.** Flowise
  3.1.3 ships `startAgentflow` at 1.4; the templates carried 1.1, so every
  agent showed "Node version 1.1 outdated" when opened. Cosmetic — the node
  has no version-conditional code, so behaviour came from the current release
  either way — and worth fixing for that reason: a warning that is always
  there teaches people to skim past warnings. The other three node types were
  already current. A test pins the versions against the pinned Flowise image,
  so upgrading the image has to revisit them.
- **The upgrade path could turn a placeholder into a live credential.**
  Twenty-two keys in `.env.example` read `generate-with-bootstrap`, and every
  one is a secret. A key added by an upgrade got that literal copied into the
  real `.env` — a credential published in this repository and identical on
  every installation that took the same route. It reached a live deployment
  as both Langfuse project keys, and nothing objected: as a string there is
  nothing wrong with it. The fallback now generates a value whenever the
  example carries the placeholder, and the upgrade entry reports any key in a
  live `.env` still holding it.
- **Observability was installed but never switched on.** The profile ran
  Langfuse and ClickHouse — well over a gigabyte of memory — and received
  nothing: no Langfuse project existed, so there were no API keys for anything
  to report with, and no agent template carried tracing configuration. An n8n
  workflow had been patching traces that were never created, every thirty
  minutes. Langfuse now initialises its organisation, project, user and keys
  headlessly on first start, and importing an agent creates the Flowise
  credential and switches tracing on for that chatflow. Flowise has no global
  switch for Langfuse — its env-based tracing covers LangSmith only — so the
  setting belongs on each chatflow.

  Found by a Garage evaluation that checked whether Langfuse had written any
  objects and found none. The MinIO bucket it had been using all along was
  empty too, which is what ruled out the store as the cause.
- **Restarting a service offers to restart what depends on it.** A recreated
  container can come back at a different address, and a client that resolved
  the name once keeps dialling the old one — reported as a timeout against
  something that is demonstrably running. `depends_on` orders startup and
  does not propagate a restart, so the admin tool now reads the reverse graph
  from Compose's own configuration, transitively. Offered rather than done:
  restarting Postgres would otherwise sweep half the stack along unasked, and
  declining says what the consequence will look like.
- **Memory limits where a runaway is possible.** No container had one, so any
  single service could push the host into swap and let the kernel choose a
  victim — not necessarily the culprit. Docling and ClickHouse are now capped;
  Neo4j's page cache, which it otherwise sizes from whatever RAM it detects,
  is set explicitly. Postgres, Weaviate, MinIO and Redis are deliberately left
  unlimited: their memory grows with the data, so a limit would not stop a
  runaway, it would schedule an outage for whenever the index outgrew the
  number someone picked today — and a limit on Redis without an eviction
  policy means the kernel kills the queue rather than Redis dropping keys.
- **The ingest stops on a failure that cannot resolve.** An OpenAI account
  with no credits produced 23 identical 429s, one per chunk — every call
  doomed the moment the first came back, and the one fact that mattered
  buried in a wall of repeated text. A rejected key, a 403 and an exhausted
  quota now stop the run and report how much was left. A plain rate limit, a
  5xx and a timeout deliberately do not: those clear by themselves, and
  giving up on them throws away work the run could have finished.
- **An unconfigured provider is refused, not substituted.** Nine places
  resolved `LLM_PROVIDER`/`EMBEDDING_PROVIDER` with a default of
  `anthropic`/`openai`. With the variable empty or misspelled, the request
  went to a vendor nobody had configured, carrying a credential shaped for a
  different one, and came back as an authentication error from a service the
  operator had never chosen — pointing away from the actual problem. The
  resolver now names the variable and lists the accepted values, the GUI
  turns that into a message saying no agent was changed, and the ingest's
  image-description node skips with a stated reason instead of calling
  Anthropic with an OpenAI key.

- The two n8n variable names 1.0.0 listed as unverified are settled.
  `WEBHOOK_URL` is deprecated by n8n itself — its config documents
  `N8N_WEBHOOK_URL` as "Successor to the deprecated `WEBHOOK_URL`" — so the
  current name is used. `N8N_DEFAULT_HTTP_TIMEOUT` is read nowhere: absent
  from all 471 `@Env` declarations in `@n8n/config`, absent from the
  near-empty legacy convict schema, and the request helpers that build every
  outbound call contain no `process.env` read at all. Removed; a setting that
  looks like it configures a timeout and does not is worse than no setting.

---

## 1.0.0 — 2026-08-10

The reason 0.9.0 was not 1.0 is gone: the ingest pipeline has now run end to
end on a live server. A PDF was uploaded through the Content Admin, converted
by Docling, cleaned, chunked into 23 pieces, embedded, written to Weaviate —
and an agent answered a question from it that is only answerable from that
document, citing the source.

Everything below was found by getting to that point. Most of it had been
sitting in the code unnoticed, because nothing had exercised it.

### Added

- **Tailscale deployment mode.** A second way to deploy: no domain, no DNS,
  no port forwarding, no nginx. Tailscale issues the certificate over DNS-01,
  the chat is public via Funnel, and every administrative interface is
  reachable only from inside the tailnet. Chosen as the wizard's first
  question. LTI is not available in this mode — an LMS needs stable,
  institutionally approved URLs, not a `*.ts.net` name.
- **The installer verifies instead of instructing.** Three steps happen in a
  browser and cannot be scripted: the Flowise account and its API key, the
  n8n owner account, the Content Admin account. The installer now stays open,
  checks each against the running services, stores the Flowise key only after
  Flowise has accepted it, and announces readiness only once everything has
  passed. Stopping early says exactly what is left unproven.
- **Password reset by email** in the Content Admin, plus a way back in
  without it: `sudo smartrag` → *Reset Content Admin account*. The reply is
  identical for an existing and an invented username, the link only ever goes
  to `ADMIN_EMAIL`, and the token works once within an hour.
- **API key rotation** for the LLM and embedding providers in the admin menu —
  including pushing the new value into Flowise, which keeps its own copy.
- **Upgrade detects values that were never resolved**, not just missing keys,
  and cleans up duplicated ones.

### Fixed

Three that made the system unusable in ways nothing reported:

- **n8n was never using PostgreSQL.** Its database settings carry no `N8N_`
  prefix — `DB_TYPE`, `DB_POSTGRESDB_*` — so all five of ours were ignored and
  n8n ran on SQLite while the Postgres database sat empty. Nothing failed;
  a backup of Postgres would simply have contained none of n8n's workflows,
  credentials or history. **Upgrade required.**
- **Backticks in one message string forked twelve thousand processes.** A
  command name written as markdown inside a double-quoted bash string is
  command substitution. The command was this project's own admin tool, which
  sources the same catalogue — so it recursed about four thousand levels deep
  and held six gigabytes. It surfaced as "high RAM usage".
- **Writing `.env` replaced its inode**, and Docker bind-mounts a single file
  by inode. Every container kept reading the version it started with, for its
  whole lifetime, while the host showed the new value. A rotated API key kept
  failing with the old key's billing error.

And the rest:

- Langfuse reads `REDIS_AUTH`, not `REDIS_PASSWORD`, and answered `WRONGPASS`
  to the empty password it therefore used. **Upgrade required.**
- `SMTP_SENDER_EMAIL` stayed `noreply@${DOMAIN}` in `.env`. Compose expands
  that for `environment:` but not for `env_file`, and n8n takes its whole
  environment from `env_file` — the ingest's completion mail would have gone
  out with a literal `${DOMAIN}` in the sender. **Upgrade required.**
- `smartrag-langfuse-web` had no healthcheck, and the wait loop could not tell
  "no healthcheck" from "still starting", so every install lost 180 seconds
  and printed unrelated logs beside the timeout.
- `N8N_TRUST_PROXY` does not exist; the setting is `N8N_PROXY_HOPS`, a count.
  Without it, rate limiting keyed on the proxy's address rather than the
  client's — behind one reverse proxy, that is everyone at once.
- The public chat URL was assembled from `DOMAIN`, applying the domain-mode
  naming rule in every mode. On a Tailscale install it named a host with no
  certificate and no DNS record, and handed it to students.
- An empty translation was treated as a missing one, so a deliberately blank
  table header rendered as the literal key `docs_col_action`.
- Rotating an API key did not reach Flowise: an existing credential was looked
  up by name and returned untouched, so agents kept using the replaced key —
  and re-importing found the same stale credential.
- The admin menu's first entry was overwritten by buffered keystrokes, and the
  menu had outgrown its own height, hiding entries below a scroll fold.
- `exit` at a wizard prompt did not exit.
- Tailscale setup could not tell "MagicDNS is off" from "the name has not
  arrived yet", and read a peer's hostname instead of this machine's.
- The MinIO notification to `/webhook/minio-notify` was configured and served
  by nothing, producing an error line for every uploaded document.
- The mail nodes in the ingest workflow made a delivery failure fail the whole
  run, so a successful ingest looked like a failed one wherever no relay is
  configured.

### Changed

- `.env` values may no longer contain a line break: bash keeps everything
  after it inside the value while the Content Admin's reader splits on lines,
  so the two would disagree about which keys exist.
- The installer warns before regenerating secrets over initialised databases,
  naming which stores hold data and why a password cannot be changed after
  initdb.
- `start-services.sh` recreates containers when `.env` is newer than they are.
- The `smartrag` command installs itself without asking.

### Upgrading from 0.9.0

```bash
cd /srv/smart-rag && git pull
sudo smartrag        # → Upgrade — apply pending migrations
bash scripts/compose.sh up -d --build
```

The upgrade entry now reports both missing keys and values that were never
resolved; accept both.

**n8n moves from SQLite to PostgreSQL** the next time its container is
recreated, which the compose change alone will trigger. The new database is
empty: the owner account has to be created again in the browser, and the
workflows re-imported via `sudo smartrag` → *Ingest*. Nothing is deleted —
the old `database.sqlite` stays in the volume — but nothing is migrated
either. Plan for it rather than discovering it.

Any `.env` change made from the admin TUI before this release never reached
the running containers. If a value looks right on the host and wrong in the
application, recreate the container once; from this release on, it applies.

### Known limitations

- MinIO is archived upstream. The pinned image is the last one published and
  works; replacing it is a decision to be made, not an emergency. See below.
- Several courses can share one installation, but the Content Admin still has
  a single account and a single course selection. The design that resolves
  this is recorded in `docs/ARCHITECTURE.md` (6a, 6b).
- The knowledge graph is seeded through a guided path, not automatically.
- ~~`WEBHOOK_URL` and `N8N_DEFAULT_HTTP_TIMEOUT` are passed to n8n under names
  that could not be confirmed.~~ Resolved after 1.0.0 — see the entry above.

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