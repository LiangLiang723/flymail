from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
from urllib.parse import unquote, urlparse

import aiomysql
from pymysql.err import IntegrityError

from flymail.infrastructure.db.migrations.runner import current_schema_version, run_migrations
from flymail.infrastructure.db.migrations.v0001_identity import MIGRATION as IDENTITY_MIGRATION
from flymail.infrastructure.db.pool import DatabasePool
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase


EXPECTED_TABLES = {
    "schema_migrations",
    "users", "user_profiles", "user_sessions", "user_settings", "audit_events",
    "contacts", "authorized_storage_roots",
    "mail_accounts", "mail_identities", "provider_credentials",
    "oauth_authorization_states", "outbound_proxy_configs",
    "mailboxes", "messages", "message_headers", "message_remote_instances",
    "message_memberships", "threads", "thread_messages", "thread_projections",
    "message_bodies", "message_body_parts", "message_attachments",
    "content_objects", "content_references", "body_search_documents",
    "mail_operations", "outbox_events", "worker_jobs", "job_attempts",
    "sync_cursors", "account_runtime_state", "realtime_events",
    "notification_channels", "notification_rules", "notification_image_publishers",
    "notification_events", "notification_deliveries",
    "drafts", "draft_recipients", "draft_attachments", "send_attempts",
    "saved_searches", "search_history", "backup_jobs",
}


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    pool: DatabasePool

    @classmethod
    def database_url(cls) -> str:
        value = os.environ.get("FLYMAIL_TEST_DATABASE_URL", "").strip()
        if not value:
            raise unittest.SkipTest("FLYMAIL_TEST_DATABASE_URL is required for migration tests")
        return value

    @classmethod
    def database_name(cls) -> str:
        name = unquote(urlparse(cls.database_url()).path.lstrip("/"))
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise RuntimeError("migration test database name must contain only letters, digits, or underscore")
        return name

    @classmethod
    def database_user(cls) -> str:
        user = unquote(urlparse(cls.database_url()).username or "")
        if not re.fullmatch(r"[A-Za-z0-9_]+", user):
            raise RuntimeError("migration test database user must contain only letters, digits, or underscore")
        return user

    async def asyncSetUp(self) -> None:
        socket_path = os.environ.get("FLYMAIL_TEST_MYSQL_SOCKET", "/run/mysqld/mysqld.sock")
        if not os.path.exists(socket_path):
            raise unittest.SkipTest("root MySQL socket is required to recreate the migration test database")
        database = self.database_name()
        database_user = self.database_user()
        admin = await aiomysql.connect(
            user="root",
            unix_socket=socket_path,
            db="mysql",
            charset="utf8mb4",
            autocommit=True,
        )
        try:
            async with admin.cursor() as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s",
                    (database,),
                )
                if int((await cursor.fetchone())[0] or 0) > 0:
                    await cursor.execute(f"DROP DATABASE `{database}`")
                await cursor.execute(
                    f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
                await cursor.execute(
                    f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{database_user}'@'127.0.0.1'"
                )
        finally:
            admin.close()
        self.pool = await DatabasePool.create(MySqlIsolatedAsyncioTestCase.settings("api"))

    async def asyncTearDown(self) -> None:
        await self.pool.close()

    async def fetchall(self, sql: str, params: tuple | list = ()) -> list[tuple]:
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def scalar(self, sql: str, params: tuple | list = ()):
        rows = await self.fetchall(sql, params)
        return rows[0][0] if rows else None

    async def index_columns(self, table: str, index: str) -> list[tuple[str, str | None]]:
        rows = await self.fetchall(
            """
            SELECT column_name, collation
            FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s
            ORDER BY seq_in_index
            """,
            (table, index),
        )
        return [(str(row[0]), row[1]) for row in rows]

    async def test_empty_database_applies_all_migrations_and_second_run_is_noop(self):
        self.assertEqual(await run_migrations(self.pool), [1, 2, 3, 4, 5, 6, 7, 8])
        async with self.pool.acquire() as connection:
            self.assertEqual(await current_schema_version(connection), 8)
        self.assertEqual(await run_migrations(self.pool), [])

        records = await self.fetchall(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        )
        self.assertEqual(records, [
            (1, "identity_and_configuration"),
            (2, "mail_and_threads"),
            (3, "jobs_realtime_and_drafts"),
            (4, "content_and_search"),
            (5, "job_claim_order"),
            (6, "message_thread_fallback_index"),
            (7, "worker_scheduler_scope"),
            (8, "message_body_parts"),
        ])

    async def test_required_tables_and_ascii_identifier_collation_exist(self):
        await run_migrations(self.pool)
        rows = await self.fetchall(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
        )
        self.assertEqual({str(row[0]) for row in rows}, EXPECTED_TABLES)

        id_collation = await self.scalar(
            """
            SELECT collation_name
            FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'users' AND column_name = 'id'
            """
        )
        self.assertEqual(id_collation, "ascii_bin")

    async def test_concurrent_runners_apply_each_version_once(self):
        results = await asyncio.gather(run_migrations(self.pool), run_migrations(self.pool))
        self.assertEqual(sorted(results, key=len), [[], [1, 2, 3, 4, 5, 6, 7, 8]])
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM schema_migrations"),
            8,
        )

    async def test_partial_ddl_without_version_record_recovers_idempotently(self):
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(IDENTITY_MIGRATION.statements[0])
                await connection.commit()

        self.assertEqual(await run_migrations(self.pool), [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM schema_migrations"), 8)

    async def test_job_claim_index_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 5")
                await cursor.execute("ALTER TABLE worker_jobs DROP INDEX idx_worker_jobs_claim_order")
                await connection.commit()

        self.assertEqual(await run_migrations(self.pool), [5])
        self.assertEqual(
            await self.index_columns("worker_jobs", "idx_worker_jobs_claim_order"),
            [("queue_name", "A"), ("priority", "A"), ("available_at", "A"), ("id", "A"), ("status", "A")],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 5")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [5])

    async def test_message_fallback_index_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 6")
                await cursor.execute("ALTER TABLE messages DROP INDEX idx_messages_subject_fallback")
                await connection.commit()

        self.assertEqual(await run_migrations(self.pool), [6])
        self.assertEqual(
            await self.index_columns("messages", "idx_messages_subject_fallback"),
            [("user_uid", "A"), ("normalized_subject", "A"), ("received_at", "D"), ("id", "D")],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 6")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [6])

    async def test_worker_scheduler_scope_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 7")
                await cursor.execute("ALTER TABLE worker_jobs DROP INDEX idx_worker_jobs_scheduler")
                await cursor.execute("ALTER TABLE worker_jobs DROP COLUMN provider_key")
                await cursor.execute("ALTER TABLE worker_jobs DROP COLUMN account_id")
                await connection.commit()

        self.assertEqual(await run_migrations(self.pool), [7])
        self.assertEqual(
            int(await self.scalar(
                """
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'worker_jobs'
                  AND column_name IN ('account_id', 'provider_key')
                """
            ) or 0),
            2,
        )
        self.assertEqual(
            await self.index_columns("worker_jobs", "idx_worker_jobs_scheduler"),
            [
                ("queue_name", "A"), ("status", "A"),
                ("available_at", "A"), ("priority", "A"),
                ("provider_key", "A"), ("account_id", "A"), ("id", "A"),
            ],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 7")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [7])

    async def test_message_body_parts_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 8")
                await cursor.execute("DROP TABLE message_body_parts")
                await connection.commit()

        self.assertEqual(await run_migrations(self.pool), [8])
        self.assertEqual(
            await self.index_columns("message_body_parts", "uq_message_body_parts_kind"),
            [("remote_instance_id", "A"), ("body_kind", "A")],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 8")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [8])

    async def test_critical_index_column_order_and_direction(self):
        await run_migrations(self.pool)

        self.assertEqual(
            await self.index_columns("thread_projections", "idx_thread_projection_cursor"),
            [("user_uid", "A"), ("semantic_mailbox", "A"), ("latest_message_at", "D"), ("thread_id", "D")],
        )
        self.assertEqual(
            await self.index_columns("worker_jobs", "idx_worker_jobs_claim"),
            [("queue_name", "A"), ("status", "A"), ("available_at", "A"), ("priority", "A"), ("id", "A")],
        )
        self.assertEqual(
            await self.index_columns("worker_jobs", "idx_worker_jobs_claim_order"),
            [("queue_name", "A"), ("priority", "A"), ("available_at", "A"), ("id", "A"), ("status", "A")],
        )
        self.assertEqual(
            await self.index_columns("worker_jobs", "idx_worker_jobs_scheduler"),
            [
                ("queue_name", "A"), ("status", "A"),
                ("available_at", "A"), ("priority", "A"),
                ("provider_key", "A"), ("account_id", "A"), ("id", "A"),
            ],
        )
        self.assertEqual(
            await self.index_columns("messages", "idx_messages_subject_fallback"),
            [("user_uid", "A"), ("normalized_subject", "A"), ("received_at", "D"), ("id", "D")],
        )
        self.assertEqual(
            await self.index_columns("message_body_parts", "uq_message_body_parts_kind"),
            [("remote_instance_id", "A"), ("body_kind", "A")],
        )
        self.assertEqual(
            await self.index_columns("outbox_events", "idx_outbox_unpublished"),
            [("published_at", "A"), ("created_at", "A"), ("id", "A")],
        )
        self.assertEqual(
            await self.index_columns("message_remote_instances", "uq_remote_identity"),
            [("account_id", "A"), ("mailbox_id", "A"), ("uidvalidity", "A"), ("remote_uid", "A")],
        )
        self.assertEqual(
            await self.index_columns("content_references", "idx_content_references_object"),
            [("content_sha256", "A"), ("reference_kind", "A"), ("user_uid", "A")],
        )

    async def test_account_wide_sync_cursor_scope_is_unique(self):
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO sync_cursors (
                        id, user_uid, account_id, mailbox_id, phase, cursor_type, updated_at
                    ) VALUES (%s, %s, %s, '', %s, 'json', 0)
                    """,
                    ("cur_first", "usr_owner", "acc_primary", "summary"),
                )
                await connection.commit()
                with self.assertRaises(IntegrityError):
                    await cursor.execute(
                        """
                        INSERT INTO sync_cursors (
                            id, user_uid, account_id, mailbox_id, phase, cursor_type, updated_at
                        ) VALUES (%s, %s, %s, '', %s, 'json', 0)
                        """,
                        ("cur_second", "usr_owner", "acc_primary", "summary"),
                    )
                await connection.rollback()

    async def test_fulltext_index_and_detected_parser_are_recorded(self):
        await run_migrations(self.pool)
        fulltext_columns = await self.index_columns("body_search_documents", "ft_body_search")
        self.assertEqual(
            [column for column, _direction in fulltext_columns],
            ["subject_text", "participants_text", "body_text"],
        )

        raw_metadata = await self.scalar(
            "SELECT metadata_json FROM schema_migrations WHERE version = 4"
        )
        metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else raw_metadata
        plugin_active = int(await self.scalar(
            """
            SELECT COUNT(*) FROM information_schema.plugins
            WHERE plugin_name = 'ngram' AND plugin_status = 'ACTIVE'
            """
        ) or 0) > 0
        self.assertEqual(metadata["fulltext_parser"], "ngram" if plugin_active else "standard")


if __name__ == "__main__":
    unittest.main()
