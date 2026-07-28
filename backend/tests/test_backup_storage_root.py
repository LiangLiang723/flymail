import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class BackupStorageRootTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_backup_root_stays_under_flymail_data_dir(self):
        from services import backup

        with tempfile.TemporaryDirectory() as tmp:
            default_root = Path(tmp) / "flymail" / "backup"
            with patch.object(backup, "DEFAULT_BACKUP_ROOT", default_root):
                self.assertEqual(backup.get_backup_root(), default_root)

    async def test_user_target_outside_storage_root_falls_back_to_default(self):
        from services import backup

        with tempfile.TemporaryDirectory() as tmp:
            storage_root = Path(tmp) / "data"
            default_root = storage_root / "flymail" / "backup"
            outside = Path(tmp) / "outside"
            outside.mkdir()
            with (
                patch.object(backup, "STORAGE_ROOT", storage_root),
                patch.object(backup, "DEFAULT_BACKUP_ROOT", default_root),
                patch.object(backup, "get_user_setting", new=AsyncMock(return_value=str(outside))),
                patch.object(backup, "is_path_authorized", return_value=True),
            ):
                root = await backup.get_backup_root_async("user-1")
            self.assertEqual(root, default_root)

    async def test_target_policy_allows_default_tree_and_rejects_external_authorized_path(self):
        from services import backup

        with tempfile.TemporaryDirectory() as tmp:
            storage_root = Path(tmp) / "data"
            default_root = storage_root / "flymail" / "backup"
            outside = Path(tmp) / "outside"
            outside.mkdir()
            with (
                patch.object(backup, "STORAGE_ROOT", storage_root),
                patch.object(backup, "DEFAULT_BACKUP_ROOT", default_root),
                patch.object(backup, "is_path_authorized", return_value=True),
            ):
                self.assertTrue(backup.is_backup_target_allowed(default_root / "nested"))
                self.assertFalse(backup.is_backup_target_allowed(outside))

    async def test_available_directories_exclude_authorized_paths_outside_storage_root(self):
        from services import backup

        with tempfile.TemporaryDirectory() as tmp:
            storage_root = Path(tmp) / "data"
            default_root = storage_root / "flymail" / "backup"
            allowed = storage_root / "nas" / "archive"
            outside = Path(tmp) / "outside"
            allowed.mkdir(parents=True)
            outside.mkdir()
            with (
                patch.object(backup, "STORAGE_ROOT", storage_root),
                patch.object(backup, "DEFAULT_BACKUP_ROOT", default_root),
                patch.object(backup, "get_accessible_paths", return_value=[str(allowed), str(outside)]),
            ):
                items = await backup.get_available_backup_dirs("user-1")

            paths = [item["path"] for item in items]
            self.assertEqual(paths[0], str(default_root))
            self.assertIn(str(allowed), paths)
            self.assertNotIn(str(outside), paths)


if __name__ == "__main__":
    unittest.main()
