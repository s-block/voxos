import pytest

from voxos.agent_runtime.tool_execution import (
    ToolArgumentSchemaError,
    prune_unsupported_tool_arguments,
    validate_tool_arguments_schema,
)
from voxos.logos.models import ActivityIcon
from voxos.mcp.models import (
    McpAuthType,
    McpPresetCategory,
    McpPresetCompatibility,
)
from voxos.mcp.presets import (
    MCP_PRESET_CATALOG_RESEARCHED_AT,
    MCP_PRESET_CATALOG_VERSION,
    adapt_mcp_preset_tool_schema,
    find_mcp_service_preset,
    find_mcp_service_preset_variant,
    list_mcp_service_presets,
    mcp_preset_tool_activity,
    mcp_preset_tool_authority_argument_allowed_paths,
    mcp_preset_tool_description_guidance,
    mcp_preset_tool_rejection_guidance,
    normalize_mcp_preset_tool_arguments,
)


def test_mcp_preset_catalog_contains_curated_services() -> None:
    presets = list_mcp_service_presets()
    preset_ids = {preset.id for preset in presets}

    assert len(presets) >= 12
    assert {
        "atlassian",
        "aftership",
        "aws",
        "chrome-devtools",
        "elastic",
        "mcp-gateway",
        "exa",
        "tavily",
        "firecrawl",
        "flightradar24",
        "browser-use-cloud",
        "browserbase",
        "browserless",
        "apify",
        "google-workspace",
        "github",
        "google-maps-grounding-lite",
        "home-assistant",
        "hubspot",
        "microsoft-work-iq",
        "mongodb",
        "sentry",
        "linear",
        "notion",
        "openbnb",
        "paypal",
        "playwright",
        "shopify",
        "slack",
        "stripe",
        "todoist",
        "tripgo",
        "viator",
        "weather-open-meteo",
        "motherduck",
    }.issubset(preset_ids)
    assert {
        "duckduckgo-gateway",
        "brave-search-gateway",
        "playwright-gateway",
        "local-browser-use-gateway",
        "lastminute",
        "yelp",
    }.isdisjoint(preset_ids)
    assert {preset.catalog_version for preset in presets} == {
        MCP_PRESET_CATALOG_VERSION,
    }
    assert {preset.researched_at for preset in presets} == {
        MCP_PRESET_CATALOG_RESEARCHED_AT,
    }
    assert all(preset.logo.domain for preset in presets)
    assert find_mcp_service_preset("slack").logo.domain == "slack.com"
    assert find_mcp_service_preset("mcp-gateway").logo.domain == ("modelcontextprotocol.io")
    assert mcp_preset_tool_activity("exa", "web_search_exa") == (
        "Searching the web",
        ActivityIcon.WEB,
    )
    assert mcp_preset_tool_activity("exa", "web_fetch_exa") == (
        "Reading webpages",
        ActivityIcon.WEB,
    )


def test_mcp_preset_catalog_exposes_hosted_travel_services() -> None:
    google_maps = find_mcp_service_preset("google-maps-grounding-lite")
    aftership = find_mcp_service_preset("aftership")
    viator = find_mcp_service_preset("viator")
    tripgo = find_mcp_service_preset("tripgo")
    openbnb = find_mcp_service_preset("openbnb")

    assert google_maps is not None
    assert google_maps.category == McpPresetCategory.TRAVEL_LIFESTYLE
    google_maps_variant = find_mcp_service_preset_variant(google_maps, "api-key")
    assert google_maps_variant is not None
    assert google_maps_variant.url == "https://mapstools.googleapis.com/mcp"
    assert google_maps_variant.auth.auth_type == McpAuthType.API_KEY_HEADER
    assert google_maps_variant.auth.api_key_header_name == "X-Goog-Api-Key"  # pragma: allowlist secret
    assert google_maps_variant.starter_allowed_tools == (
        "search_places",
        "lookup_weather",
        "compute_routes",
        "resolve_names",
        "resolve_maps_urls",
    )

    assert aftership is not None
    aftership_variant = find_mcp_service_preset_variant(aftership, "public")
    assert aftership_variant is not None
    assert aftership_variant.url == "https://mcp.aftership.com/tracking/public"
    assert aftership_variant.starter_allowed_tools == ("track_shipment",)
    assert "get_returns_demo" in aftership_variant.risky_tools

    assert viator is not None
    viator_variant = find_mcp_service_preset_variant(viator, "public")
    assert viator_variant is not None
    assert viator_variant.url == "https://exp-app-mcp.prod.ep.viator.com/mcp"
    assert viator_variant.starter_allowed_tools == (
        "search_experiences",
        "get_experience_details",
    )

    assert tripgo is not None
    tripgo_variant = find_mcp_service_preset_variant(tripgo, "public")
    assert tripgo_variant is not None
    assert tripgo_variant.url == ("https://tripgo-mcp-server.skedgo-account.workers.dev/mcp")
    assert "tripgo-routing" in tripgo_variant.starter_allowed_tools

    assert openbnb is not None
    openbnb_variant = find_mcp_service_preset_variant(openbnb, "oauth")
    assert openbnb_variant is not None
    assert openbnb_variant.url == "https://mcp.openbnb.ai/mcp"
    assert openbnb_variant.auth.auth_type == McpAuthType.OAUTH_AUTHORIZATION_CODE
    assert openbnb_variant.auth.requires_oauth_client is False
    assert openbnb_variant.starter_allowed_tools == ()
    assert {guide.name for guide in openbnb.tool_guidance} == {
        "airbnb_search",
        "airbnb_listing",
        "maps_search_places",
        "maps_place_details",
        "maps_geocode",
        "maps_reverse_geocode",
        "maps_directions",
    }


