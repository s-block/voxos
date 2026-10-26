from dataclasses import dataclass

from voxos.browser_accounts.models import BrowserAccountErrorCategory
from voxos.browser_accounts.providers.base import (
    BrowserProviderOperationError,
)
from voxos.browser_runtime.contracts import (
    BrowserTaskModelAdapter,
    BrowserTaskModelCredential,
)
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.text.errors import MissingTextCredentialError, TextPreferencesNotFoundError
from voxos.text.models import (
    TextProviderProtocol,
    effective_text_model,
    text_model_for_browser_use,
    text_provider_definition,
)
from voxos.text.storage import UserTextCredentialService, UserTextPreferenceService


@dataclass(frozen=True, slots=True)
class BrowserTaskModelCredentialResolver:
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService

    async def resolve(
        self,
        *,
        user_id: str,
    ) -> BrowserTaskModelCredential:
        async with self.session_factory() as session:
            preference_service = UserTextPreferenceService(session)
            try:
                preferences = await preference_service.get_preferences(user_id)
            except TextPreferencesNotFoundError as exc:
                raise BrowserProviderOperationError(
                    BrowserAccountErrorCategory.PROVIDER_AUTH,
                ) from exc

            provider = preferences.provider
            model = text_model_for_browser_use(
                provider,
                effective_text_model(preferences),
            )
            if model is None:
                raise BrowserProviderOperationError(
                    BrowserAccountErrorCategory.PROVIDER_REQUEST_INVALID,
                )
            definition = text_provider_definition(provider)
            base_url = definition.base_url
            credential_ref = preferences.credential_ref
            if definition.allow_custom_base_url and preferences.provider_config.base_url is not None:
                base_url = preferences.provider_config.base_url

            credential_service = UserTextCredentialService(
                session=session,
                secret_vault=self.secret_vault,
            )
            try:
                if credential_ref is None:
                    record = await credential_service.get_latest_active_credential_record(
                        user_id=user_id,
                        provider=provider,
                    )
                    credential_ref = record.id
                credential = await credential_service.resolve_credential(
                    user_id=user_id,
                    provider=provider,
                    credential_ref=credential_ref,
                )
            except MissingTextCredentialError as exc:
                raise BrowserProviderOperationError(
                    BrowserAccountErrorCategory.PROVIDER_AUTH,
                ) from exc

            return BrowserTaskModelCredential(
                adapter=(
                    BrowserTaskModelAdapter.OPENAI_COMPATIBLE
                    if definition.protocol == TextProviderProtocol.CHAT_COMPLETIONS
                    else BrowserTaskModelAdapter.STAGEHAND_NATIVE
                ),
                model_name=(
                    model
                    if definition.protocol == TextProviderProtocol.CHAT_COMPLETIONS
                    else f"{provider.value}/{model}"
                ),
                base_url=base_url,
                api_key=credential.token,
            )
