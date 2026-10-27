import httpx

from voxos.auth.bearer import BearerTokenExtractor
from voxos.auth.dependencies import (
    FirebaseAuthDependency,
    NewUserInitializer,
    StatelessFirebaseAuthDependency,
)
from voxos.auth.errors import AuthenticationConfigurationError
from voxos.auth.firebase import FirebaseAdminRevocationChecker, FirebaseTokenVerifier
from voxos.auth.public_keys import FirebasePublicKeyCache, get_shared_http_client
from voxos.auth.settings import FirebaseAuthSettings
from voxos.db.session import AsyncSessionFactory


class FirebaseAuthFactory:
    def __init__(
        self,
        settings: FirebaseAuthSettings,
        session_factory: AsyncSessionFactory | None = None,
        http_client: httpx.AsyncClient | None = None,
        new_user_initializer: NewUserInitializer | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._http_client = http_client
        self._new_user_initializer = new_user_initializer
        self._public_key_cache: FirebasePublicKeyCache | None = None
        self._token_verifier: FirebaseTokenVerifier | None = None

    def create_public_key_cache(self) -> FirebasePublicKeyCache:
        if self._public_key_cache is None:
            self._public_key_cache = FirebasePublicKeyCache(
                http_client=self._get_http_client(),
                certs_url=self._settings.firebase_certs_url,
                max_cache_ttl_seconds=(self._settings.firebase_public_key_cache_max_ttl_seconds),
            )
        return self._public_key_cache

    def create_token_verifier(self) -> FirebaseTokenVerifier:
        if self._token_verifier is None:
            self._token_verifier = FirebaseTokenVerifier(
                public_key_cache=self.create_public_key_cache(),
                project_id=self._settings.firebase_project_id,
                issuer=self._settings.issuer,
                clock_skew_seconds=self._settings.firebase_auth_clock_skew_seconds,
                revocation_checker=(
                    FirebaseAdminRevocationChecker(
                        project_id=self._settings.firebase_project_id,
                        clock_skew_seconds=self._settings.firebase_auth_clock_skew_seconds,
                    )
                    if self._settings.firebase_auth_check_revoked
                    else None
                ),
            )
        return self._token_verifier

    def create_dependency(self) -> FirebaseAuthDependency:
        return FirebaseAuthDependency(
            token_extractor=BearerTokenExtractor(),
            token_verifier=self.create_token_verifier(),
            session_factory=self._require_session_factory(),
            last_seen_update_interval_seconds=(self._settings.firebase_auth_last_seen_update_interval_seconds),
            new_user_initializer=self._new_user_initializer,
        )

    def create_stateless_dependency(self) -> StatelessFirebaseAuthDependency:
        return StatelessFirebaseAuthDependency(
            token_extractor=BearerTokenExtractor(),
            token_verifier=self.create_token_verifier(),
        )

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client

        return get_shared_http_client(
            timeout_seconds=(self._settings.firebase_public_key_fetch_timeout_seconds),
        )

    def _require_session_factory(self) -> AsyncSessionFactory:
        if self._session_factory is None:
            raise AuthenticationConfigurationError
        return self._session_factory


def create_firebase_auth_dependency(
    settings: FirebaseAuthSettings,
    session_factory: AsyncSessionFactory | None = None,
    http_client: httpx.AsyncClient | None = None,
    new_user_initializer: NewUserInitializer | None = None,
) -> FirebaseAuthDependency:
    return FirebaseAuthFactory(
        settings=settings,
        session_factory=session_factory,
        http_client=http_client,
        new_user_initializer=new_user_initializer,
    ).create_dependency()


def create_stateless_firebase_auth_dependency(
    settings: FirebaseAuthSettings,
    http_client: httpx.AsyncClient | None = None,
) -> StatelessFirebaseAuthDependency:
    return FirebaseAuthFactory(
        settings=settings,
        http_client=http_client,
    ).create_stateless_dependency()
