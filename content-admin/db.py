"""
The Content Admin's own database: courses, accounts, assignments, slots.

Why a database at all. `slots.json` was right for ten slots and one operator.
The target is dozens of courses at ten slots each with several maintainers
working at once, and a JSON file rewritten whole by concurrent writers loses
one of them — not often, and not visibly, which is the bad kind of rare. This
runs against the Postgres that is already part of every installation.

Which database. Not `POSTGRES_DB`: that one is called "smartrag" and belongs
to Langfuse (see DATABASE_URL in .env). Ours is `contentadmin`, named after
its consumer like `flowise` and `n8n` beside it, and created by
docker/postgres-init/01-create-databases.sql — which runs only when the data
directory is first initialised. On an installation that predates it, the
database has to be created by hand; connect() says so rather than failing
with psycopg's own wording.

Migrations. Numbered SQL files in migrations/, applied in order, each
recorded in schema_version. Not scaffolding for one feature: from here on the
schema changes over the life of an installation, and hand-edited tables are
how two deployments stop being the same software. Each file runs inside one
transaction with an advisory lock held, so two containers starting at the
same moment cannot both apply it.
"""

import logging
import os
import re
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

from env_file import read_env

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(os.getenv("SMARTRAG_MIGRATIONS_DIR",
                                str(Path(__file__).parent / "migrations")))

# One number, held for the whole migration run. Any value works as long as it
# is ours alone; this one is "smartrag" in ASCII, summed.
_MIGRATION_LOCK = 0x5352_4147

_pool: ConnectionPool | None = None


class DatabaseError(RuntimeError):
    """Raised with something an operator can act on, never psycopg's raw text
    where that text names a cause we already know."""


def dsn() -> str:
    """Where to connect. An explicit DSN wins, so tests can point at their own
    database without a .env in sight."""
    explicit = os.getenv("SMARTRAG_DB_DSN", "").strip()
    if explicit:
        return explicit

    env = read_env()
    user = env.get("POSTGRES_USER", "").strip()
    password = env.get("POSTGRES_PASSWORD", "").strip()
    if not user or not password:
        raise DatabaseError(
            "POSTGRES_USER or POSTGRES_PASSWORD is missing from .env, so the "
            "Content Admin cannot reach its database."
        )
    host = os.getenv("SMARTRAG_DB_HOST", "smartrag-postgres")
    port = os.getenv("SMARTRAG_DB_PORT", "5432")
    name = os.getenv("SMARTRAG_DB_NAME", "contentadmin")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def pool() -> ConnectionPool:
    """The process-wide pool, opened on first use.

    Small on purpose: one gunicorn worker serving a handful of maintainers
    needs a handful of connections, and a large pool against a Postgres that
    also carries Langfuse, Flowise and n8n is a way to run that server out of
    connections for everybody.
    """
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            dsn(),
            min_size=1,
            max_size=int(os.getenv("SMARTRAG_DB_POOL_MAX", "5")),
            timeout=10,
            open=True,
            kwargs={"autocommit": False},
        )
    return _pool


