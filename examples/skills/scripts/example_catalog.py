import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from voxos.agent_skills.models import (
    SkillCapabilityPreference,
    SkillCategory,
    SkillCreateRequest,
)

DEFAULT_EXAMPLE_SKILLS_PATH = Path(__file__).resolve().parent.parent
CATALOG_FILENAME = "catalog.toml"
SELECTOR_PART_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKILL_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ExampleSkillCatalogError(ValueError):
    """Raised when the packaged example-skill catalog is invalid."""


class ExampleSkillSelectionError(ValueError):
    """Raised when a requested MCP group has no curated examples."""


class McpExampleSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preset_id: str = Field(min_length=1, max_length=128)
    preset_variant_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("preset_id", "preset_variant_id", mode="before")
    @classmethod
    def normalize_part(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if not SELECTOR_PART_PATTERN.fullmatch(normalized):
            message = "MCP selector parts must use lowercase letters, digits, and hyphens"
            raise ValueError(message)
        return normalized

    @property
    def canonical_name(self) -> str:
        if self.preset_variant_id is None:
            return self.preset_id
        return f"{self.preset_id}:{self.preset_variant_id}"

    @classmethod
    def parse(cls, value: str) -> "McpExampleSelector":
        normalized = value.strip().lower()
        if not normalized:
            message = "MCP selector must not be empty"
            raise ExampleSkillSelectionError(message)
        preset_id, separator, preset_variant_id = normalized.partition(":")
        if separator and (not preset_variant_id or ":" in preset_variant_id):
            message = f"invalid MCP selector: {value}"
            raise ExampleSkillSelectionError(message)
        try:
            return cls(
                preset_id=preset_id,
                preset_variant_id=preset_variant_id if separator else None,
            )
        except ValidationError as error:
            message = f"invalid MCP selector: {value}"
            raise ExampleSkillSelectionError(message) from error

    def matches(
        self,
        *,
        preset_id: str | None,
        preset_variant_id: str | None,
    ) -> bool:
        if preset_id != self.preset_id:
            return False
        return self.preset_variant_id is None or preset_variant_id == self.preset_variant_id


class ExampleSkillRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selector: McpExampleSelector
    tools: tuple[str, ...] = Field(min_length=1)

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(tool.strip() for tool in value)
        if any(not tool for tool in normalized):
            message = "required MCP tool names must not be empty"
            raise ValueError(message)
        if len(set(normalized)) != len(normalized):
            message = "required MCP tool names must be unique"
            raise ValueError(message)
        return normalized


class ExampleSkillManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=128)
    category: SkillCategory = SkillCategory.TOOL_USE
    description: str = Field(min_length=1, max_length=512)
    body_path: str = Field(min_length=1, max_length=256)
    load_selector: McpExampleSelector
    requirements: tuple[ExampleSkillRequirement, ...] = Field(min_length=1)

    @field_validator("name", "slug", "description", "body_path")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not SKILL_SLUG_PATTERN.fullmatch(value):
            message = "example skill slugs must use lowercase letters, digits, and hyphens"
            raise ValueError(message)
        return value


class ExampleSkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skills: tuple[ExampleSkillManifestEntry, ...] = Field(min_length=1)


class ExampleSkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request: SkillCreateRequest
    body_path: str = Field(min_length=1, max_length=256)
    load_selector: McpExampleSelector
    requirements: tuple[ExampleSkillRequirement, ...] = Field(min_length=1)

    @property
    def slug(self) -> str:
        if self.request.slug is None:
            message = "example skill definitions must have an explicit slug"
            raise ExampleSkillCatalogError(message)
        return self.request.slug


