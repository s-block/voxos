from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field

STANDARD_FIREBASE_CLAIMS = frozenset(
    {
        "aud",
        "auth_time",
        "email",
        "email_verified",
        "exp",
        "firebase",
        "iat",
        "iss",
        "name",
        "picture",
        "sub",
        "user_id",
    },
)


class FirebaseTokenHeader(BaseModel):
    model_config = ConfigDict(frozen=True)

    alg: Literal["RS256"]
    kid: str = Field(min_length=1)


class FirebaseTokenClaims(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    uid: str = Field(min_length=1, validation_alias="sub")
    aud: str = Field(min_length=1)
    iss: str = Field(min_length=1)
    auth_time: int = Field(ge=0)
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    email: EmailStr | None = None
    email_verified: bool | None = None
    name: str | None = None
    picture: str | None = None
    custom_claims: dict[str, object] = Field(default_factory=dict, repr=False)

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> Self:
        model_input = dict(payload)
        model_input["custom_claims"] = {
            key: value for key, value in payload.items() if key not in STANDARD_FIREBASE_CLAIMS
        }
        return cls.model_validate(model_input)


class VerifiedFirebaseIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    firebase_user_id: str = Field(min_length=1)
    email: EmailStr | None = None
    email_verified: bool | None = None
    display_name: str | None = None
    photo_url: str | None = None
    claims: FirebaseTokenClaims = Field(repr=False)

    @classmethod
    def from_claims(cls, claims: FirebaseTokenClaims) -> Self:
        return cls(
            firebase_user_id=claims.uid,
            email=claims.email,
            email_verified=claims.email_verified,
            display_name=claims.name,
            photo_url=claims.picture,
            claims=claims,
        )


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    firebase_user_id: str = Field(min_length=1)
    email: EmailStr | None = None
    email_verified: bool | None = None
    display_name: str | None = None
    photo_url: str | None = None
    claims: FirebaseTokenClaims | None = Field(default=None, repr=False)

    @property
    def local_user_id(self) -> str:
        return self.user_id

    @classmethod
    def from_verified_identity(
        cls,
        user_id: str,
        identity: VerifiedFirebaseIdentity,
    ) -> Self:
        return cls(
            user_id=user_id,
            firebase_user_id=identity.firebase_user_id,
            email=identity.email,
            email_verified=identity.email_verified,
            display_name=identity.display_name,
            photo_url=identity.photo_url,
            claims=identity.claims,
        )
