"""Tenant-isolated mailbox account, identity, and credential repositories."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field

import aiomysql
import pymysql

from flymail.domain.errors import ConflictError, NotFoundError
from flymail.domain.ids import new_id
from flymail.infrastructure.security.credentials import EncryptedValue
from flymail.repositories.base import TenantContext, fetch_all, fetch_one, normalize_email


_ACCOUNT_STATUSES = {"pending", "active", "disabled", "auth_required", "deleting"}
_CREDENTIAL_TYPES = {"password", "authorization_code", "oauth"}


def _decode_json(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _encode_b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(bytes(value)).decode("ascii").rstrip("=")


def _decode_b64(value: str) -> bytes:
    padding = "=" * (-len(str(value)) % 4)
    return base64.b64decode(str(value) + padding, altchars=b"-_", validate=True)


@dataclass(frozen=True, slots=True)
class MailAccount:
    id: str
    user_uid: str
    provider_key: str
    email: str
    normalized_email: str
    display_name: str
    remark: str
    group_name: str
    status: str
    endpoint_config: dict
    icon_mode: str
    icon_value: str
    icon_object_sha256: str | None
    poll_interval_seconds: int
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class MailIdentity:
    id: str
    user_uid: str
    account_id: str
    from_address: str
    normalized_from_address: str
    display_name: str
    reply_to: str
    signature_html: str
    signature_text: str
    is_default: bool
    is_verified: bool
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class EncryptedCredentialRecord:
    id: str
    user_uid: str
    account_id: str
    credential_type: str
    value: EncryptedValue
    expires_at: float | None
    credential_version: int
    created_at: float
    updated_at: float
    auth_tag_b64: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class OutboundProxy:
    id: str
    user_uid: str
    scheme: str
    host: str
    port: int
    enabled: bool
    has_credentials: bool
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True, repr=False)
class EncryptedProxyRecord:
    proxy: OutboundProxy
    value: EncryptedValue | None


@dataclass(frozen=True, slots=True, repr=False)
class OAuthStateRecord:
    id: str
    user_uid: str
    session_id: str
    provider_key: str
    account_id: str
    state_hash: str
    pkce_algorithm: str
    verifier: EncryptedValue
    redirect_uri: str
    expires_at: float
    consumed_at: float | None
    created_at: float


def _map_account(row) -> MailAccount:
    return MailAccount(
        id=str(row["id"]),
        user_uid=str(row["user_uid"]),
        provider_key=str(row["provider_key"]),
        email=str(row["email"]),
        normalized_email=str(row["normalized_email"]),
        display_name=str(row["display_name"] or ""),
        remark=str(row["remark"] or ""),
        group_name=str(row["group_name"] or ""),
        status=str(row["status"]),
        endpoint_config=_decode_json(row["endpoint_config"]),
        icon_mode=str(row["icon_mode"] or "provider"),
        icon_value=str(row["icon_value"] or ""),
        icon_object_sha256=(
            str(row["icon_object_sha256"]) if row["icon_object_sha256"] else None
        ),
        poll_interval_seconds=int(row["poll_interval_seconds"] or 0),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
    )


def _map_identity(row) -> MailIdentity:
    return MailIdentity(
        id=str(row["id"]),
        user_uid=str(row["user_uid"]),
        account_id=str(row["account_id"]),
        from_address=str(row["from_address"]),
        normalized_from_address=str(row["normalized_from_address"]),
        display_name=str(row["display_name"] or ""),
        reply_to=str(row["reply_to"] or ""),
        signature_html=str(row["signature_html"] or ""),
        signature_text=str(row["signature_text"] or ""),
        is_default=bool(row["is_default"]),
        is_verified=bool(row["is_verified"]),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
    )


def _map_credential(row) -> EncryptedCredentialRecord:
    nonce = bytes(row["nonce"])
    ciphertext = bytes(row["ciphertext"])
    auth_tag = bytes(row["auth_tag"]) if row["auth_tag"] else b""
    return EncryptedCredentialRecord(
        id=str(row["id"]),
        user_uid=str(row["user_uid"]),
        account_id=str(row["account_id"]),
        credential_type=str(row["credential_type"]),
        value=EncryptedValue(
            algorithm=str(row["algorithm"]),
            key_version=int(row["key_version"] or 0),
            nonce_b64=_encode_b64(nonce),
            ciphertext_b64=_encode_b64(ciphertext),
        ),
        expires_at=float(row["expires_at"]) if row["expires_at"] is not None else None,
        credential_version=int(row["credential_version"] or 0),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
        auth_tag_b64=_encode_b64(auth_tag) if auth_tag else "",
    )


_ACCOUNT_COLUMNS = """
    id, user_uid, provider_key, email, normalized_email, display_name,
    remark, group_name, status, endpoint_config, icon_mode, icon_value,
    icon_object_sha256, poll_interval_seconds, created_at, updated_at
