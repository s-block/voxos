from dataclasses import dataclass, field

import pytest

from voxos.persona.models import AgentSoul, VerbosityLevel
from voxos.persona.rules import (
    PersonaRule,
    PersonaRuleCategory,
    PersonaRuleEffect,
)
from voxos.persona.services import AgentPersonaService
from voxos.persona.updates import (
    PersonaField,
    PersonaFieldChange,
    PersonaRuleChange,
    PersonaRuleUpdateAction,
    PersonaUpdateRequest,
    PersonaUpdateSource,
    PersonaUpdateStatus,
)


@dataclass(slots=True)
class InMemoryPersonaStore:
    souls: dict[str, AgentSoul] = field(default_factory=dict)
    saved_souls: list[AgentSoul] = field(default_factory=list)

    async def get_soul(self, user_id: str) -> AgentSoul | None:
        return self.souls.get(user_id)

    async def save_soul(self, soul: AgentSoul) -> None:
        self.souls[soul.user_id] = soul
        self.saved_souls.append(soul)


@pytest.mark.asyncio
async def test_service_applies_low_risk_preference_update() -> None:
    store = InMemoryPersonaStore()
    service = AgentPersonaService(store=store)

    result = await service.update_soul(
        PersonaUpdateRequest(
            user_id="user-123",
            source=PersonaUpdateSource.USER,
            field_changes=(
                PersonaFieldChange(
                    field=PersonaField.VERBOSITY,
                    value=VerbosityLevel.DETAILED.value,
                ),
            ),
        ),
    )

    assert result.status == PersonaUpdateStatus.APPLIED
    assert result.soul.persona.verbosity == VerbosityLevel.DETAILED
    assert result.soul.version == 2
    assert store.saved_souls == [result.soul]


@pytest.mark.asyncio
async def test_service_requires_confirmation_for_core_identity_change() -> None:
    store = InMemoryPersonaStore()
    service = AgentPersonaService(store=store)

    result = await service.update_soul(
        PersonaUpdateRequest(
            user_id="user-123",
            source=PersonaUpdateSource.AGENT,
            field_changes=(PersonaFieldChange(field=PersonaField.NAME, value="New Identity"),),
        ),
    )

    assert result.status == PersonaUpdateStatus.NEEDS_CONFIRMATION
    assert result.pending_changes == ("persona:name",)
    assert store.saved_souls == []


@pytest.mark.asyncio
async def test_service_applies_confirmed_protected_change() -> None:
    store = InMemoryPersonaStore()
    service = AgentPersonaService(store=store)

    result = await service.update_soul(
        PersonaUpdateRequest(
            user_id="user-123",
            source=PersonaUpdateSource.USER,
            confirmed=True,
            field_changes=(PersonaFieldChange(field=PersonaField.NAME, value="Ada"),),
        ),
    )

    assert result.status == PersonaUpdateStatus.APPLIED
    assert result.soul.persona.name == "Ada"
    assert store.saved_souls == [result.soul]


@pytest.mark.asyncio
async def test_service_requires_confirmation_for_agent_sensitive_preferences() -> None:
    service = AgentPersonaService()

    result = await service.update_soul(
        PersonaUpdateRequest(
            user_id="user-123",
            source=PersonaUpdateSource.TOOL,
            field_changes=(
                PersonaFieldChange(
                    field=PersonaField.USER_PREFERENCES,
                    value=("Do not mention billing details.",),
                ),
            ),
        ),
    )

    assert result.status == PersonaUpdateStatus.NEEDS_CONFIRMATION
    assert result.pending_changes == ("persona:user_preferences",)


@pytest.mark.asyncio
async def test_service_requires_confirmation_to_disable_protected_rule() -> None:
    rule = PersonaRule(
        id="calendar-confirmation",
        category=PersonaRuleCategory.CALENDAR,
        effect=PersonaRuleEffect.REQUIRE_CONFIRMATION,
        subject="update_calendar_event",
        description="Never update calendar events without confirmation.",
        priority=95,
        requires_confirmation_to_change=True,
    )
    store = InMemoryPersonaStore(
        souls={"user-123": AgentSoul(user_id="user-123", rules=(rule,))},
    )
    service = AgentPersonaService(store=store)

    result = await service.update_soul(
        PersonaUpdateRequest(
            user_id="user-123",
            source=PersonaUpdateSource.TOOL,
            rule_changes=(
                PersonaRuleChange(
                    action=PersonaRuleUpdateAction.DISABLE,
                    rule_id="calendar-confirmation",
                ),
            ),
        ),
    )

    assert result.status == PersonaUpdateStatus.NEEDS_CONFIRMATION
    assert result.pending_changes == ("rule:disable:calendar-confirmation",)
    assert store.saved_souls == []


@pytest.mark.asyncio
async def test_service_rejects_missing_rule_target() -> None:
    service = AgentPersonaService()

    result = await service.update_soul(
        PersonaUpdateRequest(
            user_id="user-123",
            source=PersonaUpdateSource.USER,
            rule_changes=(
                PersonaRuleChange(
                    action=PersonaRuleUpdateAction.REMOVE,
                    rule_id="missing-rule",
                ),
            ),
        ),
    )

    assert result.status == PersonaUpdateStatus.REJECTED
    assert result.blocked_changes == ("rule:missing-rule:not_found",)
