import base64
import binascii
import hmac
import secrets
from dataclasses import dataclass
from hashlib import sha256

import orjson
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

WHATSAPP_SESSION_DATA_KEY_BYTES = 32
WHATSAPP_SESSION_NONCE_BYTES = 12
WHATSAPP_SESSION_CIPHER_VERSION = 1


class WhatsAppCryptoError(RuntimeError):
    def __init__(self, message: str = "WhatsApp session data could not be decrypted") -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class WhatsAppCipherContext:
    user_id: str
    integration_id: str
    key_category: str
    logical_key_id: str
    revision: int

    def associated_data(self) -> bytes:
        return orjson.dumps(
            (
                WHATSAPP_SESSION_CIPHER_VERSION,
                self.user_id,
                self.integration_id,
                self.key_category,
                self.logical_key_id,
                self.revision,
            ),
        )


@dataclass(frozen=True, slots=True)
class WhatsAppEncryptedValue:
    ciphertext: bytes
    nonce: bytes
    revision: int


class WhatsAppSessionCipher:
    def __init__(self, data_key: bytes) -> None:
        if len(data_key) != WHATSAPP_SESSION_DATA_KEY_BYTES:
            message = "WhatsApp session data key must contain exactly 32 bytes"
            raise ValueError(message)
        self._data_key = data_key
        self._cipher = AESGCM(data_key)

    def encrypt(
        self,
        *,
        plaintext: bytes,
        context: WhatsAppCipherContext,
    ) -> WhatsAppEncryptedValue:
        nonce = secrets.token_bytes(WHATSAPP_SESSION_NONCE_BYTES)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext,
            context.associated_data(),
        )
        return WhatsAppEncryptedValue(
            ciphertext=ciphertext,
            nonce=nonce,
            revision=context.revision,
        )

    def decrypt(
        self,
        *,
        encrypted: WhatsAppEncryptedValue,
        context: WhatsAppCipherContext,
    ) -> bytes:
        if encrypted.revision != context.revision:
            raise WhatsAppCryptoError
        try:
            return self._cipher.decrypt(
                encrypted.nonce,
                encrypted.ciphertext,
                context.associated_data(),
            )
        except (InvalidTag, ValueError) as error:
            raise WhatsAppCryptoError from error

    def identity_fingerprint(self, normalized_phone_number: str) -> str:
        return hmac.new(
            self._data_key,
            b"voxos-whatsapp-identity-v1\0" + normalized_phone_number.encode("utf-8"),
            sha256,
        ).hexdigest()


def generate_session_data_key() -> bytes:
    return secrets.token_bytes(WHATSAPP_SESSION_DATA_KEY_BYTES)


def encode_session_data_key(data_key: bytes) -> SecretStr:
    if len(data_key) != WHATSAPP_SESSION_DATA_KEY_BYTES:
        message = "WhatsApp session data key must contain exactly 32 bytes"
        raise ValueError(message)
    return SecretStr(base64.b64encode(data_key).decode("ascii"))


def decode_session_data_key(encoded_data_key: SecretStr) -> bytes:
    try:
        decoded = base64.b64decode(
            encoded_data_key.get_secret_value().encode("ascii"),
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error) as error:
        message = "WhatsApp session data key is invalid"
        raise ValueError(message) from error
    if len(decoded) != WHATSAPP_SESSION_DATA_KEY_BYTES:
        message = "WhatsApp session data key must contain exactly 32 bytes"
        raise ValueError(message)
    return decoded
