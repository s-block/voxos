from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from voxos.api.livekit import AuthenticatedUserDependency
from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.notifications.installations import (
    PushInstallationConflictError,
    PushInstallationService,
)
from voxos.notifications.models import PushPlatform


class PushInstallationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    installation_id: str = Field(min_length=1, max_length=256)
    registration_token: str = Field(min_length=1, max_length=4096, repr=False)
    platform: Literal["android", "ios"]

    @field_validator("installation_id", "registration_token", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class PushInstallationRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    installation_id: str = Field(min_length=1, max_length=256)

    @field_validator("installation_id", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


def create_push_notifications_router(
    *,
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
) -> APIRouter:
    router = APIRouter(prefix="/api/push/installations", tags=["push-notifications"])

    @router.post("/register", status_code=status.HTTP_204_NO_CONTENT)
    async def register_installation(
        request: PushInstallationRequest,
        current_user: Annotated[AuthenticatedUser, Depends(auth_dependency)],
    ) -> Response:
        service = PushInstallationService(
            session_factory=tenant_scoped_session_factory(
                session_factory,
                current_user.user_id,
            ),
        )
        try:
            await service.register(
                user_id=current_user.user_id,
                installation_id=request.installation_id,
                registration_token=request.registration_token,
                platform=PushPlatform(request.platform),
            )
        except PushInstallationConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/revoke", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_installation(
        request: PushInstallationRevokeRequest,
        current_user: Annotated[AuthenticatedUser, Depends(auth_dependency)],
    ) -> Response:
        service = PushInstallationService(
            session_factory=tenant_scoped_session_factory(
                session_factory,
                current_user.user_id,
            ),
        )
        await service.revoke(
            user_id=current_user.user_id,
            installation_id=request.installation_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
