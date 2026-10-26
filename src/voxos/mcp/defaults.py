from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from voxos.agent_runtime.models import JsonObject
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.mcp.models import (
    McpDiscoveredToolDefinition,
    McpServerSafeMetadata,
    McpTrustState,
)
from voxos.mcp.presets import (
    EXA_KEYLESS_VARIANT_ID,
    EXA_MCP_URL,
    EXA_PRESET_ID,
    find_mcp_service_preset,
    find_mcp_service_preset_variant,
    preset_safe_metadata,
)
from voxos.mcp.records import UserMcpServerRecord

DEFAULT_EXA_LABEL = "Exa"
DEFAULT_EXA_TOOL_NAMES = ("web_search_exa", "web_fetch_exa")
APPLICATION_DEFAULTS_VERSION = "2026-08-03.1"


def default_exa_tool_definitions() -> tuple[McpDiscoveredToolDefinition, ...]:
    preset = find_mcp_service_preset(EXA_PRESET_ID)
    if preset is None:
        message = "Exa MCP preset is not configured"
        raise RuntimeError(message)
    guidance_by_name = {guide.name: guide for guide in preset.tool_guidance}
    return (
        McpDiscoveredToolDefinition(
            name="web_search_exa",
            description=(
                "Search the web for any topic and get clean, ready-to-use content. "
                "Use for current information, news, facts, people, companies, and "
                "factual questions. If the returned excerpts are insufficient, "
                "follow up with web_fetch_exa on the best source URLs."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "A semantically rich natural-language description of "
                            "the information or ideal source to find."
                        ),
                    },
                    "numResults": {
                        "type": "number",
                        "description": "Number of search results to return (default: 10).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            guidance=guidance_by_name.get("web_search_exa"),
        ),
        McpDiscoveredToolDefinition(
            name="web_fetch_exa",
            description=(
                "Read one or more webpages as clean markdown. Use after web_search_exa "
                "when excerpts are insufficient or when the user supplies a URL."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URLs to read; batch independent URLs in one call.",
                    },
                    "maxCharacters": {
                        "type": "number",
                        "minimum": 1,
                        "description": "Maximum characters to extract per page (default: 3000).",
                    },
                },
                "required": ["urls"],
                "additionalProperties": False,
            },
            guidance=guidance_by_name.get("web_fetch_exa"),
        ),
    )


def default_exa_safe_metadata() -> JsonObject:
    preset = find_mcp_service_preset(EXA_PRESET_ID)
    if preset is None:
        message = "Exa MCP preset is not configured"
        raise RuntimeError(message)
    variant = find_mcp_service_preset_variant(preset, EXA_KEYLESS_VARIANT_ID)
    if variant is None:
        message = "Exa keyless MCP preset variant is not configured"
        raise RuntimeError(message)
    metadata = preset_safe_metadata(preset, variant)
    metadata.update(
        {
            "platform_default": True,
            "default_allowed_tools": list(DEFAULT_EXA_TOOL_NAMES),
            "tool_definitions": [definition.model_dump(mode="json") for definition in default_exa_tool_definitions()],
        },
    )
    return metadata


def platform_default_tool_names(record: UserMcpServerRecord) -> tuple[str, ...]:
    try:
        metadata = McpServerSafeMetadata.model_validate(record.safe_metadata)
    except ValueError:
        return ()
    if (
        not metadata.platform_default
        or metadata.preset_id != EXA_PRESET_ID
        or metadata.preset_variant_id != EXA_KEYLESS_VARIANT_ID
        or record.url != EXA_MCP_URL
        or record.transport != "streamable_http"
        or record.auth_type != "none"
    ):
        return ()
    allowed = set(record.allowed_tools)
    return tuple(tool_name for tool_name in metadata.default_allowed_tools if tool_name in allowed)


@dataclass(frozen=True, slots=True)
class InitialMcpServerProvisioner:
    session_factory: AsyncSessionFactory

    @property
    def initialization_version(self) -> str:
        return APPLICATION_DEFAULTS_VERSION

    async def initialize_user(self, user_id: str) -> None:
        scoped_factory = tenant_scoped_session_factory(
            session_factory=self.session_factory,
            user_id=user_id,
        )
        async with scoped_factory() as session:
            existing_result = await session.exec(
                select(UserMcpServerRecord).where(
                    col(UserMcpServerRecord.user_id) == user_id,
                ),
            )
            existing_servers = tuple(existing_result.all())
            if any(server.url == EXA_MCP_URL for server in existing_servers):
                return
            session.add(
                UserMcpServerRecord(
                    user_id=user_id,
                    label=available_default_exa_label(
                        {server.label for server in existing_servers},
                    ),
                    transport="streamable_http",
                    url=EXA_MCP_URL,
                    auth_type="none",
                    enabled=True,
                    trust_state=McpTrustState.TRUSTED.value,
                    status="connected",
                    auth_config={},
                    safe_metadata=default_exa_safe_metadata(),
                    allowed_tools=list(DEFAULT_EXA_TOOL_NAMES),
                    discovered_tools=list(DEFAULT_EXA_TOOL_NAMES),
                ),
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                concurrent_result = await session.exec(
                    select(UserMcpServerRecord).where(
                        col(UserMcpServerRecord.user_id) == user_id,
                        col(UserMcpServerRecord.url) == EXA_MCP_URL,
                    ),
                )
                if concurrent_result.first() is None:
                    raise


def available_default_exa_label(existing_labels: set[str]) -> str:
    for candidate in (
        DEFAULT_EXA_LABEL,
        "Exa Web Search",
        "Exa Web Search (default)",
    ):
        if candidate not in existing_labels:
            return candidate
    suffix = 2
    while f"Exa Web Search (default {suffix})" in existing_labels:
        suffix += 1
    return f"Exa Web Search (default {suffix})"
