"""
Courses as objects: creating one, listing them, finishing a half-created one.

Creating a course is not a row. It is a Weaviate collection, a Garage bucket,
a grant of the ingest key on that bucket, and ten slot rows — four things
across three systems, any of which can fail with the others already done.

**The order is the design.** The row is written first, with `provisioned_at`
still NULL, and only set once every side effect is in place. The plan for
this phase said the opposite — side effects first, then the record — and that
is worse: a crash between the collection and the bucket then leaves a
collection nobody has a record of, and the next attempt with the same name
either collides with it or silently adopts it. Writing the intent first costs
one row that may need cleaning up; writing it last costs orphans in two other
systems with nothing pointing at them.

So a course is in one of three states, and all three are visible:

  * no row               — nothing happened, the name is free
  * row, provisioned_at NULL — creation started and did not finish; the GUI
                           shows it as incomplete and `provision()` resumes
  * row, provisioned_at set  — usable

Every step is idempotent, because "resume" means running the whole thing
again and having the finished parts say so.
"""

import logging
import os
import re

import db
from garage_client import GarageClient, GarageError
from langfuse_client import LangfuseClient, LangfuseError
from weaviate_client import WeaviateClient, WeaviateError
from env_file import read_env

logger = logging.getLogger(__name__)

SLOTS_PER_COURSE = 10

# Same shape the schema enforces. Checked here too so the GUI can say what is
# wrong with a name before the database refuses it in less friendly words.
COURSE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")

SCHEMA_TEMPLATE = os.getenv("SMARTRAG_SCHEMA_PATH", "/app/weaviate/schema.json")


class CourseError(RuntimeError):
    """Something an operator can act on, with the half-done state named."""


def collection_for(course_id: str) -> str:
    """Weaviate class names must start with a capital and cannot contain a
    hyphen, so the course id cannot be used directly."""
    return "Chunks_" + course_id.replace("-", "_")


def bucket_for(course_id: str) -> str:
    """The convention the single-course installation already used, so an
    installation migrating into this keeps its bucket name."""
    return f"{course_id}-rag"


# ─── Reading ─────────────────────────────────────────────────────────────────

def all_courses() -> list[dict]:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, collection, bucket, created_at, provisioned_at "
                "FROM courses ORDER BY name"
            )
            rows = cur.fetchall()
        conn.commit()
    return [
        {"id": r[0], "name": r[1], "collection": r[2], "bucket": r[3],
         "created_at": r[4], "provisioned_at": r[5], "ready": r[5] is not None}
        for r in rows
    ]


def get_course(course_id: str) -> dict | None:
    return next((c for c in all_courses() if c["id"] == course_id), None)


# ─── Creating ────────────────────────────────────────────────────────────────

def create_course(course_id: str, name: str,
                  weaviate: WeaviateClient | None = None,
                  garage: GarageClient | None = None) -> dict:
    """Record the intent, then provision. Raises CourseError with what is
    already in place when a step fails."""
    course_id = (course_id or "").strip().lower()
    name = (name or "").strip()

    if not COURSE_ID.match(course_id):
        raise CourseError(
            f"'{course_id}' is not a usable course id. Lower-case letters, "
            "digits and hyphens, starting with a letter or digit, 2–63 "
            "characters — it becomes a bucket name and part of a collection "
            "name, and neither accepts anything else."
        )
    if not name:
        raise CourseError("A course needs a name — it is what maintainers see.")

    existing = get_course(course_id)
    if existing and existing["ready"]:
        raise CourseError(f"A course '{course_id}' already exists.")

    if not existing:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO courses (id, name, collection, bucket) "
                    "VALUES (%s, %s, %s, %s)",
                    (course_id, name, collection_for(course_id), bucket_for(course_id)),
                )
                # The slots exist from the start, empty. A course with no slot
                # rows and a course with ten empty ones look the same to a
                # maintainer, and creating them lazily means every later write
                # has to consider whether the row is there.
                for slot in range(1, SLOTS_PER_COURSE + 1):
                    cur.execute(
                        "INSERT INTO agent_slots (course_id, slot) VALUES (%s, %s)",
                        (course_id, slot),
                    )
            conn.commit()

    return provision(course_id, weaviate=weaviate, garage=garage)


