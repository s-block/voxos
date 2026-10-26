from dataclasses import dataclass

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, select
from starlette.requests import Request

from voxos.auth.bearer import BearerTokenExtractor
from voxos.auth.dependencies import FirebaseAuthDependency, StatelessFirebaseAuthDependency
from voxos.auth.errors import (
    AuthenticationConfigurationError,
    FirebaseRevocationUnavailableError,
    MissingAuthorizationTokenError,
)
from voxos.auth.models import FirebaseTokenClaims, VerifiedFirebaseIdentity
from voxos.auth.records import FirebaseUserRecord
from voxos.db.session import AsyncSessionFactory


@dataclass(slots=True)
class StaticTokenVerifier:
    identity: VerifiedFirebaseIdentity
    seen_token: str | None = None

    async def verify(self, token: str) -> VerifiedFirebaseIdentity:
        self.seen_token = token
        return self.identity


class RejectingTokenExtractor:
    def extract(self, authorization_header: str | None) -> str:
        raise MissingAuthorizationTokenError


class UnavailableTokenVerifier:
    async def verify(self, token: str) -> VerifiedFirebaseIdentity:
        raise FirebaseRevocationUnavailableError


class FailingSessionContext:
    async def __aenter__(self) -> None:
        raise SQLAlchemyError

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class FailingSessionFactory:
    def __call__(self) -> FailingSessionContext:
        return FailingSessionContext()


@dataclass(slots=True)
class RecordingNewUserInitializer:
    user_ids: list[str]
    initialization_version: str = "test-defaults-v1"

    async def initialize_user(self, user_id: str) -> None:
        self.user_ids.append(user_id)


@dataclass(slots=True)
class FlakyNewUserInitializer:
    user_ids: list[str]
    failures_remaining: int = 1
    initialization_version: str = "test-defaults-v1"

    async def initialize_user(self, user_id: str) -> None:
        self.user_ids.append(user_id)
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise SQLAlchemyError


