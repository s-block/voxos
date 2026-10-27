from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_core.models import (
    AgentRunContext,
    AgentTool,
    AgentToolResult,
)
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.tool_execution import (
    ToolExecutionContext,
    ToolExecutionSecurityError,
    validate_llm_tool_arguments,
)
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationService,
    ToolGrantScope,
    ToolReference,
    ToolRisk,
)
from voxos.contacts.services import UserContactService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    CommunicationOutboundCallRequest,
    CommunicationOutboundSmsRequest,
    IntegrationActionResponse,
    IntegrationCapability,
    IntegrationDirection,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationStatus,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.telnyx.provider import TelnyxIntegrationProvider
from voxos.integrations.providers.twilio.provider import TwilioIntegrationProvider
from voxos.integrations.records import (
    IntegrationMessageRecord,
    IntegrationResourceRecord,
    UserIntegrationRecord,
)
from voxos.integrations.services import (
    UserIntegrationService,
    VoiceAgentDispatcher,
    VoiceAgentReadinessWaiter,
    VoiceCallPreflight,
    VoiceWorkerDemandCanceller,
    VoiceWorkerDemandRequester,
    VoiceWorkerReadinessWaiter,
)
from voxos.integrations.tool_names import (
    TWILIO_PLACE_CALL_TOOL_NAME,
    TWILIO_SEND_SMS_TOOL_NAME,
    WHATSAPP_SEND_MESSAGE_TOOL_NAME,
)
from voxos.integrations.tool_names import (
    provider_integration_tool_name as integration_provider_tool_name,
)
from voxos.whatsapp.outbound import (
    QueuedWhatsAppMessage,
    WhatsAppOutboundMessageService,
)

INBOUND_SMS_REPLY_CONTEXT_INVALID_ERROR = "Inbound SMS reply context is invalid"
INBOUND_SMS_REPLY_CONTEXT_MISMATCH_ERROR = "Inbound SMS reply context does not match the integration tool"
INBOUND_SMS_REPLY_MESSAGE_NOT_FOUND_ERROR = "Inbound SMS reply message was not found"
SMS_DESTINATION_REQUIRED_ERROR = "to_number is required outside an inbound SMS reply run"


class SendSmsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    to_number: str | None = Field(default=None, min_length=1, max_length=32)
    body: str = Field(min_length=1, max_length=1600)


class PlaceCallArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contact_id: str = Field(min_length=1, max_length=36)
    objective: str = Field(min_length=1, max_length=4000)

    @field_validator("objective", mode="before")
    @classmethod
    def strip_objective(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SendWhatsAppMessageArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contact_id: str = Field(min_length=1, max_length=36)
    body: str = Field(min_length=1, max_length=16_384)


@dataclass(frozen=True, slots=True)
class UserIntegrationToolsetConfig:
    integration_id: str
    resource_id: str
    provider: IntegrationProviderType
    display_name: str
    capabilities: tuple[IntegrationCapability, ...]

    def supports(self, action: str) -> bool:
        if action == TWILIO_SEND_SMS_TOOL_NAME:
            return IntegrationCapability.SMS_SEND in self.capabilities
        if action == TWILIO_PLACE_CALL_TOOL_NAME:
            return IntegrationCapability.OUTBOUND_VOICE in self.capabilities
        if action == WHATSAPP_SEND_MESSAGE_TOOL_NAME:
            return (
                self.provider == IntegrationProviderType.WHATSAPP
                and IntegrationCapability.WHATSAPP_SEND in self.capabilities
            )
        return False


@dataclass(frozen=True, slots=True)
class ResolvedIntegrationTool:
    config: UserIntegrationToolsetConfig
    provider_tool_name: str
    action: str


@dataclass(frozen=True, slots=True)
class IntegrationAgentToolProvider:
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService
    public_base_url: str
    authorization_service: ToolAuthorizationService | None = None
    voice_call_preflight: VoiceCallPreflight | None = None
    voice_worker_demand_requester: VoiceWorkerDemandRequester | None = None
    voice_worker_readiness_waiter: VoiceWorkerReadinessWaiter | None = None
    voice_agent_dispatcher: VoiceAgentDispatcher | None = None
    voice_agent_readiness_waiter: VoiceAgentReadinessWaiter | None = None
    voice_worker_demand_canceller: VoiceWorkerDemandCanceller | None = None
    provider_registry: IntegrationProviderRegistry = field(
        default_factory=lambda: IntegrationProviderRegistry(
            telnyx=TelnyxIntegrationProvider(),
            twilio=TwilioIntegrationProvider(),
        ),
    )

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        configs = await self._toolset_configs(context.user_id)
        contact_search_allowed = await self._contact_search_allowed(context)
        tools: list[AgentTool] = []
        for config in configs:
            candidate_names = tuple(
                provider_integration_tool_name(config=config, action=action)
                for action in supported_actions_for_config(config)
            )
            if self.authorization_service is not None:
                allowed_names = await self.authorization_service.allowed_tool_names(
                    scope=self._execution_context(
                        context=context,
                        source="agent_core.integration_tools.list",
                        require_run=False,
                    ).to_agent_scope(),
                    tool_source=ToolGrantScope.INTEGRATION,
                    tool_names=candidate_names,
                    integration_id=config.integration_id,
                )
            else:
                allowed_names = candidate_names
            for tool_name in allowed_names:
                action = integration_action_from_provider_name(
                    config=config,
                    provider_tool_name=tool_name,
                )
                if action_requires_contact_search(action) and not contact_search_allowed:
                    continue
                tools.append(agent_tool_from_integration_config(config=config, action=action))
        return tuple(tools)

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        execution_context = self._execution_context(
            context=context,
            source="agent_core.integration_tools.execute",
            require_run=True,
        )
        descriptor = await self._resolve_tool_descriptor(
            user_id=execution_context.user_id,
            provider_tool_name=tool_name,
        )
        try:
            validate_llm_tool_arguments(arguments_json)
        except ToolExecutionSecurityError as exc:
            await self._audit_argument_denial(
                execution_context=execution_context,
                descriptor=descriptor,
                reason=str(exc),
            )
            raise
        await self._authorize_tool(
            execution_context=execution_context,
            descriptor=descriptor,
        )
        async with self.session_factory() as session:
            result: IntegrationActionResponse | QueuedWhatsAppMessage
            if descriptor.action == TWILIO_SEND_SMS_TOOL_NAME:
                sms_args = SendSmsArguments.model_validate(arguments_json)
                to_number = await self._resolved_sms_destination(
                    context=context,
                    descriptor=descriptor,
                    proposed_to_number=sms_args.to_number,
                )
                result = await self._integration_service(session).send_sms(
                    user_id=execution_context.user_id,
                    provider=descriptor.config.provider,
                    request=CommunicationOutboundSmsRequest(
                        integration_id=descriptor.config.integration_id,
                        resource_id=descriptor.config.resource_id,
                        agent_id=execution_context.agent_id,
                        to_number=to_number,
                        body=sms_args.body,
                    ),
                )
            elif descriptor.action == WHATSAPP_SEND_MESSAGE_TOOL_NAME:
                await self._authorize_contact_search_prerequisite(
                    execution_context=execution_context,
                )
                whatsapp_args = SendWhatsAppMessageArguments.model_validate(arguments_json)
                contact = await UserContactService(session).get_contact(
                    user_id=execution_context.user_id,
                    contact_id=whatsapp_args.contact_id,
                )
                if contact is None:
                    message = "Contact was not found"
                    raise ValueError(message)
                result = await WhatsAppOutboundMessageService(session).enqueue_message(
                    user_id=execution_context.user_id,
                    integration_id=descriptor.config.integration_id,
                    resource_id=descriptor.config.resource_id,
                    to_number=contact.phone_number,
                    body=whatsapp_args.body,
                )
            else:
                await self._authorize_contact_search_prerequisite(
                    execution_context=execution_context,
                )
                call_args = PlaceCallArguments.model_validate(arguments_json)
                contact = await UserContactService(session).get_contact(
                    user_id=execution_context.user_id,
                    contact_id=call_args.contact_id,
                )
                if contact is None:
                    message = "Contact was not found"
                    raise ValueError(message)
                result = await self._integration_service(session).start_outbound_call(
                    user_id=execution_context.user_id,
                    provider=descriptor.config.provider,
                    request=CommunicationOutboundCallRequest(
                        integration_id=descriptor.config.integration_id,
                        resource_id=descriptor.config.resource_id,
                        agent_id=execution_context.agent_id,
                        to_number=contact.phone_number,
                        objective=call_args.objective,
                        initiating_run_id=execution_context.run_id,
                    ),
                )
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=(
                f"{descriptor.action} accepted by {descriptor.config.provider.value} with status {result.status}."
            ),
            is_error=False,
            raw_output=result.model_dump(mode="json"),
        )

    def _integration_service(self, session: AsyncSession) -> UserIntegrationService:
        return UserIntegrationService(
            session=session,
            secret_vault=self.secret_vault,
            public_base_url=self.public_base_url,
            provider_registry=self.provider_registry,
            session_factory=self.session_factory,
            authorization_service=None,
            voice_call_preflight=self.voice_call_preflight,
            voice_worker_demand_requester=self.voice_worker_demand_requester,
            voice_worker_readiness_waiter=self.voice_worker_readiness_waiter,
            voice_agent_dispatcher=self.voice_agent_dispatcher,
            voice_agent_readiness_waiter=self.voice_agent_readiness_waiter,
            voice_worker_demand_canceller=self.voice_worker_demand_canceller,
        )

    async def _resolved_sms_destination(
        self,
        *,
        context: AgentRunContext,
        descriptor: ResolvedIntegrationTool,
        proposed_to_number: str | None,
    ) -> str:
        if context.run_id is None:
            return required_sms_destination(proposed_to_number)
        async with self.session_factory() as session:
            run = await session.get(AgentRunRecord, context.run_id)
            if run is None or run.user_id != context.user_id or run.agent_id != context.agent_id:
                return required_sms_destination(proposed_to_number)
            inbound_context = run.payload_json.get("integration")
            if inbound_context is None:
                return required_sms_destination(proposed_to_number)
            if not isinstance(inbound_context, dict):
                raise PermissionError(INBOUND_SMS_REPLY_CONTEXT_INVALID_ERROR)
            provider = inbound_context.get("provider")
            integration_id = inbound_context.get("integration_id")
            resource_id = inbound_context.get("resource_id")
            external_message_id = inbound_context.get("external_message_id")
            if (
                provider != descriptor.config.provider.value
                or integration_id != descriptor.config.integration_id
                or resource_id != descriptor.config.resource_id
                or not isinstance(external_message_id, str)
            ):
                raise PermissionError(INBOUND_SMS_REPLY_CONTEXT_MISMATCH_ERROR)
            statement = select(IntegrationMessageRecord).where(
                col(IntegrationMessageRecord.user_id) == context.user_id,
                col(IntegrationMessageRecord.integration_id) == descriptor.config.integration_id,
                col(IntegrationMessageRecord.resource_id) == descriptor.config.resource_id,
                col(IntegrationMessageRecord.provider) == descriptor.config.provider.value,
                col(IntegrationMessageRecord.direction) == IntegrationDirection.INBOUND.value,
                col(IntegrationMessageRecord.external_message_id) == external_message_id,
            )
            message = (await session.exec(statement)).one_or_none()
            if message is None:
                raise PermissionError(INBOUND_SMS_REPLY_MESSAGE_NOT_FOUND_ERROR)
            return message.from_number

    async def _toolset_configs(
        self,
        user_id: str,
    ) -> tuple[UserIntegrationToolsetConfig, ...]:
        registered_provider_values = (
            *(provider.value for provider in self.provider_registry.providers),
            IntegrationProviderType.WHATSAPP.value,
        )
        if not registered_provider_values:
            return ()
        statement = (
            select(UserIntegrationRecord, IntegrationResourceRecord)
            .join(
                IntegrationResourceRecord,
                col(IntegrationResourceRecord.integration_id) == UserIntegrationRecord.id,
            )
            .where(
                col(UserIntegrationRecord.user_id) == user_id,
                col(UserIntegrationRecord.provider).in_(registered_provider_values),
                col(UserIntegrationRecord.status) == IntegrationStatus.CONNECTED.value,
                col(UserIntegrationRecord.enabled).is_(True),
                col(UserIntegrationRecord.deleted_at).is_(None),
                col(IntegrationResourceRecord.user_id) == user_id,
                col(IntegrationResourceRecord.resource_type) == IntegrationResourceType.PHONE_NUMBER.value,
                col(IntegrationResourceRecord.status) == IntegrationResourceStatus.CONNECTED.value,
                col(IntegrationResourceRecord.deleted_at).is_(None),
            )
            .order_by(col(IntegrationResourceRecord.created_at).asc())
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            return tuple(
                UserIntegrationToolsetConfig(
                    integration_id=integration.id,
                    resource_id=resource.id,
                    provider=IntegrationProviderType(integration.provider),
                    display_name=resource.display_name,
                    capabilities=tuple(IntegrationCapability(capability) for capability in resource.capabilities),
                )
                for integration, resource in result.all()
            )

    async def _resolve_tool_descriptor(
        self,
        *,
        user_id: str,
        provider_tool_name: str,
    ) -> ResolvedIntegrationTool:
        for config in await self._toolset_configs(user_id):
            for action in supported_actions_for_config(config):
                if provider_integration_tool_name(config=config, action=action) == provider_tool_name:
                    return ResolvedIntegrationTool(
                        config=config,
                        provider_tool_name=provider_tool_name,
                        action=action,
                    )
        message = f"Integration tool is not enabled for this user: {provider_tool_name}"
        raise PermissionError(message)

    async def _authorize_tool(
        self,
        *,
        execution_context: ToolExecutionContext,
        descriptor: ResolvedIntegrationTool,
    ) -> None:
        if self.authorization_service is None:
            return
        decision = await self.authorization_service.authorize_tool(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=descriptor.provider_tool_name,
                tool_source=ToolGrantScope.INTEGRATION,
                integration_id=descriptor.config.integration_id,
                risk=ToolRisk.HIGH,
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)

    async def _contact_search_allowed(self, context: AgentRunContext) -> bool:
        if self.authorization_service is None:
            return True
        allowed_names = await self.authorization_service.allowed_tool_names(
            scope=self._execution_context(
                context=context,
                source="agent_core.integration_tools.contact_prerequisite",
                require_run=False,
            ).to_agent_scope(),
            tool_source=ToolGrantScope.INTERNAL,
            tool_names=("search_contacts",),
        )
        return "search_contacts" in allowed_names

    async def _authorize_contact_search_prerequisite(
        self,
        *,
        execution_context: ToolExecutionContext,
    ) -> None:
        if self.authorization_service is None:
            return
        allowed_names = await self.authorization_service.allowed_tool_names(
            scope=execution_context.to_agent_scope(),
            tool_source=ToolGrantScope.INTERNAL,
            tool_names=("search_contacts",),
        )
        if "search_contacts" not in allowed_names:
            message = (
                "Outbound calls and WhatsApp messages require the search_contacts prerequisite; "
                "contact search is not allowed for this agent."
            )
            raise PermissionError(message)

    async def _audit_argument_denial(
        self,
        *,
        execution_context: ToolExecutionContext,
        descriptor: ResolvedIntegrationTool,
        reason: str,
    ) -> None:
        if self.authorization_service is None:
            return
        await self.authorization_service.record_tool_audit_event(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=descriptor.provider_tool_name,
                tool_source=ToolGrantScope.INTEGRATION,
                integration_id=descriptor.config.integration_id,
                risk=ToolRisk.HIGH,
            ),
            allowed=False,
            reason=reason,
            metadata_json={"category": "invalid_arguments"},
        )

    def _execution_context(
        self,
        *,
        context: AgentRunContext,
        source: str,
        require_run: bool,
    ) -> ToolExecutionContext:
        return ToolExecutionContext.from_run_context(
            context,
            source=source,
            require_run=require_run,
        )


