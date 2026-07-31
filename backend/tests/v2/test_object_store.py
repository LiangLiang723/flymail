from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from flymail.domain.enums import ObjectKind
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.models import ObjectVerificationStatus
from flymail.infrastructure.object_store.quota import QuotaService
from flymail.infrastructure.object_store.store import ObjectStore, object_path
from flymail.repositories.objects import ObjectRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


async def chunks(*values: bytes):
    for value in values:
        yield value


class ObjectStoreFilesystemTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-objects-")
        base = Path(self.temp_dir.name)
        self.object_root = base / "objects" / "sha256"
        self.temp_root = base / "objects" / ".tmp"
        self.store = ObjectStore(self.object_root, self.temp_root)

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_same_content_produces_one_physical_file_and_same_digest(self):
        first = await self.store.put_stream(ObjectKind.BODY_TEXT, chunks(b"hello ", b"world"))
        second = await self.store.put_stream(ObjectKind.ATTACHMENT, chunks(b"hello world"))

        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(first.path, second.path)
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.path.read_bytes(), b"hello world")
        files = [path for path in self.object_root.rglob("*") if path.is_file()]
        self.assertEqual(files, [first.path])

    async def test_concurrent_same_content_writes_publish_one_verified_object(self):
        results = await asyncio.gather(
            *[
                self.store.put_stream(ObjectKind.BODY_TEXT, chunks(b"concurrent-content"))
                for _ in range(8)
            ]
        )

        self.assertEqual({item.content_sha256 for item in results}, {results[0].content_sha256})
        self.assertEqual(sum(1 for item in results if item.created), 1)
        self.assertEqual(results[0].path.read_bytes(), b"concurrent-content")
        self.assertEqual(
            [path for path in self.object_root.rglob("*") if path.is_file()],
            [results[0].path],
        )

    async def test_invalid_digest_path_is_rejected(self):
        for digest in ("", "abc", "g" * 64, "../" + "a" * 64, "a" * 65):
            with self.subTest(digest=digest[:12]):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    object_path(self.object_root, digest)

        normalized = object_path(self.object_root, "A" * 64)
        self.assertEqual(normalized.name, "a" * 64)
        self.assertEqual(normalized.parent.name, "aa")

    async def test_interrupted_write_leaves_no_final_or_temporary_file(self):
        async def interrupted():
            yield b"partial"
            raise RuntimeError("stream interrupted")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            await self.store.put_stream(ObjectKind.BODY_TEXT, interrupted())

        self.assertEqual(list(self.temp_root.glob("*")), [])
        self.assertEqual([path for path in self.object_root.rglob("*") if path.is_file()], [])

    async def test_expected_size_mismatch_removes_temporary_file(self):
        with self.assertRaisesRegex(ValueError, "expected size"):
            await self.store.put_stream(ObjectKind.BODY_HTML, chunks(b"short"), expected_size=99)

        self.assertEqual(list(self.temp_root.glob("*")), [])
        self.assertEqual([path for path in self.object_root.rglob("*") if path.is_file()], [])

    async def test_missing_and_corrupt_objects_have_explicit_verification_states(self):
        missing = await self.store.verify("f" * 64)
        self.assertEqual(missing.status, ObjectVerificationStatus.MISSING)

        stored = await self.store.put_stream(ObjectKind.RAW_EML, chunks(b"valid-content"))
        stored.path.write_bytes(b"corrupted")
        corrupt = await self.store.verify(stored.content_sha256)
        self.assertEqual(corrupt.status, ObjectVerificationStatus.CORRUPT)
        self.assertNotEqual(corrupt.actual_sha256, stored.content_sha256)

    async def test_open_reads_verified_object_and_rejects_symlink(self):
        stored = await self.store.put_stream(ObjectKind.BODY_TEXT, chunks(b"read-me"))
        async with self.store.open(stored.content_sha256) as handle:
            self.assertEqual(handle.read(), b"read-me")

        stored.path.unlink()
        stored.path.symlink_to(Path(self.temp_dir.name) / "outside")
        with self.assertRaisesRegex(ValueError, "unsafe object path"):
            async with self.store.open(stored.content_sha256):
                pass


class ObjectRepositoryAndQuotaTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.pool)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-object-db-")
        base = Path(self.temp_dir.name)
        self.store = ObjectStore(base / "objects" / "sha256", base / "objects" / ".tmp")
        self.quota = QuotaService(self.pool, self.store)
        await self._clear_content_tables()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_content_tables(self) -> None:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in (
                    "body_search_documents",
                    "message_attachments",
                    "message_bodies",
                    "content_references",
                    "content_objects",
                ):
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _attach_reference(
        self,
        stored,
        *,
        user_uid: str,
        reference_kind: str,
        reference_id: str,
        pinned: bool = False,
        last_accessed_at: float = 0,
    ) -> str:
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            async with repository.lock_object(stored.content_sha256):
                await connection.begin()
                try:
                    reference_uid = await repository.attach_reference(
                        stored,
                        user_uid=user_uid,
                        reference_kind=reference_kind,
                        reference_id=reference_id,
                        pinned=pinned,
                        last_accessed_at=last_accessed_at,
                    )
                    await connection.commit()
                    return reference_uid
                except Exception:
                    await connection.rollback()
                    raise

    async def _detach_reference(
        self,
        *,
        user_uid: str,
        reference_kind: str,
        reference_id: str,
    ) -> str | None:
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT content_sha256
                        FROM content_references
                        WHERE user_uid = %s AND reference_kind = %s AND reference_id = %s
                        LIMIT 1
                        """,
                        (user_uid, reference_kind, reference_id),
                    )
                    row = await cursor.fetchone()
                if not row:
                    await connection.rollback()
                    return None
                digest = str(row[0])
                await connection.rollback()
                async with repository.lock_object(digest):
                    await connection.begin()
                    detached = await repository.detach_reference(
                        user_uid=user_uid,
                        reference_kind=reference_kind,
                        reference_id=reference_id,
                    )
                    await connection.commit()
                    return detached
            except Exception:
                await connection.rollback()
                raise

    async def _count_references(self, digest: str) -> int:
        async with self.pool.acquire() as connection:
            return await ObjectRepository(connection).count_references(digest)

    async def _remove_unreferenced(self, digest: str) -> bool:
        async with self.pool.acquire() as connection:
            return await self.store.remove_unreferenced(
                digest,
                ObjectRepository(connection),
            )

    async def _put_and_attach(
        self,
        *,
        data: bytes,
        object_kind: ObjectKind,
        user_uid: str,
        reference_kind: str,
        reference_id: str,
        pinned: bool = False,
        last_accessed_at: float = 0,
    ):
        stored = await self.store.put_stream(object_kind, chunks(data))
        await self._attach_reference(
            stored,
            user_uid=user_uid,
            reference_kind=reference_kind,
            reference_id=reference_id,
            pinned=pinned,
            last_accessed_at=last_accessed_at,
        )
        return stored

    async def _insert_message_body(
        self,
        *,
        user_uid: str,
        message_id: str,
        digest: str,
        last_accessed_at: float,
    ) -> None:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO message_bodies (
                        message_id, user_uid, text_object_sha256, state, body_size_bytes,
                        cached_at, last_accessed_at, updated_at
                    ) VALUES (%s, %s, %s, 'ready', 1, %s, %s, %s)
                    """,
                    (message_id, user_uid, digest, last_accessed_at, last_accessed_at, last_accessed_at),
                )
                await cursor.execute(
                    """
                    INSERT INTO body_search_documents (
                        message_id, user_uid, body_text, updated_at
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (message_id, user_uid, f"search-{message_id}", last_accessed_at),
                )
            await connection.commit()

    async def test_last_reference_removes_object_but_any_global_reference_preserves_it(self):
        stored = await self._put_and_attach(
            data=b"shared-body",
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_one",
            reference_kind="message_body_text",
            reference_id="msg_one",
        )
        await self._attach_reference(
            stored,
            user_uid="usr_two",
            reference_kind="message_body_text",
            reference_id="msg_two",
        )

        await self._detach_reference(
            user_uid="usr_one",
            reference_kind="message_body_text",
            reference_id="msg_one",
        )
        self.assertFalse(await self._remove_unreferenced(stored.content_sha256))
        self.assertTrue(stored.path.is_file())

        await self._detach_reference(
            user_uid="usr_two",
            reference_kind="message_body_text",
            reference_id="msg_two",
        )
        self.assertTrue(await self._remove_unreferenced(stored.content_sha256))
        self.assertFalse(stored.path.exists())
        self.assertEqual(await self._count_references(stored.content_sha256), 0)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_objects WHERE content_sha256 = %s",
                (stored.content_sha256,),
            ),
            0,
        )

    async def test_attach_requires_existing_final_file(self):
        stored = await self.store.put_stream(ObjectKind.BODY_TEXT, chunks(b"must-exist"))
        forged = replace(stored, path=stored.path.with_name("missing-object"))

        with self.assertRaises(FileNotFoundError):
            await self._attach_reference(
                forged,
                user_uid="usr_missing",
                reference_kind="message_body_text",
                reference_id="msg_missing",
            )

        self.assertEqual(await self._count_references(stored.content_sha256), 0)

    async def test_file_delete_failure_restores_metadata_without_restoring_reference(self):
        stored = await self._put_and_attach(
            data=b"delete-failure",
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_delete_failure",
            reference_kind="message_body_text",
            reference_id="msg_delete_failure",
        )
        await self._detach_reference(
            user_uid="usr_delete_failure",
            reference_kind="message_body_text",
            reference_id="msg_delete_failure",
        )
        original_unlink = Path.unlink

        def fail_target_unlink(path: Path, *args, **kwargs):
            if path == stored.path:
                raise OSError("simulated delete failure")
            return original_unlink(path, *args, **kwargs)

        with self.assertLogs("flymail.v2.object_store", level="WARNING") as captured:
            with patch.object(Path, "unlink", new=fail_target_unlink):
                removed = await self._remove_unreferenced(stored.content_sha256)

        self.assertIn("object cleanup failed", "\n".join(captured.output))

        self.assertFalse(removed)
        self.assertTrue(stored.path.is_file())
        self.assertEqual(await self._count_references(stored.content_sha256), 0)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_objects WHERE content_sha256 = %s",
                (stored.content_sha256,),
            ),
            1,
        )

    async def test_repository_does_not_commit_reference_changes(self):
        stored = await self.store.put_stream(ObjectKind.BODY_TEXT, chunks(b"rollback-reference"))
        async with self.pool.acquire() as connection:
            repository = ObjectRepository(connection)
            async with repository.lock_object(stored.content_sha256):
                await connection.begin()
                await repository.attach_reference(
                    stored,
                    user_uid="usr_rollback",
                    reference_kind="message_body_text",
                    reference_id="msg_rollback",
                )
                await connection.rollback()

        self.assertEqual(await self._count_references(stored.content_sha256), 0)
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_objects WHERE content_sha256 = %s",
                (stored.content_sha256,),
            ),
            0,
        )

    async def test_user_usage_counts_each_digest_once_and_uses_reference_semantics(self):
        shared = await self._put_and_attach(
            data=b"1234567890",
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_usage",
            reference_kind="message_body_text",
            reference_id="msg_usage_one",
        )
        await self._attach_reference(
            shared,
            user_uid="usr_usage",
            reference_kind="message_body_text",
            reference_id="msg_usage_two",
        )
        await self._put_and_attach(
            data=b"attachment-bytes",
            object_kind=ObjectKind.ATTACHMENT,
            user_uid="usr_usage",
            reference_kind="message_attachment",
            reference_id="att_usage",
        )
        await self._put_and_attach(
            data=b"draft-local-only",
            object_kind=ObjectKind.DRAFT_ATTACHMENT,
            user_uid="usr_usage",
            reference_kind="draft_attachment",
            reference_id="draft_usage",
        )

        body_usage = await self.quota.get_user_usage("usr_usage", {ObjectKind.BODY_TEXT})
        attachment_usage = await self.quota.get_user_usage("usr_usage", {ObjectKind.ATTACHMENT})
        draft_usage = await self.quota.get_user_usage("usr_usage", {ObjectKind.DRAFT_ATTACHMENT})

        self.assertEqual(body_usage, len(b"1234567890"))
        self.assertEqual(attachment_usage, len(b"attachment-bytes"))
        self.assertEqual(draft_usage, len(b"draft-local-only"))

    async def test_zero_body_quota_is_unlimited(self):
        stored = await self._put_and_attach(
            data=b"never-evict-for-zero",
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_unlimited",
            reference_kind="message_body_text",
            reference_id="msg_unlimited",
            last_accessed_at=1,
        )
        await self._insert_message_body(
            user_uid="usr_unlimited",
            message_id="msg_unlimited",
            digest=stored.content_sha256,
            last_accessed_at=1,
        )

        result = await self.quota.evict_body_cache("usr_unlimited", 0)

        self.assertEqual(result.object_count, 0)
        self.assertEqual(result.logical_bytes_released, 0)
        self.assertEqual(result.before_bytes, result.after_bytes)
        self.assertTrue(stored.path.is_file())

    async def test_body_eviction_is_lru_unique_and_removes_search_documents(self):
        old = await self._put_and_attach(
            data=b"o" * 20,
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_quota",
            reference_kind="message_body_text",
            reference_id="msg_old",
            last_accessed_at=10,
        )
        await self._attach_reference(
            old,
            user_uid="usr_quota",
            reference_kind="message_body_text",
            reference_id="msg_old_duplicate",
            last_accessed_at=10,
        )
        new = await self._put_and_attach(
            data=b"n" * 15,
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_quota",
            reference_kind="message_body_text",
            reference_id="msg_new",
            last_accessed_at=20,
        )
        pinned = await self._put_and_attach(
            data=b"p" * 50,
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_quota",
            reference_kind="message_body_text",
            reference_id="msg_pinned",
            pinned=True,
            last_accessed_at=1,
        )
        for message_id, stored, accessed in (
            ("msg_old", old, 10),
            ("msg_old_duplicate", old, 10),
            ("msg_new", new, 20),
            ("msg_pinned", pinned, 1),
        ):
            await self._insert_message_body(
                user_uid="usr_quota",
                message_id=message_id,
                digest=stored.content_sha256,
                last_accessed_at=accessed,
            )

        result = await self.quota.evict_body_cache("usr_quota", 50)

        self.assertEqual(result.before_bytes, 85)
        self.assertEqual(result.after_bytes, 50)
        self.assertEqual(result.logical_bytes_released, 35)
        self.assertEqual(result.object_count, 2)
        self.assertEqual(result.message_count, 3)
        self.assertFalse(old.path.exists())
        self.assertFalse(new.path.exists())
        self.assertTrue(pinned.path.is_file())
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM body_search_documents WHERE user_uid = %s",
                ("usr_quota",),
            ),
            1,
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM message_bodies WHERE user_uid = %s AND state = 'evicted'",
                ("usr_quota",),
            ),
            3,
        )

    async def test_active_read_or_worker_lease_lock_skips_candidate_without_blocking(self):
        leased = await self._put_and_attach(
            data=b"l" * 20,
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_leased",
            reference_kind="message_body_text",
            reference_id="msg_leased",
            last_accessed_at=1,
        )
        fallback = await self._put_and_attach(
            data=b"f" * 15,
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_leased",
            reference_kind="message_body_text",
            reference_id="msg_fallback",
            last_accessed_at=2,
        )
        await self._insert_message_body(
            user_uid="usr_leased",
            message_id="msg_leased",
            digest=leased.content_sha256,
            last_accessed_at=1,
        )
        await self._insert_message_body(
            user_uid="usr_leased",
            message_id="msg_fallback",
            digest=fallback.content_sha256,
            last_accessed_at=2,
        )

        async with self.pool.acquire() as lease_connection:
            lease_repository = ObjectRepository(lease_connection)
            async with lease_repository.lock_object(leased.content_sha256):
                result = await self.quota.evict_body_cache("usr_leased", 20)

        self.assertEqual(result.before_bytes, 35)
        self.assertEqual(result.after_bytes, 20)
        self.assertEqual(result.object_count, 1)
        self.assertTrue(leased.path.is_file())
        self.assertFalse(fallback.path.exists())
        self.assertEqual(await self._count_references(leased.content_sha256), 1)
        self.assertEqual(await self._count_references(fallback.content_sha256), 0)

    async def test_protected_business_references_are_never_eviction_candidates(self):
        protected_specs = (
            (ObjectKind.BODY_TEXT, "draft_body_text", "draft_body"),
            (ObjectKind.DRAFT_ATTACHMENT, "draft_attachment", "pending_send_attachment"),
            (ObjectKind.USER_AVATAR, "user_avatar", "avatar"),
            (ObjectKind.ACCOUNT_ICON, "account_icon", "account_icon"),
            (ObjectKind.CONTACT_AVATAR, "contact_avatar", "contact_avatar"),
            (ObjectKind.NOTIFICATION_ASSET, "notification_asset", "notification_asset"),
        )
        stored_objects = []
        for index, (kind, reference_kind, reference_id) in enumerate(protected_specs):
            stored_objects.append(
                await self._put_and_attach(
                    data=(f"protected-{index}".encode()),
                    object_kind=kind,
                    user_uid="usr_protected",
                    reference_kind=reference_kind,
                    reference_id=reference_id,
                    last_accessed_at=1,
                )
            )

        result = await self.quota.evict_body_cache("usr_protected", 1)

        self.assertEqual(result.before_bytes, 0)
        self.assertEqual(result.object_count, 0)
        self.assertTrue(all(item.path.is_file() for item in stored_objects))
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM content_references WHERE user_uid = %s",
                ("usr_protected",),
            ),
            len(protected_specs),
        )

    async def test_eviction_releases_logical_bytes_but_preserves_shared_physical_object(self):
        shared = await self._put_and_attach(
            data=b"shared-across-users",
            object_kind=ObjectKind.BODY_TEXT,
            user_uid="usr_evict",
            reference_kind="message_body_text",
            reference_id="msg_evict",
            last_accessed_at=1,
        )
        await self._attach_reference(
            shared,
            user_uid="usr_other",
            reference_kind="message_body_text",
            reference_id="msg_other",
            last_accessed_at=1,
        )
        await self._insert_message_body(
            user_uid="usr_evict",
            message_id="msg_evict",
            digest=shared.content_sha256,
            last_accessed_at=1,
        )

        result = await self.quota.evict_body_cache("usr_evict", 1)

        self.assertEqual(result.logical_bytes_released, len(b"shared-across-users"))
        self.assertEqual(result.physical_bytes_released, 0)
        self.assertTrue(shared.path.is_file())
        self.assertEqual(await self._count_references(shared.content_sha256), 1)


if __name__ == "__main__":
    unittest.main()
