from copy import deepcopy
from datetime import date
from typing import cast

from voxos.agent_runtime.models import JsonObject
from voxos.logos.domains import logo_for_mcp_preset
from voxos.logos.models import ActivityIcon
from voxos.mcp.models import (
    McpAuthType,
    McpPresetAuthGuide,
    McpPresetCategory,
    McpPresetCompatibility,
    McpPresetToolGuide,
    McpPresetToolRisk,
    McpServicePreset,
    McpServicePresetVariant,
    McpTokenEndpointAuthMethod,
    McpTransportType,
)
from voxos.security.outbound_endpoints import normalize_endpoint_url

MCP_PRESET_CATALOG_VERSION = "2026-08-02.1"
MCP_PRESET_CATALOG_RESEARCHED_AT = date(2026, 7, 24)
EXA_PRESET_ID = "exa"
EXA_KEYLESS_VARIANT_ID = "keyless"
EXA_MCP_URL = "https://mcp.exa.ai/mcp"
GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID = "google-maps-grounding-lite"
GOOGLE_MAPS_SEARCH_PLACES_TOOL = "search_places"
GOOGLE_MAPS_LOOKUP_WEATHER_TOOL = "lookup_weather"
OPENBNB_PRESET_ID = "openbnb"
OPENBNB_SEARCH_TOOL = "airbnb_search"
GOOGLE_MAPS_LOOKUP_WEATHER_GUIDANCE = (
    "Call this tool directly with exactly one camelCase location field: "
    "location.address, location.placeId, or location.latLng. Prefer a specific "
    "location.address such as 'London, UK'; do not call resolve_names first. "
    "If using a resolved Google resource name, remove its 'places/' prefix before "
    "passing location.placeId. Dates must use {year, month, day} integer fields."
)
GOOGLE_MAPS_SEARCH_PLACES_GUIDANCE = (
    "Call this tool with camelCase fields: required textQuery; optional regionCode, "
    "languageCode, and locationBias. Do not use snake_case fields, included_type, "
    "limit, or max_results; this MCP server does not accept them. When using the "
    "result, preserve the exact returned Google Maps place and attribution URLs and "
    "keep each source with the recommendation it supports; never invent a Maps URL."
)
OPENBNB_SEARCH_DATE_GUIDANCE = (
    "Date filters are mutually exclusive. For fixed dates, send only checkin and "
    "checkout. For flexible dates, send flexibleTripLengths and optionally "
    "flexibleTripDates. For monthly stays, send only monthlyStartDate and "
    "monthlyLength. For a no-date search, omit all six date fields."
)
OPENBNB_DATE_FIELDS = (
    "checkin",
    "checkout",
    "flexibleTripLengths",
    "flexibleTripDates",
    "monthlyStartDate",
    "monthlyLength",
)
_MCP_PRESET_TOOL_AUTHORITY_ARGUMENT_ALLOWED_PATHS = {
    ("browserless", "browserless_agent"): frozenset(
        {
            ("commands", "*", "params", "url"),
        },
    ),
    ("browserless", "browserless_crawl"): frozenset({("url",)}),
    ("browserless", "browserless_download"): frozenset({("url",)}),
    ("browserless", "browserless_export"): frozenset({("url",)}),
    ("browserless", "browserless_map"): frozenset({("url",)}),
    ("browserless", "browserless_performance"): frozenset({("url",)}),
    ("browserless", "browserless_smartscraper"): frozenset({("url",)}),
}


def list_mcp_service_presets(
    *,
    trusted_endpoint_urls: frozenset[str] = frozenset(),
) -> tuple[McpServicePreset, ...]:
    normalized_trusted_urls = {normalize_endpoint_url(url) for url in trusted_endpoint_urls}
    presets: list[McpServicePreset] = []
    for preset in MCP_SERVICE_PRESETS:
        variants = tuple(
            variant
            for variant in preset.variants
            if variant.trusted_private_host is None
            or (variant.url is not None and normalize_endpoint_url(variant.url) in normalized_trusted_urls)
        )
        if variants:
            presets.append(preset.model_copy(update={"variants": variants}))
    return tuple(presets)


def find_mcp_service_preset(preset_id: str) -> McpServicePreset | None:
    return MCP_SERVICE_PRESETS_BY_ID.get(preset_id)


def mcp_preset_tool_is_read_only(
    preset_id: str | None,
    tool_name: str,
) -> bool:
    if preset_id is None:
        return False
    preset = find_mcp_service_preset(preset_id)
    if preset is None:
        return False
    return any(guidance.name == tool_name and guidance.read_only for guidance in preset.tool_guidance)


def mcp_preset_tool_activity(
    preset_id: str | None,
    tool_name: str,
) -> tuple[str | None, ActivityIcon | None]:
    if preset_id is None:
        return None, None
    preset = find_mcp_service_preset(preset_id)
    if preset is None:
        return None, None
    guide = next(
        (guidance for guidance in preset.tool_guidance if guidance.name == tool_name),
        None,
    )
    if guide is None:
        return None, None
    return guide.activity_label, guide.activity_icon


def mcp_preset_tool_risk(
    preset_id: str | None,
    tool_name: str,
) -> McpPresetToolRisk:
    if preset_id is None:
        return McpPresetToolRisk.MEDIUM
    preset = find_mcp_service_preset(preset_id)
    if preset is None:
        return McpPresetToolRisk.MEDIUM
    guidance = next(
        (guidance for guidance in preset.tool_guidance if guidance.name == tool_name),
        None,
    )
    return McpPresetToolRisk.MEDIUM if guidance is None else guidance.risk


def mcp_preset_tool_route(
    preset_id: str | None,
    tool_name: str,
) -> tuple[str, int] | None:
    if preset_id is None:
        return None
    preset = find_mcp_service_preset(preset_id)
    if preset is None:
        return None
    for guidance in preset.tool_guidance:
        if guidance.name == tool_name and guidance.route_action is not None and guidance.automatic_priority is not None:
            return (guidance.route_action, guidance.automatic_priority)
    return None


def find_mcp_service_preset_variant(
    preset: McpServicePreset,
    variant_id: str | None,
) -> McpServicePresetVariant | None:
    resolved_variant_id = variant_id or preset.variants[0].id
    for variant in preset.variants:
        if variant.id == resolved_variant_id:
            return variant
    return None


def mcp_preset_tool_authority_argument_allowed_paths(
    preset_id: str | None,
    tool_name: str,
) -> frozenset[tuple[str, ...]]:
    if preset_id is None:
        return frozenset()
    return _MCP_PRESET_TOOL_AUTHORITY_ARGUMENT_ALLOWED_PATHS.get(
        (preset_id, tool_name),
        frozenset(),
    )


def mcp_preset_tool_description_guidance(
    preset_id: str | None,
    tool_name: str,
) -> str | None:
    if preset_id == GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID and tool_name == GOOGLE_MAPS_SEARCH_PLACES_TOOL:
        return GOOGLE_MAPS_SEARCH_PLACES_GUIDANCE
    if preset_id == GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID and tool_name == GOOGLE_MAPS_LOOKUP_WEATHER_TOOL:
        return GOOGLE_MAPS_LOOKUP_WEATHER_GUIDANCE
    if preset_id == OPENBNB_PRESET_ID and tool_name == OPENBNB_SEARCH_TOOL:
        return OPENBNB_SEARCH_DATE_GUIDANCE
    return None


def mcp_preset_tool_rejection_guidance(
    preset_id: str | None,
    tool_name: str,
) -> str | None:
    if preset_id == GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID and tool_name == GOOGLE_MAPS_SEARCH_PLACES_TOOL:
        return (
            "Retry search_places with required textQuery and, only when needed, regionCode, "
            "languageCode, or locationBias. Do not send snake_case fields, included_type, "
            "limit, or max_results."
        )
    if preset_id == GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID and tool_name == GOOGLE_MAPS_LOOKUP_WEATHER_TOOL:
        return (
            "Retry one lookup_weather call using location.address only, for example "
            '{"location":{"address":"London, UK"}}, and add date only as an object '
            "with integer year, month, and day fields. Do not send both address and "
            "placeId, extra top-level fields, snake_case field names, or a places/ prefix."
        )
    if preset_id == OPENBNB_PRESET_ID and tool_name == OPENBNB_SEARCH_TOOL:
        return (
            "Retry with exactly one date mode: checkin plus checkout; "
            "flexibleTripLengths with optional flexibleTripDates; monthlyStartDate "
            "plus monthlyLength; or omit every date field."
        )
    return None


def adapt_mcp_preset_tool_schema(
    preset_id: str | None,
    tool_name: str,
    input_schema: JsonObject,
) -> JsonObject:
    if preset_id == OPENBNB_PRESET_ID and tool_name == OPENBNB_SEARCH_TOOL:
        return _adapt_openbnb_search_schema(input_schema)
    if preset_id == GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID and tool_name == GOOGLE_MAPS_SEARCH_PLACES_TOOL:
        return _adapt_google_maps_search_places_schema(input_schema)
    if preset_id != GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID or tool_name != GOOGLE_MAPS_LOOKUP_WEATHER_TOOL:
        return input_schema

    schema = deepcopy(input_schema)
    schema["additionalProperties"] = False
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return schema

    location = definitions.get("Location")
    if isinstance(location, dict):
        location["additionalProperties"] = False
        location["oneOf"] = [
            {"required": ["latLng"]},
            {"required": ["address"]},
            {"required": ["placeId"]},
        ]
        location_properties = location.get("properties")
        if isinstance(location_properties, dict):
            for field_name in ("address", "placeId"):
                field_schema = location_properties.get(field_name)
                if isinstance(field_schema, dict):
                    field_schema["minLength"] = 1

    date_definition = definitions.get("Date")
    if isinstance(date_definition, dict):
        date_definition["additionalProperties"] = False
        date_definition["required"] = ["year", "month", "day"]
        date_properties = date_definition.get("properties")
        if isinstance(date_properties, dict):
            _set_numeric_range(date_properties.get("year"), minimum=1, maximum=9999)
            _set_numeric_range(date_properties.get("month"), minimum=1, maximum=12)
            _set_numeric_range(date_properties.get("day"), minimum=1, maximum=31)

    lat_lng = definitions.get("LatLng")
    if isinstance(lat_lng, dict):
        lat_lng["additionalProperties"] = False
        lat_lng["required"] = ["latitude", "longitude"]
        lat_lng_properties = lat_lng.get("properties")
        if isinstance(lat_lng_properties, dict):
            _set_numeric_range(
                lat_lng_properties.get("latitude"),
                minimum=-90,
                maximum=90,
            )
            _set_numeric_range(
                lat_lng_properties.get("longitude"),
                minimum=-180,
                maximum=180,
            )

    properties = schema.get("properties")
    if isinstance(properties, dict):
        _set_numeric_range(properties.get("hour"), minimum=0, maximum=23)
    return schema


