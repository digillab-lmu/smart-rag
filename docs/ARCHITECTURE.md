# Architecture decisions

Why this system is built the way it is. Each entry says what was decided,
why, and — the part that is usually missing — what goes wrong if someone
changes it back.

These are load-bearing. Most were arrived at by being wrong first, and
several are counterintuitive enough that a reasonable person would undo
them. Where a decision rests on someone else's source, the file and version
are named so it can be re-checked rather than taken on trust.

For how to *operate* the system, see [`operations-guide.md`](operations-guide.md).
For what breaks and what to do about it, see [`RUNBOOK.md`](RUNBOOK.md).

---

## 1 · Two applications, one boundary

**Decision.** `scripts/admin.sh` (a TUI on the host, as root) does
infrastructure. `content-admin/` (a web GUI in a container) does course
content. The GUI has no Docker socket and no host filesystem beyond a single
bind-mounted `.env`; it reaches other services over HTTP on the internal
Docker network only.

**Why.** The GUI is exposed to the public internet and logged into by
teaching staff. A web interface that could exec into containers would be a
far larger target than one that can only make HTTP calls, and the people
using it have no reason to need that power.

**If you change it.** The obvious temptation is to let the GUI run
`deploy-n8n-workflows.sh` so the operator doesn't have to. Resist it. The
GUI already handles this correctly: it *shows* the step and offers a
message to forward to whoever administers the server.

---

## 2 · Public URLs are resolved in `.env`, never assembled in Compose

**Decision.** Any URL containing a subdomain is computed by
`scripts/lib/templates.sh` through `subdomain_host()` and written to `.env`
fully resolved. `docker-compose.yml` only ever reads the finished value.

**Why.** `SUBDOMAIN_PREFIX` is set when the plain names collide with
something already on the host, and Compose interpolation cannot express
"prefix, but only if there is one".

**If you change it.** Writing `https://s3.${DOMAIN}` in the compose file
silently drops the prefix. On a prefixed installation that points at a
hostname with no DNS record, no vhost and no certificate — and the symptom
appears somewhere else entirely, as a service that will not log in.
`tests/test_public_urls.sh` refuses any compose value that puts something in
front of `${DOMAIN}`.

---

## 3 · Host ports and container ports are different things

**Decision.** `N8N_PORT`, `WEAVIATE_HTTP_PORT` and friends in `.env` are
**host-side** bindings, which the wizard may move on a conflict. Every
container-to-container URL hardcodes the container's own port: n8n 5678,
Weaviate 8080, Flowise 3000, Docling 5001, markdowncleaner 8000.

**Why.** Only the host binding can move. Inside the network the port is
fixed by the image.

**If you change it.** `MINIO_NOTIFY_WEBHOOK_ENDPOINT` once used
`${N8N_PORT}` for an internal URL. On a deployment where the wizard had
resolved n8n to 5778, MinIO logged `connect: connection refused` against
`smartrag-n8n:5778` on every object written.

---

## 4 · Flowise credentials live under `FLOWISE_CREDENTIAL_ID`

**Decision.** `agent_templates.set_credential_ids()` writes the credential
id to `FLOWISE_CREDENTIAL_ID` in every node config block, and keeps the
`credential` key in sync.

**Why.** That is the key Flowise's runtime reads. Verified in flowise@3.1.3:
`Agent.ts:909` and `LLM.ts:376` (`credential: modelConfig['FLOWISE_CREDENTIAL_ID']`),
plus `Agent.ts:830` and `:845` for embeddings and the vector store. The
`credential` key the templates ship with is what the canvas shows; the
runtime ignores it.

**If you change it.** Every agent answers `Missing credentials. Please pass
an apiKey, or set the OPENAI_API_KEY environment variable` on the first
message — regardless of the configured provider, because with no credential
attached the node falls back to the provider SDK's own environment variable.
That misleading provider name is what made it expensive to diagnose.

---

## 5 · Two Weaviate filter formats, deliberately not unified

**Decision.** Agent retrieval filters (in the templates) use the gRPC
TypeScript client's shape. Deletion (in `weaviate_client.py`) uses the REST
API's classic `where` filter. They look similar and are not compatible.

| | Retrieval (agents) | Deletion (REST) |
|---|---|---|
| combine | `filters: [...]` | `operands: [...]` |
| property | `target: {property: "x"}` | `path: ["x"]` |
| value | one untyped `value` | typed: `valueText`, `valueInt` |

**Why.** They are different APIs. Flowise's node speaks gRPC through
`@langchain/weaviate`; batch deletion has no gRPC equivalent we use.
Verified in `weaviate/typescript-client`
(`src/collections/filters/{types,classes}.ts`) and `weaviate/weaviate`
(`openapi-specs/schema.json`).

**If you change it.** Using the wrong shape is *accepted* by the API and
matches nothing. A retrieval returns no results; a deletion deletes nothing
and reports success. Both tests assert the vocabulary of their own side and
that the other side's terms do not appear.

Related: the JSON type of a filter value decides its wire encoding.
`agent_id` is an `int` property, so its placeholder in the templates is
deliberately **unquoted** — quoting it produces `"4"`, encoded as
`valueText`, matching nothing on an int column.

---

## 6 · `course_id` on the data, not a stack per course

**Decision.** One installation can host several courses. Every object the
agents retrieve carries a `course_id` and every agent filters on it. The
Weaviate collection is shared.

**Why.** A full deployment per course multiplies RAM by the number of
courses, and the documented minimum is already 8 GB.

**If you change it.** An agent without a `course_id` condition sees every
course's material. Two archetypes had no filter at all before this and would
have done exactly that. A consequence worth knowing: all courses on one
installation share the embedding model, since a Weaviate collection has one
vector configuration.

