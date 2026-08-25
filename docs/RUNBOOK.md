# Runbook

What actually goes wrong, and what to do about it. Every entry here is a
failure that has happened on a real deployment — the symptom is quoted as it
appears, because that is what you will be searching for.

**Start here:** the Content Admin GUI's **System status** page checks most of
this live, and `sudo smartrag` → *Status* covers the rest. Between them they
identify the cause of the first four entries without any of the commands
below.

---

## Uploading a document fails with a 404

**Symptom.** The upload page reports an error mentioning
`/webhook/document-ingest` and HTTP 404. Everything else in the GUI works.

**Cause.** n8n has no active ingest workflow. Almost always because the
import step of the install has not been completed — it needs an n8n *owner*
account, which can only be created in a browser, so a first install cannot
finish it unattended.

**Check.** `sudo smartrag` → *Status*, bottom line ("Document ingest"). Or:

```bash
curl -s http://127.0.0.1:${N8N_PORT:-5678}/webhook/document-ingest
```

`"not registered for GET requests"` means the webhook **is** fine — that is
the method-mismatch answer, not an error. `The requested webhook … is not
registered.` means it is genuinely missing.

**Fix.** `sudo smartrag` → *Ingest — (re-)import n8n credentials +
workflows*. If n8n still has no owner account, the entry walks you through
creating one and finishes the import itself.

---

## An agent answers "Missing credentials … set the OPENAI_API_KEY"

**Symptom.** In the chat: `Error in LLM node: Missing credentials. Please
pass an apiKey, or set the OPENAI_API_KEY environment variable.` — even
though the configured provider is not OpenAI.

**Cause.** The chatflow in Flowise has no credential attached. The mention
of OpenAI is a red herring: with no credential, the node falls back to the
provider SDK's own environment variable, and that is the name OpenAI's SDK
puts in the message.

**Check.** Is the container running the current code?

```bash
docker exec smartrag-content-admin grep -c FLOWISE_CREDENTIAL_ID /app/agent_templates.py
```

A low number (1–2) means an old build.

**Fix.** Rebuild, then **re-import the agent** — the fix only takes effect
at import time, and the flow already in Flowise keeps its empty credential:

```bash
cd /srv/smart-rag && git pull
bash scripts/compose.sh up -d --build smartrag-content-admin
```

Then in the GUI: open the agent → *Save and import to Flowise*. Publishing
survives a re-import.

---

## An agent answers "Cannot read properties of undefined (reading 'env')"

**Symptom.** `NodeVM Execution Error: TypeError: Cannot read properties of
undefined (reading 'env')`.

**Cause.** A custom-function node reached for `process.env`. Flowise's code
sandbox sets `process` to `undefined`, so any `… || process.env.X || …`
chain throws as soon as the value before it is empty — which
`EMBEDDING_BASE_URL` is on every standard provider.

