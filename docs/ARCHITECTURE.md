# Architecture decisions

Why this system is built the way it is. Each entry records what was decided,
the reasoning behind it, and the failure it prevents.

These decisions describe constraints that matter when the system is modified.
Several were introduced in response to a failure and are not obvious from the
current implementation. Where a decision depends on an external implementation,
the relevant file and version are given so the source can be checked
independently.

For how to *operate* the system, see [`operations-guide.md`](operations-guide.md).
For what breaks and what to do about it, see [`RUNBOOK.md`](RUNBOOK.md).

---

## 1 · Two applications, separated by privilege

**Decision.** `scripts/admin.sh` (a TUI on the host, as root) does
infrastructure. `content-admin/` (a web GUI in a container) does course
content. The GUI has no Docker socket and no host filesystem beyond a single
bind-mounted `.env`; it reaches other services over HTTP on the internal
Docker network only.

**Rationale.** The GUI is exposed to the public internet and logged into by
teaching staff. A web interface able to exec into containers would present a
considerably larger attack surface, and the tasks it serves do not require
that access.

**Failure mode.** Invoking `deploy-n8n-workflows.sh` from the GUI would cross
the privilege boundary, since that script needs host-level access. Deployment
therefore remains an administrative operation. The GUI displays the step and
provides a message that can be forwarded to whoever administers the server.

---

## 2 · Public URLs are resolved in `.env`

**Decision.** Any URL containing a subdomain is computed by
`scripts/lib/templates.sh` through `subdomain_host()` and written to `.env`
fully resolved. `docker-compose.yml` only ever reads the finished value.

**Rationale.** `SUBDOMAIN_PREFIX` is set when the plain names collide with
something already on the host, and Compose interpolation cannot express
"prefix, but only if there is one".

**Failure mode.** Writing `https://s3.${DOMAIN}` in the compose file drops the
prefix. On a prefixed installation the resulting hostname has no DNS record,
no vhost and no certificate, and the symptom appears elsewhere, as a service
that cannot log in. `tests/test_public_urls.sh` rejects any compose value that
puts something in front of `${DOMAIN}`.

---

## 3 · Host ports and container ports

**Decision.** `N8N_PORT`, `WEAVIATE_HTTP_PORT` and friends in `.env` are
**host-side** bindings, which the wizard may move on a conflict. Every
container-to-container URL hardcodes the container's own port: n8n 5678,
Weaviate 8080, Flowise 3000, Docling 5001, markdowncleaner 8000.

**Rationale.** Only the host binding can move. Inside the network the port is
fixed by the image.

**Observed failure.** In the MinIO era, `MINIO_NOTIFY_WEBHOOK_ENDPOINT` used
`${N8N_PORT}` for an internal URL. On a deployment where the wizard had
resolved n8n to 5778, MinIO logged `connect: connection refused` against
`smartrag-n8n:5778` on every object written.

---

## 4 · Flowise credentials live under `FLOWISE_CREDENTIAL_ID`

**Decision.** `agent_templates.set_credential_ids()` writes the credential
id to `FLOWISE_CREDENTIAL_ID` in every node config block, and keeps the
`credential` key in sync.

**Evidence.** That is the key Flowise's runtime reads. Verified in
flowise@3.1.3: `Agent.ts:909` and `LLM.ts:376`
(`credential: modelConfig['FLOWISE_CREDENTIAL_ID']`), plus `Agent.ts:830` and
`:845` for embeddings and the vector store. The `credential` key the templates
ship with is what the canvas displays; the runtime ignores it.

**Observed failure.** Every agent answers `Missing credentials. Please pass an
apiKey, or set the OPENAI_API_KEY environment variable` on the first message,
regardless of the configured provider: with no credential attached, the node
falls back to the provider SDK's own environment variable. The error therefore
names the provider SDK rather than the configured provider, which makes the
missing Flowise credential difficult to identify.

---

## 5 · Two Weaviate filter formats

**Decision.** Agent retrieval filters (in the templates) use the gRPC
TypeScript client's shape. Deletion (in `weaviate_client.py`) uses the REST
API's classic `where` filter. They look similar and are not compatible.

| | Retrieval (agents) | Deletion (REST) |
|---|---|---|
| combine | `filters: [...]` | `operands: [...]` |
| property | `target: {property: "x"}` | `path: ["x"]` |
| value | one untyped `value` | typed: `valueText`, `valueInt` |

