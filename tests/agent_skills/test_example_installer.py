from collections.abc import Iterable

import pytest
from examples.skills.scripts import example_installer as example_installer_module
from examples.skills.scripts.example_catalog import ExampleSkillCatalog
from examples.skills.scripts.example_installer import (
    ExampleSkillInstaller,
    ExampleSkillUserNotFoundError,
)
from sqlmodel import col, select

from voxos.agent_runtime.records import AgentToolGrantRecord
from voxos.agent_skills.models import (
    SkillCreateRequest,
    SkillDocument,
    SkillStatus,
)
from voxos.agent_skills.records import AgentSkillRecord
from voxos.agent_skills.services import AgentSkillAlreadyExistsError
from voxos.auth.records import FirebaseUserRecord
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.models import McpServerStatus, McpTrustState
from voxos.mcp.records import UserMcpServerRecord

GITHUB_TOOLS = (
    "get_file_contents",
    "list_branches",
    "list_commits",
    "list_issues",
    "list_pull_requests",
    "search_code",
    "pull_request_read",
    "get_commit",
)
MAPS_TOOLS = ("search_places",)
TRIPGO_TOOLS = (
    "tripgo-locations",
    "tripgo-routing",
    "tripgo-departures",
    "tripgo-get-trip-url",
)
OPENBNB_TOOLS = (
    "airbnb_search",
    "airbnb_listing",
    "maps_distance_matrix",
    "maps_search_places",
    "maps_place_details",
)
GMAIL_TOOLS = (
    "gmail.search_threads",
    "gmail.get_thread",
    "gmail.list_labels",
    "gmail.label_thread",
    "gmail.create_draft",
)
CALENDAR_TOOLS = (
    "calendar.list_events",
    "calendar.get_event",
)


@pytest.mark.asyncio
async def test_github_install_is_scoped_idempotent_and_preserves_edits(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await _add_user(sqlite_session_factory, "user-a")
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="GitHub",
        preset_id="github",
        tools=GITHUB_TOOLS,
    )
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    first = await installer.install(
        user_id="user-a",
        requested_groups=("github",),
    )

    assert first.created == (
        "github-repository-orientation",
        "github-pull-request-review",
    )
    assert not first.blocked
    await _set_skill_state(
        sqlite_session_factory,
        slug="github-repository-orientation",
        status=SkillStatus.PROPOSED,
        body_markdown="User-edited repository workflow.",
    )
    await _set_skill_state(
        sqlite_session_factory,
        slug="github-pull-request-review",
        status=SkillStatus.ARCHIVED,
        body_markdown="User-edited review workflow.",
    )

    second = await installer.install(
        user_id="user-a",
        requested_groups=("github",),
    )

    assert second.created == ()
    assert second.already_exists == (
        "github-repository-orientation",
        "github-pull-request-review",
    )
    records = await _skill_records(sqlite_session_factory)
    assert {record.body_markdown for record in records} == {
        "User-edited repository workflow.",
        "User-edited review workflow.",
    }
    assert {record.status for record in records} == {
        SkillStatus.PROPOSED.value,
        SkillStatus.ARCHIVED.value,
    }


