"""
The data layer: the migration mechanism and the four tables it creates.

Two halves, and they are separated deliberately.

The first needs no database: whether a migration file will ever be applied is
decided by its *name*, and a file that is silently ignored because of a typo
is a schema difference nobody sees until two installations behave
differently.

The second needs a real Postgres, not a mock. Concurrency is the whole reason
for leaving slots.json behind — a suite that never runs two writers has not
tested the decision it was written for. sqlite would answer differently about
advisory locks, ON CONFLICT and partial unique indexes, which are exactly the
things being relied on. Without a database this suite exits 10, which the
runner reports as "could not run" rather than as a pass.

Two ways to give it one. Postgres publishes no host port in this deployment,
so either reach the container's own address from the host, or run this file
inside the Content Admin container, where psycopg and the network are already
in place:

    docker cp tests/test_db.py smartrag-content-admin:/tmp/test_db.py
    docker exec -e SMARTRAG_TEST_DSN="postgresql://USER:PASS@smartrag-postgres:5432/contentadmin_test" \
        smartrag-content-admin python3 /tmp/test_db.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# /app when this file has been copied into the Content Admin container, which
# is one of the two ways to reach a real Postgres: the database publishes no
# host port, so either the host talks to the container's own address or the
# suite runs where the network already works.
APP_DIR = REPO / "content-admin"
if not APP_DIR.is_dir() and Path("/app/db.py").exists():
    APP_DIR = Path("/app")
sys.path.insert(0, str(APP_DIR))

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
env_path.write_text('POSTGRES_USER="u"\nPOSTGRES_PASSWORD="p"\n')
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)

failures = []

# The pool keeps worker threads alive; without this a failing run ends in a
# page of "couldn't stop thread" hints that bury the actual error.
import atexit  # noqa: E402


def _shutdown():
    try:
        db.close_pool()
    except Exception:  # noqa: BLE001
        pass


atexit.register(_shutdown)


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def done():
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        sys.exit(1)


try:
    import db
except ModuleNotFoundError as exc:
    # Name the module that is actually missing. The first version said
    # "psycopg is not installed" for every import failure, and the first time
    # it fired the missing module was db itself — the container had not been
    # rebuilt since db.py was added. A wrong diagnosis in the message costs
    # more than no message.
    missing = getattr(exc, "name", "") or str(exc)
    if missing == "db":
        print(f"db.py is not importable from {APP_DIR}. Inside the container "
              "that means the image predates it: rebuild with\n"
              "  bash scripts/compose.sh up -d --build smartrag-content-admin")
    else:
        print(f"{missing} is not installed ({exc}). On the host, run "
              "tests/run-tests.sh, which installs "
              "content-admin/requirements.txt into tests/.venv; inside the "
              "container, rebuild the image so requirements.txt is applied.")
    sys.exit(10)


# ─── 1. Without a database: the naming rules ─────────────────────────────────
def with_migrations(names, body):
    d = Path(tempfile.mkdtemp())
    for n in names:
        (d / n).write_text("SELECT 1;")
    old = db.MIGRATIONS_DIR
    db.MIGRATIONS_DIR = d
    try:
        return body()
    finally:
        db.MIGRATIONS_DIR = old


got = with_migrations(["002_second.sql", "001_first.sql"],
                      lambda: [v for v, _ in db.migration_files()])
check("migrations are ordered by number, not by discovery", got == [1, 2], got)


def expect_error(names, fragment):
    def body():
        try:
            db.migration_files()
        except db.DatabaseError as exc:
            return fragment in str(exc)
        return False
    return with_migrations(names, body)


# A misnamed file is an error, not something to skip past: skipping it means
# one installation has a table the next one does not.
check("a misnamed migration is rejected",
      expect_error(["001_ok.sql", "oops.sql"], "never be applied"))
check("a migration with no number is rejected",
      expect_error(["001_ok.sql", "add_users.sql"], "never be applied"))
check("two migrations with the same number are rejected",
      expect_error(["001_a.sql", "001_b.sql"], "share the number"))

# The real directory has to satisfy its own rule.
real = db.migration_files()
check("the shipped migrations are well-named and ordered",
      [v for v, _ in real] == sorted(v for v, _ in real),
      [p.name for _, p in real])
# Fail here rather than later. With no migrations everything below still
# "passes" — nothing is applied, so nothing is missing — until the first
# INSERT hits a table that was never created, and the crash arrives before
# the failure report does. That is exactly how the missing COPY in the
# Dockerfile presented.
if not real:
    print(f"FAILURES:\n  - no migrations found in {db.MIGRATIONS_DIR}. "
          "Inside the container this means the image does not contain "
          "content-admin/migrations/.")
    sys.exit(1)

# The DSN must not be guessable into existence from an empty .env.
#
# The file's contents are swapped, not the SMARTRAG_ENV_PATH variable:
# env_file.py resolves that path into a module constant at import time, so
# changing the variable afterwards does nothing. The first version of this
# check did exactly that and reported a product failure that was its own.
os.environ.pop("SMARTRAG_DB_DSN", None)
saved_env = env_path.read_text()
env_path.write_text("")
try:
    db.dsn()
    check("an .env without credentials is refused", False, "dsn() returned one anyway")
except db.DatabaseError as exc:
    check("an .env without credentials is refused", "POSTGRES_USER" in str(exc), str(exc))
finally:
    env_path.write_text(saved_env)

# The database name must not be POSTGRES_DB: that one is Langfuse's.
os.environ.pop("SMARTRAG_DB_NAME", None)
built = db.dsn()
check("the Content Admin gets its own database, not Langfuse's",
      built.endswith("/contentadmin"), built.rsplit("@", 1)[-1])
init_sql = REPO / "docker" / "postgres-init" / "01-create-databases.sql"
if init_sql.exists():
    check("the init SQL creates it",
          "CREATE DATABASE contentadmin;" in init_sql.read_text())
else:
    # Running from inside the container, where the repo is not mounted. Said
    # out loud rather than passing quietly: a check that vanishes with its
    # input is a check nobody notices losing.
    print("  note: postgres-init not visible from here, that one check did not run")


# ─── 2. With a database ──────────────────────────────────────────────────────
TEST_DSN = os.getenv("SMARTRAG_TEST_DSN", "").strip()
if not TEST_DSN:
    done()   # report the naming failures if any, before bowing out
    print("No SMARTRAG_TEST_DSN set, so the half of this suite that needs a "
          "real Postgres did not run. The migration mechanism, the four "
          "tables, their constraints and concurrent writers are all "
          "unverified.\n"
          "  Postgres publishes no host port, so use the container's own "
          "address:\n"
          "    PGHOST=$(docker inspect -f "
          "'{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "
          "smartrag-postgres)\n"
          "    SMARTRAG_TEST_DSN=\"postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD"
          "@$PGHOST:5432/contentadmin_test\" bash tests/run-tests.sh test_db")
    sys.exit(10)

os.environ["SMARTRAG_DB_DSN"] = TEST_DSN
db.close_pool()

try:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
except db.DatabaseError as exc:
    print(f"SMARTRAG_TEST_DSN is set but unusable: {exc}")
    sys.exit(10)


def reset():
    """A clean schema. Dropping rather than creating a database keeps this
    usable against a Postgres where the test user cannot create one."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS agent_slots, user_courses, users, "
                        "courses, schema_version CASCADE")
        conn.commit()


