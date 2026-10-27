from dataclasses import dataclass

import pytest

from voxos.agent_core.browser_tools import (
    BROWSER_TOOL_NAME,
    BROWSER_USE_EXECUTION_TOOL_NAME,
    BrowserAgentToolProvider,
)
from voxos.agent_core.models import AgentRunContext
from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationDecision,
    ToolGrantScope,
    ToolReference,
    ToolRisk,
)
from voxos.browser_accounts.errors import BrowserAccountAuthorizationError
from voxos.browser_accounts.models import BrowserAccountProvider
from voxos.browser_accounts.services import (
    AvailableBrowserAccount,
    PendingBrowserAuthorization,
)
from voxos.browser_accounts.tool_capabilities import browser_profile_handle
from voxos.browser_runtime.contracts import BrowserTaskActionMode, BrowserTaskResult


class FakeBrowserService:
    def __init__(
        self,
        *,
        pending: bool = False,
        required: bool = False,
        accounts: tuple[AvailableBrowserAccount, ...] = (),
        pending_provider: BrowserAccountProvider = BrowserAccountProvider.BROWSER_USE_CLOUD,
    ) -> None:
        self.pending = pending
        self.required = required
        self.accounts = accounts
        self.pending_provider = pending_provider
        self.executions: list[dict[str, object]] = []

    async def pending_authorization(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        run_id: str | None = None,
    ) -> PendingBrowserAuthorization | None:
        del user_id, agent_id, session_id, run_id
        if not self.pending:
            return None
        return PendingBrowserAuthorization(
            authorization_id="approval-1",
            provider_connection_id=(
                None if self.pending_provider == BrowserAccountProvider.STAGEHAND_LOCAL else "browser-provider-server"
            ),
            execution_tool_name=(
                BROWSER_TOOL_NAME
                if self.pending_provider == BrowserAccountProvider.STAGEHAND_LOCAL
                else BROWSER_USE_EXECUTION_TOOL_NAME
            ),
            required=self.required,
            provider=self.pending_provider,
        )

    async def list_assigned_ready_accounts(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> tuple[AvailableBrowserAccount, ...]:
        del user_id, agent_id
        return self.accounts

    async def execute_assigned_run_task(
        self,
        *,
        user_id: str,
        agent_id: str,
        account_id: str,
        session_id: str,
        run_id: str,
        instruction: str,
        start_url: str | None,
        action_mode: BrowserTaskActionMode,
    ) -> BrowserTaskResult:
        self.executions.append(
            {
                "kind": "profile",
                "user_id": user_id,
                "agent_id": agent_id,
                "account_id": account_id,
                "session_id": session_id,
                "run_id": run_id,
                "instruction": instruction,
                "start_url": start_url,
                "action_mode": action_mode,
            },
        )
        return BrowserTaskResult(output_text="Profile result", successful=True)

    async def execute_authorized_task(
        self,
        *,
        user_id: str,
        agent_id: str,
        run_id: str,
        session_id: str,
        expected_authorization_id: str | None,
    ) -> BrowserTaskResult:
        self.executions.append(
            {
                "kind": "authorized",
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "session_id": session_id,
                "authorization_id": expected_authorization_id,
            },
        )
        return BrowserTaskResult(output_text="Authorized result", successful=True)


class FakeBrowserRuntimeService:
    def __init__(self) -> None:
        self.executions: list[dict[str, object]] = []

    async def execute_task(
        self,
        *,
        user_id: str,
        instruction: str,
        start_url: str,
        allowed_origins: tuple[str, ...],
        action_mode: BrowserTaskActionMode,
    ) -> BrowserTaskResult:
        self.executions.append(
            {
                "kind": "public",
                "user_id": user_id,
                "instruction": instruction,
                "start_url": start_url,
                "allowed_origins": allowed_origins,
                "action_mode": action_mode,
            },
        )
        return BrowserTaskResult(output_text="Public result", successful=True)


@dataclass
class FakeAuthorizationService:
    allowed: bool = True
    authorized_reference: ToolReference | None = None

    async def allowed_tool_names(
        self,
        *,
        scope: AgentScope,
        tool_source: ToolGrantScope,
        tool_names: tuple[str, ...],
        mcp_server_id: str | None = None,
        integration_id: str | None = None,
    ) -> tuple[str, ...]:
        del scope, tool_source, mcp_server_id, integration_id
        return tool_names if self.allowed else ()

    async def authorize_tool(
        self,
        *,
        scope: AgentScope,
        reference: ToolReference,
    ) -> ToolAuthorizationDecision:
        del scope
        self.authorized_reference = reference
        return ToolAuthorizationDecision(
            allowed=self.allowed,
            reason="allowed" if self.allowed else "denied",
            reference=reference,
        )


@pytest.mark.asyncio
async def test_exposes_one_general_browser_tool_with_optional_profiles() -> None:
    account = available_account()
    provider = BrowserAgentToolProvider(
        profile_service=FakeBrowserService(accounts=(account,)),  # type: ignore[arg-type]
        runtime_service=FakeBrowserRuntimeService(),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(),  # type: ignore[arg-type]
    )

    tools = await provider.list_tools(root_context())

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == BROWSER_TOOL_NAME
    assert tool.source == ToolGrantScope.INTERNAL
    assert tool.risk == ToolRisk.HIGH
    assert tool.always_visible is True
    assert tool.parameters_schema["required"] == ["instruction"]
    profile_schema = tool.parameters_schema["properties"]["profile"]
    assert profile_schema["enum"] == [browser_profile_handle(account.account_id)]
    assert "Eon Next" in tool.description
    assert "https://eonnext.com" in tool.description


@pytest.mark.asyncio
async def test_public_browser_task_uses_agent_instruction_without_a_profile() -> None:
    service = FakeBrowserService()
    runtime_service = FakeBrowserRuntimeService()
    provider = BrowserAgentToolProvider(
        profile_service=service,  # type: ignore[arg-type]
        runtime_service=runtime_service,  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(),  # type: ignore[arg-type]
    )

    result = await provider.execute_tool(
        context=root_context(),
        tool_name=BROWSER_TOOL_NAME,
        call_id="call-public",
        arguments_json={
            "instruction": "Summarize the API documentation",
            "start_url": "https://docs.example.com/api",
        },
    )

    assert result.output_text == "Public result"
    assert result.raw_output == {"completed": True}
    assert runtime_service.executions == [
        {
            "kind": "public",
            "user_id": "user-1",
            "instruction": "Summarize the API documentation",
            "start_url": "https://docs.example.com/api",
            "allowed_origins": ("https://docs.example.com",),
            "action_mode": BrowserTaskActionMode.READ_ONLY,
        },
    ]


@pytest.mark.asyncio
async def test_saved_profile_task_uses_agent_instruction_and_scoped_profile() -> None:
    account = available_account()
    service = FakeBrowserService(accounts=(account,))
    authorization = FakeAuthorizationService()
    provider = BrowserAgentToolProvider(
        profile_service=service,  # type: ignore[arg-type]
        runtime_service=FakeBrowserRuntimeService(),  # type: ignore[arg-type]
        authorization_service=authorization,  # type: ignore[arg-type]
    )

    result = await provider.execute_tool(
        context=root_context(),
        tool_name=BROWSER_TOOL_NAME,
        call_id="call-profile",
        arguments_json={
            "instruction": "Sign in and download the latest bill",
            "profile": browser_profile_handle(account.account_id),
            "action_mode": "state_changes_allowed",
        },
    )

    assert result.output_text == "Profile result"
    assert service.executions[0]["instruction"] == "Sign in and download the latest bill"
    assert service.executions[0]["action_mode"] == BrowserTaskActionMode.STATE_CHANGES_ALLOWED
    assert authorization.authorized_reference == ToolReference(
        tool_name="browserless_agent",
        tool_source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="browserless-server",
        risk=ToolRisk.HIGH,
    )


@pytest.mark.asyncio
async def test_unassigned_profile_handle_is_rejected() -> None:
    provider = BrowserAgentToolProvider(
        profile_service=FakeBrowserService(),  # type: ignore[arg-type]
        runtime_service=FakeBrowserRuntimeService(),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(),  # type: ignore[arg-type]
    )

    with pytest.raises(BrowserAccountAuthorizationError):
        await provider.execute_tool(
            context=root_context(),
            tool_name=BROWSER_TOOL_NAME,
            call_id="call-denied",
            arguments_json={
                "instruction": "Use a different user's profile",
                "profile": browser_profile_handle("attacker-account"),
            },
        )


@pytest.mark.asyncio
async def test_scheduled_browser_task_remains_required_and_pre_authorized() -> None:
    service = FakeBrowserService(
        pending=True,
        required=True,
        pending_provider=BrowserAccountProvider.STAGEHAND_LOCAL,
    )
    provider = BrowserAgentToolProvider(
        profile_service=service,  # type: ignore[arg-type]
        runtime_service=FakeBrowserRuntimeService(),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(),  # type: ignore[arg-type]
    )

    tools = await provider.list_tools(root_context())
    assert tools[0].required is True

    result = await provider.execute_tool(
        context=root_context(),
        tool_name=BROWSER_TOOL_NAME,
        call_id="call-scheduled",
        arguments_json={"instruction": "Complete the scheduled browser task"},
    )

    assert result.output_text == "Authorized result"
    assert service.executions[0]["kind"] == "authorized"


@pytest.mark.asyncio
async def test_browser_tool_rejects_invalid_arguments_and_subagents() -> None:
    provider = BrowserAgentToolProvider(
        profile_service=FakeBrowserService(),  # type: ignore[arg-type]
        runtime_service=FakeBrowserRuntimeService(),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(),  # type: ignore[arg-type]
    )
    with pytest.raises(BrowserAccountAuthorizationError):
        await provider.execute_tool(
            context=root_context(),
            tool_name=BROWSER_TOOL_NAME,
            call_id="call-invalid",
            arguments_json={"instruction": "Browse without a start URL"},
        )

    subagent = root_context().model_copy(update={"parent_run_id": "parent-run"})
    assert await provider.list_tools(subagent) == ()
    with pytest.raises(BrowserAccountAuthorizationError):
        await provider.execute_tool(
            context=subagent,
            tool_name=BROWSER_TOOL_NAME,
            call_id="call-subagent",
            arguments_json={
                "instruction": "Browse",
                "start_url": "https://example.com",
            },
        )


def root_context() -> AgentRunContext:
    return AgentRunContext(
        session_id="chat-1",
        run_id="run-1",
        root_run_id="run-1",
        user_id="user-1",
        agent_id="agent-1",
        provider="openai",
        model="gpt-5.4-mini",
        instructions="Help the user.",
        communication_medium=CommunicationMedium.WEB_CHAT,
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
