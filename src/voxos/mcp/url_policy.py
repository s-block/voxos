from dataclasses import dataclass

from voxos.security.outbound_endpoints import (
    HTTP_URL_HOST_ERROR,
    HTTP_URL_SCHEME_ERROR,
    LOCAL_HOSTNAME_SUFFIXES,
    LOCAL_HOSTNAMES,
    MCP_OUTBOUND_URL_CREDENTIALS_ERROR,
    MCP_OUTBOUND_URL_DNS_ERROR,
    MCP_OUTBOUND_URL_FRAGMENT_ERROR,
    MCP_OUTBOUND_URL_PRIVATE_HOST_ERROR,
    MCP_OUTBOUND_URL_SCHEME_ERROR,
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
    ResolvedOutboundEndpoint,
    is_ip_literal,
    normalize_host,
    normalized_hostname,
    resolve_host_addresses,
    validate_public_host,
    validate_public_ip_address,
    validated_http_url,
    validated_mcp_outbound_url,
    validated_mcp_server_url_syntax,
)

__all__ = [
    "HTTP_URL_HOST_ERROR",
    "HTTP_URL_SCHEME_ERROR",
    "LOCAL_HOSTNAMES",
    "LOCAL_HOSTNAME_SUFFIXES",
    "MCP_OUTBOUND_URL_CREDENTIALS_ERROR",
    "MCP_OUTBOUND_URL_DNS_ERROR",
    "MCP_OUTBOUND_URL_FRAGMENT_ERROR",
    "MCP_OUTBOUND_URL_PRIVATE_HOST_ERROR",
    "MCP_OUTBOUND_URL_SCHEME_ERROR",
    "McpOutboundUrlPolicy",
    "is_ip_literal",
    "normalize_host",
    "normalized_hostname",
    "resolve_host_addresses",
    "validate_public_host",
    "validate_public_ip_address",
    "validated_http_url",
    "validated_mcp_outbound_url",
    "validated_mcp_server_url_syntax",
]


@dataclass(frozen=True, slots=True)
class McpOutboundUrlPolicy:
    endpoint_policy: OutboundEndpointPolicy = OutboundEndpointPolicy()

    async def validate_url(self, value: str) -> str:
        return (await self.resolve_url(value)).url

    async def resolve_url(self, value: str) -> ResolvedOutboundEndpoint:
        return await self.endpoint_policy.resolve_url(
            value,
            OutboundEndpointPurpose.MCP,
        )

    def allows_trusted_url(self, value: str) -> bool:
        return self.endpoint_policy.allows_trusted_url(
            value,
            OutboundEndpointPurpose.MCP,
        )
