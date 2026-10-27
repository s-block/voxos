import base64

import pytest
from pydantic import SecretStr

from voxos.security.encryption.aws_kms_envelope import AwsKmsEnvelopeEncryptionService
from voxos.security.encryption.errors import SecretEncryptionError
from voxos.security.encryption.factory import create_secret_encryption_service
from voxos.security.encryption.gcp_kms_envelope import GcpKmsEnvelopeEncryptionService
from voxos.security.encryption.local_aes_gcm import LocalAesGcmEncryptionService
from voxos.security.encryption.models import EncryptionBackend
from voxos.security.encryption.settings import EncryptionSettings

LOCAL_KEY = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


def test_factory_creates_local_backend_from_generic_setting() -> None:
    settings = EncryptionSettings(
        secret_encryption_backend=EncryptionBackend.LOCAL_AES_GCM,
        local_secret_encryption_key=SecretStr(LOCAL_KEY),
    )

    service = create_secret_encryption_service(settings)

    assert isinstance(service, LocalAesGcmEncryptionService)


def test_factory_requires_local_key_for_local_backend() -> None:
    settings = EncryptionSettings(
        secret_encryption_backend=EncryptionBackend.LOCAL_AES_GCM,
        local_secret_encryption_key=None,
    )

    with pytest.raises(SecretEncryptionError):
        create_secret_encryption_service(settings)


def test_factory_creates_kms_backend_with_endpoint_url() -> None:
    settings = EncryptionSettings(
        secret_encryption_backend=EncryptionBackend.AWS_KMS_ENVELOPE,
        aws_kms_key_id="alias/voxos-test",
        aws_region="eu-west-2",
        aws_kms_endpoint_url="http://localhost:4566",
    )

    service = create_secret_encryption_service(settings)

    assert isinstance(service, AwsKmsEnvelopeEncryptionService)


def test_factory_requires_kms_key_id_for_kms_backend() -> None:
    settings = EncryptionSettings(
        secret_encryption_backend=EncryptionBackend.AWS_KMS_ENVELOPE,
        aws_kms_key_id=None,
    )

    with pytest.raises(SecretEncryptionError):
        create_secret_encryption_service(settings)


def test_factory_creates_gcp_kms_backend() -> None:
    settings = EncryptionSettings(
        secret_encryption_backend=EncryptionBackend.GCP_KMS_ENVELOPE,
        gcp_kms_key_id=("projects/example/locations/europe-west2/keyRings/voxos/cryptoKeys/user-secrets"),
    )

    service = create_secret_encryption_service(settings)

    assert isinstance(service, GcpKmsEnvelopeEncryptionService)


def test_factory_requires_gcp_kms_key_id() -> None:
    settings = EncryptionSettings(
        secret_encryption_backend=EncryptionBackend.GCP_KMS_ENVELOPE,
        gcp_kms_key_id=None,
    )

    with pytest.raises(SecretEncryptionError):
        create_secret_encryption_service(settings)
