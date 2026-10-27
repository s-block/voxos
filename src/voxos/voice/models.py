from pydantic import BaseModel, ConfigDict, Field, SecretStr

from voxos.providers.model_catalog import OPENAI_REALTIME_MODEL_OPTIONS
from voxos.providers.models import RealtimeVoiceConfig, RealtimeVoiceProvider


class UserVoicePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    provider: RealtimeVoiceProvider = Field(
        default=RealtimeVoiceProvider.OPENAI_REALTIME,
    )
    model: str = Field(default=OPENAI_REALTIME_MODEL_OPTIONS[0].model, min_length=1)
    voice: str = Field(default="marin", min_length=1)
    credential_ref: str = Field(min_length=1)
    provider_config: RealtimeVoiceConfig = Field(
        default_factory=RealtimeVoiceConfig,
    )


class ResolvedVoiceCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: RealtimeVoiceProvider
    reference: str = Field(min_length=1)
    token: SecretStr
