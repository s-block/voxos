from typing import Self


class SecretEncryptionError(RuntimeError):
    """Base error for secret encryption and decryption failures."""

    @classmethod
    def invalid_backend(cls, backend: str) -> Self:
        return cls(f"Unsupported secret encryption backend: {backend}")

    @classmethod
    def missing_config(cls, setting_name: str) -> Self:
        return cls(f"Missing required secret encryption setting: {setting_name}")

    @classmethod
    def malformed_local_key(cls) -> Self:
        return cls(
            "LOCAL_SECRET_ENCRYPTION_KEY must be base64 encoded 32-byte key material",
        )

    @classmethod
    def encryption_failed(cls) -> Self:
        return cls("Secret encryption failed")

    @classmethod
    def decryption_failed(cls) -> Self:
        return cls("Secret decryption failed")
