import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import db
from services.sync import MailSyncService


class _Cursor:
    description = []

    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class CustomFolderVisibilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_account_folder_counts_keep_custom_folders_after_core_folders(self):
        rows = [
            ("oa", "OA", "OA", 267, 4, 267, 20.0),
            ("inbox", "INBOX", "收件箱", 254, 12, 254, 30.0),
            ("rovo", "ROVO", "ROVO", 556, 2, 556, 10.0),
            ("&txqlrpaad+u-", "&TxqLrpAad+U-", "&TxqLrpAad+U-", 8, 8, 8, 5.0),
        ]
        fake_db = SimpleNamespace(execute=AsyncMock(return_value=_Cursor(rows)))

        with patch.object(db, "get_db", AsyncMock(return_value=fake_db)):
            result = await db.list_account_folder_counts("account-1")

        self.assertEqual(
            [item["folder_path"] for item in result[:5]],
            ["INBOX", "Sent Messages", "Drafts", "Junk", "Trash"],
        )
        self.assertEqual(
            [item["folder_path"] for item in result[5:]],
            ["OA", "ROVO", "&TxqLrpAad+U-"],
        )
        meeting_folder = next(item for item in result if item["folder_path"] == "&TxqLrpAad+U-")
        self.assertEqual(meeting_folder["display_name"], "会议通知")

    async def test_custom_noop_listener_uses_all_remote_folders(self):
        receiver = SimpleNamespace(
            fetch_folders=AsyncMock(return_value=[
                SimpleNamespace(name="收件箱", path="INBOX"),
                SimpleNamespace(name="已发送", path="&XfJT0ZAB-"),
                SimpleNamespace(name="ROVO", path="ROVO"),
                SimpleNamespace(name="会议通知", path="&TxqLrpAad+U-"),
            ])
        )
        account = SimpleNamespace(provider="custom")

        folders = await MailSyncService()._get_idle_folders(receiver, account)

        self.assertEqual(folders, ["INBOX", "&XfJT0ZAB-", "ROVO", "&TxqLrpAad+U-"])

    async def test_custom_periodic_sync_uses_all_discovered_folders(self):
        rows = [
            {"folder_path": "INBOX", "updated_at": 30.0},
            {"folder_path": "ROVO", "updated_at": 20.0},
            {"folder_path": "OA", "updated_at": 10.0},
            {"folder_path": "Trash", "updated_at": 0.0},
        ]
        account = SimpleNamespace(provider="custom", id="account-1")

        with patch("services.sync.list_account_folder_counts", AsyncMock(return_value=rows)):
            folders = await MailSyncService()._get_idle_folders_from_config(account)

        self.assertEqual(folders, ["INBOX", "ROVO", "OA"])


if __name__ == "__main__":
    unittest.main()
