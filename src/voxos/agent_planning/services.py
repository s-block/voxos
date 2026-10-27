from dataclasses import dataclass

from sqlalchemy import delete
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_planning.models import (
    TaskPlan,
    TaskPlanCreateRequest,
    TaskPlanUpdateRequest,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskStep,
    TaskStepUpdateRequest,
)
from voxos.agent_planning.records import (
    AgentTaskEventRecord,
    AgentTaskPlanRecord,
    AgentTaskStepRecord,
)
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory


@dataclass(frozen=True, slots=True)
class AgentPlanningService:
    session_factory: AsyncSessionFactory

    async def create_plan(
        self,
        *,
        user_id: str,
        request: TaskPlanCreateRequest,
    ) -> TaskPlan:
        plan_record = AgentTaskPlanRecord(
            user_id=user_id,
            session_id=request.session_id,
            parent_task_id=request.parent_task_id,
            objective=request.objective,
            acceptance_criteria=list(request.acceptance_criteria),
            source=request.source.value,
            priority=request.priority.value,
            status=request.status.value,
            allow_parallel_steps=request.allow_parallel_steps,
        )
        step_records = tuple(
            AgentTaskStepRecord(
                plan_id=plan_record.id,
                user_id=user_id,
                position=index,
                title=step,
                status=TaskStatus.ACTIVE.value,
            )
            for index, step in enumerate(request.steps, start=1)
        )
        async with self.session_factory() as session:
            session.add(plan_record)
            session.add_all(step_records)
            session.add(
                AgentTaskEventRecord(
                    plan_id=plan_record.id,
                    user_id=user_id,
                    event_type="created",
                    note=request.objective,
                ),
            )
            await session.commit()
            await session.refresh(plan_record)
            for step_record in step_records:
                await session.refresh(step_record)
        return plan_from_records(plan_record, step_records)

    async def get_plan(self, *, user_id: str, plan_id: str) -> TaskPlan | None:
        async with self.session_factory() as session:
            return await self._load_plan_in_session(
                session=session,
                user_id=user_id,
                plan_id=plan_id,
            )

    async def list_plans(
        self,
        *,
        user_id: str,
        statuses: tuple[TaskStatus, ...] = (
            TaskStatus.PROPOSED,
            TaskStatus.ACTIVE,
            TaskStatus.IN_PROGRESS,
            TaskStatus.BLOCKED,
        ),
        limit: int = 20,
    ) -> tuple[TaskPlan, ...]:
        statement = (
            select(AgentTaskPlanRecord)
            .where(col(AgentTaskPlanRecord.user_id) == user_id)
            .order_by(col(AgentTaskPlanRecord.updated_at).desc())
            .limit(limit)
        )
        if statuses:
            statement = statement.where(
                col(AgentTaskPlanRecord.status).in_(
                    tuple(status.value for status in statuses),
                ),
            )

        async with self.session_factory() as session:
            result = await session.exec(statement)
            plans: list[TaskPlan] = []
            for plan_record in result.all():
                plan = await self._load_plan_in_session(
                    session=session,
                    user_id=user_id,
                    plan_id=plan_record.id,
                )
                if plan is not None:
                    plans.append(plan)
            return tuple(plans)

    async def update_plan(
        self,
        *,
        user_id: str,
        plan_id: str,
        request: TaskPlanUpdateRequest,
    ) -> TaskPlan | None:
        async with self.session_factory() as session:
            plan = await session.get(AgentTaskPlanRecord, plan_id)
            if plan is None or plan.user_id != user_id:
                return None

            if request.objective is not None:
                plan.objective = request.objective
            if request.acceptance_criteria is not None:
                plan.acceptance_criteria = list(request.acceptance_criteria)
            if request.priority is not None:
                plan.priority = request.priority.value
            if request.status is not None:
                plan.status = request.status.value
            if request.allow_parallel_steps is not None:
                plan.allow_parallel_steps = request.allow_parallel_steps
            plan.updated_at = utc_now()
            session.add(
                AgentTaskEventRecord(
                    plan_id=plan.id,
                    user_id=user_id,
                    event_type="updated",
                    note=request.objective,
                ),
            )
            await session.commit()
            return await self._load_plan_in_session(
                session=session,
                user_id=user_id,
                plan_id=plan.id,
            )

    async def archive_plan(self, *, user_id: str, plan_id: str) -> TaskPlan | None:
        return await self.update_plan(
            user_id=user_id,
            plan_id=plan_id,
            request=TaskPlanUpdateRequest(status=TaskStatus.ARCHIVED),
        )

    async def delete_plan(self, *, user_id: str, plan_id: str) -> TaskPlan | None:
        async with self.session_factory() as session:
            plan = await session.get(AgentTaskPlanRecord, plan_id)
            if plan is None or plan.user_id != user_id:
                return None
            steps = await self._step_records(session=session, plan_id=plan.id)
            deleted = plan_from_records(plan, steps)
            await session.exec(
                delete(AgentTaskEventRecord).where(
                    col(AgentTaskEventRecord.plan_id) == plan.id,
                    col(AgentTaskEventRecord.user_id) == user_id,
                ),
            )
            await session.exec(
                delete(AgentTaskStepRecord).where(
                    col(AgentTaskStepRecord.plan_id) == plan.id,
                    col(AgentTaskStepRecord.user_id) == user_id,
                ),
            )
            await session.delete(plan)
            await session.commit()
        return deleted

    async def update_step(
        self,
        *,
        user_id: str,
        request: TaskStepUpdateRequest,
    ) -> TaskPlan | None:
        async with self.session_factory() as session:
            step = await session.get(AgentTaskStepRecord, request.step_id)
            if step is None or step.user_id != user_id:
                return None
            plan = await session.get(AgentTaskPlanRecord, step.plan_id)
            if plan is None or plan.user_id != user_id:
                return None
            if (
                request.status == TaskStatus.IN_PROGRESS
                and not plan.allow_parallel_steps
                and await self._has_other_in_progress_step(
                    session=session,
                    plan_id=plan.id,
                    step_id=step.id,
                )
            ):
                message = "plan already has an in-progress step"
                raise ValueError(message)

            step.status = request.status.value
            step.updated_at = utc_now()
            step.evidence_session_id = request.evidence_session_id
            plan.updated_at = utc_now()
            if await self._all_steps_completed(session=session, plan_id=plan.id, step=step):
                plan.status = TaskStatus.COMPLETED.value
            session.add(
                AgentTaskEventRecord(
                    plan_id=plan.id,
                    step_id=step.id,
                    user_id=user_id,
                    event_type=f"step_{request.status.value}",
                    note=request.note,
                ),
            )
            await session.commit()
            return await self._load_plan_in_session(
                session=session,
                user_id=user_id,
                plan_id=plan.id,
            )

    async def list_active_plans(
        self,
        *,
        user_id: str,
        limit: int = 5,
    ) -> tuple[TaskPlan, ...]:
        return await self.list_plans(
            user_id=user_id,
            statuses=(
                TaskStatus.PROPOSED,
                TaskStatus.ACTIVE,
                TaskStatus.IN_PROGRESS,
                TaskStatus.BLOCKED,
            ),
            limit=limit,
        )

    async def complete_plan(self, *, user_id: str, plan_id: str) -> TaskPlan | None:
        async with self.session_factory() as session:
            plan = await session.get(AgentTaskPlanRecord, plan_id)
            if plan is None or plan.user_id != user_id:
                return None
            plan.status = TaskStatus.COMPLETED.value
            plan.updated_at = utc_now()
            steps = await self._step_records(session=session, plan_id=plan.id)
            for step in steps:
                if step.status not in {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.CANCELLED.value,
                }:
                    step.status = TaskStatus.COMPLETED.value
                    step.updated_at = utc_now()
            session.add(
                AgentTaskEventRecord(
                    plan_id=plan.id,
                    user_id=user_id,
                    event_type="completed",
                ),
            )
            await session.commit()
            return await self._load_plan_in_session(
                session=session,
                user_id=user_id,
                plan_id=plan.id,
            )

    async def _load_plan_in_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        plan_id: str,
    ) -> TaskPlan | None:
        plan = await session.get(AgentTaskPlanRecord, plan_id)
        if plan is None or plan.user_id != user_id:
            return None
        steps = await self._step_records(session=session, plan_id=plan_id)
        return plan_from_records(plan, tuple(steps))

    async def _step_records(
        self,
        *,
        session: AsyncSession,
        plan_id: str,
    ) -> tuple[AgentTaskStepRecord, ...]:
        statement = (
            select(AgentTaskStepRecord)
            .where(col(AgentTaskStepRecord.plan_id) == plan_id)
            .order_by(col(AgentTaskStepRecord.position).asc())
        )
        result = await session.exec(statement)
        return tuple(result.all())

    async def _has_other_in_progress_step(
        self,
        *,
        session: AsyncSession,
        plan_id: str,
        step_id: str,
    ) -> bool:
        statement = select(AgentTaskStepRecord).where(
            col(AgentTaskStepRecord.plan_id) == plan_id,
            col(AgentTaskStepRecord.id) != step_id,
            col(AgentTaskStepRecord.status) == TaskStatus.IN_PROGRESS.value,
        )
        result = await session.exec(statement)
        return result.first() is not None

    async def _all_steps_completed(
        self,
        *,
        session: AsyncSession,
        plan_id: str,
        step: AgentTaskStepRecord,
    ) -> bool:
        steps = await self._step_records(session=session, plan_id=plan_id)
        return all(
            (TaskStatus.COMPLETED.value if item.id == step.id else item.status) == TaskStatus.COMPLETED.value
            for item in steps
        )


def plan_from_records(
    plan: AgentTaskPlanRecord,
    steps: tuple[AgentTaskStepRecord, ...],
) -> TaskPlan:
    return TaskPlan(
        id=plan.id,
        user_id=plan.user_id,
        session_id=plan.session_id,
        parent_task_id=plan.parent_task_id,
        objective=plan.objective,
        acceptance_criteria=tuple(plan.acceptance_criteria),
        source=TaskSource(plan.source),
        priority=TaskPriority(plan.priority),
        status=TaskStatus(plan.status),
        allow_parallel_steps=plan.allow_parallel_steps,
        steps=tuple(step_from_record(step) for step in steps),
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def step_from_record(step: AgentTaskStepRecord) -> TaskStep:
    return TaskStep(
        id=step.id,
        plan_id=step.plan_id,
        user_id=step.user_id,
        position=step.position,
        title=step.title,
        status=TaskStatus(step.status),
        evidence_session_id=step.evidence_session_id,
        created_at=step.created_at,
        updated_at=step.updated_at,
    )