**Rationale.** They are different APIs. Flowise's node speaks gRPC through
`@langchain/weaviate`; batch deletion has no gRPC equivalent in use here.
Verified in `weaviate/typescript-client`
(`src/collections/filters/{types,classes}.ts`) and `weaviate/weaviate`
(`openapi-specs/schema.json`).

**Failure mode.** The wrong shape is accepted by the API and matches nothing.
A retrieval returns no results; a deletion deletes nothing and reports
success. Both tests assert the vocabulary of their own side and that the other
side's terms do not appear.

**Related.** The JSON type of a filter value decides its wire encoding.
`agent_id` is an `int` property, so its placeholder in the templates is
unquoted; quoting it produces `"4"`, which is encoded as `valueText` and
matches nothing on an int column.

---

## 6 · `course_id` on the data, not a stack per course

**Decision.** One installation can host several courses. Every object the
agents retrieve carries a `course_id` and every agent filters on it. The
Weaviate collection is shared.

**Rationale.** A full deployment per course multiplies RAM by the number of
courses, and the documented minimum is already 8 GB.

**Failure mode.** An agent without a `course_id` condition sees every course's
material. Two archetypes had no filter at all before this and would have done
so. One consequence of the shared collection: all courses on an installation
use the same embedding model, since a Weaviate collection has one vector
configuration.

> **Superseded in part — see 6a.** The shared *chunk* collection is being
> replaced by one per course. `course_id` stays on the data, and the shared
> classes stay shared; only the material served to students moves.

---

## 6a · One chunk collection per course

**Status.** Agreed 2026-08-06, built 2026-08-11. Every course gets its own
chunk collection, created by the Content Admin from the `__COLLECTION_NAME__`
template in `weaviate/schema.json` at the moment the course is created.

**Decision.** The collection students' material is retrieved from is created
per course, by the Content Admin, when a course is created. `ChatHistory`,
`UserMemory`, `TestResults` and `WorkflowState` stay shared and keep
`course_id`: they hold what a learner produced, and their value is that they
span agents. `course_id` also stays on the chunks, as a second boundary and
for provenance.

**Rationale.** The boundary in decision 6 is a JSON string stored inside each
chatflow — `{"operator":"Equal","target":{"property":"course_id"},…}` —
substituted at import and editable afterwards in Flowise's own GUI. Ten agents
times N courses is a large number of places that must stay correct, and the
two failure modes are not symmetric:

- With a shared collection and a filter that is missing or mistyped, the agent
  answers from every course. The answers remain plausible, so the fault can go
  unnoticed until a student sees another course's material.
- With a per-course collection and a wrong name, nothing is found at all, and
  the first test of the course shows it.

For material served to students, the failure that is immediately visible is
preferable. A malformed Weaviate filter accepted by the API and matching
nothing has already occurred here (decision 5), which is the failure class the
course boundary would otherwise rest on.

**Consequences.** Creating a course becomes an operation with side effects (a
collection, a bucket, a set of slots), and so does deleting one. Both need the
treatment document deletion already receives: name what disappears, count it,
and require a deliberate confirmation.

**Reverting.** Once two courses share a collection, returning to per-course
collections costs an export and a re-embed rather than a rename, which is why
this was decided before a second course existed anywhere.

---

## 6b · Courses are created at runtime, not at install time

**Status.** Agreed 2026-08-06, built 2026-08-20, the last piece of it after a
fresh install showed the installer still asking. The paragraphs below are the
decision as it was taken; what was actually built is at the end of this
section.

**Decision.** `bootstrap.sh` stops asking for a course. It asks for a name for
the *installation* and deploys only the shared schema. Courses — including
their collection, bucket and agent slots — are created in the Content Admin.
`COURSE_ID`, `COURSE_NAME` and `WEAVIATE_COLLECTION_NAME` leave `.env` and
become fields of a course record; existing installations migrate by turning
their single course into the first record, keeping the collection's current
name.

**Rationale.** A course is created and deleted while the system runs. Asking
for one during installation makes a runtime object into a property of the
host, and that is what limited the earlier design to a single course.

**What this changes.** Three couplings, all of which read the course from an
environment variable and must take it from the request instead:

- The ingest workflows: `$env.COURSE_ID` for the bucket name and
  `$env.WEAVIATE_COLLECTION_NAME` in three places.
