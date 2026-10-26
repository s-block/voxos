from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy import Engine, event
from sqlmodel import col, select

from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    IntegrationDirection,
    IntegrationMessageStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
    IntegrationResourceStatus,
    IntegrationResourceType,
)
from voxos.integrations.records import (
    IntegrationMessageRecord,
    IntegrationReplyRecord,
    IntegrationResourceRecord,
)
from voxos.whatsapp.crypto import generate_session_data_key
from voxos.whatsapp.models import (
    WhatsAppLinkMethod,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import (
    WhatsAppOutboxRecord,
    WhatsAppSessionKeyRecord,
    WhatsAppSessionLeaseRecord,
    WhatsAppSessionRecord,
)
from voxos.whatsapp.store import (
    MAX_AUTH_WRITES,
    WhatsAppAuthWrite,
    WhatsAppLeaseLostError,
    WhatsAppSessionDataStore,
    WhatsAppWorkerStore,
)

USER_ID = "user-123"
INTEGRATION_ID = "integration-123"
CREDENTIAL_ID = "credential-123"


async def seed_session(session_factory: AsyncSessionFactory) -> None:
    async with session_factory() as session:
        session.add(
            WhatsAppSessionRecord(
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                resource_id="resource-123",
                source_resource_id="source-resource-123",
                credential_id=CREDENTIAL_ID,
                phone_number="+447418606704",
            ),
        )
        await session.commit()


def data_store(
    *,
    session_factory: AsyncSessionFactory,
    vault: UserSecretVaultService,
) -> WhatsAppSessionDataStore:
    return WhatsAppSessionDataStore(
        session_factory=session_factory,
        vault=vault,
        user_id=USER_ID,
    )


@pytest.mark.asyncio
async def test_auth_values_are_encrypted_and_revisioned(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_session(sqlite_session_factory)
    store = data_store(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    await store.seal_session_data_key(
        credential_id=CREDENTIAL_ID,
        data_key=generate_session_data_key(),
    )
    cipher = await store.resolve_cipher(credential_id=CREDENTIAL_ID)
    first_value = {
        "noiseKey": {
            "private": {
                "$voxos_type": "buffer",
                "base64": "cHJpdmF0ZQ==",
            },
        },
    }

    await store.set_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        writes=(
            WhatsAppAuthWrite(
                category="credentials",
                logical_id="credentials",
                value=first_value,
            ),
        ),
    )
    values = await store.get_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        category="credentials",
        logical_ids=("credentials", "missing"),
    )

    assert values == {"credentials": first_value, "missing": None}

    async with sqlite_session_factory() as session:
        record = (await session.exec(select(WhatsAppSessionKeyRecord))).one()
        first_ciphertext = record.ciphertext
        assert b"private" not in record.ciphertext

    await store.set_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        writes=(
            WhatsAppAuthWrite(
                category="credentials",
                logical_id="credentials",
                value={"registered": True},
            ),
        ),
    )

    async with sqlite_session_factory() as session:
        record = (await session.exec(select(WhatsAppSessionKeyRecord))).one()
        assert record.revision == 2
        assert record.ciphertext != first_ciphertext


@pytest.mark.asyncio
async def test_auth_null_and_tagged_undefined_delete_values(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_session(sqlite_session_factory)
    store = data_store(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    await store.seal_session_data_key(
        credential_id=CREDENTIAL_ID,
        data_key=generate_session_data_key(),
    )
    cipher = await store.resolve_cipher(credential_id=CREDENTIAL_ID)
    await store.set_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        writes=(
            WhatsAppAuthWrite("session", "one", {"value": 1}),
            WhatsAppAuthWrite("session", "two", {"value": 2}),
        ),
    )

    await store.set_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        writes=(
            WhatsAppAuthWrite("session", "one", None),
            WhatsAppAuthWrite(
                "session",
                "two",
                {"$voxos_type": "undefined"},
            ),
        ),
    )

    assert await store.get_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        category="session",
        logical_ids=("one", "two"),
    ) == {"one": None, "two": None}


