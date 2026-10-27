import pytest
from pydantic import SecretStr, ValidationError

from voxos.api.livekit import LiveKitSettingsRequest
from voxos.voice.endpoint_policy import (
    INVALID_LIVEKIT_LOG_TARGET,
    LiveKitEndpointPolicy,
    livekit_log_target,
    normalize_livekit_url,
    validate_livekit_worker_endpoint,
)
from voxos.voice.errors import DisallowedLiveKitEndpointError
from voxos.voice.supervisor import (
    LiveKitProjectRegistration,
    ProjectWorkerCredentialsMessage,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (" WSS://VOICE.Example.TEST:443/ ", "wss://voice.example.test"),
        ("ws://livekit:7880/", "ws://livekit:7880"),
        ("ws://[::1]:7880", "ws://[::1]:7880"),
    ),
)
def test_normalize_livekit_url(value: str, expected: str) -> None:
    assert normalize_livekit_url(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "",
        "https://voice.example.test",
        "wss:///missing-host",
        "wss://user:password@voice.example.test",  # pragma: allowlist secret
        "wss://voice.example.test?token=secret",  # pragma: allowlist secret
        "wss://voice.example.test?",
        "wss://voice.example.test#fragment",
        "wss://voice.example.test#",
        "wss://voice example.test",
        "wss://voice.example.test\\unexpected",
        "wss://voice.example.test\x00/path",
        "wss://voice.example.test:",
        "wss://voice.example.test:not-a-port",
        "wss://voice.example.test/tenant/socket",
    ),
)
def test_normalize_livekit_url_rejects_unsafe_or_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_livekit_url(value)


def test_livekit_settings_request_uses_canonical_url() -> None:
    request = LiveKitSettingsRequest(
        livekit_url=" WSS://VOICE.Example.TEST:443/ ",
        livekit_api_key=SecretStr("api-key"),  # pragma: allowlist secret
        livekit_api_secret=SecretStr("api-secret"),  # pragma: allowlist secret
    )

    assert request.livekit_url == "wss://voice.example.test"


def test_livekit_settings_request_rejects_query_string() -> None:
    with pytest.raises(ValidationError):
        LiveKitSettingsRequest(
            livekit_url="wss://voice.example.test?token=secret",
            livekit_api_key=SecretStr("api-key"),  # pragma: allowlist secret
            livekit_api_secret=SecretStr("api-secret"),  # pragma: allowlist secret
        )


def test_livekit_log_target_omits_path_and_rejects_unsafe_url() -> None:
    assert livekit_log_target("wss://voice.example.test:8443/") == "wss://voice.example.test:8443"
    assert livekit_log_target("wss://voice.example.test:8443/private/tenant/path") == INVALID_LIVEKIT_LOG_TARGET
    assert (
        livekit_log_target(
            "wss://user:secret@voice.example.test/private?token=value"  # pragma: allowlist secret
        )
        == INVALID_LIVEKIT_LOG_TARGET
    )


@pytest.mark.asyncio
async def test_public_endpoint_policy_accepts_global_wss_addresses() -> None:
    async def resolve(_hostname: str, _port: int) -> tuple[str, ...]:
        return ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")

    result = await validate_livekit_worker_endpoint(
        "wss://voice.example.com",
        policy=LiveKitEndpointPolicy.PUBLIC_WSS,
        resolver=resolve,
    )

    assert result == "wss://voice.example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    (
        "127.0.0.1",
        "10.0.0.10",
        "169.254.169.254",
        "::1",
        "fd00::1",
    ),
)
async def test_public_endpoint_policy_rejects_non_global_addresses(
    address: str,
) -> None:
    async def resolve(_hostname: str, _port: int) -> tuple[str, ...]:
        return (address,)

    with pytest.raises(DisallowedLiveKitEndpointError):
        await validate_livekit_worker_endpoint(
            "wss://voice.example.com",
            policy=LiveKitEndpointPolicy.PUBLIC_WSS,
            resolver=resolve,
        )


@pytest.mark.asyncio
async def test_public_endpoint_policy_rejects_plain_websocket() -> None:
    with pytest.raises(DisallowedLiveKitEndpointError):
        await validate_livekit_worker_endpoint(
            "ws://livekit:7880",
            policy=LiveKitEndpointPolicy.PUBLIC_WSS,
        )


@pytest.mark.asyncio
async def test_trusted_private_endpoint_policy_allows_local_websocket() -> None:
    async def unexpected_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
        raise AssertionError

    result = await validate_livekit_worker_endpoint(
        "ws://livekit:7880",
        policy=LiveKitEndpointPolicy.TRUSTED_PRIVATE,
        resolver=unexpected_resolver,
    )

    assert result == "ws://livekit:7880"


def test_project_worker_messages_hide_credentials_and_url_from_repr() -> None:
    registration = LiveKitProjectRegistration(
        project_key="project-key",
        livekit_url="wss://voice.example.test/private/path",
        api_key="tenant-api-key",  # pragma: allowlist secret
        api_secret=SecretStr("tenant-api-secret"),  # pragma: allowlist secret
    )
    credentials = ProjectWorkerCredentialsMessage(
        livekit_url="wss://voice.example.test/private/path",
        api_key="tenant-api-key",  # pragma: allowlist secret
        api_secret=SecretStr("tenant-api-secret").get_secret_value(),  # pragma: allowlist secret
    )

    registration_repr = repr(registration)
    credentials_repr = repr(credentials)
    for sensitive_value in (
        "voice.example.test",
        "private/path",
        "tenant-api-key",
        "tenant-api-secret",
    ):
        assert sensitive_value not in registration_repr
        assert sensitive_value not in credentials_repr
