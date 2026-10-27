import pytest
from sqlmodel import select

from voxos.agent_core.integration_tools import (
    IntegrationAgentToolProvider,
    UserIntegrationToolsetConfig,
    action_requires_contact_search,
    integration_tool_description,
    integration_tool_schema,
    provider_integration_tool_name,
    supported_actions_for_config,
)
from voxos.agent_core.models import AgentRunContext
from voxos.agent_runtime.tool_policy import ToolGrantScope
from voxos.contacts.models import ContactCreateRequest
from voxos.contacts.services import UserContactService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    CommunicationOutboundCallRequest,
    IntegrationActionResponse,
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationStatus,
)
from voxos.integrations.records import IntegrationResourceRecord, UserIntegrationRecord
from voxos.integrations.services import UserIntegrationService
from voxos.integrations.tool_names import (
    TWILIO_PLACE_CALL_TOOL_NAME,
    TWILIO_SEND_SMS_TOOL_NAME,
    WHATSAPP_SEND_MESSAGE_TOOL_NAME,
)
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import WhatsAppOutboxRecord, WhatsAppSessionRecord


def test_integration_tool_descriptions_include_full_phone_number() -> None:
    config = UserIntegrationToolsetConfig(
        integration_id="integration-123",
        resource_id="resource-123",
        provider=IntegrationProviderType.TWILIO,
        display_name="+15550001111",
        capabilities=(
            IntegrationCapability.OUTBOUND_VOICE,
            IntegrationCapability.SMS_SEND,
        ),
    )
    assert (
        integration_tool_description(
            config=config,
            action=TWILIO_SEND_SMS_TOOL_NAME,
        )
        == "Send an SMS from the connected twilio number +15550001111."
    )
    assert integration_tool_description(
        config=config,
        action=TWILIO_PLACE_CALL_TOOL_NAME,
    ) == (
        "Place an outbound call from the connected twilio number +15550001111. "
        "Search contacts first for every named person, use the sole match without "
        "asking whether contacts should be searched, clarify only zero or multiple "
        "matches, and provide the complete caller-safe objective for the phone agent."
    )


def test_place_call_schema_requires_contact_and_objective() -> None:
    schema = integration_tool_schema(TWILIO_PLACE_CALL_TOOL_NAME)

    assert schema["required"] == ["contact_id", "objective"]
    assert schema["properties"]["objective"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 4000,
        "description": (
            "Complete caller-safe task the phone agent must pursue, including any "
            "already-resolved facts it may disclose."
        ),
    }


