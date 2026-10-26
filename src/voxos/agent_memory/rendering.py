from dataclasses import dataclass

from voxos.agent_memory.models import AgentMemory


@dataclass(frozen=True, slots=True)
class AgentMemoryRenderer:
    max_items: int = 12
    max_chars_per_memory: int = 220

    def render(self, memories: tuple[AgentMemory, ...]) -> str | None:
        if not memories:
            return None

        lines = ["# Long-term User Memory"]
        for memory in memories[: self.max_items]:
            text = _truncate(memory.text, self.max_chars_per_memory)
            lines.append(f"- ({memory.kind.value}) {text}")
        return "\n".join(lines)


def _truncate(text: str, max_chars: int) -> str:
    stripped_text = " ".join(text.split())
    if len(stripped_text) <= max_chars:
        return stripped_text
    return f"{stripped_text[: max_chars - 1].rstrip()}..."
