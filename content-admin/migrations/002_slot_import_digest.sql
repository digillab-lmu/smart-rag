-- What a slot was imported from, so "imported" can stop meaning "at some
-- point".
--
-- An agent in Flowise is a copy, not a reference. Changing a template in this
-- repository, or the slot's own content, or a model name in .env, changes
-- nothing about the agent that is answering learners until somebody presses
-- import again — and the interface said "imported" either way. On 2026-08-13
-- that let two cross-course leaks survive their own fix on a running
-- installation, and the only way to tell was a SQL query against Flowise.
--
-- The digest is of the flow as it would be built right now: template, slot
-- content, system prompt, course and the .env-derived values, before
-- credential ids are stamped in. Recomputable without calling Flowise, which
-- is what lets a page render "this one is behind" without ten round trips.
ALTER TABLE agent_slots ADD COLUMN IF NOT EXISTS imported_digest text;

-- Kept beside it because "behind since when" is the first question after
-- "behind", and because a slot imported before this migration has a NULL
-- digest that must not read as "up to date".
ALTER TABLE agent_slots ADD COLUMN IF NOT EXISTS imported_at timestamptz;
