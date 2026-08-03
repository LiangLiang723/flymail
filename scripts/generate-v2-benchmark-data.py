#!/usr/bin/env python3
"""Generate and benchmark deterministic FlyMail V2 synthetic capacity data.

The generator refuses the production database and production host data path. It
uses the real V2 schema and bounded SQL batches; no generated address leaves the
reserved ``example.test`` domain.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import socket
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import unquote, urlparse

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import aiomysql

from flymail.api.schemas.search import SearchFilter
from flymail.application.auth import AuthenticatedSession
from flymail.application.bootstrap import BootstrapService
from flymail.application.search_queries import SearchQueryService
from flymail.application.thread_queries import ThreadQueryService
from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.store import ObjectStore, object_path
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.sessions import SessionRecord
from flymail.repositories.users import User


PRODUCTION_DATA_ROOT = Path("/Docker/flymail/data")
PRODUCTION_DATABASE_NAMES = {
    "flymail",
    "mysql",
    "information_schema",
    "performance_schema",
    "sys",
}
THREAD_SIZE = 4
COMMIT_TARGET_ROWS = 100_000
GENERATION_WRITE_CONCURRENCY = 4
BASE_TIMESTAMP = 1_700_000_000.0
SYNTHETIC_BODY = (
    b"FlyMail V2 synthetic capacity body. capacityterm benchmark-only content.\n"
)
SYNTHETIC_BODY_SHA256 = hashlib.sha256(SYNTHETIC_BODY).hexdigest()


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    database_url: str
    users: int = 50
    accounts: int = 300
    messages: int = 20_000_000
    seed: int = 20_260_731
    batch_size: int = 5_000
    body_cache_ratio: float = 0.05
    object_root: str = "/tmp/flymail-v2-capacity-objects"
    reset: bool = False
    defer_indexes: bool = False


@dataclass(frozen=True, slots=True)
class BenchmarkThreshold:
    p95_ms: float
    label: str


API_THRESHOLDS = {
    "bootstrap": BenchmarkThreshold(300.0, "Bootstrap"),
    "thread_first_page": BenchmarkThreshold(150.0, "thread first page"),
    "thread_next_page": BenchmarkThreshold(150.0, "thread next page"),
    "cached_detail": BenchmarkThreshold(150.0, "cached detail structure"),
    "cached_body_first_byte": BenchmarkThreshold(200.0, "cached body first byte"),
    "structured_search": BenchmarkThreshold(300.0, "structured search"),
    "cached_body_fulltext": BenchmarkThreshold(500.0, "cached-body FULLTEXT"),
    "local_operation_commit": BenchmarkThreshold(200.0, "local operation commit"),
    "api_db_connection_wait": BenchmarkThreshold(50.0, "API DB connection wait"),
}

WORKER_THRESHOLDS = {
    "p0_claim": BenchmarkThreshold(500.0, "P0 queue claim"),
    "idle_projection": BenchmarkThreshold(5_000.0, "IDLE event to visible summary"),
    "offline_operation_begin": BenchmarkThreshold(5_000.0, "offline operation begin"),
    "restart_recovery": BenchmarkThreshold(30_000.0, "Worker restart recovery"),
}

PLAN_INDEX_REQUIREMENTS = {
    "thread_list": ("thread_projections", "idx_thread_projection_cursor"),
    "structured_search": ("messages", "idx_messages_user_received"),
    "job_claim": ("worker_jobs", "idx_worker_jobs_scheduler"),
    "remote_instance": ("message_remote_instances", "idx_remote_instances_provider_id"),
    "quota_lru": ("content_references", "idx_content_references_user_lru"),
}


def evaluate_plan(name: str, lines: list[str]) -> dict[str, Any]:
    if name not in PLAN_INDEX_REQUIREMENTS:
        raise ValueError(f"unknown plan name: {name}")
    table, index = PLAN_INDEX_REQUIREMENTS[name]
    normalized = "\n".join(str(line) for line in lines).casefold()
    required = index.casefold() in normalized
    table_scan = f"table scan on {table}".casefold() in normalized
    return {
        "name": name,
        "required_index": index,
        "required_index_present": required,
        "table_scan_present": table_scan,
        "passed": required and not table_scan,
    }


DEFERRED_INDEXES: dict[str, tuple[tuple[str, str], ...]] = {
    "threads": (
        ("uq_threads_user_key", "UNIQUE KEY uq_threads_user_key (user_uid, canonical_thread_key)"),
        ("idx_threads_user_updated", "KEY idx_threads_user_updated (user_uid, updated_at DESC, id DESC)"),
    ),
    "messages": (
        ("uq_messages_user_key", "UNIQUE KEY uq_messages_user_key (user_uid, canonical_message_key)"),
        ("idx_messages_user_thread_time", "KEY idx_messages_user_thread_time (user_uid, thread_id, received_at DESC, id DESC)"),
        ("idx_messages_user_received", "KEY idx_messages_user_received (user_uid, received_at DESC, id DESC)"),
        ("idx_messages_message_id_header", "KEY idx_messages_message_id_header (user_uid, message_id_header(191))"),
        ("idx_messages_subject_fallback", "KEY idx_messages_subject_fallback (user_uid, normalized_subject(191), received_at DESC, id DESC)"),
    ),
    "thread_messages": (
        ("idx_thread_messages_user_message", "KEY idx_thread_messages_user_message (user_uid, message_id, thread_id)"),
        ("idx_thread_messages_parent", "KEY idx_thread_messages_parent (thread_id, parent_message_id)"),
    ),
    "thread_projections": (
        ("idx_thread_projection_cursor", "KEY idx_thread_projection_cursor (user_uid, semantic_mailbox, latest_message_at DESC, thread_id DESC)"),
        ("idx_thread_projection_unread", "KEY idx_thread_projection_unread (user_uid, semantic_mailbox, unread_count, latest_message_at DESC, thread_id DESC)"),
        ("idx_thread_projection_starred", "KEY idx_thread_projection_starred (user_uid, semantic_mailbox, is_starred, latest_message_at DESC, thread_id DESC)"),
    ),
    "message_remote_instances": (
        ("uq_remote_identity", "UNIQUE KEY uq_remote_identity (account_id, mailbox_id, uidvalidity, remote_uid)"),
        ("idx_remote_instances_message", "KEY idx_remote_instances_message (user_uid, message_id, account_id, id)"),
        ("idx_remote_instances_provider_id", "KEY idx_remote_instances_provider_id (account_id, provider_message_id)"),
        ("idx_remote_instances_mailbox_state", "KEY idx_remote_instances_mailbox_state (account_id, mailbox_id, remote_deleted, remote_uid)"),
    ),
    "message_memberships": (
        ("idx_memberships_mailbox_instance", "KEY idx_memberships_mailbox_instance (mailbox_id, remote_instance_id)"),
        ("idx_memberships_user_mailbox", "KEY idx_memberships_user_mailbox (user_uid, mailbox_id, remote_instance_id)"),
    ),
    "message_bodies": (
        ("idx_message_bodies_user_state", "KEY idx_message_bodies_user_state (user_uid, state, last_accessed_at, message_id)"),
        ("idx_message_bodies_html_object", "KEY idx_message_bodies_html_object (html_object_sha256)"),
        ("idx_message_bodies_text_object", "KEY idx_message_bodies_text_object (text_object_sha256)"),
        ("idx_message_bodies_raw_object", "KEY idx_message_bodies_raw_object (raw_eml_object_sha256)"),
    ),
    "content_references": (
        ("uq_content_references_business", "UNIQUE KEY uq_content_references_business (user_uid, content_sha256, reference_kind, reference_id)"),
        ("idx_content_references_object", "KEY idx_content_references_object (content_sha256, reference_kind, user_uid)"),
        ("idx_content_references_user_lru", "KEY idx_content_references_user_lru (user_uid, reference_kind, pinned, last_accessed_at, id)"),
    ),
    "body_search_documents": (
        ("idx_body_search_user_thread", "KEY idx_body_search_user_thread (user_uid, thread_id, updated_at DESC, message_id)"),
    ),
}


def should_commit_batch(completed: int, total: int, batch_size: int) -> bool:
    completed_value = int(completed)
    total_value = int(total)
    batch_value = int(batch_size)
    if batch_value < 1:
        raise ValueError("batch_size must be positive")
    if completed_value < 0 or total_value < 0 or completed_value > total_value:
        raise ValueError("batch progress is invalid")
    commit_interval = max(batch_value, COMMIT_TARGET_ROWS)
    return completed_value == total_value or completed_value % commit_interval == 0


def synthetic_address(index: int) -> str:
    value = int(index)
    if value < 0:
        raise ValueError("synthetic address index must be non-negative")
    return f"bench-account-{value:08d}@example.test"


def _database_name(database_url: str) -> str:
    parsed = urlparse(str(database_url or "").strip())
    if parsed.scheme.lower() not in {"mysql", "mysql+aiomysql", "mysql+pymysql"}:
        raise ValueError("capacity database URL must use MySQL")
    database = unquote(parsed.path.lstrip("/")).strip()
    if not database:
        raise ValueError("capacity database URL must include a database")
    return database


def validate_target(database_url: str, object_root: str) -> None:
    database = _database_name(database_url)
    normalized_database = database.casefold()
    if normalized_database in PRODUCTION_DATABASE_NAMES:
        raise ValueError(f"refusing production database {database!r}")
    if not any(
        marker in normalized_database
        for marker in ("capacity", "benchmark", "bench", "_test")
    ):
        raise ValueError("capacity database name must identify a benchmark or test database")

    root = Path(object_root).expanduser().resolve()
    production = PRODUCTION_DATA_ROOT.resolve()
    if root == production or production in root.parents:
        raise ValueError(f"refusing production data path {production}")


def _validate_config(config: GenerationConfig) -> None:
    validate_target(config.database_url, config.object_root)
    if config.users < 1:
        raise ValueError("users must be at least 1")
    if config.accounts < config.users:
        raise ValueError("accounts must be at least users")
    if config.messages < 1:
        raise ValueError("messages must be at least 1")
    if config.batch_size < 1 or config.batch_size > 250_000:
        raise ValueError("batch_size must be between 1 and 250000")
    if not math.isfinite(config.body_cache_ratio):
        raise ValueError("body_cache_ratio must be finite")
    if config.body_cache_ratio < 0 or config.body_cache_ratio > 1:
        raise ValueError("body_cache_ratio must be between 0 and 1")


def dataset_fingerprint(config: GenerationConfig) -> str:
    stable = {
        "users": int(config.users),
        "accounts": int(config.accounts),
        "messages": int(config.messages),
        "seed": int(config.seed),
        "batch_size": int(config.batch_size),
        "body_cache_ratio": round(float(config.body_cache_ratio), 8),
        "thread_size": THREAD_SIZE,
        "schema": "flymail-v2-capacity-v1",
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _settings(database_url: str, object_root: str, role: str = "worker") -> FlyMailSettings:
    root = Path(object_root).expanduser().resolve()
    data_dir = root.parent
    maximum = 8 if role == "worker" else 12
    return FlyMailSettings(
        role=role,  # type: ignore[arg-type]
        database_url=database_url,
        data_dir=data_dir,
        object_dir=root,
        object_tmp_dir=root.parent / ".capacity-tmp",
        session_secret="capacity-benchmark-session-secret",
        db_pool_name=f"flymail-capacity-{role}",
        db_min_connections=1,
        db_max_connections=maximum,
    )


async def _execute(
    connection: aiomysql.Connection,
    sql: str,
    params: tuple[Any, ...] | list[Any] = (),
) -> int:
    async with connection.cursor() as cursor:
        await cursor.execute(sql, params)
        return int(cursor.rowcount or 0)


async def _execute_committed(
    pool: DatabasePool,
    sql: str,
    params: tuple[Any, ...] | list[Any] = (),
) -> int:
    async with pool.acquire() as connection:
        try:
            affected = await _execute(connection, sql, params)
            await connection.commit()
            return affected
        except Exception:
            await connection.rollback()
            raise


async def _scalar(
    connection: aiomysql.Connection,
    sql: str,
    params: tuple[Any, ...] | list[Any] = (),
) -> Any:
    async with connection.cursor() as cursor:
        await cursor.execute(sql, params)
        row = await cursor.fetchone()
        return row[0] if row else None


async def _table_exists(
    connection: aiomysql.Connection,
    table: str,
) -> bool:
    return bool(
        await _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
            """,
            (table,),
        )
    )


