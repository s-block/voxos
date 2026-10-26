from datetime import UTC, datetime

import orjson
import pytest

from voxos.agent_core.models import AgentRunContext
from voxos.agent_core.subagent_tools import (
    DELEGATE_AGENT_TASK_TOOL_NAME,
    SubAgentToolProvider,
)
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.subagents import SubAgentService
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_execution import (
    ToolArgumentSchemaError,
    validate_tool_arguments_schema,
)
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolRisk,
)
from voxos.db.session import AsyncSessionFactory
from voxos.location.models import ClientLocation
from voxos.text.model_policy import TextSubAgentModelPolicy
from voxos.text.models import TextProvider, TextProviderConfig, UserTextPreferences
from voxos.text.storage import UserTextPreferenceService


@pytest.mark.asyncio
async def test_subagent_tool_exposes_allowed_models_and_queues_selected_model(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name=DELEGATE_AGENT_TASK_TOOL_NAME,
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )
    effective_policy_service = EffectiveToolPolicyService(authorization_service)
    parent_policy = await effective_policy_service.snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )
    async with sqlite_session_factory() as session:
        await UserTextPreferenceService(session).set_preferences(
            UserTextPreferences(
                user_id="user-123",
                provider=TextProvider.OPENAI,
                model="gpt-5.6-sol",
                credential_ref=None,
                provider_config=TextProviderConfig(
                    allowed_subagent_models=("gpt-5.6-sol", "gpt-5.6-luna"),
                    subagent_model="gpt-5.6-luna",
                ),
            ),
        )
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    parent = await run_store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={
            "text": "Parent task",
            "provider": "openai",
            "model": "gpt-5.6-sol",
        },
        created_by_user_id="user-123",
        effective_tool_policy_json=parent_policy.to_json(),
    )
    model_policy = TextSubAgentModelPolicy(session_factory=sqlite_session_factory)
    provider = SubAgentToolProvider(
        subagent_service=SubAgentService(
            run_store=run_store,
            identity_service=identity_service,
            effective_tool_policy_service=effective_policy_service,
            model_policy=model_policy,
        ),
        model_policy=model_policy,
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id=parent.id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-5.6-sol",
        instructions="Delegate focused work when useful.",
        current_location=ClientLocation(
            latitude=42.4304,
            longitude=19.2594,
            horizontal_accuracy_m=30,
            captured_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        ),
    )

    tools = await provider.list_tools(context)
    model_schema = tools[0].parameters_schema["properties"]["model"]
    assert "context_mode" not in tools[0].parameters_schema["properties"]
    with pytest.raises(
        ToolArgumentSchemaError,
        match="contains unsupported fields",
    ):
        validate_tool_arguments_schema(
            {
                "task_prompt": "Summarize the relevant evidence.",
                "context_mode": "isolated",
                "parent_summary": "Parent context.",
            },
            tools[0].parameters_schema,
        )
    result = await provider.execute_tool(
        context=context,
        tool_name=DELEGATE_AGENT_TASK_TOOL_NAME,
        call_id="call-123",
        arguments_json={
            "task_prompt": "Summarize the relevant evidence.",
            "model": "gpt-5.6-sol",
        },
    )

    assert [tool.name for tool in tools] == [DELEGATE_AGENT_TASK_TOOL_NAME]
    assert model_schema["anyOf"][0]["enum"] == [
        "gpt-5.6-sol",
        "gpt-5.6-luna",
    ]
    output = orjson.loads(result.output_text)
    child = await run_store.get_run(
        user_id="user-123",
        run_id=output["child_run_id"],
    )
    assert child is not None
    assert child.payload_json["provider"] == "openai"
    assert child.payload_json["model"] == "gpt-5.6-sol"
    assert child.payload_json["context_mode"] == "isolated"
    assert "parent_summary" not in child.payload_json
    assert child.payload_json["current_location"]["latitude"] == 42.4304

    summary_result = await provider.execute_tool(
        context=context,
        tool_name=DELEGATE_AGENT_TASK_TOOL_NAME,
        call_id="call-summary",
        arguments_json={
            "task_prompt": "Compare the relevant evidence.",
            "parent_summary": "The parent has already identified two candidates.",
        },
    )
    summary_output = orjson.loads(summary_result.output_text)
    summary_child = await run_store.get_run(
        user_id="user-123",
        run_id=summary_output["child_run_id"],
    )
    assert summary_child is not None
    assert summary_child.payload_json["context_mode"] == "summary"
    assert summary_child.payload_json["parent_summary"] == ("The parent has already identified two candidates.")


@pytest.mark.asyncio
async def test_subagent_tool_is_hidden_without_an_explicit_grant(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    model_policy = TextSubAgentModelPolicy(session_factory=sqlite_session_factory)
    provider = SubAgentToolProvider(
        subagent_service=SubAgentService(
            run_store=SqlModelAgentRunStore(session_factory=sqlite_session_factory),
            identity_service=identity_service,
            model_policy=model_policy,
        ),
        model_policy=model_policy,
        authorization_service=authorization_service,
    )

    tools = await provider.list_tools(
        AgentRunContext(
            session_id="session-123",
            run_id="run-123",
            user_id="user-123",
            agent_id=agent.id,
            provider="openai",
            model="gpt-5.6-sol",
            instructions="Be helpful.",
        ),
    )

    assert tools == ()
