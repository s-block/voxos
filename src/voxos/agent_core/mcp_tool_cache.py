import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic

import orjson
from cachetools import TTLCache

from voxos.agent_core.models import AgentRunContext, AgentTool

McpToolLoader = Callable[[], Awaitable[tuple[AgentTool, ...]]]


@dataclass(frozen=True, slots=True)
class McpToolCatalogCacheKey:
    user_id: str
    agent_id: str
    policy_fingerprint: str

    @classmethod
    def from_context(cls, context: AgentRunContext) -> "McpToolCatalogCacheKey":
        policy = context.effective_tool_policy
        if policy is None:
            policy_fingerprint = f"unscoped:{context.session_id}"
        else:
            policy_payload = {
                "entries": [entry.model_dump(mode="json") for entry in policy.entries],
                "resolved_routes": [route.model_dump(mode="json") for route in policy.resolved_routes],
            }
            policy_fingerprint = sha256(
                orjson.dumps(policy_payload, option=orjson.OPT_SORT_KEYS),
            ).hexdigest()
        return cls(
            user_id=context.user_id,
            agent_id=context.agent_id,
            policy_fingerprint=policy_fingerprint,
        )


class McpToolCatalogCache:
    """Bounded, process-local MCP tool catalogue cache with single-flight loading."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._values: TTLCache[
            McpToolCatalogCacheKey,
            tuple[AgentTool, ...],
        ] = TTLCache(
            maxsize=max_entries,
            ttl=ttl_seconds,
            timer=timer,
        )
        self._in_flight: dict[
            McpToolCatalogCacheKey,
            asyncio.Task[tuple[AgentTool, ...]],
        ] = {}
        self._invalidated_tasks: set[asyncio.Task[tuple[AgentTool, ...]]] = set()
        self._lock = asyncio.Lock()

    async def get_or_load(
        self,
        key: McpToolCatalogCacheKey,
        loader: McpToolLoader,
    ) -> tuple[AgentTool, ...]:
        async with self._lock:
            cached = self._values.get(key)
            if cached is not None:
                return cached
            task = self._in_flight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._load_and_store(
                        key=key,
                        loader=loader,
                    ),
                )
                self._in_flight[key] = task
        return await asyncio.shield(task)

    async def invalidate_user(self, user_id: str) -> int:
        async with self._lock:
            matching_keys = tuple(key for key in self._values if key.user_id == user_id)
            for key in matching_keys:
                self._values.pop(key, None)
            in_flight_keys = tuple(key for key in self._in_flight if key.user_id == user_id)
            for key in in_flight_keys:
                task = self._in_flight.pop(key)
                self._invalidated_tasks.add(task)
            return len(matching_keys)

    async def _load_and_store(
        self,
        *,
        key: McpToolCatalogCacheKey,
        loader: McpToolLoader,
    ) -> tuple[AgentTool, ...]:
        try:
            tools = await loader()
            async with self._lock:
                current_task = asyncio.current_task()
                if current_task not in self._invalidated_tasks and self._in_flight.get(key) is current_task:
                    self._values[key] = tools
            return tools
        finally:
            async with self._lock:
                current_task = asyncio.current_task()
                if self._in_flight.get(key) is current_task:
                    self._in_flight.pop(key, None)
                if current_task is not None:
                    self._invalidated_tasks.discard(current_task)
