import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.models import (
    EncryptedPayload,
    EncryptionBackend,
    EncryptionContext,
)

AES_256_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12


class LocalAesGcmEncryptionService:
    def __init__(self, key: bytes, key_id: str = "local") -> None:
        if len(key) != AES_256_KEY_BYTES:
            raise SecretEncryptionError.malformed_local_key()
        self._aesgcm = AESGCM(key)
        self._key_id = key_id

    @classmethod
    def from_base64_key(
        cls,
        encoded_key: SecretStr,
        key_id: str = "local",
    ) -> "LocalAesGcmEncryptionService":
        try:
            key = base64.b64decode(
                encoded_key.get_secret_value(),
                validate=True,
            )
        except (binascii.Error, ValueError) as exc:
            raise SecretEncryptionError.malformed_local_key() from exc
        return cls(key=key, key_id=key_id)

    async def encrypt_secret(
        self,
        secret: SecretStr,
        context: EncryptionContext,
    ) -> EncryptedPayload:
        try:
            nonce = os.urandom(AES_GCM_NONCE_BYTES)
            ciphertext = self._aesgcm.encrypt(
                nonce,
                secret.get_secret_value().encode(),
                context.authenticated_data(),
            )
            return EncryptedPayload(
                backend=EncryptionBackend.LOCAL_AES_GCM,
                ciphertext=ciphertext,
                nonce=nonce,
                key_id=self._key_id,
                context=context.as_dict(),
            )
        except SecretEncryptionError:
            raise
        except Exception as exc:
            raise SecretEncryptionError.encryption_failed() from exc

    async def decrypt_secret(
        self,
        payload: EncryptedPayload,
        context: EncryptionContext,
    ) -> SecretStr:
        if payload.backend != EncryptionBackend.LOCAL_AES_GCM:
            raise SecretEncryptionError.invalid_backend(payload.backend.value)
        if payload.context != context.as_dict():
            raise SecretEncryptionError.decryption_failed()

        try:
            plaintext = self._aesgcm.decrypt(
                payload.nonce,
                payload.ciphertext,
                context.authenticated_data(),
            )
        except InvalidTag as exc:
            raise SecretEncryptionError.decryption_failed() from exc
        except Exception as exc:
            raise SecretEncryptionError.decryption_failed() from exc

        return SecretStr(plaintext.decode())
