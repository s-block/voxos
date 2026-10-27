from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import col, select

from examples.skills.scripts.example_catalog import (
    ExampleSkillCatalog,
    ExampleSkillDefinition,
    ExampleSkillRequirement,
)
from voxos.agent_skills.services import (
    AgentSkillAlreadyExistsError,
    AgentSkillService,
)
from voxos.auth.records import FirebaseUserRecord
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.mcp.models import (
    McpServerSafeMetadata,
    McpServerStatus,
    McpTrustState,
)
from voxos.mcp.records import UserMcpServerRecord

INSTALLABLE_MCP_STATUSES = frozenset(
    {
        McpServerStatus.CONNECTED,
        McpServerStatus.NEEDS_AUTH,
        McpServerStatus.UNTESTED,
    },
)
WARNING_MCP_STATUSES = frozenset(
    {
        McpServerStatus.NEEDS_AUTH,
        McpServerStatus.UNTESTED,
    },
)


class ExampleSkillInstallerError(RuntimeError):
    """Raised when the example installer cannot safely complete preflight."""


class ExampleSkillUserNotFoundError(ExampleSkillInstallerError):
    """Raised when the explicitly selected local Voxos user does not exist."""


class ExampleMcpInventoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    label: str = Field(min_length=1, max_length=256)
    enabled: bool
    trust_state: McpTrustState
    status: McpServerStatus
    allowed_tools: tuple[str, ...] = Field(default_factory=tuple)
    discovered_tools: tuple[str, ...] = Field(default_factory=tuple)
    safe_metadata: McpServerSafeMetadata


class ExampleSkillInstallIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(min_length=1, max_length=128)
    selector: str = Field(min_length=1, max_length=257)
    reason: str = Field(min_length=1, max_length=512)
    missing_discovered_tools: tuple[str, ...] = Field(default_factory=tuple)
    missing_allowed_tools: tuple[str, ...] = Field(default_factory=tuple)


class ExampleSkillInstallReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_groups: tuple[str, ...] = Field(min_length=1)
    selected_groups: tuple[str, ...] = Field(min_length=1)
    dry_run: bool
    ready: tuple[str, ...] = Field(default_factory=tuple)
    created: tuple[str, ...] = Field(default_factory=tuple)
    already_exists: tuple[str, ...] = Field(default_factory=tuple)
    blocked: tuple[ExampleSkillInstallIssue, ...] = Field(default_factory=tuple)
    warnings: tuple[ExampleSkillInstallIssue, ...] = Field(default_factory=tuple)

    @property
    def has_usable_selection(self) -> bool:
        return bool(self.ready or self.created or self.already_exists)


@dataclass(frozen=True, slots=True)
class RequirementEvaluation:
    issue: ExampleSkillInstallIssue | None = None
    warning: ExampleSkillInstallIssue | None = None


@dataclass(frozen=True, slots=True)
class CandidateProblems:
    reasons: tuple[str, ...]
    missing_discovered_tools: tuple[str, ...]
    missing_allowed_tools: tuple[str, ...]

    @property
    def score(self) -> int:
        return len(self.reasons) + len(self.missing_discovered_tools) + len(self.missing_allowed_tools)


@dataclass(frozen=True, slots=True)
class ExampleSkillInstaller:
    session_factory: AsyncSessionFactory
    catalog: ExampleSkillCatalog

    async def install(
        self,
        *,
        user_id: str,
        requested_groups: tuple[str, ...],
        dry_run: bool = False,
    ) -> ExampleSkillInstallReport:
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            message = "local Voxos user ID must not be empty"
            raise ExampleSkillUserNotFoundError(message)

        selected_groups = self.catalog.resolve_groups(requested_groups)
        definitions = self.catalog.definitions_for_groups(selected_groups)
        await self._require_user(normalized_user_id)

        tenant_session_factory = tenant_scoped_session_factory(
            self.session_factory,
            normalized_user_id,
        )
        inventory = await _load_mcp_inventory(
            session_factory=tenant_session_factory,
            user_id=normalized_user_id,
        )
        skill_service = AgentSkillService(tenant_session_factory)
        existing_slugs = {
            skill.slug
            for skill in await skill_service.list_user_skills(
                user_id=normalized_user_id,
                statuses=(),
            )
        }

        ready_definitions: list[ExampleSkillDefinition] = []
        already_exists: list[str] = []
        blocked: list[ExampleSkillInstallIssue] = []
        warnings: list[ExampleSkillInstallIssue] = []
        for definition in definitions:
            if definition.slug in existing_slugs:
                already_exists.append(definition.slug)
                continue

            definition_issues: list[ExampleSkillInstallIssue] = []
            for requirement in definition.requirements:
                evaluation = _evaluate_requirement(
                    slug=definition.slug,
                    requirement=requirement,
                    inventory=inventory,
                )
                if evaluation.issue is not None:
                    definition_issues.append(evaluation.issue)
                if evaluation.warning is not None:
                    warnings.append(evaluation.warning)
            if definition_issues:
                blocked.extend(definition_issues)
            else:
                ready_definitions.append(definition)

        ready = tuple(definition.slug for definition in ready_definitions)
        created: list[str] = []
        if not dry_run:
            for definition in ready_definitions:
                try:
                    await skill_service.create_user_skill(
                        user_id=normalized_user_id,
                        request=definition.request,
                    )
                except AgentSkillAlreadyExistsError:
                    already_exists.append(definition.slug)
                else:
                    created.append(definition.slug)
            ready = ()

        return ExampleSkillInstallReport(
            requested_groups=requested_groups,
            selected_groups=selected_groups,
            dry_run=dry_run,
            ready=ready,
            created=tuple(created),
            already_exists=tuple(already_exists),
            blocked=tuple(blocked),
            warnings=tuple(warnings),
        )

    async def _require_user(self, user_id: str) -> None:
        async with self.session_factory() as session:
            result = await session.exec(
                select(FirebaseUserRecord.id).where(
                    col(FirebaseUserRecord.id) == user_id,
                ),
            )
        if result.one_or_none() is None:
            message = "local Voxos user was not found"
            raise ExampleSkillUserNotFoundError(message)


