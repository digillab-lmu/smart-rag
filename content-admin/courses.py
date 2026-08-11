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