reset()

applied = db.migrate()
check("migrating an empty database applies everything", applied == [v for v, _ in real],
      applied)
again = db.migrate()
check("migrating twice applies nothing the second time", again == [], again)

state = db.schema_state()
check("nothing is pending afterwards", state["pending"] == [], state)
check("the applied list matches the files on disk",
      state["applied"] == [v for v, _ in real], state)

with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'")
        tables = {r[0] for r in cur.fetchall()}
    conn.commit()
for t in ("courses", "users", "user_courses", "agent_slots", "schema_version"):
    check(f"{t} exists", t in tables, sorted(tables))


def insert_course(cid="kurs-a", name="Kurs A"):
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO courses (id, name, collection, bucket) "
                "VALUES (%s, %s, %s, %s)",
                (cid, name, f"Chunks_{cid.replace('-', '_')}", f"{cid}-rag"))
        conn.commit()


def rejects(sql, params=()):
    """True when the database refuses the statement — the point being that
    the rule lives in the schema, not only in Python that a later route can
    forget to call."""
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        return False
    except Exception:  # noqa: BLE001 — any refusal counts
        return True


insert_course()

check("a course id with spaces is refused",
      rejects("INSERT INTO courses (id, name, collection, bucket) "
              "VALUES ('has space', 'x', 'c1', 'b1')"))
check("a course id in capitals is refused",
      rejects("INSERT INTO courses (id, name, collection, bucket) "
              "VALUES ('KursB', 'x', 'c2', 'b2')"))
check("two courses cannot share a collection",
      rejects("INSERT INTO courses (id, name, collection, bucket) "
              "VALUES ('kurs-b', 'x', 'Chunks_kurs_a', 'b3')"))
