import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson

from voxos.whatsapp.models import WhatsAppWorkerHealth
from voxos.whatsapp.settings import WhatsAppWorkerSettings
from voxos.whatsapp.worker import _write_health_file, healthcheck


def test_standalone_worker_import_registers_foreign_key_models() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import voxos.whatsapp.worker; from sqlmodel import SQLModel; SQLModel.metadata.sorted_tables"),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_healthcheck_accepts_fresh_connected_worker(tmp_path: Path) -> None:
    health_path = _health_path(tmp_path)
    settings = _settings(health_path)
    now = datetime.now(tz=UTC)
    _write_health_file(
        health_path,
        orjson.dumps(
            WhatsAppWorkerHealth(
                worker_id="worker-one",
                bridge_connected=True,
                active_sessions=2,
                last_successful_poll_at=now,
                updated_at=now,
            ).model_dump(mode="json"),
        ),
    )

    assert healthcheck(settings)


def test_healthcheck_rejects_stale_poll_or_disconnected_bridge(
    tmp_path: Path,
) -> None:
    health_path = _health_path(tmp_path)
    settings = _settings(health_path)
    now = datetime.now(tz=UTC)
    _write_health_file(
        health_path,
        orjson.dumps(
            WhatsAppWorkerHealth(
                worker_id="worker-one",
                bridge_connected=True,
                active_sessions=0,
                last_successful_poll_at=now - timedelta(seconds=30),
                updated_at=now,
            ).model_dump(mode="json"),
        ),
    )
    assert not healthcheck(settings)

    _write_health_file(
        health_path,
        orjson.dumps(
            WhatsAppWorkerHealth(
                worker_id="worker-one",
                bridge_connected=False,
                active_sessions=0,
                last_successful_poll_at=now,
                updated_at=now,
            ).model_dump(mode="json"),
        ),
    )
    assert not healthcheck(settings)


def test_healthcheck_rejects_missing_or_invalid_file(tmp_path: Path) -> None:
    health_path = _health_path(tmp_path)
    settings = _settings(health_path)

    assert not healthcheck(settings)
    health_path.write_bytes(b"not-json")
    assert not healthcheck(settings)


def _health_path(tmp_path: Path) -> Path:
    return tmp_path / "whatsapp-health.json"


def _settings(health_path: Path) -> WhatsAppWorkerSettings:
    return WhatsAppWorkerSettings(
        whatsapp_health_path=str(health_path),
        whatsapp_health_freshness_seconds=10,
    )