- `chathistory-sync` and `usermemory-summary`, which stamp `COURSE_ID` onto
  every record. With several courses they must derive it from the chatflow,
  and that lookup does not exist yet: the Content Admin knows the mapping
  (`slots.json` carries `chatflow_id`), n8n does not. This coupling fails
  without any error, by writing the history under the wrong course.
- Agent import, where `WEAVIATE_COLLECTION_NAME` and `COURSE_ID` are already
  auto-filled fields, which is why per-course collections cost almost nothing
  at import time.

**The boundary Flowise does not enforce.** Flowise has no per-course
separation, and its API keys carry action permissions (`chatflows:update`)
rather than object scopes; there is no key that sees only one course's agents.
With per-course maintainers, the only thing keeping one out of another's
agents is the Content Admin's own authorization. It therefore belongs at a
single choke point every route passes through rather than repeated per route:
a check repeated in fifteen routes will eventually be omitted in one, and the
omission is not visible. Chatflow names must also carry the course, since
Flowise's names are global and `find_chatflow_by_name` would otherwise match
the wrong one.

**Scope.** One installation, several courses, maintainers per course. Not a
second tenancy level above the installation: an installation-wide name used to
derive collection names would put course data back under an `.env` value and
move the problem rather than solve it.

---

**What was actually built (2026-08-20).** All three couplings are gone, and
two of them turned out to be worse than reading the wrong variable:

- The ingest workflows fell back to an installation-wide course id when the
  request carried none. With the variable removed, that fallback would have
  written chunks with an empty course id: written, counted, reported as
  success, and invisible to every agent, because each agent's retrieval
  filters on the course. Both workflows now refuse an upload that names no
  course or no collection, and state which is missing.
- `chathistory-sync` and `usermemory-summary` take the course from the record
  they are processing. The lookup this section described as missing is the
  `course_id` the sync already writes.
- Agent import was the cheap one, as predicted: the course arrives as a course
  and its collection with it.

Also removed, being the same assumption in another form: a bash
collection-name derivation that produced a different name from the Python one
now in use, the course bucket the installer created (a course's bucket is
created with the course), the per-course class in the staged schema, and
`COURSE_ID` in `credentials.txt`, which under `set -u` terminated the
bootstrap after `.env` had been written.

`tests/test_course_scoping.py` checks the whole tree for the single-course
assumption returning, in code rather than in comments.

## 6c · What was decided when the build was planned (2026-08-11)

**Status.** Agreed 2026-08-11, before any of 6a/6b was built. Four questions
were open in the design above; these are the answers, with what each one
costs.

**Maintainers belong to n courses.** Two roles: an installation administrator
who creates courses and accounts, and a course maintainer who may be assigned
to several courses and switches between them. The alternative, one course per
account, is simpler and was rejected because the same person routinely looks
after a lecture and its seminar, and two logins for one person leads to shared
passwords.

**Deleting a course asks what to do with the learner data.** Content
(collection, bucket, slots) always goes. `ChatHistory`, `UserMemory` and
`TestResults` are the subject of an explicit question at deletion time, with
no pre-selected answer: keeping them can be legitimate for research and needs
a legal basis, and deciding that on the operator's behalf without asking would
be wrong in either direction. Whatever is chosen, the inventory is counted and
shown first.

**No migration path from the single-course release (0.2.0).** The course values leave
`.env`, and rather than a migration this is a new installation. That trade is
defensible only because that release has essentially no installed base beyond
this project's own test machine. It must not result in silent damage, so
bootstrap and the upgrade path detect a `COURSE_ID` in `.env` and refuse to
run, naming the reason. Refusing leaves the existing installation unchanged,
so the situation can be corrected before any migration is attempted.

**Postgres, not JSON files.** `slots.json` was adequate for ten slots. The
target is 50+ courses at ten slots each, with several maintainers writing at
once, and a JSON file with hundreds of entries and concurrent writers risks
data loss. Courses, users, assignments and slots move into the Postgres that
already runs, behind a versioned schema.

**Out of scope, and why.** `n8n/workflows/` — `chathistory-sync`,
`usermemory-summary` — is not installed by the deployer, which reads only
`workflows-ingest/`. The course-stamping coupling 6b describes in those
workflows is therefore real but dormant. They stay out of scope because they
cannot be tested on a live system here, and untested course routing for
learner data should not be shipped.

---

