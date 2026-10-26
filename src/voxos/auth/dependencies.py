import logging
from typing import Protocol

from fastapi import HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from voxos.auth.bearer import BearerTokenExtractor
from voxos.auth.errors import AuthenticationConfigurationError, AuthenticationError, FirebaseRevocationUnavailableError
from voxos.auth.models import AuthenticatedUser, VerifiedFirebaseIdentity
from voxos.auth.protocols import FirebaseIdentityVerifier
from voxos.auth.services import FirebaseUserService
from voxos.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)

WWW_AUTHENTICATE_BEARER = {"WWW-Authenticate": "Bearer"}


class NewUserInitializer(Protocol):
    @property
    def initialization_version(self) -> str:
        """Return the durable application-default version this initializer installs."""

    async def initialize_user(self, user_id: str) -> None:
        """Idempotently create application defaults for a persisted user."""


class StatelessFirebaseAuthDependency:
    def __init__(
        self,
        token_extractor: BearerTokenExtractor,
        token_verifier: FirebaseIdentityVerifier,
    ) -> None:
        self._token_extractor = token_extractor
        self._token_verifier = token_verifier

    async def __call__(self, request: Request) -> VerifiedFirebaseIdentity:
        try:
            token = self._token_extractor.extract(request.headers.get("Authorization"))
            return await self._token_verifier.verify(token)
        except FirebaseRevocationUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication service unavailable",
                headers={"Retry-After": "5"},
            ) from exc
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers=WWW_AUTHENTICATE_BEARER,
            ) from exc


class FirebaseAuthDependency:
    def __init__(
        self,
        token_extractor: BearerTokenExtractor,
        token_verifier: FirebaseIdentityVerifier,
        session_factory: AsyncSessionFactory | None,
        last_seen_update_interval_seconds: int = 300,
        new_user_initializer: NewUserInitializer | None = None,
    ) -> None:
        if session_factory is None:
            raise AuthenticationConfigurationError

        self._identity_dependency = StatelessFirebaseAuthDependency(token_extractor, token_verifier)
        self._session_factory: AsyncSessionFactory = session_factory
        self._last_seen_update_interval_seconds = last_seen_update_interval_seconds
        self._new_user_initializer = new_user_initializer

    async def __call__(self, request: Request) -> AuthenticatedUser:
        identity = await self._identity_dependency(request)
        return await self._resolve_user(identity)

    async def _resolve_user(self, identity: VerifiedFirebaseIdentity) -> AuthenticatedUser:
        try:
            async with self._session_factory() as session:
                service = FirebaseUserService(session)
                resolution = await service.resolve_from_verified_identity(
                    identity=identity,
                    last_seen_update_interval_seconds=(self._last_seen_update_interval_seconds),
                )
            initializer = self._new_user_initializer
            if (
                initializer is not None
                and resolution.record.application_defaults_version != initializer.initialization_version
            ):
                await initializer.initialize_user(resolution.record.id)
                async with self._session_factory() as session:
                    await FirebaseUserService(
                        session,
                    ).mark_application_defaults_initialized(
                        user_id=resolution.record.id,
                        version=initializer.initialization_version,
                    )
            return AuthenticatedUser.from_verified_identity(
                user_id=resolution.record.id,
                identity=identity,
            )
        except SQLAlchemyError as exc:
            logger.exception(
                "failed to persist authenticated firebase user: firebase_user_id=%s",
                identity.firebase_user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service unavailable",
            ) from exc
