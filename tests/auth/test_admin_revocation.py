from unittest.mock import MagicMock

import pytest
from firebase_admin import auth as firebase_admin_auth
from google.auth.exceptions import DefaultCredentialsError

from voxos.auth.errors import FirebaseRevocationUnavailableError, InvalidFirebaseTokenError
from voxos.auth.firebase import FirebaseAdminRevocationChecker
from voxos.auth.models import FirebaseTokenClaims


@pytest.fixture
def claims() -> FirebaseTokenClaims:
    return FirebaseTokenClaims.from_payload(
        {
            "aud": "test-project",
            "iss": "https://securetoken.google.com/test-project",
            "sub": "verified-user",
            "auth_time": 100,
            "iat": 100,
            "exp": 200,
        },
    )


@pytest.mark.asyncio
async def test_admin_checker_uses_explicit_project_app_and_checks_each_token(
    monkeypatch: pytest.MonkeyPatch,
    claims: FirebaseTokenClaims,
) -> None:
    app = MagicMock()
    get_app = MagicMock(side_effect=ValueError("not initialized"))
    initialize_app = MagicMock(return_value=app)
    verify = MagicMock(return_value={"uid": "verified-user"})
    monkeypatch.setattr("voxos.auth.firebase.firebase_admin.get_app", get_app)
    monkeypatch.setattr("voxos.auth.firebase.firebase_admin.initialize_app", initialize_app)
    monkeypatch.setattr("voxos.auth.firebase.firebase_admin_auth.verify_id_token", verify)
    checker = FirebaseAdminRevocationChecker(project_id="test-project", clock_skew_seconds=60)

    await checker.verify_not_revoked("first-token", claims)
    await checker.verify_not_revoked("second-token", claims)

    get_app.assert_called_once_with("voxos-auth-test-project")
    initialize_app.assert_called_once_with(
        options={"projectId": "test-project", "httpTimeout": 5},
        name="voxos-auth-test-project",
    )
    assert verify.call_count == 2
    verify.assert_called_with("second-token", app=app, check_revoked=True, clock_skew_seconds=60)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [firebase_admin_auth.RevokedIdTokenError("revoked"), firebase_admin_auth.UserDisabledError("disabled")],
)
async def test_admin_checker_rejects_revoked_or_disabled_user(
    monkeypatch: pytest.MonkeyPatch,
    claims: FirebaseTokenClaims,
    error: Exception,
) -> None:
    monkeypatch.setattr("voxos.auth.firebase.firebase_admin.get_app", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("voxos.auth.firebase.firebase_admin_auth.verify_id_token", MagicMock(side_effect=error))
    checker = FirebaseAdminRevocationChecker(project_id="test-project", clock_skew_seconds=60)
    with pytest.raises(InvalidFirebaseTokenError):
        await checker.verify_not_revoked("private-token-value", claims)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        DefaultCredentialsError("private credential diagnostic"),
        firebase_admin_auth.CertificateFetchError("private credential diagnostic", cause=None),
        firebase_admin_auth.InsufficientPermissionError(
            "private credential diagnostic", cause=None, http_response=None
        ),
    ],
)
async def test_admin_checker_fails_closed_when_credentials_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    claims: FirebaseTokenClaims,
    failure: Exception,
) -> None:
    monkeypatch.setattr("voxos.auth.firebase.firebase_admin.get_app", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        "voxos.auth.firebase.firebase_admin_auth.verify_id_token",
        MagicMock(side_effect=failure),
    )
    checker = FirebaseAdminRevocationChecker(project_id="test-project", clock_skew_seconds=60)
    with pytest.raises(FirebaseRevocationUnavailableError) as error:
        await checker.verify_not_revoked("private-token-value", claims)
    assert "private credential diagnostic" not in str(error.value)
    assert "private-token-value" not in str(error.value)


@pytest.mark.asyncio
async def test_admin_checker_rejects_sdk_subject_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    claims: FirebaseTokenClaims,
) -> None:
    monkeypatch.setattr("voxos.auth.firebase.firebase_admin.get_app", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        "voxos.auth.firebase.firebase_admin_auth.verify_id_token", MagicMock(return_value={"uid": "other-user"})
    )
    checker = FirebaseAdminRevocationChecker(project_id="test-project", clock_skew_seconds=60)
    with pytest.raises(InvalidFirebaseTokenError):
        await checker.verify_not_revoked("private-token-value", claims)
