import ipaddress

import pytest

from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
    OutboundEndpointSettings,
    TrustedOutboundEndpoint,
    TrustedOutboundEndpointKind,
)


async def public_addresses(
    host: str,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    assert host == "models.example.com"
    return (ipaddress.ip_address("93.184.216.34"),)


@pytest.mark.asyncio
async def test_public_policy_requires_https_global_dns_and_port_443() -> None:
    policy = OutboundEndpointPolicy(address_resolver=public_addresses)

    endpoint = await policy.resolve_url(
        "https://models.example.com:443/v1/",
        OutboundEndpointPurpose.TEXT_PROVIDER,
    )

    assert endpoint.url == "https://models.example.com/v1"
    with pytest.raises(ValueError, match="port 443"):
        await policy.resolve_url(
            "https://models.example.com:8443/v1",
            OutboundEndpointPurpose.TEXT_PROVIDER,
        )
    with pytest.raises(ValueError, match="must use https"):
        await policy.resolve_url(
            "http://models.example.com/v1",
            OutboundEndpointPurpose.TEXT_PROVIDER,
        )


@pytest.mark.asyncio
async def test_local_registration_is_exact_by_purpose_url_and_path() -> None:
    policy = OutboundEndpointPolicy(
        trusted_endpoints=(
            TrustedOutboundEndpoint(
                purpose=OutboundEndpointPurpose.TEXT_PROVIDER,
                kind=TrustedOutboundEndpointKind.LOCAL_CONTAINER,
                url="http://localhost:11434/v1",
            ),
        ),
    )

    endpoint = await policy.resolve_url(
        "http://LOCALHOST:11434/v1/",
        OutboundEndpointPurpose.TEXT_PROVIDER,
    )

    assert endpoint.url == "http://localhost:11434/v1"
    with pytest.raises(ValueError):
        await policy.resolve_url(
            "http://localhost:11434/admin",
            OutboundEndpointPurpose.TEXT_PROVIDER,
        )
    with pytest.raises(ValueError):
        await policy.resolve_url(
            "http://localhost:11434/v1",
            OutboundEndpointPurpose.MCP,
        )


def test_settings_parse_json_registry_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OUTBOUND_TRUSTED_ENDPOINTS_JSON",
        """[{"purpose":"mcp","kind":"local_container","url":"http://mcp:8000/mcp"}]""",
    )
    monkeypatch.setenv(
        "OUTBOUND_ADDITIONAL_TRUSTED_ENDPOINTS_JSON",
        """[{"purpose":"text_provider","kind":"local_container","url":"http://localhost:11434/v1"}]""",
    )

    settings = OutboundEndpointSettings(_env_file=None)

    assert settings.trusted_endpoints[0].normalized_url == "http://mcp:8000/mcp"
    assert settings.all_trusted_endpoints[1].normalized_url == "http://localhost:11434/v1"
