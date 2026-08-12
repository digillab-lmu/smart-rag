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
reinstalled. `n8n/workflows/` was not deployed at all when this was written;
that is Phase 0 below, and it is done.

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

## Phase 1 · The data layer (completed 2026-08-11)

Database `smartrag` in `postgres-init`, `psycopg[binary]` in the Content
Admin, a `db.py` with a connection pool, and a small versioned migration
runner (`schema_version` plus numbered steps). The migration runner is not
scaffolding for this feature — from here on the schema changes over the life
of an installation, and hand-edited tables are how two deployments stop being
the same software.

Tables: `courses`, `users`, `user_courses`, `agent_slots`.

**Proven** against the installation's own Postgres: migrating twice applies
nothing the second time, three simultaneous migrations record each step
exactly once, every schema constraint refuses what it should, and four
concurrent writers all keep their values — the case JSON loses silently.

The database is called `contentadmin`, not `smartrag` as this plan first
said: that name is `POSTGRES_DB` and already belongs to Langfuse.

## Phase 2 · A course becomes an object (completed 2026-08-11)

Create and list courses. Creating one has real side effects: a Weaviate
collection from the `__COLLECTION_NAME__` template, a Garage bucket with a
grant, ten slot rows.

The hard part is **partial failure** — collection created, bucket not. Side
effects first, then the record, and on failure a message naming what already
exists. A half-created course that looks whole is worse than a failed
creation.

**Proven** twice over. The four failure paths — bucket fails after the
collection, grant fails, grant reports success and does nothing, second
attempt on a half-made course — against stubs that can fail on demand. Then
live: two courses created from the GUI produced `Chunks_mathe_1` and
`Chunks_chemie_1` in Weaviate, `mathe-1-rag` and `chemie-1-rag` in Garage,
and `RWO` for the ingest key on both. That last run was the first real
exercise of Garage's admin API, which until then had only been derived from
its OpenAPI document.

**Decided against the plan.** This section said side effects first, then the
record. The record goes first, with `provisioned_at` NULL until the rest is
done: written last, a crash between the collection and the bucket leaves a
collection nobody has a record of, and the next attempt either collides with
it or adopts it silently.

## Phase 3 · Slots and agents per course (completed 2026-08-11)

Every slot route carries a course. Import substitutes *that course's*
collection and id. Chatflow names carry the course, because Flowise's names
are global and `find_chatflow_by_name` would otherwise match another course's
agent — a wrong-course match that looks like a successful import.

**Proven by** two courses with an identically named agent, both imported: two
distinct chatflows, each carrying its own collection and filtering on its own
course id, and neither carrying the other's. Confirmed live on 2026-08-11 —
`SMART RAG — mathe-1 — Tutor` and `SMART RAG — chemie-1 — Tutor`, different
chatflow ids, each holding only its own collection.

**What it cost.** Nine test suites exercise slots through app.py and now need
a database, so a local Postgres 17 — the same major version the deployment
runs — became part of the development setup; without one those suites report
"could not run" rather than passing. The schema also caught an unrealistic
fake: a stubbed Flowise that returned one chatflow id for every slot, which
the unique index refuses, and rightly — two slots on one chatflow means each
import silently overwrites the other.

## Phase 4 · Ingest per course (completed 2026-08-11)

The upload carries the course; the workflow takes `course_id`, the collection
and the bucket **from the request** instead of `$env`. Progress rows carry the
course.

**Proven** on the live system: a document uploaded into mathe-1 produced 23
chunks in `Chunks_mathe_1` and none in `Chunks_chemie_1`, the chunks carry
`course_id: mathe-1` — the value every agent filters on — and the archived
copy went to `mathe-1-rag`. `Testkurs2Chunks` stayed at 113, so nothing
reaches the original course any more.

Getting there cost three misdiagnoses worth remembering. n8n reported "may
have run out of memory"; Docling's log showed two clean conversions and the
machine had 7.9 GB free. The execution table gave the real answer: the
workflow import had restarted n8n eleven seconds into the run. The deployer
now waits for running executions instead of cutting them off.

**This phase is the point of no return.** After it the ingest is
course-bound, and going back costs a re-embed rather than a revert.

## Phase 5 · Users and authorisation (completed 2026-08-12)

Two roles, n:m assignment, a course switcher. Authorisation at **one** choke
point — a `before_request` that resolves the active course and asserts
membership. Checked in fifteen places is forgotten in one, and the omission is
invisible until someone reads another course's material.