@pytest.mark.asyncio
async def test_cross_mcp_requirement_must_be_satisfied_by_the_same_user(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await _add_user(sqlite_session_factory, "user-a")
    await _add_user(sqlite_session_factory, "user-b")
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="TripGo",
        preset_id="tripgo",
        preset_variant_id="public",
        tools=TRIPGO_TOOLS,
    )
    await _add_server(
        sqlite_session_factory,
        user_id="user-b",
        label="Maps",
        preset_id="google-maps-grounding-lite",
        tools=MAPS_TOOLS,
    )
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    blocked = await installer.install(
        user_id="user-a",
        requested_groups=("tripgo",),
        dry_run=True,
    )

    assert blocked.ready == ()
    assert [issue.selector for issue in blocked.blocked] == (["google-maps-grounding-lite"])

    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="Maps",
        preset_id="google-maps-grounding-lite",
        tools=MAPS_TOOLS,
    )
    ready = await installer.install(
        user_id="user-a",
        requested_groups=("tripgo",),
        dry_run=True,
    )

    assert ready.ready == ("multimodal-journey-plan",)
    assert not ready.blocked


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "failure",
        "enabled",
        "trust_state",
        "status",
        "discovered_tools",
        "allowed_tools",
    ),
    (
        (
            "disabled",
            False,
            McpTrustState.TRUSTED,
            McpServerStatus.DISABLED,
            OPENBNB_TOOLS,
            OPENBNB_TOOLS,
        ),
        (
            "untrusted",
            True,
            McpTrustState.UNTRUSTED,
            McpServerStatus.CONNECTED,
            OPENBNB_TOOLS,
            OPENBNB_TOOLS,
        ),
        (
            "undiscovered",
            True,
            McpTrustState.TRUSTED,
            McpServerStatus.CONNECTED,
            OPENBNB_TOOLS[:-1],
            OPENBNB_TOOLS,
        ),
        (
            "unallowed",
            True,
            McpTrustState.TRUSTED,
            McpServerStatus.CONNECTED,
            OPENBNB_TOOLS,
            OPENBNB_TOOLS[:-1],
        ),
    ),
)
async def test_openbnb_readiness_failures_block_install(
    sqlite_session_factory: AsyncSessionFactory,
    failure: str,
    enabled: bool,
    trust_state: McpTrustState,
    status: McpServerStatus,
    discovered_tools: tuple[str, ...],
    allowed_tools: tuple[str, ...],
) -> None:
    await _add_user(sqlite_session_factory, "user-a")
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label=f"OpenBnB {failure}",
        preset_id="openbnb",
        preset_variant_id="oauth",
        tools=(),
        enabled=enabled,
        trust_state=trust_state,
        status=status,
        discovered_tools=discovered_tools,
        allowed_tools=allowed_tools,
    )
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    report = await installer.install(
        user_id="user-a",
        requested_groups=("openbnb",),
    )

    assert report.created == ()
    assert report.blocked
    assert await _skill_records(sqlite_session_factory) == ()


@pytest.mark.asyncio
async def test_openbnb_dry_run_then_install_creates_one_skill_and_no_grants(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await _add_user(sqlite_session_factory, "user-a")
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="OpenBnB",
        preset_id="openbnb",
        preset_variant_id="oauth",
        tools=OPENBNB_TOOLS,
    )
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    dry_run = await installer.install(
        user_id="user-a",
        requested_groups=("openbnb",),
        dry_run=True,
    )

    assert dry_run.ready == ("accommodation-shortlist",)
    assert await _skill_records(sqlite_session_factory) == ()

    installed = await installer.install(
        user_id="user-a",
        requested_groups=("openbnb",),
    )

    assert installed.created == ("accommodation-shortlist",)
    assert [record.slug for record in await _skill_records(sqlite_session_factory)] == [
        "accommodation-shortlist",
    ]
    async with sqlite_session_factory() as session:
        grant_result = await session.exec(select(AgentToolGrantRecord))
    assert grant_result.all() == []


@pytest.mark.asyncio
async def test_needs_auth_installs_with_warning_without_leaking_server_details(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await _add_user(sqlite_session_factory, "user-a")
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="Gmail",
        preset_id="google-workspace",
        preset_variant_id="gmail",
        tools=GMAIL_TOOLS,
        status=McpServerStatus.NEEDS_AUTH,
        url="https://private-host.invalid/mcp",
        auth_config={"private_header": "private-value"},
    )
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    report = await installer.install(
        user_id="user-a",
        requested_groups=("google-workspace:gmail",),
    )

    assert report.created == ("gmail-inbox-triage-and-drafting",)
    assert [warning.selector for warning in report.warnings] == [
        "google-workspace:gmail",
    ]
    serialized = report.model_dump_json()
    assert "private-host" not in serialized
    assert "private-value" not in serialized


@pytest.mark.asyncio
async def test_google_workspace_install_uses_qualified_provider_tool_names(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await _add_user(sqlite_session_factory, "user-a")
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="Gmail",
        preset_id="google-workspace",
        preset_variant_id="gmail",
        tools=GMAIL_TOOLS,
    )
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="Calendar",
        preset_id="google-workspace",
        preset_variant_id="calendar",
        tools=CALENDAR_TOOLS,
    )
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    report = await installer.install(
        user_id="user-a",
        requested_groups=("google-workspace",),
    )

    assert report.created == (
        "gmail-inbox-triage-and-drafting",
        "meeting-preparation-brief",
    )
    assert report.blocked == ()