def close_pool() -> None:
    """For tests and for a clean shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def _known_cause(exc: Exception) -> str | None:
    """Turn the two failures that actually happen into sentences that say what
    to do. Everything else keeps psycopg's own words, which are usually
    better than a paraphrase."""
    text = str(exc)
    # Any database name, not just ours: a test run points at its own, and the
    # first version recognised only "contentadmin", so a missing test database
    # produced seven lines of connection-pool retries and no sentence saying
    # what was wrong.
    missing = re.search(r'database "([^"]+)" does not exist', text)
    if missing:
        name = missing.group(1)
        return (
            f'The database "{name}" does not exist. Databases are created when '
            "Postgres initialises its data directory, so an installation that "
            "predates a database does not have it. Create it once:\n"
            '  docker exec smartrag-postgres psql -U "$POSTGRES_USER" '
            f'-d "$POSTGRES_DB" -c "CREATE DATABASE {name};"'
        )
    if "Connection refused" in text or "could not translate host name" in text:
        return (
            "Postgres is not reachable at the address the Content Admin uses "
            "(smartrag-postgres:5432). Check that the container is running: "
            "docker ps --filter name=smartrag-postgres"
        )
    return None


def connect():
    """A pooled connection as a context manager.

    Use as:  with db.connect() as conn:  ...  — the transaction commits on a
    clean exit and rolls back on an exception, which is psycopg's own
    behaviour for a connection context.
    """
    try:
        return pool().connection()
    except Exception as exc:  # noqa: BLE001 — psycopg raises several types
        cause = _known_cause(exc)
        raise DatabaseError(cause or f"Could not reach the database: {exc}") from exc


# ─── Migrations ──────────────────────────────────────────────────────────────

_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def migration_files() -> list[tuple[int, Path]]:
    """Every migration, in order. A file that does not match the naming rule
    is an error rather than something to skip: a migration silently ignored
    because of a typo in its name is a schema difference nobody sees."""
    if not MIGRATIONS_DIR.is_dir():
        # Not an empty list. A deployment with no migrations directory is
        # broken, and answering "nothing to apply" makes it look finished:
        # the image once shipped without migrations/, migrate() reported
        # success, and every table was missing until something tried to read
        # one.
        raise DatabaseError(
            f"No migrations directory at {MIGRATIONS_DIR}. The image is "
            "incomplete — it must contain content-admin/migrations/."
        )
    found: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.iterdir()):
        if path.name.startswith(".") or path.is_dir():
            continue
        m = _NAME.match(path.name)
        if not m:
            raise DatabaseError(
                f"{path.name} is in migrations/ but is not named "
                "NNN_lower_snake_case.sql, so it would never be applied."
            )
        found.append((int(m.group(1)), path))

    numbers = [n for n, _ in found]
    duplicates = {n for n in numbers if numbers.count(n) > 1}
    if duplicates:
        raise DatabaseError(
            f"Two migrations share the number(s) {sorted(duplicates)}. Order "
            "would depend on the file name, which is how two installations "
            "end up with different schemas."
        )
    return found


def applied_versions(conn) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version     integer     PRIMARY KEY,
                name        text        NOT NULL,
                applied_at  timestamptz NOT NULL DEFAULT now()
            )
        """)
        cur.execute("SELECT version FROM schema_version")
        return {row[0] for row in cur.fetchall()}


def migrate() -> list[int]:
    """Apply everything not yet applied. Returns the versions applied now.

    Idempotent and safe to call from every container start: the advisory lock
    means a second caller waits and then finds nothing to do, rather than
    running the same CREATE TABLE twice and failing the start of a service
    that was fine.
    """
    pending_applied: list[int] = []
    files = migration_files()

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            done = applied_versions(conn)
            conn.commit()
            for version, path in files:
                if version in done:
                    continue
                sql = path.read_text()
                logger.info("Applying migration %s", path.name)
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_version (version, name) VALUES (%s, %s)",
                        (version, path.name),
                    )
                conn.commit()
                pending_applied.append(version)
        except psycopg.Error as exc:
            conn.rollback()
            raise DatabaseError(
                f"Migration failed and was rolled back: {exc}"
            ) from exc
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
            conn.commit()

    return pending_applied


def schema_state() -> dict:
    """What is applied and what is pending — for the status page, and for
    saying "the database is behind the code" instead of failing on a missing
    column later."""
    files = migration_files()
    with connect() as conn:
        done = applied_versions(conn)
        conn.commit()
    return {
        "applied": sorted(done),
        "pending": sorted(v for v, _ in files if v not in done),
        "latest": max((v for v, _ in files), default=0),
    }


# ─── Command line ────────────────────────────────────────────────────────────
# Applying migrations is not wired into the application's startup yet, and
# deliberately so: nothing reads these tables in this phase, and a container
# that refuses to start because a database it does not use is unreachable
# would be a step backwards. Until a route needs them, this is how they are
# applied and inspected:
#
#   docker exec smartrag-content-admin python3 /app/db.py status
#   docker exec smartrag-content-admin python3 /app/db.py migrate
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    try:
        if command == "migrate":
            applied = migrate()
            print(f"applied: {applied}" if applied else "nothing to apply")
        elif command == "status":
            state = schema_state()
            print(f"applied: {state['applied']}")
            print(f"pending: {state['pending']}")
            print(f"latest on disk: {state['latest']}")
        else:
            print(f"unknown command {command!r}; use 'migrate' or 'status'")
            sys.exit(2)
    except DatabaseError as exc:
        print(str(exc))
        sys.exit(1)
