#!/bin/sh
set -eu
# psql reads these from its environment, never from process arguments.
export APP_DB_PASSWORD="$(cat /run/secrets/db_app_password)"
export MIGRATION_DB_PASSWORD="$(cat /run/secrets/db_migration_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_password APP_DB_PASSWORD
\getenv migration_password MIGRATION_DB_PASSWORD
CREATE USER workspace_app PASSWORD :'app_password';
CREATE USER workspace_migration PASSWORD :'migration_password';
CREATE SCHEMA workspace AUTHORIZATION workspace_migration;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA workspace TO workspace_app;
-- The migration role installs pg_trgm (trusted extension) for forgiving search.
SELECT format('GRANT CREATE ON DATABASE %I TO workspace_migration', current_database()) \gexec
ALTER DEFAULT PRIVILEGES FOR ROLE workspace_migration IN SCHEMA workspace
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO workspace_app;
SQL
