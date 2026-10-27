from voxos.place_cards.google_maps import (
    append_google_maps_sources_for_model,
    google_maps_search_place_cards,
)
from voxos.place_cards.models import selected_place_cards_for_message

GOOGLE_MAPS_RESULT: dict[str, object] = {
    "summary": "Two nearby places.",
    "places": [
        {
            "place": "places/ChIJ123",
            "id": "ChIJ123",
            "googleMapsLinks": {
                "placeUrl": ("https://www.google.com/maps/place/?q=place_id:ChIJ123"),
                "directionsUrl": ("https://www.google.com/maps/dir/?api=1&destination=ChIJ123"),
            },
            "attribution": {
                "title": "Konoba Stari Grad",
                "url": "https://www.google.com/maps/place/?q=place_id:ChIJ123",
            },
            "location": {"latitude": 42.4304, "longitude": 19.2594},
        },
        {
            "place": "places/ChIJ456",
            "id": "ChIJ456",
            "googleMapsLinks": {
                "placeUrl": "https://maps.app.goo.gl/example",
            },
            "attribution": {
                "title": "Restoran Ribnica",
                "url": "https://maps.app.goo.gl/example",
            },
        },
    ],
}


def test_extracts_google_maps_cards_from_structured_tool_result() -> None:
    cards = google_maps_search_place_cards(
        structured_content=GOOGLE_MAPS_RESULT,
        content_text="Two nearby places.",
    )

    assert [card.place_id for card in cards] == ["ChIJ123", "ChIJ456"]
    assert cards[0].title == "Konoba Stari Grad"
    assert cards[0].place_url == ("https://www.google.com/maps/place/?q=place_id:ChIJ123")
    assert cards[0].directions_url == ("https://www.google.com/maps/dir/?api=1&destination=ChIJ123")
    assert cards[0].latitude == 42.4304
    assert cards[0].longitude == 19.2594
    assert cards[1].directions_url is None


def test_rejects_untrusted_place_urls_without_rejecting_valid_siblings() -> None:
    result = dict(GOOGLE_MAPS_RESULT)
    result["places"] = [
        {
            "id": "unsafe",
            "googleMapsLinks": {
                "placeUrl": "https://google.com.attacker.example/place",
            },
            "attribution": {
                "title": "Unsafe",
                "url": "https://www.google.com/maps/place/unsafe",
            },
        },
        GOOGLE_MAPS_RESULT["places"][0],  # type: ignore[index]
    ]

    cards = google_maps_search_place_cards(
        structured_content=result,
        content_text="",
    )

    assert [card.place_id for card in cards] == ["ChIJ123"]


def test_skips_structurally_malformed_places_without_rejecting_valid_siblings() -> None:
    result = dict(GOOGLE_MAPS_RESULT)
    result["places"] = [
        {
            "id": "missing-links",
            "attribution": {
                "title": "Incomplete",
                "url": "https://www.google.com/maps/place/incomplete",
            },
        },
        GOOGLE_MAPS_RESULT["places"][0],  # type: ignore[index]
    ]

    cards = google_maps_search_place_cards(
        structured_content=result,
        content_text="",
    )

    assert [card.place_id for card in cards] == ["ChIJ123"]


def test_appends_exact_provider_links_to_model_visible_tool_output_once() -> None:
    cards = google_maps_search_place_cards(
        structured_content=GOOGLE_MAPS_RESULT,
        content_text="",
    )

    first = append_google_maps_sources_for_model("Two nearby places.", cards)
    second = append_google_maps_sources_for_model(first, cards)

    assert cards[0].place_url in first
    assert cards[1].place_url in first
    assert first.count("Google Maps source links") == 1
    assert second == first


def test_selects_referenced_cards_across_searches_and_enforces_limit() -> None:
    first_batch = google_maps_search_place_cards(
        structured_content=GOOGLE_MAPS_RESULT,
        content_text="",
    )
    later_result = dict(GOOGLE_MAPS_RESULT)
    later_result["places"] = [
        {
            "id": f"later-{index}",
            "googleMapsLinks": {
                "placeUrl": f"https://www.google.com/maps/place/later-{index}",
            },
            "attribution": {
                "title": f"Later Place {index}",
                "url": f"https://www.google.com/maps/place/later-{index}",
            },
        }
        for index in range(8)
    ]
    later_batch = google_maps_search_place_cards(
        structured_content=later_result,
        content_text="",
    )

    referenced = selected_place_cards_for_message(
        (first_batch, later_batch),
        assistant_text="Try Konoba Stari Grad and Later Place 7.",
    )
    fallback = selected_place_cards_for_message(
        (first_batch, later_batch),
        assistant_text="Here are the strongest nearby options.",
    )

    assert [card.place_id for card in referenced] == ["ChIJ123", "later-7"]
    assert [card.place_id for card in fallback] == [
        "later-0",
        "later-1",
        "later-2",
        "later-3",
        "later-4",
        "later-5",
    ]
