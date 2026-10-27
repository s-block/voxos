from pathlib import Path

import pytest
from examples.skills.scripts.example_catalog import (
    DEFAULT_EXAMPLE_SKILLS_PATH,
    ExampleSkillCatalog,
    ExampleSkillCatalogError,
    ExampleSkillSelectionError,
    McpExampleSelector,
    parse_mcp_group_list,
)

from voxos.agent_skills.models import SkillCategory
from voxos.mcp.presets import (
    find_mcp_service_preset,
    find_mcp_service_preset_variant,
)

REQUIRED_WORKFLOW_SECTIONS = (
    "## Use when / do not use when",
    "## Inputs to resolve",
    "## Tool workflow",
    "## Validation and fallback rules",
    "## Output contract",
    "## Safety and side effects",
)


def test_repository_catalog_loads_all_curated_definitions() -> None:
    catalog = ExampleSkillCatalog.load()

    assert len(catalog.definitions) == 11
    assert len({definition.slug for definition in catalog.definitions}) == 11
    assert all(definition.request.category == SkillCategory.TOOL_USE for definition in catalog.definitions)
    assert all(0 < len(definition.request.body_markdown) <= 12_000 for definition in catalog.definitions)
    for definition in catalog.definitions:
        assert tuple(
            (preference.capability, preference.tool_names) for preference in definition.request.preferred_capabilities
        ) == tuple((requirement.selector.canonical_name, requirement.tools) for requirement in definition.requirements)
    assert catalog.group_skills()["github"] == (
        "github-repository-orientation",
        "github-pull-request-review",
    )
    assert catalog.group_skills()["openbnb"] == ("accommodation-shortlist",)


def test_every_example_has_a_complete_workflow_and_names_its_required_tools() -> None:
    catalog = ExampleSkillCatalog.load()

    for definition in catalog.definitions:
        body = definition.request.body_markdown
        for section in REQUIRED_WORKFLOW_SECTIONS:
            assert section in body, f"{definition.slug} is missing {section}"
        for requirement in definition.requirements:
            for tool in requirement.tools:
                assert f"`{tool}`" in body, f"{definition.slug} does not document its required tool {tool}"


def test_catalog_data_lives_in_the_repository_examples_tree() -> None:
    assert DEFAULT_EXAMPLE_SKILLS_PATH.name == "skills"
    assert DEFAULT_EXAMPLE_SKILLS_PATH.parent.name == "examples"
    assert DEFAULT_EXAMPLE_SKILLS_PATH.joinpath("catalog.toml").is_file()
    assert DEFAULT_EXAMPLE_SKILLS_PATH.joinpath("README.md").is_file()
    assert DEFAULT_EXAMPLE_SKILLS_PATH.joinpath(
        "accommodation-shortlist.md",
    ).is_file()


def test_readme_documents_every_curated_skill_and_group() -> None:
    catalog = ExampleSkillCatalog.load()
    readme = DEFAULT_EXAMPLE_SKILLS_PATH.joinpath("README.md").read_text(
        encoding="utf-8",
    )

    for definition in catalog.definitions:
        assert f"`{definition.slug}`" in readme
    for group in catalog.list_groups():
        assert f"`{group}`" in readme


def test_catalog_resolves_exact_union_and_bare_preset_groups() -> None:
    catalog = ExampleSkillCatalog.load()

    assert catalog.resolve_groups(("openbnb", "github", "openbnb")) == (
        "openbnb",
        "github",
    )
    assert catalog.resolve_groups(("google-workspace",)) == (
        "google-workspace:calendar",
        "google-workspace:gmail",
    )
    assert tuple(definition.slug for definition in catalog.definitions_for_groups(("openbnb",))) == (
        "accommodation-shortlist",
    )


def test_google_workspace_requirements_use_advertised_preset_tool_names() -> None:
    catalog = ExampleSkillCatalog.load()
    preset = find_mcp_service_preset("google-workspace")

    assert preset is not None
    for definition in catalog.definitions:
        for requirement in definition.requirements:
            if requirement.selector.preset_id != preset.id:
                continue
            variant = find_mcp_service_preset_variant(
                preset,
                requirement.selector.preset_variant_id,
            )
            assert variant is not None
            advertised_tools = {
                *variant.starter_allowed_tools,
                *variant.risky_tools,
            }
            assert set(requirement.tools) <= advertised_tools


def test_selector_parsing_is_canonical_and_rejects_unknown_groups() -> None:
    selector = McpExampleSelector.parse(" Google-Workspace:Gmail ")

    assert selector.preset_id == "google-workspace"
    assert selector.preset_variant_id == "gmail"
    assert selector.canonical_name == "google-workspace:gmail"
    assert parse_mcp_group_list("github, openbnb,github") == ("github", "openbnb")

    with pytest.raises(ExampleSkillSelectionError, match="no curated example"):
        ExampleSkillCatalog.load().resolve_groups(("not-configured",))


def test_catalog_rejects_duplicate_slugs_before_use(
    tmp_path: Path,
) -> None:
    (tmp_path / "body-one.md").write_text("One", encoding="utf-8")
    (tmp_path / "body-two.md").write_text("Two", encoding="utf-8")
    (tmp_path / "catalog.toml").write_text(
        """
[[skills]]
name = "One"
slug = "duplicate"
description = "First duplicate."
body_path = "body-one.md"
load_selector = { preset_id = "exa" }
[[skills.requirements]]
selector = { preset_id = "exa" }
tools = ["search"]

[[skills]]
name = "Two"
slug = "duplicate"
description = "Second duplicate."
body_path = "body-two.md"
load_selector = { preset_id = "exa" }
[[skills.requirements]]
selector = { preset_id = "exa" }
tools = ["search"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ExampleSkillCatalogError, match="duplicate.*slug"):
        ExampleSkillCatalog.load(tmp_path)


def test_catalog_rejects_unsafe_body_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "catalog.toml").write_text(
        """
[[skills]]
name = "Unsafe"
slug = "unsafe"
description = "Unsafe body path."
body_path = "../outside.md"
load_selector = { preset_id = "exa" }
[[skills.requirements]]
selector = { preset_id = "exa" }
tools = ["search"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ExampleSkillCatalogError, match="escapes"):
        ExampleSkillCatalog.load(tmp_path)
