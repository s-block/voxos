import base64

import pytest
from pydantic import SecretStr

from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.local_aes_gcm import LocalAesGcmEncryptionService
from voxos.security.encryption.models import EncryptionBackend, EncryptionContext


def test_local_aes_gcm_rejects_malformed_key() -> None:
    with pytest.raises(SecretEncryptionError):
        LocalAesGcmEncryptionService(key=b"too-short")

    with pytest.raises(SecretEncryptionError):
        LocalAesGcmEncryptionService.from_base64_key(SecretStr("not-base64"))


@pytest.mark.asyncio
async def test_local_aes_gcm_round_trips_secret() -> None:
    key = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    service = LocalAesGcmEncryptionService.from_base64_key(SecretStr(key))
    context = EncryptionContext(
        user_id="user-123",
        provider="openai_realtime",
        credential_id="credential-123",
        purpose="voice_provider_credential",
    )

    payload = await service.encrypt_secret(SecretStr("sk-test-secret"), context)
    decrypted = await service.decrypt_secret(payload, context)

    assert payload.backend == EncryptionBackend.LOCAL_AES_GCM
    assert payload.ciphertext != b"sk-test-secret"
    assert payload.encrypted_data_key is None
    assert decrypted.get_secret_value() == "sk-test-secret"
    assert "sk-test-secret" not in str(payload)


@pytest.mark.asyncio
async def test_local_aes_gcm_uses_random_nonce() -> None:
    service = LocalAesGcmEncryptionService(
        key=b"0123456789abcdef0123456789abcdef",
    )
    context = EncryptionContext(
        user_id="user-123",
        provider="openai_realtime",
        credential_id="credential-123",
        purpose="voice_provider_credential",
    )

    first = await service.encrypt_secret(SecretStr("sk-test-secret"), context)
    second = await service.encrypt_secret(SecretStr("sk-test-secret"), context)

    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


@pytest.mark.asyncio
async def test_local_aes_gcm_rejects_wrong_context() -> None:
    service = LocalAesGcmEncryptionService(
        key=b"0123456789abcdef0123456789abcdef",
    )
    context = EncryptionContext(
        user_id="user-123",
        provider="openai_realtime",
        credential_id="credential-123",
        purpose="voice_provider_credential",
    )
    wrong_context = EncryptionContext(
        user_id="user-456",
        provider="openai_realtime",
        credential_id="credential-123",
        purpose="voice_provider_credential",
    )
    payload = await service.encrypt_secret(SecretStr("sk-test-secret"), context)

    with pytest.raises(SecretEncryptionError):
        await service.decrypt_secret(payload, wrong_context)
