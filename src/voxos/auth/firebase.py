import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol, cast

import firebase_admin
import jwt
from firebase_admin import auth as firebase_admin_auth
from firebase_admin import exceptions as firebase_admin_exceptions
from google.auth.exceptions import GoogleAuthError
from jwt import PyJWTError
from pydantic import ValidationError

from voxos.auth.errors import FirebaseRevocationUnavailableError, InvalidFirebaseTokenError
from voxos.auth.models import (
    FirebaseTokenClaims,
    FirebaseTokenHeader,
    VerifiedFirebaseIdentity,
)
from voxos.auth.public_keys import FirebasePublicKeyCache

logger = logging.getLogger(__name__)

REQUIRED_FIREBASE_CLAIMS = ["aud", "iss", "exp", "iat", "auth_time", "sub"]
FIREBASE_ADMIN_TOKEN_ERRORS = (
    firebase_admin_auth.ExpiredIdTokenError,
    firebase_admin_auth.InvalidIdTokenError,
    firebase_admin_auth.RevokedIdTokenError,
    firebase_admin_auth.UserDisabledError,
    firebase_admin_auth.UserNotFoundError,
)


class FirebaseRevocationChecker(Protocol):
    async def verify_not_revoked(self, token: str, claims: FirebaseTokenClaims) -> None:
        """Raise when a Firebase token has been revoked or cannot be checked."""


@dataclass(slots=True)
class FirebaseAdminRevocationChecker:
    project_id: str
    clock_skew_seconds: int
    _app: firebase_admin.App | None = field(default=None, init=False, repr=False)
    _app_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    async def verify_not_revoked(self, token: str, claims: FirebaseTokenClaims) -> None:
        try:
            payload = await asyncio.to_thread(
                self._verify,
                token,
            )
        except FIREBASE_ADMIN_TOKEN_ERRORS as exc:
            logger.debug(
                "firebase admin revoked-token check failed: %s",
                exc.__class__.__name__,
            )
            raise InvalidFirebaseTokenError from exc
        except (GoogleAuthError, ValueError, firebase_admin_exceptions.FirebaseError) as exc:
            logger.warning("firebase session verification unavailable: %s", exc.__class__.__name__)
            raise FirebaseRevocationUnavailableError from exc

        firebase_user_id = payload.get("uid") or payload.get("sub")
        if firebase_user_id != claims.uid:
            raise InvalidFirebaseTokenError.invalid_subject()

    def _verify(self, token: str) -> dict[str, object]:
        with self._app_lock:
            if self._app is None:
                app_name = f"voxos-auth-{self.project_id}"
                try:
                    self._app = firebase_admin.get_app(app_name)
                except ValueError:
                    self._app = firebase_admin.initialize_app(
                        options={"projectId": self.project_id, "httpTimeout": 5},
                        name=app_name,
                    )
            app = self._app
        return cast(
            dict[str, object],
            firebase_admin_auth.verify_id_token(
                token,
                app=app,
                check_revoked=True,
                clock_skew_seconds=self.clock_skew_seconds,
            ),
        )


class FirebaseTokenVerifier:
    def __init__(
        self,
        public_key_cache: FirebasePublicKeyCache,
        project_id: str,
        issuer: str,
        clock_skew_seconds: int,
        time_clock: Callable[[], float] = time.time,
        revocation_checker: FirebaseRevocationChecker | None = None,
    ) -> None:
        self._public_key_cache = public_key_cache
        self._project_id = project_id
        self._issuer = issuer
        self._clock_skew_seconds = clock_skew_seconds
        self._time_clock = time_clock
        self._revocation_checker = revocation_checker

    async def verify(self, token: str) -> VerifiedFirebaseIdentity:
        header = self._parse_header(token)
        public_key = await self._public_key_cache.get_key(header.kid)
        payload = self._decode_payload(token=token, public_key=public_key)
        claims = self._parse_claims(payload)
        self._validate_claims(claims)
        if self._revocation_checker is not None:
            await self._revocation_checker.verify_not_revoked(token, claims)
        return VerifiedFirebaseIdentity.from_claims(claims)

    def _parse_header(self, token: str) -> FirebaseTokenHeader:
        try:
            untrusted_header = jwt.get_unverified_header(token)
        except PyJWTError as exc:
            raise InvalidFirebaseTokenError.invalid_header() from exc

        try:
            return FirebaseTokenHeader.model_validate(untrusted_header)
        except ValidationError as exc:
            raw_alg = untrusted_header.get("alg")
            raw_kid = untrusted_header.get("kid")
            logger.debug(
                "rejected firebase token header: alg=%r kid_present=%s",
                raw_alg,
                isinstance(raw_kid, str) and raw_kid != "",
            )
            raise InvalidFirebaseTokenError.invalid_header() from exc

    def _decode_payload(self, token: str, public_key: str) -> dict[str, object]:
        try:
            payload = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._project_id,
                issuer=self._issuer,
                leeway=self._clock_skew_seconds,
                options={"require": REQUIRED_FIREBASE_CLAIMS},
            )
        except PyJWTError as exc:
            logger.debug(
                "rejected firebase token for project_id=%s: %s",
                self._project_id,
                exc.__class__.__name__,
            )
            raise InvalidFirebaseTokenError from exc

        if not isinstance(payload, dict):
            raise InvalidFirebaseTokenError

        return cast(dict[str, object], payload)

    def _parse_claims(self, payload: dict[str, object]) -> FirebaseTokenClaims:
        try:
            return FirebaseTokenClaims.from_payload(payload)
        except ValidationError as exc:
            raise InvalidFirebaseTokenError.invalid_claims() from exc

    def _validate_claims(self, claims: FirebaseTokenClaims) -> None:
        now = self._time_clock()
        leeway = float(self._clock_skew_seconds)
        if claims.aud != self._project_id:
            raise InvalidFirebaseTokenError.invalid_audience()
        if claims.iss != self._issuer:
            raise InvalidFirebaseTokenError.invalid_issuer()
        if claims.uid.strip() == "":
            raise InvalidFirebaseTokenError.invalid_subject()
        if claims.iat > now + leeway:
            raise InvalidFirebaseTokenError.invalid_issued_at()
        if claims.auth_time > now + leeway:
            raise InvalidFirebaseTokenError.invalid_auth_time()
        if claims.exp <= now - leeway:
            raise InvalidFirebaseTokenError.expired()