**Fix.** `git pull` and re-import the agents. Custom code must read
`$vars?.NAME || 'default'`. See
[ARCHITECTURE.md § 7](ARCHITECTURE.md#7-flowises-code-sandbox-has-no-process).

---

## An agent retrieves nothing from its course

**Symptom.** The agent answers, but never cites or uses any course document.
No error anywhere — a retrieval that matches nothing is not a failure.

Every retrieval filters on `course_id`, deliberately as a hard stop rather
than a filter that matches everything: the alternative serves one course's
material to another course's students, silently. So an empty answer means the
filter and the data disagree, and there are three ways for that to happen.

**1 · The agent is behind.** An agent in Flowise is a copy of the template
taken at import time. If the filter, the course or the content changed since,
the agent still carries the old one. The Agents page says so per slot —
"behind — re-import" — and offers "Re-import every agent of this course". A
slot that reads "imported (version unknown)" was imported before that check
existed and has not been ruled out.

**2 · Nothing has been ingested into this course.** Each course has its own
collection, so a document in another course is not in this one. The Vector DB
page lists what is actually there, per document.

**3 · The agent belongs to another course.** Check which course's chatflow is
answering:

```bash
docker exec -i smartrag-postgres psql -U "$POSTGRES_USER" -d contentadmin -c "SELECT course_id, slot, name, chatflow_id FROM agent_slots WHERE chatflow_id IS NOT NULL ORDER BY course_id, slot;"
```

**Not a cause any more:** data ingested before course scoping existed. There
is no upgrade path from 1.x — a 2.x installation starts fresh — and the
script that used to tag such data has been removed rather than kept for a
case that cannot arise.

---

## A course's chat history is empty, or holds another course's conversations

**Symptom.** Conversations happened, the `chathistory-sync` executions are
all green, and the course's `ChatHistory` count stays at zero — or a course
that was never used holds hundreds of messages.

**Cause.** Two versions of the workflow wrote the wrong course. Before the
course lookup existed, every message was stamped with the installation's
single `COURSE_ID`; after it was added, `Prepare messages` looked the course
up, used it to decide whether to skip the message, and then built its output
object without it, so the messages were written with no course at all. Both
are fixed in the workflow — deploy it before repairing, or the repair is
undone five minutes later:

```bash
sudo bash scripts/deploy-n8n-workflows.sh
```

**Fix.** The repair reads the truth rather than assuming it: a stored message
names the Flowise message it came from, that message names its chatflow, and
a chatflow belongs to one slot of one course. Report first, nothing written:

```bash
sudo bash scripts/repair-chathistory-course.sh
```

Read the report. It names each move as "from → to", and counts separately
what it will **not** touch: objects with no `trace_id`, objects whose Flowise
message has been deleted, and objects from a chatflow that is in no slot.
That last group is the normal state of an older installation, and guessing a
course for it would move one course's conversations into another. Then:

```bash
sudo bash scripts/repair-chathistory-course.sh --apply
```

Running it again is safe — what is already right is left alone. Only
`course_id` is written; the vector is not recomputed.

Counting per course, to check before and after:

```bash
set -a && . ./.env && set +a && curl -s -X POST "http://127.0.0.1:${WEAVIATE_HTTP_PORT}/v1/graphql" -H "Authorization: Bearer $WEAVIATE_API_KEY" -H 'Content-Type: application/json' -d '{"query":"{ Aggregate { ChatHistory(groupBy: [\"course_id\"]) { groupedBy { value } meta { count } } } }"}' | jq
```

Objects with no course appear in no group, so compare the sum of the groups
against the total:

```bash
set -a && . ./.env && set +a && curl -s -X POST "http://127.0.0.1:${WEAVIATE_HTTP_PORT}/v1/graphql" -H "Authorization: Bearer $WEAVIATE_API_KEY" -H 'Content-Type: application/json' -d '{"query":"{ Aggregate { ChatHistory { meta { count } } } }"}' | jq
```

A `where` filter on `course_id` being null does not work here: `IsNull`
requires `indexNullState` in the class definition, which this schema does not
set. The difference between the two counts is the answer.

---

## A learner's record is empty, or holds another course's concepts

**Symptom.** An agent says "No prior learning record found" for a learner who
has been chatting for weeks — or, before the fix below, greeted them with
concepts from a course they are not in.

**Cause.** `UserMemory` is one record per learner **per course**, and until
this was fixed it was one per learner. The summary workflow looped over
learners alone, merged both courses' concepts into a single record and
stamped it with the installation's single course from the environment; the
six agents that read a record filtered on `user_id` only, with `limit: 1`, so
they took whichever record Weaviate returned first.

**Fix.** Deploy the workflow and re-import the agents — the course reaches an
agent's code through the `{{COURSE_ID}}` substitution at import time, so an
agent imported before this change keeps the old query:

```bash
sudo bash scripts/deploy-n8n-workflows.sh
```

Then, in the Content Admin, re-import each agent of each course.

**A learner has several records, and the runs are all red.** That is the
older shape of the same area, and it is worth recognising: the workflow used
to let Weaviate assign a random id, write the new record and then delete the
previous one. The delete node's URL contained an unevaluated expression, so
it failed every time — the run was marked as an error nobody read, and one
duplicate stayed behind on each run. On the machine this was found on, the
most active learner had ten.

Both are gone by construction rather than by cleanup: a record's id is now
derived from the (course, learner) pair, everything that exists for that pair
is deleted before the write, and there is no delete-afterwards step left to
fail. Nothing has to be deleted by hand.

Existing duplicates go on the next run, including for a pair with nothing new
to summarise — that path writes no record but still removes every copy except
the one it is skipping on. Without that it would have been the common case
that keeps them: a learner who has stopped writing in a course is skipped on
every run for ever.

That id is an RFC 4122 version-5 UUID, which is SHA-1 — so the n8n container
has to allow Node's `crypto` module in Code nodes. `docker-compose.yml` asks
for it, but an environment change only reaches a container that is
**recreated**; restarting is not enough. `deploy-n8n-workflows.sh` checks the
running container and offers to do it. By hand:

```bash
cd docker && docker compose up -d smartrag-n8n
```

**The old records.** They carry whatever `COURSE_ID` the `.env` held when they
were written — the installation's single course — regardless of which course
the learner was actually talking about. So they are not invisible: they sit
under that one course, and a record summarising a conversation from another
course sits there with them.

For every other course, nothing has to be done: the next summary run finds no
record for that (learner, course) pair, starts its cursor at 1970 and rebuilds
one from that course's whole history. The old records simply stay where they
are. To see how they are distributed:

```bash
set -a && . ./.env && set +a && curl -s -X POST "http://127.0.0.1:${WEAVIATE_HTTP_PORT}/v1/graphql" -H "Authorization: Bearer $WEAVIATE_API_KEY" -H 'Content-Type: application/json' -d '{"query":"{ Aggregate { UserMemory(groupBy: [\"course_id\"]) { groupedBy { value } meta { count } } } }"}' | jq
```

A single group holding everything is the signature of the old behaviour. As
with `ChatHistory`, a record without a course appears in no group at all, so
compare the sum against the total:

```bash
set -a && . ./.env && set +a && curl -s -X POST "http://127.0.0.1:${WEAVIATE_HTTP_PORT}/v1/graphql" -H "Authorization: Bearer $WEAVIATE_API_KEY" -H 'Content-Type: application/json' -d '{"query":"{ Aggregate { UserMemory { meta { count } } } }"}' | jq
```

Some of them belong to a pair that never had a conversation in that course
at all: the old workflow looped over every learner and stamped each one with
the installation's single course, so a learner who only ever wrote in Mathe
ended up with a record filed under Testkurs. Those are the ones the summary
run cannot reach — it visits the pairs that have conversations, and this pair
has none. To find them, compare who has a record in a course with who has
spoken in it:

```bash
set -a && . ./.env && set +a && for cls in UserMemory ChatHistory; do echo "— $cls —"; curl -s "http://127.0.0.1:${WEAVIATE_HTTP_PORT}/v1/objects?class=$cls&limit=1000" -H "Authorization: Bearer $WEAVIATE_API_KEY" | jq -r '.objects[] | "\(.properties.course_id)  \(.properties.user_id)"' | sort -u; done
```

A pair listed under `UserMemory` but not under `ChatHistory` is one of these.
Delete it by its object id — `?class=UserMemory` lists them with `.id` — and
check the record's `summary` first if you want to see which course it really
came from.

There is deliberately no repair script and no rule in the workflow for this.
A chat message can be traced back to its course through the chatflow it came
from; a learning record cannot — it is a summary, and which course it
summarises is only recoverable by reading it. A rule that deleted records on
the strength of a missing conversation would also be wrong the day anyone
puts a retention policy on `ChatHistory`. And it cannot arise on an
installation set up with courses from the start: a record is only ever
written for a pair that has a conversation. This is a one-off state of
installations upgraded from the single-course era, and clearing it is a
decision for a person.

Removing a course's records deliberately — all of them, chat history and
graph included — belongs to course deletion, where it is an explicit act
rather than an inference from an absence.

---

## A deletion stops at "deleted the chatflows" with HTTP 403

**Symptom.** The deletion report shows every other step done and this one in
bold: `DELETE /chatflows/… → HTTP 403: {"message":"Forbidden"}`. The course
stays in the list, which is correct — the record is kept whenever a step
fails.

**Cause.** The Flowise API key may create and update chatflows but not delete
them. Installations set up before course deletion existed were told to grant
Create and Update, because nothing needed more.

**Fix.** In Flowise, open the API key's permissions and add **Chatflows:
Delete**, then run the deletion again. Everything already removed stays
removed; each step is safe to repeat.

---

## Deleting a course

**When.** A retention period has expired, a course was created by mistake, or
a test course has served its purpose. Administrators only — creating a course
provisions a collection, a bucket and a storage grant, and undoing that cannot
be a smaller act than doing it.

**Where.** Content Admin → *Courses* → *Delete* on the course's row. The first
page counts what the course consists of; the second reports what each system
answered. The course id has to be typed, which is the one confirmation that
cannot be given by muscle memory.

**Read the inventory before confirming.** A line that says **unknown** rather
than a number means that system did not answer. That is not "nothing there" —
it may hold data the page cannot see. Deleting anyway removes what can be
reached and reports the rest as failed; the course then stays in the list.

**What goes, in this order, and why the order.**

1. The chat sessions of the course's agents are read from Flowise. A Langfuse
   trace carries a learner id and Flowise's chat id, never a course, so this
   is the only bridge from a course to its traces — and step 3 burns it.
2. Langfuse is asked to delete those traces. **Asked**: Langfuse removes trace
   data within about fifteen minutes and confirms nothing. Scores and
   observations go with them. On an installation without the observability
   profile this step is reported as skipped.
3. The chatflows are deleted in Flowise, which removes their chat messages,
   feedback and uploaded files itself.
4. The course's chunk collection is dropped whole; its records in
   `ChatHistory`, `UserMemory` and `TestResults` are deleted by filter,
   because those three are shared between courses.
5. The bucket is emptied and then removed. Both halves are needed: Garage
   refuses a bucket that is not empty and its admin API cannot empty one.
6. The course's concepts and their links are deleted from Neo4j.
7. Its progress rows are cleared.
8. **Last**, the course record — which takes its agent slots and its
   maintainer assignments with it by cascade.

**If a step fails, the course record is kept.** That is deliberate: while it
exists the course is still listed and the deletion can simply be run again,
picking up where it stopped. Every step is safe to repeat — an already-deleted
chatflow or bucket is counted as such, not treated as an error. Removing the
record after a failed step would turn a recoverable half-deletion into data in
five systems that nothing points at.

**What deletion does not touch.**

* **An account whose only course this was.** It survives and can then reach
  nothing. The inventory says how many such accounts there are; removing a
  person because a course ended is not a decision this should make on its own.
* **n8n's execution data**, which can contain message content and is pruned by
  n8n's own retention.

**Afterwards, measure the other course rather than assuming isolation:**

```bash
set -a && . ./.env && set +a && curl -s -X POST "http://127.0.0.1:${WEAVIATE_HTTP_PORT}/v1/graphql" -H "Authorization: Bearer $WEAVIATE_API_KEY" -H 'Content-Type: application/json' -d '{"query":"{ Aggregate { ChatHistory(groupBy: [\"course_id\"]) { groupedBy { value } meta { count } } } }"}' | jq
```

The deleted course must be gone from the grouping and every other course's
count unchanged. A collection that is still there is visible in
`GET /v1/schema`, and a bucket that survived in Garage's bucket list.

---

## Objects are not being stored, and nothing says so

**Symptom.** Uploads appear to succeed, Langfuse's dashboard stays empty, or
a bucket reports zero objects — while `smartrag-garage` is healthy and
answering.

**Cause — almost always the layout.** Garage stores nothing until capacity has
been assigned to a node. Without it the service starts, passes its
healthcheck, accepts connections and refuses every write. It is the one
failure mode with no MinIO equivalent, and it looks like anything but itself.

```bash
docker exec smartrag-garage /garage layout show
docker exec smartrag-garage /garage status
```

An empty or pending layout is the answer. Applying it is idempotent:

```bash
sudo bash /srv/smart-rag/scripts/deploy-garage.sh --lang de
```

That also (re)creates the buckets and re-imports the keys, and says which of
them already existed rather than failing on them.

**If the layout is applied**, check that the key exists and may write where it
is being used — Garage has no root user, so a key with no grant on a bucket is
refused even though it is a valid key:

```bash
docker exec smartrag-garage /garage bucket info langfuse-events
docker exec smartrag-garage /garage key list
```

`bucket info` lists the keys permitted on it, with `RWO` for read/write/owner.
A bucket with no key listed is a bucket nothing can write to.

**To prove the write path end to end**, send Langfuse one trace and watch the
object count. This isolates the store from everything else — no agent, no
Flowise, no chat — and it is the only check that distinguishes "configured
correctly" from "actually writing":

```bash
cd /srv/smart-rag && set -a && . ./.env && set +a
docker exec smartrag-garage /garage bucket info langfuse-events | grep -i objects
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' \
  -u "${LANGFUSE_INIT_PROJECT_PUBLIC_KEY}:${LANGFUSE_INIT_PROJECT_SECRET_KEY}" \
  -H 'Content-Type: application/json' \
  -X POST "http://127.0.0.1:${LANGFUSE_PORT}/api/public/ingestion" \
  -d "{\"batch\":[{\"id\":\"probe-$(date +%s)\",\"type\":\"trace-create\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%S.000Z)\",\"body\":{\"id\":\"probe-$(date +%s)\",\"name\":\"s3-probe\"}}]}"
sleep 20
docker exec smartrag-garage /garage bucket info langfuse-events | grep -i objects
```

`HTTP 207` is the success answer here — batch ingestion replies multi-status,
not 200 — and the count must be higher afterwards. Take the count BEFORE as
well as after: without it, "there are objects" does not distinguish this write
from an older one. A 401 is an authentication problem and says nothing about
the store; note that the project keys are named
`LANGFUSE_INIT_PROJECT_PUBLIC_KEY` and `LANGFUSE_INIT_PROJECT_SECRET_KEY`.

**There is no web console.** Garage has none. Everything above is the
interface, and the installer does the routine parts.

---

## A service authenticates with a variable name

**Symptom.** An authentication failure against a service that is running, with
credentials that look right in `.env`.

**Cause.** A value in `.env` that still contains `${SOMETHING}`. Values reach
most containers through `env_file`, and **`env_file` does not interpolate** —
`${GARAGE_LANGFUSE_SECRET_KEY}` arrives as those 29 characters. Compose does
expand `${...}` inside an `environment:` block, which is why some services are
fine and others are not, in the same file.

```bash
grep -nE '^[A-Z_]+="[^"]*\$\{' /srv/smart-rag/.env
```

Anything listed is being passed literally to whatever reads it. `sudo smartrag`
→ *Upgrade* reports and repairs these, and also reports any value still holding
the `generate-with-bootstrap` placeholder — which is a published string, not a
secret.

**A change made from the admin tool has to reach the container.** Values from
`env_file` are read when the container is created, not per request:

```bash
cd /srv/smart-rag && bash scripts/compose.sh up -d --force-recreate <service>
```

---

## Everything is slow, and services fail in unrelated ways

**Symptom.** Any combination of: the object store logging that it is taking
its drive offline, n8n taking minutes to restart, containers
disappearing, `load average` far above the core count while CPU sits idle.

**Cause.** Memory. Check first — this looks like a dozen different bugs and
is one:

```bash
free -h && top -b -n1 | head -12
```

Swap at or near 100% is the tell. The documented minimum is 8 GB for `core`,
12 GB with observability.

**Immediate relief.** The observability profile (Langfuse + ClickHouse) is
optional and is the largest consumer. In `.env`, set
`COMPOSE_PROFILES="core"`, then:

```bash
docker stop smartrag-clickhouse smartrag-langfuse-web smartrag-langfuse-worker
docker rm   smartrag-clickhouse smartrag-langfuse-web smartrag-langfuse-worker
```

Data stays in the volumes; the profile can be switched back on later.

**Note.** The installer now warns about this before it happens — an
installation predating that check got no warning at all.

---

## Langfuse timestamps are hours off, or a time range shows nothing

**Symptom.** A trace's time does not match when the conversation happened;
filtering by "last hour" returns nothing, or two fields of the same trace
disagree. Measured on this project: the event happened at 08:20:52 UTC, the
trace's `timestamp` read `12:20:52Z` and its `updatedAt` `10:21:08Z` — the
same record wrong by four hours and by two.

**Cause.** ClickHouse or Postgres running in a non-UTC timezone. Langfuse
does not support that and says so: queries return "incorrect or empty
results"
([docs](https://langfuse.com/faq/all/self-hosting-timezone-errors)). The
clock is not wrong — local time is being labelled `Z`.

```bash
docker exec smartrag-clickhouse clickhouse-client --user "$CLICKHOUSE_USER" \
  --password "$CLICKHOUSE_PASSWORD" --query "SELECT timezone()"
```

Anything but `UTC` is the answer.

**There are two independent shifts, and fixing one leaves the other.** The
stores render what they hold, and Flowise supplies the timestamp on every
trace it emits — it formats local time and labels it `Z`. On this
installation each contributed two hours, which is why the same trace was
wrong by four in one field and two in another.

**Fix.** Current versions pin `TZ: "UTC"` on postgres, clickhouse, both
Langfuse services and both Flowise services. Pull and recreate them:

```bash
cd /srv/smart-rag && git pull
bash scripts/compose.sh up -d --force-recreate smartrag-clickhouse \
     smartrag-langfuse-web smartrag-langfuse-worker \
     smartrag-flowise smartrag-flowise-worker
```

Flowise's own UI then shows UTC. That is the smaller price: one timestamp
read occasionally in its chat list, against every trace, cost figure and
time filter in Langfuse being wrong.

**What happens to traces already written.** The two halves behave
differently, which is worth knowing before anyone tries to "repair" the data.
ClickHouse's timezone affected how stored values are *rendered*, so fixing it
corrects every existing trace retroactively — verified on a record inserted
with a known UTC timestamp, which read two hours high before the change and
correctly afterwards. Flowise's half was written into the value itself, so
traces created before that fix stay two hours high for ever. In practice:
older records are wrong by two hours, not four, and nothing needs migrating.

Postgres also reads its timezone once, when its data directory is created, so
an existing installation keeps the old setting there until it is reinstalled.
Langfuse keeps only metadata in Postgres, so the visible symptom goes away
with ClickHouse.

**Verifying a fix.** Compare a fresh trace against `date -u`. Do not use
`?limit=1`: the API sorts by timestamp, and traces written before the fix
carry times in the future, so they stay at the top and a correct new trace
sorts underneath them. Fetch a page and compare each timestamp with the real
clock instead.

**Why not set the whole stack to UTC.** n8n, the Content Admin and the rest
keep the installation's timezone on purpose: a local timestamp in a log is
what somebody reading it at 2am wants.

---

## The GUI shows "SyntaxError: JSON.parse: unexpected character"

**Symptom.** A browser error where a result was expected.

**Cause.** The server answered with something that is not JSON — usually a
proxy's error page (502/504), occasionally an unhandled server error. The
message describes the parser, not the problem.

**Fix.** Current versions show the HTTP status and a snippet of the body
instead, which names the real failure. If you see the bare SyntaxError, the
container is running older code — rebuild it. Then read the new message and
check `docker logs smartrag-content-admin`.

---

## Upload fails with 413

**Symptom.** `413 Request Entity Too Large` from nginx.

**Cause.** `client_max_body_size` on the `content.` vhost is smaller than
the file.

**Fix.** It is set to 200M to match the GUI's own limit. If your nginx
config predates that, `sudo smartrag` → *SSL* → regenerate the nginx config,
or edit `client_max_body_size` in the `content.` server block and
`systemctl reload nginx`.

---

## Certificates are about to expire

**Check.** `sudo smartrag` → *SSL* shows status and expiry for every
certificate.

**Fix.** certbot renews automatically via its systemd timer. To force it:
same menu, *renew*. If renewal fails, it is nearly always DNS or port 80 —
`sudo smartrag` → *DNS check* verifies every subdomain still resolves here.

---

## No completion emails from the ingest

**Symptom.** Documents process, but no notification arrives.

**Cause.** `SMTP_HOST` is empty. That is a legitimate wizard outcome: if an
existing mail server was detected, the wizard offers to leave it alone, and
then SMART RAG deliberately does not use it.

**Fix.** `sudo smartrag` → *Mail service — set up and test*. It asks one
question with four answers, each stating what it needs beforehand; the entry
also sends a test mail, so the result is visible without waiting for the next
ingest. With Postfix already on the host, the answer is the first one and the
value is the pinned Docker gateway `172.28.92.1`, port 25. Ingest works either
way — only the notifications are affected.

---

## A workflow was removed from the repository but is still in n8n

**Symptom.** A `git pull` drops a workflow, and n8n goes on running it —
possibly on a schedule, possibly failing every time.

**Cause.** The deployer imports and activates workflows; nothing removes one.
n8n holds its own copy in its database, and a file disappearing from the
repository says nothing to it.

**Fix.** Deactivating is the urgent half, and the only half with a command:

```bash
docker exec smartrag-n8n n8n update:workflow --id=<workflow-id> --active=false
```

Then delete it in the interface — *Workflows* → the workflow → ⋯ → *Delete*.
n8n's CLI (1.123.67) has `import:workflow`, `update:workflow`, `list:workflow`
and `export:workflow`, but no `delete:workflow`; `n8n delete:workflow` answers
`Command "delete:workflow" not found`.

To see what is actually there:

```bash
docker exec smartrag-n8n n8n list:workflow
```

---

## A document fails with "Conversion is taking too long"

**Symptom.** The document list shows a document as failed with

```
504 - {"detail":"Conversion is taking too long. The maximum wait time is
configure as DOCLING_SERVE_MAX_SYNC_WAIT=120."}
```

**Cause.** What takes the time is pages, scans and figures — not file size. A
1.2 MB scanned PDF exceeded 120 seconds on the test machine while text
documents many times larger convert in seconds.

**Fix.** `DOCLING_MAX_SYNC_WAIT` in `.env`, in seconds, default 1500. Raise
it, then recreate the container so the new value reaches it:

```bash
cd /srv/smart-rag && bash scripts/compose.sh up -d --force-recreate smartrag-docling
```

**One ceiling not to cross.** The workflow node that calls docling has its own
timeout of 1800 seconds. `DOCLING_MAX_SYNC_WAIT` must stay below it — if
docling outlasts n8n, the conversion is cut off by n8n instead, and its
message says only that a request timed out. The component that knows why has
to be the one that gives up first. `tests/test_ingest_limits.sh` holds that
ordering; raising the ceiling means raising the node's timeout in
`n8n/workflows-ingest/ingest-document.json` first and re-importing the
workflows.

**If it still times out**, the document is genuinely too heavy for one pass —
split it. A book-length scan is better ingested as chapters anyway: retrieval
quality does not improve by feeding one enormous document.

---

## Backing up, or moving to another machine

`sudo smartrag` → *Backup*, or directly:

```bash
sudo bash scripts/backup.sh --keep 7
```

The services stop for the duration. That is not an oversight to work around:
Postgres and ClickHouse write continuously, and a tar of a running data
directory is a snapshot of no consistent moment. `--running` skips the stop
and marks the archive as torn, which the restore then says out loud.

Restoring — on this machine or another — starts with a dry run, always:

```bash
sudo bash scripts/restore.sh /path/to/smartrag-20260819T101500Z.tar.gz --dry-run
```

It refuses, before unpacking anything, an archive from a different Postgres
major version, one whose `.env` and data were not taken together, one that
fails its checksum, and a target that already holds data. `--force` moves an
existing installation aside rather than deleting it. If the new machine
answers at a different address, `--rename new.example.edu` rewrites `DOMAIN`
and states what it does not reach: the TLS certificates, the LTI registration
in the LMS, and any chat link already given out.

**A restore that starts every container is not a restore that works**, and
neither is a backup the restore script declined to refuse. To find out whether
an archive actually opens, without a second machine and without touching the
running installation:

```bash
sudo bash scripts/verify-backup.sh /path/to/smartrag-….tar.gz
```

It unpacks the archive into a scratch directory and starts throwaway
containers against that copy — own names, own high loopback-only ports, no
shared network — and asks each one whether it can read its own data: Postgres
with the archived password, Garage for its buckets, Weaviate for its
collections. Everything is removed afterwards, including after a failure.

After a real restore, check the same things through the application: chunk
counts per course and object counts per bucket on the Courses page, then ask
one agent a question and see that it answers with a citation.

**Garage, and the one thing not yet settled on real hardware.** Garage's
layout carries a node id created on the machine it was laid out on. This
installation runs a single node with `replication_factor = 1` and a fixed
`rpc_public_addr`, so a copied metadata directory should bring the same node
id and the same layout with it — *should*, because it was reasoning and not a
measurement. `verify-backup.sh` measures it: it starts Garage against the
archive's copied metadata directory and lists the buckets. If they are there,
the layout travelled. If it reports no usable layout, the fallback is to lay
out a fresh cluster and copy the objects back at S3 level — slower, and known
to work.

### What an archive contains, if you would rather do it by hand

```bash
sudo bash /srv/smart-rag/scripts/admin.sh   # → Stop, so the copy is not torn
sudo tar czf smartrag-$(date +%F).tar.gz \
     -C /srv/smart-rag .env \
     -C / "$(grep -oP '(?<=^BASE_DATA_PATH=").*(?=")' /srv/smart-rag/.env | sed 's|^/||')"
```

Two things are easy to get wrong.

**`.env` and the data directories are one unit.** Postgres, Neo4j and
ClickHouse read their password once, when their data directory is first
created, so a restored data directory without its original `.env` cannot be
opened at all. `N8N_ENCRYPTION_KEY` is the same story: without it, every
credential stored in n8n is ciphertext nobody can read.

**Copy nothing while it runs.** A live Postgres or ClickHouse directory
copied under load is a torn copy that may restore and then fail later. Stop
the stack first; this system tolerates the downtime.

Moving to a machine with a **different address** is more than a copy: the
domain or MagicDNS name appears throughout `.env`, in the published chat
URLs, and in the TLS certificates. Plan that as a rename, not as a restore.

---

## Something else

1. `sudo smartrag` → *Status* — containers **and** the ingest webhook.
2. The GUI's **System status** page — API keys, Flowise, agents, and the
   conversion services.
3. `bash scripts/compose.sh logs -f <service>`.
4. `bash tests/run-tests.sh` — if a suite fails on an unmodified checkout,
   that is worth reporting rather than working around.

When reporting a problem, the useful three things are: the exact message,
which of the checks above were green, and whether the deployment was
upgraded or installed fresh.
