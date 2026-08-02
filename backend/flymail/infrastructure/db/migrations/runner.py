"""Versioned, advisory-locked FlyMail V2 MySQL migrations."""

from __future__ import annotations

import json
import re
import time

import aiomysql

from flymail.infrastructure.db.migrations import Migration
from flymail.infrastructure.db.migrations.v0001_identity import MIGRATION as IDENTITY_MIGRATION
from flymail.infrastructure.db.migrations.v0002_mail import MIGRATION as MAIL_MIGRATION
from flymail.infrastructure.db.migrations.v0003_jobs import MIGRATION as JOBS_MIGRATION
from flymail.infrastructure.db.migrations.v0004_content_search import build_migration as build_content_migration
from flymail.infrastructure.db.migrations.v0005_job_claim_order import MIGRATION as JOB_CLAIM_ORDER_MIGRATION
from flymail.infrastructure.db.migrations.v0006_message_fallback_index import MIGRATION as MESSAGE_FALLBACK_INDEX_MIGRATION
from flymail.infrastructure.db.migrations.v0007_worker_scheduler_scope import MIGRATION as WORKER_SCHEDULER_SCOPE_MIGRATION
from flymail.infrastructure.db.migrations.v0008_message_body_parts import MIGRATION as MESSAGE_BODY_PARTS_MIGRATION
from flymail.infrastructure.db.migrations.v0009_reliable_sender import MIGRATION as RELIABLE_SENDER_MIGRATION
from flymail.infrastructure.db.migrations.v0010_notification_asset_reference import MIGRATION as NOTIFICATION_ASSET_REFERENCE_MIGRATION
from flymail.infrastructure.db.migrations.v0011_process_heartbeats import MIGRATION as PROCESS_HEARTBEATS_MIGRATION
from flymail.infrastructure.db.migrations.v0012_authentication_sessions import MIGRATION as AUTHENTICATION_SESSIONS_MIGRATION
from flymail.infrastructure.db.migrations.v0013_bulk_mail_operations import MIGRATION as BULK_MAIL_OPERATIONS_MIGRATION
from flymail.infrastructure.db.migrations.v0014_draft_versions import MIGRATION as DRAFT_VERSIONS_MIGRATION
from flymail.infrastructure.db.pool import DatabasePool


LATEST_SCHEMA_VERSION = DRAFT_VERSIONS_MIGRATION.version

_MIGRATION_LOCK_NAME = "flymail_v2_schema_migration"
_CREATE_TABLE_PATTERN = re.compile(
    r"^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`?([A-Za-z0-9_]+)`?",
    re.IGNORECASE,
)
_ADD_INDEX_PATTERN = re.compile(
    r"^\s*ALTER\s+TABLE\s+`?([A-Za-z0-9_]+)`?\s+ADD\s+(?:UNIQUE\s+)?(?:KEY|INDEX)\s+`?([A-Za-z0-9_]+)`?",
    re.IGNORECASE,
)
_ADD_COLUMN_PATTERN = re.compile(
    r"^\s*ALTER\s+TABLE\s+`?([A-Za-z0-9_]+)`?\s+ADD\s+COLUMN\s+`?([A-Za-z0-9_]+)`?",
    re.IGNORECASE,
)
_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INT NOT NULL PRIMARY KEY,
    name VARCHAR(191) NOT NULL,
    metadata_json JSON NOT NULL,
    applied_at DOUBLE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""


async def _table_exists(connection: aiomysql.Connection, table_name: str) -> bool:
    async with connection.cursor() as cursor:
        await cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
            """,
            (table_name,),
        )
        row = await cursor.fetchone()
        return bool(row and int(row[0] or 0) > 0)


async def _column_exists(
    connection: aiomysql.Connection,
    table_name: str,
    column_name: str,
) -> bool:
    async with connection.cursor() as cursor:
        await cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name = %s
            """,
            (table_name, column_name),
        )
        row = await cursor.fetchone()
        return bool(row and int(row[0] or 0) > 0)


