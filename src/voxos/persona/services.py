from dataclasses import dataclass
from typing import Mapping

from voxos.persona.models import (
    AgentPersona,
    AgentSoul,
    FormalityLevel,
    HumourStyle,
    PersonaTone,
    VerbosityLevel,
    WarmthLevel,
    utc_now,
)
from voxos.persona.rules import PersonaRule
from voxos.persona.storage import PersonaStore
from voxos.persona.updates import (
    PersonaField,
    PersonaFieldChange,
    PersonaFieldValue,
    PersonaRuleChange,
    PersonaRuleUpdateAction,
    PersonaUpdateRequest,
    PersonaUpdateResult,
    PersonaUpdateSource,
    PersonaUpdateStatus,
)

PROTECTED_PERSONA_FIELDS = frozenset(
    (
        PersonaField.NAME,
        PersonaField.ROLE,
        PersonaField.BEHAVIOURAL_BOUNDARIES,
    ),
)
SENSITIVE_AGENT_UPDATED_FIELDS = frozenset((PersonaField.USER_PREFERENCES,))
AGENTIC_UPDATE_SOURCES = frozenset(
    (
        PersonaUpdateSource.AGENT,
        PersonaUpdateSource.TOOL,
    ),
)


@dataclass(frozen=True, slots=True)
class _UpdateValidation:
    pending_changes: tuple[str, ...]
    blocked_changes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AppliedUpdate:
    soul: AgentSoul
    applied_changes: tuple[str, ...]


