import logging
import os
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from google.cloud import kms_v1
from pydantic import SecretStr

from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.local_aes_gcm import (
    AES_256_KEY_BYTES,
    AES_GCM_NONCE_BYTES,
)
from voxos.security.encryption.models import (
    EncryptedPayload,
    EncryptionBackend,
    EncryptionContext,
)

logger = logging.getLogger(__name__)
GCP_KMS_KEY_ID_SETTING = "GCP_KMS_KEY_ID"


class EncryptResponse(Protocol):
    ciphertext: bytes


class DecryptResponse(Protocol):
    plaintext: bytes


class AsyncGcpKmsClient(Protocol):
    async def encrypt(self, request: dict[str, object]) -> EncryptResponse:
        """Encrypt one envelope data key."""

    async def decrypt(self, request: dict[str, object]) -> DecryptResponse:
        """Decrypt one envelope data key."""


class GcpKmsClientFactory(Protocol):
    def create_client(self) -> AbstractAsyncContextManager[AsyncGcpKmsClient]:
        """Create an async Google Cloud KMS client context manager."""


class GoogleCloudKmsClientFactory:
    def create_client(self) -> AbstractAsyncContextManager[AsyncGcpKmsClient]:
        return cast(
            AbstractAsyncContextManager[AsyncGcpKmsClient],
            kms_v1.KeyManagementServiceAsyncClient(),
        )


class GcpKmsEnvelopeEncryptionService:
    def __init__(
        self,
        key_id: str,
        kms_client_factory: GcpKmsClientFactory | None = None,
    ) -> None:
        if not key_id:
            raise SecretEncryptionError.missing_config(GCP_KMS_KEY_ID_SETTING)
        self._key_id = key_id
        self._kms_client_factory = kms_client_factory or GoogleCloudKmsClientFactory()

    async def encrypt_secret(
        self,
        secret: SecretStr,
        context: EncryptionContext,
    ) -> EncryptedPayload:
        plaintext_data_key = os.urandom(AES_256_KEY_BYTES)
        try:
            async with self._kms_client_factory.create_client() as client:
                response = await client.encrypt(
                    request={
                        "name": self._key_id,
                        "plaintext": plaintext_data_key,
                        "additional_authenticated_data": context.authenticated_data(),
                    },
                )
            encrypted_data_key = required_bytes(response.ciphertext)
            nonce = os.urandom(AES_GCM_NONCE_BYTES)
            ciphertext = AESGCM(plaintext_data_key).encrypt(
                nonce,
                secret.get_secret_value().encode(),
                context.authenticated_data(),
            )
            return EncryptedPayload(
                backend=EncryptionBackend.GCP_KMS_ENVELOPE,
                ciphertext=ciphertext,
                nonce=nonce,
                encrypted_data_key=encrypted_data_key,
                key_id=self._key_id,
                context=context.as_dict(),
            )
        except SecretEncryptionError:
            raise
        except Exception as exc:
            logger.exception("Google Cloud KMS envelope secret encryption failed")
            raise SecretEncryptionError.encryption_failed() from exc

    async def decrypt_secret(
        self,
        payload: EncryptedPayload,
        context: EncryptionContext,
    ) -> SecretStr:
        if payload.backend != EncryptionBackend.GCP_KMS_ENVELOPE:
            raise SecretEncryptionError.invalid_backend(payload.backend.value)
        if payload.context != context.as_dict() or payload.encrypted_data_key is None:
            raise SecretEncryptionError.decryption_failed()

        try:
            async with self._kms_client_factory.create_client() as client:
                response = await client.decrypt(
                    request={
                        "name": payload.key_id or self._key_id,
                        "ciphertext": payload.encrypted_data_key,
                        "additional_authenticated_data": context.authenticated_data(),
                    },
                )
            plaintext_data_key = required_bytes(response.plaintext)
            plaintext = AESGCM(plaintext_data_key).decrypt(
                payload.nonce,
                payload.ciphertext,
                context.authenticated_data(),
            )
        except InvalidTag as exc:
            raise SecretEncryptionError.decryption_failed() from exc
        except SecretEncryptionError:
            raise
        except Exception as exc:
            logger.exception("Google Cloud KMS envelope secret decryption failed")
            raise SecretEncryptionError.decryption_failed() from exc

        return SecretStr(plaintext.decode())


def required_bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise SecretEncryptionError.decryption_failed()
    return value
