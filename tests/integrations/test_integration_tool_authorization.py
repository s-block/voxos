import pytest

from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolReference,
    ToolRisk,
)
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.models import (
    IntegrationAuthType,
    IntegrationProviderType,
    IntegrationStatus,
)
from voxos.integrations.records import UserIntegrationRecord


@pytest.mark.asyncio
async def test_integration_grant_requires_matching_integration_id(
    sqlite_session_factory,
) -> None:
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration = UserIntegrationRecord(
            user_id="user-1",
            provider=IntegrationProviderType.TWILIO.value,
            label="Twilio",
            status=IntegrationStatus.CONNECTED.value,
            enabled=True,
            auth_type=IntegrationAuthType.API_KEY.value,
            setup_state="connected",
        )
        session.add(integration)
        await session.commit()
        await session.refresh(integration)

    service = SqlModelToolAuthorizationService(
        session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
    )
    await service.set_grants(
        user_id="user-1",
        agent_id="agent-1",
        grants=(
            ToolGrantUpdate(
                tool_name="twilio_abc_send_sms",
                tool_source=ToolGrantScope.INTEGRATION,
                integration_id=integration.id,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.HIGH,
            ),
        ),
    )

    allowed = await service.authorize_tool(
        scope=agent_scope(),
        reference=ToolReference(
            tool_name="twilio_abc_send_sms",
            tool_source=ToolGrantScope.INTEGRATION,
            integration_id=integration.id,
            risk=ToolRisk.HIGH,
        ),
    )
    denied = await service.authorize_tool(
        scope=agent_scope(),
        reference=ToolReference(
            tool_name="twilio_abc_send_sms",
            tool_source=ToolGrantScope.INTEGRATION,
            integration_id="other-integration",
            risk=ToolRisk.HIGH,
        ),
    )

    assert allowed.allowed is True
    assert denied.allowed is False


def agent_scope() -> AgentScope:
    return AgentScope(
        owner_user_id="user-1",
        agent_id="agent-1",
        channel="text",
        source="test",
    )
