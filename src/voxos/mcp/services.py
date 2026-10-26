from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import orjson
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.logos.domains import logo_from_url
from voxos.logos.models import BrandLogo
from voxos.mcp.auth_strategies import (
    McpAuthStrategyDependencies,
    McpPrivateKeyMaterial,
    auth_config_from_record,
    strategy_for_auth_type,
)
from voxos.mcp.client import (
    DiscoveredMcpCapabilities,
    DiscoveredMcpTool,
    McpClientConnectionConfig,
    UserMcpClient,
)
from voxos.mcp.delegation import (
    DELEGATION_PROVIDER_MONZO,
    McpCredentialDelegationSigner,
)
from voxos.mcp.errors import (
    McpAuthRequiredError,
    McpInvalidAuthTypeError,
    McpOAuthConfigurationError,
    McpOAuthStateError,
    McpPresetConfigurationError,
    McpPresetNotFoundError,
    McpServerLabelConflictError,
    McpServerNotFoundError,
    McpServerUrlPolicyError,
)
from voxos.mcp.models import (
    McpAuthSummary,
    McpAuthType,
    McpClientCredentialsAuthRequest,
    McpDiscoveredToolDefinition,
    McpOAuthCallbackResult,
    McpOAuthClientInfo,
    McpOAuthStartRequest,
    McpOAuthStartResponse,
    McpOAuthToken,
    McpPresetAuthGuide,
    McpPresetCompatibility,
    McpPresetServerCreateRequest,
    McpPresetToolGuide,
    McpPrivateKeyJwtAuthRequest,
    McpSecretKind,
    McpServerAuthConfig,
    McpServerCreateRequest,
    McpServerResponse,
    McpServerSafeMetadata,
    McpServerStatus,
    McpServerUpdateRequest,
    McpServicePresetVariant,
    McpStaticAuthRequest,
    McpTokenEndpointAuthMethod,
    McpTransportType,
    McpTrustState,
)
from voxos.mcp.oauth import McpOAuthHttpClient, state_hash
from voxos.mcp.oauth_state import McpOAuthStateSigner
from voxos.mcp.presets import (
    find_mcp_service_preset,
    find_mcp_service_preset_variant,
    preset_safe_metadata,
)
from voxos.mcp.records import (
    UserMcpOAuthStateRecord,
    UserMcpSecretRecord,
    UserMcpServerRecord,
    new_mcp_record_id,
    utc_now,
)
from voxos.mcp.url_policy import McpOutboundUrlPolicy

MCP_ENCRYPTION_PROVIDER = "mcp_server"
OAUTH_STATE_TTL = timedelta(minutes=10)
PRIVATE_KEY_ALGORITHM_METADATA_KEY = "algorithm"
MAX_DISCOVERED_TOOL_DEFINITIONS = 100
MAX_TOOL_DESCRIPTION_CHARS = 2048
MAX_TOOL_SCHEMA_BYTES = 16_384
MCP_CREDENTIAL_DELEGATION_HEADER = "X-MCP-Credential-Delegation"
MONZO_AUTHORIZATION_ENDPOINT = "https://auth.monzo.com/"
MONZO_TOKEN_ENDPOINT = "https://api.monzo.com/oauth2/token"
MONZO_PRESET_ID = "monzo"
MCP_SERVER_URL_NOT_PERMITTED_MESSAGE = "MCP server URL is not permitted"


@dataclass(frozen=True, slots=True)
class ConsumedOAuthState:
    user_id: str
    server_id: str
    redirect_uri: str
    code_verifier: str | None


@dataclass(frozen=True, slots=True)
class UserMcpToolsetConfig:
    id: str
    server_id: str
    label: str
    url: str
    transport: McpTransportType
    headers: dict[str, str]
    allowed_tools: tuple[str, ...]
    tool_definitions: tuple[McpDiscoveredToolDefinition, ...] = ()
    preset_id: str | None = None
    logo: BrandLogo | None = None


