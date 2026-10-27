import pytest
from pydantic import SecretStr, ValidationError

from voxos.browser_accounts.settings import BrowserAccountSettings

BROWSER_ACCOUNT_ENVIRONMENT_VARIABLES = (
    "BROWSER_ACCOUNTS_ENABLED",
    "BROWSER_ACCOUNTS_SETUP_TTL_SECONDS",
    "BROWSER_ACCOUNTS_AUTHORIZATION_TTL_SECONDS",
    "BROWSER_ACCOUNTS_TASK_TIMEOUT_SECONDS",
    "BROWSER_ACCOUNTS_CLEANUP_TIMEOUT_SECONDS",
    "BROWSER_ACCOUNTS_TASK_LEASE_SECONDS",
    "BROWSER_ACCOUNTS_MAX_TASK_STEPS",
    "BROWSER_ACCOUNTS_MAX_OUTPUT_CHARACTERS",
    "BROWSER_ACCOUNTS_POLL_INTERVAL_SECONDS",
    "BROWSER_ACCOUNTS_BROWSERLESS_BASE_URL",
    "BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN",
)


def test_browser_account_settings_have_operational_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in BROWSER_ACCOUNT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    settings = BrowserAccountSettings(_env_file=None)

    assert settings.enabled is True
    assert settings.setup_ttl_seconds == 900
    assert settings.authorization_ttl_seconds == 600
    assert settings.task_timeout_seconds == 600
    assert settings.cleanup_timeout_seconds == 15
    assert settings.task_lease_seconds == 720
    assert settings.max_task_steps == 40
    assert settings.max_output_characters == 12_000
    assert settings.poll_interval_seconds == 1.0
    assert settings.task_lease_seconds > (settings.task_timeout_seconds + settings.cleanup_timeout_seconds)


def test_browser_accounts_can_be_disabled_explicitly() -> None:
    settings = BrowserAccountSettings(_env_file=None, enabled=False)

    assert settings.enabled is False


@pytest.mark.parametrize("credential", ["x" * 31, "x" * 513, "x" * 32 + "\n", "x" * 32 + " "])
def test_stagehand_service_token_rejects_unsafe_configuration(credential: str) -> None:
    with pytest.raises(ValidationError):
        BrowserAccountSettings(_env_file=None, stagehand_service_token=SecretStr(credential))


def test_stagehand_service_token_is_redacted() -> None:
    credential = "test-browser-service-token-with-enough-length"
    settings = BrowserAccountSettings(_env_file=None, stagehand_service_token=SecretStr(credential))
    assert credential not in repr(settings)
    assert settings.stagehand_service_token is not None
    assert settings.stagehand_service_token.get_secret_value() == credential


def test_task_lease_must_outlive_remote_task_and_cleanup_window() -> None:
    with pytest.raises(ValidationError, match="task lease must exceed"):
        BrowserAccountSettings(
            _env_file=None,
            task_timeout_seconds=900,
            cleanup_timeout_seconds=60,
            task_lease_seconds=60,
        )
