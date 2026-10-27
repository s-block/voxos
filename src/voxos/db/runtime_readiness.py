import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from voxos.db.engine import create_database_engine
from voxos.db.settings import DatabaseSettings


@dataclass(frozen=True, slots=True)
class RuntimeDatabaseReadiness:
    no_superuser: bool
    no_create_role: bool
    no_create_database: bool
    no_replication: bool
    no_bypass_rls: bool
    cannot_assume_database_owner: bool
    no_schema_create: bool
    owns_no_application_tables: bool
    cannot_update_alembic_version: bool
    row_security_enabled: bool

    def failures(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, passed in (
                ("NOSUPERUSER", self.no_superuser),
                ("NOCREATEROLE", self.no_create_role),
                ("NOCREATEDB", self.no_create_database),
                ("NOREPLICATION", self.no_replication),
                ("NOBYPASSRLS", self.no_bypass_rls),
                (
                    "cannot assume the database owner role",
                    self.cannot_assume_database_owner,
                ),
                ("cannot create in the public schema", self.no_schema_create),
                ("owns no application tables", self.owns_no_application_tables),
                (
                    "cannot update alembic_version",
                    self.cannot_update_alembic_version,
                ),
                ("row_security is enabled", self.row_security_enabled),
            )
            if not passed
        )


RUNTIME_DATABASE_READINESS_SQL = text(
    """
    SELECT
        NOT role.rolsuper AS no_superuser,
        NOT role.rolcreaterole AS no_create_role,
        NOT role.rolcreatedb AS no_create_database,
        NOT role.rolreplication AS no_replication,
        NOT role.rolbypassrls AS no_bypass_rls,
        NOT pg_has_role(current_user, database.datdba, 'MEMBER')
            AS cannot_assume_database_owner,
        NOT COALESCE(has_schema_privilege(current_user, 'public', 'CREATE'), false)
            AS no_schema_create,
        NOT EXISTS (
            SELECT 1
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema')
              AND namespace.nspname NOT LIKE 'pg_toast%'
              AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
              AND pg_has_role(current_user, relation.relowner, 'MEMBER')
        ) AS owns_no_application_tables,
        CASE
            WHEN to_regclass('public.alembic_version') IS NULL THEN true
            ELSE NOT has_table_privilege(
                current_user,
                'public.alembic_version',
                'INSERT,UPDATE,DELETE,TRUNCATE'
            )
        END AS cannot_update_alembic_version,
        current_setting('row_security') = 'on' AS row_security_enabled
    FROM pg_roles AS role
    JOIN pg_database AS database
      ON database.datname = current_database()
    WHERE role.rolname = current_user
    """
)


async def inspect_runtime_database(
    engine: AsyncEngine,
) -> RuntimeDatabaseReadiness:
    async with engine.connect() as connection:
        row = (await connection.execute(RUNTIME_DATABASE_READINESS_SQL)).mappings().one()

    return RuntimeDatabaseReadiness(
        no_superuser=bool(row["no_superuser"]),
        no_create_role=bool(row["no_create_role"]),
        no_create_database=bool(row["no_create_database"]),
        no_replication=bool(row["no_replication"]),
        no_bypass_rls=bool(row["no_bypass_rls"]),
        cannot_assume_database_owner=bool(row["cannot_assume_database_owner"]),
        no_schema_create=bool(row["no_schema_create"]),
        owns_no_application_tables=bool(row["owns_no_application_tables"]),
        cannot_update_alembic_version=bool(row["cannot_update_alembic_version"]),
        row_security_enabled=bool(row["row_security_enabled"]),
    )


async def check_configured_runtime_database() -> RuntimeDatabaseReadiness:
    engine = create_database_engine(DatabaseSettings())
    try:
        return await inspect_runtime_database(engine)
    finally:
        await engine.dispose()


def main() -> None:
    readiness = asyncio.run(check_configured_runtime_database())
    failures = readiness.failures()
    if failures:
        for failure in failures:
            sys.stdout.write(f"FAILED: {failure}\n")
        raise SystemExit(1)
    sys.stdout.write("Runtime database role readiness checks passed.\n")


if __name__ == "__main__":
    main()
