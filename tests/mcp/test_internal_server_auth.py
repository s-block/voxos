from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP

from voxos.agent_memory.mcp import create_agent_memory_mcp_server
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.mcp import create_agent_planning_mcp_server
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_skills.mcp import create_agent_skill_mcp_server
from voxos.agent_skills.services import AgentSkillService
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.auth import McpTransportAuthConfig
from voxos.persona.mcp import create_persona_mcp_server
from voxos.persona.services import AgentPersonaService

ServerFactory = Callable[[AsyncSessionFactory, McpTransportAuthConfig], FastMCP]


@dataclass(slots=True)
class CountingTokenVerifier:
    calls: int = 0

    async def verify_token(self, token: str) -> AccessToken | None:
        self.calls += 1
        return AccessToken(
            token=token,
            client_id="firebase-user-123",
            scopes=[],
            subject="local-user-123",
        )


def auth_config(verifier: CountingTokenVerifier) -> McpTransportAuthConfig:
    return McpTransportAuthConfig(
        token_verifier=verifier,
        issuer_url="https://issuer.example.com",
        resource_server_url="https://resource.example.com",
    )


def memory_server(
    session_factory: AsyncSessionFactory,
    config: McpTransportAuthConfig,
) -> FastMCP:
    return create_agent_memory_mcp_server(
        memory_service=AgentMemoryService(session_factory),
        auth_config=config,
    )


def planning_server(
    session_factory: AsyncSessionFactory,
    config: McpTransportAuthConfig,
) -> FastMCP:
    return create_agent_planning_mcp_server(
        planning_service=AgentPlanningService(session_factory),
        auth_config=config,
    )


def skill_server(
    session_factory: AsyncSessionFactory,
    config: McpTransportAuthConfig,
) -> FastMCP:
    return create_agent_skill_mcp_server(
        skill_service=AgentSkillService(session_factory),
        auth_config=config,
    )


def persona_server(
    session_factory: AsyncSessionFactory,
    config: McpTransportAuthConfig,
) -> FastMCP:
    return create_persona_mcp_server(
        persona_service=AgentPersonaService(),
        auth_config=config,
    )


@pytest.mark.parametrize(
    ("server_factory", "server_name"),
    (
        (memory_server, "memory"),
        (planning_server, "planning"),
        (skill_server, "skills"),
        (persona_server, "persona"),
    ),
)
def test_internal_mcp_server_rejects_missing_bearer_token_before_tools(
    sqlite_session_factory: AsyncSessionFactory,
    server_factory: ServerFactory,
    server_name: str,
) -> None:
    verifier = CountingTokenVerifier()
    server = server_factory(sqlite_session_factory, auth_config(verifier))

    response = TestClient(server.streamable_http_app()).post("/mcp")

    assert response.status_code == 401, server_name
    assert response.json() == {
        "error": "invalid_token",
        "error_description": "Authentication required",
    }
    assert verifier.calls == 0
