from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from voxos.auth.models import AuthenticatedUser
from voxos.contacts.models import (
    Contact,
    ContactCreateRequest,
    ContactListResponse,
    ContactUpdateRequest,
)
from voxos.contacts.services import (
    ContactPhoneNumberConflictError,
    UserContactService,
)
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
CONTACT_NOT_FOUND = "Contact was not found"
CONTACTS_UNAVAILABLE = "Contacts are unavailable"


def create_contacts_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
) -> APIRouter:
    router = APIRouter(prefix="/api/contacts", tags=["contacts"])

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def scoped_session_factory_for(user_id: str) -> AsyncSessionFactory:
        return tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )

    @router.get("", response_model=ContactListResponse)
    async def list_contacts(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> ContactListResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                contacts = await UserContactService(session).list_contacts(
                    user_id=current_user.user_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return ContactListResponse(contacts=contacts)

    @router.post("", response_model=Contact, status_code=status.HTTP_201_CREATED)
    async def create_contact(
        request_body: ContactCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> Contact:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await UserContactService(session).create_contact(
                    user_id=current_user.user_id,
                    request=request_body,
                )
        except ContactPhoneNumberConflictError as exc:
            raise conflict() from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

    @router.patch("/{contact_id}", response_model=Contact)
    async def update_contact(
        contact_id: str,
        request_body: ContactUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> Contact:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                contact = await UserContactService(session).update_contact(
                    user_id=current_user.user_id,
                    contact_id=contact_id,
                    request=request_body,
                )
        except ContactPhoneNumberConflictError as exc:
            raise conflict() from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if contact is None:
            raise not_found()
        return contact

    @router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_contact(
        contact_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> None:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                deleted = await UserContactService(session).delete_contact(
                    user_id=current_user.user_id,
                    contact_id=contact_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if not deleted:
            raise not_found()

    return router


def not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=CONTACT_NOT_FOUND)


def conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A contact with this phone number already exists",
    )


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=CONTACTS_UNAVAILABLE,
    )
