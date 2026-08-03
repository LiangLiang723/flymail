from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
from urllib.parse import unquote, urlparse

import aiomysql
from pymysql.err import IntegrityError

from flymail.infrastructure.db.migrations.runner import (
    LATEST_SCHEMA_VERSION,
    current_schema_version,
    run_migrations,
)
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
    "mail_operations", "bulk_mail_operations", "outbox_events", "worker_jobs", "job_attempts",
    "process_heartbeats", "login_rate_limits", "sync_cursors", "account_runtime_state", "realtime_events",
    "notification_channels", "notification_rules", "notification_image_publishers",
    "notification_events", "notification_deliveries", "notification_preferences",
    "drafts", "draft_versions", "draft_recipients", "draft_attachments", "send_attempts",
    "saved_searches", "search_history", "backup_jobs", "backup_archives",
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

    async def column_names(self, table: str) -> list[str]:
        rows = await self.fetchall(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [str(row[0]) for row in rows]

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
        expected_versions = list(range(1, LATEST_SCHEMA_VERSION + 1))
        self.assertEqual(await run_migrations(self.pool), expected_versions)
        async with self.pool.acquire() as connection:
            self.assertEqual(await current_schema_version(connection), LATEST_SCHEMA_VERSION)
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
            (9, "reliable_sender_state"),
            (10, "notification_asset_reference"),
            (11, "process_heartbeats"),
            (12, "authentication_sessions"),
            (13, "bulk_mail_operations"),
            (14, "draft_versions"),
            (15, "notification_preferences"),
            (16, "backup_archives"),
            (17, "hybrid_fulltext_search"),
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
        self.assertEqual(
            sorted(results, key=len),
            [[], list(range(1, LATEST_SCHEMA_VERSION + 1))],
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM schema_migrations"),
            LATEST_SCHEMA_VERSION,
        )

    async def test_partial_ddl_without_version_record_recovers_idempotently(self):
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(IDENTITY_MIGRATION.statements[0])
                await connection.commit()

        self.assertEqual(
            await run_migrations(self.pool),
            list(range(1, LATEST_SCHEMA_VERSION + 1)),
        )
        self.assertEqual(
            await self.scalar("SELECT COUNT(*) FROM schema_migrations"),
            LATEST_SCHEMA_VERSION,
        )

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

    async def test_reliable_sender_state_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 9")
                await cursor.execute("ALTER TABLE drafts DROP INDEX idx_drafts_send_state")
                await cursor.execute("ALTER TABLE drafts DROP COLUMN verification_attempts")
                await cursor.execute("ALTER TABLE drafts DROP COLUMN composed_object_sha256")
                await cursor.execute("ALTER TABLE drafts DROP COLUMN send_state")
                await connection.commit()

        self.assertEqual(await run_migrations(self.pool), [9])
        self.assertEqual(
            int(await self.scalar(
                """
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'drafts'
                  AND column_name IN (
                    'send_state', 'composed_object_sha256', 'verification_attempts'
                  )
                """
            ) or 0),
            3,
        )
        self.assertEqual(
            await self.index_columns("drafts", "idx_drafts_send_state"),
            [
                ("user_uid", "A"), ("send_state", "A"),
                ("scheduled_at", "A"), ("id", "A"),
            ],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 9")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [9])

    async def test_notification_asset_reference_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 10")
                await cursor.execute(
                    "ALTER TABLE notification_events DROP INDEX idx_notification_events_asset"
                )
                await cursor.execute(
                    "ALTER TABLE notification_events DROP COLUMN notification_asset_id"
                )
                await connection.commit()

        self.assertEqual(await run_migrations(self.pool), [10])
        self.assertEqual(
            int(await self.scalar(
                """
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'notification_events'
                  AND column_name = 'notification_asset_id'
                """
            ) or 0),
            1,
        )
        self.assertEqual(
            await self.index_columns(
                "notification_events",
                "idx_notification_events_asset",
            ),
            [("notification_asset_id", "A"), ("id", "A")],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 10")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [10])

    async def test_process_heartbeat_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 11")
                await cursor.execute("DROP TABLE process_heartbeats")
                await connection.commit()

        self.assertEqual(await run_migrations(self.pool), [11])
        self.assertEqual(
            await self.index_columns(
                "process_heartbeats",
                "idx_process_heartbeats_role_time",
            ),
            [("role", "A"), ("heartbeat_at", "D"), ("process_id", "A")],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 11")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [11])

    async def test_authentication_session_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        self.assertEqual(
            await self.column_names("user_sessions"),
            [
                "id", "user_uid", "token_hash", "password_version",
                "csrf_token_hash", "expires_at", "revoked_at",
                "last_seen_at", "created_at",
            ],
        )
        self.assertEqual(
            await self.index_columns(
                "login_rate_limits",
                "idx_login_rate_limits_blocked",
            ),
            [("blocked_until", "A"), ("updated_at", "A")],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 12")
                await cursor.execute("DROP TABLE login_rate_limits")
                await cursor.execute(
                    "ALTER TABLE user_sessions DROP COLUMN csrf_token_hash"
                )
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [12])
        self.assertIn("csrf_token_hash", await self.column_names("user_sessions"))
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='login_rate_limits'"
            ),
            1,
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 12")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [12])

    async def test_bulk_mail_operation_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        self.assertEqual(
            await self.index_columns(
                "bulk_mail_operations",
                "uq_bulk_mail_operations_idempotency",
            ),
            [("user_uid", "A"), ("idempotency_key", "A")],
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 13")
                await cursor.execute("DROP TABLE bulk_mail_operations")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [13])
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='bulk_mail_operations'"
            ),
            1,
        )

        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 13")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [13])

    async def test_draft_version_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        self.assertEqual(
            await self.index_columns("draft_versions", "idx_draft_versions_version"),
            [("draft_id", "A"), ("version", "A"), ("source", "A"), ("id", "A")],
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 14")
                await cursor.execute("DROP TABLE draft_versions")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [14])
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='draft_versions'"
            ),
            1,
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 14")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [14])

    async def test_notification_preferences_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        self.assertEqual(
            await self.column_names("notification_preferences"),
            [
                "user_uid", "in_app_enabled", "external_enabled",
                "include_images", "quiet_hours_json", "event_preferences_json",
                "created_at", "updated_at",
            ],
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 15")
                await cursor.execute("DROP TABLE notification_preferences")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [15])
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='notification_preferences'"
            ),
            1,
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 15")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [15])

    async def test_backup_archives_upgrade_and_crash_recovery_are_idempotent(self):
        await run_migrations(self.pool)
        self.assertEqual(
            await self.index_columns("backup_archives", "idx_backup_archives_status"),
            [("status", "A"), ("updated_at", "A"), ("id", "A")],
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 16")
                await cursor.execute("DROP TABLE backup_archives")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [16])
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='backup_archives'"
            ),
            1,
        )
        async with self.pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM schema_migrations WHERE version = 16")
                await connection.commit()
        self.assertEqual(await run_migrations(self.pool), [16])

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
            await self.index_columns("drafts", "idx_drafts_send_state"),
            [
                ("user_uid", "A"), ("send_state", "A"),
                ("scheduled_at", "A"), ("id", "A"),
            ],
        )
        self.assertEqual(
            await self.index_columns("notification_events", "idx_notification_events_asset"),
            [("notification_asset_id", "A"), ("id", "A")],
        )
        self.assertEqual(
            await self.index_columns(
                "bulk_mail_operations",
                "idx_bulk_mail_operations_user_status",
            ),
            [("user_uid", "A"), ("status", "A"), ("id", "A")],
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
        standard_columns = await self.index_columns(
            "body_search_documents",
            "ft_body_search_standard",
        )
        self.assertEqual(
            [column for column, _direction in standard_columns],
            ["body_text", "subject_text", "participants_text"],
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
