"""Grant runtime data access after migrations while preserving the owner boundary."""

# This file is mounted as a standalone Helm hook script.
# ruff: noqa: INP001

import asyncio
import os
import re
import sys

from sqlalchemy import text

from voxos.db.engine import create_database_engine
from voxos.db.settings import DatabaseSettings


async def main() -> None:
    runtime_role = os.environ["VOXOS_RUNTIME_ROLE"]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", runtime_role) is None:
        message = "VOXOS_RUNTIME_ROLE must be a simple PostgreSQL identifier"
        raise ValueError(message)
    engine = create_database_engine(DatabaseSettings())
    try:
        async with engine.begin() as connection:
            owner = await connection.scalar(
                text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'")
            )
            if not isinstance(owner, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", owner) is None:
                message = "Unexpected application schema owner"
                raise ValueError(message)
            if runtime_role == owner:
                message = "Runtime role must differ from the schema owner"
                raise ValueError(message)
            await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{runtime_role}"'))
            await connection.execute(
                text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{runtime_role}"')
            )
            await connection.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{runtime_role}"'))
            await connection.execute(
                text(
                    f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
                    f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{runtime_role}"'
                )
            )
            await connection.execute(
                text(
                    f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
                    f'GRANT USAGE, SELECT ON SEQUENCES TO "{runtime_role}"'
                )
            )
            await connection.execute(
                text(
                    f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
                    f'GRANT EXECUTE ON FUNCTIONS TO "{runtime_role}"'
                )
            )
            functions = await connection.scalars(
                text(
                    "SELECT quote_ident(n.nspname)||'.'||quote_ident(p.proname)||'('||"
                    "pg_get_function_identity_arguments(p.oid)||')' "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND pg_get_userbyid(p.proowner)=:owner"
                ),
                {"owner": owner},
            )
            for signature in functions:
                await connection.execute(text(f'GRANT EXECUTE ON FUNCTION {signature} TO "{runtime_role}"'))
            await connection.execute(text(f'REVOKE ALL ON TABLE public.alembic_version FROM "{runtime_role}"'))
            await connection.execute(text(f'GRANT SELECT ON TABLE public.alembic_version TO "{runtime_role}"'))
        sys.stdout.write("Runtime data grants configured without ownership or migration privileges\n")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
