"""Runs of the concept-map build: one row each, from queued to reviewed.

A build is long, it happens outside the request that started it, and it costs
real money. So its state has to survive a page reload, a container restart and
the operator going home — and the proposal it produces is stored rather than
shown once, because it is read by a person, possibly not the one who started
it, and possibly not that day.

The state machine is deliberately small:

    queued → running → proposed → applied
                   ↘ failed

`proposed` is not `applied`. That gap is the whole safety of this feature:
nothing reaches Neo4j until a person submits the review.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import db

STATES = ("queued", "running", "proposed", "applied", "failed")
ACTIVE = ("queued", "running")


class BuildError(RuntimeError):
    """Something about a build that the caller has to be told, in words."""


def _row(r) -> dict:
    return {
        "id": r[0], "course_id": r[1], "state": r[2],
        "scope": r[3] or [], "stats": r[4] or {}, "proposal": r[5],
        "error": r[6], "started_by": r[7], "started_at": r[8],
        "finished_at": r[9], "applied_at": r[10],
        "active": r[2] in ACTIVE,
    }


_COLUMNS = ("id, course_id, state, scope, stats, proposal, error, "
            "started_by, started_at, finished_at, applied_at")


def start(course_id: str, scope: list[int], started_by: str = "") -> dict:
    """Record a new build, or refuse because one is already running.

    The refusal is a unique index rather than a check-then-insert: two clicks
    a second apart would both pass a check, and the second run costs the same
    money as the first while producing a proposal that races the first one to
    be the one the reviewer sees.
    """
    build_id = f"gb-{uuid.uuid4().hex[:16]}"
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO graph_builds (id, course_id, state, scope, started_by) "
                    "VALUES (%s, %s, 'queued', %s, %s)",
                    (build_id, course_id, json.dumps(sorted(scope)), started_by))
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — psycopg's UniqueViolation
        if "graph_builds_one_active_idx" in str(exc):
            raise BuildError(
                "A build is already running for this course. Wait for it to "
                "finish, or cancel it, rather than paying for a second pass "
                "over the same material.") from exc
        raise
    return get(build_id)


def get(build_id: str) -> dict | None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM graph_builds WHERE id = %s",
                        (build_id,))
            row = cur.fetchone()
        conn.commit()
    return _row(row) if row else None


def latest(course_id: str) -> dict | None:
    """The most recent build of a course, whatever became of it."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUMNS} FROM graph_builds WHERE course_id = %s "
                "ORDER BY started_at DESC LIMIT 1", (course_id,))
            row = cur.fetchone()
        conn.commit()
    return _row(row) if row else None


def active(course_id: str) -> dict | None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUMNS} FROM graph_builds WHERE course_id = %s "
                "AND state IN ('queued', 'running') LIMIT 1", (course_id,))
            row = cur.fetchone()
        conn.commit()
    return _row(row) if row else None


def running(build_id: str, stats: dict[str, Any] | None = None) -> bool:
    """The workflow reporting that it has begun, and how far it has come."""
    return _advance(build_id, "running", stats=stats)


def propose(build_id: str, proposal: dict, stats: dict | None = None) -> bool:
    """The finished proposal, stored for review. Still nothing written."""
    return _advance(build_id, "proposed", proposal=proposal, stats=stats,
                    finished=True)


def fail(build_id: str, message: str) -> bool:
    return _advance(build_id, "failed", error=message[:4000], finished=True)


def applied(build_id: str) -> bool:
    return _advance(build_id, "applied", mark_applied=True)


def _advance(build_id: str, state: str, proposal: dict | None = None,
             stats: dict | None = None, error: str | None = None,
             finished: bool = False, mark_applied: bool = False) -> bool:
    """Move one build along, refusing to move one that is already finished.

    A late callback from a workflow that was cancelled, or a duplicate
    delivery of one that already reported, must not overwrite a proposal the
    operator is in the middle of reading. `applied` and `failed` are ends.
    """
    if state not in STATES:
        raise BuildError(f"Unknown build state: {state!r}")
    sets = ["state = %s"]
    params: list[Any] = [state]
    if proposal is not None:
        sets.append("proposal = %s")
        params.append(json.dumps(proposal))
    if stats is not None:
        sets.append("stats = %s")
        params.append(json.dumps(stats))
    if error is not None:
        sets.append("error = %s")
        params.append(error)
    if finished:
        sets.append("finished_at = now()")
    if mark_applied:
        sets.append("applied_at = now()")
    params.append(build_id)
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE graph_builds SET {', '.join(sets)} "
                "WHERE id = %s AND state NOT IN ('applied', 'failed')",
                tuple(params))
            changed = cur.rowcount
        conn.commit()
    return changed > 0


def forget_course(course_id: str) -> int:
    """Builds go with their course. The cascade in the schema does this too;
    this exists for the caller that deletes a course's traces explicitly."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM graph_builds WHERE course_id = %s",
                        (course_id,))
            n = cur.rowcount
        conn.commit()
    return n
