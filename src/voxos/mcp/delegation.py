from __future__ import annotations

import base64
import hmac
import secrets
import time
from collections.abc import Generator
from hashlib import sha256
from typing import TYPE_CHECKING

import httpx2 as httpx
import orjson
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

if TYPE_CHECKING:
    from voxos.mcp.settings import McpSettings

DELEGATION_VERSION = 1
DELEGATION_AUDIENCE = "voxos-mcp-credential-broker"
DELEGATION_PROVIDER_MONZO = "monzo"
DELEGATION_NONCE_BYTES = 24


class McpCredentialDelegationError(Exception):
    """A credential delegation assertion is missing, invalid, or expired."""


class SignedMcpCredentialDelegation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(alias="v")
    audience: str = Field(alias="aud", min_length=1, max_length=128)
    user_id: str = Field(alias="sub", min_length=1, max_length=256)
    server_id: str = Field(min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=64)
    nonce: str = Field(alias="jti", min_length=1)
    issued_at: int = Field(alias="iat", ge=0)
    expires_at: int = Field(alias="exp", ge=0)


class McpCredentialDelegationSigner:
    """Issue and verify short-lived, audience-bound broker assertions."""

    def __init__(
        self,
        *,
        secret: SecretStr,
        ttl_seconds: int = 60,
        clock_skew_seconds: int = 15,
    ) -> None:
        secret_value = secret.get_secret_value().strip()
        if len(secret_value.encode()) < 32:
            raise McpCredentialDelegationError
        self._secret = secret_value.encode()
        self._ttl_seconds = ttl_seconds
        self._clock_skew_seconds = clock_skew_seconds

    def sign(self, *, user_id: str, server_id: str, provider: str) -> str:
        now = int(time.time())
        payload = SignedMcpCredentialDelegation(
            v=DELEGATION_VERSION,
            aud=DELEGATION_AUDIENCE,
            sub=user_id.strip(),
            server_id=server_id.strip(),
            provider=provider.strip(),
            jti=secrets.token_urlsafe(DELEGATION_NONCE_BYTES),
            iat=now,
            exp=now + self._ttl_seconds,
        )
        payload_text = _base64url_encode(orjson.dumps(payload.model_dump(by_alias=True)))
        signature_text = _base64url_encode(self._signature(payload_text))
        return f"{payload_text}.{signature_text}"

    def verify(self, assertion: str) -> SignedMcpCredentialDelegation:
        try:
            payload_text, signature_text = assertion.split(".", 1)
            if not payload_text or not signature_text:
                raise ValueError
            expected_signature = _base64url_encode(self._signature(payload_text))
            if not hmac.compare_digest(signature_text, expected_signature):
                raise ValueError
            payload = SignedMcpCredentialDelegation.model_validate_json(_base64url_decode(payload_text))
        except (ValueError, ValidationError):
            raise McpCredentialDelegationError from None

        now = int(time.time())
        if (
            payload.version != DELEGATION_VERSION
            or payload.audience != DELEGATION_AUDIENCE
            or payload.issued_at > now + self._clock_skew_seconds
            or payload.expires_at + self._clock_skew_seconds < now
        ):
            raise McpCredentialDelegationError
        return payload

    def _signature(self, payload_text: str) -> bytes:
        return hmac.new(
            self._secret,
            payload_text.encode(),
            sha256,
        ).digest()


class McpCredentialDelegationHttpAuth(httpx.Auth):
    """Mint a fresh broker assertion for every outbound MCP HTTP request."""

    def __init__(
        self,
        *,
        signer: McpCredentialDelegationSigner,
        user_id: str,
        server_id: str,
        provider: str,
        header_name: str,
    ) -> None:
        self._signer = signer
        self._user_id = user_id
        self._server_id = server_id
        self._provider = provider
        self._header_name = header_name

    def auth_flow(
        self,
        request: httpx.Request,
    ) -> Generator[httpx.Request, httpx.Response, None]:
        assertion = self._signer.sign(
            user_id=self._user_id,
            server_id=self._server_id,
            provider=self._provider,
        )
        request.headers[self._header_name] = f"Bearer {assertion}"
        yield request


def credential_delegation_signer_from_settings(
    settings: McpSettings,
) -> McpCredentialDelegationSigner | None:
    secret = settings.mcp_credential_delegation_signing_secret or settings.mcp_oauth_state_signing_secret
    if secret is None:
        return None
    return McpCredentialDelegationSigner(
        secret=secret,
        ttl_seconds=settings.mcp_credential_delegation_ttl_seconds,
        clock_skew_seconds=(settings.mcp_credential_delegation_clock_skew_seconds),
    )


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(f"{value}{padding}")
    except ValueError:
        raise McpCredentialDelegationError from None
