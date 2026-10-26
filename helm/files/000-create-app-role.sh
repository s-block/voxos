#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${APP_DB_NAME:?APP_DB_NAME is required}"
: "${APP_DB_USER:?APP_DB_USER is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"
: "${APP_DB_OWNER:?APP_DB_OWNER is required}"
: "${APP_DB_OWNER_PASSWORD:?APP_DB_OWNER_PASSWORD is required}"
identifier_pattern='^[A-Za-z_][A-Za-z0-9_]*$'
for identifier in "$POSTGRES_USER" "$POSTGRES_DB" "$APP_DB_USER" "$APP_DB_NAME" "$APP_DB_OWNER"; do
  [[ "$identifier" =~ $identifier_pattern ]] || { echo 'Database names and users must be simple identifiers' >&2; exit 1; }
done
[[ "$APP_DB_NAME" == "$POSTGRES_DB" ]] || { echo 'APP_DB_NAME must match POSTGRES_DB' >&2; exit 1; }
[[ "$APP_DB_USER" != "$POSTGRES_USER" ]] || { echo 'Application role must differ from the bootstrap superuser' >&2; exit 1; }
[[ "$APP_DB_OWNER" != "$POSTGRES_USER" && "$APP_DB_OWNER" != "$APP_DB_USER" ]] || { echo 'Runtime, owner and bootstrap roles must differ' >&2; exit 1; }
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<SQL
\getenv app_password APP_DB_PASSWORD
\getenv owner_password APP_DB_OWNER_PASSWORD
SELECT format('CREATE ROLE %I', '$APP_DB_OWNER') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$APP_DB_OWNER') \gexec
SELECT format('CREATE ROLE %I', '$APP_DB_USER') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$APP_DB_USER') \gexec
ALTER ROLE "$APP_DB_OWNER" WITH LOGIN PASSWORD :'owner_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE "$APP_DB_USER" WITH LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER DATABASE "$APP_DB_NAME" OWNER TO "$APP_DB_OWNER";
ALTER SCHEMA public OWNER TO "$APP_DB_OWNER";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM "$APP_DB_USER";
GRANT USAGE ON SCHEMA public TO "$APP_DB_USER";
CREATE EXTENSION IF NOT EXISTS vector;
SQL
