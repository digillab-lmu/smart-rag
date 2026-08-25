# Changelog

Notable changes, newest first. Dates are release dates.

This project follows [semantic versioning](https://semver.org/) loosely: the
minor version moves when something an operator would notice changes, and
entries marked **Upgrade required** need an explicit step on an existing
installation — `sudo smartrag` → *Upgrade* applies most of them.

---

## Unreleased

### Added

- **The knowledge graph can now be proposed from the course's own material.**
  Until now the concept list and its prerequisite edges had to be produced by
  hand: the page offered a prompt to copy into some chat window and a box to
  paste the answer back into. That is a reasonable fallback, and it is still
  there — it needs no API key and no reachable provider — but it made the
  graph the one part of a course setup that nobody finished.

  *Propose from the material* now reads the course's indexed documents, sends
  them to the strong model (`LLM_MODEL_STRONG`) and fills the review box with
  the result. What it sends is an outline, not the corpus: one entry per
  section — title, chapter, section number and a 400-character excerpt — up
  to a character budget. Chunks of the same section collapse into one entry,
  so the budget is spent on the shape of the course rather than on repetition.
  If the material does not fit, the page says so; a map built from half a
  course is a different map, and the operator should know which one they are
  reading.

  Three things it deliberately does not do. It does not write: the proposal
  lands in the same review box as a pasted one and reaches Neo4j only when
  somebody submits it. It does not use a prompt of its own — it sends the very
  text the page offers for copying, so the two routes cannot drift into asking
  different questions. And it refuses an empty course rather than inventing
  something: with no material, the model would return its general knowledge of
  the subject dressed as this course's structure.

  The answer goes through the same `parse_proposal()` as before, so a cycle, a
  self-loop or an edge naming an unknown concept is caught at the proposal
  step rather than after it has been applied.

### Fixed

- **An upload could carry no bibliographic data at all, and the title fell
  back to the file name.** Only the slot and the file were ever required. Two
  of three documents on the test installation therefore had no authors and no
  year — and these are not decoration: `source_title`, `authors` and `year`
  are in every retrieving agent's `weaviateMetadataKeys`, so they travel with
  each retrieved passage and are what an answer names its source with.

  The title is now required, and the fallback to the file name is gone — that
  fallback is how a document reached the index called after its slug, which is
  then the "source" a student sees. Authors and year are deliberately *not*
  required: a ministry framework has no personal author, and a mandatory field
  there buys invented entries. An invented source is worse than a missing one.
  Their absence is said once, after the upload has succeeded, as a warning
  rather than a refusal.

  The topic field's help now says what it actually does: it goes into the head
  of the converted document and is embedded with it, but unlike the other
  three it is not handed to the agent — checked against all six agent
  templates, not assumed.

- **A failed ingest could only be waited out.** Its row stayed in the progress
  table for half an hour — long enough to keep describing something the
  operator had already read and dealt with — and there was no way to say so.
  Rows that have stopped moving now have a *Clear* button. Running ones do
  not: hiding a running job would not stop it, and the next page load would
  bring it back looking new.

  It clears the progress entry and nothing else. Two tables sit one above the
  other on that page, and the wording keeps them apart: a document that was
  written is in the list below and is removed there. The clear is scoped to
  the selected course — the browser sends only a job id, and a job id from
  another course must not be removable through it.

- **Documents failed after two minutes with a message naming an environment
  variable.** `DOCLING_SERVE_MAX_SYNC_WAIT` was never set, so the image's own
  120 seconds applied — while the n8n node calling docling waited patiently
  for 1800. A 1.2 MB scanned PDF was enough to exceed it.

  It is now `DOCLING_MAX_SYNC_WAIT` in `.env`, default 1500 seconds, and it
  must stay below the node's timeout: the innermost limit has to be the
  shortest, or the component that knows why a conversion stopped never gets to
  say so. `tests/test_ingest_limits.sh` holds that ordering, and the size
  ordering with it — the Content Admin's 200 MB is below n8n's 256 MB, so an
  oversized file is refused by the page rather than by a webhook.

  The upload page states both limits before the upload rather than after it,
  and says which one usually bites: not megabytes, but pages and scans. The
  minutes shown are read from the configured value, not written into the text
  a second time.

- **Phase 7b worked exactly once per installation.** `garage node id` returns
  the full 64-character key; `garage layout show` prints only its first
  sixteen. The check for "is a layout already applied" compared the full id
  against that display, so it never matched — every re-run took the
  no-layout-yet branch, tried to assign a layout that was already correct, and
  died on `layout apply`. Invisible until a `--continue`, which is exactly
  when it matters. The script had already truncated the same id for display
  two lines earlier.

  The version to apply is now read from the hint Garage prints after staging
  a change, with current + 1 as the fallback. Hardening rather than a second
  bug: on the output seen so far the previous expression — the last "version
  N" anywhere in the output — happened to land on the same number.

- **The same failure one phase later: the staged Weaviate schema.** Phase 4
  writes three things. `.env` is in the repository and survives anything; the
  other two land under `BASE_DATA_PATH`, which is the one directory an
  operator deletes on purpose to start over — and *continue the deployment*
  then skips phase 4, because `.env` still being there looks like proof that
  configuration happened. Garage got a directory where its config should be;
  the schema was simply gone, and phase 8 died on it.

  `scripts/deploy-schemas.sh` now writes it when missing. Nothing has to be
  asked: since courses stopped being an installation-wide setting, that file
  is `weaviate/schema.json` with the per-course template removed.

  `tests/test_bootstrap_incomplete.sh` generalises it — whatever phase 4
  writes under `BASE_DATA_PATH`, some consumer outside bootstrap must be able
  to produce again, so a third file cannot repeat this.

- **Garage would not start after a fresh install, and could not recover.** Its
  configuration is mounted as a *file*; when the host path is missing, Docker
  creates a directory there, Garage reads a directory as its configuration and
  restart-loops with `IO error: Is a directory` — which names neither the file
  nor the cause. It was self-sustaining: the directory stays, so every later
  start fails identically, including a bootstrap re-run that keeps its `.env`
  and therefore never reaches the phase that writes the file.

  `scripts/start-services.sh` now checks before anything starts. A missing
  file is written — every value in it comes from `.env`, so there is nobody to
  ask — and a directory is refused with the repair, because removing something
  is the operator's call.

  Writing the file is not sufficient on a host that has started the container
  once already, which the first version of this fix got wrong: a bind mount is
  resolved when a container is *created*, and `compose up` only restarts an
  existing one, so it kept failing against a path that had become a perfectly
  good file. The container is now removed after the repair and left for
  compose to build again — Garage keeps no state inside it.

- **Two comments described machinery that had been gone for four months.**
  `docker-compose.yml` said buckets are created on startup and that the object
  store notifies n8n on upload. Both were MinIO's; Garage has no bucket
  notifications, and the Content Admin posts each file to n8n itself. An
  orphaned MinIO block sat above the Garage service with no service under it,
  and the core profile still listed MinIO as a member.

### Changed

- **Garage's configuration is a template in the repository**, like every other
  component's — `garage/garage.toml.template`, rendered by
  `write_garage_config()`. It previously existed only as a heredoc inside
  `scripts/lib/templates.sh`: nothing to review in a diff, nothing in a
  `garage/` directory to find, and no way to re-create the file without
  walking through the wizard again. Rendering takes its values from the wizard
  on a first install and from `.env` afterwards, so the file can be rebuilt at
  any time. The rendered file is still never committed — it carries the RPC
  secret — and the test that enforced that now also requires the template to
  carry none.

### Changed

- Ubuntu 26.04 is a tested release, not an accepted-with-a-warning one. It is
  what this project is developed and run on; the installer, the whole service
  stack, the backup path and the ingest have all been exercised there
  repeatedly, which is a stronger claim than 24.04 originally carried. 24.04
  stays on the list — it did not stop working when 26.04 started.

### Removed

- `langfuse-userid-patch` — the n8n workflow that back-filled a `userId` onto
  Langfuse traces, together with the `smartrag-langfuse` credential that
  existed only for it. Both situations it could meet make it wrong: launched
  through the LTI middleware, Flowise already sets the trace's userId, so
  there is nothing to patch; launched without it, the only id available is
  Flowise's own chat id, which the embed keeps in a browser's local storage —
  writing that into a field called `userId` turns a browser into a person,
  which is exactly the confusion that makes an erasure request unanswerable.
  It was also broken: it referenced a node by a name that had been changed.

  **On an existing installation the workflow stays in n8n until it is removed
  there** — taking it out of this repository does not take it out of a running
  system, where the deployer had activated it. Stop it first, which is the
  part that matters, since it ran every thirty minutes:

      docker exec smartrag-n8n n8n update:workflow \
          --id=smartrag-langfuse-userid-patch --active=false

  Then delete it in the n8n interface — *Workflows* → *Langfuse — patch userId
  on Flowise traces* → ⋯ → *Delete*. n8n's CLI has `import:workflow` and
  `update:workflow` but no `delete:workflow`, so there is no command for the
  second step. The watchdog no longer expects the workflow, so nothing will
  report it missing in the meantime.

## Unreleased

### Added

- **A course can be deleted.** Two pages: the first counts what the course
  consists of across six systems, the second reports what each of them
  answered. The course id has to be typed, because it is the one confirmation
  that cannot be given by muscle memory. Proven on a live installation by
  filling a throwaway course, deleting it, and measuring the others: their
  chat-record counts were identical before and after. **The Flowise API key
  now needs Delete on chatflows** — the installer's permission list says so,
  and an installation set up earlier will see a 403 on that one step until it
  is granted.
- **A small S3 client** (`content-admin/s3_client.py`), Signature Version 4
  written out against stdlib and checked against AWS's published get-vanilla
  vector. Garage's admin API cannot empty a bucket and refuses to delete one
  that is not empty, so without this a deleted course would leave its
  documents behind for the next course of the same name to adopt. It also
  closes an older hole: removing a single document in the GUI used to leave
  its archived markdown in the bucket for ever.

### Fixed

- **The inventory answered a German operator in English.** Its labels are
  built in Python rather than in a template, which is how that one table
  escaped the rule that every operator-facing string exists in both
  languages.
- **A failed deletion step hid its reason** behind the count of what it
  managed.
- **"Not countable" and "did not answer" were the same state**, so a line
  that cannot have a number warned as if a service were down.
- The Content Admin's pages use 80% of the window rather than a 900-pixel
  column, while running text stays at a readable measure.

---

## 2.0.0-beta.1 — 2026-08-13

**A pre-release, deliberately.** One installation can now hold several
courses, and that has been proven on a live server rather than argued:
documents, agents, chat history, learning records, the concept graph and the
accounts that reach them are all separated per course, and each separation was
demonstrated by measuring one course while acting on another. What is not
done is what keeps this from being 2.0.0 — deleting a course, erasing one
person's data, backup and restore, and the decision about where the concept
graph should live. `1.0.0` remains the release to install if you want the
single-course system that has been running.

**There is no upgrade path from 1.x.** A 1.x installation's data carries no
course, and nothing tags it retroactively any more (see *Removed*). A 2.x
installation starts fresh and creates its courses in the Content Admin.

### Added

- **An agent now says which version of its template it was built from.** An
  agent in Flowise is a copy, not a reference: changing a template here, a
  slot's content, or a model name in `.env` reaches nobody until each agent of
  each course is imported again — and the page said "Imported" either way.
  That let two cross-course leaks survive their own fix on a running
  installation, findable only by querying Flowise's database. An import now
  records a digest of the flow it built, the Agents page compares it against
  the flow that would be built now, and a button re-imports every agent of a
  course. Three states, not two: "behind" is only said when the flows differ,
  and a slot imported before this existed reads as "version unknown" rather
  than being accused. **Upgrade required:** none — the migration runs at
  start.
- **A failed workflow reaches a person.** `usermemory-summary` failed on every
  scheduled run for at least two days — ten red executions — and was found
  only because its output was being examined for something else. An error
  workflow now mails the configured address, and an hourly watchdog covers
  what an error trigger cannot see: a workflow switched off, one that stopped
  being triggered, and one that reports success while writing nothing. Both go
  quiet after the first mail and say how many failures the next one stands
  for, because a five-minute schedule that fails is 288 mails a day and the
  second day teaches everyone to filter them.
- **Pending database migrations are applied when the Content Admin starts.**
  Nothing in this repository had ever applied one: the admin menu entry called
  "apply pending migrations" checked `.env` keys and never touched SQL, and
  the first migration reached the test installation only because somebody ran
  the CLI by hand. The application now applies them at startup and refuses to
  serve if it cannot; the menu entry does what its name says as well.
- **`scripts/repair-chathistory-course.sh`** files existing chat history under
  the course it came from, for installations that ran either of the two broken
  versions. It reads the truth rather than assuming it — a stored message
  names the Flowise message it came from, that names its chatflow, and a
  chatflow belongs to one slot of one course — and leaves untouched, with a
  count, everything where that chain is incomplete. Report by default.

- **Chat history was filed under whichever course `.env` named.**
  `chathistory-sync` stamped `$env.COURSE_ID` onto every message it wrote,
  every five minutes. With one course that was right; with two it silently
  attributed one course's conversations to another. It now looks the course
  up from the chatflow the message came from — the mapping is a column in the
  Content Admin's database — and skips a message whose chatflow it cannot
  place rather than guessing, because a missing message can be synced later
  and a misfiled one is only found by somebody reading another course's
  conversation. **Upgrade required:** re-import the n8n workflows; a new
  `smartrag-contentadmin` credential is created for the lookup.
  `usermemory-summary` is not converted yet and still uses `$env.COURSE_ID`.
- **A concept's name was globally unique, which made a per-course graph
  impossible.** `neo4j/schema.cypher` constrained `Concept.name` across the
  whole installation, so two courses could not both have a concept called
  "Cognitive Load" — the second course's write would simply fail. Neo4j
  Community cannot constrain a pair of properties (a node key is Enterprise),
  so the pair is folded into one synthetic `key` property, `course::name`,
  and the constraint moved there. Found by reading the schema after the
  feature was written; the tests stub the transport and could not have seen
  it. **Upgrade required:** re-run the schema step, which drops the old
  constraint.
- **Prerequisite circles are refused.** "A before B before C before A" is a
  contradiction, and nothing downstream objects: the agent would fetch
  prerequisites for ever or teach in an arbitrary order, and the map would
  look plausible.
- **The knowledge graph belongs to a course, and is no longer edited by
  pasting Cypher.** Concepts carried no course and the agents matched them by
  name alone, so with two courses one course's prerequisites answered for the
  other — the last of the cross-course leaks. Every concept now carries its
  course, every read and write names it inside the pattern, and each agent
  matches only its own.

  The page changed with it. It used to hand out a Cypher prompt and run
  whatever came back; a boundary cannot be enforced inside a statement
  somebody else wrote, and checking Cypher before running it would mean
  parsing Cypher. The model is asked for JSON instead, which is validated
  here — a nameless or duplicated concept, an unknown field, an edge to a
  concept that is not in the answer, or an answer that is not JSON at all are
  each refused with the reason — and the writing is done with parameterised
  statements. `MERGE` throughout, so applying the same answer twice changes
  nothing the second time.

  The page also shows what is in the graph now: concepts with how many links
  lead in and out (a duplicate is the one with none), all links, removal of a
  single concept, and starting over. Concepts created before the split belong
  to no course and are invisible to every agent; the page says so and offers
  to claim them for the current course rather than guessing which one they
  were for.
- **Accounts, roles, and who may reach which course.** The single account in
  `.env` became rows in the database. Two roles: an installation
  administrator, who manages courses and accounts and works anywhere, and a
  maintainer, who works in the courses assigned to them and sees no others —
  not in the list, not in the switcher, not by typing a URL. The check lives
  in one function called from one decorator, and a test reads Flask's own
  route table to require it on every course-bound route, so a route added
  later without it fails the suite rather than quietly showing one course's
  material to another course's maintainer. The role is looked up per request
  rather than copied into the session, so a withdrawn assignment takes effect
  on the next click instead of the next login. The last administrator can be
  neither demoted nor deleted: an installation with none can only be repaired
  from a shell. Password resets go to the address on the account, falling
  back to `ADMIN_EMAIL`, and never to an address typed into the form. The
  existing `.env` account is adopted as the first administrator on the first
  start, hash and all, so nobody has to be told a new password and nobody can
  claim the installation through the first-run page.
- **The workflow import restarted n8n over a running ingest.** Observed: an
  upload started at 20:12:43, `deploy-n8n-workflows.sh` restarted n8n eleven
  seconds later, and the execution was recorded as `crashed` — with n8n's own
  hint blaming memory, which sent the diagnosis after Docling and the machine's
  RAM before the timestamps settled it. A conversion takes minutes, so the
  window is wide. The deployer now asks n8n's execution table what is running,
  waits up to five minutes, and if something is still going asks before
  restarting — defaulting to not destroying work in progress.
- **A failing ingest step now says so on the page.** The steps that actually
  fail — conversion, the object store, chunking and embedding — report their
  own failure from an error output, which unlike n8n's Error Trigger runs
  inside the same execution and can name the document it was working on. Rows
  in progress also spin and the page refreshes every seven seconds rather than
  fifteen, which is why stages appeared to jump straight from "accepted" to
  "finished" on a small document.
- **Uploads went to whichever course `.env` named.** The selected course
  governed what was displayed and nothing else: the ingest workflow read the
  collection, the course id and the bucket from n8n's environment, which
  holds one course for the whole installation. A document uploaded into a new
  course therefore arrived in the original one — with the upload reporting
  success and the new course's list correctly showing nothing. All three now
  travel with the upload. An installation with a single course is unaffected:
  the environment remains as a fallback, second in each expression rather
  than first. **Upgrade required:** re-import the n8n workflows.
- **The progress table showed another course's uploads, and kept finished
  ones.** Progress rows carried no course, so a document being processed in
  one course appeared while a different course was selected — next to a
  document list that correctly showed nothing, which is two contradictory
  statements about the same upload. Rows carry their course now, and a
  finished one leaves the table at once: its completion is already visible,
  because the document appears in the list below. Failures stay, since
  nothing else mentions them.
- **The document list showed another course's documents.** It read the
  collection and course id from `.env`, so whichever course was selected in
  the header, the page listed the one fixed collection from the
  single-course era — authoritative-looking and belonging to somebody else.
  It reads the selected course now.
- **Navigation, tidied.** The current page is marked; *Add documents* and
  *Vector DB* are grouped under *RAG*; *System status* moved to the end and
  absorbed the Flowise connection page, which is a setup step rather than a
  daily destination — reachable from there in every state, because a key is
  replaced while things are still working. Logging out moved up beside the
  language switch. Each page's opening paragraph now says what the page is
  and what can be done on it, instead of explaining the machinery: the
  status page in particular used to open by explaining that its checks are
  live rather than cached, which matters to whoever is debugging it and to
  nobody else.
- **Agents belong to a course.** Slots moved out of `slots.json` into the
  database, with the course as a required argument everywhere — no
  course-less variant is left for a later change to fall into. Every page
  that touches agents, documents or uploads resolves an active course in one
  place; with a single course it is chosen automatically, with several the
  operator picks and nothing is guessed. Importing an agent substitutes *that
  course's* collection and course id, and the chatflow's name in Flowise
  carries the course: Flowise's names are global, so two courses with an
  agent called "Tutor" would otherwise be one chatflow, each import
  overwriting the other and reporting success both times.
- **Courses can be created, and a half-created one says so.** A new *Courses*
  page lists them and creates them; creating one also creates its chunk
  collection, its bucket and the ingest key's permission on that bucket. If
  any of that fails the course is recorded and listed as *unfinished* rather
  than left half-made and looking complete, and finishing it is a button that
  is safe to press repeatedly. Unfinished courses are listed above the ready
  ones, because a page that shows both the same way tells an operator their
  broken course is fine. Nothing else uses courses yet — agents, uploads and
  who may see which course are the phases after this one.
- **A database for courses, accounts and agent slots.** First step of the
  multi-course work: Postgres gains a `contentadmin` database (not
  `POSTGRES_DB`, which is called "smartrag" and is already Langfuse's), the
  Content Admin gains a connection pool and a versioned migration runner, and
  migration 001 creates `courses`, `users`, `user_courses` and `agent_slots`.
  Nothing reads them yet and no behaviour changes; applying and inspecting is
  `docker exec smartrag-content-admin python3 /app/db.py migrate|status`.
  The rules live in the schema rather than only in Python — a course id's
  shape, ten slots per course, one chatflow per slot, one agent name per
  course case-insensitively — because a route can forget a check and a
  constraint cannot.
- **The system status page links to Flowise and n8n.** Their addresses were
  otherwise only in `.env` or in an email from the day the system was
  installed. The link rides on each service's check and shows in every state,
  including a passing one — opening Flowise is an ordinary thing to want, not
  a repair step. It is the public address, not the container address the check
  itself probes.
- **The document list shows what is still being processed.** It is built from
  Weaviate, so an upload used to show nothing at all until its chunks existed
  — twenty minutes of an unchanged page for a scanned PDF with figures, with
  no way to tell work from silent failure. The ingest workflow now reports its
  own stages (converted, figures described, archived, chunked and embedded),
  and the page lists them and refreshes itself while something is moving.
  n8n's public API was the alternative and was rejected: it needs an API key
  a human must create in a browser, and mapping an execution back to a
  document is guesswork as soon as two uploads overlap.

  A run that dies between two stages reports nothing, so its row says how long
  it has been silent rather than claiming to work or inventing a failure.
  Nothing about the display can affect the ingest: the reports hang off side
  branches, swallow their own errors, time out in five seconds, and a progress
  row that cannot be written loses the row, not the upload. **Upgrade
  required:** `sudo smartrag` → *Upgrade* adds `INGEST_STATUS_TOKEN`, then
  re-import the n8n workflows.

- **The installer writes the hand-over message to the Content Admin.**
  Everything else it prints is read by a system administrator in a terminal on
  the server; the person who will use the system daily gets none of it, and
  cannot guess the address — least of all in Tailscale mode, where it is a
  machine name on a private network. So the closing step now composes that
  message: what this is, where to work, the first three things to do in order,
  whether an account already exists, and who to ask. With a mail relay
  configured it offers to send it to an address you give it; without one it
  prints it between two lines to be copied out. Nothing is sent without the
  message being shown in full first and the send confirmed.
  Also available afterwards as *Hand-over message for the Content Admin* in
  `sudo smartrag` — for a second person, or when the role changes hands.

### Fixed

- **Chat history was written with no course at all.** The fix that read the
  course from the chatflow looked it up, used it to decide whether to skip the
  message — and then built its output object without it. Both ends of the
  chain were right and the middle was empty, so every execution was green and
  every row was written with `course_id` null. The tests checked both ends,
  which is how it passed. The workflow's Code nodes are now executed by the
  suite, not read.
- **A learning record belonged to a learner, not to a learner in a course.**
  `usermemory-summary` looped over learners alone: someone in two courses got
  one record with both courses' concepts merged, stamped with the
  installation's single course, and the six agents that read a record filtered
  on the learner only — so an agent greeted a learner with what they had
  struggled with in another course. The loop now runs over courses and then
  over the learners inside each, and a record's id is derived from that pair,
  which makes a duplicate impossible rather than cleaned up afterwards. The
  duplicates the old version left — one per run, ten for the most active
  learner — are removed on the next run, including for a pair with nothing new
  to summarise. **Upgrade required:** the n8n container must be recreated, not
  just restarted, so Code nodes may use Node's `crypto`; the deploy script
  checks and offers it.
- **Two agent queries reached across every course.** `Load UserMemory` and
  `Load Relevant ChatHistory` filtered on the learner alone. The second does a
  similarity search over the learner's own questions and quotes the hits back
  into the prompt — in one course's agent, from another course's
  conversations. Found by enumerating every query in every archetype instead
  of grepping for the one that had already been fixed: 17 queries, 6 of them
  unscoped. They also stopped interpolating the learner id into the query
  text. **Upgrade required:** re-import every agent of every course.
- **Neo4j held 871 MB for a few hundred concepts.** Invisible on 16 GB, a
  tenth of the machine on the documented 8 GB minimum. Sized to what it
  actually holds. The hardware table now carries measured figures instead of
  estimates, and says plainly that `core,observability` peaks at 7.8 GB and
  does not fit 8 GB.
- **Every page printed its messages twice.** `base.html` renders `error` and
  `success` above the content, and four pages rendered them again. A
  duplicated confirmation is easy to stop seeing; a duplicated error reads as
  two failures.
- **"In Bearbeitung" meant the selected course, not an unfinished one** — in a
  column that also says "unfertig", which is the state where a course's
  collection, bucket or write permission is missing and uploads go nowhere.


- **The installer reported a 180-second timeout that had not elapsed.** After
  the restart it waited for n8n's `healthz`, then took whatever the ingest
  webhook said at that instant — and any unrecognised answer was reported as
  "n8n did not come back within 180s", on an installation where n8n was up and
  answering. Two separate questions were being told as one. They are now
  separate: whether n8n is back, and whether the webhook is registered, which
  legitimately takes a few more seconds because n8n serves `healthz` before it
  finishes registering webhooks. The webhook is now polled for up to 30 more
  seconds (`N8N_WEBHOOK_SETTLE`), and if it still answers something unknown
  the message says n8n is up and quotes the reply verbatim. That wait now
  covers n8n's own "is not registered" too — the same transient in n8n's
  words, which the first version took as final and aborted an install whose
  webhook answered correctly moments later. It says once that it is waiting,
  because a silent half-minute after "n8n restarted" reads as a hang, and the
  next move is Ctrl-C in the middle of the step that decides whether uploads
  work.
- **…and the same mistake was in six parameters, the other way round.** In a
  node parameter the `=` marks the whole value as an expression and must be
  the first character. Six `Authorization` headers were written as
  `Bearer ={{ $env.WEAVIATE_API_KEY }}`, so the `=` was just a character, the
  value a plain string, and Weaviate received the literal text — the same 29
  characters that came back 401 from the Code node, arriving by the opposite
  error. Five of them are in `usermemory-summary`, which had not run yet
  because its schedule is slower. A test now requires the marker to be first
  and to appear once, in both contexts.
- **ChatHistory sync could never have worked.** Its first live run failed with
  a 401 from Weaviate, and the cause was in the source all along: the Code
  node sent `Bearer ={{ $env.WEAVIATE_API_KEY }}` — n8n does not evaluate
  expressions inside a Code node, so Weaviate received those 29 characters as
  the token. Behind it were three more that would each have failed the next
  run: the cursor object does not exist on a fresh installation, so the read
  was a 404 that threw and the write was a 404 that could never create it —
  and the first attempt to catch that 404 did not work either, because a Code
  node runs in n8n's task runner and an exception loses its structured fields
  crossing that boundary. The status is now read from the response
  (`ignoreHttpStatusErrors` plus `returnFullResponse`) rather than from a
  thrown error; and
  the deduplication query pasted a hash into GraphQL unquoted. Weaviate's port
  was also hard-coded in three workflows although it follows
  `WEAVIATE_HTTP_PORT` — unlike Docling, markdowncleaner and the Content
  Admin, whose ports have no compose mapping and are properties of their
  images.
- **The memory and observability workflows were never installed.** The
  deployer read `workflows-ingest/` only, while `n8n/workflows/README.md`
  stated that bootstrap imported them automatically — so cross-agent chat
  recall, the learner memory summary and the Langfuse trace patcher were
  documented, present and dead. All three are now deployed and activated, and
  finishing them turned up six further defects: no workflow ids (every import
  would have left another copy, and a duplicate five-minute schedule runs
  twice), two credentials the deployer never created, an Anthropic node with a
  hard-coded model in a provider-agnostic project, a literal `{{COURSE_NAME}}`
  in a prompt, a hard-coded Langfuse port, and a SQL query built by pasting a
  value from Langfuse into it. `langfuse-userid-patch` ships only with the
  `observability` profile and, because it writes learner names into Langfuse,
  carries that warning in its README.
- **Every document was archived over the previous one.** The ingest built its
  object key from the uploaded file's name, read out of the binary in hand —
  but by that point the binary is Docling's response, not the upload, so the
  name was gone and a constant fallback took over. Every document in a course
  landed at `agent_<n>/document.md`, each overwriting the last. Retrieval was
  unaffected (the chunks in Weaviate are separate and complete), but of the
  archived markdown only the most recent document survived, and
  `source_file` is identical on every chunk ingested before this fix.
  The name now travels as its own form field, and the fallback is derived
  from the title or a timestamp rather than a constant, so a lost name can
  never again silently overwrite somebody else's document. The keys are also
  readable now — runs of punctuation collapse to a single dash instead of one
  dash per character, and the name is capped at 80 characters, because these
  are read by a person looking through `garage bucket list-objects`. **Upgrade
  required:** re-import the n8n workflows and rebuild the Content Admin;
  re-upload anything whose archived copy matters.
- **Langfuse ran on a ClickHouse it does not support.** `TZ="Europe/Berlin"`
  reached postgres, clickhouse and both Langfuse services, and Langfuse
  requires UTC for its stores — its documentation warns that queries otherwise
  return "incorrect or empty results". Measured here: a conversation at
  08:20:52 UTC produced a trace whose `timestamp` read `12:20:52Z` and whose
  `updatedAt` read `10:21:08Z`, the same record wrong by four hours and by
  two. The clocks were correct throughout; local time was being labelled `Z`.
  Those four services now pin `TZ: "UTC"` in `environment:`, which overrides
  the value every service inherits from `.env` through `env_file` — that
  inheritance is how Flowise ran in Europe/Berlin without a line naming it.
  Everything an operator reads logs from keeps the local timezone. Traces
  already written keep their wrong timestamps.

  Fixing the stores exposed a second, independent shift of the same kind:
  Flowise supplies the timestamp on every trace it emits and formats local
  time while labelling it `Z`, so a conversation at 08:20:52 UTC still
  arrived as `10:20:52Z` once ClickHouse was correct. Both Flowise services
  are pinned to UTC as well; the cost is that Flowise's own UI shows UTC,
  which is the smaller price. Verified to the second afterwards: a request
  made at 09:01:17 UTC produced a trace stamped `09:01:17.869Z`. This half is
  written into the value, so traces from before it stay two hours high.
- **n8n's settings file is no longer world-readable inside the container.**
  `/home/node/.n8n/config` holds the encryption key that every stored
  credential — S3, SMTP, the LLM keys — is encrypted with, and n8n creates it
  0644. `N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true` makes n8n enforce 0600
  itself, which it will do by default in a future version anyway.
- **Langfuse against Garage is now proven, not assumed.** One trace through the
  ingestion API, with the bucket's object count taken before and after: 0
  objects, HTTP 207, then one object of 227 bytes, and the trace readable back
  through the API — so the write reached the store and the worker carried it
  on to ClickHouse. The runbook carries that probe, because a Garage without a
  layout is healthy, answers, and discards every write, and no amount of
  correct-looking configuration distinguishes the two.
- **The Start node in every agent template was three versions behind.** Flowise
  3.1.3 ships `startAgentflow` at 1.4; the templates carried 1.1, so every
  agent showed "Node version 1.1 outdated" when opened. Cosmetic — the node
  has no version-conditional code, so behaviour came from the current release
  either way — and worth fixing for that reason: a warning that is always
  there teaches people to skim past warnings. The other three node types were
  already current. A test pins the versions against the pinned Flowise image,
  so upgrading the image has to revisit them.
- **MinIO is replaced by Garage.** MinIO was archived upstream eight days
  after its last release. Garage was chosen by running this stack's actual S3
  operations against it — including a 32 MB multipart round trip and a
  presigned URL fetched without credentials — and then pointing Langfuse's own
  client at it, which is the part its feature table could not answer.
  `scripts/spike-garage.sh` is that evaluation, kept for the next candidate.

  Three differences matter in operation. Garage stores nothing until a layout
  assigns capacity — without one it is healthy, accepts connections and
  refuses every write — so provisioning applies the layout first and reads its
  version rather than assuming 1. Its image has no shell, so provisioning runs
  the binary from outside. And it has no root user and no bucket policies:
  permissions are per key and per bucket, so the ingest cannot read Langfuse's
  traces and Langfuse cannot read course documents.

  Credentials are still generated by the wizard and adopted by Garage through
  `key import`, so `.env` remains the source of truth. Flowise moves to local
  file storage — its files are per-chatflow working data, not course content.
  There is no storage console any more; Garage has none.

- **Langfuse never had valid S3 credentials.** Every `LANGFUSE_S3_*` secret in
  `.env` carried `${MINIO_LANGFUSE_*}` and was never substituted — Langfuse
  reads its configuration through `env_file`, where `${...}` is passed through
  literally, so it had been authenticating with a variable name. Invisible
  because tracing was switched off and nothing ever wrote an object. Now
  resolved by the wizard.
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

### Removed

- **`scripts/migrate-add-course-id.sh`.** It assigned `.env`'s `COURSE_ID` to
  every object that lacked one, which was right while an installation had one
  course and false the moment it had three — it announced "existing data now
  belongs to course testkurs2" on a machine with two other courses. It only
  ever filled blanks, so it never did damage, but the case it was written for
  cannot arise where there is no in-place upgrade from 1.x. Removed with its
  menu entry, its messages and every reference to it.

### Known limitations

- **A course cannot be deleted.** Creating one provisions a collection, a
  bucket, ten slots and a grant; nothing removes them again.
- **One person's data cannot be erased.** It is spread over Weaviate's three
  learner classes, Flowise's `chat_message`, Langfuse's traces and n8n's
  execution data, and there is no path that covers all of them. The data
  protection officer has approved the processing; the erasure obligation that
  comes with it is not yet implemented.
- **No backup or restore**, and therefore no supported way to move an
  installation to another machine.
- **Where the concept graph lives is undecided** (ARCHITECTURE 6d). It works,
  per course, but its boundary is a property that queries must name, while
  every other boundary in this system is physical.
- **Learner data references concepts as free text**, so nothing it says can be
  counted against the concept map.

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