def _adapt_google_maps_search_places_schema(input_schema: JsonObject) -> JsonObject:
    schema = deepcopy(input_schema)
    schema["additionalProperties"] = False
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return schema

    location_bias = definitions.get("LocationBias")
    if isinstance(location_bias, dict):
        location_bias["additionalProperties"] = False

    circle = definitions.get("Circle")
    if isinstance(circle, dict):
        circle["additionalProperties"] = False
        circle["required"] = ["center"]
        circle_properties = circle.get("properties")
        if isinstance(circle_properties, dict):
            _set_numeric_range(
                circle_properties.get("radiusMeters"),
                minimum=0,
                maximum=50_000,
            )

    lat_lng = definitions.get("LatLng")
    if isinstance(lat_lng, dict):
        lat_lng["additionalProperties"] = False
        lat_lng["required"] = ["latitude", "longitude"]
        lat_lng_properties = lat_lng.get("properties")
        if isinstance(lat_lng_properties, dict):
            _set_numeric_range(
                lat_lng_properties.get("latitude"),
                minimum=-90,
                maximum=90,
            )
            _set_numeric_range(
                lat_lng_properties.get("longitude"),
                minimum=-180,
                maximum=180,
            )
    return schema


def _adapt_openbnb_search_schema(input_schema: JsonObject) -> JsonObject:
    schema = deepcopy(input_schema)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for field_name in ("checkin", "checkout", "monthlyStartDate"):
            field_schema = properties.get(field_name)
            if isinstance(field_schema, dict):
                field_schema["pattern"] = r"^\d{4}-\d{2}-\d{2}$"

    fixed_date_fields = ("checkin", "checkout")
    flexible_date_fields = ("flexibleTripLengths", "flexibleTripDates")
    monthly_date_fields = ("monthlyStartDate", "monthlyLength")
    schema["oneOf"] = [
        {
            "not": {
                "anyOf": [{"required": [field_name]} for field_name in OPENBNB_DATE_FIELDS],
            },
        },
        _date_mode_schema(
            required_fields=fixed_date_fields,
            forbidden_fields=(*flexible_date_fields, *monthly_date_fields),
        ),
        _date_mode_schema(
            required_fields=("flexibleTripLengths",),
            forbidden_fields=(*fixed_date_fields, *monthly_date_fields),
        ),
        _date_mode_schema(
            required_fields=monthly_date_fields,
            forbidden_fields=(*fixed_date_fields, *flexible_date_fields),
        ),
    ]
    return schema


def _date_mode_schema(
    *,
    required_fields: tuple[str, ...],
    forbidden_fields: tuple[str, ...],
) -> JsonObject:
    return {
        "required": list(required_fields),
        "not": {
            "anyOf": [{"required": [field_name]} for field_name in forbidden_fields],
        },
    }


def normalize_mcp_preset_tool_arguments(
    preset_id: str | None,
    tool_name: str,
    arguments: JsonObject,
) -> JsonObject:
    if preset_id != GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID:
        return arguments
    if tool_name == GOOGLE_MAPS_SEARCH_PLACES_TOOL:
        return _rename_arguments(
            arguments,
            {
                "text_query": "textQuery",
                "region_code": "regionCode",
                "language_code": "languageCode",
                "location_bias": "locationBias",
            },
        )
    if tool_name != GOOGLE_MAPS_LOOKUP_WEATHER_TOOL:
        return arguments
    location = arguments.get("location")
    if not isinstance(location, dict):
        return arguments
    place_id = location.get("placeId")
    if not isinstance(place_id, str) or not place_id.startswith("places/"):
        return arguments
    normalized_place_id = place_id.removeprefix("places/")
    if not normalized_place_id:
        return arguments
    normalized_location = dict(location)
    normalized_location["placeId"] = normalized_place_id
    return {**arguments, "location": normalized_location}


def _rename_arguments(
    arguments: JsonObject,
    aliases: dict[str, str],
) -> JsonObject:
    normalized = dict(arguments)
    for alias, canonical_name in aliases.items():
        if alias not in normalized or canonical_name in normalized:
            continue
        normalized[canonical_name] = normalized.pop(alias)
    return normalized


def _set_numeric_range(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, dict):
        value["minimum"] = minimum
        value["maximum"] = maximum


def preset_safe_metadata(
    preset: McpServicePreset,
    variant: McpServicePresetVariant,
) -> JsonObject:
    return cast(
        JsonObject,
        {
            "preset_id": preset.id,
            "preset_variant_id": variant.id,
            "preset_label": preset.label,
            "catalog_version": preset.catalog_version,
            "researched_at": preset.researched_at.isoformat(),
            "category": preset.category.value,
            "docs_urls": list(preset.docs_urls),
            "warnings": [*preset.warnings, *variant.warnings],
            "tool_guidance": [guidance.model_dump(mode="json") for guidance in preset.tool_guidance],
            "logo": preset.logo.model_dump(mode="json"),
        },
    )


def _auth(
    auth_type: McpAuthType,
    label: str,
    description: str,
    *,
    requires_secret: bool = False,
    api_key_header_name: str | None = None,
    oauth_scopes: tuple[str, ...] = (),
    requires_oauth_client: bool = False,
    oauth_token_endpoint_auth_method: McpTokenEndpointAuthMethod | None = None,
    requires_provider_oauth: bool = False,
    provider_oauth_label: str | None = None,
    provider_oauth_description: str | None = None,
) -> McpPresetAuthGuide:
    return McpPresetAuthGuide(
        auth_type=auth_type,
        label=label,
        description=description,
        requires_secret=requires_secret,
        api_key_header_name=api_key_header_name,
        oauth_scopes=oauth_scopes,
        requires_oauth_client=requires_oauth_client,
        oauth_token_endpoint_auth_method=oauth_token_endpoint_auth_method,
        requires_provider_oauth=requires_provider_oauth,
        provider_oauth_label=provider_oauth_label,
        provider_oauth_description=provider_oauth_description,
    )


def _variant(
    variant_id: str,
    label: str,
    compatibility: McpPresetCompatibility,
    auth: McpPresetAuthGuide,
    *,
    url: str | None = None,
    transport: McpTransportType = McpTransportType.STREAMABLE_HTTP,
    optional_auth: McpPresetAuthGuide | None = None,
    starter_allowed_tools: tuple[str, ...] = (),
    risky_tools: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
    url_placeholder: str | None = None,
    trusted_private_host: str | None = None,
) -> McpServicePresetVariant:
    return McpServicePresetVariant(
        id=variant_id,
        label=label,
        compatibility=compatibility,
        url=url,
        url_placeholder=url_placeholder,
        trusted_private_host=trusted_private_host,
        transport=transport,
        auth=auth,
        optional_auth=optional_auth,
        starter_allowed_tools=starter_allowed_tools,
        risky_tools=risky_tools,
        warnings=warnings,
    )


def _tool(
    name: str,
    *,
    label: str | None = None,
    description: str | None = None,
    risk: McpPresetToolRisk = McpPresetToolRisk.MEDIUM,
    recommended: bool = False,
    read_only: bool = False,
    disabled_by_default: bool = False,
    activity_label: str | None = None,
    activity_icon: ActivityIcon | None = None,
    route_action: str | None = None,
    automatic_priority: int | None = None,
) -> McpPresetToolGuide:
    return McpPresetToolGuide(
        name=name,
        label=label,
        description=description,
        risk=risk,
        recommended=recommended,
        read_only=read_only,
        disabled_by_default=disabled_by_default,
        activity_label=activity_label,
        activity_icon=activity_icon,
        route_action=route_action,
        automatic_priority=automatic_priority,
    )


