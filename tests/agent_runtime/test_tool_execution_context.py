import pytest
from pydantic import ValidationError

from voxos.agent_core.models import AgentRunContext
from voxos.agent_runtime.tool_execution import (
    ToolArgumentAuthorityValidator,
    ToolAuthorityArgumentError,
    ToolExecutionContext,
    ToolExecutionSecurityError,
    is_authority_argument_key,
    validate_llm_tool_arguments,
)


def test_tool_execution_context_is_server_created_and_frozen() -> None:
    run_context = AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
    )

    execution_context = ToolExecutionContext.from_run_context(
        run_context,
        source="test",
        require_run=True,
    )

    assert execution_context.user_id == "user-123"
    assert execution_context.run_id == "run-123"
    with pytest.raises(ValidationError):
        execution_context.user_id = "other-user"


def test_tool_execution_context_requires_run_for_execution() -> None:
    run_context = AgentRunContext(
        session_id="session-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
    )

    with pytest.raises(ToolExecutionSecurityError):
        ToolExecutionContext.from_run_context(
            run_context,
            source="test",
            require_run=True,
        )


def test_tool_execution_context_accepts_server_selected_channel() -> None:
    run_context = AgentRunContext(
        session_id="session-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-realtime",
        instructions="System instructions.",
    )

    execution_context = ToolExecutionContext.from_run_context(
        run_context,
        source="voice.mcp_tools.execute",
        channel="voice",
    )

    assert execution_context.channel == "voice"
    assert execution_context.to_agent_scope().channel == "voice"


@pytest.mark.parametrize(
    "key",
    (
        "user_id",
        "userId",
        "owner-user-id",
        "mcp_server_id",
        "credentialId",
        "headers",
        "authorization",
        "apiKey",
        "evidence_session_id",
        "parentRunId",
    ),
)
def test_authority_argument_key_detection(key: str) -> None:
    assert is_authority_argument_key(key)


def test_tool_argument_validator_rejects_authority_keys_recursively() -> None:
    arguments = {
        "request": {
            "query": "build status",
            "filters": [
                {"kind": "open"},
                {"owner_user_id": "other-user"},
            ],
        },
    }

    with pytest.raises(ToolAuthorityArgumentError) as exc_info:
        validate_llm_tool_arguments(arguments)

    assert exc_info.value.path == ("request", "filters", "1", "owner_user_id")


def test_tool_argument_validator_allows_explicit_schema_exception() -> None:
    ToolArgumentAuthorityValidator(
        allowed_paths=frozenset({("request", "url")}),
    ).validate({"request": {"url": "https://example.test"}})


def test_tool_argument_validator_allows_only_numeric_wildcard_paths() -> None:
    validator = ToolArgumentAuthorityValidator(
        allowed_paths=frozenset({("commands", "*", "params", "url")}),
    )

    validator.validate(
        {
            "commands": [
                {
                    "method": "goto",
                    "params": {"url": "https://example.test"},
                },
            ],
        },
    )

    with pytest.raises(ToolAuthorityArgumentError):
        validator.validate(
            {
                "commands": {
                    "attacker": {
                        "params": {"url": "https://example.test"},
                    },
                },
            },
        )
