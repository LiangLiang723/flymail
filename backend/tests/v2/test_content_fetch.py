from __future__ import annotations

import asyncio
import base64
import gzip
import quopri
import tempfile
import unittest
from pathlib import Path

from flymail.domain.errors import ConflictError, PermanentError
from flymail.domain.enums import ObjectKind
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.quota import QuotaService
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.providers.core.bodystructure import parse_bodystructure
from flymail.repositories.accounts import AccountRepository
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.mailboxes import MailboxRepository
from flymail.repositories.messages import MessageRepository
from flymail.repositories.users import UserRepository
from flymail.workers.content_fetch import (
    ContentFetchService,
    ContentJobPublisher,
    RemoteContentLocator,
)
from flymail.workers.ingestion import MessageIngestionService, RemoteSummary
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


class FakeContentTransport:
    def __init__(self, responses: dict[str, bytes | tuple[bytes, ...]]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[RemoteContentLocator, str]] = []

    def stream(
        self,
        locator: RemoteContentLocator,
        fetch_spec: str,
    ):
        self.calls.append((locator, fetch_spec))
        if fetch_spec not in self.responses:
            raise AssertionError(f"unexpected fetch spec: {fetch_spec}")
        raw = self.responses[fetch_spec]
        chunks = raw if isinstance(raw, tuple) else (raw,)

        async def generate():
            for chunk in chunks:
                await asyncio.sleep(0)
                yield chunk

        return generate()


class ContentFetchTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-content-")
        root = Path(self.temp_dir.name)
        self.store = ObjectStore(root / "objects", root / "tmp")
        self.tenant, self.account, self.mailbox, self.message_id, self.remote_id = (
            await self._create_message()
        )
        self.tree = self._content_tree()
        self.transport = FakeContentTransport(self._responses())
        self.publisher = ContentJobPublisher(self.api_pool)
        self.service = ContentFetchService(
            self.api_pool,
            self.store,
            self.transport,
            self.publisher,
            body_limit_bytes=2 * 1024 * 1024,
            attachment_limit_bytes=2 * 1024 * 1024,
            partial_chunk_bytes=4,
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "job_attempts",
            "worker_jobs",
            "outbox_events",
            "body_search_documents",
            "content_references",
            "content_objects",
            "message_attachments",
            "message_body_parts",
            "message_bodies",
            "thread_projections",
            "thread_messages",
            "message_memberships",
            "message_remote_instances",
            "message_headers",
            "messages",
            "threads",
            "mailboxes",
            "provider_credentials",
            "mail_identities",
            "mail_accounts",
            "user_profiles",
            "user_settings",
            "users",
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_message(self):
        async with self.pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr-content-admin"),
                username="content-user",
                password_hash="test-password-hash",
            )
            tenant = TenantContext(user.id)
            account = await AccountRepository(connection).create_account(
                tenant,
                provider_key="generic",
                email="content@example.com",
                status="active",
            )
            mailbox = await MailboxRepository(connection).upsert_mailbox(
                tenant,
                account_id=account.id,
                native_key="INBOX",
                native_name="Inbox",
                semantic_key="inbox",
                mailbox_type="folder",
                uidvalidity=77,
            )
            await connection.commit()
        await MessageIngestionService(self.api_pool).ingest_batch(
            account,
            mailbox,
            (
                RemoteSummary(
                    remote_uid=9,
                    uidvalidity=77,
                    message_id_header="<content@example.com>",
                    subject="Layered content",
                    from_addresses=("alice@example.com",),
                    to_addresses=("content@example.com",),
                    sent_at=100,
                    received_at=100,
                    size_bytes=4096,
                    has_attachments=True,
                    snippet="metadata only",
                ),
            ),
        )
        message_id = str(await self.scalar("SELECT id FROM messages LIMIT 1"))
        remote_id = str(await self.scalar("SELECT id FROM message_remote_instances LIMIT 1"))
        return tenant, account, mailbox, message_id, remote_id

    @staticmethod
    def _content_tree():
        raw = [
            [
                [
                    [
                        "TEXT", "PLAIN", ["CHARSET", "UTF-8"], None, None,
                        "BASE64", 64, 2,
                    ],
                    [
                        "TEXT", "HTML", ["CHARSET", "UTF-8"], None, None,
                        "QUOTED-PRINTABLE", 512, 8,
                    ],
                    "ALTERNATIVE",
                ],
                [
                    "IMAGE", "PNG", ["NAME", "logo.png"], "<logo-cid>", None,
                    "BASE64", 12, None, ["INLINE", ["FILENAME", "logo.png"]],
                ],
                [
                    "IMAGE", "PNG", ["NAME", "unused.png"], "<unused-cid>", None,
                    "BASE64", 12, None, ["INLINE", ["FILENAME", "unused.png"]],
                ],
                "RELATED",
                ["TYPE", "text/html"],
            ],
            [
                "APPLICATION", "OCTET-STREAM", ["NAME", "report.bin"], None, None,
                "BINARY", 10, None, ["ATTACHMENT", ["FILENAME", "report.bin"]],
            ],
            [
                "APPLICATION", "OCTET-STREAM", ["NAME", "copy.bin"], None, None,
                "BINARY", 10, None, ["ATTACHMENT", ["FILENAME", "copy.bin"]],
            ],
            "MIXED",
        ]
        return parse_bodystructure(raw)

    @staticmethod
    def _responses() -> dict[str, bytes | tuple[bytes, ...]]:
        html = (
            b'<html><body><script>alert(1)</script><p>Hello <b>world</b></p>'
            b'<img src="cid:logo-cid"><img src="https://tracker.example/pixel">'
            b'</body></html>'
        )
        attachment = b"0123456789"
        return {
            "BODY.PEEK[1.1.1]": base64.b64encode(b"Plain hello world"),
            "BODY.PEEK[1.1.2]": quopri.encodestring(html),
            "BODY.PEEK[1.2]": base64.b64encode(b"logo-bytes"),
            "BODY.PEEK[1.3]": base64.b64encode(b"unused-data"),
            "BODY.PEEK[2]": attachment,
            "BODY.PEEK[3]": attachment,
            "BODY.PEEK[2]<0.4>": b"0123",
            "BODY.PEEK[2]<4.4>": b"4567",
            "BODY.PEEK[2]<8.2>": b"89",
            "BODY.PEEK[]": b"From: alice@example.com\r\n\r\nRaw message",
        }

    async def record_structure(self):
        return await self.service.record_structure(
            self.tenant,
            message_id=self.message_id,
            remote_instance_id=self.remote_id,
            tree=self.tree,
            now=101,
        )

    async def read_object(self, digest: str) -> bytes:
        compression = str(await self.scalar(
            "SELECT compression FROM content_objects WHERE content_sha256 = %s",
            (digest,),
        ))
        async with self.store.open(digest) as handle:
            data = handle.read()
        return gzip.decompress(data) if compression == "gzip" else data

    async def test_record_structure_writes_metadata_without_fetching_bytes(self):
        result = await self.record_structure()
        self.assertEqual(result.body_parts, 2)
        self.assertEqual(result.inline_parts, 2)
        self.assertEqual(result.ordinary_attachments, 2)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(
            await self.rows(
                "SELECT body_kind, imap_part FROM message_body_parts ORDER BY body_kind"
            ),
            [("html", "1.1.2"), ("text", "1.1.1")],
        )
        attachments = await self.rows(
            """
            SELECT imap_part, is_inline, cache_state, content_sha256
            FROM message_attachments ORDER BY imap_part
            """
        )
        self.assertEqual(
            attachments,
            [
                ("1.2", 1, "not_requested", None),
                ("1.3", 1, "not_requested", None),
                ("2", 0, "not_requested", None),
                ("3", 0, "not_requested", None),
            ],
        )

    async def test_body_fetch_uses_only_exact_body_parts_and_queues_referenced_cid(self):
        await self.record_structure()
        job_ids = await asyncio.gather(
            *(self.service.request_body(self.tenant, self.message_id, now=102) for _ in range(8))
        )
        self.assertEqual(len(set(job_ids)), 1)
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'content.body'"
            ) or 0),
            1,
        )
        result = await self.service.fetch_body(self.tenant, self.message_id, now=103)
        self.assertEqual(result.state, "ready")
        specs = [spec for _locator, spec in self.transport.calls]
        self.assertEqual(specs, ["BODY.PEEK[1.1.1]", "BODY.PEEK[1.1.2]"])
        self.assertNotIn("BODY.PEEK[2]", specs)
        self.assertNotIn("BODY.PEEK[3]", specs)
        self.assertNotIn("BODY.PEEK[]", specs)

        body_row = await self.row(
            """
            SELECT state, html_object_sha256, text_object_sha256
            FROM message_bodies WHERE message_id = %s
            """,
            (self.message_id,),
        )
        self.assertEqual(body_row[0], "ready")
        self.assertTrue(body_row[1])
        self.assertTrue(body_row[2])
        html = (await self.read_object(str(body_row[1]))).decode("utf-8")
        self.assertNotIn("<script", html.casefold())
        self.assertNotIn("tracker.example", html)
        self.assertNotIn("cid:", html.casefold())
        logo_id = str(await self.scalar(
            "SELECT id FROM message_attachments WHERE content_id = 'logo-cid'"
        ))
        self.assertIn(f"/api/v2/mail/content/inline/{logo_id}", html)
        self.assertNotIn(str(body_row[1]), html)

        inline_states = await self.rows(
            """
            SELECT content_id, is_referenced_inline, cache_state
            FROM message_attachments WHERE is_inline = 1 ORDER BY content_id
            """
        )
        self.assertEqual(
            inline_states,
            [
                ("logo-cid", 1, "queued"),
                ("unused-cid", 0, "not_requested"),
            ],
        )
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'content.inline'"
            ) or 0),
            1,
        )
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM body_search_documents WHERE message_id = %s",
                (self.message_id,),
            ) or 0),
            1,
        )

    async def test_attachment_fetch_uses_exact_or_partial_part_and_deduplicates_content(self):
        await self.record_structure()
        attachment_ids = [
            str(row[0])
            for row in await self.rows(
                "SELECT id FROM message_attachments WHERE is_inline = 0 ORDER BY imap_part"
            )
        ]
        await self.service.request_attachment(self.tenant, attachment_ids[0], now=104)
        first = await self.service.fetch_attachment(
            self.tenant,
            attachment_ids[0],
            supports_partial=True,
            now=105,
        )
        await self.service.request_attachment(self.tenant, attachment_ids[1], now=106)
        second = await self.service.fetch_attachment(
            self.tenant,
            attachment_ids[1],
            supports_partial=False,
            now=107,
        )
        self.assertEqual(first.content_sha256, second.content_sha256)
        specs = [spec for _locator, spec in self.transport.calls]
        self.assertEqual(
            specs,
            [
                "BODY.PEEK[2]<0.4>",
                "BODY.PEEK[2]<4.4>",
                "BODY.PEEK[2]<8.2>",
                "BODY.PEEK[3]",
            ],
        )
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM content_objects WHERE object_kind = 'attachment'"
            ) or 0),
            1,
        )
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE reference_kind = 'message_attachment'"
            ) or 0),
            2,
        )

    async def test_attachment_stream_reaches_object_store_before_remote_stream_finishes(self):
        await self.record_structure()
        attachment_id = str(await self.scalar(
            "SELECT id FROM message_attachments WHERE is_inline = 0 ORDER BY imap_part LIMIT 1"
        ))
        await self.service.request_attachment(self.tenant, attachment_id, now=107.1)
        first_chunk_sent = asyncio.Event()
        release_remote = asyncio.Event()
        store_started = asyncio.Event()

        class StreamingTransport:
            def stream(_self, locator, fetch_spec):
                async def generate():
                    yield b"0123"
                    first_chunk_sent.set()
                    await release_remote.wait()
                    yield b"456789"
                return generate()

        class TrackingStore(ObjectStore):
            async def put_stream(_self, kind, chunks, expected_size=None):
                store_started.set()
                return await super().put_stream(kind, chunks, expected_size)

        root = Path(self.temp_dir.name) / "streaming"
        self.service.transport = StreamingTransport()
        self.service.store = TrackingStore(root / "objects", root / "tmp")
        task = asyncio.create_task(
            self.service.fetch_attachment(
                self.tenant,
                attachment_id,
                supports_partial=False,
                now=107.2,
            )
        )
        await asyncio.wait_for(first_chunk_sent.wait(), timeout=1)
        try:
            self.assertTrue(store_started.is_set())
        finally:
            release_remote.set()
            await task

    async def test_inline_fetch_is_separate_and_uses_exact_part(self):
        await self.record_structure()
        await self.service.request_body(self.tenant, self.message_id, now=108)
        await self.service.fetch_body(self.tenant, self.message_id, now=109)
        self.transport.calls.clear()
        inline_id = str(await self.scalar(
            "SELECT id FROM message_attachments WHERE content_id = 'logo-cid'"
        ))
        result = await self.service.fetch_inline(self.tenant, inline_id, now=110)
        self.assertEqual(result.state, "ready")
        self.assertEqual(
            [spec for _locator, spec in self.transport.calls],
            ["BODY.PEEK[1.2]"],
        )

    async def test_raw_eml_is_only_fetched_by_explicit_raw_job(self):
        await self.record_structure()
        await self.service.request_body(self.tenant, self.message_id, now=111)
        await self.service.fetch_body(self.tenant, self.message_id, now=112)
        self.assertNotIn("BODY.PEEK[]", [spec for _locator, spec in self.transport.calls])
        self.transport.calls.clear()
        await self.service.request_raw_eml(self.tenant, self.message_id, now=113)
        result = await self.service.fetch_raw_eml(self.tenant, self.message_id, now=114)
        self.assertEqual(result.state, "ready")
        self.assertEqual(
            [spec for _locator, spec in self.transport.calls],
            ["BODY.PEEK[]"],
        )
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE reference_kind = 'raw_eml'"
            ) or 0),
            1,
        )

    async def test_declared_oversize_body_is_rejected_before_network_fetch(self):
        await self.record_structure()
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE message_body_parts
                    SET remote_size_bytes = %s
                    WHERE message_id = %s AND body_kind = 'html'
                    """,
                    (self.service.body_limit_bytes + 1, self.message_id),
                )
            await connection.commit()
        await self.service.request_body(self.tenant, self.message_id, now=114)
        with self.assertRaises(PermanentError):
            await self.service.fetch_body(self.tenant, self.message_id, now=115)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(
            await self.scalar("SELECT state FROM message_bodies WHERE message_id = %s", (self.message_id,)),
            "failed",
        )

    async def test_declared_oversize_attachment_is_rejected_before_network_fetch(self):
        await self.record_structure()
        attachment_id = str(await self.scalar(
            "SELECT id FROM message_attachments WHERE is_inline = 0 ORDER BY imap_part LIMIT 1"
        ))
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "UPDATE message_attachments SET remote_size_bytes = %s WHERE id = %s",
                    (self.service.attachment_limit_bytes + 1, attachment_id),
                )
            await connection.commit()
        await self.service.request_attachment(self.tenant, attachment_id, now=115)
        with self.assertRaises(PermanentError):
            await self.service.fetch_attachment(
                self.tenant,
                attachment_id,
                supports_partial=True,
                now=116,
            )
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(
            await self.scalar("SELECT cache_state FROM message_attachments WHERE id = %s", (attachment_id,)),
            "failed",
        )

    async def test_cancelled_body_fetch_does_not_leave_fetching_state(self):
        await self.record_structure()
        await self.service.request_body(self.tenant, self.message_id, now=117)
        started = asyncio.Event()

        class BlockingTransport:
            def stream(_self, locator, fetch_spec):
                async def generate():
                    started.set()
                    await asyncio.Event().wait()
                    yield b""
                return generate()

        self.service.transport = BlockingTransport()
        task = asyncio.create_task(
            self.service.fetch_body(self.tenant, self.message_id, now=118)
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(
            await self.scalar("SELECT state FROM message_bodies WHERE message_id = %s", (self.message_id,)),
            "failed",
        )

    async def test_repeated_raw_fetch_replaces_cached_size_instead_of_double_counting(self):
        await self.record_structure()
        await self.service.request_body(self.tenant, self.message_id, now=119)
        await self.service.fetch_body(self.tenant, self.message_id, now=120)
        await self.service.request_raw_eml(self.tenant, self.message_id, now=121)
        await self.service.fetch_raw_eml(self.tenant, self.message_id, now=122)
        first_size = int(await self.scalar(
            "SELECT body_size_bytes FROM message_bodies WHERE message_id = %s",
            (self.message_id,),
        ) or 0)
        await self.service.request_raw_eml(self.tenant, self.message_id, now=123)
        await self.service.fetch_raw_eml(self.tenant, self.message_id, now=124)
        second_size = int(await self.scalar(
            "SELECT body_size_bytes FROM message_bodies WHERE message_id = %s",
            (self.message_id,),
        ) or 0)
        self.assertEqual(second_size, first_size)

    async def test_second_body_executor_is_rejected_before_remote_fetch(self):
        await self.record_structure()
        await self.service.request_body(self.tenant, self.message_id, now=124.1)
        async with self.pool.acquire() as connection:
            await connection.begin()
            await MessageRepository(connection).transition_body_state(
                self.tenant,
                self.message_id,
                "fetching",
                now=124.2,
            )
            await connection.commit()
        with self.assertRaises(ConflictError):
            await self.service.fetch_body(self.tenant, self.message_id, now=124.3)
        self.assertEqual(self.transport.calls, [])

    async def test_ready_body_and_attachment_requests_do_not_enqueue_new_work(self):
        await self.record_structure()
        await self.service.request_body(self.tenant, self.message_id, now=124.4)
        await self.service.fetch_body(self.tenant, self.message_id, now=124.5)
        body_jobs = int(await self.scalar(
            "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'content.body'"
        ) or 0)
        with self.assertRaises(ConflictError):
            await self.service.request_body(self.tenant, self.message_id, now=124.6)
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'content.body'"
            ) or 0),
            body_jobs,
        )

        attachment_id = str(await self.scalar(
            "SELECT id FROM message_attachments WHERE is_inline = 0 ORDER BY imap_part LIMIT 1"
        ))
        await self.service.request_attachment(self.tenant, attachment_id, now=124.7)
        await self.service.fetch_attachment(
            self.tenant,
            attachment_id,
            supports_partial=True,
            now=124.8,
        )
        attachment_jobs = int(await self.scalar(
            "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'content.attachment'"
        ) or 0)
        with self.assertRaises(ConflictError):
            await self.service.request_attachment(self.tenant, attachment_id, now=124.9)
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE job_kind = 'content.attachment'"
            ) or 0),
            attachment_jobs,
        )

    async def test_second_attachment_executor_is_rejected_before_remote_fetch(self):
        await self.record_structure()
        attachment_id = str(await self.scalar(
            "SELECT id FROM message_attachments WHERE is_inline = 0 ORDER BY imap_part LIMIT 1"
        ))
        await self.service.request_attachment(self.tenant, attachment_id, now=125.1)
        async with self.pool.acquire() as connection:
            await connection.begin()
            await self.service._transition_attachment(
                connection,
                self.tenant,
                attachment_id,
                "fetching",
                125.2,
            )
            await connection.commit()
        with self.assertRaises(ConflictError):
            await self.service.fetch_attachment(
                self.tenant,
                attachment_id,
                supports_partial=True,
                now=125.3,
            )
        self.assertEqual(self.transport.calls, [])

    async def test_body_state_machine_rejects_illegal_transition(self):
        await self.record_structure()
        async with self.pool.acquire() as connection:
            await connection.begin()
            repository = MessageRepository(connection)
            self.assertTrue(await repository.transition_body_state(
                self.tenant, self.message_id, "queued", now=120
            ))
            self.assertTrue(await repository.transition_body_state(
                self.tenant, self.message_id, "fetching", now=121
            ))
            self.assertTrue(await repository.transition_body_state(
                self.tenant, self.message_id, "ready", now=122
            ))
            with self.assertRaises(ConflictError):
                await repository.transition_body_state(
                    self.tenant, self.message_id, "fetching", now=123
                )
            await connection.rollback()

    async def test_body_eviction_removes_search_and_references_not_message_metadata(self):
        await self.record_structure()
        await self.service.request_body(self.tenant, self.message_id, now=130)
        await self.service.fetch_body(self.tenant, self.message_id, now=131)
        result = await QuotaService(self.api_pool, self.store).evict_body_cache(
            self.tenant.user_uid,
            1,
        )
        self.assertGreater(result.logical_bytes_released, 0)
        self.assertEqual(
            int(await self.scalar("SELECT COUNT(*) FROM messages WHERE id = %s", (self.message_id,)) or 0),
            1,
        )
        self.assertEqual(
            await self.scalar("SELECT state FROM message_bodies WHERE message_id = %s", (self.message_id,)),
            "evicted",
        )
        self.assertEqual(
            await self.row(
                "SELECT body_state, search_state FROM messages WHERE id = %s",
                (self.message_id,),
            ),
            ("evicted", "evicted"),
        )
        self.assertEqual(
            int(await self.scalar(
                "SELECT COUNT(*) FROM body_search_documents WHERE message_id = %s",
                (self.message_id,),
            ) or 0),
            0,
        )
        self.assertEqual(
            int(await self.scalar(
                """
                SELECT COUNT(*) FROM content_references
                WHERE user_uid = %s AND reference_kind IN ('message_body_html','message_body_text')
                """,
                (self.tenant.user_uid,),
            ) or 0),
            0,
        )

    async def rows(self, sql: str, params: tuple = ()) -> list[tuple]:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def row(self, sql: str, params: tuple = ()) -> tuple:
        rows = await self.rows(sql, params)
        if not rows:
            raise AssertionError("expected one row")
        return rows[0]


class ContentFetchStaticContracts(unittest.TestCase):
    def test_non_raw_paths_do_not_construct_full_message_fetch(self):
        source = Path("flymail/workers/content_fetch.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('"BODY.PEEK[]"'), 1)
        self.assertNotIn("email.walk(", source)


if __name__ == "__main__":
    unittest.main()
