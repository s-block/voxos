import argparse
import asyncio
import logging
import os
import signal
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import orjson
from pydantic import ValidationError

from voxos.credentials.services import CredentialsServiceFactory
from voxos.db.engine import create_database_engine, create_session_factory
from voxos.db.settings import DatabaseSettings
from voxos.db.tenant import whatsapp_worker_session_factory
from voxos.observability.sentry import initialize_sentry
from voxos.security.encryption.factory import create_secret_encryption_service
from voxos.security.encryption.settings import EncryptionSettings
from voxos.whatsapp.models import WhatsAppWorkerHealth
from voxos.whatsapp.settings import WhatsAppWorkerSettings
from voxos.whatsapp.supervisor import WhatsAppWorkerSupervisor

logger = logging.getLogger(__name__)
HEALTH_WRITE_INTERVAL_MAX_SECONDS = 2.0


async def run_worker_from_settings(
    settings: WhatsAppWorkerSettings | None = None,
) -> None:
    initialize_sentry("whatsapp-worker")
    resolved_settings = settings or WhatsAppWorkerSettings()
    database_engine = create_database_engine(DatabaseSettings())
    base_session_factory = create_session_factory(database_engine)
    secret_vault = CredentialsServiceFactory(
        create_secret_encryption_service(EncryptionSettings()),
    ).user_secret_vault()
    supervisor = WhatsAppWorkerSupervisor(
        base_session_factory=base_session_factory,
        worker_session_factory=whatsapp_worker_session_factory(
            base_session_factory,
        ),
        secret_vault=secret_vault,
        settings=resolved_settings,
    )
    stop_health = asyncio.Event()
    health_task = asyncio.create_task(
        _publish_health(
            supervisor=supervisor,
            settings=resolved_settings,
            stop_event=stop_health,
        ),
        name="whatsapp-worker-health",
    )
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        supervisor.request_stop()
        stop_health.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, request_stop)
    try:
        await supervisor.run()
    finally:
        stop_health.set()
        await health_task
        await asyncio.to_thread(
            _write_health_file,
            Path(resolved_settings.whatsapp_health_path),
            _health_payload(
                supervisor=supervisor,
                bridge_connected=False,
            ),
        )
        await database_engine.dispose()


async def _publish_health(
    *,
    supervisor: WhatsAppWorkerSupervisor,
    settings: WhatsAppWorkerSettings,
    stop_event: asyncio.Event,
) -> None:
    interval = min(
        HEALTH_WRITE_INTERVAL_MAX_SECONDS,
        settings.whatsapp_health_freshness_seconds / 3,
    )
    health_path = Path(settings.whatsapp_health_path)
    while not stop_event.is_set():
        if supervisor.last_successful_poll_at is not None:
            await asyncio.to_thread(
                _write_health_file,
                health_path,
                _health_payload(
                    supervisor=supervisor,
                    bridge_connected=supervisor.bridge_connected,
                ),
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass


def _health_payload(
    *,
    supervisor: WhatsAppWorkerSupervisor,
    bridge_connected: bool,
) -> bytes:
    last_poll = supervisor.last_successful_poll_at
    if last_poll is None:
        last_poll = datetime.fromtimestamp(0, tz=UTC)
    health = WhatsAppWorkerHealth(
        worker_id=supervisor.worker_id,
        bridge_connected=bridge_connected,
        active_sessions=supervisor.active_session_count,
        last_successful_poll_at=last_poll,
        updated_at=datetime.now(tz=UTC),
    )
    return orjson.dumps(health.model_dump(mode="json"))


def _write_health_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary_path.write_bytes(payload)
    temporary_path.chmod(0o600)
    os.replace(temporary_path, path)


def healthcheck(settings: WhatsAppWorkerSettings | None = None) -> bool:
    resolved_settings = settings or WhatsAppWorkerSettings()
    try:
        raw = Path(resolved_settings.whatsapp_health_path).read_bytes()
        health = WhatsAppWorkerHealth.model_validate(orjson.loads(raw))
    except (OSError, orjson.JSONDecodeError, ValidationError, ValueError):
        return False
    now = datetime.now(tz=UTC)
    updated_age = (now - _as_utc(health.updated_at)).total_seconds()
    poll_age = (now - _as_utc(health.last_successful_poll_at)).total_seconds()
    freshness = resolved_settings.whatsapp_health_freshness_seconds
    return health.bridge_connected and -freshness <= updated_age <= freshness and -freshness <= poll_age <= freshness


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WhatsApp worker.")
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Exit successfully when the worker health file is fresh.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.healthcheck:
        return 0 if healthcheck() else 1
    initialize_sentry("whatsapp-worker")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker_from_settings())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
