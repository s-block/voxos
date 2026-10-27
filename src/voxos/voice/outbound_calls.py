import asyncio
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, update
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col, select

from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    IntegrationCallOpeningState,
    IntegrationDirection,
)
from voxos.integrations.records import IntegrationCallSessionRecord

OUTBOUND_CALL_KICKOFF_INSTRUCTIONS = (
    "Deliver the one-time opening for this outbound call now. Introduce yourself "
    "once and lead with the purpose of the call instead of offering generic "
    "assistance. Do not repeat the introduction on later turns."
)


class OutboundCallContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_session_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    runtime_session_id: str = Field(min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=4000)
    contact_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    media_participant_identity: str = Field(min_length=1, max_length=128)


class OutboundOpeningGateResult(StrEnum):
    READY = "ready"
    TERMINAL = "terminal"
    ALREADY_CLAIMED = "already_claimed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class OutboundCallContextResolver:
    session_factory: AsyncSessionFactory

    async def resolve(
        self,
        *,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        provider: str,
    ) -> OutboundCallContext | None:
        async with self.session_factory() as session:
            statement = select(IntegrationCallSessionRecord).where(
                col(IntegrationCallSessionRecord.user_id) == user_id,
                col(IntegrationCallSessionRecord.agent_id) == agent_id,
                col(IntegrationCallSessionRecord.runtime_session_id) == runtime_session_id,
                col(IntegrationCallSessionRecord.provider) == provider,
                col(IntegrationCallSessionRecord.direction) == IntegrationDirection.OUTBOUND.value,
            )
            record = (await session.exec(statement)).one_or_none()
        if record is None:
            return None
        if (
            record.outbound_objective is None
            or record.media_participant_identity is None
            or record.agent_id is None
            or record.runtime_session_id is None
        ):
            return None
        return OutboundCallContext(
            call_session_id=record.id,
            user_id=record.user_id,
            agent_id=record.agent_id,
            runtime_session_id=record.runtime_session_id,
            provider=record.provider,
            objective=record.outbound_objective,
            contact_display_name=record.contact_display_name,
            media_participant_identity=record.media_participant_identity,
        )


