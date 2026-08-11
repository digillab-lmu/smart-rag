-- The four tables the multi-course work rests on.
--
-- Nothing reads them yet: this migration lands first so that everything
-- after it can assume the shape exists, and so the migration mechanism
-- itself is exercised on a change that cannot break a running system.

-- A course. The values that used to be installation-wide in .env —
-- COURSE_ID, COURSE_NAME, WEAVIATE_COLLECTION_NAME — become columns here,
-- because a course is created and deleted while the system runs and never
-- was a property of the host.
CREATE TABLE IF NOT EXISTS courses (
    id                 text        PRIMARY KEY,
    name               text        NOT NULL,
    -- The chunk collection and the bucket are per course (ARCHITECTURE 6a).
    -- Stored rather than derived: a course created before a naming change
    -- must keep pointing at the collection that actually holds its data, and
    -- a rule that computes the name would quietly repoint it.
    collection         text        NOT NULL UNIQUE,
    bucket             text        NOT NULL UNIQUE,
    created_at         timestamptz NOT NULL DEFAULT now(),
    -- Creation touches Weaviate and Garage before this row exists. A course
    -- whose side effects half-succeeded must be visible as unfinished rather
    -- than looking complete.
    provisioned_at     timestamptz,
    CONSTRAINT courses_id_shape CHECK (id ~ '^[a-z0-9][a-z0-9-]{1,62}$')
);

-- An account. Two roles only: the installation administrator, who creates
-- courses and accounts, and a course maintainer, who is assigned to some
-- number of them (ARCHITECTURE 6c).
CREATE TABLE IF NOT EXISTS users (
    id              bigserial   PRIMARY KEY,
    username        text        NOT NULL UNIQUE,
    email           text,
    password_hash   text        NOT NULL,
    role            text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_login_at   timestamptz,
    -- Reset tokens live here rather than in .env, where the previous
    -- single-account version kept them and where the admin TUI displays
    -- them. Hashed, for the same reason as before.
    reset_token_hash text,
    reset_expires_at timestamptz,
    CONSTRAINT users_role CHECK (role IN ('admin', 'maintainer'))
);

-- Who may work on what. n:m on purpose: the same person routinely looks
-- after a lecture and its seminar, and two logins for one person is how
-- shared passwords start.
CREATE TABLE IF NOT EXISTS user_courses (
    user_id    bigint      NOT NULL REFERENCES users(id)   ON DELETE CASCADE,
    course_id  text        NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    added_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, course_id)
);

CREATE INDEX IF NOT EXISTS user_courses_course_idx ON user_courses (course_id);

-- The agent slots, formerly slots.json. Ten per course, and the number is
-- enforced here rather than only in the application: a slot 11 would import
-- into Flowise and then be unreachable from a GUI that renders ten.
CREATE TABLE IF NOT EXISTS agent_slots (
    course_id      text        NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    slot           smallint    NOT NULL,
    archetype      text,
    name           text,
    -- The archetype's filled-in fields, as they were in slots.json. A blob
    -- because the shape belongs to the archetype and changes with it; giving
    -- each field a column would mean a migration per template edit.
    content        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    system_prompt  text,
    chatflow_id    text,
    published      boolean     NOT NULL DEFAULT false,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (course_id, slot),
    CONSTRAINT agent_slots_range CHECK (slot BETWEEN 1 AND 10)
);

-- A chatflow id is Flowise's, and two slots pointing at one chatflow means
-- one of them silently edits the other's agent.
CREATE UNIQUE INDEX IF NOT EXISTS agent_slots_chatflow_idx
    ON agent_slots (chatflow_id) WHERE chatflow_id IS NOT NULL;

-- Names are shown to students, so they have to be distinct within a course.
-- Case-insensitive: "Tutor" and "tutor" are the same name to a reader.
CREATE UNIQUE INDEX IF NOT EXISTS agent_slots_name_idx
    ON agent_slots (course_id, lower(name)) WHERE name IS NOT NULL;
