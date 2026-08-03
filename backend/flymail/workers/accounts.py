"""Worker-only mailbox verification and deferred account cleanup handlers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import quote

from flymail.domain.errors import UnsafeEndpointError
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.infrastructure.security.outbound import (
    EndpointResolver,
    resolve_host,
    validate_public_endpoint,
)
from flymail.providers.errors import ProviderError, ProviderErrorCode
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.accounts import (
    AccountRepository,
    CredentialRepository,
    MailAccount,
    ProxyRepository,
)
from flymail.repositories.base import TenantContext
from flymail.repositories.outbox import OutboxRepository
from flymail.workers.dispatcher import JobContext, JobOutcome


class AccountVerificationGateway(Protocol):
    async def verify(
        self,
        *,
        account: MailAccount,
        credential_type: str,
        credential: bytes,
        endpoint_config: Mapping[str, Mapping[str, object]],
        proxy_url: str | None,
    ) -> None: ...


class AccountCleanupGateway(Protocol):
    async def cleanup(self, *, user_uid: str, account_id: str) -> None: ...


def _required_payload_text(payload: Mapping[str, object], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


class AccountVerificationHandler:
    """Load credentials inside Worker, revalidate endpoints, and verify remotely."""

    def __init__(
        self,
        pool: DatabasePool,
        master_secret: str,
        gateway: AccountVerificationGateway,
        *,
        registry: ProviderRegistry | None = None,
        endpoint_resolver: EndpointResolver = resolve_host,
        allow_private_endpoints: bool = False,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        self.cipher = CredentialCipher.from_master_secret(master_secret)
        self.gateway = gateway
        self.registry = registry or ProviderRegistry.default()
        self.endpoint_resolver = endpoint_resolver
        self.allow_private_endpoints = bool(allow_private_endpoints)

    def _endpoint_config(self, account: MailAccount) -> dict[str, dict[str, object]]:
        raw = dict(account.endpoint_config)
        if not raw:
            endpoints = self.registry.get(account.provider_key).default_endpoints()
            if endpoints.imap is None or endpoints.smtp is None:
                raise UnsafeEndpointError("mail account endpoints are unavailable")
            raw = {
                "imap": {
                    "host": endpoints.imap.host,
                    "port": endpoints.imap.port,
                    "security": endpoints.imap.security.value,
                },
                "smtp": {
                    "host": endpoints.smtp.host,
                    "port": endpoints.smtp.port,
                    "security": endpoints.smtp.security.value,
                },
            }
        normalized: dict[str, dict[str, object]] = {}
        for protocol in ("imap", "smtp"):
            item = raw.get(protocol)
            if not isinstance(item, Mapping):
                raise UnsafeEndpointError("mail endpoint configuration is incomplete")
            host = validate_public_endpoint(
                str(item.get("host") or ""),
                int(item.get("port") or 0),
                resolver=self.endpoint_resolver,
                allow_private=self.allow_private_endpoints,
            )
            normalized[protocol] = {
                "host": host,
                "port": int(item.get("port") or 0),
                "security": str(item.get("security") or ""),
            }
        return normalized

    def _proxy_url(self, record) -> str | None:
        if record is None or not record.proxy.enabled:
            return None
        validate_public_endpoint(
            record.proxy.host,
            record.proxy.port,
            resolver=self.endpoint_resolver,
            allow_private=self.allow_private_endpoints,
        )
        username = ""
        password = ""
        if record.value is not None:
            decoded = json.loads(
                self.cipher.decrypt(record.proxy.id, record.value).decode("utf-8")
            )
            if isinstance(decoded, dict):
                username = str(decoded.get("username") or "")
                password = str(decoded.get("password") or "")
        auth = ""
        if username:
            auth = quote(username, safe="")
            if password:
                auth += f":{quote(password, safe='')}"
            auth += "@"
        return f"{record.proxy.scheme}://{auth}{record.proxy.host}:{record.proxy.port}"

    async def __call__(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        if not context.user_uid or not context.account_id or not context.provider_key:
            return JobOutcome.fail("InvalidAccountScope", "account verification scope is invalid")
        account_id = _required_payload_text(payload, "account_id")
        if account_id != context.account_id:
            return JobOutcome.fail("InvalidAccountScope", "account verification scope is invalid")
        try:
            requested_version = int(payload.get("credential_version") or 0)
        except (TypeError, ValueError):
            return JobOutcome.fail("InvalidCredentialVersion", "credential version is invalid")
        tenant = TenantContext(context.user_uid)
        async with self.pool.acquire() as connection:
            account = await AccountRepository(connection).get_account(tenant, account_id)
            credential = await CredentialRepository(connection).get_encrypted(tenant, account_id)
            proxy_record = await ProxyRepository(connection).get_user_proxy(tenant)
        if account is None or account.provider_key != context.provider_key:
            return JobOutcome.fail("AccountNotFound", "mail account was not found")
        if credential is None:
            return JobOutcome.fail("CredentialNotFound", "mail account credential was not found")
        if credential.credential_version != requested_version:
            return JobOutcome.success()
        try:
            endpoints = self._endpoint_config(account)
            proxy_url = self._proxy_url(proxy_record)
        except UnsafeEndpointError:
            return JobOutcome.fail("UnsafeEndpoint", "mail endpoint is not publicly routable")
        plaintext = self.cipher.decrypt(account.id, credential.value)
        try:
            await self.gateway.verify(
                account=account,
                credential_type=credential.credential_type,
                credential=plaintext,
                endpoint_config=endpoints,
                proxy_url=proxy_url,
            )
        except ProviderError as exc:
            if exc.retryable:
                return JobOutcome.retry(exc.code.value, exc.safe_detail)
            if exc.code not in {
                ProviderErrorCode.AUTHENTICATION_FAILED,
                ProviderErrorCode.AUTHORIZATION_REQUIRED,
            }:
                return JobOutcome.fail(exc.code.value, exc.safe_detail)
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    current = await CredentialRepository(connection).get_encrypted(
                        tenant,
                        account.id,
                    )
                    if current is None or current.credential_version != requested_version:
                        await connection.rollback()
                        return JobOutcome.success()
                    accounts = AccountRepository(connection)
                    if not await accounts.update_status(
                        tenant,
                        account.id,
                        "auth_required",
                    ):
                        raise RuntimeError(
                            "mail account disappeared during verification"
                        )
                    await accounts.ensure_runtime_state(
                        tenant,
                        account.id,
                        status="auth_required",
                    )
                    await OutboxRepository(
                        connection,
                        tenant,
                        trace_id=context.job_id,
                    ).append(
                        "account.authorization_required",
                        account.id,
                        {
                            "account_id": account.id,
                            "reason_code": exc.code.value,
                        },
                    )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
            return JobOutcome.fail(exc.code.value, exc.safe_detail)

        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                current = await CredentialRepository(connection).get_encrypted(
                    tenant,
                    account.id,
                )
                if current is None or current.credential_version != requested_version:
                    await connection.rollback()
                    return JobOutcome.success()
                accounts = AccountRepository(connection)
                if not await accounts.update_status(tenant, account.id, "active"):
                    raise RuntimeError("mail account disappeared during verification")
                await accounts.ensure_runtime_state(tenant, account.id, status="normal")
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=context.job_id,
                ).append(
                    "account.verified",
                    account.id,
                    {
                        "account_id": account.id,
                        "credential_version": requested_version,
                    },
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return JobOutcome.success()


class AccountCleanupHandler:
    """Execute deferred cleanup only after the account entered deleting state."""

    def __init__(
        self,
        pool: DatabasePool,
        gateway: AccountCleanupGateway,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        self.gateway = gateway

    async def __call__(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        if not context.user_uid or context.account_id is not None:
            return JobOutcome.fail("InvalidCleanupScope", "account cleanup scope is invalid")
        account_id = _required_payload_text(payload, "account_id")
        tenant = TenantContext(context.user_uid)
        async with self.pool.acquire() as connection:
            account = await AccountRepository(connection).get_account(tenant, account_id)
        if account is None:
            return JobOutcome.success()
        if account.status != "deleting":
            return JobOutcome.fail("AccountNotDeleting", "mail account is not deleting")

        await self.gateway.cleanup(user_uid=tenant.user_uid, account_id=account.id)

        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                accounts = AccountRepository(connection)
                current = await accounts.get_account(tenant, account.id)
                if current is None:
                    await OutboxRepository(
                        connection,
                        tenant,
                        trace_id=context.job_id,
                    ).append(
                        "account.cleanup_completed",
                        account.id,
                        {"account_id": account.id},
                    )
                    await connection.commit()
                    return JobOutcome.success()
                if current.status != "deleting":
                    await connection.rollback()
                    return JobOutcome.fail(
                        "AccountNotDeleting",
                        "mail account is not deleting",
                    )
                if not await accounts.update_status(tenant, account.id, "disabled"):
                    raise RuntimeError("mail account disappeared during cleanup")
                await accounts.ensure_runtime_state(tenant, account.id, status="disabled")
                await OutboxRepository(
                    connection,
                    tenant,
                    trace_id=context.job_id,
                ).append(
                    "account.cleanup_completed",
                    account.id,
                    {"account_id": account.id},
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return JobOutcome.success()
