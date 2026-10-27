import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from examples.skills.scripts import load_examples
from examples.skills.scripts.example_installer import (
    ExampleSkillInstallerError,
    ExampleSkillInstallReport,
)

DATABASE_ENGINE_CREATED = "database engine must not be created"
SAFE_PREFLIGHT_FAILED = "safe preflight failed"


def test_standalone_cli_import_registers_skill_foreign_key_models() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import examples.skills.scripts.load_examples; "
                "from sqlmodel import SQLModel; "
                "assert 'agent_reflection_jobs' in SQLModel.metadata.tables"
            ),
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_make_loader_preserves_explicit_controls_when_dotenv_conflicts(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    (tmp_path / ".env").write_text(
        "USER_ID=dotenv-user\nMCP=exa\nDRY_RUN=0\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"
    make_executable = shutil.which("make")
    assert make_executable is not None

    completed = subprocess.run(  # noqa: S603 - fixed executable and arguments
        [
            make_executable,
            "--no-print-directory",
            "-f",
            str(repository_root / "Makefile"),
            "example-skills-load",
            "USER_ID=explicit-user",
            "MCP=github",
            "DRY_RUN=1",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        "run",
        "python",
        "-m",
        "examples.skills.scripts.load_examples",
        "--user-id",
        "explicit-user",
        "--mcp",
        "github",
        "--dry-run",
    ]


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_list_does_not_create_a_database_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_called(_settings: object) -> FakeEngine:
        raise AssertionError(DATABASE_ENGINE_CREATED)

    monkeypatch.setattr(load_examples, "create_database_engine", fail_if_called)

    exit_code = await load_examples.async_main(["--list"])

    assert exit_code == 0
    assert '"selector":"openbnb"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_load_requires_user_and_mcp_arguments() -> None:
    with pytest.raises(SystemExit) as missing_both:
        await load_examples.async_main([])
    assert missing_both.value.code == 2

    with pytest.raises(SystemExit) as missing_mcp:
        await load_examples.async_main(["--user-id", "user-a"])
    assert missing_mcp.value.code == 2


@pytest.mark.asyncio
async def test_unknown_selector_returns_error_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_called(_settings: object) -> FakeEngine:
        raise AssertionError(DATABASE_ENGINE_CREATED)

    monkeypatch.setattr(load_examples, "create_database_engine", fail_if_called)

    exit_code = await load_examples.async_main(
        ["--user-id", "user-a", "--mcp", "unknown"],
    )

    assert exit_code == 2
    assert "no curated example skills" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_comma_separated_and_bare_preset_selection_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = FakeEngine()
    captured: dict[str, object] = {}

    class FakeInstaller:
        def __init__(self, *, session_factory: object, catalog: object) -> None:
            captured["session_factory"] = session_factory
            captured["catalog"] = catalog

        async def install(
            self,
            *,
            user_id: str,
            requested_groups: tuple[str, ...],
            dry_run: bool,
        ) -> ExampleSkillInstallReport:
            captured["user_id"] = user_id
            captured["requested_groups"] = requested_groups
            captured["dry_run"] = dry_run
            return ExampleSkillInstallReport(
                requested_groups=requested_groups,
                selected_groups=(
                    "github",
                    "google-workspace:calendar",
                    "google-workspace:gmail",
                ),
                dry_run=dry_run,
                ready=(
                    "github-repository-orientation",
                    "github-pull-request-review",
                    "meeting-preparation-brief",
                    "gmail-inbox-triage-and-drafting",
                ),
            )

    _install_cli_fakes(
        monkeypatch=monkeypatch,
        engine=engine,
        installer=FakeInstaller,
    )

    exit_code = await load_examples.async_main(
        [
            "--user-id",
            "user-a",
            "--mcp",
            "github,google-workspace",
            "--dry-run",
        ],
    )

    assert exit_code == 0
    assert captured["requested_groups"] == ("github", "google-workspace")
    assert captured["dry_run"] is True
    assert engine.disposed
    output = capsys.readouterr().out
    assert '"google-workspace:calendar"' in output
    assert '"google-workspace:gmail"' in output


@pytest.mark.asyncio
async def test_engine_is_disposed_when_installer_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = FakeEngine()

    class FailingInstaller:
        def __init__(self, *, session_factory: object, catalog: object) -> None:
            del session_factory, catalog

        async def install(
            self,
            *,
            user_id: str,
            requested_groups: tuple[str, ...],
            dry_run: bool,
        ) -> ExampleSkillInstallReport:
            del user_id, requested_groups, dry_run
            raise ExampleSkillInstallerError(SAFE_PREFLIGHT_FAILED)

    _install_cli_fakes(
        monkeypatch=monkeypatch,
        engine=engine,
        installer=FailingInstaller,
    )

    exit_code = await load_examples.async_main(
        ["--user-id", "user-a", "--mcp", "openbnb"],
    )

    assert exit_code == 1
    assert engine.disposed
    assert "safe preflight failed" in capsys.readouterr().err


def _install_cli_fakes(
    *,
    monkeypatch: pytest.MonkeyPatch,
    engine: FakeEngine,
    installer: object,
) -> None:
    monkeypatch.setattr(load_examples, "create_database_engine", lambda _settings: engine)
    monkeypatch.setattr(
        load_examples,
        "create_session_factory",
        lambda _engine: object(),
    )
    monkeypatch.setattr(load_examples, "ExampleSkillInstaller", installer)
