from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel

from voxos.db.models import new_record_id, utc_now
from voxos.integrations.models import (
    IntegrationAuthType,
    IntegrationCallStatus,
    IntegrationCredentialStatus,
    IntegrationDirection,
    IntegrationEventStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
    IntegrationResourceStatus,
    IntegrationRouteStatus,
    IntegrationStatus,
    IntegrationTranscriptStatus,
)


class UserIntegrationRecord(SQLModel, table=True):
    __tablename__ = "user_integrations"
    __table_args__ = (
        Index(
            "uq_user_integrations_active_label",
            "user_id",
            "label",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_user_integrations_user_provider_status", "user_id", "provider", "status"),
        Index("ix_user_integrations_user_enabled", "user_id", "enabled"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    label: str = Field(min_length=1, max_length=256)
    status: str = Field(
        default=IntegrationStatus.NEEDS_AUTH.value,
        sa_column=Column(String(32), nullable=False),
    )
    enabled: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    auth_type: str = Field(
        default=IntegrationAuthType.API_KEY.value,
        sa_column=Column(String(64), nullable=False),
    )
    setup_state: str = Field(default="created", min_length=1, max_length=64)
    default_agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    safe_metadata: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    last_checked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    status_message: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class IntegrationCredentialRecord(SQLModel, table=True):
    __tablename__ = "integration_credentials"
    __table_args__ = (
        Index("ix_integration_credentials_user_integration", "user_id", "integration_id"),
        Index("ix_integration_credentials_user_kind", "user_id", "provider", "secret_kind"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    secret_kind: str = Field(min_length=1, max_length=64)
    status: str = Field(
        default=IntegrationCredentialStatus.ACTIVE.value,
        sa_column=Column(String(64), nullable=False),
    )
    public_identifier: str | None = Field(default=None, min_length=1, max_length=256)
    last_four: str | None = Field(default=None, min_length=1, max_length=8)
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    safe_metadata: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class IntegrationResourceRecord(SQLModel, table=True):
    __tablename__ = "integration_resources"
    __table_args__ = (
        Index(
            "uq_integration_resources_active_external",
            "user_id",
            "provider",
            "resource_type",
            "external_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("ix_integration_resources_user_integration", "user_id", "integration_id"),
        Index("ix_integration_resources_user_status", "user_id", "provider", "status"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    resource_type: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=256)
    status: str = Field(
        default=IntegrationResourceStatus.CONNECTED.value,
        sa_column=Column(String(64), nullable=False),
    )
    capabilities: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    safe_metadata: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    retained_metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class IntegrationWebhookRouteRecord(SQLModel, table=True):
    __tablename__ = "integration_webhook_routes"
    __table_args__ = (
        UniqueConstraint("provider", "route_key_hash", name="uq_integration_webhook_route_hash"),
        Index("ix_integration_webhook_routes_user_integration", "user_id", "integration_id"),
        Index("ix_integration_webhook_routes_resource_purpose", "resource_id", "purpose"),
        Index(
            "uq_integration_webhook_routes_active_resource_purpose",
            "provider",
            "resource_id",
            "purpose",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    resource_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    purpose: str = Field(min_length=1, max_length=64)
    route_key_hash: str = Field(min_length=64, max_length=64)
    status: str = Field(
        default=IntegrationRouteStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    rotated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    disabled_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class IntegrationEventRecord(SQLModel, table=True):
    __tablename__ = "integration_events"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "idempotency_key", name="uq_integration_events_idempotency"),
        Index("ix_integration_events_user_integration_created", "user_id", "integration_id", "created_at"),
        Index("ix_integration_events_user_resource_created", "user_id", "resource_id", "created_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    resource_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    event_type: str = Field(min_length=1, max_length=128)
    direction: str = Field(
        default=IntegrationDirection.UNKNOWN.value,
        sa_column=Column(String(32), nullable=False),
    )
    external_event_id: str | None = Field(default=None, min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=512)
    status: str = Field(
        default=IntegrationEventStatus.ACCEPTED.value,
        sa_column=Column(String(32), nullable=False),
    )
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    safe_summary_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    error_type: str | None = Field(default=None, min_length=1, max_length=128)
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    received_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    processed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class IntegrationMessageRecord(SQLModel, table=True):
    __tablename__ = "integration_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "external_message_id", name="uq_integration_messages_external"),
        Index("ix_integration_messages_user_resource_created", "user_id", "resource_id", "created_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    resource_id: str = Field(index=True, min_length=1, max_length=36)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    direction: str = Field(sa_column=Column(String(32), nullable=False))
    external_message_id: str = Field(min_length=1, max_length=256)
    from_number: str = Field(min_length=1, max_length=32)
    to_number: str = Field(min_length=1, max_length=32)
    contact_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    contact_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    body: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="unknown", min_length=1, max_length=64)
    segments: int | None = Field(default=None, ge=0)
    price: str | None = Field(default=None, max_length=64)
    price_unit: str | None = Field(default=None, max_length=16)
    sent_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    received_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class IntegrationConversationRecord(SQLModel, table=True):
    __tablename__ = "integration_conversations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "integration_id",
            "resource_id",
            "medium",
            "remote_peer",
            "agent_id",
            name="uq_integration_conversations_peer",
        ),
        Index(
            "ix_integration_conversations_user_resource_peer",
            "user_id",
            "resource_id",
            "medium",
            "remote_peer",
        ),
        Index(
            "ix_integration_conversations_user_expires",
            "user_id",
            "expires_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    resource_id: str = Field(index=True, min_length=1, max_length=36)
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    medium: str = Field(sa_column=Column(String(32), nullable=False))
    remote_peer: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    runtime_session_id: str = Field(index=True, min_length=1, max_length=36)
    last_interaction_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class IntegrationReplyRecord(SQLModel, table=True):
    __tablename__ = "integration_replies"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "inbound_message_id",
            name="uq_integration_replies_inbound_message",
        ),
        Index(
            "ix_integration_replies_user_status_updated",
            "user_id",
            "status",
            "updated_at",
        ),
        Index(
            "ix_integration_replies_user_conversation_created",
            "user_id",
            "conversation_id",
            "created_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    inbound_message_id: str = Field(index=True, min_length=1, max_length=36)
    conversation_id: str = Field(index=True, min_length=1, max_length=36)
    agent_run_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    outbound_message_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    status: str = Field(
        default=IntegrationReplyStatus.PENDING.value,
        sa_column=Column(String(32), nullable=False),
    )
    response_profile: str = Field(min_length=1, max_length=64)
    generated_body: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    encoding: str | None = Field(default=None, min_length=1, max_length=32)
    estimated_segments: int | None = Field(
        default=None,
        ge=0,
        sa_column=Column(Integer, nullable=True),
    )
    condensation_attempted: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    overflow_fallback_used: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    suppression_reason: str | None = Field(default=None, min_length=1, max_length=128)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    inbound_transcript_recorded_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    outbound_transcript_recorded_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class IntegrationCallSessionRecord(SQLModel, table=True):
    __tablename__ = "integration_call_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", "external_call_id", name="uq_integration_call_sessions_external"),
        Index("ix_integration_call_sessions_user_resource_created", "user_id", "resource_id", "created_at"),
        Index(
            "ix_integration_call_sessions_user_runtime_session",
            "user_id",
            "runtime_session_id",
        ),
        CheckConstraint(
            "opening_state IS NULL OR opening_state IN "
            "('pending', 'claimed', 'scheduled', 'completed', 'interrupted', 'failed')",
            name="ck_integration_call_sessions_opening_state",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    resource_id: str = Field(index=True, min_length=1, max_length=36)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    direction: str = Field(
        default=IntegrationDirection.UNKNOWN.value,
        sa_column=Column(String(32), nullable=False),
    )
    external_call_id: str = Field(min_length=1, max_length=256)
    from_number: str | None = Field(default=None, max_length=32)
    to_number: str | None = Field(default=None, max_length=32)
    status: str = Field(
        default=IntegrationCallStatus.INITIATING.value,
        sa_column=Column(String(64), nullable=False),
    )
    contact_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    contact_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    caller_is_admin: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    agent_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    runtime_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    initiating_run_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    livekit_room_name: str | None = Field(default=None, min_length=1, max_length=512)
    media_participant_identity: str | None = Field(default=None, min_length=1, max_length=128)
    media_stream_id: str | None = Field(default=None, min_length=1, max_length=256)
    outbound_objective: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    opening_state: str | None = Field(
        default=None,
        sa_column=Column(String(32), nullable=True),
    )
    opening_attempt_count: int | None = Field(
        default=None,
        ge=0,
        sa_column=Column(Integer, nullable=True),
    )
    transcript_enabled: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    transcript_status: str = Field(
        default=IntegrationTranscriptStatus.DISABLED.value,
        sa_column=Column(String(64), nullable=False),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    answered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    worker_registered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    agent_ready_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    provider_dial_requested_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    media_ready_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    opening_claimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    opening_scheduled_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    first_agent_speech_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    opening_completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class IntegrationCallTranscriptRecord(SQLModel, table=True):
    __tablename__ = "integration_call_transcripts"
    __table_args__ = (
        Index("ix_integration_call_transcripts_user_call_created", "user_id", "call_session_id", "created_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    call_session_id: str = Field(index=True, min_length=1, max_length=36)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    source: str = Field(min_length=1, max_length=64)
    speaker: str = Field(min_length=1, max_length=64)
    text: str = Field(sa_column=Column(Text, nullable=False))
    is_final: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    confidence: float | None = Field(default=None, ge=0, le=1)
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class IntegrationAuditLogRecord(SQLModel, table=True):
    __tablename__ = "integration_audit_logs"
    __table_args__ = (
        Index("ix_integration_audit_user_integration_created", "user_id", "integration_id", "created_at"),
        Index("ix_integration_audit_user_action_created", "user_id", "action", "created_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    provider: str = Field(
        default=IntegrationProviderType.TWILIO.value,
        sa_column=Column(String(64), nullable=False),
    )
    action: str = Field(min_length=1, max_length=128)
    actor_type: str = Field(min_length=1, max_length=64)
    actor_id: str | None = Field(default=None, min_length=1, max_length=256)
    outcome: str = Field(min_length=1, max_length=64)
    safe_metadata: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