def provision(course_id: str,
              weaviate: WeaviateClient | None = None,
              garage: GarageClient | None = None) -> dict:
    """Bring a recorded course fully into being. Safe to call on one that is
    already finished, and safe to call again after a failure."""
    course = get_course(course_id)
    if course is None:
        raise CourseError(f"No course '{course_id}' is recorded, so there is "
                          "nothing to provision.")

    env = read_env()
    weaviate = weaviate or WeaviateClient(
        os.getenv("SMARTRAG_WEAVIATE_URL",
                  f"http://smartrag-weaviate:{env.get('WEAVIATE_HTTP_PORT', '8080')}"),
        env.get("WEAVIATE_API_KEY", ""),
    )
    garage = garage or GarageClient()
    access_key = env.get("GARAGE_ACCESS_KEY", "").strip()
    if not access_key:
        raise CourseError(
            "GARAGE_ACCESS_KEY is missing from .env, so the ingest key cannot "
            "be granted on the course's bucket. Without the grant the bucket "
            "exists and accepts nothing."
        )

    done: list[str] = []
    try:
        if weaviate.create_collection(course["collection"], SCHEMA_TEMPLATE):
            done.append(f"collection {course['collection']}")
        else:
            done.append(f"collection {course['collection']} (already there)")

        bucket = garage.create_bucket(course["bucket"])
        done.append(f"bucket {course['bucket']}")

        bucket_id = bucket.get("id")
        if not bucket_id:
            raise CourseError(
                f"Garage returned no id for bucket {course['bucket']}, so the "
                "ingest key cannot be granted on it."
            )
        garage.allow_key(bucket_id, access_key)
        done.append("ingest key granted")

        # Verified, not assumed. A bucket and a key both existing while the
        # grant between them is missing is the failure that shows up much
        # later as an upload that stores nothing.
        if not garage.key_can_write(course["bucket"], access_key):
            raise CourseError(
                f"The ingest key is still not allowed to write to "
                f"{course['bucket']}. The bucket exists; uploads would be "
                "accepted and stored nowhere."
            )
    except (WeaviateError, GarageError, CourseError) as exc:
        raise CourseError(
            f"Course '{course_id}' is recorded but not finished. Done so far: "
            f"{', '.join(done) if done else 'nothing'}. Failed at: {exc}\n"
            "The course is listed as incomplete; fixing the cause and "
            "provisioning again continues from here."
        ) from exc

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE courses SET provisioned_at = now() WHERE id = %s",
                        (course_id,))
        conn.commit()

    logger.info("Course %s provisioned: %s", course_id, "; ".join(done))
    return get_course(course_id)


def unfinished() -> list[dict]:
    """Courses whose creation did not complete. The GUI lists these
    separately: a course that looks like the others but has no bucket is how
    an upload fails an hour later with a message about object storage."""
    return [c for c in all_courses() if not c["ready"]]


# ─── What a course consists of ───────────────────────────────────────────────

class InventoryItem(dict):
    """One line of the inventory: a system, what it holds, and how many.

    `count` is None when the number could not be established. That is not the
    same as zero, and the difference decides whether a deletion is safe to
    start: a service that is down answers "nothing here" to a naive
    implementation, and the operator then confirms a deletion believing there
    is nothing to lose.
    """

    def __init__(self, system: str, label: str, count: int | None = None,
                 error: str = "", note: str = "", unknowable: bool = False,
                 args: tuple = (), note_args: tuple = ()):
        # `label` and `note` are i18n keys, not sentences. This table is read
        # by the person deciding whether to delete a course, and it used to
        # arrive in English on a German page — the one place in this
        # application where that happened, because these strings are built
        # here rather than in a template.
        # `unknowable` separates two things that both leave count at None and
        # mean opposite things to the operator: a system that did not answer,
        # which may hold data and warrants stopping — and a number that cannot
        # exist, like Langfuse traces, which carry no course by design.
        # Counting the second as a failure sends somebody to fix a service
        # that is working.
        super().__init__(system=system, label=label, count=count,
                         error=error, note=note, unknowable=unknowable,
                         args=args, note_args=note_args)


