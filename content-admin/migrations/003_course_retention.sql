-- How long a course's data may be kept.
--
-- The data protection officer's green light came with the obligation that
-- makes this column necessary: personal data is kept for a stated period, not
-- indefinitely, and somebody has to be able to say what that period is for
-- each course. A retention period decided at creation is one that exists; one
-- decided when the question is asked is one that gets set to "as long as we
-- need".
--
-- A date, not an interval. "Two years" has to be counted from something, and
-- the something differs — a semester course from its end, a study from its
-- last data collection, a course rerun each term from whichever run this is.
-- Storing the answer instead of the arithmetic means nobody has to reconstruct
-- which start date was meant.
--
-- Nullable on purpose, and it is not the same as "keep forever": NULL means
-- nobody has said yet. The dashboard can then ask, which it cannot do about a
-- course that answered "no limit" — a distinction that disappears the moment
-- this defaults to anything.
ALTER TABLE courses ADD COLUMN IF NOT EXISTS retention_until date;

-- Why that date, in the operator's own words. Not for the software: for the
-- next person, who will find a date two years out with no idea whether it
-- came from an ethics application, a funding period or a guess.
ALTER TABLE courses ADD COLUMN IF NOT EXISTS retention_note text;

-- When the retention period was last acted on, and by whom. An expiry that
-- has been dealt with and one that has not look identical otherwise, and the
-- dashboard would go on warning about a course whose learner data was erased
-- last week.
ALTER TABLE courses ADD COLUMN IF NOT EXISTS retention_applied_at timestamptz;

-- A date in the past at the moment it is set is a data-entry slip, not a
-- retention period, and it would raise an expiry warning the same day. The
-- check is deliberately not "later than now" — that would make every existing
-- row unupdatable the day after its date passes, including the row whose
-- expiry is being recorded as handled.
-- Guarded, because Postgres has no ADD CONSTRAINT IF NOT EXISTS for a CHECK
-- and every other statement in this file is repeatable. A migration is
-- recorded once, so this should never run twice — but "should never" is how a
-- half-applied migration becomes a file nobody dares re-run.
DO $$
BEGIN
    ALTER TABLE courses ADD CONSTRAINT courses_retention_sane
        CHECK (retention_until IS NULL OR retention_until > DATE '2020-01-01');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

-- The dashboard's question is "which courses are due", asked on every load.
CREATE INDEX IF NOT EXISTS courses_retention_idx
    ON courses (retention_until) WHERE retention_until IS NOT NULL;