**Proven by** negative tests generated from Flask's own route table, so a
route added without protection fails the suite rather than being noticed in a
course. Four mutations were run against it: a route with the decorator
removed, an authorisation function that always says yes, a session cookie
that survives a withdrawn assignment, and a demotable last administrator.

Confirmed live on 2026-08-12: a maintainer assigned only to mathe-1 cannot
see chemie-1 — not in the list, not in the switcher.

Two things the writing of it changed. The first attempt recognised the
decorators by name, which `functools.wraps` copies from the view — it would
have passed for an undecorated route, so the decorators now set an explicit
marker. And `flowise-setup` moved from any logged-in user to administrators
only: the API key it stores is installation-wide, not a course's.

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

## Phase 7a · One vocabulary for concepts

Learner data already points at concepts, in three places and as free text:
`concepts_mentioned` on every chat message, `concepts_struggling` and
`concepts_mastered` in UserMemory, `gaps` and `strengths` in TestResults.
None of it can be counted against the concept map, because the strings do not
match the nodes — and nothing notices, because there is no join to fail.

Normalising that is the prerequisite for every analytical question worth
asking of this system: which concept is the bottleneck for a cohort, which
prerequisite the map claims and the data denies, what a given learner should
do next. It is also independent of where the graph is stored (ARCHITECTURE
6d), so it can be built before that decision rather than after it.

**Not a data-protection question.** This is course vocabulary, not people.
Linking learners to concepts as durable data is, and belongs with the LTI
question that is still with the data protection officer.

**Proven by** an extraction that resolves to a concept in the map, or to
nothing, with the "nothing" counted and visible — a silent 40% miss rate
would make every number computed on top of it wrong in the same direction.

## Phase 8 · Moving the installation to another server

Asked for while phase 4 was being proven: this deployment will move to
different hardware within a year or so. Also the missing backup story — the
two are one problem, because a restore onto a second machine *is* a move.

**The state, in full.** Everything that matters is in two places:
`BASE_DATA_PATH` (postgres, weaviate, garage, neo4j, clickhouse, redis, n8n,
flowise, langfuse, content-admin) and `.env`. Nothing else on the host is
irreplaceable — nginx config and certificates are regenerated, images are
pinned and pulled.

That is why this is not hard. Three things make it non-trivial anyway, and
none of them is the copying:

  * **`.env` is not optional and not separable.** Postgres, Neo4j and
    ClickHouse read their password once, when their data directory is
    created. A data directory restored without its `.env` is unreadable, and
    the two must therefore travel together or neither. The same applies to
    `N8N_ENCRYPTION_KEY`: without it every stored credential in n8n is
    undecryptable ciphertext.
  * **A copy taken while the services run is a torn copy.** Postgres and
    ClickHouse must be stopped, or dumped with their own tools. Stopping is
    simpler and this system tolerates downtime.
  * **The address is baked in.** `.env` holds the domain or the MagicDNS
    name in a dozen values, published chat URLs point at it, and TLS
    certificates are issued for it. Moving to a machine with a different
    address is a rename, not a copy — and the rename is the only genuinely
    fiddly part.

**Shape.** Two commands, `smartrag backup` and `smartrag restore`, with the
restore refusing rather than guessing:

  * refuse a restore onto a different Postgres major version, because a data
    directory is not portable across them;
  * refuse when the archive's `.env` and the data directories disagree about
    which installation they belong to;
  * say plainly when the target's address differs from the archive's, and
    offer the rename as a deliberate step with the list of what it will
    rewrite — never silently.

**Garage needs its own step.** Its layout carries a node id. A copied data
directory may or may not bring a usable one to a new host; that has to be
established against the real thing before the restore claims to work, the
way the layout requirement itself was. If it does not, the fallback is an
S3-level copy into a freshly laid-out cluster, which is slower and known to
work.

**Proven by** a restore onto a second machine, followed by the checks this
project already trusts: chunk counts per course, an object count per bucket,
one trace through Langfuse, and one agent answering with a citation. A
restore that starts every container is not a restore that works.

**Out of scope until then:** nothing about this depends on the multi-course
work, so it can be built before or after phase 5–7. Doing it *before* the
reinstall in phase 7 would be convenient — it turns that reinstall into the
first real test of the restore path.

## Phase 9 · Documentation and 2.0

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

*The course stamping in `n8n/workflows/` is now live rather than dormant.*
Those workflows run on a schedule and take the course from `$env.COURSE_ID`,
which is correct while there is one course and silently wrong the moment there
are two. Phase 4/5 must convert them, and until then no second course may
exist on a deployment that runs them — which is exactly the ordering this plan
already imposes.