@pytest.mark.asyncio
async def test_maximum_auth_write_batch_persists_atomically(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_session(sqlite_session_factory)
    store = data_store(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    await store.seal_session_data_key(
        credential_id=CREDENTIAL_ID,
        data_key=generate_session_data_key(),
    )
    cipher = await store.resolve_cipher(credential_id=CREDENTIAL_ID)

    await store.set_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        writes=tuple(
            WhatsAppAuthWrite(
                category="pre-key",
                logical_id=str(index),
                value={"keyId": index},
            )
            for index in range(MAX_AUTH_WRITES)
        ),
    )

    async with sqlite_session_factory() as session:
        records = (await session.exec(select(WhatsAppSessionKeyRecord))).all()
    assert len(records) == MAX_AUTH_WRITES


@pytest.mark.asyncio
async def test_auth_write_locks_only_the_requested_key_pairs(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_session(sqlite_session_factory)
    store = data_store(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    await store.seal_session_data_key(
        credential_id=CREDENTIAL_ID,
        data_key=generate_session_data_key(),
    )
    cipher = await store.resolve_cipher(credential_id=CREDENTIAL_ID)
    await store.set_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        writes=(
            WhatsAppAuthWrite("session", "target", {"revision": 1}),
            WhatsAppAuthWrite("session", "leave-unlocked", {"revision": 1}),
        ),
    )

    captured_key_selects: list[tuple[str, object]] = []

    def capture_key_select(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("SELECT") and "FROM whatsapp_session_keys" in statement:
            captured_key_selects.append((statement, parameters))

    async with sqlite_session_factory() as session:
        engine = cast(Engine, session.get_bind())
    event.listen(engine, "before_cursor_execute", capture_key_select)
    try:
        await store.set_auth_values(
            integration_id=INTEGRATION_ID,
            cipher=cipher,
            writes=(WhatsAppAuthWrite("session", "target", {"revision": 2}),),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_key_select)

    assert len(captured_key_selects) == 1
    statement, parameters = captured_key_selects[0]
    normalized_statement = " ".join(statement.split())
    assert (
        "(whatsapp_session_keys.key_category, "
        "whatsapp_session_keys.logical_key_id) IN (VALUES (?, ?))" in normalized_statement
    )
    assert "target" in repr(parameters)
    assert "leave-unlocked" not in repr(parameters)

    values = await store.get_auth_values(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        category="session",
        logical_ids=("leave-unlocked",),
    )
    assert values == {"leave-unlocked": {"revision": 1}}


@pytest.mark.asyncio
async def test_challenge_is_encrypted_and_expires(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_session(sqlite_session_factory)
    store = data_store(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    await store.seal_session_data_key(
        credential_id=CREDENTIAL_ID,
        data_key=generate_session_data_key(),
    )
    cipher = await store.resolve_cipher(credential_id=CREDENTIAL_ID)
    expires_at = utc_now() + timedelta(minutes=1)

    await store.save_challenge(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        method=WhatsAppLinkMethod.QR,
        requested_method=WhatsAppLinkMethod.QR,
        request_revision=1,
        value="private-qr-value",
        expires_at=expires_at,
    )
    challenge = await store.get_challenge(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
    )

    assert challenge is not None
    assert challenge.value == "private-qr-value"
    assert challenge.method == WhatsAppLinkMethod.QR

    async with sqlite_session_factory() as session:
        record = (await session.exec(select(WhatsAppSessionRecord))).one()
        assert record.state == WhatsAppSessionState.AWAITING_LINK.value
        assert record.challenge_ciphertext is not None
        assert b"private-qr-value" not in record.challenge_ciphertext


@pytest.mark.asyncio
async def test_stale_challenge_cannot_regress_connected_session(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_session(sqlite_session_factory)
    store = data_store(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    await store.seal_session_data_key(
        credential_id=CREDENTIAL_ID,
        data_key=generate_session_data_key(),
    )
    cipher = await store.resolve_cipher(credential_id=CREDENTIAL_ID)
    async with sqlite_session_factory() as session:
        record = (await session.exec(select(WhatsAppSessionRecord))).one()
        record.state = WhatsAppSessionState.CONNECTED.value
        await session.commit()

    await store.save_challenge(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        method=WhatsAppLinkMethod.QR,
        requested_method=WhatsAppLinkMethod.QR,
        request_revision=1,
        value="stale-private-qr-value",
        expires_at=utc_now() + timedelta(minutes=1),
    )

    async with sqlite_session_factory() as session:
        record = (await session.exec(select(WhatsAppSessionRecord))).one()
        assert record.state == WhatsAppSessionState.CONNECTED.value
        assert record.challenge_ciphertext is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_method", "request_revision"),
    (
        (WhatsAppLinkMethod.QR, 1),
        (WhatsAppLinkMethod.PAIRING_CODE, 2),
    ),
)
async def test_stale_or_wrong_method_challenge_is_not_persisted(
    requested_method: WhatsAppLinkMethod,
    request_revision: int,
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_session(sqlite_session_factory)
    store = data_store(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    await store.seal_session_data_key(
        credential_id=CREDENTIAL_ID,
        data_key=generate_session_data_key(),
    )
    cipher = await store.resolve_cipher(credential_id=CREDENTIAL_ID)
    async with sqlite_session_factory() as session:
        record = (await session.exec(select(WhatsAppSessionRecord))).one()
        record.link_method = WhatsAppLinkMethod.QR.value
        record.challenge_request_revision = 2
        await session.commit()

    await store.save_challenge(
        integration_id=INTEGRATION_ID,
        cipher=cipher,
        method=requested_method,
        requested_method=requested_method,
        request_revision=request_revision,
        value="stale-private-challenge",
        expires_at=utc_now() + timedelta(minutes=1),
    )

    async with sqlite_session_factory() as session:
        record = (await session.exec(select(WhatsAppSessionRecord))).one()
        assert record.link_method == WhatsAppLinkMethod.QR.value
        assert record.challenge_request_revision == 2
        assert record.challenge_ciphertext is None
        assert record.challenge_revision == 0


@pytest.mark.asyncio
async def test_demand_listing_filters_other_worker_leases_before_limit(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    now = utc_now()
    other_worker = "worker-with-full-capacity"
    current_worker = "worker-looking-for-work"
    async with sqlite_session_factory() as session:
        for index in range(101):
            integration_id = f"leased-integration-{index}"
            user_id = f"leased-user-{index}"
            session.add(
                WhatsAppSessionRecord(
                    user_id=user_id,
                    integration_id=integration_id,
                    resource_id=f"leased-resource-{index}",
                    source_resource_id=f"leased-source-{index}",
                    credential_id=f"leased-credential-{index}",
                    phone_number="+447700900000",
                    updated_at=now - timedelta(minutes=10),
                ),
            )
            session.add(
                WhatsAppSessionLeaseRecord(
                    integration_id=integration_id,
                    user_id=user_id,
                    owner_id=other_worker,
                    generation=1,
                    lease_expires_at=now + timedelta(minutes=5),
                ),
            )
        session.add(
            WhatsAppSessionRecord(
                user_id="available-user",
                integration_id="available-integration",
                resource_id="available-resource",
                source_resource_id="available-source",
                credential_id="available-credential",
                phone_number="+447700900001",
                updated_at=now - timedelta(minutes=5),
            ),
        )
        session.add(
            WhatsAppSessionRecord(
                user_id="owned-user",
                integration_id="owned-integration",
                resource_id="owned-resource",
                source_resource_id="owned-source",
                credential_id="owned-credential",
                phone_number="+447700900002",
                updated_at=now,
            ),
        )
        session.add(
            WhatsAppSessionLeaseRecord(
                integration_id="owned-integration",
                user_id="owned-user",
                owner_id=current_worker,
                generation=1,
                lease_expires_at=now + timedelta(minutes=5),
            ),
        )
        await session.commit()

    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demands = await worker_store.list_session_demands(
        owner_id=current_worker,
        limit=100,
    )

    assert [demand.integration_id for demand in demands] == [
        "owned-integration",
        "available-integration",
    ]


@pytest.mark.asyncio
async def test_demand_listing_recovers_legacy_terminal_disconnects(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    now = utc_now()
    async with sqlite_session_factory() as session:
        session.add(
            WhatsAppSessionRecord(
                user_id="recoverable-user",
                integration_id="recoverable-integration",
                resource_id="recoverable-resource",
                source_resource_id="recoverable-source",
                credential_id="recoverable-credential",
                phone_number="+447700900001",
                state=WhatsAppSessionState.FAILED.value,
                failure_code="already_linked",
                updated_at=now,
            ),
        )
        session.add(
            WhatsAppSessionRecord(
                user_id="legacy-user",
                integration_id="legacy-integration",
                resource_id="legacy-resource",
                source_resource_id="legacy-source",
                credential_id="legacy-credential",
                phone_number="+447700900002",
                state=WhatsAppSessionState.FAILED.value,
                failure_code="terminal_local_failure",
                updated_at=now + timedelta(seconds=1),
            ),
        )
        session.add(
            WhatsAppSessionRecord(
                user_id="terminal-user",
                integration_id="terminal-integration",
                resource_id="terminal-resource",
                source_resource_id="terminal-source",
                credential_id="terminal-credential",
                phone_number="+447700900003",
                state=WhatsAppSessionState.FAILED.value,
                failure_code="session_runtime_failure",
                updated_at=now,
            ),
        )
        await session.commit()

    demands = await WhatsAppWorkerStore(
        sqlite_session_factory,
    ).list_session_demands(
        owner_id="worker-one",
        limit=10,
    )

    assert [item.integration_id for item in demands] == [
        "recoverable-integration",
        "legacy-integration",
    ]


@pytest.mark.asyncio
async def test_lease_takeover_increments_generation_and_fences_old_owner(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await seed_session(sqlite_session_factory)
    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = (
        await worker_store.list_session_demands(
            owner_id="worker-one",
            limit=10,
        )
    )[0]
    first = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-one",
        lease_ttl_seconds=30,
    )
    assert first is not None
    assert first.generation == 1
    assert (
        await worker_store.claim_session(
            demand=demand,
            owner_id="worker-two",
            lease_ttl_seconds=30,
        )
        is None
    )

    store = data_store(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    await store.seal_session_data_key(
        credential_id=CREDENTIAL_ID,
        data_key=generate_session_data_key(),
    )
    cipher = await store.resolve_cipher(
        credential_id=CREDENTIAL_ID,
        fence=first,
    )

    async with sqlite_session_factory() as session:
        lease = (
            await session.exec(
                select(WhatsAppSessionLeaseRecord).where(
                    col(WhatsAppSessionLeaseRecord.integration_id) == INTEGRATION_ID,
                ),
            )
        ).one()
        lease.lease_expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    second = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-two",
        lease_ttl_seconds=30,
    )
    assert second is not None
    assert second.generation == 2

    with pytest.raises(WhatsAppLeaseLostError):
        await store.set_auth_values(
            integration_id=INTEGRATION_ID,
            cipher=cipher,
            fence=first,
            writes=(WhatsAppAuthWrite("credentials", "credentials", {}),),
        )


@pytest.mark.asyncio
async def test_release_and_controller_restart_keep_generation_monotonic(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await seed_session(sqlite_session_factory)
    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = (
        await worker_store.list_session_demands(
            owner_id="worker-one",
            limit=1,
        )
    )[0]
    first = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-one",
        lease_ttl_seconds=30,
    )
    assert first is not None

    restarted = await worker_store.advance_session_generation(fence=first)
    assert restarted.generation == first.generation + 1
    assert await worker_store.release_session(fence=restarted)

    reclaimed = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-two",
        lease_ttl_seconds=30,
    )
    assert reclaimed is not None
    assert reclaimed.generation == restarted.generation + 1

    async with sqlite_session_factory() as session:
        leases = (await session.exec(select(WhatsAppSessionLeaseRecord))).all()
    assert len(leases) == 1


@pytest.mark.asyncio
async def test_outbox_claim_commits_submission_before_send(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await seed_session(sqlite_session_factory)
    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = (
        await worker_store.list_session_demands(
            owner_id="worker-one",
            limit=1,
        )
    )[0]
    fence = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-one",
        lease_ttl_seconds=30,
    )
    assert fence is not None
    async with sqlite_session_factory() as session:
        whatsapp_session = (await session.exec(select(WhatsAppSessionRecord))).one()
        whatsapp_session.state = WhatsAppSessionState.CONNECTED.value
        session.add(
            IntegrationResourceRecord(
                id="resource-123",
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                provider=IntegrationProviderType.WHATSAPP.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id="source-resource-123",
                display_name="+447700900456",
                status=IntegrationResourceStatus.CONNECTED.value,
            ),
        )
        session.add(
            IntegrationMessageRecord(
                id="inbound-message-123",
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                resource_id="resource-123",
                provider=IntegrationProviderType.WHATSAPP.value,
                direction=IntegrationDirection.INBOUND.value,
                external_message_id="inbound-provider-message-123",
                from_number="+447700900123",
                to_number="+447700900456",
                body="Hi",
                status=IntegrationMessageStatus.RECEIVED.value,
            ),
        )
        session.add(
            IntegrationReplyRecord(
                id="reply-123",
                user_id=USER_ID,
                inbound_message_id="inbound-message-123",
                conversation_id="conversation-123",
                status=IntegrationReplyStatus.GENERATED.value,
                response_profile="whatsapp:v1",
                generated_body="Hello",
            ),
        )
        session.add(
            WhatsAppOutboxRecord(
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                resource_id="resource-123",
                reply_id="reply-123",
                target_jid="447700900123@s.whatsapp.net",
                body="Hello",
            ),
        )
        await session.commit()

    claimed = await worker_store.claim_outbox(fence=fence)

    assert claimed is not None
    assert claimed.status == WhatsAppOutboxStatus.SUBMITTING.value
    assert claimed.submission_started_at is not None
    assert await worker_store.complete_outbox(
        fence=fence,
        outbox_id=claimed.id,
        status=WhatsAppOutboxStatus.SENT,
        external_message_id="provider-message-123",
    )

    async with sqlite_session_factory() as session:
        stored = (await session.exec(select(WhatsAppOutboxRecord))).one()
        reply = (await session.exec(select(IntegrationReplyRecord))).one()
        outbound = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.direction) == IntegrationDirection.OUTBOUND.value,
                ),
            )
        ).one()
        assert stored.status == WhatsAppOutboxStatus.SENT.value
        assert stored.external_message_id == "provider-message-123"
        assert stored.sent_at is not None
        assert reply.status == IntegrationReplyStatus.PROVIDER_ACCEPTED.value
        assert reply.outbound_message_id == outbound.id
        assert outbound.external_message_id == "provider-message-123"
        assert outbound.from_number == "+447700900456"
        assert outbound.to_number == "+447700900123"


@pytest.mark.asyncio
async def test_direct_outbox_message_records_outbound_delivery(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await seed_session(sqlite_session_factory)
    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = (
        await worker_store.list_session_demands(
            owner_id="worker-one",
            limit=1,
        )
    )[0]
    fence = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-one",
        lease_ttl_seconds=30,
    )
    assert fence is not None
    async with sqlite_session_factory() as session:
        whatsapp_session = (await session.exec(select(WhatsAppSessionRecord))).one()
        whatsapp_session.state = WhatsAppSessionState.CONNECTED.value
        session.add(
            IntegrationResourceRecord(
                id="resource-123",
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                provider=IntegrationProviderType.WHATSAPP.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id="source-resource-123",
                display_name="+447700900456",
                status=IntegrationResourceStatus.CONNECTED.value,
            ),
        )
        session.add(
            WhatsAppOutboxRecord(
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                resource_id="resource-123",
                target_jid="447700900123@s.whatsapp.net",
                body="Hello Josh",
            ),
        )
        await session.commit()

    claimed = await worker_store.claim_outbox(fence=fence)

    assert claimed is not None
    assert claimed.reply_id is None
    assert await worker_store.complete_outbox(
        fence=fence,
        outbox_id=claimed.id,
        status=WhatsAppOutboxStatus.SENT,
        external_message_id="provider-message-456",
    )
    async with sqlite_session_factory() as session:
        stored = (await session.exec(select(WhatsAppOutboxRecord))).one()
        outbound = (await session.exec(select(IntegrationMessageRecord))).one()
    assert stored.status == WhatsAppOutboxStatus.SENT.value
    assert outbound.direction == IntegrationDirection.OUTBOUND.value
    assert outbound.external_message_id == "provider-message-456"
    assert outbound.from_number == "+447700900456"
    assert outbound.to_number == "+447700900123"
