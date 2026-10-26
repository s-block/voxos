import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import orjson
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from voxos.auth.errors import FirebasePublicKeyFetchError, FirebasePublicKeyNotFoundError
from voxos.auth.public_keys import (
    FirebasePublicKeyCache,
    cache_ttl_from_header,
    public_key_pem_from_key_material,
)

PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----"


@dataclass(slots=True)
class MutableClock:
    value: float = 100.0

    def __call__(self) -> float:
        return self.value


@dataclass(slots=True)
class CertEndpoint:
    bodies: tuple[dict[str, str], ...]
    cache_control: str = "public, max-age=60"
    request_count: int = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        index = min(self.request_count - 1, len(self.bodies) - 1)
        return httpx.Response(
            status_code=200,
            content=orjson.dumps(self.bodies[index]),
            headers={"Cache-Control": self.cache_control},
            request=request,
        )


def test_cache_ttl_from_header_uses_max_age_and_caps_value() -> None:
    assert cache_ttl_from_header("public, max-age=120", 300) == 120
    assert cache_ttl_from_header("max-age=999999", 300) == 300
    assert cache_ttl_from_header(None, 300) == 300


@pytest.mark.asyncio
async def test_public_key_cache_fetches_once_before_expiry() -> None:
    clock = MutableClock()
    endpoint = CertEndpoint(bodies=({"kid-1": PUBLIC_KEY},))
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint.handler)) as client:
        cache = FirebasePublicKeyCache(
            http_client=client,
            certs_url="https://firebase.test/certs",
            max_cache_ttl_seconds=300,
            monotonic_clock=clock,
        )

        first = await cache.get_key("kid-1")
        second = await cache.get_key("kid-1")

    assert first == PUBLIC_KEY
    assert second == PUBLIC_KEY
    assert endpoint.request_count == 1


@pytest.mark.asyncio
async def test_public_key_cache_refreshes_after_expiry() -> None:
    clock = MutableClock()
    endpoint = CertEndpoint(
        bodies=(
            {"kid-1": PUBLIC_KEY},
            {"kid-1": PUBLIC_KEY},
        ),
        cache_control="max-age=10",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint.handler)) as client:
        cache = FirebasePublicKeyCache(
            http_client=client,
            certs_url="https://firebase.test/certs",
            max_cache_ttl_seconds=300,
            monotonic_clock=clock,
        )

        await cache.get_key("kid-1")
        clock.value = 111.0
        await cache.get_key("kid-1")

    assert endpoint.request_count == 2


@pytest.mark.asyncio
async def test_public_key_cache_refreshes_once_for_unknown_key_id() -> None:
    endpoint = CertEndpoint(
        bodies=(
            {"kid-1": PUBLIC_KEY},
            {"kid-2": PUBLIC_KEY},
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint.handler)) as client:
        cache = FirebasePublicKeyCache(
            http_client=client,
            certs_url="https://firebase.test/certs",
            max_cache_ttl_seconds=300,
        )

        await cache.get_key("kid-1")
        key = await cache.get_key("kid-2")

    assert key == PUBLIC_KEY
    assert endpoint.request_count == 2


@pytest.mark.asyncio
async def test_public_key_cache_raises_when_key_id_remains_unknown() -> None:
    endpoint = CertEndpoint(bodies=({"kid-1": PUBLIC_KEY},))
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint.handler)) as client:
        cache = FirebasePublicKeyCache(
            http_client=client,
            certs_url="https://firebase.test/certs",
            max_cache_ttl_seconds=300,
        )

        with pytest.raises(FirebasePublicKeyNotFoundError):
            await cache.get_key("missing-kid")


@pytest.mark.asyncio
async def test_public_key_cache_shares_expired_refresh_across_concurrent_requests() -> None:
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        await asyncio.sleep(0.01)
        return httpx.Response(
            status_code=200,
            content=orjson.dumps({"kid-1": PUBLIC_KEY}),
            headers={"Cache-Control": "max-age=60"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache = FirebasePublicKeyCache(
            http_client=client,
            certs_url="https://firebase.test/certs",
            max_cache_ttl_seconds=300,
        )

        keys = await asyncio.gather(*(cache.get_key("kid-1") for _ in range(10)))

    assert keys == [PUBLIC_KEY] * 10
    assert request_count == 1


@pytest.mark.asyncio
async def test_public_key_cache_fails_closed_on_fetch_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache = FirebasePublicKeyCache(
            http_client=client,
            certs_url="https://firebase.test/certs",
            max_cache_ttl_seconds=300,
        )

        with pytest.raises(FirebasePublicKeyFetchError):
            await cache.get_key("kid-1")


@pytest.mark.asyncio
async def test_public_key_cache_fails_closed_on_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            content=b"not-json",
            headers={"Cache-Control": "max-age=60"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache = FirebasePublicKeyCache(
            http_client=client,
            certs_url="https://firebase.test/certs",
            max_cache_ttl_seconds=300,
        )

        with pytest.raises(FirebasePublicKeyFetchError):
            await cache.get_key("kid-1")


def test_public_key_pem_from_key_material_converts_x509_certificate() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "firebase.test")],
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()

    public_key_pem = public_key_pem_from_key_material(certificate_pem)

    assert "BEGIN PUBLIC KEY" in public_key_pem
