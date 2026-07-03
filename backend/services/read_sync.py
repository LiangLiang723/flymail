import asyncio

from db import (
    delete_pending_read_sync,
    get_account_by_id,
    list_pending_read_sync,
    mark_pending_read_sync_failed,
)
from models import Account
from providers.factory import ProviderFactory
from services.token import ensure_token as ensure_account_token
from utils.logger import get_logger
from utils.tasks import create_background_task

logger = get_logger("read_sync")

_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
READ_SYNC_INTERVAL_SECONDS = 30


async def _resolve_remote_folder(receiver, folder: str) -> str:
    requested = (folder or "INBOX").strip() or "INBOX"
    try:
        folders = await receiver.fetch_folders()
    except Exception:
        return requested
    requested_lower = requested.lower()
    for item in folders:
        if (item.path or "").lower() == requested_lower or (item.name or "").lower() == requested_lower:
            return item.path
    return requested


async def _remote_message_is_read(receiver, folder: str, uid_num: int) -> bool:
    unseen_uids = set(await receiver.fetch_unseen_uids(folder))
    return uid_num not in unseen_uids


async def mark_remote_message_read(account: Account, uid_num: int, folder: str) -> None:
    credentials = await ensure_account_token(account)
    receiver = ProviderFactory.get_receiver(account.provider)
    await receiver.connect(credentials)
    try:
        remote_folder = await _resolve_remote_folder(receiver, folder)
        uid_str = str(uid_num)
        try:
            await receiver.mark_as_read(uid_str, folder=remote_folder)
        except Exception:
            if not await _remote_message_is_read(receiver, remote_folder, uid_num):
                raise
    finally:
        await receiver.disconnect()


async def _sync_pending_read_once(limit: int = 100) -> int:
    rows = await list_pending_read_sync(limit)
    synced = 0
    for row in rows:
        account = await get_account_by_id(row["account_id"])
        if not account or account.status == "offline":
            continue
        if not row.get("desired_read", True):
            continue
        try:
            await mark_remote_message_read(account, row["uid"], row["folder"])
            await delete_pending_read_sync(account.id, row["uid"], row["folder"])
            synced += 1
        except Exception as exc:
            await mark_pending_read_sync_failed(account.id, row["uid"], row["folder"], str(exc))
            logger.debug(
                "pending read sync failed: account=%s uid=%s folder=%s error=%s",
                account.email,
                row["uid"],
                row["folder"],
                exc,
            )
    return synced


async def _run_pending_read_sync() -> None:
    global _stop_event
    _stop_event = asyncio.Event()
    while not _stop_event.is_set():
        try:
            synced = await _sync_pending_read_once()
            if synced:
                logger.info("pending read sync completed: %s", synced)
        except Exception as exc:
            logger.warning("pending read sync loop failed: %s", exc)
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=READ_SYNC_INTERVAL_SECONDS)
        except TimeoutError:
            pass


def start_pending_read_sync() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = create_background_task(_run_pending_read_sync(), name="pending_read_sync")


async def stop_pending_read_sync() -> None:
    global _task, _stop_event
    if _stop_event:
        _stop_event.set()
    if _task:
        await asyncio.gather(_task, return_exceptions=True)
    _task = None
    _stop_event = None
