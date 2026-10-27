from dataclasses import dataclass

from voxos.agent_planning.models import TaskPlan, TaskStatus

OPEN_STEP_STATUSES = {
    TaskStatus.PROPOSED,
    TaskStatus.ACTIVE,
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
}


@dataclass(frozen=True, slots=True)
class AgentPlanRenderer:
    max_plans: int = 3
    max_steps_per_plan: int = 6

    def render(self, plans: tuple[TaskPlan, ...]) -> str | None:
        if not plans:
            return None

        lines = ["# Active Task Plan"]
        for plan in plans[: self.max_plans]:
            lines.append(f"- Objective: {plan.objective} [{plan.status.value}]")
            for step in plan.steps[: self.max_steps_per_plan]:
                if step.status in OPEN_STEP_STATUSES:
                    lines.append(
                        f"  - {step.position}. {step.title} [{step.status.value}]",
                    )
        return "\n".join(lines)
