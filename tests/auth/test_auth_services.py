from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from voxos.auth.models import FirebaseTokenClaims, VerifiedFirebaseIdentity
from voxos.auth.records import FirebaseUserRecord
from voxos.auth.services import FirebaseUserService
from voxos.db.session import AsyncSessionFactory


@dataclass(slots=True)
class MutableDateTimeClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


def verified_identity(
    firebase_user_id: str = "firebase-user-123",
    email: str = "user@example.com",
    display_name: str = "Test User",
) -> VerifiedFirebaseIdentity:
    claims = FirebaseTokenClaims.model_validate(
        {
            "sub": firebase_user_id,
            "aud": "project-id",
            "iss": "issuer",
            "auth_time": 1,
            "iat": 1,
            "exp": 2,
            "email": email,
            "email_verified": True,
            "name": display_name,
            "picture": "https://example.com/user.png",
        },
    )
    return VerifiedFirebaseIdentity.from_claims(claims)


def as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@pytest.mark.asyncio
async def test_firebase_user_service_creates_record(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    clock = MutableDateTimeClock(now)

    async with sqlite_session_factory() as session:
        record = await FirebaseUserService(session, clock).upsert_from_verified_identity(
            identity=verified_identity(),
            last_seen_update_interval_seconds=300,
        )

    assert record.firebase_user_id == "firebase-user-123"
    assert record.email == "user@example.com"
    assert record.email_verified is True
    assert record.display_name == "Test User"
    assert record.photo_url == "https://example.com/user.png"
    assert as_aware_utc(record.last_seen_at) == now


@pytest.mark.asyncio
async def test_firebase_user_service_updates_safe_profile_fields(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    clock = MutableDateTimeClock(datetime(2026, 6, 12, 12, 0, tzinfo=UTC))

    async with sqlite_session_factory() as session:
        service = FirebaseUserService(session, clock)
        await service.upsert_from_verified_identity(
            identity=verified_identity(),
            last_seen_update_interval_seconds=300,
        )
        clock.value = clock.value + timedelta(seconds=60)
        record = await service.upsert_from_verified_identity(
            identity=verified_identity(
                email="next@example.com",
                display_name="Next User",
            ),
            last_seen_update_interval_seconds=300,
        )

    assert record.email == "next@example.com"
    assert record.display_name == "Next User"


@pytest.mark.asyncio
async def test_firebase_user_service_keeps_local_id_stable(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    clock = MutableDateTimeClock(datetime(2026, 6, 12, 12, 0, tzinfo=UTC))

    async with sqlite_session_factory() as session:
        service = FirebaseUserService(session, clock)
        first = await service.upsert_from_verified_identity(
            identity=verified_identity(),
            last_seen_update_interval_seconds=300,
        )
        second = await service.upsert_from_verified_identity(
            identity=verified_identity(email="next@example.com"),
            last_seen_update_interval_seconds=300,
        )

    assert second.id == first.id
    assert second.firebase_user_id == "firebase-user-123"
    assert second.email == "next@example.com"


@pytest.mark.asyncio
async def test_firebase_user_service_throttles_last_seen_updates(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    clock = MutableDateTimeClock(datetime(2026, 6, 12, 12, 0, tzinfo=UTC))

    async with sqlite_session_factory() as session:
        service = FirebaseUserService(session, clock)
        first = await service.upsert_from_verified_identity(
            identity=verified_identity(),
            last_seen_update_interval_seconds=300,
        )
        first_seen_at = as_aware_utc(first.last_seen_at)

        clock.value = clock.value + timedelta(seconds=60)
        second = await service.upsert_from_verified_identity(
            identity=verified_identity(),
            last_seen_update_interval_seconds=300,
        )
        second_seen_at = as_aware_utc(second.last_seen_at)

        clock.value = clock.value + timedelta(seconds=300)
        third = await service.upsert_from_verified_identity(
            identity=verified_identity(),
            last_seen_update_interval_seconds=300,
        )

    assert first_seen_at == second_seen_at
    assert as_aware_utc(third.last_seen_at) > second_seen_at


@pytest.mark.asyncio
async def test_firebase_user_record_enforces_unique_firebase_uid(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        session.add(FirebaseUserRecord(firebase_user_id="firebase-user-123"))
        session.add(FirebaseUserRecord(firebase_user_id="firebase-user-123"))

        with pytest.raises(IntegrityError):
            await session.commit()
