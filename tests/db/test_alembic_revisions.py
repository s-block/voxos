from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RESERVED_VOICE_REVISION = "202607090001"
VOICE_DEMAND_REVISION = "202607090002"
CONTACTS_REVISION = "202607130001"
COMMUNICATIONS_REVISION = "e5f3de5e4e06"
WHATSAPP_REVISION = "398ea89de8ce"
WHATSAPP_MOBILE_REGISTRATION_REVISION = "b7aefc1c5bde"  # pragma: allowlist secret
WHATSAPP_CONTACT_IDENTITIES_REVISION = "06a519f1b316"  # pragma: allowlist secret
WHATSAPP_GROUP_CONVERSATION_REVISION = "9ced105cdeee"  # pragma: allowlist secret
WHATSAPP_UNMASKED_PHONE_NUMBERS_REVISION = "9d3048bd7659"  # pragma: allowlist secret
GOAL_DIRECTED_OUTBOUND_CALLS_REVISION = "9329a3aa4885"  # pragma: allowlist secret
MULTIMODAL_ATTACHMENTS_REVISION = "6a020680226e"  # pragma: allowlist secret
DURABLE_ATTACHMENT_DELETION_REVISION = "7cd844e67d81"  # pragma: allowlist secret
AGENT_TOOL_ROUTE_PREFERENCES_REVISION = "5a44fb0f86f4"  # pragma: allowlist secret
EXA_DEFAULTS_REVISION = "202608030001"  # pragma: allowlist secret
APPLICATION_DEFAULTS_REVISION = "d88f0d358809"  # pragma: allowlist secret
DIRECT_WHATSAPP_REVISION = "a23104f53d65"  # pragma: allowlist secret
SKILL_CAPABILITIES_REVISION = "2f40fe48bfed"  # pragma: allowlist secret
NON_ADMIN_MCP_REVISION = "5bab8459bff3"  # pragma: allowlist secret
BROWSER_ACCOUNTS_REVISION = "a8f1c2d3e4b5"  # pragma: allowlist secret
BROWSER_ACCOUNT_SCHEMA_REVISION = "60e7e59bdfc3"  # pragma: allowlist secret
BROWSER_ACCOUNT_COUNTRY_REVISION = "e27efebf8898"  # pragma: allowlist secret
LOCAL_BROWSER_ACCOUNTS_REVISION = "826e4de3de09"  # pragma: allowlist secret
CODEX_CLOUD_REVISION = "679f69df3d03"  # pragma: allowlist secret
AGENT_ATTACHMENT_SOURCE_REVISION = "096d5fec85c1"  # pragma: allowlist secret
RUN_CHAT_DELIVERY_REVISION = "ca88b0c2bd60"  # pragma: allowlist secret
PUSH_NOTIFICATIONS_REVISION = "e7c447235901"  # pragma: allowlist secret
AGENT_WEBHOOKS_REVISION = "98930ac5cc6d"  # pragma: allowlist secret
VOICE_WORKER_WARM_REQUESTS_REVISION = "c0fdb7fcd673"  # pragma: allowlist secret
PERSISTENT_VOICE_WORKER_WARM_REQUESTS_REVISION = "3ee251b6a41f"  # pragma: allowlist secret