class AgentPersonaService:
    def __init__(
        self,
        store: PersonaStore | None = None,
        default_persona: AgentPersona | None = None,
    ) -> None:
        self._store = store
        self._default_persona = default_persona or AgentPersona()

    async def load_soul(self, user_id: str) -> AgentSoul:
        if self._store is not None:
            stored_soul = await self._store.get_soul(user_id)
            if stored_soul is not None:
                return stored_soul

        return AgentSoul(user_id=user_id, persona=self._default_persona)

    async def update_soul(
        self,
        request: PersonaUpdateRequest,
    ) -> PersonaUpdateResult:
        soul = await self.load_soul(request.user_id)
        validation = self._validate_update_request(request=request, soul=soul)
        if validation.blocked_changes:
            return PersonaUpdateResult(
                status=PersonaUpdateStatus.REJECTED,
                soul=soul,
                blocked_changes=validation.blocked_changes,
            )
        if validation.pending_changes and not request.confirmed:
            return PersonaUpdateResult(
                status=PersonaUpdateStatus.NEEDS_CONFIRMATION,
                soul=soul,
                pending_changes=validation.pending_changes,
            )

        try:
            applied_update = self._apply_update(request=request, soul=soul)
        except ValueError as exc:
            return PersonaUpdateResult(
                status=PersonaUpdateStatus.REJECTED,
                soul=soul,
                blocked_changes=(str(exc),),
            )

        if self._store is not None:
            await self._store.save_soul(applied_update.soul)

        return PersonaUpdateResult(
            status=PersonaUpdateStatus.APPLIED,
            soul=applied_update.soul,
            applied_changes=applied_update.applied_changes,
        )

    def _validate_update_request(
        self,
        request: PersonaUpdateRequest,
        soul: AgentSoul,
    ) -> _UpdateValidation:
        pending_changes = [
            self._field_change_label(change)
            for change in request.field_changes
            if self._field_change_requires_confirmation(
                change=change,
                source=request.source,
            )
        ]
        blocked_changes = list(self._validate_rule_change_targets(request, soul))
        pending_changes.extend(
            self._rule_change_label(change)
            for change in request.rule_changes
            if self._rule_change_requires_confirmation(change=change, soul=soul)
        )
        return _UpdateValidation(
            pending_changes=tuple(pending_changes),
            blocked_changes=tuple(blocked_changes),
        )

    def _validate_rule_change_targets(
        self,
        request: PersonaUpdateRequest,
        soul: AgentSoul,
    ) -> tuple[str, ...]:
        rules_by_id = self._rules_by_id(soul.rules)
        blocked_changes: list[str] = []
        for change in request.rule_changes:
            if (
                change.action == PersonaRuleUpdateAction.ADD
                and change.rule is not None
                and change.rule.id in rules_by_id
            ):
                blocked_changes.append(f"rule:{change.rule.id}:already_exists")
            if change.action in {
                PersonaRuleUpdateAction.REPLACE,
                PersonaRuleUpdateAction.ENABLE,
                PersonaRuleUpdateAction.DISABLE,
                PersonaRuleUpdateAction.REMOVE,
            }:
                rule_id = self._rule_change_id(change)
                if rule_id not in rules_by_id:
                    blocked_changes.append(f"rule:{rule_id}:not_found")
        return tuple(blocked_changes)

    def _field_change_requires_confirmation(
        self,
        change: PersonaFieldChange,
        source: PersonaUpdateSource,
    ) -> bool:
        if change.field in PROTECTED_PERSONA_FIELDS:
            return True
        return change.field in SENSITIVE_AGENT_UPDATED_FIELDS and source in AGENTIC_UPDATE_SOURCES

    def _rule_change_requires_confirmation(
        self,
        change: PersonaRuleChange,
        soul: AgentSoul,
    ) -> bool:
        existing_rule = self._rules_by_id(soul.rules).get(self._rule_change_id(change))
        if existing_rule is not None and existing_rule.is_protected:
            return True
        return change.rule is not None and change.rule.is_protected

    def _apply_update(
        self,
        request: PersonaUpdateRequest,
        soul: AgentSoul,
    ) -> _AppliedUpdate:
        persona = soul.persona
        applied_changes: list[str] = []
        for change in request.field_changes:
            persona = self._apply_field_change(persona=persona, change=change)
            applied_changes.append(self._field_change_label(change))

        rules = soul.rules
        for rule_change in request.rule_changes:
            rules = self._apply_rule_change(rules=rules, change=rule_change)
            applied_changes.append(self._rule_change_label(rule_change))

        updated_soul = soul.model_copy(
            update={
                "persona": persona,
                "rules": rules,
                "version": soul.version + 1,
                "updated_at": utc_now(),
            },
        )
        return _AppliedUpdate(
            soul=updated_soul,
            applied_changes=tuple(applied_changes),
        )

    def _apply_field_change(
        self,
        persona: AgentPersona,
        change: PersonaFieldChange,
    ) -> AgentPersona:
        value = change.value
        if change.field == PersonaField.NAME:
            return self._copy_persona(
                persona=persona,
                update={"name": self._string_value(value)},
            )
        if change.field == PersonaField.ROLE:
            return self._copy_persona(
                persona=persona,
                update={"role": self._string_value(value)},
            )
        if change.field == PersonaField.TONE:
            return self._copy_persona(
                persona=persona,
                update={"tone": PersonaTone(self._string_value(value))},
            )
        if change.field == PersonaField.HUMOUR_STYLE:
            return self._copy_persona(
                persona=persona,
                update={"humour_style": HumourStyle(self._string_value(value))},
            )
        if change.field == PersonaField.FORMALITY:
            return self._copy_persona(
                persona=persona,
                update={"formality": FormalityLevel(self._string_value(value))},
            )
        if change.field == PersonaField.WARMTH:
            return self._copy_persona(
                persona=persona,
                update={"warmth": WarmthLevel(self._string_value(value))},
            )
        if change.field == PersonaField.VERBOSITY:
            return self._copy_persona(
                persona=persona,
                update={"verbosity": VerbosityLevel(self._string_value(value))},
            )
        if change.field == PersonaField.SPEAKING_STYLE:
            return self._copy_persona(
                persona=persona,
                update={"speaking_style": self._tuple_value(value)},
            )
        if change.field == PersonaField.BEHAVIOURAL_BOUNDARIES:
            return self._copy_persona(
                persona=persona,
                update={"behavioural_boundaries": self._tuple_value(value)},
            )
        if change.field == PersonaField.USER_PREFERENCES:
            return self._copy_persona(
                persona=persona,
                update={"user_preferences": self._tuple_value(value)},
            )
        if change.field.value == PersonaField.BACKSTORY.value:
            return self._copy_persona(
                persona=persona,
                update={"backstory": self._optional_string_value(value)},
            )
        message = f"unsupported persona field: {change.field.value}"
        raise ValueError(message)

    def _apply_rule_change(
        self,
        rules: tuple[PersonaRule, ...],
        change: PersonaRuleChange,
    ) -> tuple[PersonaRule, ...]:
        if change.action == PersonaRuleUpdateAction.ADD:
            if change.rule is None:
                message = "add rule change requires a rule"
                raise ValueError(message)
            return (*rules, change.rule)
        if change.action == PersonaRuleUpdateAction.REPLACE:
            if change.rule is None:
                message = "replace rule change requires a rule"
                raise ValueError(message)
            return tuple(change.rule if rule.id == change.rule.id else rule for rule in rules)
        if change.action == PersonaRuleUpdateAction.ENABLE:
            return self._set_rule_enabled(
                rules=rules,
                rule_id=self._rule_change_id(change),
                enabled=True,
            )
        if change.action == PersonaRuleUpdateAction.DISABLE:
            return self._set_rule_enabled(
                rules=rules,
                rule_id=self._rule_change_id(change),
                enabled=False,
            )
        if change.action.value == PersonaRuleUpdateAction.REMOVE.value:
            rule_id = self._rule_change_id(change)
            return tuple(rule for rule in rules if rule.id != rule_id)
        message = f"unsupported rule change action: {change.action.value}"
        raise ValueError(message)

    def _set_rule_enabled(
        self,
        rules: tuple[PersonaRule, ...],
        rule_id: str,
        enabled: bool,
    ) -> tuple[PersonaRule, ...]:
        return tuple(rule.model_copy(update={"enabled": enabled}) if rule.id == rule_id else rule for rule in rules)

    def _copy_persona(
        self,
        persona: AgentPersona,
        update: Mapping[str, object],
    ) -> AgentPersona:
        data = persona.model_dump()
        data.update(update)
        return AgentPersona.model_validate(data)

    def _string_value(self, value: PersonaFieldValue) -> str:
        if not isinstance(value, str):
            message = "persona field value must be a string"
            raise ValueError(message)
        stripped_value = value.strip()
        if not stripped_value:
            message = "persona field value must not be empty"
            raise ValueError(message)
        return stripped_value

    def _optional_string_value(self, value: PersonaFieldValue) -> str | None:
        if value is None:
            return None
        return self._string_value(value)

    def _tuple_value(self, value: PersonaFieldValue) -> tuple[str, ...]:
        if isinstance(value, str):
            values: tuple[str, ...] = (value,)
        elif isinstance(value, tuple):
            values = value
        else:
            message = "persona field value must be a string tuple"
            raise ValueError(message)

        stripped_values = tuple(item.strip() for item in values)
        if not stripped_values or any(not item for item in stripped_values):
            message = "persona field values must not be empty"
            raise ValueError(message)
        return stripped_values

    def _rules_by_id(
        self,
        rules: tuple[PersonaRule, ...],
    ) -> dict[str, PersonaRule]:
        return {rule.id: rule for rule in rules}

    def _rule_change_id(self, change: PersonaRuleChange) -> str:
        if change.rule is not None:
            return change.rule.id
        if change.rule_id is None:
            message = f"{change.action.value} rule changes require rule_id"
            raise ValueError(message)
        return change.rule_id

    def _field_change_label(self, change: PersonaFieldChange) -> str:
        return f"persona:{change.field.value}"

    def _rule_change_label(self, change: PersonaRuleChange) -> str:
        return f"rule:{change.action.value}:{self._rule_change_id(change)}"
