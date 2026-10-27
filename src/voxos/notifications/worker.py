import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_
from sqlmodel import col, select

from voxos.db.models import new_record_id, utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.notifications.models import (
    PushInstallationRecord,
    PushNotificationOutboxRecord,
    PushOutboxStatus,
)
from voxos.notifications.sender import (
    PushDeliveryTarget,
    PushMessage,
    PushSender,
    PushSendResult,
)
from voxos.notifications.settings import PushNotificationSettings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PushNotificationDeliveryWorker:
    session_factory: AsyncSessionFactory
    sender: PushSender
    settings: PushNotificationSettings
    worker_id: str

    async def run_once(self) -> int:
        processed = 0
        for _ in range(self.settings.claim_batch_size):
            outbox = await self._claim_next()
            if outbox is None:
                break
            await self._deliver(outbox)
            processed += 1
        return processed

    async def _claim_next(self) -> PushNotificationOutboxRecord | None:
        while True:
            now = utc_now()
            async with self.session_factory() as session:
                statement = (
                    select(PushNotificationOutboxRecord)
                    .where(
                        or_(
                            and_(
                                col(PushNotificationOutboxRecord.status).in_(
                                    (
                                        PushOutboxStatus.PENDING.value,
                                        PushOutboxStatus.RETRY.value,
                                    ),
                                ),
                                col(PushNotificationOutboxRecord.next_attempt_at) <= now,
                            ),
                            and_(
                                col(PushNotificationOutboxRecord.status) == PushOutboxStatus.PROCESSING.value,
                                col(PushNotificationOutboxRecord.lease_expires_at).is_not(None),
                                col(PushNotificationOutboxRecord.lease_expires_at) <= now,
                            ),
                        ),
                    )
                    .order_by(
                        col(PushNotificationOutboxRecord.next_attempt_at),
                        col(PushNotificationOutboxRecord.created_at),
                    )
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                result = await session.exec(statement)
                record = result.first()
                if record is None:
                    return None
                if record.attempt_count >= record.max_attempts:
                    record.status = PushOutboxStatus.FAILED.value
                    record.lease_owner = None
                    record.lease_expires_at = None
                    record.last_error_code = record.last_error_code or "lease_expired"
                    record.updated_at = now
                    await session.commit()
                    continue
                record.status = PushOutboxStatus.PROCESSING.value
                record.attempt_count += 1
                record.lease_owner = self._new_lease_owner()
                record.lease_expires_at = now + timedelta(
                    seconds=self.settings.lease_seconds,
                )
                record.updated_at = now
                await session.commit()
                await session.refresh(record)
                return record

    async def _deliver(self, outbox: PushNotificationOutboxRecord) -> None:
        lease_owner = outbox.lease_owner
        if lease_owner is None:
            error_message = "Claimed push notification is missing its lease owner"
            raise RuntimeError(error_message)
        installations = await self._load_active_installations(outbox.user_id)
        installation_by_id = {record.id: record for record in installations}
        targets = list(outbox.target_installation_ids_json)
        completed = set(outbox.completed_installation_ids_json)
        if not targets:
            targets = list(installation_by_id)
            if not await self._set_targets(outbox.id, lease_owner, targets):
                return

        pending_ids = [
            installation_id
            for installation_id in targets
            if installation_id not in completed and installation_id in installation_by_id
        ]
        completed.update(installation_id for installation_id in targets if installation_id not in installation_by_id)
        if not pending_ids:
            await self._mark_delivered(outbox.id, lease_owner, completed)
            return

        message = PushMessage(
            title=outbox.title,
            body=outbox.body,
            data=dict(outbox.data_json),
        )
        delivery_targets = tuple(
            PushDeliveryTarget(
                installation_id=installation_id,
                registration_token=installation_by_id[installation_id].registration_token,
            )
            for installation_id in pending_ids
        )
        try:
            result = await self._send_with_lease_heartbeat(
                outbox_id=outbox.id,
                lease_owner=lease_owner,
                message=message,
                targets=delivery_targets,
            )
        except Exception as exc:
            logger.exception(
                "Push notification sender failed",
                extra={"outbox_id": outbox.id, "error_type": type(exc).__name__},
            )
            await self._finalize_attempt(
                outbox=outbox,
                lease_owner=lease_owner,
                completed=completed,
                invalid_ids=set(),
                has_transient_failures=True,
                error_code=type(exc).__name__,
            )
            return
        completed.update(result.completed_installation_ids)
        completed.update(result.invalid_installation_ids)
        await self._finalize_attempt(
            outbox=outbox,
            lease_owner=lease_owner,
            completed=completed,
            invalid_ids=set(result.invalid_installation_ids),
            has_transient_failures=bool(result.transient_installation_ids),
            error_code=result.error_code,
        )

    async def _load_active_installations(
        self,
        user_id: str,
    ) -> tuple[PushInstallationRecord, ...]:
        async with self.session_factory() as session:
            result = await session.exec(
                select(PushInstallationRecord).where(
                    col(PushInstallationRecord.user_id) == user_id,
                    col(PushInstallationRecord.active).is_(True),
                ),
            )
            return tuple(result.all())

    async def _set_targets(
        self,
        outbox_id: str,
        lease_owner: str,
        targets: list[str],
    ) -> bool:
        async with self.session_factory() as session:
            record = await session.get(PushNotificationOutboxRecord, outbox_id)
            if record is None or record.lease_owner != lease_owner:
                return False
            record.target_installation_ids_json = targets
            record.updated_at = utc_now()
            await session.commit()
            return True

    async def _mark_delivered(
        self,
        outbox_id: str,
        lease_owner: str,
        completed: set[str],
    ) -> None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await session.get(PushNotificationOutboxRecord, outbox_id)
            if record is None or record.lease_owner != lease_owner:
                return
            record.completed_installation_ids_json = sorted(completed)
            record.status = PushOutboxStatus.DELIVERED.value
            record.delivered_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            record.last_error_code = None
            record.updated_at = now
            await session.commit()

    async def _finalize_attempt(
        self,
        *,
        outbox: PushNotificationOutboxRecord,
        lease_owner: str,
        completed: set[str],
        invalid_ids: set[str],
        has_transient_failures: bool,
        error_code: str | None,
    ) -> None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await session.get(PushNotificationOutboxRecord, outbox.id)
            if record is None or record.lease_owner != lease_owner:
                return
            if invalid_ids:
                result = await session.exec(
                    select(PushInstallationRecord).where(
                        col(PushInstallationRecord.id).in_(invalid_ids),
                    ),
                )
                for installation in result.all():
                    installation.active = False
                    installation.revoked_at = now
                    installation.updated_at = now

            record.completed_installation_ids_json = sorted(completed)
            targets_completed = set(record.target_installation_ids_json) <= completed
            if targets_completed or not has_transient_failures:
                record.status = PushOutboxStatus.DELIVERED.value
                record.delivered_at = now
            elif record.attempt_count >= record.max_attempts:
                record.status = PushOutboxStatus.FAILED.value
            else:
                record.status = PushOutboxStatus.RETRY.value
                delay = self.settings.retry_base_seconds * 2 ** min(
                    record.attempt_count - 1,
                    6,
                )
                record.next_attempt_at = now + timedelta(seconds=delay)
            record.lease_owner = None
            record.lease_expires_at = None
            record.last_error_code = error_code
            record.updated_at = now
            await session.commit()

    async def _send_with_lease_heartbeat(
        self,
        *,
        outbox_id: str,
        lease_owner: str,
        message: PushMessage,
        targets: tuple[PushDeliveryTarget, ...],
    ) -> PushSendResult:
        stop_event = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._lease_heartbeat(
                outbox_id=outbox_id,
                lease_owner=lease_owner,
                stop_event=stop_event,
            ),
        )
        try:
            result = await self.sender.send(message=message, targets=targets)
            await self._renew_lease(outbox_id=outbox_id, lease_owner=lease_owner)
            return result
        finally:
            stop_event.set()
            await heartbeat

    async def _lease_heartbeat(
        self,
        *,
        outbox_id: str,
        lease_owner: str,
        stop_event: asyncio.Event,
    ) -> None:
        interval_seconds = max(1.0, self.settings.lease_seconds / 3)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=interval_seconds,
                )
            except TimeoutError:
                try:
                    if not await self._renew_lease(
                        outbox_id=outbox_id,
                        lease_owner=lease_owner,
                    ):
                        return
                except Exception:
                    logger.exception(
                        "Push notification lease renewal failed",
                        extra={"outbox_id": outbox_id},
                    )

    async def _renew_lease(self, *, outbox_id: str, lease_owner: str) -> bool:
        now = utc_now()
        async with self.session_factory() as session:
            record = await session.get(PushNotificationOutboxRecord, outbox_id)
            if (
                record is None
                or record.lease_owner != lease_owner
                or record.status != PushOutboxStatus.PROCESSING.value
            ):
                return False
            record.lease_expires_at = now + timedelta(
                seconds=self.settings.lease_seconds,
            )
            record.updated_at = now
            await session.commit()
            return True

    def _new_lease_owner(self) -> str:
        worker_prefix = self.worker_id.strip() or "push-worker"
        return f"{worker_prefix[:91]}:{new_record_id()}"
