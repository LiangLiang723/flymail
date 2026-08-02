"""Mailbox account application transactions for FlyMail V2."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote, urlsplit, urlunsplit

from cryptography.exceptions import InvalidSignature

from flymail.domain.errors import (
    ConflictError,
    NotFoundError,
    UnsafeEndpointError,
    UnsupportedProviderError,
)
from flymail.domain.ids import new_id
from flymail.infrastructure.db.pool import DatabasePool
from flymail.application.personal import sanitize_signature_html
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.infrastructure.security.outbound import EndpointResolver, resolve_host, validate_public_endpoint
from flymail.infrastructure.security.sessions import (
    new_session_token,
    sign_session_cookie,
    verify_session_cookie,
)
from flymail.providers.contracts import ServiceEndpoint, TransportSecurity
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.accounts import (
    AccountRepository,
    CredentialRepository,
    IdentityRepository,
    MailAccount,
    MailIdentity,
    OAuthStateRecord,
    OAuthStateRepository,
    OutboundProxy,
    ProxyRepository,
)
from flymail.repositories.audit import AuditRepository
from flymail.repositories.base import TenantContext, normalize_email
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.outbox import OutboxRepository


@dataclass(frozen=True, slots=True)
class CreateAccountCommand:
    provider_key: str
    email: str
    display_name: str
    credential_type: str
    credential: str
    endpoint_config: dict | None = None
    poll_interval_seconds: int = 300


@dataclass(frozen=True, slots=True)
class UpdateAccountCommand:
    display_name: str | None = None
    remark: str | None = None
    group_name: str | None = None
    poll_interval_seconds: int | None = None
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class SaveProxyCommand:
    scheme: str
    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class UpsertIdentityCommand:
    from_address: str
    display_name: str = ""
    reply_to: str = ""
    signature_html: str = ""
    signature_text: str = ""
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class UpdateIdentityCommand:
    display_name: str | None = None
    reply_to: str | None = None
    signature_html: str | None = None
    signature_text: str | None = None
    is_default: bool | None = None


@dataclass(frozen=True, slots=True)
class UpdateCredentialCommand:
    credential_type: str
    credential: str


@dataclass(frozen=True, slots=True)
class DeleteAccountResult:
    account: MailAccount
    cleanup_job_id: str


@dataclass(frozen=True, slots=True)
class StartOAuthCommand:
    provider_key: str
    email: str
    display_name: str
    redirect_uri: str
    account_id: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthStartResult:
    state: str
    account_id: str
    authorization_url: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class OAuthCallbackResult:
    account: MailAccount
    job_id: str


class OAuthGateway(Protocol):
    def build_authorization_url(
        self,
        *,
        provider_key: str,
        state: str,
        code_challenge: str,
        redirect_uri: str,
        proxy_url: str | None,
    ) -> str: ...

    async def exchange_code(
        self,
        *,
        provider_key: str,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        proxy_url: str | None,
    ) -> dict: ...


class _UnconfiguredOAuthGateway:
    def build_authorization_url(self, **_kwargs) -> str:
        raise ConflictError("OAuth provider client is not configured")

    async def exchange_code(self, **_kwargs) -> dict:
        raise ConflictError("OAuth provider client is not configured")


IdentityPolicy = Callable[[MailAccount, str], bool]


def _primary_identity_policy(account: MailAccount, from_address: str) -> bool:
    return normalize_email(from_address) == account.normalized_email


class AccountsService:
    """Coordinate tenant-scoped account mutations on explicit transactions."""

    def __init__(
        self,
        pool: DatabasePool,
        master_secret: str,
        *,
        registry: ProviderRegistry | None = None,
        endpoint_resolver: EndpointResolver = resolve_host,
        allow_private_endpoints: bool = False,
        identity_policy: IdentityPolicy = _primary_identity_policy,
        oauth_gateway: OAuthGateway | None = None,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        normalized_secret = str(master_secret or "")
        self.cipher = CredentialCipher.from_master_secret(normalized_secret)
        self.state_secret = normalized_secret.encode("utf-8")
        self.registry = registry or ProviderRegistry.default()
        self.endpoint_resolver = endpoint_resolver
        self.allow_private_endpoints = bool(allow_private_endpoints)
        self.identity_policy = identity_policy
        self.oauth_gateway = oauth_gateway or _UnconfiguredOAuthGateway()
        self.now_fn = now_fn

    def _provider_plugin(self, provider_key: str):
        normalized = str(provider_key or "").strip().casefold()
        try:
            return self.registry.get(normalized)
        except KeyError:
            raise UnsupportedProviderError("mail provider is not supported") from None

    @staticmethod
    def _validate_redirect_uri(value: str) -> str:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise UnsafeEndpointError("OAuth redirect URI must use HTTPS")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise UnsafeEndpointError("OAuth redirect URI is invalid")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))

    def _decode_state(self, state: str) -> tuple[str, str]:
        try:
            payload = verify_session_cookie(state, self.state_secret)
            state_id, raw = payload.split(":", 1)
            if not state_id or not raw:
                raise InvalidSignature
            return state_id, hashlib.sha256(state.encode("utf-8")).hexdigest()
        except (InvalidSignature, ValueError):
            raise NotFoundError("OAuth state was not found") from None

    async def _proxy_url(
        self,
        connection,
        tenant: TenantContext,
    ) -> str | None:
        record = await ProxyRepository(connection).get_user_proxy(tenant)
        if record is None or not record.proxy.enabled:
            return None
        validate_public_endpoint(
            record.proxy.host,
            record.proxy.port,
            resolver=self.endpoint_resolver,
            allow_private=self.allow_private_endpoints,
        )
        credentials = {"username": "", "password": ""}
        if record.value is not None:
            decoded = json.loads(
                self.cipher.decrypt(record.proxy.id, record.value).decode("utf-8")
            )
            if isinstance(decoded, dict):
                credentials = {
                    "username": str(decoded.get("username") or ""),
                    "password": str(decoded.get("password") or ""),
                }
        auth = ""
        if credentials["username"]:
            auth = quote(credentials["username"], safe="")
            if credentials["password"]:
                auth += f":{quote(credentials['password'], safe='')}"
            auth += "@"
        return f"{record.proxy.scheme}://{auth}{record.proxy.host}:{record.proxy.port}"

    def _normalize_endpoint_config(
        self,
        provider_key: str,
        value: dict | None,
    ) -> dict:
        plugin = self._provider_plugin(provider_key)
        provider_endpoints = plugin.default_endpoints()
        raw = dict(value or {})
        if provider_endpoints.user_supplied and not raw:
            raise UnsafeEndpointError("custom provider endpoints are required")
        if not provider_endpoints.user_supplied and raw:
            raise UnsafeEndpointError("fixed provider endpoints cannot be overridden")
        if not raw:
            return {}

        normalized: dict[str, dict[str, object]] = {}
        for protocol in ("imap", "smtp"):
            item = raw.get(protocol)
            if not isinstance(item, dict):
                raise UnsafeEndpointError("both IMAP and SMTP endpoints are required")
            try:
                security = TransportSecurity(str(item.get("security") or "").casefold())
                endpoint = ServiceEndpoint(
                    host=str(item.get("host") or ""),
                    port=int(item.get("port") or 0),
                    security=security,
                )
            except (TypeError, ValueError) as exc:
                raise UnsafeEndpointError("mail endpoint configuration is invalid") from exc
            host = validate_public_endpoint(
                endpoint.host,
                endpoint.port,
                resolver=self.endpoint_resolver,
                allow_private=self.allow_private_endpoints,
            )
            normalized[protocol] = {
                "host": host,
                "port": endpoint.port,
                "security": endpoint.security.value,
            }
        return normalized

    async def start_oauth(
        self,
        tenant: TenantContext,
        session_id: str,
        command: StartOAuthCommand,
        *,
        request_id: str,
    ) -> OAuthStartResult:
        provider_key = str(command.provider_key or "").strip().casefold()
        plugin = self._provider_plugin(provider_key)
        if not plugin.capabilities().supports_oauth:
            raise ConflictError("provider does not support OAuth")
        redirect_uri = self._validate_redirect_uri(command.redirect_uri)
        now = float(self.now_fn())
        expires_at = now + 600
        state_id = new_id("oauth")
        raw_state, _state_token_hash = new_session_token()
        state = sign_session_cookie(f"{state_id}:{raw_state}", self.state_secret)
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        verifier, _verifier_hash = new_session_token()
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")

        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                accounts = AccountRepository(connection)
                account = None
                if command.account_id:
                    account = await accounts.get_account(tenant, command.account_id)
                    if account is None:
                        raise NotFoundError("mail account not found")
                    if account.provider_key != provider_key:
                        raise ConflictError("OAuth provider does not match account")
                else:
                    account = await accounts.create_account(
                        tenant,
                        provider_key=provider_key,
                        email=command.email,
                        display_name=command.display_name,
                        status="pending",
                        poll_interval_seconds=plugin.capabilities().recommended_poll_seconds,
                    )
                    await IdentityRepository(connection).create_identity(
                        tenant,
                        account.id,
                        from_address=account.email,
                        display_name=account.display_name,
                        is_default=True,
                        is_verified=True,
                    )
                    await accounts.ensure_runtime_state(
                        tenant,
                        account.id,
                        status="normal",
                    )
                encrypted_verifier = self.cipher.encrypt(
                    account.id,
                    verifier.encode("ascii"),
                )
                await OAuthStateRepository(connection).create(
                    tenant,
                    state_id=state_id,
                    session_id=session_id,
                    provider_key=provider_key,
                    account_id=account.id,
                    state_hash=state_hash,
                    verifier=encrypted_verifier,
                    redirect_uri=redirect_uri,
                    expires_at=expires_at,
                    now=now,
                )
                proxy_url = await self._proxy_url(connection, tenant)
                authorization_url = self.oauth_gateway.build_authorization_url(
                    provider_key=provider_key,
                    state=state,
                    code_challenge=challenge,
                    redirect_uri=redirect_uri,
                    proxy_url=proxy_url,
                )
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.oauth_started",
                    account.id,
                    {
                        "account_id": account.id,
                        "provider_key": provider_key,
                        "state_id": state_id,
                    },
                )
                await AuditRepository(connection).append(
                    event_type="account.oauth_started",
                    result_code="pending",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="account",
                    resource_id=account.id,
                    safe_metadata={"provider_key": provider_key, "state_id": state_id},
                    now=now,
                )
                await connection.commit()
                return OAuthStartResult(
                    state=state,
                    account_id=account.id,
                    authorization_url=authorization_url,
                    expires_at=expires_at,
                )
            except Exception:
                await connection.rollback()
                raise

    async def oauth_status(
        self,
        tenant: TenantContext,
        session_id: str,
        state: str,
    ) -> str:
        _state_id, state_hash = self._decode_state(state)
        async with self.pool.acquire() as connection:
            record = await OAuthStateRepository(connection).get_by_hash(
                tenant,
                session_id,
                state_hash,
            )
        if record is None:
            raise NotFoundError("OAuth state was not found")
        if record.consumed_at is not None:
            return "consumed"
        if record.expires_at <= float(self.now_fn()):
            return "expired"
        return "pending"

    async def complete_oauth(
        self,
        tenant: TenantContext,
        session_id: str,
        *,
        state: str,
        code: str,
        request_id: str,
    ) -> OAuthCallbackResult:
        state_id, state_hash = self._decode_state(state)
        now = float(self.now_fn())
        record: OAuthStateRecord
        proxy_url: str | None
        verifier: str
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = OAuthStateRepository(connection)
                loaded = await repository.get_by_hash(
                    tenant,
                    session_id,
                    state_hash,
                    for_update=True,
                )
                if loaded is None or loaded.id != state_id:
                    raise NotFoundError("OAuth state was not found")
                if loaded.consumed_at is not None:
                    raise ConflictError("OAuth state was already consumed")
                if loaded.expires_at <= now:
                    raise ConflictError("OAuth state expired")
                verifier = self.cipher.decrypt(
                    loaded.account_id,
                    loaded.verifier,
                ).decode("ascii")
                proxy_url = await self._proxy_url(connection, tenant)
                if not await repository.consume(loaded.id, now=now):
                    raise ConflictError("OAuth state was already consumed")
                await connection.commit()
                record = loaded
            except Exception:
                await connection.rollback()
                raise

        token_payload = await self.oauth_gateway.exchange_code(
            provider_key=record.provider_key,
            code=str(code or ""),
            code_verifier=verifier,
            redirect_uri=record.redirect_uri,
            proxy_url=proxy_url,
        )
        if not isinstance(token_payload, dict) or not token_payload:
            raise ConflictError("OAuth token exchange returned no credentials")
        encoded_token = json.dumps(
            token_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        token_expiry = token_payload.get("expires_at")
        expires_at = float(token_expiry) if token_expiry is not None else None

        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                account = await AccountRepository(connection).get_account(
                    tenant,
                    record.account_id,
                )
                if account is None or account.provider_key != record.provider_key:
                    raise NotFoundError("mail account not found")
                encrypted = self.cipher.encrypt(account.id, encoded_token)
                credential = await CredentialRepository(connection).store_encrypted(
                    tenant,
                    account.id,
                    credential_type="oauth",
                    value=encrypted,
                    expires_at=expires_at,
                )
                job_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="account.verify",
                        user_uid=tenant.user_uid,
                        account_id=account.id,
                        provider_key=account.provider_key,
                        priority=0,
                        dedupe_key=(
                            f"account-verify:{account.id}:{credential.credential_version}"
                        ),
                        payload={
                            "account_id": account.id,
                            "credential_version": credential.credential_version,
                        },
                    )
                )
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.oauth_completed",
                    account.id,
                    {"account_id": account.id, "job_id": job_id},
                )
                await AuditRepository(connection).append(
                    event_type="account.oauth_completed",
                    result_code="queued",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="account",
                    resource_id=account.id,
                    safe_metadata={"provider_key": account.provider_key, "job_id": job_id},
                    now=now,
                )
                await connection.commit()
                return OAuthCallbackResult(account=account, job_id=job_id)
            except Exception:
                await connection.rollback()
                raise

    async def list_identities(
        self,
        tenant: TenantContext,
        account_id: str,
    ) -> tuple[MailIdentity, ...]:
        await self.get_account(tenant, account_id)
        async with self.pool.acquire() as connection:
            identities = await IdentityRepository(connection).list_identities(
                tenant,
                account_id,
            )
        return tuple(identities)

    async def create_identity(
        self,
        tenant: TenantContext,
        account_id: str,
        command: UpsertIdentityCommand,
        *,
        request_id: str,
    ) -> MailIdentity:
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                account = await AccountRepository(connection).get_account(tenant, account_id)
                if account is None:
                    raise NotFoundError("mail account not found")
                if not self.identity_policy(account, command.from_address):
                    raise ConflictError("sender address is not provider verified")
                identity = await IdentityRepository(connection).create_identity(
                    tenant,
                    account.id,
                    from_address=command.from_address,
                    display_name=command.display_name,
                    reply_to=command.reply_to,
                    signature_html=sanitize_signature_html(command.signature_html),
                    signature_text=command.signature_text,
                    is_default=command.is_default,
                    is_verified=True,
                )
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.identity_created",
                    account.id,
                    {"account_id": account.id, "identity_id": identity.id},
                )
                await AuditRepository(connection).append(
                    event_type="account.identity_created",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="identity",
                    resource_id=identity.id,
                    safe_metadata={"account_id": account.id},
                )
                await connection.commit()
                return identity
            except Exception:
                await connection.rollback()
                raise

    async def update_identity(
        self,
        tenant: TenantContext,
        account_id: str,
        identity_id: str,
        command: UpdateIdentityCommand,
        *,
        request_id: str,
    ) -> MailIdentity:
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = IdentityRepository(connection)
                current = await repository.get_identity(tenant, account_id, identity_id)
                if current is None:
                    raise NotFoundError("mail identity not found")
                identity = await repository.update_identity(
                    tenant,
                    account_id,
                    identity_id,
                    display_name=(
                        current.display_name
                        if command.display_name is None
                        else command.display_name
                    ),
                    reply_to=(
                        current.reply_to
                        if command.reply_to is None
                        else command.reply_to
                    ),
                    signature_html=(
                        current.signature_html
                        if command.signature_html is None
                        else sanitize_signature_html(command.signature_html)
                    ),
                    signature_text=(
                        current.signature_text
                        if command.signature_text is None
                        else command.signature_text
                    ),
                    is_default=(
                        current.is_default
                        if command.is_default is None
                        else command.is_default
                    ),
                )
                if identity is None:
                    raise NotFoundError("mail identity not found")
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.identity_updated",
                    account_id,
                    {"account_id": account_id, "identity_id": identity.id},
                )
                await AuditRepository(connection).append(
                    event_type="account.identity_updated",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="identity",
                    resource_id=identity.id,
                    safe_metadata={"account_id": account_id},
                )
                await connection.commit()
                return identity
            except Exception:
                await connection.rollback()
                raise

    async def get_oauth_proxy(self, tenant: TenantContext) -> OutboundProxy | None:
        async with self.pool.acquire() as connection:
            record = await ProxyRepository(connection).get_user_proxy(tenant)
        return record.proxy if record is not None else None

    async def save_oauth_proxy(
        self,
        tenant: TenantContext,
        command: SaveProxyCommand,
        *,
        request_id: str,
    ) -> OutboundProxy:
        scheme = str(command.scheme or "").strip().casefold()
        if scheme != "http":
            raise UnsafeEndpointError("proxy must use http scheme")
        host = validate_public_endpoint(
            command.host,
            command.port,
            resolver=self.endpoint_resolver,
            allow_private=self.allow_private_endpoints,
        )
        username = str(command.username or "")
        password = str(command.password or "")
        if password and not username:
            raise ValueError("proxy username is required when password is supplied")

        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = ProxyRepository(connection)
                existing = await repository.get_user_proxy(tenant, for_update=True)
                proxy_id = existing.proxy.id if existing is not None else new_id("prx")
                encrypted = None
                if username:
                    plaintext = json.dumps(
                        {"username": username, "password": password},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    encrypted = self.cipher.encrypt(proxy_id, plaintext)
                proxy = await repository.store_user_proxy(
                    tenant,
                    proxy_id=proxy_id,
                    scheme=scheme,
                    host=host,
                    port=command.port,
                    value=encrypted,
                )
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.oauth_proxy_updated",
                    proxy.id,
                    {
                        "proxy_id": proxy.id,
                        "host": proxy.host,
                        "port": proxy.port,
                        "enabled": proxy.enabled,
                    },
                    aggregate_type="proxy",
                )
                await AuditRepository(connection).append(
                    event_type="account.oauth_proxy_updated",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="proxy",
                    resource_id=proxy.id,
                    safe_metadata={
                        "host": proxy.host,
                        "port": proxy.port,
                        "enabled": proxy.enabled,
                    },
                )
                await connection.commit()
                return proxy
            except Exception:
                await connection.rollback()
                raise

    async def list_accounts(self, tenant: TenantContext) -> tuple[MailAccount, ...]:
        async with self.pool.acquire() as connection:
            accounts = await AccountRepository(connection).list_accounts(tenant)
        return tuple(accounts)

    async def get_account(
        self,
        tenant: TenantContext,
        account_id: str,
    ) -> MailAccount:
        async with self.pool.acquire() as connection:
            account = await AccountRepository(connection).get_account(tenant, account_id)
        if account is None:
            raise NotFoundError("mail account not found")
        return account

    async def update_account(
        self,
        tenant: TenantContext,
        account_id: str,
        command: UpdateAccountCommand,
        *,
        request_id: str,
    ) -> MailAccount:
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                accounts = AccountRepository(connection)
                current = await accounts.get_account(
                    tenant,
                    account_id,
                    for_update=True,
                )
                if current is None:
                    raise NotFoundError("mail account not found")
                if current.status == "deleting" and command.enabled is not None:
                    raise ConflictError("deleting account status cannot be changed")

                account = await accounts.update_account(
                    tenant,
                    current.id,
                    display_name=(
                        current.display_name
                        if command.display_name is None
                        else command.display_name
                    ),
                    remark=current.remark if command.remark is None else command.remark,
                    group_name=(
                        current.group_name
                        if command.group_name is None
                        else command.group_name
                    ),
                    poll_interval_seconds=(
                        current.poll_interval_seconds
                        if command.poll_interval_seconds is None
                        else command.poll_interval_seconds
                    ),
                )
                if account is None:
                    raise NotFoundError("mail account not found")

                jobs = JobRepository(connection)
                cancelled_jobs = 0
                verification_job_id: str | None = None
                if command.enabled is False:
                    if not await accounts.update_status(tenant, account.id, "disabled"):
                        raise NotFoundError("mail account not found")
                    await accounts.ensure_runtime_state(
                        tenant,
                        account.id,
                        status="disabled",
                    )
                    cancelled_jobs = await jobs.cancel_pending_non_send_for_account(
                        tenant.user_uid,
                        account.id,
                        now=float(self.now_fn()),
                    )
                elif command.enabled is True and current.status in {
                    "disabled",
                    "auth_required",
                }:
                    credential = await CredentialRepository(connection).get_encrypted(
                        tenant,
                        account.id,
                    )
                    if credential is None:
                        await accounts.update_status(tenant, account.id, "auth_required")
                        await accounts.ensure_runtime_state(
                            tenant,
                            account.id,
                            status="auth_required",
                        )
                    else:
                        await accounts.update_status(tenant, account.id, "pending")
                        await accounts.ensure_runtime_state(
                            tenant,
                            account.id,
                            status="normal",
                        )
                        verification_job_id = await jobs.enqueue(
                            JobSpec(
                                queue_name="interactive",
                                job_kind="account.verify",
                                user_uid=tenant.user_uid,
                                account_id=account.id,
                                provider_key=account.provider_key,
                                priority=0,
                                dedupe_key=(
                                    f"account-verify:{account.id}:"
                                    f"{credential.credential_version}"
                                ),
                                payload={
                                    "account_id": account.id,
                                    "credential_version": (
                                        credential.credential_version
                                    ),
                                },
                            )
                        )

                updated = await accounts.get_account(tenant, account.id)
                if updated is None:
                    raise NotFoundError("mail account not found")
                event_payload: dict[str, object] = {
                    "account_id": updated.id,
                    "provider_key": updated.provider_key,
                    "status": updated.status,
                    "cancelled_jobs": cancelled_jobs,
                }
                if verification_job_id is not None:
                    event_payload["verification_job_id"] = verification_job_id
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.updated",
                    updated.id,
                    event_payload,
                )
                await AuditRepository(connection).append(
                    event_type="account.updated",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="account",
                    resource_id=updated.id,
                    safe_metadata={
                        "provider_key": updated.provider_key,
                        "status": updated.status,
                        "cancelled_jobs": cancelled_jobs,
                        "verification_queued": verification_job_id is not None,
                    },
                )
                await connection.commit()
                return updated
            except Exception:
                await connection.rollback()
                raise

    async def update_credential(
        self,
        tenant: TenantContext,
        account_id: str,
        command: UpdateCredentialCommand,
        *,
        request_id: str,
    ) -> str:
        credential_type = str(command.credential_type or "").strip().casefold()
        credential_value = str(command.credential or "")
        if credential_type not in {"password", "authorization_code"}:
            raise ValueError("unsupported credential type")
        if not credential_value:
            raise ValueError("credential is required")
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                accounts = AccountRepository(connection)
                account = await accounts.get_account(tenant, account_id)
                if account is None:
                    raise NotFoundError("mail account not found")
                encrypted = self.cipher.encrypt(
                    account.id,
                    credential_value.encode("utf-8"),
                )
                stored = await CredentialRepository(connection).store_encrypted(
                    tenant,
                    account.id,
                    credential_type=credential_type,
                    value=encrypted,
                )
                await accounts.update_status(tenant, account.id, "pending")
                await accounts.ensure_runtime_state(tenant, account.id, status="normal")
                job_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="account.verify",
                        user_uid=tenant.user_uid,
                        account_id=account.id,
                        provider_key=account.provider_key,
                        priority=0,
                        dedupe_key=(
                            f"account-verify:{account.id}:{stored.credential_version}"
                        ),
                        payload={
                            "account_id": account.id,
                            "credential_version": stored.credential_version,
                        },
                    )
                )
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.credential_updated",
                    account.id,
                    {"account_id": account.id, "job_id": job_id},
                )
                await AuditRepository(connection).append(
                    event_type="account.credential_updated",
                    result_code="queued",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="account",
                    resource_id=account.id,
                    safe_metadata={
                        "credential_version": stored.credential_version,
                        "job_id": job_id,
                    },
                )
                await connection.commit()
                return job_id
            except Exception:
                await connection.rollback()
                raise

    async def delete_account(
        self,
        tenant: TenantContext,
        account_id: str,
        *,
        confirm_email: str,
        request_id: str,
    ) -> DeleteAccountResult:
        now = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                accounts = AccountRepository(connection)
                account = await accounts.get_account(tenant, account_id)
                if account is None:
                    raise NotFoundError("mail account not found")
                if normalize_email(confirm_email) != account.normalized_email:
                    raise ConflictError("account email confirmation does not match")
                jobs = JobRepository(connection)
                if await jobs.has_uncertain_send(tenant.user_uid, account.id):
                    raise ConflictError("account has an unresolved send result")
                if not await accounts.update_status(tenant, account.id, "deleting"):
                    raise NotFoundError("mail account not found")
                await accounts.ensure_runtime_state(
                    tenant,
                    account.id,
                    status="disabled",
                )
                cancelled = await jobs.cancel_pending_non_send_for_account(
                    tenant.user_uid,
                    account.id,
                    now=now,
                )
                cleanup_job_id = await jobs.enqueue(
                    JobSpec(
                        queue_name="maintenance",
                        job_kind="account.cleanup",
                        user_uid=tenant.user_uid,
                        priority=100,
                        dedupe_key=f"account-cleanup:{account.id}",
                        payload={"account_id": account.id},
                    ),
                    now=now,
                )
                updated = await accounts.get_account(tenant, account.id)
                if updated is None:
                    raise NotFoundError("mail account not found")
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.deletion_requested",
                    account.id,
                    {
                        "account_id": account.id,
                        "cleanup_job_id": cleanup_job_id,
                        "cancelled_jobs": cancelled,
                    },
                )
                await AuditRepository(connection).append(
                    event_type="account.deletion_requested",
                    result_code="queued",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="account",
                    resource_id=account.id,
                    safe_metadata={
                        "cleanup_job_id": cleanup_job_id,
                        "cancelled_jobs": cancelled,
                    },
                    now=now,
                )
                await connection.commit()
                return DeleteAccountResult(
                    account=updated,
                    cleanup_job_id=cleanup_job_id,
                )
            except Exception:
                await connection.rollback()
                raise

    async def request_verification(
        self,
        tenant: TenantContext,
        account_id: str,
        *,
        request_id: str,
    ) -> str:
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                account = await AccountRepository(connection).get_account(tenant, account_id)
                if account is None:
                    raise NotFoundError("mail account not found")
                credential = await CredentialRepository(connection).get_encrypted(
                    tenant,
                    account.id,
                )
                if credential is None:
                    raise ConflictError("mail account credential is missing")
                job_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="interactive",
                        job_kind="account.verify",
                        user_uid=tenant.user_uid,
                        account_id=account.id,
                        provider_key=account.provider_key,
                        priority=0,
                        dedupe_key=(
                            f"account-verify:{account.id}:{credential.credential_version}"
                        ),
                        payload={
                            "account_id": account.id,
                            "credential_version": credential.credential_version,
                        },
                    )
                )
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.verification_requested",
                    account.id,
                    {"account_id": account.id, "job_id": job_id},
                )
                await AuditRepository(connection).append(
                    event_type="account.verification_requested",
                    result_code="queued",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="account",
                    resource_id=account.id,
                    safe_metadata={"job_id": job_id},
                )
                await connection.commit()
                return job_id
            except Exception:
                await connection.rollback()
                raise

    async def create_account(
        self,
        tenant: TenantContext,
        command: CreateAccountCommand,
        *,
        request_id: str,
    ) -> MailAccount:
        provider_key = str(command.provider_key or "").strip().casefold()
        endpoint_config = self._normalize_endpoint_config(
            provider_key,
            command.endpoint_config,
        )
        credential_type = str(command.credential_type or "").strip().casefold()
        credential = str(command.credential or "")
        if credential_type not in {"password", "authorization_code"}:
            raise ValueError("unsupported credential type")
        if not credential:
            raise ValueError("credential is required")

        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                account_repository = AccountRepository(connection)
                account = await account_repository.create_account(
                    tenant,
                    provider_key=provider_key,
                    email=command.email,
                    display_name=command.display_name,
                    status="pending",
                    endpoint_config=endpoint_config,
                    poll_interval_seconds=command.poll_interval_seconds,
                )
                encrypted = self.cipher.encrypt(
                    account.id,
                    credential.encode("utf-8"),
                )
                await CredentialRepository(connection).store_encrypted(
                    tenant,
                    account.id,
                    credential_type=credential_type,
                    value=encrypted,
                )
                await IdentityRepository(connection).create_identity(
                    tenant,
                    account.id,
                    from_address=account.email,
                    display_name=account.display_name,
                    is_default=True,
                    is_verified=True,
                )
                await account_repository.ensure_runtime_state(
                    tenant,
                    account.id,
                    status="normal",
                )
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=request_id,
                ).append(
                    "account.created",
                    account.id,
                    {
                        "account_id": account.id,
                        "provider_key": account.provider_key,
                        "status": account.status,
                    },
                )
                await AuditRepository(connection).append(
                    event_type="account.created",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="account",
                    resource_id=account.id,
                    safe_metadata={"provider_key": account.provider_key},
                )
                await connection.commit()
                return account
            except Exception:
                await connection.rollback()
                raise
