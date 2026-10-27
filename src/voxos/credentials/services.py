from dataclasses import dataclass

from voxos.credentials.vault import UserSecretVaultService
from voxos.security.encryption.base import SecretEncryptionService


@dataclass(frozen=True, slots=True)
class CredentialsServiceFactory:
    encryption_service: SecretEncryptionService

    def user_secret_vault(self) -> UserSecretVaultService:
        return UserSecretVaultService(self.encryption_service)
