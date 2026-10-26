import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import cast

import httpx
import orjson
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from voxos.auth.errors import (
    FirebasePublicKeyFetchError,
    FirebasePublicKeyNotFoundError,
)

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 300
MAX_AGE_PATTERN = re.compile(r"(?:^|,\s*)max-age=(\d+)(?:,|$)", re.IGNORECASE)

_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


class FirebasePublicKeyCache:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        certs_url: str,
        max_cache_ttl_seconds: int,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http_client = http_client
        self._certs_url = certs_url
        self._max_cache_ttl_seconds = max_cache_ttl_seconds
        self._monotonic_clock = monotonic_clock
        self._keys: dict[str, str] = {}
        self._expires_at = 0.0
        self._refresh_lock = asyncio.Lock()

    async def get_key(self, key_id: str) -> str:
        await self._ensure_fresh()
        key = self._keys.get(key_id)
        if key is not None:
            return key

        await self._refresh_for_key_id(key_id)
        key = self._keys.get(key_id)
        if key is None:
            raise FirebasePublicKeyNotFoundError(key_id)
        return key

    async def _ensure_fresh(self) -> None:
        if not self._is_expired():
            return

        async with self._refresh_lock:
            if self._is_expired():
                await self._fetch_and_replace_keys()

    async def _refresh_for_key_id(self, key_id: str) -> None:
        async with self._refresh_lock:
            if key_id in self._keys and not self._is_expired():
                return
            await self._fetch_and_replace_keys()

    async def _fetch_and_replace_keys(self) -> None:
        try:
            response = await self._http_client.get(self._certs_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "failed to fetch firebase public keys from %s: %s",
                self._certs_url,
                exc.__class__.__name__,
            )
            raise FirebasePublicKeyFetchError from exc

        keys = parse_public_key_response(response.content)
        if not keys:
            raise FirebasePublicKeyFetchError.empty_response()

        ttl_seconds = cache_ttl_from_header(
            cache_control=response.headers.get("Cache-Control"),
            max_cache_ttl_seconds=self._max_cache_ttl_seconds,
        )
        self._keys = keys
        self._expires_at = self._monotonic_clock() + ttl_seconds

    def _is_expired(self) -> bool:
        return self._monotonic_clock() >= self._expires_at


def get_shared_http_client(timeout_seconds: float) -> httpx.AsyncClient:
    global _SHARED_HTTP_CLIENT

    if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
        _SHARED_HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
        )
    return _SHARED_HTTP_CLIENT


async def close_shared_http_client() -> None:
    global _SHARED_HTTP_CLIENT

    if _SHARED_HTTP_CLIENT is not None and not _SHARED_HTTP_CLIENT.is_closed:
        await _SHARED_HTTP_CLIENT.aclose()
    _SHARED_HTTP_CLIENT = None


def cache_ttl_from_header(
    cache_control: str | None,
    max_cache_ttl_seconds: int,
) -> int:
    if cache_control is None:
        return min(DEFAULT_CACHE_TTL_SECONDS, max_cache_ttl_seconds)

    match = MAX_AGE_PATTERN.search(cache_control)
    if match is None:
        return min(DEFAULT_CACHE_TTL_SECONDS, max_cache_ttl_seconds)

    return min(int(match.group(1)), max_cache_ttl_seconds)


def parse_public_key_response(response_content: bytes) -> dict[str, str]:
    try:
        decoded = orjson.loads(response_content)
    except orjson.JSONDecodeError as exc:
        raise FirebasePublicKeyFetchError.invalid_response() from exc

    if not isinstance(decoded, dict):
        raise FirebasePublicKeyFetchError.invalid_response()

    keys: dict[str, str] = {}
    decoded_mapping = cast(dict[object, object], decoded)
    for key_id, key_material in decoded_mapping.items():
        if not isinstance(key_id, str) or not isinstance(key_material, str):
            raise FirebasePublicKeyFetchError.invalid_response()
        keys[key_id] = public_key_pem_from_key_material(key_material)

    return keys


def public_key_pem_from_key_material(key_material: str) -> str:
    if "BEGIN PUBLIC KEY" in key_material:
        return key_material

    if "BEGIN CERTIFICATE" not in key_material:
        raise FirebasePublicKeyFetchError.invalid_material()

    try:
        certificate = x509.load_pem_x509_certificate(key_material.encode("utf-8"))
    except ValueError as exc:
        raise FirebasePublicKeyFetchError.invalid_certificate() from exc

    public_key = certificate.public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise FirebasePublicKeyFetchError.non_rsa_certificate()

    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
