import pytest
from cryptography.exceptions import InvalidTag

from voxos.whatsapp.crypto import (
    WhatsAppCipherContext,
    WhatsAppCryptoError,
    WhatsAppEncryptedValue,
    WhatsAppSessionCipher,
    decode_session_data_key,
    encode_session_data_key,
    generate_session_data_key,
)


def cipher_context(**changes: str | int) -> WhatsAppCipherContext:
    values: dict[str, str | int] = {
        "user_id": "user-123",
        "integration_id": "integration-123",
        "key_category": "creds",
        "logical_key_id": "noise-key",
        "revision": 1,
    }
    values.update(changes)
    return WhatsAppCipherContext(
        user_id=str(values["user_id"]),
        integration_id=str(values["integration_id"]),
        key_category=str(values["key_category"]),
        logical_key_id=str(values["logical_key_id"]),
        revision=int(values["revision"]),
    )


def test_session_cipher_round_trip_uses_fresh_nonces() -> None:
    cipher = WhatsAppSessionCipher(generate_session_data_key())
    context = cipher_context()

    first = cipher.encrypt(plaintext=b"private auth state", context=context)
    second = cipher.encrypt(plaintext=b"private auth state", context=context)

    assert first.ciphertext != b"private auth state"
    assert first.nonce != second.nonce
    assert cipher.decrypt(encrypted=first, context=context) == b"private auth state"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_id", "other-user"),
        ("integration_id", "other-integration"),
        ("key_category", "keys"),
        ("logical_key_id", "other-key"),
        ("revision", 2),
    ],
)
def test_session_cipher_rejects_wrong_context(field: str, value: str | int) -> None:
    cipher = WhatsAppSessionCipher(generate_session_data_key())
    encrypted = cipher.encrypt(
        plaintext=b"private auth state",
        context=cipher_context(),
    )

    with pytest.raises(WhatsAppCryptoError):
        cipher.decrypt(
            encrypted=encrypted,
            context=cipher_context(**{field: value}),
        )


def test_session_cipher_rejects_tampering_without_exposing_crypto_error() -> None:
    cipher = WhatsAppSessionCipher(generate_session_data_key())
    context = cipher_context()
    encrypted = cipher.encrypt(plaintext=b"private auth state", context=context)
    tampered = WhatsAppEncryptedValue(
        ciphertext=encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1]),
        nonce=encrypted.nonce,
        revision=encrypted.revision,
    )

    with pytest.raises(WhatsAppCryptoError) as raised:
        cipher.decrypt(encrypted=tampered, context=context)

    assert isinstance(raised.value.__cause__, InvalidTag)
    assert "user-123" not in str(raised.value)


def test_session_data_key_encoding_round_trip() -> None:
    data_key = generate_session_data_key()

    assert decode_session_data_key(encode_session_data_key(data_key)) == data_key


def test_session_data_key_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        encode_session_data_key(b"too short")
