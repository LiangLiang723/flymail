"""Tenant-safe loading and refresh of provider account runtime credentials."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote

from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.infrastructure.security.outbound import (
    EndpointResolver,
    resolve_host,
    validate_public_endpoint,
)
from flymail.providers.contracts import ServiceEndpoint
from flymail.providers.network import (
    ResolvedAccountEndpoints,
    RuntimeCredential,
    decode_runtime_credential,
    resolve_account_endpoints,
)
from flymail.providers.oauth import ProductionOAuthGateway
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.accounts import (
    AccountRepository,
    CredentialRepository,
    MailAccount,
    ProxyRepository,
)
from flymail.repositories.base import TenantContext


@dataclass(frozen=True, slots=True, repr=False)
class LoadedProviderAccount:
    account: MailAccount
    endpoints: ResolvedAccountEndpoints
    credential: RuntimeCredential = field(repr=False)
    proxy_url: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return (
            "LoadedProviderAccount("
            f"account_id={self.account.id!r}, user_uid={self.account.user_uid!r}, "
            f"provider_key={self.account.provider_key!r}, "
            f"has_proxy={bool(self.proxy_url)!r}, auth_kind={self.credential.auth_kind!r})"
        )


class ProviderAccountLoader:
    def __init__(
        self,
        pool: DatabasePool,
        master_secret: str,
        *,
        registry: ProviderRegistry | None = None,
        oauth_gateway: ProductionOAuthGateway | None = None,
        endpoint_resolver: EndpointResolver = resolve_host,
        allow_private_endpoints: bool = False,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        self.cipher = CredentialCipher.from_master_secret(master_secret)
        self.registry = registry or ProviderRegistry.default()
        self.oauth_gateway = oauth_gateway or ProductionOAuthGateway()
        self.endpoint_resolver = endpoint_resolver
        self.allow_private_endpoints = bool(allow_private_endpoints)
        self.now_fn = now_fn

    async def load(
        self,
        account_id: str,
        *,
        expected_user_uid: str | None = None,
        require_active: bool = True,
    ) -> LoadedProviderAccount:
        normalized_account = str(account_id or "").strip()
        if not normalized_account:
            raise ValueError("account_id is required")
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT user_uid FROM mail_accounts WHERE id=%s",
                    (normalized_account,),
                )
                row = await cursor.fetchone()
            if row is None:
                raise ValueError("mail account was not found")
            user_uid = str(row[0])
            if expected_user_uid is not None and user_uid != str(expected_user_uid):
                raise ValueError("mail account does not belong to expected user")
            tenant = TenantContext(user_uid)
            account = await AccountRepository(connection).get_account(tenant, normalized_account)
            encrypted = await CredentialRepository(connection).get_encrypted(tenant, normalized_account)
            proxy_record = await ProxyRepository(connection).get_user_proxy(tenant)
        if account is None or encrypted is None:
            raise ValueError("mail account credential was not found")
        if require_active and account.status != "active":
            raise ValueError("mail account is not active")

        plaintext = self.cipher.decrypt(account.id, encrypted.value)
        credential = decode_runtime_credential(
            account,
            encrypted.credential_type,
            plaintext,
        )
        proxy_url = self._proxy_url(proxy_record)
        if (
            credential.auth_kind == "oauth"
            and credential.expires_at > 0
            and credential.expires_at <= float(self.now_fn()) + 300
        ):
            credential = await self._refresh(
                tenant,
                account,
                encrypted.credential_version,
                credential,
                proxy_url,
            )
        return LoadedProviderAccount(
            account=account,
            endpoints=self._validated_endpoints(
                resolve_account_endpoints(account, self.registry)
            ),
            credential=credential,
            proxy_url=proxy_url,
        )

    def _validated_endpoints(
        self,
        endpoints: ResolvedAccountEndpoints,
    ) -> ResolvedAccountEndpoints:
        return ResolvedAccountEndpoints(
            imap=ServiceEndpoint(
                validate_public_endpoint(
                    endpoints.imap.host,
                    endpoints.imap.port,
                    resolver=self.endpoint_resolver,
                    allow_private=self.allow_private_endpoints,
                ),
                endpoints.imap.port,
                endpoints.imap.security,
            ),
            smtp=ServiceEndpoint(
                validate_public_endpoint(
                    endpoints.smtp.host,
                    endpoints.smtp.port,
                    resolver=self.endpoint_resolver,
                    allow_private=self.allow_private_endpoints,
                ),
                endpoints.smtp.port,
                endpoints.smtp.security,
            ),
        )

    def _proxy_url(self, record) -> str | None:
        if record is None or not record.proxy.enabled:
            return None
        proxy_host = validate_public_endpoint(
            record.proxy.host,
            record.proxy.port,
            resolver=self.endpoint_resolver,
            allow_private=self.allow_private_endpoints,
        )
        username = ""
        password = ""
        if record.value is not None:
            payload = json.loads(
                self.cipher.decrypt(record.proxy.id, record.value).decode("utf-8")
            )
            if not isinstance(payload, dict):
                raise ValueError("proxy credential is invalid")
            username = str(payload.get("username") or "")
            password = str(payload.get("password") or "")
        auth = ""
        if username:
            auth = quote(username, safe="")
            if password:
                auth += f":{quote(password, safe='')}"
            auth += "@"
        return (
            f"{record.proxy.scheme}://{auth}"
            f"{proxy_host}:{record.proxy.port}"
        )

    async def _refresh(
        self,
        tenant: TenantContext,
        account: MailAccount,
        expected_version: int,
        credential: RuntimeCredential,
        proxy_url: str | None,
    ) -> RuntimeCredential:
        if not credential.refresh_token:
            raise ValueError("OAuth authorization must be renewed")
        token = await self.oauth_gateway.refresh_token(
            provider_key=account.provider_key,
            refresh_token=credential.refresh_token,
            proxy_url=proxy_url,
        )
        token.setdefault("refresh_token", credential.refresh_token)
        encoded = json.dumps(
            token,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = CredentialRepository(connection)
                current = await repository.get_encrypted(tenant, account.id)
                if current is None:
                    raise ValueError("mail account credential was not found")
                if current.credential_version != expected_version:
                    await connection.rollback()
                    plaintext = self.cipher.decrypt(account.id, current.value)
                    return decode_runtime_credential(
                        account,
                        current.credential_type,
                        plaintext,
                    )
                refreshed = await repository.store_encrypted(
                    tenant,
                    account.id,
                    credential_type="oauth",
                    value=self.cipher.encrypt(account.id, encoded),
                    expires_at=(
                        float(token["expires_at"])
                        if token.get("expires_at") is not None
                        else None
                    ),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        plaintext = self.cipher.decrypt(account.id, refreshed.value)
        return decode_runtime_credential(account, refreshed.credential_type, plaintext)


__all__ = ["LoadedProviderAccount", "ProviderAccountLoader"]
