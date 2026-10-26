from voxos.auth.models import (
    AuthenticatedUser,
    FirebaseTokenClaims,
    VerifiedFirebaseIdentity,
)


def test_verified_firebase_identity_is_distinct_from_authenticated_user() -> None:
    claims = FirebaseTokenClaims.model_validate(
        {
            "sub": "firebase-user-123",
            "aud": "project-id",
            "iss": "issuer",
            "auth_time": 1,
            "iat": 1,
            "exp": 2,
            "email": "user@example.com",
        },
    )
    identity = VerifiedFirebaseIdentity.from_claims(claims)

    user = AuthenticatedUser.from_verified_identity(
        user_id="local-user-123",
        identity=identity,
    )

    assert identity.firebase_user_id == "firebase-user-123"
    assert user.user_id == "local-user-123"
    assert user.firebase_user_id == "firebase-user-123"
    assert user.local_user_id == "local-user-123"
