"""
Where a launch finds its chatflow.

Until 0.3.0 this came from the environment: `agents.json` named a variable
per agent, `CHATFLOW_AGENT01` and so on, and the Content Admin wrote those
variables when an agent was imported. Two consequences followed from that,
and both are removed here.

  * The variables were written for one course only — the oldest, chosen by
    `_first_course_id()` in the Content Admin — because there is one variable
    per slot and no room for a course in the name. A second course could
    import its agents and this service would never learn their chatflows.

  * `AGENT_CONFIG` was built once when the module was imported, so a newly
    imported agent needed the container recreated before a launch could reach
    it. Nothing said so, and a launch in the meantime resolved to an empty
    chatflow id.

The chatflow ids live in `agent_slots`, which the Content Admin writes on
every import. Reading them here at request time means a launch always finds
what was imported last, and it means the course can be part of the key.

The course is not yet chosen per launch — the caller passes the oldest one,
which is exactly the set of chatflows the environment variables used to hold.
Deriving it from the LTI context claim is the next step, and it changes the
caller rather than this module.

Connection details come from the environment because compose passes the whole
of `.env` to this container. That is deliberately not a copy of the Content
Admin's `db.py`: this service reads two columns and never writes, so it needs
neither migrations nor its own error vocabulary.
"""

import logging
import os

import psycopg
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


class SlotLookupError(RuntimeError):
    """The database could not be asked. Distinct from "no such slot", which is
    a normal answer and returns None."""


def _dsn() -> str:
    explicit = os.getenv("SMARTRAG_DB_DSN", "").strip()
    if explicit:
        return explicit
    user = os.getenv("POSTGRES_USER", "").strip()
    password = os.getenv("POSTGRES_PASSWORD", "").strip()
    if not user or not password:
        raise SlotLookupError(
            "POSTGRES_USER or POSTGRES_PASSWORD is not set for this container, "
            "so the LTI middleware cannot look up which chatflow an agent uses."
        )
    host = os.getenv("SMARTRAG_DB_HOST", "smartrag-postgres")
    port = os.getenv("SMARTRAG_DB_PORT", "5432")
    name = os.getenv("SMARTRAG_DB_NAME", "contentadmin")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def _get_pool() -> ConnectionPool:
    # Two connections: this service answers a launch and a chat page, both
    # short, against a Postgres that also carries Flowise, n8n and Langfuse.
    global _pool
    if _pool is None:
        _pool = ConnectionPool(_dsn(), min_size=1,
                               max_size=int(os.getenv("LTI_DB_POOL_MAX", "2")),
                               timeout=10, open=True)
    return _pool


def _query(sql: str, args: tuple):
    try:
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchone()
    except SlotLookupError:
        raise
    except psycopg.Error as exc:
        raise SlotLookupError(f"The database did not answer: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — the pool raises several types
        raise SlotLookupError(f"The database could not be reached: {exc}") from exc


def default_course_id() -> str | None:
    """The oldest course, which is the one whose chatflows the environment
    variables used to carry. Replaced by the course of the launch once the
    LTI context claim is mapped."""
    row = _query("SELECT id FROM courses ORDER BY created_at, id LIMIT 1", ())
    return row[0] if row else None


def chatflow_for(course_id: str, slot: int) -> str | None:
    """The chatflow of one slot, or None if the slot is empty or the course
    does not exist. An empty string in the column counts as None: a slot that
    was configured but never imported has no chatflow to launch into."""
    if not course_id or not slot:
        return None
    row = _query(
        "SELECT chatflow_id FROM agent_slots WHERE course_id = %s AND slot = %s",
        (course_id, slot),
    )
    if not row:
        return None
    return (row[0] or "").strip() or None
