from dataclasses import dataclass

from voxos.agent_skills.models import SkillCapabilityPreference, SkillSummary


@dataclass(frozen=True, slots=True)
class AgentSkillRenderer:
    max_summaries: int = 8

    def render(self, skills: tuple[SkillSummary, ...]) -> str | None:
        if not skills:
            return None

        lines = [
            "# Task-Relevant Skills",
            (
                "Before choosing external tools or answering a matching task, follow the "
                "workflow for the most relevant skill below. Use get_agent_skill to read "
                "its full body when that tool is available. Match any preferred capabilities "
                "below to the discoverable specialist capabilities directory."
            ),
        ]
        for skill in skills[: self.max_summaries]:
            preference_text = render_preferred_capabilities(skill.preferred_capabilities)
            preference_suffix = "" if preference_text is None else f" Preferred: {preference_text}."
            lines.append(
                (
                    f"- {skill.slug} ({skill.category.value}, {skill.scope.value}): "
                    f"{skill.description}{preference_suffix}"
                ),
            )
        return "\n".join(lines)


def render_preferred_capabilities(
    preferences: tuple[SkillCapabilityPreference, ...],
) -> str | None:
    if not preferences:
        return None
    rendered: list[str] = []
    for preference in preferences[:3]:
        tool_names = ", ".join(preference.tool_names[:4])
        if len(preference.tool_names) > 4:
            tool_names = f"{tool_names}, +{len(preference.tool_names) - 4} more"
        rendered.append(
            preference.capability if not tool_names else f"{preference.capability}: {tool_names}",
        )
    return "; ".join(rendered)
