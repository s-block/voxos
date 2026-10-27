from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

FIREBASE_CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"


class FirebaseAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    firebase_project_id: str = Field(default="", min_length=1, alias="FIREBASE_PROJECT_ID")
    firebase_certs_url: str = Field(
        default=FIREBASE_CERTS_URL,
        min_length=1,
        alias="FIREBASE_CERTS_URL",
    )
    firebase_auth_clock_skew_seconds: int = Field(
        default=60,
        ge=0,
        le=300,
        alias="FIREBASE_AUTH_CLOCK_SKEW_SECONDS",
    )
    firebase_public_key_fetch_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        alias="FIREBASE_PUBLIC_KEY_FETCH_TIMEOUT_SECONDS",
    )
    firebase_public_key_cache_max_ttl_seconds: int = Field(
        default=86_400,
        ge=1,
        alias="FIREBASE_PUBLIC_KEY_CACHE_MAX_TTL_SECONDS",
    )
    firebase_auth_last_seen_update_interval_seconds: int = Field(
        default=300,
        ge=0,
        alias="FIREBASE_AUTH_LAST_SEEN_UPDATE_INTERVAL_SECONDS",
    )
    firebase_auth_check_revoked: bool = Field(
        default=False,
        alias="FIREBASE_AUTH_CHECK_REVOKED",
    )

    @property
    def issuer(self) -> str:
        return f"https://securetoken.google.com/{self.firebase_project_id}"
