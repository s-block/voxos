from urllib.parse import urlparse

from voxos.logos.models import BrandLogo

VOICE_PROVIDER_LOGOS: dict[str, BrandLogo] = {
    "openai_realtime": BrandLogo(domain="openai.com", label="OpenAI"),
    "google_gemini_live": BrandLogo(domain="google.com", label="Google Gemini"),
    "hume_evi3": BrandLogo(domain="hume.ai", label="Hume"),
    "elevenlabs_conversational_ai": BrandLogo(
        domain="elevenlabs.io",
        label="ElevenLabs",
    ),
    "mistral_voxtral": BrandLogo(domain="mistral.ai", label="Mistral"),
    "grok_voice_agent": BrandLogo(domain="x.ai", label="xAI"),
}

TEXT_PROVIDER_LOGOS: dict[str, BrandLogo] = {
    "openai": BrandLogo(domain="openai.com", label="OpenAI"),
    "openai_compatible": BrandLogo(
        domain="openai.com",
        label="OpenAI compatible",
    ),
    "xai": BrandLogo(domain="x.ai", label="xAI"),
    "nvidia": BrandLogo(domain="nvidia.com", label="NVIDIA"),
}

INTEGRATION_PROVIDER_LOGOS: dict[str, BrandLogo] = {
    "telnyx": BrandLogo(domain="telnyx.com", label="Telnyx"),
    "twilio": BrandLogo(domain="twilio.com", label="Twilio"),
    "whatsapp": BrandLogo(domain="whatsapp.com", label="WhatsApp"),
}

MCP_PRESET_LOGOS: dict[str, BrandLogo] = {
    "aftership": BrandLogo(domain="aftership.com", label="AfterShip"),
    "apify": BrandLogo(domain="apify.com", label="Apify"),
    "atlassian": BrandLogo(domain="atlassian.com", label="Atlassian"),
    "aws": BrandLogo(domain="aws.amazon.com", label="AWS"),
    "browser-use-cloud": BrandLogo(domain="browser-use.com", label="Browser Use"),
    "browserbase": BrandLogo(domain="browserbase.com", label="Browserbase"),
    "browserless": BrandLogo(domain="browserless.io", label="Browserless"),
    "chrome-devtools": BrandLogo(domain="google.com", label="Chrome DevTools"),
    "elastic": BrandLogo(domain="elastic.co", label="Elastic"),
    "exa": BrandLogo(domain="exa.ai", label="Exa"),
    "firecrawl": BrandLogo(domain="firecrawl.dev", label="Firecrawl"),
    "flightradar24": BrandLogo(domain="flightradar24.com", label="Flightradar24"),
    "github": BrandLogo(domain="github.com", label="GitHub"),
    "google-maps-grounding-lite": BrandLogo(domain="google.com", label="Google Maps"),
    "google-workspace": BrandLogo(domain="google.com", label="Google Workspace"),
    "home-assistant": BrandLogo(domain="home-assistant.io", label="Home Assistant"),
    "hubspot": BrandLogo(domain="hubspot.com", label="HubSpot"),
    "linear": BrandLogo(domain="linear.app", label="Linear"),
    "mcp-gateway": BrandLogo(domain="modelcontextprotocol.io", label="MCP Gateway"),
    "microsoft-work-iq": BrandLogo(domain="microsoft.com", label="Microsoft"),
    "monzo": BrandLogo(domain="monzo.com", label="Monzo"),
    "mongodb": BrandLogo(domain="mongodb.com", label="MongoDB"),
    "motherduck": BrandLogo(domain="motherduck.com", label="MotherDuck"),
    "notion": BrandLogo(domain="notion.so", label="Notion"),
    "openbnb": BrandLogo(domain="openbnb.ai", label="OpenBnB"),
    "paypal": BrandLogo(domain="paypal.com", label="PayPal"),
    "playwright": BrandLogo(domain="playwright.dev", label="Playwright"),
    "sentry": BrandLogo(domain="sentry.io", label="Sentry"),
    "shopify": BrandLogo(domain="shopify.com", label="Shopify"),
    "slack": BrandLogo(domain="slack.com", label="Slack"),
    "stripe": BrandLogo(domain="stripe.com", label="Stripe"),
    "tavily": BrandLogo(domain="tavily.com", label="Tavily"),
    "todoist": BrandLogo(domain="todoist.com", label="Todoist"),
    "tripgo": BrandLogo(domain="skedgo.com", label="TripGo"),
    "viator": BrandLogo(domain="viator.com", label="Viator"),
    "weather-open-meteo": BrandLogo(domain="open-meteo.com", label="Open-Meteo"),
}

LIVEKIT_LOGO = BrandLogo(domain="livekit.io", label="LiveKit")


def logo_for_voice_provider(provider: str) -> BrandLogo:
    return VOICE_PROVIDER_LOGOS[provider]


def logo_for_text_provider(provider: str) -> BrandLogo:
    return TEXT_PROVIDER_LOGOS[provider]


def logo_for_integration_provider(provider: str) -> BrandLogo:
    return INTEGRATION_PROVIDER_LOGOS[provider]


def logo_for_mcp_preset(preset_id: str) -> BrandLogo:
    return MCP_PRESET_LOGOS[preset_id]


def logo_from_url(url: str, label: str) -> BrandLogo | None:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname is None:
        return None
    try:
        return BrandLogo(domain=hostname, label=label)
    except ValueError:
        return None
