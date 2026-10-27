import pytest
from pydantic import ValidationError

from voxos.browser_runtime.contracts import BrowserTaskActionMode
from voxos.browser_runtime.models import BrowserTaskArguments


def test_public_browser_task_normalizes_url_and_defaults_to_read_only() -> None:
    task = BrowserTaskArguments.model_validate(
        {
            "instruction": "  Summarize the documentation  ",
            "start_url": "https://docs.example.com/guide?version=2",
        },
    )

    assert task.instruction == "Summarize the documentation"
    assert task.start_url == "https://docs.example.com/guide?version=2"
    assert task.action_mode == BrowserTaskActionMode.READ_ONLY


@pytest.mark.parametrize(
    "start_url",
    [
        "http://example.com",
        "https://127.0.0.1",
        "https://localhost",
        "https://user:password@example.com",  # pragma: allowlist secret
        "https://example.com/page#secret",
    ],
)
def test_browser_task_rejects_unsafe_start_urls(start_url: str) -> None:
    with pytest.raises(ValidationError):
        BrowserTaskArguments.model_validate(
            {
                "instruction": "Browse",
                "start_url": start_url,
            },
        )


def test_saved_profile_cannot_broaden_its_configured_origins() -> None:
    with pytest.raises(ValidationError, match="configured origins"):
        BrowserTaskArguments.model_validate(
            {
                "instruction": "Use the saved login",
                "profile": "profile_123",
                "allowed_origins": ["https://unapproved.example.com"],
            },
        )