def test_google_weather_preset_repairs_provider_schema_and_resource_names() -> None:
    provider_schema = {
        "type": "object",
        "$defs": {
            "Date": {
                "type": "object",
                "properties": {
                    "year": {"type": "integer"},
                    "month": {"type": "integer"},
                    "day": {"type": "integer"},
                },
            },
            "LatLng": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
            },
            "Location": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "placeId": {"type": "string"},
                    "latLng": {"$ref": "#/$defs/LatLng"},
                },
            },
        },
        "properties": {
            "location": {"$ref": "#/$defs/Location"},
            "date": {"$ref": "#/$defs/Date"},
            "hour": {"type": "integer"},
        },
        "required": ["location"],
    }

    schema = adapt_mcp_preset_tool_schema(
        "google-maps-grounding-lite",
        "lookup_weather",
        provider_schema,
    )

    validate_tool_arguments_schema(
        {
            "location": {"address": "London, UK"},
            "date": {"year": 2026, "month": 7, "day": 28},
        },
        schema,
    )
    with pytest.raises(ToolArgumentSchemaError):
        validate_tool_arguments_schema(
            {
                "location": {
                    "address": "London, UK",
                    "placeId": "example-place-id",
                },
            },
            schema,
        )
    with pytest.raises(ToolArgumentSchemaError):
        validate_tool_arguments_schema(
            {
                "location": {"address": "London, UK"},
                "date": {"date": "2026-07-28"},
            },
            schema,
        )
    with pytest.raises(ToolArgumentSchemaError):
        validate_tool_arguments_schema(
            {
                "location": {"address": "London, UK"},
                "placeId": "example-place-id",
            },
            schema,
        )

    arguments = {
        "location": {
            "placeId": "places/example-place-id",
        },
    }
    assert normalize_mcp_preset_tool_arguments(
        "google-maps-grounding-lite",
        "lookup_weather",
        arguments,
    ) == {
        "location": {
            "placeId": "example-place-id",
        },
    }
    assert arguments["location"] == {
        "placeId": "places/example-place-id",
    }
    assert "do not call resolve_names first" in (
        mcp_preset_tool_description_guidance(
            "google-maps-grounding-lite",
            "lookup_weather",
        )
        or ""
    )
    assert "location.address only" in (
        mcp_preset_tool_rejection_guidance(
            "google-maps-grounding-lite",
            "lookup_weather",
        )
        or ""
    )


def test_google_maps_search_places_uses_camel_case_arguments() -> None:
    arguments = {
        "text_query": "restaurants in Victoria, London",
        "region_code": "GB",
        "language_code": "en",
        "location_bias": {"circle": {"radiusMeters": 500}},
    }

    assert normalize_mcp_preset_tool_arguments(
        "google-maps-grounding-lite",
        "search_places",
        arguments,
    ) == {
        "textQuery": "restaurants in Victoria, London",
        "regionCode": "GB",
        "languageCode": "en",
        "locationBias": {"circle": {"radiusMeters": 500}},
    }
    assert arguments["text_query"] == "restaurants in Victoria, London"
    assert "required textQuery" in (
        mcp_preset_tool_description_guidance(
            "google-maps-grounding-lite",
            "search_places",
        )
        or ""
    )
    assert "Do not send snake_case" in (
        mcp_preset_tool_rejection_guidance(
            "google-maps-grounding-lite",
            "search_places",
        )
        or ""
    )


