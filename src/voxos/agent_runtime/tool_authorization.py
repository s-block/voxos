from dataclasses import dataclass
from hashlib import sha256

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicySnapshot,
    effective_tool_policy_from_json,
)
from voxos.agent_runtime.learning_lock import lock_learning_settings
from voxos.agent_runtime.records import (
    AgentRunRecord,
    AgentSessionRecord,
    AgentToolAuditEventRecord,
    AgentToolGrantRecord,
)
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationDecision,
    ToolCapability,
    ToolGrant,
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolPolicy,
    ToolReference,
    ToolRisk,
)
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import IntegrationStatus
from voxos.integrations.records import UserIntegrationRecord
from voxos.mcp.defaults import platform_default_tool_names
from voxos.mcp.models import McpServerStatus, McpTrustState
from voxos.mcp.records import UserMcpServerRecord

READ_ONLY_INTERNAL_TOOL_NAMES = (
    "search_agent_history",
    "read_agent_history",
    "get_current_datetime",
    "list_agent_memories",
    "list_agent_skills",
    "get_agent_skill",
    "list_task_plans",
    "search_contacts",
    "search_voxos_docs",
    "read_voxos_doc_page",
)
CONVERSATIONAL_INTERNAL_TOOL_NAMES = ("generate_image",)
SCHEDULING_INTERNAL_TOOL_NAMES = ("create_agent_schedule",)
DEFAULT_ALLOWED_INTERNAL_TOOL_NAMES = (
    *READ_ONLY_INTERNAL_TOOL_NAMES,
    *CONVERSATIONAL_INTERNAL_TOOL_NAMES,
    *SCHEDULING_INTERNAL_TOOL_NAMES,
    "record_agent_improvement",
)
MUTATING_INTERNAL_TOOL_NAMES = (
    "record_agent_improvement",
    "delegate_agent_task",
    "create_agent_memory",
    "propose_agent_memory",
    "archive_agent_memory",
    "confirm_agent_memory",
    "create_user_skill",
    "update_user_skill",
    "archive_user_skill",
    "confirm_skill_proposal",
    "create_task_plan",
    "update_task_step",
    "complete_task_plan",
)
INTERNAL_TOOL_NAMES = (
    *READ_ONLY_INTERNAL_TOOL_NAMES,
    *CONVERSATIONAL_INTERNAL_TOOL_NAMES,
    *SCHEDULING_INTERNAL_TOOL_NAMES,
    *MUTATING_INTERNAL_TOOL_NAMES,
)
INTERNAL_GRANT_SERVER_ERROR = "internal tool grants must not include an MCP server ID"
UNKNOWN_INTERNAL_TOOL_ERROR = "unknown internal tool grant"
MCP_SERVER_REQUIRED_ERROR = "MCP tool grants require an MCP server ID"
MCP_SERVER_NOT_FOUND_ERROR = "MCP server was not found"
MCP_TOOL_NOT_ALLOWED_ERROR = "MCP tool must be saved in the server allowlist before it can be granted"
INTEGRATION_GRANT_SERVER_ERROR = "integration tool grants must not include an MCP server ID"
INTEGRATION_REQUIRED_ERROR = "integration tool grants require an integration ID"
INTEGRATION_NOT_FOUND_ERROR = "integration was not found"
INTERNAL_TOOL_INTEGRATION_ERROR = "internal tool grants must not include an integration ID"
MCP_TOOL_INTEGRATION_ERROR = "MCP tool grants must not include an integration ID"
INTERNAL_TOOL_POLICIES = (
    tuple(
        ToolPolicy(
            tool_name=tool_name,
            capabilities=(ToolCapability.READ,),
            risk=ToolRisk.MEDIUM if tool_name in {"search_agent_history", "read_agent_history"} else ToolRisk.LOW,
            confirmation_required=False,
        )
        for tool_name in READ_ONLY_INTERNAL_TOOL_NAMES
    )
    + tuple(
        ToolPolicy(
            tool_name=tool_name,
            capabilities=(ToolCapability.WRITE, ToolCapability.EXTERNAL_SIDE_EFFECT),
            risk=ToolRisk.MEDIUM,
            confirmation_required=False,
        )
        for tool_name in CONVERSATIONAL_INTERNAL_TOOL_NAMES
    )
    + tuple(
        ToolPolicy(
            tool_name=tool_name,
            capabilities=(ToolCapability.WRITE, ToolCapability.EXTERNAL_SIDE_EFFECT),
            risk=ToolRisk.MEDIUM,
            confirmation_required=True,
        )
        for tool_name in SCHEDULING_INTERNAL_TOOL_NAMES
    )
    + tuple(
        ToolPolicy(
            tool_name=tool_name,
            capabilities=(ToolCapability.WRITE,),
            risk=ToolRisk.MEDIUM,
            confirmation_required=True,
        )
        for tool_name in MUTATING_INTERNAL_TOOL_NAMES
    )
)


