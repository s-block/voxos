from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine

from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.api.admission import AdmissionAuthDependency, ApiAdmissionController, ApiAdmissionMiddleware
from voxos.api.agent_webhooks import create_agent_webhooks_router
from voxos.api.agents import create_agents_router
from voxos.api.browser_accounts import create_browser_accounts_router
from voxos.api.chats import create_chats_router
from voxos.api.client_config import ClientConfigSettings, create_client_config_router
from voxos.api.codex_cloud import create_codex_cloud_router
from voxos.api.contacts import create_contacts_router
from voxos.api.improvements import create_improvements_router
from voxos.api.integrations import create_integrations_router
from voxos.api.livekit import AuthenticatedUserDependency, create_livekit_router
from voxos.api.mcp_credential_broker import (
    create_mcp_credential_broker_router,
)
from voxos.api.mcp_servers import create_mcp_servers_router
from voxos.api.runs import create_runs_router
from voxos.api.schedules import create_schedules_router
from voxos.api.settings import ApiSettings
from voxos.api.text_settings import create_text_settings_router
from voxos.api.voice_settings import create_voice_settings_router
from voxos.attachments.settings import AttachmentSettings
from voxos.auth.factory import create_firebase_auth_dependency
from voxos.auth.settings import FirebaseAuthSettings
from voxos.browser_accounts.providers.browser_use_cloud import BrowserUseCloudProvider
from voxos.browser_accounts.providers.browserless import BrowserlessProvider
from voxos.browser_accounts.providers.composite import CompositeBrowserAccountProvider
from voxos.browser_accounts.providers.stagehand_local import StagehandLocalProvider
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.codex_cloud.settings import CodexCloudSettings
from voxos.credentials.services import CredentialsServiceFactory
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.engine import create_database_engine, create_session_factory
from voxos.db.session import AsyncSessionFactory
from voxos.db.settings import DatabaseSettings
from voxos.mcp.defaults import InitialMcpServerProvisioner
from voxos.mcp.delegation import (
    credential_delegation_signer_from_settings,
)
from voxos.mcp.settings import McpSettings
from voxos.notifications.router import create_push_notifications_router
from voxos.notifications.settings import PushNotificationSettings
from voxos.observability.sentry import initialize_sentry
from voxos.security.encryption.factory import create_secret_encryption_service
from voxos.security.encryption.settings import EncryptionSettings
from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointSettings,
)
from voxos.text.providers import OpenAICompatibleTextProvider
from voxos.utils.logging import configure_voxos_logging
from voxos.voice.livekit_tokens import LiveKitVoiceTokenService
from voxos.voice.settings import VoiceAgentSettings
from voxos.voice.warmup import (
    ActivityWarmingAuthDependency,
    VoiceWorkerActivityWarmer,
    VoiceWorkerActivityWarmup,
)
from voxos.whatsapp.router import create_whatsapp_router
from voxos.whatsapp.settings import WhatsAppWorkerSettings


