import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from voxos.agent_core.mcp_tool_cache import (
    McpToolCatalogCache,
    McpToolCatalogCacheKey,
)
from voxos.agent_core.models import AgentRunContext, AgentTool
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyEntry,
    EffectiveToolPolicySnapshot,
)
from voxos.agent_runtime.tool_policy import ToolGrantScope


@dataclass(slots=True)
class FakeTimer:
    value: float = 0.0

    def __call__(self) -> float:
        return self.value


def cache_key(*, user_id: str = "user-123") -> McpToolCatalogCacheKey:
    return McpToolCatalogCacheKey(
        user_id=user_id,
        agent_id="agent-123",
        policy_fingerprint="policy-123",
    )


def tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"{name} description",
        source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="server-123",
    )


@pytest.mark.asyncio
async def test_cache_reuses_successful_catalog_load() -> None:
    cache = McpToolCatalogCache(max_entries=10, ttl_seconds=60)
    calls = 0

    async def loader() -> tuple[AgentTool, ...]:
        nonlocal calls
        calls += 1
        return (tool("search"),)

    first = await cache.get_or_load(cache_key(), loader)
    second = await cache.get_or_load(cache_key(), loader)

    assert first == second
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_coalesces_concurrent_catalog_loads() -> None:
    cache = McpToolCatalogCache(max_entries=10, ttl_seconds=60)
    release = asyncio.Event()
    calls = 0

    async def loader() -> tuple[AgentTool, ...]:
        nonlocal calls
        calls += 1
        await release.wait()
        return (tool("search"),)

    first = asyncio.create_task(cache.get_or_load(cache_key(), loader))
    second = asyncio.create_task(cache.get_or_load(cache_key(), loader))
    await asyncio.sleep(0)
    release.set()

    assert await first == await second
    assert calls == 1


@pytest.mark.asyncio
async def test_invalidation_forces_a_new_load() -> None:
    cache = McpToolCatalogCache(max_entries=10, ttl_seconds=60)
    calls = 0

    async def loader() -> tuple[AgentTool, ...]:
        nonlocal calls
        calls += 1
        return (tool(f"search-{calls}"),)

    first = await cache.get_or_load(cache_key(), loader)
    removed = await cache.invalidate_user("user-123")
    second = await cache.get_or_load(cache_key(), loader)

    assert removed == 1
    assert first != second
    assert calls == 2


@pytest.mark.asyncio
async def test_invalidation_does_not_restore_an_in_flight_stale_load() -> None:
    cache = McpToolCatalogCache(max_entries=10, ttl_seconds=60)
    release = asyncio.Event()
    calls = 0

    async def loader() -> tuple[AgentTool, ...]:
        nonlocal calls
        calls += 1
        current_call = calls
        if current_call == 1:
            await release.wait()
        return (tool(f"search-{current_call}"),)

    stale_load = asyncio.create_task(cache.get_or_load(cache_key(), loader))
    await asyncio.sleep(0)
    await cache.invalidate_user("user-123")
    fresh = await cache.get_or_load(cache_key(), loader)
    release.set()

    assert (await stale_load)[0].name == "search-1"
    assert fresh[0].name == "search-2"
    assert (await cache.get_or_load(cache_key(), loader))[0].name == "search-2"
    assert calls == 2


@pytest.mark.asyncio
async def test_cache_entry_expires_after_ttl() -> None:
    timer = FakeTimer()
    cache = McpToolCatalogCache(
        max_entries=10,
        ttl_seconds=60,
        timer=timer,
    )
    calls = 0

    async def loader() -> tuple[AgentTool, ...]:
        nonlocal calls
        calls += 1
        return (tool(f"search-{calls}"),)

    await cache.get_or_load(cache_key(), loader)
    timer.value = 61
    refreshed = await cache.get_or_load(cache_key(), loader)

    assert refreshed[0].name == "search-2"
    assert calls == 2


def test_policy_fingerprint_ignores_snapshot_identity_and_timestamp() -> None:
    entry = EffectiveToolPolicyEntry(
        tool_name="search",
        tool_source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="server-123",
    )
    first_policy = EffectiveToolPolicySnapshot(
        id="policy-first",
        user_id="user-123",
        agent_id="agent-123",
        entries=(entry,),
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
    )
    second_policy = first_policy.model_copy(
        update={
            "id": "policy-second",
            "created_at": first_policy.created_at + timedelta(minutes=1),
        },
    )
    changed_policy = first_policy.model_copy(update={"entries": ()})

    def context(policy: EffectiveToolPolicySnapshot) -> AgentRunContext:
        return AgentRunContext(
            session_id="session-123",
            user_id="user-123",
            agent_id="agent-123",
            provider="openai",
            model="gpt-5.4-mini",
            instructions="Help the user.",
            effective_tool_policy=policy,
        )

    first_key = McpToolCatalogCacheKey.from_context(context(first_policy))
    second_key = McpToolCatalogCacheKey.from_context(context(second_policy))
    changed_key = McpToolCatalogCacheKey.from_context(context(changed_policy))

    assert first_key == second_key
    assert first_key != changed_key
