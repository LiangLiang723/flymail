import unittest
from unittest.mock import AsyncMock, patch

import db


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class CachedBodyProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_matches_body_fill_selection(self):
        database = AsyncMock()
        database.execute.return_value = _FakeCursor((12, 8))

        with patch.object(db, "get_db", AsyncMock(return_value=database)):
            progress = await db.get_cached_body_check_progress("account-1", "INBOX")

        self.assertEqual(
            progress,
            {
                "total_count": 12,
                "checked_count": 4,
                "remaining_count": 8,
            },
        )
        sql, params = database.execute.await_args.args
        self.assertIn("COALESCE(cm.body_text, '') = ''", sql)
        self.assertIn("COALESCE(cm.body_html, '') = ''", sql)
        self.assertIn("cached_message_empty_body_checks", sql)
        self.assertEqual(params[0], "account-1")
        self.assertIn("INBOX", params)


if __name__ == "__main__":
    unittest.main()