def agent_tool_from_integration_config(
    *,
    config: UserIntegrationToolsetConfig,
    action: str,
) -> AgentTool:
    return AgentTool(
        name=provider_integration_tool_name(config=config, action=action),
        description=integration_tool_description(config=config, action=action),
        parameters_schema=integration_tool_schema(action),
        source=ToolGrantScope.INTEGRATION,
        risk=ToolRisk.HIGH,
        integration_id=config.integration_id,
    )


def provider_integration_tool_name(
    *,
    config: UserIntegrationToolsetConfig,
    action: str,
) -> str:
    return integration_provider_tool_name(
        integration_id=config.integration_id,
        resource_id=config.resource_id,
        action=action,
    )


def integration_action_from_provider_name(
    *,
    config: UserIntegrationToolsetConfig,
    provider_tool_name: str,
) -> str:
    for action in supported_actions_for_config(config):
        if provider_integration_tool_name(config=config, action=action) == provider_tool_name:
            return action
    message = f"Unknown integration tool: {provider_tool_name}"
    raise PermissionError(message)


def integration_tool_description(
    *,
    config: UserIntegrationToolsetConfig,
    action: str,
) -> str:
    if action == TWILIO_SEND_SMS_TOOL_NAME:
        return f"Send an SMS from the connected {config.provider.value} number {config.display_name}."
    if action == WHATSAPP_SEND_MESSAGE_TOOL_NAME:
        return (
            "Send a WhatsApp message from the connected WhatsApp account. Search contacts first for every "
            "named person, use the sole match without asking whether contacts should be searched, and clarify "
            "only zero or multiple matches."
        )
    return (
        f"Place an outbound call from the connected {config.provider.value} number "
        f"{config.display_name}. Search contacts first for every named person, use the sole "
        "match without asking whether contacts should be searched, clarify only zero or "
        "multiple matches, and provide the complete caller-safe objective for the phone agent."
    )