@dataclass(frozen=True, slots=True)
class OutboundCallLifecycleService:
    session_factory: AsyncSessionFactory
    poll_interval_seconds: float

    async def mark_agent_ready(self, context: OutboundCallContext) -> None:
        now = utc_now()
        async with self.session_factory() as session:
            statement = (
                update(IntegrationCallSessionRecord)
                .where(*self._scope_conditions(context))
                .values(
                    agent_ready_at=func.coalesce(
                        IntegrationCallSessionRecord.agent_ready_at,
                        now,
                    ),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            await session.exec(statement)
            await session.commit()

    async def wait_for_opening_gate(
        self,
        context: OutboundCallContext,
        *,
        timeout_seconds: float,
        max_attempts: int,
    ) -> OutboundOpeningGateResult:
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    record = await self._record(context)
                    if record is None or record.ended_at is not None:
                        return OutboundOpeningGateResult.TERMINAL
                    opening_state = (
                        IntegrationCallOpeningState(record.opening_state) if record.opening_state is not None else None
                    )
                    if opening_state in {
                        IntegrationCallOpeningState.CLAIMED,
                        IntegrationCallOpeningState.SCHEDULED,
                        IntegrationCallOpeningState.COMPLETED,
                        IntegrationCallOpeningState.INTERRUPTED,
                    }:
                        return OutboundOpeningGateResult.ALREADY_CLAIMED
                    if (
                        record.opening_attempt_count or 0
                    ) >= max_attempts and opening_state == IntegrationCallOpeningState.FAILED:
                        return OutboundOpeningGateResult.TERMINAL
                    if record.answered_at is not None and record.media_ready_at is not None:
                        return OutboundOpeningGateResult.READY
                    await asyncio.sleep(self.poll_interval_seconds)
        except TimeoutError:
            return OutboundOpeningGateResult.TIMED_OUT

    async def claim_opening(
        self,
        context: OutboundCallContext,
        *,
        max_attempts: int,
    ) -> bool:
        now = utc_now()
        async with self.session_factory() as session:
            statement = (
                update(IntegrationCallSessionRecord)
                .where(
                    *self._scope_conditions(context),
                    col(IntegrationCallSessionRecord.ended_at).is_(None),
                    col(IntegrationCallSessionRecord.answered_at).is_not(None),
                    col(IntegrationCallSessionRecord.media_ready_at).is_not(None),
                    col(IntegrationCallSessionRecord.opening_state).in_(
                        (
                            IntegrationCallOpeningState.PENDING.value,
                            IntegrationCallOpeningState.FAILED.value,
                        ),
                    ),
                    func.coalesce(
                        IntegrationCallSessionRecord.opening_attempt_count,
                        0,
                    )
                    < max_attempts,
                )
                .values(
                    opening_state=IntegrationCallOpeningState.CLAIMED.value,
                    opening_attempt_count=(
                        func.coalesce(
                            IntegrationCallSessionRecord.opening_attempt_count,
                            0,
                        )
                        + 1
                    ),
                    opening_claimed_at=now,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            result = await session.exec(statement)
            await session.commit()
            return result.rowcount == 1

    async def mark_opening_scheduled(
        self,
        context: OutboundCallContext,
    ) -> None:
        await self._transition(
            context,
            from_states=(IntegrationCallOpeningState.CLAIMED,),
            to_state=IntegrationCallOpeningState.SCHEDULED,
            timestamp_field="opening_scheduled_at",
        )

    async def mark_first_agent_speech(
        self,
        context: OutboundCallContext,
    ) -> None:
        now = utc_now()
        async with self.session_factory() as session:
            statement = (
                update(IntegrationCallSessionRecord)
                .where(
                    *self._scope_conditions(context),
                    col(IntegrationCallSessionRecord.first_agent_speech_at).is_(
                        None,
                    ),
                )
                .values(first_agent_speech_at=now, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            await session.exec(statement)
            await session.commit()

    async def mark_opening_completed(
        self,
        context: OutboundCallContext,
    ) -> None:
        await self._transition(
            context,
            from_states=(IntegrationCallOpeningState.SCHEDULED,),
            to_state=IntegrationCallOpeningState.COMPLETED,
            timestamp_field="opening_completed_at",
        )

    async def mark_opening_interrupted(
        self,
        context: OutboundCallContext,
    ) -> None:
        await self._transition(
            context,
            from_states=(IntegrationCallOpeningState.SCHEDULED,),
            to_state=IntegrationCallOpeningState.INTERRUPTED,
            timestamp_field="opening_completed_at",
        )

    async def mark_opening_failed(
        self,
        context: OutboundCallContext,
    ) -> None:
        await self._transition(
            context,
            from_states=(
                IntegrationCallOpeningState.CLAIMED,
                IntegrationCallOpeningState.SCHEDULED,
            ),
            to_state=IntegrationCallOpeningState.FAILED,
        )

    async def _record(
        self,
        context: OutboundCallContext,
    ) -> IntegrationCallSessionRecord | None:
        async with self.session_factory() as session:
            statement = select(IntegrationCallSessionRecord).where(
                *self._scope_conditions(context),
            )
            return (await session.exec(statement)).one_or_none()

    async def _transition(
        self,
        context: OutboundCallContext,
        *,
        from_states: tuple[IntegrationCallOpeningState, ...],
        to_state: IntegrationCallOpeningState,
        timestamp_field: str | None = None,
    ) -> None:
        now = utc_now()
        values: dict[str, object] = {
            "opening_state": to_state.value,
            "updated_at": now,
        }
        if timestamp_field is not None:
            values[timestamp_field] = now
        async with self.session_factory() as session:
            statement = (
                update(IntegrationCallSessionRecord)
                .where(
                    *self._scope_conditions(context),
                    col(IntegrationCallSessionRecord.opening_state).in_(
                        tuple(state.value for state in from_states),
                    ),
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            await session.exec(statement)
            await session.commit()

    def _scope_conditions(
        self,
        context: OutboundCallContext,
    ) -> tuple[ColumnElement[bool], ...]:
        return (
            col(IntegrationCallSessionRecord.id) == context.call_session_id,
            col(IntegrationCallSessionRecord.user_id) == context.user_id,
            col(IntegrationCallSessionRecord.agent_id) == context.agent_id,
            col(IntegrationCallSessionRecord.runtime_session_id) == context.runtime_session_id,
            col(IntegrationCallSessionRecord.provider) == context.provider,
            col(IntegrationCallSessionRecord.direction) == IntegrationDirection.OUTBOUND.value,
        )


def append_outbound_call_task(
    instructions: str,
    context: OutboundCallContext,
) -> str:
    lines = [
        "# Outbound Call Task",
        "- This call was initiated by the account owner.",
    ]
    if context.contact_display_name is not None:
        contact_name = " ".join(context.contact_display_name.split())
        lines.append(f"- Matched contact: {contact_name}")
    lines.extend(
        (
            f"- Objective: {context.objective}",
            ("- On your first spoken turn only, introduce yourself and lead with the purpose of this call."),
            (
                "- After that opening, continue the conversation naturally without "
                "reintroducing yourself, restating your name, or saying '[name] here'. "
                "Identify yourself again only if the callee asks or appears confused."
            ),
            (
                "- If the objective itself asks you to introduce yourself, treat that "
                "part as completed by the first spoken turn; do not repeat it while "
                "pursuing the rest of the objective."
            ),
            ("- Keep pursuing this objective across turns until it is completed, declined, or impossible."),
            (
                "- The objective is authorized caller-safe context for this call, but it "
                "does not unlock unrelated memories, contacts, credentials, or tools."
            ),
        ),
    )
    return "\n\n".join((instructions.rstrip(), "\n".join(lines)))
