import pytest
from examples.skills.scripts.example_catalog import ExampleSkillCatalog
from pydantic import ValidationError

from voxos.agent_skills.models import (
    SkillCapabilityPreference,
    SkillCreateRequest,
    SkillDocument,
    SkillRenderContext,
    SkillScope,
    SkillStatus,
)
from voxos.agent_skills.services import (
    AgentSkillAlreadyExistsError,
    AgentSkillService,
    _filter_skills,
)
from voxos.db.session import AsyncSessionFactory


def test_skill_capability_preference_normalizes_and_validates_values() -> None:
    preference = SkillCapabilityPreference(
        capability=" Google Maps ",
        tool_names=(" search_places ", "search_places"),
    )

    assert preference.capability == "Google Maps"
    assert preference.tool_names == ("search_places",)
    with pytest.raises(ValidationError):
        SkillCapabilityPreference(capability=" ")


@pytest.mark.asyncio
async def test_skill_service_lists_developer_and_user_skills_without_leaking_users(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentSkillService(sqlite_session_factory)
    created = await service.create_user_skill(
        user_id="user-a",
        request=SkillCreateRequest(
            name="Deployment Notes",
            slug="deployment-notes",
            description="Remember deployment review steps.",
            body_markdown="Check migration safety before deploy commands.",
            preferred_capabilities=(
                SkillCapabilityPreference(
                    capability="github",
                    tool_names=("pull_request_read",),
                ),
            ),
        ),
    )

    user_a_skills = await service.list_relevant_skills(user_id="user-a")
    user_b_skills = await service.list_relevant_skills(user_id="user-b")

    assert created.status == SkillStatus.ACTIVE
    assert created.preferred_capabilities[0].capability == "github"
    assert created.preferred_capabilities[0].tool_names == ("pull_request_read",)
    assert "deployment-notes" in {skill.slug for skill in user_a_skills}
    assert "deployment-notes" not in {skill.slug for skill in user_b_skills}
    builtin_slugs = {skill.slug for skill in user_b_skills}
    assert "browser-use" in builtin_slugs
    assert "voice-session-personalization" in builtin_slugs


@pytest.mark.asyncio
async def test_skill_service_raises_typed_conflict_for_existing_slug(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentSkillService(sqlite_session_factory)
    request = SkillCreateRequest(
        name="Deployment Notes",
        slug="deployment-notes",
        description="Remember deployment review steps.",
        body_markdown="Check migration safety before deploy commands.",
    )
    await service.create_user_skill(user_id="user-a", request=request)

    with pytest.raises(AgentSkillAlreadyExistsError) as error:
        await service.create_user_skill(user_id="user-a", request=request)

    assert error.value.slug == "deployment-notes"


@pytest.mark.asyncio
async def test_relevance_ranking_surfaces_each_curated_example(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentSkillService(sqlite_session_factory)
    for definition in ExampleSkillCatalog.load().definitions:
        await service.create_user_skill(
            user_id="user-a",
            request=definition.request,
        )

    prompt_matrix = (
        (
            "research current evidence and compare authoritative web sources",
            "evidence-backed-web-research",
        ),
        (
            "audit this website for content accessibility SEO and performance",
            "website-content-performance-audit",
        ),
        (
            "help me understand this GitHub repository architecture",
            "github-repository-orientation",
        ),
        (
            "review this PR for correctness regressions and missing tests",
            "github-pull-request-review",
        ),
        (
            "make a local day itinerary with places weather and walking routes",
            "grounded-local-day-plan",
        ),
        (
            "plan a public transport journey with departures and transfers",
            "multimodal-journey-plan",
        ),
        (
            "brief me on live flight activity around this airport",
            "flight-activity-brief",
        ),
        (
            "shortlist vacation rental accommodation near the wedding venue",
            "accommodation-shortlist",
        ),
        (
            "triage my Gmail inbox and prepare reply drafts",
            "gmail-inbox-triage-and-drafting",
        ),
        (
            "prepare a brief for tomorrow's meeting using calendar and email",
            "meeting-preparation-brief",
        ),
        (
            "review my personal spending and transactions from last month",
            "personal-spending-review",
        ),
    )

    for query, expected_slug in prompt_matrix:
        skills = await service.list_relevant_skills(
            user_id="user-a",
            context=SkillRenderContext(query=query),
        )
        assert skills
        assert skills[0].slug == expected_slug
        assert expected_slug in {skill.slug for skill in skills[:8]}


@pytest.mark.asyncio
async def test_relevance_filter_ignores_stop_word_only_queries(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    service = AgentSkillService(sqlite_session_factory)
    await service.create_user_skill(
        user_id="user-a",
        request=SkillCreateRequest(
            name="Common Words",
            slug="common-words",
            description="Use this when the user asks you for help.",
            body_markdown="The body contains many common connective words.",
        ),
    )

    skills = await service.list_relevant_skills(
        user_id="user-a",
        context=SkillRenderContext(query="please help me use the thing"),
    )

    assert skills == ()


def test_relevance_filter_retains_short_acronyms_and_stable_ties() -> None:
    first = _active_skill(
        slug="first",
        description="Review PR and CI evidence.",
    )
    second = _active_skill(
        slug="second",
        description="Review PR and CI evidence.",
    )

    matched = _filter_skills((first, second), "PR CI")

    assert matched == (first, second)


def test_relevance_filter_matches_prefixes_and_exact_short_names() -> None:
    deployment = _active_skill(
        slug="deployment-guide",
        description="Production rollout workflow.",
    )
    go = _active_skill(
        slug="go-development",
        description="Compiled service development.",
    )

    assert _filter_skills((deployment, go), "deploy") == (deployment,)
    assert _filter_skills((deployment, go), "Go") == (go,)


def test_relevance_filter_preserves_active_order_without_query() -> None:
    first = _active_skill(slug="first", description="First.")
    archived = first.model_copy(
        update={"id": "archived", "slug": "archived", "status": SkillStatus.ARCHIVED},
    )
    second = _active_skill(slug="second", description="Second.")

    assert _filter_skills((first, archived, second), None) == (first, second)


def _active_skill(*, slug: str, description: str) -> SkillDocument:
    return SkillDocument(
        id=slug,
        scope=SkillScope.USER,
        owner_user_id="user-a",
        name=slug.title(),
        slug=slug,
        status=SkillStatus.ACTIVE,
        description=description,
        body_markdown=description,
    )
