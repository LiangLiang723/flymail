import unittest
from unittest.mock import AsyncMock, patch

import db
from models import CachedMessage


class _Cursor:
    def __init__(self, one=None, rows=None, rowcount=1):
        self._one = one
        self._rows = rows or []
        self.rowcount = rowcount

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._rows


class _DB:
    def __init__(self, cursors):
        self.cursors = list(cursors)
        self.calls = []
        self.commits = 0

    async def execute(self, sql, params=None):
        self.calls.append((sql, list(params or [])))
        return self.cursors.pop(0)

    async def commit(self):
        self.commits += 1


class MessageSearchDbTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_search_builds_scoped_bound_conditions(self):
        fake = _DB([
            _Cursor(one=(1, 1)),
            _Cursor(rows=[("m1", 7, "Project", "alice@example.com", "me@example.com", "2026-07-10T00:00:00Z", 0, 1, "INBOX", 1, "acc-1", "thread-1")]),
        ])
        with patch.object(db, "get_db", new=AsyncMock(return_value=fake)):
            result = await db.search_cached_messages_by_folder(
                "user-1",
                "acc-1",
                "INBOX",
                "budget",
                from_addr="alice",
                to_addr="bob",
                subject="project",
                body="milestone",
                after="2026-07-01",
                before="2026-08-01",
                read_filter="unread",
                attachment_filter=True,
                starred_filter=True,
            )

        sql, params = fake.calls[0]
        self.assertIn("user_uid = ?", sql)
        self.assertIn("account_id = ?", sql)
        self.assertIn("cc LIKE ?", sql)
        self.assertIn("body_text LIKE ?", sql)
        self.assertIn("is_read = 0", sql)
        self.assertIn("has_attachments = 1", sql)
        self.assertIn("is_starred = 1", sql)
        self.assertIn("date >= ?", sql)
        self.assertIn("date < ?", sql)
        self.assertIn("user-1", params)
        self.assertIn("acc-1", params)
        self.assertIn("%budget%", params)
        self.assertEqual(result["messages"][0]["thread_key"], "thread-1")

    async def test_legacy_thread_backfill_uses_subject_fallback_with_scope(self):
        fake = _DB([
            _Cursor(rows=[("row-1", "<legacy@example.com>", "", "", "Re: Weekly Report")]),
            _Cursor(rowcount=1),
        ])
        with patch.object(db, "get_db", new=AsyncMock(return_value=fake)):
            updated = await db.ensure_cached_message_thread_keys("user-1", "acc-1", "INBOX")

        self.assertEqual(updated, 1)
        select_sql, select_params = fake.calls[0]
        self.assertIn("user_uid = ?", select_sql)
        self.assertIn("account_id = ?", select_sql)
        update_sql, update_params = fake.calls[1]
        self.assertIn("UPDATE cached_messages SET thread_key", update_sql)
        self.assertTrue(update_params[0].startswith("subject:"))
        self.assertEqual(update_params[-2:], ["user-1", "acc-1"])
        self.assertEqual(select_params[0:2], ["user-1", "acc-1"])

    async def test_conversation_list_returns_latest_message_and_counts(self):
        fake = _DB([
            _Cursor(one=(2, 1)),
            _Cursor(rows=[
                (
                    "latest", 9, "Re: Project", "alice@example.com", "me@example.com",
                    "2026-08-07T02:00:00Z", 0, 0, "INBOX", 1, "acc-1", "rfc:abc", 3, 2,
                ),
            ]),
        ])
        with (
            patch.object(db, "get_db", new=AsyncMock(return_value=fake)),
            patch.object(db, "ensure_cached_message_thread_keys", new=AsyncMock(return_value=0)),
        ):
            result = await db.get_message_conversations(
                "user-1", "acc-1", "INBOX", page=1, page_size=40
            )

        count_sql, count_params = fake.calls[0]
        self.assertIn("COUNT(DISTINCT thread_key)", count_sql)
        self.assertIn("user_uid = ?", count_sql)
        self.assertEqual(count_params[0], "user-1")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["unread_total"], 1)
        self.assertEqual(result["messages"][0]["thread_key"], "rfc:abc")
        self.assertEqual(result["messages"][0]["message_count"], 3)
        self.assertEqual(result["messages"][0]["unread_count"], 2)
        self.assertTrue(result["messages"][0]["has_attachments"])

    async def test_conversation_detail_is_chronological_and_scoped(self):
        fake = _DB([
            _Cursor(rows=[
                ("m1", 1, "Project", "a@example.com", "me@example.com", "", "2026-08-07T01:00:00Z", 1, 0, "INBOX", 0, "acc-1", "rfc:abc"),
                ("m2", 2, "Re: Project", "me@example.com", "a@example.com", "", "2026-08-07T02:00:00Z", 0, 0, "INBOX", 0, "acc-1", "rfc:abc"),
            ]),
        ])
        with patch.object(db, "get_db", new=AsyncMock(return_value=fake)):
            result = await db.get_conversation_messages("user-1", "acc-1", "INBOX", "rfc:abc")

        sql, params = fake.calls[0]
        self.assertIn("user_uid = ?", sql)
        self.assertIn("account_id = ?", sql)
        self.assertIn("thread_key = ?", sql)
        self.assertIn("ORDER BY date ASC, uid ASC", sql)
        self.assertEqual(params[0], "user-1")
        self.assertEqual(params[1], "acc-1")
        self.assertEqual([item["uid"] for item in result], [1, 2])

    async def test_upsert_persists_thread_columns(self):
        fake = _DB([_Cursor(rowcount=1)])
        message = CachedMessage(
            id="legacy-id",
            account_id="acc-1",
            user_uid="user-1",
            uid=42,
            folder="INBOX",
            subject="Re: Project",
            from_addr="alice@example.com",
            to_addr="me@example.com",
            date="2026-08-07T00:00:00Z",
            message_id="<child@example.com>",
            in_reply_to="<parent@example.com>",
            references_header="<root@example.com> <parent@example.com>",
            thread_key="rfc:abc",
        )
        with patch.object(db, "get_db", new=AsyncMock(return_value=fake)):
            await db.upsert_cached_messages([message])

        sql, params = fake.calls[0]
        self.assertIn("in_reply_to", sql)
        self.assertIn("references_header", sql)
        self.assertIn("thread_key", sql)
        self.assertIn("<parent@example.com>", params)
        self.assertIn("rfc:abc", params)


if __name__ == "__main__":
    unittest.main()
