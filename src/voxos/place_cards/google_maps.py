from collections.abc import Mapping

import orjson
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from voxos.agent_runtime.models import JsonObject
from voxos.place_cards.models import PlaceCardArtifact


class _GoogleMapsLinks(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    place_url: str = Field(alias="placeUrl", min_length=1)
    directions_url: str | None = Field(
        default=None,
        alias="directionsUrl",
        min_length=1,
    )


class _GoogleMapsAttribution(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)


class _GoogleMapsLocation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class _GoogleMapsPlace(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1, max_length=256)
    google_maps_links: _GoogleMapsLinks = Field(alias="googleMapsLinks")
    attribution: _GoogleMapsAttribution
    location: _GoogleMapsLocation | None = None


class _GoogleMapsSearchEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    places: tuple[object, ...] = ()


def google_maps_search_place_cards(
    *,
    structured_content: Mapping[str, object] | None,
    content_text: str,
) -> tuple[PlaceCardArtifact, ...]:
    candidate = _structured_candidate(structured_content, content_text)
    if candidate is None:
        return ()
    try:
        response = _GoogleMapsSearchEnvelope.model_validate(candidate)
    except ValidationError:
        return ()

    cards: list[PlaceCardArtifact] = []
    seen_place_ids: set[str] = set()
    for position, raw_place in enumerate(response.places):
        try:
            place = _GoogleMapsPlace.model_validate(raw_place)
        except ValidationError:
            continue
        if place.id in seen_place_ids:
            continue
        location = place.location
        try:
            card = PlaceCardArtifact(
                provider="google_maps",
                place_id=place.id,
                title=place.attribution.title,
                place_url=place.google_maps_links.place_url,
                attribution_url=place.attribution.url,
                directions_url=place.google_maps_links.directions_url,
                latitude=None if location is None else location.latitude,
                longitude=None if location is None else location.longitude,
                position=position,
            )
        except ValidationError:
            continue
        cards.append(card)
        seen_place_ids.add(place.id)
    return tuple(cards)


def append_google_maps_sources_for_model(
    output_text: str,
    cards: tuple[PlaceCardArtifact, ...],
) -> str:
    if not cards or all(card.place_url in output_text for card in cards):
        return output_text
    source_payload: JsonObject = {
        "source": "Google Maps",
        "places": [
            {
                "placeId": card.place_id,
                "title": card.title,
                "placeUrl": card.place_url,
                "attributionUrl": card.attribution_url,
                **({} if card.directions_url is None else {"directionsUrl": card.directions_url}),
            }
            for card in cards
        ],
    }
    rendered_sources = orjson.dumps(source_payload).decode()
    stripped_output = output_text.rstrip()
    if not stripped_output:
        return rendered_sources
    return f"{stripped_output}\n\nGoogle Maps source links (use these exact URLs):\n{rendered_sources}"


def _structured_candidate(
    structured_content: Mapping[str, object] | None,
    content_text: str,
) -> Mapping[str, object] | None:
    if structured_content is not None:
        return structured_content
    try:
        parsed = orjson.loads(content_text)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {str(key): value for key, value in parsed.items()}
