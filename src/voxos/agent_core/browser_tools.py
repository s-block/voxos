from dataclasses import dataclass

from pydantic import ValidationError

from voxos.agent_core.models import AgentRunContext, AgentTool, AgentToolResult
from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.tool_execution import ToolExecutionContext
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
from voxos.browser_runtime.contracts import BrowserTaskResult
from voxos.browser_runtime.models import BrowserTaskArguments, browser_url_origin
from voxos.browser_runtime.service import BrowserRuntimeService

BROWSER_TOOL_NAME = "use_browser"
BROWSER_USE_EXECUTION_TOOL_NAME = "run_session"
BROWSER_TOOL_DESCRIPTION = (
    "Run a general browser task from a clear instruction. This tool can browse public "
    "websites or use one of the optional saved browser profiles listed below. Supply a "
    "public HTTPS start_url for public browsing. When using a saved profile, select its "
    "opaque profile handle; Voxos supplies its scoped secrets and allowed origins without "
    "revealing them. Use state_changes_allowed only when the user explicitly requested a "
    "state-changing action. Call this tool directly rather than delegating browser work."
)
SUBAGENT_AUTHORITY_DENIED = "Browser authority does not transfer to subagents"
SCHEDULED_BROWSER_TOOL_DENIED = "The scheduled browser tool is not granted to this agent"
BROWSER_PROFILE_DENIED = "The saved browser profile is not granted to this agent"
INVALID_BROWSER_ARGUMENTS = "The browser task arguments are invalid"


