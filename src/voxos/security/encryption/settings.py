from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from voxos.security.encryption.models import EncryptionBackend


class EncryptionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    secret_encryption_backend: EncryptionBackend = Field(
        default=EncryptionBackend.LOCAL_AES_GCM,
        alias="SECRET_ENCRYPTION_BACKEND",
    )
    local_secret_encryption_key: SecretStr | None = Field(
        default=None,
        alias="LOCAL_SECRET_ENCRYPTION_KEY",
    )
    aws_kms_key_id: str | None = Field(default=None, alias="AWS_KMS_KEY_ID")
    aws_region: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AWS_KMS_REGION",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
        ),
    )
    aws_kms_endpoint_url: str | None = Field(
        default=None,
        alias="AWS_KMS_ENDPOINT_URL",
    )
    gcp_kms_key_id: str | None = Field(default=None, alias="GCP_KMS_KEY_ID")
