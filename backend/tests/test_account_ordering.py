import unittest
from unittest.mock import AsyncMock, patch

import db
from models import Account


class FakeCursor:
    def __init__(self, rows=None, description=None, rowcount=None):
        self._rows = list(rows or [])
        self.description = description or []
        self.rowcount = len(self._rows) if rowcount is None else rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class RecordingDb:
    def __init__(self, *, max_sort_order=None, owned_ids=None):
        self.calls = []
        self.executemany_calls = []
        self.max_sort_order = max_sort_order
        self.owned_ids = list(owned_ids or [])

    async def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, params))
        if normalized.startswith("SELECT MAX(sort_order)"):
            return FakeCursor(rows=[(self.max_sort_order,)])
        if normalized.startswith("SELECT id FROM accounts"):
            return FakeCursor(rows=[(account_id,) for account_id in self.owned_ids])
        return FakeCursor()

    async def executemany(self, sql, params):
        materialized = list(params)
        self.executemany_calls.append((" ".join(str(sql).split()), materialized))
        return FakeCursor(rowcount=len(materialized))

    async def commit(self):
        self.calls.append(("COMMIT", None))


class AccountOrderingRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_accounts_orders_by_saved_position(self):
        fake_db = RecordingDb()

        with patch.object(db, "get_db", AsyncMock(return_value=fake_db)):
            accounts = await db.get_accounts("user-1")

        self.assertEqual(accounts, [])
        self.assertEqual(len(fake_db.calls), 1)
        sql, params = fake_db.calls[0]
        self.assertIn(
            "WHERE user_uid = ? ORDER BY sort_order ASC, created_at ASC, id ASC",
            sql,
        )
        self.assertEqual(params, ("user-1",))

    async def test_create_account_appends_after_existing_accounts(self):
        fake_db = RecordingDb(max_sort_order=4)
        account = Account(
            id="account-new",
            user_uid="user-1",
            email="new@example.com",
            provider="custom",
            credentials_json="{}",
            status="connected",
            created_at=10,
            updated_at=10,
        )

        with patch.object(db, "get_db", AsyncMock(return_value=fake_db)):
            created = await db.create_account(account)

        self.assertIs(created, account)
        self.assertEqual(account.sort_order, 5)
        commands = [sql for sql, _params in fake_db.calls]
        self.assertEqual(commands[0], "BEGIN")
        self.assertEqual(commands[1], "SELECT id FROM users WHERE id = ? FOR UPDATE")
        self.assertIn("SELECT MAX(sort_order) FROM accounts WHERE user_uid = ? FOR UPDATE", commands[2])
        insert_sql, insert_params = next(
            (sql, params) for sql, params in fake_db.calls if sql.startswith("INSERT INTO accounts")
        )
        self.assertIn("sort_order", insert_sql)
        self.assertEqual(insert_params[10], 5)
        self.assertEqual(commands[-1], "COMMIT")

    async def test_reorder_accounts_updates_complete_owned_sequence(self):
        fake_db = RecordingDb(owned_ids=["a", "b", "c"])

        with patch.object(db, "get_db", AsyncMock(return_value=fake_db)):
            result = await db.reorder_accounts("user-1", ["b", "a", "c"])

        self.assertTrue(result)
        self.assertEqual([sql for sql, _params in fake_db.calls], [
            "BEGIN",
            "SELECT id FROM accounts WHERE user_uid = ? ORDER BY sort_order ASC, created_at ASC, id ASC FOR UPDATE",
            "COMMIT",
        ])
        self.assertEqual(len(fake_db.executemany_calls), 1)
        update_sql, update_params = fake_db.executemany_calls[0]
        self.assertIn(
            "UPDATE accounts SET sort_order = ?, updated_at = ? WHERE id = ? AND user_uid = ?",
            update_sql,
        )
        self.assertEqual(
            [(row[0], row[2], row[3]) for row in update_params],
            [(0, "b", "user-1"), (1, "a", "user-1"), (2, "c", "user-1")],
        )

    async def test_reorder_accounts_rejects_invalid_sequence_without_updates(self):
        invalid_orders = [
            ["a", "a", "c"],
            ["a", "b"],
            ["a", "b", "outside"],
        ]
        for account_ids in invalid_orders:
            with self.subTest(account_ids=account_ids):
                fake_db = RecordingDb(owned_ids=["a", "b", "c"])
                with patch.object(db, "get_db", AsyncMock(return_value=fake_db)):
                    result = await db.reorder_accounts("user-1", account_ids)

                self.assertFalse(result)
                self.assertEqual(fake_db.executemany_calls, [])
                self.assertEqual(fake_db.calls[-1][0], "ROLLBACK")


if __name__ == "__main__":
    unittest.main()
