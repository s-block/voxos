import ssl
from pathlib import Path
from unittest.mock import patch

import pytest

from voxos.db.engine import database_connect_args
from voxos.db.settings import DatabaseSettings, DatabaseTlsMode


def test_database_connect_args_preserves_driver_default() -> None:
    settings = DatabaseSettings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://localhost:5432/voxos",
    )

    assert database_connect_args(settings) == {}


def test_database_connect_args_enforces_verified_tls(tmp_path: Path) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("test CA bundle", encoding="utf-8")
    settings = DatabaseSettings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://example.com:5432/voxos",
        DATABASE_TLS_MODE="verify-full",
        DATABASE_TLS_CA_FILE=ca_file,
    )

    ssl_context = ssl.create_default_context()
    with patch(
        "voxos.db.engine.ssl.create_default_context",
        return_value=ssl_context,
    ) as create_default_context:
        connect_args = database_connect_args(settings)

    create_default_context.assert_called_once_with(cafile=str(ca_file))
    assert connect_args["ssl"] is ssl_context
    assert ssl_context.check_hostname is True
    assert ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert settings.database_tls_mode == DatabaseTlsMode.VERIFY_FULL


def test_database_connect_args_rejects_verified_tls_for_sqlite() -> None:
    settings = DatabaseSettings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///voxos.db",
        DATABASE_TLS_MODE="verify-full",
    )

    with pytest.raises(
        ValueError,
        match="DATABASE_TLS_MODE=verify-full requires a PostgreSQL database",
    ):
        database_connect_args(settings)