@pytest.mark.asyncio
async def test_install_treats_create_conflict_as_idempotent_race(
    sqlite_session_factory: AsyncSessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RacingSkillService:
        def __init__(self, session_factory: AsyncSessionFactory) -> None:
            del session_factory

        async def list_user_skills(
            self,
            *,
            user_id: str,
            statuses: tuple[SkillStatus, ...],
        ) -> tuple[SkillDocument, ...]:
            del user_id, statuses
            return ()

        async def create_user_skill(
            self,
            *,
            user_id: str,
            request: SkillCreateRequest,
        ) -> SkillDocument:
            del user_id
            raise AgentSkillAlreadyExistsError(request.slug or request.name)

    await _add_user(sqlite_session_factory, "user-a")
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="OpenBnB",
        preset_id="openbnb",
        preset_variant_id="oauth",
        tools=OPENBNB_TOOLS,
    )
    monkeypatch.setattr(
        example_installer_module,
        "AgentSkillService",
        RacingSkillService,
    )
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    report = await installer.install(
        user_id="user-a",
        requested_groups=("openbnb",),
    )

    assert report.created == ()
    assert report.already_exists == ("accommodation-shortlist",)


@pytest.mark.asyncio
async def test_connected_server_is_preferred_over_reconnectable_duplicate(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await _add_user(sqlite_session_factory, "user-a")
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="Gmail old",
        preset_id="google-workspace",
        preset_variant_id="gmail",
        tools=GMAIL_TOOLS,
        status=McpServerStatus.NEEDS_AUTH,
    )
    await _add_server(
        sqlite_session_factory,
        user_id="user-a",
        label="Gmail ready",
        preset_id="google-workspace",
        preset_variant_id="gmail",
        tools=GMAIL_TOOLS,
    )
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    report = await installer.install(
        user_id="user-a",
        requested_groups=("google-workspace:gmail",),
    )

    assert report.created == ("gmail-inbox-triage-and-drafting",)
    assert report.warnings == ()


@pytest.mark.asyncio
async def test_missing_server_and_missing_user_are_reported_without_writes(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await _add_user(sqlite_session_factory, "user-a")
    installer = ExampleSkillInstaller(
        session_factory=sqlite_session_factory,
        catalog=ExampleSkillCatalog.load(),
    )

    missing_server = await installer.install(
        user_id="user-a",
        requested_groups=("openbnb",),
    )

    assert missing_server.created == ()
    assert missing_server.blocked[0].reason == ("matching MCP server is not configured for this user")
    with pytest.raises(ExampleSkillUserNotFoundError, match="was not found"):
        await installer.install(
            user_id="missing-user",
            requested_groups=("openbnb",),
        )
    assert await _skill_records(sqlite_session_factory) == ()


async def _add_user(
    session_factory: AsyncSessionFactory,
    user_id: str,
) -> None:
    async with session_factory() as session:
        session.add(
            FirebaseUserRecord(
                id=user_id,
                firebase_user_id=f"firebase-{user_id}",
            ),
        )
        await session.commit()


async def _add_server(
    session_factory: AsyncSessionFactory,
    *,
    user_id: str,
    label: str,
    preset_id: str,
    tools: Iterable[str],
    preset_variant_id: str | None = None,
    enabled: bool = True,
    trust_state: McpTrustState = McpTrustState.TRUSTED,
    status: McpServerStatus = McpServerStatus.CONNECTED,
    discovered_tools: Iterable[str] | None = None,
    allowed_tools: Iterable[str] | None = None,
    url: str = "https://example.test/mcp",
    auth_config: dict[str, object] | None = None,
) -> None:
    tool_tuple = tuple(tools)
    async with session_factory() as session:
        session.add(
            UserMcpServerRecord(
                user_id=user_id,
                label=label,
                transport="streamable_http",
                url=url,
                auth_type="none",
                enabled=enabled,
                trust_state=trust_state.value,
                status=status.value,
                auth_config=auth_config or {},
                safe_metadata={
                    "preset_id": preset_id,
                    "preset_variant_id": preset_variant_id,
                },
                discovered_tools=list(
                    tool_tuple if discovered_tools is None else discovered_tools,
                ),
                allowed_tools=list(
                    tool_tuple if allowed_tools is None else allowed_tools,
                ),
            ),
        )
        await session.commit()


async def _set_skill_state(
    session_factory: AsyncSessionFactory,
    *,
    slug: str,
    status: SkillStatus,
    body_markdown: str,
) -> None:
    async with session_factory() as session:
        result = await session.exec(
            select(AgentSkillRecord).where(col(AgentSkillRecord.slug) == slug),
        )
        record = result.one()
        record.status = status.value
        record.body_markdown = body_markdown
        await session.commit()


async def _skill_records(
    session_factory: AsyncSessionFactory,
) -> tuple[AgentSkillRecord, ...]:
    async with session_factory() as session:
        result = await session.exec(
            select(AgentSkillRecord).order_by(col(AgentSkillRecord.created_at)),
        )
    return tuple(result.all())
