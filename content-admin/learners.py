"""
One person, across every system that holds anything about them.

A learner is not an account. Nothing in this application's own `users` table
is a student — that table holds the people who maintain courses. A learner
exists only as a pseudonymous id handed over by the LMS, and that id is
scattered across four systems under three different names:

    Weaviate     user_id      ChatHistory, UserMemory, TestResults
    Flowise      sessionId    "<learner>|<something>" — the part before "|"
    Flowise      chatId       a conversation, which is *not* the learner
    Langfuse     sessionId    equals Flowise's chatId, and nothing else

Two of those are worth spelling out, because getting either wrong produces a
deletion that reports success and leaves data behind.

**The learner id is the part of Flowise's session id before the first "|".**
Not inferred: every agent derives it that way
(`$flow.sessionId?.split('|')[0]`, in all six agent templates), and the
chathistory-sync workflow stores the same split as `user_id`. A session id
with no "|" is the whole id, which is what the split does too.

**What a Langfuse trace is keyed by depends on how the chat was opened**, and
there is no way to tell after the fact. Flowise 3.1.3 builds its trace with
`{ name, sessionId: this.options.chatId, ...nodeData.inputs.analytics.langFuse }`
— the override is spread *over* the defaults, at both of the two places that
construct one, and `analytics` is the one overrideConfig key Flowise applies
without it having to be enabled per node. So:

  * launched by the LTI middleware, which sends
    `analytics.langFuse = {userId, sessionId}`, the trace carries the learner
    as `userId` and the middleware's own string as `sessionId`;
  * launched without it, the trace carries no learner at all and its
    `sessionId` is Flowise's `chatId`.

So an erasure asks along all three routes, and none of them may be the only
one. Two of the three run through Flowise's chat records, which means they
have to be read *before* anything in Flowise is deleted — exactly as a course
deletion does.

What is deliberately **not** here:

  * **Neo4j** holds concepts of a course, extracted from its documents. No
    node carries a learner.
  * **Garage** holds the course's documents. A learner uploads nothing.
  * **Postgres** holds courses, accounts and slots. A learner is in none of
    them.

Saying so is part of the answer: "we looked and there is nothing" is what
makes an erasure record complete, and a system quietly missing from the list
is indistinguishable from one that was forgotten.
"""

import logging
import os

from env_file import read_env
from flowise_client import FlowiseClient, FlowiseError
from langfuse_client import LangfuseClient, LangfuseError
from weaviate_client import WeaviateClient, WeaviateError

import courses
import db

logger = logging.getLogger(__name__)

# Flowise's session id is "<learner>|<something>". The separator is not a
# guess and not a setting: it is what the agent templates split on.
SEPARATOR = "|"


class LearnerError(RuntimeError):
    """Said in terms of the person and the system, never psycopg's or
    requests' own wording where we already know the cause."""


def lti_configured(env: dict | None = None) -> bool:
    """Whether chats are launched through the LTI middleware.

    This decides what an erasure can honestly promise. With LTI, the learner
    id is the platform's `sub` claim — one pseudonym per person, stable across
    logins, browsers and devices, so "everything about this person" is a set
    that exists. Without it, Flowise falls back to its own chat id, which the
    embed keeps in the browser's localStorage: a new browser, a private
    window or cleared site data is a new person as far as every record here is
    concerned, and nothing links the old ones to the new.

    So an erasure on a non-LTI installation removes what that one id touched
    and cannot claim to have removed everything about a human being. The page
    that offers it has to say so rather than imply completeness.

    Detected from the compose profile, as the observability profile is: the
    middleware only runs when "lti" is among them.
    """
    env = env if env is not None else read_env()
    return "lti" in (env.get("COMPOSE_PROFILES") or "")


def learner_of(session_id: str) -> str:
    """The learner behind one Flowise session id.

    Mirrors `$flow.sessionId?.split('|')[0]` exactly, including the case with
    no separator at all — a chat opened outside the LMS has a bare session
    id, and treating that as "no learner" would hide those conversations from
    an erasure.
    """
    return (session_id or "").split(SEPARATOR)[0]


# ─── Clients ─────────────────────────────────────────────────────────────────
# Built here rather than by each caller, and each inside the try of whatever
# uses it: a client that raises in its constructor — Garage without a token,
# Langfuse without keys — must produce one unknown line, never a page that
# fails to render.

def _weaviate(env: dict) -> WeaviateClient:
    return WeaviateClient(
        os.getenv("SMARTRAG_WEAVIATE_URL",
                  f"http://smartrag-weaviate:{env.get('WEAVIATE_HTTP_PORT', '8080')}"),
        env.get("WEAVIATE_API_KEY", ""))


def _flowise(env: dict) -> FlowiseClient:
    return FlowiseClient(
        os.getenv("SMARTRAG_FLOWISE_URL",
                  f"http://smartrag-flowise:{env.get('FLOWISE_PORT', '3000')}"),
        env.get("FLOWISE_API_KEY", ""))


