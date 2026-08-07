import asyncio
import base64
import hashlib
import os
import json
import re
import time
from email.utils import getaddresses
from typing import Any, List, Optional
from urllib.parse import unquote, urlparse

import aiomysql
import pymysql

from data_paths import ensure_data_dirs
from models import Account, CachedAttachment, CachedMessage, Notification, Signature, User
from utils.logger import get_logger


logger = get_logger("db")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_CONNECT_RETRY_COUNT = 15
DB_CONNECT_RETRY_DELAY = 2
DB_MESSAGE_BODY_MAX_BYTES = 1024 * 1024
DB_EXECUTEMANY_MAX_BYTES = 2 * 1024 * 1024


def _parse_database_url(database_url: str) -> dict[str, Any]:
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    parsed = urlparse(database_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"mysql", "mysql+pymysql", "mysql+aiomysql"}:
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")
    db_name = parsed.path.lstrip("/")
    if not db_name:
        raise RuntimeError("DATABASE_URL must include a database name")
    charset = "utf8mb4"
    for part in (parsed.query or "").split("&"):
        if part.startswith("charset="):
            charset = part.split("=", 1)[1] or charset
            break
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "db": db_name,
        "charset": charset,
        "autocommit": False,
    }


def _translate_sql(sql: str) -> str:
    translated = sql.replace("?", "%s")
    translated = translated.replace("AUTOINCREMENT", "AUTO_INCREMENT")
    translated = translated.replace("ON CONFLICT(id) DO UPDATE SET", "ON DUPLICATE KEY UPDATE")
    translated = translated.replace("id INTEGER PRIMARY KEY AUTO_INCREMENT", "id BIGINT PRIMARY KEY AUTO_INCREMENT")
    translated = translated.replace("subject = excluded.subject,", "subject = VALUES(subject),")
    translated = translated.replace("from_addr = excluded.from_addr,", "from_addr = VALUES(from_addr),")
    translated = translated.replace("to_addr = excluded.to_addr,", "to_addr = VALUES(to_addr),")
    translated = translated.replace("date = excluded.date,", "date = VALUES(date),")
    translated = translated.replace("is_read = excluded.is_read,", "is_read = VALUES(is_read),")
    translated = translated.replace("is_starred = excluded.is_starred,", "is_starred = VALUES(is_starred),")
    translated = translated.replace("has_attachments = excluded.has_attachments,", "has_attachments = VALUES(has_attachments),")
    translated = translated.replace("cached_at = excluded.cached_at,", "cached_at = VALUES(cached_at),")
    translated = translated.replace("filename = excluded.filename,", "filename = VALUES(filename),")
    translated = translated.replace("content_type = excluded.content_type,", "content_type = VALUES(content_type),")
    translated = translated.replace("size = excluded.size,", "size = VALUES(size),")
    translated = translated.replace("content_id = excluded.content_id,", "content_id = VALUES(content_id),")
    translated = translated.replace("is_inline = excluded.is_inline,", "is_inline = VALUES(is_inline),")
    translated = translated.replace("local_path = excluded.local_path,", "local_path = VALUES(local_path),")
    translated = translated.replace(
        "body_text = COALESCE(excluded.body_text, cached_messages.body_text),",
        "body_text = COALESCE(VALUES(body_text), cached_messages.body_text),",
    )
    translated = translated.replace(
        "body_html = COALESCE(excluded.body_html, cached_messages.body_html)",
        "body_html = COALESCE(VALUES(body_html), cached_messages.body_html)",
    )
    translated = translated.replace(
        "storage_path = COALESCE(excluded.storage_path, cached_messages.storage_path),",
        "storage_path = COALESCE(VALUES(storage_path), cached_messages.storage_path),",
    )
    translated = translated.replace(
        "storage_path = COALESCE(excluded.storage_path, cached_messages.storage_path)",
        "storage_path = COALESCE(VALUES(storage_path), cached_messages.storage_path)",
    )
    translated = translated.replace("INSERT OR REPLACE INTO folder_stats", "INSERT INTO folder_stats")
    translated = translated.replace("PRIMARY KEY (user_uid, key)", "PRIMARY KEY (user_uid, setting_key)")
    translated = translated.replace(" key TEXT NOT NULL,", " setting_key VARCHAR(255) NOT NULL,")
    translated = translated.replace(" key TEXT NOT NULL", " setting_key VARCHAR(255) NOT NULL")
    translated = translated.replace("SELECT value FROM user_settings WHERE user_uid = %s AND key = %s", "SELECT value FROM user_settings WHERE user_uid = %s AND setting_key = %s")
    translated = translated.replace("SELECT key, value FROM user_settings WHERE user_uid = %s AND key IN", "SELECT setting_key, value FROM user_settings WHERE user_uid = %s AND setting_key IN")
    translated = translated.replace("SELECT key, value FROM user_settings WHERE user_uid = %s", "SELECT setting_key, value FROM user_settings WHERE user_uid = %s")
    translated = translated.replace("INSERT INTO user_settings (user_uid, key, value, updated_at)", "INSERT INTO user_settings (user_uid, setting_key, value, updated_at)")
    translated = translated.replace(
        "ON CONFLICT(user_uid, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
    )
    return translated


def _extract_create_index_parts(sql: str) -> tuple[str, str] | None:
    match = re.search(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+([A-Za-z0-9_]+)\s+ON\s+([A-Za-z0-9_]+)\s*\(",
        sql,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1), match.group(2)


def _truncate_text_bytes(value: Optional[str], max_bytes: int) -> Optional[str]:
    if not value:
        return value
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    truncated = encoded[:max_bytes]
    while truncated:
        try:
            return truncated.decode("utf-8") + "\n<!-- truncated -->"
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return "<!-- truncated -->"


def _estimate_param_size(params: Any) -> int:
    if params is None:
        return 0
    if isinstance(params, (list, tuple)):
        return sum(_estimate_param_size(item) for item in params)
    if isinstance(params, bytes):
        return len(params)
    if isinstance(params, str):
        return len(params.encode("utf-8"))
    return len(str(params).encode("utf-8"))


def build_cached_message_id(account_id: str, folder: str, uid: int) -> str:
    raw_folder = (folder or "INBOX").strip() or "INBOX"
    safe_folder = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_folder).strip("_")
    safe_folder = (safe_folder or "folder")[:80]
    folder_hash = hashlib.sha1(raw_folder.encode("utf-8")).hexdigest()[:12]
    return f"{account_id}_{safe_folder}_{folder_hash}_{int(uid)}"


class BufferedCursor:
    def __init__(self, rows=None, description=None, rowcount: int = 0, lastrowid: int = 0):
        self._rows = list(rows or [])
        self._index = 0
        self.description = description or []
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row


class MySQLConnection:
    def __init__(self, pool: aiomysql.Pool, connect_kwargs: dict[str, Any]):
        self._pool = pool
        self._connect_kwargs = dict(connect_kwargs)
        self._pool_lock = asyncio.Lock()
        self._transaction_lock = asyncio.Lock()
        self._transaction_connections: dict[asyncio.Task, aiomysql.Connection] = {}
        self.row_factory = None

    async def _ensure_connected(self):
        async with self._pool_lock:
            if self._pool is None or self._pool.closed:
                self._pool = await aiomysql.create_pool(
                    minsize=1,
                    maxsize=10,
                    **self._connect_kwargs,
                )

    async def _get_transaction_connection(self, task: Optional[asyncio.Task]) -> Optional[aiomysql.Connection]:
        if task is None:
            return None
        async with self._transaction_lock:
            return self._transaction_connections.get(task)

    async def _set_transaction_connection(self, task: asyncio.Task, conn: aiomysql.Connection) -> None:
        async with self._transaction_lock:
            self._transaction_connections[task] = conn

    async def _pop_transaction_connection(self, task: Optional[asyncio.Task]) -> Optional[aiomysql.Connection]:
        if task is None:
            return None
        async with self._transaction_lock:
            return self._transaction_connections.pop(task, None)

    async def execute(self, sql: str, params=None):
        query = _translate_sql(sql)
        command = query.strip().upper()
        current_task = asyncio.current_task()

        if command == "BEGIN":
            return await self._begin_transaction(current_task)
        if command == "COMMIT":
            return await self._finish_transaction(current_task, commit=True)
        if command == "ROLLBACK":
            return await self._finish_transaction(current_task, commit=False)

        index_meta = _extract_create_index_parts(query)
        return await self._execute_with_retry(query, params, index_meta, current_task)

    async def executemany(self, sql: str, param_list):
        query = _translate_sql(sql)
        current_task = asyncio.current_task()
        for attempt in range(2):
            owned_conn = await self._get_transaction_connection(current_task)
            try:
                return await self._executemany_once(query, param_list, owned_conn)
            except (AssertionError, pymysql.err.InterfaceError, pymysql.err.OperationalError):
                if owned_conn is not None:
                    raise
                if attempt == 1:
                    raise

    async def commit(self):
        current_task = asyncio.current_task()
        owned_conn = await self._get_transaction_connection(current_task)
        if owned_conn is not None:
            await owned_conn.commit()

    async def _begin_transaction(self, task: Optional[asyncio.Task]):
        if task is None:
            raise RuntimeError("BEGIN requires an active task")
        existing = await self._get_transaction_connection(task)
        if existing is not None:
            return BufferedCursor([], [], 0, 0)
        await self._ensure_connected()
        conn = await self._pool.acquire()
        try:
            await conn.ping(reconnect=True)
            await conn.begin()
        except Exception:
            self._pool.release(conn)
            raise
        await self._set_transaction_connection(task, conn)
        return BufferedCursor([], [], 0, 0)

    async def _finish_transaction(self, task: Optional[asyncio.Task], *, commit: bool):
        conn = await self._pop_transaction_connection(task)
        if conn is None:
            return BufferedCursor([], [], 0, 0)
        try:
            if commit:
                await conn.commit()
            else:
                await conn.rollback()
        finally:
            self._pool.release(conn)
        return BufferedCursor([], [], 0, 0)

    async def _execute_with_retry(self, query: str, params, index_meta, task: Optional[asyncio.Task]):
        for attempt in range(2):
            owned_conn = await self._get_transaction_connection(task)
            try:
                return await self._execute_once(query, params, index_meta, owned_conn)
            except (AssertionError, pymysql.err.InterfaceError, pymysql.err.OperationalError):
                if owned_conn is not None:
                    raise
                if attempt == 1:
                    raise

    async def _execute_once(self, query: str, params, index_meta, owned_conn: Optional[aiomysql.Connection]):
        if owned_conn is not None:
            return await self._execute_on_connection(owned_conn, query, params, index_meta, commit_after=False)
        await self._ensure_connected()
        async with self._pool.acquire() as conn:
            await conn.ping(reconnect=True)
            return await self._execute_on_connection(conn, query, params, index_meta, commit_after=True)

    async def _executemany_once(self, query: str, param_list, owned_conn: Optional[aiomysql.Connection]):
        if owned_conn is not None:
            async with owned_conn.cursor() as cursor:
                await cursor.executemany(query, param_list)
                return BufferedCursor([], cursor.description, cursor.rowcount, cursor.lastrowid)
        await self._ensure_connected()
        async with self._pool.acquire() as conn:
            await conn.ping(reconnect=True)
            async with conn.cursor() as cursor:
                await cursor.executemany(query, param_list)
                await conn.commit()
                return BufferedCursor([], cursor.description, cursor.rowcount, cursor.lastrowid)

    async def _execute_on_connection(self, conn: aiomysql.Connection, query: str, params, index_meta, *, commit_after: bool):
        async with conn.cursor() as cursor:
            if index_meta:
                index_name, table_name = index_meta
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
                exists_row = await cursor.fetchone()
                if exists_row and exists_row[0]:
                    if commit_after:
                        await conn.commit()
                    return BufferedCursor([], [], 0, 0)
                query = re.sub(r"\s+IF\s+NOT\s+EXISTS", "", query, count=1, flags=re.IGNORECASE)
            await cursor.execute(query, params)
            rows = await cursor.fetchall() if cursor.description else []
            if commit_after:
                await conn.commit()
            return BufferedCursor(rows, cursor.description, cursor.rowcount, cursor.lastrowid)


async def _connect_mysql_with_retry() -> MySQLConnection:
    connect_kwargs = _parse_database_url(DATABASE_URL)
    last_error: Exception | None = None
    for attempt in range(1, DB_CONNECT_RETRY_COUNT + 1):
        try:
            pool = await aiomysql.create_pool(
                minsize=1,
                maxsize=10,
                **connect_kwargs,
            )
            return MySQLConnection(pool, connect_kwargs)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "数据库连接失败，稍后重试 (%d/%d): %s",
                attempt,
                DB_CONNECT_RETRY_COUNT,
                exc,
            )
            if attempt == DB_CONNECT_RETRY_COUNT:
                raise
            await asyncio.sleep(DB_CONNECT_RETRY_DELAY)
    raise last_error or RuntimeError("数据库连接失败")

# 全局单例数据库连接池，避免每次操作都新建连接。
_db_instance: Optional[MySQLConnection] = None
# 保护单例创建，防止并发 get_db() 创建多个连接池。
_db_lock = asyncio.Lock()


async def get_db() -> MySQLConnection:
    """获取全局数据库连接池。"""
    global _db_instance
    if _db_instance is not None and getattr(_db_instance, "_pool", None) is not None:
        await _db_instance._ensure_connected()
        return _db_instance
    async with _db_lock:
        if _db_instance is None or getattr(_db_instance, "_pool", None) is None:
            ensure_data_dirs()
            _db_instance = await _connect_mysql_with_retry()
        else:
            await _db_instance._ensure_connected()
    return _db_instance


async def _widen_mail_address_columns(db) -> None:
    """将可能包含大量收件人的地址字段升级为 LONGTEXT。"""
    for table in ("cached_messages", "notifications", "message_archive"):
        cursor = await db.execute(
            """
            SELECT DATA_TYPE
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = ?
              AND column_name = 'to_addr'
            """,
            (table,),
        )
        row = await cursor.fetchone()
        if not row or str(row[0]).lower() == "longtext":
            continue
        await db.execute(f"ALTER TABLE {table} MODIFY COLUMN to_addr LONGTEXT")


