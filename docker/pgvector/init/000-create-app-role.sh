#!/usr/bin/env bash
set -Eeuo pipefail

: "${APP_DB_NAME:?APP_DB_NAME is required}"
: "${APP_DB_USER:?APP_DB_USER is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"

identifier_pattern='^[A-Za-z_][A-Za-z0-9_]*$'
if [[ ! "${APP_DB_NAME}" =~ ${identifier_pattern} ]]; then
  echo "APP_DB_NAME must be a simple PostgreSQL identifier" >&2
  exit 1
fi

if [[ ! "${APP_DB_USER}" =~ ${identifier_pattern} ]]; then
  echo "APP_DB_USER must be a simple PostgreSQL identifier" >&2
  exit 1
fi

if [[ "${APP_DB_USER}" == "${POSTGRES_USER}" ]]; then
  echo "APP_DB_USER must not match POSTGRES_USER; app role must not be the bootstrap superuser" >&2
  exit 1
fi

psql_admin=(
  psql
  -v ON_ERROR_STOP=1
  --username "${POSTGRES_USER}"
)

role_exists="$("${psql_admin[@]}" --dbname "${POSTGRES_DB}" -tAc "SELECT 1 FROM pg_roles WHERE rolname = '${APP_DB_USER}'")"
if [[ "${role_exists}" == "1" ]]; then
  "${psql_admin[@]}" \
    --dbname "${POSTGRES_DB}" \
    -v app_password="${APP_DB_PASSWORD}" <<SQL
ALTER ROLE "${APP_DB_USER}" WITH LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
SQL
else
  "${psql_admin[@]}" \
    --dbname "${POSTGRES_DB}" \
    -v app_password="${APP_DB_PASSWORD}" <<SQL
CREATE ROLE "${APP_DB_USER}" WITH LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
SQL
fi

"${psql_admin[@]}" \
  --dbname "${POSTGRES_DB}" \
  -c "ALTER DATABASE \"${APP_DB_NAME}\" OWNER TO \"${APP_DB_USER}\";"

"${psql_admin[@]}" \
  --dbname "${APP_DB_NAME}" \
  -c "ALTER SCHEMA public OWNER TO \"${APP_DB_USER}\"; GRANT USAGE, CREATE ON SCHEMA public TO \"${APP_DB_USER}\"; REVOKE CREATE ON SCHEMA public FROM PUBLIC;"