def inventory(course_id: str,
              weaviate: WeaviateClient | None = None,
              garage: GarageClient | None = None,
              neo4j=None,
              flowise=None) -> dict:
    """Everything that belongs to one course, counted where it can be counted.

    Read-only. Called before a deletion so the operator confirms a number
    rather than a name, and callable on its own — "what is in this course"
    is a fair question with no intention behind it.

    Every system is asked separately and a failure in one is recorded against
    that line rather than raised: an inventory that stops at the first
    unreachable service tells the operator less than one that says which
    single line is unknown.
    """
    course = get_course(course_id)
    if course is None:
        raise CourseError(f"No course '{course_id}' is recorded.")

    env = read_env()
    items: list[InventoryItem] = []

    # ── Postgres: rows that go automatically ────────────────────────────────
    # agent_slots and user_courses reference courses(id) ON DELETE CASCADE, so
    # these need no deletion step — but they are listed, because "the course
    # row disappears and so do ten slots" is part of what is being confirmed.
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FILTER (WHERE archetype IS NOT NULL), "
                    "       count(*) FILTER (WHERE chatflow_id IS NOT NULL) "
                    "FROM agent_slots WHERE course_id = %s", (course_id,))
                configured, imported = cur.fetchone()
                cur.execute("SELECT count(*) FROM user_courses WHERE course_id = %s",
                            (course_id,))
                (members,) = cur.fetchone()
                # An account whose only course this is keeps existing and can
                # then reach nothing. Deleting it is a separate decision — a
                # person is not a property of a course — so it is reported,
                # not acted on.
                cur.execute(
                    "SELECT count(*) FROM user_courses uc "
                    "WHERE uc.course_id = %s AND NOT EXISTS ("
                    "  SELECT 1 FROM user_courses o "
                    "  WHERE o.user_id = uc.user_id AND o.course_id <> uc.course_id)",
                    (course_id,))
                (stranded,) = cur.fetchone()
            conn.commit()
        items.append(InventoryItem("postgres", "inv_slots_configured", configured))
        items.append(InventoryItem("postgres", "inv_slots_imported", imported))
        items.append(InventoryItem("postgres", "inv_maintainers", members))
        if stranded:
            items.append(InventoryItem(
                "postgres", "inv_maintainers_stranded", stranded,
                note="inv_maintainers_stranded_note"))
    except db.DatabaseError as exc:
        items.append(InventoryItem("postgres", "inv_slots_and_maintainers", None, str(exc)))

    # ── Weaviate: one collection of its own, three shared classes ───────────
    try:
        weaviate = weaviate or WeaviateClient(
            os.getenv("SMARTRAG_WEAVIATE_URL",
                      f"http://smartrag-weaviate:{env.get('WEAVIATE_HTTP_PORT', '8080')}"),
            env.get("WEAVIATE_API_KEY", ""))
        if weaviate.collection_exists(course["collection"]):
            items.append(InventoryItem(
                "weaviate", "inv_chunks", args=(course["collection"],),
                count=weaviate.count_chunks(course["collection"], course_id),
                note="inv_chunks_note"))
        else:
            items.append(InventoryItem(
                "weaviate", "inv_collection", 0, args=(course["collection"],),
                note="inv_absent_note"))
    except WeaviateError as exc:
        items.append(InventoryItem("weaviate", "inv_chunks_plain", None, str(exc)))

    for cls in WeaviateClient.SHARED_LEARNER_CLASSES:
        try:
            items.append(InventoryItem(
                "weaviate", "inv_shared_class", args=(cls,),
                count=weaviate.count_by_course(cls, course_id),
                note="inv_shared_class_note"))
        except WeaviateError as exc:
            items.append(InventoryItem("weaviate", "inv_shared_class", None, str(exc), args=(cls,)))

    # ── Garage: the bucket, counted by Garage itself ────────────────────────
    # No S3 client is needed to count: GetBucketInfo reports objects and
    # bytes. Emptying it is another matter — the admin API has no object
    # operations and refuses to delete a bucket that is not empty.
    try:
        # Constructed inside the try, not before it: GarageClient raises when
        # the admin token is missing, and a construction failure outside would
        # turn one unknown line into a page that does not render — the exact
        # opposite of what this function is for.
        garage = garage or GarageClient()
        info = garage.bucket_info(course["bucket"])
        if info is None:
            items.append(InventoryItem("garage", "inv_bucket", 0,
                                       args=(course["bucket"],),
                                       note="inv_absent_note"))
        else:
            items.append(InventoryItem(
                "garage", "inv_objects", args=(course["bucket"],),
                count=int(info.get("objects") or 0),
                note="inv_objects_note", note_args=(int(info.get("bytes") or 0),)))
            unfinished_uploads = int(info.get("unfinishedUploads") or 0)
            if unfinished_uploads:
                items.append(InventoryItem(
                    "garage", "inv_unfinished", unfinished_uploads,
                    note="inv_unfinished_note"))
    except GarageError as exc:
        items.append(InventoryItem("garage", "inv_object_storage", None, str(exc)))

    # ── Neo4j: the course's part of the graph ───────────────────────────────
    try:
        if neo4j is None:
            from neo4j_client import Neo4jClient
            neo4j = Neo4jClient(
                os.getenv("SMARTRAG_NEO4J_URL",
                          f"http://smartrag-neo4j:{env.get('NEO4J_HTTP_PORT', '7474')}"),
                "neo4j", env.get("NEO4J_PASSWORD", ""))
        counts = neo4j.counts(course_id)
        items.append(InventoryItem("neo4j", "inv_concepts", int(counts.get("concepts") or 0)))
        items.append(InventoryItem("neo4j", "inv_links",
                                   int(counts.get("edges") or 0)))
    except Exception as exc:  # noqa: BLE001 — Neo4jError, and requests' own
        items.append(InventoryItem("neo4j", "inv_graph", None, str(exc)))

    # ── Flowise: the chatflows, and whether they are still there ────────────
    # The slot table knows which chatflow it created; only Flowise knows
    # whether it still exists. The difference matters at deletion time — an
    # id that is gone is not a failure, it is already done — and it is the
    # one thing here the database cannot answer.
    if flowise is not None:
        try:
            live = {cf.get("id") for cf in flowise.list_chatflows()}
            with db.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT chatflow_id FROM agent_slots "
                        "WHERE course_id = %s AND chatflow_id IS NOT NULL",
                        (course_id,))
                    ids = [r[0] for r in cur.fetchall()]
                conn.commit()
            present = [i for i in ids if i in live]
            items.append(InventoryItem(
                "flowise", "inv_chatflows", len(present),
                note="inv_chatflows_note"))
            if len(ids) > len(present):
                items.append(InventoryItem(
                    "flowise", "inv_chatflows_gone", len(ids) - len(present),
                    note="inv_chatflows_gone_note"))
        except Exception as exc:  # noqa: BLE001 — FlowiseError, and requests' own
            items.append(InventoryItem("flowise", "inv_chatflows_plain", None, str(exc)))

    # ── What this inventory deliberately does not count ─────────────────────
    # Langfuse traces carry a userId and Flowise's chatId, and no course. The
    # only route from a course to its traces runs through Flowise's chat
    # records — which Flowise deletes together with the chatflow, so after a
    # deletion the mapping no longer exists either. Saying so is the honest
    # answer; a zero here would read as "there are none".
    if "observability" in (env.get("COMPOSE_PROFILES") or ""):
        items.append(InventoryItem(
            "langfuse", "inv_traces", None, unknowable=True,
            error="inv_traces_why", note="inv_traces_note"))

    return {"course": course, "items": items}


