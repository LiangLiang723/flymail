"""Shared content-addressed cache for downloaded mail attachments.

Business authorization remains in the message routes. This module only manages
physical objects, attachment references, per-user logical usage and LRU cleanup.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from data_paths import ATTACHMENT_CACHE_TMP_DIR, ATTACHMENT_SHA256_DIR
from db import (
    batch_delete_cached_messages,
    clear_cached_attachment_storage,
    clear_user_attachment_hash_references,
    delete_cached_attachments_by_account,
    delete_cached_message,
    delete_cached_messages_by_account,
    get_attachment_cache_object,
    get_shared_attachment_cache_usage_bytes,
    get_user_attachment_cache_usage_bytes,
    get_user_setting,
    list_attachment_hashes_for_messages,
    list_user_attachment_cache_lru,
    pop_unreferenced_attachment_cache_object,
    purge_deleted_from_cache,
    replace_cached_attachment_object,
    restore_attachment_cache_object,
    touch_cached_attachment_object,
    upsert_attachment_cache_object,
)
from models import CachedAttachment
from utils.logger import get_logger


logger = get_logger("attachment_cache")

ATTACHMENT_CACHE_LIMIT_KEY = "attachment_cache_limit_mb"
DEFAULT_ATTACHMENT_CACHE_LIMIT_MB = 2048
MIN_ATTACHMENT_CACHE_LIMIT_MB = 100
CHUNK_SIZE = 1024 * 1024
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_MUTATION_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class AttachmentDownloadFile:
    path: str
    transient: bool = False


@dataclass(frozen=True)
class StoredAttachmentObject:
    content_sha256: str
    size: int
    local_path: str
    created: bool


@dataclass
class AttachmentCacheCleanup:
    before_bytes: int = 0
    after_bytes: int = 0
    cleared_references: int = 0
    evicted_user_objects: int = 0
    deleted_shared_objects: int = 0
    freed_physical_bytes: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "before_bytes": self.before_bytes,
            "after_bytes": self.after_bytes,
            "cleared_references": self.cleared_references,
            "evicted_user_objects": self.evicted_user_objects,
            "deleted_shared_objects": self.deleted_shared_objects,
            "freed_physical_bytes": self.freed_physical_bytes,
        }

    def merge_physical(self, other: "AttachmentCacheCleanup") -> None:
        self.deleted_shared_objects += int(other.deleted_shared_objects or 0)
        self.freed_physical_bytes += int(other.freed_physical_bytes or 0)


def _normalize_hash(value: str) -> str:
    digest = str(value or "").strip().lower()
    return digest if _HASH_RE.fullmatch(digest) else ""


def _object_path(digest: str) -> Path:
    normalized = _normalize_hash(digest)
    if not normalized:
        raise ValueError("invalid SHA-256 digest")
    return ATTACHMENT_SHA256_DIR / normalized[:2] / normalized


def validate_attachment_cache_limit_mb(value: int) -> int:
    normalized = int(value)
    if normalized < 0 or 0 < normalized < MIN_ATTACHMENT_CACHE_LIMIT_MB:
        raise ValueError("非零容量不能低于 100 MB")
    return normalized


async def get_user_attachment_cache_limit_mb(user_uid: str) -> int:
    value = await get_user_setting(
        user_uid,
        ATTACHMENT_CACHE_LIMIT_KEY,
        DEFAULT_ATTACHMENT_CACHE_LIMIT_MB,
    )
    try:
        return validate_attachment_cache_limit_mb(int(value))
    except (TypeError, ValueError):
        return DEFAULT_ATTACHMENT_CACHE_LIMIT_MB


async def get_user_attachment_cache_usage(user_uid: str) -> int:
    return await get_user_attachment_cache_usage_bytes(user_uid)


async def get_shared_attachment_cache_usage() -> int:
    return await get_shared_attachment_cache_usage_bytes()


def _create_temp_path(suffix: str = ".part") -> Path:
    ATTACHMENT_CACHE_TMP_DIR.mkdir(parents=True, exist_ok=True)
    return ATTACHMENT_CACHE_TMP_DIR / f"{uuid.uuid4().hex}{suffix}"


def _finalize_temp_object(temp_path: Path, digest: str, size: int) -> StoredAttachmentObject:
    target = _object_path(digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        os.link(temp_path, target)
        created = True
    except FileExistsError:
        pass
    finally:
        temp_path.unlink(missing_ok=True)
    return StoredAttachmentObject(digest, int(size), str(target), created)


def store_attachment_bytes(data: bytes) -> StoredAttachmentObject:
    payload = bytes(data or b"")
    temp_path = _create_temp_path()
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("wb") as handle:
            for offset in range(0, len(payload), CHUNK_SIZE):
                chunk = payload[offset:offset + CHUNK_SIZE]
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        return _finalize_temp_object(temp_path, digest.hexdigest(), size)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def store_attachment_file(source_path: Path) -> StoredAttachmentObject:
    source = Path(source_path)
    if not source.is_file() or source.is_symlink():
        raise FileNotFoundError(str(source))
    temp_path = _create_temp_path()
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as source_handle, temp_path.open("wb") as target_handle:
            while True:
                chunk = source_handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                target_handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        return _finalize_temp_object(temp_path, digest.hexdigest(), size)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


async def _bind_stored_object(
    attachment: CachedAttachment,
    stored: StoredAttachmentObject,
) -> str:
    now = time.time()
    bound = attachment.model_copy(
        update={
            "size": stored.size,
            "local_path": stored.local_path,
            "content_sha256": stored.content_sha256,
            "last_accessed_at": now,
            "cached_at": now,
        }
    )
    await upsert_attachment_cache_object(
        stored.content_sha256,
        stored.size,
        stored.local_path,
        now,
    )
    return await replace_cached_attachment_object(bound)


async def cache_attachment_bytes(
    attachment: CachedAttachment,
    data: bytes,
    *,
    enforce_quota: bool = True,
) -> StoredAttachmentObject:
    stored: StoredAttachmentObject | None = None
    previous_hash = ""
    try:
        async with _OBJECT_MUTATION_LOCK:
            stored = await asyncio.to_thread(store_attachment_bytes, data)
            previous_hash = await _bind_stored_object(attachment, stored)
    except Exception:
        if stored is not None:
            await release_unreferenced_objects({stored.content_sha256})
        raise

    if previous_hash:
        await release_unreferenced_objects({previous_hash})
    if enforce_quota and not attachment.is_inline:
        await enforce_user_attachment_cache_limit(
            attachment.user_uid,
            protected_sha256=stored.content_sha256,
        )
    return stored


async def cache_attachment_file(
    attachment: CachedAttachment,
    source_path: Path,
    *,
    remove_source: bool = False,
    enforce_quota: bool = False,
) -> StoredAttachmentObject:
    source = Path(source_path)
    stored: StoredAttachmentObject | None = None
    previous_hash = ""
    try:
        async with _OBJECT_MUTATION_LOCK:
            stored = await asyncio.to_thread(store_attachment_file, source)
            previous_hash = await _bind_stored_object(attachment, stored)
    except Exception:
        if stored is not None:
            await release_unreferenced_objects({stored.content_sha256})
        raise

    if remove_source:
        try:
            if source.resolve() != Path(stored.local_path).resolve():
                source.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "legacy attachment source cleanup failed file=%s error=%s",
                source.name,
                type(exc).__name__,
            )
    if previous_hash:
        await release_unreferenced_objects({previous_hash})
    if enforce_quota and not attachment.is_inline:
        await enforce_user_attachment_cache_limit(
            attachment.user_uid,
            protected_sha256=stored.content_sha256,
        )
    return stored


def _is_safe_object_path(path: Path, digest: str = "") -> bool:
    try:
        if path.is_symlink():
            return False
        resolved = path.resolve()
        root = ATTACHMENT_SHA256_DIR.resolve()
        if not resolved.is_relative_to(root):
            return False
        normalized = _normalize_hash(digest)
        if normalized and resolved != _object_path(normalized).resolve():
            return False
        return True
    except (OSError, ValueError):
        return False


async def resolve_cached_attachment_path(
    attachment: dict,
    *,
    touch: bool = True,
) -> Path | None:
    digest = _normalize_hash(str((attachment or {}).get("content_sha256") or ""))
    if not digest:
        return None
    object_record = await get_attachment_cache_object(digest)
    path = Path(str((attachment or {}).get("local_path") or ""))
    valid = bool(object_record)
    if valid:
        record_path = Path(str(object_record.get("local_path") or ""))
        try:
            valid = (
                path.resolve() == record_path.resolve()
                and _is_safe_object_path(path, digest)
                and path.is_file()
            )
        except OSError:
            valid = False

    if not valid:
        previous_hash = await clear_cached_attachment_storage(
            str((attachment or {}).get("account_id") or ""),
            int((attachment or {}).get("uid") or 0),
            str((attachment or {}).get("folder") or "INBOX"),
            int((attachment or {}).get("part_number") or 0),
        )
        if previous_hash:
            await release_unreferenced_objects({previous_hash})
        return None

    if touch:
        await touch_cached_attachment_object(
            str((attachment or {}).get("account_id") or ""),
            int((attachment or {}).get("uid") or 0),
            str((attachment or {}).get("folder") or "INBOX"),
            int((attachment or {}).get("part_number") or 0),
            time.time(),
        )
    return path


async def release_unreferenced_objects(
    content_hashes: Iterable[str],
) -> AttachmentCacheCleanup:
    result = AttachmentCacheCleanup()
    normalized_hashes = sorted({digest for value in content_hashes if (digest := _normalize_hash(value))})
    async with _OBJECT_MUTATION_LOCK:
        for digest in normalized_hashes:
            record = await pop_unreferenced_attachment_cache_object(digest)
            if not record:
                continue
            path = Path(str(record.get("local_path") or ""))
            if not _is_safe_object_path(path, digest):
                await restore_attachment_cache_object(record)
                logger.warning(
                    "attachment object cleanup refused hash=%s file=%s",
                    digest[:12],
                    path.name,
                )
                continue
            if not path.exists():
                result.deleted_shared_objects += 1
                continue
            if not path.is_file():
                await restore_attachment_cache_object(record)
                continue
            try:
                path.unlink()
            except OSError as exc:
                await restore_attachment_cache_object(record)
                logger.warning(
                    "attachment object cleanup failed hash=%s file=%s error=%s",
                    digest[:12],
                    path.name,
                    type(exc).__name__,
                )
                continue
            result.deleted_shared_objects += 1
            result.freed_physical_bytes += int(record.get("size") or 0)
    return result


async def enforce_user_attachment_cache_limit(
    user_uid: str,
    limit_mb: int | None = None,
    *,
    protected_sha256: str = "",
) -> AttachmentCacheCleanup:
    limit = (
        await get_user_attachment_cache_limit_mb(user_uid)
        if limit_mb is None
        else validate_attachment_cache_limit_mb(limit_mb)
    )
    before = await get_user_attachment_cache_usage_bytes(user_uid)
    result = AttachmentCacheCleanup(before_bytes=before, after_bytes=before)
    if limit == 0:
        return result

    limit_bytes = limit * 1024 * 1024
    if before <= limit_bytes:
        return result

    protected = _normalize_hash(protected_sha256)
    current = before
    for row in await list_user_attachment_cache_lru(user_uid):
        digest = _normalize_hash(str(row.get("content_sha256") or ""))
        if not digest or digest == protected:
            continue
        cleared = await clear_user_attachment_hash_references(user_uid, digest)
        if cleared <= 0:
            continue
        result.cleared_references += int(cleared)
        result.evicted_user_objects += 1
        physical = await release_unreferenced_objects({digest})
        result.merge_physical(physical)
        current = await get_user_attachment_cache_usage_bytes(user_uid)
        result.after_bytes = current
        if current <= limit_bytes:
            break
    return result


async def should_persist_normal_attachment(user_uid: str, size: int) -> bool:
    limit = await get_user_attachment_cache_limit_mb(user_uid)
    return limit == 0 or int(size or 0) <= limit * 1024 * 1024


def write_transient_download(data: bytes) -> Path:
    path = _create_temp_path(".download")
    payload = bytes(data or b"")
    try:
        with path.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def remove_transient_download(path: Path) -> None:
    target = Path(path)
    try:
        if target.is_symlink():
            return
        resolved = target.resolve()
        if not resolved.is_relative_to(ATTACHMENT_CACHE_TMP_DIR.resolve()):
            return
        target.unlink(missing_ok=True)
    except OSError:
        return


async def delete_cached_message_and_release(account_id: str, uid: int, folder: str) -> bool:
    hashes = await list_attachment_hashes_for_messages(account_id, folder, [int(uid)])
    deleted = await delete_cached_message(account_id, int(uid), folder)
    if hashes:
        await release_unreferenced_objects(hashes)
    return deleted


async def batch_delete_cached_messages_and_release(
    account_id: str,
    uids: list[int],
    folder: str,
) -> int:
    normalized_uids = [int(uid) for uid in uids]
    hashes = await list_attachment_hashes_for_messages(account_id, folder, normalized_uids)
    deleted = await batch_delete_cached_messages(account_id, normalized_uids, folder)
    if hashes:
        await release_unreferenced_objects(hashes)
    return deleted


async def purge_deleted_from_cache_and_release(
    account_id: str,
    folder: str,
    valid_uids: set[int],
) -> int:
    hashes = await list_attachment_hashes_for_messages(account_id, folder)
    deleted = await purge_deleted_from_cache(account_id, folder, valid_uids)
    if hashes:
        await release_unreferenced_objects(hashes)
    return deleted


async def clear_account_cache_and_release(account_id: str) -> tuple[int, int]:
    hashes = await list_attachment_hashes_for_messages(account_id)
    deleted_messages = await delete_cached_messages_by_account(account_id)
    deleted_attachments = await delete_cached_attachments_by_account(account_id)
    if hashes:
        await release_unreferenced_objects(hashes)
    return deleted_messages, deleted_attachments
