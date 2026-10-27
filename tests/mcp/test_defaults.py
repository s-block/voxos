import pytest
from sqlmodel import select

from voxos.db.session import AsyncSessionFactory
from voxos.mcp.defaults import (
    DEFAULT_EXA_TOOL_NAMES,
    InitialMcpServerProvisioner,
    platform_default_tool_names,
)
from voxos.mcp.models import McpServerSafeMetadata
from voxos.mcp.records import UserMcpServerRecord


@pytest.mark.asyncio
async def test_initial_mcp_provisioner_creates_connected_keyless_exa_once(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    provisioner = InitialMcpServerProvisioner(sqlite_session_factory)

    await provisioner.initialize_user("user-123")
    await provisioner.initialize_user("user-123")

    async with sqlite_session_factory() as session:
        result = await session.exec(select(UserMcpServerRecord))
        servers = result.all()

    assert len(servers) == 1
    server = servers[0]
    assert server.url == "https://mcp.exa.ai/mcp"
    assert server.enabled is True
    assert server.status == "connected"
    assert server.trust_state == "trusted"
    assert tuple(server.allowed_tools) == DEFAULT_EXA_TOOL_NAMES
    assert tuple(server.discovered_tools) == DEFAULT_EXA_TOOL_NAMES
    assert platform_default_tool_names(server) == DEFAULT_EXA_TOOL_NAMES
    metadata = McpServerSafeMetadata.model_validate(server.safe_metadata)
    assert metadata.platform_default is True
    assert {definition.name for definition in metadata.tool_definitions} == set(
        DEFAULT_EXA_TOOL_NAMES,
    )
    search = next(definition for definition in metadata.tool_definitions if definition.name == "web_search_exa")
    assert search.input_schema["required"] == ["query"]


@pytest.mark.asyncio
async def test_initial_mcp_provisioner_preserves_existing_exa_opt_out(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        session.add(
            UserMcpServerRecord(
                user_id="user-123",
                label="My Exa",
                transport="streamable_http",
                url="https://mcp.exa.ai/mcp",
                auth_type="none",
                enabled=False,
            ),
        )
        await session.commit()

    await InitialMcpServerProvisioner(sqlite_session_factory).initialize_user(
        "user-123",
    )

    async with sqlite_session_factory() as session:
        result = await session.exec(select(UserMcpServerRecord))
        servers = result.all()

    assert len(servers) == 1
    assert servers[0].label == "My Exa"
    assert servers[0].enabled is False


@pytest.mark.asyncio
async def test_initial_mcp_provisioner_uses_available_label(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        session.add(
            UserMcpServerRecord(
                user_id="user-123",
                label="Exa",
                transport="streamable_http",
                url="https://custom.example/mcp",
                auth_type="none",
                enabled=True,
            ),
        )
        await session.commit()

    await InitialMcpServerProvisioner(sqlite_session_factory).initialize_user(
        "user-123",
    )

    async with sqlite_session_factory() as session:
        result = await session.exec(select(UserMcpServerRecord))
        servers = result.all()

    assert {server.label for server in servers} == {"Exa", "Exa Web Search"}
    assert any(server.url == "https://mcp.exa.ai/mcp" for server in servers)