@dataclass(frozen=True, slots=True)
class BrowserAgentToolProvider:
    profile_service: BrowserAccountService
    runtime_service: BrowserRuntimeService
    authorization_service: ToolAuthorizationService

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        if not self._is_root_context(context):
            return ()
        execution_context = ToolExecutionContext.from_run_context(
            context,
            source="agent_core.browser_tools.list",
        )
        pending = await self.profile_service.pending_authorization(
            user_id=context.user_id,
            agent_id=context.agent_id,
            session_id=context.session_id,
            run_id=context.run_id,
        )
        if pending is not None:
            allowed = await self._pending_authority_allowed(
                pending_provider=pending.provider,
                provider_connection_id=pending.provider_connection_id,
                execution_tool_name=pending.execution_tool_name,
                execution_context=execution_context,
            )
            if not allowed:
                if pending.required:
                    raise BrowserAccountAuthorizationError(
                        SCHEDULED_BROWSER_TOOL_DENIED,
                    )
                return ()
            return (
                self._tool(
                    accounts=(),
                    required=pending.required,
                    source=_tool_authority(
                        provider=pending.provider,
                        provider_connection_id=pending.provider_connection_id,
                    ),
                    source_tool_name=pending.execution_tool_name,
                ),
            )

        accounts = await self._allowed_accounts(
            accounts=await self.profile_service.list_assigned_ready_accounts(
                user_id=context.user_id,
                agent_id=context.agent_id,
            ),
            execution_context=execution_context,
        )
        return (
            self._tool(
                accounts=accounts,
                required=False,
                source=(ToolGrantScope.INTERNAL, None),
                source_tool_name=None,
            ),
        )

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        if tool_name != BROWSER_TOOL_NAME:
            raise PermissionError(tool_name)
        if not self._is_root_context(context):
            raise BrowserAccountAuthorizationError(SUBAGENT_AUTHORITY_DENIED)
        try:
            arguments = BrowserTaskArguments.model_validate(arguments_json)
        except ValidationError as exc:
            raise BrowserAccountAuthorizationError(
                INVALID_BROWSER_ARGUMENTS,
            ) from exc
        execution_context = ToolExecutionContext.from_run_context(
            context,
            source="agent_core.browser_tools.execute",
            require_run=True,
        )
        if execution_context.run_id is None:
            raise BrowserAccountAuthorizationError

        pending = await self.profile_service.pending_authorization(
            user_id=execution_context.user_id,
            agent_id=execution_context.agent_id,
            session_id=context.session_id,
            run_id=execution_context.run_id,
        )
        if pending is not None:
            await self._authorize_provider(
                provider=pending.provider,
                provider_connection_id=pending.provider_connection_id,
                execution_tool_name=pending.execution_tool_name,
                execution_context=execution_context,
            )
            result = await self.profile_service.execute_authorized_task(
                user_id=execution_context.user_id,
                agent_id=execution_context.agent_id,
                run_id=execution_context.run_id,
                session_id=context.session_id,
                expected_authorization_id=pending.authorization_id,
            )
            return self._result(call_id=call_id, result=result)

        if arguments.profile is None:
            if arguments.start_url is None:
                raise BrowserAccountAuthorizationError(
                    INVALID_BROWSER_ARGUMENTS,
                )
            allowed_origins = arguments.allowed_origins or (browser_url_origin(arguments.start_url),)
            result = await self.runtime_service.execute_task(
                user_id=execution_context.user_id,
                instruction=arguments.instruction,
                start_url=arguments.start_url,
                allowed_origins=allowed_origins,
                action_mode=arguments.action_mode,
            )
            return self._result(call_id=call_id, result=result)

        accounts = await self._allowed_accounts(
            accounts=await self.profile_service.list_assigned_ready_accounts(
                user_id=execution_context.user_id,
                agent_id=execution_context.agent_id,
            ),
            execution_context=execution_context,
        )
        account = next(
            (candidate for candidate in accounts if browser_profile_handle(candidate.account_id) == arguments.profile),
            None,
        )
        if account is None:
            raise BrowserAccountAuthorizationError(BROWSER_PROFILE_DENIED)
        await self._authorize_account(
            account=account,
            execution_context=execution_context,
        )
        result = await self.profile_service.execute_assigned_run_task(
            user_id=execution_context.user_id,
            agent_id=execution_context.agent_id,
            account_id=account.account_id,
            session_id=context.session_id,
            run_id=execution_context.run_id,
            instruction=arguments.instruction,
            start_url=arguments.start_url,
            action_mode=arguments.action_mode,
        )
        return self._result(call_id=call_id, result=result)

    async def _allowed_accounts(
        self,
        *,
        accounts: tuple[AvailableBrowserAccount, ...],
        execution_context: ToolExecutionContext,
    ) -> tuple[AvailableBrowserAccount, ...]:
        allowed: list[AvailableBrowserAccount] = []
        for account in accounts:
            if account.provider == BrowserAccountProvider.STAGEHAND_LOCAL:
                allowed.append(account)
                continue
            source, server_id = _tool_authority(
                provider=account.provider,
                provider_connection_id=account.provider_connection_id,
            )
            names = await self.authorization_service.allowed_tool_names(
                scope=execution_context.to_agent_scope(),
                tool_source=source,
                tool_names=(account.execution_tool_name,),
                mcp_server_id=server_id,
            )
            if account.execution_tool_name in names:
                allowed.append(account)
        return tuple(allowed)

    async def _pending_authority_allowed(
        self,
        *,
        pending_provider: BrowserAccountProvider,
        provider_connection_id: str | None,
        execution_tool_name: str,
        execution_context: ToolExecutionContext,
    ) -> bool:
        if pending_provider == BrowserAccountProvider.STAGEHAND_LOCAL:
            return True
        source, server_id = _tool_authority(
            provider=pending_provider,
            provider_connection_id=provider_connection_id,
        )
        allowed = await self.authorization_service.allowed_tool_names(
            scope=execution_context.to_agent_scope(),
            tool_source=source,
            tool_names=(execution_tool_name,),
            mcp_server_id=server_id,
        )
        return execution_tool_name in allowed

    async def _authorize_account(
        self,
        *,
        account: AvailableBrowserAccount,
        execution_context: ToolExecutionContext,
    ) -> None:
        await self._authorize_provider(
            provider=account.provider,
            provider_connection_id=account.provider_connection_id,
            execution_tool_name=account.execution_tool_name,
            execution_context=execution_context,
        )

    async def _authorize_provider(
        self,
        *,
        provider: BrowserAccountProvider,
        provider_connection_id: str | None,
        execution_tool_name: str,
        execution_context: ToolExecutionContext,
    ) -> None:
        if provider == BrowserAccountProvider.STAGEHAND_LOCAL:
            return
        source, server_id = _tool_authority(
            provider=provider,
            provider_connection_id=provider_connection_id,
        )
        decision = await self.authorization_service.authorize_tool(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=execution_tool_name,
                tool_source=source,
                mcp_server_id=server_id,
                risk=ToolRisk.HIGH,
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)

    @staticmethod
    def _tool(
        *,
        accounts: tuple[AvailableBrowserAccount, ...],
        required: bool,
        source: tuple[ToolGrantScope, str | None],
        source_tool_name: str | None,
    ) -> AgentTool:
        profile_handles = [browser_profile_handle(account.account_id) for account in accounts]
        profile_schema: dict[str, object] = {
            "type": "string",
            "description": "Optional opaque handle for an assigned saved browser profile.",
        }
        if profile_handles:
            profile_schema["enum"] = profile_handles
        description = BROWSER_TOOL_DESCRIPTION
        catalog = browser_profile_catalog(accounts)
        if catalog:
            description = f"{description}\n\nAssigned saved profiles:\n{catalog}"
        return AgentTool(
            name=BROWSER_TOOL_NAME,
            description=description,
            parameters_schema={
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "The complete browser objective and relevant context.",
                        "minLength": 1,
                        "maxLength": 12000,
                    },
                    "start_url": {
                        "type": "string",
                        "format": "uri",
                        "description": (
                            "Public HTTPS page to open. Required without a saved profile; "
                            "optional for an approved page within a saved profile's origins."
                        ),
                    },
                    "allowed_origins": {
                        "type": "array",
                        "items": {"type": "string", "format": "uri"},
                        "maxItems": 20,
                        "description": (
                            "Optional exact HTTPS origins for a public task. Do not supply "
                            "this when using a saved profile."
                        ),
                    },
                    "profile": profile_schema,
                    "action_mode": {
                        "type": "string",
                        "enum": ["read_only", "state_changes_allowed"],
                        "default": "read_only",
                    },
                },
                "required": ["instruction"],
                "additionalProperties": False,
            },
            source=source[0],
            risk=ToolRisk.HIGH,
            required=required,
            parallel_safe=False,
            always_visible=True,
            mcp_server_id=source[1],
            source_tool_name=source_tool_name,
            catalog_label="Browser",
            capability_key="browser-use",
        )

    @staticmethod
    def _result(*, call_id: str, result: BrowserTaskResult) -> AgentToolResult:
        return AgentToolResult(
            call_id=call_id,
            name=BROWSER_TOOL_NAME,
            output_text=result.output_text,
            is_error=not result.successful,
            raw_output={"completed": result.successful},
        )

    @staticmethod
    def _is_root_context(context: AgentRunContext) -> bool:
        return context.communication_medium == CommunicationMedium.WEB_CHAT and context.parent_run_id is None


def _tool_authority(
    *,
    provider: BrowserAccountProvider,
    provider_connection_id: str | None,
) -> tuple[ToolGrantScope, str | None]:
    if provider == BrowserAccountProvider.STAGEHAND_LOCAL:
        return ToolGrantScope.INTERNAL, None
    return ToolGrantScope.MCP_SERVER, provider_connection_id
