"""Notification model, channel, publisher, and durable delivery contracts."""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from flymail.domain.enums import ObjectKind
from flymail.domain.errors import ConflictError
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.credentials import CredentialCipher
from flymail.notifications.channels import ChannelRegistry
from flymail.notifications.contracts import (
    DeliveryResult,
    HttpRequest,
    HttpResponse,
    ImageAsset,
    ImagePublisherConfig,
    NotificationConfig,
    NotificationMessage,
    ProxyConfig,
    validate_public_http_url,
)
from flymail.notifications.image_publishers import ImagePublisherRegistry
from flymail.repositories.base import TenantContext
from flymail.repositories.objects import ObjectRepository
from flymail.repositories.jobs import LeasedJob
from flymail.workers.dispatcher import JobContext, WorkerDispatcher
from flymail.workers.notifications import (
    NotificationDeliveryHandler,
    NotificationService,
)
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


async def _one_chunk(value: bytes):
    yield value


def _decode_b64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class FakeHttpTransport:
    def __init__(self) -> None:
        self.requests: list[HttpRequest] = []
        self.responses: list[HttpResponse | BaseException] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return HttpResponse(200, {"ok": True}, "ok")


class NotificationContractsTests(MySqlIsolatedAsyncioTestCase):
    async def test_message_is_bounded_unicode_safe_and_excludes_sensitive_fields(self):
        message = NotificationMessage(
            event_id="evt_safe",
            event_type="mail.new",
            title="T" * 400,
            summary=("摘要🙂" * 400) + "\ud800",
            action_path="/mail/thread/thr_safe",
            occurred_at=100,
            account_id="acc_safe",
        )
        self.assertLessEqual(len(message.title), 160)
        self.assertLessEqual(len(message.summary), 700)
        message.title.encode("utf-8")
        message.summary.encode("utf-8")
        self.assertNotIn("\ud800", message.summary)
        serialized = repr(message)
        for forbidden in ("password", "credential", "oauth", "attachment_bytes", "raw_html"):
            self.assertNotIn(forbidden, serialized.casefold())

    async def test_http_request_repr_redacts_url_headers_payload_proxy_and_content(self):
        request = HttpRequest(
            "POST",
            "https://api.telegram.org/bottelegram-secret/sendMessage",
            headers={"Authorization": "Bearer webhook-secret"},
            json_body={"device_key": "bark-device-secret"},
            content=b"notification-image-secret",
            proxy_url="http://user:proxy-password@proxy.example:8080",
        )
        serialized = repr(request)
        for forbidden in (
            "telegram-secret",
            "webhook-secret",
            "bark-device-secret",
            "notification-image-secret",
            "proxy-password",
        ):
            self.assertNotIn(forbidden, serialized)

    async def test_supported_event_types_are_exact(self):
        for event_type in (
            "mail.new",
            "send.sent",
            "send.failed",
            "backup.completed",
            "backup.failed",
            "account.authorization_required",
            "system.storage_warning",
        ):
            NotificationMessage(
                event_id=f"evt_{event_type}",
                event_type=event_type,
                title="Title",
                summary="Summary",
                action_path="/",
                occurred_at=1,
            )
        with self.assertRaises(ValueError):
            NotificationMessage(
                event_id="evt_bad",
                event_type="mail.body.full",
                title="Title",
                summary="Summary",
                action_path="/",
                occurred_at=1,
            )

    async def test_public_url_validation_rejects_private_loopback_and_private_dns(self):
        for value in (
            "http://127.0.0.1/hook",
            "http://169.254.169.254/latest/meta-data",
            "https://10.0.0.1/hook",
            "ftp://example.com/file",
            "https://user:pass@example.com/hook",
        ):
            with self.assertRaises(ValueError):
                validate_public_http_url(value)

        def private_resolver(_host: str, _port: int):
            return ("192.168.1.10",)

        with self.assertRaises(ValueError):
            validate_public_http_url(
                "https://internal.example/hook",
                resolver=private_resolver,
            )
        self.assertEqual(
            validate_public_http_url(
                "https://notify.example/hook",
                resolver=lambda _host, _port: ("8.8.8.8",),
            ),
            "https://notify.example/hook",
        )

    async def test_all_channel_adapters_map_same_safe_message(self):
        transport = FakeHttpTransport()
        registry = ChannelRegistry.default(
            transport,
            resolver=lambda _host, _port: ("8.8.8.8",),
        )
        message = NotificationMessage(
            event_id="evt_channels",
            event_type="mail.new",
            title="New mail",
            summary="Safe summary",
            action_path="/mail/thread/1",
            occurred_at=100,
        )
        configs = {
            "bark": NotificationConfig(
                "chn_bark", "bark",
                {"endpoint_url": "https://bark.example/push"},
                {"device_key": "bark-device-secret"},
            ),
            "telegram": NotificationConfig(
                "chn_telegram", "telegram",
                {"chat_id": "123456"},
                {"bot_token": "telegram-token-secret"},
            ),
            "wecom": NotificationConfig(
                "chn_wecom", "wecom", {},
                {"webhook_url": "https://wecom.example/hook"},
            ),
            "dingtalk": NotificationConfig(
                "chn_dingtalk", "dingtalk", {},
                {"webhook_url": "https://dingtalk.example/hook"},
            ),
            "feishu": NotificationConfig(
                "chn_feishu", "feishu", {},
                {"webhook_url": "https://feishu.example/hook"},
            ),
            "generic_webhook": NotificationConfig(
                "chn_generic", "generic_webhook",
                {"endpoint_url": "https://webhook.example/events"},
                {"authorization": "Bearer webhook-secret"},
            ),
        }
        for key, config in configs.items():
            result = await registry.get(key).send(message, config, None)
            self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(transport.requests), 6)
        payloads = [
            json.dumps(dict(request.json_body), ensure_ascii=False)
            for request in transport.requests
        ]
        self.assertTrue(all("New mail" in payload for payload in payloads))
        self.assertTrue(all("Safe summary" in payload for payload in payloads))
        self.assertTrue(transport.requests[0].url.endswith("/bark-device-secret"))
        self.assertNotIn("device_key", transport.requests[0].json_body)
        self.assertIn("chat_id", transport.requests[1].json_body)
        self.assertIn("markdown", transport.requests[2].json_body)
        self.assertIn("msgtype", transport.requests[3].json_body)
        self.assertIn("content", transport.requests[4].json_body)
        self.assertEqual(transport.requests[5].headers["Authorization"], "Bearer webhook-secret")

    async def test_bark_and_telegram_image_requests_match_existing_protocol(self):
        transport = FakeHttpTransport()
        registry = ChannelRegistry.default(
            transport,
            resolver=lambda _host, _port: ("8.8.8.8",),
        )
        message = NotificationMessage(
            event_id="evt_image_channels",
            event_type="mail.new",
            title="New mail",
            summary="Safe summary",
            action_path="/mail/thread/1",
            occurred_at=100,
            image_url="https://cdn.example/mail.png",
        )
        transport.responses = [
            HttpResponse(200, {"code": 200}, "ok"),
            HttpResponse(200, {"ok": True, "result": {"message_id": 7}}, "ok"),
        ]
        bark = await registry.get("bark").send(
            message,
            NotificationConfig(
                "chn_bark_image",
                "bark",
                {"endpoint_url": "https://bark.example"},
                {"device_key": "bark-device-secret"},
            ),
            None,
        )
        telegram = await registry.get("telegram").send(
            message,
            NotificationConfig(
                "chn_telegram_image",
                "telegram",
                {"chat_id": "123456"},
                {"bot_token": "telegram-token-secret"},
            ),
            None,
        )
        self.assertEqual((bark.status, telegram.status), ("succeeded", "succeeded"))
        self.assertTrue(transport.requests[0].url.endswith("/bark-device-secret"))
        self.assertEqual(transport.requests[0].json_body["image"], message.image_url)
        self.assertNotIn("icon", transport.requests[0].json_body)
        self.assertTrue(transport.requests[1].url.endswith("/sendPhoto"))
        self.assertEqual(transport.requests[1].json_body["photo"], message.image_url)
        self.assertNotIn("image_url", transport.requests[1].json_body)

    async def test_channel_body_error_codes_are_not_misclassified_as_success(self):
        transport = FakeHttpTransport()
        registry = ChannelRegistry.default(
            transport,
            resolver=lambda _host, _port: ("8.8.8.8",),
        )
        message = NotificationMessage(
            event_id="evt_body_status",
            event_type="system.storage_warning",
            title="Storage",
            summary="Low disk",
            action_path="/settings/storage",
            occurred_at=1,
        )
        transport.responses = [
            HttpResponse(200, {"ok": False, "error_code": 429}, "too many requests"),
            HttpResponse(200, {"errcode": 40014, "errmsg": "invalid token"}, "error"),
            HttpResponse(200, {"code": 500, "message": "server error"}, "error"),
        ]
        telegram = await registry.get("telegram").send(
            message,
            NotificationConfig(
                "chn_telegram_body_error",
                "telegram",
                {"chat_id": "123456"},
                {"bot_token": "telegram-token-secret"},
            ),
            None,
        )
        wecom = await registry.get("wecom").send(
            message,
            NotificationConfig(
                "chn_wecom_body_error",
                "wecom",
                {},
                {"webhook_url": "https://wecom.example/hook"},
            ),
            None,
        )
        bark = await registry.get("bark").send(
            message,
            NotificationConfig(
                "chn_bark_body_error",
                "bark",
                {"endpoint_url": "https://bark.example"},
                {"device_key": "bark-device-secret"},
            ),
            None,
        )
        self.assertEqual(telegram.status, "retry")
        self.assertEqual(wecom.status, "failed")
        self.assertEqual(bark.status, "retry")

    async def test_http_status_classification_is_retryable_or_permanent(self):
        transport = FakeHttpTransport()
        registry = ChannelRegistry.default(
            transport,
            resolver=lambda _host, _port: ("8.8.8.8",),
        )
        message = NotificationMessage(
            event_id="evt_status",
            event_type="system.storage_warning",
            title="Storage",
            summary="Low disk",
            action_path="/settings/storage",
            occurred_at=1,
        )
        config = NotificationConfig(
            "chn_generic", "generic_webhook",
            {"endpoint_url": "https://webhook.example/events"},
            {},
        )
        transport.responses = [HttpResponse(503, {}, "unavailable")]
        self.assertEqual(
            (await registry.get("generic_webhook").send(message, config, None)).status,
            "retry",
        )
        transport.responses = [HttpResponse(401, {}, "unauthorized")]
        self.assertEqual(
            (await registry.get("generic_webhook").send(message, config, None)).status,
            "failed",
        )

    async def test_image_publishers_validate_and_map_upload_contracts(self):
        transport = FakeHttpTransport()
        publishers = ImagePublisherRegistry.default(
            transport,
            resolver=lambda _host, _port: ("8.8.8.8",),
        )
        asset = ImageAsset(
            asset_id="asset_contract",
            filename="mail.png",
            content_type="image/png",
            content=b"png-data",
        )
        transport.responses = [
            HttpResponse(
                200,
                {
                    "url": "https://cdn.example/image.png",
                    "delete_url": "https://imgbed.example/api/images/delete/1",
                },
                "ok",
            ),
            HttpResponse(
                200,
                {"public_url": "https://cdn.example/image.png"},
                "ok",
            ),
        ]
        flymail = await publishers.get("flymail_imgbed").publish(
            asset,
            ImagePublisherConfig(
                "pub_flymail",
                "flymail_imgbed",
                "https://imgbed.example/api/images",
                {"expires_seconds": 600},
                {"token": "imgbed-token-secret"},
            ),
            None,
        )
        self.assertEqual(flymail.url, "https://cdn.example/image.png")
        self.assertTrue(flymail.cleanup_supported)
        generic = await publishers.get("generic_https").publish(
            asset,
            ImagePublisherConfig(
                "pub_generic",
                "generic_https",
                "https://publisher.example/upload",
                {"url_field": "public_url"},
                {"authorization": "Bearer publisher-secret"},
            ),
            None,
        )
        self.assertEqual(generic.url, "https://cdn.example/image.png")
        self.assertEqual(len(transport.requests), 2)
        for request in transport.requests:
            self.assertEqual(request.content, b"png-data")
        with self.assertRaises(ValueError):
            ImagePublisherConfig(
                "pub_bad",
                "generic_https",
                "https://127.0.0.1/upload",
                {},
                {},
            )


class NotificationDeliveryTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-notify-")
        root = Path(self.temp_dir.name)
        self.store = ObjectStore(root / "objects", root / "tmp")
        self.tenant = TenantContext("usr_notify")
        self.master_secret = "notification-master-secret-value"
        self.cipher = CredentialCipher.from_master_secret(self.master_secret)
        self.transport = FakeHttpTransport()
        self.channels = ChannelRegistry.default(
            self.transport,
            resolver=lambda _host, _port: ("8.8.8.8",),
        )
        self.publishers = ImagePublisherRegistry.default(
            self.transport,
            resolver=lambda _host, _port: ("8.8.8.8",),
        )
        self.service = NotificationService(self.api_pool)
        self.handler = NotificationDeliveryHandler(
            self.worker_pool,
            self.store,
            self.cipher,
            self.channels,
            self.publishers,
        )
        await self._create_user_account()
        await self._create_channel(
            "chn_inapp", "in_app", "In app", {}, {}, use_proxy=False
        )
        await self._create_channel(
            "chn_telegram", "telegram", "Telegram",
            {"chat_id": "123456"},
            {"bot_token": "telegram-notify-secret"},
            use_proxy=True,
        )
        await self._create_channel(
            "chn_generic", "generic_webhook", "Webhook",
            {"endpoint_url": "https://webhook.example/events"},
            {"authorization": "Bearer webhook-notify-secret"},
            use_proxy=True,
        )
        await self._create_rule("rule_inapp", "chn_inapp", use_proxy=False)
        await self._create_rule("rule_telegram", "chn_telegram", use_proxy=True)
        await self._create_rule("rule_generic", "chn_generic", use_proxy=False)
        await self._create_proxy()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "job_attempts",
            "worker_jobs",
            "outbox_events",
            "realtime_events",
            "notification_deliveries",
            "notification_events",
            "notification_rules",
            "notification_image_publishers",
            "notification_channels",
            "outbound_proxy_configs",
            "content_references",
            "content_objects",
            "mail_accounts",
            "users",
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_user_account(self) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO users (
                        id, username, password_hash, role, enabled,
                        password_version, created_at, updated_at
                    ) VALUES ('usr_notify', 'notify-user', 'test-hash', 'user', 1, 1, 1, 1)
                    """
                )
                await cursor.execute(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        status, poll_interval_seconds, created_at, updated_at
                    ) VALUES ('acc_notify', 'usr_notify', 'generic',
                              'notify@example.com', 'notify@example.com',
                              'active', 300, 1, 1)
                    """
                )
            await connection.commit()

    def _encrypted(self, scope_id: str, value: object):
        encrypted = self.cipher.encrypt(
            scope_id,
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )
        return (
            encrypted.algorithm,
            encrypted.key_version,
            _decode_b64(encrypted.nonce_b64),
            _decode_b64(encrypted.ciphertext_b64),
        )

    async def _create_channel(
        self,
        channel_id: str,
        channel_key: str,
        display_name: str,
        public_config: dict,
        secret_config: dict,
        *,
        use_proxy: bool,
    ) -> None:
        algorithm, key_version, nonce, ciphertext = self._encrypted(
            channel_id,
            secret_config,
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO notification_channels (
                        id, user_uid, channel_key, display_name, enabled,
                        public_config, secret_algorithm, secret_key_version,
                        secret_nonce, secret_ciphertext, secret_auth_tag,
                        use_proxy, created_at, updated_at
                    ) VALUES (%s, 'usr_notify', %s, %s, 1, %s, %s, %s,
                              %s, %s, NULL, %s, 1, 1)
                    """,
                    (
                        channel_id,
                        channel_key,
                        display_name,
                        json.dumps(public_config),
                        algorithm,
                        key_version,
                        nonce,
                        ciphertext,
                        1 if use_proxy else 0,
                    ),
                )
            await connection.commit()

    async def _create_rule(
        self,
        rule_id: str,
        channel_id: str,
        *,
        use_proxy: bool,
        image_publisher_id: str | None = None,
    ) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO notification_rules (
                        id, user_uid, event_type, channel_id,
                        image_publisher_id, enabled, filter_json,
                        dedupe_window_seconds, created_at, updated_at
                    ) VALUES (%s, 'usr_notify', 'mail.new', %s, %s, 1, %s, 0, 1, 1)
                    """,
                    (
                        rule_id,
                        channel_id,
                        image_publisher_id,
                        json.dumps({"use_proxy": use_proxy, "image_enabled": bool(image_publisher_id)}),
                    ),
                )
            await connection.commit()

    async def _create_proxy(self) -> None:
        encrypted = self.cipher.encrypt("proxy_notify", b"proxy-password-secret")
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO outbound_proxy_configs (
                        id, user_uid, account_id, traffic_scope,
                        proxy_scheme, host, port, username,
                        password_algorithm, password_key_version,
                        password_nonce, password_ciphertext,
                        password_auth_tag, enabled, created_at, updated_at
                    ) VALUES (
                        'proxy_notify', 'usr_notify', NULL, 'notifications',
                        'http', 'proxy.example', 8080, 'proxy-user',
                        %s, %s, %s, %s, NULL, 1, 1, 1
                    )
                    """,
                    (
                        encrypted.algorithm,
                        encrypted.key_version,
                        _decode_b64(encrypted.nonce_b64),
                        _decode_b64(encrypted.ciphertext_b64),
                    ),
                )
            await connection.commit()

    async def _create_publisher(self, publisher_id: str = "pub_notify") -> None:
        algorithm, key_version, nonce, ciphertext = self._encrypted(
            publisher_id,
            {"token": "publisher-notify-secret"},
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO notification_image_publishers (
                        id, user_uid, publisher_key, display_name,
                        endpoint_url, enabled, public_config,
                        secret_algorithm, secret_key_version, secret_nonce,
                        secret_ciphertext, secret_auth_tag, created_at, updated_at
                    ) VALUES (
                        %s, 'usr_notify', 'flymail_imgbed', 'ImgBed',
                        'https://imgbed.example/api/images', 1,
                        '{"expires_seconds":600}', %s, %s, %s, %s, NULL, 1, 1
                    )
                    """,
                    (publisher_id, algorithm, key_version, nonce, ciphertext),
                )
            await connection.commit()

    async def _create_asset(self) -> tuple[str, str]:
        stored = await self.store.put_stream(
            ObjectKind.NOTIFICATION_ASSET,
            _one_chunk(b"temporary-notification-image"),
            expected_size=len(b"temporary-notification-image"),
        )
        reference_id = "ref_notification_asset"
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            repository = ObjectRepository(connection)
            async with repository.lock_object(stored.content_sha256):
                asset_id = await repository.attach_reference(
                    stored,
                    user_uid=self.tenant.user_uid,
                    reference_kind="notification_asset",
                    reference_id=reference_id,
                    pinned=False,
                    last_accessed_at=1,
                )
            await connection.commit()
        return asset_id, stored.content_sha256

    def context(self, *, channel_id: str, attempt: int = 1) -> JobContext:
        return JobContext(
            job_id=f"job_notify_{channel_id}_{attempt}",
            user_uid=self.tenant.user_uid,
            account_id="acc_notify",
            provider_key="generic",
            queue_name="notifications",
            worker_id="worker_notify",
            attempt_count=attempt,
            stop_event=asyncio.Event(),
        )

    async def scalar(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                row = await cursor.fetchone()
                return row[0] if row else None

    async def row(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchone()

    async def rows(self, sql: str, params: tuple | list = ()):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchall()

    async def publish(self, *, asset_id: str | None = None, dedupe: str = "mail-new-1"):
        return await self.service.publish(
            self.tenant,
            event_type="mail.new",
            aggregate_id="msg_notify",
            title="New message",
            summary="Safe notification preview",
            action_path="/mail/thread/thr_notify",
            account_id="acc_notify",
            dedupe_key=dedupe,
            notification_asset_id=asset_id,
            now=100,
        )

    async def test_concurrent_duplicate_source_creates_one_event_and_delivery_set(self):
        results = await asyncio.gather(*(self.publish() for _ in range(8)))
        self.assertEqual(len({result.event_id for result in results}), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM notification_events"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM notification_deliveries"), 2)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE job_kind='notification.deliver'"),
            2,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM outbox_events WHERE event_type='notification.created'"),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM realtime_events WHERE event_type='notification.created'"),
            1,
        )

    async def test_new_mail_creates_one_in_app_event_and_deduped_external_jobs(self):
        first = await self.publish()
        second = await self.publish()
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM notification_events"), 1)
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM notification_deliveries"), 2)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM worker_jobs WHERE job_kind='notification.deliver'"),
            2,
        )
        payloads = await self.rows(
            "SELECT payload FROM worker_jobs WHERE job_kind='notification.deliver'"
        )
        serialized = "\n".join(str(row[0]) for row in payloads)
        for forbidden in (
            "telegram-notify-secret",
            "webhook-notify-secret",
            "proxy-password-secret",
            "Safe notification preview",
        ):
            self.assertNotIn(forbidden, serialized)

    async def test_handler_decrypts_secrets_and_uses_proxy_only_when_rule_allows(self):
        published = await self.publish()
        deliveries = await self.rows(
            """
            SELECT d.id, c.id, c.channel_key
            FROM notification_deliveries d
            JOIN notification_channels c ON c.id=d.channel_id
            WHERE d.notification_event_id=%s
            ORDER BY c.channel_key
            """,
            (published.event_id,),
        )
        for delivery_id, channel_id, _channel_key in deliveries:
            outcome = await self.handler.handle(
                self.context(channel_id=str(channel_id)),
                {"delivery_id": str(delivery_id)},
            )
            self.assertEqual(outcome.action, "complete")
        self.assertEqual(len(self.transport.requests), 2)
        generic_request = next(
            request for request in self.transport.requests
            if "webhook.example" in request.url
        )
        telegram_request = next(
            request for request in self.transport.requests
            if "api.telegram.org" in request.url
        )
        self.assertIsNone(generic_request.proxy_url)
        self.assertIn("webhook-notify-secret", generic_request.headers["Authorization"])
        self.assertIsNotNone(telegram_request.proxy_url)
        self.assertIn("proxy-user", telegram_request.proxy_url)
        self.assertIn("telegram-notify-secret", telegram_request.url)
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM notification_deliveries WHERE status='succeeded'"),
            2,
        )

    async def test_retryable_and_permanent_failure_are_isolated(self):
        published = await self.publish()
        deliveries = await self.rows(
            """
            SELECT d.id, c.id, c.channel_key
            FROM notification_deliveries d
            JOIN notification_channels c ON c.id=d.channel_id
            WHERE d.notification_event_id=%s
            ORDER BY c.channel_key
            """,
            (published.event_id,),
        )
        self.transport.responses = [
            HttpResponse(401, {}, "unauthorized"),
            HttpResponse(503, {}, "unavailable"),
        ]
        outcomes = []
        for delivery_id, channel_id, _channel_key in deliveries:
            outcomes.append(await self.handler.handle(
                self.context(channel_id=str(channel_id)),
                {"delivery_id": str(delivery_id)},
            ))
        self.assertEqual({outcome.action for outcome in outcomes}, {"fail", "retry"})
        self.assertEqual(
            set(row[0] for row in await self.rows(
                "SELECT status FROM notification_deliveries ORDER BY status"
            )),
            {"failed", "retry_wait"},
        )
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM notification_events"), 1)

    async def test_corrupt_channel_secret_fails_safely_without_stuck_sending(self):
        published = await self.publish(dedupe="corrupt-secret")
        delivery_id, channel_id = await self.row(
            """
            SELECT d.id, d.channel_id
            FROM notification_deliveries d
            JOIN notification_channels c ON c.id=d.channel_id
            WHERE d.notification_event_id=%s AND c.channel_key='generic_webhook'
            """,
            (published.event_id,),
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE notification_channels SET secret_ciphertext=X'00' WHERE id=%s",
                    (channel_id,),
                )
            await connection.commit()
        outcome = await self.handler.handle(
            self.context(channel_id=str(channel_id)),
            {"delivery_id": str(delivery_id)},
        )
        self.assertEqual(outcome.action, "fail")
        self.assertEqual(outcome.error_class, "NotificationConfigurationError")
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM notification_deliveries WHERE id=%s",
                (delivery_id,),
            ),
            "failed",
        )

    async def test_stale_sending_delivery_never_repeats_external_request(self):
        published = await self.publish(dedupe="stale-sending")
        delivery_id, channel_id = await self.row(
            """
            SELECT d.id, d.channel_id
            FROM notification_deliveries d
            JOIN notification_channels c ON c.id=d.channel_id
            WHERE d.notification_event_id=%s AND c.channel_key='generic_webhook'
            """,
            (published.event_id,),
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE notification_deliveries SET status='sending', attempt_count=1 WHERE id=%s",
                    (delivery_id,),
                )
            await connection.commit()
        outcome = await self.handler.handle(
            self.context(channel_id=str(channel_id), attempt=2),
            {"delivery_id": str(delivery_id)},
        )
        self.assertEqual(outcome.action, "fail")
        self.assertEqual(outcome.error_class, "NotificationResultUncertain")
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(
            await self.scalar(
                "SELECT status FROM notification_deliveries WHERE id=%s",
                (delivery_id,),
            ),
            "failed",
        )

    async def test_handler_registers_directly_with_worker_dispatcher(self):
        published = await self.publish(dedupe="dispatcher-notification")
        delivery_id, channel_id = await self.row(
            """
            SELECT d.id, d.channel_id
            FROM notification_deliveries d
            JOIN notification_channels c ON c.id=d.channel_id
            WHERE d.notification_event_id=%s AND c.channel_key='generic_webhook'
            """,
            (published.event_id,),
        )
        dispatcher = WorkerDispatcher()
        dispatcher.register("notification.deliver", self.handler.handle)
        outcome = await dispatcher.dispatch(
            LeasedJob(
                id="job_notification_dispatch",
                user_uid=self.tenant.user_uid,
                account_id="acc_notify",
                provider_key="generic",
                queue_name="notifications",
                job_kind="notification.deliver",
                priority=50,
                available_at=100,
                lease_owner="worker_notify",
                lease_token="lease_notification_dispatch",
                lease_expires_at=160,
                attempt_count=1,
                max_attempts=8,
                dedupe_key=f"notification-delivery:{delivery_id}",
                payload={"delivery_id": str(delivery_id)},
            ),
            stop_event=asyncio.Event(),
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(len(self.transport.requests), 1)

    async def test_duplicate_delivery_job_does_not_send_twice(self):
        published = await self.publish()
        delivery_id, channel_id = await self.row(
            """
            SELECT d.id, d.channel_id
            FROM notification_deliveries d
            JOIN notification_channels c ON c.id=d.channel_id
            WHERE d.notification_event_id=%s AND c.channel_key='generic_webhook'
            """,
            (published.event_id,),
        )
        first = await self.handler.handle(
            self.context(channel_id=str(channel_id)),
            {"delivery_id": str(delivery_id)},
        )
        second = await self.handler.handle(
            self.context(channel_id=str(channel_id), attempt=2),
            {"delivery_id": str(delivery_id)},
        )
        self.assertEqual(first.action, "complete")
        self.assertEqual(second.action, "complete")
        self.assertEqual(len(self.transport.requests), 1)

    async def test_terminal_replay_releases_asset_left_by_post_commit_crash(self):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("UPDATE notification_rules SET enabled=0 WHERE id='rule_telegram'")
            await connection.commit()
        asset_id, digest = await self._create_asset()
        published = await self.publish(asset_id=asset_id, dedupe="terminal-replay-cleanup")
        delivery_id, channel_id = await self.row(
            "SELECT id, channel_id FROM notification_deliveries WHERE notification_event_id=%s",
            (published.event_id,),
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE notification_deliveries SET status='succeeded', delivered_at=100 WHERE id=%s",
                    (delivery_id,),
                )
            await connection.commit()

        outcome = await self.handler.handle(
            self.context(channel_id=str(channel_id), attempt=2),
            {"delivery_id": str(delivery_id)},
        )

        self.assertEqual(outcome.action, "complete")
        self.assertEqual(self.transport.requests, [])
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM content_references WHERE id=%s", (asset_id,)),
            0,
        )
        self.assertFalse((self.store.root / digest[:2] / digest).exists())

    async def test_disabled_user_account_rule_or_channel_prevents_delivery(self):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("UPDATE notification_rules SET enabled=0 WHERE id='rule_generic'")
                await cursor.execute("UPDATE notification_channels SET enabled=0 WHERE id='chn_telegram'")
            await connection.commit()
        published = await self.publish(dedupe="disabled-rules")
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM notification_deliveries WHERE notification_event_id=%s",
                (published.event_id,),
            ),
            0,
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("UPDATE users SET enabled=0 WHERE id='usr_notify'")
            await connection.commit()
        with self.assertRaises(ConflictError):
            await self.publish(dedupe="disabled-user")

    async def test_asset_waits_for_all_deliveries_then_is_released(self):
        await self._create_publisher()
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE notification_rules
                    SET image_publisher_id='pub_notify', filter_json=%s
                    WHERE id IN ('rule_generic', 'rule_telegram')
                    """,
                    (json.dumps({"use_proxy": False, "image_enabled": True}),),
                )
            await connection.commit()
        asset_id, digest = await self._create_asset()
        published = await self.publish(asset_id=asset_id, dedupe="image-multi")
        deliveries = await self.rows(
            """
            SELECT d.id, d.channel_id, c.channel_key
            FROM notification_deliveries d
            JOIN notification_channels c ON c.id=d.channel_id
            WHERE d.notification_event_id=%s
            ORDER BY c.channel_key
            """,
            (published.event_id,),
        )
        self.transport.responses = [
            HttpResponse(200, {"url": "https://cdn.example/image-1.png"}, "ok"),
            HttpResponse(200, {"ok": True}, "ok"),
            HttpResponse(200, {"url": "https://cdn.example/image-2.png"}, "ok"),
            HttpResponse(200, {"ok": True}, "ok"),
        ]
        first_id, first_channel, _first_key = deliveries[0]
        first = await self.handler.handle(
            self.context(channel_id=str(first_channel)),
            {"delivery_id": str(first_id)},
        )
        self.assertEqual(first.action, "complete")
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM content_references WHERE id=%s", (asset_id,)),
            1,
        )
        self.assertTrue((self.store.root / digest[:2] / digest).exists())
        second_id, second_channel, _second_key = deliveries[1]
        second = await self.handler.handle(
            self.context(channel_id=str(second_channel)),
            {"delivery_id": str(second_id)},
        )
        self.assertEqual(second.action, "complete")
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM content_references WHERE id=%s", (asset_id,)),
            0,
        )
        self.assertFalse((self.store.root / digest[:2] / digest).exists())
        channel_payloads = [
            dict(request.json_body)
            for request in self.transport.requests
            if request.content is None
        ]
        self.assertTrue(any("image_url" in payload for payload in channel_payloads))

    async def test_publisher_failure_falls_back_to_text_and_final_delivery_releases_asset(self):
        await self._create_publisher()
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE notification_rules
                    SET image_publisher_id='pub_notify', filter_json=%s
                    WHERE id='rule_generic'
                    """,
                    (json.dumps({"use_proxy": False, "image_enabled": True}),),
                )
                await cursor.execute("UPDATE notification_rules SET enabled=0 WHERE id='rule_telegram'")
            await connection.commit()
        asset_id, digest = await self._create_asset()
        published = await self.publish(asset_id=asset_id, dedupe="image-fallback")
        delivery_id, channel_id = await self.row(
            "SELECT id, channel_id FROM notification_deliveries WHERE notification_event_id=%s",
            (published.event_id,),
        )
        self.transport.responses = [
            HttpResponse(500, {}, "publisher unavailable"),
            HttpResponse(200, {"ok": True}, "ok"),
        ]
        outcome = await self.handler.handle(
            self.context(channel_id=str(channel_id)),
            {"delivery_id": str(delivery_id)},
        )
        self.assertEqual(outcome.action, "complete")
        self.assertEqual(len(self.transport.requests), 2)
        webhook_payload = self.transport.requests[-1].json_body
        self.assertNotIn("image_url", webhook_payload)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE id=%s",
                (asset_id,),
            ),
            0,
        )
        self.assertFalse((self.store.root / digest[:2] / digest).exists())


if __name__ == "__main__":
    import unittest

    unittest.main()