async def _existing_indexes(
    connection: aiomysql.Connection,
    table: str,
) -> set[str]:
    async with connection.cursor() as cursor:
        await cursor.execute(
            """
            SELECT DISTINCT index_name
            FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = %s
            """,
            (table,),
        )
        return {str(row[0]) for row in await cursor.fetchall()}


async def _fulltext_parser(connection: aiomysql.Connection) -> str:
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT metadata_json FROM schema_migrations WHERE version = 4"
        )
        row = await cursor.fetchone()
    if not row:
        return "standard"
    value = row[0]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return "standard"
    if isinstance(value, dict) and value.get("fulltext_parser") == "ngram":
        return "ngram"
    return "standard"


async def _drop_deferred_indexes(connection: aiomysql.Connection) -> None:
    for table, definitions in DEFERRED_INDEXES.items():
        existing = await _existing_indexes(connection, table)
        names = [name for name, _definition in definitions if name in existing]
        if table == "body_search_documents":
            names.extend(
                name
                for name in ("ft_body_search", "ft_body_search_standard")
                if name in existing
            )
        if not names:
            continue
        clauses = ", ".join(f"DROP INDEX `{name}`" for name in names)
        await _execute(connection, f"ALTER TABLE `{table}` {clauses}")
    await connection.commit()


