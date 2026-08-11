"""
Agent-slot state: which archetype and content sit in slots 1–10 *of a course*,
and the Flowise chatflow id once imported.

This was a JSON file with ten entries and no course. Both had to go:

  * The course, because slots belong to one. Without it, ten slots are all
    there is, and a second course would silently share the first one's
    agents. Every function here therefore takes the course first, and takes
    it as an argument rather than reading it from a session or an
    environment variable — a default course is how a wrong-course write
    happens without anybody passing the wrong value.
  * The file, because several maintainers now write at once and a JSON file
    is rewritten whole. Two saves in the same second and one of them is
    simply gone, with nothing to show for it.

The reasoning that survives from the file version: `system_prompt` is stored
as an override, not as a copy of the archetype's default, so a slot nobody
edited keeps tracking the template and improvements to it reach existing
agents. `content` stays a single blob because its shape belongs to the
archetype and changes with it; a column per field would mean a migration
every time a template gains a question.
"""

import json

import db

MAX_SLOTS = 10


class SlotError(RuntimeError):
    """A slot operation that cannot be carried out, phrased for the person who
    triggered it."""


def _row_to_slot(row) -> dict:
    return {
        "archetype": row[0],
        "name": row[1],
        "content": row[2] or {},
        "system_prompt": row[3],
        "chatflow_id": row[4],
        "published": row[5],
    }


def all_slots(course_id: str) -> dict[str, dict]:
    """Slots "1".."10" of one course, each either {} (unconfigured) or the
    saved values. The empty ones are included so a caller can render ten rows
    without knowing how many exist."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT slot, archetype, name, content, system_prompt, "
                "       chatflow_id, published "
                "FROM agent_slots WHERE course_id = %s", (course_id,))
            rows = {r[0]: _row_to_slot(r[1:]) for r in cur.fetchall()}
        conn.commit()
    return {
        str(i): (rows[i] if i in rows and rows[i]["archetype"] else {})
        for i in range(1, MAX_SLOTS + 1)
    }


def get_slot(course_id: str, slot: int) -> dict:
    return all_slots(course_id).get(str(slot), {})


def name_taken(course_id: str, name: str, exclude_slot: int) -> bool:
    """Case-insensitive, and scoped to the course — the same agent name in two
    different courses is normal, and forbidding it would make every course
    after the first name its agents around the others."""
    normalized = name.strip().casefold()
    if not normalized:
        return False
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM agent_slots "
                "WHERE course_id = %s AND slot <> %s AND name IS NOT NULL "
                "  AND lower(name) = %s LIMIT 1",
                (course_id, exclude_slot, normalized))
            found = cur.fetchone() is not None
        conn.commit()
    return found


def save_slot(course_id: str, slot: int, archetype: str,
              content: dict[str, str], name: str,
              system_prompt: str | None = None) -> None:
    if not (1 <= slot <= MAX_SLOTS):
        raise ValueError(f"slot must be 1..{MAX_SLOTS}, got {slot}")
    with db.connect() as conn:
        with conn.cursor() as cur:
            # The row already exists — creating a course creates its ten empty
            # slots — so this is an update, and the chatflow id is left alone
            # rather than being carried across by the caller.
            cur.execute(
                "UPDATE agent_slots SET archetype = %s, name = %s, "
                "       content = %s, system_prompt = %s, updated_at = now() "
                "WHERE course_id = %s AND slot = %s",
                (archetype, name, json.dumps(content), system_prompt,
                 course_id, slot))
            if cur.rowcount == 0:
                raise SlotError(
                    f"Course {course_id!r} has no slot {slot}. Either the "
                    "course does not exist or its creation did not finish — "
                    "the Courses page lists unfinished ones."
                )
        conn.commit()


def set_chatflow_id(course_id: str, slot: int, chatflow_id: str) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_slots SET chatflow_id = %s, updated_at = now() "
                "WHERE course_id = %s AND slot = %s AND archetype IS NOT NULL",
                (chatflow_id, course_id, slot))
            if cur.rowcount == 0:
                raise SlotError(f"slot {slot} has no saved content yet")
        conn.commit()


def set_published(course_id: str, slot: int, published: bool) -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_slots SET published = %s, updated_at = now() "
                "WHERE course_id = %s AND slot = %s",
                (published, course_id, slot))
        conn.commit()


def course_of_chatflow(chatflow_id: str) -> str | None:
    """Which course an imported agent belongs to.

    The lookup n8n does not have and the Content Admin does. It exists here
    rather than being derived elsewhere because the mapping is a column: one
    chatflow belongs to one slot, and the unique index on chatflow_id is what
    makes that a fact rather than an assumption.
    """
    if not chatflow_id:
        return None
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT course_id FROM agent_slots WHERE chatflow_id = %s",
                        (chatflow_id,))
            row = cur.fetchone()
        conn.commit()
    return row[0] if row else None
