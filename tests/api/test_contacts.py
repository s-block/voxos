from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from voxos.api.contacts import create_contacts_router
from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


def authenticated_user(user_id: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        firebase_user_id=f"firebase-{user_id}",
        email="user@example.com",
        email_verified=True,
    )


def create_test_client(
    *,
    sqlite_session_factory: AsyncSessionFactory,
    user_id: str,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_contacts_router(
            auth_dependency=StaticAuthDependency(authenticated_user(user_id)),
            session_factory=sqlite_session_factory,
        ),
    )
    return TestClient(app)


def test_contacts_api_crud_and_optional_field_clearing(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="owner-user",
    )

    create_response = client.post(
        "/api/contacts",
        json={
            "display_name": "  Alice Example  ",
            "phone_number": "  +447700900123  ",
            "email": "alice@example.com",
            "notes": "  Call after lunch  ",
            "is_admin": False,
        },
    )
    contact = create_response.json()
    list_response = client.get("/api/contacts")
    update_response = client.patch(
        f"/api/contacts/{contact['id']}",
        json={"email": None, "notes": None, "is_admin": True},
    )
    delete_response = client.delete(f"/api/contacts/{contact['id']}")
    missing_response = client.patch(
        f"/api/contacts/{contact['id']}",
        json={"display_name": "Missing"},
    )

    assert create_response.status_code == 201
    assert contact["user_id"] == "owner-user"
    assert contact["display_name"] == "Alice Example"
    assert contact["phone_number"] == "+447700900123"
    assert contact["notes"] == "Call after lunch"
    assert list_response.status_code == 200
    assert list_response.json()["contacts"] == [contact]
    assert update_response.status_code == 200
    assert update_response.json()["email"] is None
    assert update_response.json()["notes"] is None
    assert update_response.json()["is_admin"] is True
    assert delete_response.status_code == 204
    assert missing_response.status_code == 404


def test_contacts_api_rejects_invalid_or_authority_bearing_input(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="owner-user",
    )

    invalid_phone = client.post(
        "/api/contacts",
        json={"display_name": "Alice", "phone_number": "07700900123"},
    )
    invalid_email = client.post(
        "/api/contacts",
        json={
            "display_name": "Alice",
            "phone_number": "+447700900123",
            "email": "not-an-email",
        },
    )
    supplied_user_id = client.post(
        "/api/contacts",
        json={
            "user_id": "other-user",
            "display_name": "Alice",
            "phone_number": "+447700900123",
        },
    )
    empty_patch = client.patch("/api/contacts/contact-id", json={})
    null_admin = client.patch(
        "/api/contacts/contact-id",
        json={"is_admin": None},
    )

    assert invalid_phone.status_code == 422
    assert invalid_email.status_code == 422
    assert supplied_user_id.status_code == 422
    assert empty_patch.status_code == 422
    assert null_admin.status_code == 422


def test_contacts_api_enforces_tenant_ownership_and_phone_uniqueness(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    owner_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="owner-user",
    )
    other_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_id="other-user",
    )
    contact_request = {
        "display_name": "Shared number",
        "phone_number": "+12025550123",
    }

    create_response = owner_client.post("/api/contacts", json=contact_request)
    contact = create_response.json()
    duplicate_response = owner_client.post(
        "/api/contacts",
        json=contact_request | {"display_name": "Duplicate"},
    )
    other_user_same_number_response = other_client.post(
        "/api/contacts",
        json=contact_request,
    )
    wrong_user_update = other_client.patch(
        f"/api/contacts/{contact['id']}",
        json={"display_name": "Forbidden"},
    )
    wrong_user_delete = other_client.delete(f"/api/contacts/{contact['id']}")

    assert create_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert "+12025550123" not in duplicate_response.text
    assert other_user_same_number_response.status_code == 201
    assert wrong_user_update.status_code == 404
    assert wrong_user_delete.status_code == 404
    assert owner_client.get("/api/contacts").json()["contacts"] == [contact]
