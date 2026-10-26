from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_memory.models import (
    AgentMemory,
    MemoryCreateRequest,
    MemorySearchRequest,
)
from voxos.agent_memory.services import AgentMemoryService
from voxos.db.tenant import tenant_user_context
from voxos.mcp.auth import (
    AuthenticatedMcpUserResolver,
    McpTransportAuthConfig,
    McpUserResolver,
)

MEMORY_MCP_TOOL_NAMES = (
    "list_agent_memories",
    "create_agent_memory",
    "propose_agent_memory",
    "archive_agent_memory",
    "confirm_agent_memory",
)


class AgentMemoryListResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memories: tuple[AgentMemory, ...] = Field(default_factory=tuple)


class AgentMemoryMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory: AgentMemory | None = None
    message: str | None = Field(default=None, min_length=1, max_length=512)


@dataclass(frozen=True, slots=True)
class AgentMemoryMcpTools:
    memory_service: AgentMemoryService
    user_resolver: McpUserResolver

    async def list_agent_memories(
        self,
        request: MemorySearchRequest | None = None,
    ) -> AgentMemoryListResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            return AgentMemoryListResult(
                memories=await self.memory_service.list_memories(
                    user_id=user_id,
                    request=request,
                ),
            )

    async def create_agent_memory(
        self,
        request: MemoryCreateRequest,
    ) -> AgentMemoryMutationResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            return AgentMemoryMutationResult(
                memory=await self.memory_service.propose_memory(
                    user_id=user_id,
                    request=request,
                ),
            )

    async def propose_agent_memory(
        self,
        request: MemoryCreateRequest,
    ) -> AgentMemoryMutationResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            return AgentMemoryMutationResult(
                memory=await self.memory_service.propose_memory(
                    user_id=user_id,
                    request=request,
                ),
            )

    async def archive_agent_memory(self, memory_id: str) -> AgentMemoryMutationResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            memory = await self.memory_service.archive_memory(
                user_id=user_id,
                memory_id=memory_id,
            )
        return AgentMemoryMutationResult(
            memory=memory,
            message=None if memory is not None else "Memory was not found.",
        )

    async def confirm_agent_memory(self, memory_id: str) -> AgentMemoryMutationResult:
        self.user_resolver.require_user_id()
        message = "Confirm proposed memories through authenticated Knowledge review"
        raise PermissionError(message)


def create_agent_memory_mcp_server(
    memory_service: AgentMemoryService,
    auth_config: McpTransportAuthConfig,
) -> FastMCP:
    tools = AgentMemoryMcpTools(
        memory_service=memory_service,
        user_resolver=AuthenticatedMcpUserResolver(require_local_user_claim=True),
    )
    mcp = FastMCP(
        name="Voxos Agent Memory",
        instructions=(
            "Manage retained long-term memories for the authenticated Voxos user. "
            "The authenticated user is derived from the bearer token; tools must "
            "not accept or override user IDs."
        ),
        json_response=True,
        token_verifier=auth_config.token_verifier,
        auth=auth_config.auth_settings(),
    )

    @mcp.tool(name="list_agent_memories")
    async def list_agent_memories(
        request: MemorySearchRequest | None = None,
    ) -> AgentMemoryListResult:
        return await tools.list_agent_memories(request)

    @mcp.tool(name="create_agent_memory")
    async def create_agent_memory(
        request: MemoryCreateRequest,
    ) -> AgentMemoryMutationResult:
        return await tools.create_agent_memory(request)

    @mcp.tool(name="propose_agent_memory")
    async def propose_agent_memory(
        request: MemoryCreateRequest,
    ) -> AgentMemoryMutationResult:
        return await tools.propose_agent_memory(request)

    @mcp.tool(name="archive_agent_memory")
    async def archive_agent_memory(memory_id: str) -> AgentMemoryMutationResult:
        return await tools.archive_agent_memory(memory_id)

    @mcp.tool(name="confirm_agent_memory")
    async def confirm_agent_memory(memory_id: str) -> AgentMemoryMutationResult:
        return await tools.confirm_agent_memory(memory_id)

    return mcp
