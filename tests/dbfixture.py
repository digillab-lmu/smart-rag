"""
Shared setup for the suites that touch agent slots.

Slots moved from a JSON file into Postgres, and with them the eight suites
that exercise them through app.py. Each one now needs three things before it
can run: a database, the schema, and a course for the slots to belong to.
Repeating that in eight files would guarantee they drift.

Without SMARTRAG_TEST_DSN these suites cannot run. They say so and exit 10,
which the runner reports as "could not run" — not as a pass. That is the
honest outcome: what they cover is real behaviour, and a green line for a
suite that never executed is worse than a yellow one that admits it.

    docker cp tests/ smartrag-content-admin:/tmp/tests
    docker exec -e SMARTRAG_TEST_DSN=postgresql://... \\
        smartrag-content-admin python3 /tmp/tests/test_app_smoke.py
"""

import os
import sys
import tempfile
from pathlib import Path

COURSE_ID = "testkurs"
COURSE_NAME = "Testkurs"


def app_dir() -> Path:
    repo = Path(__file__).resolve().parent.parent
    app = repo / "content-admin"
    if not app.is_dir() and Path("/app/db.py").exists():
        app = Path("/app")
    return app


def require_database():
    """Connect, migrate, and return (db, courses, course).

    Exits 10 when there is no database to use, before the importing suite has
    done anything that would fail confusingly later.
    """
    sys.path.insert(0, str(app_dir()))

    dsn = os.getenv("SMARTRAG_TEST_DSN", "").strip()
    if not dsn:
        print("These checks exercise agent slots, which now live in Postgres, "
              "so they need one. Set SMARTRAG_TEST_DSN, or run this file "
              "inside the Content Admin container where the database is "
              "reachable.")
        sys.exit(10)
    os.environ["SMARTRAG_DB_DSN"] = dsn

    try:
        import db
    except ModuleNotFoundError as exc:
        print(f"{getattr(exc, 'name', exc)} is not importable from "
              f"{app_dir()}. On the host run tests/run-tests.sh; inside the "
              "container rebuild the image.")
        sys.exit(10)

    try:
        db.migrate()
    except db.DatabaseError as exc:
        print(f"The test database is not usable: {exc}")
        sys.exit(10)

    import atexit
    atexit.register(db.close_pool)

    # Accounts too. A suite that creates one through /setup finds the
    # previous suite's account still there, is redirected to the login page,
    # and then fails on something three steps away — the same shared-database
    # trap the courses had.
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users")
        conn.commit()

    course = _ensure_course(db)
    return db, course


def _ensure_course(db):
    """Exactly one ready course, created directly rather than through
    courses.create_course(): these suites are not about provisioning, and
    going through it would need Weaviate and Garage to be reachable.

    Exactly one, and other courses removed. All suites share one database, so
    a suite that aborts part-way leaves its courses behind — and the next one
    then finds several, gets redirected to the course list because none can
    be chosen automatically, and fails for a reason that has nothing to do
    with what it tests. That happened, and it looked like flakiness.
    """
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM courses WHERE id <> %s", (COURSE_ID,))
            cur.execute(
                "INSERT INTO courses (id, name, collection, bucket, provisioned_at) "
                "VALUES (%s, %s, %s, %s, now()) "
                "ON CONFLICT (id) DO UPDATE SET provisioned_at = now() ",
                (COURSE_ID, COURSE_NAME, "TestkursChunks", f"{COURSE_ID}-rag"))
            for slot in range(1, 11):
                cur.execute(
                    "INSERT INTO agent_slots (course_id, slot) VALUES (%s, %s) "
                    "ON CONFLICT (course_id, slot) DO NOTHING", (COURSE_ID, slot))
        conn.commit()
    return {"id": COURSE_ID, "name": COURSE_NAME,
            "collection": "TestkursChunks", "bucket": f"{COURSE_ID}-rag",
            "ready": True}


def clear_slots(db):
    """Back to ten empty slots, between checks that each assume a clean
    course."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_slots SET archetype = NULL, name = NULL, "
                "content = '{}'::jsonb, system_prompt = NULL, "
                "chatflow_id = NULL, published = false WHERE course_id = %s",
                (COURSE_ID,))
        conn.commit()


def tmp_env(extra: str = "") -> Path:
    """An .env for the suite, with the values every one of them needs."""
    path = Path(tempfile.mkdtemp()) / ".env"
    path.write_text(
        'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
        'POSTGRES_USER="u"\nPOSTGRES_PASSWORD="p"\n'
        'DOMAIN="example.com"\nADMIN_EMAIL="admin@example.com"\n'
        + extra)
    return path
