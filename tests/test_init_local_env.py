import base64
import importlib.util
import stat
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "init_local_env.py"
KEY_PLACEHOLDER = "LOCAL_SECRET_ENCRYPTION_KEY=replace-with-base64-32-byte-key"  # pragma: allowlist secret
EXAMPLE_CONTENT = "DB_USER=voxos\n"


@pytest.fixture
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("init_local_env", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(content: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in content.splitlines() if "=" in line)


def test_fills_placeholder_empty_and_missing_settings(script: ModuleType) -> None:
    content, filled = script.fill_generated_settings(
        f"{KEY_PLACEHOLDER}\nBROWSER_AGENT_SERVICE_TOKEN=\nDB_USER=voxos\n"
    )

    settings = _settings(content)
    assert set(filled) == set(script.GENERATED_SETTINGS)
    assert len(base64.b64decode(settings["LOCAL_SECRET_ENCRYPTION_KEY"])) == 32
    assert len(settings["BROWSER_AGENT_SERVICE_TOKEN"]) == 64
    assert settings["MCP_OAUTH_STATE_SIGNING_SECRET"] != settings["VOICE_ROUTE_SIGNING_SECRET"]
    assert settings["DB_USER"] == "voxos"


def test_keeps_values_that_are_already_set(script: ModuleType) -> None:
    original = "".join(f"{name}=existing-{name}\n" for name in script.GENERATED_SETTINGS)

    content, filled = script.fill_generated_settings(original)

    assert content == original
    assert filled == []


@pytest.mark.parametrize("assignment", ["export {name}=existing", "{name} = existing", "  export  {name}= 'existing'"])
def test_keeps_values_in_any_assignment_form_dotenv_loads(script: ModuleType, assignment: str) -> None:
    original = "".join(f"{assignment.format(name=name)}\n" for name in script.GENERATED_SETTINGS)

    content, filled = script.fill_generated_settings(original)

    assert content == original
    assert filled == []


@pytest.mark.parametrize("value", ["", '""', "''"])
def test_fills_empty_quoted_values_and_keeps_the_export_prefix(script: ModuleType, value: str) -> None:
    name = next(iter(script.GENERATED_SETTINGS))

    content, filled = script.fill_generated_settings(f"export {name}={value}\n")

    assert name in filled
    assert content.count(f"{name}=") == 1
    assert content.startswith(f"export {name}=")
    assert len(content.splitlines()[0]) > len(f"export {name}={value}")


def test_ignores_a_commented_out_setting(script: ModuleType) -> None:
    name = next(iter(script.GENERATED_SETTINGS))

    content, filled = script.fill_generated_settings(f"# {name}=old\n")

    assert name in filled
    assert content.startswith(f"# {name}=old\n{name}=")


def test_restricts_an_existing_world_readable_env_when_writing_secrets(script: ModuleType, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(EXAMPLE_CONTENT, encoding="utf-8")
    env_path.chmod(0o644)

    assert script.init_local_env(env_path, tmp_path / ".env.example")

    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert "DB_USER=voxos" in env_path.read_text(encoding="utf-8")


def test_creates_owner_only_env_and_a_second_run_changes_nothing(script: ModuleType, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    example_path.write_text(EXAMPLE_CONTENT, encoding="utf-8")

    assert script.init_local_env(env_path, example_path)
    first_run = env_path.read_text(encoding="utf-8")

    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert script.init_local_env(env_path, example_path) == []
    assert env_path.read_text(encoding="utf-8") == first_run
    assert example_path.read_text(encoding="utf-8") == EXAMPLE_CONTENT