def test_migration_lineage_is_linear_through_application_defaults_revision() -> None:
    config = Config(REPOSITORY_ROOT / "alembic.ini")
    config.set_main_option(
        "script_location",
        str(REPOSITORY_ROOT / "alembic"),
    )
    revisions = ScriptDirectory.from_config(config)

    reserved_revision = revisions.get_revision(RESERVED_VOICE_REVISION)
    demand_revision = revisions.get_revision(VOICE_DEMAND_REVISION)
    contacts_revision = revisions.get_revision(CONTACTS_REVISION)
    communications_revision = revisions.get_revision(COMMUNICATIONS_REVISION)
    whatsapp_revision = revisions.get_revision(WHATSAPP_REVISION)
    whatsapp_mobile_registration_revision = revisions.get_revision(
        WHATSAPP_MOBILE_REGISTRATION_REVISION,
    )
    whatsapp_contact_identities_revision = revisions.get_revision(
        WHATSAPP_CONTACT_IDENTITIES_REVISION,
    )
    whatsapp_group_conversation_revision = revisions.get_revision(
        WHATSAPP_GROUP_CONVERSATION_REVISION,
    )
    whatsapp_unmasked_phone_numbers_revision = revisions.get_revision(
        WHATSAPP_UNMASKED_PHONE_NUMBERS_REVISION,
    )
    goal_directed_outbound_calls_revision = revisions.get_revision(
        GOAL_DIRECTED_OUTBOUND_CALLS_REVISION,
    )
    multimodal_attachments_revision = revisions.get_revision(
        MULTIMODAL_ATTACHMENTS_REVISION,
    )
    durable_attachment_deletion_revision = revisions.get_revision(
        DURABLE_ATTACHMENT_DELETION_REVISION,
    )
    agent_tool_route_preferences_revision = revisions.get_revision(
        AGENT_TOOL_ROUTE_PREFERENCES_REVISION,
    )
    exa_defaults_revision = revisions.get_revision(EXA_DEFAULTS_REVISION)
    application_defaults_revision = revisions.get_revision(
        APPLICATION_DEFAULTS_REVISION,
    )
    direct_whatsapp_revision = revisions.get_revision(DIRECT_WHATSAPP_REVISION)
    skill_capabilities_revision = revisions.get_revision(
        SKILL_CAPABILITIES_REVISION,
    )
    non_admin_mcp_revision = revisions.get_revision(NON_ADMIN_MCP_REVISION)
    browser_accounts_revision = revisions.get_revision(BROWSER_ACCOUNTS_REVISION)
    browser_account_schema_revision = revisions.get_revision(
        BROWSER_ACCOUNT_SCHEMA_REVISION,
    )
    browser_account_country_revision = revisions.get_revision(
        BROWSER_ACCOUNT_COUNTRY_REVISION,
    )
    local_browser_accounts_revision = revisions.get_revision(
        LOCAL_BROWSER_ACCOUNTS_REVISION,
    )
    codex_cloud_revision = revisions.get_revision(CODEX_CLOUD_REVISION)
    agent_attachment_source_revision = revisions.get_revision(
        AGENT_ATTACHMENT_SOURCE_REVISION,
    )
    run_chat_delivery_revision = revisions.get_revision(RUN_CHAT_DELIVERY_REVISION)
    push_notifications_revision = revisions.get_revision(PUSH_NOTIFICATIONS_REVISION)
    agent_webhooks_revision = revisions.get_revision(AGENT_WEBHOOKS_REVISION)
    voice_worker_warm_requests_revision = revisions.get_revision(
        VOICE_WORKER_WARM_REQUESTS_REVISION,
    )
    persistent_voice_worker_warm_requests_revision = revisions.get_revision(
        PERSISTENT_VOICE_WORKER_WARM_REQUESTS_REVISION,
    )

    assert reserved_revision is not None
    assert reserved_revision.down_revision == "202607050001"
    assert demand_revision is not None
    assert demand_revision.down_revision == RESERVED_VOICE_REVISION
    assert contacts_revision is not None
    assert contacts_revision.down_revision == VOICE_DEMAND_REVISION
    assert communications_revision is not None
    assert communications_revision.down_revision == CONTACTS_REVISION
    assert whatsapp_revision is not None
    assert whatsapp_revision.down_revision == COMMUNICATIONS_REVISION
    assert whatsapp_mobile_registration_revision is not None
    assert whatsapp_mobile_registration_revision.down_revision == WHATSAPP_REVISION
    assert whatsapp_contact_identities_revision is not None
    assert whatsapp_contact_identities_revision.down_revision == WHATSAPP_MOBILE_REGISTRATION_REVISION
    assert whatsapp_group_conversation_revision is not None
    assert whatsapp_group_conversation_revision.down_revision == WHATSAPP_CONTACT_IDENTITIES_REVISION
    assert whatsapp_unmasked_phone_numbers_revision is not None
    assert whatsapp_unmasked_phone_numbers_revision.down_revision == WHATSAPP_GROUP_CONVERSATION_REVISION
    assert goal_directed_outbound_calls_revision is not None
    assert goal_directed_outbound_calls_revision.down_revision == WHATSAPP_UNMASKED_PHONE_NUMBERS_REVISION
    assert multimodal_attachments_revision is not None
    assert multimodal_attachments_revision.down_revision == GOAL_DIRECTED_OUTBOUND_CALLS_REVISION
    assert durable_attachment_deletion_revision is not None
    assert durable_attachment_deletion_revision.down_revision == MULTIMODAL_ATTACHMENTS_REVISION
    assert agent_tool_route_preferences_revision is not None
    assert agent_tool_route_preferences_revision.down_revision == DURABLE_ATTACHMENT_DELETION_REVISION
    assert exa_defaults_revision is not None
    assert exa_defaults_revision.down_revision == AGENT_TOOL_ROUTE_PREFERENCES_REVISION
    assert application_defaults_revision is not None
    assert application_defaults_revision.down_revision == EXA_DEFAULTS_REVISION
    assert direct_whatsapp_revision is not None
    assert direct_whatsapp_revision.down_revision == APPLICATION_DEFAULTS_REVISION
    assert skill_capabilities_revision is not None
    assert skill_capabilities_revision.down_revision == DIRECT_WHATSAPP_REVISION
    assert non_admin_mcp_revision is not None
    assert non_admin_mcp_revision.down_revision == SKILL_CAPABILITIES_REVISION
    assert browser_accounts_revision is not None
    assert browser_accounts_revision.down_revision == NON_ADMIN_MCP_REVISION
    assert browser_account_schema_revision is not None
    assert browser_account_schema_revision.down_revision == BROWSER_ACCOUNTS_REVISION
    assert browser_account_country_revision is not None
    assert browser_account_country_revision.down_revision == BROWSER_ACCOUNT_SCHEMA_REVISION
    assert local_browser_accounts_revision is not None
    assert local_browser_accounts_revision.down_revision == BROWSER_ACCOUNT_COUNTRY_REVISION
    assert codex_cloud_revision is not None
    assert codex_cloud_revision.down_revision == LOCAL_BROWSER_ACCOUNTS_REVISION
    assert agent_attachment_source_revision is not None
    assert agent_attachment_source_revision.down_revision == CODEX_CLOUD_REVISION
    assert run_chat_delivery_revision is not None
    assert run_chat_delivery_revision.down_revision == AGENT_ATTACHMENT_SOURCE_REVISION
    assert push_notifications_revision is not None
    assert agent_webhooks_revision is not None
    assert agent_webhooks_revision.down_revision == PUSH_NOTIFICATIONS_REVISION
    assert push_notifications_revision.down_revision == RUN_CHAT_DELIVERY_REVISION
    assert voice_worker_warm_requests_revision is not None
    assert voice_worker_warm_requests_revision.down_revision == AGENT_WEBHOOKS_REVISION
    assert persistent_voice_worker_warm_requests_revision is not None
    assert persistent_voice_worker_warm_requests_revision.down_revision == VOICE_WORKER_WARM_REQUESTS_REVISION
    learning_revision = revisions.get_revision("a48afa3de8d4")
    assert learning_revision is not None
    assert learning_revision.down_revision == PERSISTENT_VOICE_WORKER_WARM_REQUESTS_REVISION
    assert revisions.get_heads() == ["a48afa3de8d4"]