check("two courses cannot share a bucket",
      rejects("INSERT INTO courses (id, name, collection, bucket) "
              "VALUES ('kurs-c', 'x', 'c4', 'kurs-a-rag')"))

check("slot 0 is refused",
      rejects("INSERT INTO agent_slots (course_id, slot) VALUES ('kurs-a', 0)"))
check("slot 11 is refused",
      rejects("INSERT INTO agent_slots (course_id, slot) VALUES ('kurs-a', 11)"))
check("a slot in a course that does not exist is refused",
      rejects("INSERT INTO agent_slots (course_id, slot) VALUES ('nope', 1)"))

with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_slots (course_id, slot, name, chatflow_id) "
                    "VALUES ('kurs-a', 1, 'Tutor', 'cf-1')")
    conn.commit()

check("two slots cannot claim one chatflow",
      rejects("INSERT INTO agent_slots (course_id, slot, chatflow_id) "
              "VALUES ('kurs-a', 2, 'cf-1')"))
check("a name is unique within a course, ignoring case",
      rejects("INSERT INTO agent_slots (course_id, slot, name) "
              "VALUES ('kurs-a', 3, 'tutor')"))

insert_course("kurs-b", "Kurs B")
with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_slots (course_id, slot, name) "
                    "VALUES ('kurs-b', 1, 'Tutor')")
    conn.commit()
check("…but the same name in another course is fine", True)

# Several unnamed slots must coexist: the unique index is partial for that
# reason, and a plain UNIQUE would allow exactly one empty slot per course.
with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_slots (course_id, slot) VALUES ('kurs-b', 2)")
        cur.execute("INSERT INTO agent_slots (course_id, slot) VALUES ('kurs-b', 3)")
    conn.commit()
check("empty slots do not collide with each other", True)

# Deleting a course must take its slots and assignments with it, or the next
# course created with the same id inherits them.
with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM courses WHERE id = 'kurs-b'")
        cur.execute("SELECT count(*) FROM agent_slots WHERE course_id = 'kurs-b'")
        left = cur.fetchone()[0]
    conn.commit()
check("deleting a course removes its slots", left == 0, left)

# ─── 3. Two writers at once — the reason for leaving JSON ────────────────────
# With slots.json this is the losing case: both read, both write the whole
# file, the second overwrites the first and nobody is told.
insert_course("kurs-c", "Kurs C")
with db.connect() as conn:
    with conn.cursor() as cur:
        for slot in range(1, 11):
            cur.execute("INSERT INTO agent_slots (course_id, slot) VALUES ('kurs-c', %s)",
                        (slot,))
    conn.commit()

errors: list[str] = []


def writer(slot, label):
    try:
        for _ in range(20):
            with db.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_slots SET name = %s, updated_at = now() "
                        "WHERE course_id = 'kurs-c' AND slot = %s",
                        (label, slot))
                conn.commit()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{label}: {exc}")


threads = [threading.Thread(target=writer, args=(i + 1, f"agent-{i + 1}"))
           for i in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("four writers at once all succeed", not errors, errors[:2])
with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT slot, name FROM agent_slots "
                    "WHERE course_id = 'kurs-c' AND name IS NOT NULL ORDER BY slot")
        rows = cur.fetchall()
    conn.commit()
check("every writer's value survived", rows == [(i + 1, f"agent-{i + 1}") for i in range(4)],
      rows)

# And two migrations at once — two containers starting together. The advisory
# lock means the second waits and then finds nothing to do, rather than both
# running the same CREATE TABLE.
reset()
mig_errors: list[str] = []


def migrator():
    try:
        db.migrate()
    except Exception as exc:  # noqa: BLE001
        mig_errors.append(str(exc))


ts = [threading.Thread(target=migrator) for _ in range(3)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("three simultaneous migrations do not collide", not mig_errors, mig_errors[:2])
with db.connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM schema_version")
        n = cur.fetchone()[0]
    conn.commit()
check("each migration is recorded exactly once", n == len(real), n)

reset()
db.close_pool()

done()
print(
    "All data-layer checks passed: migrations are ordered by number and a "
    "misnamed or duplicated one is refused rather than skipped; the Content "
    "Admin uses its own database rather than Langfuse's; migrating is "
    "idempotent and three containers migrating at once record each step "
    "exactly once; the schema itself refuses a malformed course id, a shared "
    "collection or bucket, a slot outside 1..10, a slot in a course that does "
    "not exist, two slots claiming one chatflow and a duplicate agent name "
    "within a course while allowing it across courses and allowing several "
    "empty slots; deleting a course takes its slots with it; and four "
    "concurrent writers all keep their values, which is what JSON could not do."
)
