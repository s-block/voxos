import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyEntry,
    EffectiveToolPolicySnapshot,
)
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolRisk
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import IntegrationDirection, IntegrationProviderType
from voxos.integrations.records import IntegrationCallSessionRecord
from voxos.mcp.models import (
    McpAuthType,
    McpServerStatus,
    McpTransportType,
    McpTrustState,
)
from voxos.mcp.non_admin_access import NonAdminMcpToolAccess
from voxos.mcp.records import UserMcpServerRecord
from voxos.voice.caller_access import (
    VoiceCallerAccess,
    VoiceCallerAccessLevel,
    VoiceCallerAccessResolver,
    policy_for_caller_access,
)


@pytest.mark.asyncio
async def test_phone_caller_access_uses_immutable_admin_snapshot(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        await add_call_session(
            session,
            contact_id="contact-123",
            contact_display_name="Ada Lovelace",
            caller_is_admin=True,
        )

    access = await VoiceCallerAccessResolver(sqlite_session_factory).resolve(
        user_id="user-123",
        agent_id="agent-123",
        runtime_session_id="runtime-123",
        source=IntegrationProviderType.TWILIO.value,
    )

    assert access == VoiceCallerAccess(
        level=VoiceCallerAccessLevel.ADMIN_CONTACT,
        contact_display_name="Ada Lovelace",
    )
    assert access.allows_private_context is True


@pytest.mark.asyncio
async def test_phone_caller_access_fails_closed_for_unknown_or_wrong_agent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        await add_call_session(
            session,
            contact_id=None,
            contact_display_name=None,
            caller_is_admin=False,
        )
    resolver = VoiceCallerAccessResolver(sqlite_session_factory)

    unknown = await resolver.resolve(
        user_id="user-123",
        agent_id="agent-123",
        runtime_session_id="missing-runtime",
        source=IntegrationProviderType.TWILIO.value,
    )
    wrong_agent = await resolver.resolve(
        user_id="user-123",
        agent_id="other-agent",
        runtime_session_id="runtime-123",
        source=IntegrationProviderType.TWILIO.value,
    )

    assert unknown.level == VoiceCallerAccessLevel.LIMITED_CONTACT
    assert wrong_agent.level == VoiceCallerAccessLevel.LIMITED_CONTACT
    assert unknown.allows_private_context is False


@pytest.mark.asyncio
async def test_non_phone_voice_session_keeps_owner_access_without_call_lookup(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    access = await VoiceCallerAccessResolver(sqlite_session_factory).resolve(
        user_id="user-123",
        agent_id="agent-123",
        runtime_session_id="runtime-123",
        source="livekit_api",
    )

    assert access.level == VoiceCallerAccessLevel.OWNER
    assert access.allows_private_context is True


@pytest.mark.asyncio
async def test_unknown_future_voice_source_fails_closed_as_limited(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        await add_call_session(
            session,
            contact_id="contact-123",
            contact_display_name="Ada Lovelace",
            caller_is_admin=True,
            provider="future_voice_provider",
        )

    access = await VoiceCallerAccessResolver(sqlite_session_factory).resolve(
        user_id="user-123",
        agent_id="agent-123",
        runtime_session_id="runtime-123",
        source="future_voice_provider",
    )

    assert access.level == VoiceCallerAccessLevel.LIMITED_CONTACT
    assert access.allows_private_context is False


@pytest.mark.asyncio
async def test_limited_caller_allows_only_opted_in_mcp_tools(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        session.add_all(
            (
                mcp_server_record(
                    server_id="travel-server",
                    label="Travel",
                    url="https://travel.example.com/mcp",
                    allowed_tools=("search_hotels",),
                    non_admin_tools_enabled=True,
                ),
                mcp_server_record(
                    server_id="github-server",
                    label="GitHub",
                    url="https://github.example.com/mcp",
                    allowed_tools=("issues.list",),
                ),
            ),
        )
        await session.commit()

    access = await VoiceCallerAccessResolver(sqlite_session_factory).resolve(
        user_id="user-123",
        agent_id="agent-123",
        runtime_session_id="missing-runtime",
        source=IntegrationProviderType.TWILIO.value,
    )
    base_policy = EffectiveToolPolicySnapshot(
        user_id="user-123",
        agent_id="agent-123",
        entries=(
            policy_entry(
                "search_hotels",
                ToolGrantScope.MCP_SERVER,
                mcp_server_id="travel-server",
            ),
            policy_entry(
                "issues.list",
                ToolGrantScope.MCP_SERVER,
                mcp_server_id="github-server",
            ),
        ),
    )

    limited_policy = policy_for_caller_access(
        base_policy=base_policy,
        caller_access=access,
    )

    assert access.level == VoiceCallerAccessLevel.LIMITED_CONTACT
    assert access.limited_mcp_tool_access == (
        NonAdminMcpToolAccess(
            server_id="travel-server",
            tool_names=("search_hotels",),
        ),
    )
    assert tuple(entry.tool_name for entry in limited_policy.entries) == ("search_hotels",)
    assert tuple(entry.mcp_server_id for entry in limited_policy.entries) == ("travel-server",)


@pytest.mark.asyncio
async def test_limited_caller_requires_a_usable_opted_in_mcp_server(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        session.add_all(
            (
                mcp_server_record(
                    server_id="active-server",
                    label="Active",
                    url="https://active.example.com/mcp",
                    allowed_tools=("search",),
                    non_admin_tools_enabled=True,
                ),
                mcp_server_record(
                    server_id="disabled-server",
                    label="Disabled",
                    url="https://disabled.example.com/mcp",
                    allowed_tools=("search",),
                    enabled=False,
                    non_admin_tools_enabled=True,
                ),
                mcp_server_record(
                    server_id="untrusted-server",
                    label="Untrusted",
                    url="https://untrusted.example.com/mcp",
                    allowed_tools=("search",),
                    trust_state=McpTrustState.UNTRUSTED,
                    non_admin_tools_enabled=True,
                ),
                mcp_server_record(
                    server_id="disconnected-server",
                    label="Disconnected",
                    url="https://disconnected.example.com/mcp",
                    allowed_tools=("search",),
                    status=McpServerStatus.FAILED,
                    non_admin_tools_enabled=True,
                ),
                mcp_server_record(
                    server_id="no-tools-server",
                    label="No tools",
                    url="https://no-tools.example.com/mcp",
                    allowed_tools=(),
                    non_admin_tools_enabled=True,
                ),
            ),
        )
        await session.commit()

    access = await VoiceCallerAccessResolver(sqlite_session_factory).resolve(
        user_id="user-123",
        agent_id="agent-123",
        runtime_session_id="missing-runtime",
        source=IntegrationProviderType.TWILIO.value,
    )

    assert access.limited_mcp_tool_access == (NonAdminMcpToolAccess(server_id="active-server", tool_names=("search",)),)


def test_limited_caller_policy_intersects_exact_mcp_tool_access() -> None:
    base_policy = EffectiveToolPolicySnapshot(
        user_id="user-123",
        agent_id="agent-123",
        entries=(
            policy_entry("get_current_datetime", ToolGrantScope.INTERNAL),
            policy_entry(
                "issues.list",
                ToolGrantScope.MCP_SERVER,
                mcp_server_id="server-123",
            ),
            policy_entry(
                "issues.delete",
                ToolGrantScope.MCP_SERVER,
                mcp_server_id="server-123",
            ),
            policy_entry(
                "issues.list",
                ToolGrantScope.MCP_SERVER,
                mcp_server_id="server-456",
            ),
            policy_entry(
                "place_call",
                ToolGrantScope.INTEGRATION,
                integration_id="integration-123",
            ),
        ),
    )

    limited_policy = policy_for_caller_access(
        base_policy=base_policy,
        caller_access=VoiceCallerAccess(
            level=VoiceCallerAccessLevel.LIMITED_CONTACT,
            limited_mcp_tool_access=(
                NonAdminMcpToolAccess(
                    server_id="server-123",
                    tool_names=("issues.list",),
                ),
            ),
        ),
    )

    assert tuple(entry.tool_name for entry in limited_policy.entries) == ("issues.list",)
    assert tuple(entry.mcp_server_id for entry in limited_policy.entries) == ("server-123",)
    assert limited_policy.parent_policy_id == base_policy.id


def test_admin_and_owner_policy_remain_unchanged() -> None:
    base_policy = EffectiveToolPolicySnapshot(
        user_id="user-123",
        agent_id="agent-123",
        entries=(policy_entry("get_current_datetime", ToolGrantScope.INTERNAL),),
    )

    admin_policy = policy_for_caller_access(
        base_policy=base_policy,
        caller_access=VoiceCallerAccess(
            level=VoiceCallerAccessLevel.ADMIN_CONTACT,
        ),
    )
    owner_policy = policy_for_caller_access(
        base_policy=base_policy,
        caller_access=VoiceCallerAccess(level=VoiceCallerAccessLevel.OWNER),
    )

    assert admin_policy is base_policy
    assert owner_policy is base_policy


async def add_call_session(
    session: AsyncSession,
    *,
    contact_id: str | None,
    contact_display_name: str | None,
    caller_is_admin: bool,
    provider: str = IntegrationProviderType.TWILIO.value,
) -> None:
    session.add(
        IntegrationCallSessionRecord(
            user_id="user-123",
            integration_id="integration-123",
            resource_id="resource-123",
            provider=provider,
            direction=IntegrationDirection.INBOUND.value,
            external_call_id="call-123",
            from_number="+15551234567",
            to_number="+15557654321",
            agent_id="agent-123",
            runtime_session_id="runtime-123",
            contact_id=contact_id,
            contact_display_name=contact_display_name,
            caller_is_admin=caller_is_admin,
        ),
    )
    await session.commit()


def policy_entry(
    tool_name: str,
    tool_source: ToolGrantScope,
    *,
    mcp_server_id: str | None = None,
    integration_id: str | None = None,
) -> EffectiveToolPolicyEntry:
    return EffectiveToolPolicyEntry(
        tool_name=tool_name,
        tool_source=tool_source,
        mcp_server_id=mcp_server_id,
        integration_id=integration_id,
        risk=ToolRisk.LOW,
    )


def mcp_server_record(
    *,
    server_id: str,
    label: str,
    url: str,
    allowed_tools: tuple[str, ...],
    enabled: bool = True,
    trust_state: McpTrustState = McpTrustState.TRUSTED,
    status: McpServerStatus = McpServerStatus.CONNECTED,
    non_admin_tools_enabled: bool = False,
) -> UserMcpServerRecord:
    return UserMcpServerRecord(
        id=server_id,
        user_id="user-123",
        label=label,
        transport=McpTransportType.STREAMABLE_HTTP.value,
        url=url,
        auth_type=McpAuthType.NONE.value,
        enabled=enabled,
        non_admin_tools_enabled=non_admin_tools_enabled,
        trust_state=trust_state.value,
        status=status.value,
        allowed_tools=list(allowed_tools),
        discovered_tools=list(allowed_tools),
    )
