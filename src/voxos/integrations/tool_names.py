import re
from hashlib import sha256

TWILIO_SEND_SMS_TOOL_NAME = "twilio_send_sms"
TWILIO_PLACE_CALL_TOOL_NAME = "twilio_place_call"
WHATSAPP_SEND_MESSAGE_TOOL_NAME = "whatsapp_send_message"
INTEGRATION_PROVIDER_TOOL_PREFIX = "twilio"
INTEGRATION_PROVIDER_TOOL_NAME_MAX_LENGTH = 64
UNSAFE_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")


def provider_integration_tool_name(
    *,
    integration_id: str,
    resource_id: str,
    action: str,
) -> str:
    digest = sha256(f"{integration_id}:{resource_id}:{action}".encode("utf-8")).hexdigest()[:10]
    suffix = safe_provider_tool_suffix(action)
    prefix = f"{INTEGRATION_PROVIDER_TOOL_PREFIX}_{digest}_"
    suffix_length = INTEGRATION_PROVIDER_TOOL_NAME_MAX_LENGTH - len(prefix)
    return f"{prefix}{suffix[:suffix_length]}"


def safe_provider_tool_suffix(tool_name: str) -> str:
    safe_name = UNSAFE_TOOL_NAME_CHARS.sub("_", tool_name).strip("_")
    return safe_name or "tool"
