from pathlib import Path

import orjson
import pytest

from voxos.codex_cloud.execution import (
    _submission_arguments,
    normalize_remote_status,
    parse_cloud_list,
    parse_submission_output,
    remote_task_summary,
)
from voxos.codex_cloud.models import CodexCloudRemoteStatus, CodexCloudTaskRunPayload
from voxos.codex_cloud.sandbox import DockerCodexSandboxRunner
from voxos.codex_cloud.settings import CodexCloudSettings


def test_docker_command_is_disposable_hardened_and_has_no_workspace_mount(
    tmp_path: Path,
) -> None:
    settings = CodexCloudSettings(
        CODEX_CLOUD_RUNNER_IMAGE="voxos-codex-runner:test",
        CODEX_CLOUD_DOCKER_EXECUTABLE="docker",
    )
    command = DockerCodexSandboxRunner(settings).docker_command(
        codex_home=tmp_path,
        arguments=("cloud", "list", "--json"),
    )

    assert command[0:3] == ("docker", "run", "--rm")
    assert "--read-only" in command
    assert command[command.index("--cap-drop") : command.index("--cap-drop") + 2] == ("--cap-drop", "ALL")
    assert "no-new-privileges" in command
    assert f"type=bind,source={tmp_path.resolve()},target=/codex-home,rw" in command
    assert str(Path.cwd()) not in command
    assert command[-3:] == ("cloud", "list", "--json")


def test_submission_output_requires_a_canonical_task_url() -> None:
    task_id = "task_e_123456789abc"

    parsed_id, parsed_url = parse_submission_output(
        f"Task created: https://chatgpt.com/codex/tasks/{task_id}\n",
    )

    assert parsed_id == task_id
    assert parsed_url == f"https://chatgpt.com/codex/tasks/{task_id}"
    with pytest.raises(ValueError, match="task URL"):
        parse_submission_output("Task may have been created")


def test_cloud_list_parser_accepts_current_object_and_legacy_array_shapes() -> None:
    task = {
        "id": "task-1",
        "status": "IN_PROGRESS",
        "url": "https://chatgpt.com/codex/tasks/task-1",
        "environment_id": "env-1",
        "summary": {
            "files_changed": 3,
            "lines_added": 21,
            "lines_removed": 8,
        },
    }

    current = parse_cloud_list(orjson.dumps({"tasks": [task], "cursor": "next-page"}).decode())[0]
    assert current.id == "task-1"
    assert remote_task_summary(current) == "3 files changed (+21/-8 lines)"
    assert parse_cloud_list(orjson.dumps([task]).decode())[0].environment_id == "env-1"


def test_remote_status_normalization_is_conservative() -> None:
    assert normalize_remote_status("IN_PROGRESS") == CodexCloudRemoteStatus.RUNNING
    assert normalize_remote_status("READY") == CodexCloudRemoteStatus.READY
    assert normalize_remote_status("applied") == CodexCloudRemoteStatus.READY
    assert normalize_remote_status("new-provider-state") == CodexCloudRemoteStatus.UNKNOWN


def test_submission_prompt_is_separated_from_cli_options() -> None:
    arguments = _submission_arguments(
        CodexCloudTaskRunPayload(
            account_id="account-1",
            environment_id="owner/repository",
            prompt="--attempts 4",
        ),
    )

    assert arguments[-2:] == ("--", "--attempts 4")
