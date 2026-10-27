from enum import StrEnum
from typing import Self

import orjson
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EncryptionBackend(StrEnum):
    LOCAL_AES_GCM = "local_aes_gcm"
    AWS_KMS_ENVELOPE = "aws_kms_envelope"
    GCP_KMS_ENVELOPE = "gcp_kms_envelope"


class EncryptionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    credential_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)

    def as_dict(self) -> dict[str, str]:
        return self.model_dump(mode="json")

    def authenticated_data(self) -> bytes:
        return orjson.dumps(self.as_dict(), option=orjson.OPT_SORT_KEYS)


class EncryptedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: EncryptionBackend
    ciphertext: bytes = Field(repr=False)
    nonce: bytes = Field(repr=False, min_length=12, max_length=12)
    algorithm: str = Field(default="AES-256-GCM", min_length=1)
    key_id: str | None = Field(default=None, min_length=1)
    encrypted_data_key: bytes | None = Field(default=None, repr=False)
    context: dict[str, str] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_backend_metadata(self) -> Self:
        if (
            self.backend
            in {
                EncryptionBackend.AWS_KMS_ENVELOPE,
                EncryptionBackend.GCP_KMS_ENVELOPE,
            }
            and self.encrypted_data_key is None
        ):
            message = "KMS envelope payloads require encrypted_data_key"
            raise ValueError(message)
        return self
