from voxos.location.models import ClientLocation


def render_current_location_instructions(location: ClientLocation) -> str:
    return "\n".join(
        (
            "# Current Device Location",
            "The user explicitly shared this request-scoped WGS84 location:",
            f"- Latitude: {location.latitude:.7f}",
            f"- Longitude: {location.longitude:.7f}",
            f"- Horizontal accuracy: {location.horizontal_accuracy_m:.1f} metres",
            f"- Captured at: {location.captured_at.isoformat()}",
            (
                "Use it only when location materially helps answer the current request, such as "
                "nearby places, maps, local weather, travel, or local search."
            ),
            (
                "For tool calls, follow the tool's live input schema. Translate these coordinates "
                "into latitude/longitude, lat/lon, location bias, or another required shape. "
                "GeoJSON coordinate arrays are ordered longitude then latitude."
            ),
            (
                "Do not invent a street address or place name from coordinates. Resolve one with "
                "an available map or geocoding tool only when the request needs it."
            ),
        ),
    )


def append_current_location_instructions(
    instructions: str,
    location: ClientLocation | None,
) -> str:
    if location is None:
        return instructions
    return "\n\n".join(
        (
            instructions,
            render_current_location_instructions(location),
        ),
    )
