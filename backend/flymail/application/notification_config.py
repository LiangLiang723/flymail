"""Encrypted tenant notification-channel, rule, publisher, and test-delivery configuration."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import aiomysql

from flymail.api.schemas.notifications import (
    NotificationChannelResponse,
    NotificationPublisherResponse,
    NotificationRuleResponse,
    NotificationTestResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.domain.errors import ApiContractError, NotFoundError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.security.credentials import CredentialCipher, EncryptedValue
from flymail.notifications.contracts import validate_public_http_url
from flymail.repositories.audit import AuditRepository
from flymail.repositories.base import TenantContext
from flymail.repositories.jobs import JobRepository, JobSpec


MAX_SECRET_JSON_BYTES = 16 * 1024
MAX_PUBLIC_CONFIG_BYTES = 16 * 1024


def _decode_b64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _bounded_json(value: dict[str, object], label: str, limit: int) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limit:
        raise ApiContractError("configuration_too_large", f"{label} is too large", status_code=422)
    return encoded


def _encrypted_columns(value: EncryptedValue) -> tuple[str, int, bytes, bytes]:
    return (
        value.algorithm,
        value.key_version,
        _decode_b64(value.nonce_b64),
        _decode_b64(value.ciphertext_b64),
    )


class NotificationConfigService:
    def __init__(
        self,
        pool: DatabasePool,
        cipher: CredentialCipher,
        *,
        now_fn=time.time,
        url_validator=validate_public_http_url,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(cipher, CredentialCipher):
            raise TypeError("cipher must be CredentialCipher")
        self.pool = pool
        self.cipher = cipher
        self.now_fn = now_fn
        self.url_validator = url_validator

    def _channel_public_config(
        self,
        channel_key: str,
        public_config: dict[str, object],
    ) -> dict[str, object]:
        normalized = dict(public_config)
        endpoint = str(normalized.get("endpoint_url") or "").strip()
        if channel_key in {"generic_webhook", "wecom", "dingtalk", "feishu"}:
            if not endpoint:
                raise ApiContractError(
                    "notification_endpoint_required",
                    "notification endpoint URL is required",
                    status_code=422,
                )
            try:
                normalized["endpoint_url"] = self.url_validator(endpoint)
            except ValueError as exc:
                raise ApiContractError(
                    "unsafe_notification_endpoint",
                    "notification endpoint must be publicly routable",
                    status_code=422,
                ) from exc
        _bounded_json(normalized, "notification public configuration", MAX_PUBLIC_CONFIG_BYTES)
        return normalized

    async def list_channels(
        self,
        session: AuthenticatedSession,
    ) -> tuple[NotificationChannelResponse, ...]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, channel_key, display_name, enabled, public_config,
                           secret_ciphertext, use_proxy, updated_at
                    FROM notification_channels
                    WHERE user_uid = %s
                    ORDER BY display_name, id
                    LIMIT 200
                    """,
                    (tenant.user_uid,),
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(self._channel(row) for row in rows)

    async def create_channel(
        self,
        session: AuthenticatedSession,
        *,
        channel_key: str,
        display_name: str,
        enabled: bool,
        public_config: dict[str, object],
        secret: dict[str, str],
        use_proxy: bool,
        request_id: str,
    ) -> NotificationChannelResponse:
        tenant = TenantContext(session.user.id)
        channel_id = new_id("notifych")
        timestamp = float(self.now_fn())
        normalized_public = self._channel_public_config(channel_key, public_config)
        secret_json = _bounded_json(
            {str(key): str(value) for key, value in secret.items()},
            "notification secret configuration",
            MAX_SECRET_JSON_BYTES,
        )
        encrypted = self.cipher.encrypt(channel_id, secret_json.encode("utf-8")) if secret else None
        encrypted_columns = _encrypted_columns(encrypted) if encrypted else (None, None, None, None)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO notification_channels (
                            id, user_uid, channel_key, display_name, enabled,
                            public_config, secret_algorithm, secret_key_version,
                            secret_nonce, secret_ciphertext, secret_auth_tag,
                            use_proxy, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                  %s, %s, NULL, %s, %s, %s)
                        """,
                        (
                            channel_id,
                            tenant.user_uid,
                            channel_key,
                            str(display_name).strip(),
                            1 if enabled else 0,
                            json.dumps(normalized_public, ensure_ascii=False, sort_keys=True),
                            *encrypted_columns,
                            1 if use_proxy else 0,
                            timestamp,
                            timestamp,
                        ),
                    )
                await AuditRepository(connection).append(
                    event_type="notification.channel_created",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_channel",
                    resource_id=channel_id,
                    safe_metadata={"channel_key": channel_key},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return await self.get_channel(session, channel_id)

    async def get_channel(
        self,
        session: AuthenticatedSession,
        channel_id: str,
    ) -> NotificationChannelResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, channel_key, display_name, enabled, public_config,
                           secret_ciphertext, use_proxy, updated_at
                    FROM notification_channels
                    WHERE id = %s AND user_uid = %s
                    """,
                    (str(channel_id).strip(), tenant.user_uid),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("notification channel was not found")
        return self._channel(dict(row))

    async def update_channel(
        self,
        session: AuthenticatedSession,
        channel_id: str,
        *,
        channel_key: str,
        display_name: str,
        enabled: bool,
        public_config: dict[str, object],
        secret: dict[str, str],
        use_proxy: bool,
        request_id: str,
    ) -> NotificationChannelResponse:
        tenant = TenantContext(session.user.id)
        normalized_id = str(channel_id).strip()
        timestamp = float(self.now_fn())
        normalized_public = self._channel_public_config(channel_key, public_config)
        encrypted = None
        if secret:
            secret_json = _bounded_json(
                {str(key): str(value) for key, value in secret.items()},
                "notification secret configuration",
                MAX_SECRET_JSON_BYTES,
            )
            encrypted = self.cipher.encrypt(normalized_id, secret_json.encode("utf-8"))
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    if encrypted is None:
                        await cursor.execute(
                            """
                            UPDATE notification_channels
                            SET channel_key=%s, display_name=%s, enabled=%s,
                                public_config=%s, use_proxy=%s, updated_at=%s
                            WHERE id=%s AND user_uid=%s
                            """,
                            (
                                channel_key, str(display_name).strip(), 1 if enabled else 0,
                                json.dumps(normalized_public, ensure_ascii=False, sort_keys=True),
                                1 if use_proxy else 0, timestamp, normalized_id, tenant.user_uid,
                            ),
                        )
                    else:
                        algorithm, key_version, nonce, ciphertext = _encrypted_columns(encrypted)
                        await cursor.execute(
                            """
                            UPDATE notification_channels
                            SET channel_key=%s, display_name=%s, enabled=%s,
                                public_config=%s, secret_algorithm=%s,
                                secret_key_version=%s, secret_nonce=%s,
                                secret_ciphertext=%s, secret_auth_tag=NULL,
                                use_proxy=%s, updated_at=%s
                            WHERE id=%s AND user_uid=%s
                            """,
                            (
                                channel_key, str(display_name).strip(), 1 if enabled else 0,
                                json.dumps(normalized_public, ensure_ascii=False, sort_keys=True),
                                algorithm, key_version, nonce, ciphertext,
                                1 if use_proxy else 0, timestamp, normalized_id, tenant.user_uid,
                            ),
                        )
                    if cursor.rowcount != 1:
                        raise NotFoundError("notification channel was not found")
                await AuditRepository(connection).append(
                    event_type="notification.channel_updated",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_channel",
                    resource_id=normalized_id,
                    safe_metadata={"channel_key": channel_key},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return await self.get_channel(session, normalized_id)

    async def delete_channel(
        self,
        session: AuthenticatedSession,
        channel_id: str,
        *,
        request_id: str,
    ) -> None:
        tenant = TenantContext(session.user.id)
        normalized_id = str(channel_id).strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM notification_rules WHERE user_uid=%s AND channel_id=%s",
                        (tenant.user_uid, normalized_id),
                    )
                    await cursor.execute(
                        "DELETE FROM notification_channels WHERE user_uid=%s AND id=%s",
                        (tenant.user_uid, normalized_id),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("notification channel was not found")
                await AuditRepository(connection).append(
                    event_type="notification.channel_deleted",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_channel",
                    resource_id=normalized_id,
                    safe_metadata={},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def list_rules(
        self,
        session: AuthenticatedSession,
    ) -> tuple[NotificationRuleResponse, ...]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, event_type, channel_id, image_publisher_id,
                           enabled, filter_json, dedupe_window_seconds, updated_at
                    FROM notification_rules
                    WHERE user_uid=%s
                    ORDER BY event_type, id
                    LIMIT 500
                    """,
                    (tenant.user_uid,),
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(self._rule(row) for row in rows)

    async def create_rule(
        self,
        session: AuthenticatedSession,
        *,
        event_type: str,
        channel_id: str,
        image_publisher_id: str | None,
        enabled: bool,
        use_proxy: bool,
        dedupe_window_seconds: int,
        request_id: str,
    ) -> NotificationRuleResponse:
        tenant = TenantContext(session.user.id)
        rule_id = new_id("notifyrule")
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await self._require_channel(connection, tenant, channel_id)
                if image_publisher_id:
                    await self._require_publisher(connection, tenant, image_publisher_id)
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO notification_rules (
                            id, user_uid, event_type, channel_id, image_publisher_id,
                            enabled, filter_json, dedupe_window_seconds,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            rule_id, tenant.user_uid, event_type, channel_id,
                            image_publisher_id, 1 if enabled else 0,
                            json.dumps({"use_proxy": bool(use_proxy)}, sort_keys=True),
                            int(dedupe_window_seconds), timestamp, timestamp,
                        ),
                    )
                await AuditRepository(connection).append(
                    event_type="notification.rule_created",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_rule",
                    resource_id=rule_id,
                    safe_metadata={"event_type": event_type},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return NotificationRuleResponse(
            id=rule_id,
            event_type=event_type,
            channel_id=channel_id,
            image_publisher_id=image_publisher_id,
            enabled=enabled,
            use_proxy=use_proxy,
            dedupe_window_seconds=int(dedupe_window_seconds),
            updated_at=timestamp,
        )

    async def update_rule(
        self,
        session: AuthenticatedSession,
        rule_id: str,
        *,
        event_type: str,
        channel_id: str,
        image_publisher_id: str | None,
        enabled: bool,
        use_proxy: bool,
        dedupe_window_seconds: int,
        request_id: str,
    ) -> NotificationRuleResponse:
        tenant = TenantContext(session.user.id)
        normalized_id = str(rule_id).strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await self._require_channel(connection, tenant, channel_id)
                if image_publisher_id:
                    await self._require_publisher(connection, tenant, image_publisher_id)
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE notification_rules
                        SET event_type=%s, channel_id=%s, image_publisher_id=%s,
                            enabled=%s, filter_json=%s,
                            dedupe_window_seconds=%s, updated_at=%s
                        WHERE id=%s AND user_uid=%s
                        """,
                        (
                            event_type, channel_id, image_publisher_id,
                            1 if enabled else 0,
                            json.dumps({"use_proxy": bool(use_proxy)}, sort_keys=True),
                            int(dedupe_window_seconds), timestamp,
                            normalized_id, tenant.user_uid,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("notification rule was not found")
                await AuditRepository(connection).append(
                    event_type="notification.rule_updated",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_rule",
                    resource_id=normalized_id,
                    safe_metadata={"event_type": event_type},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return NotificationRuleResponse(
            id=normalized_id,
            event_type=event_type,
            channel_id=channel_id,
            image_publisher_id=image_publisher_id,
            enabled=enabled,
            use_proxy=use_proxy,
            dedupe_window_seconds=int(dedupe_window_seconds),
            updated_at=timestamp,
        )

    async def delete_rule(
        self,
        session: AuthenticatedSession,
        rule_id: str,
        *,
        request_id: str,
    ) -> None:
        tenant = TenantContext(session.user.id)
        normalized_id = str(rule_id).strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM notification_rules WHERE id=%s AND user_uid=%s",
                        (normalized_id, tenant.user_uid),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("notification rule was not found")
                await AuditRepository(connection).append(
                    event_type="notification.rule_deleted",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_rule",
                    resource_id=normalized_id,
                    safe_metadata={},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def list_publishers(
        self,
        session: AuthenticatedSession,
    ) -> tuple[NotificationPublisherResponse, ...]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, publisher_key, display_name, endpoint_url,
                           enabled, public_config, secret_ciphertext, updated_at
                    FROM notification_image_publishers
                    WHERE user_uid=%s
                    ORDER BY display_name, id
                    LIMIT 200
                    """,
                    (tenant.user_uid,),
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(self._publisher(row) for row in rows)

    async def create_publisher(
        self,
        session: AuthenticatedSession,
        *,
        publisher_key: str,
        display_name: str,
        endpoint_url: str,
        enabled: bool,
        public_config: dict[str, object],
        secret: dict[str, str],
        request_id: str,
    ) -> NotificationPublisherResponse:
        tenant = TenantContext(session.user.id)
        publisher_id = new_id("notifypub")
        timestamp = float(self.now_fn())
        try:
            endpoint = self.url_validator(endpoint_url, require_https=True)
        except ValueError as exc:
            raise ApiContractError(
                "unsafe_publisher_endpoint",
                "image publisher endpoint must be public HTTPS",
                status_code=422,
            ) from exc
        public_json = _bounded_json(public_config, "publisher public configuration", MAX_PUBLIC_CONFIG_BYTES)
        secret_json = _bounded_json(
            {str(key): str(value) for key, value in secret.items()},
            "publisher secret configuration",
            MAX_SECRET_JSON_BYTES,
        )
        encrypted = self.cipher.encrypt(publisher_id, secret_json.encode("utf-8")) if secret else None
        encrypted_columns = _encrypted_columns(encrypted) if encrypted else (None, None, None, None)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO notification_image_publishers (
                            id, user_uid, publisher_key, display_name, endpoint_url,
                            enabled, public_config, secret_algorithm,
                            secret_key_version, secret_nonce, secret_ciphertext,
                            secret_auth_tag, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                  %s, %s, %s, NULL, %s, %s)
                        """,
                        (
                            publisher_id, tenant.user_uid, publisher_key,
                            str(display_name).strip(), endpoint, 1 if enabled else 0,
                            public_json, *encrypted_columns, timestamp, timestamp,
                        ),
                    )
                await AuditRepository(connection).append(
                    event_type="notification.publisher_created",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_publisher",
                    resource_id=publisher_id,
                    safe_metadata={"publisher_key": publisher_key},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return NotificationPublisherResponse(
            id=publisher_id,
            publisher_key=publisher_key,
            display_name=str(display_name).strip(),
            endpoint_url=endpoint,
            enabled=enabled,
            public_config=dict(public_config),
            secret_configured=bool(secret),
            updated_at=timestamp,
        )

    async def update_publisher(
        self,
        session: AuthenticatedSession,
        publisher_id: str,
        *,
        publisher_key: str,
        display_name: str,
        endpoint_url: str,
        enabled: bool,
        public_config: dict[str, object],
        secret: dict[str, str],
        request_id: str,
    ) -> NotificationPublisherResponse:
        tenant = TenantContext(session.user.id)
        normalized_id = str(publisher_id).strip()
        timestamp = float(self.now_fn())
        try:
            endpoint = self.url_validator(endpoint_url, require_https=True)
        except ValueError as exc:
            raise ApiContractError(
                "unsafe_publisher_endpoint",
                "image publisher endpoint must be public HTTPS",
                status_code=422,
            ) from exc
        public_json = _bounded_json(public_config, "publisher public configuration", MAX_PUBLIC_CONFIG_BYTES)
        encrypted = None
        if secret:
            secret_json = _bounded_json(
                {str(key): str(value) for key, value in secret.items()},
                "publisher secret configuration",
                MAX_SECRET_JSON_BYTES,
            )
            encrypted = self.cipher.encrypt(normalized_id, secret_json.encode("utf-8"))
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    if encrypted is None:
                        await cursor.execute(
                            """
                            UPDATE notification_image_publishers
                            SET publisher_key=%s, display_name=%s, endpoint_url=%s,
                                enabled=%s, public_config=%s, updated_at=%s
                            WHERE id=%s AND user_uid=%s
                            """,
                            (
                                publisher_key, str(display_name).strip(), endpoint,
                                1 if enabled else 0, public_json, timestamp,
                                normalized_id, tenant.user_uid,
                            ),
                        )
                    else:
                        algorithm, key_version, nonce, ciphertext = _encrypted_columns(encrypted)
                        await cursor.execute(
                            """
                            UPDATE notification_image_publishers
                            SET publisher_key=%s, display_name=%s, endpoint_url=%s,
                                enabled=%s, public_config=%s,
                                secret_algorithm=%s, secret_key_version=%s,
                                secret_nonce=%s, secret_ciphertext=%s,
                                secret_auth_tag=NULL, updated_at=%s
                            WHERE id=%s AND user_uid=%s
                            """,
                            (
                                publisher_key, str(display_name).strip(), endpoint,
                                1 if enabled else 0, public_json,
                                algorithm, key_version, nonce, ciphertext,
                                timestamp, normalized_id, tenant.user_uid,
                            ),
                        )
                    if cursor.rowcount != 1:
                        raise NotFoundError("notification image publisher was not found")
                await AuditRepository(connection).append(
                    event_type="notification.publisher_updated",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_publisher",
                    resource_id=normalized_id,
                    safe_metadata={"publisher_key": publisher_key},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, publisher_key, display_name, endpoint_url,
                           enabled, public_config, secret_ciphertext, updated_at
                    FROM notification_image_publishers
                    WHERE id=%s AND user_uid=%s
                    """,
                    (normalized_id, tenant.user_uid),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("notification image publisher was not found")
        return self._publisher(dict(row))

    async def delete_publisher(
        self,
        session: AuthenticatedSession,
        publisher_id: str,
        *,
        request_id: str,
    ) -> None:
        tenant = TenantContext(session.user.id)
        normalized_id = str(publisher_id).strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE notification_rules SET image_publisher_id=NULL, updated_at=%s
                        WHERE user_uid=%s AND image_publisher_id=%s
                        """,
                        (timestamp, tenant.user_uid, normalized_id),
                    )
                    await cursor.execute(
                        "DELETE FROM notification_image_publishers WHERE id=%s AND user_uid=%s",
                        (normalized_id, tenant.user_uid),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("notification image publisher was not found")
                await AuditRepository(connection).append(
                    event_type="notification.publisher_deleted",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_publisher",
                    resource_id=normalized_id,
                    safe_metadata={},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def test_channel(
        self,
        session: AuthenticatedSession,
        channel_id: str,
        *,
        request_id: str,
    ) -> NotificationTestResponse:
        tenant = TenantContext(session.user.id)
        normalized_channel = str(channel_id).strip()
        timestamp = float(self.now_fn())
        event_id = new_id("notifyevt")
        delivery_id = new_id("notifydel")
        idempotency = f"notification-test:{normalized_channel}:{request_id}"
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await self._require_channel(
                    connection,
                    tenant,
                    normalized_channel,
                    require_enabled=True,
                )
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT id FROM notification_rules
                        WHERE user_uid=%s AND channel_id=%s AND event_type='mail.new'
                          AND enabled=1
                        ORDER BY id LIMIT 1
                        """,
                        (tenant.user_uid, normalized_channel),
                    )
                    if await cursor.fetchone() is None:
                        raise ApiContractError(
                            "notification_rule_required",
                            "an enabled mail.new rule is required for channel testing",
                            status_code=409,
                        )
                    await cursor.execute(
                        """
                        INSERT INTO notification_events (
                            id, user_uid, event_type, title, summary,
                            action_path, account_id, dedupe_key, created_at
                        ) VALUES (%s, %s, 'mail.new', 'FlyMail test notification',
                                  'Notification channel test', '/settings/notifications',
                                  NULL, %s, %s)
                        """,
                        (event_id, tenant.user_uid, idempotency, timestamp),
                    )
                    await cursor.execute(
                        """
                        INSERT INTO notification_deliveries (
                            id, user_uid, notification_event_id, channel_id,
                            status, attempt_count, available_at,
                            idempotency_key, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, 'pending', 0, %s, %s, %s, %s)
                        """,
                        (
                            delivery_id, tenant.user_uid, event_id, normalized_channel,
                            timestamp, idempotency, timestamp, timestamp,
                        ),
                    )
                task_id = await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="notifications",
                        job_kind="notification.deliver",
                        payload={"delivery_id": delivery_id},
                        user_uid=tenant.user_uid,
                        priority=50,
                        available_at=timestamp,
                        max_attempts=8,
                        dedupe_key=f"notification-delivery:{idempotency}",
                    ),
                    now=timestamp,
                )
                await AuditRepository(connection).append(
                    event_type="notification.channel_test_queued",
                    result_code="queued",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="notification_channel",
                    resource_id=normalized_channel,
                    safe_metadata={"task_id": task_id},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return NotificationTestResponse(task_id=task_id)

    @staticmethod
    def _rule(row: dict[str, Any]) -> NotificationRuleResponse:
        filters = _json_object(row["filter_json"])
        return NotificationRuleResponse(
            id=str(row["id"]),
            event_type=str(row["event_type"]),
            channel_id=str(row["channel_id"]),
            image_publisher_id=(
                str(row["image_publisher_id"]) if row["image_publisher_id"] else None
            ),
            enabled=bool(row["enabled"]),
            use_proxy=bool(filters.get("use_proxy")),
            dedupe_window_seconds=int(row["dedupe_window_seconds"] or 0),
            updated_at=float(row["updated_at"] or 0),
        )

    @staticmethod
    def _publisher(row: dict[str, Any]) -> NotificationPublisherResponse:
        return NotificationPublisherResponse(
            id=str(row["id"]),
            publisher_key=str(row["publisher_key"]),
            display_name=str(row["display_name"]),
            endpoint_url=str(row["endpoint_url"]),
            enabled=bool(row["enabled"]),
            public_config=_json_object(row["public_config"]),
            secret_configured=row["secret_ciphertext"] is not None,
            updated_at=float(row["updated_at"] or 0),
        )

    @staticmethod
    def _channel(row: dict[str, Any]) -> NotificationChannelResponse:
        return NotificationChannelResponse(
            id=str(row["id"]),
            channel_key=str(row["channel_key"]),
            display_name=str(row["display_name"]),
            enabled=bool(row["enabled"]),
            public_config=_json_object(row["public_config"]),
            secret_configured=row["secret_ciphertext"] is not None,
            use_proxy=bool(row["use_proxy"]),
            updated_at=float(row["updated_at"] or 0),
        )

    @staticmethod
    async def _require_channel(
        connection,
        tenant: TenantContext,
        channel_id: str,
        *,
        require_enabled: bool = False,
    ) -> None:
        enabled_clause = " AND enabled=1" if require_enabled else ""
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT id FROM notification_channels WHERE id=%s AND user_uid=%s"
                + enabled_clause,
                (str(channel_id).strip(), tenant.user_uid),
            )
            if await cursor.fetchone() is None:
                raise NotFoundError("notification channel was not found")

    @staticmethod
    async def _require_publisher(
        connection,
        tenant: TenantContext,
        publisher_id: str,
    ) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT id FROM notification_image_publishers
                WHERE id=%s AND user_uid=%s AND enabled=1
                """,
                (str(publisher_id).strip(), tenant.user_uid),
            )
            if await cursor.fetchone() is None:
                raise NotFoundError("notification image publisher was not found")