async def init_db():
    """初始化数据库表和索引。"""
    db = await get_db()

    await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id VARCHAR(191) PRIMARY KEY,
                username VARCHAR(191) NOT NULL UNIQUE,
                nickname VARCHAR(191) DEFAULT '',
                avatar_path VARCHAR(1024) DEFAULT '',
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(32) NOT NULL DEFAULT 'user',
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id VARCHAR(191) PRIMARY KEY,
                user_uid VARCHAR(191) NOT NULL,
                email VARCHAR(255) NOT NULL,
                provider VARCHAR(64) NOT NULL,
                credentials_json LONGTEXT,
                status VARCHAR(64) DEFAULT 'disconnected',
                remark VARCHAR(255) DEFAULT '',
                group_name VARCHAR(255) DEFAULT '',
                hide_email INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                poll_interval_seconds INTEGER DEFAULT 10,
                icon_type VARCHAR(32) NOT NULL DEFAULT 'default',
                icon_value VARCHAR(255) NOT NULL DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS cached_messages (
                id VARCHAR(191) PRIMARY KEY,
                account_id VARCHAR(191) NOT NULL,
                user_uid VARCHAR(191) NOT NULL,
                uid INTEGER NOT NULL,
                folder VARCHAR(255) NOT NULL,
                subject VARCHAR(512) DEFAULT '',
                from_addr VARCHAR(512) DEFAULT '',
                to_addr LONGTEXT,
                cc LONGTEXT,
                date VARCHAR(128) DEFAULT '',
                is_read INTEGER DEFAULT 0,
                is_starred INTEGER DEFAULT 0,
                has_attachments INTEGER DEFAULT 0,
                body_text LONGTEXT,
                body_html LONGTEXT,
                message_id VARCHAR(998) DEFAULT '',
                body_checked INTEGER DEFAULT 0,
                storage_path LONGTEXT,
                cached_at REAL DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS cached_attachments (
                account_id VARCHAR(191) NOT NULL,
                user_uid VARCHAR(191) NOT NULL,
                uid INTEGER NOT NULL,
                folder VARCHAR(255) NOT NULL,
                part_number INTEGER NOT NULL,
                filename VARCHAR(512) DEFAULT '',
                content_type VARCHAR(255) DEFAULT '',
                size BIGINT DEFAULT 0,
                content_id VARCHAR(512) DEFAULT '',
                is_inline INTEGER DEFAULT 0,
                local_path LONGTEXT,
                content_sha256 CHAR(64) DEFAULT '',
                last_accessed_at REAL DEFAULT 0,
                cached_at REAL DEFAULT 0,
                PRIMARY KEY (account_id, folder, uid, part_number)
            )
        """)
    await db.execute("""
            CREATE TABLE IF NOT EXISTS attachment_cache_objects (
                content_sha256 CHAR(64) PRIMARY KEY,
                size BIGINT NOT NULL,
                local_path LONGTEXT NOT NULL,
                created_at REAL DEFAULT 0
            )
        """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_user ON cached_messages(user_uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_folder ON cached_messages(folder)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_account_folder ON cached_messages(account_id, folder)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_uid ON cached_messages(account_id, folder, uid)")
    await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_cached_messages_account_folder_uid ON cached_messages(account_id, folder, uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_read ON cached_messages(account_id, folder, is_read)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_cached_attachments_lookup ON cached_attachments(account_id, folder, uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_uid)")

    await db.execute("""
            CREATE TABLE IF NOT EXISTS cached_message_empty_body_checks (
                account_id VARCHAR(191) NOT NULL,
                folder VARCHAR(255) NOT NULL,
                uid INTEGER NOT NULL,
                checked_at REAL DEFAULT 0,
                PRIMARY KEY (account_id, folder, uid)
            )
        """)

    await db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id VARCHAR(191) PRIMARY KEY,
                user_uid VARCHAR(191) NOT NULL,
                account_id VARCHAR(191) NOT NULL,
                provider VARCHAR(64) NOT NULL,
                email VARCHAR(255) NOT NULL,
                folder VARCHAR(255) DEFAULT 'INBOX',
                is_read INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0,
                type VARCHAR(64) DEFAULT 'new_mail',
                message VARCHAR(1024) DEFAULT '',
                message_cache_id VARCHAR(191) DEFAULT '',
                message_uid INTEGER DEFAULT 0,
                rfc_message_id VARCHAR(998) DEFAULT '',
                subject VARCHAR(512) DEFAULT '',
                from_addr VARCHAR(512) DEFAULT '',
                to_addr LONGTEXT,
                cc LONGTEXT,
                mail_date VARCHAR(128) DEFAULT '',
                body_preview LONGTEXT,
                has_attachments INTEGER DEFAULT 0,
                batch_count INTEGER DEFAULT 1,
                extra_json LONGTEXT
            )
        """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(user_uid, is_read)")

    await db.execute("""
            CREATE TABLE IF NOT EXISTS folder_stats (
                account_id VARCHAR(191) NOT NULL,
                folder VARCHAR(255) NOT NULL,
                total_count INTEGER DEFAULT 0,
                unread_count INTEGER DEFAULT 0,
                updated_at REAL DEFAULT 0,
                PRIMARY KEY (account_id, folder)
            )
        """)

    await db.execute("""
            CREATE TABLE IF NOT EXISTS account_folder_counts (
                account_id VARCHAR(191) NOT NULL,
                folder_key VARCHAR(64) NOT NULL,
                folder_path VARCHAR(255) NOT NULL,
                display_name VARCHAR(255) NOT NULL,
                total_count INTEGER DEFAULT 0,
                unread_count INTEGER DEFAULT 0,
                cached_count INTEGER DEFAULT 0,
                updated_at REAL DEFAULT 0,
                PRIMARY KEY (account_id, folder_key)
            )
        """)

    await db.execute("""
            CREATE TABLE IF NOT EXISTS signatures (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(255) NOT NULL,
                content_html LONGTEXT,
                is_default INTEGER DEFAULT 0,
                is_reply_default INTEGER DEFAULT 0,
                account_id VARCHAR(191) DEFAULT '',
                user_uid VARCHAR(191) DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            )
        """)

    await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_uid VARCHAR(191) NOT NULL,
                setting_key VARCHAR(255) NOT NULL,
                value LONGTEXT,
                updated_at REAL DEFAULT 0,
                PRIMARY KEY (user_uid, setting_key)
            )
        """)

    await db.execute("""
            CREATE TABLE IF NOT EXISTS history_sync_jobs (
                id VARCHAR(191) PRIMARY KEY,
                account_id VARCHAR(191) NOT NULL,
                user_uid VARCHAR(191) NOT NULL,
                job_type VARCHAR(64) DEFAULT 'history_sync',
                status VARCHAR(64) DEFAULT 'pending',
                current_folder VARCHAR(255) DEFAULT '',
                current_page INTEGER DEFAULT 1,
                current_uid INTEGER DEFAULT 0,
                total_folders INTEGER DEFAULT 0,
                completed_folders INTEGER DEFAULT 0,
                fetched_messages INTEGER DEFAULT 0,
                downloaded_attachments INTEGER DEFAULT 0,
                downloaded_inline_images INTEGER DEFAULT 0,
                error_message VARCHAR(1024) DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                finished_at REAL DEFAULT 0
            )
        """)

    await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_read_sync (
                account_id VARCHAR(191) NOT NULL,
                user_uid VARCHAR(191) NOT NULL,
                uid INTEGER NOT NULL,
                folder VARCHAR(255) NOT NULL,
                desired_read INTEGER DEFAULT 1,
                attempts INTEGER DEFAULT 0,
                last_error VARCHAR(1024) DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                PRIMARY KEY (account_id, folder, uid)
            )
        """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_pending_read_sync_updated ON pending_read_sync(updated_at)")

    for column, declaration in (
        ("nickname", "VARCHAR(191) DEFAULT ''"),
        ("avatar_path", "VARCHAR(1024) DEFAULT ''"),
    ):
        try:
            await db.execute(f"ALTER TABLE users ADD COLUMN {column} {declaration}")
        except Exception as e:
            logger.debug("migration add users.%s ignored: %s", column, e)
    await db.execute("UPDATE users SET nickname = '' WHERE nickname IS NULL")
    await db.execute("UPDATE users SET avatar_path = '' WHERE avatar_path IS NULL")

    try:
        await db.execute("ALTER TABLE accounts ADD COLUMN hide_email INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add accounts.hide_email ignored: %s", e)
    try:
        await db.execute("ALTER TABLE accounts ADD COLUMN poll_interval_seconds INTEGER DEFAULT 10")
    except Exception as e:
        logger.debug("migration add accounts.poll_interval_seconds ignored: %s", e)
    for column, declaration in (
        ("icon_type", "VARCHAR(32) NOT NULL DEFAULT 'default'"),
        ("icon_value", "VARCHAR(255) NOT NULL DEFAULT ''"),
    ):
        try:
            await db.execute(f"ALTER TABLE accounts ADD COLUMN {column} {declaration}")
        except Exception as e:
            logger.debug("migration add accounts.%s ignored: %s", column, e)
    await db.execute("UPDATE accounts SET icon_type = 'default' WHERE icon_type IS NULL OR icon_type = ''")
    await db.execute("UPDATE accounts SET icon_value = '' WHERE icon_value IS NULL")

    try:
        await db.execute("ALTER TABLE cached_messages ADD COLUMN has_attachments INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add cached_messages.has_attachments ignored: %s", e)

    try:
        await db.execute("ALTER TABLE cached_messages ADD COLUMN storage_path LONGTEXT")
    except Exception as e:
        logger.debug("migration add cached_messages.storage_path ignored: %s", e)

    try:
        await db.execute("ALTER TABLE cached_messages ADD COLUMN body_checked INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add cached_messages.body_checked ignored: %s", e)

    for column, declaration in (
        ("content_sha256", "CHAR(64) DEFAULT ''"),
        ("last_accessed_at", "REAL DEFAULT 0"),
    ):
        try:
            await db.execute(f"ALTER TABLE cached_attachments ADD COLUMN {column} {declaration}")
        except Exception as e:
            logger.debug("migration add cached_attachments.%s ignored: %s", column, e)

    await db.execute("CREATE INDEX IF NOT EXISTS idx_cached_attachments_sha256 ON cached_attachments(content_sha256)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_cached_attachments_user_inline_access ON cached_attachments(user_uid, is_inline, last_accessed_at)")

    try:
        await db.execute("ALTER TABLE notifications ADD COLUMN type VARCHAR(64) DEFAULT 'new_mail'")
    except Exception as e:
        logger.debug("migration add notifications.type ignored: %s", e)

    try:
        await db.execute("ALTER TABLE notifications ADD COLUMN message VARCHAR(1024) DEFAULT ''")
    except Exception as e:
        logger.debug("migration add notifications.message ignored: %s", e)

    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN job_type VARCHAR(64) DEFAULT 'history_sync'")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.job_type ignored: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN current_uid INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.current_uid ignored: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN downloaded_attachments INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.downloaded_attachments ignored: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN downloaded_inline_images INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.downloaded_inline_images ignored: %s", e)
    try:
        await db.execute("ALTER TABLE history_sync_jobs ADD COLUMN finished_at REAL DEFAULT 0")
    except Exception as e:
        logger.debug("migration add history_sync_jobs.finished_at ignored: %s", e)

    try:
        await db.execute("ALTER TABLE signatures ADD COLUMN user_uid VARCHAR(191) DEFAULT ''")
    except Exception as e:
        logger.debug("migration add signatures.user_uid ignored: %s", e)
    try:
        await db.execute("ALTER TABLE signatures ADD COLUMN is_reply_default INTEGER DEFAULT 0")
    except Exception as e:
        logger.debug("migration add signatures.is_reply_default ignored: %s", e)

    for table, column, declaration in (
        ("accounts", "sort_order", "INTEGER DEFAULT 0"),
        ("cached_messages", "cc", "LONGTEXT"),
        ("cached_messages", "message_id", "VARCHAR(998) DEFAULT ''"),
        ("notifications", "message_cache_id", "VARCHAR(191) DEFAULT ''"),
        ("notifications", "message_uid", "INTEGER DEFAULT 0"),
        ("notifications", "rfc_message_id", "VARCHAR(998) DEFAULT ''"),
        ("notifications", "subject", "VARCHAR(512) DEFAULT ''"),
        ("notifications", "from_addr", "VARCHAR(512) DEFAULT ''"),
        ("notifications", "to_addr", "VARCHAR(512) DEFAULT ''"),
        ("notifications", "cc", "LONGTEXT"),
        ("notifications", "mail_date", "VARCHAR(128) DEFAULT ''"),
        ("notifications", "body_preview", "LONGTEXT"),
        ("notifications", "has_attachments", "INTEGER DEFAULT 0"),
        ("notifications", "batch_count", "INTEGER DEFAULT 1"),
        ("notifications", "extra_json", "LONGTEXT"),
    ):
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        except Exception as e:
            logger.debug("migration add %s.%s ignored: %s", table, column, e)

    await db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_msg ON notifications(user_uid, message_cache_id)")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            user_uid VARCHAR(191) NOT NULL,
            name VARCHAR(255) DEFAULT '',
            phone VARCHAR(128) DEFAULT '',
            company VARCHAR(255) DEFAULT '',
            remark LONGTEXT,
            group_name VARCHAR(255) DEFAULT '',
            created_at REAL DEFAULT 0,
            updated_at REAL DEFAULT 0
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_contacts_user ON contacts(user_uid)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS contact_emails (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            contact_id BIGINT NOT NULL,
            email VARCHAR(320) NOT NULL,
            is_primary INTEGER DEFAULT 0,
            created_at REAL DEFAULT 0,
            UNIQUE KEY uq_contact_email (contact_id, email)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_contact_emails_contact ON contact_emails(contact_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_contact_emails_email ON contact_emails(email)")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS message_archive (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            user_uid VARCHAR(191) NOT NULL,
            account_id VARCHAR(191) NOT NULL,
            folder VARCHAR(255) NOT NULL,
            uid INTEGER NOT NULL,
            message_id VARCHAR(998) DEFAULT '',
            subject VARCHAR(512) DEFAULT '',
            from_addr VARCHAR(512) DEFAULT '',
            to_addr LONGTEXT,
            cc LONGTEXT,
            date VARCHAR(128) DEFAULT '',
            size BIGINT DEFAULT 0,
            eml_path LONGTEXT NOT NULL,
            flags VARCHAR(512) DEFAULT '',
            has_attachments INTEGER DEFAULT 0,
            archived_at REAL DEFAULT 0,
            is_deleted_on_server INTEGER DEFAULT 0,
            deleted_at REAL DEFAULT 0,
            UNIQUE KEY uq_archive_message (account_id, folder, uid)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_archive_user ON message_archive(user_uid)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_archive_account_folder ON message_archive(account_id, folder)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_archive_date ON message_archive(date)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_archive_deleted ON message_archive(user_uid, is_deleted_on_server)")

    await _widen_mail_address_columns(db)
    await db.commit()

async def get_accounts(user_uid: str) -> List[Account]:
    """按用户获取账号；user_uid 为空时返回全部账号。"""
    db = await get_db()
    if user_uid:
        cursor = await db.execute("SELECT * FROM accounts WHERE user_uid = ? ORDER BY created_at DESC", (user_uid,))
    else:
        cursor = await db.execute("SELECT * FROM accounts ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [Account(**dict(zip(columns, row))) for row in rows]


async def create_account(account: Account) -> Account:
    """创建邮箱账号，并追加到当前用户账号顺序末尾。"""
    db = await get_db()
    await db.execute("BEGIN")
    try:
        await db.execute(
            "SELECT id FROM users WHERE id = ? FOR UPDATE",
            (account.user_uid,),
        )
        cursor = await db.execute(
            "SELECT MAX(sort_order) FROM accounts WHERE user_uid = ? FOR UPDATE",
            (account.user_uid,),
        )
        row = await cursor.fetchone()
        current_max = int(row[0]) if row and row[0] is not None else -1
        account.sort_order = current_max + 1
        await db.execute(
            """INSERT INTO accounts
               (id, user_uid, email, provider, credentials_json, status,
                remark, group_name, hide_email, poll_interval_seconds, sort_order,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account.id, account.user_uid, account.email, account.provider,
             account.credentials_json, account.status,
             account.remark, account.group_name,
             1 if account.hide_email else 0,
             account.poll_interval_seconds, account.sort_order,
             account.created_at, account.updated_at)
        )
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
    return account


async def delete_account(account_id: str, user_uid: str) -> bool:
    """删除当前用户的邮箱账号。"""
    db = await get_db()
    cursor = await db.execute("DELETE FROM accounts WHERE id = ? AND user_uid = ?", (account_id, user_uid))
    await db.commit()
    return cursor.rowcount > 0


async def batch_delete_cached_messages(account_id: str, uids: list[int], folder: str) -> int:
    """Get cached message count for a folder after delete/move operations."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM cached_messages WHERE account_id = ? AND folder = ?",
        (account_id, folder),
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


async def get_cached_uids(account_id: str, folder: str) -> set:
    """Return cached message UIDs for the given account and folder aliases."""
    aliases = _expand_folder_aliases(folder)
    if not aliases:
        return set()
    db = await get_db()
    placeholders = ",".join("?" * len(aliases))
    cursor = await db.execute(
        f"SELECT uid FROM cached_messages WHERE account_id = ? AND folder IN ({placeholders})",
        [account_id] + aliases,
    )
    rows = await cursor.fetchall()
    return {int(row[0]) for row in rows if row and row[0] is not None}


async def get_existing_cached_uids(account_id: str, folder: str, uids: list[int]) -> set[int]:
    """Return only requested UIDs that already exist in the local cache."""
    requested = list(dict.fromkeys(int(uid) for uid in uids if int(uid) > 0))
    aliases = _expand_folder_aliases(folder)
    if not requested or not aliases:
        return set()

    db = await get_db()
    alias_placeholders = ",".join("?" * len(aliases))
    existing: set[int] = set()
    for start in range(0, len(requested), 500):
        batch = requested[start:start + 500]
        uid_placeholders = ",".join("?" * len(batch))
        cursor = await db.execute(
            f"""SELECT uid
                FROM cached_messages
                WHERE account_id = ?
                  AND folder IN ({alias_placeholders})
                  AND uid IN ({uid_placeholders})""",
            [account_id] + aliases + batch,
        )
        rows = await cursor.fetchall()
        existing.update(int(row[0]) for row in rows if row and row[0] is not None)
    return existing


async def list_cached_read_states(
    account_id: str,
    folder: str,
    *,
    after_uid: int = 0,
    limit: int = 1000,
) -> list[dict]:
    """Page cached UID/read-state rows without loading an entire folder."""
    aliases = _expand_folder_aliases(folder)
    if not aliases:
        return []
    safe_limit = min(5000, max(1, int(limit or 1000)))
    db = await get_db()
    placeholders = ",".join("?" * len(aliases))
    cursor = await db.execute(
        f"""SELECT uid, MAX(is_read) AS is_read
            FROM cached_messages
            WHERE account_id = ?
              AND folder IN ({placeholders})
              AND uid > ?
            GROUP BY uid
            ORDER BY uid ASC
            LIMIT ?""",
        [account_id] + aliases + [max(0, int(after_uid or 0)), safe_limit],
    )
    rows = await cursor.fetchall()
    return [
        {"uid": int(row[0]), "is_read": bool(row[1])}
        for row in rows
        if row and row[0] is not None
    ]


# ==================== 文件夹统计 CRUD ====================

async def upsert_folder_stats(account_id: str, folder: str, total_count: int, unread_count: int) -> None:
    """Persist the latest IMAP folder statistics for the given account and folder."""
    db = await get_db()
    await db.execute(
        """INSERT INTO folder_stats (account_id, folder, total_count, unread_count, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
           total_count = VALUES(total_count),
           unread_count = VALUES(unread_count),
           updated_at = VALUES(updated_at)""",
        (account_id, folder, total_count, unread_count, time.time())
    )
    await db.commit()
    cached_count = None
    try:
        cached_count = await get_cached_count(account_id, folder)
    except Exception as exc:
        logger.debug("get cached count for folder counter failed: %s", exc)
    await upsert_account_folder_count(
        account_id,
        folder,
        total_count,
        unread_count,
        cached_count=cached_count,
    )


async def get_folder_stats(account_id: str, folder: str) -> dict:
    """Return folder stats with total_count, unread_count, and updated_at fields."""
    return await _get_folder_stats_by_aliases(account_id, folder)


async def list_folder_stats_by_account(account_id: str) -> List[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT folder, total_count, unread_count, updated_at FROM folder_stats WHERE account_id = ? ORDER BY updated_at DESC, folder ASC",
        (account_id,),
    )
    rows = await cursor.fetchall()
    return [
        {"folder": row[0], "total_count": row[1], "unread_count": row[2], "updated_at": row[3]}
        for row in rows
    ]


async def list_cached_folders_by_account(account_id: str) -> List[str]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT DISTINCT folder FROM cached_messages WHERE account_id = ? ORDER BY folder ASC",
        (account_id,),
    )
    rows = await cursor.fetchall()
    return [row[0] for row in rows if row and row[0]]


async def delete_folder_stats_by_account(account_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM folder_stats WHERE account_id = ?", (account_id,))
    await db.commit()
    await delete_account_folder_counts_by_account(account_id)


CORE_FOLDER_DEFINITIONS = [
    ("inbox", "INBOX", "收件箱", ["INBOX", "Inbox"]),
    ("sent", "Sent Messages", "已发送", ["Sent", "Sent Mail", "Sent Messages", "Sent Items", "[Gmail]/Sent Mail", "[Google Mail]/Sent Mail", "已发送"]),
    ("drafts", "Drafts", "草稿箱", ["Drafts", "[Gmail]/Drafts", "[Google Mail]/Drafts", "草稿箱"]),
    ("junk", "Junk", "垃圾邮件", ["Junk", "Junk Email", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam", "垃圾邮件"]),
    ("trash", "Trash", "已删除", ["Trash", "Deleted", "Deleted Items", "Deleted Messages", "[Gmail]/Trash", "[Google Mail]/Trash", "已删除"]),
]


def _decode_imap_modified_utf7_path(folder: str) -> str:
    text = (folder or "").strip()
    result = []
    i = 0
    while i < len(text):
        if text[i] != "&":
            result.append(text[i])
            i += 1
            continue
        end = text.find("-", i)
        if end < 0:
            result.append(text[i:])
            break
        if end == i + 1:
            result.append("&")
        else:
            encoded = text[i + 1:end].replace(",", "/")
            padding = (4 - len(encoded) % 4) % 4
            try:
                result.append(base64.b64decode(encoded + ("=" * padding)).decode("utf-16-be"))
            except Exception:
                result.append(text[i:end + 1])
        i = end + 1
    return "".join(result)


def folder_key_for_path(folder: str) -> str:
    folder_lower = (folder or "").strip().lower()
    decoded_leaf = _decode_imap_modified_utf7_path(folder).lower().rsplit("/", 1)[-1]
    extra_aliases = {
        "sent": {"sent mail", "[google mail]/sent mail", "已发送"},
        "drafts": {"[google mail]/drafts", "草稿箱"},
        "junk": {"[google mail]/spam", "垃圾邮件"},
        "trash": {"[google mail]/trash", "已删除"},
    }
    for key, aliases in extra_aliases.items():
        if folder_lower in aliases:
            return key
    decoded_aliases = {
        "sent": {"\u5df2\u53d1\u9001", "\u5df2\u53d1\u90ae\u4ef6"},
        "drafts": {"\u8349\u7a3f\u7bb1"},
        "junk": {"\u5783\u573e\u90ae\u4ef6"},
        "trash": {"\u5df2\u5220\u9664"},
    }
    for key, aliases in decoded_aliases.items():
        if decoded_leaf in aliases:
            return key
    for key, _default_path, _display_name, aliases in CORE_FOLDER_DEFINITIONS:
        if any(folder_lower == alias.lower() for alias in aliases):
            return key
    return folder_lower or "inbox"


def folder_display_name_for_key(folder_key: str, fallback: str = "") -> str:
    for key, _default_path, display_name, _aliases in CORE_FOLDER_DEFINITIONS:
        if key == folder_key:
            return display_name
    return fallback or folder_key


def default_path_for_folder_key(folder_key: str) -> str:
    for key, default_path, _display_name, _aliases in CORE_FOLDER_DEFINITIONS:
        if key == folder_key:
            return default_path
    return folder_key


async def upsert_account_folder_count(
    account_id: str,
    folder_path: str,
    total_count: int,
    unread_count: int,
    *,
    cached_count: int | None = None,
) -> None:
    folder_key = folder_key_for_path(folder_path)
    display_name = folder_display_name_for_key(folder_key, folder_path)
    db = await get_db()
    await db.execute(
        """INSERT INTO account_folder_counts
           (account_id, folder_key, folder_path, display_name, total_count, unread_count, cached_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
           folder_path = VALUES(folder_path),
           display_name = VALUES(display_name),
           total_count = VALUES(total_count),
           unread_count = VALUES(unread_count),
           cached_count = COALESCE(VALUES(cached_count), account_folder_counts.cached_count),
           updated_at = VALUES(updated_at)""",
        (
            account_id,
            folder_key,
            folder_path or default_path_for_folder_key(folder_key),
            display_name,
            max(int(total_count or 0), 0),
            max(int(unread_count or 0), 0),
            cached_count,
            time.time(),
        ),
    )
    await db.commit()


async def list_account_folder_counts(account_id: str) -> List[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT folder_key, folder_path, display_name, total_count, unread_count, cached_count, updated_at
           FROM account_folder_counts
           WHERE account_id = ?""",
        (account_id,),
    )
    rows = await cursor.fetchall()
    by_key = {
        row[0]: {
            "folder_key": row[0],
            "folder_path": row[1],
            "display_name": row[2],
            "total_count": int(row[3] or 0),
            "unread_count": int(row[4] or 0),
            "cached_count": int(row[5] or 0),
            "updated_at": float(row[6] or 0),
        }
        for row in rows
    }
    result = []
    core_keys = {item[0] for item in CORE_FOLDER_DEFINITIONS}
    for key, default_path, display_name, _aliases in CORE_FOLDER_DEFINITIONS:
        result.append(by_key.get(key) or {
            "folder_key": key,
            "folder_path": default_path,
            "display_name": display_name,
            "total_count": 0,
            "unread_count": 0,
            "cached_count": 0,
            "updated_at": 0,
        })
    custom_folders = []
    for key, item in by_key.items():
        if key in core_keys:
            continue
        custom_item = dict(item)
        decoded_name = _decode_imap_modified_utf7_path(custom_item.get("folder_path") or "").strip()
        custom_item["display_name"] = decoded_name or custom_item.get("display_name") or custom_item.get("folder_path") or ""
        custom_folders.append(custom_item)
    custom_folders.sort(
        key=lambda item: ((item.get("display_name") or "").lower(), (item.get("folder_path") or "").lower()),
    )
    result.extend(custom_folders)
    return result


async def delete_account_folder_counts_by_account(account_id: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM account_folder_counts WHERE account_id = ?", (account_id,))
    await db.commit()


async def adjust_account_folder_unread(account_id: str, folder_path: str, delta: int) -> None:
    folder_key = folder_key_for_path(folder_path)
    db = await get_db()
    await db.execute(
        """UPDATE account_folder_counts
           SET unread_count = GREATEST(unread_count + ?, 0),
               updated_at = ?
           WHERE account_id = ? AND folder_key = ?""",
        (int(delta or 0), time.time(), account_id, folder_key),
    )
    await db.commit()


async def get_signature_by_id(sig_id: int, user_uid: str = "") -> Optional[Signature]:
    db = await get_db()
    sql = "SELECT * FROM signatures WHERE id = ?"
    params: list[Any] = [sig_id]
    if user_uid:
        sql += " AND user_uid = ?"
        params.append(user_uid)
    sql += " LIMIT 1"
    cursor = await db.execute(sql, params)
    row = await cursor.fetchone()
    if not row:
        return None
    return Signature(**_row_to_dict(cursor, row))


async def _clear_signature_defaults(db, sig: Signature) -> None:
    """Keep default flags unique inside one user and account scope."""
    scope = (sig.user_uid or "", sig.account_id or "", int(sig.id or 0))
    if sig.is_default:
        await db.execute(
            "UPDATE signatures SET is_default = 0 WHERE user_uid = ? AND account_id = ? AND id <> ?",
            scope,
        )
    if sig.is_reply_default:
        await db.execute(
            "UPDATE signatures SET is_reply_default = 0 WHERE user_uid = ? AND account_id = ? AND id <> ?",
            scope,
        )


async def update_signature(sig: Signature) -> bool:
    """Update a signature and keep each default flag unique in its account scope."""
    db = await get_db()
    now = time.time()
    await db.execute("BEGIN")
    try:
        await _clear_signature_defaults(db, sig)
        cursor = await db.execute(
            """UPDATE signatures SET name = ?, content_html = ?, is_default = ?,
               is_reply_default = ?, account_id = ?, updated_at = ?
               WHERE id = ? AND user_uid = ?""",
            (
                sig.name,
                sig.content_html,
                1 if sig.is_default else 0,
                1 if sig.is_reply_default else 0,
                sig.account_id or "",
                now,
                sig.id,
                sig.user_uid or "",
            ),
        )
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
    return cursor.rowcount > 0


async def delete_signature(sig_id: int, user_uid: str = "") -> bool:
    db = await get_db()
    sql = "DELETE FROM signatures WHERE id = ?"
    params: list[Any] = [sig_id]
    if user_uid:
        sql += " AND user_uid = ?"
        params.append(user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


async def get_user_setting(user_uid: str, key: str, default: Any = None) -> Any:
    db = await get_db()
    cursor = await db.execute(
        "SELECT value FROM user_settings WHERE user_uid = ? AND key = ?",
        (user_uid, key),
    )
    row = await cursor.fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return default


async def set_user_setting(user_uid: str, key: str, value: Any) -> None:
    db = await get_db()
    value_json = json.dumps(value, ensure_ascii=False)
    await db.execute(
        """INSERT INTO user_settings (user_uid, key, value, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_uid, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (user_uid, key, value_json, time.time()),
    )
    await db.commit()


async def get_user_settings(user_uid: str, keys: Optional[List[str]] = None) -> dict:
    db = await get_db()
    if keys:
        placeholders = ",".join("?" * len(keys))
        cursor = await db.execute(
            f"SELECT key, value FROM user_settings WHERE user_uid = ? AND key IN ({placeholders})",
            [user_uid] + list(keys),
        )
    else:
        cursor = await db.execute(
            "SELECT key, value FROM user_settings WHERE user_uid = ?",
            (user_uid,),
        )
    rows = await cursor.fetchall()
    result = {}
    for row in rows:
        try:
            result[row[0]] = json.loads(row[1])
        except (json.JSONDecodeError, TypeError):
            logger.debug("decode user setting failed: user_uid=%s key=%s", user_uid, row[0])
    return result


async def set_user_settings(user_uid: str, settings: dict) -> None:
    """Batch upsert user settings."""
    db = await get_db()
    now = time.time()
    for key, value in settings.items():
        value_json = json.dumps(value, ensure_ascii=False)
        await db.execute(
            """INSERT INTO user_settings (user_uid, key, value, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_uid, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (user_uid, key, value_json, now),
        )
    await db.commit()


async def create_history_sync_job(job: dict) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO history_sync_jobs
           (id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
            total_folders, completed_folders, fetched_messages, downloaded_attachments,
            downloaded_inline_images, error_message, created_at, updated_at, finished_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job["id"],
            job["account_id"],
            job["user_uid"],
            job.get("job_type", "history_sync"),
            job.get("status", "pending"),
            job.get("current_folder", ""),
            job.get("current_page", 1),
            job.get("current_uid", 0),
            job.get("total_folders", 0),
            job.get("completed_folders", 0),
            job.get("fetched_messages", 0),
            job.get("downloaded_attachments", 0),
            job.get("downloaded_inline_images", 0),
            job.get("error_message", ""),
            job.get("created_at", time.time()),
            job.get("updated_at", time.time()),
            job.get("finished_at", 0),
        ),
    )
    await db.commit()


async def update_history_sync_job(job_id: str, **fields) -> None:
    if not fields:
        return
    db = await get_db()
    fields["updated_at"] = time.time()
    assignments = ", ".join(f"{key} = ?" for key in fields.keys())
    params = list(fields.values()) + [job_id]
    await db.execute(f"UPDATE history_sync_jobs SET {assignments} WHERE id = ?", params)
    await db.commit()


async def touch_history_sync_job(job_id: str) -> bool:
    """Refresh the heartbeat timestamp without changing the task status."""
    db = await get_db()
    cursor = await db.execute(
        """UPDATE history_sync_jobs
           SET updated_at = ?
           WHERE id = ? AND status IN ('pending', 'running')""",
        (time.time(), job_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_history_sync_job(account_id: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                  total_folders, completed_folders, fetched_messages, downloaded_attachments,
                  downloaded_inline_images, error_message, created_at, updated_at, finished_at
           FROM history_sync_jobs
           WHERE account_id = ?
           ORDER BY created_at DESC
           LIMIT 1""",
        (account_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


async def get_history_sync_job_by_id(job_id: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                  total_folders, completed_folders, fetched_messages, downloaded_attachments,
                  downloaded_inline_images, error_message, created_at, updated_at, finished_at
           FROM history_sync_jobs
           WHERE id = ?
           LIMIT 1""",
        (job_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


async def list_history_sync_jobs(user_uid: str) -> List[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                  total_folders, completed_folders, fetched_messages, downloaded_attachments,
                  downloaded_inline_images, error_message, created_at, updated_at, finished_at
           FROM history_sync_jobs
           WHERE user_uid = ?
           ORDER BY updated_at DESC, created_at DESC""",
        (user_uid,),
    )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


async def delete_history_sync_jobs_by_account(account_id: str, keep_job_id: str = "") -> int:
    db = await get_db()
    if keep_job_id:
        cursor = await db.execute(
            "DELETE FROM history_sync_jobs WHERE account_id = ? AND id != ?",
            (account_id, keep_job_id),
        )
    else:
        cursor = await db.execute(
            "DELETE FROM history_sync_jobs WHERE account_id = ?",
            (account_id,),
        )
    await db.commit()
    return cursor.rowcount


async def list_users() -> List[User]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, nickname, avatar_path, password_hash, role, status, created_at, updated_at FROM users ORDER BY created_at ASC"
    )
    rows = await cursor.fetchall()
    return [User(**dict(zip([description[0] for description in cursor.description], row))) for row in rows]


async def get_user_by_id(user_id: str) -> Optional[User]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, nickname, avatar_path, password_hash, role, status, created_at, updated_at FROM users WHERE id = ? LIMIT 1",
        (user_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return User(**dict(zip([description[0] for description in cursor.description], row)))


async def get_user_by_username(username: str) -> Optional[User]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT id, username, nickname, avatar_path, password_hash, role, status, created_at, updated_at FROM users WHERE username = ? LIMIT 1",
        (username,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return User(**dict(zip([description[0] for description in cursor.description], row)))


async def create_user(user: User) -> User:
    db = await get_db()
    await db.execute(
        """INSERT INTO users (id, username, nickname, avatar_path, password_hash, role, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user.id, user.username, user.nickname, user.avatar_path, user.password_hash,
            user.role, user.status, user.created_at, user.updated_at,
        ),
    )
    await db.commit()
    return user


async def delete_user(user_id: str) -> bool:
    db = await get_db()
    cursor = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
    return cursor.rowcount > 0


async def update_user_password(user_id: str, password_hash: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (password_hash, time.time(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_user_status(user_id: str, status: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
        (status, time.time(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_user_profile(user_id: str, username: str, nickname: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE users SET username = ?, nickname = ?, updated_at = ? WHERE id = ?",
        (username, nickname, time.time(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_user_avatar(user_id: str, avatar_path: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "UPDATE users SET avatar_path = ?, updated_at = ? WHERE id = ?",
        (avatar_path, time.time(), user_id),
    )
    await db.commit()
    return cursor.rowcount > 0

# ==================== Rebuilt compatibility layer ====================

def _row_to_dict(cursor, row):
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


def _expand_folder_aliases(folder: str) -> list[str]:
    folder = (folder or '').strip() or 'INBOX'
    extra_aliases = {
        "inbox": {"INBOX", "Inbox"},
        "sent": {"&XfJT0ZAB-", "[Gmail]/&XfJT0ZCuTvY-"},
        "drafts": {"&g0l6P3ux-", "[Gmail]/&g0l6Pw-"},
        "junk": {"[Gmail]/&V4NXPpCuTvY-"},
        "trash": {"[Gmail]/&XfJSIJZk-", "[Gmail]/&XfJSIJZkkK5O9g-"},
    }
    folder_key = folder_key_for_path(folder)
    for key, default_path, _display_name, aliases in CORE_FOLDER_DEFINITIONS:
        if folder_key == key:
            return sorted({default_path, *aliases, *extra_aliases.get(key, set())})
    return [folder]


async def _get_folder_stats_by_aliases(account_id: str, folder: str) -> dict:
    aliases = _expand_folder_aliases(folder)
    db = await get_db()
    placeholders = ','.join('?' * len(aliases))
    cursor = await db.execute(
        f'''SELECT COALESCE(MAX(total_count), 0),
                   COALESCE(MAX(unread_count), 0),
                   COALESCE(MAX(updated_at), 0)
            FROM folder_stats
            WHERE account_id = ? AND folder IN ({placeholders})''',
        [account_id] + aliases,
    )
    row = await cursor.fetchone()
    if row:
        return {'total_count': int(row[0] or 0), 'unread_count': int(row[1] or 0), 'updated_at': float(row[2] or 0)}
    return {'total_count': 0, 'unread_count': 0, 'updated_at': 0}


async def get_account_by_id(account_id: str):
    db = await get_db()
    cursor = await db.execute('SELECT * FROM accounts WHERE id = ? LIMIT 1', (account_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    return Account(**_row_to_dict(cursor, row))


async def get_accounts(user_uid: str) -> List[Account]:
    db = await get_db()
    if user_uid:
        cursor = await db.execute(
            'SELECT * FROM accounts WHERE user_uid = ? '
            'ORDER BY sort_order ASC, created_at ASC, id ASC',
            (user_uid,),
        )
    else:
        cursor = await db.execute(
            'SELECT * FROM accounts '
            'ORDER BY user_uid ASC, sort_order ASC, created_at ASC, id ASC'
        )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [Account(**dict(zip(columns, row))) for row in rows]


async def reorder_accounts(user_uid: str, account_ids: list[str]) -> bool:
    """按当前用户提交的完整账号 ID 序列保存排序。"""
    db = await get_db()
    await db.execute("BEGIN")
    try:
        cursor = await db.execute(
            'SELECT id FROM accounts WHERE user_uid = ? '
            'ORDER BY sort_order ASC, created_at ASC, id ASC FOR UPDATE',
            (user_uid,),
        )
        owned_ids = [str(row[0]) for row in await cursor.fetchall()]
        if len(account_ids) != len(set(account_ids)) or set(account_ids) != set(owned_ids):
            await db.execute("ROLLBACK")
            return False

        now = time.time()
        await db.executemany(
            'UPDATE accounts SET sort_order = ?, updated_at = ? '
            'WHERE id = ? AND user_uid = ?',
            [
                (index, now, account_id, user_uid)
                for index, account_id in enumerate(account_ids)
            ],
        )
        await db.execute("COMMIT")
        return True
    except Exception:
        await db.execute("ROLLBACK")
        raise


async def delete_account(account_id: str, user_uid: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        'DELETE FROM accounts WHERE id = ? AND user_uid = ?',
        (account_id, user_uid),
    )
    await db.commit()
    return cursor.rowcount > 0


async def activate_account(account_id: str, user_uid: str = '') -> bool:
    db = await get_db()
    sql = 'UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?'
    params = ['active', time.time(), account_id]
    if user_uid:
        sql += ' AND user_uid = ?'
        params.append(user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


async def deactivate_account(account_id: str, user_uid: str = '') -> bool:
    db = await get_db()
    sql = 'UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?'
    params = ['offline', time.time(), account_id]
    if user_uid:
        sql += ' AND user_uid = ?'
        params.append(user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


async def update_account_credentials(account_id: str, credentials_json: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        'UPDATE accounts SET credentials_json = ?, updated_at = ? WHERE id = ?',
        (credentials_json, time.time(), account_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_account_info(
    account_id: str,
    user_uid: str,
    remark: str = '',
    group_name: str = '',
    hide_email: bool = False,
    poll_interval_seconds: int = 10,
) -> bool:
    db = await get_db()
    interval = min(3600, max(5, int(poll_interval_seconds or 10)))
    cursor = await db.execute(
        '''UPDATE accounts
           SET remark = ?, group_name = ?, hide_email = ?, poll_interval_seconds = ?, updated_at = ?
           WHERE id = ? AND user_uid = ?''',
        (remark, group_name, 1 if hide_email else 0, interval, time.time(), account_id, user_uid),
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_account_icon(
    account_id: str,
    user_uid: str,
    icon_type: str,
    icon_value: str = '',
) -> bool:
    db = await get_db()
    cursor = await db.execute(
        '''UPDATE accounts
           SET icon_type = ?, icon_value = ?, updated_at = ?
           WHERE id = ? AND user_uid = ?''',
        (icon_type, icon_value, time.time(), account_id, user_uid),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_cached_messages_by_account(account_id: str) -> int:
    db = await get_db()
    cursor = await db.execute('DELETE FROM cached_messages WHERE account_id = ?', (account_id,))
    await db.execute('DELETE FROM pending_read_sync WHERE account_id = ?', (account_id,))
    await db.commit()
    return cursor.rowcount


async def delete_cached_attachments_by_account(account_id: str) -> int:
    db = await get_db()
    cursor = await db.execute('DELETE FROM cached_attachments WHERE account_id = ?', (account_id,))
    await db.commit()
    return cursor.rowcount


async def get_max_cached_uid(user_uid: str, account_id: str, folder: str) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT COALESCE(MAX(uid), 0)
            FROM cached_messages
            WHERE user_uid = ? AND account_id = ? AND folder IN ({placeholders})''',
        [user_uid, account_id] + aliases,
    )
    row = await cursor.fetchone()
    return int((row[0] if row else 0) or 0)


async def list_cached_counts_by_account(account_id: str) -> dict[str, int]:
    """Return cached message counts grouped by canonical folder key."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT folder, COUNT(*)
           FROM cached_messages
           WHERE account_id = ?
           GROUP BY folder""",
        (account_id,),
    )
    rows = await cursor.fetchall()
    counts: dict[str, int] = {}
    for folder, count in rows:
        key = folder_key_for_path(str(folder or ""))
        counts[key] = counts.get(key, 0) + int(count or 0)
    return counts


async def get_cached_count(account_id: str, folder: str) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'SELECT COUNT(*) FROM cached_messages WHERE account_id = ? AND folder IN ({placeholders})',
        [account_id] + aliases,
    )
    row = await cursor.fetchone()
    return int((row[0] if row else 0) or 0)


async def get_cached_body_count(account_id: str, folder: str) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT COUNT(*)
            FROM cached_messages
            WHERE account_id = ? AND folder IN ({placeholders})
              AND (COALESCE(body_text, '') <> '' OR COALESCE(body_html, '') <> '')''',
        [account_id] + aliases,
    )
    row = await cursor.fetchone()
    return int((row[0] if row else 0) or 0)


async def get_cached_body_check_progress(account_id: str, folder: str) -> dict:
    """Return body-fill progress using the same completion rule as history sync."""
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT COUNT(*),
                   COALESCE(SUM(CASE
                       WHEN COALESCE(cm.body_text, '') = ''
                        AND COALESCE(cm.body_html, '') = ''
                        AND NOT EXISTS (
                            SELECT 1
                            FROM cached_message_empty_body_checks e
                            WHERE e.account_id = cm.account_id
                              AND e.folder = cm.folder
                              AND e.uid = cm.uid
                        )
                       THEN 1 ELSE 0
                   END), 0)
            FROM cached_messages cm
            WHERE cm.account_id = ? AND cm.folder IN ({placeholders})''',
        [account_id] + aliases,
    )
    row = await cursor.fetchone()
    total_count = max(int((row or (0, 0))[0] or 0), 0)
    remaining_count = max(int((row or (0, 0))[1] or 0), 0)
    remaining_count = min(remaining_count, total_count)
    return {
        "total_count": total_count,
        "checked_count": total_count - remaining_count,
        "remaining_count": remaining_count,
    }


async def list_cached_messages_needing_body_check(
    account_id: str,
    folder: str,
    limit: int = 100,
    include_checked_empty: bool = False,
) -> List[dict]:
    """Return cached messages whose remote detail has not been checked yet."""
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    checked_condition = "1 = 1" if include_checked_empty else "COALESCE(body_checked, 0) = 0"
    empty_check_condition = (
        "AND NOT EXISTS ("
        "SELECT 1 FROM cached_message_empty_body_checks e "
        "WHERE e.account_id = cached_messages.account_id "
        "AND e.folder = cached_messages.folder "
        "AND e.uid = cached_messages.uid"
        ")"
        if include_checked_empty
        else ""
    )
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT uid, subject, from_addr, to_addr, date, is_read, is_starred, has_attachments, storage_path
            FROM cached_messages
            WHERE account_id = ? AND folder IN ({placeholders})
              AND {checked_condition}
              AND COALESCE(body_text, '') = ''
              AND COALESCE(body_html, '') = ''
              {empty_check_condition}
            ORDER BY date DESC, uid DESC
            LIMIT ?''',
        [account_id] + aliases + [int(limit or 100)],
    )
    rows = await cursor.fetchall()
    return [
        {
            "uid": row[0],
            "subject": row[1] or "",
            "from_addr": row[2] or "",
            "to_addr": row[3] or "",
            "date": row[4] or "",
            "is_read": bool(row[5]),
            "is_starred": bool(row[6]),
            "has_attachments": bool(row[7]),
            "storage_path": row[8] or "",
        }
        for row in rows
    ]


async def mark_cached_messages_body_checked(account_id: str, folder: str, uids: list[int]) -> int:
    if not uids:
        return 0
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    uid_placeholders = ','.join('?' * len(uids))
    db = await get_db()
    cursor = await db.execute(
        f'''UPDATE cached_messages
            SET body_checked = 1, cached_at = ?
            WHERE account_id = ? AND folder IN ({placeholders}) AND uid IN ({uid_placeholders})''',
        [time.time(), account_id] + aliases + [int(uid) for uid in uids],
    )
    await db.commit()
    return cursor.rowcount


async def mark_cached_messages_empty_body_checked(account_id: str, folder: str, uids: list[int]) -> int:
    if not uids:
        return 0
    aliases = _expand_folder_aliases(folder)
    db = await get_db()
    checked_at = time.time()
    affected = 0
    for alias in aliases:
        for uid in uids:
            cursor = await db.execute(
                '''INSERT INTO cached_message_empty_body_checks
                   (account_id, folder, uid, checked_at)
                   VALUES (?, ?, ?, ?)
                   ON DUPLICATE KEY UPDATE checked_at = VALUES(checked_at)''',
                (account_id, alias, int(uid), checked_at),
            )
            affected += cursor.rowcount
    await db.commit()
    return affected


async def get_cached_attachment_rows(account_id: str) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        '''SELECT account_id, user_uid, uid, folder, part_number, filename, content_type,
                  size, content_id, is_inline, local_path, content_sha256,
                  last_accessed_at, cached_at
           FROM cached_attachments
           WHERE account_id = ?''',
        (account_id,),
    )
    rows = await cursor.fetchall()
    keys = (
        "account_id", "user_uid", "uid", "folder", "part_number", "filename",
        "content_type", "size", "content_id", "is_inline", "local_path",
        "content_sha256", "last_accessed_at", "cached_at",
    )
    result = []
    for row in rows:
        item = dict(zip(keys, row))
        item["is_inline"] = bool(item.get("is_inline"))
        item["local_path"] = item.get("local_path") or ""
        item["content_sha256"] = item.get("content_sha256") or ""
        item["last_accessed_at"] = float(item.get("last_accessed_at") or 0)
        result.append(item)
    return result


async def upsert_attachment_cache_object(
    content_sha256: str,
    size: int,
    local_path: str,
    created_at: float | None = None,
) -> None:
    db = await get_db()
    await db.execute(
        '''INSERT INTO attachment_cache_objects (content_sha256, size, local_path, created_at)
           VALUES (?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE size = VALUES(size), local_path = VALUES(local_path)''',
        (content_sha256, int(size or 0), local_path, created_at or time.time()),
    )
    await db.commit()


async def get_attachment_cache_object(content_sha256: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        '''SELECT content_sha256, size, local_path, created_at
           FROM attachment_cache_objects WHERE content_sha256 = ? LIMIT 1''',
        (content_sha256,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        "content_sha256": row[0] or "",
        "size": int(row[1] or 0),
        "local_path": row[2] or "",
        "created_at": float(row[3] or 0),
    }


async def pop_unreferenced_attachment_cache_object(content_sha256: str) -> Optional[dict]:
    db = await get_db()
    await db.execute("BEGIN")
    try:
        cursor = await db.execute(
            '''SELECT content_sha256, size, local_path, created_at
               FROM attachment_cache_objects WHERE content_sha256 = ? FOR UPDATE''',
            (content_sha256,),
        )
        row = await cursor.fetchone()
        if not row:
            await db.execute("COMMIT")
            return None
        cursor = await db.execute(
            '''SELECT COUNT(*) FROM cached_attachments
               WHERE content_sha256 = ?''',
            (content_sha256,),
        )
        count_row = await cursor.fetchone()
        if int((count_row or (0,))[0] or 0) > 0:
            await db.execute("COMMIT")
            return None
        await db.execute(
            "DELETE FROM attachment_cache_objects WHERE content_sha256 = ?",
            (content_sha256,),
        )
        await db.execute("COMMIT")
        return {
            "content_sha256": row[0] or "",
            "size": int(row[1] or 0),
            "local_path": row[2] or "",
            "created_at": float(row[3] or 0),
        }
    except Exception:
        await db.execute("ROLLBACK")
        raise


async def restore_attachment_cache_object(record: dict) -> None:
    await upsert_attachment_cache_object(
        str(record.get("content_sha256") or ""),
        int(record.get("size") or 0),
        str(record.get("local_path") or ""),
        float(record.get("created_at") or time.time()),
    )


async def replace_cached_attachment_object(attachment: CachedAttachment) -> str:
    db = await get_db()
    await db.execute("BEGIN")
    try:
        cursor = await db.execute(
            '''SELECT content_sha256 FROM cached_attachments
               WHERE account_id = ? AND folder = ? AND uid = ? AND part_number = ?
               FOR UPDATE''',
            (attachment.account_id, attachment.folder, attachment.uid, attachment.part_number),
        )
        row = await cursor.fetchone()
        previous_hash = str((row or ("",))[0] or "")
        if row is not None:
            await db.execute(
                '''UPDATE cached_attachments
                   SET user_uid = ?, filename = ?, content_type = ?, size = ?, content_id = ?,
                       is_inline = ?, local_path = ?, content_sha256 = ?, last_accessed_at = ?, cached_at = ?
                   WHERE account_id = ? AND folder = ? AND uid = ? AND part_number = ?''',
                (
                    attachment.user_uid,
                    attachment.filename or "",
                    attachment.content_type or "",
                    int(attachment.size or 0),
                    attachment.content_id or "",
                    1 if attachment.is_inline else 0,
                    attachment.local_path or "",
                    attachment.content_sha256 or "",
                    float(attachment.last_accessed_at or 0),
                    float(attachment.cached_at or time.time()),
                    attachment.account_id,
                    attachment.folder,
                    int(attachment.uid),
                    int(attachment.part_number),
                ),
            )
        else:
            await db.execute(
                '''INSERT INTO cached_attachments
                   (account_id, user_uid, uid, folder, part_number, filename, content_type,
                    size, content_id, is_inline, local_path, content_sha256,
                    last_accessed_at, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    attachment.account_id,
                    attachment.user_uid,
                    int(attachment.uid),
                    attachment.folder,
                    int(attachment.part_number),
                    attachment.filename or "",
                    attachment.content_type or "",
                    int(attachment.size or 0),
                    attachment.content_id or "",
                    1 if attachment.is_inline else 0,
                    attachment.local_path or "",
                    attachment.content_sha256 or "",
                    float(attachment.last_accessed_at or 0),
                    float(attachment.cached_at or time.time()),
                ),
            )
        await db.execute("COMMIT")
        if previous_hash and previous_hash != (attachment.content_sha256 or ""):
            return previous_hash
        return ""
    except Exception:
        await db.execute("ROLLBACK")
        raise


async def touch_cached_attachment_object(
    account_id: str,
    uid: int,
    folder: str,
    part_number: int,
    accessed_at: float,
) -> bool:
    aliases = _expand_folder_aliases(folder)
    placeholders = ",".join("?" * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''UPDATE cached_attachments SET last_accessed_at = ?
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
              AND part_number = ? AND content_sha256 <> '' ''',
        [accessed_at, account_id, int(uid)] + aliases + [int(part_number)],
    )
    await db.commit()
    return cursor.rowcount > 0


async def clear_cached_attachment_storage(
    account_id: str,
    uid: int,
    folder: str,
    part_number: int,
) -> str:
    aliases = _expand_folder_aliases(folder)
    placeholders = ",".join("?" * len(aliases))
    db = await get_db()
    await db.execute("BEGIN")
    try:
        cursor = await db.execute(
            f'''SELECT content_sha256 FROM cached_attachments
                WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
                  AND part_number = ? LIMIT 1 FOR UPDATE''',
            [account_id, int(uid)] + aliases + [int(part_number)],
        )
        row = await cursor.fetchone()
        previous_hash = str((row or ("",))[0] or "")
        await db.execute(
            f'''UPDATE cached_attachments
                SET content_sha256 = '', local_path = '', last_accessed_at = 0
                WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
                  AND part_number = ?''',
            [account_id, int(uid)] + aliases + [int(part_number)],
        )
        await db.execute("COMMIT")
        return previous_hash
    except Exception:
        await db.execute("ROLLBACK")
        raise


async def clear_user_attachment_hash_references(user_uid: str, content_sha256: str) -> int:
    db = await get_db()
    cursor = await db.execute(
        '''UPDATE cached_attachments
           SET content_sha256 = '', local_path = '', last_accessed_at = 0
           WHERE user_uid = ? AND is_inline = 0 AND content_sha256 = ?''',
        (user_uid, content_sha256),
    )
    await db.commit()
    return cursor.rowcount


async def get_user_attachment_cache_usage_bytes(user_uid: str) -> int:
    db = await get_db()
    cursor = await db.execute(
        '''SELECT COALESCE(SUM(objects.size), 0)
           FROM attachment_cache_objects objects
           JOIN (
               SELECT DISTINCT content_sha256
               FROM cached_attachments
               WHERE user_uid = ? AND is_inline = 0 AND content_sha256 <> ''
           ) refs ON refs.content_sha256 = objects.content_sha256''',
        (user_uid,),
    )
    row = await cursor.fetchone()
    return int((row or (0,))[0] or 0)


async def get_shared_attachment_cache_usage_bytes() -> int:
    db = await get_db()
    cursor = await db.execute("SELECT COALESCE(SUM(size), 0) FROM attachment_cache_objects")
    row = await cursor.fetchone()
    return int((row or (0,))[0] or 0)


async def list_user_attachment_cache_lru(user_uid: str) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        '''SELECT refs.content_sha256, objects.size, refs.last_accessed_at
           FROM attachment_cache_objects objects
           JOIN (
               SELECT content_sha256, MAX(last_accessed_at) AS last_accessed_at
               FROM cached_attachments
               WHERE user_uid = ? AND is_inline = 0 AND content_sha256 <> ''
               GROUP BY content_sha256
           ) refs ON refs.content_sha256 = objects.content_sha256
           ORDER BY refs.last_accessed_at ASC, refs.content_sha256 ASC''',
        (user_uid,),
    )
    return [
        {
            "content_sha256": row[0] or "",
            "size": int(row[1] or 0),
            "last_accessed_at": float(row[2] or 0),
        }
        for row in await cursor.fetchall()
    ]


async def list_attachment_hashes_for_messages(
    account_id: str,
    folder: str = "",
    uids: Optional[list[int]] = None,
) -> set[str]:
    conditions = ["account_id = ?", "content_sha256 <> ''"]
    params: list[Any] = [account_id]
    if folder:
        aliases = _expand_folder_aliases(folder)
        conditions.append(f"folder IN ({','.join('?' * len(aliases))})")
        params.extend(aliases)
    if uids is not None:
        if not uids:
            return set()
        conditions.append(f"uid IN ({','.join('?' * len(uids))})")
        params.extend(int(uid) for uid in uids)
    db = await get_db()
    cursor = await db.execute(
        f"SELECT DISTINCT content_sha256 FROM cached_attachments WHERE {' AND '.join(conditions)}",
        params,
    )
    return {str(row[0]) for row in await cursor.fetchall() if row and row[0]}


async def list_all_cached_attachment_rows() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        '''SELECT account_id, user_uid, uid, folder, part_number, filename, content_type,
                  size, content_id, is_inline, local_path, content_sha256,
                  last_accessed_at, cached_at
           FROM cached_attachments'''
    )
    keys = (
        "account_id", "user_uid", "uid", "folder", "part_number", "filename",
        "content_type", "size", "content_id", "is_inline", "local_path",
        "content_sha256", "last_accessed_at", "cached_at",
    )
    result = []
    for row in await cursor.fetchall():
        item = dict(zip(keys, row))
        item["is_inline"] = bool(item.get("is_inline"))
        result.append(item)
    return result


async def list_all_attachment_cache_objects() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT content_sha256, size, local_path, created_at FROM attachment_cache_objects"
    )
    return [
        {
            "content_sha256": row[0] or "",
            "size": int(row[1] or 0),
            "local_path": row[2] or "",
            "created_at": float(row[3] or 0),
        }
        for row in await cursor.fetchall()
    ]


async def list_cached_attachment_local_paths() -> set[str]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT DISTINCT local_path FROM cached_attachments WHERE local_path <> ''"
    )
    return {str(row[0]) for row in await cursor.fetchall() if row and row[0]}


async def get_cached_is_read(account_id: str, uid: int, folder: str) -> Optional[bool]:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT is_read FROM cached_messages
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
            LIMIT 1''',
        [account_id, uid] + aliases,
    )
    row = await cursor.fetchone()
    return bool(row[0]) if row else None


async def get_cached_message_detail(account_id: str, uid: int, folder: str):
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT id, uid, subject, from_addr, to_addr, cc, date, is_read, is_starred, folder,
                   body_text, body_html, has_attachments, message_id, account_id, storage_path
            FROM cached_messages
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
            ORDER BY date DESC LIMIT 1''',
        [account_id, uid] + aliases,
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        'id': str(row[1]),
        'uid': row[1],
        'subject': row[2] or '',
        'from_addr': row[3] or '',
        'to_addr': row[4] or '',
        'cc': row[5] or '',
        'date': row[6] or '',
        'is_read': bool(row[7]),
        'is_starred': bool(row[8]),
        'folder': row[9] or folder,
        'body_text': row[10] or '',
        'body_html': row[11] or '',
        'has_attachments': bool(row[12]),
        'message_id': row[13] or '',
        'account_id': row[14] or account_id,
        'storage_path': row[15] or '',
        'attachments': [],
    }


async def get_cached_attachment(account_id: str, uid: int, folder: str, part_number: int):
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT account_id, user_uid, uid, folder, part_number, filename, content_type, size,
                   content_id, is_inline, local_path, content_sha256, last_accessed_at, cached_at
            FROM cached_attachments
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders}) AND part_number = ?
            LIMIT 1''',
        [account_id, uid] + aliases + [part_number],
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return {
        'account_id': row[0], 'user_uid': row[1] or '', 'uid': row[2], 'folder': row[3],
        'part_number': row[4], 'filename': row[5] or '', 'content_type': row[6] or '',
        'size': row[7] or 0, 'content_id': row[8] or '', 'is_inline': bool(row[9]),
        'local_path': row[10] or '', 'content_sha256': row[11] or '',
        'last_accessed_at': float(row[12] or 0), 'cached_at': float(row[13] or 0),
    }


async def list_cached_attachments(account_id: str, uid: int, folder: str):
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT part_number, filename, content_type, size, content_id, is_inline,
                   local_path, content_sha256, last_accessed_at, cached_at
            FROM cached_attachments
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})
            ORDER BY part_number ASC''',
        [account_id, uid] + aliases,
    )
    rows = await cursor.fetchall()
    return [
        {
            'part_number': row[0], 'filename': row[1] or '', 'content_type': row[2] or '',
            'size': row[3] or 0, 'content_id': row[4] or '', 'is_inline': bool(row[5]),
            'local_path': row[6] or '', 'content_sha256': row[7] or '',
            'last_accessed_at': float(row[8] or 0), 'cached_at': float(row[9] or 0),
        }
        for row in rows
    ]


async def update_cached_message_storage_path(account_id: str, uid: int, folder: str, storage_path: str) -> bool:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''UPDATE cached_messages SET storage_path = ?, cached_at = ?
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})''',
        [storage_path, time.time(), account_id, uid] + aliases,
    )
    await db.commit()
    return cursor.rowcount > 0


async def update_cached_message_read(account_id: str, uid: int, folder: str, is_read: bool) -> bool:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''UPDATE cached_messages SET is_read = ?, cached_at = ?
            WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})''',
        [1 if is_read else 0, time.time(), account_id, uid] + aliases,
    )
    await db.commit()
    return cursor.rowcount > 0


async def enqueue_pending_read_sync(
    account_id: str,
    user_uid: str,
    uid: int,
    folder: str,
    desired_read: bool = True,
    error: str = "",
) -> None:
    now = time.time()
    db = await get_db()
    await db.execute(
        '''INSERT INTO pending_read_sync
           (account_id, user_uid, uid, folder, desired_read, attempts, last_error, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
             desired_read = VALUES(desired_read),
             last_error = VALUES(last_error),
             updated_at = VALUES(updated_at)''',
        (account_id, user_uid, uid, folder, 1 if desired_read else 0, str(error)[:1024], now, now),
    )
    await db.commit()


async def list_pending_read_sync(limit: int = 100) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        '''SELECT account_id, user_uid, uid, folder, desired_read, attempts, last_error, created_at, updated_at
           FROM pending_read_sync
           ORDER BY updated_at ASC
           LIMIT ?''',
        (limit,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "account_id": row[0],
            "user_uid": row[1],
            "uid": int(row[2] or 0),
            "folder": row[3] or "INBOX",
            "desired_read": bool(row[4]),
            "attempts": int(row[5] or 0),
            "last_error": row[6] or "",
            "created_at": float(row[7] or 0),
            "updated_at": float(row[8] or 0),
        }
        for row in rows
    ]


async def delete_pending_read_sync(account_id: str, uid: int, folder: str) -> bool:
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM pending_read_sync WHERE account_id = ? AND uid = ? AND folder = ?",
        (account_id, uid, folder),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_pending_read_sync_by_account(account_id: str) -> int:
    db = await get_db()
    cursor = await db.execute("DELETE FROM pending_read_sync WHERE account_id = ?", (account_id,))
    await db.commit()
    return cursor.rowcount


async def mark_pending_read_sync_failed(account_id: str, uid: int, folder: str, error: str) -> None:
    db = await get_db()
    await db.execute(
        '''UPDATE pending_read_sync
           SET attempts = attempts + 1, last_error = ?, updated_at = ?
           WHERE account_id = ? AND uid = ? AND folder = ?''',
        (str(error)[:1024], time.time(), account_id, uid, folder),
    )
    await db.commit()


async def mark_all_cached_messages_read(account_id: str, folder: str) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''UPDATE cached_messages SET is_read = 1, cached_at = ?
            WHERE account_id = ? AND folder IN ({placeholders}) AND is_read = 0''',
        [time.time(), account_id] + aliases,
    )
    await db.commit()
    return cursor.rowcount


async def batch_update_cached_messages_read(account_id: str, uids: list[int], folder: str, is_read: bool) -> int:
    if not uids:
        return 0
    aliases = _expand_folder_aliases(folder)
    db = await get_db()
    affected = 0
    for uid in uids:
        placeholders = ','.join('?' * len(aliases))
        cursor = await db.execute(
            f'''UPDATE cached_messages SET is_read = ?, cached_at = ?
                WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})''',
            [1 if is_read else 0, time.time(), account_id, uid] + aliases,
        )
        affected += cursor.rowcount
    await db.commit()
    return affected


async def batch_update_is_read(account_id: str, folder: str, updates: list[tuple[int, int]]) -> int:
    if not updates:
        return 0
    aliases = _expand_folder_aliases(folder)
    db = await get_db()
    affected = 0
    for uid, is_read in updates:
        placeholders = ','.join('?' * len(aliases))
        cursor = await db.execute(
            f'''UPDATE cached_messages SET is_read = ?, cached_at = ?
                WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})''',
            [is_read, time.time(), account_id, uid] + aliases,
        )
        affected += cursor.rowcount
    await db.commit()
    return affected


async def delete_cached_message(account_id: str, uid: int, folder: str) -> bool:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'DELETE FROM cached_messages WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})',
        [account_id, uid] + aliases,
    )
    await db.execute(
        f'DELETE FROM cached_attachments WHERE account_id = ? AND uid = ? AND folder IN ({placeholders})',
        [account_id, uid] + aliases,
    )
    await db.commit()
    return cursor.rowcount > 0


async def batch_delete_cached_messages(account_id: str, uids: list[int], folder: str) -> int:
    if not uids:
        return 0
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    uid_placeholders = ','.join('?' * len(uids))
    db = await get_db()
    cursor = await db.execute(
        f'''DELETE FROM cached_messages WHERE account_id = ? AND folder IN ({placeholders}) AND uid IN ({uid_placeholders})''',
        [account_id] + aliases + uids,
    )
    await db.execute(
        f'''DELETE FROM cached_attachments WHERE account_id = ? AND folder IN ({placeholders}) AND uid IN ({uid_placeholders})''',
        [account_id] + aliases + uids,
    )
    await db.commit()
    return cursor.rowcount


async def purge_deleted_from_cache(account_id: str, folder: str, valid_uids: set[int]) -> int:
    aliases = _expand_folder_aliases(folder)
    placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'SELECT uid FROM cached_messages WHERE account_id = ? AND folder IN ({placeholders})',
        [account_id] + aliases,
    )
    rows = await cursor.fetchall()
    cached_uids = {int(row[0]) for row in rows if row and row[0] is not None}
    to_delete = sorted(cached_uids - set(valid_uids))
    if not to_delete:
        return 0
    return await batch_delete_cached_messages(account_id, to_delete, folder)


async def upsert_cached_messages(messages: list[CachedMessage]) -> int:
    if not messages:
        return 0
    db = await get_db()
    affected = 0
    for msg in messages:
        message_id = build_cached_message_id(msg.account_id, msg.folder, msg.uid)
        body_text = _truncate_text_bytes(msg.body_text or '', DB_MESSAGE_BODY_MAX_BYTES)
        body_html = _truncate_text_bytes(msg.body_html or '', DB_MESSAGE_BODY_MAX_BYTES)
        body_checked = bool(getattr(msg, "body_checked", False) or body_text or body_html)
        cursor = await db.execute(
            '''INSERT INTO cached_messages
               (id, account_id, user_uid, uid, folder, subject, from_addr, to_addr, cc, date,
                is_read, is_starred, has_attachments, body_text, body_html, message_id,
                body_checked, storage_path, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON DUPLICATE KEY UPDATE
               subject = VALUES(subject),
               from_addr = VALUES(from_addr),
               to_addr = VALUES(to_addr),
               cc = VALUES(cc),
               date = VALUES(date),
               is_read = VALUES(is_read),
               is_starred = VALUES(is_starred),
               has_attachments = VALUES(has_attachments),
               body_text = COALESCE(VALUES(body_text), cached_messages.body_text),
               body_html = COALESCE(VALUES(body_html), cached_messages.body_html),
               message_id = CASE WHEN VALUES(message_id) <> '' THEN VALUES(message_id) ELSE cached_messages.message_id END,
               body_checked = GREATEST(VALUES(body_checked), COALESCE(cached_messages.body_checked, 0)),
               storage_path = COALESCE(VALUES(storage_path), cached_messages.storage_path),
               cached_at = VALUES(cached_at)''',
            (
                message_id, msg.account_id, msg.user_uid, msg.uid, msg.folder, msg.subject,
                msg.from_addr, msg.to_addr, msg.cc or '', msg.date, 1 if msg.is_read else 0,
                1 if msg.is_starred else 0, 1 if msg.has_attachments else 0,
                body_text, body_html, msg.message_id or '', 1 if body_checked else 0,
                msg.storage_path or '', msg.cached_at or time.time(),
            ),
        )
        affected += cursor.rowcount
    await db.commit()
    return affected


async def upsert_cached_attachments(attachments: list[CachedAttachment]) -> int:
    if not attachments:
        return 0
    db = await get_db()
    affected = 0
    for att in attachments:
        cursor = await db.execute(
            '''INSERT INTO cached_attachments
               (account_id, user_uid, uid, folder, part_number, filename, content_type, size,
                content_id, is_inline, local_path, content_sha256, last_accessed_at, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON DUPLICATE KEY UPDATE
               user_uid = VALUES(user_uid),
               filename = VALUES(filename),
               content_type = VALUES(content_type),
               size = VALUES(size),
               content_id = VALUES(content_id),
               is_inline = VALUES(is_inline),
               content_sha256 = CASE
                   WHEN VALUES(content_sha256) <> '' THEN VALUES(content_sha256)
                   ELSE cached_attachments.content_sha256
               END,
               local_path = CASE
                   WHEN VALUES(content_sha256) <> '' THEN VALUES(local_path)
                   ELSE cached_attachments.local_path
               END,
               last_accessed_at = CASE
                   WHEN VALUES(content_sha256) <> '' THEN VALUES(last_accessed_at)
                   ELSE cached_attachments.last_accessed_at
               END,
               cached_at = VALUES(cached_at)''',
            (
                att.account_id, att.user_uid, att.uid, att.folder, att.part_number,
                att.filename or '', att.content_type or '', att.size or 0,
                att.content_id or '', 1 if att.is_inline else 0, att.local_path or '',
                att.content_sha256 or '', att.last_accessed_at or 0,
                att.cached_at or time.time(),
            ),
        )
        affected += cursor.rowcount
    await db.commit()
    return affected


async def get_cached_messages_by_folder(user_uid: str, account_id: str, folder: str, page: int = 1, page_size: int = 40, read_filter: str = '', attachment_filter: bool = False) -> dict:
    db = await get_db()
    aliases = _expand_folder_aliases(folder)
    folder_placeholders = ','.join('?' * len(aliases))
    conditions = ['user_uid = ?', 'account_id = ?', f'folder IN ({folder_placeholders})']
    params = [user_uid, account_id] + aliases
    if read_filter == 'unread':
        conditions.append('is_read = 0')
    elif read_filter == 'read':
        conditions.append('is_read = 1')
    if attachment_filter:
        conditions.append('has_attachments = 1')
    where_clause = ' AND '.join(conditions)
    cursor = await db.execute(f'SELECT COUNT(*) FROM cached_messages WHERE {where_clause}', params)
    filtered_total = int((await cursor.fetchone())[0] or 0)
    cursor = await db.execute(
        f'''SELECT COUNT(*)
            FROM cached_messages
            WHERE user_uid = ? AND account_id = ? AND folder IN ({folder_placeholders}) AND is_read = 0''',
        [user_uid, account_id] + aliases,
    )
    unread_total = int((await cursor.fetchone())[0] or 0)
    total = filtered_total
    offset = max(0, (page - 1) * page_size)
    cursor = await db.execute(
        f'''SELECT id, uid, subject, from_addr, to_addr, date, is_read, is_starred, folder, has_attachments, account_id
            FROM cached_messages WHERE {where_clause}
            ORDER BY date DESC, uid DESC LIMIT ? OFFSET ?''',
        params + [page_size, offset],
    )
    rows = await cursor.fetchall()
    messages = [{'id': str(row[0]), 'uid': row[1], 'subject': row[2] or '', 'from_addr': row[3] or '', 'to_addr': row[4] or '', 'date': row[5] or '', 'is_read': bool(row[6]), 'is_starred': bool(row[7]), 'folder': row[8] or folder, 'has_attachments': bool(row[9]), 'account_id': row[10] or account_id} for row in rows]
    result = {'messages': messages, 'total': total, 'unread_total': unread_total, 'page': page, 'page_size': page_size}
    result['cached_count'] = filtered_total
    return result


async def search_cached_messages_by_folder(user_uid: str, account_id: str, folder: str, keyword: str, page: int = 1, page_size: int = 40, read_filter: str = '', attachment_filter: bool = False) -> dict:
    trimmed = (keyword or '').strip()
    if not trimmed:
        return await get_cached_messages_by_folder(user_uid, account_id, folder, page, page_size, read_filter, attachment_filter)
    aliases = _expand_folder_aliases(folder)
    folder_placeholders = ','.join('?' * len(aliases))
    conditions = ['user_uid = ?', 'account_id = ?', f'folder IN ({folder_placeholders})', '(subject LIKE ? OR from_addr LIKE ? OR to_addr LIKE ? OR body_text LIKE ? OR body_html LIKE ?)']
    like = '%' + trimmed + '%'
    params = [user_uid, account_id] + aliases + [like, like, like, like, like]
    if read_filter == 'unread':
        conditions.append('is_read = 0')
    elif read_filter == 'read':
        conditions.append('is_read = 1')
    if attachment_filter:
        conditions.append('has_attachments = 1')
    where_clause = ' AND '.join(conditions)
    db = await get_db()
    cursor = await db.execute(f'''SELECT COUNT(*), SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) FROM cached_messages WHERE {where_clause}''', params)
    row = await cursor.fetchone()
    total = int(row[0] or 0) if row else 0
    unread_total = int(row[1] or 0) if row else 0
    offset = max(0, (page - 1) * page_size)
    cursor = await db.execute(f'''SELECT id, uid, subject, from_addr, to_addr, date, is_read, is_starred, folder, has_attachments, account_id FROM cached_messages WHERE {where_clause} ORDER BY date DESC, uid DESC LIMIT ? OFFSET ?''', params + [page_size, offset])
    rows = await cursor.fetchall()
    messages = [{'id': str(row[0]), 'uid': row[1], 'subject': row[2] or '', 'from_addr': row[3] or '', 'to_addr': row[4] or '', 'date': row[5] or '', 'is_read': bool(row[6]), 'is_starred': bool(row[7]), 'folder': row[8] or folder, 'has_attachments': bool(row[9]), 'account_id': row[10] or account_id} for row in rows]
    return {'messages': messages, 'total': total, 'unread_total': unread_total, 'page': page, 'page_size': page_size}


async def get_folder_filter_counts(user_uid: str, account_id: str, folder: str) -> dict:
    aliases = _expand_folder_aliases(folder)
    folder_placeholders = ','.join('?' * len(aliases))
    db = await get_db()
    cursor = await db.execute(
        f'''SELECT COUNT(*),
                   SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN is_read = 1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN has_attachments = 1 THEN 1 ELSE 0 END)
            FROM cached_messages
            WHERE user_uid = ? AND account_id = ? AND folder IN ({folder_placeholders})''',
        [user_uid, account_id] + aliases,
    )
    row = await cursor.fetchone()
    return {
        'all': int(row[0] or 0) if row else 0,
        'unread': int(row[1] or 0) if row else 0,
        'read': int(row[2] or 0) if row else 0,
        'attachments': int(row[3] or 0) if row else 0,
    }


async def create_notification(notification: Notification) -> Notification:
    db = await get_db()
    await db.execute(
        """INSERT INTO notifications (
             id, user_uid, account_id, provider, email, folder, is_read, created_at, type, message,
             message_cache_id, message_uid, rfc_message_id, subject, from_addr, to_addr, cc,
             mail_date, body_preview, has_attachments, batch_count, extra_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            notification.id, notification.user_uid, notification.account_id,
            notification.provider, notification.email, notification.folder,
            1 if notification.is_read else 0, notification.created_at,
            notification.type, notification.message,
            notification.message_cache_id, int(notification.message_uid or 0),
            notification.rfc_message_id, notification.subject, notification.from_addr,
            notification.to_addr, notification.cc, notification.mail_date,
            notification.body_preview, 1 if notification.has_attachments else 0,
            int(notification.batch_count or 1), notification.extra_json,
        ),
    )
    await db.commit()
    return notification


async def get_notifications(user_uid: str, limit: int = 50) -> List[Notification]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM notifications WHERE user_uid = ? ORDER BY created_at DESC LIMIT ?",
        (user_uid, limit),
    )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    result = []
    for row in rows:
        data = dict(zip(columns, row))
        data["is_read"] = bool(data.get("is_read"))
        data["has_attachments"] = bool(data.get("has_attachments"))
        result.append(Notification(**{key: value for key, value in data.items() if key in Notification.model_fields}))
    return result


async def mark_notification_read(notification_id: str, user_uid: str) -> bool:
    db = await get_db()
    cursor = await db.execute('UPDATE notifications SET is_read = 1 WHERE id = ? AND user_uid = ?', (notification_id, user_uid))
    await db.commit()
    return cursor.rowcount > 0


async def mark_all_notifications_read(user_uid: str) -> int:
    db = await get_db()
    cursor = await db.execute('UPDATE notifications SET is_read = 1 WHERE user_uid = ? AND is_read = 0', (user_uid,))
    await db.commit()
    return cursor.rowcount


async def clear_notifications(user_uid: str) -> int:
    db = await get_db()
    cursor = await db.execute('DELETE FROM notifications WHERE user_uid = ?', (user_uid,))
    await db.commit()
    return cursor.rowcount


async def get_signatures(user_uid: str = '') -> List[Signature]:
    db = await get_db()
    if user_uid:
        cursor = await db.execute('SELECT * FROM signatures WHERE user_uid = ? ORDER BY is_default DESC, id ASC', (user_uid,))
    else:
        cursor = await db.execute('SELECT * FROM signatures ORDER BY is_default DESC, id ASC')
    rows = await cursor.fetchall()
    return [Signature(**dict(zip([d[0] for d in cursor.description], row))) for row in rows]


async def create_signature(sig: Signature) -> Signature:
    db = await get_db()
    now = time.time()
    await db.execute('BEGIN')
    try:
        await _clear_signature_defaults(db, sig)
        cursor = await db.execute(
            '''INSERT INTO signatures
               (name, content_html, is_default, is_reply_default, account_id, user_uid, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                sig.name,
                sig.content_html,
                1 if sig.is_default else 0,
                1 if sig.is_reply_default else 0,
                sig.account_id or '',
                sig.user_uid or '',
                now,
                now,
            ),
        )
        await db.execute('COMMIT')
    except Exception:
        await db.execute('ROLLBACK')
        raise
    sig.id = cursor.lastrowid
    sig.created_at = now
    sig.updated_at = now
    return sig


HISTORY_SYNC_STALE_SECONDS = 600
HISTORY_SYNC_STALE_ERROR = "同步任务超过 10 分钟没有进度，已标记为失败，可重试"


def _history_job_row_to_dict(columns: list[str], row) -> dict[str, Any]:
    return dict(zip(columns, row))


async def _normalize_history_sync_job_staleness(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("status") not in {"pending", "running"}:
        return job
    now = time.time()
    if now - float(job.get("updated_at") or 0) <= HISTORY_SYNC_STALE_SECONDS:
        return job

    await update_history_sync_job(
        job["id"],
        status="failed",
        error_message=HISTORY_SYNC_STALE_ERROR,
        finished_at=now,
    )
    normalized = dict(job)
    normalized.update(
        status="failed",
        error_message=HISTORY_SYNC_STALE_ERROR,
        updated_at=now,
        finished_at=now,
    )
    return normalized


async def get_history_sync_job(account_id: str, job_type: str = "history_sync") -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                  total_folders, completed_folders, fetched_messages, downloaded_attachments,
                  downloaded_inline_images, error_message, created_at, updated_at, finished_at
           FROM history_sync_jobs
           WHERE account_id = ? AND job_type = ?
           ORDER BY updated_at DESC, created_at DESC
           LIMIT 1""",
        (account_id, job_type),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    job = _history_job_row_to_dict(columns, row)
    return await _normalize_history_sync_job_staleness(job)


async def list_history_sync_jobs(user_uid: str, job_type: str | None = None) -> List[dict]:
    db = await get_db()
    if job_type:
        cursor = await db.execute(
            """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                      total_folders, completed_folders, fetched_messages, downloaded_attachments,
                      downloaded_inline_images, error_message, created_at, updated_at, finished_at
               FROM history_sync_jobs
               WHERE user_uid = ? AND job_type = ?
               ORDER BY updated_at DESC, created_at DESC""",
            (user_uid, job_type),
        )
    else:
        cursor = await db.execute(
            """SELECT id, account_id, user_uid, job_type, status, current_folder, current_page, current_uid,
                      total_folders, completed_folders, fetched_messages, downloaded_attachments,
                      downloaded_inline_images, error_message, created_at, updated_at, finished_at
               FROM history_sync_jobs
               WHERE user_uid = ?
               ORDER BY updated_at DESC, created_at DESC""",
            (user_uid,),
        )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    jobs = []
    for row in rows:
        job = _history_job_row_to_dict(columns, row)
        jobs.append(await _normalize_history_sync_job_staleness(job))
    return jobs


async def activate_account(
    account_id: str,
    user_uid: str = '',
    *,
    credentials_json: str | None = None,
    status: str = "active",
) -> bool:
    db = await get_db()
    actual_user_uid = user_uid
    actual_credentials_json = credentials_json

    # Backward compatibility for old call sites:
    # activate_account(account_id, credentials_json, status="connected")
    if actual_credentials_json is None and actual_user_uid and actual_user_uid.lstrip().startswith(("{", "[")):
        actual_credentials_json = actual_user_uid
        actual_user_uid = ''

    assignments = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, time.time()]
    if actual_credentials_json is not None:
        assignments.append("credentials_json = ?")
        params.append(actual_credentials_json)
    sql = f"UPDATE accounts SET {', '.join(assignments)} WHERE id = ?"
    params.append(account_id)
    if actual_user_uid:
        sql += " AND user_uid = ?"
        params.append(actual_user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


async def deactivate_account(
    account_id: str,
    user_uid: str = '',
    *,
    status: str = "offline",
    clear_credentials: bool = False,
) -> bool:
    db = await get_db()
    assignments = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, time.time()]
    if clear_credentials:
        assignments.append("credentials_json = ?")
        params.append("")
    sql = f"UPDATE accounts SET {', '.join(assignments)} WHERE id = ?"
    params.append(account_id)
    if user_uid:
        sql += " AND user_uid = ?"
        params.append(user_uid)
    cursor = await db.execute(sql, params)
    await db.commit()
    return cursor.rowcount > 0


# ==================== Upstream contacts ====================


def _aggregate_contact_candidates(
    rows,
    *,
    own_email: str,
    existing_emails: set[str],
    search: str = "",
    limit: int = 500,
) -> list[dict]:
    """Aggregate local cached mail headers into importable contact candidates."""
    own = (own_email or "").strip().lower()
    existing = {(email or "").strip().lower() for email in existing_emails if email}
    candidates: dict[str, dict] = {}

    def parsed(value: str) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for name, address in getaddresses([value or ""]):
            normalized = (address or "").strip().lower()
            if not normalized or "@" not in normalized:
                continue
            result.append(((name or "").strip().strip('"'), normalized))
        return result

    for row in rows or []:
        from_addr, to_addr, cc_addr, mail_date = (list(row) + ["", "", "", ""])[:4]
        from_entries = parsed(from_addr)
        outgoing = any(address == own for _name, address in from_entries)
        entries = parsed(to_addr) + parsed(cc_addr) if outgoing else from_entries
        direction = "sent_count" if outgoing else "received_count"
        seen_in_message: set[str] = set()
        for name, address in entries:
            if address in seen_in_message or address == own or address in existing:
                continue
            seen_in_message.add(address)
            item = candidates.setdefault(
                address,
                {
                    "name": name,
                    "email": address,
                    "sent_count": 0,
                    "received_count": 0,
                    "total_count": 0,
                    "last_date": "",
                },
            )
            if name and not item["name"]:
                item["name"] = name
            item[direction] += 1
            item["total_count"] += 1
            date_value = str(mail_date or "")
            if date_value > item["last_date"]:
                item["last_date"] = date_value

    keyword = (search or "").strip().lower()
    items = [
        item for item in candidates.values()
        if not keyword or keyword in item["email"] or keyword in item["name"].lower()
    ]
    items.sort(key=lambda item: item["email"])
    items.sort(key=lambda item: item["last_date"], reverse=True)
    items.sort(key=lambda item: item["total_count"], reverse=True)
    return items[:max(1, min(int(limit or 500), 1000))]


async def get_contact_candidates(
    user_uid: str,
    account_id: str,
    search: str = "",
    limit: int = 500,
) -> Optional[list[dict]]:
    """Return candidates from one owned mailbox's locally cached message headers."""
    db = await get_db()
    account_cursor = await db.execute(
        "SELECT email FROM accounts WHERE id = ? AND user_uid = ? LIMIT 1",
        (account_id, user_uid),
    )
    account_row = await account_cursor.fetchone()
    if not account_row:
        return None

    existing_cursor = await db.execute(
        """SELECT ce.email
           FROM contact_emails ce
           INNER JOIN contacts c ON c.id = ce.contact_id
           WHERE c.user_uid = ?""",
        (user_uid,),
    )
    existing_rows = await existing_cursor.fetchall()
    existing_emails = {str(row[0] or "").strip().lower() for row in existing_rows}

    messages_cursor = await db.execute(
        """SELECT from_addr, to_addr, cc, date
           FROM cached_messages
           WHERE user_uid = ? AND account_id = ?
           ORDER BY cached_at DESC
           LIMIT 20000""",
        (user_uid, account_id),
    )
    message_rows = await messages_cursor.fetchall()
    return _aggregate_contact_candidates(
        message_rows,
        own_email=str(account_row[0] or ""),
        existing_emails=existing_emails,
        search=search,
        limit=limit,
    )


async def _fetch_emails_for_contacts(db, contact_ids: list[int]) -> dict[int, list[dict]]:
    if not contact_ids:
        return {}
    placeholders = ",".join("?" * len(contact_ids))
    cursor = await db.execute(
        f"SELECT id, contact_id, email, is_primary FROM contact_emails WHERE contact_id IN ({placeholders}) ORDER BY is_primary DESC, id ASC",
        contact_ids,
    )
    rows = await cursor.fetchall()
    result: dict[int, list[dict]] = {int(contact_id): [] for contact_id in contact_ids}
    for row in rows:
        result.setdefault(int(row[1]), []).append(
            {"id": int(row[0]), "email": row[2] or "", "is_primary": bool(row[3])}
        )
    return result


async def get_contacts(user_uid: str, search: str = "") -> list[dict]:
    db = await get_db()
    if search:
        like = f"%{search}%"
        cursor = await db.execute(
            """SELECT DISTINCT c.id, c.user_uid, c.name, c.phone, c.company,
                              c.remark, c.group_name, c.created_at, c.updated_at
               FROM contacts c LEFT JOIN contact_emails ce ON ce.contact_id = c.id
               WHERE c.user_uid = ? AND (c.name LIKE ? OR ce.email LIKE ?)
               ORDER BY c.name ASC, c.id ASC""",
            (user_uid, like, like),
        )
    else:
        cursor = await db.execute(
            """SELECT id, user_uid, name, phone, company, remark, group_name, created_at, updated_at
               FROM contacts WHERE user_uid = ? ORDER BY name ASC, id ASC""",
            (user_uid,),
        )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    contacts = [dict(zip(columns, row)) for row in rows]
    emails = await _fetch_emails_for_contacts(db, [int(item["id"]) for item in contacts])
    for item in contacts:
        item["emails"] = emails.get(int(item["id"]), [])
    return contacts


async def get_contact_by_id(contact_id: int, user_uid: str) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT id, user_uid, name, phone, company, remark, group_name, created_at, updated_at
           FROM contacts WHERE id = ? AND user_uid = ?""",
        (contact_id, user_uid),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    columns = [description[0] for description in cursor.description]
    contact = dict(zip(columns, row))
    contact["emails"] = (await _fetch_emails_for_contacts(db, [contact_id])).get(contact_id, [])
    return contact


async def create_contact(
    user_uid: str,
    name: str,
    emails: list[str],
    phone: str = "",
    company: str = "",
    remark: str = "",
    group_name: str = "",
) -> dict:
    db = await get_db()
    now = time.time()
    cursor = await db.execute(
        """INSERT INTO contacts (user_uid, name, phone, company, remark, group_name, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_uid, name.strip(), phone.strip(), company.strip(), remark.strip(), group_name.strip(), now, now),
    )
    contact_id = int(cursor.lastrowid)
    email_rows: list[dict] = []
    seen: set[str] = set()
    for email_value in emails:
        normalized = (email_value or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        is_primary = not email_rows
        email_cursor = await db.execute(
            "INSERT INTO contact_emails (contact_id, email, is_primary, created_at) VALUES (?, ?, ?, ?)",
            (contact_id, normalized, 1 if is_primary else 0, now),
        )
        email_rows.append({"id": int(email_cursor.lastrowid), "email": normalized, "is_primary": is_primary})
    await db.commit()
    return {
        "id": contact_id, "user_uid": user_uid, "name": name.strip(),
        "phone": phone.strip(), "company": company.strip(), "remark": remark.strip(),
        "group_name": group_name.strip(), "created_at": now, "updated_at": now,
        "emails": email_rows,
    }


async def update_contact(
    contact_id: int,
    user_uid: str,
    name: str,
    emails: list[str],
    phone: str = "",
    company: str = "",
    remark: str = "",
    group_name: str = "",
) -> bool:
    db = await get_db()
    cursor = await db.execute(
        """UPDATE contacts SET name = ?, phone = ?, company = ?, remark = ?, group_name = ?, updated_at = ?
           WHERE id = ? AND user_uid = ?""",
        (name.strip(), phone.strip(), company.strip(), remark.strip(), group_name.strip(), time.time(), contact_id, user_uid),
    )
    if cursor.rowcount == 0:
        return False
    await db.execute("DELETE FROM contact_emails WHERE contact_id = ?", (contact_id,))
    now = time.time()
    seen: set[str] = set()
    inserted = 0
    for email_value in emails:
        normalized = (email_value or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        await db.execute(
            "INSERT INTO contact_emails (contact_id, email, is_primary, created_at) VALUES (?, ?, ?, ?)",
            (contact_id, normalized, 1 if inserted == 0 else 0, now),
        )
        inserted += 1
    await db.commit()
    return True


async def delete_contact(contact_id: int, user_uid: str) -> bool:
    db = await get_db()
    owner_cursor = await db.execute(
        "SELECT id FROM contacts WHERE id = ? AND user_uid = ?",
        (contact_id, user_uid),
    )
    if not await owner_cursor.fetchone():
        return False
    await db.execute("DELETE FROM contact_emails WHERE contact_id = ?", (contact_id,))
    cursor = await db.execute(
        "DELETE FROM contacts WHERE id = ? AND user_uid = ?",
        (contact_id, user_uid),
    )
    await db.commit()
    return cursor.rowcount > 0


async def upsert_contact_by_email(user_uid: str, name: str, email: str) -> tuple[dict, bool]:
    db = await get_db()
    normalized = (email or "").strip().lower()
    cursor = await db.execute(
        """SELECT c.id, c.user_uid, c.name, c.phone, c.company, c.remark,
                  c.group_name, c.created_at, c.updated_at
           FROM contacts c JOIN contact_emails ce ON ce.contact_id = c.id
           WHERE c.user_uid = ? AND LOWER(ce.email) = ? LIMIT 1""",
        (user_uid, normalized),
    )
    row = await cursor.fetchone()
    if row:
        columns = [description[0] for description in cursor.description]
        contact = dict(zip(columns, row))
        contact_id = int(contact["id"])
        contact["emails"] = (await _fetch_emails_for_contacts(db, [contact_id])).get(contact_id, [])
        return contact, False
    return await create_contact(user_uid, name, [normalized]), True


def _address_field_contains_email(field: str, email_value: str) -> bool:
    from email.utils import getaddresses

    normalized = (email_value or "").strip().lower()
    return any(address.strip().lower() == normalized for _name, address in getaddresses([field or ""]))


async def get_contact_stats(user_uid: str, email: str) -> dict:
    normalized = (email or "").strip().lower()
    if not normalized or "@" not in normalized:
        return {"count": 0, "last_date": ""}
    db = await get_db()
    cursor = await db.execute(
        """SELECT date, from_addr, to_addr, cc FROM cached_messages
           WHERE user_uid = ? AND (
             LOCATE(?, LOWER(from_addr)) > 0
             OR LOCATE(?, LOWER(to_addr)) > 0
             OR LOCATE(?, LOWER(cc)) > 0
           )""",
        (user_uid, normalized, normalized, normalized),
    )
    rows = await cursor.fetchall()
    matched_dates = [
        row[0] or "" for row in rows
        if _address_field_contains_email(row[1] or "", normalized)
        or _address_field_contains_email(row[2] or "", normalized)
        or _address_field_contains_email(row[3] or "", normalized)
    ]
    return {"count": len(matched_dates), "last_date": max(matched_dates, default="")}


# ==================== Upstream unified inbox ====================

async def get_unified_inbox_messages(
    user_uid: str,
    account_ids: list[str],
    page: int = 1,
    page_size: int = 40,
    account_filter: str = "",
    read_filter: str = "",
    attachment_filter: bool = False,
) -> dict:
    if not account_ids:
        return {"messages": [], "total": 0, "unread_total": 0, "page": page, "page_size": page_size}
    placeholders = ",".join("?" * len(account_ids))
    conditions = [
        "m.user_uid = ?",
        "UPPER(m.folder) = 'INBOX'",
        f"m.account_id IN ({placeholders})",
        "a.user_uid = ?",
    ]
    params: list[Any] = [user_uid, *account_ids, user_uid]
    if account_filter and account_filter in account_ids:
        conditions.append("m.account_id = ?")
        params.append(account_filter)
    if read_filter == "unread":
        conditions.append("m.is_read = 0")
    elif read_filter == "read":
        conditions.append("m.is_read = 1")
    if attachment_filter:
        conditions.append("m.has_attachments = 1")
    where = " AND ".join(conditions)
    db = await get_db()
    cursor = await db.execute(
        f"""SELECT COUNT(*), COALESCE(SUM(CASE WHEN m.is_read = 0 THEN 1 ELSE 0 END), 0)
            FROM cached_messages m JOIN accounts a ON a.id = m.account_id WHERE {where}""",
        params,
    )
    total_row = await cursor.fetchone() or (0, 0)
    offset = (page - 1) * page_size
    cursor = await db.execute(
        f"""SELECT m.id, m.uid, m.subject, m.from_addr, m.to_addr, m.cc, m.date,
                   m.is_read, m.is_starred, m.folder, m.account_id, m.has_attachments
            FROM cached_messages m JOIN accounts a ON a.id = m.account_id
            WHERE {where} ORDER BY m.date DESC, m.uid DESC LIMIT ? OFFSET ?""",
        [*params, page_size, offset],
    )
    rows = await cursor.fetchall()
    messages = [
        {
            "id": row[0], "uid": int(row[1]), "subject": row[2] or "",
            "from_addr": row[3] or "", "to_addr": row[4] or "", "cc": row[5] or "",
            "date": row[6] or "", "is_read": bool(row[7]), "is_starred": bool(row[8]),
            "folder": row[9], "account_id": row[10], "has_attachments": bool(row[11]),
        }
        for row in rows
    ]
    return {
        "messages": messages,
        "total": int(total_row[0] or 0),
        "unread_total": int(total_row[1] or 0),
        "page": page,
        "page_size": page_size,
    }


async def get_unified_inbox_filter_counts(user_uid: str, account_ids: list[str], account_filter: str = "") -> dict:
    if not account_ids:
        return {"all": 0, "unread": 0, "read": 0, "attachments": 0}
    placeholders = ",".join("?" * len(account_ids))
    conditions = [
        "m.user_uid = ?",
        "UPPER(m.folder) = 'INBOX'",
        f"m.account_id IN ({placeholders})",
        "a.user_uid = ?",
    ]
    params: list[Any] = [user_uid, *account_ids, user_uid]
    if account_filter and account_filter in account_ids:
        conditions.append("m.account_id = ?")
        params.append(account_filter)
    db = await get_db()
    cursor = await db.execute(
        f"""SELECT COUNT(*),
                   COALESCE(SUM(CASE WHEN m.is_read = 0 THEN 1 ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN m.is_read = 1 THEN 1 ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN m.has_attachments = 1 THEN 1 ELSE 0 END), 0)
            FROM cached_messages m JOIN accounts a ON a.id = m.account_id
            WHERE {' AND '.join(conditions)}""",
        params,
    )
    row = await cursor.fetchone() or (0, 0, 0, 0)
    return {
        "all": int(row[0] or 0), "unread": int(row[1] or 0),
        "read": int(row[2] or 0), "attachments": int(row[3] or 0),
    }


async def get_unified_inbox_stats(user_uid: str, account_ids: list[str]) -> dict:
    if not account_ids:
        return {"total_count": 0, "unread_count": 0}
    placeholders = ",".join("?" * len(account_ids))
    db = await get_db()
    cursor = await db.execute(
        f"""SELECT COALESCE(SUM(fs.total_count), 0), COALESCE(SUM(fs.unread_count), 0)
            FROM folder_stats fs JOIN accounts a ON a.id = fs.account_id
            WHERE a.user_uid = ? AND UPPER(fs.folder) = 'INBOX'
              AND fs.account_id IN ({placeholders})""",
        [user_uid, *account_ids],
    )
    row = await cursor.fetchone() or (0, 0)
    return {"total_count": int(row[0] or 0), "unread_count": int(row[1] or 0)}


# ==================== Upstream message archive ====================

async def upsert_message_archive(archive: dict) -> bool:
    db = await get_db()
    now = time.time()
    await db.execute(
        """INSERT INTO message_archive
           (user_uid, account_id, folder, uid, message_id, subject, from_addr,
            to_addr, cc, date, size, eml_path, flags, has_attachments,
            archived_at, is_deleted_on_server, deleted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON DUPLICATE KEY UPDATE
             message_id = VALUES(message_id), subject = VALUES(subject),
             from_addr = VALUES(from_addr), to_addr = VALUES(to_addr), cc = VALUES(cc),
             date = VALUES(date), size = VALUES(size), eml_path = VALUES(eml_path),
             flags = VALUES(flags), has_attachments = VALUES(has_attachments),
             archived_at = VALUES(archived_at)""",
        (
            archive["user_uid"], archive["account_id"], archive["folder"], int(archive["uid"]),
            archive.get("message_id", ""), archive.get("subject", ""), archive.get("from_addr", ""),
            archive.get("to_addr", ""), archive.get("cc", ""), archive.get("date", ""),
            int(archive.get("size", 0) or 0), archive["eml_path"], archive.get("flags", ""),
            1 if archive.get("has_attachments") else 0, now,
            1 if archive.get("is_deleted_on_server") else 0,
            float(archive.get("deleted_at", 0) or 0),
        ),
    )
    await db.commit()
    return True


async def get_archived_uids(account_id: str, folder: str) -> dict[int, str]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT uid, eml_path FROM message_archive WHERE account_id = ? AND folder = ?",
        (account_id, folder),
    )
    return {int(row[0]): row[1] for row in await cursor.fetchall()}


async def mark_archive_deleted(account_id: str, folder: str, uids: list[int]) -> int:
    if not uids:
        return 0
    db = await get_db()
    placeholders = ",".join("?" * len(uids))
    cursor = await db.execute(
        f"""UPDATE message_archive SET is_deleted_on_server = 1, deleted_at = ?
            WHERE account_id = ? AND folder = ? AND uid IN ({placeholders})""",
        [time.time(), account_id, folder, *[int(uid) for uid in uids]],
    )
    await db.commit()
    return cursor.rowcount


async def get_archived_messages(
    user_uid: str,
    account_id: str = "",
    folder: str = "",
    page: int = 1,
    page_size: int = 40,
    deleted_filter: str = "",
) -> dict:
    db = await get_db()
    conditions = ["user_uid = ?"]
    params: list[Any] = [user_uid]
    if account_id:
        conditions.append("account_id = ?")
        params.append(account_id)
    if folder:
        from services.backup import classify_folder_category

        category = classify_folder_category(folder)
        if category == "other":
            conditions.append("folder = ?")
            params.append(folder)
        else:
            cursor = await db.execute(
                "SELECT DISTINCT folder FROM message_archive WHERE user_uid = ?" + (" AND account_id = ?" if account_id else ""),
                (user_uid, account_id) if account_id else (user_uid,),
            )
            matching = [row[0] for row in await cursor.fetchall() if classify_folder_category(row[0]) == category]
            if matching:
                conditions.append(f"folder IN ({','.join('?' * len(matching))})")
                params.extend(matching)
            else:
                conditions.append("1 = 0")
    if deleted_filter == "deleted":
        conditions.append("is_deleted_on_server = 1")
    elif deleted_filter == "alive":
        conditions.append("is_deleted_on_server = 0")
    where = " AND ".join(conditions)
    cursor = await db.execute(f"SELECT COUNT(*) FROM message_archive WHERE {where}", params)
    total_row = await cursor.fetchone()
    cursor = await db.execute(
        f"SELECT * FROM message_archive WHERE {where} ORDER BY date DESC, uid DESC LIMIT ? OFFSET ?",
        [*params, page_size, (page - 1) * page_size],
    )
    rows = await cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return {
        "messages": [dict(zip(columns, row)) for row in rows],
        "total": int((total_row or (0,))[0] or 0),
        "page": page,
        "page_size": page_size,
    }


async def get_archived_message_by_uid(
    user_uid: str,
    account_id: str,
    folder: str,
    uid: int,
) -> Optional[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM message_archive
           WHERE user_uid = ? AND account_id = ? AND folder = ? AND uid = ? LIMIT 1""",
        (user_uid, account_id, folder, int(uid)),
    )
    row = await cursor.fetchone()
    return _row_to_dict(cursor, row) if row else None


async def get_archive_stats(user_uid: str) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """SELECT COUNT(*), COALESCE(SUM(size), 0),
                  COALESCE(SUM(CASE WHEN is_deleted_on_server = 1 THEN 1 ELSE 0 END), 0),
                  COALESCE(MAX(archived_at), 0)
           FROM message_archive WHERE user_uid = ?""",
        (user_uid,),
    )
    total = await cursor.fetchone() or (0, 0, 0, 0)
    cursor = await db.execute(
        """SELECT account_id, COUNT(*),
                  COALESCE(SUM(CASE WHEN is_deleted_on_server = 1 THEN 1 ELSE 0 END), 0),
                  COALESCE(MAX(archived_at), 0)
           FROM message_archive WHERE user_uid = ? GROUP BY account_id""",
        (user_uid,),
    )
    accounts = [
        {"account_id": row[0], "count": int(row[1] or 0), "deleted_count": int(row[2] or 0), "last_archived": float(row[3] or 0)}
        for row in await cursor.fetchall()
    ]
    return {
        "total": int(total[0] or 0), "total_size": int(total[1] or 0),
        "deleted_count": int(total[2] or 0), "last_archived": float(total[3] or 0),
        "accounts": accounts,
    }


async def get_archive_folders(user_uid: str, account_id: str = "") -> list[dict]:
    db = await get_db()
    sql = """SELECT folder, COUNT(*),
                    COALESCE(SUM(CASE WHEN is_deleted_on_server = 1 THEN 1 ELSE 0 END), 0)
             FROM message_archive WHERE user_uid = ?"""
    params: list[Any] = [user_uid]
    if account_id:
        sql += " AND account_id = ?"
        params.append(account_id)
    sql += " GROUP BY folder ORDER BY folder ASC"
    cursor = await db.execute(sql, params)
    return [
        {"folder": row[0], "count": int(row[1] or 0), "deleted_count": int(row[2] or 0)}
        for row in await cursor.fetchall()
    ]