def test_google_maps_search_places_rejects_unsupported_arguments() -> None:
    provider_schema = {
        "type": "object",
        "$defs": {
            "Circle": {
                "type": "object",
                "properties": {
                    "center": {"$ref": "#/$defs/LatLng"},
                    "radiusMeters": {"type": "number"},
                },
                "required": ["center"],
            },
            "LatLng": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                },
            },
            "LocationBias": {
                "type": "object",
                "properties": {
                    "circle": {"$ref": "#/$defs/Circle"},
                },
            },
        },
        "properties": {
            "textQuery": {"type": "string"},
            "regionCode": {"type": "string"},
            "languageCode": {"type": "string"},
            "locationBias": {"$ref": "#/$defs/LocationBias"},
        },
        "required": ["textQuery"],
    }
    schema = adapt_mcp_preset_tool_schema(
        "google-maps-grounding-lite",
        "search_places",
        provider_schema,
    )
    valid_arguments = {
        "textQuery": "restaurants nearby",
        "locationBias": {
            "circle": {
                "center": {"latitude": 42.4, "longitude": 18.7},
                "radiusMeters": 5_000,
            },
        },
    }

    accepted_arguments = prune_unsupported_tool_arguments(
        {**valid_arguments, "limit": 5},
        schema,
    )

    assert accepted_arguments == valid_arguments
    validate_tool_arguments_schema(accepted_arguments, schema)

    assert provider_schema.get("additionalProperties") is None
    assert "limit" in (
        mcp_preset_tool_rejection_guidance(
            "google-maps-grounding-lite",
            "search_places",
        )
        or ""
    )


def test_openbnb_search_schema_enforces_one_date_mode() -> None:
    provider_schema = {
        "type": "object",
        "properties": {
            "location": {"type": "string"},
            "checkin": {"type": "string"},
            "checkout": {"type": "string"},
            "flexibleTripLengths": {
                "type": "array",
                "items": {"type": "string"},
            },
            "flexibleTripDates": {
                "type": "array",
                "items": {"type": "string"},
            },
            "monthlyStartDate": {"type": "string"},
            "monthlyLength": {"type": "number"},
        },
        "required": ["location"],
        "additionalProperties": False,
    }

    schema = adapt_mcp_preset_tool_schema(
        "openbnb",
        "airbnb_search",
        provider_schema,
    )

    valid_arguments = (
        {"location": "London"},
        {
            "location": "London",
            "checkin": "2026-08-07",
            "checkout": "2026-08-09",
        },
        {
            "location": "London",
            "flexibleTripLengths": ["weekend_trip"],
            "flexibleTripDates": ["august"],
        },
        {
            "location": "London",
            "monthlyStartDate": "2026-08-01",
            "monthlyLength": 1,
        },
    )
    for arguments in valid_arguments:
        validate_tool_arguments_schema(arguments, schema)

    invalid_arguments = (
        {
            "location": "London",
            "checkin": "2026-08-07",
            "checkout": "2026-08-09",
            "flexibleTripLengths": ["weekend_trip"],
        },
        {
            "location": "London",
            "flexibleTripLengths": ["weekend_trip"],
            "monthlyStartDate": "2026-08-01",
            "monthlyLength": 1,
        },
        {"location": "London", "checkin": "2026-08-07"},
        {"location": "London", "flexibleTripDates": ["august"]},
        {"location": "London", "monthlyStartDate": "August 2026", "monthlyLength": 1},
    )
    for arguments in invalid_arguments:
        with pytest.raises(ToolArgumentSchemaError):
            validate_tool_arguments_schema(arguments, schema)

    assert provider_schema["properties"]["checkin"] == {"type": "string"}
    assert "mutually exclusive" in (mcp_preset_tool_description_guidance("openbnb", "airbnb_search") or "")
    assert "exactly one date mode" in (mcp_preset_tool_rejection_guidance("openbnb", "airbnb_search") or "")


