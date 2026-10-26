import argparse
import asyncio
import sys
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import SQLModel

from examples.skills.scripts.example_catalog import (
    ExampleSkillCatalog,
    ExampleSkillCatalogError,
    ExampleSkillSelectionError,
    parse_mcp_group_list,
)
from examples.skills.scripts.example_installer import (
    ExampleSkillInstaller,
    ExampleSkillInstallerError,
)
from voxos.agent_runtime.records import AgentReflectionJobRecord
from voxos.db.engine import create_database_engine, create_session_factory
from voxos.db.settings import DatabaseSettings


class ExampleSkillGroupListing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selector: str = Field(min_length=1, max_length=257)
    skills: tuple[str, ...] = Field(min_length=1)


class ExampleSkillCatalogListing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    groups: tuple[ExampleSkillGroupListing, ...] = Field(min_length=1)


class ExampleSkillCliError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error: str = Field(min_length=1, max_length=1024)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List or install curated MCP-backed example skills.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List curated MCP groups without database access.",
    )
    parser.add_argument(
        "--user-id",
        help="Canonical local Voxos user ID that will own installed skills.",
    )
    parser.add_argument(
        "--mcp",
        help="Comma-separated canonical MCP selectors to load.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate dependencies and collisions without writing skills.",
    )
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        catalog = ExampleSkillCatalog.load()
    except ExampleSkillCatalogError as error:
        _write_error(str(error))
        return 1

    if args.list:
        listing = ExampleSkillCatalogListing(
            groups=tuple(
                ExampleSkillGroupListing(selector=selector, skills=skills)
                for selector, skills in catalog.group_skills().items()
            ),
        )
        sys.stdout.write(f"{listing.model_dump_json()}\n")
        return 0

    if not args.user_id:
        parser.error("--user-id is required unless --list is used")
    if not args.mcp:
        parser.error("--mcp is required unless --list is used")

    try:
        requested_groups = parse_mcp_group_list(args.mcp)
        catalog.resolve_groups(requested_groups)
    except ExampleSkillSelectionError as error:
        _write_error(str(error))
        return 2

    engine: AsyncEngine | None = None
    try:
        _require_related_database_models()
        engine = create_database_engine(DatabaseSettings())
        installer = ExampleSkillInstaller(
            session_factory=create_session_factory(engine),
            catalog=catalog,
        )
        report = await installer.install(
            user_id=args.user_id,
            requested_groups=requested_groups,
            dry_run=args.dry_run,
        )
    except ExampleSkillInstallerError as error:
        _write_error(str(error))
        return 1
    finally:
        if engine is not None:
            await engine.dispose()

    sys.stdout.write(f"{report.model_dump_json()}\n")
    return 0 if report.has_usable_selection else 1


def _write_error(message: str) -> None:
    sys.stderr.write(f"{ExampleSkillCliError(error=message).model_dump_json()}\n")


def _require_related_database_models() -> None:
    related_table = AgentReflectionJobRecord.__tablename__
    if related_table not in SQLModel.metadata.tables:
        message = f"required database model is not registered: {related_table}"
        raise ExampleSkillInstallerError(message)


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
