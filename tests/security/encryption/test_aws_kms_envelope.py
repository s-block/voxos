from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import TracebackType

import pytest
from pydantic import SecretStr

from voxos.security.encryption.aws_kms_envelope import (
    AsyncKmsClient,
    AwsKmsEnvelopeEncryptionService,
)
from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.models import EncryptionBackend, EncryptionContext


@dataclass(slots=True)
class FakeKmsClient:
    data_key: bytes = b"0123456789abcdef0123456789abcdef"
    encrypted_data_key: bytes = b"encrypted-data-key"
    generate_calls: list[Mapping[str, object]] = field(default_factory=list)
    decrypt_calls: list[Mapping[str, object]] = field(default_factory=list)

    async def generate_data_key(self, **kwargs: object) -> Mapping[str, object]:
        self.generate_calls.append(kwargs)
        return {
            "Plaintext": self.data_key,
            "CiphertextBlob": self.encrypted_data_key,
            "KeyId": "arn:aws:kms:eu-west-2:123:key/test",
        }

    async def decrypt(self, **kwargs: object) -> Mapping[str, object]:
        self.decrypt_calls.append(kwargs)
        return {"Plaintext": self.data_key}


@dataclass(slots=True)
class FakeKmsClientContext(AbstractAsyncContextManager[AsyncKmsClient]):
    client: FakeKmsClient

    async def __aenter__(self) -> AsyncKmsClient:
        return self.client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


@dataclass(slots=True)
class FakeKmsClientFactory:
    client: FakeKmsClient

    def create_client(self) -> AbstractAsyncContextManager[AsyncKmsClient]:
        return FakeKmsClientContext(self.client)


@pytest.mark.asyncio
async def test_aws_kms_envelope_round_trips_secret() -> None:
    client = FakeKmsClient()
    service = AwsKmsEnvelopeEncryptionService(
        key_id="alias/voxos-test",
        kms_client_factory=FakeKmsClientFactory(client),
    )
    context = EncryptionContext(
        user_id="user-123",
        provider="openai_realtime",
        credential_id="credential-123",
        purpose="voice_provider_credential",
    )

    payload = await service.encrypt_secret(SecretStr("sk-test-secret"), context)
    decrypted = await service.decrypt_secret(payload, context)

    assert payload.backend == EncryptionBackend.AWS_KMS_ENVELOPE
    assert payload.ciphertext != b"sk-test-secret"
    assert payload.encrypted_data_key == b"encrypted-data-key"
    assert decrypted.get_secret_value() == "sk-test-secret"
    assert client.generate_calls[0]["KeyId"] == "alias/voxos-test"
    assert client.generate_calls[0]["EncryptionContext"] == context.as_dict()
    assert client.decrypt_calls[0]["CiphertextBlob"] == b"encrypted-data-key"
    assert client.decrypt_calls[0]["EncryptionContext"] == context.as_dict()
    assert "sk-test-secret" not in str(payload)


@pytest.mark.asyncio
async def test_aws_kms_envelope_rejects_wrong_context() -> None:
    client = FakeKmsClient()
    service = AwsKmsEnvelopeEncryptionService(
        key_id="alias/voxos-test",
        kms_client_factory=FakeKmsClientFactory(client),
    )
    context = EncryptionContext(
        user_id="user-123",
        provider="openai_realtime",
        credential_id="credential-123",
        purpose="voice_provider_credential",
    )
    payload = await service.encrypt_secret(SecretStr("sk-test-secret"), context)

    with pytest.raises(SecretEncryptionError):
        await service.decrypt_secret(
            payload,
            EncryptionContext(
                user_id="user-456",
                provider="openai_realtime",
                credential_id="credential-123",
                purpose="voice_provider_credential",
            ),
        )


def test_aws_kms_envelope_requires_key_id() -> None:
    with pytest.raises(SecretEncryptionError):
        AwsKmsEnvelopeEncryptionService(key_id="")