def test_flightradar24_preset_filters_local_compose_by_trusted_endpoint() -> None:
    public_catalog = {preset.id: preset for preset in list_mcp_service_presets()}
    local_catalog = {
        preset.id: preset
        for preset in list_mcp_service_presets(
            trusted_endpoint_urls=frozenset(
                {"http://flightradar24-mcp:8000/mcp"},
            ),
        )
    }

    public_variants = public_catalog["flightradar24"].variants
    local_variants = local_catalog["flightradar24"].variants
    assert tuple(variant.id for variant in public_variants) == ("deployed-https",)
    assert tuple(variant.id for variant in local_variants) == (
        "local-compose",
        "deployed-https",
    )
    assert local_variants[0].url == "http://flightradar24-mcp:8000/mcp"
    assert local_variants[0].trusted_private_host == "flightradar24-mcp"
    assert "get_flight_summary_light" in local_variants[0].starter_allowed_tools
    assert "get_flight_summary_light" in local_variants[1].starter_allowed_tools
    assert local_variants[1].compatibility == McpPresetCompatibility.CUSTOM_REMOTE
    assert local_variants[1].url is None


def test_monzo_preset_uses_endpoint_bearer_and_brokered_provider_oauth() -> None:
    public_catalog = {preset.id: preset for preset in list_mcp_service_presets()}
    assert "monzo" not in public_catalog

    local_catalog = {
        preset.id: preset
        for preset in list_mcp_service_presets(
            trusted_endpoint_urls=frozenset({"http://monzo-mcp:8000/mcp"}),
        )
    }
    monzo = local_catalog["monzo"]
    variant = find_mcp_service_preset_variant(monzo, "local-compose")

    assert monzo.category == McpPresetCategory.PAYMENTS_COMMERCE
    assert monzo.logo.domain == "monzo.com"
    assert variant is not None
    assert variant.url == "http://monzo-mcp:8000/mcp"
    assert variant.trusted_private_host == "monzo-mcp"
    assert variant.auth.auth_type == McpAuthType.STATIC_BEARER
    assert variant.auth.requires_secret is True
    assert variant.auth.requires_provider_oauth is True
    assert variant.auth.provider_oauth_label == "Connect Monzo"
    assert variant.starter_allowed_tools == ()
    assert set(variant.risky_tools) == {
        "monzo_annotate_transaction",
        "monzo_deposit_into_pot",
        "monzo_withdraw_from_pot",
    }
    guidance = {tool.name: tool for tool in monzo.tool_guidance}
    assert guidance["monzo_connection_status"].risk.value == "low"
    assert guidance["monzo_list_transactions"].risk.value == "high"
    assert guidance["monzo_withdraw_from_pot"].disabled_by_default is True


def test_paypal_preset_requires_explicit_mutation_tool_grants() -> None:
    paypal = find_mcp_service_preset("paypal")

    assert paypal is not None
    assert paypal.category == McpPresetCategory.PAYMENTS_COMMERCE
    oauth = find_mcp_service_preset_variant(paypal, "oauth")
    assert oauth is not None
    assert oauth.url == "https://mcp.paypal.com/mcp"
    assert oauth.auth.auth_type == McpAuthType.OAUTH_AUTHORIZATION_CODE
    assert oauth.auth.requires_oauth_client is False
    assert oauth.starter_allowed_tools == ()
    guidance_by_name = {tool.name: tool for tool in paypal.tool_guidance}
    assert guidance_by_name["list_invoices"].recommended is True
    assert guidance_by_name["list_invoices"].read_only is True
    assert guidance_by_name["pay_order"].risk.value == "high"
    assert guidance_by_name["pay_order"].disabled_by_default is True


def test_mcp_preset_catalog_exposes_safe_direct_service_defaults() -> None:
    exa = find_mcp_service_preset("exa")

    assert exa is not None
    assert exa.category == McpPresetCategory.WEB_SEARCH_RESEARCH
    assert exa.free_available is True
    keyless = find_mcp_service_preset_variant(exa, "keyless")
    api_key = find_mcp_service_preset_variant(exa, "api-key")
    assert keyless is not None
    assert keyless.compatibility == McpPresetCompatibility.DIRECT_REMOTE
    assert keyless.url == "https://mcp.exa.ai/mcp"
    assert keyless.auth.auth_type == McpAuthType.NONE
    assert keyless.optional_auth is not None
    assert keyless.optional_auth.auth_type == McpAuthType.API_KEY_HEADER
    assert keyless.optional_auth.api_key_header_name == "x-api-key"  # pragma: allowlist secret
    assert keyless.starter_allowed_tools == (
        "web_search_exa",
        "web_fetch_exa",
    )
    assert api_key is not None
    assert api_key.auth.auth_type == McpAuthType.API_KEY_HEADER
    assert api_key.auth.api_key_header_name == "x-api-key"  # pragma: allowlist secret
    guidance = {tool.name: tool for tool in exa.tool_guidance}
    assert guidance["web_search_exa"].route_action == "web.search"
    assert guidance["web_search_exa"].automatic_priority == 10
    assert guidance["web_fetch_exa"].route_action is None


