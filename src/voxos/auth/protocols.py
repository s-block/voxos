from typing import Protocol

from voxos.auth.models import VerifiedFirebaseIdentity


class FirebaseIdentityVerifier(Protocol):
    async def verify(self, token: str) -> VerifiedFirebaseIdentity:
        """Validate a Firebase ID token and return the verified identity."""
