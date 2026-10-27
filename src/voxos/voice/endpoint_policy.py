import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from voxos.voice.errors import DisallowedLiveKitEndpointError

EndpointAddressResolver = Callable[[str, int], Awaitable[tuple[str, ...]]]
LIVEKIT_URL_SCHEMES = frozenset({"ws", "wss"})
INVALID_LIVEKIT_LOG_TARGET = "invalid-livekit-endpoint"
HOSTNAME_FORBIDDEN_CHARACTERS = frozenset("%/\\?#@")


class LiveKitEndpointPolicy(StrEnum):
    PUBLIC_WSS = "public_wss"
    TRUSTED_PRIVATE = "trusted_private"


def normalize_livekit_url(value: str) -> str:
    """Validate and canonicalize a tenant-owned LiveKit WebSocket URL."""
    stripped_value = value.strip()
    if not stripped_value or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in stripped_value
    ):
        message = "LiveKit URL must not be empty or contain whitespace or controls"
        raise ValueError(message)
    if "?" in stripped_value or "#" in stripped_value or "\\" in stripped_value:
        message = "LiveKit URL contains unsupported URL components"
        raise ValueError(message)

    try:
        parsed = urlsplit(stripped_value)
        port = parsed.port
    except ValueError as exc:
        message = "LiveKit URL is invalid"
        raise ValueError(message) from exc
    if parsed.netloc.endswith(":"):
        message = "LiveKit URL contains an invalid empty port"
        raise ValueError(message)

    scheme = parsed.scheme.lower()
    if scheme not in LIVEKIT_URL_SCHEMES:
        message = "LiveKit URL must use ws or wss"
        raise ValueError(message)
    if parsed.username is not None or parsed.password is not None:
        message = "LiveKit URL must not contain user information"
        raise ValueError(message)
    if parsed.path not in {"", "/"}:
        message = "LiveKit URL must not contain a path"
        raise ValueError(message)

    hostname = parsed.hostname
    if hostname is None or not _is_safe_hostname(hostname):
        message = "LiveKit URL must contain a valid hostname"
        raise ValueError(message)

    canonical_hostname = _canonical_hostname(hostname)
    canonical_port = port
    if (scheme == "ws" and port == 80) or (scheme == "wss" and port == 443):
        canonical_port = None
    netloc = _format_netloc(canonical_hostname, canonical_port)
    return urlunsplit((scheme, netloc, "", "", ""))


def livekit_log_target(value: str) -> str:
    """Return a non-secret LiveKit origin suitable for structured logs."""
    try:
        parsed = urlsplit(normalize_livekit_url(value))
        hostname = parsed.hostname
        if hostname is None:
            return INVALID_LIVEKIT_LOG_TARGET
        return urlunsplit(
            (
                parsed.scheme,
                _format_netloc(hostname, parsed.port),
                "",
                "",
                "",
            ),
        )
    except ValueError:
        return INVALID_LIVEKIT_LOG_TARGET


async def validate_livekit_worker_endpoint(
    value: str,
    *,
    policy: LiveKitEndpointPolicy,
    resolver: EndpointAddressResolver | None = None,
) -> str:
    """Validate the network target immediately before worker registration.

    This is an application-layer guard, not a replacement for deployment egress
    controls. DNS can change between this check and the LiveKit SDK connection.
    """
    normalized = normalize_livekit_url(value)
    if policy == LiveKitEndpointPolicy.TRUSTED_PRIVATE:
        return normalized

    parsed = urlsplit(normalized)
    hostname = parsed.hostname
    if parsed.scheme != "wss" or hostname is None:
        raise DisallowedLiveKitEndpointError
    address_resolver = resolver or resolve_endpoint_addresses
    try:
        addresses = await address_resolver(hostname, parsed.port or 443)
    except OSError as exc:
        raise DisallowedLiveKitEndpointError from exc
    if not addresses:
        raise DisallowedLiveKitEndpointError
    try:
        resolved_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
    except ValueError as exc:
        raise DisallowedLiveKitEndpointError from exc
    if any(not address.is_global for address in resolved_addresses):
        raise DisallowedLiveKitEndpointError
    return normalized


async def resolve_endpoint_addresses(hostname: str, port: int) -> tuple[str, ...]:
    address_info = await asyncio.get_running_loop().getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    return tuple(sorted({str(item[4][0]) for item in address_info}))


def _is_safe_hostname(hostname: str) -> bool:
    return bool(hostname) and not any(
        character.isspace() or character in HOSTNAME_FORBIDDEN_CHARACTERS for character in hostname
    )


def _canonical_hostname(hostname: str) -> str:
    try:
        return hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        message = "LiveKit URL must contain a valid hostname"
        raise ValueError(message) from exc


def _format_netloc(hostname: str, port: int | None) -> str:
    formatted_hostname = f"[{hostname}]" if ":" in hostname else hostname
    if port is None:
        return formatted_hostname
    return f"{formatted_hostname}:{port}"
