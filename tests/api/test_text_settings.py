from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import col, select
from starlette.requests import Request

from voxos.api.text_settings import create_text_settings_router
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.models import SecretPurpose
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.providers.model_catalog import (
    NVIDIA_IMAGE_GENERATION_MODEL_OPTIONS,
    NVIDIA_TEXT_MODEL_OPTIONS,
    OPENAI_IMAGE_GENERATION_MODEL_OPTIONS,
    OPENAI_TEXT_MODEL_OPTIONS,
    XAI_IMAGE_GENERATION_MODEL_OPTIONS,
)
from voxos.providers.models import RealtimeVoiceProvider
from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
    TrustedOutboundEndpoint,
    TrustedOutboundEndpointKind,
)
from voxos.text.models import TextProvider
from voxos.text.storage import UserTextPreferenceService
from voxos.user_settings.services import UserCredentialService


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


def authenticated_user(user_id: str = "local-user-123") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        firebase_user_id=f"firebase-{user_id}",
        email="user@example.com",
        email_verified=True,
    )


def create_test_client(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    *,
    image_input_enabled: bool = True,
    trusted_text_endpoint_urls: tuple[str, ...] = (),
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_text_settings_router(
            auth_dependency=StaticAuthDependency(authenticated_user()),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            image_input_enabled=image_input_enabled,
            outbound_endpoint_policy=OutboundEndpointPolicy(
                trusted_endpoints=tuple(
                    TrustedOutboundEndpoint(
                        purpose=OutboundEndpointPurpose.TEXT_PROVIDER,
                        kind=TrustedOutboundEndpointKind.LOCAL_CONTAINER,
                        url=url,
                    )
                    for url in trusted_text_endpoint_urls
                ),
            ),
        ),
    )
    return TestClient(app)