# ─── Deleting a course ───────────────────────────────────────────────────────

class DeletionStep(dict):
    """One thing that was done, or not, and what it did.

    `ok` False means the step failed; the course row then stays, and so does
    the course in the list — a half-deleted course that still exists is
    recoverable, one whose record is gone is only findable by someone who
    knows all six systems.
    """

    def __init__(self, system: str, action: str, ok: bool = True,
                 detail: str = "", error: str = ""):
        super().__init__(system=system, action=action, ok=ok,
                         detail=detail, error=error)


def delete_course(course_id: str,
                  weaviate: WeaviateClient | None = None,
                  garage: GarageClient | None = None,
                  neo4j=None, flowise=None, langfuse=None,
                  s3=None) -> dict:
    """Remove a course and everything that belongs to it.

    **The order is not a preference.** A Langfuse trace carries a learner id
    and Flowise's chat id, never a course, so the only route from a course to
    its traces runs through Flowise's chat records — and Flowise deletes those
    together with the chatflow. The session ids are therefore collected first,
    before anything is removed, or the traces become unreachable in the same
    moment the course does.

    **The course row goes last, and only if everything else succeeded.** While
    it exists the course is still listed, still has its slots, and the whole
    operation can simply be run again. Deleting it first would turn a failed
    step into orphaned data that nothing points at.

    Returns {"steps": [...], "deleted": bool}. Nothing raises: a deletion that
    aborts on the first problem leaves the operator with less information than
    one that tries everything and says which parts did not work.
    """
    course = get_course(course_id)
    if course is None:
        raise CourseError(f"No course '{course_id}' is recorded.")

    env = read_env()
    steps: list[DeletionStep] = []

    # ── 1. The bridge to Langfuse, while it still exists ────────────────────
    session_ids: list[str] = []
    chatflow_ids: list[str] = []
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chatflow_id FROM agent_slots "
                    "WHERE course_id = %s AND chatflow_id IS NOT NULL",
                    (course_id,))
                chatflow_ids = [r[0] for r in cur.fetchall()]
            conn.commit()
    except db.DatabaseError as exc:
        steps.append(DeletionStep("postgres", "read the course's chatflows",
                                  False, error=str(exc)))

    langfuse_on = (langfuse is not None) or LangfuseClient.configured(env)
    if langfuse_on and flowise is not None and chatflow_ids:
        try:
            for chatflow_id in chatflow_ids:
                session_ids.extend(flowise.chat_session_ids(chatflow_id))
            steps.append(DeletionStep(
                "flowise", "collected the chat sessions for Langfuse",
                detail=f"{len(session_ids)} session(s)"))
        except Exception as exc:  # noqa: BLE001 — FlowiseError and requests'
            steps.append(DeletionStep(
                "flowise", "collect the chat sessions for Langfuse", False,
                error=str(exc)))

    # ── 2. Langfuse, which deletes on its own time ──────────────────────────
    if not langfuse_on:
        steps.append(DeletionStep(
            "langfuse", "skipped — this installation runs no Langfuse",
            detail="the observability profile is off"))
    else:
        langfuse = langfuse or LangfuseClient()
        try:
            trace_ids: list[str] = []
            for session_id in session_ids:
                trace_ids.extend(langfuse.trace_ids_for_session(session_id))
            asked = langfuse.delete_traces(trace_ids)
            steps.append(DeletionStep(
                "langfuse", "asked for the traces to be deleted",
                detail=f"{asked} trace(s) — Langfuse removes them within about "
                       "15 minutes and does not confirm"))
        except LangfuseError as exc:
            steps.append(DeletionStep("langfuse", "delete the traces", False,
                                      error=str(exc)))

    # ── 3. Flowise: the agents, and the conversations it keeps with them ────
    if flowise is None:
        if chatflow_ids:
            steps.append(DeletionStep(
                "flowise", "delete the chatflows", False,
                error="not connected to Flowise, so its agents and their "
                      "conversations are still there"))
    else:
        removed, absent = 0, 0
        failures = []
        for chatflow_id in chatflow_ids:
            try:
                if flowise.delete_chatflow(chatflow_id):
                    removed += 1
                else:
                    absent += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{chatflow_id}: {exc}")
        steps.append(DeletionStep(
            "flowise", "deleted the chatflows", not failures,
            detail=f"{removed} deleted, {absent} already gone",
            error="; ".join(failures)))

    # ── 4. Weaviate: one collection of its own, three shared classes ────────
    try:
        weaviate = weaviate or WeaviateClient(
            os.getenv("SMARTRAG_WEAVIATE_URL",
                      f"http://smartrag-weaviate:{env.get('WEAVIATE_HTTP_PORT', '8080')}"),
            env.get("WEAVIATE_API_KEY", ""))
        if weaviate.collection_exists(course["collection"]):
            weaviate.delete_collection(course["collection"])
            steps.append(DeletionStep("weaviate", "deleted the collection",
                                      detail=course["collection"]))
        else:
            steps.append(DeletionStep("weaviate", "collection was already gone",
                                      detail=course["collection"]))
    except WeaviateError as exc:
        steps.append(DeletionStep("weaviate", "delete the collection", False,
                                  error=str(exc)))

    for cls in WeaviateClient.SHARED_LEARNER_CLASSES:
        try:
            n = weaviate.delete_by_course(cls, course_id)
            steps.append(DeletionStep("weaviate", f"deleted from {cls}",
                                      detail=f"{n} record(s)"))
        except WeaviateError as exc:
            steps.append(DeletionStep("weaviate", f"delete from {cls}", False,
                                      error=str(exc)))

    # ── 5. Garage: empty it, then remove it ─────────────────────────────────
    # Both halves, because Garage refuses a bucket that is not empty and its
    # admin API cannot empty one. A bucket left behind is worse than a stray
    # file: create_bucket is idempotent, so the next course of the same name
    # adopts it and its documents.
    try:
        garage = garage or GarageClient()
        if garage.bucket_info(course["bucket"]) is None:
            steps.append(DeletionStep("garage", "bucket was already gone",
                                      detail=course["bucket"]))
        else:
            from s3_client import S3Client, S3Error
            try:
                s3 = s3 or S3Client()
                emptied = s3.empty_bucket(course["bucket"])
                garage.delete_bucket(course["bucket"])
                steps.append(DeletionStep(
                    "garage", "emptied and deleted the bucket",
                    detail=f"{course['bucket']}, {emptied} object(s)"))
            except S3Error as exc:
                steps.append(DeletionStep(
                    "garage", "empty the bucket", False, error=str(exc)))
    except GarageError as exc:
        steps.append(DeletionStep("garage", "delete the bucket", False,
                                  error=str(exc)))

    # ── 6. The course's part of the graph ───────────────────────────────────
    try:
        if neo4j is None:
            from neo4j_client import Neo4jClient
            neo4j = Neo4jClient(
                os.getenv("SMARTRAG_NEO4J_URL",
                          f"http://smartrag-neo4j:{env.get('NEO4J_HTTP_PORT', '7474')}"),
                "neo4j", env.get("NEO4J_PASSWORD", ""))
        removed = neo4j.clear_course(course_id)
        steps.append(DeletionStep("neo4j", "deleted the concepts",
                                  detail=f"{removed} concept(s) and their links"))
    except Exception as exc:  # noqa: BLE001 — Neo4jError and requests'
        steps.append(DeletionStep("neo4j", "delete the concepts", False,
                                  error=str(exc)))

    # ── 7. Progress rows, which no page would ever show again ───────────────
    try:
        import ingest_status
        forgotten = ingest_status.forget_course(course_id)
        if forgotten:
            steps.append(DeletionStep("content-admin", "cleared progress rows",
                                      detail=f"{forgotten} row(s)"))
    except OSError as exc:
        steps.append(DeletionStep("content-admin", "clear progress rows", False,
                                  error=str(exc)))

    # ── 8. The record, last, and only if the rest worked ────────────────────
    if any(not s["ok"] for s in steps):
        steps.append(DeletionStep(
            "postgres", "kept the course record", False,
            error="something above did not work, so the course stays in the "
                  "list and the deletion can be run again — removing the "
                  "record now would leave data nothing points at"))
        return {"steps": steps, "deleted": False}

    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM courses WHERE id = %s", (course_id,))
            conn.commit()
        steps.append(DeletionStep(
            "postgres", "deleted the course record",
            detail="its slots and its maintainer assignments went with it"))
    except db.DatabaseError as exc:
        steps.append(DeletionStep("postgres", "delete the course record", False,
                                  error=str(exc)))
        return {"steps": steps, "deleted": False}

    logger.info("Course %s deleted: %s", course_id,
                "; ".join(f"{s['system']} {s['action']}" for s in steps))
    return {"steps": steps, "deleted": True}
