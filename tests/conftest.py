import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
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
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.engine import create_session_factory
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.records import (
    IntegrationAuditLogRecord,
    IntegrationCallSessionRecord,
    IntegrationCallTranscriptRecord,
    IntegrationConversationRecord,
    IntegrationCredentialRecord,
    IntegrationEventRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
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
from voxos.security.encryption.local_aes_gcm import LocalAesGcmEncryptionService
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

# Tests must never send events to a developer's configured Sentry project.
os.environ["SENTRY_DSN"] = ""

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
    VoiceProjectWorkerLeaseRecord,
    VoiceWorkerWarmRequestRecord,
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
    IntegrationConversationRecord,
    IntegrationReplyRecord,
    IntegrationCallSessionRecord,
    IntegrationCallTranscriptRecord,
    IntegrationAuditLogRecord,
    ProductDocSourceRecord,
    ProductDocChunkRecord,
)


@pytest_asyncio.fixture
async def sqlite_session_factory() -> AsyncIterator[AsyncSessionFactory]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)

    yield create_session_factory(engine)

    await dispose_engine(engine)


@pytest.fixture
def local_encryption_service() -> LocalAesGcmEncryptionService:
    return LocalAesGcmEncryptionService(
        key=b"0123456789abcdef0123456789abcdef",
    )


@pytest.fixture
def user_secret_vault(
    local_encryption_service: LocalAesGcmEncryptionService,
) -> UserSecretVaultService:
    return UserSecretVaultService(local_encryption_service)


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()
