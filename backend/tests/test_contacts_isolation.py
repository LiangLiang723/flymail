import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class _Model:
    model_fields = {}

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _load_db_module():
    aiomysql_stub = types.ModuleType("aiomysql")
    aiomysql_stub.Pool = object
    aiomysql_stub.Connection = object
    aiomysql_stub.create_pool = AsyncMock()

    pymysql_stub = types.ModuleType("pymysql")
    pymysql_stub.err = types.SimpleNamespace(
        InterfaceError=type("InterfaceError", (Exception,), {}),
        OperationalError=type("OperationalError", (Exception,), {}),
    )

    data_paths_stub = types.ModuleType("data_paths")
    data_paths_stub.ensure_data_dirs = lambda: None

    models_stub = types.ModuleType("models")
    for name in ("Account", "CachedAttachment", "CachedMessage", "Notification", "Signature", "User"):
        setattr(models_stub, name, _Model)

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = lambda _name: types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    modules = {
        "aiomysql": aiomysql_stub,
        "pymysql": pymysql_stub,
        "data_paths": data_paths_stub,
        "models": models_stub,
        "utils.logger": logger_stub,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        module_path = Path(__file__).resolve().parents[1] / "db" / "__init__.py"
        spec = importlib.util.spec_from_file_location("db_for_contacts_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class _Cursor:
    def __init__(self, rows=None, *, rowcount=0, description=None):
        self._rows = list(rows or [])
        self.rowcount = rowcount
        self.description = description or []
        self.lastrowid = 0

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, cursors):
        self.cursors = list(cursors)
        self.calls = []
        self.commit = AsyncMock()

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if not self.cursors:
            raise AssertionError(f"unexpected SQL: {sql}")
        return self.cursors.pop(0)


class ContactIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_contact_list_filters_by_user_uid(self):
        db = _load_db_module()
        self.assertTrue(hasattr(db, "get_contacts"), "get_contacts must be implemented")
        fake = _FakeDB([_Cursor([], description=[("id",)])])

        with patch.object(db, "get_db", AsyncMock(return_value=fake)):
            result = await db.get_contacts("user-a")

        self.assertEqual(result, [])
        sql, params = fake.calls[0]
        self.assertIn("user_uid", sql.lower())
        self.assertEqual(tuple(params), ("user-a",))

    async def test_delete_contact_does_not_delete_child_rows_before_ownership_check(self):
        db = _load_db_module()
        self.assertTrue(hasattr(db, "delete_contact"), "delete_contact must be implemented")
        fake = _FakeDB([_Cursor([])])

        with patch.object(db, "get_db", AsyncMock(return_value=fake)):
            deleted = await db.delete_contact(7, "user-a")

        self.assertFalse(deleted)
        self.assertEqual(len(fake.calls), 1)
        sql, params = fake.calls[0]
        self.assertIn("contacts", sql.lower())
        self.assertIn("user_uid", sql.lower())
        self.assertEqual(tuple(params), (7, "user-a"))
        self.assertNotIn("contact_emails", sql.lower())
        fake.commit.assert_not_awaited()

    async def test_contact_stats_uses_literal_mysql_prefilter_and_exact_address_match(self):
        db = _load_db_module()
        fake = _FakeDB([
            _Cursor([
                ("2026-08-07T10:00:00Z", "Neal <neal_chen@example.com>", "me@example.com", ""),
                ("2026-08-07T11:00:00Z", "other@example.com", "neal_chen@example.com.invalid", ""),
            ]),
        ])

        with patch.object(db, "get_db", AsyncMock(return_value=fake)):
            result = await db.get_contact_stats("user-a", "Neal_Chen@Example.com")

        self.assertEqual(result, {"count": 1, "last_date": "2026-08-07T10:00:00Z"})
        sql, params = fake.calls[0]
        self.assertIn("LOCATE", sql.upper())
        self.assertNotIn(" ESCAPE ", sql.upper())
        self.assertIn("user_uid", sql)
        self.assertEqual(
            tuple(params),
            (
                "user-a",
                "neal_chen@example.com",
                "neal_chen@example.com",
                "neal_chen@example.com",
            ),
        )


if __name__ == "__main__":
    unittest.main()
