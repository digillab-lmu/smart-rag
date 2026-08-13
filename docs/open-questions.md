# Questions to answer before the next phase (2026-08-13)

Companion to [plan-multicourse.md](plan-multicourse.md). Phases 0–5 are built
and proven; what remains was planned before three things were known, and they
change what is worth building:

1. **This installation will be reinstalled** (phase 7). Anything whose only
   job is to clean up state from the single-course era is work that the
   reinstall does for free. One such rule was written today and taken back
   out again — see the note at the end of the RUNBOOK's learning-record
   entry.
2. **A workflow failed on every scheduled run for weeks and nobody saw it.**
   Ten red executions of `usermemory-summary`. Nothing in this system says a
   background job has stopped working, and two of the three background jobs
   are the ones that keep learner data current.
3. **An agent in Flowise is a copy, not a reference.** Changing the JSON in
   this repository has no effect until every agent of every course is
   re-imported by hand, and nothing shows which agents are stale. That cost
   two rounds of confusion today, on an installation with two agents.

Each question below says what is known, what depends on the answer, and — where
there is one — a recommendation. Facts that were looked up rather than
remembered carry their source.

---

## 1 · Where the concept graph lives

The open decision from [ARCHITECTURE.md 6d](ARCHITECTURE.md). What is built
today: one Neo4j Community instance, the graph separated per course by a
`course_id` property, with `(course, name)` folded into a synthetic `key`
because a composite constraint was believed to be unavailable. The boundary is
therefore convention plus a test, while every other boundary in this system is
physical.

**Researched for this list:**

