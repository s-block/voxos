import time
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from voxos.auth.errors import (
    FirebasePublicKeyNotFoundError,
    InvalidFirebaseTokenError,
)
from voxos.auth.firebase import FirebaseTokenVerifier
from voxos.auth.models import FirebaseTokenClaims

PROJECT_ID = "test-firebase-project"
ISSUER = f"https://securetoken.google.com/{PROJECT_ID}"
KEY_ID = "kid-1"


@dataclass(slots=True)
class RsaKeyPair:
    private_pem: str
    public_pem: str


@dataclass(slots=True)
class StaticPublicKeyCache:
    public_key: str
    key_id: str = KEY_ID

    async def get_key(self, key_id: str) -> str:
        if key_id != self.key_id:
            raise FirebasePublicKeyNotFoundError(key_id)
        return self.public_key


@dataclass(slots=True)
class RecordingRevocationChecker:
    rejected: bool = False
    seen_token: str | None = None
    seen_claims: FirebaseTokenClaims | None = None

    async def verify_not_revoked(self, token: str, claims: FirebaseTokenClaims) -> None:
        self.seen_token = token
        self.seen_claims = claims
        if self.rejected:
            raise InvalidFirebaseTokenError


def generate_rsa_key_pair() -> RsaKeyPair:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return RsaKeyPair(private_pem=private_pem, public_pem=public_pem)


def firebase_payload(overrides: dict[str, object] | None = None) -> dict[str, object]:
    now = int(time.time())
    payload: dict[str, object] = {
        "aud": PROJECT_ID,
        "iss": ISSUER,
        "sub": "firebase-user-123",
        "email": "user@example.com",
        "email_verified": True,
        "name": "Test User",
        "picture": "https://example.com/user.png",
        "auth_time": now - 10,
        "iat": now - 10,
        "exp": now + 3600,
        "role": "tester",
    }
    if overrides is not None:
        payload.update(overrides)
    return payload


def encode_token(
    key_pair: RsaKeyPair,
    payload: dict[str, object] | None = None,
    key_id: str | None = KEY_ID,
) -> str:
    headers: dict[str, str] = {}
    if key_id is not None:
        headers["kid"] = key_id
    return jwt.encode(
        payload or firebase_payload(),
        key_pair.private_pem,
        algorithm="RS256",
        headers=headers,
    )


def create_verifier(
    public_key: str,
    clock_skew_seconds: int = 60,
    revocation_checker: RecordingRevocationChecker | None = None,
) -> FirebaseTokenVerifier:
    return FirebaseTokenVerifier(
        public_key_cache=StaticPublicKeyCache(public_key),
        project_id=PROJECT_ID,
        issuer=ISSUER,
        clock_skew_seconds=clock_skew_seconds,
        revocation_checker=revocation_checker,
    )


@pytest.mark.asyncio
async def test_firebase_token_verifier_returns_verified_firebase_identity() -> None:
    key_pair = generate_rsa_key_pair()
    verifier = create_verifier(key_pair.public_pem)
    token = encode_token(key_pair)

    identity = await verifier.verify(token)

    assert identity.firebase_user_id == "firebase-user-123"
    assert str(identity.email) == "user@example.com"
    assert identity.email_verified is True
    assert identity.display_name == "Test User"
    assert identity.photo_url == "https://example.com/user.png"
    assert identity.claims.custom_claims == {"role": "tester"}


@pytest.mark.asyncio
async def test_firebase_token_verifier_checks_revocation_when_configured() -> None:
    key_pair = generate_rsa_key_pair()
    checker = RecordingRevocationChecker()
    verifier = create_verifier(
        key_pair.public_pem,
        revocation_checker=checker,
    )
    token = encode_token(key_pair)

    identity = await verifier.verify(token)

    assert identity.firebase_user_id == "firebase-user-123"
    assert checker.seen_token == token
    assert checker.seen_claims is not None
    assert checker.seen_claims.uid == "firebase-user-123"


@pytest.mark.asyncio
async def test_firebase_token_verifier_rejects_revoked_token() -> None:
    key_pair = generate_rsa_key_pair()
    verifier = create_verifier(
        key_pair.public_pem,
        revocation_checker=RecordingRevocationChecker(rejected=True),
    )
    token = encode_token(key_pair)

    with pytest.raises(InvalidFirebaseTokenError):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_firebase_token_verifier_rejects_invalid_signature() -> None:
    trusted_key_pair = generate_rsa_key_pair()
    untrusted_key_pair = generate_rsa_key_pair()
    verifier = create_verifier(trusted_key_pair.public_pem)
    token = encode_token(untrusted_key_pair)

    with pytest.raises(InvalidFirebaseTokenError) as exc_info:
        await verifier.verify(token)

    assert token not in str(exc_info.value)


@pytest.mark.asyncio
async def test_firebase_token_verifier_rejects_unsupported_algorithm() -> None:
    verifier = create_verifier(generate_rsa_key_pair().public_pem)
    token = jwt.encode(
        firebase_payload(),
        "not-used-for-rs256-with-enough-length",
        algorithm="HS256",
        headers={"kid": KEY_ID},
    )

    with pytest.raises(InvalidFirebaseTokenError):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_firebase_token_verifier_rejects_missing_key_id() -> None:
    key_pair = generate_rsa_key_pair()
    verifier = create_verifier(key_pair.public_pem)
    token = encode_token(key_pair, key_id=None)

    with pytest.raises(InvalidFirebaseTokenError):
        await verifier.verify(token)


@pytest.mark.asyncio
async def test_firebase_token_verifier_rejects_unknown_key_id() -> None:
    key_pair = generate_rsa_key_pair()
    verifier = create_verifier(key_pair.public_pem)
    token = encode_token(key_pair, key_id="unknown-kid")

    with pytest.raises(FirebasePublicKeyNotFoundError):
        await verifier.verify(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "other-project"},
        {"iss": "https://securetoken.google.com/other-project"},
        {"exp": int(time.time()) - 3600},
        {"iat": int(time.time()) + 3600},
        {"auth_time": int(time.time()) + 3600},
        {"sub": ""},
    ],
)
async def test_firebase_token_verifier_rejects_invalid_claims(
    overrides: dict[str, object],
) -> None:
    key_pair = generate_rsa_key_pair()
    verifier = create_verifier(key_pair.public_pem)
    token = encode_token(key_pair, firebase_payload(overrides))

    with pytest.raises(InvalidFirebaseTokenError):
        await verifier.verify(token)
