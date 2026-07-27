-- Flowise and n8n each need their own database — the postgres image only
-- auto-creates the ONE database named by POSTGRES_DB (see .env, default
-- "smartrag"). Langfuse uses that default database directly, but Flowise
-- (DATABASE_NAME=flowise, hardcoded in docker-compose.yml) and n8n
-- (N8N_DB_POSTGRESDB_DATABASE=n8n) each expect their own.
--
-- Runs automatically ONLY on first container init (empty data directory) —
-- see the docker-entrypoint-initdb.d mount on smartrag-postgres in
-- docker-compose.yml. Has no effect on an already-initialized volume; if
-- you're fixing an existing deployment that hit "database ... does not
-- exist", create these manually instead:
--   docker exec smartrag-postgres bash -c \
--     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE flowise;" -c "CREATE DATABASE n8n;"'

CREATE DATABASE flowise;
CREATE DATABASE n8n;