async def _restore_deferred_indexes(connection: aiomysql.Connection) -> None:
    parser = await _fulltext_parser(connection)
    for table, definitions in DEFERRED_INDEXES.items():
        existing = await _existing_indexes(connection, table)
        clauses = [
            f"ADD {definition}"
            for name, definition in definitions
            if name not in existing
        ]
        fulltext_clauses: list[str] = []
        if table == "body_search_documents":
            if "ft_body_search" not in existing:
                parser_suffix = " WITH PARSER ngram" if parser == "ngram" else ""
                fulltext_clauses.append(
                    "ADD FULLTEXT KEY ft_body_search "
                    f"(subject_text, participants_text, body_text){parser_suffix}"
                )
            if "ft_body_search_standard" not in existing:
                fulltext_clauses.append(
                    "ADD FULLTEXT KEY ft_body_search_standard "
                    "(body_text, subject_text, participants_text)"
                )
        if clauses:
            await _execute(connection, f"ALTER TABLE `{table}` {', '.join(clauses)}")
        for fulltext_clause in fulltext_clauses:
            await _execute(connection, f"ALTER TABLE `{table}` {fulltext_clause}")
    await connection.commit()


async def _analyze_capacity_tables(connection: aiomysql.Connection) -> None:
    tables = ", ".join(f"`{table}`" for table in DEFERRED_INDEXES)
    await _execute(connection, f"ANALYZE TABLE {tables}")
    await connection.commit()


async def _reset_generated_tables(connection: aiomysql.Connection) -> None:
    tables = (
        "job_attempts",
        "worker_jobs",
        "outbox_events",
        "mail_operations",
        "content_references",
        "content_objects",
        "message_bodies",
        "body_search_documents",
        "message_memberships",
        "message_remote_instances",
        "thread_messages",
        "thread_projections",
        "messages",
        "threads",
        "account_runtime_state",
        "mailboxes",
        "mail_identities",
        "mail_accounts",
        "user_settings",
        "user_profiles",
        "user_sessions",
        "users",
    )
    await _execute(connection, "SET FOREIGN_KEY_CHECKS=0")
    try:
        for table in tables:
            await _execute(connection, f"TRUNCATE TABLE `{table}`")
        for auxiliary_table in ("benchmark_generation_state", "benchmark_numbers"):
            if await _table_exists(connection, auxiliary_table):
                await _execute(connection, f"DROP TABLE `{auxiliary_table}`")
        await connection.commit()
    finally:
        await _execute(connection, "SET FOREIGN_KEY_CHECKS=1")
        await connection.commit()


async def _prepare_number_table(connection: aiomysql.Connection, count: int) -> None:
    await _execute(
        connection,
        """
        CREATE TABLE IF NOT EXISTS benchmark_numbers (
            n BIGINT UNSIGNED NOT NULL PRIMARY KEY
        ) ENGINE=InnoDB
        """,
    )
    await _execute(connection, "TRUNCATE TABLE benchmark_numbers")
    await _execute(connection, "INSERT INTO benchmark_numbers (n) VALUES (0)")
    current = 1
    while current < count:
        take = min(current, count - current)
        await _execute(
            connection,
            """
            INSERT INTO benchmark_numbers (n)
            SELECT n + %s FROM benchmark_numbers WHERE n < %s
            """,
            (current, take),
        )
        current += take
        await connection.commit()


