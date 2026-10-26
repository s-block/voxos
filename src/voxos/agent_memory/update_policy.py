import re
from dataclasses import dataclass

from voxos.agent_memory.models import (
    MemoryCreateRequest,
    MemoryDecision,
    MemoryKind,
    MemorySensitivity,
    MemorySource,
    MemoryStatus,
)

AUTO_ACTIVE_KINDS = frozenset(
    (
        MemoryKind.PREFERENCE,
        MemoryKind.FACT,
        MemoryKind.PROJECT,
        MemoryKind.TASK_CONTEXT,
    ),
)
SENSITIVE_TERMS = frozenset(
    (
        "api key",
        "password",
        "credential",
        "secret",
        "bank",
        "legal",
        "medical",
        "health",
        "child",
        "biometric",
        "ssn",
    ),
)


@dataclass(frozen=True, slots=True)
class MemoryUpdatePolicy:
    def decide_create(
        self,
        request: MemoryCreateRequest,
        *,
        has_active_conflict: bool,
    ) -> MemoryDecision:
        if request.confirmed:
            return MemoryDecision(
                status=MemoryStatus.ACTIVE,
                requires_confirmation=False,
                reason="The user confirmed this memory update.",
            )
        if has_active_conflict:
            return MemoryDecision(
                status=MemoryStatus.PROPOSED,
                requires_confirmation=True,
                reason="An active memory with the same normalized key already exists.",
            )
        if request.sensitivity != MemorySensitivity.LOW:
            return MemoryDecision(
                status=MemoryStatus.PROPOSED,
                requires_confirmation=True,
                reason="Personal or sensitive memories require confirmation.",
            )
        if request.source != MemorySource.USER_EXPLICIT:
            return MemoryDecision(
                status=MemoryStatus.PROPOSED,
                requires_confirmation=True,
                reason="Inferred or tool-created memories require confirmation.",
            )
        if request.kind not in AUTO_ACTIVE_KINDS:
            return MemoryDecision(
                status=MemoryStatus.PROPOSED,
                requires_confirmation=True,
                reason="Instructional or relationship memories require confirmation.",
            )
        if request.confidence < 0.9:
            return MemoryDecision(
                status=MemoryStatus.PROPOSED,
                requires_confirmation=True,
                reason="Low-confidence memories require confirmation.",
            )
        if contains_sensitive_term(request.text):
            return MemoryDecision(
                status=MemoryStatus.PROPOSED,
                requires_confirmation=True,
                reason="Potentially sensitive text requires confirmation.",
            )
        return MemoryDecision(
            status=MemoryStatus.ACTIVE,
            requires_confirmation=False,
            reason="Explicit low-risk user memory can be activated immediately.",
        )


def contains_sensitive_term(text: str) -> bool:
    lowered_text = text.lower()
    return (
        any(term in lowered_text for term in SENSITIVE_TERMS) or re.search(r"\b\d{3}-\d{2}-\d{4}\b", text) is not None
    )
