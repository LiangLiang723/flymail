from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from flymail.domain.enums import ObjectKind
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.repositories.objects import ObjectRepository
from flymail.workers.cache_cleanup import CacheCleanupHandler
from flymail.workers.dispatcher import JobContext
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


async def chunks(value: bytes):
    yield value


class CacheCleanupTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-cache-cleanup-")
        root = Path(self.temp_dir.name)
        self.store = ObjectStore(root / "objects" / "sha256", root / "objects" / ".tmp")
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in ("message_attachments", "content_references", "content_objects"):
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _attach(self, stored, *, user_uid: str, kind: str, reference_id: str) -> None:
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            async with repository.lock_object(stored.content_sha256):
                await connection.begin()
                await repository.attach_reference(
                    stored,
                    user_uid=user_uid,
                    reference_kind=kind,
                    reference_id=reference_id,
                    last_accessed_at=1,
                )
                await connection.commit()

    async def test_attachment_cleanup_preserves_metadata_inline_and_shared_object(self):
        shared = await self.store.put_stream(ObjectKind.ATTACHMENT, chunks(b"shared-bytes"))
        inline = await self.store.put_stream(ObjectKind.INLINE_IMAGE, chunks(b"inline-bytes"))
        await self._attach(shared, user_uid="usr_cleanup", kind="message_attachment", reference_id="att_user")
        await self._attach(shared, user_uid="usr_other", kind="message_attachment", reference_id="att_other")
        await self._attach(inline, user_uid="usr_cleanup", kind="message_inline_image", reference_id="att_inline")
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO message_attachments (
                        id, user_uid, message_id, remote_instance_id, imap_part,
                        filename, remote_size_bytes, content_sha256, is_inline,
                        cache_state, last_accessed_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'ready', %s, 1, 1)
                    """,
                    (
                        ("att_user", "usr_cleanup", "msg_user", "remote_user", "1", "a.bin", 12, shared.content_sha256, 0, 1),
                        ("att_other", "usr_other", "msg_other", "remote_other", "1", "a.bin", 12, shared.content_sha256, 0, 2),
                        ("att_inline", "usr_cleanup", "msg_inline", "remote_inline", "1", "i.png", 12, inline.content_sha256, 1, 3),
                    ),
                )
            await connection.commit()

        outcome = await CacheCleanupHandler(self.pool, self.store)(
            JobContext(
                job_id="job_cleanup",
                user_uid="usr_cleanup",
                account_id=None,
                provider_key=None,
                queue_name="maintenance",
                worker_id="worker_cleanup",
                attempt_count=1,
                stop_event=asyncio.Event(),
            ),
            {
                "user_uid": "usr_cleanup",
                "body_cache_quota_bytes": 0,
                "attachment_cache_quota_bytes": 1,
            },
        )
        self.assertEqual(outcome.action, "complete")
        self.assertIsNone(
            await self.scalar("SELECT content_sha256 FROM message_attachments WHERE id='att_user'")
        )
        self.assertEqual(
            await self.scalar("SELECT cache_state FROM message_attachments WHERE id='att_user'"),
            "evicted",
        )
        self.assertEqual(
            await self.scalar("SELECT content_sha256 FROM message_attachments WHERE id='att_other'"),
            shared.content_sha256,
        )
        self.assertEqual(
            await self.scalar("SELECT content_sha256 FROM message_attachments WHERE id='att_inline'"),
            inline.content_sha256,
        )
        self.assertTrue(shared.path.is_file())
        self.assertTrue(inline.path.is_file())

    async def test_cleanup_rejects_payload_for_another_user(self):
        outcome = await CacheCleanupHandler(self.pool, self.store)(
            JobContext(
                job_id="job_wrong_scope",
                user_uid="usr_cleanup",
                account_id=None,
                provider_key=None,
                queue_name="maintenance",
                worker_id="worker_cleanup",
                attempt_count=1,
                stop_event=asyncio.Event(),
            ),
            {
                "user_uid": "usr_other",
                "body_cache_quota_bytes": 0,
                "attachment_cache_quota_bytes": 0,
            },
        )
        self.assertEqual(outcome.action, "fail")
        self.assertEqual(outcome.error_class, "InvalidCleanupScope")


if __name__ == "__main__":
    import unittest

    unittest.main()