@dataclass(frozen=True, slots=True)
class ExampleSkillCatalog:
    definitions: tuple[ExampleSkillDefinition, ...]
    root_path: Path = DEFAULT_EXAMPLE_SKILLS_PATH

    @classmethod
    def load(
        cls,
        root_path: Path = DEFAULT_EXAMPLE_SKILLS_PATH,
    ) -> "ExampleSkillCatalog":
        resolved_root = root_path.resolve()
        catalog_path = resolved_root / CATALOG_FILENAME
        try:
            payload = tomllib.loads(catalog_path.read_text(encoding="utf-8"))
            manifest = ExampleSkillManifest.model_validate(payload)
            definitions = _definitions_from_manifest(
                manifest=manifest,
                root_path=resolved_root,
            )
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
            message = f"invalid example skill catalog: {error}"
            raise ExampleSkillCatalogError(message) from error
        return cls(definitions=definitions, root_path=resolved_root)

    def list_groups(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {definition.load_selector.canonical_name for definition in self.definitions},
            ),
        )

    def resolve_groups(self, requested: tuple[str, ...]) -> tuple[str, ...]:
        if not requested:
            message = "at least one MCP selector is required"
            raise ExampleSkillSelectionError(message)

        known_groups = self.list_groups()
        resolved: list[str] = []
        for raw_selector in requested:
            selector = McpExampleSelector.parse(raw_selector)
            canonical_name = selector.canonical_name
            matches: tuple[str, ...]
            if canonical_name in known_groups:
                matches = (canonical_name,)
            elif selector.preset_variant_id is None:
                matches = tuple(
                    group for group in known_groups if McpExampleSelector.parse(group).preset_id == selector.preset_id
                )
            else:
                matches = ()
            if not matches:
                message = (
                    f"no curated example skills are available for MCP selector "
                    f"'{canonical_name}'; use --list to see valid selectors"
                )
                raise ExampleSkillSelectionError(message)
            for match in matches:
                if match not in resolved:
                    resolved.append(match)
        return tuple(resolved)

    def definitions_for_groups(
        self,
        groups: tuple[str, ...],
    ) -> tuple[ExampleSkillDefinition, ...]:
        selected = set(groups)
        return tuple(
            definition for definition in self.definitions if definition.load_selector.canonical_name in selected
        )

    def group_skills(self) -> dict[str, tuple[str, ...]]:
        return {
            group: tuple(
                definition.slug for definition in self.definitions if definition.load_selector.canonical_name == group
            )
            for group in self.list_groups()
        }


def parse_mcp_group_list(value: str) -> tuple[str, ...]:
    groups = tuple(dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip()))
    if not groups:
        message = "at least one MCP selector is required"
        raise ExampleSkillSelectionError(message)
    return groups


def _definitions_from_manifest(
    *,
    manifest: ExampleSkillManifest,
    root_path: Path,
) -> tuple[ExampleSkillDefinition, ...]:
    definitions: list[ExampleSkillDefinition] = []
    slugs: set[str] = set()
    body_paths: set[Path] = set()
    for entry in manifest.skills:
        if entry.slug in slugs:
            message = f"duplicate example skill slug: {entry.slug}"
            raise ExampleSkillCatalogError(message)
        body_path = _resolved_body_path(root_path=root_path, relative_path=entry.body_path)
        if body_path in body_paths:
            message = f"duplicate example skill body path: {entry.body_path}"
            raise ExampleSkillCatalogError(message)
        if not any(
            requirement.selector.canonical_name == entry.load_selector.canonical_name
            for requirement in entry.requirements
        ):
            message = f"primary load selector is not represented by a requirement: {entry.load_selector.canonical_name}"
            raise ExampleSkillCatalogError(message)
        try:
            body_markdown = body_path.read_text(encoding="utf-8").strip()
            request = SkillCreateRequest(
                name=entry.name,
                slug=entry.slug,
                category=entry.category,
                description=entry.description,
                body_markdown=body_markdown,
                preferred_capabilities=tuple(
                    SkillCapabilityPreference(
                        capability=requirement.selector.canonical_name,
                        tool_names=requirement.tools,
                    )
                    for requirement in entry.requirements
                ),
                confirmed=True,
            )
        except (OSError, ValidationError) as error:
            message = f"invalid example skill '{entry.slug}': {error}"
            raise ExampleSkillCatalogError(message) from error
        slugs.add(entry.slug)
        body_paths.add(body_path)
        definitions.append(
            ExampleSkillDefinition(
                request=request,
                body_path=entry.body_path,
                load_selector=entry.load_selector,
                requirements=entry.requirements,
            ),
        )
    return tuple(definitions)


def _resolved_body_path(*, root_path: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or candidate.suffix != ".md":
        message = f"example skill body must be a relative Markdown path: {relative_path}"
        raise ExampleSkillCatalogError(message)
    resolved = (root_path / candidate).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as error:
        message = f"example skill body escapes the catalog directory: {relative_path}"
        raise ExampleSkillCatalogError(message) from error
    if not resolved.is_file():
        message = f"example skill body does not exist: {relative_path}"
        raise ExampleSkillCatalogError(message)
    return resolved
