class AuthenticationError(Exception):
    """Base exception for authentication failures."""


class AuthenticationConfigurationError(AuthenticationError):
    def __init__(self) -> None:
        super().__init__("authentication dependency is not configured correctly")


class FirebaseRevocationUnavailableError(AuthenticationError):
    def __init__(self) -> None:
        super().__init__("firebase session verification is temporarily unavailable")


class MissingVerifiedFirebaseClaimsError(AuthenticationError):
    def __init__(self) -> None:
        super().__init__("authenticated user does not include verified firebase claims")


class MissingAuthorizationTokenError(AuthenticationError):
    def __init__(self) -> None:
        super().__init__("authorization bearer token is missing")


class InvalidAuthorizationHeaderError(AuthenticationError):
    def __init__(self) -> None:
        super().__init__("authorization header must be a bearer token")


class InvalidFirebaseTokenError(AuthenticationError):
    def __init__(self, reason: str = "firebase token is invalid") -> None:
        super().__init__(reason)

    @classmethod
    def invalid_header(cls) -> "InvalidFirebaseTokenError":
        return cls("firebase token header is invalid")

    @classmethod
    def invalid_claims(cls) -> "InvalidFirebaseTokenError":
        return cls("firebase token claims are invalid")

    @classmethod
    def invalid_audience(cls) -> "InvalidFirebaseTokenError":
        return cls("firebase token audience is invalid")

    @classmethod
    def invalid_issuer(cls) -> "InvalidFirebaseTokenError":
        return cls("firebase token issuer is invalid")

    @classmethod
    def invalid_subject(cls) -> "InvalidFirebaseTokenError":
        return cls("firebase token subject is invalid")

    @classmethod
    def invalid_issued_at(cls) -> "InvalidFirebaseTokenError":
        return cls("firebase token issued-at is invalid")

    @classmethod
    def invalid_auth_time(cls) -> "InvalidFirebaseTokenError":
        return cls("firebase token auth_time is invalid")

    @classmethod
    def expired(cls) -> "InvalidFirebaseTokenError":
        return cls("firebase token has expired")


class FirebasePublicKeyFetchError(AuthenticationError):
    def __init__(self, reason: str = "firebase public keys could not be fetched") -> None:
        super().__init__(reason)

    @classmethod
    def empty_response(cls) -> "FirebasePublicKeyFetchError":
        return cls("firebase public key response was empty")

    @classmethod
    def invalid_response(cls) -> "FirebasePublicKeyFetchError":
        return cls("firebase public key response was invalid")

    @classmethod
    def invalid_material(cls) -> "FirebasePublicKeyFetchError":
        return cls("firebase public key material was invalid")

    @classmethod
    def invalid_certificate(cls) -> "FirebasePublicKeyFetchError":
        return cls("firebase public key certificate was invalid")

    @classmethod
    def non_rsa_certificate(cls) -> "FirebasePublicKeyFetchError":
        return cls("firebase public key certificate was not RSA")


class FirebasePublicKeyNotFoundError(AuthenticationError):
    def __init__(self, key_id: str) -> None:
        super().__init__(f"firebase public key was not found for kid={key_id!r}")
