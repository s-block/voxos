from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from livekit.agents import RunContext, function_tool
from livekit.agents.llm import ChatMessage, FunctionTool
from livekit.agents.llm.utils import build_strict_openai_schema

from voxos.agent_core.models import AgentTool
from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicySnapshot
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationService,
    ToolGrantScope,
    ToolReference,
    ToolRisk,
)
from voxos.browser_accounts.errors import BrowserAccountAuthorizationError
from voxos.browser_accounts.models import BrowserAccountProvider
from voxos.browser_accounts.services import AvailableBrowserAccount, BrowserAccountService
from voxos.browser_accounts.tool_capabilities import (
    browser_profile_catalog,
    browser_profile_handle,
)
from voxos.browser_runtime.models import BrowserTaskArguments, browser_url_origin
from voxos.browser_runtime.service import BrowserRuntimeService
from voxos.voice.tools import (
    VoiceCatalogTool,
    VoiceLiveKitTool,
)

OWNER_BROWSER_ACCOUNT_VOICE_SOURCE = "voice.worker.owner_browser_accounts"
VOICE_BROWSER_REQUEST_MISSING = "No finalized user voice request is available"
VOICE_BROWSER_TASK_FAILED = (
    "Browser task did not complete successfully. If tool_search is attached, use it "
    "to find a matching specialist before trying another generic browser or web search."
)
BROWSER_VOICE_TOOL_NAME = "use_browser"


@dataclass(frozen=True, slots=True)
class VoiceBrowserRequest:
    client_turn_id: str
    task: str


@dataclass(frozen=True, slots=True)
class AuthenticatedBrowserVoiceToolProvider:
    profile_service: BrowserAccountService
    runtime_service: BrowserRuntimeService
    authorization_service: ToolAuthorizationService

    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        del user_id
        return ()

    async def get_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceLiveKitTool]:
        tool = await self._tool_for_scope(
            scope=scope,
            effective_tool_policy=effective_tool_policy,
        )
        return () if tool is None else (tool,)

    async def get_catalog_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceCatalogTool]:
        tool = await self._tool_for_scope(
            scope=scope,
            effective_tool_policy=effective_tool_policy,
        )
        if tool is None:
            return ()
        schema = build_strict_openai_schema(tool)["function"]["parameters"]
        description = tool.info.description or "Use a general browser."
        return (
            VoiceCatalogTool(
                definition=AgentTool(
                    name=BROWSER_VOICE_TOOL_NAME,
                    description=description[:1024],
                    parameters_schema=cast(JsonObject, schema),
                    source=ToolGrantScope.INTERNAL,
                    risk=ToolRisk.HIGH,
                    catalog_label="Browser",
                    capability_key="browser",
                ),
                function_tool=tool,
            ),
        )

    async def _tool_for_scope(
        self,
        *,
        scope: AgentScope,
        effective_tool_policy: EffectiveToolPolicySnapshot | None,
    ) -> FunctionTool[..., Awaitable[str]] | None:
        if (
            scope.source != OWNER_BROWSER_ACCOUNT_VOICE_SOURCE
            or scope.session_id is None
            or effective_tool_policy is None
        ):
            return None
        accounts = await self.profile_service.list_assigned_ready_accounts(
            user_id=scope.owner_user_id,
            agent_id=scope.agent_id,
        )
        allowed_accounts = tuple(
            account
            for account in accounts
            if account.provider == BrowserAccountProvider.STAGEHAND_LOCAL
            or effective_tool_policy.allows(_tool_reference(account))
        )
        return self._livekit_tool(scope=scope, accounts=allowed_accounts)

    def _livekit_tool(
        self,
        *,
        scope: AgentScope,
        accounts: tuple[AvailableBrowserAccount, ...],
    ) -> FunctionTool[..., Awaitable[str]]:
        async def use_browser(
            context: RunContext[object],
            instruction: str,
            start_url: str | None = None,
            profile: str | None = None,
            action_mode: str = "read_only",
        ) -> str:
            current_request = latest_voice_browser_request(
                context.session.history.messages(),
            )
            arguments = BrowserTaskArguments.model_validate(
                {
                    "instruction": instruction,
                    "start_url": start_url,
                    "profile": profile,
                    "action_mode": action_mode,
                },
            )
            if arguments.profile is None:
                if arguments.start_url is None:
                    raise BrowserAccountAuthorizationError
                result = await self.runtime_service.execute_task(
                    user_id=scope.owner_user_id,
                    instruction=arguments.instruction,
                    start_url=arguments.start_url,
                    allowed_origins=(browser_url_origin(arguments.start_url),),
                    action_mode=arguments.action_mode,
                )
            else:
                account = next(
                    (
                        candidate
                        for candidate in accounts
                        if browser_profile_handle(candidate.account_id) == arguments.profile
                    ),
                    None,
                )
                if account is None:
                    raise BrowserAccountAuthorizationError
                if account.provider != BrowserAccountProvider.STAGEHAND_LOCAL:
                    decision = await self.authorization_service.authorize_tool(
                        scope=scope,
                        reference=_tool_reference(account),
                    )
                    if not decision.allowed:
                        raise PermissionError(decision.reason)
                if scope.session_id is None:
                    raise BrowserAccountAuthorizationError
                result = await self.profile_service.execute_assigned_task(
                    user_id=scope.owner_user_id,
                    agent_id=scope.agent_id,
                    account_id=account.account_id,
                    session_id=scope.session_id,
                    client_turn_id=current_request.client_turn_id,
                    instruction=arguments.instruction,
                    start_url=arguments.start_url,
                    action_mode=arguments.action_mode,
                )
            if not result.successful:
                raise RuntimeError(VOICE_BROWSER_TASK_FAILED)
            return result.output_text

        catalog = browser_profile_catalog(accounts)
        description = (
            "Fallback for a general public or signed-in browser task from a complete "
            "instruction. Prefer a directly attached specialist, and when tool_search is "
            "attached use it before this browser for a requested service or specialist "
            "capability. "
            "Supply a public HTTPS start_url for public browsing, or select one of the "
            "saved profile handles below. Use state_changes_allowed only for an action "
            "the user explicitly requested."
        )
        if catalog:
            description = f"{description}\nAssigned saved profiles:\n{catalog}"
        return cast(
            FunctionTool[..., Awaitable[str]],
            function_tool(
                use_browser,
                name=BROWSER_VOICE_TOOL_NAME,
                description=description,
            ),
        )


def latest_voice_browser_request(
    messages: Sequence[ChatMessage],
) -> VoiceBrowserRequest:
    for message in reversed(messages):
        if message.role != "user":
            continue
        task = (message.text_content or "").strip()
        if not task:
            continue
        return VoiceBrowserRequest(
            client_turn_id=_voice_client_turn_id(message.id),
            task=task,
        )
    raise BrowserAccountAuthorizationError(VOICE_BROWSER_REQUEST_MISSING)


def _voice_client_turn_id(message_id: str) -> str:
    if 0 < len(message_id) <= 64:
        return message_id
    return sha256(message_id.encode("utf-8")).hexdigest()


def _tool_reference(account: AvailableBrowserAccount) -> ToolReference:
    is_local = account.provider == BrowserAccountProvider.STAGEHAND_LOCAL
    return ToolReference(
        tool_name=account.execution_tool_name,
        tool_source=(ToolGrantScope.INTERNAL if is_local else ToolGrantScope.MCP_SERVER),
        mcp_server_id=None if is_local else account.provider_connection_id,
        risk=ToolRisk.HIGH,
    )
