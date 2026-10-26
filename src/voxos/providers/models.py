from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

VoiceModality = Literal["text", "audio"]
DEFAULT_PREFERRED_LANGUAGE = "English"


class RealtimeVoiceProvider(StrEnum):
    OPENAI_REALTIME = "openai_realtime"
    GOOGLE_GEMINI_LIVE = "google_gemini_live"
    HUME_EVI3 = "hume_evi3"
    ELEVENLABS_CONVERSATIONAL_AI = "elevenlabs_conversational_ai"
    MISTRAL_VOXTRAL = "mistral_voxtral"
    GROK_VOICE_AGENT = "grok_voice_agent"


class RealtimeVoiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    modalities: tuple[VoiceModality, ...] = Field(default=("text", "audio"), min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    speed: float | None = Field(default=None, gt=0)
    max_session_duration_seconds: float | None = Field(default=None, gt=0)
    agent_instructions: str = Field(
        default="You are a helpful voice assistant.",
        min_length=1,
    )
    preferred_language: str = Field(
        default=DEFAULT_PREFERRED_LANGUAGE,
        min_length=1,
        max_length=128,
    )
    initial_reply_instructions: str | None = Field(
        default="Greet the user and offer your assistance.",
        min_length=1,
    )

    @field_validator(
        "base_url",
        "agent_instructions",
        "preferred_language",
        "initial_reply_instructions",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class OpenAIRealtimeConfig(RealtimeVoiceConfig):
    pass
