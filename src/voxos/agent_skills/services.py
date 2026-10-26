import re
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.learning_lock import lock_learning_settings
from voxos.agent_skills.catalog import BuiltinSkillCatalog
from voxos.agent_skills.models import (
    SkillCapabilityPreference,
    SkillCategory,
    SkillCreateRequest,
    SkillDocument,
    SkillRenderContext,
    SkillScope,
    SkillStatus,
    SkillSummary,
    SkillUpdateRequest,
)
from voxos.agent_skills.records import AgentSkillRecord
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory

SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
SEARCH_TERM_PATTERN = re.compile(r"[a-z0-9]+")
SHORT_TECHNICAL_TERMS = frozenset({"ai", "ci", "db", "mcp", "pr", "ui", "ux"})
SEARCH_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "for",
        "from",
        "get",
        "help",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "please",
        "show",
        "that",
        "the",
        "this",
        "to",
        "use",
        "want",
        "we",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
        "you",
        "your",
    },
)


class AgentSkillAlreadyExistsError(ValueError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"user skill slug already exists: {slug}")
        self.slug = slug


@dataclass(frozen=True, slots=True)
class AgentSkillService:
    session_factory: AsyncSessionFactory
    builtin_catalog: BuiltinSkillCatalog = field(default_factory=BuiltinSkillCatalog)

    async def list_relevant_skills(
        self,
        *,
        user_id: str,
        context: SkillRenderContext | None = None,
    ) -> tuple[SkillSummary, ...]:
        render_context = context or SkillRenderContext()
        documents = (
            *self.builtin_catalog.list_skills(),
            *(await self._list_user_skill_documents(user_id=user_id)),
        )
        matched = _filter_skills(documents, render_context.query)
        return tuple(_summary_from_document(document) for document in matched[: render_context.max_summaries])

    async def list_user_skills(
        self,
        *,
        user_id: str,
        statuses: tuple[SkillStatus, ...] = (
            SkillStatus.ACTIVE,
            SkillStatus.PROPOSED,
        ),
    ) -> tuple[SkillDocument, ...]:
        statement = (
            select(AgentSkillRecord)
            .where(col(AgentSkillRecord.owner_user_id) == user_id)
            .order_by(col(AgentSkillRecord.updated_at).desc())
        )
        if statuses:
            statement = statement.where(
                col(AgentSkillRecord.status).in_(
                    tuple(status.value for status in statuses),
                ),
            )

        async with self.session_factory() as session:
            result = await session.exec(statement)
            return tuple(skill_from_record(record) for record in result.all())

    async def get_skill(self, *, user_id: str, slug: str) -> SkillDocument | None:
        user_skill = await self._get_user_skill(user_id=user_id, slug=slug)
        if user_skill is not None:
            return skill_from_record(user_skill)
        return self.builtin_catalog.get_skill(slug)

    def create_learning_in_session(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        request: SkillCreateRequest,
    ) -> AgentSkillRecord:
        """Compose a policy-approved new skill in the caller's transaction."""
        record = AgentSkillRecord(
            scope=SkillScope.USER.value,
            owner_user_id=user_id,
            name=request.name,
            slug=normalized_skill_slug(request.slug or request.name),
            category=request.category.value,
            status=SkillStatus.ACTIVE.value,
            description=request.description,
            body_markdown=request.body_markdown,
            created_by_reflection_id=request.created_by_reflection_id,
        )
        session.add(record)
        return record

    async def create_user_skill(
        self,
        *,
        user_id: str,
        request: SkillCreateRequest,
    ) -> SkillDocument:
        slug = normalized_skill_slug(request.slug or request.name)
        existing = await self._get_user_skill(user_id=user_id, slug=slug)
        if existing is not None:
            raise AgentSkillAlreadyExistsError(slug)

        record = AgentSkillRecord(
            scope=SkillScope.USER.value,
            owner_user_id=user_id,
            name=request.name,
            slug=slug,
            category=request.category.value,
            status=SkillStatus.ACTIVE.value if request.confirmed else SkillStatus.PROPOSED.value,
            description=request.description,
            body_markdown=request.body_markdown,
            preferred_capabilities=[
                capability_preference_record_value(preference) for preference in request.preferred_capabilities
            ],
            created_by_reflection_id=request.created_by_reflection_id,
        )
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            session.add(record)
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                existing = await self._get_user_skill_in_session(
                    session=session,
                    user_id=user_id,
                    slug=slug,
                )
                if existing is None:
                    raise
                raise AgentSkillAlreadyExistsError(slug) from error
            await session.refresh(record)
        return skill_from_record(record)

    async def update_user_skill(
        self,
        *,
        user_id: str,
        slug: str,
        request: SkillUpdateRequest,
    ) -> SkillDocument | None:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await self._get_user_skill_in_session(
                session=session,
                user_id=user_id,
                slug=slug,
            )
            if record is None:
                return None
            if record.status == SkillStatus.ACTIVE.value and not request.confirmed:
                message = "Active skill revisions require explicit owner confirmation"
                raise ValueError(message)
            record.created_by_reflection_id = None
            if request.name is not None:
                record.name = request.name
            if request.category is not None:
                record.category = request.category.value
            if request.description is not None:
                record.description = request.description
            if request.body_markdown is not None:
                record.body_markdown = request.body_markdown
            if request.preferred_capabilities is not None:
                record.preferred_capabilities = [
                    capability_preference_record_value(preference) for preference in request.preferred_capabilities
                ]
            if request.confirmed:
                record.status = SkillStatus.ACTIVE.value
            record.updated_at = utc_now()
            await session.commit()
            await session.refresh(record)
        return skill_from_record(record)

    async def archive_user_skill(
        self,
        *,
        user_id: str,
        slug: str,
    ) -> SkillDocument | None:
        return await self._set_user_skill_status(
            user_id=user_id,
            slug=slug,
            status=SkillStatus.ARCHIVED,
        )

    async def delete_user_skill(
        self,
        *,
        user_id: str,
        slug: str,
    ) -> SkillDocument | None:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await self._get_user_skill_in_session(
                session=session,
                user_id=user_id,
                slug=slug,
            )
            if record is None:
                return None
            deleted = skill_from_record(record)
            await session.delete(record)
            await session.commit()
        return deleted

    async def confirm_skill_proposal(
        self,
        *,
        user_id: str,
        slug: str,
    ) -> SkillDocument | None:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await self._get_user_skill_in_session(session=session, user_id=user_id, slug=slug)
            if record is None:
                return None
            if record.status != SkillStatus.PROPOSED.value:
                message = "Only proposed skills can be confirmed"
                raise ValueError(message)
            record.status = SkillStatus.ACTIVE.value
            record.updated_at = utc_now()
            await session.commit()
            await session.refresh(record)
            return skill_from_record(record)

    async def _set_user_skill_status(
        self,
        *,
        user_id: str,
        slug: str,
        status: SkillStatus,
    ) -> SkillDocument | None:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await self._get_user_skill_in_session(
                session=session,
                user_id=user_id,
                slug=slug,
            )
            if record is None:
                return None
            record.status = status.value
            record.updated_at = utc_now()
            await session.commit()
            await session.refresh(record)
        return skill_from_record(record)

    async def _list_user_skill_documents(
        self,
        *,
        user_id: str,
    ) -> tuple[SkillDocument, ...]:
        statement = (
            select(AgentSkillRecord)
            .where(
                col(AgentSkillRecord.owner_user_id) == user_id,
                col(AgentSkillRecord.status).in_(
                    (SkillStatus.ACTIVE.value, SkillStatus.PROPOSED.value),
                ),
            )
            .order_by(col(AgentSkillRecord.updated_at).desc())
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            return tuple(skill_from_record(record) for record in result.all())

    async def _get_user_skill(
        self,
        *,
        user_id: str,
        slug: str,
    ) -> AgentSkillRecord | None:
        async with self.session_factory() as session:
            return await self._get_user_skill_in_session(
                session=session,
                user_id=user_id,
                slug=slug,
            )

    async def _get_user_skill_in_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        slug: str,
    ) -> AgentSkillRecord | None:
        statement = select(AgentSkillRecord).where(
            col(AgentSkillRecord.owner_user_id) == user_id,
            col(AgentSkillRecord.slug) == slug,
        )
        result = await session.exec(statement)
        return result.one_or_none()


