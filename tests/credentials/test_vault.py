import pytest
from pydantic import SecretStr

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import StoredEncryptedPayload, UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.local_aes_gcm import LocalAesGcmEncryptionService
from voxos.security.encryption.models import EncryptionBackend


def secret_reference(user_id: str = "user-123") -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider="openai_realtime",
        credential_id="credential-123",
        purpose=SecretPurpose.VOICE_PROVIDER_CREDENTIAL,
    )


@pytest.mark.asyncio
async def test_vault_seals_and_unseals_secret_through_local_backend(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    vault = UserSecretVaultService(
        LocalAesGcmEncryptionService(key=b"0123456789abcdef0123456789abcdef"),
    )

    async with sqlite_session_factory() as session:
        record = await vault.seal_secret(
            session=session,
            secret=SecretStr("sk-test-secret"),
            reference=secret_reference(),
        )
        await session.commit()
        decrypted = await vault.unseal_secret(
            session=session,
            reference=secret_reference(),
        )

    assert record.encryption_backend == EncryptionBackend.LOCAL_AES_GCM.value
    assert record.ciphertext != b"sk-test-secret"
    assert record.encryption_context["purpose"] == (SecretPurpose.VOICE_PROVIDER_CREDENTIAL.value)
    assert decrypted is not None
    assert decrypted.get_secret_value() == "sk-test-secret"
    assert "sk-test-secret" not in str(record)


@pytest.mark.asyncio
async def test_vault_rejects_wrong_owner_context(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    vault = UserSecretVaultService(
        LocalAesGcmEncryptionService(key=b"0123456789abcdef0123456789abcdef"),
    )
    async with sqlite_session_factory() as session:
        await vault.seal_secret(
            session=session,
            secret=SecretStr("sk-test-secret"),
            reference=secret_reference(),
        )
        await session.commit()
        secret = await vault.unseal_secret(
            session=session,
            reference=secret_reference(user_id="other-user"),
        )

    assert secret is None


def test_vault_payload_validation_requires_kms_data_key() -> None:
    payload = StoredEncryptedPayload(
        backend=EncryptionBackend.AWS_KMS_ENVELOPE.value,
        ciphertext=b"ciphertext",
        nonce=b"000000000000",
        encrypted_data_key=None,
        context=secret_reference().encryption_context().as_dict(),
    )

    with pytest.raises(ValueError):
        payload.to_encrypted_payload()


@pytest.mark.asyncio
async def test_vault_unseal_normalizes_malformed_stored_payload(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    vault = UserSecretVaultService(
        LocalAesGcmEncryptionService(key=b"0123456789abcdef0123456789abcdef"),
    )
    reference = secret_reference()

    async with sqlite_session_factory() as session:
        session.add(
            UserSecretRecord(
                user_id=reference.owner.user_id,
                provider=reference.provider,
                credential_id=reference.credential_id,
                purpose=reference.purpose.value,
                ciphertext=b"ciphertext",
                nonce=b"000000000000",
                encrypted_data_key=None,
                encryption_backend=EncryptionBackend.AWS_KMS_ENVELOPE.value,
                encryption_context=reference.encryption_context().as_dict(),
            ),
        )
        await session.commit()

        with pytest.raises(SecretEncryptionError):
            await vault.unseal_secret(session=session, reference=reference)