def test_browserless_preset_starts_read_only_and_gates_browser_control() -> None:
    browserless = find_mcp_service_preset("browserless")

    assert browserless is not None
    assert browserless.category == McpPresetCategory.BROWSER_AUTOMATION
    assert browserless.free_available is True
    bearer = find_mcp_service_preset_variant(browserless, "bearer")
    assert bearer is not None
    assert bearer.compatibility == McpPresetCompatibility.DIRECT_REMOTE
    assert bearer.url == "https://mcp.browserless.io/mcp"
    assert bearer.auth.auth_type == McpAuthType.STATIC_BEARER
    assert bearer.auth.requires_secret is True
    assert bearer.starter_allowed_tools == (
        "browserless_smartscraper",
        "browserless_search",
        "browserless_map",
        "browserless_performance",
    )
    assert set(bearer.risky_tools) == {
        "browserless_agent",
        "browserless_function",
        "browserless_download",
        "browserless_crawl",
    }
    guidance = {tool.name: tool for tool in browserless.tool_guidance}
    assert guidance["browserless_smartscraper"].recommended is True
    assert guidance["browserless_smartscraper"].route_action is None
    assert guidance["browserless_search"].route_action == "web.search"
    assert guidance["browserless_search"].automatic_priority == 100
    assert guidance["browserless_smartscraper"].read_only is True
    assert guidance["browserless_agent"].risk.value == "high"
    assert guidance["browserless_agent"].disabled_by_default is True
    assert guidance["browserless_function"].disabled_by_default is True
    assert mcp_preset_tool_authority_argument_allowed_paths(
        "browserless",
        "browserless_smartscraper",
    ) == frozenset({("url",)})
    assert mcp_preset_tool_authority_argument_allowed_paths(
        "browserless",
        "browserless_agent",
    ) == frozenset({("commands", "*", "params", "url")})
    assert (
        mcp_preset_tool_authority_argument_allowed_paths(
            "custom-server",
            "browserless_smartscraper",
        )
        == frozenset()
    )


def test_mcp_preset_catalog_exposes_single_generic_gateway() -> None:
    gateway_preset = find_mcp_service_preset("mcp-gateway")

    assert gateway_preset is not None
    assert gateway_preset.category == McpPresetCategory.MCP_GATEWAYS
    http_bearer = find_mcp_service_preset_variant(gateway_preset, "http-bearer")
    http_none = find_mcp_service_preset_variant(gateway_preset, "http-none")
    sse_bearer = find_mcp_service_preset_variant(gateway_preset, "sse-bearer")
    assert http_bearer is not None
    assert http_bearer.compatibility == McpPresetCompatibility.GATEWAY_REQUIRED
    assert http_bearer.url is None
    assert http_bearer.auth.auth_type == McpAuthType.STATIC_BEARER
    assert http_bearer.starter_allowed_tools == ()
    assert http_none is not None
    assert http_none.auth.auth_type == McpAuthType.NONE
    assert sse_bearer is not None
    assert sse_bearer.transport.value == "sse"
    assert "discovery" in " ".join(gateway_preset.setup_steps).lower()


def test_mcp_preset_catalog_supports_oauth_client_presets() -> None:
    google = find_mcp_service_preset("google-workspace")

    assert google is not None
    gmail = find_mcp_service_preset_variant(google, "gmail")
    assert gmail is not None
    assert gmail.compatibility == McpPresetCompatibility.DIRECT_REMOTE
    assert gmail.auth.auth_type == McpAuthType.OAUTH_AUTHORIZATION_CODE
    assert gmail.auth.requires_oauth_client is True
    assert gmail.auth.oauth_token_endpoint_auth_method == "client_secret_post"
    assert "https://www.googleapis.com/auth/gmail.readonly" in gmail.auth.oauth_scopes


def test_mcp_preset_catalog_supports_custom_remote_urls() -> None:
    shopify = find_mcp_service_preset("shopify")

    assert shopify is not None
    storefront = find_mcp_service_preset_variant(shopify, "storefront")
    assert storefront is not None
    assert storefront.compatibility == McpPresetCompatibility.CUSTOM_REMOTE
    assert storefront.url is None
    assert storefront.url_placeholder == "https://your-store.myshopify.com/api/mcp"
