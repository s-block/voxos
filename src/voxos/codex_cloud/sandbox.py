import asyncio
import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import orjson
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator

from voxos.codex_cloud.secrets import MAX_CODEX_AUTH_JSON_BYTES, OpaqueCodexAuth
from voxos.codex_cloud.settings import CodexCloudSettings

CODEX_CONFIG = """\
cli_auth_credentials_store = "file"
forced_login_method = "chatgpt"

[analytics]
enabled = false
"""
# Docker requires an absolute in-container mount target. This specification
# creates an isolated tmpfs and does not create or reuse a host temporary file.
CONTAINER_TMPFS_SPEC = "/tmp:rw,noexec,nosuid,nodev,size=64m"  # noqa: S108  # nosec B108
LOGIN_FAILED_CODE = "codex_login_failed"
LOGIN_CANCELLED_CODE = "codex_login_cancelled"
OUTPUT_LIMIT_CODE = "codex_output_limit_exceeded"
CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")


class CodexSandboxError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        ambiguous: bool = False,
        refreshed_auth_json: SecretStr | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.refreshed_auth_json = refreshed_auth_json


class CodexSandboxTimeoutError(CodexSandboxError):
    def __init__(self, *, ambiguous: bool, refreshed_auth_json: SecretStr | None = None) -> None:
        super().__init__(
            "codex_sandbox_timeout",
            retryable=True,
            ambiguous=ambiguous,
            refreshed_auth_json=refreshed_auth_json,
        )


class CodexSandboxProtocolError(CodexSandboxError):
    def __init__(self) -> None:
        super().__init__("invalid_codex_app_server_protocol")


class CodexSandboxCommandError(CodexSandboxError):
    def __init__(
        self,
        return_code: int,
        stderr: str,
        *,
        ambiguous: bool,
        refreshed_auth_json: SecretStr | None,
    ) -> None:
        super().__init__(
            "codex_command_failed",
            retryable=True,
            ambiguous=ambiguous,
            refreshed_auth_json=refreshed_auth_json,
        )
        self.return_code = return_code
        self.stderr = stderr


class CodexSandboxResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stdout: str
    stderr: str
    refreshed_auth_json: SecretStr | None = Field(default=None, repr=False)


class CodexDeviceChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    login_id: str = Field(min_length=1, max_length=128)
    verification_url: HttpUrl
    user_code: str = Field(min_length=1, max_length=64)

    @field_validator("verification_url")
    @classmethod
    def validate_verification_url(cls, value: HttpUrl) -> HttpUrl:
        if value.host not in {"auth.openai.com", "chatgpt.com"}:
            message = "Codex returned an unexpected device-login URL host"
            raise ValueError(message)
        return value


class CodexLoginResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auth_json: SecretStr = Field(repr=False)
    email: str | None = Field(default=None, max_length=320)
    plan_type: str | None = Field(default=None, max_length=64)