* Neo4j **5.26 LTS is supported until June 2028**; mainline moved to calendar
  versioning (2025.01 onwards) in January 2025. This installation pins
  `neo4j:5.26.28`, so the LTS line is a safe place to stand for two more years
  ([endoflife.date/neo4j](https://endoflife.date/neo4j),
  [Neo4j: v5 long-term support](https://neo4j.com/blog/developer/neo4j-v5-lts-evolution/)).
* **DozerDB** is at **v5.26.27.0, released 2026-06-12**, GPL, adding
  multi-database and schema constraints (property existence and uniqueness) to
  Community; the site does not claim role-based access control, and support is
  offered commercially by Greystones Group ([dozerdb.org](https://dozerdb.org/)).
  Two things to weigh: it targets **5.26.27** while this installation already
  pins **5.26.28**, which is exactly the "pinned to the patch release it was
  built for" cost the ADR named; and the plugin's GitHub repository publishes
  **no releases** — artefacts come from the project's own site and Docker Hub
  ([DozerDB/dozerdb-plugin](https://github.com/DozerDB/dozerdb-plugin)).
* **Apache AGE** — a graph extension for the PostgreSQL this system already
  runs — reached **1.6.0 with PostgreSQL 17 support**, added row-level security
  in January 2026, and is working towards PostgreSQL 18
  ([age.apache.org release notes](https://age.apache.org/release-notes/),
  [apache/age discussion #2305](https://github.com/apache/age/discussions/2305)).
  This is a fourth option the ADR does not list. It would need a Postgres image
  with the extension — this installation runs `postgres:17.10-alpine`.
* Neo4j's footprint here is **capped in the compose file**: 1 GB heap plus
  512 MB page cache. The ADR's "about 1.5 GB" is a property of the
  configuration, not an estimate.

**Q1.1 — Will the graph ever hold learner-level data?**
Concepts mastered by whom and when, interactions, cohorts over years. This is
the deciding question the ADR names: a course-level concept map is a few
hundred nodes and runs anywhere; a graph that grows with usage is where Neo4j
earns 1.5 GB. Note this is partly answered elsewhere — see §7.

**Q1.2 — Is a property boundary acceptable for the graph, given the rest of
the system is physical?**
Today it holds because every statement names the course and a test enforces
that. The failure mode is a future query that forgets, and the damage is one
course's prerequisites reaching another course's agents.

**Q1.3 — Composite uniqueness in Community: verify before designing around
it.** Neo4j's documentation blocks automated retrieval, and the synthetic
`key` rests on the belief that a two-property constraint is Enterprise-only. If
5.26 Community accepts

```cypher
CREATE CONSTRAINT test_composite IF NOT EXISTS
FOR (c:Concept) REQUIRE (c.course_id, c.name) IS UNIQUE
```

then the boundary can be strengthened without changing anything else, and the
synthetic key becomes removable. One command against the running instance
settles it. **Recommendation: run it before any other graph decision.**

**Q1.4 — If the boundary must be physical, which one?**
DozerDB gives a database per course at the cost of a GPL plugin from a single
sponsor, pinned to a patch release, with no RBAC. Postgres — plain tables or
Apache AGE — gives a foreign key and deletes the graph with the course, at the
cost of an endpoint, because agents cannot speak SQL from Flowise.

**Q1.5 — Who operates this in two years?**
A university deployment that must run unattended is a different bet on a
single-sponsor plugin than a research prototype. Relevant to Q1.4 only.

**Q1.6 — If Postgres wins, who serves the graph to the agents?**
Today each agent holds the Neo4j password in a Flowise variable and calls its
HTTP API. A read endpoint on the Content Admin would remove that password from
Flowise — a gain on its own — but needs an answer for how the agent
authenticates. **Recommendation: worth doing even if Neo4j stays.**

---

## 2 · Nothing reports a background job that has stopped

`usermemory-summary` failed on every four-hourly run for at least two days.
It was found only because its output was being examined for another reason.
`chathistory-sync` runs every five minutes and is the only thing that moves
learner conversations into the store the agents read; `ingest-document` is
how documents arrive at all.

**Q2.1 — Should the Content Admin show failed executions?**
The data is one query away: n8n writes to the same Postgres, and
`execution_entity` carries a status per run. The system page already shows
service health; "3 of the last 10 runs of ChatHistory Sync failed" is the same
kind of statement.

**Q2.2 — Should a failure send mail?**
SMTP is configured for ingest notifications. A daily digest is less noise than
per-failure mail and would still have caught this in a day rather than a week.

**Q2.3 — Is this before or after the production deployment?**
**Recommendation: before.** It is the cheapest thing on this list and the only
one that shortens every future diagnosis, including the ones nobody has had
yet.

---

## 3 · An imported agent is a copy with no version

Re-importing is required whenever an agent's JSON changes in the repository —
today, twice, for a leak in `Load UserMemory` and then another in
`Load Relevant ChatHistory`. Nothing in the GUI says an agent is behind, and
the only reliable check was a SQL query against Flowise's `chat_flow` table.

**Q3.1 — Should a slot record what it was imported from, and show when that
is behind the repository?**
A hash of the template at import time, stored on the slot, compared against the
file. Cheap, and it turns "did the import take?" from a database query into a
line in the interface.

---

## 4 · What is left over from the single-course era

**Q4.1 — `migrate-add-course-id.sh`: guard it, or retire it?**
It assigns `.env`'s `COURSE_ID` to every object that lacks one. On an
installation with three courses it announces "existing data now belongs to
course testkurs2", which is false. It only ever fills blanks, so it is
currently harmless — but the next object without a course would be stamped
wrongly and silently. The plan already records that **there is no migration
path from 1.0**, which argues for retiring it rather than guarding it.
**Recommendation: retire, unless an existing 1.0 installation is expected to
upgrade in place.**

**Q4.2 — `testkurs2` and `SMART RAG — TestAgent`.**
159 chat messages and 113 chunks belong to a course that has no row in the
courses table, and its agent sits in no slot. Make it a real course, or let the
reinstall take it? **Recommendation: let the reinstall take it**, unless
something in it is needed as a demonstration corpus.

**Q4.3 — Ten stranded learning records.**
Three pairs have a record in `testkurs2` and no conversation there. They are
unreachable for the summary workflow by design (see RUNBOOK). They disappear
with the reinstall. **Recommendation: leave them.**

**Q4.4 — Is anything on the current installation worth keeping across the
reinstall?**
If the answer is "the documents", that is a restore path (§5), not a copy.

---

## 5 · Sequence, given that a reinstall is coming

**Q5.1 — Phase 8 (backup and restore) before phase 7 (installer and
reinstall)?**
The plan already suggests it, and the argument has got stronger: a reinstall is
a restore that is allowed to fail, which makes it the honest first test of the
restore path. **Recommendation: yes, 8 before 7.**

**Q5.2 — When is the production hardware available, and what is on it?**
RAM decides whether Neo4j's 1.5 GB is a question at all (documented minimum is
8 GB). Address decides how much of the "rename" part of phase 8 is exercised.

**Q5.3 — Does production start from a restore of the test machine, or fresh?**
Fresh means the course setup is done again by hand; a restore means phase 8
must be finished and trusted first.

**Q5.4 — Phase 6 (deleting a course) before production?**
A deletion that forgets the graph, the chat history or the learning records is
the same class of bug as the three found today, and it is easier to get right
before there is data anyone minds losing.

---

## 6 · One vocabulary for concepts (phase 7a)

The plan's argument stands: learner data references concepts as free text in
three places, none of it can be counted against the map, and nothing fails
because there is no join to fail.

**Q6.1 — Is the graph the source of truth for the vocabulary, or a separate
list?**
If the graph is, then §1 is a prerequisite after all. If a separate list is,
7a can be built now and the graph decision stays open.

**Q6.2 — Retroactive or only going forward?**
There are 189 chat messages with `concepts_mentioned` on this installation and
a reinstall pending, which makes retroactive normalisation nearly free to skip
here — but not on the production instance a year from now.

**Q6.3 — What happens to an extraction that resolves to nothing?**
The plan says counted and visible. Confirm that a miss is a first-class
outcome, not a silent drop — this is the same failure shape as the four chat
messages that were written with no course.

---

## 7 · Learner data and data protection

**Q7.1 — Where does the data protection question stand?**
The plan records that the LTI question is with the data protection officer.
Durable learner-linked data is exactly what §1's deciding question is about,
and what 7a would make analysable. This gates both.

**Q7.2 — Is there a path to erase one learner's data?**
There is none today. `ChatHistory` and `UserMemory` are keyed by a learner id,
so it is buildable — but it does not exist, and "we can, in principle" is not
an answer to a request. Is it required before production?

---

## 8 · Release

**Q8.1 — What is in 2.0 and what waits?**
Multi-course is built. The graph decision, the vocabulary and failure
visibility are each defensible either side of the line.

**Q8.2 — Does publication gate anything?**
The repository is intended to be public. A secrets sweep across every file —
not a sample — is the one step this project has already learned the hard way.

---

## Answered today, recorded so they are not reopened

* **`ingest-chunk-and-embed` being inactive is correct.** It is called through
  an `executeWorkflow` node as a sub-workflow, not through a webhook, and
  sub-workflows do not need to be active.
* **Re-importing an agent keeps its chatflow id.** `upsert_chatflow` finds the
  existing flow by name and updates it, so the mapping from chatflow to course,
  and therefore the filing of chat history, survives a re-import. Verified in
  the code and then on the running installation.
* **No rule that deletes learner data on the strength of an absence.** Written,
  then removed: it can never fire on an installation set up with courses from
  the start, and it would be wrong the day anyone puts a retention policy on
  `ChatHistory`.
