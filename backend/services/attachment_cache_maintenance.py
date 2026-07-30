"""Background migration and garbage collection for attachment cache objects."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from data_paths import DOWNLOADS_DIR
from db import (
    clear_cached_attachment_storage,
    list_all_attachment_cache_objects,
    list_all_cached_attachment_rows,
    list_cached_attachment_local_paths,
)
from models import CachedAttachment
from services.attachment_cache import (
    cache_attachment_file,
    release_unreferenced_objects,
    remove_stale_untracked_cache_files,
    resolve_cached_attachment_path,
)
from utils.logger import get_logger


logger = get_logger("attachment_cache_maintenance")
WEEK_SECONDS = 7 * 24 * 60 * 60
ORPHAN_GRACE_SECONDS = 60 * 60

_migration_lock = asyncio.Lock()
_maintenance_task: asyncio.Task | None = None


def is_safe_legacy_download_file(path: Path) -> bool:
    candidate = Path(path)
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return False
        return candidate.resolve().is_relative_to(DOWNLOADS_DIR.resolve())
    except OSError:
        return False


def _attachment_from_row(row: dict) -> CachedAttachment:
    return CachedAttachment(
        account_id=str(row.get("account_id") or ""),
        user_uid=str(row.get("user_uid") or ""),
        uid=int(row.get("uid") or 0),
        folder=str(row.get("folder") or "INBOX"),
        part_number=int(row.get("part_number") or 0),
        filename=str(row.get("filename") or ""),
        content_type=str(row.get("content_type") or "application/octet-stream"),
        size=int(row.get("size") or 0),
        content_id=str(row.get("content_id") or ""),
        is_inline=bool(row.get("is_inline")),
        local_path=str(row.get("local_path") or ""),
        content_sha256=str(row.get("content_sha256") or ""),
        last_accessed_at=float(row.get("last_accessed_at") or 0),
        cached_at=float(row.get("cached_at") or 0),
    )


def _remove_empty_legacy_directories() -> None:
    if not DOWNLOADS_DIR.exists():
        return
    directories = [path for path in DOWNLOADS_DIR.rglob("*") if path.is_dir() and not path.is_symlink()]
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            continue


async def migrate_legacy_attachment_cache() -> dict[str, int]:
    stats = {
        "scanned_rows": 0,
        "inline_rows_migrated": 0,
        "normal_files_removed": 0,
        "orphan_files_removed": 0,
        "skipped_rows": 0,
        "failures": 0,
    }
    async with _migration_lock:
        rows = await list_all_cached_attachment_rows()
        for row in rows:
            stats["scanned_rows"] += 1
            try:
                content_hash = str(row.get("content_sha256") or "")
                if content_hash:
                    valid_object = await resolve_cached_attachment_path(row, touch=False)
                    if valid_object:
                        stats["skipped_rows"] += 1
                        continue

                local_path_value = str(row.get("local_path") or "")
                if not local_path_value:
                    stats["skipped_rows"] += 1
                    continue
                legacy_path = Path(local_path_value)
                if not is_safe_legacy_download_file(legacy_path):
                    logger.warning(
                        "legacy attachment migration refused account=%s uid=%s part=%s file=%s",
                        str(row.get("account_id") or ""),
                        int(row.get("uid") or 0),
                        int(row.get("part_number") or 0),
                        legacy_path.name,
                    )
                    stats["skipped_rows"] += 1
                    continue

                if bool(row.get("is_inline")):
                    await cache_attachment_file(
                        _attachment_from_row(row),
                        legacy_path,
                        remove_source=True,
                        enforce_quota=False,
                    )
                    stats["inline_rows_migrated"] += 1
                    continue

                old_hash = await clear_cached_attachment_storage(
                    str(row.get("account_id") or ""),
                    int(row.get("uid") or 0),
                    str(row.get("folder") or "INBOX"),
                    int(row.get("part_number") or 0),
                )
                if old_hash:
                    await release_unreferenced_objects({old_hash})
                legacy_path.unlink(missing_ok=True)
                stats["normal_files_removed"] += 1
            except Exception as exc:
                stats["failures"] += 1
                logger.warning(
                    "legacy attachment migration failed account=%s uid=%s part=%s error=%s",
                    str(row.get("account_id") or ""),
                    int(row.get("uid") or 0),
                    int(row.get("part_number") or 0),
                    type(exc).__name__,
                )

        known_paths = set()
        for value in await list_cached_attachment_local_paths():
            try:
                known_paths.add(str(Path(value).resolve()))
            except OSError:
                continue

        if DOWNLOADS_DIR.exists():
            for path in DOWNLOADS_DIR.rglob("*"):
                try:
                    if not is_safe_legacy_download_file(path):
                        continue
                    if str(path.resolve()) in known_paths:
                        continue
                    path.unlink()
                    stats["orphan_files_removed"] += 1
                except OSError as exc:
                    stats["failures"] += 1
                    logger.warning(
                        "legacy orphan cleanup failed file=%s error=%s",
                        path.name,
                        type(exc).__name__,
                    )
        _remove_empty_legacy_directories()
    return stats


async def garbage_collect_attachment_cache(
    orphan_grace_seconds: int = ORPHAN_GRACE_SECONDS,
) -> dict[str, int]:
    rows = await list_all_attachment_cache_objects()
    result = {
        "scanned_objects": len(rows),
        "deleted_shared_objects": 0,
        "freed_physical_bytes": 0,
        "orphan_object_files": 0,
        "orphan_object_bytes": 0,
        "stale_temp_files": 0,
    }
    for row in rows:
        cleanup = await release_unreferenced_objects({str(row.get("content_sha256") or "")})
        result["deleted_shared_objects"] += cleanup.deleted_shared_objects
        result["freed_physical_bytes"] += cleanup.freed_physical_bytes

    remaining = await list_all_attachment_cache_objects()
    known_hashes = {
        str(row.get("content_sha256") or "")
        for row in remaining
        if row.get("content_sha256")
    }
    stale = await remove_stale_untracked_cache_files(
        known_hashes,
        time.time() - max(0, int(orphan_grace_seconds)),
    )
    result.update(stale)
    return result


async def _maintenance_loop() -> None:
    try:
        migration = await migrate_legacy_attachment_cache()
        logger.info("attachment cache migration completed stats=%s", migration)
        garbage = await garbage_collect_attachment_cache()
        logger.info("attachment cache garbage collection completed stats=%s", garbage)
        while True:
            await asyncio.sleep(WEEK_SECONDS)
            garbage = await garbage_collect_attachment_cache()
            logger.info("attachment cache weekly garbage collection completed stats=%s", garbage)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("attachment cache maintenance stopped error=%s", type(exc).__name__)


def start_attachment_cache_maintenance() -> None:
    global _maintenance_task
    if _maintenance_task and not _maintenance_task.done():
        return
    _maintenance_task = asyncio.create_task(
        _maintenance_loop(),
        name="attachment_cache_maintenance",
    )


async def stop_attachment_cache_maintenance() -> None:
    global _maintenance_task
    if not _maintenance_task:
        return
    _maintenance_task.cancel()
    try:
        await _maintenance_task
    except asyncio.CancelledError:
        pass
    _maintenance_task = None