def normalized_skill_slug(text: str) -> str:
    slug = SLUG_PATTERN.sub("-", text.lower()).strip("-")
    return slug[:128] or "skill"


def skill_from_record(record: AgentSkillRecord) -> SkillDocument:
    return SkillDocument(
        id=record.id,
        scope=SkillScope(record.scope),
        owner_user_id=record.owner_user_id,
        name=record.name,
        slug=record.slug,
        category=SkillCategory(record.category),
        status=SkillStatus(record.status),
        description=record.description,
        body_markdown=record.body_markdown,
        preferred_capabilities=tuple(
            SkillCapabilityPreference.model_validate(preference) for preference in record.preferred_capabilities
        ),
        created_by_reflection_id=record.created_by_reflection_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _summary_from_document(document: SkillDocument) -> SkillSummary:
    return SkillSummary(
        id=document.id,
        scope=document.scope,
        owner_user_id=document.owner_user_id,
        name=document.name,
        slug=document.slug,
        category=document.category,
        status=document.status,
        description=document.description,
        preferred_capabilities=document.preferred_capabilities,
    )


def capability_preference_record_value(
    preference: SkillCapabilityPreference,
) -> dict[str, object]:
    return {
        "capability": preference.capability,
        "tool_names": list(preference.tool_names),
    }


def _filter_skills(
    documents: tuple[SkillDocument, ...],
    query: str | None,
) -> tuple[SkillDocument, ...]:
    active_documents = tuple(document for document in documents if document.status == SkillStatus.ACTIVE)
    if query is None:
        return active_documents

    terms = _meaningful_search_terms(query)
    if not terms:
        return ()

    scored_documents = tuple(
        (
            _skill_relevance_score(document=document, terms=terms),
            position,
            document,
        )
        for position, document in enumerate(active_documents)
    )
    return tuple(
        document
        for score, _position, document in sorted(
            scored_documents,
            key=lambda item: (-item[0], item[1]),
        )
        if score > 0
    )


def _meaningful_search_terms(query: str) -> tuple[str, ...]:
    query_terms = SEARCH_TERM_PATTERN.findall(query.lower())
    return tuple(
        dict.fromkeys(term for term in query_terms if term not in SEARCH_STOP_WORDS),
    )


def _skill_relevance_score(
    *,
    document: SkillDocument,
    terms: tuple[str, ...],
) -> int:
    name_and_slug_terms = _text_terms(f"{document.name} {document.slug}")
    description_terms = _text_terms(document.description)
    category_terms = _text_terms(document.category.value)
    body_terms = _text_terms(document.body_markdown)

    score = 0
    for term in terms:
        if term in name_and_slug_terms:
            score += 16
        elif _has_prefix_match(term=term, candidates=name_and_slug_terms):
            score += 12

        if _is_broad_search_term(term):
            if term in description_terms:
                score += 8
            elif _has_prefix_match(term=term, candidates=description_terms):
                score += 6
            if term in category_terms:
                score += 4
            if term in body_terms:
                score += 1

    prominent_terms = name_and_slug_terms | description_terms
    if len(terms) > 1 and all(
        term in prominent_terms
        or (_is_broad_search_term(term) and _has_prefix_match(term=term, candidates=prominent_terms))
        for term in terms
    ):
        score += 12
    return score


def _is_broad_search_term(term: str) -> bool:
    return len(term) >= 3 or term in SHORT_TECHNICAL_TERMS


def _has_prefix_match(*, term: str, candidates: frozenset[str]) -> bool:
    if len(term) < 3:
        return False
    return any(candidate.startswith(term) or term.startswith(candidate) for candidate in candidates)


def _text_terms(value: str) -> frozenset[str]:
    return frozenset(SEARCH_TERM_PATTERN.findall(value.lower()))