class ToolGrantValidationError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SqlModelToolAuthorizationService:
    session_factory: AsyncSessionFactory

    async def get_explicit_grant(
        self,
        *,
        user_id: str,
        agent_id: str,
        reference: ToolReference,
    ) -> ToolGrant | None:
        async with self.session_factory() as session:
            record = await self._find_grant_record(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                reference=reference,
            )
        return None if record is None else tool_grant_from_record(record)

    async def list_effective_grants(self, scope: AgentScope) -> tuple[ToolGrant, ...]:
        statement = (
            select(AgentToolGrantRecord)
            .where(
                col(AgentToolGrantRecord.user_id) == scope.owner_user_id,
                col(AgentToolGrantRecord.agent_id) == scope.agent_id,
            )
            .order_by(
                col(AgentToolGrantRecord.tool_source).asc(),
                col(AgentToolGrantRecord.tool_name).asc(),
            )
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            grants = tuple(tool_grant_from_record(record) for record in result.all())
            grants = await self._grants_with_platform_defaults(
                session=session,
                scope=scope,
                grants=grants,
            )
        return grants_with_default_internal_tools(scope=scope, grants=grants)

    async def set_grants(
        self,
        *,
        user_id: str,
        agent_id: str,
        grants: tuple[ToolGrantUpdate, ...],
        audit_reason: str = "grant_updated",
    ) -> tuple[ToolGrant, ...]:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            updated: list[AgentToolGrantRecord] = []
            for grant in grants:
                await self._validate_grant(
                    user_id=user_id,
                    grant=grant,
                    session=session,
                )
                record = await self._find_grant_record(
                    session=session,
                    user_id=user_id,
                    agent_id=agent_id,
                    reference=ToolReference(
                        tool_name=grant.tool_name,
                        tool_source=grant.tool_source,
                        mcp_server_id=grant.mcp_server_id,
                        integration_id=grant.integration_id,
                        risk=grant.risk,
                    ),
                )
                if record is None:
                    record = AgentToolGrantRecord(
                        user_id=user_id,
                        agent_id=agent_id,
                        tool_name=grant.tool_name,
                        tool_source=grant.tool_source.value,
                        mcp_server_id=grant.mcp_server_id,
                        integration_id=grant.integration_id,
                    )
                    session.add(record)
                record.status = grant.status.value
                record.risk = grant.risk.value
                record.updated_at = utc_now()
                session.add(
                    AgentToolAuditEventRecord(
                        user_id=user_id,
                        agent_id=agent_id,
                        tool_name=grant.tool_name,
                        tool_source=grant.tool_source.value,
                        mcp_server_id=grant.mcp_server_id,
                        integration_id=grant.integration_id,
                        decision=grant.status.value,
                        reason=audit_reason,
                    ),
                )
                updated.append(record)
            await session.commit()
            for record in updated:
                await session.refresh(record)
            return tuple(tool_grant_from_record(record) for record in updated)

    async def authorize_tool(
        self,
        *,
        scope: AgentScope,
        reference: ToolReference,
    ) -> ToolAuthorizationDecision:
        async with self.session_factory() as session:
            policy, policy_kind = await self._effective_policy_for_scope(
                session=session,
                scope=scope,
            )
            if policy_kind is not None and policy is None:
                decision = ToolAuthorizationDecision(
                    allowed=False,
                    reason=f"{policy_kind} has no valid effective tool policy",
                    reference=reference,
                )
            elif policy is not None:
                decision = ToolAuthorizationDecision(
                    allowed=policy.allows(reference),
                    reason=(
                        f"{policy_kind} effective policy allows execution"
                        if policy.allows(reference)
                        else f"{policy_kind} effective policy denies execution"
                    ),
                    reference=reference,
                )
            else:
                decision = await self._grant_decision(
                    session=session,
                    scope=scope,
                    reference=reference,
                )
            self._add_audit_event(
                session=session,
                scope=scope,
                reference=reference,
                allowed=decision.allowed,
                reason=decision.reason,
            )
            await session.commit()
            return decision

    async def record_tool_audit_event(
        self,
        *,
        scope: AgentScope,
        reference: ToolReference,
        allowed: bool,
        reason: str,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        async with self.session_factory() as session:
            self._add_audit_event(
                session=session,
                scope=scope,
                reference=reference,
                allowed=allowed,
                reason=reason,
                metadata_json=metadata_json,
            )
            await session.commit()

    async def allowed_tool_names(
        self,
        *,
        scope: AgentScope,
        tool_source: ToolGrantScope,
        tool_names: tuple[str, ...],
        mcp_server_id: str | None = None,
        integration_id: str | None = None,
    ) -> tuple[str, ...]:
        if not tool_names:
            return ()
        async with self.session_factory() as session:
            policy, policy_kind = await self._effective_policy_for_scope(
                session=session,
                scope=scope,
            )
            if policy_kind is not None and policy is None:
                return ()
            if policy is not None:
                return policy.allowed_tool_names(
                    tool_source=tool_source,
                    tool_names=tool_names,
                    mcp_server_id=mcp_server_id,
                    integration_id=integration_id,
                )

        statement = select(AgentToolGrantRecord).where(
            col(AgentToolGrantRecord.user_id) == scope.owner_user_id,
            col(AgentToolGrantRecord.agent_id) == scope.agent_id,
            col(AgentToolGrantRecord.tool_source) == tool_source.value,
            col(AgentToolGrantRecord.tool_name).in_(tool_names),
        )
        if mcp_server_id is None:
            statement = statement.where(col(AgentToolGrantRecord.mcp_server_id).is_(None))
        else:
            statement = statement.where(
                col(AgentToolGrantRecord.mcp_server_id) == mcp_server_id,
            )
        if integration_id is None:
            statement = statement.where(col(AgentToolGrantRecord.integration_id).is_(None))
        else:
            statement = statement.where(
                col(AgentToolGrantRecord.integration_id) == integration_id,
            )

        async with self.session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
            allowed = {record.tool_name for record in records if record.status == ToolGrantStatus.ALLOWED.value}
            existing = {record.tool_name for record in records}
            allowed.update(
                default_allowed_tool_names(
                    tool_source=tool_source,
                    tool_names=tool_names,
                    mcp_server_id=mcp_server_id,
                    integration_id=integration_id,
                    existing_tool_names=existing,
                ),
            )
            if mcp_server_id is not None:
                allowed.update(
                    await self._platform_default_tool_names(
                        session=session,
                        user_id=scope.owner_user_id,
                        mcp_server_id=mcp_server_id,
                        candidate_tool_names=tool_names,
                        existing_tool_names=existing,
                    ),
                )
        return tuple(tool_name for tool_name in tool_names if tool_name in allowed)

    async def _grants_with_platform_defaults(
        self,
        *,
        session: AsyncSession,
        scope: AgentScope,
        grants: tuple[ToolGrant, ...],
    ) -> tuple[ToolGrant, ...]:
        existing = {
            (grant.mcp_server_id, grant.tool_name) for grant in grants if grant.tool_source == ToolGrantScope.MCP_SERVER
        }
        statement = select(UserMcpServerRecord).where(
            col(UserMcpServerRecord.user_id) == scope.owner_user_id,
            col(UserMcpServerRecord.enabled).is_(True),
            col(UserMcpServerRecord.trust_state) == McpTrustState.TRUSTED.value,
            col(UserMcpServerRecord.status) == McpServerStatus.CONNECTED.value,
        )
        result = await session.exec(statement)
        defaults: list[ToolGrant] = []
        for record in result.all():
            for tool_name in platform_default_tool_names(record):
                if (record.id, tool_name) in existing:
                    continue
                grant_key = f"{scope.agent_id}:{record.id}:{tool_name}"
                defaults.append(
                    ToolGrant(
                        id=f"default-mcp-{sha256(grant_key.encode()).hexdigest()[:20]}",
                        user_id=scope.owner_user_id,
                        agent_id=scope.agent_id,
                        tool_name=tool_name,
                        tool_source=ToolGrantScope.MCP_SERVER,
                        mcp_server_id=record.id,
                        status=ToolGrantStatus.ALLOWED,
                        risk=ToolRisk.LOW,
                    ),
                )
        return (*grants, *defaults)

    async def _platform_default_reference_allowed(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        reference: ToolReference,
    ) -> bool:
        if reference.tool_source != ToolGrantScope.MCP_SERVER or reference.mcp_server_id is None:
            return False
        default_names = await self._platform_default_tool_names(
            session=session,
            user_id=user_id,
            mcp_server_id=reference.mcp_server_id,
            candidate_tool_names=(reference.tool_name,),
            existing_tool_names=set(),
        )
        return reference.tool_name in default_names

    async def _platform_default_tool_names(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        mcp_server_id: str,
        candidate_tool_names: tuple[str, ...],
        existing_tool_names: set[str],
    ) -> set[str]:
        statement = select(UserMcpServerRecord).where(
            col(UserMcpServerRecord.id) == mcp_server_id,
            col(UserMcpServerRecord.user_id) == user_id,
            col(UserMcpServerRecord.enabled).is_(True),
            col(UserMcpServerRecord.trust_state) == McpTrustState.TRUSTED.value,
            col(UserMcpServerRecord.status) == McpServerStatus.CONNECTED.value,
        )
        result = await session.exec(statement)
        record = result.one_or_none()
        if record is None:
            return set()
        defaults = set(platform_default_tool_names(record))
        return {
            tool_name
            for tool_name in candidate_tool_names
            if tool_name in defaults and tool_name not in existing_tool_names
        }

    async def _grant_decision(
        self,
        *,
        session: AsyncSession,
        scope: AgentScope,
        reference: ToolReference,
    ) -> ToolAuthorizationDecision:
        record = await self._find_grant_record(
            session=session,
            user_id=scope.owner_user_id,
            agent_id=scope.agent_id,
            reference=reference,
        )
        if record is None:
            if default_internal_tool_reference_allowed(reference):
                return ToolAuthorizationDecision(
                    allowed=True,
                    reason="default internal read-only tool allows execution",
                    reference=reference,
                )
            if await self._platform_default_reference_allowed(
                session=session,
                user_id=scope.owner_user_id,
                reference=reference,
            ):
                return ToolAuthorizationDecision(
                    allowed=True,
                    reason="platform-default read-only tool allows execution",
                    reference=reference,
                )
            return ToolAuthorizationDecision(
                allowed=False,
                reason="tool has no persisted grant",
                reference=reference,
            )
        if record.status == ToolGrantStatus.ALLOWED.value:
            return ToolAuthorizationDecision(
                allowed=True,
                reason="tool grant allows execution",
                reference=reference,
            )
        return ToolAuthorizationDecision(
            allowed=False,
            reason="tool grant denies execution",
            reference=reference,
        )

    async def _effective_policy_for_scope(
        self,
        *,
        session: AsyncSession,
        scope: AgentScope,
    ) -> tuple[EffectiveToolPolicySnapshot | None, str | None]:
        if scope.run_id is not None:
            return await self._run_policy_for_scope(session=session, scope=scope), "run"
        if scope.channel == "livekit":
            return await self._session_policy_for_scope(session=session, scope=scope), "session"
        return None, None

    async def _run_policy_for_scope(
        self,
        *,
        session: AsyncSession,
        scope: AgentScope,
    ) -> EffectiveToolPolicySnapshot | None:
        if scope.run_id is None:
            return None
        run = await session.get(AgentRunRecord, scope.run_id)
        if (
            run is None
            or run.user_id != scope.owner_user_id
            or run.agent_id != scope.agent_id
            or run.deleted_at is not None
        ):
            return None
        policy = effective_tool_policy_from_json(run.effective_tool_policy_json)
        if policy is None or policy.user_id != scope.owner_user_id or policy.agent_id != scope.agent_id:
            return None
        return policy

    async def _session_policy_for_scope(
        self,
        *,
        session: AsyncSession,
        scope: AgentScope,
    ) -> EffectiveToolPolicySnapshot | None:
        if scope.session_id is None:
            return None
        session_record = await session.get(AgentSessionRecord, scope.session_id)
        if (
            session_record is None
            or session_record.user_id != scope.owner_user_id
            or session_record.agent_id != scope.agent_id
            or session_record.channel != "livekit"
            or session_record.deleted_at is not None
        ):
            return None
        policy = effective_tool_policy_from_json(
            session_record.effective_tool_policy_json,
        )
        if policy is None or policy.user_id != scope.owner_user_id or policy.agent_id != scope.agent_id:
            return None
        return policy

    async def _validate_grant(
        self,
        *,
        user_id: str,
        grant: ToolGrantUpdate,
        session: AsyncSession,
    ) -> None:
        if grant.tool_source == ToolGrantScope.INTERNAL:
            if grant.mcp_server_id is not None:
                message = INTERNAL_GRANT_SERVER_ERROR
                raise ToolGrantValidationError(message)
            if grant.integration_id is not None:
                message = INTERNAL_TOOL_INTEGRATION_ERROR
                raise ToolGrantValidationError(message)
            if grant.tool_name not in INTERNAL_TOOL_NAMES:
                message = UNKNOWN_INTERNAL_TOOL_ERROR
                raise ToolGrantValidationError(message)
            return

        if grant.tool_source == ToolGrantScope.INTEGRATION:
            if grant.mcp_server_id is not None:
                message = INTEGRATION_GRANT_SERVER_ERROR
                raise ToolGrantValidationError(message)
            if grant.integration_id is None:
                message = INTEGRATION_REQUIRED_ERROR
                raise ToolGrantValidationError(message)
            integration_statement = select(UserIntegrationRecord).where(
                col(UserIntegrationRecord.id) == grant.integration_id,
                col(UserIntegrationRecord.user_id) == user_id,
                col(UserIntegrationRecord.status) == IntegrationStatus.CONNECTED.value,
                col(UserIntegrationRecord.deleted_at).is_(None),
            )
            integration_result = await session.exec(integration_statement)
            if integration_result.one_or_none() is None:
                message = INTEGRATION_NOT_FOUND_ERROR
                raise ToolGrantValidationError(message)
            return

        if grant.integration_id is not None:
            message = MCP_TOOL_INTEGRATION_ERROR
            raise ToolGrantValidationError(message)
        if grant.mcp_server_id is None:
            message = MCP_SERVER_REQUIRED_ERROR
            raise ToolGrantValidationError(message)

        mcp_statement = select(UserMcpServerRecord).where(
            col(UserMcpServerRecord.id) == grant.mcp_server_id,
            col(UserMcpServerRecord.user_id) == user_id,
        )
        mcp_result = await session.exec(mcp_statement)
        mcp_record = mcp_result.one_or_none()
        if mcp_record is None:
            message = MCP_SERVER_NOT_FOUND_ERROR
            raise ToolGrantValidationError(message)
        if grant.status == ToolGrantStatus.ALLOWED and grant.tool_name not in mcp_record.allowed_tools:
            message = MCP_TOOL_NOT_ALLOWED_ERROR
            raise ToolGrantValidationError(message)

    def _add_audit_event(
        self,
        *,
        session: AsyncSession,
        scope: AgentScope,
        reference: ToolReference,
        allowed: bool,
        reason: str,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        session.add(
            AgentToolAuditEventRecord(
                user_id=scope.owner_user_id,
                agent_id=scope.agent_id,
                session_id=scope.session_id,
                run_id=scope.run_id,
                tool_name=reference.tool_name,
                tool_source=reference.tool_source.value,
                mcp_server_id=reference.mcp_server_id,
                integration_id=reference.integration_id,
                decision=(ToolGrantStatus.ALLOWED.value if allowed else ToolGrantStatus.DENIED.value),
                reason=reason,
                metadata_json=metadata_json or {},
            ),
        )

    async def _find_grant_record(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        reference: ToolReference,
    ) -> AgentToolGrantRecord | None:
        statement = select(AgentToolGrantRecord).where(
            col(AgentToolGrantRecord.user_id) == user_id,
            col(AgentToolGrantRecord.agent_id) == agent_id,
            col(AgentToolGrantRecord.tool_source) == reference.tool_source.value,
            col(AgentToolGrantRecord.tool_name) == reference.tool_name,
        )
        if reference.mcp_server_id is None:
            statement = statement.where(col(AgentToolGrantRecord.mcp_server_id).is_(None))
        else:
            statement = statement.where(
                col(AgentToolGrantRecord.mcp_server_id) == reference.mcp_server_id,
            )
        if reference.integration_id is None:
            statement = statement.where(col(AgentToolGrantRecord.integration_id).is_(None))
        else:
            statement = statement.where(
                col(AgentToolGrantRecord.integration_id) == reference.integration_id,
            )
        result = await session.exec(statement)
        return result.one_or_none()


def tool_grant_from_record(record: AgentToolGrantRecord) -> ToolGrant:
    return ToolGrant(
        id=record.id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        tool_name=record.tool_name,
        tool_source=ToolGrantScope(record.tool_source),
        mcp_server_id=record.mcp_server_id,
        integration_id=record.integration_id,
        status=ToolGrantStatus(record.status),
        risk=ToolRisk(record.risk),
    )


def grants_with_default_internal_tools(
    *,
    scope: AgentScope,
    grants: tuple[ToolGrant, ...],
) -> tuple[ToolGrant, ...]:
    existing = {
        grant.tool_name
        for grant in grants
        if (
            grant.tool_source == ToolGrantScope.INTERNAL
            and grant.mcp_server_id is None
            and grant.integration_id is None
        )
    }
    defaults = tuple(
        default_internal_tool_grant(scope=scope, tool_name=tool_name)
        for tool_name in DEFAULT_ALLOWED_INTERNAL_TOOL_NAMES
        if tool_name not in existing
    )
    return (*grants, *defaults)


def default_internal_tool_grant(*, scope: AgentScope, tool_name: str) -> ToolGrant:
    policy = next(
        (policy for policy in INTERNAL_TOOL_POLICIES if policy.tool_name == tool_name),
        None,
    )
    if policy is None:
        raise ToolGrantValidationError(UNKNOWN_INTERNAL_TOOL_ERROR)
    return ToolGrant(
        id=f"default-{tool_name.replace('_', '-')}",
        user_id=scope.owner_user_id,
        agent_id=scope.agent_id,
        tool_name=tool_name,
        tool_source=ToolGrantScope.INTERNAL,
        status=ToolGrantStatus.ALLOWED,
        risk=policy.risk,
    )


def default_allowed_tool_names(
    *,
    tool_source: ToolGrantScope,
    tool_names: tuple[str, ...],
    mcp_server_id: str | None,
    integration_id: str | None,
    existing_tool_names: set[str],
) -> set[str]:
    if tool_source != ToolGrantScope.INTERNAL or mcp_server_id is not None or integration_id is not None:
        return set()
    return {
        tool_name
        for tool_name in tool_names
        if tool_name in DEFAULT_ALLOWED_INTERNAL_TOOL_NAMES and tool_name not in existing_tool_names
    }


def default_internal_tool_reference_allowed(reference: ToolReference) -> bool:
    return (
        reference.tool_source == ToolGrantScope.INTERNAL
        and reference.mcp_server_id is None
        and reference.integration_id is None
        and reference.tool_name in DEFAULT_ALLOWED_INTERNAL_TOOL_NAMES
    )