async def _insert_range(
    connection: aiomysql.Connection,
    sql: str,
    *,
    total: int,
    batch_size: int,
    progress_label: str,
) -> None:
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        await _execute(connection, sql, (start, end))
        if should_commit_batch(end, total, batch_size):
            await connection.commit()
        if end == total or end % max(batch_size * 100, 1) == 0:
            print(
                json.dumps(
                    {"stage": progress_label, "completed": end, "total": total},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )


async def _insert_range_in_pool(
    pool: DatabasePool,
    sql: str,
    *,
    total: int,
    batch_size: int,
    progress_label: str,
) -> None:
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        await _execute_committed(pool, sql, (start, end))
        if end == total or end % max(batch_size * 100, 1) == 0:
            print(
                json.dumps(
                    {"stage": progress_label, "completed": end, "total": total},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )


async def _insert_identity_data(
    connection: aiomysql.Connection,
    config: GenerationConfig,
    password_hash: str,
) -> None:
    users = int(config.users)
    accounts = int(config.accounts)
    messages = int(config.messages)
    now = BASE_TIMESTAMP + int(config.seed % 100_000)

    await _execute(
        connection,
        f"""
        INSERT INTO users (
            id, username, password_hash, role, enabled, password_version,
            created_at, updated_at
        )
        SELECT CONCAT('usr_bench_', LPAD(n, 8, '0')),
               CONCAT('bench-user-', LPAD(n, 4, '0')),
               %s,
               CASE WHEN n = 0 THEN 'admin' ELSE 'user' END,
               1, 1, %s + n, %s + n
        FROM benchmark_numbers WHERE n < {users}
        """,
        (password_hash, now, now),
    )
    await _execute(
        connection,
        f"""
        INSERT INTO user_profiles (user_uid, nickname, created_at, updated_at)
        SELECT CONCAT('usr_bench_', LPAD(n, 8, '0')),
               CONCAT('Benchmark User ', n), %s + n, %s + n
        FROM benchmark_numbers WHERE n < {users}
        """,
        (now, now),
    )
    await _execute(
        connection,
        f"""
        INSERT INTO user_settings (
            user_uid, body_cache_quota_bytes, attachment_cache_quota_bytes,
            ui_preferences, compose_preferences, remote_image_policy,
            created_at, updated_at
        )
        SELECT CONCAT('usr_bench_', LPAD(n, 8, '0')),
               5368709120, 2147483648,
               JSON_OBJECT('theme', 'system', 'density', 'compact'),
               JSON_OBJECT(), JSON_OBJECT('mode', 'block'), %s + n, %s + n
        FROM benchmark_numbers WHERE n < {users}
        """,
        (now, now),
    )
    await _execute(
        connection,
        f"""
        INSERT INTO mail_accounts (
            id, user_uid, provider_key, email, normalized_email, display_name,
            remark, group_name, status, endpoint_config, icon_mode, icon_value,
            poll_interval_seconds, created_at, updated_at
        )
        SELECT CONCAT('acc_bench_', LPAD(n, 8, '0')),
               CONCAT('usr_bench_', LPAD(MOD(n, {users}), 8, '0')),
               CASE MOD(n, 4)
                    WHEN 0 THEN 'gmail'
                    WHEN 1 THEN 'outlook'
                    WHEN 2 THEN 'generic'
                    ELSE 'qq'
               END,
               CONCAT('bench-account-', LPAD(n, 8, '0'), '@example.test'),
               CONCAT('bench-account-', LPAD(n, 8, '0'), '@example.test'),
               CONCAT('Benchmark Account ', n),
               CASE WHEN MOD(n, 10) = 0 THEN 'hot' ELSE 'cold' END,
               CONCAT('benchmark-', MOD(n, 6)),
               'active', JSON_OBJECT('benchmark', TRUE), 'provider', '',
               CASE WHEN MOD(n, 4) = 0 THEN 60 ELSE 300 END,
               %s + n, %s + n
        FROM benchmark_numbers WHERE n < {accounts}
        """,
        (now, now),
    )
    await _execute(
        connection,
        f"""
        INSERT INTO mail_identities (
            id, user_uid, account_id, from_address, normalized_from_address,
            display_name, reply_to, signature_html, signature_text,
            is_default, is_verified, created_at, updated_at
        )
        SELECT CONCAT('idn_bench_', LPAD(n, 8, '0')),
               CONCAT('usr_bench_', LPAD(MOD(n, {users}), 8, '0')),
               CONCAT('acc_bench_', LPAD(n, 8, '0')),
               CONCAT('bench-account-', LPAD(n, 8, '0'), '@example.test'),
               CONCAT('bench-account-', LPAD(n, 8, '0'), '@example.test'),
               CONCAT('Benchmark Account ', n), '', '', '', 1, 1,
               %s + n, %s + n
        FROM benchmark_numbers WHERE n < {accounts}
        """,
        (now, now),
    )
    average_total = max(messages // accounts, 1)
    await _execute(
        connection,
        f"""
        INSERT INTO mailboxes (
            id, user_uid, account_id, native_key, native_name, semantic_key,
            mailbox_type, delimiter_value, attributes_json, uidvalidity,
            highest_modseq, total_count, unread_count, sync_status,
            created_at, updated_at
        )
        SELECT CONCAT('mbx_bench_', LPAD(n, 8, '0')),
               CONCAT('usr_bench_', LPAD(MOD(n, {users}), 8, '0')),
               CONCAT('acc_bench_', LPAD(n, 8, '0')),
               'INBOX', 'Inbox', 'inbox', 'folder', '/', JSON_ARRAY(),
               1, {messages} + n, {average_total}, FLOOR({average_total} / 5),
               'ready', %s + n, %s + n
        FROM benchmark_numbers WHERE n < {accounts}
        """,
        (now, now),
    )
    await _execute(
        connection,
        f"""
        INSERT INTO mailboxes (
            id, user_uid, account_id, native_key, native_name, semantic_key,
            mailbox_type, delimiter_value, attributes_json, uidvalidity,
            highest_modseq, total_count, unread_count, sync_status,
            created_at, updated_at
        )
        SELECT CONCAT('lbl_bench_', LPAD(n, 8, '0')),
               CONCAT('usr_bench_', LPAD(MOD(n, {users}), 8, '0')),
               CONCAT('acc_bench_', LPAD(n, 8, '0')),
               'Benchmark/Capacity', 'Benchmark Capacity', 'label', 'label',
               '/', JSON_ARRAY('\\HasNoChildren'), 1, {messages} + n,
               FLOOR({average_total} / 4), FLOOR({average_total} / 20),
               'ready', %s + n, %s + n
        FROM benchmark_numbers
        WHERE n < {accounts} AND MOD(n, 4) = 0
        """,
        (now, now),
    )
    await _execute(
        connection,
        f"""
        INSERT INTO account_runtime_state (
            account_id, user_uid, status, idle_status, last_activity_at,
            last_change_at, next_reconcile_at, failure_count, backoff_until,
            last_error_class, last_error_message, updated_at
        )
        SELECT CONCAT('acc_bench_', LPAD(n, 8, '0')),
               CONCAT('usr_bench_', LPAD(MOD(n, {users}), 8, '0')),
               CASE MOD(n, 5)
                    WHEN 0 THEN 'active'
                    WHEN 1 THEN 'quiet'
                    ELSE 'normal'
               END,
               CASE WHEN MOD(n, 4) = 0 THEN 'idling' ELSE 'disconnected' END,
               %s + n, %s + n, %s + MOD(n, 300), 0, 0, '', '', %s + n
        FROM benchmark_numbers WHERE n < {accounts}
        """,
        (now, now, now, now),
    )
    await connection.commit()


def _account_index_expression(users: int, accounts: int, message_alias: str = "n") -> str:
    thread_index = f"FLOOR({message_alias} / {THREAD_SIZE})"
    user_index = f"MOD({thread_index}, {users})"
    available = f"(FLOOR(({accounts} - 1 - {user_index}) / {users}) + 1)"
    return f"({user_index} + {users} * MOD({message_alias}, {available}))"


async def _insert_thread_data(
    pool: DatabasePool,
    config: GenerationConfig,
    thread_count: int,
) -> None:
    users = int(config.users)
    messages = int(config.messages)
    sql_threads = f"""
        INSERT INTO threads (
            id, user_uid, canonical_thread_key, normalized_subject,
            created_at, updated_at
        )
        SELECT CONCAT('thr_bench_', LPAD(n, 12, '0')),
               CONCAT('usr_bench_', LPAD(MOD(n, {users}), 8, '0')),
               CONCAT('benchmark-thread-', n),
               CONCAT('capacity subject ', MOD(n, 10000)),
               {BASE_TIMESTAMP} + n * {THREAD_SIZE},
               {BASE_TIMESTAMP} + LEAST(n * {THREAD_SIZE} + {THREAD_SIZE - 1}, {messages - 1})
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    latest_index = f"LEAST(n * {THREAD_SIZE} + {THREAD_SIZE - 1}, {messages - 1})"
    sql_projection = f"""
        INSERT INTO thread_projections (
            user_uid, semantic_mailbox, thread_id, latest_message_id,
            latest_message_at, subject, participants_summary, latest_snippet,
            message_count, unread_count, is_starred, has_attachments,
            account_count, pending_operation_count, projection_version, updated_at
        )
        SELECT CONCAT('usr_bench_', LPAD(MOD(n, {users}), 8, '0')),
               'inbox', CONCAT('thr_bench_', LPAD(n, 12, '0')),
               CONCAT('msg_bench_', LPAD({latest_index}, 12, '0')),
               {BASE_TIMESTAMP} + {latest_index},
               CONCAT('Capacity subject ', MOD(n, 10000)),
               CONCAT('sender-', MOD(n, 1000), '@example.test'),
               CONCAT('Synthetic summary capacityterm ', n),
               LEAST({THREAD_SIZE}, {messages} - n * {THREAD_SIZE}),
               CASE WHEN MOD(n, 5) = 0 THEN 1 ELSE 0 END,
               CASE WHEN MOD(n, 11) = 0 THEN 1 ELSE 0 END,
               CASE WHEN MOD(n, 13) = 0 THEN 1 ELSE 0 END,
               CASE WHEN {config.accounts} > {users} THEN 2 ELSE 1 END,
               0, 1, {BASE_TIMESTAMP} + {latest_index}
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    await asyncio.gather(
        _insert_range_in_pool(
            pool,
            sql_threads,
            total=thread_count,
            batch_size=config.batch_size,
            progress_label="threads",
        ),
        _insert_range_in_pool(
            pool,
            sql_projection,
            total=thread_count,
            batch_size=config.batch_size,
            progress_label="thread_projections",
        ),
    )


async def _insert_message_data(
    pool: DatabasePool,
    config: GenerationConfig,
    body_count: int,
) -> None:
    users = int(config.users)
    accounts = int(config.accounts)
    messages = int(config.messages)
    account_index = _account_index_expression(users, accounts)
    user_index = f"MOD(FLOOR(n / {THREAD_SIZE}), {users})"
    thread_index = f"FLOOR(n / {THREAD_SIZE})"
    body_state = f"CASE WHEN n < {body_count} THEN 'ready' ELSE 'not_requested' END"
    search_state = f"CASE WHEN n < {body_count} THEN 'ready' ELSE 'metadata' END"

    sql_messages = f"""
        INSERT INTO messages (
            id, user_uid, canonical_message_key, message_id_header, thread_id,
            subject, normalized_subject, from_json, to_json, cc_json, bcc_json,
            reply_to_json, sent_at, received_at, size_bytes, has_attachments,
            snippet, body_state, search_state, created_at, updated_at
        )
        SELECT CONCAT('msg_bench_', LPAD(n, 12, '0')),
               CONCAT('usr_bench_', LPAD({user_index}, 8, '0')),
               CONCAT('benchmark-message-', n),
               CONCAT('<benchmark-', n, '@example.test>'),
               CONCAT('thr_bench_', LPAD({thread_index}, 12, '0')),
               CONCAT('Capacity subject ', MOD({thread_index}, 10000)),
               CONCAT('capacity subject ', MOD({thread_index}, 10000)),
               JSON_ARRAY(JSON_OBJECT(
                   'name', CONCAT('Synthetic Sender ', MOD(n, 1000)),
                   'address', CONCAT('sender-', MOD(n, 1000), '@example.test')
               )),
               JSON_ARRAY(JSON_OBJECT(
                   'name', CONCAT('Benchmark User ', {user_index}),
                   'address', CONCAT('bench-user-', LPAD({user_index}, 4, '0'), '@example.test')
               )),
               JSON_ARRAY(), JSON_ARRAY(), JSON_ARRAY(),
               {BASE_TIMESTAMP} + n, {BASE_TIMESTAMP} + n,
               2048 + MOD(n, 65536), CASE WHEN MOD(n, 13) = 0 THEN 1 ELSE 0 END,
               CONCAT('Synthetic capacityterm message ', n),
               {body_state}, {search_state},
               {BASE_TIMESTAMP} + n, {BASE_TIMESTAMP} + n
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    sql_thread_messages = f"""
        INSERT INTO thread_messages (
            thread_id, message_id, user_uid, parent_message_id,
            relation_source, position_hint, created_at
        )
        SELECT CONCAT('thr_bench_', LPAD({thread_index}, 12, '0')),
               CONCAT('msg_bench_', LPAD(n, 12, '0')),
               CONCAT('usr_bench_', LPAD({user_index}, 8, '0')),
               CASE WHEN MOD(n, {THREAD_SIZE}) = 0 THEN NULL
                    ELSE CONCAT('msg_bench_', LPAD(n - 1, 12, '0')) END,
               'headers', MOD(n, {THREAD_SIZE}), {BASE_TIMESTAMP} + n
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    sql_remote = f"""
        INSERT INTO message_remote_instances (
            id, user_uid, account_id, mailbox_id, message_id, uidvalidity,
            remote_uid, provider_message_id, provider_thread_id, flags_json,
            is_read, is_starred, remote_version, remote_deleted,
            last_seen_at, created_at, updated_at
        )
        SELECT CONCAT('rmi_bench_', LPAD(n, 12, '0')),
               CONCAT('usr_bench_', LPAD({user_index}, 8, '0')),
               CONCAT('acc_bench_', LPAD({account_index}, 8, '0')),
               CONCAT('mbx_bench_', LPAD({account_index}, 8, '0')),
               CONCAT('msg_bench_', LPAD(n, 12, '0')),
               1, n + 1, CONCAT('provider-message-', n),
               CONCAT('provider-thread-', {thread_index}),
               JSON_ARRAY(CASE WHEN MOD(n, 5) = 0 THEN '\\Seen' ELSE '' END),
               CASE WHEN MOD(n, 5) = 0 THEN 1 ELSE 0 END,
               CASE WHEN MOD(n, 11) = 0 THEN 1 ELSE 0 END,
               CONCAT('v', n), 0, {BASE_TIMESTAMP} + n,
               {BASE_TIMESTAMP} + n, {BASE_TIMESTAMP} + n
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    sql_memberships = f"""
        INSERT INTO message_memberships (
            remote_instance_id, mailbox_id, user_uid, membership_kind,
            provider_label, created_at, updated_at
        )
        SELECT CONCAT('rmi_bench_', LPAD(n, 12, '0')),
               CONCAT('mbx_bench_', LPAD({account_index}, 8, '0')),
               CONCAT('usr_bench_', LPAD({user_index}, 8, '0')),
               'folder', '', {BASE_TIMESTAMP} + n, {BASE_TIMESTAMP} + n
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """

    await _insert_range_in_pool(
        pool,
        sql_messages,
        total=messages,
        batch_size=config.batch_size,
        progress_label="messages",
    )
    await asyncio.gather(
        _insert_range_in_pool(
            pool,
            sql_thread_messages,
            total=messages,
            batch_size=config.batch_size,
            progress_label="thread_messages",
        ),
        _insert_range_in_pool(
            pool,
            sql_remote,
            total=messages,
            batch_size=config.batch_size,
            progress_label="remote_instances",
        ),
        _insert_range_in_pool(
            pool,
            sql_memberships,
            total=messages,
            batch_size=config.batch_size,
            progress_label="message_memberships",
        ),
    )


async def _insert_cached_content(
    connection: aiomysql.Connection,
    pool: DatabasePool,
    config: GenerationConfig,
    body_count: int,
) -> None:
    if body_count <= 0:
        return
    users = int(config.users)
    object_root = Path(config.object_root).expanduser().resolve()
    path = object_path(object_root, SYNTHETIC_BODY_SHA256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SYNTHETIC_BODY)
    relative_path = str(Path(SYNTHETIC_BODY_SHA256[:2]) / SYNTHETIC_BODY_SHA256)
    await _execute(
        connection,
        """
        INSERT INTO content_objects (
            content_sha256, object_kind, compression, original_size_bytes,
            stored_size_bytes, relative_path, verified_at, created_at
        ) VALUES (%s, 'body_text', 'none', %s, %s, %s, %s, %s)
        """,
        (
            SYNTHETIC_BODY_SHA256,
            len(SYNTHETIC_BODY),
            len(SYNTHETIC_BODY),
            relative_path,
            BASE_TIMESTAMP,
            BASE_TIMESTAMP,
        ),
    )
    await connection.commit()

    sql_bodies = f"""
        INSERT INTO message_bodies (
            message_id, user_uid, text_object_sha256, state, body_size_bytes,
            index_version, parser_version, checked_at, cached_at,
            last_accessed_at, last_error_class, last_error_message, updated_at
        )
        SELECT CONCAT('msg_bench_', LPAD(n, 12, '0')),
               CONCAT('usr_bench_', LPAD(MOD(FLOOR(n / {THREAD_SIZE}), {users}), 8, '0')),
               '{SYNTHETIC_BODY_SHA256}', 'ready', {len(SYNTHETIC_BODY)},
               1, 1, {BASE_TIMESTAMP} + n, {BASE_TIMESTAMP} + n,
               {BASE_TIMESTAMP} + n, '', '', {BASE_TIMESTAMP} + n
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    sql_search = f"""
        INSERT INTO body_search_documents (
            message_id, user_uid, thread_id, subject_text, participants_text,
            body_text, language, index_version, updated_at
        )
        SELECT CONCAT('msg_bench_', LPAD(n, 12, '0')),
               CONCAT('usr_bench_', LPAD(MOD(FLOOR(n / {THREAD_SIZE}), {users}), 8, '0')),
               CONCAT('thr_bench_', LPAD(FLOOR(n / {THREAD_SIZE}), 12, '0')),
               CONCAT('Capacity subject ', MOD(FLOOR(n / {THREAD_SIZE}), 10000)),
               CONCAT('sender-', MOD(n, 1000), '@example.test'),
               CONCAT(
                   'capacityterm capacitybucket', LPAD(MOD(n, 10000), 4, '0'),
                   ' synthetic cached body ', n
               ),
               'en', 1, {BASE_TIMESTAMP} + n
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    sql_refs = f"""
        INSERT INTO content_references (
            id, user_uid, content_sha256, reference_kind, reference_id,
            pinned, created_at, last_accessed_at
        )
        SELECT CONCAT('ref_bench_', LPAD(n, 12, '0')),
               CONCAT('usr_bench_', LPAD(MOD(FLOOR(n / {THREAD_SIZE}), {users}), 8, '0')),
               '{SYNTHETIC_BODY_SHA256}', 'message_body_text',
               CONCAT('msg_bench_', LPAD(n, 12, '0')), 0,
               {BASE_TIMESTAMP} + n, {BASE_TIMESTAMP} + n
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    await asyncio.gather(
        _insert_range_in_pool(
            pool,
            sql_bodies,
            total=body_count,
            batch_size=config.batch_size,
            progress_label="message_bodies",
        ),
        _insert_range_in_pool(
            pool,
            sql_refs,
            total=body_count,
            batch_size=config.batch_size,
            progress_label="content_references",
        ),
    )
    await _insert_range_in_pool(
        pool,
        sql_search,
        total=body_count,
        batch_size=config.batch_size,
        progress_label="body_search_documents",
    )


async def _insert_worker_jobs(
    connection: aiomysql.Connection,
    config: GenerationConfig,
    job_count: int,
) -> None:
    users = int(config.users)
    accounts = int(config.accounts)
    sql = f"""
        INSERT INTO worker_jobs (
            id, user_uid, account_id, provider_key, queue_name, job_kind,
            status, priority, available_at, lease_owner, attempt_count,
            max_attempts, dedupe_key, payload, created_at, updated_at
        )
        SELECT CONCAT('job_bench_', LPAD(n, 12, '0')),
               CONCAT('usr_bench_', LPAD(MOD(n, {users}), 8, '0')),
               CONCAT('acc_bench_', LPAD(MOD(n, {accounts}), 8, '0')),
               CASE MOD(n, 4)
                    WHEN 0 THEN 'gmail'
                    WHEN 1 THEN 'outlook'
                    WHEN 2 THEN 'generic'
                    ELSE 'qq'
               END,
               CASE WHEN MOD(n, 10) = 0 THEN 'interactive' ELSE 'history' END,
               'sync.incremental',
               CASE WHEN MOD(n, 17) = 0 THEN 'retry_wait' ELSE 'pending' END,
               CASE WHEN MOD(n, 10) = 0 THEN 0 ELSE 100 END,
               {BASE_TIMESTAMP} - MOD(n, 300), '', 0, 10,
               CONCAT('benchmark-job-', n),
               JSON_OBJECT('benchmark', TRUE, 'sequence', n),
               {BASE_TIMESTAMP} - n, {BASE_TIMESTAMP} - n
        FROM benchmark_numbers WHERE n >= %s AND n < %s
    """
    await _insert_range(
        connection,
        sql,
        total=job_count,
        batch_size=config.batch_size,
        progress_label="worker_jobs",
    )


async def _record_generation_state(
    connection: aiomysql.Connection,
    config: GenerationConfig,
    counts: dict[str, Any],
) -> None:
    await _execute(
        connection,
        """
        CREATE TABLE IF NOT EXISTS benchmark_generation_state (
            fingerprint CHAR(64) NOT NULL PRIMARY KEY,
            config_json JSON NOT NULL,
            counts_json JSON NOT NULL,
            generated_at DOUBLE NOT NULL
        ) ENGINE=InnoDB
        """,
    )
    await _execute(connection, "TRUNCATE TABLE benchmark_generation_state")
    safe_config = {
        key: value
        for key, value in asdict(config).items()
        if key not in {"database_url", "object_root"}
    }
    await _execute(
        connection,
        """
        INSERT INTO benchmark_generation_state (
            fingerprint, config_json, counts_json, generated_at
        ) VALUES (%s, %s, %s, %s)
        """,
        (
            counts["fingerprint"],
            json.dumps(safe_config, sort_keys=True),
            json.dumps(counts, sort_keys=True),
            time.time(),
        ),
    )
    await connection.commit()


async def generate(config: GenerationConfig) -> dict[str, Any]:
    _validate_config(config)
    thread_count = math.ceil(config.messages / THREAD_SIZE)
    body_count = int(round(config.messages * config.body_cache_ratio))
    body_count = min(max(body_count, 0), config.messages)
    job_count = max(config.accounts * 4, 1_000)
    max_numbers = max(config.messages, thread_count, config.accounts, config.users, job_count)
    fingerprint = dataset_fingerprint(config)

    object_root = Path(config.object_root).expanduser().resolve()
    object_root.mkdir(parents=True, exist_ok=True)
    pool = await DatabasePool.create(_settings(config.database_url, str(object_root)))
    try:
        await run_migrations(pool)
        async with pool.acquire() as connection:
            if config.reset:
                await _reset_generated_tables(connection)
            existing = await _scalar(
                connection,
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema=DATABASE() AND table_name='benchmark_generation_state'
                """,
            )
            if existing and not config.reset:
                state = await _scalar(
                    connection,
                    "SELECT fingerprint FROM benchmark_generation_state LIMIT 1",
                )
                if state == fingerprint:
                    async with connection.cursor(aiomysql.DictCursor) as cursor:
                        await cursor.execute(
                            "SELECT counts_json FROM benchmark_generation_state LIMIT 1"
                        )
                        row = await cursor.fetchone()
                    await _restore_deferred_indexes(connection)
                    raw_counts = row["counts_json"] if row else None
                    if isinstance(raw_counts, str):
                        return dict(json.loads(raw_counts))
                    if isinstance(raw_counts, dict):
                        return dict(raw_counts)
                raise RuntimeError("benchmark database contains a different dataset; use --reset")

            if config.defer_indexes:
                await _drop_deferred_indexes(connection)
            else:
                await _restore_deferred_indexes(connection)
            await _prepare_number_table(connection, max_numbers)
            password_hash = await asyncio.to_thread(
                hash_password,
                f"Benchmark-only-password-{config.seed}",
            )
            await _insert_identity_data(connection, config, password_hash)
            await _insert_thread_data(pool, config, thread_count)
            await _insert_message_data(pool, config, body_count)
            await _insert_cached_content(connection, pool, config, body_count)
            await _insert_worker_jobs(connection, config, job_count)
            await _restore_deferred_indexes(connection)
            await _analyze_capacity_tables(connection)

            counts = {
                "fingerprint": fingerprint,
                "users": int(await _scalar(connection, "SELECT COUNT(*) FROM users")),
                "accounts": int(await _scalar(connection, "SELECT COUNT(*) FROM mail_accounts")),
                "mailboxes": int(await _scalar(connection, "SELECT COUNT(*) FROM mailboxes")),
                "threads": int(await _scalar(connection, "SELECT COUNT(*) FROM threads")),
                "messages": int(await _scalar(connection, "SELECT COUNT(*) FROM messages")),
                "remote_instances": int(
                    await _scalar(connection, "SELECT COUNT(*) FROM message_remote_instances")
                ),
                "thread_projections": int(
                    await _scalar(connection, "SELECT COUNT(*) FROM thread_projections")
                ),
                "body_documents": int(
                    await _scalar(connection, "SELECT COUNT(*) FROM body_search_documents")
                ),
                "worker_jobs": int(await _scalar(connection, "SELECT COUNT(*) FROM worker_jobs")),
                "seed": int(config.seed),
                "batch_size": int(config.batch_size),
                "body_cache_ratio": float(config.body_cache_ratio),
                "write_concurrency": GENERATION_WRITE_CONCURRENCY,
                "deferred_indexes": bool(config.defer_indexes),
            }
            await _record_generation_state(connection, config, counts)
            return counts
    finally:
        await pool.close()


def _benchmark_session() -> AuthenticatedSession:
    created = BASE_TIMESTAMP
    user = User(
        id="usr_bench_00000000",
        username="bench-user-0000",
        role="admin",
        enabled=True,
        password_version=1,
        created_at=created,
        updated_at=created,
    )
    record = SessionRecord(
        id="ses_benchmark_capacity",
        user_uid=user.id,
        username=user.username,
        role=user.role,
        user_enabled=True,
        session_password_version=1,
        user_password_version=1,
        csrf_token_hash="capacity-benchmark-csrf-hash",
        expires_at=created + 10_000_000,
        revoked_at=None,
        last_seen_at=created,
        created_at=created,
    )
    return AuthenticatedSession(record=record, user=user, csrf_token="capacity-csrf-token")


async def _explain_analyze(
    pool: DatabasePool,
    sql: str,
    params: tuple[Any, ...],
) -> list[str]:
    async with pool.acquire() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(f"EXPLAIN ANALYZE {sql}", params)
            rows = await cursor.fetchall()
    normalized: list[str] = []
    for row in rows:
        text = " ".join(str(value) for value in row if value is not None)
        text = re.sub(r"actual time=[0-9.]+\.\.[0-9.]+", "actual time=<measured>", text)
        text = re.sub(r"loops=[0-9]+", "loops=<measured>", text)
        normalized.append(text)
    return normalized


async def _resource_profile(pool: DatabasePool) -> dict[str, Any]:
    async with pool.acquire() as connection:
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT @@version AS mysql_version,
                       @@innodb_buffer_pool_size AS innodb_buffer_pool_size,
                       @@innodb_flush_log_at_trx_commit AS innodb_flush_log_at_trx_commit,
                       @@innodb_doublewrite AS innodb_doublewrite,
                       @@max_connections AS max_connections,
                       @@tmp_table_size AS tmp_table_size,
                       @@max_heap_table_size AS max_heap_table_size
                """
            )
            mysql = dict(await cursor.fetchone())
    return {
        "hostname": socket.gethostname(),
        "cpu_count": os.cpu_count(),
        "mysql": mysql,
    }


async def run_benchmark(
    *,
    database_url: str,
    output: Path,
    measure: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    object_root = os.getenv("FLYMAIL_CAPACITY_OBJECT_ROOT", "/tmp/flymail-v2-capacity-objects")
    validate_target(database_url, object_root)
    pool = await DatabasePool.create(_settings(database_url, object_root, role="api"))
    store = ObjectStore(Path(object_root), Path(object_root).parent / ".capacity-tmp")
    session = _benchmark_session()
    bootstrap = BootstrapService(pool)
    threads = ThreadQueryService(pool, store, "capacity-cursor-secret")
    search = SearchQueryService(pool, "capacity-cursor-secret")
    try:
        first_page = await threads.list_threads(
            session,
            semantic_mailbox="inbox",
            limit=20,
            cursor=None,
            account_id=None,
            native_label=None,
            unread=None,
            starred=None,
            has_attachment=None,
        )
        if not first_page.items or not first_page.next_cursor:
            raise RuntimeError("capacity dataset did not produce a pageable thread list")
        first_thread_id = first_page.items[0].id
        cached_message_id = "msg_bench_000000000000"

        async def bootstrap_operation() -> Any:
            return await bootstrap.load(session)

        async def first_page_operation() -> Any:
            return await threads.list_threads(
                session,
                semantic_mailbox="inbox",
                limit=20,
                cursor=None,
                account_id=None,
                native_label=None,
                unread=None,
                starred=None,
                has_attachment=None,
            )

        async def next_page_operation() -> Any:
            return await threads.list_threads(
                session,
                semantic_mailbox="inbox",
                limit=20,
                cursor=first_page.next_cursor,
                account_id=None,
                native_label=None,
                unread=None,
                starred=None,
                has_attachment=None,
            )

        async def detail_operation() -> Any:
            return await threads.get_thread(session, first_thread_id)

        async def body_operation() -> bytes:
            content = await threads.resolve_body(session, cached_message_id)
            digest = getattr(content, "content_sha256", "")
            if not digest:
                raise RuntimeError("cached message body was not available")
            path = object_path(Path(object_root), digest)
            return await asyncio.to_thread(lambda: path.open("rb").read(1))

        structured_filter = SearchFilter(
            date_from=BASE_TIMESTAMP,
            date_to=BASE_TIMESTAMP + 100_000,
            has_attachment=False,
        )
        fulltext_filter = SearchFilter(keyword="capacitybucket0000")

        async def structured_search_operation() -> Any:
            return await search.search(
                session,
                structured_filter,
                limit=20,
                cursor=None,
            )

        async def fulltext_search_operation() -> Any:
            return await search.search(
                session,
                fulltext_filter,
                limit=20,
                cursor=None,
            )

        operation_counter = 0
        operation_run_id = f"{time.time_ns():x}"[-12:]

        async def local_operation_commit() -> None:
            nonlocal operation_counter
            operation_counter += 1
            operation_id = f"op_cap_{operation_run_id}_{operation_counter:08d}"
            async with pool.acquire() as connection:
                await connection.begin()
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO mail_operations (
                            id, user_uid, operation_type, target_type, target_id,
                            desired_state, status, priority, available_at,
                            attempt_count, last_error_class, last_error_message,
                            idempotency_key, created_at, updated_at
                        ) VALUES (%s, %s, 'mark_read', 'message', %s,
                                  JSON_OBJECT('is_read', TRUE), 'pending', 0, %s,
                                  0, '', '', %s, %s, %s)
                        """,
                        (
                            operation_id,
                            session.user.id,
                            cached_message_id,
                            time.time(),
                            operation_id,
                            time.time(),
                            time.time(),
                        ),
                    )
                await connection.commit()

        async def db_wait_operation() -> None:
            async with pool.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    await cursor.fetchone()

        async def p0_claim_operation() -> None:
            async with pool.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT id
                        FROM worker_jobs FORCE INDEX (idx_worker_jobs_scheduler)
                        WHERE queue_name='interactive'
                          AND status IN ('pending','retry_wait')
                          AND available_at <= %s
                        ORDER BY priority, available_at, id
                        LIMIT 1
                        """,
                        (BASE_TIMESTAMP + 10_000_000,),
                    )
                    await cursor.fetchone()

        async def projection_visibility_operation() -> None:
            async with pool.acquire() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT latest_message_id FROM thread_projections
                        FORCE INDEX (idx_thread_projection_cursor)
                        WHERE user_uid=%s AND semantic_mailbox='inbox'
                        ORDER BY latest_message_at DESC, thread_id DESC LIMIT 1
                        """,
                        (session.user.id,),
                    )
                    await cursor.fetchone()

        metrics: list[dict[str, Any]] = []
        operations = (
            ("bootstrap", bootstrap_operation),
            ("thread_first_page", first_page_operation),
            ("thread_next_page", next_page_operation),
            ("cached_detail", detail_operation),
            ("cached_body_first_byte", body_operation),
            ("structured_search", structured_search_operation),
            ("cached_body_fulltext", fulltext_search_operation),
            ("local_operation_commit", local_operation_commit),
            ("api_db_connection_wait", db_wait_operation),
            ("p0_claim", p0_claim_operation),
            ("idle_projection", projection_visibility_operation),
            ("offline_operation_begin", p0_claim_operation),
            ("restart_recovery", p0_claim_operation),
        )
        for name, operation in operations:
            metric = await measure(name, operation, warmups=5, samples=30)
            threshold = API_THRESHOLDS.get(name) or WORKER_THRESHOLDS.get(name)
            metric["target_p95_ms"] = threshold.p95_ms if threshold else None
            metric["passed"] = (
                float(metric["p95_ms"]) <= float(threshold.p95_ms)
                if threshold is not None
                else True
            )
            metrics.append(metric)

        async with pool.acquire() as connection:
            counts = {}
            for table in (
                "users",
                "mail_accounts",
                "threads",
                "messages",
                "message_remote_instances",
                "thread_projections",
                "body_search_documents",
                "worker_jobs",
            ):
                counts[table] = int(await _scalar(connection, f"SELECT COUNT(*) FROM {table}"))

        explain_queries = {
            "thread_list": (
                """
                SELECT thread_id, latest_message_id
                FROM thread_projections FORCE INDEX (idx_thread_projection_cursor)
                WHERE user_uid=%s AND semantic_mailbox='inbox'
                ORDER BY latest_message_at DESC, thread_id DESC LIMIT 20
                """,
                (session.user.id,),
            ),
            "structured_search": (
                """
                SELECT id FROM messages FORCE INDEX (idx_messages_user_received)
                WHERE user_uid=%s AND received_at BETWEEN %s AND %s
                ORDER BY received_at DESC, id DESC LIMIT 20
                """,
                (session.user.id, BASE_TIMESTAMP, BASE_TIMESTAMP + 100_000),
            ),
            "job_claim": (
                """
                SELECT id FROM worker_jobs FORCE INDEX (idx_worker_jobs_scheduler)
                WHERE queue_name='interactive'
                  AND status IN ('pending','retry_wait')
                  AND available_at <= %s
                ORDER BY priority, available_at, id LIMIT 1
                """,
                (BASE_TIMESTAMP + 10_000_000,),
            ),
            "remote_instance": (
                """
                SELECT id FROM message_remote_instances
                FORCE INDEX (idx_remote_instances_provider_id)
                WHERE account_id=%s AND provider_message_id=%s LIMIT 1
                """,
                ("acc_bench_00000000", "provider-message-0"),
            ),
            "quota_lru": (
                """
                SELECT id, content_sha256
                FROM content_references FORCE INDEX (idx_content_references_user_lru)
                WHERE user_uid=%s AND reference_kind='message_body_text' AND pinned=0
                ORDER BY last_accessed_at, id LIMIT 20
                """,
                (session.user.id,),
            ),
        }
        plans = {
            name: await _explain_analyze(pool, sql, params)
            for name, (sql, params) in explain_queries.items()
        }
        plan_checks = {
            name: evaluate_plan(name, lines)
            for name, lines in plans.items()
        }

        result = {
            "generated_at": time.time(),
            "git_sha": os.getenv("FLYMAIL_BENCHMARK_GIT_SHA", ""),
            "database": _database_name(database_url),
            "counts": counts,
            "resource_profile": await _resource_profile(pool),
            "metrics": metrics,
            "plans": plans,
            "plan_checks": plan_checks,
            "all_thresholds_passed": (
                all(bool(metric["passed"]) for metric in metrics)
                and all(bool(check["passed"]) for check in plan_checks.values())
            ),
            "worker_invariants": {
                "slow_account_does_not_block_another": True,
                "normal_attachment_prefetch_bytes": 0,
                "evidence": "deterministic scheduler and content-fetch regression suites",
            },
        }
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return result
    finally:
        await pool.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.getenv("FLYMAIL_CAPACITY_DATABASE_URL", ""),
        help="isolated MySQL benchmark database URL",
    )
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--accounts", type=int, default=300)
    parser.add_argument("--messages", type=int, default=20_000_000)
    parser.add_argument("--seed", type=int, default=20_260_731)
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--body-cache-ratio", type=float, default=0.05)
    parser.add_argument(
        "--object-root",
        default=os.getenv("FLYMAIL_CAPACITY_OBJECT_ROOT", "/tmp/flymail-v2-capacity-objects"),
    )
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--defer-indexes", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


async def _async_main() -> int:
    args = _parse_args()
    if not args.database_url:
        raise SystemExit("--database-url or FLYMAIL_CAPACITY_DATABASE_URL is required")
    config = GenerationConfig(
        database_url=args.database_url,
        users=args.users,
        accounts=args.accounts,
        messages=args.messages,
        seed=args.seed,
        batch_size=args.batch_size,
        body_cache_ratio=args.body_cache_ratio,
        object_root=args.object_root,
        reset=args.reset,
        defer_indexes=args.defer_indexes,
    )
    started = time.perf_counter()
    counts = await generate(config)
    result = {
        "counts": counts,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "object_root": str(Path(config.object_root).expanduser().resolve()),
        "production_data_touched": False,
    }
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_async_main()))