def integration_tool_schema(action: str) -> JsonObject:
    if action == TWILIO_SEND_SMS_TOOL_NAME:
        return {
            "type": "object",
            "properties": {
                "to_number": {"type": "string", "description": "E.164 recipient number."},
                "body": {"type": "string", "description": "SMS body."},
            },
            "required": ["to_number", "body"],
            "additionalProperties": False,
        }
    if action == WHATSAPP_SEND_MESSAGE_TOOL_NAME:
        return {
            "type": "object",
            "properties": {
                "contact_id": {
                    "type": "string",
                    "description": "Saved contact ID returned by search_contacts.",
                },
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 16384,
                    "description": "WhatsApp message body.",
                },
            },
            "required": ["contact_id", "body"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "string",
                "description": "Saved contact ID returned by search_contacts.",
            },
            "objective": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4000,
                "description": (
                    "Complete caller-safe task the phone agent must pursue, including any "
                    "already-resolved facts it may disclose."
                ),
            },
        },
        "required": ["contact_id", "objective"],
        "additionalProperties": False,
    }


def supported_actions_for_config(config: UserIntegrationToolsetConfig) -> tuple[str, ...]:
    return tuple(
        action
        for action in (
            TWILIO_SEND_SMS_TOOL_NAME,
            TWILIO_PLACE_CALL_TOOL_NAME,
            WHATSAPP_SEND_MESSAGE_TOOL_NAME,
        )
        if config.supports(action)
    )


def action_requires_contact_search(action: str) -> bool:
    return action in {
        TWILIO_PLACE_CALL_TOOL_NAME,
        WHATSAPP_SEND_MESSAGE_TOOL_NAME,
    }


def required_sms_destination(value: str | None) -> str:
    if value is None:
        raise ValueError(SMS_DESTINATION_REQUIRED_ERROR)
    return value
