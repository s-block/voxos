from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from types import TracebackType

import pytest
from pydantic import SecretStr

from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.gcp_kms_envelope import (
    AsyncGcpKmsClient,
    GcpKmsEnvelopeEncryptionService,
)
from voxos.security.encryption.models import EncryptionBackend, EncryptionContext

KEY_ID = "projects/example/locations/europe-west2/keyRings/voxos/cryptoKeys/user-secrets"


@dataclass(frozen=True, slots=True)
class FakeEncryptResponse:
    ciphertext: bytes


@dataclass(frozen=True, slots=True)
class FakeDecryptResponse:
    plaintext: bytes


@dataclass(slots=True)
class FakeKmsClient:
    data_key: bytes = b"0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
    encrypted_data_key: bytes = b"encrypted-data-key"
    encrypt_calls: list[dict[str, object]] = field(default_factory=list)
    decrypt_calls: list[dict[str, object]] = field(default_factory=list)

    async def encrypt(self, request: dict[str, object]) -> FakeEncryptResponse:
        self.encrypt_calls.append(request)
        plaintext = request["plaintext"]
        assert isinstance(plaintext, bytes)
        self.data_key = plaintext
        return FakeEncryptResponse(ciphertext=self.encrypted_data_key)

    async def decrypt(self, request: dict[str, object]) -> FakeDecryptResponse:
        self.decrypt_calls.append(request)
        return FakeDecryptResponse(plaintext=self.data_key)


@dataclass(slots=True)
class FakeKmsClientContext(AbstractAsyncContextManager[AsyncGcpKmsClient]):
    client: FakeKmsClient

    async def __aenter__(self) -> AsyncGcpKmsClient:
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

    def create_client(self) -> AbstractAsyncContextManager[AsyncGcpKmsClient]:
        return FakeKmsClientContext(self.client)


def encryption_context() -> EncryptionContext:
    return EncryptionContext(
        user_id="user-123",
        provider="openai_realtime",
        credential_id="credential-123",
        purpose="voice_provider_credential",
    )


@pytest.mark.asyncio
async def test_gcp_kms_envelope_round_trips_secret() -> None:
    client = FakeKmsClient()
    service = GcpKmsEnvelopeEncryptionService(
        key_id=KEY_ID,
        kms_client_factory=FakeKmsClientFactory(client),
    )
    context = encryption_context()

    payload = await service.encrypt_secret(SecretStr("sk-test-secret"), context)
    decrypted = await service.decrypt_secret(payload, context)

    assert payload.backend == EncryptionBackend.GCP_KMS_ENVELOPE
    assert payload.ciphertext != b"sk-test-secret"
    assert payload.encrypted_data_key == b"encrypted-data-key"
    assert payload.key_id == KEY_ID
    assert decrypted.get_secret_value() == "sk-test-secret"
    assert client.encrypt_calls == [
        {
            "name": KEY_ID,
            "plaintext": client.encrypt_calls[0]["plaintext"],
            "additional_authenticated_data": context.authenticated_data(),
        },
    ]
    assert client.decrypt_calls == [
        {
            "name": KEY_ID,
            "ciphertext": b"encrypted-data-key",
            "additional_authenticated_data": context.authenticated_data(),
        },
    ]
    assert "sk-test-secret" not in str(payload)


@pytest.mark.asyncio
async def test_gcp_kms_envelope_rejects_wrong_context() -> None:
    client = FakeKmsClient()
    service = GcpKmsEnvelopeEncryptionService(
        key_id=KEY_ID,
        kms_client_factory=FakeKmsClientFactory(client),
    )
    payload = await service.encrypt_secret(
        SecretStr("sk-test-secret"),
        encryption_context(),
    )

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


def test_gcp_kms_envelope_requires_key_id() -> None:
    with pytest.raises(SecretEncryptionError):
        GcpKmsEnvelopeEncryptionService(key_id="")
