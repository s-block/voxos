import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from voxos.agent_runtime.run_models import AgentRunEvent
from voxos.agent_runtime.run_store import SqlModelAgentRunStore


@dataclass(frozen=True, slots=True)
class RunEventStreamService:
    run_store: SqlModelAgentRunStore
    poll_interval_seconds: float = 1.0

    async def stream_events(
        self,
        *,
        user_id: str,
        run_id: str | None = None,
        since_event_id: str | None = None,
    ) -> AsyncIterator[AgentRunEvent]:
        cursor = since_event_id
        while True:
            events = await self.run_store.list_events(
                user_id=user_id,
                run_id=run_id,
                since_event_id=cursor,
                limit=100,
            )
            for event in events:
                cursor = event.id
                yield event
            if not events:
                await asyncio.sleep(self.poll_interval_seconds)
