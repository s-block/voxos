from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from livekit.agents import llm

from voxos.agent_core.models import AgentTool
from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicySnapshot

VoiceLiveKitTool = llm.Tool | llm.Toolset
VoiceCatalogFunctionTool = llm.FunctionTool | llm.RawFunctionTool


@dataclass(frozen=True, slots=True)
class VoiceCatalogTool:
    definition: AgentTool
    function_tool: VoiceCatalogFunctionTool

    def __post_init__(self) -> None:
        if self.definition.name != self.function_tool.info.name:
            message = "Voice catalog definition and function tool names must match"
            raise ValueError(message)


class VoiceToolProvider(Protocol):
    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        """Return LiveKit-compatible tools for the active voice session."""


@runtime_checkable
class ScopedVoiceToolProvider(Protocol):
    async def get_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceLiveKitTool]:
        """Return LiveKit-compatible tools for the active agent scope."""


@runtime_checkable
class ScopedVoiceCatalogToolProvider(Protocol):
    async def get_catalog_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceCatalogTool]:
        """Return external tools for run-scoped voice discovery."""


@dataclass(frozen=True, slots=True)
class ScopedVoiceToolProviderAdapter:
    provider: VoiceToolProvider
    scope: AgentScope
    effective_tool_policy: EffectiveToolPolicySnapshot | None = None

    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        if user_id != self.scope.owner_user_id:
            return ()
        if isinstance(self.provider, ScopedVoiceToolProvider):
            return await self.provider.get_tools_for_scope(
                self.scope,
                effective_tool_policy=self.effective_tool_policy,
            )
        return await self.provider.get_tools(user_id)


class NoopVoiceToolProvider:
    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        return ()
