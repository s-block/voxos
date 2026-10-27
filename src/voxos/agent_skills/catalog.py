from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_skills.models import (
    SkillCategory,
    SkillDocument,
    SkillScope,
    SkillStatus,
)

DEFAULT_BUILTIN_SKILLS_PATH = Path(__file__).with_name("builtins")


class BuiltinSkillFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=128)
    category: SkillCategory = SkillCategory.GENERAL
    description: str = Field(min_length=1, max_length=512)


@dataclass(frozen=True, slots=True)
class BuiltinSkillCatalog:
    root_path: Path = DEFAULT_BUILTIN_SKILLS_PATH

    def list_skills(self) -> tuple[SkillDocument, ...]:
        if not self.root_path.exists():
            return ()
        documents = [self._load_skill(path) for path in sorted(self.root_path.glob("*.md")) if path.is_file()]
        return tuple(document for document in documents if document is not None)

    def get_skill(self, slug: str) -> SkillDocument | None:
        for skill in self.list_skills():
            if skill.slug == slug:
                return skill
        return None

    def _load_skill(self, path: Path) -> SkillDocument | None:
        content = path.read_text(encoding="utf-8").strip()
        if not content.startswith("---"):
            return None
        frontmatter_text, body = _split_frontmatter(content)
        frontmatter = BuiltinSkillFrontmatter.model_validate(
            _frontmatter_mapping(frontmatter_text),
        )
        return SkillDocument(
            id=f"developer:{frontmatter.slug}",
            scope=SkillScope.DEVELOPER,
            owner_user_id=None,
            name=frontmatter.name,
            slug=frontmatter.slug,
            category=frontmatter.category,
            status=SkillStatus.ACTIVE,
            description=frontmatter.description,
            body_markdown=body.strip(),
        )


def _split_frontmatter(content: str) -> tuple[str, str]:
    parts = content.split("---", 2)
    if len(parts) != 3:
        message = "built-in skill file must use frontmatter delimiters"
        raise ValueError(message)
    return parts[1].strip(), parts[2].strip()


def _frontmatter_mapping(frontmatter: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        values[key.strip()] = value.strip()
    return values