async def _load_mcp_inventory(
    *,
    session_factory: AsyncSessionFactory,
    user_id: str,
) -> tuple[ExampleMcpInventoryEntry, ...]:
    statement = (
        # SQLAlchemy's typed overloads stop at four selected columns, while the
        # runtime API supports this safe eight-column projection.
        select(  # type: ignore[call-overload]
            UserMcpServerRecord.id,
            UserMcpServerRecord.label,
            UserMcpServerRecord.enabled,
            UserMcpServerRecord.trust_state,
            UserMcpServerRecord.status,
            UserMcpServerRecord.allowed_tools,
            UserMcpServerRecord.discovered_tools,
            UserMcpServerRecord.safe_metadata,
        )
        .where(col(UserMcpServerRecord.user_id) == user_id)
        .order_by(col(UserMcpServerRecord.label), col(UserMcpServerRecord.id))
    )
    async with session_factory() as session:
        result = await session.exec(statement)
        rows = tuple(result.all())

    inventory: list[ExampleMcpInventoryEntry] = []
    for (
        server_id,
        label,
        enabled,
        trust_state,
        status,
        allowed_tools,
        discovered_tools,
        safe_metadata,
    ) in rows:
        try:
            inventory.append(
                ExampleMcpInventoryEntry(
                    id=server_id,
                    label=label,
                    enabled=enabled,
                    trust_state=trust_state,
                    status=status,
                    allowed_tools=tuple(allowed_tools),
                    discovered_tools=tuple(discovered_tools),
                    safe_metadata=McpServerSafeMetadata.model_validate(
                        safe_metadata,
                    ),
                ),
            )
        except ValidationError as error:
            message = f"MCP server '{label}' has invalid safe metadata or readiness state"
            raise ExampleSkillInstallerError(message) from error
    return tuple(inventory)


def _evaluate_requirement(
    *,
    slug: str,
    requirement: ExampleSkillRequirement,
    inventory: tuple[ExampleMcpInventoryEntry, ...],
) -> RequirementEvaluation:
    candidates = tuple(
        entry
        for entry in inventory
        if requirement.selector.matches(
            preset_id=entry.safe_metadata.preset_id,
            preset_variant_id=entry.safe_metadata.preset_variant_id,
        )
    )
    selector = requirement.selector.canonical_name
    if not candidates:
        return RequirementEvaluation(
            issue=ExampleSkillInstallIssue(
                slug=slug,
                selector=selector,
                reason="matching MCP server is not configured for this user",
            ),
        )

    evaluated = tuple(
        (candidate, _candidate_problems(candidate=candidate, requirement=requirement)) for candidate in candidates
    )
    usable_candidates = tuple((candidate, problems) for candidate, problems in evaluated if problems.score == 0)
    if usable_candidates:
        candidate, _problems = min(
            usable_candidates,
            key=lambda item: (
                _mcp_status_priority(item[0].status),
                item[0].label.lower(),
                item[0].id,
            ),
        )
        warning = None
        if candidate.status in WARNING_MCP_STATUSES:
            warning = ExampleSkillInstallIssue(
                slug=slug,
                selector=selector,
                reason=(
                    f"MCP server status is {candidate.status.value}; reconnect or test it before running this skill"
                ),
            )
        return RequirementEvaluation(warning=warning)

    _candidate, problems = min(
        evaluated,
        key=lambda item: (
            item[1].score,
            item[0].label.lower(),
            item[0].id,
        ),
    )
    return RequirementEvaluation(
        issue=ExampleSkillInstallIssue(
            slug=slug,
            selector=selector,
            reason="; ".join(problems.reasons),
            missing_discovered_tools=problems.missing_discovered_tools,
            missing_allowed_tools=problems.missing_allowed_tools,
        ),
    )


def _candidate_problems(
    *,
    candidate: ExampleMcpInventoryEntry,
    requirement: ExampleSkillRequirement,
) -> CandidateProblems:
    reasons: list[str] = []
    if not candidate.enabled:
        reasons.append("MCP server is disabled")
    if candidate.trust_state != McpTrustState.TRUSTED:
        reasons.append("MCP server is untrusted")
    if candidate.status not in INSTALLABLE_MCP_STATUSES:
        reasons.append(f"MCP server status is {candidate.status.value}")

    discovered = set(candidate.discovered_tools)
    allowed = set(candidate.allowed_tools)
    missing_discovered = tuple(tool for tool in requirement.tools if tool not in discovered)
    missing_allowed = tuple(tool for tool in requirement.tools if tool not in allowed)
    if missing_discovered:
        reasons.append("required tools have not been discovered")
    if missing_allowed:
        reasons.append("required tools are not allowed")
    return CandidateProblems(
        reasons=tuple(reasons),
        missing_discovered_tools=missing_discovered,
        missing_allowed_tools=missing_allowed,
    )


def _mcp_status_priority(status: McpServerStatus) -> int:
    if status == McpServerStatus.CONNECTED:
        return 0
    if status == McpServerStatus.NEEDS_AUTH:
        return 1
    if status == McpServerStatus.UNTESTED:
        return 2
    return 3