def create_app(
    api_settings: ApiSettings | None = None,
    auth_dependency: AuthenticatedUserDependency | None = None,
    database_settings: DatabaseSettings | None = None,
    firebase_settings: FirebaseAuthSettings | None = None,
    encryption_settings: EncryptionSettings | None = None,
    http_client: httpx.AsyncClient | None = None,
    livekit_token_service: LiveKitVoiceTokenService | None = None,
    secret_vault: UserSecretVaultService | None = None,
    session_factory: AsyncSessionFactory | None = None,
    voice_settings: VoiceAgentSettings | None = None,
    whatsapp_settings: WhatsAppWorkerSettings | None = None,
    client_config_settings: ClientConfigSettings | None = None,
    mcp_settings: McpSettings | None = None,
    attachment_settings: AttachmentSettings | None = None,
    browser_account_settings: BrowserAccountSettings | None = None,
    codex_cloud_settings: CodexCloudSettings | None = None,
    push_notification_settings: PushNotificationSettings | None = None,
    outbound_endpoint_settings: OutboundEndpointSettings | None = None,
    mcp_tool_catalog_cache: McpToolCatalogCache | None = None,
    voice_worker_activity_warmer: VoiceWorkerActivityWarmup | None = None,
) -> FastAPI:
    initialize_sentry("api")
    resolved_api_settings = api_settings or ApiSettings()
    resolved_client_config_settings = client_config_settings or ClientConfigSettings()
    resolved_firebase_settings = firebase_settings or FirebaseAuthSettings()
    resolved_voice_settings = voice_settings or VoiceAgentSettings()
    resolved_whatsapp_settings = whatsapp_settings or WhatsAppWorkerSettings()
    resolved_mcp_settings = mcp_settings or McpSettings()
    resolved_attachment_settings = attachment_settings or AttachmentSettings()
    resolved_browser_account_settings = browser_account_settings or BrowserAccountSettings()
    resolved_codex_cloud_settings = codex_cloud_settings or CodexCloudSettings()
    resolved_push_notification_settings = push_notification_settings or PushNotificationSettings()
    resolved_outbound_endpoint_policy = OutboundEndpointPolicy.from_settings(
        outbound_endpoint_settings,
    )
    resolved_mcp_tool_catalog_cache = mcp_tool_catalog_cache or McpToolCatalogCache(
        max_entries=(resolved_mcp_settings.mcp_tool_catalog_cache_max_entries),
        ttl_seconds=(resolved_mcp_settings.mcp_tool_catalog_cache_ttl_seconds),
    )
    credential_delegation_signer = credential_delegation_signer_from_settings(resolved_mcp_settings)
    managed_engine: AsyncEngine | None = None
    resolved_session_factory = session_factory
    if resolved_session_factory is None:
        managed_engine = create_database_engine(
            database_settings or DatabaseSettings(),
        )
        resolved_session_factory = create_session_factory(managed_engine)

    if livekit_token_service is None:
        livekit_token_service = LiveKitVoiceTokenService.from_settings(
            resolved_voice_settings,
        )

    if secret_vault is None:
        secret_vault = CredentialsServiceFactory(
            create_secret_encryption_service(encryption_settings or EncryptionSettings()),
        ).user_secret_vault()

    managed_http_client: httpx.AsyncClient | None = None
    resolved_http_client = http_client
    if resolved_http_client is None:
        managed_http_client = httpx.AsyncClient()
        resolved_http_client = managed_http_client

    browser_account_provider = CompositeBrowserAccountProvider(
        browser_use=BrowserUseCloudProvider(
            http_client=resolved_http_client,
            settings=resolved_browser_account_settings,
        ),
        browserless=BrowserlessProvider(resolved_browser_account_settings),
        stagehand_local=StagehandLocalProvider(
            http_client=resolved_http_client,
            settings=resolved_browser_account_settings,
        ),
    )

    resolved_auth_dependency = auth_dependency
    if resolved_auth_dependency is None:
        resolved_auth_dependency = create_firebase_auth_dependency(
            settings=resolved_firebase_settings,
            session_factory=resolved_session_factory,
            new_user_initializer=InitialMcpServerProvisioner(
                session_factory=resolved_session_factory,
            ),
        )
    managed_voice_worker_activity_warmer: VoiceWorkerActivityWarmer | None = None
    resolved_voice_worker_activity_warmer = voice_worker_activity_warmer
    if resolved_voice_worker_activity_warmer is None:
        managed_voice_worker_activity_warmer = VoiceWorkerActivityWarmer(
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            settings=resolved_voice_settings,
        )
        resolved_voice_worker_activity_warmer = managed_voice_worker_activity_warmer
    admission_controller = ApiAdmissionController(resolved_api_settings)
    auth_dependency = ActivityWarmingAuthDependency(
        dependency=AdmissionAuthDependency(
            dependency=resolved_auth_dependency,
            controller=admission_controller,
        ),
        warmer=resolved_voice_worker_activity_warmer,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        initialize_sentry("api")
        configure_voxos_logging()
        try:
            yield
        finally:
            if managed_voice_worker_activity_warmer is not None:
                await managed_voice_worker_activity_warmer.aclose()
            if managed_http_client is not None:
                await managed_http_client.aclose()
            if managed_engine is not None:
                await managed_engine.dispose()

    app = FastAPI(
        title="Voxos API",
        lifespan=lifespan,
        docs_url="/docs" if resolved_api_settings.api_docs_enabled else None,
        redoc_url="/redoc" if resolved_api_settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if resolved_api_settings.api_docs_enabled else None,
    )
    app.add_middleware(ApiAdmissionMiddleware, controller=admission_controller)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_api_settings.api_cors_allow_origins),
        allow_credentials=False,
        allow_methods=["DELETE", "GET", "PATCH", "POST", "PUT", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Voxos-Idempotency-Key",
            "X-Voxos-Webhook-Token",
        ],
    )

    @app.get("/health")
    async def health(response: Response) -> dict[str, str]:
        if resolved_api_settings.deployment_revision is not None:
            response.headers["X-Voxos-Revision"] = resolved_api_settings.deployment_revision
        return {"status": "ok"}

    app.include_router(
        create_client_config_router(
            client_settings=resolved_client_config_settings,
            firebase_settings=resolved_firebase_settings,
            voice_settings=resolved_voice_settings,
            push_settings=resolved_push_notification_settings,
        ),
    )
    app.include_router(
        create_push_notifications_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
        ),
    )
    app.include_router(
        create_improvements_router(auth_dependency=auth_dependency, session_factory=resolved_session_factory)
    )
    app.include_router(
        create_agents_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            mcp_tool_catalog_cache=resolved_mcp_tool_catalog_cache,
        ),
    )
    app.include_router(
        create_contacts_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
        ),
    )
    app.include_router(
        create_livekit_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            token_service=livekit_token_service,
            voice_settings=resolved_voice_settings,
        ),
    )
    app.include_router(
        create_voice_settings_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
        ),
    )
    app.include_router(
        create_text_settings_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            image_input_enabled=resolved_attachment_settings.image_input_enabled,
            outbound_endpoint_policy=resolved_outbound_endpoint_policy,
        ),
    )
    app.include_router(
        create_chats_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            http_client=resolved_http_client,
            public_base_url=resolved_api_settings.api_public_base_url,
            voice_settings=resolved_voice_settings,
            attachment_settings=resolved_attachment_settings,
            browser_account_settings=resolved_browser_account_settings,
            browser_account_provider=browser_account_provider,
            push_settings=resolved_push_notification_settings,
            text_provider_client=OpenAICompatibleTextProvider(
                http_client=resolved_http_client,
                outbound_endpoint_policy=resolved_outbound_endpoint_policy,
            ),
            outbound_endpoint_policy=resolved_outbound_endpoint_policy,
            mcp_tool_catalog_cache=resolved_mcp_tool_catalog_cache,
        ),
    )
    app.include_router(
        create_runs_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
        ),
    )
    app.include_router(
        create_agent_webhooks_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            public_base_url=resolved_api_settings.api_public_base_url,
        ),
    )
    app.include_router(
        create_codex_cloud_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            settings=resolved_codex_cloud_settings,
        ),
    )
    app.include_router(
        create_schedules_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
        ),
    )
    app.include_router(
        create_browser_accounts_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            http_client=resolved_http_client,
            settings=resolved_browser_account_settings,
            provider=browser_account_provider,
        ),
    )
    app.include_router(
        create_mcp_servers_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            http_client=resolved_http_client,
            mcp_settings=resolved_mcp_settings,
            credential_delegation_signer=(credential_delegation_signer),
            outbound_endpoint_policy=resolved_outbound_endpoint_policy,
            mcp_tool_catalog_cache=resolved_mcp_tool_catalog_cache,
        ),
    )
    app.include_router(
        create_mcp_credential_broker_router(
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            http_client=resolved_http_client,
            delegation_signer=credential_delegation_signer,
        ),
    )
    app.include_router(
        create_whatsapp_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            settings=resolved_whatsapp_settings,
        ),
    )
    app.include_router(
        create_integrations_router(
            auth_dependency=auth_dependency,
            session_factory=resolved_session_factory,
            secret_vault=secret_vault,
            api_settings=resolved_api_settings,
            voice_settings=resolved_voice_settings,
            livekit_token_service=livekit_token_service,
            whatsapp_settings=resolved_whatsapp_settings,
        ),
    )
    return app


app = create_app()
