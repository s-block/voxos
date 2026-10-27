from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import col, select
from starlette.requests import Request

from voxos.api.voice_settings import create_voice_settings_router
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.providers.models import RealtimeVoiceProvider
from voxos.text.models import TextProvider
from voxos.text.storage import UserTextCredentialService
from voxos.user_settings.models import (
    ProviderCredentialStatus,
    UserProviderCredentialRecord,
)
from voxos.user_settings.services import (
    UserCredentialService,
    UserVoicePreferenceService,
)


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


def authenticated_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="local-user-123",
        firebase_user_id="firebase-user-123",
        email="user@example.com",
        email_verified=True,
        display_name="Test User",
        photo_url="https://example.com/user.png",
    )


def create_test_client(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_voice_settings_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
        ),
    )
    return TestClient(app)


def test_voice_settings_endpoint_returns_unconfigured_state(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.get(
        "/api/voice/settings",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "provider": "openai_realtime",
        "model": "gpt-realtime-2.1-mini",
        "voice": "marin",
        "preferred_language": "English",
        "agent_instructions": "You are a helpful voice assistant.",
        "initial_reply_instructions": "Greet the user and offer your assistance.",
    }


def test_saving_a_voice_key_without_activation_preserves_voice_defaults(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.put(
        "/api/voice/settings/provider-credential",
        json={
            "provider": "google_gemini_live",
            "api_key": "google-test-credential",  # pragma: allowlist secret
            "activate": False,
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["active_provider"] == "openai_realtime"
    assert body["active_settings"]["configured"] is False
    google = next(provider for provider in body["providers"] if provider["provider"] == "google_gemini_live")
    assert google["configured"] is True


@pytest.mark.asyncio
async def test_voice_provider_settings_ignores_text_only_credentials(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        await UserTextCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).create_credential(
            user_id="local-user-123",
            provider=TextProvider.NVIDIA,
            label="NVIDIA API Catalog",
            secret=SecretStr("nvidia-test-secret"),
        )

    response = create_test_client(sqlite_session_factory, user_secret_vault).get(
        "/api/voice/provider-settings",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    providers = response.json()["providers"]
    assert all(provider["provider"] != "nvidia" for provider in providers)
    assert {provider["provider"]: provider["label"] for provider in providers} == {
        "openai_realtime": "OpenAI",
        "google_gemini_live": "Google",
        "hume_evi3": "Hume",
        "elevenlabs_conversational_ai": "ElevenLabs",
        "mistral_voxtral": "Mistral",
        "grok_voice_agent": "xAI",
    }


@pytest.mark.asyncio
async def test_voice_settings_can_reuse_a_saved_openai_text_credential(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        text_credential = await UserTextCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).create_credential(
            user_id="local-user-123",
            provider=TextProvider.OPENAI,
            label="OpenAI",
            secret=SecretStr("sk-shared-openai"),
        )

    client = create_test_client(sqlite_session_factory, user_secret_vault)
    provider_settings_response = client.get(
        "/api/voice/provider-settings",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert provider_settings_response.status_code == 200
    openai_summary = next(
        provider
        for provider in provider_settings_response.json()["providers"]
        if provider["provider"] == "openai_realtime"
    )
    assert openai_summary["configured"] is True
    assert openai_summary["credential_last_four"] == "enai"

    settings_response = client.put(
        "/api/voice/settings",
        json={
            "provider": "openai_realtime",
            "model": "gpt-realtime-2.1-mini",
            "voice": "marin",
            "preferred_language": "English",
            "agent_instructions": "Be direct.",
            "initial_reply_instructions": "Hello.",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert settings_response.status_code == 200
    async with sqlite_session_factory() as session:
        preferences = await UserVoicePreferenceService(session).get_preferences(
            "local-user-123",
        )
        credential = await UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).resolve_credential(
            user_id="local-user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            credential_ref=preferences.credential_ref,
        )

    assert preferences.credential_ref == text_credential.id
    assert credential.token.get_secret_value() == "sk-shared-openai"


@pytest.mark.asyncio
async def test_voice_settings_endpoint_stores_openai_key_for_authenticated_user(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.put(
        "/api/voice/settings/credential",
        json={
            "openai_api_key": "sk-test-secret",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "configured": True,
        "provider": "openai_realtime",
        "model": "gpt-realtime-2.1-mini",
        "voice": "marin",
        "preferred_language": "English",
        "agent_instructions": "You are a helpful voice assistant.",
        "initial_reply_instructions": "Greet the user and offer your assistance.",
    }
    assert "sk-test-secret" not in response.text
    assert "credential_last_four" not in body

    async with sqlite_session_factory() as session:
        preferences = await UserVoicePreferenceService(session).get_preferences(
            "local-user-123",
        )
        credentials = await UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).resolve_credential(
            user_id="local-user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            credential_ref=preferences.credential_ref,
        )

    assert preferences.provider_config.agent_instructions == ("You are a helpful voice assistant.")
    assert preferences.provider_config.preferred_language == "English"
    assert preferences.provider_config.initial_reply_instructions == ("Greet the user and offer your assistance.")
    assert credentials.token.get_secret_value() == "sk-test-secret"


@pytest.mark.asyncio
async def test_voice_provider_settings_endpoint_saves_and_switches_providers(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    initial_response = client.get(
        "/api/voice/provider-settings",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert initial_response.status_code == 200
    initial_body = initial_response.json()
    assert initial_body["active_provider"] == "openai_realtime"
    assert initial_body["active_settings"]["configured"] is False
    assert {provider["provider"] for provider in initial_body["providers"]} == {
        "openai_realtime",
        "google_gemini_live",
        "hume_evi3",
        "elevenlabs_conversational_ai",
        "mistral_voxtral",
        "grok_voice_agent",
    }
    openai_summary = next(
        provider for provider in initial_body["providers"] if provider["provider"] == "openai_realtime"
    )
    assert openai_summary["logo"] == {
        "domain": "openai.com",
        "label": "OpenAI",
        "source": "domain",
    }
    assert openai_summary["suggested_models"] == [
        "gpt-realtime-2.1-mini",
        "gpt-realtime-2.1",
    ]
    assert openai_summary["model_options"][0]["recommended"] is True
    assert "$10 input" in openai_summary["model_options"][0]["pricing_summary"]
    assert openai_summary["suggested_voices"][:2] == ["marin", "cedar"]

    openai_response = client.put(
        "/api/voice/settings/provider-credential",
        json={
            "provider": "openai_realtime",
            "api_key": "sk-openai-secret",
            "activate": True,
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert openai_response.status_code == 200

    google_response = client.put(
        "/api/voice/settings/provider-credential",
        json={
            "provider": "google_gemini_live",
            "api_key": "google-live-secret",
            "activate": False,
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert google_response.status_code == 200
    google_body = google_response.json()
    assert google_body["active_provider"] == "openai_realtime"
    google_summary = next(
        provider for provider in google_body["providers"] if provider["provider"] == "google_gemini_live"
    )
    assert google_summary["logo"]["domain"] == "google.com"
    assert google_summary["configured"] is True
    assert google_summary["credential_last_four"] == "cret"
    assert google_summary["runtime_supported"] is True
    assert google_summary["suggested_models"] == [
        "gemini-3.1-flash-live-preview",
        "gemini-live-2.5-flash-native-audio",
    ]
    assert "Puck" in google_summary["suggested_voices"]

    switch_response = client.put(
        "/api/voice/settings/active-provider",
        json={"provider": "google_gemini_live"},
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert switch_response.status_code == 200
    switch_body = switch_response.json()
    assert switch_body["active_provider"] == "google_gemini_live"
    assert switch_body["active_settings"] == {
        "configured": True,
        "provider": "google_gemini_live",
        "model": "gemini-3.1-flash-live-preview",
        "voice": "Puck",
        "preferred_language": "English",
        "agent_instructions": "You are a helpful voice assistant.",
        "initial_reply_instructions": "Greet the user and offer your assistance.",
    }

    async with sqlite_session_factory() as session:
        preferences = await UserVoicePreferenceService(session).get_preferences(
            "local-user-123",
        )
        credentials = await UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).resolve_credential(
            user_id="local-user-123",
            provider=RealtimeVoiceProvider.GOOGLE_GEMINI_LIVE,
            credential_ref=preferences.credential_ref,
        )

    assert preferences.provider == RealtimeVoiceProvider.GOOGLE_GEMINI_LIVE
    assert credentials.token.get_secret_value() == "google-live-secret"


@pytest.mark.asyncio
async def test_voice_settings_endpoint_updates_agent_config_without_new_key(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    key_response = client.put(
        "/api/voice/settings/credential",
        json={"openai_api_key": "sk-test-secret"},
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert key_response.status_code == 200

    response = client.put(
        "/api/voice/settings",
        json={
            "model": "gpt-realtime-preview",
            "voice": "cedar",
            "preferred_language": "British English",
            "agent_instructions": "Be concise.",
            "initial_reply_instructions": "Say hello.",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "provider": "openai_realtime",
        "model": "gpt-realtime-preview",
        "voice": "cedar",
        "preferred_language": "British English",
        "agent_instructions": "Be concise.",
        "initial_reply_instructions": "Say hello.",
    }
    assert "sk-test-secret" not in response.text
    assert "credential_last_four" not in response.json()

    async with sqlite_session_factory() as session:
        preferences = await UserVoicePreferenceService(session).get_preferences(
            "local-user-123",
        )
        result = await session.exec(
            select(UserProviderCredentialRecord).where(
                col(UserProviderCredentialRecord.user_id) == "local-user-123",
            ),
        )
        records = result.all()

    assert preferences.model == "gpt-realtime-preview"
    assert preferences.voice == "cedar"
    assert preferences.provider_config.preferred_language == "British English"
    assert preferences.provider_config.agent_instructions == "Be concise."
    assert preferences.provider_config.initial_reply_instructions == "Say hello."
    assert len(records) == 1


def test_voice_settings_endpoint_rejects_agent_config_without_openai_key(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.put(
        "/api/voice/settings",
        json={
            "model": "gpt-realtime-preview",
            "voice": "cedar",
            "preferred_language": "British English",
            "agent_instructions": "Be concise.",
            "initial_reply_instructions": "Say hello.",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Save an OpenAI key before saving voice preferences",
    }


@pytest.mark.asyncio
async def test_voice_settings_endpoint_replaces_previous_key(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.put(
        "/api/voice/settings/credential",
        json={"openai_api_key": "sk-first-secret"},
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert response.status_code == 200

    response = client.put(
        "/api/voice/settings",
        json={
            "model": "gpt-realtime-preview",
            "voice": "cedar",
            "preferred_language": "British English",
            "agent_instructions": "Be concise.",
            "initial_reply_instructions": "Say hello.",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert response.status_code == 200

    response = client.put(
        "/api/voice/settings/credential",
        json={"openai_api_key": "sk-second-secret"},
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert response.status_code == 200

    async with sqlite_session_factory() as session:
        preferences = await UserVoicePreferenceService(session).get_preferences(
            "local-user-123",
        )
        credentials = await UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).resolve_credential(
            user_id="local-user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            credential_ref=preferences.credential_ref,
        )
        result = await session.exec(
            select(UserProviderCredentialRecord).where(
                col(UserProviderCredentialRecord.user_id) == "local-user-123",
            ),
        )
        records = result.all()

    statuses = sorted(record.status for record in records)
    assert statuses == [
        ProviderCredentialStatus.ACTIVE.value,
        ProviderCredentialStatus.REVOKED.value,
    ]
    assert preferences.model == "gpt-realtime-preview"
    assert preferences.voice == "cedar"
    assert preferences.provider_config.preferred_language == "British English"
    assert preferences.provider_config.agent_instructions == "Be concise."
    assert preferences.provider_config.initial_reply_instructions == "Say hello."
    assert credentials.token.get_secret_value() == "sk-second-secret"