def test_text_provider_settings_returns_default_unconfigured_state(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.get("/api/text/provider-settings")

    assert response.status_code == 200
    body = response.json()
    openai_models = [option.model for option in OPENAI_TEXT_MODEL_OPTIONS]
    assert body["active_provider"] == "openai"
    assert body["image_input_enabled"] is True
    assert body["active_settings"] == {
        "configured": False,
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "preferred_language": "English",
        "agent_instructions": "You are a helpful assistant.",
        "temperature": None,
        "allowed_subagent_models": openai_models,
        "subagent_model": "gpt-5.6-terra",
        "image_generation_provider": "openai",
        "image_generation_model": "gpt-image-2",
    }
    assert {provider["provider"] for provider in body["providers"]} == {
        "openai",
        "openai_compatible",
        "xai",
        "nvidia",
    }
    assert {provider["provider"]: provider["logo"]["domain"] for provider in body["providers"]} == {
        "openai": "openai.com",
        "openai_compatible": "openai.com",
        "xai": "x.ai",
        "nvidia": "nvidia.com",
    }
    openai_compatible = next(provider for provider in body["providers"] if provider["provider"] == "openai_compatible")
    assert openai_compatible["configured"] is False
    assert openai_compatible["base_url"] is None
    assert openai_compatible["model_options"] == []
    assert openai_compatible["default_image_generation_model"] is None
    openai_provider = next(provider for provider in body["providers"] if provider["provider"] == "openai")
    assert [option["model"] for option in openai_provider["model_options"]] == openai_models
    assert "function calling" in openai_provider["model_options"][3]["description"]
    assert "$0.75 input" in openai_provider["model_options"][4]["pricing_summary"]
    assert openai_provider["model_options"][0]["recommended"] is False
    assert openai_provider["model_options"][4]["recommended"] is True
    assert openai_provider["default_image_generation_model"] == "gpt-image-2"
    assert [option["model"] for option in openai_provider["image_generation_model_options"]] == [
        option.model for option in OPENAI_IMAGE_GENERATION_MODEL_OPTIONS
    ]
    assert openai_provider["image_generation_model_options"][0]["hosted_api_supports_image_editing"] is True
    nvidia_provider = next(provider for provider in body["providers"] if provider["provider"] == "nvidia")
    assert nvidia_provider["default_model"] == "openai/gpt-oss-20b"
    assert nvidia_provider["default_multimodal_model"] == "stepfun-ai/step-3.7-flash"
    assert nvidia_provider["default_browser_model"] == "stepfun-ai/step-3.7-flash"
    assert nvidia_provider["default_image_generation_model"] == ("black-forest-labs/flux.2-klein-4b")
    assert [option["model"] for option in nvidia_provider["image_generation_model_options"]] == [
        option.model for option in NVIDIA_IMAGE_GENERATION_MODEL_OPTIONS
    ]
    assert nvidia_provider["image_generation_model_options"][0]["supports_image_editing"] is True
    assert nvidia_provider["image_generation_model_options"][0]["hosted_api_supports_image_editing"] is False
    nvidia_models = [option.model for option in NVIDIA_TEXT_MODEL_OPTIONS]
    assert [option["model"] for option in nvidia_provider["model_options"]] == nvidia_models
    assert nvidia_provider["suggested_models"] == nvidia_models
    assert nvidia_provider["allowed_subagent_models"] == nvidia_models
    assert nvidia_provider["model_options"][0]["recommended"] is False
    assert (
        next(option for option in nvidia_provider["model_options"] if option["model"] == "openai/gpt-oss-20b")[
            "recommended"
        ]
        is True
    )
    assert "tool-using" in nvidia_provider["model_options"][0]["description"]
    assert "function-calling" in nvidia_provider["model_options"][4]["description"]
    assert all("Free endpoint available" in option["pricing_summary"] for option in nvidia_provider["model_options"])
    assert {option["model"] for option in nvidia_provider["model_options"] if option["supports_image_input"]} == {
        "stepfun-ai/step-3.7-flash",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "minimaxai/minimax-m3",
    }
    assert all(option["supports_tool_calling"] for option in nvidia_provider["model_options"])
    assert {option["model"] for option in nvidia_provider["model_options"] if option["supports_browser_use"]} == {
        "stepfun-ai/step-3.7-flash",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "minimaxai/minimax-m3",
    }
    assert all(
        option["supports_image_input"]
        for provider in body["providers"]
        if provider["provider"] != "nvidia"
        for option in provider["model_options"]
    )
    xai_provider = next(provider for provider in body["providers"] if provider["provider"] == "xai")
    assert [option["model"] for option in xai_provider["image_generation_model_options"]] == [
        option.model for option in XAI_IMAGE_GENERATION_MODEL_OPTIONS
    ]


@pytest.mark.asyncio
async def test_openai_compatible_endpoint_supports_manual_models_without_a_key(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory,
        user_secret_vault,
        trusted_text_endpoint_urls=("http://model-host:11434/v1",),
    )

    endpoint_response = client.put(
        "/api/text/settings/openai-compatible",
        json={
            "base_url": "http://model-host:11434/v1/",
            "available_models": [" gpt-oss:20b ", "qwen3:8b", "gpt-oss:20b"],
            "api_key": None,
        },
    )

    assert endpoint_response.status_code == 200
    body = endpoint_response.json()
    assert body["active_provider"] == "openai"
    custom_provider = next(provider for provider in body["providers"] if provider["provider"] == "openai_compatible")
    assert custom_provider["configured"] is True
    assert custom_provider["base_url"] == "http://model-host:11434/v1"
    assert [option["model"] for option in custom_provider["model_options"]] == [
        "gpt-oss:20b",
        "qwen3:8b",
    ]
    assert custom_provider["credential_last_four"] is None

    settings_response = client.put(
        "/api/text/settings",
        json={
            "provider": "openai_compatible",
            "model": "gpt-oss:20b",
            "preferred_language": "English",
            "agent_instructions": "Use tools when needed.",
            "temperature": 0.2,
            "allowed_subagent_models": ["gpt-oss:20b", "qwen3:8b"],
            "subagent_model": "qwen3:8b",
        },
    )

    assert settings_response.status_code == 200
    assert settings_response.json()["configured"] is True
    assert settings_response.json()["provider"] == "openai_compatible"
    async with sqlite_session_factory() as session:
        preferences = await UserTextPreferenceService(session).get_preferences(
            "local-user-123",
        )
    assert preferences.credential_ref is None
    assert preferences.provider_config.base_url == "http://model-host:11434/v1"
    assert preferences.provider_config.available_models == (
        "gpt-oss:20b",
        "qwen3:8b",
    )


def test_openai_compatible_endpoint_rejects_unregistered_local_hosts(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.put(
        "/api/text/settings/openai-compatible",
        json={
            "base_url": "http://localhost:11434/v1",
            "available_models": ["gpt-oss:20b"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "OpenAI-compatible endpoint URL is not permitted"


def test_openai_compatible_endpoint_accepts_exact_registered_localhost(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory,
        user_secret_vault,
        trusted_text_endpoint_urls=("http://localhost:11434/v1",),
    )

    response = client.put(
        "/api/text/settings/openai-compatible",
        json={
            "base_url": "http://localhost:11434/v1/",
            "available_models": ["gpt-oss:20b"],
        },
    )

    assert response.status_code == 200
    custom_provider = next(
        provider for provider in response.json()["providers"] if provider["provider"] == "openai_compatible"
    )
    assert custom_provider["base_url"] == "http://localhost:11434/v1"


@pytest.mark.asyncio
async def test_openai_compatible_endpoint_stores_optional_key_in_the_vault(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory,
        user_secret_vault,
        trusted_text_endpoint_urls=("http://model-host:8000/v1",),
    )

    response = client.put(
        "/api/text/settings/openai-compatible",
        json={
            "base_url": "http://model-host:8000/v1",
            "available_models": ["private-model"],
            "api_key": "private-endpoint-token",  # pragma: allowlist secret
        },
    )

    assert response.status_code == 200
    custom_provider = next(
        provider for provider in response.json()["providers"] if provider["provider"] == "openai_compatible"
    )
    assert custom_provider["credential_last_four"] == "oken"
    assert "private-endpoint-token" not in response.text
    async with sqlite_session_factory() as session:
        preferences = await UserTextPreferenceService(session).get_preferences(
            "local-user-123",
        )
        secrets = await session.exec(
            select(UserSecretRecord).where(
                col(UserSecretRecord.user_id) == "local-user-123",
                col(UserSecretRecord.purpose) == SecretPurpose.TEXT_PROVIDER_CREDENTIAL.value,
            ),
        )
    assert preferences.provider_config.openai_compatible_credential_ref is not None
    assert len(secrets.all()) == 1


def test_text_provider_settings_keeps_model_capabilities_when_rollout_is_disabled(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory,
        user_secret_vault,
        image_input_enabled=False,
    )

    response = client.get("/api/text/provider-settings")

    assert response.status_code == 200
    body = response.json()
    assert body["image_input_enabled"] is False
    assert any(option["supports_image_input"] for provider in body["providers"] for option in provider["model_options"])


def test_saving_a_text_key_without_activation_preserves_text_defaults(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.put(
        "/api/text/settings/provider-credential",
        json={
            "provider": "nvidia",
            "api_key": "nvidia-test-credential",  # pragma: allowlist secret
            "activate": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["active_provider"] == "openai"
    assert body["active_settings"]["configured"] is False
    assert body["active_settings"]["image_generation_provider"] == "nvidia"
    assert body["active_settings"]["image_generation_model"] == ("black-forest-labs/flux.2-klein-4b")
    nvidia = next(provider for provider in body["providers"] if provider["provider"] == "nvidia")
    assert nvidia["configured"] is True


@pytest.mark.asyncio
async def test_text_provider_settings_saves_encrypted_key_and_preferences(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    key_response = client.put(
        "/api/text/settings/provider-credential",
        json={
            "provider": "openai",
            "api_key": "test-text-credential-value",  # pragma: allowlist secret
            "activate": True,
        },
    )

    assert key_response.status_code == 200
    key_body = key_response.json()
    assert key_body["active_settings"]["configured"] is True
    assert "test-text-credential-value" not in key_response.text

    settings_response = client.put(
        "/api/text/settings",
        json={
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "preferred_language": "British English",
            "agent_instructions": "Be direct.",
            "temperature": 0.2,
            "allowed_subagent_models": ["gpt-5.6-sol", "gpt-5.6-luna"],
            "subagent_model": "gpt-5.6-luna",
        },
    )

    assert settings_response.status_code == 200
    assert settings_response.json() == {
        "configured": True,
        "provider": "openai",
        "model": "gpt-5.6-sol",
        "preferred_language": "British English",
        "agent_instructions": "Be direct.",
        "temperature": 0.2,
        "allowed_subagent_models": ["gpt-5.6-sol", "gpt-5.6-luna"],
        "subagent_model": "gpt-5.6-luna",
        "image_generation_provider": "openai",
        "image_generation_model": "gpt-image-2",
    }

    async with sqlite_session_factory() as session:
        preferences = await UserTextPreferenceService(session).get_preferences(
            "local-user-123",
        )
        secrets = await session.exec(
            select(UserSecretRecord).where(
                col(UserSecretRecord.user_id) == "local-user-123",
                col(UserSecretRecord.purpose) == SecretPurpose.TEXT_PROVIDER_CREDENTIAL.value,
            ),
        )

    assert preferences.provider == TextProvider.OPENAI
    assert preferences.model == "gpt-5.6-sol"
    assert preferences.provider_config.agent_instructions == "Be direct."
    assert preferences.provider_config.allowed_subagent_models == (
        "gpt-5.6-sol",
        "gpt-5.6-luna",
    )
    assert preferences.provider_config.subagent_model == "gpt-5.6-luna"
    assert len(secrets.all()) == 1


@pytest.mark.asyncio
async def test_image_generation_settings_use_an_independent_configured_provider(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)
    key_response = client.put(
        "/api/text/settings/provider-credential",
        json={
            "provider": "nvidia",
            "api_key": "nvidia-image-credential-value",  # pragma: allowlist secret
            "activate": False,
        },
    )
    assert key_response.status_code == 200

    settings_response = client.put(
        "/api/text/settings/image-generation",
        json={
            "provider": "nvidia",
            "model": "black-forest-labs/flux.1-schnell",
        },
    )

    assert settings_response.status_code == 200
    assert settings_response.json()["provider"] == "openai"
    assert settings_response.json()["image_generation_provider"] == "nvidia"
    assert settings_response.json()["image_generation_model"] == ("black-forest-labs/flux.1-schnell")

    async with sqlite_session_factory() as session:
        preferences = await UserTextPreferenceService(session).get_preferences(
            "local-user-123",
        )

    assert preferences.provider == TextProvider.OPENAI
    assert preferences.provider_config.image_generation_provider == TextProvider.NVIDIA
    assert preferences.provider_config.image_generation_model == ("black-forest-labs/flux.1-schnell")


def test_image_generation_settings_reject_unknown_models(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    response = client.put(
        "/api/text/settings/image-generation",
        json={"provider": "nvidia", "model": "stale/image-model"},
    )

    assert response.status_code == 422
    assert "current catalog model for nvidia" in response.text


@pytest.mark.asyncio
async def test_nvidia_text_settings_save_a_vault_key_and_enforce_temperature_limit(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(sqlite_session_factory, user_secret_vault)

    key_response = client.put(
        "/api/text/settings/provider-credential",
        json={
            "provider": "nvidia",
            "api_key": "nvidia-text-credential-value",  # pragma: allowlist secret
            "activate": True,
        },
    )

    assert key_response.status_code == 200
    assert "nvidia-text-credential-value" not in key_response.text
    assert key_response.json()["active_settings"]["provider"] == "nvidia"

    invalid_temperature_response = client.put(
        "/api/text/settings",
        json={
            "provider": "nvidia",
            "model": "nvidia/nemotron-3-nano-30b-a3b",
            "temperature": 1.1,
        },
    )

    assert invalid_temperature_response.status_code == 422
    assert "between 0 and 1 for nvidia" in invalid_temperature_response.text

    deprecated_model_response = client.put(
        "/api/text/settings",
        json={
            "provider": "nvidia",
            "model": "deepseek-ai/deepseek-v4-flash",
            "temperature": 0.6,
        },
    )

    assert deprecated_model_response.status_code == 422
    assert "current catalog model for nvidia" in deprecated_model_response.text

    settings_response = client.put(
        "/api/text/settings",
        json={
            "provider": "nvidia",
            "model": "nvidia/nemotron-3-nano-30b-a3b",
            "temperature": 0.6,
        },
    )

    assert settings_response.status_code == 200
    assert settings_response.json()["provider"] == "nvidia"
    async with sqlite_session_factory() as session:
        preferences = await UserTextPreferenceService(session).get_preferences(
            "local-user-123",
        )

    assert preferences.provider == TextProvider.NVIDIA
    assert preferences.provider_config.temperature == 0.6


@pytest.mark.asyncio
async def test_text_settings_can_reuse_compatible_voice_credential(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        voice_credential = await UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).create_credential(
            user_id="local-user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            label="OpenAI voice",
            secret=SecretStr("sk-shared-openai"),
        )

    client = create_test_client(sqlite_session_factory, user_secret_vault)
    provider_response = client.get("/api/text/provider-settings")

    assert provider_response.status_code == 200
    openai_summary = next(
        provider for provider in provider_response.json()["providers"] if provider["provider"] == "openai"
    )
    assert openai_summary["configured"] is True

    settings_response = client.put(
        "/api/text/settings",
        json={
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "preferred_language": "English",
            "agent_instructions": "Use the shared key.",
            "temperature": None,
        },
    )

    assert settings_response.status_code == 200
    async with sqlite_session_factory() as session:
        preferences = await UserTextPreferenceService(session).get_preferences(
            "local-user-123",
        )

    assert preferences.credential_ref == voice_credential.id
