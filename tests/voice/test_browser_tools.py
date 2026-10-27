from dataclasses import dataclass

import pytest
from livekit.agents.llm import ChatMessage
from livekit.agents.llm.utils import build_strict_openai_schema

from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyEntry,
    EffectiveToolPolicySnapshot,
)
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationDecision,
    ToolGrantScope,
    ToolReference,
    ToolRisk,
)
from voxos.browser_accounts.models import BrowserAccountProvider
from voxos.browser_accounts.services import AvailableBrowserAccount
from voxos.voice.browser_tools import (
    BROWSER_VOICE_TOOL_NAME,
    OWNER_BROWSER_ACCOUNT_VOICE_SOURCE,
    AuthenticatedBrowserVoiceToolProvider,
    latest_voice_browser_request,
)


class FakeBrowserAccountService:
    async def list_assigned_ready_accounts(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> tuple[AvailableBrowserAccount, ...]:
        assert user_id == "user-1"
        assert agent_id == "agent-1"
        return (available_account(),)


class FakeBrowserRuntimeService:
    pass


@dataclass
class FakeAuthorizationService:
    async def authorize_tool(
        self,
        *,
        scope: AgentScope,
        reference: ToolReference,
    ) -> ToolAuthorizationDecision:
        return ToolAuthorizationDecision(
            allowed=True,
            reason="allowed",
            reference=reference,
        )


@pytest.mark.asyncio
async def test_general_browser_voice_tool_is_only_exposed_to_owner_app_voice() -> None:
    provider = AuthenticatedBrowserVoiceToolProvider(
        profile_service=FakeBrowserAccountService(),  # type: ignore[arg-type]
        runtime_service=FakeBrowserRuntimeService(),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(),  # type: ignore[arg-type]
    )

    tools = await provider.get_tools_for_scope(
        voice_scope(OWNER_BROWSER_ACCOUNT_VOICE_SOURCE),
        effective_tool_policy=allowed_policy(),
    )

    assert len(tools) == 1
    assert tools[0].info.name == BROWSER_VOICE_TOOL_NAME
    assert "Fallback for a general public" in tools[0].info.description
    assert "use it before this browser" in tools[0].info.description
    assert "Eon Next" in tools[0].info.description
    parameters = build_strict_openai_schema(tools[0])["function"]["parameters"]
    assert "instruction" in parameters["properties"]
    assert "start_url" in parameters["properties"]
    assert "profile" in parameters["properties"]
    assert (
        await provider.get_tools_for_scope(
            voice_scope("voice.worker"),
            effective_tool_policy=allowed_policy(),
        )
        == ()
    )
    assert await provider.get_tools("user-1") == ()

    catalog_tools = await provider.get_catalog_tools_for_scope(
        voice_scope(OWNER_BROWSER_ACCOUNT_VOICE_SOURCE),
        effective_tool_policy=allowed_policy(),
    )
    assert len(catalog_tools) == 1
    assert catalog_tools[0].definition.name == BROWSER_VOICE_TOOL_NAME
    assert catalog_tools[0].definition.catalog_label == "Browser"
    assert catalog_tools[0].function_tool.info.name == BROWSER_VOICE_TOOL_NAME


@pytest.mark.asyncio
async def test_general_browser_voice_tool_remains_available_without_saved_profile_grants() -> None:
    provider = AuthenticatedBrowserVoiceToolProvider(
        profile_service=FakeBrowserAccountService(),  # type: ignore[arg-type]
        runtime_service=FakeBrowserRuntimeService(),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(),  # type: ignore[arg-type]
    )

    tools = await provider.get_tools_for_scope(
        voice_scope(OWNER_BROWSER_ACCOUNT_VOICE_SOURCE),
        effective_tool_policy=EffectiveToolPolicySnapshot(
            user_id="user-1",
            agent_id="agent-1",
        ),
    )

    assert len(tools) == 1
    assert tools[0].info.name == BROWSER_VOICE_TOOL_NAME
    assert "Eon Next" not in tools[0].info.description


def test_latest_voice_browser_request_uses_latest_finalized_user_message() -> None:
    request = latest_voice_browser_request(
        (
            ChatMessage(role="user", content=["Check my old bill"], id="turn-old"),
            ChatMessage(role="assistant", content=["Which account?"]),
            ChatMessage(
                role="user",
                content=["Sign in to Eon and get my latest bill"],
                id="turn-latest",
            ),
        ),
    )

    assert request.client_turn_id == "turn-latest"
    assert request.task == "Sign in to Eon and get my latest bill"


def voice_scope(source: str) -> AgentScope:
    return AgentScope(
        owner_user_id="user-1",
        agent_id="agent-1",
        session_id="voice-session",
        channel="livekit",
        source=source,
    )


def available_account() -> AvailableBrowserAccount:
    return AvailableBrowserAccount(
        account_id="account-1",
        label="Eon Next",
        provider=BrowserAccountProvider.BROWSERLESS,
        provider_connection_id="browserless-server",
        primary_origin="https://eonnext.com",
        execution_tool_name="browserless_agent",
    )


def allowed_policy() -> EffectiveToolPolicySnapshot:
    return EffectiveToolPolicySnapshot(
        user_id="user-1",
        agent_id="agent-1",
        entries=(
            EffectiveToolPolicyEntry(
                tool_name="browserless_agent",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id="browserless-server",
                risk=ToolRisk.HIGH,
            ),
        ),
    )
