import pytest

from voxos.auth.bearer import BearerTokenExtractor
from voxos.auth.errors import (
    InvalidAuthorizationHeaderError,
    MissingAuthorizationTokenError,
)


def test_bearer_token_extractor_accepts_bearer_token() -> None:
    extractor = BearerTokenExtractor()

    assert extractor.extract("Bearer firebase-token") == "firebase-token"
    assert extractor.extract("bearer firebase-token") == "firebase-token"


@pytest.mark.parametrize("header", [None, "", "   "])
def test_bearer_token_extractor_rejects_missing_header(
    header: str | None,
) -> None:
    extractor = BearerTokenExtractor()

    with pytest.raises(MissingAuthorizationTokenError):
        extractor.extract(header)


@pytest.mark.parametrize(
    "header",
    [
        "Basic firebase-token",
        "Bearer",
        "Bearer ",
        "Bearer token extra",
        "firebase-token",
    ],
)
def test_bearer_token_extractor_rejects_malformed_header(header: str) -> None:
    extractor = BearerTokenExtractor()

    with pytest.raises(InvalidAuthorizationHeaderError):
        extractor.extract(header)
