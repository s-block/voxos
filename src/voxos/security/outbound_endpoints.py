import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

HTTP_URL_HOST_ERROR = "URL must include a host"
HTTP_URL_SCHEME_ERROR = "URL must use http or https"
MCP_OUTBOUND_URL_SCHEME_ERROR = "MCP outbound URL must use https"
MCP_OUTBOUND_URL_CREDENTIALS_ERROR = "MCP outbound URL must not include credentials"
MCP_OUTBOUND_URL_FRAGMENT_ERROR = "MCP outbound URL must not include a fragment"
MCP_OUTBOUND_URL_PRIVATE_HOST_ERROR = "MCP outbound URL must use a public host"
MCP_OUTBOUND_URL_DNS_ERROR = "MCP outbound URL host could not be resolved safely"
OUTBOUND_URL_SCHEME_ERROR = "Outbound URL must use https"
OUTBOUND_URL_PORT_ERROR = "Public outbound URL must use port 443"
OUTBOUND_URL_QUERY_ERROR = "Text provider base URL must not include a query"
TRUSTED_ENDPOINT_CREDENTIALS_ERROR = "Trusted endpoint URL must not include credentials"
TRUSTED_ENDPOINT_FRAGMENT_ERROR = "Trusted endpoint URL must not include a fragment"
TRUSTED_ENDPOINT_QUERY_ERROR = "Trusted endpoint URL must not include a query"
TRUSTED_ENDPOINT_DUPLICATE_ERROR = "Trusted outbound endpoints must be unique by purpose and URL"

LOCAL_HOSTNAMES = frozenset({"localhost"})
LOCAL_HOSTNAME_SUFFIXES = (".localhost", ".local")

ResolvedIpAddresses = tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]
HostAddressResolver = Callable[[str], Awaitable[ResolvedIpAddresses]]


class OutboundEndpointPurpose(StrEnum):
    MCP = "mcp"
    TEXT_PROVIDER = "text_provider"


class TrustedOutboundEndpointKind(StrEnum):
    LOCAL_CONTAINER = "local_container"


class TrustedOutboundEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: OutboundEndpointPurpose
    kind: TrustedOutboundEndpointKind
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url", mode="before")
    @classmethod
    def strip_urls(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_endpoint(self) -> "TrustedOutboundEndpoint":
        parsed = parsed_endpoint_url(self.url)
        validate_trusted_url_components(parsed)
        return self

    @property
    def normalized_url(self) -> str:
        return normalize_endpoint_url(self.url)


class OutboundEndpointSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    trusted_endpoints: tuple[TrustedOutboundEndpoint, ...] = Field(
        default_factory=tuple,
        alias="OUTBOUND_TRUSTED_ENDPOINTS_JSON",
    )
    additional_trusted_endpoints: tuple[TrustedOutboundEndpoint, ...] = Field(
        default_factory=tuple,
        alias="OUTBOUND_ADDITIONAL_TRUSTED_ENDPOINTS_JSON",
    )

    @property
    def all_trusted_endpoints(self) -> tuple[TrustedOutboundEndpoint, ...]:
        return (*self.trusted_endpoints, *self.additional_trusted_endpoints)

    @model_validator(mode="after")
    def validate_unique_endpoints(self) -> "OutboundEndpointSettings":
        keys = [(endpoint.purpose, endpoint.normalized_url) for endpoint in self.all_trusted_endpoints]
        if len(keys) != len(set(keys)):
            raise ValueError(TRUSTED_ENDPOINT_DUPLICATE_ERROR)
        return self


@dataclass(frozen=True, slots=True)
class ResolvedOutboundEndpoint:
    url: str


@dataclass(frozen=True, slots=True)
class OutboundEndpointPolicy:
    trusted_endpoints: tuple[TrustedOutboundEndpoint, ...] = ()
    address_resolver: HostAddressResolver = field(
        default_factory=lambda: resolve_host_addresses,
    )

    @classmethod
    def from_settings(
        cls,
        settings: OutboundEndpointSettings | None = None,
    ) -> "OutboundEndpointPolicy":
        resolved_settings = settings or OutboundEndpointSettings()
        return cls(trusted_endpoints=resolved_settings.all_trusted_endpoints)

    async def resolve_url(
        self,
        value: str,
        purpose: OutboundEndpointPurpose,
    ) -> ResolvedOutboundEndpoint:
        normalized = normalize_endpoint_url(value)
        trusted = self.trusted_endpoint(normalized, purpose)
        if trusted is not None:
            return ResolvedOutboundEndpoint(url=normalized)

        parsed = parsed_endpoint_url(normalized)
        validate_public_url_components(parsed, purpose)
        host = parsed.hostname
        if host is None:
            raise ValueError(HTTP_URL_HOST_ERROR)
        validate_public_host(host)
        normalized_host = normalize_host(host)
        if not is_ip_literal(normalized_host):
            addresses = await self.address_resolver(normalized_host)
            if not addresses:
                raise ValueError(MCP_OUTBOUND_URL_DNS_ERROR)
            for address in addresses:
                validate_public_ip_address(address)
        return ResolvedOutboundEndpoint(url=normalized)

    def trusted_endpoint(
        self,
        value: str,
        purpose: OutboundEndpointPurpose,
    ) -> TrustedOutboundEndpoint | None:
        normalized = normalize_endpoint_url(value)
        return next(
            (
                endpoint
                for endpoint in self.trusted_endpoints
                if endpoint.purpose == purpose and endpoint.normalized_url == normalized
            ),
            None,
        )

    def allows_trusted_url(
        self,
        value: str,
        purpose: OutboundEndpointPurpose,
    ) -> bool:
        return self.trusted_endpoint(value, purpose) is not None

    def trusted_urls(self, purpose: OutboundEndpointPurpose) -> frozenset[str]:
        return frozenset(endpoint.normalized_url for endpoint in self.trusted_endpoints if endpoint.purpose == purpose)


def normalize_endpoint_url(value: str) -> str:
    parsed = parsed_endpoint_url(value.strip())
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(MCP_OUTBOUND_URL_CREDENTIALS_ERROR)
    host = parsed.hostname
    if host is None:
        raise ValueError(HTTP_URL_HOST_ERROR)
    normalized_host = normalize_host(host)
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if isinstance(address, ipaddress.IPv6Address):
        normalized_host = f"[{normalized_host}]"
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(OUTBOUND_URL_PORT_ERROR) from exc
    default_port = (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    netloc = normalized_host if port is None or default_port else f"{normalized_host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    normalized = SplitResult(
        scheme=parsed.scheme.lower(),
        netloc=netloc,
        path=path,
        query=parsed.query,
        fragment=parsed.fragment,
    )
    return urlunsplit(normalized)


def parsed_endpoint_url(value: str) -> SplitResult:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(OUTBOUND_URL_SCHEME_ERROR)
    if parsed.hostname is None or parsed.hostname.strip() == "":
        raise ValueError(HTTP_URL_HOST_ERROR)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(OUTBOUND_URL_PORT_ERROR) from exc
    return parsed


def validate_trusted_url_components(parsed: SplitResult) -> None:
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(TRUSTED_ENDPOINT_CREDENTIALS_ERROR)
    if parsed.fragment:
        raise ValueError(TRUSTED_ENDPOINT_FRAGMENT_ERROR)
    if parsed.query:
        raise ValueError(TRUSTED_ENDPOINT_QUERY_ERROR)


def validate_public_url_components(
    parsed: SplitResult,
    purpose: OutboundEndpointPurpose,
) -> None:
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(MCP_OUTBOUND_URL_CREDENTIALS_ERROR)
    if parsed.fragment:
        raise ValueError(MCP_OUTBOUND_URL_FRAGMENT_ERROR)
    if parsed.scheme != "https":
        raise ValueError(OUTBOUND_URL_SCHEME_ERROR)
    if parsed.port not in {None, 443}:
        raise ValueError(OUTBOUND_URL_PORT_ERROR)
    if purpose == OutboundEndpointPurpose.TEXT_PROVIDER and parsed.query:
        raise ValueError(OUTBOUND_URL_QUERY_ERROR)


def validated_http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(HTTP_URL_SCHEME_ERROR)
    if parsed.hostname is None or parsed.hostname.strip() == "":
        raise ValueError(HTTP_URL_HOST_ERROR)
    return value


def validated_mcp_server_url_syntax(value: str) -> str:
    parsed = urlsplit(validated_http_url(value))
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(MCP_OUTBOUND_URL_CREDENTIALS_ERROR)
    if parsed.fragment:
        raise ValueError(MCP_OUTBOUND_URL_FRAGMENT_ERROR)
    return value


def validated_mcp_outbound_url(
    value: str,
    *,
    allow_http: bool = False,
    allow_private: bool = False,
) -> str:
    parsed = urlsplit(validated_mcp_server_url_syntax(value))
    if parsed.scheme != "https" and not allow_http:
        raise ValueError(MCP_OUTBOUND_URL_SCHEME_ERROR)
    host = parsed.hostname
    if host is None:
        raise ValueError(HTTP_URL_HOST_ERROR)
    if not allow_private:
        validate_public_host(host)
    return value


def validate_public_host(host: str) -> None:
    normalized = normalize_host(host)
    if normalized in LOCAL_HOSTNAMES or normalized.endswith(LOCAL_HOSTNAME_SUFFIXES):
        raise ValueError(MCP_OUTBOUND_URL_PRIVATE_HOST_ERROR)
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return
    validate_public_ip_address(address)


def validate_public_ip_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if not address.is_global:
        raise ValueError(MCP_OUTBOUND_URL_PRIVATE_HOST_ERROR)


def normalized_hostname(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.hostname is None:
        raise ValueError(HTTP_URL_HOST_ERROR)
    return normalize_host(parsed.hostname)


def normalize_host(host: str) -> str:
    return host.rstrip(".").lower().encode("idna").decode("ascii")


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


async def resolve_host_addresses(host: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    loop = asyncio.get_running_loop()
    try:
        results = await loop.getaddrinfo(
            host,
            None,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError(MCP_OUTBOUND_URL_DNS_ERROR) from exc

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for family, _, _, _, sockaddr in results:
        raw_address = sockaddr[0]
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        addresses.append(ipaddress.ip_address(raw_address))
    return tuple(dict.fromkeys(addresses))