ChallengeHandler = Callable[[CodexDeviceChallenge], Awaitable[None]]
CancellationCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class DockerCodexSandboxRunner:
    settings: CodexCloudSettings

    async def run(
        self,
        *,
        arguments: Sequence[str],
        auth: OpaqueCodexAuth,
        timeout_seconds: float | None = None,
        ambiguous_on_failure: bool = False,
    ) -> CodexSandboxResult:
        with tempfile.TemporaryDirectory(prefix="voxos-codex-") as temporary_path:
            codex_home = Path(temporary_path)
            await self._prepare_codex_home(codex_home, auth=auth)
            process = await self._start_process(codex_home=codex_home, arguments=arguments)
            try:
                stdout, stderr = await asyncio.wait_for(
                    self._communicate_bounded(process),
                    timeout=timeout_seconds or self.settings.command_timeout_seconds,
                )
            except TimeoutError as exc:
                await _terminate_process(process)
                await self._remove_container(codex_home)
                refreshed_auth = await self._load_auth(codex_home)
                raise CodexSandboxTimeoutError(
                    ambiguous=ambiguous_on_failure,
                    refreshed_auth_json=(refreshed_auth.value if refreshed_auth is not None else None),
                ) from exc
            except BaseException:
                await _terminate_process(process)
                await self._remove_container(codex_home)
                raise

            refreshed_auth = await self._load_auth(codex_home)
            if process.returncode != 0:
                raise CodexSandboxCommandError(
                    process.returncode or -1,
                    stderr,
                    ambiguous=ambiguous_on_failure,
                    refreshed_auth_json=(refreshed_auth.value if refreshed_auth is not None else None),
                )
            return CodexSandboxResult(
                stdout=stdout,
                stderr=stderr,
                refreshed_auth_json=(refreshed_auth.value if refreshed_auth is not None else None),
            )

    async def login_with_device_code(
        self,
        *,
        on_challenge: ChallengeHandler,
        is_cancelled: CancellationCheck,
    ) -> CodexLoginResult:
        with tempfile.TemporaryDirectory(prefix="voxos-codex-login-") as temporary_path:
            codex_home = Path(temporary_path)
            await self._prepare_codex_home(codex_home, auth=None)
            process = await self._start_process(codex_home=codex_home, arguments=("app-server",))
            stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
            try:
                async with asyncio.timeout(self.settings.login_timeout_seconds):
                    result = await self._drive_device_login(
                        process=process,
                        on_challenge=on_challenge,
                        is_cancelled=is_cancelled,
                    )
            except TimeoutError as exc:
                raise CodexSandboxTimeoutError(ambiguous=False) from exc
            finally:
                await _terminate_process(process)
                await self._remove_container(codex_home)
                await stderr_task

            auth = await self._load_auth(codex_home)
            if auth is None:
                raise CodexSandboxProtocolError
            email_value = result.get("email")
            email = email_value if isinstance(email_value, str) else None
            plan_type_value = result.get("planType")
            plan_type = plan_type_value if isinstance(plan_type_value, str) else None
            return CodexLoginResult(
                auth_json=auth.value,
                email=email,
                plan_type=plan_type,
            )

    async def _drive_device_login(
        self,
        *,
        process: asyncio.subprocess.Process,
        on_challenge: ChallengeHandler,
        is_cancelled: CancellationCheck,
    ) -> dict[str, object]:
        await _write_json_line(
            process,
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "voxos",
                        "title": "VoxOS",
                        "version": "0.1.0",
                    },
                },
            },
        )
        await self._read_response(process, response_id=1, is_cancelled=is_cancelled)
        await _write_json_line(process, {"method": "initialized", "params": {}})
        await _write_json_line(
            process,
            {
                "method": "account/login/start",
                "id": 2,
                "params": {"type": "chatgptDeviceCode"},
            },
        )
        response = await self._read_response(process, response_id=2, is_cancelled=is_cancelled)
        challenge = CodexDeviceChallenge.model_validate(response)
        await on_challenge(challenge)

        while True:
            frame = await self._read_frame(process, is_cancelled=is_cancelled, login_id=challenge.login_id)
            if frame.get("method") != "account/login/completed":
                continue
            params = frame.get("params")
            if not isinstance(params, dict) or params.get("loginId") != challenge.login_id:
                continue
            if params.get("success") is not True:
                raise CodexSandboxError(LOGIN_FAILED_CODE)
            break

        await _write_json_line(
            process,
            {"method": "account/read", "id": 3, "params": {"refreshToken": True}},
        )
        account_result = await self._read_response(process, response_id=3, is_cancelled=is_cancelled)
        account = account_result.get("account")
        if not isinstance(account, dict) or account.get("type") != "chatgpt":
            raise CodexSandboxProtocolError
        return account

    async def _read_response(
        self,
        process: asyncio.subprocess.Process,
        *,
        response_id: int,
        is_cancelled: CancellationCheck,
    ) -> dict[str, object]:
        while True:
            frame = await self._read_frame(process, is_cancelled=is_cancelled)
            if frame.get("id") != response_id:
                continue
            if "error" in frame:
                raise CodexSandboxProtocolError
            result = frame.get("result")
            if not isinstance(result, dict):
                raise CodexSandboxProtocolError
            return result

    async def _read_frame(
        self,
        process: asyncio.subprocess.Process,
        *,
        is_cancelled: CancellationCheck,
        login_id: str | None = None,
    ) -> dict[str, object]:
        if process.stdout is None:
            raise CodexSandboxProtocolError
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=1)
            except TimeoutError:
                if await is_cancelled():
                    if login_id is not None:
                        await _write_json_line(
                            process,
                            {
                                "method": "account/login/cancel",
                                "id": 4,
                                "params": {"loginId": login_id},
                            },
                        )
                    raise CodexSandboxError(LOGIN_CANCELLED_CODE)
                continue
            if not line or len(line) > self.settings.max_output_bytes:
                raise CodexSandboxProtocolError
            try:
                frame = orjson.loads(line)
            except orjson.JSONDecodeError as exc:
                raise CodexSandboxProtocolError from exc
            if not isinstance(frame, dict):
                raise CodexSandboxProtocolError
            return frame

    async def _prepare_codex_home(self, codex_home: Path, *, auth: OpaqueCodexAuth | None) -> None:
        await asyncio.to_thread((codex_home / "config.toml").write_text, CODEX_CONFIG, "utf-8")
        if auth is not None:
            await asyncio.to_thread(
                (codex_home / "auth.json").write_text,
                auth.value.get_secret_value(),
                "utf-8",
            )
            await asyncio.to_thread((codex_home / "auth.json").chmod, 0o600)

    async def _load_auth(self, codex_home: Path) -> OpaqueCodexAuth | None:
        auth_path = codex_home / "auth.json"
        if not await asyncio.to_thread(auth_path.is_file):
            return None
        size = await asyncio.to_thread(lambda: auth_path.stat().st_size)
        if size > MAX_CODEX_AUTH_JSON_BYTES:
            raise CodexSandboxProtocolError
        value = await asyncio.to_thread(auth_path.read_text, "utf-8")
        return OpaqueCodexAuth(SecretStr(value))

    async def _start_process(
        self,
        *,
        codex_home: Path,
        arguments: Sequence[str],
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *self.docker_command(codex_home=codex_home, arguments=arguments),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_docker_child_environment(),
        )

    def docker_command(self, *, codex_home: Path, arguments: Sequence[str]) -> tuple[str, ...]:
        return (
            self.settings.docker_executable,
            "run",
            "--rm",
            "--init",
            "--cidfile",
            str(codex_home / "container.cid"),
            "--read-only",
            "--network",
            "bridge",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            self.settings.memory_limit,
            "--cpus",
            str(self.settings.cpu_limit),
            "--pids-limit",
            str(self.settings.pids_limit),
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            CONTAINER_TMPFS_SPEC,
            "--mount",
            f"type=bind,source={codex_home.resolve()},target=/codex-home,rw",
            "--env",
            "HOME=/codex-home",
            "--env",
            "CODEX_HOME=/codex-home",
            self.settings.runner_image,
            *arguments,
        )

    async def _remove_container(self, codex_home: Path) -> None:
        cid_path = codex_home / "container.cid"
        if not await asyncio.to_thread(cid_path.is_file):
            return
        container_id = (await asyncio.to_thread(cid_path.read_text, "utf-8")).strip()
        if CONTAINER_ID_PATTERN.fullmatch(container_id) is None:
            return
        process = await asyncio.create_subprocess_exec(
            self.settings.docker_executable,
            "rm",
            "-f",
            container_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=_docker_child_environment(),
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def _communicate_bounded(self, process: asyncio.subprocess.Process) -> tuple[str, str]:
        stdout_task = asyncio.create_task(self._read_bounded(process.stdout))
        stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
        wait_task = asyncio.create_task(process.wait())
        try:
            _, stdout, stderr = await asyncio.gather(wait_task, stdout_task, stderr_task)
            return stdout, stderr
        except BaseException:
            await _terminate_process(process)
            for task in (wait_task, stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(wait_task, stdout_task, stderr_task, return_exceptions=True)
            raise

    async def _read_bounded(self, stream: asyncio.StreamReader | None) -> str:
        if stream is None:
            return ""
        output = bytearray()
        while chunk := await stream.read(65536):
            if len(output) + len(chunk) > self.settings.max_output_bytes:
                raise CodexSandboxError(OUTPUT_LIMIT_CODE)
            output.extend(chunk)
        return output.decode(errors="replace")


async def _write_json_line(process: asyncio.subprocess.Process, payload: Mapping[str, object]) -> None:
    if process.stdin is None:
        raise CodexSandboxProtocolError
    process.stdin.write(orjson.dumps(payload) + b"\n")
    await process.stdin.drain()


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


def _docker_child_environment() -> dict[str, str]:
    allowed_names = ("DOCKER_CONTEXT", "DOCKER_HOST", "HOME", "PATH", "XDG_CONFIG_HOME")
    return {name: value for name in allowed_names if (value := os.environ.get(name)) is not None}
