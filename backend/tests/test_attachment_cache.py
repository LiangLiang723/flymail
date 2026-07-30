import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import db
from data_paths import build_attachment_object_path, ensure_data_dirs
from models import CachedAttachment


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


if __name__ == "__main__":
    unittest.main()
