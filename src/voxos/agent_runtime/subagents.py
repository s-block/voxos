from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyService,
    effective_tool_policy_from_json,
)
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_models import AgentRunEventKind, AgentRunKind, AgentRunStatus
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_routing import SqlModelToolRoutingService
from voxos.location.models import ClientLocation


class SubAgentContextMode(StrEnum):
    ISOLATED = "isolated"
    SUMMARY = "summary"


class SubAgentSpawnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_run_id: str = Field(min_length=1, max_length=36)
    target_agent_id: str = Field(min_length=1, max_length=36)
    task_prompt: str = Field(min_length=1, max_length=24000)
    context_mode: SubAgentContextMode = SubAgentContextMode.ISOLATED
    parent_summary: str | None = Field(default=None, min_length=1, max_length=12000)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    inputs: JsonObject = Field(default_factory=dict)
    current_location: ClientLocation | None = None

    @model_validator(mode="after")
    def validate_context(self) -> "SubAgentSpawnRequest":
        if self.context_mode == SubAgentContextMode.SUMMARY and self.parent_summary is None:
            message = "Summary context mode requires a parent summary"
            raise ValueError(message)
        if self.context_mode == SubAgentContextMode.ISOLATED and self.parent_summary is not None:
            message = "Isolated context mode cannot include a parent summary"
            raise ValueError(message)
        return self


class SubAgentModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)


class SubAgentModelPolicy(Protocol):
    async def select_model(
        self,
        *,
        user_id: str,
        parent_provider: str | None,
        parent_model: str | None,
        requested_model: str | None,
    ) -> SubAgentModelSelection:
        """Resolve and validate the provider and model for a delegated run."""


@dataclass(frozen=True, slots=True)
class SubAgentPolicy:
    max_depth: int = 3
    max_fanout: int = 8
    max_active_children_per_user: int = 16


@dataclass(frozen=True, slots=True)
class SubAgentService:
    run_store: SqlModelAgentRunStore
    identity_service: AgentIdentityService
    policy: SubAgentPolicy = SubAgentPolicy()
    effective_tool_policy_service: EffectiveToolPolicyService | None = None
    model_policy: SubAgentModelPolicy | None = None

    async def spawn_subagent(
        self,
        *,
        user_id: str,
        request: SubAgentSpawnRequest,
        parent_provider: str | None = None,
        parent_model: str | None = None,
    ) -> AgentRunRecord | None:
        parent = await self.run_store.get_run(
            user_id=user_id,
            run_id=request.parent_run_id,
        )
        if parent is None:
            return None
        await self.identity_service.require_agent(
            owner_user_id=user_id,
            agent_id=request.target_agent_id,
        )
        parent_policy = effective_tool_policy_from_json(parent.effective_tool_policy_json)
        if parent_policy is None:
            message = "Parent run has no effective tool policy"
            raise ValueError(message)
        parent_depth = subagent_depth(parent.payload_json)
        if parent_depth >= self.policy.max_depth:
            message = "Subagent depth limit exceeded"
            raise ValueError(message)

        active_children = await self.run_store.list_runs(
            user_id=user_id,
            statuses=(
                AgentRunStatus.QUEUED,
                AgentRunStatus.RUNNING,
                AgentRunStatus.CANCELLING,
            ),
            limit=self.policy.max_active_children_per_user,
        )
        active_child_count = sum(1 for run in active_children if run.parent_run_id is not None)
        if active_child_count >= self.policy.max_active_children_per_user:
            message = "Active subagent limit exceeded"
            raise ValueError(message)

        direct_children = await self.run_store.list_runs(
            user_id=user_id,
            statuses=(),
            limit=self.policy.max_fanout + 1,
        )
        direct_child_count = sum(1 for run in direct_children if run.parent_run_id == parent.id)
        if direct_child_count >= self.policy.max_fanout:
            message = "Subagent fanout limit exceeded"
            raise ValueError(message)

        model_selection = await self._model_selection(
            user_id=user_id,
            parent=parent,
            request=request,
            parent_provider=parent_provider,
            parent_model=parent_model,
        )
        payload: JsonObject = {
            "task_prompt": request.task_prompt,
            "context_mode": request.context_mode.value,
            "subagent_depth": parent_depth + 1,
            "inputs": request.inputs,
        }
        if model_selection is not None:
            payload["provider"] = model_selection.provider
            payload["model"] = model_selection.model
        if request.context_mode == SubAgentContextMode.SUMMARY:
            payload["parent_summary"] = request.parent_summary or ""
        if request.current_location is not None:
            payload["current_location"] = request.current_location.model_dump(
                mode="json",
            )

        child_policy = await self._effective_tool_policy_service().snapshot_for_subagent(
            parent_policy=parent_policy,
            user_id=user_id,
            target_agent_id=request.target_agent_id,
            session_id=parent.session_id,
        )
        child = await self.run_store.enqueue_run(
            user_id=user_id,
            agent_id=request.target_agent_id,
            kind=AgentRunKind.SUBAGENT,
            payload_json=payload,
            created_by_user_id=user_id,
            parent_run_id=parent.id,
            parent_session_id=parent.session_id,
            root_run_id=parent.root_run_id or parent.id,
            queue=parent.queue,
            max_attempts=parent.max_attempts,
            effective_tool_policy_json=child_policy.to_json(),
        )
        await self.run_store.append_event(
            run_id=parent.id,
            user_id=user_id,
            kind=AgentRunEventKind.SUBAGENT_STARTED,
            payload_json={
                "child_run_id": child.id,
                "target_agent_id": request.target_agent_id,
                "context_mode": request.context_mode.value,
                **(
                    {
                        "provider": model_selection.provider,
                        "model": model_selection.model,
                    }
                    if model_selection is not None
                    else {}
                ),
            },
        )
        return child

    async def _model_selection(
        self,
        *,
        user_id: str,
        parent: AgentRunRecord,
        request: SubAgentSpawnRequest,
        parent_provider: str | None,
        parent_model: str | None,
    ) -> SubAgentModelSelection | None:
        if self.model_policy is None:
            if request.model is not None:
                message = "Subagent model overrides require a model policy"
                raise ValueError(message)
            return None
        payload_provider = parent.payload_json.get("provider")
        payload_model = parent.payload_json.get("model")
        return await self.model_policy.select_model(
            user_id=user_id,
            parent_provider=(
                parent_provider
                if parent_provider is not None
                else payload_provider
                if isinstance(payload_provider, str)
                else None
            ),
            parent_model=(
                parent_model if parent_model is not None else payload_model if isinstance(payload_model, str) else None
            ),
            requested_model=request.model,
        )

    def _effective_tool_policy_service(self) -> EffectiveToolPolicyService:
        if self.effective_tool_policy_service is not None:
            return self.effective_tool_policy_service
        return EffectiveToolPolicyService(
            SqlModelToolAuthorizationService(self.run_store.session_factory),
            SqlModelToolRoutingService(self.run_store.session_factory),
        )


def subagent_depth(payload: JsonObject) -> int:
    value = payload.get("subagent_depth")
    if isinstance(value, int) and value >= 0:
        return value
    return 0
