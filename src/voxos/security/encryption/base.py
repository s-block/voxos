from typing import Protocol

from pydantic import SecretStr

from voxos.security.encryption.models import EncryptedPayload, EncryptionContext


class SecretEncryptionService(Protocol):
    async def encrypt_secret(
        self,
        secret: SecretStr,
        context: EncryptionContext,
    ) -> EncryptedPayload:
        """Encrypt a user secret with authenticated context."""

    async def decrypt_secret(
        self,
        payload: EncryptedPayload,
        context: EncryptionContext,
    ) -> SecretStr:
        """Decrypt a user secret with authenticated context."""
