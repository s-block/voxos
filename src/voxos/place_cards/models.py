from collections.abc import Iterable
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

PlaceCardProvider = Literal["google_maps"]
MAX_PLACE_CARDS_PER_MESSAGE = 6

_SENSITIVE_GOOGLE_MAPS_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "credential",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
    },
)


class PlaceCardArtifact(BaseModel):
    """A provider-backed place link that can be rendered beside an answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: PlaceCardProvider
    place_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    place_url: str = Field(min_length=1, max_length=2048)
    attribution_url: str = Field(min_length=1, max_length=2048)
    directions_url: str | None = Field(default=None, min_length=1, max_length=2048)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    position: int = Field(ge=0, le=100)

    @field_validator("place_id", "title", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("place_url", "attribution_url", "directions_url")
    @classmethod
    def validate_google_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        hostname = parsed.hostname
        try:
            port = parsed.port
        except ValueError:
            port = None
            hostname = None
        if (
            parsed.scheme != "https"
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and port != 443)
            or parsed.fragment
            or not _is_google_maps_url(hostname, parsed.path)
            or _has_sensitive_google_maps_query(parsed.query)
        ):
            message = "Google Maps card URLs must be credential-free Google HTTPS URLs"
            raise ValueError(message)
        return value


def _is_google_maps_url(hostname: str, path: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    normalized_path = path or "/"
    if normalized == "maps.app.goo.gl":
        return normalized_path != "/"
    if normalized == "maps.google.com":
        return normalized_path == "/" or normalized_path == "/maps" or normalized_path.startswith("/maps/")
    if normalized not in {"google.com", "www.google.com"}:
        return False
    return normalized_path == "/maps" or normalized_path.startswith("/maps/")


def _has_sensitive_google_maps_query(query: str) -> bool:
    return any(
        key.casefold() in _SENSITIVE_GOOGLE_MAPS_QUERY_KEYS for key, _value in parse_qsl(query, keep_blank_values=True)
    )


def deduplicated_place_cards(
    cards: Iterable[PlaceCardArtifact],
) -> tuple[PlaceCardArtifact, ...]:
    deduplicated: list[PlaceCardArtifact] = []
    seen: set[tuple[PlaceCardProvider, str]] = set()
    for card in cards:
        key = (card.provider, card.place_id)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(card)
    return tuple(deduplicated)


def selected_place_cards_for_message(
    batches: Iterable[Iterable[PlaceCardArtifact]],
    *,
    assistant_text: str,
    limit: int = MAX_PLACE_CARDS_PER_MESSAGE,
) -> tuple[PlaceCardArtifact, ...]:
    """Select bounded cards that support the completed assistant message."""

    if limit <= 0:
        return ()
    normalized_batches = tuple(batch for cards in batches if (batch := deduplicated_place_cards(cards)))
    if not normalized_batches:
        return ()
    all_cards = deduplicated_place_cards(card for batch in normalized_batches for card in batch)
    referenced = tuple(card for card in all_cards if _assistant_references_place_card(assistant_text, card))
    candidates = referenced or normalized_batches[-1]
    return deduplicated_place_cards(candidates)[:limit]


def _assistant_references_place_card(
    assistant_text: str,
    card: PlaceCardArtifact,
) -> bool:
    normalized_answer = " ".join(assistant_text.casefold().split())
    normalized_title = " ".join(card.title.casefold().split())
    exact_references = (
        card.place_id,
        card.place_url,
        card.attribution_url,
        card.directions_url,
    )
    return (bool(normalized_title) and normalized_title in normalized_answer) or any(
        reference is not None and reference in assistant_text for reference in exact_references
    )