"""

_IDENTITY_COLUMNS = """
    id, user_uid, account_id, from_address, normalized_from_address,
    display_name, reply_to, signature_html, signature_text, is_default,
    is_verified, created_at, updated_at
"""

_CREDENTIAL_COLUMNS = """
    id, user_uid, account_id, credential_type, algorithm, key_version,
    nonce, ciphertext, auth_tag, expires_at, credential_version,
    created_at, updated_at
"""

_PROXY_COLUMNS = """
    id, user_uid, proxy_scheme, host, port, enabled,
    password_algorithm, password_key_version, password_nonce,
    password_ciphertext, created_at, updated_at
"""

_OAUTH_STATE_COLUMNS = """
    id, user_uid, session_id, provider_key, account_draft_id,
    state_hash, pkce_algorithm, pkce_key_version, pkce_nonce,
    pkce_ciphertext, redirect_uri, expires_at, consumed_at, created_at
"""


class AccountRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def create_account(
        self,
        tenant: TenantContext,
        *,
        provider_key: str,
        email: str,
        display_name: str = "",
        status: str = "pending",
        endpoint_config: dict | None = None,
        poll_interval_seconds: int = 300,
    ) -> MailAccount:
        provider = str(provider_key or "").strip()
        display_email = str(email or "").strip()
        if not provider:
            raise ValueError("provider_key is required")
        normalized_email = normalize_email(display_email)
        if status not in _ACCOUNT_STATUSES:
            raise ValueError("unsupported account status")
        poll_interval = int(poll_interval_seconds)
        if poll_interval < 5 or poll_interval > 3600:
            raise ValueError("poll interval must be between 5 and 3600 seconds")

        account_id = new_id("acc")
        now = time.time()
        try:
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, remark, group_name, status, endpoint_config,
                        icon_mode, icon_value, icon_object_sha256,
                        poll_interval_seconds, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, '', '', %s, %s,
                              'provider', '', NULL, %s, %s, %s)
                    """,
                    (
                        account_id,
                        tenant.user_uid,
                        provider,
                        display_email,
                        normalized_email,
                        str(display_name or "").strip(),
                        status,
                        json.dumps(endpoint_config or {}, ensure_ascii=False, sort_keys=True),
                        poll_interval,
                        now,
                        now,
                    ),
                )
        except pymysql.err.IntegrityError as exc:
            if int(exc.args[0] or 0) == 1062:
                raise ConflictError("mail account already exists") from None
            raise

        return MailAccount(
            id=account_id,
            user_uid=tenant.user_uid,
            provider_key=provider,
            email=display_email,
            normalized_email=normalized_email,
            display_name=str(display_name or "").strip(),
            remark="",
            group_name="",
            status=status,
            endpoint_config=dict(endpoint_config or {}),
            icon_mode="provider",
            icon_value="",
            icon_object_sha256=None,
            poll_interval_seconds=poll_interval,
            created_at=now,
            updated_at=now,
        )

    async def get_account(
        self,
        tenant: TenantContext,
        account_id: str,
        *,
        for_update: bool = False,
    ) -> MailAccount | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            self.connection,
            f"""
            SELECT {_ACCOUNT_COLUMNS}
            FROM mail_accounts
            WHERE id = %s AND user_uid = %s{suffix}
            """,
            (str(account_id or "").strip(), tenant.user_uid),
        )
        return _map_account(row) if row else None

    async def list_accounts(self, tenant: TenantContext) -> list[MailAccount]:
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT {_ACCOUNT_COLUMNS}
            FROM mail_accounts
            WHERE user_uid = %s
            ORDER BY created_at ASC, id ASC
            """,
            (tenant.user_uid,),
        )
        return [_map_account(row) for row in rows]

    async def list_active_accounts_for_worker(self) -> list[MailAccount]:
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT {_ACCOUNT_COLUMNS}
            FROM mail_accounts
            WHERE status = 'active'
              AND EXISTS (
                  SELECT 1
                  FROM users
                  WHERE users.id = mail_accounts.user_uid
                    AND users.enabled = 1
              )
            ORDER BY user_uid ASC, id ASC
            """,
        )
        return [_map_account(row) for row in rows]

    async def update_account(
        self,
        tenant: TenantContext,
        account_id: str,
        *,
        display_name: str,
        remark: str,
        group_name: str,
        poll_interval_seconds: int,
    ) -> MailAccount | None:
        normalized_account_id = str(account_id or "").strip()
        poll_interval = int(poll_interval_seconds)
        if poll_interval < 5 or poll_interval > 3600:
            raise ValueError("poll interval must be between 5 and 3600 seconds")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE mail_accounts
                SET display_name = %s, remark = %s, group_name = %s,
                    poll_interval_seconds = %s, updated_at = %s
                WHERE id = %s AND user_uid = %s
                """,
                (
                    str(display_name or "").strip(),
                    str(remark or "").strip(),
                    str(group_name or "").strip(),
                    poll_interval,
                    time.time(),
                    normalized_account_id,
                    tenant.user_uid,
                ),
            )
        return await self.get_account(tenant, normalized_account_id)

    async def update_status(
        self,
        tenant: TenantContext,
        account_id: str,
        status: str,
    ) -> bool:
        if status not in _ACCOUNT_STATUSES:
            raise ValueError("unsupported account status")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE mail_accounts
                SET status = %s, updated_at = %s
                WHERE id = %s AND user_uid = %s
                """,
                (status, time.time(), str(account_id or "").strip(), tenant.user_uid),
            )
            changed = cursor.rowcount > 0
        if changed:
            return True
        return await self.get_account(
            tenant,
            str(account_id or "").strip(),
        ) is not None

    async def ensure_runtime_state(
        self,
        tenant: TenantContext,
        account_id: str,
        *,
        status: str = "normal",
    ) -> None:
        normalized_account_id = str(account_id or "").strip()
        if status not in {"active", "normal", "quiet", "degraded", "auth_required", "disabled"}:
            raise ValueError("unsupported account runtime status")
        owner = await self.get_account(tenant, normalized_account_id)
        if owner is None:
            raise NotFoundError("mail account not found")
        now = time.time()
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO account_runtime_state (
                    account_id, user_uid, status, idle_status,
                    last_activity_at, last_change_at, next_reconcile_at,
                    failure_count, backoff_until, last_error_class,
                    last_error_message, updated_at
                ) VALUES (%s, %s, %s, 'disconnected', 0, %s, %s, 0, 0, '', '', %s)
                AS incoming
                ON DUPLICATE KEY UPDATE
                    user_uid = incoming.user_uid,
                    status = incoming.status,
                    last_change_at = incoming.last_change_at,
                    next_reconcile_at = incoming.next_reconcile_at,
                    updated_at = incoming.updated_at
                """,
                (
                    normalized_account_id,
                    tenant.user_uid,
                    status,
                    now,
                    now,
                    now,
                ),
            )


class IdentityRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def create_identity(
        self,
        tenant: TenantContext,
        account_id: str,
        *,
        from_address: str,
        display_name: str = "",
        reply_to: str = "",
        signature_html: str = "",
        signature_text: str = "",
        is_default: bool = False,
        is_verified: bool = False,
    ) -> MailIdentity:
        normalized_account_id = str(account_id or "").strip()
        owner = await fetch_one(
            self.connection,
            """
            SELECT id
            FROM mail_accounts
            WHERE id = %s AND user_uid = %s
            """,
            (normalized_account_id, tenant.user_uid),
        )
        if owner is None:
            raise NotFoundError("mail account not found")

        display_address = str(from_address or "").strip()
        normalized_address = normalize_email(display_address)
        identity_id = new_id("ident")
        now = time.time()
        try:
            if is_default:
                async with self.connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE mail_identities
                        SET is_default = 0, updated_at = %s
                        WHERE account_id = %s AND user_uid = %s
                        """,
                        (now, normalized_account_id, tenant.user_uid),
                    )
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mail_identities (
                        id, user_uid, account_id, from_address,
                        normalized_from_address, display_name, reply_to,
                        signature_html, signature_text, is_default, is_verified,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s)
                    """,
                    (
                        identity_id,
                        tenant.user_uid,
                        normalized_account_id,
                        display_address,
                        normalized_address,
                        str(display_name or "").strip(),
                        str(reply_to or "").strip(),
                        signature_html or None,
                        signature_text or None,
                        1 if is_default else 0,
                        1 if is_verified else 0,
                        now,
                        now,
                    ),
                )
        except pymysql.err.IntegrityError as exc:
            if int(exc.args[0] or 0) == 1062:
                raise ConflictError("mail identity already exists") from None
            raise

        return MailIdentity(
            id=identity_id,
            user_uid=tenant.user_uid,
            account_id=normalized_account_id,
            from_address=display_address,
            normalized_from_address=normalized_address,
            display_name=str(display_name or "").strip(),
            reply_to=str(reply_to or "").strip(),
            signature_html=str(signature_html or ""),
            signature_text=str(signature_text or ""),
            is_default=bool(is_default),
            is_verified=bool(is_verified),
            created_at=now,
            updated_at=now,
        )

    async def list_identities(
        self,
        tenant: TenantContext,
        account_id: str,
    ) -> list[MailIdentity]:
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT {_IDENTITY_COLUMNS}
            FROM mail_identities
            WHERE user_uid = %s AND account_id = %s
            ORDER BY is_default DESC, created_at ASC, id ASC
            """,
            (tenant.user_uid, str(account_id or "").strip()),
        )
        return [_map_identity(row) for row in rows]

    async def get_identity(
        self,
        tenant: TenantContext,
        account_id: str,
        identity_id: str,
    ) -> MailIdentity | None:
        row = await fetch_one(
            self.connection,
            f"""
            SELECT {_IDENTITY_COLUMNS}
            FROM mail_identities
            WHERE id = %s AND account_id = %s AND user_uid = %s
            """,
            (
                str(identity_id or "").strip(),
                str(account_id or "").strip(),
                tenant.user_uid,
            ),
        )
        return _map_identity(row) if row else None

    async def update_identity(
        self,
        tenant: TenantContext,
        account_id: str,
        identity_id: str,
        *,
        display_name: str,
        reply_to: str,
        signature_html: str,
        signature_text: str,
        is_default: bool,
    ) -> MailIdentity | None:
        normalized_account = str(account_id or "").strip()
        normalized_identity = str(identity_id or "").strip()
        if is_default:
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE mail_identities
                    SET is_default = 0, updated_at = %s
                    WHERE account_id = %s AND user_uid = %s AND id <> %s
                    """,
                    (time.time(), normalized_account, tenant.user_uid, normalized_identity),
                )
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE mail_identities
                SET display_name = %s, reply_to = %s,
                    signature_html = %s, signature_text = %s,
                    is_default = %s, updated_at = %s
                WHERE id = %s AND account_id = %s AND user_uid = %s
                """,
                (
                    str(display_name or "").strip(),
                    str(reply_to or "").strip(),
                    signature_html or None,
                    signature_text or None,
                    1 if is_default else 0,
                    time.time(),
                    normalized_identity,
                    normalized_account,
                    tenant.user_uid,
                ),
            )
        return await self.get_identity(tenant, normalized_account, normalized_identity)


class ProxyRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _map(row) -> EncryptedProxyRecord:
        has_credentials = bool(row["password_ciphertext"])
        proxy = OutboundProxy(
            id=str(row["id"]),
            user_uid=str(row["user_uid"]),
            scheme=str(row["proxy_scheme"]),
            host=str(row["host"]),
            port=int(row["port"]),
            enabled=bool(row["enabled"]),
            has_credentials=has_credentials,
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
        )
        value = None
        if has_credentials:
            value = EncryptedValue(
                algorithm=str(row["password_algorithm"]),
                key_version=int(row["password_key_version"] or 0),
                nonce_b64=_encode_b64(bytes(row["password_nonce"])),
                ciphertext_b64=_encode_b64(bytes(row["password_ciphertext"])),
            )
        return EncryptedProxyRecord(proxy=proxy, value=value)

    async def get_user_proxy(
        self,
        tenant: TenantContext,
        *,
        for_update: bool = False,
    ) -> EncryptedProxyRecord | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            self.connection,
            f"""
            SELECT {_PROXY_COLUMNS}
            FROM outbound_proxy_configs
            WHERE user_uid = %s AND traffic_scope = 'account'
              AND account_id IS NULL
            ORDER BY created_at ASC, id ASC
            LIMIT 1{suffix}
            """,
            (tenant.user_uid,),
        )
        return self._map(row) if row else None

    async def store_user_proxy(
        self,
        tenant: TenantContext,
        *,
        proxy_id: str,
        scheme: str,
        host: str,
        port: int,
        value: EncryptedValue | None,
        enabled: bool = True,
    ) -> OutboundProxy:
        normalized_id = str(proxy_id or "").strip()
        nonce = _decode_b64(value.nonce_b64) if value is not None else None
        ciphertext = _decode_b64(value.ciphertext_b64) if value is not None else None
        algorithm = value.algorithm if value is not None else None
        key_version = value.key_version if value is not None else None
        now = time.time()
        existing = await self.get_user_proxy(tenant, for_update=True)
        if existing is None:
            created_at = now
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO outbound_proxy_configs (
                        id, user_uid, account_id, traffic_scope,
                        proxy_scheme, host, port, username,
                        password_algorithm, password_key_version,
                        password_nonce, password_ciphertext, password_auth_tag,
                        enabled, created_at, updated_at
                    ) VALUES (%s, %s, NULL, 'account', %s, %s, %s, '',
                              %s, %s, %s, %s, NULL, %s, %s, %s)
                    """,
                    (
                        normalized_id,
                        tenant.user_uid,
                        scheme,
                        host,
                        int(port),
                        algorithm,
                        key_version,
                        nonce,
                        ciphertext,
                        1 if enabled else 0,
                        now,
                        now,
                    ),
                )
        else:
            normalized_id = existing.proxy.id
            created_at = existing.proxy.created_at
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE outbound_proxy_configs
                    SET proxy_scheme = %s, host = %s, port = %s,
                        username = '', password_algorithm = %s,
                        password_key_version = %s, password_nonce = %s,
                        password_ciphertext = %s, password_auth_tag = NULL,
                        enabled = %s, updated_at = %s
                    WHERE id = %s AND user_uid = %s
                    """,
                    (
                        scheme,
                        host,
                        int(port),
                        algorithm,
                        key_version,
                        nonce,
                        ciphertext,
                        1 if enabled else 0,
                        now,
                        normalized_id,
                        tenant.user_uid,
                    ),
                )
        return OutboundProxy(
            id=normalized_id,
            user_uid=tenant.user_uid,
            scheme=scheme,
            host=host,
            port=int(port),
            enabled=bool(enabled),
            has_credentials=value is not None,
            created_at=created_at,
            updated_at=now,
        )


class OAuthStateRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    @staticmethod
    def _map(row) -> OAuthStateRecord:
        return OAuthStateRecord(
            id=str(row["id"]),
            user_uid=str(row["user_uid"]),
            session_id=str(row["session_id"]),
            provider_key=str(row["provider_key"]),
            account_id=str(row["account_draft_id"]),
            state_hash=str(row["state_hash"]),
            pkce_algorithm=str(row["pkce_algorithm"]),
            verifier=EncryptedValue(
                algorithm="AES-256-GCM",
                key_version=int(row["pkce_key_version"] or 0),
                nonce_b64=_encode_b64(bytes(row["pkce_nonce"])),
                ciphertext_b64=_encode_b64(bytes(row["pkce_ciphertext"])),
            ),
            redirect_uri=str(row["redirect_uri"]),
            expires_at=float(row["expires_at"] or 0),
            consumed_at=(
                float(row["consumed_at"])
                if row["consumed_at"] is not None
                else None
            ),
            created_at=float(row["created_at"] or 0),
        )

    async def create(
        self,
        tenant: TenantContext,
        *,
        state_id: str,
        session_id: str,
        provider_key: str,
        account_id: str,
        state_hash: str,
        verifier: EncryptedValue,
        redirect_uri: str,
        expires_at: float,
        now: float,
    ) -> OAuthStateRecord:
        normalized_state_id = str(state_id or "").strip()
        if not normalized_state_id:
            raise ValueError("state_id is required")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO oauth_authorization_states (
                    id, user_uid, session_id, provider_key, account_draft_id,
                    state_hash, pkce_algorithm, pkce_key_version, pkce_nonce,
                    pkce_ciphertext, pkce_auth_tag, redirect_uri, expires_at,
                    consumed_at, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'S256', %s, %s, %s,
                          NULL, %s, %s, NULL, %s)
                """,
                (
                    normalized_state_id,
                    tenant.user_uid,
                    str(session_id or "").strip(),
                    str(provider_key or "").strip().casefold(),
                    str(account_id or "").strip(),
                    str(state_hash or "").strip(),
                    verifier.key_version,
                    _decode_b64(verifier.nonce_b64),
                    _decode_b64(verifier.ciphertext_b64),
                    str(redirect_uri or "").strip(),
                    float(expires_at),
                    float(now),
                ),
            )
        return OAuthStateRecord(
            id=normalized_state_id,
            user_uid=tenant.user_uid,
            session_id=str(session_id or "").strip(),
            provider_key=str(provider_key or "").strip().casefold(),
            account_id=str(account_id or "").strip(),
            state_hash=str(state_hash or "").strip(),
            pkce_algorithm="S256",
            verifier=verifier,
            redirect_uri=str(redirect_uri or "").strip(),
            expires_at=float(expires_at),
            consumed_at=None,
            created_at=float(now),
        )

    async def get_by_hash(
        self,
        tenant: TenantContext,
        session_id: str,
        state_hash: str,
        *,
        for_update: bool = False,
    ) -> OAuthStateRecord | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = await fetch_one(
            self.connection,
            f"""
            SELECT {_OAUTH_STATE_COLUMNS}
            FROM oauth_authorization_states
            WHERE user_uid = %s AND session_id = %s AND state_hash = %s
            LIMIT 1{suffix}
            """,
            (
                tenant.user_uid,
                str(session_id or "").strip(),
                str(state_hash or "").strip(),
            ),
        )
        return self._map(row) if row else None

    async def consume(self, state_id: str, *, now: float) -> bool:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE oauth_authorization_states
                SET consumed_at = %s
                WHERE id = %s AND consumed_at IS NULL
                """,
                (float(now), str(state_id or "").strip()),
            )
            return cursor.rowcount == 1


class CredentialRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def store_encrypted(
        self,
        tenant: TenantContext,
        account_id: str,
        *,
        credential_type: str,
        value: EncryptedValue,
        expires_at: float | None = None,
    ) -> EncryptedCredentialRecord:
        normalized_account_id = str(account_id or "").strip()
        owner = await fetch_one(
            self.connection,
            """
            SELECT id
            FROM mail_accounts
            WHERE id = %s AND user_uid = %s
            """,
            (normalized_account_id, tenant.user_uid),
        )
        if owner is None:
            raise NotFoundError("mail account not found")
        if credential_type not in _CREDENTIAL_TYPES:
            raise ValueError("unsupported credential type")

        nonce = _decode_b64(value.nonce_b64)
        ciphertext = _decode_b64(value.ciphertext_b64)
        now = time.time()
        existing = await fetch_one(
            self.connection,
            """
            SELECT id, credential_version, created_at
            FROM provider_credentials
            WHERE account_id = %s AND user_uid = %s
            FOR UPDATE
            """,
            (normalized_account_id, tenant.user_uid),
        )
        if existing:
            credential_id = str(existing["id"])
            version = int(existing["credential_version"] or 0) + 1
            created_at = float(existing["created_at"] or 0)
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE provider_credentials
                    SET credential_type = %s, algorithm = %s, key_version = %s,
                        nonce = %s, ciphertext = %s, auth_tag = NULL,
                        expires_at = %s, credential_version = %s, updated_at = %s
                    WHERE id = %s AND user_uid = %s
                    """,
                    (
                        credential_type,
                        value.algorithm,
                        value.key_version,
                        nonce,
                        ciphertext,
                        expires_at,
                        version,
                        now,
                        credential_id,
                        tenant.user_uid,
                    ),
                )
        else:
            credential_id = new_id("cred")
            version = 1
            created_at = now
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO provider_credentials (
                        id, user_uid, account_id, credential_type, algorithm,
                        key_version, nonce, ciphertext, auth_tag, expires_at,
                        credential_version, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL,
                              %s, 1, %s, %s)
                    """,
                    (
                        credential_id,
                        tenant.user_uid,
                        normalized_account_id,
                        credential_type,
                        value.algorithm,
                        value.key_version,
                        nonce,
                        ciphertext,
                        expires_at,
                        now,
                        now,
                    ),
                )

        return EncryptedCredentialRecord(
            id=credential_id,
            user_uid=tenant.user_uid,
            account_id=normalized_account_id,
            credential_type=credential_type,
            value=value,
            expires_at=expires_at,
            credential_version=version,
            created_at=created_at,
            updated_at=now,
        )

    async def get_encrypted(
        self,
        tenant: TenantContext,
        account_id: str,
    ) -> EncryptedCredentialRecord | None:
        row = await fetch_one(
            self.connection,
            f"""
            SELECT {_CREDENTIAL_COLUMNS}
            FROM provider_credentials
            WHERE account_id = %s AND user_uid = %s
            """,
            (str(account_id or "").strip(), tenant.user_uid),
        )
        return _map_credential(row) if row else None
