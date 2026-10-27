"""enable keyless Exa web search by default

Revision ID: 202608030001
Revises: 5a44fb0f86f4
Create Date: 2026-08-03 00:00:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "202608030001"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "5a44fb0f86f4"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
EXA_TOOL_NAMES = ("web_search_exa", "web_fetch_exa")


def upgrade() -> None:
    connection = op.get_bind()
    users = sa.table(
        "auth_firebase_users",
        sa.column("id", sa.String(length=36)),
    )
    servers = _servers_table()
    now = datetime.now(UTC)

    for user_row in connection.execute(sa.select(users.c.id)):
        user_id = str(user_row.id)
        _set_tenant_user(connection, user_id)
        existing_rows = tuple(
            connection.execute(
                sa.select(servers.c.label, servers.c.url).where(
                    servers.c.user_id == user_id,
                ),
            ),
        )
        if any(str(row.url) == EXA_MCP_URL for row in existing_rows):
            continue
        label = _available_label({str(row.label) for row in existing_rows})
        connection.execute(
            servers.insert().values(
                id=str(uuid4()),
                user_id=user_id,
                label=label,
                transport="streamable_http",
                url=EXA_MCP_URL,
                auth_type="none",
                enabled=True,
                trust_state="trusted",
                status="connected",
                status_message=None,
                auth_config={},
                safe_metadata=_default_exa_metadata(),
                allowed_tools=list(EXA_TOOL_NAMES),
                discovered_tools=list(EXA_TOOL_NAMES),
                created_at=now,
                updated_at=now,
            ),
        )


def downgrade() -> None:
    connection = op.get_bind()
    users = sa.table(
        "auth_firebase_users",
        sa.column("id", sa.String(length=36)),
    )
    servers = _servers_table()
    for user_row in connection.execute(sa.select(users.c.id)):
        user_id = str(user_row.id)
        _set_tenant_user(connection, user_id)
        rows = connection.execute(
            sa.select(
                servers.c.id,
                servers.c.url,
                servers.c.transport,
                servers.c.auth_type,
                servers.c.safe_metadata,
            ).where(
                servers.c.user_id == user_id,
            ),
        )
        for row in rows:
            metadata = row.safe_metadata
            if (
                row.url == EXA_MCP_URL
                and row.transport == "streamable_http"
                and row.auth_type == "none"
                and isinstance(metadata, dict)
                and metadata.get("platform_default") is True
                and metadata.get("preset_id") == "exa"
                and metadata.get("preset_variant_id") == "keyless"
            ):
                connection.execute(servers.delete().where(servers.c.id == row.id))


def _servers_table() -> sa.TableClause:
    return sa.table(
        "user_mcp_servers",
        sa.column("id", sa.String(length=36)),
        sa.column("user_id", sa.String(length=256)),
        sa.column("label", sa.String(length=256)),
        sa.column("transport", sa.String(length=32)),
        sa.column("url", sa.String(length=2048)),
        sa.column("auth_type", sa.String(length=64)),
        sa.column("enabled", sa.Boolean()),
        sa.column("trust_state", sa.String(length=32)),
        sa.column("status", sa.String(length=32)),
        sa.column("status_message", sa.String(length=512)),
        sa.column("auth_config", sa.JSON()),
        sa.column("safe_metadata", sa.JSON()),
        sa.column("allowed_tools", sa.JSON()),
        sa.column("discovered_tools", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _set_tenant_user(connection: sa.Connection, user_id: str) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.execute(
        sa.text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


def _available_label(existing_labels: set[str]) -> str:
    for candidate in ("Exa", "Exa Web Search", "Exa Web Search (default)"):
        if candidate not in existing_labels:
            return candidate
    suffix = 2
    while f"Exa Web Search (default {suffix})" in existing_labels:
        suffix += 1
    return f"Exa Web Search (default {suffix})"


def _default_exa_metadata() -> dict[str, object]:
    search_guidance = {
        "name": "web_search_exa",
        "label": None,
        "description": None,
        "risk": "low",
        "recommended": True,
        "read_only": True,
        "disabled_by_default": False,
        "route_action": "web.search",
        "automatic_priority": 10,
    }
    fetch_guidance = {
        "name": "web_fetch_exa",
        "label": None,
        "description": None,
        "risk": "low",
        "recommended": True,
        "read_only": True,
        "disabled_by_default": False,
        "route_action": None,
        "automatic_priority": None,
    }
    return {
        "preset_id": "exa",
        "preset_variant_id": "keyless",
        "preset_label": "Exa",
        "catalog_version": "2026-08-02.1",
        "researched_at": "2026-07-24",
        "category": "web_search_research",
        "docs_urls": [
            "https://exa.ai/docs/reference/exa-mcp",
            "https://dashboard.exa.ai/api-keys",
        ],
        "warnings": [],
        "tool_guidance": [search_guidance, fetch_guidance],
        "tool_definitions": [
            {
                "name": "web_search_exa",
                "description": (
                    "Search the web for any topic and get clean, ready-to-use content. "
                    "Use for current information, news, facts, people, companies, and "
                    "factual questions. If the returned excerpts are insufficient, "
                    "follow up with web_fetch_exa on the best source URLs."
                ),
                "input_schema": {
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
                "guidance": search_guidance,
            },
            {
                "name": "web_fetch_exa",
                "description": (
                    "Read one or more webpages as clean markdown. Use after web_search_exa "
                    "when excerpts are insufficient or when the user supplies a URL."
                ),
                "input_schema": {
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
                            "description": ("Maximum characters to extract per page (default: 3000)."),
                        },
                    },
                    "required": ["urls"],
                    "additionalProperties": False,
                },
                "guidance": fetch_guidance,
            },
        ],
        "platform_default": True,
        "default_allowed_tools": list(EXA_TOOL_NAMES),
    }
