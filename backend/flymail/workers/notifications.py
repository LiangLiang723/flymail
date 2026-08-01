"""Durable notification event fan-out and isolated channel delivery Worker."""

from __future__ import annotations

import base64
import gzip
import json
import time
from dataclasses import dataclass
from typing import Mapping

import aiomysql
from cryptography.exceptions import InvalidTag

from flymail.domain.errors import ConflictError, NotFoundError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.db.uow import SqlUnitOfWork
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.credentials import CredentialCipher, EncryptedValue
from flymail.notifications.channels import ChannelRegistry
from flymail.notifications.contracts import (
    EVENT_TYPES,
    ImageAsset,
    ImagePublisherConfig,
    NotificationConfig,
    NotificationMessage,
    ProxyConfig,
)
from flymail.notifications.image_publishers import ImagePublisherRegistry
from flymail.repositories.base import TenantContext, fetch_all, fetch_one
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.objects import ObjectRepository
from flymail.repositories.outbox import OutboxRepository
from flymail.workers.dispatcher import JobContext, JobOutcome


_EXTERNAL_CHANNELS = frozenset(
    {"bark", "telegram", "wecom", "dingtalk", "feishu", "generic_webhook"}
)
_PROXY_CHANNELS = frozenset({"telegram", "generic_webhook"})
_TERMINAL_DELIVERY_STATES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class PublishedNotification:
    event_id: str
    delivery_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DeliveryScope:
    delivery_id: str
    user_uid: str
    status: str
    notification_event_id: str
    event_type: str
    title: str
    summary: str
    action_path: str
    account_id: str | None
    notification_asset_id: str | None
    occurred_at: float
    channel_id: str
    channel_key: str
    channel_enabled: bool
    channel_public_config: dict[str, object]
    channel_secret_algorithm: str
    channel_secret_key_version: int
    channel_secret_nonce: bytes
    channel_secret_ciphertext: bytes
    channel_use_proxy: bool
    rule_id: str
    rule_enabled: bool
    rule_filter: dict[str, object]
    image_publisher_id: str | None
    publisher_key: str
    publisher_endpoint_url: str
    publisher_enabled: bool
    publisher_public_config: dict[str, object]
    publisher_secret_algorithm: str
    publisher_secret_key_version: int
    publisher_secret_nonce: bytes
    publisher_secret_ciphertext: bytes
    user_enabled: bool
    account_status: str
    provider_key: str
    terminal: bool = False
    result_uncertain: bool = False


