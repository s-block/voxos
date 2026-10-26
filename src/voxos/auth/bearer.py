from voxos.auth.errors import (
    InvalidAuthorizationHeaderError,
    MissingAuthorizationTokenError,
)


class BearerTokenExtractor:
    def extract(self, authorization_header: str | None) -> str:
        if authorization_header is None or authorization_header.strip() == "":
            raise MissingAuthorizationTokenError

        parts = authorization_header.strip().split()
        if len(parts) != 2:
            raise InvalidAuthorizationHeaderError

        scheme, token = parts
        if scheme.lower() != "bearer" or token.strip() == "":
            raise InvalidAuthorizationHeaderError

        return token
