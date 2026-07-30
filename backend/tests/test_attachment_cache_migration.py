import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services import attachment_cache
from services import attachment_cache_maintenance as maintenance


class AttachmentCacheMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_normal_legacy_attachment_is_uncached_but_metadata_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloads = Path(tmp) / "download"
            legacy = downloads / "user@example.com" / "2026" / "07" / "10" / "1_report.pdf"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"report")
            row = {
                "account_id": "account-1", "user_uid": "user-1", "uid": 10,
                "folder": "INBOX", "part_number": 1, "filename": "report.pdf",
                "content_type": "application/pdf", "size": 6, "content_id": "",
                "is_inline": False, "local_path": str(legacy), "content_sha256": "",
                "last_accessed_at": 0, "cached_at": 1,
            }
            clear = AsyncMock(return_value="")
            with (
                patch.object(maintenance, "DOWNLOADS_DIR", downloads),
                patch.object(maintenance, "list_all_cached_attachment_rows", new=AsyncMock(return_value=[row])),
                patch.object(maintenance, "clear_cached_attachment_storage", clear),
                patch.object(maintenance, "list_cached_attachment_local_paths", new=AsyncMock(return_value=set())),
            ):
                stats = await maintenance.migrate_legacy_attachment_cache()

            clear.assert_awaited_once_with("account-1", 10, "INBOX", 1)
            self.assertFalse(legacy.exists())
            self.assertEqual(stats["normal_files_removed"], 1)
            self.assertEqual(stats["failures"], 0)

    async def test_duplicate_inline_images_share_one_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloads = Path(tmp) / "download"
            first = downloads / "a" / "1_logo.png"
            second = downloads / "b" / "1_logo.png"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"same-image")
            second.write_bytes(b"same-image")
            rows = [
                {
                    "account_id": "account-1", "user_uid": "user-1", "uid": 10,
                    "folder": "INBOX", "part_number": 1, "filename": "logo.png",
                    "content_type": "image/png", "size": 10, "content_id": "cid-1",
                    "is_inline": True, "local_path": str(first), "content_sha256": "",
                    "last_accessed_at": 0, "cached_at": 1,
                },
                {
                    "account_id": "account-2", "user_uid": "user-2", "uid": 20,
                    "folder": "INBOX", "part_number": 1, "filename": "logo.png",
                    "content_type": "image/png", "size": 10, "content_id": "cid-2",
                    "is_inline": True, "local_path": str(second), "content_sha256": "",
                    "last_accessed_at": 0, "cached_at": 1,
                },
            ]
            stored = attachment_cache.StoredAttachmentObject(
                content_sha256="d" * 64,
                size=10,
                local_path=str(Path(tmp) / "objects" / ("d" * 64)),
                created=True,
            )

            async def fake_cache_file(_attachment, source_path, *, remove_source=False, enforce_quota=False):
                self.assertFalse(enforce_quota)
                if remove_source:
                    source_path.unlink(missing_ok=True)
                return stored

            cache_file = AsyncMock(side_effect=fake_cache_file)
            with (
                patch.object(maintenance, "DOWNLOADS_DIR", downloads),
                patch.object(maintenance, "list_all_cached_attachment_rows", new=AsyncMock(return_value=rows)),
                patch.object(maintenance, "cache_attachment_file", cache_file),
                patch.object(maintenance, "list_cached_attachment_local_paths", new=AsyncMock(return_value=set())),
            ):
                stats = await maintenance.migrate_legacy_attachment_cache()

            self.assertEqual(cache_file.await_count, 2)
            self.assertEqual(stats["inline_rows_migrated"], 2)
            self.assertEqual([call.args[1] for call in cache_file.await_args_list], [first, second])
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())

    async def test_migration_is_idempotent_for_shared_object_reference(self):
        row = {
            "account_id": "account-1", "user_uid": "user-1", "uid": 10,
            "folder": "INBOX", "part_number": 1, "filename": "logo.png",
            "content_type": "image/png", "size": 10, "content_id": "cid-1",
            "is_inline": True, "local_path": "/objects/aa/hash",
            "content_sha256": "a" * 64, "last_accessed_at": 2, "cached_at": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            downloads = Path(tmp) / "download"
            downloads.mkdir()
            with (
                patch.object(maintenance, "DOWNLOADS_DIR", downloads),
                patch.object(maintenance, "list_all_cached_attachment_rows", new=AsyncMock(return_value=[row])),
                patch.object(maintenance, "resolve_cached_attachment_path", new=AsyncMock(return_value=Path(row["local_path"]))),
                patch.object(maintenance, "cache_attachment_file", new=AsyncMock()) as cache_file,
                patch.object(maintenance, "list_cached_attachment_local_paths", new=AsyncMock(return_value={row["local_path"]})),
            ):
                stats = await maintenance.migrate_legacy_attachment_cache()

        cache_file.assert_not_awaited()
        self.assertEqual(stats["skipped_rows"], 1)

    async def test_migration_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloads = Path(tmp) / "download"
            outside = Path(tmp) / "outside.bin"
            outside.write_bytes(b"secret")
            downloads.mkdir()
            link = downloads / "escape.bin"
            link.symlink_to(outside)
            with patch.object(maintenance, "DOWNLOADS_DIR", downloads):
                self.assertFalse(maintenance.is_safe_legacy_download_file(link))


class AttachmentCacheGarbageCollectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_removes_only_stale_untracked_object_and_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            sha_root = Path(tmp) / "sha256"
            temp_root = Path(tmp) / ".tmp"
            known = "a" * 64
            orphan = "b" * 64
            known_path = sha_root / known[:2] / known
            orphan_path = sha_root / orphan[:2] / orphan
            fresh_path = sha_root / ("c" * 2) / ("c" * 64)
            stale_temp = temp_root / "old.download"
            for path, data in (
                (known_path, b"known"),
                (orphan_path, b"orphan"),
                (fresh_path, b"fresh"),
                (stale_temp, b"temp"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            old = time.time() - 7200
            os.utime(orphan_path, (old, old))
            os.utime(stale_temp, (old, old))

            with (
                patch.object(attachment_cache, "ATTACHMENT_SHA256_DIR", sha_root),
                patch.object(attachment_cache, "ATTACHMENT_CACHE_TMP_DIR", temp_root),
            ):
                stats = await attachment_cache.remove_stale_untracked_cache_files(
                    {known},
                    time.time() - 3600,
                )

            self.assertTrue(known_path.exists())
            self.assertFalse(orphan_path.exists())
            self.assertTrue(fresh_path.exists())
            self.assertFalse(stale_temp.exists())
            self.assertEqual(stats["orphan_object_files"], 1)
            self.assertEqual(stats["stale_temp_files"], 1)

    async def test_gc_releases_unreferenced_rows_then_scans_current_known_hashes(self):
        initial = [
            {"content_sha256": "a" * 64, "size": 4, "local_path": "/objects/a", "created_at": 1},
            {"content_sha256": "b" * 64, "size": 5, "local_path": "/objects/b", "created_at": 1},
        ]
        remaining = [initial[1]]
        release = AsyncMock(return_value=attachment_cache.AttachmentCacheCleanup())
        remove = AsyncMock(return_value={
            "orphan_object_files": 0,
            "orphan_object_bytes": 0,
            "stale_temp_files": 0,
        })
        with (
            patch.object(maintenance, "list_all_attachment_cache_objects", new=AsyncMock(side_effect=[initial, remaining])),
            patch.object(maintenance, "release_unreferenced_objects", release),
            patch.object(maintenance, "remove_stale_untracked_cache_files", remove),
        ):
            await maintenance.garbage_collect_attachment_cache(orphan_grace_seconds=3600)

        self.assertEqual(release.await_count, 2)
        known_hashes = remove.await_args.args[0]
        self.assertEqual(known_hashes, {"b" * 64})


class AttachmentCacheMaintenanceLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_singleton_and_stop_cancels_task(self):
        maintenance._maintenance_task = None
        started = asyncio.Event()

        async def loop():
            started.set()
            await asyncio.Event().wait()

        with patch.object(maintenance, "_maintenance_loop", loop):
            maintenance.start_attachment_cache_maintenance()
            first = maintenance._maintenance_task
            maintenance.start_attachment_cache_maintenance()
            self.assertIs(first, maintenance._maintenance_task)
            await started.wait()
            await maintenance.stop_attachment_cache_maintenance()

        self.assertIsNone(maintenance._maintenance_task)
        self.assertTrue(first.cancelled())


if __name__ == "__main__":
    unittest.main()
