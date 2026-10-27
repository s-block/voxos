from voxos.security.encryption.aws_kms_envelope import AwsKmsEnvelopeEncryptionService
from voxos.security.encryption.base import SecretEncryptionService
from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.gcp_kms_envelope import GcpKmsEnvelopeEncryptionService
from voxos.security.encryption.local_aes_gcm import LocalAesGcmEncryptionService
from voxos.security.encryption.models import EncryptionBackend
from voxos.security.encryption.settings import EncryptionSettings

LOCAL_SECRET_ENCRYPTION_KEY_SETTING = "LOCAL_SECRET_ENCRYPTION_KEY"
AWS_KMS_KEY_ID_SETTING = "AWS_KMS_KEY_ID"
GCP_KMS_KEY_ID_SETTING = "GCP_KMS_KEY_ID"


def create_secret_encryption_service(
    settings: EncryptionSettings,
) -> SecretEncryptionService:
    if settings.secret_encryption_backend == EncryptionBackend.LOCAL_AES_GCM:
        if settings.local_secret_encryption_key is None:
            raise SecretEncryptionError.missing_config(
                LOCAL_SECRET_ENCRYPTION_KEY_SETTING,
            )
        return LocalAesGcmEncryptionService.from_base64_key(
            settings.local_secret_encryption_key,
        )

    if settings.secret_encryption_backend == EncryptionBackend.AWS_KMS_ENVELOPE:
        if settings.aws_kms_key_id is None:
            raise SecretEncryptionError.missing_config(AWS_KMS_KEY_ID_SETTING)
        return AwsKmsEnvelopeEncryptionService(
            key_id=settings.aws_kms_key_id,
            region_name=settings.aws_region,
            endpoint_url=settings.aws_kms_endpoint_url,
        )

    if settings.secret_encryption_backend == EncryptionBackend.GCP_KMS_ENVELOPE:
        if settings.gcp_kms_key_id is None:
            raise SecretEncryptionError.missing_config(GCP_KMS_KEY_ID_SETTING)
        return GcpKmsEnvelopeEncryptionService(key_id=settings.gcp_kms_key_id)

    raise SecretEncryptionError.invalid_backend(
        settings.secret_encryption_backend.value,
    )
