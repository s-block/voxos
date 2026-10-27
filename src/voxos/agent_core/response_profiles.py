from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CommunicationMedium(StrEnum):
    WEB_CHAT = "web_chat"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    VOICE = "voice"


class ResponseProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    medium: CommunicationMedium
    version: str = Field(min_length=1, max_length=64)
    supports_markdown: bool
    max_recent_messages: int = Field(ge=1, le=200)
    max_history_characters: int = Field(ge=1, le=1_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=8192)
    instruction: str = Field(min_length=1)

    @property
    def identifier(self) -> str:
        return f"{self.medium.value}:{self.version}"


PROFILE_VERSION = "v1"

RESPONSE_PROFILES: dict[CommunicationMedium, ResponseProfile] = {
    CommunicationMedium.WEB_CHAT: ResponseProfile(
        medium=CommunicationMedium.WEB_CHAT,
        version=PROFILE_VERSION,
        supports_markdown=True,
        max_recent_messages=40,
        max_history_characters=80_000,
        max_output_tokens=2048,
        instruction=(
            "# Communication Medium Contract\n"
            "- Medium: web chat.\n"
            "- Match the response depth to the request; concise answers are preferred for simple questions, "
            "while explicit requests for detail may receive a longer answer."
        ),
    ),
    CommunicationMedium.SMS: ResponseProfile(
        medium=CommunicationMedium.SMS,
        version=PROFILE_VERSION,
        supports_markdown=False,
        max_recent_messages=24,
        max_history_characters=32_000,
        max_output_tokens=180,
        instruction=(
            "# Communication Medium Contract\n"
            "- Medium: SMS.\n"
            "- Be direct and default to one or two short sentences. Aim for one SMS segment and normally stay "
            "within two segments.\n"
            "- Avoid unnecessary emoji and decorative Unicode. Preserve the user's language and any meaningful "
            "characters.\n"
            "- If the user explicitly needs more detail, give the most useful concise answer and offer to continue "
            "instead of sending a long block of text.\n"
            "- If a required tool is unavailable, state that briefly or ask one focused follow-up question; never "
            "silently omit the reply."
        ),
    ),
    CommunicationMedium.WHATSAPP: ResponseProfile(
        medium=CommunicationMedium.WHATSAPP,
        version=PROFILE_VERSION,
        supports_markdown=False,
        max_recent_messages=32,
        max_history_characters=64_000,
        max_output_tokens=512,
        instruction=(
            "# Communication Medium Contract\n"
            "- Medium: WhatsApp. Write for a mobile conversation using short paragraphs or a few compact bullets; "
            "do not use wide tables.\n"
            "- Default to one to three short paragraphs and roughly 100 words or fewer. Expand only when the user "
            "explicitly asks for detail or correctness requires it.\n"
            "- Put the answer first and offer to continue rather than sending an unnecessarily long message."
        ),
    ),
    CommunicationMedium.VOICE: ResponseProfile(
        medium=CommunicationMedium.VOICE,
        version=PROFILE_VERSION,
        supports_markdown=False,
        max_recent_messages=40,
        max_history_characters=64_000,
        max_output_tokens=160,
        instruction=(
            "# Communication Medium Contract\n"
            "- Medium: spoken voice. Use natural, speakable language with no raw URLs.\n"
            "- Default to one or two conversational sentences, usually about 30 to 50 spoken words, and cover one "
            "main idea per turn.\n"
            "- If more detail would help, give the key point first and ask whether the caller wants you to continue.\n"
            "- Do not cut an answer off mid-sentence."
        ),
    ),
}


def response_profile(medium: CommunicationMedium) -> ResponseProfile:
    return RESPONSE_PROFILES[medium]


def communication_medium_for_channel(channel: str) -> CommunicationMedium:
    normalized = channel.strip().lower()
    if normalized in {"sms", "text_message"}:
        return CommunicationMedium.SMS
    if normalized == "whatsapp":
        return CommunicationMedium.WHATSAPP
    if normalized in {"voice", "livekit", "realtime_voice"}:
        return CommunicationMedium.VOICE
    return CommunicationMedium.WEB_CHAT


def channel_for_communication_medium(medium: CommunicationMedium) -> str:
    if medium == CommunicationMedium.WEB_CHAT:
        return "text"
    if medium == CommunicationMedium.VOICE:
        return "livekit"
    return medium.value


def render_response_profile_instruction(medium: CommunicationMedium) -> str:
    profile = response_profile(medium)
    formatting_instruction = (
        "- Markdown is supported; use it only when it materially improves clarity. When citing web sources, "
        "use descriptive Markdown links with complete https:// URLs instead of bare domains or raw URLs."
        if profile.supports_markdown
        else (
            "- Reply in plain text only. Do not use Markdown syntax, including headings, tables, code fences, "
            "or emphasis markers such as asterisks and underscores."
        )
    )
    return f"{profile.instruction}\n{formatting_instruction}\n- Response profile: {profile.identifier}."


def response_profile_prompt_snapshot(
    base_instructions: str,
    medium: CommunicationMedium,
) -> str:
    profile_marker = f"Response profile: {response_profile(medium).identifier}."
    stripped = base_instructions.strip()
    return profile_marker if not stripped else f"{stripped}\n\n{profile_marker}"
