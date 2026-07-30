import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import db
from data_paths import build_attachment_object_path, ensure_data_dirs
from models import CachedAttachment
from services import attachment_cache


class AttachmentCachePathTest(unittest.TestCase):
    def test_sha256_path_uses_two_character_bucket(self):
        digest = "ab" + "1" * 62
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "objects" / "sha256"
            with patch("data_paths.ATTACHMENT_SHA256_DIR", root):
                self.assertEqual(build_attachment_object_path(digest), root / "ab" / digest)

    def test_invalid_sha256_is_rejected(self):
        with self.assertRaises(ValueError):
            build_attachment_object_path("../escape")

    def test_ensure_data_dirs_creates_object_and_tmp_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with (
                patch("data_paths.BASE_DATA_DIR", base),
                patch("data_paths.FILES_DIR", base / "files"),
                patch("data_paths.CONFIG_DIR", base / "config"),
                patch("data_paths.LOGS_DIR", base / "logs"),
                patch("data_paths.UPLOADS_DIR", base / "files" / "uploads"),
                patch("data_paths.DOWNLOADS_DIR", base / "files" / "download"),
                patch("data_paths.BACKUP_DIR", base / "backup"),
                patch("data_paths.ATTACHMENT_OBJECTS_DIR", base / "files" / "objects"),
                patch("data_paths.ATTACHMENT_SHA256_DIR", base / "files" / "objects" / "sha256"),
                patch("data_paths.ATTACHMENT_CACHE_TMP_DIR", base / "files" / "objects" / ".tmp"),
            ):
                ensure_data_dirs()
                self.assertTrue((base / "files" / "objects" / "sha256").is_dir())
                self.assertTrue((base / "files" / "objects" / ".tmp").is_dir())

    def test_cached_attachment_accepts_object_reference_fields(self):
        item = CachedAttachment(
            account_id="account-1",
            user_uid="user-1",
            uid=10,
            folder="INBOX",
            part_number=1,
            content_sha256="a" * 64,
            last_accessed_at=123.0,
        )
        self.assertEqual(item.content_sha256, "a" * 64)
        self.assertEqual(item.last_accessed_at, 123.0)


class _Cursor:
    def __init__(self, row=None, rows=None, rowcount=0):
        self._row = row
        self._rows = rows or []
        self.rowcount = rowcount
        self.description = []

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class AttachmentCacheRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_replace_cached_attachment_object_returns_replaced_hash(self):
        fake_db = AsyncMock()
        fake_db.execute.side_effect = [
            _Cursor(),
            _Cursor(row=("a" * 64,)),
            _Cursor(rowcount=1),
            _Cursor(),
        ]
        attachment = CachedAttachment(
            account_id="account-1",
            user_uid="user-1",
            uid=10,
            folder="INBOX",
            part_number=1,
            filename="report.pdf",
            size=4,
            local_path="/objects/bb",
            content_sha256="b" * 64,
            last_accessed_at=123.0,
        )

        with patch("db.get_db", new=AsyncMock(return_value=fake_db)):
            old_hash = await db.replace_cached_attachment_object(attachment)

        self.assertEqual(old_hash, "a" * 64)
        update_sql = fake_db.execute.await_args_list[2].args[0]
        self.assertIn("content_sha256", update_sql)
        self.assertIn("last_accessed_at", update_sql)

    async def test_user_usage_query_is_distinct_by_hash(self):
        fake_db = AsyncMock()
        fake_db.execute.return_value = _Cursor(row=(20,))
        with patch("db.get_db", new=AsyncMock(return_value=fake_db)):
            usage = await db.get_user_attachment_cache_usage_bytes("user-1")

        self.assertEqual(usage, 20)
        sql = fake_db.execute.await_args.args[0]
        self.assertIn("SELECT DISTINCT content_sha256", sql)
        self.assertIn("is_inline = 0", sql)


class AttachmentObjectStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_content_creates_one_physical_object(self):
        payload = b"same-content"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            temp_root = Path(tmp) / ".tmp"
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(attachment_cache, "ATTACHMENT_CACHE_TMP_DIR", temp_root),
            ):
                first = await asyncio.to_thread(attachment_cache.store_attachment_bytes, payload)
                second = await asyncio.to_thread(attachment_cache.store_attachment_bytes, payload)
                files = [path for path in root.rglob("*") if path.is_file()]

        self.assertEqual(first.content_sha256, digest)
        self.assertEqual(first.local_path, second.local_path)
        self.assertEqual(len(files), 1)
        self.assertTrue(first.created)
        self.assertFalse(second.created)

    async def test_concurrent_same_content_keeps_one_physical_file(self):
        payload = b"concurrent-content"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            temp_root = Path(tmp) / ".tmp"
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(attachment_cache, "ATTACHMENT_CACHE_TMP_DIR", temp_root),
            ):
                results = await asyncio.gather(*[
                    asyncio.to_thread(attachment_cache.store_attachment_bytes, payload)
                    for _ in range(4)
                ])
                files = [path for path in root.rglob("*") if path.is_file()]

        self.assertEqual({item.content_sha256 for item in results}, {digest})
        self.assertEqual(len(files), 1)
        self.assertEqual(sum(1 for item in results if item.created), 1)

    async def test_cache_bind_releases_replaced_object(self):
        attachment = CachedAttachment(
            account_id="account-1", user_uid="user-1", uid=10,
            folder="INBOX", part_number=1, filename="a.bin",
        )
        stored = attachment_cache.StoredAttachmentObject("b" * 64, 4, "/tmp/object", True)
        with (
            patch.object(attachment_cache, "store_attachment_bytes", return_value=stored),
            patch.object(attachment_cache, "upsert_attachment_cache_object", new=AsyncMock()),
            patch.object(attachment_cache, "replace_cached_attachment_object", new=AsyncMock(return_value="a" * 64)),
            patch.object(attachment_cache, "release_unreferenced_objects", new=AsyncMock()) as release,
            patch.object(attachment_cache, "enforce_user_attachment_cache_limit", new=AsyncMock()),
        ):
            await attachment_cache.cache_attachment_bytes(attachment, b"data")

        release.assert_awaited_once_with({"a" * 64})

    async def test_last_reference_deletes_object_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            path = root / "cc" / ("c" * 64)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"payload")
            record = {"content_sha256": "c" * 64, "size": 7, "local_path": str(path), "created_at": 1.0}
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(attachment_cache, "pop_unreferenced_attachment_cache_object", new=AsyncMock(return_value=record)),
            ):
                result = await attachment_cache.release_unreferenced_objects({"c" * 64})

            self.assertFalse(path.exists())
            self.assertEqual(result.deleted_shared_objects, 1)
            self.assertEqual(result.freed_physical_bytes, 7)

    async def test_release_refuses_object_path_outside_object_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sha256"
            outside = Path(tmp) / "outside.bin"
            outside.write_bytes(b"payload")
            record = {"content_sha256": "e" * 64, "size": 7, "local_path": str(outside), "created_at": 1.0}
            restore = AsyncMock()
            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", root),
                patch.object(attachment_cache, "pop_unreferenced_attachment_cache_object", new=AsyncMock(return_value=record)),
                patch.object(attachment_cache, "restore_attachment_cache_object", restore),
            ):
                result = await attachment_cache.release_unreferenced_objects({"e" * 64})

            self.assertTrue(outside.exists())
            restore.assert_awaited_once_with(record)
            self.assertEqual(result.deleted_shared_objects, 0)


class AttachmentQuotaTest(unittest.IsolatedAsyncioTestCase):
    def test_limit_validation_accepts_zero_and_100_plus(self):
        self.assertEqual(attachment_cache.validate_attachment_cache_limit_mb(0), 0)
        self.assertEqual(attachment_cache.validate_attachment_cache_limit_mb(100), 100)
        self.assertEqual(attachment_cache.validate_attachment_cache_limit_mb(2048), 2048)
        with self.assertRaises(ValueError):
            attachment_cache.validate_attachment_cache_limit_mb(99)
        with self.assertRaises(ValueError):
            attachment_cache.validate_attachment_cache_limit_mb(-1)

    async def test_zero_limit_does_not_evict(self):
        with patch.object(attachment_cache, "get_user_attachment_cache_usage_bytes", new=AsyncMock(return_value=500)):
            result = await attachment_cache.enforce_user_attachment_cache_limit("user-1", 0)
        self.assertEqual(result.before_bytes, 500)
        self.assertEqual(result.after_bytes, 500)

    async def test_lru_clears_oldest_user_hash_first(self):
        mb = 1024 * 1024
        lru = [
            {"content_sha256": "a" * 64, "size": 80 * mb, "last_accessed_at": 10},
            {"content_sha256": "b" * 64, "size": 80 * mb, "last_accessed_at": 20},
        ]
        usage = AsyncMock(side_effect=[160 * mb, 80 * mb])
        clear = AsyncMock(return_value=3)
        release = AsyncMock(return_value=attachment_cache.AttachmentCacheCleanup(
            deleted_shared_objects=1,
            freed_physical_bytes=80 * mb,
        ))
        with (
            patch.object(attachment_cache, "get_user_attachment_cache_usage_bytes", usage),
            patch.object(attachment_cache, "list_user_attachment_cache_lru", new=AsyncMock(return_value=lru)),
            patch.object(attachment_cache, "clear_user_attachment_hash_references", clear),
            patch.object(attachment_cache, "release_unreferenced_objects", release),
        ):
            result = await attachment_cache.enforce_user_attachment_cache_limit("user-1", 100)

        clear.assert_awaited_once_with("user-1", "a" * 64)
        self.assertEqual(result.cleared_references, 3)
        self.assertEqual(result.evicted_user_objects, 1)
        self.assertEqual(result.after_bytes, 80 * mb)

    async def test_oversized_single_attachment_is_not_persisted(self):
        with patch.object(attachment_cache, "get_user_attachment_cache_limit_mb", new=AsyncMock(return_value=100)):
            allowed = await attachment_cache.should_persist_normal_attachment("user-1", 101 * 1024 * 1024)
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
