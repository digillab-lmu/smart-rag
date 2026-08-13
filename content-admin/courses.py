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
                 error: str = "", note: str = ""):
        super().__init__(system=system, label=label, count=count,
                         error=error, note=note)


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
        items.append(InventoryItem("postgres", "agent slots configured", configured))
        items.append(InventoryItem("postgres", "agents imported into Flowise", imported))
        items.append(InventoryItem("postgres", "maintainers assigned", members))
        if stranded:
            items.append(InventoryItem(
                "postgres", "maintainers who would be left with no course", stranded,
                note="their accounts stay; removing them is a separate decision"))
    except db.DatabaseError as exc:
        items.append(InventoryItem("postgres", "slots and maintainers", None, str(exc)))

    # ── Weaviate: one collection of its own, three shared classes ───────────
    weaviate = weaviate or WeaviateClient(
        os.getenv("SMARTRAG_WEAVIATE_URL",
                  f"http://smartrag-weaviate:{env.get('WEAVIATE_HTTP_PORT', '8080')}"),
        env.get("WEAVIATE_API_KEY", ""))
    try:
        if weaviate.collection_exists(course["collection"]):
            items.append(InventoryItem(
                "weaviate", f"document chunks in {course['collection']}",
                weaviate.count_chunks(course["collection"], course_id),
                note="the whole collection goes"))
        else:
            items.append(InventoryItem(
                "weaviate", f"collection {course['collection']}", 0,
                note="does not exist — nothing to remove"))
    except WeaviateError as exc:
        items.append(InventoryItem("weaviate", "document chunks", None, str(exc)))

    for cls in WeaviateClient.SHARED_LEARNER_CLASSES:
        try:
            items.append(InventoryItem(
                "weaviate", f"{cls} records", weaviate.count_by_course(cls, course_id),
                note="shared with other courses — removed by filter"))
        except WeaviateError as exc:
            items.append(InventoryItem("weaviate", f"{cls} records", None, str(exc)))

    # ── Garage: the bucket, counted by Garage itself ────────────────────────
    # No S3 client is needed to count: GetBucketInfo reports objects and
    # bytes. Emptying it is another matter — the admin API has no object
    # operations and refuses to delete a bucket that is not empty.
    garage = garage or GarageClient()
    try:
        info = garage.bucket_info(course["bucket"])
        if info is None:
            items.append(InventoryItem("garage", f"bucket {course['bucket']}", 0,
                                       note="does not exist — nothing to remove"))
        else:
            items.append(InventoryItem(
                "garage", f"objects in {course['bucket']}",
                int(info.get("objects") or 0),
                note=f"{int(info.get('bytes') or 0)} bytes"))
            unfinished_uploads = int(info.get("unfinishedUploads") or 0)
            if unfinished_uploads:
                items.append(InventoryItem(
                    "garage", "unfinished uploads", unfinished_uploads,
                    note="these also keep the bucket from being deleted"))
    except GarageError as exc:
        items.append(InventoryItem("garage", "object storage", None, str(exc)))

    # ── Neo4j: the course's part of the graph ───────────────────────────────
    if neo4j is None:
        from neo4j_client import Neo4jClient
        neo4j = Neo4jClient(
            os.getenv("SMARTRAG_NEO4J_URL",
                      f"http://smartrag-neo4j:{env.get('NEO4J_HTTP_PORT', '7474')}"),
            "neo4j", env.get("NEO4J_PASSWORD", ""))
    try:
        counts = neo4j.counts(course_id)
        items.append(InventoryItem("neo4j", "concepts", int(counts.get("concepts") or 0)))
        items.append(InventoryItem("neo4j", "prerequisite links",
                                   int(counts.get("edges") or 0)))
    except Exception as exc:  # noqa: BLE001 — Neo4jError, and requests' own
        items.append(InventoryItem("neo4j", "concept graph", None, str(exc)))

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
                "flowise", "chatflows to delete", len(present),
                note="their conversations, feedback and uploaded files go with "
                     "them — Flowise removes those itself"))
            if len(ids) > len(present):
                items.append(InventoryItem(
                    "flowise", "chatflows already gone", len(ids) - len(present),
                    note="recorded here but not in Flowise; nothing to do"))
        except Exception as exc:  # noqa: BLE001 — FlowiseError, and requests' own
            items.append(InventoryItem("flowise", "chatflows", None, str(exc)))

    # ── What this inventory deliberately does not count ─────────────────────
    # Langfuse traces carry a userId and Flowise's chatId, and no course. The
    # only route from a course to its traces runs through Flowise's chat
    # records — which Flowise deletes together with the chatflow, so after a
    # deletion the mapping no longer exists either. Saying so is the honest
    # answer; a zero here would read as "there are none".
    if "observability" in (env.get("COMPOSE_PROFILES") or ""):
        items.append(InventoryItem(
            "langfuse", "traces", None,
            error="not attributable to a course — traces carry a learner id and "
                  "Flowise's chat id, never a course id",
            note="reachable per learner, which is what an erasure request needs"))

    return {"course": course, "items": items}
