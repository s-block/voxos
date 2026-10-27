import pytest
from pydantic import ValidationError

from voxos.browser_accounts.models import (
    BrowserAccountCreateRequest,
    BrowserAccountUpdateRequest,
)


def test_browser_account_request_normalizes_exact_https_origins() -> None:
    request = BrowserAccountCreateRequest(
        label="  Work GitHub  ",
        primary_origin="https://GitHub.com/",
        authentication_origins=("https://LOGIN.example.com",),
        browser_country_code=" gb ",
        provider_connection_id="connection-1",
    )

    assert request.label == "Work GitHub"
    assert request.primary_origin == "https://github.com"
    assert request.authentication_origins == ("https://login.example.com",)
    assert request.browser_country_code == "GB"


def test_browser_account_update_normalizes_editable_details() -> None:
    request = BrowserAccountUpdateRequest(
        label="  Eon Next  ",
        primary_origin="https://EONNEXT.com/",
        authentication_origins=("https://AUTH.eonnext.com/",),
    )

    assert request.label == "Eon Next"
    assert request.primary_origin == "https://eonnext.com"
    assert request.authentication_origins == ("https://auth.eonnext.com",)
    assert request.browser_country_code == "GB"


@pytest.mark.parametrize("country_code", ("G", "GBR", "1B", "G!"))
def test_browser_account_request_rejects_invalid_country_code(
    country_code: str,
) -> None:
    with pytest.raises(ValidationError):
        BrowserAccountUpdateRequest(
            label="Eon Next",
            primary_origin="https://eonnext.com",
            browser_country_code=country_code,
        )


@pytest.mark.parametrize(
    "origin",
    (
        "http://example.com",
        "https://example.com/path",
        "https://user:password@example.com",  # pragma: allowlist secret
        "https://*.example.com",
        "https://127.0.0.1",
        "https://localhost",
        "https://example.com:8443",
        "https://example.com?token=value",  # pragma: allowlist secret
        "https://example.com.",
        "https://éxample.com",
    ),
)
def test_browser_account_request_rejects_ambiguous_origins(origin: str) -> None:
    with pytest.raises(ValidationError):
        BrowserAccountCreateRequest(
            label="Work",
            primary_origin=origin,
            provider_connection_id="connection-1",
        )