def request_with_authorization(header: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if header is not None:
        headers.append((b"authorization", header.encode()))
    return Request({"type": "http", "headers": headers})


def verified_identity(
    email: str = "user@example.com",
    display_name: str = "Test User",
) -> VerifiedFirebaseIdentity:
    claims = FirebaseTokenClaims.model_validate(
        {
            "sub": "firebase-user-123",
            "aud": "project-id",
            "iss": "issuer",
            "auth_time": 1,
            "iat": 1,
            "exp": 2,
            "email": email,
            "email_verified": True,
            "name": display_name,
        },
    )
    return VerifiedFirebaseIdentity.from_claims(claims)


@pytest.mark.asyncio
async def test_stateless_firebase_auth_dependency_returns_verified_identity() -> None:
    identity = verified_identity()
    verifier = StaticTokenVerifier(identity=identity)
    dependency = StatelessFirebaseAuthDependency(
        token_extractor=BearerTokenExtractor(),
        token_verifier=verifier,
    )

    result = await dependency(request_with_authorization("Bearer firebase-token"))

    assert result == identity
    assert verifier.seen_token == "firebase-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("stateless", [False, True])
async def test_revocation_backend_failure_returns_503_without_persisting_user(
    stateless: bool,
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    verifier = UnavailableTokenVerifier()
    dependency = (
        StatelessFirebaseAuthDependency(token_extractor=BearerTokenExtractor(), token_verifier=verifier)
        if stateless
        else FirebaseAuthDependency(
            token_extractor=BearerTokenExtractor(),
            token_verifier=verifier,
            session_factory=sqlite_session_factory,
        )
    )
    with pytest.raises(HTTPException) as error:
        await dependency(request_with_authorization("Bearer private-token-value"))
    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "5"}
    async with sqlite_session_factory() as session:
        assert (await session.exec(select(FirebaseUserRecord))).all() == []


@pytest.mark.asyncio
async def test_firebase_auth_dependency_returns_local_authenticated_user(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity = verified_identity()
    verifier = StaticTokenVerifier(identity=identity)
    dependency = FirebaseAuthDependency(
        token_extractor=BearerTokenExtractor(),
        token_verifier=verifier,
        session_factory=sqlite_session_factory,
    )

    result = await dependency(request_with_authorization("Bearer firebase-token"))

    assert result.user_id != result.firebase_user_id
    assert result.local_user_id == result.user_id
    assert result.firebase_user_id == "firebase-user-123"
    assert str(result.email) == "user@example.com"
    assert result.claims == identity.claims
    assert verifier.seen_token == "firebase-token"


@pytest.mark.asyncio
async def test_firebase_auth_dependency_maps_auth_failure_to_401(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    dependency = FirebaseAuthDependency(
        token_extractor=RejectingTokenExtractor(),
        token_verifier=StaticTokenVerifier(identity=verified_identity()),
        session_factory=sqlite_session_factory,
    )

    with pytest.raises(HTTPException) as exc_info:
        await dependency(request_with_authorization(None))

    assert exc_info.value.status_code == 401
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.asyncio
async def test_firebase_auth_dependency_maps_persistence_failure_to_500() -> None:
    dependency = FirebaseAuthDependency(
        token_extractor=BearerTokenExtractor(),
        token_verifier=StaticTokenVerifier(identity=verified_identity()),
        session_factory=FailingSessionFactory(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await dependency(request_with_authorization("Bearer firebase-token"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Authentication service unavailable"


@pytest.mark.asyncio
async def test_firebase_auth_dependency_reuses_local_user_id(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    dependency = FirebaseAuthDependency(
        token_extractor=BearerTokenExtractor(),
        token_verifier=StaticTokenVerifier(identity=verified_identity()),
        session_factory=sqlite_session_factory,
        last_seen_update_interval_seconds=300,
    )

    first_user = await dependency(request_with_authorization("Bearer firebase-token"))
    dependency = FirebaseAuthDependency(
        token_extractor=BearerTokenExtractor(),
        token_verifier=StaticTokenVerifier(
            identity=verified_identity(
                email="next@example.com",
                display_name="Next User",
            ),
        ),
        session_factory=sqlite_session_factory,
        last_seen_update_interval_seconds=300,
    )
    second_user = await dependency(request_with_authorization("Bearer firebase-token"))

    async with sqlite_session_factory() as session:
        result = await session.exec(
            select(FirebaseUserRecord).where(
                col(FirebaseUserRecord.firebase_user_id) == second_user.firebase_user_id,
            ),
        )

    record = result.one()

    assert first_user.user_id == record.id
    assert second_user.user_id == record.id
    assert second_user.firebase_user_id == "firebase-user-123"
    assert str(second_user.email) == "next@example.com"
    assert record.email == "next@example.com"


@pytest.mark.asyncio
async def test_firebase_auth_dependency_initializes_new_user_once(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    initializer = RecordingNewUserInitializer(user_ids=[])
    dependency = FirebaseAuthDependency(
        token_extractor=BearerTokenExtractor(),
        token_verifier=StaticTokenVerifier(identity=verified_identity()),
        session_factory=sqlite_session_factory,
        new_user_initializer=initializer,
    )

    first_user = await dependency(request_with_authorization("Bearer firebase-token"))
    second_user = await dependency(request_with_authorization("Bearer firebase-token"))

    async with sqlite_session_factory() as session:
        record = await session.get(FirebaseUserRecord, first_user.user_id)

    assert first_user.user_id == second_user.user_id
    assert initializer.user_ids == [first_user.user_id]
    assert record is not None
    assert record.application_defaults_version == initializer.initialization_version


@pytest.mark.asyncio
async def test_firebase_auth_dependency_retries_incomplete_user_initialization(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    initializer = FlakyNewUserInitializer(user_ids=[])
    dependency = FirebaseAuthDependency(
        token_extractor=BearerTokenExtractor(),
        token_verifier=StaticTokenVerifier(identity=verified_identity()),
        session_factory=sqlite_session_factory,
        new_user_initializer=initializer,
    )

    with pytest.raises(HTTPException) as exc_info:
        await dependency(request_with_authorization("Bearer firebase-token"))

    assert exc_info.value.status_code == 500
    async with sqlite_session_factory() as session:
        result = await session.exec(select(FirebaseUserRecord))
        incomplete_record = result.one()
    assert incomplete_record.application_defaults_version is None

    user = await dependency(request_with_authorization("Bearer firebase-token"))

    assert initializer.user_ids == [user.user_id, user.user_id]
    async with sqlite_session_factory() as session:
        completed_record = await session.get(FirebaseUserRecord, user.user_id)
    assert completed_record is not None
    assert completed_record.application_defaults_version == initializer.initialization_version


def test_firebase_auth_dependency_rejects_missing_session_factory() -> None:
    with pytest.raises(AuthenticationConfigurationError):
        FirebaseAuthDependency(
            token_extractor=BearerTokenExtractor(),
            token_verifier=StaticTokenVerifier(identity=verified_identity()),
            session_factory=None,
        )