def _preset(
    preset_id: str,
    category: McpPresetCategory,
    label: str,
    description: str,
    cost_posture: str,
    *,
    free_available: bool,
    variants: tuple[McpServicePresetVariant, ...],
    tool_guidance: tuple[McpPresetToolGuide, ...] = (),
    setup_steps: tuple[str, ...] = (),
    docs_urls: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> McpServicePreset:
    return McpServicePreset(
        id=preset_id,
        category=category,
        label=label,
        description=description,
        cost_posture=cost_posture,
        free_available=free_available,
        catalog_version=MCP_PRESET_CATALOG_VERSION,
        researched_at=MCP_PRESET_CATALOG_RESEARCHED_AT,
        variants=variants,
        tool_guidance=tool_guidance,
        setup_steps=setup_steps,
        docs_urls=docs_urls,
        warnings=warnings,
        logo=logo_for_mcp_preset(preset_id),
    )


NO_AUTH = _auth(
    McpAuthType.NONE,
    "No auth",
    "Use the public or keyless endpoint without upstream credentials.",
)
EXA_API_KEY_AUTH = _auth(
    McpAuthType.API_KEY_HEADER,
    "Exa API key",
    "Optionally store an Exa API key for authenticated production limits.",
    requires_secret=True,
    api_key_header_name="x-api-key",  # pragma: allowlist secret
)
GATEWAY_BEARER_AUTH = _auth(
    McpAuthType.STATIC_BEARER,
    "Gateway bearer token",
    "Store the bearer token for your HTTPS MCP gateway in VoxOS.",
    requires_secret=True,
)
GATEWAY_NO_AUTH = _auth(
    McpAuthType.NONE,
    "Gateway without auth",
    "Use only for a gateway that is already protected by network policy.",
)
OAUTH_AUTH = _auth(
    McpAuthType.OAUTH_AUTHORIZATION_CODE,
    "OAuth",
    "Complete provider OAuth in the browser with PKCE.",
)
OAUTH_CLIENT_POST_AUTH = _auth(
    McpAuthType.OAUTH_AUTHORIZATION_CODE,
    "OAuth client",
    "Enter the provider OAuth client ID and secret, then complete OAuth in the browser with PKCE.",
    requires_oauth_client=True,
    oauth_token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
)
STATIC_BEARER_AUTH = _auth(
    McpAuthType.STATIC_BEARER,
    "Bearer token",
    "Store the provider API token in VoxOS as an encrypted bearer token.",
    requires_secret=True,
)
LOCAL_MONZO_BROKER_AUTH = _auth(
    McpAuthType.STATIC_BEARER,
    "MCP endpoint bearer",
    ("Store the separate MCP endpoint bearer in VoxOS. VoxOS manages your Monzo OAuth tokens independently."),
    requires_secret=True,
    requires_oauth_client=True,
    requires_provider_oauth=True,
    provider_oauth_label="Connect Monzo",
    provider_oauth_description=(
        "VoxOS stores and refreshes your Monzo authorization. The MCP container "
        "receives only a short-lived credential delegation."
    ),
)
MCP_SERVICE_PRESETS = (
    _preset(
        EXA_PRESET_ID,
        McpPresetCategory.WEB_SEARCH_RESEARCH,
        "Exa",
        "Web search, neural search, and page fetch for research workflows.",
        "Keyless access and a free plan are available; API keys raise limits.",
        free_available=True,
        variants=(
            _variant(
                EXA_KEYLESS_VARIANT_ID,
                "Keyless remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                NO_AUTH,
                url=EXA_MCP_URL,
                optional_auth=EXA_API_KEY_AUTH,
                starter_allowed_tools=("web_search_exa", "web_fetch_exa"),
            ),
            _variant(
                "api-key",
                "API key remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                EXA_API_KEY_AUTH,
                url=EXA_MCP_URL,
                starter_allowed_tools=("web_search_exa", "web_fetch_exa"),
            ),
        ),
        tool_guidance=(
            _tool(
                "web_search_exa",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
                activity_label="Searching the web",
                activity_icon=ActivityIcon.WEB,
                route_action="web.search",
                automatic_priority=10,
            ),
            _tool(
                "web_fetch_exa",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
                activity_label="Reading webpages",
                activity_icon=ActivityIcon.WEB,
            ),
        ),
        setup_steps=(
            "Use keyless access for testing or create an API key in the Exa dashboard.",
            "Register the Exa remote MCP URL and test discovery.",
            "Start with search and fetch tools before enabling agent-style research tools.",
        ),
        docs_urls=(
            "https://exa.ai/docs/reference/exa-mcp",
            "https://dashboard.exa.ai/api-keys",
        ),
    ),
    _preset(
        "tavily",
        McpPresetCategory.WEB_SEARCH_RESEARCH,
        "Tavily",
        "Search and extraction tools for answer-oriented research.",
        "Free account and API key are available; production usage depends on plan.",
        free_available=True,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.tavily.com/mcp/",
                starter_allowed_tools=("tavily-search", "tavily-extract"),
            ),
            _variant(
                "bearer",
                "Bearer API key",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://mcp.tavily.com/mcp/",
                starter_allowed_tools=("tavily-search", "tavily-extract"),
            ),
        ),
        tool_guidance=(
            _tool(
                "tavily-search",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
                route_action="web.search",
                automatic_priority=20,
            ),
            _tool("tavily-extract", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
        ),
        setup_steps=(
            "Create a Tavily account.",
            "Use OAuth where available, or create a Tavily API key.",
            "Prefer bearer-header auth in VoxOS instead of putting keys in URLs.",
        ),
        docs_urls=(
            "https://docs.tavily.com/documentation/mcp",
            "https://app.tavily.com",
        ),
    ),
    _preset(
        "firecrawl",
        McpPresetCategory.WEB_SEARCH_RESEARCH,
        "Firecrawl",
        "Search, scrape, map, crawl, and structured web extraction.",
        "Keyless rate-limited endpoint is available; authenticated usage depends on plan.",
        free_available=True,
        variants=(
            _variant(
                "keyless",
                "Keyless remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                NO_AUTH,
                url="https://mcp.firecrawl.dev/v2/mcp",
                starter_allowed_tools=(
                    "firecrawl_search",
                    "firecrawl_scrape",
                    "firecrawl_map",
                ),
                risky_tools=("firecrawl_crawl", "firecrawl_extract"),
            ),
            _variant(
                "bearer",
                "Bearer API key",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://mcp.firecrawl.dev/v2/mcp",
                starter_allowed_tools=(
                    "firecrawl_search",
                    "firecrawl_scrape",
                    "firecrawl_map",
                ),
                risky_tools=("firecrawl_crawl", "firecrawl_extract"),
            ),
        ),
        tool_guidance=(
            _tool(
                "firecrawl_search",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
                route_action="web.search",
                automatic_priority=30,
            ),
            _tool("firecrawl_scrape", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("firecrawl_map", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("firecrawl_crawl", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("firecrawl_extract", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Register the keyless endpoint for testing or create a Firecrawl API key.",
            "Use bearer auth for provider keys; do not put API keys in URLs.",
            "Review crawl and extraction cost before allowing broad crawl tools.",
        ),
        docs_urls=("https://docs.firecrawl.dev/mcp-server",),
    ),
    _preset(
        "mcp-gateway",
        McpPresetCategory.MCP_GATEWAYS,
        "MCP Gateway",
        "Register one public HTTPS MCP gateway that exposes one or more upstream MCP tools.",
        "Free or self-hosted; provider, browser, and infrastructure costs depend on the upstream servers.",
        free_available=True,
        variants=(
            _variant(
                "http-bearer",
                "Streamable HTTP with bearer auth",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_BEARER_AUTH,
            ),
            _variant(
                "http-none",
                "Streamable HTTP without VoxOS auth",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_NO_AUTH,
                warnings=("Use this only when the gateway is protected outside VoxOS.",),
            ),
            _variant(
                "sse-bearer",
                "SSE with bearer auth",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_BEARER_AUTH,
                transport=McpTransportType.SSE,
            ),
            _variant(
                "sse-none",
                "SSE without VoxOS auth",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_NO_AUTH,
                transport=McpTransportType.SSE,
                warnings=("Use this only when the gateway is protected outside VoxOS.",),
            ),
        ),
        setup_steps=(
            "Deploy or configure the gateway with the upstream MCP servers it should expose.",
            "Expose one public HTTPS endpoint and protect it with bearer auth where possible.",
            "Register the gateway URL, test discovery, then choose allowed tools from the discovered tool list.",
        ),
        warnings=(
            "One gateway registration can expose multiple tools; keep allowed tools narrow after discovery.",
            "Local stdio and localhost URLs cannot be registered in VoxOS.",
        ),
    ),
    _preset(
        "google-maps-grounding-lite",
        McpPresetCategory.TRAVEL_LIFESTYLE,
        "Google Maps Grounding Lite",
        "Google Maps place search, weather, routes, name resolution, and Maps URL resolution.",
        "Google Maps Platform billing and per-project quotas apply; a no-cost demo key is available for prototyping.",
        free_available=True,
        variants=(
            _variant(
                "api-key",
                "API key remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.API_KEY_HEADER,
                    "Google Maps API key",
                    "Store the Google Maps Platform API key in the X-Goog-Api-Key header.",
                    requires_secret=True,
                    api_key_header_name="X-Goog-Api-Key",  # pragma: allowlist secret
                ),
                url="https://mapstools.googleapis.com/mcp",
                starter_allowed_tools=(
                    "search_places",
                    "lookup_weather",
                    "compute_routes",
                    "resolve_names",
                    "resolve_maps_urls",
                ),
            ),
        ),
        tool_guidance=(
            _tool(
                "search_places",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "lookup_weather",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "compute_routes",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool("resolve_names", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("resolve_maps_urls", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
        ),
        setup_steps=(
            "Create or choose a Google Cloud project with Maps Grounding Lite enabled.",
            "Create a restricted API key and store it in the encrypted VoxOS user vault.",
            "Set project quotas and billing alerts before enabling production traffic.",
        ),
        docs_urls=(
            "https://developers.google.com/maps/ai/grounding-lite",
            "https://console.cloud.google.com/google/maps-apis/credentials",
        ),
        warnings=("Restrict the API key to Maps Grounding Lite and monitor per-project spend.",),
    ),
    _preset(
        "flightradar24",
        McpPresetCategory.TRAVEL_LIFESTYLE,
        "Flightradar24",
        "Live and historical flights, tracks, airline details, and airport information.",
        "A Flightradar24 API subscription is required; usage is charged to the key configured on the MCP deployment.",
        free_available=False,
        variants=(
            _variant(
                "local-compose",
                "Local Compose",
                McpPresetCompatibility.DIRECT_REMOTE,
                NO_AUTH,
                url="http://flightradar24-mcp:8000/mcp",
                trusted_private_host="flightradar24-mcp",
                starter_allowed_tools=(
                    "get_live_flights_positions_light",
                    "get_live_flights_count",
                    "get_flight_summary_light",
                    "get_airline_info",
                    "get_airport_info_light",
                ),
                warnings=("This endpoint is reachable only from the explicitly trusted local Compose network.",),
            ),
            _variant(
                "deployed-https",
                "Deployed HTTPS endpoint",
                McpPresetCompatibility.CUSTOM_REMOTE,
                NO_AUTH,
                url_placeholder="https://your-flightradar24-mcp.example.com/mcp",
                starter_allowed_tools=(
                    "get_live_flights_positions_light",
                    "get_live_flights_count",
                    "get_flight_summary_light",
                    "get_airline_info",
                    "get_airport_info_light",
                ),
                warnings=(
                    (
                        "Use a public HTTPS endpoint and protect access with network controls "
                        "when the deployment has no application-layer auth."
                    ),
                ),
            ),
        ),
        tool_guidance=(
            _tool(
                "get_live_flights_positions_light",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "get_live_flights_count",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "get_flight_summary_light",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "get_airline_info",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "get_airport_info_light",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool("get_flight_tracks", read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool(
                "get_historic_flights_positions_full",
                read_only=True,
                risk=McpPresetToolRisk.MEDIUM,
            ),
        ),
        setup_steps=(
            "For local use, set FR24_API_KEY in .env and start the server with make mcp-stack-up.",
            "Choose Local Compose when Voxos and the MCP server share the local Docker network.",
            "For another deployment, choose Deployed HTTPS endpoint and enter its full /mcp URL.",
            "Review Flightradar24 plan quotas before granting historical or full-detail tools.",
        ),
        docs_urls=(
            "https://github.com/Flightradar24/fr24api-mcp",
            "https://fr24api.flightradar24.com/docs/mcp-server",
        ),
        warnings=(
            "The provider API key remains in the MCP server environment and is shared by users of that deployment.",
            "The official MCP package is read-only but provider quota and historical-data charges still apply.",
        ),
    ),
    _preset(
        "aftership",
        McpPresetCategory.TRAVEL_LIFESTYLE,
        "AfterShip",
        "Real-time parcel tracking across supported carriers.",
        (
            "The public endpoint is free for personal, non-commercial interactive use; "
            "commercial or high-volume use requires an AfterShip agreement."
        ),
        free_available=True,
        variants=(
            _variant(
                "public",
                "Public tracking remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                NO_AUTH,
                url="https://mcp.aftership.com/tracking/public",
                starter_allowed_tools=("track_shipment",),
                risky_tools=("get_returns_demo",),
            ),
        ),
        tool_guidance=(
            _tool("track_shipment", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool(
                "get_returns_demo",
                description="Generate a merchant-facing AfterShip Returns Center preview.",
                read_only=True,
                risk=McpPresetToolRisk.MEDIUM,
                disabled_by_default=True,
            ),
        ),
        setup_steps=(
            "Register the public AfterShip tracking endpoint.",
            "Use shipment tracking for interactive consumer requests only.",
            "Contact AfterShip before commercial or automated high-volume use.",
        ),
        docs_urls=("https://github.com/AfterShip/tracking-and-returns-mcp",),
        warnings=(
            "The public fair-use policy excludes bulk, automated, and commercial traffic.",
            "The returns demo is for merchants evaluating AfterShip, not consumers returning purchases.",
        ),
    ),
    _preset(
        "viator",
        McpPresetCategory.TRAVEL_LIFESTYLE,
        "Viator Experiences",
        "Search Viator activities and retrieve experience details with booking links.",
        "The hosted search endpoint is available without MCP credentials; commercial and affiliate terms still apply.",
        free_available=True,
        variants=(
            _variant(
                "public",
                "Public experiences remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                NO_AUTH,
                url="https://exp-app-mcp.prod.ep.viator.com/mcp",
                starter_allowed_tools=(
                    "search_experiences",
                    "get_experience_details",
                ),
            ),
        ),
        tool_guidance=(
            _tool(
                "search_experiences",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "get_experience_details",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
        ),
        setup_steps=(
            "Register the hosted Viator Experiences endpoint.",
            "Enable search and detail tools for travel-planning agents.",
            "Send users to the returned Viator link to complete any booking.",
        ),
        docs_urls=("https://docs.viator.com/partner-api/mcp/",),
        warnings=("The MCP server returns booking links; it does not complete purchases inside VoxOS.",),
    ),
    _preset(
        "tripgo",
        McpPresetCategory.TRAVEL_LIFESTYLE,
        "TripGo by SkedGo",
        "Public transport departures, multimodal routing, locations, and shareable trip links.",
        (
            "The official public MCP endpoint is keyless; production capacity and "
            "commercial terms should be confirmed with SkedGo."
        ),
        free_available=True,
        variants=(
            _variant(
                "public",
                "Public routing remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                NO_AUTH,
                url="https://tripgo-mcp-server.skedgo-account.workers.dev/mcp",
                starter_allowed_tools=(
                    "tripgo-routing",
                    "tripgo-get-trip-url",
                    "tripgo-locations",
                    "tripgo-departures",
                ),
            ),
        ),
        tool_guidance=(
            _tool("tripgo-routing", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool(
                "tripgo-get-trip-url",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool("tripgo-locations", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("tripgo-departures", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
        ),
        setup_steps=(
            "Register SkedGo's public TripGo MCP endpoint.",
            "Enable routing, location, departure, and trip-link tools.",
            "Confirm commercial capacity and support terms before broad production rollout.",
        ),
        docs_urls=("https://github.com/skedgo/tripgo-mcp-server",),
        warnings=("The public endpoint uses SkedGo's deployment credential and does not provide a per-user SLA.",),
    ),
    _preset(
        "monzo",
        McpPresetCategory.PAYMENTS_COMMERCE,
        "Monzo",
        ("Personal account balances, pots, and transactions through the locally hosted Monzo MCP."),
        (
            "The Monzo Developer API is for personal or small explicit-user "
            "integrations; normal account and API limits apply."
        ),
        free_available=True,
        variants=(
            _variant(
                "local-compose",
                "Local Compose",
                McpPresetCompatibility.DIRECT_REMOTE,
                LOCAL_MONZO_BROKER_AUTH,
                url="http://monzo-mcp:8000/mcp",
                trusted_private_host="monzo-mcp",
                risky_tools=(
                    "monzo_annotate_transaction",
                    "monzo_deposit_into_pot",
                    "monzo_withdraw_from_pot",
                ),
                warnings=(
                    (
                        "The endpoint is reachable only from the trusted local "
                        "Compose network and serves multiple VoxOS users statelessly."
                    ),
                    ("Use the separate MCP endpoint bearer from its owner-only file, never a Monzo OAuth token."),
                ),
            ),
        ),
        tool_guidance=(
            _tool(
                "monzo_connection_status",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "monzo_list_accounts",
                read_only=True,
                risk=McpPresetToolRisk.MEDIUM,
            ),
            _tool(
                "monzo_get_balance",
                read_only=True,
                risk=McpPresetToolRisk.HIGH,
            ),
            _tool(
                "monzo_list_pots",
                read_only=True,
                risk=McpPresetToolRisk.HIGH,
            ),
            _tool(
                "monzo_get_transaction",
                read_only=True,
                risk=McpPresetToolRisk.HIGH,
            ),
            _tool(
                "monzo_list_transactions",
                read_only=True,
                risk=McpPresetToolRisk.HIGH,
            ),
            _tool(
                "monzo_annotate_transaction",
                risk=McpPresetToolRisk.HIGH,
                disabled_by_default=True,
            ),
            _tool(
                "monzo_deposit_into_pot",
                risk=McpPresetToolRisk.HIGH,
                disabled_by_default=True,
            ),
            _tool(
                "monzo_withdraw_from_pot",
                risk=McpPresetToolRisk.HIGH,
                disabled_by_default=True,
            ),
        ),
        setup_steps=(
            "Create a Monzo OAuth client and register the VoxOS callback URL.",
            "Choose Local Compose and enter the separate MCP endpoint bearer.",
            "Connect Monzo in VoxOS and approve the connection in the Monzo mobile app.",
            "Test discovery, then explicitly grant only the required read tools.",
        ),
        docs_urls=(
            "https://github.com/s-block/monzo-mcp",
            "https://docs.monzo.com/",
        ),
        warnings=(
            (
                "Balances and transactions enter model context and may be retained "
                "in VoxOS chats, runs, transcripts, and tool events."
            ),
            ("Do not expose the MCP endpoint or the VoxOS credential-broker route through untrusted network ingress."),
            ("Financial write tools are disabled in the supported Compose configuration."),
        ),
    ),
    _preset(
        "paypal",
        McpPresetCategory.PAYMENTS_COMMERCE,
        "PayPal",
        "Merchant orders, invoices, refunds, subscriptions, disputes, catalog, tracking, and reporting.",
        "A PayPal Business account is required for production; normal PayPal transaction and merchant fees apply.",
        free_available=False,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.paypal.com/mcp",
                risky_tools=(
                    "create_invoice",
                    "send_invoice",
                    "send_invoice_reminder",
                    "cancel_sent_invoice",
                    "create_order",
                    "pay_order",
                    "create_refund",
                    "accept_dispute_claim",
                    "create_shipment_tracking",
                    "update_shipment_tracking",
                    "create_product",
                    "create_subscription_plan",
                    "create_subscription",
                    "update_subscription",
                    "cancel_subscription",
                ),
            ),
        ),
        tool_guidance=(
            _tool("list_invoices", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_invoice", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_order", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_refund", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list_disputes", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_dispute", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool(
                "get_shipment_tracking",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool("list_products", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("show_product_details", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool(
                "list_subscription_plans",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "show_subscription_plan_details",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "show_subscription_details",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool("list_transactions", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool(
                "get_merchant_insights",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool("create_invoice", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("send_invoice", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("send_invoice_reminder", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("cancel_sent_invoice", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_order", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("pay_order", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_refund", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("accept_dispute_claim", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_shipment_tracking", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("update_shipment_tracking", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_product", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_subscription_plan", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_subscription", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("update_subscription", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("cancel_subscription", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Connect a PayPal account through the hosted OAuth flow.",
            "Use a PayPal Sandbox merchant while validating tool discovery and approvals.",
            "Review and explicitly grant each live mutation tool before production use.",
        ),
        docs_urls=(
            "https://developer.paypal.com/tools/mcp-server/",
            "https://github.com/paypal/agent-toolkit",
        ),
        warnings=(
            (
                "PayPal tools operate on the connected merchant account and can create "
                "financial or customer-visible side effects."
            ),
            "No PayPal mutation tool is enabled automatically.",
        ),
    ),
    _preset(
        "openbnb",
        McpPresetCategory.TRAVEL_LIFESTYLE,
        "OpenBnB",
        "Vacation-rental search, listing details, nearby places, maps, and routing.",
        "The hosted OpenBnB service currently offers a free account without a credit card.",
        free_available=True,
        variants=(
            _variant(
                "oauth",
                "Hosted OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.openbnb.ai/mcp",
            ),
        ),
        tool_guidance=(
            _tool("airbnb_search", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool(
                "airbnb_listing",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "maps_search_places",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "maps_place_details",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "maps_geocode",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "maps_reverse_geocode",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
            _tool(
                "maps_directions",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.LOW,
            ),
        ),
        setup_steps=(
            "Create or sign in to a free OpenBnB account through the hosted OAuth flow.",
            "Test discovery and review the read-only search, listing, places, and route tools.",
            "Keep booking completion on the provider website.",
        ),
        docs_urls=(
            "https://openbnb.ai/",
            "https://github.com/openbnb-org/mcp-server-airbnb",
        ),
        warnings=(
            "OpenBnB is community-operated and is not affiliated with or endorsed by Airbnb.",
            "Listing extraction depends on public upstream pages and may break when those pages change.",
        ),
    ),
    _preset(
        "google-workspace",
        McpPresetCategory.PRODUCTIVITY_COLLABORATION,
        "Google Workspace",
        "Remote Google Workspace MCP servers for Gmail, Drive, Calendar, Chat, and People.",
        (
            "Google Cloud project and Workspace/user OAuth consent setup are required; "
            "Google API usage depends on the account."
        ),
        free_available=True,
        variants=(
            _variant(
                "gmail",
                "Gmail",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.OAUTH_AUTHORIZATION_CODE,
                    "OAuth client",
                    "Use a Google OAuth client ID and secret for Gmail MCP.",
                    oauth_scopes=(
                        "https://www.googleapis.com/auth/gmail.readonly",
                        "https://www.googleapis.com/auth/gmail.compose",
                    ),
                    requires_oauth_client=True,
                    oauth_token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
                ),
                url="https://gmailmcp.googleapis.com/mcp/v1",
                starter_allowed_tools=(
                    "gmail.search_threads",
                    "gmail.get_thread",
                    "gmail.list_labels",
                    "gmail.list_drafts",
                ),
                risky_tools=(
                    "gmail.create_draft",
                    "gmail.create_label",
                    "gmail.label_message",
                    "gmail.label_thread",
                    "gmail.unlabel_message",
                    "gmail.unlabel_thread",
                ),
            ),
            _variant(
                "drive",
                "Google Drive",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.OAUTH_AUTHORIZATION_CODE,
                    "OAuth client",
                    "Use a Google OAuth client ID and secret for Drive MCP.",
                    oauth_scopes=(
                        "https://www.googleapis.com/auth/drive.readonly",
                        "https://www.googleapis.com/auth/drive.file",
                    ),
                    requires_oauth_client=True,
                    oauth_token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
                ),
                url="https://drivemcp.googleapis.com/mcp/v1",
                starter_allowed_tools=(
                    "drive.search_files",
                    "drive.read_file_content",
                    "drive.get_file_metadata",
                    "drive.list_recent_files",
                ),
                risky_tools=("drive.copy_file", "drive.create_file"),
            ),
            _variant(
                "calendar",
                "Google Calendar",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.OAUTH_AUTHORIZATION_CODE,
                    "OAuth client",
                    "Use a Google OAuth client ID and secret for Calendar MCP.",
                    oauth_scopes=(
                        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
                        "https://www.googleapis.com/auth/calendar.events.freebusy",
                        "https://www.googleapis.com/auth/calendar.events.readonly",
                    ),
                    requires_oauth_client=True,
                    oauth_token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
                ),
                url="https://calendarmcp.googleapis.com/mcp/v1",
                starter_allowed_tools=(
                    "calendar.list_calendars",
                    "calendar.list_events",
                    "calendar.get_event",
                    "calendar.suggest_time",
                ),
                risky_tools=(
                    "calendar.create_event",
                    "calendar.update_event",
                    "calendar.delete_event",
                    "calendar.respond_to_event",
                ),
            ),
            _variant(
                "people",
                "People",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.OAUTH_AUTHORIZATION_CODE,
                    "OAuth client",
                    "Use a Google OAuth client ID and secret for People MCP.",
                    oauth_scopes=(
                        "https://www.googleapis.com/auth/directory.readonly",
                        "https://www.googleapis.com/auth/userinfo.profile",
                        "https://www.googleapis.com/auth/contacts.readonly",
                    ),
                    requires_oauth_client=True,
                    oauth_token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
                ),
                url="https://people.googleapis.com/mcp/v1",
                starter_allowed_tools=(
                    "people.get_user_profile",
                    "people.search_contacts",
                    "people.search_directory_people",
                ),
            ),
            _variant(
                "chat",
                "Google Chat",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.OAUTH_AUTHORIZATION_CODE,
                    "OAuth client",
                    "Use a Google OAuth client ID and secret for Chat MCP.",
                    oauth_scopes=(
                        "https://www.googleapis.com/auth/chat.spaces.readonly",
                        "https://www.googleapis.com/auth/chat.memberships.readonly",
                        "https://www.googleapis.com/auth/chat.messages.readonly",
                        "https://www.googleapis.com/auth/chat.messages.create",
                        "https://www.googleapis.com/auth/chat.users.readstate.readonly",
                    ),
                    requires_oauth_client=True,
                    oauth_token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
                ),
                url="https://chatmcp.googleapis.com/mcp/v1",
                starter_allowed_tools=(
                    "chat.search_conversations",
                    "chat.list_messages",
                    "chat.search_messages",
                ),
                risky_tools=("chat.send_message",),
            ),
        ),
        tool_guidance=(
            _tool("gmail.search_threads", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("gmail.get_thread", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("gmail.create_draft", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("drive.search_files", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("drive.read_file_content", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("drive.create_file", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("calendar.list_events", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("calendar.create_event", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("calendar.update_event", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("people.get_user_profile", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("people.search_contacts", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("chat.search_messages", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("chat.send_message", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Enable the Google Workspace APIs and MCP services for the products you want to connect.",
            "Create a Google OAuth web client and add the VoxOS OAuth redirect URI shown below.",
            "Choose one Workspace product per server; Google exposes a separate MCP URL for each product.",
        ),
        docs_urls=(
            "https://developers.google.com/workspace/guides/configure-mcp-servers",
            "https://developers.google.com/workspace/guides/configure-mcp-servers#support-products",
        ),
        warnings=("Google Workspace MCP is currently a developer preview.",),
    ),
    _preset(
        "microsoft-work-iq",
        McpPresetCategory.PRODUCTIVITY_COLLABORATION,
        "Microsoft Work IQ",
        "Microsoft 365 Copilot work context for mail, meetings, documents, Teams, and people.",
        "Microsoft 365 Copilot license, usage-based billing, and admin consent are required.",
        free_available=False,
        variants=(
            _variant(
                "gateway-bearer",
                "Remote gateway",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_BEARER_AUTH,
                url_placeholder="https://your-workiq-gateway.example.com/mcp",
            ),
        ),
        setup_steps=(
            "Install or run the official Work IQ MCP server with npx -y @microsoft/workiq mcp.",
            "Complete Microsoft tenant admin consent and EULA requirements before exposing it.",
            "Publish it through an HTTPS MCP gateway protected with bearer auth, then register that gateway URL.",
        ),
        docs_urls=(
            "https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/cli",
            "https://learn.microsoft.com/en-us/microsoft-copilot-studio/use-work-iq",
            "https://github.com/microsoft/work-iq",
        ),
        warnings=("Work IQ is a local stdio MCP server; VoxOS needs a remote HTTPS gateway for it.",),
    ),
    _preset(
        "slack",
        McpPresetCategory.PRODUCTIVITY_COLLABORATION,
        "Slack",
        "Slack search, messages, channels, canvases, users, reactions, and files.",
        "Slack workspace and an internal or Marketplace Slack app are required.",
        free_available=True,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.OAUTH_AUTHORIZATION_CODE,
                    "OAuth client",
                    "Use a Slack app client ID and secret for confidential OAuth.",
                    oauth_scopes=(
                        "search:read.public",
                        "search:read.private",
                        "search:read.files",
                        "search:read.users",
                        "channels:history",
                        "groups:history",
                        "mpim:history",
                        "im:history",
                        "files:read",
                        "emoji:read",
                        "users:read",
                        "users:read.email",
                        "channels:read",
                        "groups:read",
                        "mpim:read",
                        "canvases:read",
                    ),
                    requires_oauth_client=True,
                    oauth_token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
                ),
                url="https://mcp.slack.com/mcp",
                starter_allowed_tools=(
                    "search_messages",
                    "search_files",
                    "read_channel",
                    "read_thread",
                ),
                risky_tools=(
                    "send_message",
                    "create_conversation",
                    "add_reaction",
                    "create_canvas",
                    "update_canvas",
                ),
            ),
        ),
        tool_guidance=(
            _tool("search_messages", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("search_files", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("read_channel", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("read_thread", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("send_message", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_conversation", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_canvas", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("update_canvas", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Create or reuse a Slack internal or Marketplace app that is allowed to use MCP.",
            "Add the VoxOS OAuth redirect URI shown below, then paste the app client ID and secret.",
            "Start with read/search scopes; add write scopes and re-authenticate only for trusted agents.",
        ),
        docs_urls=(
            "https://docs.slack.dev/ai/slack-mcp-server/",
            "https://slack.com/help/articles/48855576908307-Guide-to-Model-Context-Protocol-in-Slack",
        ),
        warnings=("Slack does not support Dynamic Client Registration for MCP.",),
    ),
    _preset(
        "todoist",
        McpPresetCategory.PRODUCTIVITY_COLLABORATION,
        "Todoist",
        "Hosted Todoist MCP for reading, creating, and updating tasks and projects.",
        "Todoist account required; the hosted MCP uses OAuth and a free Todoist account can connect.",
        free_available=True,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.OAUTH_AUTHORIZATION_CODE,
                    "OAuth",
                    "Complete Todoist OAuth in the browser.",
                    oauth_scopes=("data:read_write",),
                ),
                url="https://ai.todoist.net/mcp",
                starter_allowed_tools=("findTasksByDate",),
            ),
        ),
        tool_guidance=(
            _tool("findTasksByDate", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("addTasks", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("updateTasks", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Register the hosted Todoist MCP URL.",
            "Complete browser OAuth when VoxOS opens the authorization URL.",
            "Review task and project writes before enabling write tools broadly.",
        ),
        docs_urls=(
            "https://developer.todoist.com/api/v1/#todoist-mcp",
            "https://www.todoist.com/help/articles/use-chatgpt-with-todoist-mcp-WEeLx9d8h",
            "https://github.com/Doist/todoist-mcp",
        ),
    ),
    _preset(
        "atlassian",
        McpPresetCategory.DEVELOPER_OPERATIONS,
        "Atlassian",
        "Atlassian Rovo MCP for Jira, Confluence, Bitbucket, Compass, and Teamwork Graph.",
        "Atlassian Cloud account required; beta tools may later consume Rovo credits.",
        free_available=True,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.atlassian.com/v1/mcp/authv2",
                starter_allowed_tools=(
                    "atlassianUserInfo",
                    "getAccessibleAtlassianResources",
                    "searchAtlassian",
                    "fetchAtlassian",
                    "getJiraIssue",
                    "searchJiraIssuesUsingJql",
                    "getConfluencePage",
                    "searchConfluenceUsingCql",
                ),
                risky_tools=(
                    "createJiraIssue",
                    "editJiraIssue",
                    "transitionJiraIssue",
                    "addCommentToJiraIssue",
                    "createConfluencePage",
                    "updateConfluencePage",
                    "createConfluenceFooterComment",
                    "createConfluenceInlineComment",
                    "addTeamworkGraphContext",
                ),
            ),
        ),
        tool_guidance=(
            _tool("atlassianUserInfo", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("getAccessibleAtlassianResources", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("searchAtlassian", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("fetchAtlassian", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("getJiraIssue", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("searchJiraIssuesUsingJql", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("getConfluencePage", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("searchConfluenceUsingCql", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("createJiraIssue", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("editJiraIssue", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("transitionJiraIssue", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("createConfluencePage", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("updateConfluencePage", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Register the official Atlassian Rovo MCP URL.",
            "Complete OAuth in the browser; organization admins can control tool permission groups.",
            "Start with search/read tools before enabling Jira or Confluence mutation tools.",
        ),
        docs_urls=(
            "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/",
            "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools/",
            "https://github.com/atlassian/atlassian-mcp-server",
        ),
    ),
    _preset(
        "stripe",
        McpPresetCategory.BUSINESS_SYSTEMS,
        "Stripe",
        "Stripe API and knowledge-base tools for customers, billing, reports, refunds, and docs.",
        "Stripe account required; OAuth access must be enabled by an administrator, or use a restricted API key.",
        free_available=True,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.stripe.com",
                starter_allowed_tools=(
                    "stripe_api_search",
                    "stripe_api_details",
                    "stripe_api_read",
                    "get_stripe_account_info",
                    "search_stripe_documentation",
                ),
                risky_tools=("stripe_api_write", "create_refund", "stripe_report"),
            ),
            _variant(
                "restricted-key",
                "Restricted API key",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://mcp.stripe.com",
                starter_allowed_tools=(
                    "stripe_api_search",
                    "stripe_api_details",
                    "stripe_api_read",
                    "get_stripe_account_info",
                    "search_stripe_documentation",
                ),
                risky_tools=("stripe_api_write", "create_refund", "stripe_report"),
            ),
        ),
        tool_guidance=(
            _tool("stripe_api_search", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("stripe_api_details", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("stripe_api_read", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("get_stripe_account_info", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("search_stripe_documentation", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("stripe_api_write", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_refund", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("stripe_report", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Enable MCP access in the Stripe Dashboard or create a restricted API key.",
            "Prefer OAuth for user-scoped access; use restricted keys only for autonomous agents.",
            "Keep write and refund tools disabled unless the agent has explicit payment-operation authority.",
        ),
        docs_urls=(
            "https://docs.stripe.com/mcp",
            "https://docs.stripe.com/agents",
            "https://github.com/mcp/com.stripe/tree/main/mcp",
        ),
        warnings=("Stripe write tools can create or mutate payment resources.",),
    ),
    _preset(
        "shopify",
        McpPresetCategory.BUSINESS_SYSTEMS,
        "Shopify Storefront",
        "Store-specific Shopify MCP endpoints for product catalog, cart, checkout, and policies.",
        "No auth is required for Storefront MCP, but availability depends on the individual store.",
        free_available=True,
        variants=(
            _variant(
                "storefront",
                "Storefront cart and policies",
                McpPresetCompatibility.CUSTOM_REMOTE,
                NO_AUTH,
                starter_allowed_tools=("get_cart", "search_shop_policies_and_faqs"),
                risky_tools=("update_cart",),
                url_placeholder="https://your-store.myshopify.com/api/mcp",
            ),
            _variant(
                "ucp-catalog",
                "UCP catalog",
                McpPresetCompatibility.CUSTOM_REMOTE,
                NO_AUTH,
                starter_allowed_tools=("search_catalog", "lookup_catalog", "get_product"),
                url_placeholder="https://your-store.myshopify.com/api/ucp/mcp",
            ),
        ),
        tool_guidance=(
            _tool("search_catalog", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("lookup_catalog", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_product", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_cart", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("search_shop_policies_and_faqs", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("update_cart", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Copy the MCP endpoint from the Shopify store you want agents to help with.",
            "Use the Storefront endpoint for cart and policy tools, or the UCP endpoint for catalog-only browsing.",
            "Test each store because some stores can restrict MCP endpoint access.",
        ),
        docs_urls=(
            "https://shopify.dev/docs/apps/build/storefront-mcp/servers/storefront",
            "https://shopify.dev/docs/apps/build/ai-toolkit",
        ),
        warnings=("Cart mutation tools can alter a shopper's checkout state.",),
    ),
    _preset(
        "hubspot",
        McpPresetCategory.BUSINESS_SYSTEMS,
        "HubSpot",
        "Remote HubSpot MCP for CRM records, activities, content, marketing, and user details.",
        "HubSpot account and MCP auth app are required.",
        free_available=True,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_CLIENT_POST_AUTH,
                url="https://mcp.hubspot.com",
                starter_allowed_tools=("get_user_details",),
            ),
        ),
        tool_guidance=(_tool("get_user_details", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),),
        setup_steps=(
            "Create an MCP auth app in HubSpot and add the VoxOS OAuth redirect URI shown below.",
            "Paste the HubSpot client ID and secret, then complete browser OAuth.",
            "HubSpot determines available object scopes from the MCP tools and the user's granted permissions.",
        ),
        docs_urls=(
            "https://developers.hubspot.com/docs/apps/developer-platform/build-apps/integrate-with-the-remote-hubspot-mcp-server",
            "https://developers.hubspot.com/ai-tools/mcp",
        ),
        warnings=("HubSpot activity objects are blocked when Sensitive Data is enabled on the account.",),
    ),
    _preset(
        "home-assistant",
        McpPresetCategory.SMART_HOME_ENVIRONMENT,
        "Home Assistant",
        "Home Assistant MCP server for Assist context, exposed entities, and smart-home control.",
        "Self-hosted Home Assistant instance required; no paid provider account is required.",
        free_available=True,
        variants=(
            _variant(
                "long-lived-token",
                "Long-lived token",
                McpPresetCompatibility.CUSTOM_REMOTE,
                STATIC_BEARER_AUTH,
                url_placeholder="https://home.example.com/api/mcp",
                risky_tools=("assist",),
            ),
        ),
        setup_steps=(
            "Enable the Model Context Protocol Server integration in Home Assistant.",
            "Expose only the entities the agent should see or control.",
            "Enter the public HTTPS /api/mcp URL and store a Home Assistant long-lived access token as bearer auth.",
        ),
        docs_urls=(
            "https://www.home-assistant.io/integrations/mcp_server/",
            "https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token",
        ),
        warnings=("Home automation tools can act on lights, locks, sensors, and other real-world devices.",),
    ),
    _preset(
        "weather-open-meteo",
        McpPresetCategory.SMART_HOME_ENVIRONMENT,
        "Weather MCP",
        "Free weather MCP backed by NOAA, Open-Meteo, and related public weather data sources.",
        (
            "The recommended open-source server uses free, keyless weather APIs; "
            "hosting costs depend on where you deploy it."
        ),
        free_available=True,
        variants=(
            _variant(
                "remote-gateway",
                "Remote Open-Meteo gateway",
                McpPresetCompatibility.CUSTOM_REMOTE,
                NO_AUTH,
                starter_allowed_tools=(
                    "get_forecast",
                    "get_current_conditions",
                    "get_weather_alerts",
                    "search_location",
                    "check_service_status",
                ),
                url_placeholder="https://your-weather-mcp.example.com/mcp",
            ),
        ),
        tool_guidance=(
            _tool("get_forecast", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_current_conditions", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_weather_alerts", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("search_location", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("air_quality", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("lightning_activity", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
        ),
        setup_steps=(
            "Deploy a remote HTTPS wrapper for a free Open-Meteo/NOAA weather MCP server.",
            "Keep it no-auth only if you are comfortable exposing read-only weather tools publicly.",
            "Test discovery and allow only the weather tools you need.",
        ),
        docs_urls=(
            "https://github.com/weather-mcp/weather-mcp",
            "https://open-meteo.com/",
        ),
        warnings=(
            (
                "I did not find an official, stable, free hosted no-auth weather MCP endpoint "
                "suitable for a direct preset."
            ),
        ),
    ),
    _preset(
        "aws",
        McpPresetCategory.DEVELOPER_OPERATIONS,
        "AWS",
        "AWS Knowledge remote MCP plus an option for managed AWS operations through a SigV4-capable gateway.",
        (
            "AWS Knowledge is free and keyless; operational AWS access requires AWS credentials "
            "and normal AWS account costs."
        ),
        free_available=True,
        variants=(
            _variant(
                "knowledge",
                "AWS Knowledge",
                McpPresetCompatibility.DIRECT_REMOTE,
                NO_AUTH,
                url="https://knowledge-mcp.global.api.aws",
                starter_allowed_tools=(
                    "search_documentation",
                    "read_documentation",
                    "list_regions",
                    "get_regional_availability",
                    "retrieve_skill",
                ),
            ),
            _variant(
                "managed-gateway",
                "Managed AWS via gateway",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_BEARER_AUTH,
                url_placeholder="https://your-aws-mcp-gateway.example.com/mcp",
                warnings=(
                    (
                        "The gateway must sign upstream AWS MCP requests with SigV4; "
                        "VoxOS does not store AWS credentials for MCP."
                    ),
                ),
            ),
        ),
        tool_guidance=(
            _tool("search_documentation", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("read_documentation", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list_regions", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_regional_availability", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("retrieve_skill", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
        ),
        setup_steps=(
            "Use AWS Knowledge for direct keyless AWS docs, regional availability, and AWS skill retrieval.",
            "For AWS account operations, deploy a SigV4-capable proxy or gateway around the managed AWS MCP server.",
            "Restrict IAM permissions and CloudTrail-monitor all operational tool calls.",
        ),
        docs_urls=(
            "https://awslabs.github.io/mcp/servers/aws-knowledge-mcp-server",
            "https://docs.aws.amazon.com/agent-toolkit/latest/userguide/mcp-server.html",
            "https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html",
        ),
        warnings=("Direct AWS account operations are intentionally not configured without a SigV4-capable gateway.",),
    ),
    _preset(
        "playwright",
        McpPresetCategory.BROWSER_AUTOMATION,
        "Playwright",
        "Official Playwright MCP for browser automation using accessibility snapshots.",
        "Free and local, but VoxOS needs it exposed through an HTTPS MCP gateway.",
        free_available=True,
        variants=(
            _variant(
                "gateway-bearer",
                "Remote gateway",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_BEARER_AUTH,
                url_placeholder="https://your-playwright-gateway.example.com/mcp",
                starter_allowed_tools=("browser_snapshot",),
            ),
        ),
        tool_guidance=(
            _tool("browser_navigate", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("browser_click", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("browser_type", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("browser_snapshot", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
        ),
        setup_steps=(
            "Run the official Playwright MCP server with npx @playwright/mcp@latest.",
            "Expose it through a remote HTTPS MCP gateway protected by bearer auth.",
            "Grant browser action tools only to agents that should browse and interact with websites.",
        ),
        docs_urls=(
            "https://playwright.dev/docs/getting-started-mcp",
            "https://github.com/microsoft/playwright-mcp",
        ),
        warnings=("Browser automation can read page content and perform actions as the browser user.",),
    ),
    _preset(
        "chrome-devtools",
        McpPresetCategory.BROWSER_AUTOMATION,
        "Chrome DevTools",
        "Chrome DevTools MCP for live browser inspection, console/network debugging, and performance traces.",
        "Free and local, but VoxOS needs it exposed through an HTTPS MCP gateway.",
        free_available=True,
        variants=(
            _variant(
                "gateway-bearer",
                "Remote gateway",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_BEARER_AUTH,
                url_placeholder="https://your-devtools-gateway.example.com/mcp",
                starter_allowed_tools=(
                    "list_console_messages",
                    "list_network_requests",
                ),
            ),
        ),
        tool_guidance=(
            _tool("performance_start_trace", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("performance_stop_trace", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("navigate_page", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("list_console_messages", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("list_network_requests", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
        ),
        setup_steps=(
            "Run Chrome DevTools MCP with npx chrome-devtools-mcp@latest near the browser it should inspect.",
            "Expose it through a remote HTTPS MCP gateway protected by bearer auth.",
            "Keep tools scoped to trusted debugging agents because they can inspect active browser sessions.",
        ),
        docs_urls=(
            "https://developer.chrome.com/docs/devtools/agents/get-started",
            "https://developer.chrome.com/blog/chrome-devtools-mcp",
            "https://github.com/ChromeDevTools/chrome-devtools-mcp",
        ),
        warnings=("Chrome DevTools MCP can expose authenticated browser content to agents.",),
    ),
    _preset(
        "mongodb",
        McpPresetCategory.DATA_ANALYTICS,
        "MongoDB",
        "Official MongoDB MCP for Atlas/self-hosted databases, schema inspection, queries, and Atlas tools.",
        "MongoDB deployment required; credentials should stay in the remote gateway environment.",
        free_available=True,
        variants=(
            _variant(
                "gateway-bearer",
                "Remote gateway",
                McpPresetCompatibility.GATEWAY_REQUIRED,
                GATEWAY_BEARER_AUTH,
                url_placeholder="https://your-mongodb-mcp-gateway.example.com/mcp",
                starter_allowed_tools=(
                    "list-databases",
                    "list-collections",
                    "collection-schema",
                    "find",
                    "count",
                    "aggregate",
                ),
                risky_tools=("insert-one", "update-one", "update-many", "delete-one", "delete-many"),
            ),
        ),
        tool_guidance=(
            _tool("list-databases", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list-collections", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("collection-schema", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("find", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("count", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("aggregate", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("update-many", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("delete-many", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            (
                "Run the official MongoDB MCP server with a dedicated least-privilege MongoDB user "
                "or Atlas connection string."
            ),
            "Expose it through an HTTPS MCP gateway and protect the gateway with bearer auth.",
            "Keep database credentials in the gateway environment, not in VoxOS.",
        ),
        docs_urls=(
            "https://www.mongodb.com/docs/mcp-server/get-started/",
            "https://www.mongodb.com/products/tools/mcp-server",
            "https://github.com/mongodb-js/mongodb-mcp-server",
        ),
        warnings=("MongoDB write tools can modify or delete database records.",),
    ),
    _preset(
        "elastic",
        McpPresetCategory.DATA_ANALYTICS,
        "Elastic",
        "Elastic Agent Builder MCP for Kibana-hosted tools over Elasticsearch data.",
        "Elastic deployment or serverless project required; API key scope controls available data.",
        free_available=True,
        variants=(
            _variant(
                "agent-builder-api-key",
                "Agent Builder API key",
                McpPresetCompatibility.CUSTOM_REMOTE,
                _auth(
                    McpAuthType.API_KEY_HEADER,
                    "Authorization header",
                    "Store the full Elastic Authorization header value, including the ApiKey prefix.",
                    requires_secret=True,
                    api_key_header_name="Authorization",  # pragma: allowlist secret
                ),
                url_placeholder="https://your-kibana.example.com/api/agent_builder/mcp",
            ),
        ),
        setup_steps=(
            "Copy the Agent Builder MCP URL from Kibana, including the space path if used.",
            (
                "Create a scoped Elastic API key with Agent Builder application privileges "
                "and least-privilege index access."
            ),
            "Paste the full Authorization header value in the API key field, for example ApiKey <encoded-key>.",
        ),
        docs_urls=(
            "https://www.elastic.co/docs/explore-analyze/ai-features/agent-builder/mcp-server",
            "https://www.elastic.co/docs/explore-analyze/ai-features",
            "https://github.com/elastic/mcp-server-elasticsearch",
        ),
        warnings=("Elastic tools execute with the privileges assigned to the API key.",),
    ),
    _preset(
        "browserless",
        McpPresetCategory.BROWSER_AUTOMATION,
        "Browserless",
        "Hosted stateful browser control plus stateless scrape, search, crawl, export, and audit tools.",
        (
            "Free plan includes 1,000 units per month, two concurrent browsers, "
            "and one-minute sessions; paid plans raise the limits."
        ),
        free_available=True,
        variants=(
            _variant(
                "bearer",
                "Bearer API token",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://mcp.browserless.io/mcp",
                starter_allowed_tools=(
                    "browserless_smartscraper",
                    "browserless_search",
                    "browserless_map",
                    "browserless_performance",
                ),
                risky_tools=(
                    "browserless_agent",
                    "browserless_function",
                    "browserless_download",
                    "browserless_crawl",
                ),
            ),
        ),
        tool_guidance=(
            _tool(
                "browserless_smartscraper",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.MEDIUM,
            ),
            _tool(
                "browserless_search",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.MEDIUM,
                route_action="web.search",
                automatic_priority=100,
            ),
            _tool(
                "browserless_map",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.MEDIUM,
            ),
            _tool(
                "browserless_performance",
                recommended=True,
                read_only=True,
                risk=McpPresetToolRisk.MEDIUM,
            ),
            _tool(
                "browserless_export",
                read_only=True,
                risk=McpPresetToolRisk.MEDIUM,
            ),
            _tool(
                "browserless_agent",
                risk=McpPresetToolRisk.HIGH,
                disabled_by_default=True,
            ),
            _tool(
                "browserless_function",
                risk=McpPresetToolRisk.HIGH,
                disabled_by_default=True,
            ),
            _tool(
                "browserless_download",
                risk=McpPresetToolRisk.HIGH,
                disabled_by_default=True,
            ),
            _tool(
                "browserless_crawl",
                read_only=True,
                risk=McpPresetToolRisk.HIGH,
                disabled_by_default=True,
            ),
        ),
        setup_steps=(
            "Create a Browserless account and copy an API token from the account dashboard.",
            "Store the API token in VoxOS as bearer auth; do not put it in the MCP URL.",
            "Test discovery with the starter read-only tools.",
            ("Grant browserless_agent only to trusted agents that need logins or multi-step browser control."),
        ),
        docs_urls=(
            "https://docs.browserless.io/mcp/browserless-mcp-server/setup",
            "https://docs.browserless.io/mcp/browser-agent",
            "https://docs.browserless.io/mcp/rest-api-tools",
            "https://cloud.browserless.io/pricing",
        ),
        warnings=(
            (
                "Browserless runs the browser in its cloud and can receive page content, "
                "cookies, and credentials entered into browser sessions."
            ),
            (
                "Free-plan sessions are limited to one minute; browser time, reconnects, "
                "proxies, and CAPTCHA solving consume units."
            ),
        ),
    ),
    _preset(
        "browser-use-cloud",
        McpPresetCategory.BROWSER_AUTOMATION,
        "Browser Use Cloud",
        "Managed browser agent sessions and session inspection.",
        "Browser Use Cloud account and API key are required.",
        free_available=False,
        variants=(
            _variant(
                "api-key",
                "Cloud API key",
                McpPresetCompatibility.DIRECT_REMOTE,
                _auth(
                    McpAuthType.API_KEY_HEADER,
                    "API key header",
                    "Store the Browser Use API key in the x-browser-use-api-key header.",
                    requires_secret=True,
                    api_key_header_name="x-browser-use-api-key",  # pragma: allowlist secret
                ),
                url="https://api.browser-use.com/v3/mcp",
                starter_allowed_tools=(
                    "get_session",
                    "get_session_messages",
                    "list_sessions",
                ),
                risky_tools=("run_session", "send_task", "stop_session"),
            ),
        ),
        tool_guidance=(
            _tool("get_session", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("get_session_messages", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("list_sessions", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool(
                "list_browser_profiles",
                read_only=True,
                risk=McpPresetToolRisk.HIGH,
                disabled_by_default=True,
            ),
            _tool("run_session", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("send_task", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("stop_session", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Create a Browser Use Cloud account.",
            "Create an API key in Browser Use Cloud settings.",
            "Use Browser Accounts for reusable signed-in profiles; do not pass login credentials to MCP tools.",
            "Start with session inspection tools before allowing browser actions.",
        ),
        docs_urls=(
            "https://docs.browser-use.com/cloud/guides/mcp-server",
            "https://docs.browser-use.com/cloud/quickstart",
            "https://cloud.browser-use.com/settings?tab=api-keys&new=1",
        ),
        warnings=(
            "Browser automation can expose page content and perform actions.",
            (
                "Profile discovery and authenticated use belong in Browser Accounts, "
                "outside model-controlled MCP arguments."
            ),
        ),
    ),
    _preset(
        "browserbase",
        McpPresetCategory.BROWSER_AUTOMATION,
        "Browserbase",
        "Hosted browser automation through Browserbase and Stagehand.",
        "Browserbase account and API key are required.",
        free_available=False,
        variants=(
            _variant(
                "bearer",
                "Bearer API key",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://mcp.browserbase.com/mcp",
                starter_allowed_tools=("observe", "extract"),
                risky_tools=("start", "navigate", "act", "end"),
            ),
        ),
        tool_guidance=(
            _tool("observe", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("extract", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("start", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("navigate", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("act", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("end", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Create a Browserbase account and API key.",
            "Register the remote MCP URL and store the API key as bearer auth.",
            "Keep browser control tools limited to trusted browser agents.",
        ),
        docs_urls=(
            "https://docs.stagehand.dev/v3/integrations/mcp/setup",
            "https://www.browserbase.com",
        ),
        warnings=("Browser action tools should not be granted broadly.",),
    ),
    _preset(
        "apify",
        McpPresetCategory.SCRAPING_ACTOR_PLATFORMS,
        "Apify",
        "Actor marketplace, scraping workflows, docs search, and RAG browsing.",
        "Apify account required; usage depends on plan and Actor costs.",
        free_available=False,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.apify.com",
                starter_allowed_tools=(
                    "search-actors",
                    "fetch-actor-details",
                    "search-apify-docs",
                    "fetch-apify-docs",
                    "apify/rag-web-browser",
                    "get-actor-output",
                ),
                risky_tools=("call-actor",),
            ),
            _variant(
                "bearer",
                "Bearer API token",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://mcp.apify.com",
                starter_allowed_tools=(
                    "search-actors",
                    "fetch-actor-details",
                    "search-apify-docs",
                    "fetch-apify-docs",
                    "apify/rag-web-browser",
                    "get-actor-output",
                ),
                risky_tools=("call-actor",),
            ),
        ),
        tool_guidance=(
            _tool("search-actors", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("fetch-actor-details", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("search-apify-docs", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("fetch-apify-docs", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("apify/rag-web-browser", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("get-actor-output", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("call-actor", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Create an Apify account.",
            "Use OAuth where possible, or create an Apify API token.",
            "Keep actor execution tools disabled until cost and scope are reviewed.",
        ),
        docs_urls=(
            "https://docs.apify.com/platform/integrations/mcp",
            "https://mcp.apify.com/",
        ),
        warnings=("SSE is deprecated for Apify MCP; use Streamable HTTP.",),
    ),
    _preset(
        "github",
        McpPresetCategory.DEVELOPER_OPERATIONS,
        "GitHub",
        "Repository context, issues, pull requests, Actions, and code search.",
        "GitHub account and fine-grained PAT are required for bearer auth.",
        free_available=True,
        variants=(
            _variant(
                "readonly",
                "Read-only remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://api.githubcopilot.com/mcp/readonly",
                starter_allowed_tools=(),
                risky_tools=("create_issue", "update_issue", "create_pull_request"),
            ),
            _variant(
                "general",
                "General remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://api.githubcopilot.com/mcp/",
                starter_allowed_tools=(),
                risky_tools=("create_issue", "update_issue", "create_pull_request"),
                warnings=("Prefer the read-only URL unless a workflow needs writes.",),
            ),
        ),
        tool_guidance=(
            _tool("search_repositories", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_file_contents", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list_issues", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list_pull_requests", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("create_issue", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("update_issue", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_pull_request", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Create a fine-grained GitHub PAT with minimum repository permissions.",
            "Pick the narrowest URL variant that covers the workflow.",
            "Add write tools only for agents explicitly trusted to mutate GitHub.",
        ),
        docs_urls=(
            "https://github.com/github/github-mcp-server",
            "https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md",
        ),
    ),
    _preset(
        "sentry",
        McpPresetCategory.DEVELOPER_OPERATIONS,
        "Sentry",
        "Debugging errors, issues, stack traces, traces, and Sentry workflows.",
        "Sentry account required.",
        free_available=False,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.sentry.dev/mcp",
                starter_allowed_tools=(
                    "use_sentry",
                    "find_organizations",
                    "find_projects",
                    "search_issues",
                    "get_issue_details",
                    "get_event_stacktrace",
                ),
            ),
        ),
        tool_guidance=(
            _tool("use_sentry", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("find_organizations", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("find_projects", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("search_issues", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_issue_details", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_event_stacktrace", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
        ),
        setup_steps=(
            "Register the Sentry remote MCP URL.",
            "Complete OAuth; the endpoint advertises OAuth metadata and dynamic registration.",
            "Keep issue mutation tools disabled until needed.",
        ),
        docs_urls=(
            "https://github.com/getsentry/sentry-mcp",
            "https://docs.sentry.io/llms.txt",
            "https://mcp.sentry.dev/.well-known/oauth-authorization-server",
        ),
    ),
    _preset(
        "linear",
        McpPresetCategory.DEVELOPER_OPERATIONS,
        "Linear",
        "Issue, project, comment, team, and user workflows in Linear.",
        "Linear account required; availability can depend on plan and client.",
        free_available=False,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.linear.app/mcp",
            ),
            _variant(
                "bearer",
                "Bearer API token",
                McpPresetCompatibility.DIRECT_REMOTE,
                STATIC_BEARER_AUTH,
                url="https://mcp.linear.app/mcp",
            ),
        ),
        tool_guidance=(
            _tool("list_issues", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("get_issue", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list_projects", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list_teams", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("create_issue", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("update_issue", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("create_comment", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Register the Linear remote MCP URL.",
            "Prefer OAuth for user-scoped access.",
            "Add create, update, or comment tools only for trusted agents.",
        ),
        docs_urls=("https://linear.app/docs/mcp",),
    ),
    _preset(
        "notion",
        McpPresetCategory.KNOWLEDGE_DOCUMENTS,
        "Notion",
        "Workspace search, pages, databases, comments, users, teams, and notes.",
        "Notion account required; some features depend on Notion plan capabilities.",
        free_available=False,
        variants=(
            _variant(
                "oauth-http",
                "OAuth Streamable HTTP",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.notion.com/mcp",
                starter_allowed_tools=(
                    "notion-search",
                    "notion-fetch",
                    "notion-get-users",
                    "notion-get-teams",
                    "notion-get-comments",
                ),
            ),
            _variant(
                "oauth-sse",
                "OAuth SSE fallback",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://mcp.notion.com/sse",
                transport=McpTransportType.SSE,
                starter_allowed_tools=(
                    "notion-search",
                    "notion-fetch",
                    "notion-get-users",
                    "notion-get-teams",
                    "notion-get-comments",
                ),
                warnings=("Use only if Streamable HTTP does not work for the client.",),
            ),
        ),
        tool_guidance=(
            _tool("notion-search", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("notion-fetch", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("notion-get-users", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("notion-get-teams", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("notion-get-comments", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("notion-create-pages", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("notion-update-page", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
            _tool("notion-create-comment", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Register the Notion remote MCP URL.",
            "Complete OAuth and choose the workspace/pages to grant.",
            "Add write tools only for agents that should edit Notion.",
        ),
        docs_urls=(
            "https://developers.notion.com/docs/mcp",
            "https://developers.notion.com/guides/mcp/get-started-with-mcp",
            "https://developers.notion.com/guides/mcp/mcp-supported-tools",
            "https://developers.notion.com/guides/mcp/mcp-security-best-practices",
        ),
        warnings=("The MCP connection has the same effective access as the connected Notion user.",),
    ),
    _preset(
        "motherduck",
        McpPresetCategory.DATA_ANALYTICS,
        "MotherDuck",
        "SQL over MotherDuck and DuckDB plus catalog and documentation tools.",
        "MotherDuck account required; usage depends on plan.",
        free_available=False,
        variants=(
            _variant(
                "oauth",
                "OAuth remote",
                McpPresetCompatibility.DIRECT_REMOTE,
                OAUTH_AUTH,
                url="https://api.motherduck.com/mcp",
                starter_allowed_tools=(
                    "list_databases",
                    "list_tables",
                    "list_columns",
                    "search_catalog",
                    "query",
                    "ask_docs_question",
                ),
                risky_tools=("query_rw",),
            ),
        ),
        tool_guidance=(
            _tool("list_databases", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list_tables", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("list_columns", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("search_catalog", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("query", recommended=True, read_only=True, risk=McpPresetToolRisk.MEDIUM),
            _tool("ask_docs_question", recommended=True, read_only=True, risk=McpPresetToolRisk.LOW),
            _tool("query_rw", risk=McpPresetToolRisk.HIGH, disabled_by_default=True),
        ),
        setup_steps=(
            "Register the MotherDuck remote MCP URL.",
            "Complete OAuth for the MotherDuck account.",
            "Keep read-write tools disabled unless the agent may modify data.",
        ),
        docs_urls=(
            "https://motherduck.com/docs/sql-reference/mcp/",
            "https://github.com/motherduckdb/mcp-server-motherduck",
        ),
        warnings=("The remote MCP exposes both read-only and read-write capabilities.",),
    ),
)

MCP_SERVICE_PRESETS_BY_ID = {preset.id: preset for preset in MCP_SERVICE_PRESETS}
