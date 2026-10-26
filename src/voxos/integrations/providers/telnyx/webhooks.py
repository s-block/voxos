import base64
import binascii
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from voxos.integrations.providers.base import WebhookVerificationResult

TELNYX_SIGNATURE_HEADER_NAME = "telnyx-signature-ed25519"
TELNYX_TIMESTAMP_HEADER_NAME = "telnyx-timestamp"
TELNYX_STREAM_AUTH_TOKEN_HEADER_NAME = "x-telnyx-streaming-auth-token"
TELNYX_SIGNATURE_INVALID_ERROR = "Telnyx webhook signature is invalid."
TELNYX_SIGNATURE_MISSING_ERROR = "Telnyx webhook signature headers are missing."
TELNYX_TIMESTAMP_INVALID_ERROR = "Telnyx webhook timestamp is invalid."
TELNYX_TIMESTAMP_EXPIRED_ERROR = "Telnyx webhook timestamp is outside the allowed replay window."
TELNYX_PUBLIC_KEY_INVALID_ERROR = "Telnyx webhook public key is invalid."
TELNYX_SIGNATURE_REPLAY_TOLERANCE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class TelnyxWebhookVerificationRequest:
    body: bytes
    signature: str | None
    timestamp: str | None
    public_key: str
    now: float | None = None


@dataclass(frozen=True, slots=True)
class TelnyxWebhookVerifier:
    def validate_webhook(
        self,
        request: TelnyxWebhookVerificationRequest,
    ) -> WebhookVerificationResult:
        if request.signature is None or request.timestamp is None:
            return WebhookVerificationResult(
                valid=False,
                reason=TELNYX_SIGNATURE_MISSING_ERROR,
            )
        try:
            timestamp = int(request.timestamp)
        except ValueError:
            return WebhookVerificationResult(
                valid=False,
                reason=TELNYX_TIMESTAMP_INVALID_ERROR,
            )
        now = request.now if request.now is not None else time.time()
        if abs(now - timestamp) > TELNYX_SIGNATURE_REPLAY_TOLERANCE_SECONDS:
            return WebhookVerificationResult(
                valid=False,
                reason=TELNYX_TIMESTAMP_EXPIRED_ERROR,
            )
        try:
            verifier = Ed25519PublicKey.from_public_bytes(
                public_key_bytes(request.public_key),
            )
            verifier.verify(
                signature_bytes(request.signature),
                request.timestamp.encode("utf-8") + b"|" + request.body,
            )
        except (binascii.Error, InvalidSignature, ValueError):
            return WebhookVerificationResult(
                valid=False,
                reason=TELNYX_SIGNATURE_INVALID_ERROR,
            )
        return WebhookVerificationResult(valid=True)


def public_key_bytes(value: str) -> bytes:
    stripped = value.strip()
    if stripped.startswith("-----BEGIN PUBLIC KEY-----"):
        return pem_public_key_bytes(stripped)
    try:
        return bytes.fromhex(stripped)
    except ValueError:
        return base64.b64decode(stripped, validate=True)


def signature_bytes(value: str) -> bytes:
    stripped = value.strip()
    try:
        return base64.b64decode(stripped, validate=True)
    except binascii.Error:
        return bytes.fromhex(stripped)


def pem_public_key_bytes(value: str) -> bytes:
    lines = tuple(
        line.strip()
        for line in value.splitlines()
        if line.strip()
        and not line.startswith("-----BEGIN PUBLIC KEY-----")
        and not line.startswith("-----END PUBLIC KEY-----")
    )
    return base64.b64decode("".join(lines), validate=True)[-32:]
