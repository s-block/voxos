import pytest
from pydantic import SecretStr

from voxos.browser_accounts.model_resolution import BrowserTaskModelCredentialResolver
from voxos.browser_accounts.models import BrowserAccountErrorCategory
from voxos.browser_accounts.providers.base import (
    BrowserProviderOperationError,
)
from voxos.browser_runtime.contracts import BrowserTaskModelAdapter
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.text.models import (
    TextProvider,
    TextProviderConfig,
    UserTextPreferences,
)
from voxos.text.storage import UserTextCredentialService, UserTextPreferenceService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "adapter", "model_name"),
    [
        (
            TextProvider.OPENAI,
            "gpt-5.4-mini",
            BrowserTaskModelAdapter.STAGEHAND_NATIVE,
            "openai/gpt-5.4-mini",
        ),
        (
            TextProvider.NVIDIA,
            "openai/gpt-oss-20b",
            BrowserTaskModelAdapter.OPENAI_COMPATIBLE,
            "stepfun-ai/step-3.7-flash",
        ),
        (
            TextProvider.NVIDIA,
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            BrowserTaskModelAdapter.OPENAI_COMPATIBLE,
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        ),
    ],
)
async def test_browser_model_resolution_uses_a_capable_model_for_the_current_provider(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    provider: TextProvider,
    model: str,
    adapter: BrowserTaskModelAdapter,
    model_name: str,
) -> None:
    async with sqlite_session_factory() as session:
        credential = await UserTextCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).create_credential(
            user_id="browser-model-user",
            provider=provider,
            label="Browser model",
            secret=SecretStr("browser-model-secret"),
        )
        await UserTextPreferenceService(session).set_preferences(
            UserTextPreferences(
                user_id="browser-model-user",
                provider=provider,
                model=model,
                credential_ref=credential.id,
                provider_config=TextProviderConfig(),
            ),
        )

    resolved = await BrowserTaskModelCredentialResolver(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    ).resolve(user_id="browser-model-user")

    assert resolved.adapter == adapter
    assert resolved.model_name == model_name
    assert resolved.api_key.get_secret_value() == "browser-model-secret"


@pytest.mark.asyncio
async def test_browser_model_resolution_does_not_silently_default_to_openai(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    resolver = BrowserTaskModelCredentialResolver(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    )

    with pytest.raises(BrowserProviderOperationError) as error:
        await resolver.resolve(user_id="unconfigured-browser-user")

    assert error.value.category == BrowserAccountErrorCategory.PROVIDER_AUTH
