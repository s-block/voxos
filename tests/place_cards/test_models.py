import pytest
from pydantic import ValidationError

from voxos.place_cards.models import PlaceCardArtifact


@pytest.mark.parametrize(
    "url",
    (
        "https://accounts.google.com/signin",
        "https://www.google.com/url?q=https://attacker.example",
        "https://www.google.com/maps/place/example?key=secret-value",
        "https://www.google.com/maps/place/example#token=secret-value",
        "https://www.google.com:444/maps/place/example",
        "https://maps.app.goo.gl/",
    ),
)
def test_rejects_non_maps_or_credential_bearing_google_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        PlaceCardArtifact(
            provider="google_maps",
            place_id="ChIJ123",
            title="Example",
            place_url=url,
            attribution_url="https://www.google.com/maps/place/example",
            position=0,
        )


@pytest.mark.parametrize(
    "url",
    (
        "https://www.google.com/maps/place/example?api=1",
        "https://google.com/maps/dir/?api=1&destination=example",
        "https://maps.google.com/?cid=123",
        "https://maps.app.goo.gl/example",
    ),
)
def test_accepts_supported_google_maps_url_shapes(url: str) -> None:
    card = PlaceCardArtifact(
        provider="google_maps",
        place_id="ChIJ123",
        title="Example",
        place_url=url,
        attribution_url=url,
        position=0,
    )

    assert card.place_url == url
