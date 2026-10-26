from dataclasses import dataclass
from typing import Protocol

import firebase_admin
from firebase_admin import exceptions, messaging

TRANSIENT_FIREBASE_CODES = frozenset(
    {
        exceptions.ABORTED,
        exceptions.CANCELLED,
        exceptions.DEADLINE_EXCEEDED,
        exceptions.INTERNAL,
        exceptions.RESOURCE_EXHAUSTED,
        exceptions.UNAVAILABLE,
        exceptions.UNKNOWN,
    },
)


@dataclass(frozen=True, slots=True)
class PushDeliveryTarget:
    installation_id: str
    registration_token: str


@dataclass(frozen=True, slots=True)
class PushMessage:
    title: str
    body: str
    data: dict[str, str]


@dataclass(frozen=True, slots=True)
class PushSendResult:
    completed_installation_ids: tuple[str, ...]
    invalid_installation_ids: tuple[str, ...]
    transient_installation_ids: tuple[str, ...]
    error_code: str | None = None


class PushSender(Protocol):
    async def send(
        self,
        *,
        message: PushMessage,
        targets: tuple[PushDeliveryTarget, ...],
    ) -> PushSendResult:
        """Deliver one notification to the supplied FCM registrations."""


class FirebasePushSender:
    def __init__(self, *, project_id: str) -> None:
        try:
            self._app = firebase_admin.get_app()
        except ValueError:
            self._app = firebase_admin.initialize_app(
                options={"projectId": project_id},
            )

    async def send(
        self,
        *,
        message: PushMessage,
        targets: tuple[PushDeliveryTarget, ...],
    ) -> PushSendResult:
        completed: list[str] = []
        invalid: list[str] = []
        transient: list[str] = []
        last_error_code: str | None = None

        for offset in range(0, len(targets), 500):
            batch = targets[offset : offset + 500]
            multicast = messaging.MulticastMessage(
                tokens=[target.registration_token for target in batch],
                data=message.data,
                notification=messaging.Notification(
                    title=message.title,
                    body=message.body,
                ),
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id="agent_updates",
                        icon="ic_stat_voxos",
                        sound="default",
                    ),
                ),
                apns=messaging.APNSConfig(
                    headers={"apns-priority": "10"},
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            content_available=True,
                            sound="default",
                        ),
                    ),
                ),
            )
            try:
                response = await messaging.send_each_for_multicast_async(
                    multicast,
                    dry_run=False,
                    app=self._app,
                )
            except exceptions.FirebaseError as exc:
                last_error_code = exc.code
                if exc.code in TRANSIENT_FIREBASE_CODES:
                    transient.extend(target.installation_id for target in batch)
                else:
                    completed.extend(target.installation_id for target in batch)
                continue

            for target, send_response in zip(batch, response.responses, strict=True):
                if send_response.success:
                    completed.append(target.installation_id)
                    continue
                error = send_response.exception
                if isinstance(
                    error,
                    (messaging.SenderIdMismatchError, messaging.UnregisteredError),
                ):
                    invalid.append(target.installation_id)
                    last_error_code = type(error).__name__
                elif isinstance(error, exceptions.FirebaseError) and error.code in TRANSIENT_FIREBASE_CODES:
                    transient.append(target.installation_id)
                    last_error_code = error.code
                else:
                    completed.append(target.installation_id)
                    last_error_code = (
                        error.code if isinstance(error, exceptions.FirebaseError) else type(error).__name__
                    )

        return PushSendResult(
            completed_installation_ids=tuple(completed),
            invalid_installation_ids=tuple(invalid),
            transient_installation_ids=tuple(transient),
            error_code=last_error_code,
        )
