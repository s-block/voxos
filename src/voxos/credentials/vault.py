from dataclasses import dataclass, field

from pydantic import SecretStr
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretReference
from voxos.credentials.records import UserSecretRecord, utc_now
from voxos.security.encryption.base import SecretEncryptionService
from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.models import EncryptedPayload, EncryptionBackend


@dataclass(frozen=True, slots=True)
class StoredEncryptedPayload:
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    encrypted_data_key: bytes | None = field(default=None, repr=False)
    backend: str = EncryptionBackend.LOCAL_AES_GCM.value
    key_id: str | None = None
    algorithm: str = "AES-256-GCM"
    context: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_encrypted_payload(
        cls,
        payload: EncryptedPayload,
    ) -> "StoredEncryptedPayload":
        return cls(
            ciphertext=payload.ciphertext,
            nonce=payload.nonce,
            encrypted_data_key=payload.encrypted_data_key,
            backend=payload.backend.value,
            key_id=payload.key_id,
            algorithm=payload.algorithm,
            context=dict(payload.context),
        )

    def to_encrypted_payload(self) -> EncryptedPayload:
        return EncryptedPayload(
            backend=EncryptionBackend(self.backend),
            ciphertext=self.ciphertext,
            nonce=self.nonce,
            encrypted_data_key=self.encrypted_data_key,
            key_id=self.key_id,
            algorithm=self.algorithm,
            context=dict(self.context),
        )


class UserSecretVaultService:
    def __init__(self, encryption_service: SecretEncryptionService) -> None:
        self._encryption_service = encryption_service

    async def seal_secret(
        self,
        session: AsyncSession,
        secret: SecretStr,
        reference: SecretReference,
    ) -> UserSecretRecord:
        payload = await self._encryption_service.encrypt_secret(
            secret=secret,
            context=reference.encryption_context(),
        )
        stored_payload = StoredEncryptedPayload.from_encrypted_payload(payload)
        record = await self._get_record(session=session, reference=reference)
        if record is None:
            record = UserSecretRecord(
                user_id=reference.owner.user_id,
                provider=reference.provider,
                credential_id=reference.credential_id,
                purpose=reference.purpose.value,
                ciphertext=stored_payload.ciphertext,
                nonce=stored_payload.nonce,
                encrypted_data_key=stored_payload.encrypted_data_key,
                encryption_backend=stored_payload.backend,
                encryption_key_id=stored_payload.key_id,
                encryption_algorithm=stored_payload.algorithm,
                encryption_context=stored_payload.context,
            )
            session.add(record)
            return record

        record.ciphertext = stored_payload.ciphertext
        record.nonce = stored_payload.nonce
        record.encrypted_data_key = stored_payload.encrypted_data_key
        record.encryption_backend = stored_payload.backend
        record.encryption_key_id = stored_payload.key_id
        record.encryption_algorithm = stored_payload.algorithm
        record.encryption_context = stored_payload.context
        record.updated_at = utc_now()
        return record

    async def unseal_secret(
        self,
        session: AsyncSession,
        reference: SecretReference,
    ) -> SecretStr | None:
        record = await self._get_record(session=session, reference=reference)
        if record is None:
            return None
        payload = stored_payload_from_record(record)
        try:
            encrypted_payload = payload.to_encrypted_payload()
        except ValueError as exc:
            raise SecretEncryptionError.decryption_failed() from exc
        return await self._encryption_service.decrypt_secret(
            payload=encrypted_payload,
            context=reference.encryption_context(),
        )

    async def delete_secret(
        self,
        session: AsyncSession,
        reference: SecretReference,
    ) -> None:
        record = await self._get_record(session=session, reference=reference)
        if record is not None:
            await session.delete(record)

    async def _get_record(
        self,
        session: AsyncSession,
        reference: SecretReference,
    ) -> UserSecretRecord | None:
        statement = select(UserSecretRecord).where(
            col(UserSecretRecord.user_id) == reference.owner.user_id,
            col(UserSecretRecord.provider) == reference.provider,
            col(UserSecretRecord.credential_id) == reference.credential_id,
            col(UserSecretRecord.purpose) == reference.purpose.value,
        )
        result = await session.exec(statement)
        return result.one_or_none()


def stored_payload_from_record(record: UserSecretRecord) -> StoredEncryptedPayload:
    return StoredEncryptedPayload(
        backend=record.encryption_backend,
        ciphertext=record.ciphertext,
        nonce=record.nonce,
        encrypted_data_key=record.encrypted_data_key,
        key_id=record.encryption_key_id,
        algorithm=record.encryption_algorithm,
        context=record.encryption_context,
    )