async def _index_exists(
    connection: aiomysql.Connection,
    table_name: str,
    index_name: str,
) -> bool:
    async with connection.cursor() as cursor:
        await cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND index_name = %s
            """,
            (table_name, index_name),
        )
        row = await cursor.fetchone()
        return bool(row and int(row[0] or 0) > 0)


async def current_schema_version(connection: aiomysql.Connection) -> int:
    if not await _table_exists(connection, "schema_migrations"):
        return 0
    async with connection.cursor() as cursor:
        await cursor.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
        row = await cursor.fetchone()
        return int(row[0] or 0) if row else 0


async def _ngram_available(connection: aiomysql.Connection) -> bool:
    async with connection.cursor() as cursor:
        await cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.plugins
            WHERE plugin_name = 'ngram' AND plugin_status = 'ACTIVE'
            """
        )
        row = await cursor.fetchone()
        return bool(row and int(row[0] or 0) > 0)


async def _migrations(connection: aiomysql.Connection) -> tuple[Migration, ...]:
    content_migration = build_content_migration(use_ngram=await _ngram_available(connection))
    return (
        IDENTITY_MIGRATION,
        MAIL_MIGRATION,
        JOBS_MIGRATION,
        content_migration,
        JOB_CLAIM_ORDER_MIGRATION,
        MESSAGE_FALLBACK_INDEX_MIGRATION,
        WORKER_SCHEDULER_SCOPE_MIGRATION,
        MESSAGE_BODY_PARTS_MIGRATION,
        RELIABLE_SENDER_MIGRATION,
        NOTIFICATION_ASSET_REFERENCE_MIGRATION,
        PROCESS_HEARTBEATS_MIGRATION,
        AUTHENTICATION_SESSIONS_MIGRATION,
        BULK_MAIL_OPERATIONS_MIGRATION,
        DRAFT_VERSIONS_MIGRATION,
    )


async def _execute_idempotent_ddl(connection: aiomysql.Connection, statement: str) -> None:
    table_match = _CREATE_TABLE_PATTERN.match(statement)
    if table_match and await _table_exists(connection, table_match.group(1)):
        return
    column_match = _ADD_COLUMN_PATTERN.match(statement)
    if column_match and await _column_exists(
        connection,
        column_match.group(1),
        column_match.group(2),
    ):
        return
    index_match = _ADD_INDEX_PATTERN.match(statement)
    if index_match and await _index_exists(
        connection,
        index_match.group(1),
        index_match.group(2),
    ):
        return
    async with connection.cursor() as cursor:
        await cursor.execute(statement)


async def _acquire_migration_lock(connection: aiomysql.Connection) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute("SELECT GET_LOCK(%s, 30)", (_MIGRATION_LOCK_NAME,))
        row = await cursor.fetchone()
    if not row or int(row[0] or 0) != 1:
        raise RuntimeError("could not acquire FlyMail V2 schema migration lock")


async def _release_migration_lock(connection: aiomysql.Connection) -> None:
    async with connection.cursor() as cursor:
        await cursor.execute("SELECT RELEASE_LOCK(%s)", (_MIGRATION_LOCK_NAME,))
        await cursor.fetchone()


async def run_migrations(pool: DatabasePool) -> list[int]:
    """Apply all absent V2 migrations and return the versions applied now.

    MySQL DDL implicitly commits. Every DDL statement is therefore idempotent,
    and the version row is inserted only after all statements for that version
    succeed. A crash leaves the version absent so the advisory-locked runner can
    safely repeat the version.
    """

    applied_now: list[int] = []
    async with pool.acquire() as connection:
        await _acquire_migration_lock(connection)
        try:
            await _execute_idempotent_ddl(connection, _SCHEMA_MIGRATIONS_DDL)
            await connection.commit()

            async with connection.cursor() as cursor:
                await cursor.execute("SELECT version FROM schema_migrations")
                existing = {int(row[0]) for row in await cursor.fetchall()}

            for migration in await _migrations(connection):
                if migration.version in existing:
                    continue
                try:
                    for statement in migration.statements:
                        await _execute_idempotent_ddl(connection, statement)
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            """
                            INSERT INTO schema_migrations (version, name, metadata_json, applied_at)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (
                                migration.version,
                                migration.name,
                                json.dumps(dict(migration.metadata), ensure_ascii=False, sort_keys=True),
                                time.time(),
                            ),
                        )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
                existing.add(migration.version)
                applied_now.append(migration.version)
        finally:
            await _release_migration_lock(connection)
    return applied_now
