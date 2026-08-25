-- Which material the course's concept map is built from, and what each build did.
--
-- The concept map covers a whole course, across every agent in it. That is the
-- point — a shared vocabulary is worth nothing if each agent has its own — but
-- a course is also a container several people put agents into, and not all of
-- that material belongs together. Somebody presses rebuild, and a colleague's
-- unrelated corpus is merged into the map.
--
-- The graph is written with MERGE, so a rebuild adds rather than overwrites:
-- nothing curated is destroyed. What used to be impossible is separating the
-- two again afterwards, because a concept recorded no trace of where it came
-- from. The provenance that fixes that lives in Neo4j, on the concepts and
-- edges themselves. What lives here is the two things Postgres owns: which
-- agents take part, and what each build was.

-- Whether this agent's documents are read when the map is built.
--
-- Default true: a course whose agents all belong together is the normal case,
-- and a default of false would make the feature look broken — nothing would
-- ever enter the map without somebody first finding a checkbox. The default is
-- only delicate for an agent added *after* a build has already run, and that
-- is not handled by the default: those agents are listed before the next build
-- and their inclusion asked for, so the common case stays quiet and the
-- awkward one is visible without anybody having read anything first.
--
-- Unticking this does not remove anything by itself. It changes what future
-- builds read; taking the agent's contribution back out of the graph is a
-- separate, explicit step, because a concept two agents' material supports
-- must survive the departure of one of them. The interface has to keep those
-- two states apart — "no longer included" and "its contribution is still in
-- there" — or the checkbox is telling a lie.
ALTER TABLE agent_slots ADD COLUMN IF NOT EXISTS in_graph boolean NOT NULL DEFAULT true;

-- One row per attempt to build the map.
--
-- A build is long, it runs outside the request that started it, and it costs
-- real money, so its state has to survive a page reload, a restart, and the
-- operator going home. The proposal it produces is stored rather than shown
-- once: it is reviewed by a person, possibly not the one who started it, and
-- possibly not that day.
CREATE TABLE IF NOT EXISTS graph_builds (
    id           text        PRIMARY KEY,
    course_id    text        NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    -- queued → running → proposed → applied, or failed at any point.
    -- "proposed" is not "applied": the whole safety of this feature is that
    -- nothing reaches Neo4j until a person submits the review.
    state        text        NOT NULL DEFAULT 'queued',
    -- Which slots were included, as decided when the build started. Kept even
    -- after the checkboxes change, because the question a reviewer asks about
    -- an old proposal is "what was this built from", and today's answer is not
    -- that.
    scope        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- Documents read, slices sent, concepts and edges proposed, what was
    -- truncated or dropped and why. What the reviewer needs to judge the
    -- proposal, and what the operator needs to judge the bill.
    stats        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    proposal     jsonb,
    error        text,
    started_by   text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    applied_at   timestamptz,
    CONSTRAINT graph_builds_state CHECK
        (state IN ('queued', 'running', 'proposed', 'applied', 'failed'))
);

-- Two questions, both asked on the graph page: what is the current build for
-- this course, and what has this course's history been.
CREATE INDEX IF NOT EXISTS graph_builds_course_idx
    ON graph_builds (course_id, started_at DESC);

-- A course may have one build in flight, not five. Without this, an impatient
-- second click is a second run over the same corpus at full price, and two
-- proposals whose order of arrival decides which one the reviewer sees.
CREATE UNIQUE INDEX IF NOT EXISTS graph_builds_one_active_idx
    ON graph_builds (course_id) WHERE state IN ('queued', 'running');
