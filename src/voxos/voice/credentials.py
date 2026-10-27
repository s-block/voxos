from dataclasses import dataclass
from typing import Protocol

from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.providers.models import RealtimeVoiceProvider
from voxos.user_settings.services import UserCredentialService
from voxos.voice.models import ResolvedVoiceCredentials


class VoiceCredentialResolver(Protocol):
    async def resolve(
        self,
        user_id: str,
        provider: RealtimeVoiceProvider,
        credential_ref: str,
    ) -> ResolvedVoiceCredentials:
        """Resolve provider credentials without reading ordinary preferences."""


@dataclass(frozen=True, slots=True)
class SqlModelVoiceCredentialResolver:
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService

    async def resolve(
        self,
        user_id: str,
        provider: RealtimeVoiceProvider,
        credential_ref: str,
    ) -> ResolvedVoiceCredentials:
        async with self.session_factory() as session:
            service = UserCredentialService(
                session=session,
                secret_vault=self.secret_vault,
            )
            return await service.resolve_credential(
                user_id=user_id,
                provider=provider,
                credential_ref=credential_ref,
            )
