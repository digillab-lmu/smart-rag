# Build plan: several courses on one installation

Companion to [ARCHITECTURE.md](ARCHITECTURE.md) decisions 6a, 6b and 6c —
those say *what* and *why*, this says *in what order* and *how each step is
proven*. It is written to be thrown away once the work is done.

The ordering rule throughout: **every phase ends with a system that runs.**
Not a system with the feature finished, but one that can be deployed, used
and rolled back to. Where that is impossible it is said so.

---

## What one course is wired into today

Established by reading the tree on 2026-08-11, not from memory:

| Place | What assumes a single course |
| --- | --- |
| `.env` | `COURSE_ID`, `COURSE_NAME`, `WEAVIATE_COLLECTION_NAME` — installation-wide |
| `weaviate/schema.json` | five classes; four shared ones carrying `course_id`, one chunk class `__COLLECTION_NAME__` |
| `content-admin/storage.py` | ten agent slots in one JSON file, with no course at all |
| Garage | one bucket, `${COURSE_ID}-rag` |
| `agent_templates.py` | `COURSE_ID` and `WEAVIATE_COLLECTION_NAME` substituted at import |
| `workflows-ingest/*.json` | `$env.COURSE_ID`, `$env.WEAVIATE_COLLECTION_NAME` |
| `content-admin/app.py` | 17 routes, none course-aware; exactly one account |
| `docker/postgres-init/` | runs **only** on first initialisation of the data directory |

Two of these decide the shape of the work. The last one means a new database
cannot appear on an existing installation without manual work — consistent
with 6c's no-migration decision, and the reason our own test machine has to be
reinstalled. And `n8n/workflows/` is not deployed at all (the deployer reads
`workflows-ingest/` only), which is why its course stamping is out of scope.

---

## Phase 0 · The core workflows, done first (completed 2026-08-11)

`chathistory-sync`, `usermemory-summary` and `langfuse-userid-patch` are now
deployed by `deploy-n8n-workflows.sh` rather than being present and dead. They
had to be finished before the course work, because converting workflows that
have never run means guessing at what conversion breaks.

What finishing them required, none of it visible from the outside: fixed
workflow ids (without one, every import leaves another copy, and a duplicate
five-minute schedule runs twice); a Postgres credential and a Langfuse basic-
auth credential the deployer never created; replacing an Anthropic node with a
hard-coded model by the provider-agnostic call the rest of the project uses;
removing a `{{COURSE_NAME}}` from a prompt, which n8n does not substitute;
taking Langfuse's port from the environment; and parameterising a SQL query
that pasted a value from Langfuse into its own text.

The remaining work for these is Phase 4/5: they take the course from the
chatflow instead of `$env.COURSE_ID`.

**Personal data.** `langfuse-userid-patch` parses the LTI session id and
writes the learner's name into Langfuse. It ships only with the
`observability` profile and does nothing without the LTI middleware, but where
LTI is in use the legal basis for identifying learners has to be settled
first. That is recorded in the workflow README and is not a technical
question.

## Phase 1 · The data layer

Database `smartrag` in `postgres-init`, `psycopg[binary]` in the Content
Admin, a `db.py` with a connection pool, and a small versioned migration
runner (`schema_version` plus numbered steps). The migration runner is not
scaffolding for this feature — from here on the schema changes over the life
of an installation, and hand-edited tables are how two deployments stop being
the same software.

Tables: `courses`, `users`, `user_courses`, `agent_slots`.

**Proven by** tests against a real Postgres rather than a mock, including two
writers at once — concurrency is the reason for leaving JSON, so a test suite
that never runs two writers has not tested the decision. The system behaves
exactly as before at the end of this phase; nothing reads the new tables yet.

## Phase 2 · A course becomes an object

Create and list courses. Creating one has real side effects: a Weaviate
collection from the `__COLLECTION_NAME__` template, a Garage bucket with a
grant, ten slot rows.

The hard part is **partial failure** — collection created, bucket not. Side
effects first, then the record, and on failure a message naming what already
exists. A half-created course that looks whole is worse than a failed
creation.

**Proven by** creating two courses and checking both collections and both
buckets on the live system; then creating one with Garage stopped, and
checking that nothing is left behind that the next attempt will trip over.

## Phase 3 · Slots and agents per course

Every slot route carries a course. Import substitutes *that course's*
collection and id. Chatflow names carry the course, because Flowise's names
are global and `find_chatflow_by_name` would otherwise match another course's
agent — a wrong-course match that looks like a successful import.

**Proven by** two courses with an identically named agent, both imported: two
distinct chatflows in Flowise, each querying its own collection.

## Phase 4 · Ingest per course

The upload carries the course; the workflow takes `course_id`, the collection
and the bucket **from the request** instead of `$env`. Progress rows carry the
course.

**Proven by** the test this whole feature exists for: ingest into course A,
ask in course B, and get nothing — and then the same query in course A, so
that "nothing" is not simply everything being broken.

**This phase is the point of no return.** After it the ingest is
course-bound, and going back costs a re-embed rather than a revert.

## Phase 5 · Users and authorisation

Two roles, n:m assignment, a course switcher. Authorisation at **one** choke
point — a `before_request` that resolves the active course and asserts
membership. Checked in fifteen places is forgotten in one, and the omission is
invisible until someone reads another course's material.

**Proven by** negative tests generated from the route table, so a new route
added without protection fails the suite rather than being noticed later.

## Phase 6 · Deleting a course

Inventory first ("3 agents, 78 chunks, 12 objects, 431 chat messages"), then
the question about learner data with no pre-selected answer, then execution.

**Proven by** deleting one course and measuring the other separately —
collection, bucket, slots and chunk count — rather than assuming isolation
that has never been observed.

## Phase 7 · The installer

Bootstrap stops asking for a course and asks for a name for the
*installation*; it deploys only the shared schema. The first account is the
installation administrator. Hand-over message, `smartrag` menu and uninstall
follow.

**Proven by** a full reinstall on the test machine, then phases 2–6 run
through as one sequence by hand.

## Phase 8 · Documentation and 2.0

Runbook entries for the new failure shapes — a course whose collection is
missing, an account with no course — the operations guide, the changelog, the
release.

---

## Decided without asking

**One Garage key for all course buckets**, granted per bucket, rather than a
key per course. n8n is a single trusted service; per-course keys would mean
key management at runtime for no gain. The separation that matters —  ingest
versus Langfuse — is unchanged.

**A bucket per course**, not one bucket with prefixes, so deleting a course is
a bucket operation rather than a scan.

**`course_id` stays on the chunks** as a second boundary and for provenance,
as decision 6a already settled.

## Known risks

*No migration* means an existing 1.0 installation loses its data on upgrade
unless its operator backs up first. The refusal guard turns that into a stop
instead of a loss; it cannot turn it into a migration.

*Phase 4 is irreversible* in practice. Everything before it can be reverted by
deploying the previous version.

*The dormant coupling* in `n8n/workflows/` stays dormant. If those workflows
are ever deployed, their course stamping must be built first — they would
otherwise file every learner's history under whichever course `.env` happens
to name.