def test_whatsapp_send_uses_a_contact_and_is_only_available_to_whatsapp() -> None:
    config = UserIntegrationToolsetConfig(
        integration_id="integration-123",
        resource_id="resource-123",
        provider=IntegrationProviderType.WHATSAPP,
        display_name="+447700900456",
        capabilities=(IntegrationCapability.WHATSAPP_SEND,),
    )

    assert supported_actions_for_config(config) == (WHATSAPP_SEND_MESSAGE_TOOL_NAME,)
    assert action_requires_contact_search(WHATSAPP_SEND_MESSAGE_TOOL_NAME) is True
    assert integration_tool_description(
        config=config,
        action=WHATSAPP_SEND_MESSAGE_TOOL_NAME,
    ).startswith("Send a WhatsApp message")
    assert integration_tool_schema(WHATSAPP_SEND_MESSAGE_TOOL_NAME) == {
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


@pytest.mark.asyncio
async def test_place_call_is_omitted_when_contact_search_is_denied(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = UserIntegrationToolsetConfig(
        integration_id="integration-123",
        resource_id="resource-123",
        provider=IntegrationProviderType.TWILIO,
        display_name="+15550001111",
        capabilities=(IntegrationCapability.OUTBOUND_VOICE,),
    )

    async def toolset_configs(
        self: IntegrationAgentToolProvider,
        user_id: str,
    ) -> tuple[UserIntegrationToolsetConfig, ...]:
        del self
        assert user_id == "user-123"
        return (config,)

    class ContactDeniedAuthorization:
        async def allowed_tool_names(
            self,
            *,
            scope: object,
            tool_source: ToolGrantScope,
            tool_names: tuple[str, ...],
            **_kwargs: object,
        ) -> tuple[str, ...]:
            del scope
            return tool_names if tool_source == ToolGrantScope.INTEGRATION else ()

    monkeypatch.setattr(
        IntegrationAgentToolProvider,
        "_toolset_configs",
        toolset_configs,
    )
    provider = IntegrationAgentToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.test",
        authorization_service=ContactDeniedAuthorization(),  # type: ignore[arg-type]
    )

    tools = await provider.list_tools(
        AgentRunContext(
            session_id="session-123",
            user_id="user-123",
            agent_id="agent-123",
            provider="openai",
            model="gpt-4.1-mini",
            instructions="System instructions.",
        ),
    )

    assert tools == ()


@pytest.mark.asyncio
async def test_agent_place_call_resolves_contact_inside_run_tenant(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with sqlite_session_factory() as session:
        owner_contact = await UserContactService(session).create_contact(
            user_id="user-123",
            request=ContactCreateRequest(
                display_name="Ada Lovelace",
                phone_number="+15551234567",
            ),
        )
        other_contact = await UserContactService(session).create_contact(
            user_id="other-user",
            request=ContactCreateRequest(
                display_name="Other User",
                phone_number="+15559876543",
            ),
        )

    config = UserIntegrationToolsetConfig(
        integration_id="integration-123",
        resource_id="resource-123",
        provider=IntegrationProviderType.TWILIO,
        display_name="+15550001111",
        capabilities=(IntegrationCapability.OUTBOUND_VOICE,),
    )

    async def toolset_configs(
        self: IntegrationAgentToolProvider,
        user_id: str,
    ) -> tuple[UserIntegrationToolsetConfig, ...]:
        _ = self
        assert user_id == "user-123"
        return (config,)

    outbound_requests: list[CommunicationOutboundCallRequest] = []

    async def start_outbound_call(
        self: UserIntegrationService,
        *,
        user_id: str,
        provider: IntegrationProviderType,
        request: CommunicationOutboundCallRequest,
    ) -> IntegrationActionResponse:
        _ = self
        assert user_id == "user-123"
        assert provider == IntegrationProviderType.TWILIO
        outbound_requests.append(request)
        return IntegrationActionResponse(
            id="action-123",
            provider=provider,
            action="place_call",
            status="accepted",
        )

    monkeypatch.setattr(
        IntegrationAgentToolProvider,
        "_toolset_configs",
        toolset_configs,
    )
    monkeypatch.setattr(
        UserIntegrationService,
        "start_outbound_call",
        start_outbound_call,
    )
    provider = IntegrationAgentToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.test",
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
    )
    tool_name = provider_integration_tool_name(
        config=config,
        action=TWILIO_PLACE_CALL_TOOL_NAME,
    )

    result = await provider.execute_tool(
        context=context,
        tool_name=tool_name,
        call_id="call-123",
        arguments_json={
            "contact_id": owner_contact.id,
            "objective": "Introduce yourself and ask whether Ada needs any help.",
        },
    )

    assert result.is_error is False
    assert len(outbound_requests) == 1
    assert outbound_requests[0].to_number == owner_contact.phone_number
    assert outbound_requests[0].agent_id == "agent-123"
    assert outbound_requests[0].initiating_run_id == "run-123"
    assert outbound_requests[0].objective == ("Introduce yourself and ask whether Ada needs any help.")

    with pytest.raises(ValueError, match="Contact was not found"):
        await provider.execute_tool(
            context=context,
            tool_name=tool_name,
            call_id="call-456",
            arguments_json={
                "contact_id": other_contact.id,
                "objective": "Introduce yourself.",
            },
        )
    assert len(outbound_requests) == 1


@pytest.mark.asyncio
async def test_agent_whatsapp_message_queues_for_a_saved_contact(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "user-123"
    integration_id = "whatsapp-integration-123"
    resource_id = "whatsapp-resource-123"
    now = utc_now()
    async with sqlite_session_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id=user_id,
            request=ContactCreateRequest(
                display_name="Josh",
                phone_number="+447700900123",
            ),
        )
        session.add(
            UserIntegrationRecord(
                id=integration_id,
                user_id=user_id,
                provider=IntegrationProviderType.WHATSAPP.value,
                label="WhatsApp",
                status=IntegrationStatus.CONNECTED.value,
                enabled=True,
                auth_type=IntegrationAuthType.LINKED_DEVICE.value,
                setup_state=WhatsAppSessionState.CONNECTED.value,
            ),
        )
        session.add(
            IntegrationResourceRecord(
                id=resource_id,
                user_id=user_id,
                integration_id=integration_id,
                provider=IntegrationProviderType.WHATSAPP.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id="source-resource-123",
                display_name="+447700900456",
                status=IntegrationResourceStatus.CONNECTED.value,
                capabilities=[IntegrationCapability.WHATSAPP_SEND.value],
            ),
        )
        session.add(
            WhatsAppSessionRecord(
                user_id=user_id,
                integration_id=integration_id,
                resource_id=resource_id,
                source_resource_id="source-resource-123",
                credential_id="credential-123",
                desired_state=WhatsAppDesiredState.CONNECTED.value,
                state=WhatsAppSessionState.CONNECTED.value,
                phone_number="+447700900456",
                validated_at=now,
                connected_at=now,
            ),
        )
        await session.commit()

    provider = IntegrationAgentToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.test",
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        user_id=user_id,
        agent_id="agent-123",
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
    )
    tool_name = provider_integration_tool_name(
        config=UserIntegrationToolsetConfig(
            integration_id=integration_id,
            resource_id=resource_id,
            provider=IntegrationProviderType.WHATSAPP,
            display_name="+447700900456",
            capabilities=(IntegrationCapability.WHATSAPP_SEND,),
        ),
        action=WHATSAPP_SEND_MESSAGE_TOOL_NAME,
    )

    assert [tool.name for tool in await provider.list_tools(context)] == [tool_name]
    result = await provider.execute_tool(
        context=context,
        tool_name=tool_name,
        call_id="call-123",
        arguments_json={
            "contact_id": contact.id,
            "body": "Tell Josh that Project Hail Mary has a great ending.",
        },
    )

    assert result.is_error is False
    assert result.raw_output["status"] == WhatsAppOutboxStatus.QUEUED.value
    async with sqlite_session_factory() as session:
        outbox = (await session.exec(select(WhatsAppOutboxRecord))).one()
    assert outbox.reply_id is None
    assert outbox.target_jid == "447700900123@s.whatsapp.net"