## 6d · Where the concept graph should live (open, 2026-08-12)

**Status.** Not decided. Recorded so the comparison does not have to be
repeated.

**What is built.** The graph is per course, enforced by a `course_id` on every
node and in every statement, with the pair (course, name) folded into a
synthetic `key` property.

**Correction (2026-08-13).** The reason given for that synthetic key was
wrong. It said Neo4j Community cannot constrain two properties at once. What
is Enterprise-only is the *node key*, meaning uniqueness and existence
together. Plain composite uniqueness is not, and was confirmed against the
running 5.26.28 Community instance:

```cypher
CREATE CONSTRAINT ... FOR (c:Concept) REQUIRE (c.course_id, c.name) IS UNIQUE
```

was accepted. The synthetic key can therefore be replaced by a constraint the
database enforces on the real pair. What does not change: Community still
allows exactly one database, so a query that omits `course_id` still reaches
every course. The boundary becomes database-enforced for uniqueness and
remains a convention for isolation.

**Why this boundary is weaker than the rest of the system.** Everywhere else
the boundary is physical: a Weaviate collection per course, a Garage bucket
per course, rows behind a foreign key. Here it is a property, and it holds
because queries name it and a test checks that they do. It is maintained by
convention rather than by the database.

**The three options in detail.** ★★★ good, ★★☆ workable, ★☆☆ poor, — absent.
Rows marked *(unverified)* are from documentation, not from a test here.

| | Neo4j CE 5.26 (today) | Neo4j + DozerDB | PostgreSQL + Apache AGE |
| --- | --- | --- | --- |
| **Course boundary** | property, by convention ★☆☆ | one database per course ★★★ | foreign key / one graph per course ★★★ |
| Composite uniqueness | ★★★ (verified 2026-08-13) | ★★★ | ★★☆ unique index on the property pair |
| Existence constraint | — Enterprise | ★★★ *(unverified)* | ★★★ `NOT NULL` |
| Several databases | — one only | ★★★ | ★★★ schemas/graphs |
| Roles and permissions | — single user | — not advertised | ★★★ Postgres roles, row-level security since 1.6.0 |
| Query language | Cypher, full ★★★ | Cypher, full ★★★ | openCypher subset ★★☆ |
| Deep traversal | ★★★ native | ★★★ native | ★★☆ weaker, but our deepest query is a prerequisite chain |
| Graph algorithms | ★★★ GDS plugin | ★★★ GDS plugin | ★☆☆ SQL/recursive CTE or in Python |
| Deleting a course | a `MATCH … DELETE` that must name the course ★★☆ | `DROP DATABASE` ★★★ | `DELETE … WHERE course_id` / drop the graph ★★★ |
| Backup and restore | its own data directory, its own step ★★☆ | as Neo4j ★★☆ | inside the Postgres dump we already take ★★★ |
| Memory | ~600 MB after the 2026-08-13 sizing ★★☆ | as Neo4j ★★☆ | shares Postgres, effectively free ★★★ |
| Extra moving part | one container ★★☆ | container **plus** a GPL plugin ★☆☆ | a custom Postgres image with the extension ★★☆ |
| How agents reach it | HTTP straight from Flowise ★★★ | as Neo4j ★★★ | needs an endpoint — Flowise cannot speak SQL ★☆☆ |
| Password held in Flowise | yes ★☆☆ | yes ★☆☆ | no, if the endpoint authenticates ★★★ |
| Provenance and lifetime | Neo4j Inc., 5.26 LTS to June 2028 ★★★ | single sponsor, tracks 5.26.27 while we pin 5.26.28, no GitHub releases ★☆☆ | Apache Software Foundation ★★★ |
| Work from here | none; drop the synthetic key ★★★ | migrate the graph, pin a matching patch pair ★★☆ | new image, endpoint, rewrite every query and both agent nodes ★☆☆ |


The three options differ most in the row about the course boundary. Neo4j as
deployed today costs nothing and provides the weakest one. DozerDB provides a
physical boundary and adds a dependency this project would carry alone. AGE
provides the boundary, database roles and a single backup covering everything,
at the cost of a one-time migration; its weakest row is the one about reaching
it from Flowise, and that same row is what removes a password from Flowise.

