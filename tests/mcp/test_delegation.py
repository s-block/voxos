import httpx2 as httpx
from pydantic import SecretStr

from voxos.mcp.delegation import (
    DELEGATION_PROVIDER_MONZO,
    McpCredentialDelegationHttpAuth,
    McpCredentialDelegationSigner,
)
from voxos.mcp.services import MCP_CREDENTIAL_DELEGATION_HEADER

SIGNING_SECRET = "test-credential-delegation-signing-key"  # pragma: allowlist secret


def test_http_auth_mints_a_fresh_delegation_for_every_request() -> None:
    signer = McpCredentialDelegationSigner(
        secret=SecretStr(SIGNING_SECRET),
    )
    auth = McpCredentialDelegationHttpAuth(
        signer=signer,
        user_id="user-123",
        server_id="11111111-1111-1111-1111-111111111111",
        provider=DELEGATION_PROVIDER_MONZO,
        header_name=MCP_CREDENTIAL_DELEGATION_HEADER,
    )

    first = next(auth.auth_flow(httpx.Request("POST", "https://mcp.example.com/mcp")))
    second = next(auth.auth_flow(httpx.Request("POST", "https://mcp.example.com/mcp")))

    first_assertion = first.headers[MCP_CREDENTIAL_DELEGATION_HEADER].removeprefix("Bearer ")
    second_assertion = second.headers[MCP_CREDENTIAL_DELEGATION_HEADER].removeprefix("Bearer ")
    assert first_assertion != second_assertion
    first_payload = signer.verify(first_assertion)
    second_payload = signer.verify(second_assertion)
    assert first_payload.user_id == second_payload.user_id == "user-123"
    assert first_payload.server_id == second_payload.server_id
    assert first_payload.provider == second_payload.provider == DELEGATION_PROVIDER_MONZO
