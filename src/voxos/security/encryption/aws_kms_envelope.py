import logging
import os
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from aiobotocore.session import get_session
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.local_aes_gcm import AES_GCM_NONCE_BYTES
from voxos.security.encryption.models import (
    EncryptedPayload,
    EncryptionBackend,
    EncryptionContext,
)

logger = logging.getLogger(__name__)
AIOBOTOCORE_SESSION = get_session()
AWS_KMS_DATA_KEY_SPEC = "AES_256"
AWS_KMS_KEY_ID_SETTING = "AWS_KMS_KEY_ID"


class AsyncKmsClient(Protocol):
    async def generate_data_key(self, **kwargs: object) -> Mapping[str, object]:
        """Generate a plaintext data key and encrypted data key."""

    async def decrypt(self, **kwargs: object) -> Mapping[str, object]:
        """Decrypt an encrypted KMS data key."""


class KmsClientFactory(Protocol):
    def create_client(self) -> AbstractAsyncContextManager[AsyncKmsClient]:
        """Create an async KMS client context manager."""


class AiobotocoreKmsClientFactory:
    def __init__(
        self,
        region_name: str | None,
        endpoint_url: str | None = None,
    ) -> None:
        self._region_name = region_name
        self._endpoint_url = endpoint_url

    def create_client(self) -> AbstractAsyncContextManager[AsyncKmsClient]:
        client_kwargs: dict[str, str] = {}
        if self._region_name is not None:
            client_kwargs["region_name"] = self._region_name
        if self._endpoint_url is not None:
            client_kwargs["endpoint_url"] = self._endpoint_url
        return cast(
            AbstractAsyncContextManager[AsyncKmsClient],
            AIOBOTOCORE_SESSION.create_client("kms", **client_kwargs),
        )


class AwsKmsEnvelopeEncryptionService:
    def __init__(
        self,
        key_id: str,
        region_name: str | None = None,
        endpoint_url: str | None = None,
        kms_client_factory: KmsClientFactory | None = None,
    ) -> None:
        if not key_id:
            raise SecretEncryptionError.missing_config(AWS_KMS_KEY_ID_SETTING)
        self._key_id = key_id
        self._kms_client_factory = kms_client_factory or AiobotocoreKmsClientFactory(
            region_name=region_name,
            endpoint_url=endpoint_url,
        )

    async def encrypt_secret(
        self,
        secret: SecretStr,
        context: EncryptionContext,
    ) -> EncryptedPayload:
        try:
            async with self._kms_client_factory.create_client() as client:
                response = await client.generate_data_key(
                    KeyId=self._key_id,
                    KeySpec=AWS_KMS_DATA_KEY_SPEC,
                    EncryptionContext=context.as_dict(),
                )
            plaintext_data_key = required_bytes(response, "Plaintext")
            encrypted_data_key = required_bytes(response, "CiphertextBlob")
            kms_key_id = optional_string(response, "KeyId") or self._key_id
            nonce = os.urandom(AES_GCM_NONCE_BYTES)
            ciphertext = AESGCM(plaintext_data_key).encrypt(
                nonce,
                secret.get_secret_value().encode(),
                context.authenticated_data(),
            )
            return EncryptedPayload(
                backend=EncryptionBackend.AWS_KMS_ENVELOPE,
                ciphertext=ciphertext,
                nonce=nonce,
                encrypted_data_key=encrypted_data_key,
                key_id=kms_key_id,
                context=context.as_dict(),
            )
        except SecretEncryptionError:
            raise
        except Exception as exc:
            logger.exception("AWS KMS envelope secret encryption failed")
            raise SecretEncryptionError.encryption_failed() from exc

    async def decrypt_secret(
        self,
        payload: EncryptedPayload,
        context: EncryptionContext,
    ) -> SecretStr:
        if payload.backend != EncryptionBackend.AWS_KMS_ENVELOPE:
            raise SecretEncryptionError.invalid_backend(payload.backend.value)
        if payload.context != context.as_dict() or payload.encrypted_data_key is None:
            raise SecretEncryptionError.decryption_failed()

        try:
            async with self._kms_client_factory.create_client() as client:
                response = await client.decrypt(
                    CiphertextBlob=payload.encrypted_data_key,
                    EncryptionContext=context.as_dict(),
                )
            plaintext_data_key = required_bytes(response, "Plaintext")
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
            logger.exception("AWS KMS envelope secret decryption failed")
            raise SecretEncryptionError.decryption_failed() from exc

        return SecretStr(plaintext.decode())


def required_bytes(response: Mapping[str, object], field_name: str) -> bytes:
    value = response.get(field_name)
    if not isinstance(value, bytes):
        raise SecretEncryptionError.decryption_failed()
    return value


def optional_string(response: Mapping[str, object], field_name: str) -> str | None:
    value = response.get(field_name)
    if isinstance(value, str):
        return value
    return None