def _chatflow_ids(course_id: str | None) -> list[str]:
    """The chatflows to search. One course's, or every course's.

    Read from this application's own table rather than from Flowise: a
    chatflow that exists in Flowise but belongs to no slot is not part of any
    course and is not this system's to touch.
    """
    # Ordered, so a person's inventory lists their agents the same way twice
    # running. Without it the order follows the physical rows and changes
    # after any unrelated update.
    sql = ("SELECT chatflow_id FROM agent_slots "
           "WHERE chatflow_id IS NOT NULL")
    args: tuple = ()
    if course_id:
        sql += " AND course_id = %s"
        args = (course_id,)
    sql += " ORDER BY course_id, slot"
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            ids = [r[0] for r in cur.fetchall()]
        conn.commit()
    return ids


def sessions_of(user_id: str, course_id: str | None = None,
                flowise=None) -> list[dict]:
    """Every Flowise conversation belonging to one learner.

    Returns [{chatflow_id, session_id, chat_id}]. The chat ids are the bridge
    to Langfuse and are collected here, while the records still exist.

    A chatflow that cannot be read raises rather than being skipped. This
    feeds an erasure, and "we could not read one of the eleven chatflows" has
    to stop it — silently erasing ten of eleven is the failure mode that
    reads as success.
    """
    if not user_id:
        raise LearnerError("Refusing to search without a learner id.")
    env = read_env()
    flowise = flowise or _flowise(env)
    found: list[dict] = []
    for chatflow_id in _chatflow_ids(course_id):
        for record in flowise.chat_records(chatflow_id):
            if learner_of(record["session_id"]) == user_id:
                found.append({"chatflow_id": chatflow_id, **record})
    return found


# ─── What is held about a person ─────────────────────────────────────────────

def inventory(user_id: str, course_id: str | None = None,
              weaviate=None, flowise=None) -> dict:
    """Everything held about one learner, counted where it can be counted.

    Read-only, and answerable on its own: "what do you have about me" is a
    question somebody may ask without asking for a deletion, and it has to be
    answerable without performing one.

    Every system is asked separately and a failure is recorded against its
    own line rather than raised — an inventory that stops at the first
    unreachable service says less than one that names the single line it
    could not establish.
    """
    if not user_id:
        raise LearnerError("Refusing to look up a learner without an id.")
    if course_id and courses.get_course(course_id) is None:
        raise LearnerError(f"No course '{course_id}' is recorded.")

    env = read_env()
    items: list[courses.InventoryItem] = []

    # ── Weaviate: the three classes that carry a learner ────────────────────
    for cls in WeaviateClient.SHARED_LEARNER_CLASSES:
        try:
            weaviate = weaviate or _weaviate(env)
            items.append(courses.InventoryItem(
                "weaviate", "inv_learner_class", args=(cls,),
                count=weaviate.count_by_learner(cls, user_id, course_id),
                note="inv_learner_class_note"))
        except WeaviateError as exc:
            items.append(courses.InventoryItem(
                "weaviate", "inv_learner_class", None, str(exc), args=(cls,)))

    # ── Flowise: the conversations themselves ───────────────────────────────
    sessions: list[dict] = []
    try:
        flowise = flowise or _flowise(env)
        sessions = sessions_of(user_id, course_id, flowise)
        items.append(courses.InventoryItem(
            "flowise", "inv_learner_sessions", len(sessions),
            note="inv_learner_sessions_note"))
    except (FlowiseError, db.DatabaseError) as exc:
        items.append(courses.InventoryItem(
            "flowise", "inv_learner_sessions", None, str(exc)))

    # ── Langfuse: reachable only through those conversations ────────────────
    if LangfuseClient.configured(env):
        chat_ids = [s["chat_id"] for s in sessions if s["chat_id"]]
        items.append(courses.InventoryItem(
            "langfuse", "inv_learner_traces", len(chat_ids),
            note="inv_learner_traces_note"))

    # ── The three systems that hold nothing, said out loud ──────────────────
    for system, label in (("neo4j", "inv_learner_none_graph"),
                          ("garage", "inv_learner_none_storage"),
                          ("postgres", "inv_learner_none_accounts")):
        items.append(courses.InventoryItem(system, label, 0,
                                           note="inv_learner_none_note"))

    return {"user_id": user_id, "course_id": course_id,
            "sessions": sessions, "items": items}


# ─── Erasing a person ────────────────────────────────────────────────────────

