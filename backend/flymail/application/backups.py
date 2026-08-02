"""Administrator backup creation, inspection, download, and isolated restore rehearsal."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

import aiomysql

from flymail.api.schemas.backups import (
    BackupArchiveResponse,
    BackupInspectionResponse,
    RestoreRehearsalResponse,
)
from flymail.application.auth import AuthenticatedSession
from flymail.config import FlyMailSettings
from flymail.domain.errors import ApiContractError, NotFoundError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.migrations.runner import LATEST_SCHEMA_VERSION
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.audit import AuditRepository
from version import VERSION


_FORMAT_VERSION = 1
_MAX_MEMBERS = 200_000
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BackupFile:
    path: Path
    filename: str
    content_type: str = "application/gzip"


@dataclass(frozen=True, slots=True)
class _DatabaseAddress:
    host: str
    port: int
    user: str
    password: str
    database: str


def _database_address(url: str) -> _DatabaseAddress:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"mysql", "mysql+aiomysql", "mysql+pymysql"}:
        raise ValueError("unsupported database URL")
    database = unquote(parsed.path.lstrip("/"))
    if not database or not parsed.username:
        raise ValueError("database URL is incomplete")
    return _DatabaseAddress(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=unquote(parsed.username),
        password=unquote(parsed.password or ""),
        database=database,
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

    def _archive_path(self, archive_name: str) -> Path:
        name = Path(str(archive_name or "")).name
        if not name or name != archive_name:
            raise ApiContractError("invalid_backup_path", "备份路径无效", status_code=400)
        path = self.root / name
        resolved_root = self.root.resolve()
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(resolved_root):
            raise ApiContractError("invalid_backup_path", "备份路径无效", status_code=400)
        return path

    @staticmethod
    def _manifest(row: dict) -> dict:
        raw = row.get("manifest_json")
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return dict(value) if isinstance(value, dict) else {}
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
            app_version=str(manifest.get("app_version")) if manifest.get("app_version") else None,
            schema_version=(int(manifest["schema_version"]) if manifest.get("schema_version") is not None else None),
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
                    FROM backup_archives WHERE id = %s
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
                    ORDER BY created_at DESC, id DESC
                    LIMIT 200
                    """
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(self._response(row) for row in rows)

    async def get_archive(self, backup_id: str) -> BackupArchiveResponse:
        return self._response(await self._record(backup_id))

    def _write_client_config(self, path: Path, address: _DatabaseAddress) -> None:
        content = "\n".join(
            (
                "[client]",
                f"host={_quoted_option(address.host)}",
                f"port={address.port}",
                f"user={_quoted_option(address.user)}",
                f"password={_quoted_option(address.password)}",
                "default-character-set=utf8mb4",
                "",
            )
        )
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)

    async def _dump_database(self, destination: Path, config: Path) -> None:
        address = _database_address(self.settings.database_url)
        with destination.open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                "mysqldump",
                f"--defaults-extra-file={config}",
                "--single-transaction",
                "--skip-lock-tables",
                "--hex-blob",
                "--set-gtid-purged=OFF",
                "--no-tablespaces",
                address.database,
                stdout=output,
                stderr=asyncio.subprocess.DEVNULL,
            )
            code = await process.wait()
        if code != 0:
            raise ApiContractError(
                "backup_database_failed",
                "数据库备份失败",
                status_code=500,
            )

    def _object_files(self) -> list[tuple[Path, str]]:
        root = self.settings.object_dir
        if not root.exists():
            return []
        result: list[tuple[Path, str]] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ApiContractError(
                    "unsafe_backup_source",
                    "对象存储包含不安全的符号链接",
                    status_code=500,
                )
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                result.append((path, f"objects/sha256/{relative}"))
        return result

    def _build_archive(self, dump: Path, archive_tmp: Path, manifest_path: Path) -> dict:
        files: list[dict] = [
            {
                "path": "database.sql",
                "sha256": _sha256_file(dump),
                "size_bytes": dump.stat().st_size,
            }
        ]
        objects = self._object_files()
        for source, member in objects:
            files.append(
                {
                    "path": member,
                    "sha256": _sha256_file(source),
                    "size_bytes": source.stat().st_size,
                }
            )
        manifest = {
            "format_version": _FORMAT_VERSION,
            "app_version": VERSION,
            "schema_version": LATEST_SCHEMA_VERSION,
            "created_at": float(self.now_fn()),
            "database": _database_address(self.settings.database_url).database,
            "files": files,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        with tarfile.open(archive_tmp, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.add(manifest_path, arcname="manifest.json", recursive=False)
            archive.add(dump, arcname="database.sql", recursive=False)
            for source, member in objects:
                archive.add(source, arcname=member, recursive=False)
        return manifest

    async def create_archive(
        self,
        session: AuthenticatedSession,
        *,
        request_id: str,
    ) -> BackupArchiveResponse:
        timestamp = float(self.now_fn())
        backup_id = new_id("backup")
        archive_name = f"flymail-{VERSION}-{backup_id}.tar.gz"
        final_path = self._archive_path(archive_name)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO backup_archives (
                            id, created_by, status, archive_name,
                            created_at, updated_at
                        ) VALUES (%s, %s, 'creating', %s, %s, %s)
                        """,
                        (backup_id, session.user.id, archive_name, timestamp, timestamp),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

        stage = Path(tempfile.mkdtemp(prefix=f".{backup_id}-", dir=self.root))
        archive_tmp = stage / "archive.tar.gz"
        config = stage / "mysql.cnf"
        dump = stage / "database.sql"
        manifest_path = stage / "manifest.json"
        try:
            address = _database_address(self.settings.database_url)
            self._write_client_config(config, address)
            await self._dump_database(dump, config)
            manifest = await asyncio.to_thread(
                self._build_archive,
                dump,
                archive_tmp,
                manifest_path,
            )
            archive_sha = await asyncio.to_thread(_sha256_file, archive_tmp)
            size = archive_tmp.stat().st_size
            os.replace(archive_tmp, final_path)
            completed = float(self.now_fn())
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
                                json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                                completed,
                                completed,
                                backup_id,
                            ),
                        )
                    await AuditRepository(connection).append(
                        event_type="admin.backup.create",
                        result_code="success",
                        request_id=request_id,
                        user_uid=None,
                        actor_user_uid=session.user.id,
                        resource_type="backup_archive",
                        resource_id=backup_id,
                        safe_metadata={"size_bytes": size},
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
                                last_error_message='backup creation failed',
                                updated_at=%s
                            WHERE id=%s
                            """,
                            (type(exc).__name__[:96], failed, backup_id),
                        )
                    await connection.commit()
                except Exception:
                    await connection.rollback()
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return await self.get_archive(backup_id)

    async def download(self, backup_id: str) -> BackupFile:
        row = await self._record(backup_id)
        if str(row["status"]) != "completed":
            raise ApiContractError("backup_not_ready", "备份尚未完成", status_code=409)
        path = self._archive_path(str(row["archive_name"]))
        if not path.is_file() or path.is_symlink():
            raise NotFoundError("backup archive file was not found")
        actual = await asyncio.to_thread(_sha256_file, path)
        if actual != str(row["archive_sha256"] or ""):
            raise ApiContractError("backup_corrupt", "备份归档校验失败", status_code=409)
        return BackupFile(path=path, filename=str(row["archive_name"]))

    @staticmethod
    def _safe_member(name: str) -> bool:
        path = PurePosixPath(str(name or ""))
        return bool(path.parts) and not path.is_absolute() and all(
            part not in {"", ".", ".."} for part in path.parts
        )

    def _inspect_path(self, path: Path, expected_sha: str) -> tuple[dict, int, int]:
        if _sha256_file(path) != expected_sha:
            raise ApiContractError("backup_corrupt", "备份归档校验失败", status_code=409)
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
        return manifest, len(expected_names) - 1, total

    async def inspect(self, backup_id: str) -> BackupInspectionResponse:
        row = await self._record(backup_id)
        if str(row["status"]) != "completed":
            raise ApiContractError("backup_not_ready", "备份尚未完成", status_code=409)
        path = self._archive_path(str(row["archive_name"]))
        manifest, file_count, total = await asyncio.to_thread(
            self._inspect_path,
            path,
            str(row["archive_sha256"] or ""),
        )
        schema_version = int(manifest.get("schema_version") or 0)
        compatible = 1 <= schema_version <= LATEST_SCHEMA_VERSION
        warnings = () if compatible else ("schema_version_not_supported",)
        return BackupInspectionResponse(
            backup_id=str(row["id"]),
            valid=True,
            compatible=compatible,
            format_version=int(manifest["format_version"]),
            app_version=str(manifest.get("app_version") or ""),
            schema_version=schema_version,
            archive_sha256=str(row["archive_sha256"]),
            file_count=file_count,
            total_uncompressed_bytes=total,
            warnings=warnings,
        )

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

    async def restore_rehearsal(
        self,
        session: AuthenticatedSession,
        backup_id: str,
        *,
        request_id: str,
    ) -> RestoreRehearsalResponse:
        inspection = await self.inspect(backup_id)
        if not inspection.compatible:
            raise ApiContractError("backup_incompatible", "备份版本不兼容", status_code=409)
        row = await self._record(backup_id)
        path = self._archive_path(str(row["archive_name"]))
        stage = Path(tempfile.mkdtemp(prefix="restore-rehearsal-", dir=self.root))
        database_name = "flymail_rehearsal_" + new_id("db").replace("-", "")[-24:]
        database_removed = False
        try:
            with tarfile.open(path, "r:gz") as archive:
                member = archive.getmember("database.sql")
                source = archive.extractfile(member)
                if source is None:
                    raise ApiContractError("backup_corrupt", "数据库备份不可读", status_code=409)
                dump_path = stage / "database.sql"
                with dump_path.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            socket = Path("/run/mysqld/mysqld.sock")
            if not socket.exists():
                raise ApiContractError(
                    "restore_rehearsal_unavailable",
                    "当前部署不支持隔离恢复演练",
                    status_code=409,
                )
            mysql_base = (
                "mysql",
                "--protocol=socket",
                f"--socket={socket}",
                "-uroot",
                "--batch",
                "--skip-column-names",
            )
            await self._run(*mysql_base, "-e", f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4")
            try:
                await self._run(*mysql_base, database_name, stdin=dump_path)
                output = await self._run(
                    *mysql_base,
                    database_name,
                    "-e",
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE(); SELECT COALESCE(MAX(version),0) FROM schema_migrations;",
                )
                values = [int(line) for line in output.decode("utf-8").splitlines() if line.strip().isdigit()]
                if len(values) < 2:
                    raise ApiContractError("restore_rehearsal_failed", "临时恢复验证失败", status_code=500)
                table_count, restored_schema = values[-2], values[-1]
            finally:
                await self._run(*mysql_base, "-e", f"DROP DATABASE IF EXISTS `{database_name}`")
                database_removed = True
            completed = float(self.now_fn())
            async with self.pool.acquire() as connection:
                await connection.begin()
                try:
                    await AuditRepository(connection).append(
                        event_type="admin.backup.restore_rehearsal",
                        result_code="success",
                        request_id=request_id,
                        user_uid=None,
                        actor_user_uid=session.user.id,
                        resource_type="backup_archive",
                        resource_id=backup_id,
                        safe_metadata={"table_count": table_count, "schema_version": restored_schema},
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
                verified_file_count=inspection.file_count,
                temporary_database_removed=database_removed,
                temporary_files_removed=True,
            )
        finally:
            if not database_removed and Path("/run/mysqld/mysqld.sock").exists():
                try:
                    await self._run(
                        "mysql",
                        "--protocol=socket",
                        "--socket=/run/mysqld/mysqld.sock",
                        "-uroot",
                        "-e",
                        f"DROP DATABASE IF EXISTS `{database_name}`",
                    )
                except Exception:
                    pass
            shutil.rmtree(stage, ignore_errors=True)
