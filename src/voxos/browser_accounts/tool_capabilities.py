import unicodedata
from collections.abc import Sequence
from hashlib import sha256
from typing import Protocol, TypeVar

BROWSER_PROFILE_HANDLE_PREFIX = "profile_"


class BrowserProfileDescription(Protocol):
    @property
    def account_id(self) -> str: ...

    @property
    def label(self) -> str: ...

    @property
    def primary_origin(self) -> str: ...


BrowserProfileT = TypeVar("BrowserProfileT", bound=BrowserProfileDescription)


def browser_profile_handle(account_id: str) -> str:
    opaque_handle = sha256(account_id.encode("utf-8")).hexdigest()[:24]
    return f"{BROWSER_PROFILE_HANDLE_PREFIX}{opaque_handle}"


def browser_profile_catalog(accounts: Sequence[BrowserProfileT]) -> str:
    return "\n".join(
        (
            f"- {browser_profile_handle(account.account_id)}: "
            f"{_description_identifier(account.label)} "
            f"({_description_identifier(account.primary_origin)})"
        )
        for account in accounts
    )


def _description_identifier(value: str) -> str:
    normalized = " ".join(value.split())
    visible = "".join(character for character in normalized if not unicodedata.category(character).startswith("C"))
    return visible.replace("'", "’")[:200]
