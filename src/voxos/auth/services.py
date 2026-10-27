from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.auth.errors import MissingVerifiedFirebaseClaimsError
from voxos.auth.models import AuthenticatedUser, VerifiedFirebaseIdentity
from voxos.auth.records import FirebaseUserRecord, utc_now

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class FirebaseUserResolution:
    record: FirebaseUserRecord
    created: bool


class FirebaseUserService:
    def __init__(
        self,
        session: AsyncSession,
        clock: Clock = utc_now,
    ) -> None:
        self._session = session
        self._clock = clock

    async def get_by_firebase_user_id(
        self,
        firebase_user_id: str,
    ) -> FirebaseUserRecord | None:
        statement = select(FirebaseUserRecord).where(
            col(FirebaseUserRecord.firebase_user_id) == firebase_user_id,
        )
        result = await self._session.exec(statement)
        return result.one_or_none()

    async def upsert_from_verified_identity(
        self,
        identity: VerifiedFirebaseIdentity,
        last_seen_update_interval_seconds: int,
    ) -> FirebaseUserRecord:
        resolution = await self.resolve_from_verified_identity(
            identity=identity,
            last_seen_update_interval_seconds=last_seen_update_interval_seconds,
        )
        return resolution.record

    async def resolve_from_verified_identity(
        self,
        identity: VerifiedFirebaseIdentity,
        last_seen_update_interval_seconds: int,
    ) -> FirebaseUserResolution:
        now = self._clock()
        record = await self.get_by_firebase_user_id(identity.firebase_user_id)
        if record is None:
            return await self._create_record(identity=identity, now=now)

        return FirebaseUserResolution(
            record=await self._update_record(
                record=record,
                identity=identity,
                now=now,
                last_seen_update_interval_seconds=last_seen_update_interval_seconds,
            ),
            created=False,
        )

    async def upsert_from_authenticated_user(
        self,
        user: AuthenticatedUser,
        last_seen_update_interval_seconds: int,
    ) -> FirebaseUserRecord:
        if user.claims is None:
            raise MissingVerifiedFirebaseClaimsError

        identity = VerifiedFirebaseIdentity(
            firebase_user_id=user.firebase_user_id,
            email=user.email,
            email_verified=user.email_verified,
            display_name=user.display_name,
            photo_url=user.photo_url,
            claims=user.claims,
        )
        return await self.upsert_from_verified_identity(
            identity=identity,
            last_seen_update_interval_seconds=last_seen_update_interval_seconds,
        )

    async def mark_application_defaults_initialized(
        self,
        *,
        user_id: str,
        version: str,
    ) -> FirebaseUserRecord:
        record = await self._session.get(FirebaseUserRecord, user_id)
        if record is None:
            message = f"Firebase user record was not found: {user_id}"
            raise RuntimeError(message)
        if record.application_defaults_version == version:
            return record
        record.application_defaults_version = version
        record.updated_at = self._clock()
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def _create_record(
        self,
        identity: VerifiedFirebaseIdentity,
        now: datetime,
    ) -> FirebaseUserResolution:
        record = FirebaseUserRecord(
            firebase_user_id=identity.firebase_user_id,
            email=str(identity.email) if identity.email is not None else None,
            email_verified=identity.email_verified,
            display_name=identity.display_name,
            photo_url=identity.photo_url,
            created_at=now,
            updated_at=now,
            last_seen_at=now,
        )
        self._session.add(record)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing_record = await self.get_by_firebase_user_id(identity.firebase_user_id)
            if existing_record is None:
                raise
            return FirebaseUserResolution(
                record=await self._update_record(
                    record=existing_record,
                    identity=identity,
                    now=now,
                    last_seen_update_interval_seconds=0,
                ),
                created=False,
            )

        await self._session.refresh(record)
        return FirebaseUserResolution(record=record, created=True)

    async def _update_record(
        self,
        record: FirebaseUserRecord,
        identity: VerifiedFirebaseIdentity,
        now: datetime,
        last_seen_update_interval_seconds: int,
    ) -> FirebaseUserRecord:
        changed = self._update_profile_fields(record, identity)
        if self._should_update_last_seen(
            record=record,
            now=now,
            interval=timedelta(seconds=last_seen_update_interval_seconds),
        ):
            record.last_seen_at = now
            changed = True

        if changed:
            record.updated_at = now
            await self._session.commit()
            await self._session.refresh(record)

        return record

    def _update_profile_fields(
        self,
        record: FirebaseUserRecord,
        identity: VerifiedFirebaseIdentity,
    ) -> bool:
        next_email = str(identity.email) if identity.email is not None else None
        changed = False
        if record.email != next_email:
            record.email = next_email
            changed = True
        if record.email_verified != identity.email_verified:
            record.email_verified = identity.email_verified
            changed = True
        if record.display_name != identity.display_name:
            record.display_name = identity.display_name
            changed = True
        if record.photo_url != identity.photo_url:
            record.photo_url = identity.photo_url
            changed = True
        return changed

    def _should_update_last_seen(
        self,
        record: FirebaseUserRecord,
        now: datetime,
        interval: timedelta,
    ) -> bool:
        if interval.total_seconds() <= 0:
            return True

        last_seen_at = record.last_seen_at
        if last_seen_at.tzinfo is None:
            last_seen_at = last_seen_at.replace(tzinfo=UTC)

        return now - last_seen_at >= interval
