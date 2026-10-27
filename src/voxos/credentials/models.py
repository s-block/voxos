from dataclasses import dataclass
from enum import StrEnum

from voxos.security.encryption.models import EncryptionBackend, EncryptionContext


class SecretPurpose(StrEnum):
    VOICE_PROVIDER_CREDENTIAL = "voice_provider_credential"
    TEXT_PROVIDER_CREDENTIAL = "text_provider_credential"
    MCP_SERVER_SECRET = "mcp_server_secret"
    MCP_OAUTH_STATE = "mcp_oauth_state"
    LIVEKIT_CREDENTIALS = "livekit_credentials"
    INTEGRATION_CREDENTIAL = "integration_credential"
    WHATSAPP_SESSION_DATA_KEY = "whatsapp_session_data_key"
    WHATSAPP_MOBILE_REGISTRATION_INBOX = "whatsapp_mobile_registration_inbox"
    BROWSER_PROFILE_REFERENCE = "browser_profile_reference"
    BROWSER_SESSION_REFERENCE = "browser_session_reference"
    BROWSER_ACCOUNT_CREDENTIAL = "browser_account_credential"
    CODEX_ACCOUNT_AUTH = "codex_account_auth"


@dataclass(frozen=True, slots=True)
class SecretOwner:
    user_id: str


@dataclass(frozen=True, slots=True)
class SecretReference:
    owner: SecretOwner
    provider: str
    credential_id: str
    purpose: SecretPurpose

    def encryption_context(self) -> EncryptionContext:
        return EncryptionContext(
            user_id=self.owner.user_id,
            provider=self.provider,
            credential_id=self.credential_id,
            purpose=self.purpose.value,
        )


@dataclass(frozen=True, slots=True)
class StoredSecretSummary:
    reference: SecretReference
    backend: EncryptionBackend
    key_id: str | None
    algorithm: str