def _required_text(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _decode_json(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if value is None or value == "":
        return {}
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    decoded = json.loads(str(value))
    if not isinstance(decoded, dict):
        raise ValueError("notification JSON configuration must be an object")
    return {str(key): item for key, item in decoded.items()}


def _encode_b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _encrypted_value(
    algorithm: object,
    key_version: object,
    nonce: object,
    ciphertext: object,
) -> EncryptedValue | None:
    if not algorithm:
        return None
    if nonce is None or ciphertext is None:
        raise ValueError("encrypted notification configuration is incomplete")
    return EncryptedValue(
        algorithm=str(algorithm),
        key_version=int(key_version or 0),
        nonce_b64=_encode_b64(bytes(nonce)),
        ciphertext_b64=_encode_b64(bytes(ciphertext)),
    )


class NotificationService:
    """Consume a safe source event into one in-app event and external jobs."""

    def __init__(self, pool: DatabasePool) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool

    async def publish(
        self,
        tenant: TenantContext,
        *,
        event_type: str,
        aggregate_id: str,
        title: str,
        summary: str,
        action_path: str | None,
        account_id: str | None,
        dedupe_key: str,
        notification_asset_id: str | None = None,
        now: float | None = None,
    ) -> PublishedNotification:
        timestamp = float(time.time() if now is None else now)
        normalized_type = _required_text(event_type, "event_type")
        if normalized_type not in EVENT_TYPES:
            raise ValueError(f"unsupported notification event type: {normalized_type}")
        normalized_dedupe = _required_text(dedupe_key, "dedupe_key")
        normalized_aggregate = _required_text(aggregate_id, "aggregate_id")
        normalized_account = str(account_id or "").strip() or None
        normalized_asset = str(notification_asset_id or "").strip() or None
        event_id = new_id("notevt")
        safe_message = NotificationMessage(
            event_id=event_id,
            event_type=normalized_type,
            title=title,
            summary=summary,
            action_path=action_path,
            occurred_at=timestamp,
            account_id=normalized_account,
            notification_asset_id=normalized_asset,
        )
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            user = await fetch_one(
                connection,
                "SELECT id FROM users WHERE id = %s AND enabled = 1 FOR UPDATE",
                (tenant.user_uid,),
            )
            if user is None:
                raise ConflictError("notification user is disabled or missing")
            provider_key: str | None = None
            if normalized_account:
                account = await fetch_one(
                    connection,
                    """
                    SELECT provider_key, status
                    FROM mail_accounts
                    WHERE user_uid = %s AND id = %s
                    FOR UPDATE
                    """,
                    (tenant.user_uid, normalized_account),
                )
                if account is None or str(account["status"]) != "active":
                    raise ConflictError("notification account is disabled or missing")
                provider_key = str(account["provider_key"])
            if normalized_asset:
                asset = await fetch_one(
                    connection,
                    """
                    SELECT r.id
                    FROM content_references r
                    JOIN content_objects o
                      ON o.content_sha256 = r.content_sha256
                    WHERE r.id = %s AND r.user_uid = %s
                      AND r.reference_kind = 'notification_asset'
                      AND o.object_kind = 'notification_asset'
                    FOR UPDATE
                    """,
                    (normalized_asset, tenant.user_uid),
                )
                if asset is None:
                    raise NotFoundError("notification asset was not found")
            existing = await fetch_one(
                connection,
                """
                SELECT id FROM notification_events
                WHERE user_uid = %s AND dedupe_key = %s
                FOR UPDATE
                """,
                (tenant.user_uid, normalized_dedupe),
            )
            if existing is not None:
                existing_id = str(existing["id"])
                deliveries = await fetch_all(
                    connection,
                    """
                    SELECT id FROM notification_deliveries
                    WHERE user_uid = %s AND notification_event_id = %s
                    ORDER BY id
                    """,
                    (tenant.user_uid, existing_id),
                )
                await uow.commit()
                return PublishedNotification(
                    existing_id,
                    tuple(str(row["id"]) for row in deliveries),
                )

            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO notification_events (
                        id, user_uid, event_type, title, summary,
                        action_path, account_id, dedupe_key, created_at,
                        read_at, dismissed_at, notification_asset_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              NULL, NULL, %s)
                    """,
                    (
                        event_id,
                        tenant.user_uid,
                        normalized_type,
                        safe_message.title,
                        safe_message.summary,
                        safe_message.action_path or "",
                        normalized_account,
                        normalized_dedupe,
                        timestamp,
                        normalized_asset,
                    ),
                )
            rules = await fetch_all(
                connection,
                """
                SELECT r.id AS rule_id, r.channel_id, c.channel_key
                FROM notification_rules r
                JOIN notification_channels c
                  ON c.id = r.channel_id AND c.user_uid = r.user_uid
                WHERE r.user_uid = %s AND r.event_type = %s
                  AND r.enabled = 1 AND c.enabled = 1
                ORDER BY r.id
                """,
                (tenant.user_uid, normalized_type),
            )
            delivery_ids: list[str] = []
            for rule in rules:
                channel_key = str(rule["channel_key"])
                if channel_key == "in_app":
                    continue
                if channel_key not in _EXTERNAL_CHANNELS:
                    continue
                channel_id = str(rule["channel_id"])
                delivery_id = new_id("notifydel")
                idempotency = f"{event_id}:{channel_id}"
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO notification_deliveries (
                            id, user_uid, notification_event_id, channel_id,
                            status, attempt_count, available_at, delivered_at,
                            last_error_class, last_error_message,
                            idempotency_key, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, 'pending', 0, %s, NULL,
                                  '', '', %s, %s, %s)
                        """,
                        (
                            delivery_id,
                            tenant.user_uid,
                            event_id,
                            channel_id,
                            timestamp,
                            idempotency,
                            timestamp,
                            timestamp,
                        ),
                    )
                await JobRepository(connection).enqueue(
                    JobSpec(
                        queue_name="notifications",
                        job_kind="notification.deliver",
                        payload={"delivery_id": delivery_id},
                        user_uid=tenant.user_uid,
                        account_id=normalized_account,
                        provider_key=provider_key,
                        priority=50,
                        available_at=timestamp,
                        max_attempts=8,
                        dedupe_key=f"notification-delivery:{idempotency}",
                    ),
                    now=timestamp,
                )
                delivery_ids.append(delivery_id)
            await OutboxRepository(connection, tenant).append(
                "notification.created",
                event_id,
                {
                    "notification_event_id": event_id,
                    "source_event_type": normalized_type,
                    "source_aggregate_id": normalized_aggregate,
                    "external_delivery_count": len(delivery_ids),
                },
                aggregate_type="notification_event",
                now=timestamp,
            )
            await self._insert_realtime(
                connection,
                tenant.user_uid,
                "notification.created",
                "notification_event",
                event_id,
                {"notification_event_id": event_id},
                timestamp,
            )
            await uow.commit()
            return PublishedNotification(event_id, tuple(delivery_ids))

    @staticmethod
    async def _insert_realtime(
        connection: aiomysql.Connection,
        user_uid: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        now: float,
    ) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO realtime_events (
                    event_id, user_uid, event_type, aggregate_type,
                    aggregate_id, payload, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    new_id("rtevt"),
                    user_uid,
                    event_type,
                    aggregate_type,
                    aggregate_id,
                    json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                    now,
                    now + 7 * 24 * 3600,
                ),
            )

    @staticmethod
    def _connection(uow: SqlUnitOfWork) -> aiomysql.Connection:
        if uow.connection is None:
            raise RuntimeError("unit of work connection is unavailable")
        return uow.connection