**The deciding question.** Graph algorithms are not the criterion. At a few
hundred concepts per course, prerequisite chains, bottlenecks and clustering
all run in milliseconds in Python against data fetched from anywhere. The
question is whether the graph will one day span learners and interactions
across courses and years, growing with usage rather than with the number of
courses. In that case traversal depth begins to matter and Neo4j is the better
fit; until then it costs about 1.5 GB of RAM on a machine whose documented
minimum is 8 GB.

**What can be done before deciding.** Normalise the concept vocabulary.
Learner data already references concepts as free text —
`concepts_mentioned` on chat messages, `concepts_struggling` in UserMemory,
`gaps` in TestResults — and none of it can be counted against the graph
because "Cognitive Load", "cognitive load theory" and "Kognitive Belastung"
are three different strings. That work is useful under any of the three
options, and it is not a data-protection question, because it concerns course
vocabulary rather than people.

---

## 7 · Flowise's code sandbox has no `process`

**Decision.** Custom-function nodes in the agent templates read secrets as
`$vars?.NAME || 'default'`, never `process.env`.

**Evidence.** `createCodeExecutionSandbox` in flowise@3.1.3
(`packages/components/src/utils.ts`) sets `process`, `util`, `Symbol`,
`child_process` and `fs` to `undefined`. `$vars` is populated from Flowise
Variables, which the import creates.

**Observed failure.** `$vars?.X || process.env.X || 'default'` throws
`TypeError: Cannot read properties of undefined (reading 'env')` as soon as
`X` is empty, so the default written immediately after it is never reached.
`EMBEDDING_BASE_URL` is empty on every standard provider, so this occurred on
the first message of every stock install. The error names the property being
read rather than the missing object, so the message does not point at the
cause.

---

## 8 · n8n's workflows are imported by CLI, not the API

**Decision.** `deploy-n8n-workflows.sh` uses `docker exec … n8n
import:credentials` / `import:workflow`.

**Rationale.** n8n's REST API requires an API key that exists only once
someone creates it in the UI, which an installer cannot do. The CLI has no
such requirement, and `import:credentials` accepts plain JSON, encrypting it
with the instance's own `N8N_ENCRYPTION_KEY`.

**Remaining manual step.** The CLI needs an n8n owner account to assign
imported objects to, and that account can only be created in a browser. This
is why the installer waits at `run_n8n_import_guided()`.

---

## 9 · n8n returns 404 both for a missing webhook and for a wrong method

**Decision.** `n8n_webhook_state()` probes with GET and treats
`"not registered for GET requests"` as success.

**Evidence.** n8n answers 404 when a webhook is missing and when it exists but
was called with the wrong method, with different messages. Verified in
`packages/cli/src/errors/response-errors/webhook-not-found.error.ts` at
n8n@1.123.0. GET is used because a POST would start a real ingest run.

**Failure mode.** Treating any 404 as failure reports a working system as
broken.

---

## 10 · Distinct exit codes for skipped and unverified phases

**Decision.** `EXIT_SKIPPED` (10) and `EXIT_UNVERIFIED` (11) in `common.sh`. A
phase that could not run yet, and one that ran but could not confirm its
result, are distinct from both success and failure.

**Rationale.** The installer used to exit 0 when the n8n import stepped aside
for a missing owner account, and then printed the same completion banner as a
finished install. The first sign of trouble was a 404 in the GUI, hours later
and unconnected to the cause.

**Failure modes.** Collapsing these codes back into 0 restores a success
banner over an unfinished install. Turning them into failures aborts a working
installation because a container was slow to restart.

---

## 11 · Success is reported only after verification

**Decision.** `deploy-n8n-workflows.sh` verifies the webhook is registered
before printing its success line. The GUI's System status page asks each
service directly rather than reading a stored flag.

**Rationale.** Every command in the import can report success while the
webhook is dead: activation is keyed by fixed workflow ids, and an id that
does not match what the import created leaves the workflow inactive without
anything failing.

**Observed failure.** This occurred on a live installation, where the output
contained both "could not be verified" and "documents can now be uploaded",
two lines apart.

---

## 12 · `.env` is written once; a live schema is never rewritten

**Decision.** The wizard generates `.env` on first run and does not regenerate
it. `deploy-schemas.sh` skips Weaviate classes that already exist. Upgrades
that need more go through `sudo smartrag` → *Upgrade*.

**Rationale.** Neither should overwrite a value in a running installation
without being asked to.

