import base64
import hmac
import secrets
import time
from binascii import Error as Base64DecodeError
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

import orjson
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from voxos.voice.errors import InvalidVoiceRouteSignatureError

ROUTE_TOKEN_VERSION = 1
ROUTE_TOKEN_SEPARATOR = "."
DEFAULT_ROUTE_CLOCK_SKEW_SECONDS = 60
MIN_ROUTE_SIGNING_SECRET_LENGTH = 32


class SignedVoiceRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=ROUTE_TOKEN_VERSION, alias="v")
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    runtime_session_id: str = Field(min_length=1, max_length=36)
    room_name: str = Field(min_length=1, max_length=512)
    nonce: str = Field(min_length=16, max_length=128)
    issued_at: int = Field(alias="iat", ge=0)
    expires_at: int = Field(alias="exp", ge=0)


@dataclass(frozen=True, slots=True)
class LiveKitRouteSigner:
    secret: SecretStr
    ttl_seconds: int
    clock_skew_seconds: int = DEFAULT_ROUTE_CLOCK_SKEW_SECONDS
    time_clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        if len(self.secret.get_secret_value().strip()) < MIN_ROUTE_SIGNING_SECRET_LENGTH:
            message = "Voice route signing secret must contain at least 32 characters"
            raise ValueError(message)

    def sign(
        self,
        *,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
    ) -> str:
        now = int(self.time_clock())
        route = SignedVoiceRoute(
            user_id=user_id,
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
            room_name=room_name,
            nonce=secrets.token_urlsafe(24),
            iat=now,
            exp=now + self.ttl_seconds,
        )
        payload = orjson.dumps(route.model_dump(mode="json", by_alias=True))
        signature = hmac.new(self._secret_bytes(), payload, sha256).digest()
        return f"{base64url_encode(payload)}{ROUTE_TOKEN_SEPARATOR}{base64url_encode(signature)}"

    def verify(self, token: str, *, room_name: str | None = None) -> SignedVoiceRoute:
        try:
            encoded_payload, encoded_signature = token.split(ROUTE_TOKEN_SEPARATOR, 1)
        except ValueError as exc:
            raise InvalidVoiceRouteSignatureError from exc

        try:
            payload = base64url_decode(encoded_payload)
            signature = base64url_decode(encoded_signature)
        except (Base64DecodeError, ValueError) as exc:
            raise InvalidVoiceRouteSignatureError from exc

        expected_signature = hmac.new(self._secret_bytes(), payload, sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise InvalidVoiceRouteSignatureError

        try:
            route = SignedVoiceRoute.model_validate(orjson.loads(payload))
        except (orjson.JSONDecodeError, ValidationError) as exc:
            raise InvalidVoiceRouteSignatureError from exc

        now = int(self.time_clock())
        if route.version != ROUTE_TOKEN_VERSION:
            raise InvalidVoiceRouteSignatureError
        if route.issued_at > now + self.clock_skew_seconds:
            raise InvalidVoiceRouteSignatureError
        if route.expires_at <= now - self.clock_skew_seconds:
            raise InvalidVoiceRouteSignatureError
        if room_name is not None and route.room_name != room_name:
            raise InvalidVoiceRouteSignatureError
        return route

    def _secret_bytes(self) -> bytes:
        return self.secret.get_secret_value().encode("utf-8")


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
