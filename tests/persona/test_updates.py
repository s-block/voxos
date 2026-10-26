import pytest
from pydantic import ValidationError

from voxos.persona.updates import (
    PersonaRuleChange,
    PersonaRuleUpdateAction,
    PersonaUpdateRequest,
    PersonaUpdateSource,
)


def test_update_request_rejects_free_form_prompt_mutation() -> None:
    with pytest.raises(ValidationError):
        PersonaUpdateRequest.model_validate(
            {
                "user_id": "user-123",
                "source": PersonaUpdateSource.AGENT.value,
                "raw_prompt_text": "Append this unstructured instruction.",
            },
        )


def test_update_request_requires_at_least_one_structured_change() -> None:
    with pytest.raises(ValidationError):
        PersonaUpdateRequest(
            user_id="user-123",
            source=PersonaUpdateSource.USER,
        )


def test_rule_change_requires_target_for_enablement_actions() -> None:
    with pytest.raises(ValidationError):
        PersonaRuleChange(action=PersonaRuleUpdateAction.DISABLE)