**Cost.** An installer that regenerates `.env` can destroy a working
configuration on a re-run. The cost of not regenerating it is that upgrades
occasionally need an explicit step, which is what the Upgrade entry is for,
and why new `.env` keys have to be added to `_default_for_env_key()` in
`admin.sh` at the same time as to `.env.example`.

---

## 13 · Every operator-facing string exists in both languages

**Decision.** `scripts/lib/messages.sh` for the shell, `content-admin/i18n.py`
for the GUI, English as the fallback. `check_translations()` in each.

**Rationale.** The intended users are German-speaking teaching staff, and the
system is meant to be deployable elsewhere.

**What the tests catch.** An English-only string, and a translation with a
different *number* of format placeholders. A German string once had its two
`%s` in the opposite order, so a migration announced a course id where a URL
belonged. Order cannot be checked mechanically; count can, and that catches
most cases.

---

## 14 · Images are pinned to exact versions

**Decision.** Every image in `docker-compose.yml` carries a specific tag, with
no `:latest`. Python dependencies are pinned exactly.

**Rationale.** An upstream change during term in a teaching system is not
recoverable on a useful timescale.

**Known consequence, since resolved.** `minio/minio` was archived upstream
(confirmed via the GitHub API: `archived: true`, last push 2026-04-24) with
`RELEASE.2025-09-07T16-13-09Z` as its final image. That decision has since
been made — see 16.

---

## 15 · Tests print a summary sentence

**Decision.** `tests/` are plain scripts. Each collects failures rather than
stopping at the first, and prints one sentence stating what it verified.

**Rationale.** That sentence is readable by someone who was not present when
the bug was found.

**Notes.** Moving to pytest is reasonable if this grows; the summaries are
worth keeping in some form, as is the practice of asserting on the mechanism
rather than the symptom and checking that a test fails against the bug before
it is trusted. One test here covered nothing for a period, because it grepped
a function with a fixed context window that no longer reached the lines it was
meant to check.

---

## 16 · Object storage: Garage

**Decision.** Object storage is Garage (`dxflrs/garage`, pinned). It holds
uploaded course documents and Langfuse's blobs. Flowise does not use it; its
files are local.

**Rationale.** MinIO was archived eight days after its last release. Nothing
broke, but nothing would be fixed either, and a successor was chosen on a test
machine rather than during an incident.

**Evidence.** The candidate was assessed by running it rather than by reading
its documentation. Every S3 operation this stack performs was executed against
a real Garage — put, get with a byte comparison, list, head, a 32 MB multipart
round trip, a presigned URL fetched with plain curl and no credentials,
recursive delete — and Langfuse's own client was then pointed at it and wrote
objects. That last step was the most informative, since Langfuse does not list
Garage as supported and its key layout and multipart behaviour are not
answered by a server-side feature table. `scripts/spike-garage.sh` is that
evaluation, kept so the next candidate can be judged the same way.

**Three differences that shape the code.**

*Garage stores nothing until a layout assigns capacity.* A Garage with no
layout is healthy, accepts connections, and refuses every write.
`deploy-garage.sh` therefore applies the layout before creating anything, and
reads the pending layout's version rather than passing `1`: a hard-coded `1`
succeeds exactly once and fails for the rest of the installation's life.

*Its image is `FROM scratch`.* There is no shell, so provisioning from the
container's own entrypoint, as MinIO did, is not possible. Provisioning runs
the binary directly through `docker exec`, from outside.

*There is no root user and there are no bucket policies.* Permissions are per
key and per bucket. Two keys exist: one for the ingest's document bucket, one
for Langfuse's three. The separation MinIO had by convention is enforced here.

**Credentials stay with the installer.** `garage key import` accepts an id and
a secret, so the wizard generates them as before and Garage adopts them;
`.env` remains the source of truth. They are generated in Garage's own shapes
— `GK` + 24 hex, 64 hex — rather than relying on how strictly it validates.

**What was given up.** The web console. Garage has none and nothing replaces
it; the nginx vhost, the Tailscale port, the certificate and the DNS check for
it are gone. Buckets and keys are provisioned by the installer, so there is
nothing routine to click.

**Reverting.** The Langfuse settings need care: they reach the container
through `env_file`, which does not interpolate, so every one has to be written
resolved. They were not, for the whole MinIO era. Langfuse authenticated with
the literal string `${MINIO_LANGFUSE_SECRET_KEY}`, which went unnoticed
because tracing was switched off and nothing ever wrote.