class McpSecretStore:
    def __init__(
        self,
        session: AsyncSession,
        secret_vault: UserSecretVaultService,
    ) -> None:
        self._session = session
        self._secret_vault = secret_vault

    async def upsert_secret(
        self,
        user_id: str,
        server_id: str,
        secret_kind: McpSecretKind,
        secret: SecretStr,
        secret_metadata: dict[str, object] | None = None,
        expires_at: datetime | None = None,
    ) -> UserMcpSecretRecord:
        record = await self._get_secret_record(
            user_id=user_id,
            server_id=server_id,
            secret_kind=secret_kind,
        )
        if record is None:
            record = UserMcpSecretRecord(
                id=new_mcp_record_id(),
                user_id=user_id,
                server_id=server_id,
                secret_kind=secret_kind.value,
            )
            self._session.add(record)

        reference = mcp_secret_reference(
            user_id=user_id,
            record_id=record.id,
            purpose=SecretPurpose.MCP_SERVER_SECRET,
        )
        await self._secret_vault.seal_secret(
            session=self._session,
            secret=secret,
            reference=reference,
        )
        record.secret_metadata = secret_metadata or {}
        record.last_four = None
        record.expires_at = expires_at
        record.updated_at = utc_now()
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def load_secret_value(
        self,
        user_id: str,
        server_id: str,
        secret_kind: McpSecretKind,
    ) -> SecretStr | None:
        record = await self._get_secret_record(
            user_id=user_id,
            server_id=server_id,
            secret_kind=secret_kind,
        )
        if record is None:
            return None
        return await self._secret_vault.unseal_secret(
            session=self._session,
            reference=mcp_secret_reference(
                user_id=user_id,
                record_id=record.id,
                purpose=SecretPurpose.MCP_SERVER_SECRET,
            ),
        )

    async def load_secret_summary(
        self,
        user_id: str,
        server_id: str,
        secret_kind: McpSecretKind,
    ) -> McpAuthSummary:
        record = await self._get_secret_record(
            user_id=user_id,
            server_id=server_id,
            secret_kind=secret_kind,
        )
        if record is None:
            return McpAuthSummary(authenticated=False, needs_reauth=True)
        return McpAuthSummary(
            authenticated=True,
            needs_reauth=False,
            expires_at=record.expires_at,
            scopes=metadata_scopes(record.secret_metadata),
        )

    async def store_oauth_client_info(
        self,
        user_id: str,
        server_id: str,
        client_info: McpOAuthClientInfo,
    ) -> None:
        metadata: dict[str, object] = {
            "client_id": client_info.client_id,
            "scopes": list(client_info.scopes),
            "token_endpoint": client_info.token_endpoint,
            "token_endpoint_auth_method": client_info.token_endpoint_auth_method.value,
        }
        await self.upsert_secret(
            user_id=user_id,
            server_id=server_id,
            secret_kind=McpSecretKind.OAUTH_CLIENT_INFO,
            secret=SecretStr(model_secret_json(client_info)),
            secret_metadata=metadata,
        )

    async def load_oauth_client_info(
        self,
        user_id: str,
        server_id: str,
    ) -> McpOAuthClientInfo | None:
        secret = await self.load_secret_value(
            user_id=user_id,
            server_id=server_id,
            secret_kind=McpSecretKind.OAUTH_CLIENT_INFO,
        )
        if secret is None:
            return None
        return McpOAuthClientInfo.model_validate_json(secret.get_secret_value())

    async def store_oauth_token(
        self,
        user_id: str,
        server_id: str,
        token: McpOAuthToken,
    ) -> None:
        await self.upsert_secret(
            user_id=user_id,
            server_id=server_id,
            secret_kind=McpSecretKind.OAUTH_TOKEN,
            secret=SecretStr(model_secret_json(token)),
            secret_metadata={"scopes": list(token.scopes)},
            expires_at=token.expires_at,
        )

    async def load_oauth_token(
        self,
        user_id: str,
        server_id: str,
    ) -> McpOAuthToken | None:
        secret = await self.load_secret_value(
            user_id=user_id,
            server_id=server_id,
            secret_kind=McpSecretKind.OAUTH_TOKEN,
        )
        if secret is None:
            return None
        return McpOAuthToken.model_validate_json(secret.get_secret_value())

    async def store_private_key_material(
        self,
        user_id: str,
        server_id: str,
        private_key: SecretStr,
        algorithm: str,
    ) -> None:
        await self.upsert_secret(
            user_id=user_id,
            server_id=server_id,
            secret_kind=McpSecretKind.PRIVATE_KEY,
            secret=private_key,
            secret_metadata={PRIVATE_KEY_ALGORITHM_METADATA_KEY: algorithm},
        )

    async def load_private_key_material(
        self,
        user_id: str,
        server_id: str,
    ) -> McpPrivateKeyMaterial | None:
        record = await self._get_secret_record(
            user_id=user_id,
            server_id=server_id,
            secret_kind=McpSecretKind.PRIVATE_KEY,
        )
        if record is None:
            return None
        secret = await self.load_secret_value(
            user_id=user_id,
            server_id=server_id,
            secret_kind=McpSecretKind.PRIVATE_KEY,
        )
        if secret is None:
            return None
        algorithm_value = record.secret_metadata.get(PRIVATE_KEY_ALGORITHM_METADATA_KEY)
        algorithm = "RS256" if not isinstance(algorithm_value, str) else algorithm_value
        return McpPrivateKeyMaterial(private_key=secret, algorithm=algorithm)

    async def store_oauth_state(
        self,
        user_id: str,
        server_id: str,
        state: str,
        redirect_uri: str,
        authorization_url: str,
        code_verifier: str | None,
    ) -> UserMcpOAuthStateRecord:
        await self._delete_pending_oauth_states(user_id=user_id, server_id=server_id)
        record_id = new_mcp_record_id()
        reference = mcp_secret_reference(
            user_id=user_id,
            record_id=record_id,
            purpose=SecretPurpose.MCP_OAUTH_STATE,
        )
        if code_verifier is not None:
            await self._secret_vault.seal_secret(
                session=self._session,
                secret=SecretStr(code_verifier),
                reference=reference,
            )
        record = UserMcpOAuthStateRecord(
            id=record_id,
            user_id=user_id,
            server_id=server_id,
            state_hash=state_hash(state),
            redirect_uri=redirect_uri,
            authorization_url=authorization_url,
            expires_at=utc_now() + OAUTH_STATE_TTL,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def consume_oauth_state(
        self,
        server_id: str,
        state: str,
        *,
        requires_code_verifier: bool,
    ) -> ConsumedOAuthState:
        statement = select(UserMcpOAuthStateRecord).where(
            col(UserMcpOAuthStateRecord.server_id) == server_id,
            col(UserMcpOAuthStateRecord.state_hash) == state_hash(state),
            col(UserMcpOAuthStateRecord.used_at).is_(None),
        )
        result = await self._session.exec(statement)
        record = result.one_or_none()
        if record is None:
            raise McpOAuthStateError
        if ensure_aware(record.expires_at) <= utc_now():
            await self._session.delete(record)
            await self._session.commit()
            raise McpOAuthStateError

        verifier = await self._secret_vault.unseal_secret(
            session=self._session,
            reference=mcp_secret_reference(
                user_id=record.user_id,
                record_id=record.id,
                purpose=SecretPurpose.MCP_OAUTH_STATE,
            ),
        )
        if requires_code_verifier and verifier is None:
            raise McpOAuthStateError
        consumed_state = ConsumedOAuthState(
            user_id=record.user_id,
            server_id=record.server_id,
            redirect_uri=record.redirect_uri,
            code_verifier=(None if verifier is None else verifier.get_secret_value()),
        )
        await self._secret_vault.delete_secret(
            session=self._session,
            reference=mcp_secret_reference(
                user_id=record.user_id,
                record_id=record.id,
                purpose=SecretPurpose.MCP_OAUTH_STATE,
            ),
        )
        await self._session.delete(record)
        await self._session.commit()
        return consumed_state

    async def delete_auth_for_server(self, user_id: str, server_id: str) -> None:
        secret_records = await self._secret_records_for_server(
            user_id=user_id,
            server_id=server_id,
        )
        for record in secret_records:
            await self._secret_vault.delete_secret(
                session=self._session,
                reference=mcp_secret_reference(
                    user_id=user_id,
                    record_id=record.id,
                    purpose=SecretPurpose.MCP_SERVER_SECRET,
                ),
            )
        await self._session.exec(
            delete(UserMcpSecretRecord).where(
                col(UserMcpSecretRecord.user_id) == user_id,
                col(UserMcpSecretRecord.server_id) == server_id,
            ),
        )
        await self._delete_pending_oauth_states(user_id=user_id, server_id=server_id)
        await self._session.commit()

    async def _delete_pending_oauth_states(self, user_id: str, server_id: str) -> None:
        state_records = await self._oauth_state_records_for_server(
            user_id=user_id,
            server_id=server_id,
        )
        for record in state_records:
            await self._secret_vault.delete_secret(
                session=self._session,
                reference=mcp_secret_reference(
                    user_id=user_id,
                    record_id=record.id,
                    purpose=SecretPurpose.MCP_OAUTH_STATE,
                ),
            )
        await self._session.exec(
            delete(UserMcpOAuthStateRecord).where(
                col(UserMcpOAuthStateRecord.user_id) == user_id,
                col(UserMcpOAuthStateRecord.server_id) == server_id,
            ),
        )

    async def _get_secret_record(
        self,
        user_id: str,
        server_id: str,
        secret_kind: McpSecretKind,
    ) -> UserMcpSecretRecord | None:
        statement = select(UserMcpSecretRecord).where(
            col(UserMcpSecretRecord.user_id) == user_id,
            col(UserMcpSecretRecord.server_id) == server_id,
            col(UserMcpSecretRecord.secret_kind) == secret_kind.value,
        )
        result = await self._session.exec(statement)
        return result.one_or_none()

    async def _secret_records_for_server(
        self,
        user_id: str,
        server_id: str,
    ) -> tuple[UserMcpSecretRecord, ...]:
        statement = select(UserMcpSecretRecord).where(
            col(UserMcpSecretRecord.user_id) == user_id,
            col(UserMcpSecretRecord.server_id) == server_id,
        )
        result = await self._session.exec(statement)
        return tuple(result.all())

    async def _oauth_state_records_for_server(
        self,
        user_id: str,
        server_id: str,
    ) -> tuple[UserMcpOAuthStateRecord, ...]:
        statement = select(UserMcpOAuthStateRecord).where(
            col(UserMcpOAuthStateRecord.user_id) == user_id,
            col(UserMcpOAuthStateRecord.server_id) == server_id,
        )
        result = await self._session.exec(statement)
        return tuple(result.all())


class UserMcpServerService:
    def __init__(
        self,
        session: AsyncSession,
        secret_vault: UserSecretVaultService,
        mcp_client: UserMcpClient,
        oauth_client: McpOAuthHttpClient,
        oauth_state_signer: McpOAuthStateSigner | None = None,
        credential_delegation_signer: McpCredentialDelegationSigner | None = None,
        url_policy: McpOutboundUrlPolicy | None = None,
    ) -> None:
        self._session = session
        self._secret_store = McpSecretStore(
            session=session,
            secret_vault=secret_vault,
        )
        self._mcp_client = mcp_client
        self._oauth_client = oauth_client
        self._oauth_state_signer = oauth_state_signer
        self._credential_delegation_signer = credential_delegation_signer
        self._url_policy = url_policy or McpOutboundUrlPolicy()

    async def create_server(
        self,
        user_id: str,
        request: McpServerCreateRequest,
    ) -> McpServerResponse:
        return await self._create_server_record(
            user_id=user_id,
            label=request.label,
            url=request.url,
            transport=request.transport,
            auth_type=request.auth_type,
            enabled=request.enabled,
            non_admin_tools_enabled=request.non_admin_tools_enabled,
            allowed_tools=request.allowed_tools,
            auth_config=request.auth_config(),
            safe_metadata={},
        )

    async def create_server_from_preset(
        self,
        user_id: str,
        preset_id: str,
        request: McpPresetServerCreateRequest,
    ) -> McpServerResponse:
        preset = find_mcp_service_preset(preset_id)
        if preset is None:
            raise McpPresetNotFoundError(preset_id)
        variant = find_mcp_service_preset_variant(preset, request.variant_id)
        if variant is None:
            message = f"MCP preset {preset_id} variant was not found"
            raise McpPresetConfigurationError(message)

        if variant.trusted_private_host is not None and (
            variant.url is None or not self._url_policy.allows_trusted_url(variant.url)
        ):
            message = "This local MCP preset is not enabled in the current environment"
            raise McpServerUrlPolicyError(message)

        if variant.compatibility == McpPresetCompatibility.DIRECT_REMOTE:
            if request.url is not None:
                message = "Direct MCP presets do not allow URL overrides"
                raise McpPresetConfigurationError(message)
            if variant.url is None:
                message = "Direct MCP preset is missing a service URL"
                raise McpPresetConfigurationError(message)
            url = variant.url
        else:
            if request.url is None:
                message = (
                    "MCP presets with custom URLs require a public HTTPS URL; "
                    "gateway presets require a public HTTPS gateway URL"
                )
                raise McpPresetConfigurationError(message)
            url = request.url

        api_key_header_name = variant.auth.api_key_header_name
        if api_key_header_name is None and variant.optional_auth is not None:
            api_key_header_name = variant.optional_auth.api_key_header_name
        auth_config = McpServerAuthConfig(
            api_key_header_name=api_key_header_name,
            scopes=variant.auth.oauth_scopes,
        )
        return await self._create_server_record(
            user_id=user_id,
            label=request.label or preset.label,
            url=url,
            transport=variant.transport,
            auth_type=variant.auth.auth_type,
            enabled=request.enabled,
            non_admin_tools_enabled=request.non_admin_tools_enabled,
            allowed_tools=request.allowed_tools or (),
            auth_config=auth_config,
            safe_metadata=preset_safe_metadata(preset, variant),
        )

    async def _create_server_record(
        self,
        user_id: str,
        label: str,
        url: str,
        transport: McpTransportType,
        auth_type: McpAuthType,
        enabled: bool,
        non_admin_tools_enabled: bool,
        allowed_tools: tuple[str, ...],
        auth_config: McpServerAuthConfig,
        safe_metadata: dict[str, object],
    ) -> McpServerResponse:
        validated_url = await self._validated_server_url(url)
        record = UserMcpServerRecord(
            user_id=user_id,
            label=label,
            transport=transport.value,
            url=validated_url,
            auth_type=auth_type.value,
            enabled=enabled,
            non_admin_tools_enabled=non_admin_tools_enabled,
            trust_state=McpTrustState.UNTRUSTED.value,
            status=initial_status(
                enabled=enabled,
                auth_type=auth_type,
            ).value,
            auth_config=auth_config.model_dump(mode="json"),
            safe_metadata=safe_metadata,
            allowed_tools=list(allowed_tools),
        )
        self._session.add(record)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise McpServerLabelConflictError(label) from exc
        await self._session.refresh(record)
        return await self.response_from_record(record)

    async def list_servers(self, user_id: str) -> tuple[McpServerResponse, ...]:
        statement = (
            select(UserMcpServerRecord)
            .where(col(UserMcpServerRecord.user_id) == user_id)
            .order_by(col(UserMcpServerRecord.label))
        )
        result = await self._session.exec(statement)
        records = result.all()
        return tuple([await self.response_from_record(record) for record in records])

    async def get_server(
        self,
        user_id: str,
        server_id: str,
    ) -> McpServerResponse:
        return await self.response_from_record(
            await self.get_server_record(user_id=user_id, server_id=server_id),
        )

    async def get_server_record(
        self,
        user_id: str,
        server_id: str,
    ) -> UserMcpServerRecord:
        statement = select(UserMcpServerRecord).where(
            col(UserMcpServerRecord.id) == server_id,
            col(UserMcpServerRecord.user_id) == user_id,
        )
        result = await self._session.exec(statement)
        record = result.one_or_none()
        if record is None:
            raise McpServerNotFoundError(server_id)
        return record

    async def update_server(
        self,
        user_id: str,
        server_id: str,
        request: McpServerUpdateRequest,
    ) -> McpServerResponse:
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        if request.label is not None:
            record.label = request.label
        if request.url is not None:
            record.url = await self._validated_server_url(request.url)
            record.trust_state = McpTrustState.UNTRUSTED.value
            record.status = initial_status(
                enabled=record.enabled,
                auth_type=McpAuthType(record.auth_type),
            ).value
            record.status_message = None
        if request.transport is not None:
            record.transport = request.transport.value
        if request.enabled is not None:
            record.enabled = request.enabled
            record.status = initial_status(
                enabled=request.enabled,
                auth_type=McpAuthType(record.auth_type),
            ).value
        if request.non_admin_tools_enabled is not None:
            record.non_admin_tools_enabled = request.non_admin_tools_enabled
        if request.allowed_tools is not None:
            record.allowed_tools = list(request.allowed_tools)
        if request.api_key_header_name is not None:
            config = auth_config_from_record(record)
            record.auth_config = config.model_copy(
                update={"api_key_header_name": request.api_key_header_name},
            ).model_dump(mode="json")
        record.updated_at = utc_now()
        await self._session.commit()
        await self._session.refresh(record)
        return await self.response_from_record(record)

    async def _validated_server_url(self, value: str) -> str:
        try:
            return await self._url_policy.validate_url(value)
        except ValueError as exc:
            raise McpServerUrlPolicyError(MCP_SERVER_URL_NOT_PERMITTED_MESSAGE) from exc

    async def delete_server(self, user_id: str, server_id: str) -> None:
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        await self._secret_store.delete_auth_for_server(user_id=user_id, server_id=server_id)
        await self._session.delete(record)
        await self._session.commit()

    async def store_static_auth(
        self,
        user_id: str,
        server_id: str,
        request: McpStaticAuthRequest,
    ) -> McpServerResponse:
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        auth_type = McpAuthType(record.auth_type)
        safe_metadata = safe_metadata_from_record(record.safe_metadata)
        optional_auth = optional_auth_for_record(record, safe_metadata)
        effective_auth_type = (
            optional_auth.auth_type if auth_type == McpAuthType.NONE and optional_auth is not None else auth_type
        )
        if effective_auth_type not in {
            McpAuthType.STATIC_BEARER,
            McpAuthType.API_KEY_HEADER,
        }:
            raise McpInvalidAuthTypeError(server_id=server_id, auth_type=auth_type.value)

        secret_kind = (
            McpSecretKind.STATIC_BEARER if effective_auth_type == McpAuthType.STATIC_BEARER else McpSecretKind.API_KEY
        )
        if effective_auth_type == McpAuthType.API_KEY_HEADER:
            header_name = request.api_key_header_name
            if optional_auth is not None:
                expected_header_name = optional_auth.api_key_header_name
                if (
                    header_name is not None
                    and expected_header_name is not None
                    and header_name.lower() != expected_header_name.lower()
                ):
                    message = "Optional preset credentials must use the configured API key header"
                    raise McpPresetConfigurationError(message)
                header_name = expected_header_name
            await self._update_api_key_header(record, header_name)

        await self._secret_store.upsert_secret(
            user_id=user_id,
            server_id=server_id,
            secret_kind=secret_kind,
            secret=request.secret,
        )
        await self._mark_auth_ready(record)
        return await self.response_from_record(record)

    async def store_client_credentials_auth(
        self,
        user_id: str,
        server_id: str,
        request: McpClientCredentialsAuthRequest,
    ) -> McpServerResponse:
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        if McpAuthType(record.auth_type) != McpAuthType.OAUTH_CLIENT_CREDENTIALS:
            raise McpInvalidAuthTypeError(
                server_id=server_id,
                auth_type=McpAuthType.OAUTH_CLIENT_CREDENTIALS.value,
            )

        client_info, token = await self._oauth_client.client_credentials_token(request)
        await self._secret_store.store_oauth_client_info(user_id, server_id, client_info)
        await self._secret_store.store_oauth_token(user_id, server_id, token)
        await self._mark_auth_ready(record)
        return await self.response_from_record(record)

    async def store_private_key_jwt_auth(
        self,
        user_id: str,
        server_id: str,
        request: McpPrivateKeyJwtAuthRequest,
    ) -> McpServerResponse:
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        if McpAuthType(record.auth_type) != McpAuthType.PRIVATE_KEY_JWT:
            raise McpInvalidAuthTypeError(
                server_id=server_id,
                auth_type=McpAuthType.PRIVATE_KEY_JWT.value,
            )

        client_info, token = await self._oauth_client.private_key_jwt_token(request)
        await self._secret_store.store_oauth_client_info(user_id, server_id, client_info)
        await self._secret_store.store_private_key_material(
            user_id=user_id,
            server_id=server_id,
            private_key=request.private_key,
            algorithm=request.algorithm,
        )
        await self._secret_store.store_oauth_token(user_id, server_id, token)
        await self._mark_auth_ready(record)
        return await self.response_from_record(record)

    async def start_oauth(
        self,
        user_id: str,
        server_id: str,
        request: McpOAuthStartRequest,
    ) -> McpOAuthStartResponse:
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        brokered_monzo = is_brokered_monzo(record)
        if McpAuthType(record.auth_type) != McpAuthType.OAUTH_AUTHORIZATION_CODE and not brokered_monzo:
            raise McpInvalidAuthTypeError(
                server_id=server_id,
                auth_type=McpAuthType.OAUTH_AUTHORIZATION_CODE.value,
            )

        signed_state = self._require_oauth_state_signer().sign(
            user_id=user_id,
            server_id=server_id,
        )
        oauth_request = await self._oauth_request_with_stored_client(
            user_id=user_id,
            server_id=server_id,
            request=request,
        )
        if brokered_monzo:
            client_id = oauth_request.client_id
            client_secret = oauth_request.client_secret
            if client_id is None or client_secret is None:
                message = "Monzo OAuth client ID and secret must be supplied together"
                raise McpOAuthConfigurationError(message)
            validate_monzo_oauth_client_credentials(
                client_id=client_id,
                client_secret=client_secret,
            )
            oauth_request = oauth_request.model_copy(
                update={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "authorization_endpoint": MONZO_AUTHORIZATION_ENDPOINT,
                    "token_endpoint": MONZO_TOKEN_ENDPOINT,
                    "registration_endpoint": None,
                    "token_endpoint_auth_method": (McpTokenEndpointAuthMethod.CLIENT_SECRET_POST),
                    "scopes": (),
                },
            )
        if brokered_monzo:
            prepared = await self._oauth_client.prepare_authorization(
                server_url=record.url,
                request=oauth_request,
                state=signed_state,
                use_pkce=False,
            )
        else:
            prepared = await self._oauth_client.prepare_authorization(
                server_url=record.url,
                request=oauth_request,
                state=signed_state,
            )
        await self._secret_store.store_oauth_client_info(
            user_id=user_id,
            server_id=server_id,
            client_info=prepared.client_info,
        )
        state_record = await self._secret_store.store_oauth_state(
            user_id=user_id,
            server_id=server_id,
            state=prepared.state,
            redirect_uri=oauth_request.redirect_uri,
            authorization_url=prepared.authorization_url,
            code_verifier=prepared.code_verifier,
        )
        record.status = McpServerStatus.NEEDS_AUTH.value
        record.updated_at = utc_now()
        await self._session.commit()
        return McpOAuthStartResponse(
            authorization_url=prepared.authorization_url,
            expires_at=state_record.expires_at,
        )

    async def _oauth_request_with_stored_client(
        self,
        user_id: str,
        server_id: str,
        request: McpOAuthStartRequest,
    ) -> McpOAuthStartRequest:
        if request.client_id is not None or request.client_secret is not None:
            return request
        existing_client = await self._secret_store.load_oauth_client_info(
            user_id,
            server_id,
        )
        if existing_client is None:
            return request
        return request.model_copy(
            update={
                "client_id": existing_client.client_id,
                "client_secret": (
                    None if existing_client.client_secret is None else SecretStr(existing_client.client_secret)
                ),
                "scopes": request.scopes or existing_client.scopes,
                "token_endpoint_auth_method": (
                    request.token_endpoint_auth_method or existing_client.token_endpoint_auth_method
                ),
            },
        )

    async def complete_oauth(
        self,
        server_id: str,
        code: str,
        state: str,
    ) -> McpOAuthCallbackResult:
        signed_state = self._require_oauth_state_signer().verify(
            state,
            server_id=server_id,
        )
        consumed_state = await self._secret_store.consume_oauth_state(
            server_id=server_id,
            state=state,
            requires_code_verifier=not is_brokered_monzo(
                await self.get_server_record(
                    user_id=signed_state.user_id,
                    server_id=server_id,
                )
            ),
        )
        if consumed_state.user_id != signed_state.user_id:
            raise McpOAuthStateError
        record = await self.get_server_record(
            user_id=consumed_state.user_id,
            server_id=server_id,
        )
        client_info = await self._secret_store.load_oauth_client_info(
            user_id=consumed_state.user_id,
            server_id=server_id,
        )
        if client_info is None:
            raise McpAuthRequiredError(server_id)

        token = await self._oauth_client.exchange_authorization_code(
            client_info=client_info,
            code=code,
            redirect_uri=consumed_state.redirect_uri,
            code_verifier=consumed_state.code_verifier,
        )
        await self._secret_store.store_oauth_token(
            user_id=consumed_state.user_id,
            server_id=server_id,
            token=token,
        )
        await self._mark_auth_ready(record)
        return McpOAuthCallbackResult.completed(server_id=server_id)

    def _require_oauth_state_signer(self) -> McpOAuthStateSigner:
        if self._oauth_state_signer is None:
            message = "MCP OAuth state signing is not configured"
            raise McpOAuthConfigurationError(message)
        return self._oauth_state_signer

    async def revoke_auth(self, user_id: str, server_id: str) -> McpServerResponse:
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        optional_auth = optional_auth_for_record(
            record,
            safe_metadata_from_record(record.safe_metadata),
        )
        await self._secret_store.delete_auth_for_server(user_id=user_id, server_id=server_id)
        record.status = (
            McpServerStatus.DISABLED.value
            if not record.enabled
            else (McpServerStatus.UNTESTED.value if optional_auth is not None else McpServerStatus.NEEDS_AUTH.value)
        )
        record.status_message = None
        record.updated_at = utc_now()
        await self._session.commit()
        await self._session.refresh(record)
        return await self.response_from_record(record)

    async def test_server(
        self,
        user_id: str,
        server_id: str,
    ) -> McpServerResponse:
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        await self._require_provider_auth(record)
        auth = await strategy_for_auth_type(McpAuthType(record.auth_type)).auth_for_connection(
            server=record,
            dependencies=self._auth_dependencies(),
        )
        if is_brokered_monzo(record):
            auth.headers[MCP_CREDENTIAL_DELEGATION_HEADER] = f"Bearer {self._sign_credential_delegation(record)}"
        capabilities = await self._mcp_client.discover_capabilities(
            McpClientConnectionConfig(
                url=record.url,
                transport=McpTransportType(record.transport),
                headers=auth.headers,
            ),
        )
        apply_discovered_capabilities(record, capabilities)
        record.trust_state = McpTrustState.TRUSTED.value
        record.status = McpServerStatus.CONNECTED.value
        record.status_message = None
        record.updated_at = utc_now()
        await self._session.commit()
        await self._session.refresh(record)
        return await self.response_from_record(record)

    async def cache_discovered_capabilities(
        self,
        *,
        user_id: str,
        server_id: str,
        expected_url: str,
        expected_transport: McpTransportType,
        capabilities: DiscoveredMcpCapabilities,
    ) -> bool:
        """Cache live MCP definitions when the persisted endpoint is unchanged."""
        record = await self.get_server_record(user_id=user_id, server_id=server_id)
        if record.url != expected_url or record.transport != expected_transport.value:
            return False
        apply_discovered_capabilities(record, capabilities)
        record.updated_at = utc_now()
        await self._session.commit()
        await self._session.refresh(record)
        return True

    async def enabled_toolset_configs(
        self,
        user_id: str,
    ) -> tuple[UserMcpToolsetConfig, ...]:
        statement = select(UserMcpServerRecord).where(
            col(UserMcpServerRecord.user_id) == user_id,
            col(UserMcpServerRecord.enabled).is_(True),
            col(UserMcpServerRecord.trust_state) == McpTrustState.TRUSTED.value,
            col(UserMcpServerRecord.status) == McpServerStatus.CONNECTED.value,
        )
        result = await self._session.exec(statement)
        configs: list[UserMcpToolsetConfig] = []
        for record in result.all():
            allowed_tools = tuple(record.allowed_tools)
            if not allowed_tools:
                continue
            safe_metadata = safe_metadata_from_record(record.safe_metadata)
            try:
                auth = await strategy_for_auth_type(
                    McpAuthType(record.auth_type),
                ).auth_for_connection(
                    server=record,
                    dependencies=self._auth_dependencies(),
                )
                if is_brokered_monzo(record):
                    await self._require_provider_auth(record)
                    auth.headers[MCP_CREDENTIAL_DELEGATION_HEADER] = (
                        f"Bearer {self._sign_credential_delegation(record)}"
                    )
            except (McpAuthRequiredError, McpOAuthConfigurationError):
                record.status = McpServerStatus.NEEDS_AUTH.value
                record.updated_at = utc_now()
                continue
            configs.append(
                UserMcpToolsetConfig(
                    id=f"user-mcp-{record.id}",
                    server_id=record.id,
                    label=record.label,
                    url=record.url,
                    transport=McpTransportType(record.transport),
                    headers=auth.headers,
                    allowed_tools=allowed_tools,
                    tool_definitions=safe_metadata.tool_definitions,
                    preset_id=runtime_preset_id_for_record(
                        record,
                        safe_metadata,
                    ),
                    logo=safe_metadata.logo or logo_from_url(record.url, record.label),
                ),
            )
        await self._session.commit()
        return tuple(configs)

    async def response_from_record(
        self,
        record: UserMcpServerRecord,
    ) -> McpServerResponse:
        safe_metadata = safe_metadata_from_record(record.safe_metadata)
        auth_summary = await strategy_for_auth_type(
            McpAuthType(record.auth_type),
        ).status(
            server=record,
            dependencies=self._auth_dependencies(),
        )
        optional_auth = optional_auth_for_record(record, safe_metadata)
        optional_auth_configured = False
        if optional_auth is not None:
            optional_auth_summary = await self._secret_store.load_secret_summary(
                record.user_id,
                record.id,
                McpSecretKind.API_KEY,
            )
            optional_auth_configured = optional_auth_summary.authenticated
        provider_auth = await self._provider_auth_summary(record) if is_brokered_monzo(record) else None
        return McpServerResponse(
            id=record.id,
            label=record.label,
            url=record.url,
            transport=McpTransportType(record.transport),
            auth_type=McpAuthType(record.auth_type),
            enabled=record.enabled,
            non_admin_tools_enabled=record.non_admin_tools_enabled,
            trust_state=McpTrustState(record.trust_state),
            status=McpServerStatus(record.status),
            status_message=record.status_message,
            allowed_tools=tuple(record.allowed_tools),
            discovered_tools=tuple(record.discovered_tools),
            safe_metadata=safe_metadata,
            logo=safe_metadata.logo or logo_from_url(record.url, record.label),
            auth=auth_summary,
            optional_auth=optional_auth,
            optional_auth_configured=optional_auth_configured,
            provider_auth=provider_auth,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def _update_api_key_header(
        self,
        record: UserMcpServerRecord,
        api_key_header_name: str | None,
    ) -> None:
        config = auth_config_from_record(record)
        header_name = api_key_header_name or config.api_key_header_name
        if header_name is None:
            raise McpAuthRequiredError(record.id)
        record.auth_config = McpServerAuthConfig(
            api_key_header_name=header_name,
        ).model_dump(mode="json")

    async def _mark_auth_ready(self, record: UserMcpServerRecord) -> None:
        if is_brokered_monzo(record):
            endpoint_auth = await self._secret_store.load_secret_summary(
                record.user_id,
                record.id,
                McpSecretKind.STATIC_BEARER,
            )
            provider_auth = await self._provider_auth_summary(record)
            ready = endpoint_auth.authenticated and provider_auth.authenticated
        else:
            ready = True
        record.status = (
            McpServerStatus.DISABLED.value
            if not record.enabled
            else (McpServerStatus.UNTESTED.value if ready else McpServerStatus.NEEDS_AUTH.value)
        )
        record.status_message = None
        record.updated_at = utc_now()
        await self._session.commit()
        await self._session.refresh(record)

    def _auth_dependencies(self) -> McpAuthStrategyDependencies:
        return McpAuthStrategyDependencies(
            secret_store=self._secret_store,
            oauth_client=self._oauth_client,
        )

    async def _provider_auth_summary(
        self,
        record: UserMcpServerRecord,
    ) -> McpAuthSummary:
        token = await self._secret_store.load_oauth_token(
            record.user_id,
            record.id,
        )
        if token is None:
            return McpAuthSummary(authenticated=False, needs_reauth=True)
        return McpAuthSummary(
            authenticated=True,
            needs_reauth=token.expires_at is not None
            and ensure_aware(token.expires_at) <= utc_now()
            and token.refresh_token is None,
            expires_at=token.expires_at,
            scopes=token.scopes,
        )

    async def _require_provider_auth(
        self,
        record: UserMcpServerRecord,
    ) -> None:
        if not is_brokered_monzo(record):
            return
        summary = await self._provider_auth_summary(record)
        if not summary.authenticated or summary.needs_reauth:
            raise McpAuthRequiredError(record.id)

    def _sign_credential_delegation(
        self,
        record: UserMcpServerRecord,
    ) -> str:
        if self._credential_delegation_signer is None:
            message = "MCP credential delegation signing is not configured"
            raise McpOAuthConfigurationError(message)
        return self._credential_delegation_signer.sign(
            user_id=record.user_id,
            server_id=record.id,
            provider=DELEGATION_PROVIDER_MONZO,
        )


def initial_status(enabled: bool, auth_type: McpAuthType) -> McpServerStatus:
    if not enabled:
        return McpServerStatus.DISABLED
    if auth_type == McpAuthType.NONE:
        return McpServerStatus.UNTESTED
    return McpServerStatus.NEEDS_AUTH


def validate_monzo_oauth_client_credentials(
    *,
    client_id: str,
    client_secret: SecretStr,
) -> None:
    if client_id.strip().startswith("user_"):
        message = "Use the Monzo OAuth client ID, not the API Playground user ID"
        raise McpOAuthConfigurationError(message)
    secret_value = client_secret.get_secret_value().strip()
    if secret_value.startswith("eyJ") and secret_value.count(".") == 2:
        message = "Use the Monzo OAuth client secret, not an API Playground access token"
        raise McpOAuthConfigurationError(message)


def is_brokered_monzo(record: UserMcpServerRecord) -> bool:
    return record.safe_metadata.get("preset_id") == MONZO_PRESET_ID


def mcp_secret_reference(
    user_id: str,
    record_id: str,
    purpose: SecretPurpose,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=MCP_ENCRYPTION_PROVIDER,
        credential_id=record_id,
        purpose=purpose,
    )


def model_secret_json(model: McpOAuthClientInfo | McpOAuthToken) -> str:
    return orjson.dumps(model.model_dump(mode="json")).decode()


def metadata_scopes(metadata: dict[str, object]) -> tuple[str, ...]:
    scopes = metadata.get("scopes")
    if not isinstance(scopes, list):
        return ()
    return tuple(scope for scope in scopes if isinstance(scope, str))


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def safe_metadata_from_record(metadata: dict[str, object]) -> McpServerSafeMetadata:
    try:
        return McpServerSafeMetadata.model_validate(metadata)
    except ValueError:
        return McpServerSafeMetadata()


def runtime_preset_id_for_record(
    record: UserMcpServerRecord,
    safe_metadata: McpServerSafeMetadata,
) -> str | None:
    if safe_metadata.preset_id is None:
        return None
    preset = find_mcp_service_preset(safe_metadata.preset_id)
    if preset is None:
        return None
    variant = find_mcp_service_preset_variant(
        preset,
        safe_metadata.preset_variant_id,
    )
    if (
        variant is None
        or variant.compatibility != McpPresetCompatibility.DIRECT_REMOTE
        or variant.url != record.url
        or variant.transport.value != record.transport
    ):
        return None
    return preset.id


def optional_auth_for_record(
    record: UserMcpServerRecord,
    safe_metadata: McpServerSafeMetadata,
) -> McpPresetAuthGuide | None:
    if McpAuthType(record.auth_type) != McpAuthType.NONE or safe_metadata.preset_id is None:
        return None
    preset = find_mcp_service_preset(safe_metadata.preset_id)
    if preset is None:
        return None
    selected_variant = find_mcp_service_preset_variant(
        preset,
        safe_metadata.preset_variant_id,
    )
    if selected_variant is not None and preset_variant_matches_record(
        selected_variant,
        record,
    ):
        return selected_variant.optional_auth
    fallback_variant = next(
        (variant for variant in preset.variants if preset_variant_matches_record(variant, record)),
        None,
    )
    return None if fallback_variant is None else fallback_variant.optional_auth


def preset_variant_matches_record(
    variant: McpServicePresetVariant,
    record: UserMcpServerRecord,
) -> bool:
    return (
        variant.auth.auth_type == McpAuthType.NONE
        and variant.url == record.url
        and variant.transport.value == record.transport
    )


def safe_metadata_with_discovered_tools(
    metadata: dict[str, object],
    tool_definitions: tuple[DiscoveredMcpTool, ...],
    preferred_tool_names: Sequence[str] = (),
) -> dict[str, object]:
    safe_metadata = safe_metadata_from_record(metadata)
    guidance_by_name = {guidance.name: guidance for guidance in safe_metadata.tool_guidance}
    tools_by_name = {tool.name: tool for tool in tool_definitions}
    ordered_tools = [tools_by_name[name] for name in preferred_tool_names if name in tools_by_name]
    preferred_names = {tool.name for tool in ordered_tools}
    ordered_tools.extend(tool for tool in tool_definitions if tool.name not in preferred_names)
    discovered_definitions = tuple(
        discovered_tool_definition(tool, guidance_by_name.get(tool.name))
        for tool in ordered_tools[:MAX_DISCOVERED_TOOL_DEFINITIONS]
    )
    updated_metadata = safe_metadata.model_copy(
        update={"tool_definitions": discovered_definitions},
    )
    return updated_metadata.model_dump(mode="json")


def apply_discovered_capabilities(
    record: UserMcpServerRecord,
    capabilities: DiscoveredMcpCapabilities,
) -> None:
    record.discovered_tools = list(capabilities.tools)
    record.safe_metadata = safe_metadata_with_discovered_tools(
        record.safe_metadata,
        capabilities.tool_definitions,
        preferred_tool_names=tuple(record.allowed_tools),
    )


def discovered_tool_definition(
    tool: DiscoveredMcpTool,
    guidance: McpPresetToolGuide | None,
) -> McpDiscoveredToolDefinition:
    description = tool.description.strip() or tool.name
    return McpDiscoveredToolDefinition(
        name=tool.name,
        description=description[:MAX_TOOL_DESCRIPTION_CHARS],
        input_schema=bounded_input_schema(tool.input_schema),
        guidance=guidance,
    )


def bounded_input_schema(input_schema: dict[str, object]) -> dict[str, object]:
    try:
        encoded_schema = orjson.dumps(input_schema)
    except TypeError:
        return {}
    if len(encoded_schema) > MAX_TOOL_SCHEMA_BYTES:
        return {}
    return input_schema
