import unittest


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _RecordingDb:
    def __init__(self, existing_type: str):
        self.existing_type = existing_type
        self.statements: list[str] = []

    async def execute(self, sql: str, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT DATA_TYPE FROM information_schema.columns"):
            return _Cursor((self.existing_type,))
        self.statements.append(normalized)
        return _Cursor(None)


class MailHeaderColumnMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_to_addr_columns_are_widened_to_longtext(self):
        import db

        migration = getattr(db, "_widen_mail_address_columns", None)
        self.assertIsNotNone(
            migration,
            "init_db must provide a migration for recipient headers longer than 512 characters",
        )

        recorder = _RecordingDb("varchar")
        await migration(recorder)

        self.assertEqual(
            recorder.statements,
            [
                "ALTER TABLE cached_messages MODIFY COLUMN to_addr LONGTEXT",
                "ALTER TABLE notifications MODIFY COLUMN to_addr LONGTEXT",
                "ALTER TABLE message_archive MODIFY COLUMN to_addr LONGTEXT",
            ],
        )

    async def test_longtext_columns_are_not_altered_again(self):
        import db

        migration = getattr(db, "_widen_mail_address_columns", None)
        self.assertIsNotNone(migration)

        recorder = _RecordingDb("longtext")
        await migration(recorder)

        self.assertEqual(recorder.statements, [])


if __name__ == "__main__":
    unittest.main()
