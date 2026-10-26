from datetime import UTC, datetime

from voxos.location.models import ClientLocation
from voxos.location.rendering import render_current_location_instructions


def test_location_instructions_explain_tool_shapes_without_claiming_an_address() -> None:
    instructions = render_current_location_instructions(
        ClientLocation(
            latitude=42.4304,
            longitude=19.2594,
            horizontal_accuracy_m=45,
            captured_at=datetime(2026, 8, 30, 12, 34, 56, tzinfo=UTC),
        ),
    )

    assert "# Current Device Location" in instructions
    assert "Latitude: 42.4304000" in instructions
    assert "Longitude: 19.2594000" in instructions
    assert "location bias" in instructions
    assert "GeoJSON coordinate arrays are ordered longitude then latitude" in instructions
    assert "Do not invent a street address" in instructions
