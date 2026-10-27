import base64
import hmac
import secrets
import time
from hashlib import sha256

import orjson
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from voxos.mcp.errors import McpOAuthStateError

STATE_VERSION = 1
STATE_NONCE_BYTES = 24


class SignedMcpOAuthState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(alias="v")
    user_id: str = Field(min_length=1, max_length=256)
    server_id: str = Field(min_length=1, max_length=36)
    nonce: str = Field(min_length=1)
    issued_at: int = Field(alias="iat", ge=0)
    expires_at: int = Field(alias="exp", ge=0)


class McpOAuthStateSigner:
    def __init__(
        self,
        *,
        secret: SecretStr,
        ttl_seconds: int = 600,
        clock_skew_seconds: int = 60,
    ) -> None:
        secret_value = secret.get_secret_value().strip()
        if not secret_value:
            raise McpOAuthStateError
        self._secret = secret_value.encode()
        self._ttl_seconds = ttl_seconds
        self._clock_skew_seconds = clock_skew_seconds

    def sign(self, *, user_id: str, server_id: str) -> str:
        now = int(time.time())
        payload = SignedMcpOAuthState(
            v=STATE_VERSION,
            user_id=user_id.strip(),
            server_id=server_id.strip(),
            nonce=secrets.token_urlsafe(STATE_NONCE_BYTES),
            iat=now,
            exp=now + self._ttl_seconds,
        )
        payload_bytes = orjson.dumps(payload.model_dump(by_alias=True))
        payload_text = base64url_encode(payload_bytes)
        signature_text = base64url_encode(self._signature(payload_text))
        return f"{payload_text}.{signature_text}"

    def verify(
        self,
        state: str,
        *,
        server_id: str | None = None,
    ) -> SignedMcpOAuthState:
        payload_text, signature_text = split_state(state)
        expected_signature = base64url_encode(self._signature(payload_text))
        if not hmac.compare_digest(signature_text, expected_signature):
            raise McpOAuthStateError

        payload = SignedMcpOAuthState.model_validate_json(
            base64url_decode(payload_text),
        )
        if payload.version != STATE_VERSION:
            raise McpOAuthStateError
        if server_id is not None and payload.server_id != server_id:
            raise McpOAuthStateError
        now = int(time.time())
        if payload.issued_at > now + self._clock_skew_seconds:
            raise McpOAuthStateError
        if payload.expires_at + self._clock_skew_seconds < now:
            raise McpOAuthStateError
        return payload

    def _signature(self, payload_text: str) -> bytes:
        return hmac.new(
            self._secret,
            payload_text.encode(),
            sha256,
        ).digest()


def split_state(state: str) -> tuple[str, str]:
    try:
        payload_text, signature_text = state.split(".", 1)
    except ValueError as exc:
        raise McpOAuthStateError from exc
    if not payload_text or not signature_text:
        raise McpOAuthStateError
    return payload_text, signature_text


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(f"{value}{padding}")
    except ValueError as exc:
        raise McpOAuthStateError from exc