class NotificationDeliveryHandler:
    def __init__(
        self,
        pool: DatabasePool,
        store: ObjectStore,
        cipher: CredentialCipher,
        channels: ChannelRegistry,
        publishers: ImagePublisherRegistry,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(store, ObjectStore):
            raise TypeError("store must be ObjectStore")
        if not isinstance(cipher, CredentialCipher):
            raise TypeError("cipher must be CredentialCipher")
        if not isinstance(channels, ChannelRegistry):
            raise TypeError("channels must be ChannelRegistry")
        if not isinstance(publishers, ImagePublisherRegistry):
            raise TypeError("publishers must be ImagePublisherRegistry")
        self.pool = pool
        self.store = store
        self.cipher = cipher
        self.channels = channels
        self.publishers = publishers

    async def handle(
        self,
        context: JobContext,
        payload: Mapping[str, object],
    ) -> JobOutcome:
        if not isinstance(context, JobContext):
            raise TypeError("context must be JobContext")
        tenant = TenantContext(_required_text(context.user_uid, "user_uid"))
        delivery_id = _required_text(payload.get("delivery_id"), "delivery_id")
        timestamp = time.time()
        scope = await self._begin_delivery(
            tenant,
            delivery_id,
            context=context,
            now=timestamp,
        )
        if scope.terminal:
            await self._release_asset_if_final(
                tenant,
                scope.notification_event_id,
            )
            if scope.result_uncertain:
                return JobOutcome.fail(
                    "NotificationResultUncertain",
                    "notification delivery result requires manual review",
                )
            return JobOutcome.success()
        try:
            channel_secret = self._decrypt_json(
                scope.channel_id,
                scope.channel_secret_algorithm,
                scope.channel_secret_key_version,
                scope.channel_secret_nonce,
                scope.channel_secret_ciphertext,
            )
            config = NotificationConfig(
                scope.channel_id,
                scope.channel_key,
                scope.channel_public_config,
                channel_secret,
            )
            proxy = await self._load_proxy(tenant, scope)
        except (ValueError, InvalidTag):
            outcome = JobOutcome.fail(
                "NotificationConfigurationError",
                "notification channel configuration is invalid",
            )
            await self._finish_delivery(
                tenant,
                scope,
                status="failed",
                error_class=outcome.error_class,
                error_message=outcome.error_message,
                now=timestamp,
            )
            await self._release_asset_if_final(
                tenant,
                scope.notification_event_id,
            )
            return outcome
        message = NotificationMessage(
            event_id=scope.notification_event_id,
            event_type=scope.event_type,
            title=scope.title,
            summary=scope.summary,
            action_path=scope.action_path,
            occurred_at=scope.occurred_at,
            account_id=scope.account_id,
            notification_asset_id=scope.notification_asset_id,
        )
        published = None
        publisher = None
        publisher_config = None
        if (
            scope.notification_asset_id
            and scope.image_publisher_id
            and scope.publisher_enabled
            and bool(scope.rule_filter.get("image_enabled"))
        ):
            try:
                asset = await self._load_asset(tenant, scope.notification_asset_id)
                publisher_secret = self._decrypt_json(
                    scope.image_publisher_id,
                    scope.publisher_secret_algorithm,
                    scope.publisher_secret_key_version,
                    scope.publisher_secret_nonce,
                    scope.publisher_secret_ciphertext,
                )
                publisher_config = ImagePublisherConfig(
                    scope.image_publisher_id,
                    scope.publisher_key,
                    scope.publisher_endpoint_url,
                    scope.publisher_public_config,
                    publisher_secret,
                )
                publisher = self.publishers.get(scope.publisher_key)
                published = await publisher.publish(asset, publisher_config, proxy)
                message = message.with_image_url(published.url)
            except Exception:
                published = None
                publisher = None
                publisher_config = None
        try:
            result = await self.channels.get(scope.channel_key).send(
                message,
                config,
                proxy,
            )
        except ValueError:
            result = None
            outcome = JobOutcome.fail(
                "NotificationConfigurationError",
                "notification channel configuration is invalid",
            )
            await self._finish_delivery(
                tenant,
                scope,
                status="failed",
                error_class=outcome.error_class,
                error_message=outcome.error_message,
                now=timestamp,
            )
        except Exception:
            result = None
            outcome = JobOutcome.retry(
                "NotificationUnexpectedError",
                "notification channel failed unexpectedly",
            )
            await self._finish_delivery(
                tenant,
                scope,
                status="retry_wait",
                error_class=outcome.error_class,
                error_message=outcome.error_message,
                now=timestamp,
            )
        else:
            if result.status == "succeeded":
                outcome = JobOutcome.success()
                await self._finish_delivery(
                    tenant,
                    scope,
                    status="succeeded",
                    error_class="",
                    error_message="",
                    now=timestamp,
                )
            elif result.status == "retry":
                outcome = JobOutcome.retry(
                    "NotificationRetryableFailure",
                    result.safe_detail or "notification delivery will be retried",
                )
                await self._finish_delivery(
                    tenant,
                    scope,
                    status="retry_wait",
                    error_class=outcome.error_class,
                    error_message=outcome.error_message,
                    now=timestamp,
                )
            else:
                outcome = JobOutcome.fail(
                    "NotificationPermanentFailure",
                    result.safe_detail or "notification delivery failed",
                )
                await self._finish_delivery(
                    tenant,
                    scope,
                    status="failed",
                    error_class=outcome.error_class,
                    error_message=outcome.error_message,
                    now=timestamp,
                )
        if published and publisher and publisher_config:
            await publisher.cleanup(published, publisher_config, proxy)
        if outcome.action in {"complete", "fail"}:
            await self._release_asset_if_final(
                tenant,
                scope.notification_event_id,
            )
        return outcome

    async def _begin_delivery(
        self,
        tenant: TenantContext,
        delivery_id: str,
        *,
        context: JobContext,
        now: float,
    ) -> _DeliveryScope:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            row = await fetch_one(
                connection,
                """
                SELECT d.id AS delivery_id, d.status AS delivery_status,
                       d.notification_event_id,
                       e.event_type, e.title, e.summary, e.action_path,
                       e.account_id, e.notification_asset_id, e.created_at,
                       c.id AS channel_id, c.channel_key,
                       c.enabled AS channel_enabled,
                       c.public_config AS channel_public_config,
                       c.secret_algorithm AS channel_secret_algorithm,
                       c.secret_key_version AS channel_secret_key_version,
                       c.secret_nonce AS channel_secret_nonce,
                       c.secret_ciphertext AS channel_secret_ciphertext,
                       c.use_proxy AS channel_use_proxy,
                       r.id AS rule_id, r.enabled AS rule_enabled,
                       r.filter_json AS rule_filter,
                       r.image_publisher_id,
                       p.publisher_key, p.endpoint_url,
                       p.enabled AS publisher_enabled,
                       p.public_config AS publisher_public_config,
                       p.secret_algorithm AS publisher_secret_algorithm,
                       p.secret_key_version AS publisher_secret_key_version,
                       p.secret_nonce AS publisher_secret_nonce,
                       p.secret_ciphertext AS publisher_secret_ciphertext,
                       u.enabled AS user_enabled,
                       COALESCE(a.status, '') AS account_status,
                       COALESCE(a.provider_key, '') AS provider_key
                FROM notification_deliveries d
                JOIN notification_events e
                  ON e.id = d.notification_event_id AND e.user_uid = d.user_uid
                JOIN notification_channels c
                  ON c.id = d.channel_id AND c.user_uid = d.user_uid
                JOIN notification_rules r
                  ON r.user_uid = d.user_uid
                 AND r.event_type = e.event_type
                 AND r.channel_id = c.id
                JOIN users u ON u.id = d.user_uid
                LEFT JOIN mail_accounts a
                  ON a.id = e.account_id AND a.user_uid = e.user_uid
                LEFT JOIN notification_image_publishers p
                  ON p.id = r.image_publisher_id AND p.user_uid = r.user_uid
                WHERE d.user_uid = %s AND d.id = %s
                FOR UPDATE
                """,
                (tenant.user_uid, delivery_id),
            )
            if row is None:
                raise NotFoundError("notification delivery was not found")
            account_id = str(row["account_id"] or "").strip() or None
            if account_id:
                if (
                    account_id != _required_text(context.account_id, "account_id")
                    or str(row["provider_key"])
                    != _required_text(context.provider_key, "provider_key")
                ):
                    raise ConflictError("notification job scope does not match event account")
            status = str(row["delivery_status"])
            if status in _TERMINAL_DELIVERY_STATES:
                await uow.commit()
                return self._scope(row, terminal=True)
            enabled = (
                bool(row["user_enabled"])
                and bool(row["channel_enabled"])
                and bool(row["rule_enabled"])
                and (not account_id or str(row["account_status"]) == "active")
            )
            if not enabled:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE notification_deliveries
                        SET status = 'cancelled',
                            last_error_class = 'NotificationDisabled',
                            last_error_message = 'user, account, channel, or rule is disabled',
                            updated_at = %s
                        WHERE user_uid = %s AND id = %s
                        """,
                        (now, tenant.user_uid, delivery_id),
                    )
                await uow.commit()
                return self._scope(row, terminal=True)
            if status == "sending":
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE notification_deliveries
                        SET status = 'failed',
                            last_error_class = 'NotificationResultUncertain',
                            last_error_message = 'previous delivery result is unknown',
                            updated_at = %s
                        WHERE user_uid = %s AND id = %s AND status = 'sending'
                        """,
                        (now, tenant.user_uid, delivery_id),
                    )
                    if cursor.rowcount != 1:
                        raise ConflictError("notification delivery state changed concurrently")
                await NotificationService._insert_realtime(
                    connection,
                    tenant.user_uid,
                    "notification.delivery.updated",
                    "notification_delivery",
                    delivery_id,
                    {
                        "delivery_id": delivery_id,
                        "notification_event_id": str(row["notification_event_id"]),
                        "status": "failed",
                    },
                    now,
                )
                await OutboxRepository(connection, tenant).append(
                    "notification.delivery.updated",
                    delivery_id,
                    {
                        "delivery_id": delivery_id,
                        "notification_event_id": str(row["notification_event_id"]),
                        "status": "failed",
                    },
                    aggregate_type="notification_delivery",
                    now=now,
                )
                await uow.commit()
                return self._scope(row, terminal=True, result_uncertain=True)
            if status not in {"pending", "retry_wait"}:
                raise ConflictError("notification delivery cannot be started")
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE notification_deliveries
                    SET status = 'sending', attempt_count = attempt_count + 1,
                        last_error_class = '', last_error_message = '',
                        updated_at = %s
                    WHERE user_uid = %s AND id = %s
                    """,
                    (now, tenant.user_uid, delivery_id),
                )
            await uow.commit()
            return self._scope(row, terminal=False)

    @staticmethod
    def _scope(
        row: Mapping[str, object],
        *,
        terminal: bool,
        result_uncertain: bool = False,
    ) -> _DeliveryScope:
        return _DeliveryScope(
            delivery_id=str(row["delivery_id"]),
            user_uid=str(row.get("user_uid") or ""),
            status=str(row["delivery_status"]),
            notification_event_id=str(row["notification_event_id"]),
            event_type=str(row["event_type"]),
            title=str(row["title"]),
            summary=str(row["summary"] or ""),
            action_path=str(row["action_path"] or ""),
            account_id=str(row["account_id"] or "").strip() or None,
            notification_asset_id=str(row["notification_asset_id"] or "").strip() or None,
            occurred_at=float(row["created_at"] or 0),
            channel_id=str(row["channel_id"]),
            channel_key=str(row["channel_key"]),
            channel_enabled=bool(row["channel_enabled"]),
            channel_public_config=_decode_json(row["channel_public_config"]),
            channel_secret_algorithm=str(row["channel_secret_algorithm"] or ""),
            channel_secret_key_version=int(row["channel_secret_key_version"] or 0),
            channel_secret_nonce=bytes(row["channel_secret_nonce"] or b""),
            channel_secret_ciphertext=bytes(row["channel_secret_ciphertext"] or b""),
            channel_use_proxy=bool(row["channel_use_proxy"]),
            rule_id=str(row["rule_id"]),
            rule_enabled=bool(row["rule_enabled"]),
            rule_filter=_decode_json(row["rule_filter"]),
            image_publisher_id=str(row["image_publisher_id"] or "").strip() or None,
            publisher_key=str(row["publisher_key"] or ""),
            publisher_endpoint_url=str(row["endpoint_url"] or ""),
            publisher_enabled=bool(row["publisher_enabled"]),
            publisher_public_config=_decode_json(row["publisher_public_config"]),
            publisher_secret_algorithm=str(row["publisher_secret_algorithm"] or ""),
            publisher_secret_key_version=int(row["publisher_secret_key_version"] or 0),
            publisher_secret_nonce=bytes(row["publisher_secret_nonce"] or b""),
            publisher_secret_ciphertext=bytes(row["publisher_secret_ciphertext"] or b""),
            user_enabled=bool(row["user_enabled"]),
            account_status=str(row["account_status"] or ""),
            provider_key=str(row["provider_key"] or ""),
            terminal=terminal,
            result_uncertain=result_uncertain,
        )

    def _decrypt_json(
        self,
        scope_id: str,
        algorithm: str,
        key_version: int,
        nonce: bytes,
        ciphertext: bytes,
    ) -> dict[str, object]:
        encrypted = _encrypted_value(
            algorithm,
            key_version,
            nonce,
            ciphertext,
        )
        if encrypted is None:
            return {}
        plaintext = self.cipher.decrypt(scope_id, encrypted)
        return _decode_json(plaintext)

    async def _load_proxy(
        self,
        tenant: TenantContext,
        scope: _DeliveryScope,
    ) -> ProxyConfig | None:
        if (
            scope.channel_key not in _PROXY_CHANNELS
            or not scope.channel_use_proxy
            or not bool(scope.rule_filter.get("use_proxy"))
        ):
            return None
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT id, proxy_scheme, host, port, username,
                       password_algorithm, password_key_version,
                       password_nonce, password_ciphertext
                FROM outbound_proxy_configs
                WHERE user_uid = %s AND traffic_scope = 'notifications'
                  AND enabled = 1
                ORDER BY id LIMIT 1
                """,
                (tenant.user_uid,),
            )
        if row is None:
            return None
        encrypted = _encrypted_value(
            row["password_algorithm"],
            row["password_key_version"],
            row["password_nonce"],
            row["password_ciphertext"],
        )
        password = ""
        if encrypted is not None:
            password = self.cipher.decrypt(str(row["id"]), encrypted).decode("utf-8")
        return ProxyConfig(
            str(row["id"]),
            str(row["proxy_scheme"]),
            str(row["host"]),
            int(row["port"]),
            str(row["username"] or ""),
            password,
        )

    async def _load_asset(
        self,
        tenant: TenantContext,
        asset_id: str,
    ) -> ImageAsset:
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT r.id, r.content_sha256, o.compression, o.object_kind
                FROM content_references r
                JOIN content_objects o
                  ON o.content_sha256 = r.content_sha256
                WHERE r.user_uid = %s AND r.id = %s
                  AND r.reference_kind = 'notification_asset'
                """,
                (tenant.user_uid, asset_id),
            )
        if row is None or str(row["object_kind"]) != "notification_asset":
            raise NotFoundError("notification asset was not found")
        digest = str(row["content_sha256"])
        async with self.store.open(digest) as handle:
            content = handle.read()
        compression = str(row["compression"] or "none")
        if compression == "gzip":
            content = gzip.decompress(content)
        elif compression != "none":
            raise ValueError("unsupported notification asset compression")
        return ImageAsset(asset_id, "notification.png", "image/png", content)

    async def _finish_delivery(
        self,
        tenant: TenantContext,
        scope: _DeliveryScope,
        *,
        status: str,
        error_class: str,
        error_message: str,
        now: float,
    ) -> None:
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE notification_deliveries
                    SET status = %s,
                        delivered_at = CASE WHEN %s = 'succeeded' THEN %s ELSE NULL END,
                        last_error_class = %s, last_error_message = %s,
                        updated_at = %s
                    WHERE user_uid = %s AND id = %s AND status = 'sending'
                    """,
                    (
                        status,
                        status,
                        now,
                        str(error_class or "")[:96],
                        str(error_message or "")[:512],
                        now,
                        tenant.user_uid,
                        scope.delivery_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConflictError("notification delivery state changed concurrently")
            await NotificationService._insert_realtime(
                connection,
                tenant.user_uid,
                "notification.delivery.updated",
                "notification_delivery",
                scope.delivery_id,
                {
                    "delivery_id": scope.delivery_id,
                    "notification_event_id": scope.notification_event_id,
                    "status": status,
                },
                now,
            )
            await OutboxRepository(connection, tenant).append(
                "notification.delivery.updated",
                scope.delivery_id,
                {
                    "delivery_id": scope.delivery_id,
                    "notification_event_id": scope.notification_event_id,
                    "status": status,
                },
                aggregate_type="notification_delivery",
                now=now,
            )
            await uow.commit()

    async def _release_asset_if_final(
        self,
        tenant: TenantContext,
        event_id: str,
    ) -> None:
        digest: str | None = None
        async with SqlUnitOfWork(self.pool) as uow:
            connection = self._connection(uow)
            event = await fetch_one(
                connection,
                """
                SELECT notification_asset_id
                FROM notification_events
                WHERE user_uid = %s AND id = %s
                FOR UPDATE
                """,
                (tenant.user_uid, event_id),
            )
            if event is None:
                raise NotFoundError("notification event was not found")
            asset_id = str(event["notification_asset_id"] or "").strip()
            if not asset_id:
                await uow.commit()
                return
            pending = await fetch_one(
                connection,
                """
                SELECT COUNT(*) AS pending_count
                FROM notification_deliveries
                WHERE user_uid = %s AND notification_event_id = %s
                  AND status NOT IN ('succeeded', 'failed', 'cancelled')
                """,
                (tenant.user_uid, event_id),
            )
            if int(pending["pending_count"] or 0) > 0:
                await uow.commit()
                return
            reference = await fetch_one(
                connection,
                """
                SELECT content_sha256
                FROM content_references
                WHERE user_uid = %s AND id = %s
                  AND reference_kind = 'notification_asset'
                FOR UPDATE
                """,
                (tenant.user_uid, asset_id),
            )
            async with connection.cursor() as cursor:
                if reference is not None:
                    digest = str(reference["content_sha256"])
                    await cursor.execute(
                        "DELETE FROM content_references WHERE user_uid = %s AND id = %s",
                        (tenant.user_uid, asset_id),
                    )
                await cursor.execute(
                    """
                    UPDATE notification_events
                    SET notification_asset_id = NULL
                    WHERE user_uid = %s AND id = %s
                    """,
                    (tenant.user_uid, event_id),
                )
            await uow.commit()
        if digest:
            async with self.pool.acquire() as connection:
                await self.store.remove_unreferenced(
                    digest,
                    ObjectRepository(connection),
                )

    @staticmethod
    def _connection(uow: SqlUnitOfWork) -> aiomysql.Connection:
        if uow.connection is None:
            raise RuntimeError("unit of work connection is unavailable")
        return uow.connection
