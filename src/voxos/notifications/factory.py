from voxos.auth.settings import FirebaseAuthSettings
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import notification_worker_session_factory
from voxos.notifications.sender import FirebasePushSender
from voxos.notifications.settings import PushNotificationSettings
from voxos.notifications.worker import PushNotificationDeliveryWorker


def create_push_delivery_worker(
    *,
    session_factory: AsyncSessionFactory,
    worker_id: str,
    settings: PushNotificationSettings,
    firebase_settings: FirebaseAuthSettings,
) -> PushNotificationDeliveryWorker | None:
    if not settings.enabled:
        return None
    project_id = firebase_settings.firebase_project_id.strip()
    if not project_id:
        message = "FIREBASE_PROJECT_ID is required when push notifications are enabled"
        raise RuntimeError(message)
    return PushNotificationDeliveryWorker(
        session_factory=notification_worker_session_factory(session_factory),
        sender=FirebasePushSender(project_id=project_id),
        settings=settings,
        worker_id=worker_id,
    )