def erase(user_id: str, course_id: str | None = None,
          weaviate=None, flowise=None, langfuse=None) -> dict:
    """Remove everything held about one learner.

    **The order is the same one a course deletion obeys, for the same
    reason.** The chat ids are read first, while Flowise still has them,
    because they are the only route to the traces; Langfuse is asked next;
    Flowise's own records go after that; Weaviate last, because nothing else
    depends on them.

    `course_id` narrows the erasure to one course — which is what a retention
    period expiring means. Without one it is everything, everywhere, which is
    what an erasure request means. The two are deliberately the same code
    path: a second implementation of "delete a person" is a second place for
    a system to be forgotten.

    Returns {"steps": [...], "erased": bool}. Nothing raises: an erasure that
    stops at the first problem leaves the operator knowing less than one that
    tries everything and says which parts did not work. `erased` is False if
    any step failed, and the operation is safe to run again — every step is
    idempotent, and a second run over already-empty systems reports zeros.
    """
    if not user_id:
        raise LearnerError("Refusing to erase without a learner id.")
    if course_id and courses.get_course(course_id) is None:
        raise LearnerError(f"No course '{course_id}' is recorded.")

    env = read_env()
    steps: list[courses.DeletionStep] = []

    # ── 1. The bridge to Langfuse, while it still exists ────────────────────
    sessions: list[dict] = []
    bridge_ok = False
    try:
        flowise = flowise or _flowise(env)
        sessions = sessions_of(user_id, course_id, flowise)
        bridge_ok = True
        steps.append(courses.DeletionStep(
            "flowise", "read the learner's conversations",
            detail=f"{len(sessions)} session(s)"))
    except (FlowiseError, db.DatabaseError, LearnerError) as exc:
        steps.append(courses.DeletionStep(
            "flowise", "read the learner's conversations", False, error=str(exc)))

    # ── 2. Langfuse, which deletes on its own time ──────────────────────────
    if not LangfuseClient.configured(env):
        steps.append(courses.DeletionStep(
            "langfuse", "no observability profile — nothing to delete",
            detail="skipped"))
    elif not bridge_ok:
        # Not "0 traces": the chat ids are unknown, so whether there are
        # traces is unknown too, and a step that says nothing was there would
        # be a claim this code cannot make.
        steps.append(courses.DeletionStep(
            "langfuse", "delete the traces", False,
            error="The conversations could not be read, so the traces they "
                  "point at could not be identified."))
    else:
        try:
            langfuse = langfuse or LangfuseClient()
            # Three routes, because which one finds anything depends on how
            # the learner opened the chat, and an erasure may not depend on
            # knowing that.
            #
            #   userId     — only set when the LTI middleware launched the
            #                chat, which sends
            #                overrideConfig.analytics.langFuse = {userId,
            #                sessionId} and Flowise spreads it over its
            #                defaults. The direct route, when it exists.
            #   sessionId  — the middleware's own string, which is what the
            #                trace's sessionId then is.
            #   chatId     — Flowise's default when nothing overrode it.
            #
            # Overlapping on purpose: the same trace found twice costs one id
            # in a set, and a route missing costs the whole erasure.
            trace_ids: set[str] = set(langfuse.trace_ids_for_user(user_id))
            for key in ("session_id", "chat_id"):
                for value in {s[key] for s in sessions if s.get(key)}:
                    trace_ids.update(langfuse.trace_ids_for_session(value))
            asked = langfuse.delete_traces(sorted(trace_ids)) if trace_ids else 0
            # "asked to delete", never "deleted": Langfuse removes trace data
            # asynchronously, within about fifteen minutes, and confirms
            # nothing.
            steps.append(courses.DeletionStep(
                "langfuse", "asked Langfuse to delete the traces",
                detail=f"{asked} trace(s)"))
        except LangfuseError as exc:
            steps.append(courses.DeletionStep(
                "langfuse", "delete the traces", False, error=str(exc)))

    # ── 3. Flowise: the conversations, one session at a time ────────────────
    # Not the chatflow — it belongs to the course and to everyone else in it.
    if bridge_ok:
        removed, failed = 0, []
        for session_id in {s["session_id"] for s in sessions}:
            for chatflow_id in {s["chatflow_id"] for s in sessions
                                if s["session_id"] == session_id}:
                try:
                    flowise.delete_chat_session(chatflow_id, session_id)
                    removed += 1
                except FlowiseError as exc:
                    failed.append(f"{chatflow_id}: {exc}")
        if failed:
            steps.append(courses.DeletionStep(
                "flowise", "delete the conversations", False,
                detail=f"{removed} deleted", error="; ".join(failed)))
        else:
            steps.append(courses.DeletionStep(
                "flowise", "deleted the conversations",
                detail=f"{removed} session(s)"))

    # ── 4. Weaviate: history, learning record, test results ─────────────────
    for cls in WeaviateClient.SHARED_LEARNER_CLASSES:
        try:
            weaviate = weaviate or _weaviate(env)
            count = weaviate.delete_by_learner(cls, user_id, course_id)
            steps.append(courses.DeletionStep(
                "weaviate", f"deleted from {cls}", detail=f"{count} object(s)"))
        except WeaviateError as exc:
            steps.append(courses.DeletionStep(
                "weaviate", f"delete from {cls}", False, error=str(exc)))

    erased = all(s["ok"] for s in steps)
    scope = f"course {course_id}" if course_id else "every course"
    if erased:
        logger.info("Erased learner %s from %s: %s", user_id, scope,
                    "; ".join(f"{s['system']} {s['action']}" for s in steps))
    else:
        for failed_step in (s for s in steps if not s["ok"]):
            logger.error("Erasing learner %s from %s: %s %s failed: %s",
                         user_id, scope, failed_step["system"],
                         failed_step["action"], failed_step["error"])
    return {"steps": steps, "erased": erased,
            "user_id": user_id, "course_id": course_id}
