"""Create a local .env from .env.example and fill in its generated secrets.

Existing values are kept, so running this again never rotates a key that already
protects stored data. Standard library only: it runs before dependencies are
installed.
"""
# ruff: noqa: INP001 - a standalone script, run directly rather than imported from a package.

import base64
import os
import re
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_PREFIX = "replace-with-"


def _encryption_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _signing_secret() -> str:
    return secrets.token_urlsafe(32)


def _service_token() -> str:
    return secrets.token_hex(32)


GENERATED_SETTINGS: dict[str, Callable[[], str]] = {
    "LOCAL_SECRET_ENCRYPTION_KEY": _encryption_key,
    "MCP_OAUTH_STATE_SIGNING_SECRET": _signing_secret,
    "VOICE_ROUTE_SIGNING_SECRET": _signing_secret,
    "BROWSER_AGENT_SERVICE_TOKEN": _service_token,
    "STEEL_SERVICE_TOKEN": _service_token,
}


def fill_generated_settings(content: str) -> tuple[str, list[str]]:
    """Returns the content with unset secrets generated, and the names it filled."""
    filled: list[str] = []
    for name, generate in GENERATED_SETTINGS.items():
        pattern = re.compile(rf"^{name}=(.*)$", re.MULTILINE)
        match = pattern.search(content)
        if match is None:
            separator = "" if content.endswith("\n") or not content else "\n"
            content = f"{content}{separator}{name}={generate()}\n"
            filled.append(name)
            continue
        value = match.group(1).strip()
        if value and not value.startswith(PLACEHOLDER_PREFIX):
            continue
        content = f"{content[: match.start()]}{name}={generate()}{content[match.end() :]}"
        filled.append(name)
    return content, filled


def init_local_env(env_path: Path, example_path: Path) -> list[str]:
    """Writes env_path with owner-only permissions and returns the settings it generated."""
    source = env_path if env_path.exists() else example_path
    content, filled = fill_generated_settings(source.read_text(encoding="utf-8"))
    if filled or source != env_path:
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as env_file:
            env_file.write(content)
    return filled


def main() -> None:
    filled = init_local_env(REPOSITORY_ROOT / ".env", REPOSITORY_ROOT / ".env.example")
    outcome = f"Generated in .env: {', '.join(filled)}" if filled else ".env already has its secrets."
    next_step = "Next: add your Firebase project settings (the FIREBASE_* values), then run make up."
    sys.stdout.write(f"{outcome}\n{next_step}\n")


if __name__ == "__main__":
    main()
