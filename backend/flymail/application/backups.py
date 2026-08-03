"""Password-encrypted business backup, inspection, and isolated restore rehearsal."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import struct
import tarfile
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse, urlunparse

import aiomysql
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from flymail.api.schemas.backups import (
    BackupArchiveResponse,
    BackupInspectionResponse,
    RestoreRehearsalResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.config import FlyMailSettings
from flymail.domain.errors import ApiContractError, NotFoundError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.migrations.runner import LATEST_SCHEMA_VERSION, run_migrations
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.security.credentials import CredentialCipher, EncryptedValue
from flymail.repositories.audit import AuditRepository
from version import VERSION


_FORMAT_VERSION = 2
_MAGIC = b"FLYMAIL-BACKUP-V2\n"
_TAG_BYTES = 16
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_MAX_HEADER_BYTES = 64 * 1024
_MAX_MEMBERS = 200_000
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024 * 1024
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_OBJECT_REFERENCE_KINDS = (
    "draft_body_html",
    "draft_body_text",
    "draft_attachment",
    "user_avatar",
    "account_icon",
    "contact_avatar",
)
_INCLUDED_TABLES = (
    "users",
    "user_profiles",
    "user_settings",
    "audit_events",
    "contacts",
    "authorized_storage_roots",
    "mail_accounts",
    "mail_identities",
    "provider_credentials",
    "outbound_proxy_configs",
    "mailboxes",
    "messages",
    "message_headers",
    "message_remote_instances",
    "message_memberships",
    "threads",
    "thread_messages",
    "thread_projections",
    "message_attachments",
    "content_objects",
    "content_references",
    "mail_operations",
    "sync_cursors",
    "account_runtime_state",
    "notification_channels",
    "notification_rules",
    "notification_image_publishers",
    "notification_events",
    "notification_preferences",
    "drafts",
    "draft_versions",
    "draft_recipients",
    "draft_attachments",
    "send_attempts",
    "saved_searches",
    "search_history",
)
_EXCLUDED_TABLES = (
    "user_sessions",
    "login_rate_limits",
    "oauth_authorization_states",
    "message_bodies",
    "message_body_parts",
    "body_search_documents",
    "bulk_mail_operations",
    "outbox_events",
    "worker_jobs",
    "job_attempts",
    "process_heartbeats",
    "realtime_events",
    "notification_deliveries",
    "backup_jobs",
    "backup_archives",
)
_SECRET_TABLES = {
    "provider_credentials": ("account_id", "algorithm", "key_version", "nonce", "ciphertext", "auth_tag"),
    "outbound_proxy_configs": (
        "id", "password_algorithm", "password_key_version",
        "password_nonce", "password_ciphertext", "password_auth_tag",
    ),
    "notification_channels": (
        "id", "secret_algorithm", "secret_key_version",
        "secret_nonce", "secret_ciphertext", "secret_auth_tag",
    ),
    "notification_image_publishers": (
        "id", "secret_algorithm", "secret_key_version",
        "secret_nonce", "secret_ciphertext", "secret_auth_tag",
    ),
}


@dataclass(frozen=True, slots=True)
class BackupFile:
    path: Path
    filename: str
    content_type: str = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class _DatabaseAddress:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True, slots=True)
class _EncryptionContext:
    header: dict[str, object]
    header_bytes: bytes
    key: bytes


@dataclass(frozen=True, slots=True)
class _SnapshotResult:
    database_name: str
    encrypted_secret_count: int
    table_row_counts: dict[str, int]
    object_rows: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _PreparedArchive:
    stage: Path
    tar_path: Path
    manifest: dict[str, object]
    file_count: int
    total_bytes: int
    encryption: _EncryptionContext


def _database_address(url: str) -> _DatabaseAddress:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"mysql", "mysql+aiomysql", "mysql+pymysql"}:
        raise ValueError("unsupported database URL")
    database = unquote(parsed.path.lstrip("/"))
    user = unquote(parsed.username or "")
    if not _NAME_PATTERN.fullmatch(database) or not _NAME_PATTERN.fullmatch(user):
        raise ValueError("database URL contains an unsafe database or user name")
    return _DatabaseAddress(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=user,
        password=unquote(parsed.password or ""),
        database=database,
    )


def _database_url_for(url: str, database: str) -> str:
    if not _NAME_PATTERN.fullmatch(database):
        raise ValueError("unsafe temporary database name")
    parsed = urlparse(url)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            "/" + quote(database, safe=""),
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _quoted_option(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _encrypted_value(algorithm: object, key_version: object, nonce: object, ciphertext: object) -> EncryptedValue:
    return EncryptedValue(
        algorithm=str(algorithm or ""),
        key_version=int(key_version or 0),
        nonce_b64=_encode(bytes(nonce or b"")),
        ciphertext_b64=_encode(bytes(ciphertext or b"")),
    )


def _encrypted_columns(value: EncryptedValue) -> tuple[str, int, bytes, bytes, None]:
    return (
        value.algorithm,
        value.key_version,
        _decode(value.nonce_b64),
        _decode(value.ciphertext_b64),
        None,
    )


def _derive_key(password: str, salt: bytes) -> bytes:
    return Scrypt(
        salt=salt,
        length=32,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    ).derive(password.encode("utf-8"))


def _new_encryption_context(password: str) -> _EncryptionContext:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password, salt)
    verifier = hmac.new(key, b"flymail-backup-password-check-v2", hashlib.sha256).digest()
    header = {
        "format_version": _FORMAT_VERSION,
        "cipher": "AES-256-GCM",
        "kdf": "scrypt",
        "scrypt_n": _SCRYPT_N,
        "scrypt_r": _SCRYPT_R,
        "scrypt_p": _SCRYPT_P,
        "salt": _encode(salt),
        "nonce": _encode(nonce),
        "password_verifier": _encode(verifier),
    }
    header_bytes = json.dumps(
        header,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _EncryptionContext(header=header, header_bytes=header_bytes, key=key)


def _context_from_header(header_bytes: bytes, password: str) -> _EncryptionContext:
    try:
        header = json.loads(header_bytes.decode("utf-8"))
        if not isinstance(header, dict):
            raise ValueError
        if int(header.get("format_version") or 0) != _FORMAT_VERSION:
            raise ValueError
        if header.get("cipher") != "AES-256-GCM" or header.get("kdf") != "scrypt":
            raise ValueError
        if (
            int(header.get("scrypt_n") or 0) != _SCRYPT_N
            or int(header.get("scrypt_r") or 0) != _SCRYPT_R
            or int(header.get("scrypt_p") or 0) != _SCRYPT_P
        ):
            raise ValueError
        salt = _decode(str(header["salt"]))
        nonce = _decode(str(header["nonce"]))
        verifier = _decode(str(header["password_verifier"]))
        if len(salt) != 16 or len(nonce) != 12 or len(verifier) != 32:
            raise ValueError
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ApiContractError(
            "backup_incompatible",
            "备份加密头无效或版本不兼容",
            status_code=409,
        ) from None
    key = _derive_key(password, salt)
    expected = hmac.new(key, b"flymail-backup-password-check-v2", hashlib.sha256).digest()
    if not hmac.compare_digest(expected, verifier):
        raise ApiContractError(
            "backup_password_invalid",
            "备份密码错误",
            status_code=401,
        )
    return _EncryptionContext(header=header, header_bytes=header_bytes, key=key)


def _encrypt_file(source: Path, destination: Path, context: _EncryptionContext) -> None:
    nonce = _decode(str(context.header["nonce"]))
    encryptor = Cipher(algorithms.AES(context.key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(context.header_bytes)
    with source.open("rb") as input_handle, destination.open("wb") as output_handle:
        output_handle.write(_MAGIC)
        output_handle.write(struct.pack(">I", len(context.header_bytes)))
        output_handle.write(context.header_bytes)
        while True:
            chunk = input_handle.read(1024 * 1024)
            if not chunk:
                break
            output_handle.write(encryptor.update(chunk))
        output_handle.write(encryptor.finalize())
        output_handle.write(encryptor.tag)
        output_handle.flush()
        os.fsync(output_handle.fileno())


def _decrypt_file(source: Path, destination: Path, password: str) -> _EncryptionContext:
    size = source.stat().st_size
    with source.open("rb") as input_handle:
        if input_handle.read(len(_MAGIC)) != _MAGIC:
            raise ApiContractError(
                "backup_incompatible",
                "备份不是 FlyMail V2 加密归档",
                status_code=409,
            )
        raw_length = input_handle.read(4)
        if len(raw_length) != 4:
            raise ApiContractError("backup_corrupt", "备份加密头已损坏", status_code=409)
        header_length = struct.unpack(">I", raw_length)[0]
        if header_length < 2 or header_length > _MAX_HEADER_BYTES:
            raise ApiContractError("backup_corrupt", "备份加密头长度无效", status_code=409)
        header_bytes = input_handle.read(header_length)
        if len(header_bytes) != header_length:
            raise ApiContractError("backup_corrupt", "备份加密头不完整", status_code=409)
        context = _context_from_header(header_bytes, password)
        ciphertext_start = len(_MAGIC) + 4 + header_length
        ciphertext_length = size - ciphertext_start - _TAG_BYTES
        if ciphertext_length < 1:
            raise ApiContractError("backup_corrupt", "备份密文不完整", status_code=409)
        input_handle.seek(size - _TAG_BYTES)
        tag = input_handle.read(_TAG_BYTES)
        input_handle.seek(ciphertext_start)
        decryptor = Cipher(
            algorithms.AES(context.key),
            modes.GCM(_decode(str(context.header["nonce"])), tag),
        ).decryptor()
        decryptor.authenticate_additional_data(header_bytes)
        remaining = ciphertext_length
        try:
            with destination.open("wb") as output_handle:
                while remaining:
                    chunk = input_handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ApiContractError("backup_corrupt", "备份密文被截断", status_code=409)
                    remaining -= len(chunk)
                    output_handle.write(decryptor.update(chunk))
                output_handle.write(decryptor.finalize())
                output_handle.flush()
                os.fsync(output_handle.fileno())
        except InvalidTag:
            destination.unlink(missing_ok=True)
            raise ApiContractError("backup_corrupt", "备份密文校验失败", status_code=409) from None
    return context


class BackupService:
    def __init__(
        self,
        pool: DatabasePool,
        settings: FlyMailSettings,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        self.settings = settings
        self.now_fn = now_fn
        self.root = settings.data_dir / "backups"
        self.root.mkdir(parents=True, exist_ok=True)
        self.instance_cipher = CredentialCipher.from_master_secret(settings.session_secret)
        self.address = _database_address(settings.database_url)

    def _archive_path(self, archive_name: str) -> Path:
        name = Path(str(archive_name or "")).name
        if not name or name != archive_name:
            raise ApiContractError("invalid_backup_path", "备份路径无效", status_code=400)
        path = self.root / name
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(self.root.resolve()):
            raise ApiContractError("invalid_backup_path", "备份路径无效", status_code=400)
        return path

    @staticmethod
    def _manifest(row: dict) -> dict:
        raw = row.get("manifest_json")
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return dict(decoded) if isinstance(decoded, dict) else {}
        return {}

    @classmethod
    def _response(cls, row: dict) -> BackupArchiveResponse:
        manifest = cls._manifest(row)
        return BackupArchiveResponse(
            id=str(row["id"]),
            status=str(row["status"]),
            archive_name=str(row["archive_name"]),
            size_bytes=max(int(row["size_bytes"] or 0), 0),
            archive_sha256=str(row["archive_sha256"]) if row["archive_sha256"] else None,
            encrypted=bool(manifest.get("encrypted")),
            app_version=str(manifest.get("app_version")) if manifest.get("app_version") else None,
            schema_version=int(manifest["schema_version"]) if manifest.get("schema_version") is not None else None,
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
            completed_at=float(row["completed_at"]) if row["completed_at"] is not None else None,
            last_error_class=str(row["last_error_class"] or ""),
            last_error_message=str(row["last_error_message"] or ""),
        )

    async def _record(self, backup_id: str) -> dict:
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, created_by, status, archive_name, archive_sha256,
                           size_bytes, manifest_json, last_error_class,
                           last_error_message, created_at, updated_at, completed_at
                    FROM backup_archives WHERE id=%s
                    """,
                    (str(backup_id or "").strip(),),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("backup archive was not found")
        return dict(row)

    async def list_archives(self) -> tuple[BackupArchiveResponse, ...]:
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, created_by, status, archive_name, archive_sha256,
                           size_bytes, manifest_json, last_error_class,
                           last_error_message, created_at, updated_at, completed_at
                    FROM backup_archives
                    ORDER BY created_at DESC, id DESC LIMIT 200
                    """
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(self._response(row) for row in rows)

    async def get_archive(self, backup_id: str) -> BackupArchiveResponse:
        return self._response(await self._record(backup_id))

    async def _root_connection(self, database: str = "mysql", *, autocommit: bool = True):
        socket = Path("/run/mysqld/mysqld.sock")
        if not socket.exists():
            raise ApiContractError(
                "backup_snapshot_unavailable",
                "当前部署不支持隔离业务快照",
                status_code=409,
            )
        return await aiomysql.connect(
            user="root",
            unix_socket=str(socket),
            db=database,
            charset="utf8mb4",
            autocommit=autocommit,
        )

    async def _create_database(self, prefix: str) -> str:
        database = f"flymail_{prefix}_{new_id('db').replace('-', '')[-24:]}"
        if not _NAME_PATTERN.fullmatch(database):
            raise RuntimeError("unsafe temporary database name")
        connection = await self._root_connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
                await cursor.execute(
                    f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{self.address.user}'@'127.0.0.1'"
                )
        finally:
            connection.close()
        return database

    async def _drop_database(self, database: str) -> None:
        if not _NAME_PATTERN.fullmatch(database):
            return
        connection = await self._root_connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        finally:
            connection.close()

    def _settings_for_database(self, database: str, suffix: str) -> FlyMailSettings:
        return replace(
            self.settings,
            database_url=_database_url_for(self.settings.database_url, database),
            db_pool_name=f"flymail-{suffix}",
            db_min_connections=1,
            db_max_connections=3,
        )

    async def _migrate_database(self, database: str, suffix: str) -> None:
        pool = await DatabasePool.create(self._settings_for_database(database, suffix))
        try:
            await run_migrations(pool)
        finally:
            await pool.close()

    @staticmethod
    async def _columns(connection, table: str) -> tuple[str, ...]:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=DATABASE() AND table_name=%s
                ORDER BY ordinal_position
                """,
                (table,),
            )
            columns = tuple(str(row[0]) for row in await cursor.fetchall())
        if not columns or any(not _NAME_PATTERN.fullmatch(column) for column in columns):
            raise RuntimeError(f"invalid table metadata for {table}")
        return columns

    @staticmethod
    def _select_sql(table: str) -> str:
        if table == "content_references":
            values = ",".join("'" + value + "'" for value in _OBJECT_REFERENCE_KINDS)
            return f"SELECT * FROM `{table}` WHERE reference_kind IN ({values})"
        if table == "content_objects":
            values = ",".join("'" + value + "'" for value in _OBJECT_REFERENCE_KINDS)
            return (
                f"SELECT * FROM `{table}` WHERE content_sha256 IN ("
                f"SELECT DISTINCT content_sha256 FROM content_references "
                f"WHERE reference_kind IN ({values}))"
            )
        return f"SELECT * FROM `{table}`"

    @staticmethod
    def _normalize_value(value: object) -> object:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return value

    def _reencrypt_row(
        self,
        table: str,
        row: dict[str, object],
        source_cipher: CredentialCipher,
        target_cipher: CredentialCipher,
    ) -> bool:
        spec = _SECRET_TABLES.get(table)
        if spec is None:
            return False
        scope_column, algorithm_column, version_column, nonce_column, ciphertext_column, auth_tag_column = spec
        ciphertext = row.get(ciphertext_column)
        if ciphertext is None:
            return False
        scope = str(row.get(scope_column) or "").strip()
        plaintext = source_cipher.decrypt(
            scope,
            _encrypted_value(
                row.get(algorithm_column),
                row.get(version_column),
                row.get(nonce_column),
                ciphertext,
            ),
        )
        encrypted = target_cipher.encrypt(scope, plaintext)
        algorithm, key_version, nonce, new_ciphertext, auth_tag = _encrypted_columns(encrypted)
        row[algorithm_column] = algorithm
        row[version_column] = key_version
        row[nonce_column] = nonce
        row[ciphertext_column] = new_ciphertext
        row[auth_tag_column] = auth_tag
        return True

    def _transform_snapshot_row(
        self,
        table: str,
        raw: dict[str, object],
        backup_cipher: CredentialCipher,
    ) -> tuple[dict[str, object], bool]:
        row = dict(raw)
        secret_changed = self._reencrypt_row(
            table,
            row,
            self.instance_cipher,
            backup_cipher,
        )
        if table == "message_attachments":
            row["content_sha256"] = None
            if str(row.get("cache_state") or "") not in {"unavailable", "failed"}:
                row["cache_state"] = "evicted"
        elif table == "notification_events" and "notification_asset_id" in row:
            row["notification_asset_id"] = None
        elif table == "mail_operations":
            if str(row.get("status") or "") in {"pending", "applying", "retry_wait", "conflict"}:
                row["status"] = "review_required"
                row["last_error_class"] = "RestoreReviewRequired"
                row["last_error_message"] = "restored operation requires remote-state review"
                row["completed_at"] = None
        elif table == "drafts":
            if str(row.get("status") or "") in {"queued", "sending", "failed", "conflict"}:
                row["status"] = "review_required"
            if str(row.get("send_state") or "") in {
                "queued", "sending", "failed", "verification_required"
            }:
                row["send_state"] = "review_required"
        return row, secret_changed

    async def _copy_snapshot(
        self,
        target_database: str,
        backup_cipher: CredentialCipher,
    ) -> _SnapshotResult:
        source = await self._root_connection(self.address.database, autocommit=False)
        target = await self._root_connection(target_database, autocommit=False)
        secret_count = 0
        row_counts: dict[str, int] = {}
        try:
            async with source.cursor() as cursor:
                await cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                await cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            async with target.cursor() as cursor:
                await cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            for table in _INCLUDED_TABLES:
                columns = await self._columns(source, table)
                placeholders = ",".join("%s" for _ in columns)
                quoted_columns = ",".join(f"`{column}`" for column in columns)
                insert_sql = f"INSERT INTO `{table}` ({quoted_columns}) VALUES ({placeholders})"
                count = 0
                async with source.cursor(aiomysql.SSDictCursor) as read_cursor:
                    await read_cursor.execute(self._select_sql(table))
                    while True:
                        rows = await read_cursor.fetchmany(500)
                        if not rows:
                            break
                        values: list[tuple[object, ...]] = []
                        for raw in rows:
                            transformed, changed = self._transform_snapshot_row(
                                table,
                                dict(raw),
                                backup_cipher,
                            )
                            secret_count += 1 if changed else 0
                            values.append(
                                tuple(
                                    self._normalize_value(transformed.get(column))
                                    for column in columns
                                )
                            )
                        async with target.cursor() as write_cursor:
                            await write_cursor.executemany(insert_sql, values)
                        count += len(values)
                row_counts[table] = count
            await target.commit()
            await source.rollback()
            async with target.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT content_sha256, relative_path, stored_size_bytes
                    FROM content_objects
                    ORDER BY content_sha256
                    """
                )
                objects = tuple(dict(row) for row in await cursor.fetchall())
            return _SnapshotResult(
                database_name=target_database,
                encrypted_secret_count=secret_count,
                table_row_counts=row_counts,
                object_rows=objects,
            )
        except Exception:
            await target.rollback()
            await source.rollback()
            raise
        finally:
            source.close()
            target.close()

    def _write_client_config(self, path: Path) -> None:
        path.write_text(
            "\n".join(
                (
                    "[client]",
                    f"host={_quoted_option(self.address.host)}",
                    f"port={self.address.port}",
                    f"user={_quoted_option(self.address.user)}",
                    f"password={_quoted_option(self.address.password)}",
                    "default-character-set=utf8mb4",
                    "",
                )
            ),
            encoding="utf-8",
        )
        os.chmod(path, 0o600)

    async def _dump_database(self, database: str, destination: Path, config: Path) -> None:
        with destination.open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                "mysqldump",
                f"--defaults-extra-file={config}",
                "--single-transaction",
                "--skip-lock-tables",
                "--hex-blob",
                "--set-gtid-purged=OFF",
                "--no-tablespaces",
                "--skip-comments",
                database,
                stdout=output,
                stderr=asyncio.subprocess.DEVNULL,
            )
            code = await process.wait()
        if code != 0:
            raise ApiContractError("backup_database_failed", "数据库业务快照导出失败", status_code=500)

    def _object_files(self, rows: tuple[dict[str, object], ...]) -> list[tuple[Path, str, str, int]]:
        root = self.settings.object_dir.resolve()
        result: list[tuple[Path, str, str, int]] = []
        for row in rows:
            digest = str(row["content_sha256"])
            relative = str(row["relative_path"])
            expected_size = int(row["stored_size_bytes"] or 0)
            source = self.settings.object_dir / relative
            if source.is_symlink() or not source.is_file():
                raise ApiContractError("backup_object_missing", "业务对象缺失或不安全", status_code=409)
            resolved = source.resolve()
            if not resolved.is_relative_to(root) or _sha256_file(resolved) != digest:
                raise ApiContractError("backup_object_corrupt", "业务对象校验失败", status_code=409)
            if resolved.stat().st_size != expected_size:
                raise ApiContractError("backup_object_corrupt", "业务对象大小不匹配", status_code=409)
            result.append((resolved, f"objects/sha256/{relative}", digest, expected_size))
        return result

    def _build_tar(
        self,
        dump: Path,
        tar_path: Path,
        manifest_path: Path,
        snapshot: _SnapshotResult,
        context: _EncryptionContext,
    ) -> dict[str, object]:
        objects = self._object_files(snapshot.object_rows)
        files: list[dict[str, object]] = [
            {"path": "database.sql", "sha256": _sha256_file(dump), "size_bytes": dump.stat().st_size}
        ]
        for source, member, digest, size in objects:
            files.append({"path": member, "sha256": digest, "size_bytes": size})
        manifest: dict[str, object] = {
            "format_version": _FORMAT_VERSION,
            "encrypted": True,
            "cipher": context.header["cipher"],
            "kdf": {
                "name": context.header["kdf"],
                "n": context.header["scrypt_n"],
                "r": context.header["scrypt_r"],
                "p": context.header["scrypt_p"],
                "salt": context.header["salt"],
            },
            "app_version": VERSION,
            "schema_version": LATEST_SCHEMA_VERSION,
            "created_at": float(self.now_fn()),
            "included_tables": list(_INCLUDED_TABLES),
            "excluded_tables": list(_EXCLUDED_TABLES),
            "table_row_counts": snapshot.table_row_counts,
            "encrypted_secret_count": snapshot.encrypted_secret_count,
            "business_object_count": len(objects),
            "files": files,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.add(manifest_path, arcname="manifest.json", recursive=False)
            archive.add(dump, arcname="database.sql", recursive=False)
            for source, member, _digest, _size in objects:
                archive.add(source, arcname=member, recursive=False)
        return manifest

    async def create_archive(
        self,
        session: AuthenticatedSession,
        *,
        password: str,
        request_id: str,
    ) -> BackupArchiveResponse:
        if password == "":
            raise ApiContractError("backup_password_required", "备份密码不能为空", status_code=422)
        timestamp = float(self.now_fn())
        backup_id = new_id("backup")
        archive_name = f"flymail-{VERSION}-{backup_id}.flymailbak"
        final_path = self._archive_path(archive_name)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO backup_archives (
                            id, created_by, status, archive_name, created_at, updated_at
                        ) VALUES (%s, %s, 'creating', %s, %s, %s)
                        """,
                        (backup_id, session.user.id, archive_name, timestamp, timestamp),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

        stage = Path(tempfile.mkdtemp(prefix=f".{backup_id}-", dir=self.root))
        snapshot_database = ""
        try:
            context = await asyncio.to_thread(_new_encryption_context, password)
            backup_cipher = CredentialCipher(context.key, 1)
            snapshot_database = await self._create_database("snapshot")
            await self._migrate_database(snapshot_database, "backup-snapshot")
            snapshot = await self._copy_snapshot(snapshot_database, backup_cipher)
            config = stage / "mysql.cnf"
            dump = stage / "database.sql"
            tar_path = stage / "business.tar.gz"
            manifest_path = stage / "manifest.json"
            encrypted_tmp = stage / "archive.flymailbak"
            self._write_client_config(config)
            await self._dump_database(snapshot_database, dump, config)
            manifest = await asyncio.to_thread(
                self._build_tar,
                dump,
                tar_path,
                manifest_path,
                snapshot,
                context,
            )
            await asyncio.to_thread(_encrypt_file, tar_path, encrypted_tmp, context)
            archive_sha = await asyncio.to_thread(_sha256_file, encrypted_tmp)
            size = encrypted_tmp.stat().st_size
            os.replace(encrypted_tmp, final_path)
            completed = float(self.now_fn())
            public_manifest = {
                "format_version": _FORMAT_VERSION,
                "encrypted": True,
                "cipher": "AES-256-GCM",
                "kdf": {"name": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P},
                "app_version": VERSION,
                "schema_version": LATEST_SCHEMA_VERSION,
                "included_tables": list(_INCLUDED_TABLES),
                "excluded_tables": list(_EXCLUDED_TABLES),
                "business_object_count": manifest["business_object_count"],
                "encrypted_secret_count": manifest["encrypted_secret_count"],
            }
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            """
                            UPDATE backup_archives
                            SET status='completed', archive_sha256=%s,
                                size_bytes=%s, manifest_json=%s,
                                last_error_class='', last_error_message='',
                                updated_at=%s, completed_at=%s
                            WHERE id=%s
                            """,
                            (
                                archive_sha,
                                size,
                                json.dumps(public_manifest, ensure_ascii=False, sort_keys=True),
                                completed,
                                completed,
                                backup_id,
                            ),
                        )
                    await AuditRepository(connection).append(
                        event_type="admin.backup.create",
                        result_code="success",
                        request_id=request_id,
                        actor_user_uid=session.user.id,
                        resource_type="backup_archive",
                        resource_id=backup_id,
                        safe_metadata={
                            "size_bytes": size,
                            "business_object_count": int(manifest["business_object_count"]),
                            "encrypted_secret_count": int(manifest["encrypted_secret_count"]),
                        },
                        now=completed,
                    )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
        except Exception as exc:
            final_path.unlink(missing_ok=True)
            failed = float(self.now_fn())
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            """
                            UPDATE backup_archives
                            SET status='failed', last_error_class=%s,
                                last_error_message='backup creation failed', updated_at=%s
                            WHERE id=%s
                            """,
                            (type(exc).__name__[:96], failed, backup_id),
                        )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
            raise
        finally:
            if snapshot_database:
                await self._drop_database(snapshot_database)
            shutil.rmtree(stage, ignore_errors=True)
        return await self.get_archive(backup_id)

    async def download(self, backup_id: str) -> BackupFile:
        row = await self._record(backup_id)
        if str(row["status"]) != "completed":
            raise ApiContractError("backup_not_ready", "备份尚未完成", status_code=409)
        path = self._archive_path(str(row["archive_name"]))
        if not path.is_file() or path.is_symlink():
            raise NotFoundError("backup archive file was not found")
        if await asyncio.to_thread(_sha256_file, path) != str(row["archive_sha256"] or ""):
            raise ApiContractError("backup_corrupt", "备份归档校验失败", status_code=409)
        return BackupFile(path=path, filename=str(row["archive_name"]))

    @staticmethod
    def _safe_member(name: str) -> bool:
        path = PurePosixPath(str(name or ""))
        return bool(path.parts) and not path.is_absolute() and all(
            part not in {"", ".", ".."} for part in path.parts
        )

    def _inspect_tar(self, path: Path) -> tuple[dict[str, object], int, int]:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > _MAX_MEMBERS:
                raise ApiContractError("backup_too_large", "备份成员数量过多", status_code=413)
            total = 0
            member_map: dict[str, tarfile.TarInfo] = {}
            for member in members:
                if not self._safe_member(member.name) or not member.isfile():
                    raise ApiContractError("unsafe_backup_archive", "备份包含不安全成员", status_code=409)
                total += max(int(member.size), 0)
                if total > _MAX_TOTAL_BYTES:
                    raise ApiContractError("backup_too_large", "备份解压后过大", status_code=413)
                if member.name in member_map:
                    raise ApiContractError("backup_corrupt", "备份包含重复成员", status_code=409)
                member_map[member.name] = member
            manifest_member = member_map.get("manifest.json")
            if manifest_member is None or manifest_member.size > _MAX_MANIFEST_BYTES:
                raise ApiContractError("backup_corrupt", "备份清单缺失或过大", status_code=409)
            handle = archive.extractfile(manifest_member)
            if handle is None:
                raise ApiContractError("backup_corrupt", "备份清单不可读", status_code=409)
            try:
                manifest = json.loads(handle.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiContractError("backup_corrupt", "备份清单无效", status_code=409) from None
            if not isinstance(manifest, dict) or int(manifest.get("format_version") or 0) != _FORMAT_VERSION:
                raise ApiContractError("backup_incompatible", "备份格式版本不兼容", status_code=409)
            listed = manifest.get("files")
            if not isinstance(listed, list):
                raise ApiContractError("backup_corrupt", "备份文件清单无效", status_code=409)
            expected_names = {"manifest.json"}
            for item in listed:
                if not isinstance(item, dict):
                    raise ApiContractError("backup_corrupt", "备份文件清单无效", status_code=409)
                name = str(item.get("path") or "")
                if not self._safe_member(name) or name == "manifest.json":
                    raise ApiContractError("backup_corrupt", "备份文件路径无效", status_code=409)
                expected_names.add(name)
                member = member_map.get(name)
                if member is None or int(item.get("size_bytes") or -1) != member.size:
                    raise ApiContractError("backup_corrupt", "备份文件大小不匹配", status_code=409)
                source = archive.extractfile(member)
                if source is None:
                    raise ApiContractError("backup_corrupt", "备份成员不可读", status_code=409)
                digest = hashlib.sha256()
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                if digest.hexdigest() != str(item.get("sha256") or ""):
                    raise ApiContractError("backup_corrupt", "备份成员校验失败", status_code=409)
            if set(member_map) != expected_names:
                raise ApiContractError("backup_corrupt", "备份包含未声明成员", status_code=409)
        return dict(manifest), len(expected_names) - 1, total

    async def _prepare_archive(self, backup_id: str, password: str) -> _PreparedArchive:
        row = await self._record(backup_id)
        if str(row["status"]) != "completed":
            raise ApiContractError("backup_not_ready", "备份尚未完成", status_code=409)
        path = self._archive_path(str(row["archive_name"]))
        if not path.is_file() or path.is_symlink():
            raise NotFoundError("backup archive file was not found")
        expected_sha = str(row["archive_sha256"] or "")
        if await asyncio.to_thread(_sha256_file, path) != expected_sha:
            raise ApiContractError("backup_corrupt", "备份归档校验失败", status_code=409)
        stage = Path(tempfile.mkdtemp(prefix="inspect-", dir=self.root))
        tar_path = stage / "business.tar.gz"
        try:
            encryption = await asyncio.to_thread(_decrypt_file, path, tar_path, password)
            manifest, file_count, total = await asyncio.to_thread(self._inspect_tar, tar_path)
            return _PreparedArchive(
                stage=stage,
                tar_path=tar_path,
                manifest=manifest,
                file_count=file_count,
                total_bytes=total,
                encryption=encryption,
            )
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    async def inspect(self, backup_id: str, *, password: str) -> BackupInspectionResponse:
        prepared = await self._prepare_archive(backup_id, password)
        try:
            schema_version = int(prepared.manifest.get("schema_version") or 0)
            compatible = 1 <= schema_version <= LATEST_SCHEMA_VERSION
            row = await self._record(backup_id)
            return BackupInspectionResponse(
                backup_id=str(row["id"]),
                valid=True,
                compatible=compatible,
                encrypted=True,
                format_version=int(prepared.manifest["format_version"]),
                app_version=str(prepared.manifest.get("app_version") or ""),
                schema_version=schema_version,
                archive_sha256=str(row["archive_sha256"]),
                file_count=prepared.file_count,
                total_uncompressed_bytes=prepared.total_bytes,
                business_object_count=int(prepared.manifest.get("business_object_count") or 0),
                encrypted_secret_count=int(prepared.manifest.get("encrypted_secret_count") or 0),
                included_tables=tuple(str(value) for value in prepared.manifest.get("included_tables", [])),
                excluded_tables=tuple(str(value) for value in prepared.manifest.get("excluded_tables", [])),
                warnings=() if compatible else ("schema_version_not_supported",),
            )
        finally:
            shutil.rmtree(prepared.stage, ignore_errors=True)

    async def _run(self, *args: str, stdin: Path | None = None) -> bytes:
        input_handle = stdin.open("rb") if stdin else None
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=input_handle,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await process.communicate()
        finally:
            if input_handle is not None:
                input_handle.close()
        if process.returncode != 0:
            raise ApiContractError("restore_rehearsal_failed", "临时恢复演练失败", status_code=500)
        return output

    def _extract_tar(self, tar_path: Path, stage: Path, manifest: dict[str, object]) -> tuple[Path, Path]:
        dump_path = stage / "database.sql"
        object_root = stage / "objects" / "sha256"
        object_root.mkdir(parents=True, exist_ok=True)
        listed = {
            str(item["path"]): item
            for item in manifest.get("files", [])
            if isinstance(item, dict) and item.get("path")
        }
        with tarfile.open(tar_path, "r:gz") as archive:
            for name, item in listed.items():
                member = archive.getmember(name)
                source = archive.extractfile(member)
                if source is None:
                    raise ApiContractError("backup_corrupt", "备份成员不可读", status_code=409)
                if name == "database.sql":
                    destination = dump_path
                elif name.startswith("objects/sha256/"):
                    relative = PurePosixPath(name).relative_to("objects/sha256")
                    destination = object_root.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                else:
                    raise ApiContractError("unsafe_backup_archive", "备份包含未知成员", status_code=409)
                with destination.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                if destination.stat().st_size != int(item["size_bytes"]):
                    raise ApiContractError("backup_corrupt", "备份成员大小不匹配", status_code=409)
                if _sha256_file(destination) != str(item["sha256"]):
                    raise ApiContractError("backup_corrupt", "备份成员校验失败", status_code=409)
        if not dump_path.is_file():
            raise ApiContractError("backup_corrupt", "数据库业务快照缺失", status_code=409)
        return dump_path, object_root

    async def _reencrypt_database_secrets(
        self,
        database: str,
        backup_cipher: CredentialCipher,
    ) -> int:
        pool = await DatabasePool.create(self._settings_for_database(database, "restore-secrets"))
        count = 0
        try:
            async with pool.acquire() as connection:
                await connection.begin()
                try:
                    for table, spec in _SECRET_TABLES.items():
                        scope_column, algorithm_column, version_column, nonce_column, ciphertext_column, auth_tag_column = spec
                        async with connection.cursor(aiomysql.DictCursor) as cursor:
                            await cursor.execute(
                                f"SELECT * FROM `{table}` WHERE `{ciphertext_column}` IS NOT NULL FOR UPDATE"
                            )
                            rows = [dict(row) for row in await cursor.fetchall()]
                        for row in rows:
                            if not self._reencrypt_row(
                                table,
                                row,
                                backup_cipher,
                                self.instance_cipher,
                            ):
                                continue
                            async with connection.cursor() as cursor:
                                await cursor.execute(
                                    f"""
                                    UPDATE `{table}` SET
                                        `{algorithm_column}`=%s,
                                        `{version_column}`=%s,
                                        `{nonce_column}`=%s,
                                        `{ciphertext_column}`=%s,
                                        `{auth_tag_column}`=%s
                                    WHERE `{scope_column}`=%s
                                    """,
                                    (
                                        row[algorithm_column], row[version_column],
                                        row[nonce_column], row[ciphertext_column],
                                        row[auth_tag_column], row[scope_column],
                                    ),
                                )
                            verified = self.instance_cipher.decrypt(
                                str(row[scope_column]),
                                _encrypted_value(
                                    row[algorithm_column], row[version_column],
                                    row[nonce_column], row[ciphertext_column],
                                ),
                            )
                            if not verified:
                                raise ApiContractError(
                                    "restore_secret_invalid",
                                    "恢复凭证重新加密验证失败",
                                    status_code=409,
                                )
                            count += 1
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
        finally:
            await pool.close()
        return count

    async def restore_rehearsal(
        self,
        session: AuthenticatedSession,
        backup_id: str,
        *,
        password: str,
        request_id: str,
    ) -> RestoreRehearsalResponse:
        prepared = await self._prepare_archive(backup_id, password)
        database_name = ""
        database_removed = False
        try:
            schema_version = int(prepared.manifest.get("schema_version") or 0)
            if not (1 <= schema_version <= LATEST_SCHEMA_VERSION):
                raise ApiContractError("backup_incompatible", "备份版本不兼容", status_code=409)
            dump_path, _object_root = await asyncio.to_thread(
                self._extract_tar,
                prepared.tar_path,
                prepared.stage,
                prepared.manifest,
            )
            database_name = await self._create_database("restore")
            config = prepared.stage / "mysql.cnf"
            self._write_client_config(config)
            await self._run(
                "mysql",
                f"--defaults-extra-file={config}",
                database_name,
                stdin=dump_path,
            )
            await self._migrate_database(database_name, "restore-migrate")
            backup_cipher = CredentialCipher(prepared.encryption.key, 1)
            re_encrypted = await self._reencrypt_database_secrets(database_name, backup_cipher)
            connection = await self._root_connection(database_name)
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")
                    table_count = int((await cursor.fetchone())[0] or 0)
                    await cursor.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations")
                    restored_schema = int((await cursor.fetchone())[0] or 0)
                    await cursor.execute("SELECT COUNT(*) FROM mail_operations WHERE status='review_required'")
                    operation_reviews = int((await cursor.fetchone())[0] or 0)
                    await cursor.execute("SELECT COUNT(*) FROM drafts WHERE status='review_required' OR send_state='review_required'")
                    draft_reviews = int((await cursor.fetchone())[0] or 0)
                    await cursor.execute(
                        "SELECT COUNT(*) FROM worker_jobs WHERE status IN ('pending','retry_wait','leased','running')"
                    )
                    runnable_jobs = int((await cursor.fetchone())[0] or 0)
                if runnable_jobs:
                    raise ApiContractError(
                        "restore_runnable_jobs_present",
                        "恢复快照包含可执行后台任务",
                        status_code=409,
                    )
            finally:
                connection.close()
            await self._drop_database(database_name)
            database_removed = True
            completed = float(self.now_fn())
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    await AuditRepository(connection).append(
                        event_type="admin.backup.restore_rehearsal",
                        result_code="success",
                        request_id=request_id,
                        actor_user_uid=session.user.id,
                        resource_type="backup_archive",
                        resource_id=backup_id,
                        safe_metadata={
                            "table_count": table_count,
                            "schema_version": restored_schema,
                            "re_encrypted_secret_count": re_encrypted,
                            "review_required_operation_count": operation_reviews,
                            "review_required_draft_count": draft_reviews,
                        },
                        now=completed,
                    )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
                    raise
            return RestoreRehearsalResponse(
                backup_id=backup_id,
                restored_schema_version=restored_schema,
                restored_table_count=table_count,
                verified_file_count=prepared.file_count,
                re_encrypted_secret_count=re_encrypted,
                review_required_operation_count=operation_reviews,
                review_required_draft_count=draft_reviews,
                temporary_database_removed=database_removed,
                temporary_files_removed=True,
            )
        finally:
            if database_name and not database_removed:
                try:
                    await self._drop_database(database_name)
                except Exception:
                    pass
            shutil.rmtree(prepared.stage, ignore_errors=True)
