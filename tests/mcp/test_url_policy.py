import ipaddress

import pytest

from voxos.mcp.url_policy import McpOutboundUrlPolicy
from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
    TrustedOutboundEndpoint,
    TrustedOutboundEndpointKind,
)


@pytest.mark.asyncio
async def test_outbound_url_policy_rejects_private_dns_resolution() -> None:
    async def private_addresses(
        host: str,
    ) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
        assert host == "mcp.example.com"
        return (ipaddress.ip_address("10.0.0.12"),)

    policy = McpOutboundUrlPolicy(
        OutboundEndpointPolicy(address_resolver=private_addresses),
    )

    with pytest.raises(ValueError, match="public host"):
        await policy.validate_url("https://mcp.example.com/mcp")


@pytest.mark.asyncio
async def test_outbound_url_policy_allows_public_dns_resolution() -> None:
    async def public_addresses(
        host: str,
    ) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
        assert host == "mcp.example.com"
        return (ipaddress.ip_address("93.184.216.34"),)

    policy = McpOutboundUrlPolicy(
        OutboundEndpointPolicy(address_resolver=public_addresses),
    )

    result = await policy.validate_url("https://mcp.example.com/mcp")

    assert result == "https://mcp.example.com/mcp"


@pytest.mark.asyncio
async def test_outbound_url_policy_allows_only_exact_trusted_private_endpoint() -> None:
    policy = McpOutboundUrlPolicy(
        OutboundEndpointPolicy(
            trusted_endpoints=(
                TrustedOutboundEndpoint(
                    purpose=OutboundEndpointPurpose.MCP,
                    kind=TrustedOutboundEndpointKind.LOCAL_CONTAINER,
                    url="http://flightradar24-mcp:8000/mcp",
                ),
            ),
        ),
    )

    result = await policy.validate_url(
        "http://flightradar24-mcp:8000/mcp",
    )

    assert result == "http://flightradar24-mcp:8000/mcp"
    with pytest.raises(ValueError, match="must use https"):
        await policy.validate_url("http://flightradar24-mcp:8000/admin")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.example.com/mcp",
        "https://mcp.example.com:8443/mcp",
        "https://user:password@mcp.example.com/mcp",  # pragma: allowlist secret
        "https://mcp.example.com/mcp#fragment",
        "https://localhost/mcp",
        "https://169.254.169.254/latest/meta-data",
    ],
)
async def test_unregistered_endpoint_rejects_unsafe_public_url(url: str) -> None:
    with pytest.raises(ValueError):
        await McpOutboundUrlPolicy().validate_url(url)
