from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from voxos.db.models import new_record_id, utc_now
from voxos.whatsapp.models import (
    WhatsAppContactResolutionState,
    WhatsAppDesiredState,
    WhatsAppLinkMethod,
    WhatsAppMobileRegistrationState,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)


class WhatsAppContactIdentityRecord(SQLModel, table=True):
    __tablename__ = "whatsapp_contact_identities"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "integration_id",
            "pn_jid",
            name="uq_whatsapp_contact_identities_pn",
        ),
        UniqueConstraint(
            "user_id",
            "integration_id",
            "lid_jid",
            name="uq_whatsapp_contact_identities_lid",
        ),
        UniqueConstraint(
            "user_id",
            "integration_id",
            "username",
            name="uq_whatsapp_contact_identities_username",
        ),
        Index(
            "ix_whatsapp_contact_identities_user_integration",
            "user_id",
            "integration_id",
        ),
        Index(
            "ix_whatsapp_contact_identities_user_contact",
            "user_id",
            "contact_id",
        ),
        CheckConstraint(
            "pn_jid IS NOT NULL OR lid_jid IS NOT NULL OR username IS NOT NULL",
            name="ck_whatsapp_contact_identities_has_identifier",
        ),
        CheckConstraint(
            "resolution_state IN ('unlinked', 'linked', 'conflict')",
            name="ck_whatsapp_contact_identities_resolution_state",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(min_length=1, max_length=36)
    contact_id: str | None = Field(default=None, min_length=1, max_length=36)
    phone_number: str | None = Field(default=None, min_length=2, max_length=16)
    pn_jid: str | None = Field(default=None, min_length=1, max_length=128)
    lid_jid: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=256)
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    resolution_state: str = Field(
        default=WhatsAppContactResolutionState.UNLINKED.value,
        sa_column=Column(String(32), nullable=False),
    )
    first_seen_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_seen_at: datetime = Field(
        default_factory=utc_now,
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


class WhatsAppMobileRegistrationRecord(SQLModel, table=True):
    __tablename__ = "whatsapp_mobile_registrations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            name="uq_whatsapp_mobile_registrations_user",
        ),
        UniqueConstraint(
            "user_id",
            "source_resource_id",
            name="uq_whatsapp_mobile_registrations_source_resource",
        ),
        Index(
            "ix_whatsapp_mobile_registrations_capture",
            "user_id",
            "source_resource_id",
            "capture_expires_at",
        ),
        CheckConstraint(
            "state IN ('armed', 'armed_voice', 'code_received', 'expired')",
            name="ck_whatsapp_mobile_registrations_state",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    source_integration_id: str = Field(index=True, min_length=1, max_length=36)
    source_resource_id: str = Field(index=True, min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=64)
    phone_number: str = Field(min_length=1, max_length=32)
    state: str = Field(
        default=WhatsAppMobileRegistrationState.ARMED.value,
        sa_column=Column(String(32), nullable=False),
    )
    capture_expires_at: datetime = Field(
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


class WhatsAppSessionRecord(SQLModel, table=True):
    __tablename__ = "whatsapp_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_whatsapp_sessions_user"),
        UniqueConstraint("integration_id", name="uq_whatsapp_sessions_integration"),
        UniqueConstraint("resource_id", name="uq_whatsapp_sessions_resource"),
        UniqueConstraint(
            "user_id",
            "source_resource_id",
            name="uq_whatsapp_sessions_source_resource",
        ),
        Index(
            "ix_whatsapp_sessions_worker_demand",
            "desired_state",
            "state",
            "updated_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    resource_id: str = Field(index=True, min_length=1, max_length=36)
    source_resource_id: str = Field(index=True, min_length=1, max_length=36)
    credential_id: str = Field(min_length=1, max_length=36)
    desired_state: str = Field(
        default=WhatsAppDesiredState.CONNECTED.value,
        sa_column=Column(String(32), nullable=False),
    )
    state: str = Field(
        default=WhatsAppSessionState.AWAITING_LINK.value,
        sa_column=Column(String(32), nullable=False),
    )
    link_method: str = Field(
        default=WhatsAppLinkMethod.QR.value,
        sa_column=Column(String(32), nullable=False),
    )
    phone_number: str = Field(min_length=1, max_length=32)
    challenge_ciphertext: bytes | None = Field(
        default=None,
        sa_column=Column(LargeBinary, nullable=True),
    )
    challenge_nonce: bytes | None = Field(
        default=None,
        sa_column=Column(LargeBinary, nullable=True),
    )
    challenge_revision: int = Field(
        default=0,
        ge=0,
        sa_column=Column(Integer, nullable=False),
    )
    challenge_request_revision: int = Field(
        default=1,
        ge=1,
        sa_column=Column(Integer, nullable=False),
    )
    challenge_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    validation_fingerprint: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    validated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    connected_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_seen_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    failure_code: str | None = Field(default=None, min_length=1, max_length=64)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class WhatsAppSessionKeyRecord(SQLModel, table=True):
    __tablename__ = "whatsapp_session_keys"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "integration_id",
            "key_category",
            "logical_key_id",
            name="uq_whatsapp_session_keys_logical",
        ),
        Index(
            "ix_whatsapp_session_keys_user_integration",
            "user_id",
            "integration_id",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    key_category: str = Field(min_length=1, max_length=64)
    logical_key_id: str = Field(min_length=1, max_length=512)
    ciphertext: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    nonce: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    revision: int = Field(
        default=1,
        ge=1,
        sa_column=Column(Integer, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class WhatsAppSessionLeaseRecord(SQLModel, table=True):
    __tablename__ = "whatsapp_session_leases"
    __table_args__ = (
        Index("ix_whatsapp_session_leases_user", "user_id"),
        Index(
            "ix_whatsapp_session_leases_expiry",
            "lease_expires_at",
        ),
    )

    integration_id: str = Field(primary_key=True, min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    owner_id: str = Field(min_length=1, max_length=128)
    generation: int = Field(
        default=1,
        ge=1,
        sa_column=Column(Integer, nullable=False),
    )
    lease_expires_at: datetime = Field(
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


class WhatsAppOutboxRecord(SQLModel, table=True):
    __tablename__ = "whatsapp_outbox"
    __table_args__ = (
        UniqueConstraint("reply_id", name="uq_whatsapp_outbox_reply"),
        Index(
            "ix_whatsapp_outbox_worker_queue",
            "status",
            "created_at",
        ),
        Index(
            "ix_whatsapp_outbox_user_integration",
            "user_id",
            "integration_id",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    integration_id: str = Field(index=True, min_length=1, max_length=36)
    resource_id: str = Field(index=True, min_length=1, max_length=36)
    reply_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    target_jid: str = Field(min_length=1, max_length=512)
    body: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(
        default=WhatsAppOutboxStatus.QUEUED.value,
        sa_column=Column(String(32), nullable=False),
    )
    owner_id: str | None = Field(default=None, min_length=1, max_length=128)
    lease_generation: int | None = Field(default=None, ge=1)
    external_message_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    failure_code: str | None = Field(default=None, min_length=1, max_length=64)
    queued_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    submission_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    sent_at: datetime | None = Field(
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