---

## 7 · Flowise's code sandbox has no `process`

**Decision.** Custom-function nodes in the agent templates read secrets as
`$vars?.NAME || 'default'` — never `process.env`.

**Why.** `createCodeExecutionSandbox` in flowise@3.1.3
(`packages/components/src/utils.ts`) explicitly sets `process`, `util`,
`Symbol`, `child_process` and `fs` to `undefined`. `$vars` is populated from
Flowise Variables, which the import creates.

**If you change it.** `$vars?.X || process.env.X || 'default'` throws
`TypeError: Cannot read properties of undefined (reading 'env')` the moment
`X` is empty — the *default* written right after it is never reached. And
`EMBEDDING_BASE_URL` is empty on every standard provider, so this fired on
the first message of every stock install. The error names the property being
read rather than the missing object, which is why it reads as a mystery.

---

## 8 · n8n's workflows are imported by CLI, not the API

**Decision.** `deploy-n8n-workflows.sh` uses `docker exec … n8n
import:credentials` / `import:workflow`.

**Why.** n8n's REST API needs an API key that only exists once a human
creates one in the UI. The CLI has no such requirement, and
`import:credentials` accepts plain JSON, encrypting it with the instance's
own `N8N_ENCRYPTION_KEY`.

**If you change it.** You reintroduce a chicken-and-egg the installer cannot
resolve. Note the remaining manual step: the CLI needs an n8n *owner*
account to assign imported objects to, and that can only be created in a
browser — hence the guided wait in `run_n8n_import_guided()`.

---

## 9 · A 404 from n8n can mean the webhook is *fine*

**Decision.** `n8n_webhook_state()` probes with GET and treats
`"not registered for GET requests"` as **success**.

**Why.** n8n answers 404 both when a webhook is missing and when it exists
but was asked with the wrong method — with different messages. Verified in
`packages/cli/src/errors/response-errors/webhook-not-found.error.ts` at
n8n@1.123.0. GET is used because a POST would start a real ingest run.

**If you change it.** Treating any 404 as failure reports a working system
as broken.

---

## 10 · "Skipped" and "unverified" are not "done" — and not "failed"

**Decision.** `EXIT_SKIPPED` (10) and `EXIT_UNVERIFIED` (11) in
`common.sh`. A phase that could not run yet, and one that ran but could not
confirm its result, are distinct from both success and failure.

**Why.** The installer used to exit 0 when the n8n import stepped aside for
a missing owner account, and then printed the same completion banner as a
finished install. The first sign of trouble was a 404 in the GUI, hours
later and nowhere near the cause.

**If you change it.** Anything that collapses these back into 0 restores a
success banner over an unfinished install. Conversely, making them failures
aborts a perfectly good installation because a container was slow to
restart.

---

## 11 · Nothing reports success it has not checked

**Decision.** `deploy-n8n-workflows.sh` verifies the webhook is registered
before printing its success line. The GUI's System status page asks each
service directly rather than reading a stored flag.

**Why.** Every command in the import can report success while the webhook
is dead — activation is keyed by fixed workflow ids, and an id that doesn't
match what the import created leaves the workflow inactive with nothing
failing.

**If you change it.** This has already happened once in the field, and the
message it produced said both "could not be verified" and "documents can now
be uploaded", two lines apart.

---

## 12 · `.env` is written once; a live schema is never rewritten

**Decision.** The wizard generates `.env` on first run and does not
regenerate it. `deploy-schemas.sh` skips Weaviate classes that already
exist. Upgrades that need more go through `sudo smartrag` → *Upgrade*.

**Why.** Neither should silently overwrite something in production.

**If you change it.** You get an installer that can destroy a working
configuration on a re-run. The cost of keeping it is that upgrades
occasionally need an explicit step — which is what the Upgrade entry is
for, and why new `.env` keys must be added to `_default_for_env_key()` in
`admin.sh` at the same time as `.env.example`.

---

## 13 · Every operator-facing string exists in both languages

**Decision.** `scripts/lib/messages.sh` for the shell, `content-admin/i18n.py`
for the GUI, English as the fallback. `check_translations()` in each.

**Why.** The intended users are German-speaking teaching staff, and the
system is meant to be deployable elsewhere.

**If you change it.** Adding an English-only string is caught by the tests.
So is a translation with a different *number* of format placeholders — a
German string once had its two `%s` in the opposite order, so a migration
announced a course id where a URL belonged. Order cannot be checked
mechanically; count can, and it catches most of it.

---

## 14 · Images are pinned to exact versions

**Decision.** Every image in `docker-compose.yml` carries a specific tag —
no `:latest`. Python dependencies are pinned exactly.

**Why.** A silent upstream change in a teaching system during term is not
recoverable on a useful timescale.

**Known consequence.** `minio/minio` is archived upstream (confirmed via the
GitHub API: `archived: true`, last push 2026-04-24) and
`RELEASE.2025-09-07T16-13-09Z` is the last image ever published to Docker
Hub. quay.io carries hotfix rebuilds of that same release. A migration to
Garage or SeaweedFS is a known future decision, not an emergency — but it is
a decision someone will have to make.

---

## 15 · Tests print a sentence, not a dot

**Decision.** `tests/` are plain scripts. Each collects failures rather than
stopping at the first, and prints one sentence saying what it verified.

**Why.** That sentence is readable by someone who was not there when the bug
was found. A row of green dots is not.

**If you change it.** Moving to pytest is a reasonable step if this grows —
keep the summaries in some form. And keep the discipline the README records:
assert on the mechanism rather than the symptom, and prove the test fails
against the bug before trusting it. One test here silently covered nothing
for a while because it grepped a function with a fixed context window that
stopped reaching the lines it was meant to check.
