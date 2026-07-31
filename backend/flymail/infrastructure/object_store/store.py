"""Atomic content-addressed file storage for FlyMail V2."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterable, AsyncIterator, BinaryIO

from flymail.domain.enums import ObjectKind
from flymail.infrastructure.object_store.models import (
    ObjectVerification,
    ObjectVerificationStatus,
    StoredObject,
)


logger = logging.getLogger("flymail.v2.object_store")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_READ_CHUNK_SIZE = 1024 * 1024


def object_path(root: Path, digest: str) -> Path:
    normalized = str(digest or "").strip().lower()
    if not _DIGEST_PATTERN.fullmatch(normalized):
        raise ValueError("invalid SHA-256 digest")
    return Path(root) / normalized[:2] / normalized


def _write_all(file_descriptor: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    written = 0
    while written < len(view):
        count = os.write(file_descriptor, view[written:])
        if count <= 0:
            raise OSError("object file write returned zero bytes")
        written += count


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class ObjectStore:
    def __init__(self, root: Path, temp_root: Path) -> None:
        self.root = Path(root)
        self.temp_root = Path(temp_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)

    async def put_stream(
        self,
        kind: ObjectKind,
        chunks: AsyncIterable[bytes],
        expected_size: int | None = None,
    ) -> StoredObject:
        if not isinstance(kind, ObjectKind):
            raise TypeError("kind must be an ObjectKind")
        if expected_size is not None and int(expected_size) < 0:
            raise ValueError("expected size must be non-negative")

        temp_path = self.temp_root / f"{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        digest = hashlib.sha256()
        size = 0
        descriptor_open = True
        try:
            async for raw_chunk in chunks:
                if not isinstance(raw_chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("object stream chunks must be bytes-like")
                chunk = bytes(raw_chunk)
                if not chunk:
                    continue
                await asyncio.to_thread(_write_all, descriptor, chunk)
                digest.update(chunk)
                size += len(chunk)
            await asyncio.to_thread(os.fsync, descriptor)
            os.close(descriptor)
            descriptor_open = False

            if expected_size is not None and size != int(expected_size):
                raise ValueError(f"expected size {int(expected_size)} bytes but received {size}")

            content_sha256 = digest.hexdigest()
            target = object_path(self.root, content_sha256)
            target.parent.mkdir(parents=True, exist_ok=True)
            created = await asyncio.to_thread(
                self._finalize_temp_object,
                temp_path,
                target,
                content_sha256,
                size,
            )

            return StoredObject(
                content_sha256=content_sha256,
                kind=kind,
                original_size_bytes=size,
                stored_size_bytes=size,
                relative_path=str(Path(content_sha256[:2]) / content_sha256),
                path=target,
                created=created,
            )
        except BaseException:
            if descriptor_open:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            temp_path.unlink(missing_ok=True)
            raise

    def _finalize_temp_object(
        self,
        temp_path: Path,
        target: Path,
        content_sha256: str,
        size: int,
    ) -> bool:
        bucket_descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(bucket_descriptor, fcntl.LOCK_EX)
            if target.exists() or target.is_symlink():
                self._require_safe_regular_file(target)
                actual_sha256, actual_size = _hash_file(target)
                if actual_sha256 != content_sha256 or actual_size != size:
                    raise OSError("existing content-addressed object failed verification")
                return False
            os.replace(temp_path, target)
            os.fsync(bucket_descriptor)
            return True
        finally:
            fcntl.flock(bucket_descriptor, fcntl.LOCK_UN)
            os.close(bucket_descriptor)
            temp_path.unlink(missing_ok=True)

    @asynccontextmanager
    async def open(self, content_sha256: str) -> AsyncIterator[BinaryIO]:
        target = object_path(self.root, content_sha256)
        self._require_safe_regular_file(target)
        handle = target.open("rb")
        try:
            yield handle
        finally:
            handle.close()

    async def verify(
        self,
        content_sha256: str,
        *,
        expected_size: int | None = None,
    ) -> ObjectVerification:
        normalized = str(content_sha256 or "").strip().lower()
        target = object_path(self.root, normalized)
        if not target.exists() and not target.is_symlink():
            return ObjectVerification(
                content_sha256=normalized,
                status=ObjectVerificationStatus.MISSING,
                path=target,
                expected_size_bytes=expected_size,
            )
        try:
            self._require_safe_regular_file(target)
            actual_sha256, actual_size = await asyncio.to_thread(_hash_file, target)
        except (OSError, ValueError):
            return ObjectVerification(
                content_sha256=normalized,
                status=ObjectVerificationStatus.CORRUPT,
                path=target,
                expected_size_bytes=expected_size,
            )

        status = ObjectVerificationStatus.READY
        if actual_sha256 != normalized or (
            expected_size is not None and actual_size != int(expected_size)
        ):
            status = ObjectVerificationStatus.CORRUPT
        return ObjectVerification(
            content_sha256=normalized,
            status=status,
            path=target,
            expected_size_bytes=expected_size,
            actual_size_bytes=actual_size,
            actual_sha256=actual_sha256,
        )

    async def remove_unreferenced(self, content_sha256: str, repository) -> bool:
        normalized = str(content_sha256 or "").strip().lower()
        target = object_path(self.root, normalized)
        connection = repository.connection
        async with repository.lock_object(normalized):
            await connection.begin()
            try:
                record = await repository.delete_metadata_if_unreferenced(normalized)
                if record is None:
                    await connection.rollback()
                    return False
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

            remaining_references = await repository.count_references(normalized)
            await connection.rollback()
            if remaining_references > 0:
                await self._restore_metadata(connection, repository, record)
                return False

            expected_relative = str(Path(normalized[:2]) / normalized)
            if record.relative_path != expected_relative:
                await self._restore_metadata(connection, repository, record)
                logger.warning(
                    "object cleanup refused hash=%s reason=relative_path_mismatch",
                    normalized[:12],
                )
                return False

            if not target.exists() and not target.is_symlink():
                return True
            try:
                self._require_safe_regular_file(target)
                target.unlink()
                return True
            except (OSError, ValueError) as exc:
                await self._restore_metadata(connection, repository, record)
                logger.warning(
                    "object cleanup failed hash=%s error=%s",
                    normalized[:12],
                    type(exc).__name__,
                )
                return False

    async def _restore_metadata(self, connection, repository, record) -> None:
        await connection.begin()
        try:
            await repository.restore_object(record)
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise

    def _require_safe_regular_file(self, target: Path) -> None:
        if target.is_symlink():
            raise ValueError("unsafe object path")
        root = self.root.resolve()
        resolved = target.resolve()
        if not resolved.is_relative_to(root) or not target.is_file():
            raise ValueError("unsafe object path")
