import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_memory.records import AgentMemoryRecord
from voxos.agent_planning.records import (
    AgentTaskEventRecord,
    AgentTaskPlanRecord,
    AgentTaskStepRecord,
)
from voxos.agent_runtime.records import (
    AgentImprovementEvidenceRecord,
    AgentLearningSettingsRecord,
    AgentReflectionJobRecord,
    AgentReflectionProposalRecord,
    AgentRunEventRecord,
    AgentRunRecord,
    AgentRuntimeEventRecord,
    AgentScheduleRecord,
    AgentSessionRecord,
    AgentToolAuditEventRecord,
    AgentToolEventRecord,
    AgentToolGrantRecord,
    AgentToolRoutePreferenceRecord,
    AgentTranscriptRecord,
    AgentUsageRecord,
    AgentWebhookRecord,
)
from voxos.agent_skills.records import AgentSkillRecord
from voxos.attachments.records import MessageAttachmentRecord
from voxos.auth.records import FirebaseUserRecord
from voxos.browser_accounts.records import (
    AgentBrowserAccountGrantRecord,
    BrowserAccountAuditEventRecord,
    BrowserAccountLeaseRecord,
    BrowserAccountRecord,
    BrowserAccountRunAuthorizationRecord,
)
from voxos.codex_cloud.records import (
    CodexAccountRecord,
    CodexAuthSessionRecord,
    CodexCloudTaskRecord,
)
from voxos.contacts.records import UserContactRecord
from voxos.credentials.records import UserSecretRecord
from voxos.db.engine import database_connect_args
from voxos.db.migrations import prepare_database_extensions
from voxos.db.settings import DatabaseSettings
from voxos.integrations.records import (
    IntegrationAuditLogRecord,
    IntegrationCallSessionRecord,
    IntegrationCallTranscriptRecord,
    IntegrationCredentialRecord,
    IntegrationEventRecord,
    IntegrationMessageRecord,
    IntegrationResourceRecord,
    IntegrationWebhookRouteRecord,
    UserIntegrationRecord,
)
from voxos.mcp.records import (
    UserMcpOAuthStateRecord,
    UserMcpSecretRecord,
    UserMcpServerRecord,
)
from voxos.notifications.models import (
    PushInstallationRecord,
    PushNotificationOutboxRecord,
)
from voxos.product_docs.records import ProductDocChunkRecord, ProductDocSourceRecord
from voxos.text.records import UserTextPreferenceRecord
from voxos.user_settings.models import (
    UserLiveKitSettingsRecord,
    UserProviderCredentialRecord,
    UserVoicePreferenceRecord,
)
from voxos.voice.demand import (
    VoiceCallDemandRecord,
    VoiceProjectWorkerLeaseRecord,
    VoiceWorkerWarmRequestRecord,
)
from voxos.whatsapp.records import (
    WhatsAppContactIdentityRecord,
    WhatsAppMobileRegistrationRecord,
    WhatsAppOutboxRecord,
    WhatsAppSessionKeyRecord,
    WhatsAppSessionLeaseRecord,
    WhatsAppSessionRecord,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata
REGISTERED_MODELS = (
    UserSecretRecord,
    UserContactRecord,
    FirebaseUserRecord,
    BrowserAccountRecord,
    AgentBrowserAccountGrantRecord,
    BrowserAccountRunAuthorizationRecord,
    BrowserAccountAuditEventRecord,
    BrowserAccountLeaseRecord,
    CodexAccountRecord,
    CodexAuthSessionRecord,
    CodexCloudTaskRecord,
    UserMcpOAuthStateRecord,
    UserMcpSecretRecord,
    UserMcpServerRecord,
    UserLiveKitSettingsRecord,
    UserProviderCredentialRecord,
    UserTextPreferenceRecord,
    UserVoicePreferenceRecord,
    PushInstallationRecord,
    PushNotificationOutboxRecord,
    VoiceCallDemandRecord,
    VoiceWorkerWarmRequestRecord,
    VoiceProjectWorkerLeaseRecord,
    WhatsAppContactIdentityRecord,
    WhatsAppMobileRegistrationRecord,
    WhatsAppSessionRecord,
    WhatsAppSessionKeyRecord,
    WhatsAppSessionLeaseRecord,
    WhatsAppOutboxRecord,
    UserAgentRecord,
    AgentSessionRecord,
    AgentRunRecord,
    AgentRunEventRecord,
    AgentWebhookRecord,
    AgentScheduleRecord,
    AgentTranscriptRecord,
    MessageAttachmentRecord,
    AgentToolEventRecord,
    AgentToolGrantRecord,
    AgentToolRoutePreferenceRecord,
    AgentToolAuditEventRecord,
    AgentUsageRecord,
    AgentRuntimeEventRecord,
    AgentLearningSettingsRecord,
    AgentImprovementEvidenceRecord,
    AgentReflectionJobRecord,
    AgentReflectionProposalRecord,
    AgentMemoryRecord,
    AgentSkillRecord,
    AgentTaskPlanRecord,
    AgentTaskStepRecord,
    AgentTaskEventRecord,
    UserIntegrationRecord,
    IntegrationCredentialRecord,
    IntegrationResourceRecord,
    IntegrationWebhookRouteRecord,
    IntegrationEventRecord,
    IntegrationMessageRecord,
    IntegrationCallSessionRecord,
    IntegrationCallTranscriptRecord,
    IntegrationAuditLogRecord,
    ProductDocSourceRecord,
    ProductDocChunkRecord,
)


def run_migrations_offline() -> None:
    context.configure(
        url=DatabaseSettings().async_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    settings = DatabaseSettings()
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = settings.async_database_url()
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=database_connect_args(settings),
    )

    async with connectable.connect() as connection:
        await prepare_database_extensions(connection)
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
