from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.notifications.models import PushInstallationRecord, PushPlatform


class PushInstallationConflictError(ValueError):
    def __init__(self) -> None:
        super().__init__("This app installation is registered to another account.")


@dataclass(frozen=True, slots=True)
class PushInstallationService:
    session_factory: AsyncSessionFactory

    async def register(
        self,
        *,
        user_id: str,
        installation_id: str,
        registration_token: str,
        platform: PushPlatform,
    ) -> None:
        now = utc_now()
        try:
            async with self.session_factory() as session:
                result = await session.exec(
                    select(PushInstallationRecord).where(
                        col(PushInstallationRecord.user_id) == user_id,
                        col(PushInstallationRecord.installation_id) == installation_id,
                    ),
                )
                record = result.one_or_none()
                if record is None:
                    record = PushInstallationRecord(
                        user_id=user_id,
                        installation_id=installation_id,
                        registration_token=registration_token,
                        platform=platform.value,
                        active=True,
                        last_seen_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(record)
                else:
                    record.registration_token = registration_token
                    record.platform = platform.value
                    record.active = True
                    record.revoked_at = None
                    record.last_seen_at = now
                    record.updated_at = now
                await session.commit()
        except IntegrityError as exc:
            raise PushInstallationConflictError from exc

    async def revoke(self, *, user_id: str, installation_id: str) -> None:
        async with self.session_factory() as session:
            result = await session.exec(
                select(PushInstallationRecord).where(
                    col(PushInstallationRecord.user_id) == user_id,
                    col(PushInstallationRecord.installation_id) == installation_id,
                ),
            )
            record = result.one_or_none()
            if record is not None:
                await session.delete(record)
                await session.commit()
