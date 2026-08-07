import asyncio
import base64
import os
import time
from pathlib import Path

from fastapi import APIRouter, Body, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from data_paths import coalesce_message_date, ensure_data_dirs
from db import (
    adjust_account_folder_unread,
    batch_update_is_read,
    batch_update_cached_messages_read,
    delete_pending_read_sync,
    enqueue_pending_read_sync,
    get_accounts,
    get_cached_attachment,
    get_cached_is_read,
    get_cached_message_detail,
    get_cached_messages_by_folder,
    get_conversation_messages,
    get_folder_filter_counts,
    get_message_conversations,
    get_folder_stats,
    get_unified_inbox_messages,
    get_unified_inbox_stats,
    get_unified_inbox_filter_counts,
    get_user_settings,
    mark_all_cached_messages_read,
    list_account_folder_counts,
    list_cached_attachments,
    search_cached_messages_by_folder,
    update_cached_message_read,
    upsert_cached_messages,
    upsert_folder_stats,
)
from deps import get_uid
from errors import AppError
from models import Account, CachedAttachment
from providers.base import MessageList
from providers.factory import ProviderFactory
from routes._helpers import (
    _OUTLOOK_RECONNECTING_MSG,
    _find_account_or_error,
    _is_outlook_connection_error,
    _notify_if_permanent_token_error,
    _safe_disconnect,
    _with_outlook_retry,
)
from schemas import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    BatchMarkReadRequest,
    BatchMarkReadResponse,
    MarkAllReadRequest,
    MarkAllReadResponse,
    DeleteResponse,
    MessageItem,
    MessageListResponse,
    MessageResponse,
    MarkReadRequest,
    PrefetchMessagesRequest,
    PrefetchMessagesResponse,
    StatusResponse,
    UploadAttachmentResponse,
    RegisterNasAttachmentRequest,
    SaveAttachmentToNasRequest,
    SaveAttachmentToNasResponse,
)
from services.attachments import (
    MAX_SINGLE_FILE_SIZE,
    build_upload_path,
    is_temp_upload_path,
    resolve_compose_attachment_path,
    resolve_user_attachment_path,
    sanitize_attachment_filename,
    unique_target_file,
)
from services.attachment_cache import (
    AttachmentDownloadFile,
    batch_delete_cached_messages_and_release,
    cache_attachment_bytes,
    delete_cached_message_and_release,
    remove_transient_download,
    resolve_cached_attachment_path,
    should_persist_normal_attachment,
    write_transient_download,
)
from services.message_search import parse_message_search
from services.sync import sync_service
from services.sync_coordinator import sync_coordinator
from services.token import ensure_token as ensure_account_token
from utils.logger import get_logger
from utils.tasks import create_background_task

logger = get_logger("routes.messages")
router = APIRouter(tags=["邮件"])

ensure_data_dirs()

_REMOTE_PAGE_REFRESHING: set[tuple[str, str, int, int]] = set()
_MISSING_SUMMARY_REFRESHING: set[tuple[str, str]] = set()
ZERO_COUNT_RECHECK_SECONDS = 300
REMOTE_PAGE_FETCH_TIMEOUT_SECONDS = 45


def _extract_uid(message_id: str) -> str:
    value = str(message_id or "").strip()
    if ":" in value:
        value = value.rsplit(":", 1)[-1]
    if "_" in value:
        value = value.rsplit("_", 1)[-1]
    return value


def _message_uid_int(message_id: str) -> int:
    try:
        return int(_extract_uid(message_id))
    except ValueError as exc:
        raise AppError(400, "邮件 ID 无效") from exc


async def _get_account(request: Request, account_id: str) -> tuple[str, Account]:
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)
    if not accounts:
        raise AppError(404, "当前用户还没有绑定邮箱账号")
    account, _ = _find_account_or_error(accounts, account_id)
    return user_uid, account


REMOTE_FOLDER_ALIAS_ORDER = {
    "inbox": ["INBOX", "Inbox"],
    "sent": ["Sent Messages", "Sent Items", "[Gmail]/Sent Mail", "[Google Mail]/Sent Mail", "Sent", "Sent Mail", "已发送"],
    "drafts": ["Drafts", "[Gmail]/Drafts", "[Google Mail]/Drafts", "草稿箱"],
    "junk": ["Junk", "Junk Email", "Spam", "[Gmail]/Spam", "[Google Mail]/Spam", "垃圾邮件"],
    "trash": ["Deleted Messages", "Deleted Items", "[Gmail]/Trash", "Trash", "Deleted", "已删除"],
}


def _remote_folder_alias_key(folder: str) -> str:
    folder_lower = (folder or "INBOX").strip().lower()
    decoded_leaf = _decode_imap_modified_utf7_path(folder).lower().rsplit("/", 1)[-1]
    for key, aliases in REMOTE_FOLDER_ALIAS_ORDER.items():
        if any(folder_lower == alias.lower() for alias in aliases):
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
    return ""


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


async def _get_effective_folder_stats(account_id: str, folder: str) -> dict:
    stats = await get_folder_stats(account_id, folder)
    if stats.get("updated_at"):
        return stats

    alias_key = _remote_folder_alias_key(folder)
    if not alias_key:
        return stats
    for item in await list_account_folder_counts(account_id):
        if item.get("folder_key") == alias_key and item.get("updated_at"):
            return {
                "total_count": int(item.get("total_count", 0) or 0),
                "unread_count": int(item.get("unread_count", 0) or 0),
                "updated_at": float(item.get("updated_at", 0) or 0),
            }
    return stats


async def _resolve_remote_folder(receiver, folder: str) -> str:
    requested = (folder or "INBOX").strip() or "INBOX"
    alias_key = _remote_folder_alias_key(requested)
    if not alias_key:
        return requested

    try:
        folders = await receiver.fetch_folders()
    except Exception as exc:
        logger.debug("resolve remote folder failed: folder=%s error=%s", requested, exc)
        return requested

    for item in folders:
        if (
            _remote_folder_alias_key(item.path or "") == alias_key
            or _remote_folder_alias_key(item.name or "") == alias_key
        ):
            return item.path
    for alias in REMOTE_FOLDER_ALIAS_ORDER[alias_key]:
        alias_lower = alias.lower()
        for item in folders:
            if (item.path or "").lower() == alias_lower or (item.name or "").lower() == alias_lower:
                return item.path
    return requested


def _message_to_item(message, account_id: str) -> dict:
    return {
        "id": f"{account_id}_{message.uid}",
        "uid": message.uid,
        "subject": message.subject or "",
        "from_addr": message.from_addr or "",
        "to_addr": message.to_addr or "",
        "date": message.date or "",
        "is_read": bool(message.is_read),
        "is_starred": bool(message.is_starred),
        "folder": message.folder or "INBOX",
        "body_text": message.body_text or "",
        "body_html": message.body_html or "",
        "attachments": [
            {
                "filename": attachment.filename or "",
                "content_type": attachment.content_type or "",
                "size": attachment.size or 0,
                "part_number": attachment.part_number,
                "content_id": attachment.content_id or "",
                "is_inline": bool(attachment.is_inline),
            }
            for attachment in (message.attachments or [])
        ],
        "has_attachments": bool(message.has_attachments),
        "message_id": getattr(message, "message_id", "") or "",
        "in_reply_to": getattr(message, "in_reply_to", "") or "",
        "references_header": getattr(message, "references_header", "") or "",
        "thread_key": getattr(message, "thread_key", "") or "",
        "account_id": account_id,
    }


async def _cache_remote_page(account: Account, folder: str, result: MessageList) -> None:
    if not result:
        return
    from services.mail_cache import _messages_to_cached, try_acquire_sync_lock

    lock = await try_acquire_sync_lock(account.id)
    if not lock:
        logger.debug("cache remote page skipped: account=%s folder=%s sync busy", account.email, folder)
        return

    try:
        if result.messages:
            cached_messages = _messages_to_cached(result.messages, account)
            await upsert_cached_messages(cached_messages)
            await batch_update_is_read(
                account.id,
                folder,
                [(message.uid, 1 if message.is_read else 0) for message in result.messages if message.uid > 0],
            )
        await upsert_folder_stats(account.id, folder, result.total, result.unread_total)
    finally:
        lock.release()


async def _cache_remote_detail_with_assets(receiver, account: Account, folder: str, detail) -> dict | None:
    from services.mail_cache import _messages_to_cached, try_acquire_sync_lock
    from services.history_sync import _cache_message_assets

    lock = await try_acquire_sync_lock(account.id)
    if not lock:
        logger.debug("cache remote detail skipped: account=%s sync busy", account.email)
        cached_payload = await get_cached_message_detail(account.id, detail.uid, folder)
        if cached_payload:
            cached_payload["attachments"] = await list_cached_attachments(account.id, detail.uid, folder)
        return cached_payload or _message_to_item(detail, account.id)

    try:
        cached_detail = await get_cached_message_detail(account.id, detail.uid, folder)
        effective_message_date = coalesce_message_date(detail.date, (cached_detail or {}).get("date", ""))
        body_html, storage_path, _att_count, _inline_count = await _cache_message_assets(
            receiver,
            account,
            folder,
            detail,
        )
        detail.body_html = body_html
        detail.date = coalesce_message_date(detail.date, effective_message_date)
        cached_messages = _messages_to_cached([detail], account)
        if cached_messages:
            cached_messages[0].storage_path = storage_path
        await upsert_cached_messages(cached_messages)
        cached_payload = await get_cached_message_detail(account.id, detail.uid, folder)
        if cached_payload:
            cached_payload["attachments"] = await list_cached_attachments(account.id, detail.uid, folder)
        return cached_payload
    finally:
        lock.release()


async def _cache_detail_assets_in_background(account: Account, folder: str, uid_num: int, user_uid: str) -> None:
    if account.status == "offline" or sync_service.is_account_suspended(account.id):
        return
    receiver = None
    try:
        credentials = await ensure_account_token(account)
        receiver = ProviderFactory.get_receiver(account.provider)
        await receiver.connect(credentials)
        remote_folder = await _resolve_remote_folder(receiver, folder)
        detail = await receiver.fetch_message_detail(str(uid_num), folder=remote_folder)
        await _cache_remote_detail_with_assets(receiver, account, remote_folder, detail)
        await sync_service.refresh_clients(account.id, folder, user_uid=user_uid)
    except Exception as exc:
        logger.debug("cache detail assets in background failed: account=%s folder=%s uid=%s error=%s", account.email, folder, uid_num, exc)
    finally:
        if receiver:
            await _safe_disconnect(receiver)


async def _cached_detail_assets_complete(account_id: str, uid: int, folder: str, cached: dict | None) -> bool:
    if not cached or not (cached.get("body_text") or cached.get("body_html")):
        return False
    body_html = str(cached.get("body_html") or "")
    if "<!-- truncated -->" in body_html and "data:image/" in body_html.lower():
        return False
    return True


async def _fetch_remote_page_to_cache(
    *,
    user_uid: str,
    account: Account,
    folder: str,
    page: int,
    page_size: int,
    priority: str = "background",
) -> tuple[MessageList | None, str]:
    if account.status == "offline" or sync_service.is_account_suspended(account.id):
        return None, ""

    context_factory = (
        sync_coordinator.interactive
        if priority == "interactive"
        else sync_coordinator.background
    )
    async with context_factory(account.id) as allowed:
        if not allowed:
            return None, "账号同步任务正在占用远端连接，已返回本地邮件"

        try:
            async def _fetch_remote():
                credentials = await ensure_account_token(account)
                receiver = ProviderFactory.get_receiver(account.provider)
                await receiver.connect(credentials)
                try:
                    remote_folder = await _resolve_remote_folder(receiver, folder)
                    result = await receiver.fetch_messages(remote_folder, page=page, page_size=page_size)
                    try:
                        unseen_uids = set(await receiver.fetch_unseen_uids(remote_folder))
                        for message in result.messages:
                            message.is_read = message.uid not in unseen_uids
                    except Exception as exc:
                        logger.debug("list unread sync failed: %s", exc)
                    return result, remote_folder
                finally:
                    await _safe_disconnect(receiver)

            result, remote_folder = await asyncio.wait_for(
                _with_outlook_retry(account, _fetch_remote),
                timeout=REMOTE_PAGE_FETCH_TIMEOUT_SECONDS,
            )
            await _cache_remote_page(account, remote_folder, result)
            await sync_service.refresh_clients(account.id, folder, user_uid=user_uid)
            return result, ""
        except TimeoutError:
            message = "远端邮箱响应超时，请稍后重试"
            logger.warning(
                "fetch remote page timeout: account=%s folder=%s page=%s timeout=%ss",
                account.email,
                folder,
                page,
                REMOTE_PAGE_FETCH_TIMEOUT_SECONDS,
            )
            return None, message
        except Exception as exc:
            logger.warning("fetch remote page failed: account=%s folder=%s page=%s error=%s", account.email, folder, page, exc)
            await _notify_if_permanent_token_error(exc, account, user_uid)
            if _is_outlook_connection_error(account, str(exc)):
                return None, _OUTLOOK_RECONNECTING_MSG
            return None, str(exc)


async def _run_missing_summary_sync(account: Account, folder: str, user_uid: str) -> None:
    key = (account.id, folder or "INBOX")
    try:
        from services.mail_cache import sync_missing_message_summaries

        async def _notify_batch(_completed: int, _total: int) -> None:
            await sync_service.refresh_clients(account.id, folder, user_uid=user_uid)

        await sync_missing_message_summaries(
            account,
            folder,
            on_batch=_notify_batch,
        )
    finally:
        _MISSING_SUMMARY_REFRESHING.discard(key)


def _schedule_missing_summary_sync(account: Account, folder: str, user_uid: str) -> bool:
    key = (account.id, folder or "INBOX")
    if key in _MISSING_SUMMARY_REFRESHING:
        return False
    _MISSING_SUMMARY_REFRESHING.add(key)
    create_background_task(
        _run_missing_summary_sync(account, folder, user_uid),
        name="refresh_missing_summaries",
    )
    return True


def _refresh_remote_page_in_background(
    *,
    user_uid: str,
    account: Account,
    folder: str,
    page: int,
    page_size: int,
) -> None:
    key = (account.id, folder or "INBOX", int(page or 1), int(page_size or 50))
    if key in _REMOTE_PAGE_REFRESHING:
        return
    _REMOTE_PAGE_REFRESHING.add(key)
    task = create_background_task(
        _fetch_remote_page_to_cache(
            user_uid=user_uid,
            account=account,
            folder=folder,
            page=page,
            page_size=page_size,
        ),
        name="refresh_remote_page_cache",
    )
    task.add_done_callback(lambda _task: _REMOTE_PAGE_REFRESHING.discard(key))


async def _adjust_folder_unread_stats(account_id: str, folder: str, delta: int) -> None:
    stats = await get_folder_stats(account_id, folder)
    if not stats.get("updated_at"):
        return
    total = int(stats.get("total_count", 0) or 0)
    unread = max(0, int(stats.get("unread_count", 0) or 0) + int(delta or 0))
    await upsert_folder_stats(account_id, folder, total, unread)


async def _remote_message_is_read(receiver, folder: str, uid_num: int) -> bool:
    unseen_uids = set(await receiver.fetch_unseen_uids(folder))
    return uid_num not in unseen_uids


async def _mark_remote_message_read(account: Account, uid_num: int, folder: str) -> None:
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
        await _safe_disconnect(receiver)


async def _fetch_remote_folder_counts(
    *,
    user_uid: str,
    account: Account,
    folder: str,
) -> tuple[dict | None, str]:
    if account.status == "offline" or sync_service.is_account_suspended(account.id):
        return None, ""

    try:
        async def _fetch_counts():
            credentials = await ensure_account_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                remote_folder = await _resolve_remote_folder(receiver, folder)
                counts = await receiver.fetch_folder_counts([remote_folder])
                return counts.get(remote_folder) or {}, remote_folder
            finally:
                await _safe_disconnect(receiver)

        counts, remote_folder = await _with_outlook_retry(account, _fetch_counts)
        total = int(counts.get("total", 0) or 0)
        unread = int(counts.get("unread", 0) or 0)
        await upsert_folder_stats(account.id, remote_folder, total, unread)
        return {"total": total, "unread": unread}, ""
    except Exception as exc:
        logger.warning("fetch remote folder counts failed: account=%s folder=%s error=%s", account.email, folder, exc)
        await _notify_if_permanent_token_error(exc, account, user_uid)
        if _is_outlook_connection_error(account, str(exc)):
            return None, _OUTLOOK_RECONNECTING_MSG
        return None, str(exc)


async def _persist_attachment_locally(
    *,
    account: Account,
    user_uid: str,
    folder: str,
    uid_num: int,
    message_date: str,
    attachment,
    data: bytes,
) -> AttachmentDownloadFile:
    del message_date
    record = CachedAttachment(
        account_id=account.id,
        user_uid=user_uid,
        uid=uid_num,
        folder=folder,
        part_number=attachment.part_number,
        filename=attachment.filename or "",
        content_type=attachment.content_type or "application/octet-stream",
        size=attachment.size or len(data),
        content_id=attachment.content_id or "",
        is_inline=bool(attachment.is_inline),
        cached_at=time.time(),
    )
    if not record.is_inline and not await should_persist_normal_attachment(user_uid, len(data)):
        temp_path = await asyncio.to_thread(write_transient_download, data)
        return AttachmentDownloadFile(path=str(temp_path), transient=True)

    stored = await cache_attachment_bytes(
        record,
        data,
        enforce_quota=not record.is_inline,
    )
    return AttachmentDownloadFile(path=stored.local_path, transient=False)


def _build_list_response(payload: dict, account_id: str, filter_counts: dict) -> dict:
    return {
        "messages": payload.get("messages", []),
        "total": payload.get("total", 0),
        "unread_total": payload.get("unread_total", 0),
        "page": payload.get("page", 1),
        "page_size": payload.get("page_size", 50),
        "account_id": account_id,
        "filter_counts": filter_counts,
    }


def _remote_filter_counts(result: MessageList, local_filter_counts: dict) -> dict:
    return _remote_count_filter_counts(result.total, result.unread_total, local_filter_counts)


def _remote_count_filter_counts(total_count: int, unread_count: int, local_filter_counts: dict) -> dict:
    total = int(total_count or 0)
    unread = int(unread_count or 0)
    return {
        "all": total,
        "unread": unread,
        "read": max(0, total - unread),
        "attachments": int((local_filter_counts or {}).get("attachments", 0) or 0),
    }


def _build_remote_list_response(result: MessageList, account_id: str, filter_counts: dict) -> dict:
    return {
        "messages": [_message_to_item(message, account_id) for message in result.messages],
        "total": result.total,
        "unread_total": result.unread_total,
        "page": result.page,
        "page_size": result.page_size,
        "account_id": account_id,
        "filter_counts": filter_counts,
    }


def _trust_zero_folder_stats(folder: str, folder_stats: dict) -> bool:
    alias_key = _remote_folder_alias_key(folder)
    if alias_key != "sent":
        return True
    return False


def _local_page_is_complete(
    local_data: dict,
    folder_stats: dict,
    page: int,
    page_size: int,
    read_filter: str = "",
    trust_zero_stats: bool = True,
) -> bool:
    if not folder_stats.get("updated_at"):
        return False

    total_count = int(folder_stats.get("total_count", 0) or 0)
    unread_count = int(folder_stats.get("unread_count", 0) or 0)
    if read_filter == "unread":
        remote_total = unread_count
    elif read_filter == "read":
        remote_total = max(0, total_count - unread_count)
    else:
        remote_total = total_count
    local_total = int(local_data.get("total", 0) or 0)
    if remote_total == 0:
        return trust_zero_stats
    if local_total < remote_total:
        return False

    expected_page_size = min(page_size, max(0, remote_total - max(0, page - 1) * page_size))
    return len(local_data.get("messages", [])) >= expected_page_size


def _apply_remote_filter_counts(local_data: dict, folder_stats: dict) -> None:
    local_data["total"] = int(folder_stats.get("total_count", 0) or 0)
    local_data["unread_total"] = int(folder_stats.get("unread_count", 0) or 0)
    local_data["filter_counts"] = _remote_count_filter_counts(
        local_data["total"],
        local_data["unread_total"],
        local_data.get("filter_counts", {}),
    )


def _apply_filter_total(local_data: dict, read_filter: str) -> None:
    counts = local_data.get("filter_counts") or {}
    if read_filter == "unread":
        local_data["total"] = int(counts.get("unread", 0) or 0)
    elif read_filter == "read":
        local_data["total"] = int(counts.get("read", 0) or 0)


async def _load_local_messages(
    *,
    user_uid: str,
    account: Account,
    folder: str,
    page: int,
    page_size: int,
    read_filter: str,
    attachment_filter: bool,
    keyword: str = "",
    from_addr: str = "",
    to_addr: str = "",
    subject: str = "",
    body: str = "",
    after: str = "",
    before: str = "",
    starred_filter: bool = False,
) -> dict:
    has_search_conditions = any(
        value.strip() for value in (keyword, from_addr, to_addr, subject, body, after, before)
    ) or starred_filter
    if has_search_conditions:
        data = await search_cached_messages_by_folder(
            user_uid,
            account.id,
            folder,
            keyword.strip(),
            page=page,
            page_size=page_size,
            read_filter=read_filter,
            attachment_filter=attachment_filter,
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=body,
            after=after,
            before=before,
            starred_filter=starred_filter,
        )
    else:
        data = await get_cached_messages_by_folder(
            user_uid,
            account.id,
            folder,
            page=page,
            page_size=page_size,
            read_filter=read_filter,
            attachment_filter=attachment_filter,
        )
    filter_counts = await get_folder_filter_counts(user_uid, account.id, folder)
    return _build_list_response(data, account.id, filter_counts)


@router.get("/api/messages/unified", response_model=MessageListResponse, summary="获取聚合收件箱")
async def list_unified_messages(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=40, ge=1, le=100),
    account_filter: str = Query(default=""),
    read_filter: str = Query(default=""),
    attachment_filter: bool = Query(default=False),
    keyword: str = Query(default=""),
):
    user_uid = await get_uid(request)
    accounts = await get_accounts(user_uid)
    if not accounts:
        return {"messages": [], "total": 0, "unread_total": 0, "page": page, "page_size": page_size, "no_accounts": True}
    settings = await get_user_settings(
        user_uid,
        ["unified_inbox_enabled", "unified_account_ids"],
    )
    if not bool(settings.get("unified_inbox_enabled", False)):
        return {"messages": [], "total": 0, "unread_total": 0, "page": page, "page_size": page_size, "no_accounts": True}
    configured_ids = settings.get("unified_account_ids", [])
    valid_ids = {account.id for account in accounts}
    account_ids = [account_id for account_id in configured_ids if account_id in valid_ids]
    if not account_ids:
        return {"messages": [], "total": 0, "unread_total": 0, "page": page, "page_size": page_size, "no_accounts": True}
    result = await get_unified_inbox_messages(
        user_uid, account_ids, page, page_size, account_filter, read_filter, attachment_filter, keyword
    )
    account_map = {account.id: account for account in accounts}
    for message in result["messages"]:
        account = account_map.get(message.get("account_id", ""))
        if account:
            message["account_email"] = account.email
            message["account_provider"] = account.provider
    stats = await get_unified_inbox_stats(user_uid, account_ids)
    if stats["total_count"] > 0:
        result["total"] = stats["total_count"]
        result["unread_total"] = stats["unread_count"]
    result["filter_counts"] = await get_unified_inbox_filter_counts(user_uid, account_ids, account_filter)
    return result


@router.get("/api/messages", response_model=MessageListResponse, summary="获取邮件列表")
async def list_messages(
    request: Request,
    folder: str = Query(default="INBOX"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    account_id: str = Query(default=""),
    read_filter: str = Query(default=""),
    attachment_filter: bool = Query(default=False),
):
    user_uid, account = await _get_account(request, account_id)
    local_data = await _load_local_messages(
        user_uid=user_uid,
        account=account,
        folder=folder,
        page=page,
        page_size=page_size,
        read_filter=read_filter,
        attachment_filter=attachment_filter,
    )
    folder_stats = await _get_effective_folder_stats(account.id, folder)
    trust_zero_stats = _trust_zero_folder_stats(folder, folder_stats)

    if not read_filter and not attachment_filter:
        if _local_page_is_complete(local_data, folder_stats, page, page_size, read_filter, trust_zero_stats):
            _apply_remote_filter_counts(local_data, folder_stats)
            return local_data
        if local_data.get("messages"):
            if account.status != "offline" and not sync_service.is_account_suspended(account.id):
                if page > 1:
                    result, error = await _fetch_remote_page_to_cache(
                        user_uid=user_uid,
                        account=account,
                        folder=folder,
                        page=page,
                        page_size=page_size,
                        priority="interactive",
                    )
                    if result:
                        local_filter_counts = await get_folder_filter_counts(user_uid, account.id, folder)
                        return _build_remote_list_response(
                            result,
                            account.id,
                            _remote_filter_counts(result, local_filter_counts),
                        )
                    if error == _OUTLOOK_RECONNECTING_MSG:
                        local_data["reconnecting"] = True
                    if error:
                        local_data["error"] = error
                else:
                    _refresh_remote_page_in_background(
                        user_uid=user_uid,
                        account=account,
                        folder=folder,
                        page=page,
                        page_size=page_size,
                    )
            if folder_stats.get("updated_at"):
                _apply_remote_filter_counts(local_data, folder_stats)
            return local_data

        if folder_stats.get("updated_at"):
            if account.status != "offline" and not sync_service.is_account_suspended(account.id):
                result, error = await _fetch_remote_page_to_cache(
                    user_uid=user_uid,
                    account=account,
                    folder=folder,
                    page=page,
                    page_size=page_size,
                    priority="interactive",
                )
                if result:
                    local_filter_counts = await get_folder_filter_counts(user_uid, account.id, folder)
                    return _build_remote_list_response(
                        result,
                        account.id,
                        _remote_filter_counts(result, local_filter_counts),
                    )
                if error == _OUTLOOK_RECONNECTING_MSG:
                    local_data["reconnecting"] = True
                if error:
                    local_data["error"] = error
            _apply_remote_filter_counts(local_data, folder_stats)
            return local_data

        result, error = await _fetch_remote_page_to_cache(
            user_uid=user_uid,
            account=account,
            folder=folder,
            page=page,
            page_size=page_size,
            priority="interactive",
        )
        local_filter_counts = await get_folder_filter_counts(user_uid, account.id, folder)
        if result:
            return _build_remote_list_response(
                result,
                account.id,
                _remote_filter_counts(result, local_filter_counts),
            )
        if error == _OUTLOOK_RECONNECTING_MSG:
            local_data["reconnecting"] = True
            local_data["error"] = error
            return local_data

    remote_result = None
    remote_error = ""
    if read_filter and not attachment_filter and not _local_page_is_complete(local_data, folder_stats, page, page_size, read_filter, trust_zero_stats):
        if not local_data.get("messages"):
            if folder_stats.get("updated_at"):
                if account.status != "offline" and not sync_service.is_account_suspended(account.id):
                    _refresh_remote_page_in_background(
                        user_uid=user_uid,
                        account=account,
                        folder=folder,
                        page=page,
                        page_size=page_size,
                    )
            else:
                remote_result, remote_error = await _fetch_remote_page_to_cache(
                    user_uid=user_uid,
                    account=account,
                    folder=folder,
                    page=page,
                    page_size=page_size,
                    priority="interactive",
                )

    if read_filter and not attachment_filter:
        if remote_result:
            local_data = await _load_local_messages(
                user_uid=user_uid,
                account=account,
                folder=folder,
                page=page,
                page_size=page_size,
                read_filter=read_filter,
                attachment_filter=attachment_filter,
            )
            local_data["filter_counts"] = _remote_filter_counts(
                remote_result,
                local_data.get("filter_counts", {}),
            )
        elif _local_page_is_complete(local_data, folder_stats, page, page_size, read_filter, trust_zero_stats):
            _apply_remote_filter_counts(local_data, folder_stats)
        elif folder_stats.get("updated_at"):
            _apply_remote_filter_counts(local_data, folder_stats)
        else:
            remote_counts, error = await _fetch_remote_folder_counts(
                user_uid=user_uid,
                account=account,
                folder=folder,
            )
            remote_error = remote_error or error
            if remote_counts:
                local_data["filter_counts"] = _remote_count_filter_counts(
                    remote_counts.get("total", 0),
                    remote_counts.get("unread", 0),
                    local_data.get("filter_counts", {}),
                )
        _apply_filter_total(local_data, read_filter)
        if remote_error == _OUTLOOK_RECONNECTING_MSG:
            local_data["reconnecting"] = True
            local_data["error"] = remote_error
    return local_data


@router.get("/api/messages/search", response_model=MessageListResponse, summary="搜索邮件")
async def search_messages(
    request: Request,
    folder: str = Query(default="INBOX"),
    keyword: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    account_id: str = Query(default=""),
    read_filter: str = Query(default=""),
    attachment_filter: bool = Query(default=False),
    starred_filter: bool = Query(default=False),
    from_addr: str = Query(default=""),
    to_addr: str = Query(default=""),
    subject: str = Query(default=""),
    body: str = Query(default=""),
    after: str = Query(default=""),
    before: str = Query(default=""),
):
    user_uid, account = await _get_account(request, account_id)
    parsed = parse_message_search(keyword)
    effective_read_filter = read_filter or parsed.read_state
    effective_attachment_filter = attachment_filter or parsed.has_attachment
    effective_starred_filter = starred_filter or parsed.starred

    # Search must feel instant: query the local cache immediately and refresh the
    # newest remote page in the background instead of blocking on IMAP/network I/O.
    if account.status != "offline" and not sync_service.is_account_suspended(account.id):
        _refresh_remote_page_in_background(
            user_uid=user_uid,
            account=account,
            folder=folder,
            page=1,
            page_size=page_size,
        )

    return await _load_local_messages(
        user_uid=user_uid,
        account=account,
        folder=folder,
        page=page,
        page_size=page_size,
        read_filter=effective_read_filter,
        attachment_filter=effective_attachment_filter,
        keyword=parsed.free_text,
        from_addr=from_addr or parsed.from_addr,
        to_addr=to_addr or parsed.to_addr,
        subject=subject or parsed.subject,
        body=body,
        after=after or parsed.after,
        before=before or parsed.before,
        starred_filter=effective_starred_filter,
    )


@router.get("/api/messages/conversations", response_model=MessageListResponse, summary="获取邮件会话列表")
async def list_message_conversations(
    request: Request,
    folder: str = Query(default="INBOX"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    account_id: str = Query(default=""),
    keyword: str = Query(default=""),
    read_filter: str = Query(default=""),
    attachment_filter: bool = Query(default=False),
    starred_filter: bool = Query(default=False),
    from_addr: str = Query(default=""),
    to_addr: str = Query(default=""),
    subject: str = Query(default=""),
    body: str = Query(default=""),
    after: str = Query(default=""),
    before: str = Query(default=""),
):
    user_uid, account = await _get_account(request, account_id)
    parsed = parse_message_search(keyword)
    data = await get_message_conversations(
        user_uid,
        account.id,
        folder,
        page=page,
        page_size=page_size,
        keyword=parsed.free_text,
        read_filter=read_filter or parsed.read_state,
        attachment_filter=attachment_filter or parsed.has_attachment,
        from_addr=from_addr or parsed.from_addr,
        to_addr=to_addr or parsed.to_addr,
        subject=subject or parsed.subject,
        body=body,
        after=after or parsed.after,
        before=before or parsed.before,
        starred_filter=starred_filter or parsed.starred,
    )
    data["account_id"] = account.id
    data["filter_counts"] = await get_folder_filter_counts(user_uid, account.id, folder)
    return data


@router.get("/api/messages/conversation", response_model=MessageListResponse, summary="获取会话内邮件")
async def list_conversation_messages(
    request: Request,
    thread_key: str = Query(min_length=1, max_length=191),
    folder: str = Query(default="INBOX"),
    account_id: str = Query(default=""),
):
    user_uid, account = await _get_account(request, account_id)
    messages = await get_conversation_messages(user_uid, account.id, folder, thread_key)
    return {
        "messages": messages,
        "total": len(messages),
        "unread_total": sum(1 for item in messages if not item.get("is_read")),
        "page": 1,
        "page_size": max(1, len(messages)),
        "account_id": account.id,
        "filter_counts": {},
    }


@router.get("/api/messages/refresh", response_model=MessageListResponse, summary="刷新最近一页邮件")
async def refresh_messages(
    request: Request,
    folder: str = Query(default="INBOX"),
    page_size: int = Query(default=50, ge=1, le=100),
    account_id: str = Query(default=""),
):
    user_uid, account = await _get_account(request, account_id)

    async def _local_result(error: str = ""):
        data = await _load_local_messages(
            user_uid=user_uid,
            account=account,
            folder=folder,
            page=1,
            page_size=page_size,
            read_filter="",
            attachment_filter=False,
        )
        if error:
            data["error"] = error
        return data

    if account.status == "offline":
        return await _local_result()
    if await sync_coordinator.is_exclusive(account.id):
        return await _local_result("历史同步或缓存维护正在进行，已返回本地邮件")

    try:
        async def _refresh_remote():
            credentials = await ensure_account_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                remote_folder = await _resolve_remote_folder(receiver, folder)
                result = await receiver.fetch_messages(remote_folder, page=1, page_size=page_size)
                try:
                    unseen_uids = set(await receiver.fetch_unseen_uids(remote_folder))
                    for message in result.messages:
                        message.is_read = message.uid not in unseen_uids
                except Exception as exc:
                    logger.debug("refresh unread sync failed: %s", exc)
                return result, remote_folder
            finally:
                await _safe_disconnect(receiver)

        async with sync_coordinator.interactive(account.id) as allowed:
            if not allowed:
                return await _local_result("历史同步或缓存维护正在进行，已返回本地邮件")
            result, remote_folder = await asyncio.wait_for(
                _with_outlook_retry(account, _refresh_remote),
                timeout=REMOTE_PAGE_FETCH_TIMEOUT_SECONDS,
            )
            await _cache_remote_page(account, remote_folder, result)
            await sync_service.refresh_clients(account.id, folder, user_uid=user_uid)
        _schedule_missing_summary_sync(account, remote_folder, user_uid)
    except TimeoutError:
        logger.warning(
            "refresh messages timeout: account=%s folder=%s timeout=%ss",
            account.email,
            folder,
            REMOTE_PAGE_FETCH_TIMEOUT_SECONDS,
        )
        return await _local_result("远端邮箱响应超时，请稍后重试")
    except Exception as exc:
        logger.warning("refresh messages failed: account=%s folder=%s error=%s", account.email, folder, exc)
        await _notify_if_permanent_token_error(exc, account, user_uid)
        if _is_outlook_connection_error(account, str(exc)):
            local_data = await _local_result()
            local_data["reconnecting"] = True
            local_data["error"] = _OUTLOOK_RECONNECTING_MSG
            return local_data
        return await _local_result("刷新远端邮箱失败，请稍后重试")

    return await _local_result()


@router.get("/api/messages/{message_id}", response_model=MessageItem, summary="获取邮件详情")
async def get_message_detail(
    request: Request,
    message_id: str,
    folder: str = Query(default="INBOX"),
    account_id: str = Query(default=""),
):
    user_uid, account = await _get_account(request, account_id)
    uid_num = _message_uid_int(message_id)

    cached = await get_cached_message_detail(account.id, uid_num, folder)
    if cached and await _cached_detail_assets_complete(account.id, uid_num, folder, cached):
        cached["attachments"] = await list_cached_attachments(account.id, uid_num, folder)
        return cached

    if account.status == "offline":
        if cached:
            cached["attachments"] = await list_cached_attachments(account.id, uid_num, folder)
            return cached
        raise AppError(404, "本地未找到这封邮件，且账号当前处于离线状态")

    try:
        async def _fetch_detail():
            credentials = await ensure_account_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                remote_folder = await _resolve_remote_folder(receiver, folder)
                detail = await receiver.fetch_message_detail(str(uid_num), folder=remote_folder)
                cached_payload = await _cache_remote_detail_with_assets(receiver, account, remote_folder, detail)
                return cached_payload or _message_to_item(detail, account.id)
            finally:
                await _safe_disconnect(receiver)

        payload = await _with_outlook_retry(account, _fetch_detail)
        cached_is_read = await get_cached_is_read(account.id, uid_num, folder)
        if cached_is_read is not None:
            payload["is_read"] = cached_is_read
        return payload
    except Exception as exc:
        logger.error("load message detail failed: account=%s uid=%s folder=%s error=%s", account.email, uid_num, folder, exc)
        await _notify_if_permanent_token_error(exc, account, user_uid)
        if _is_outlook_connection_error(account, str(exc)):
            raise AppError(503, _OUTLOOK_RECONNECTING_MSG)
        raise AppError(500, "加载邮件详情失败")


@router.post("/api/prefetch-messages", response_model=PrefetchMessagesResponse, summary="预取邮件正文")
async def prefetch_messages(
    request: Request,
    body: PrefetchMessagesRequest = Body(default_factory=PrefetchMessagesRequest),
):
    user_uid, account = await _get_account(request, body.account_id)
    message_ids = [msg_id for msg_id in body.message_ids if msg_id]
    if not message_ids or account.status == "offline":
        return {"success": True, "queued": 0, "prefetched": 0}
    if sync_service.is_account_suspended(account.id):
        return {"success": True, "queued": 0, "prefetched": 0}

    async def _prefetch():
        prefetched = 0
        resolved_folder = body.folder
        folder_resolved = False
        for message_id in message_ids[:50]:
            uid_num = _message_uid_int(message_id)
            cached = await get_cached_message_detail(account.id, uid_num, body.folder)
            if await _cached_detail_assets_complete(account.id, uid_num, body.folder, cached):
                continue
            try:
                credentials = await ensure_account_token(account)
                receiver = ProviderFactory.get_receiver(account.provider)
                await receiver.connect(credentials)
                try:
                    if not folder_resolved:
                        resolved_folder = await _resolve_remote_folder(receiver, body.folder)
                        folder_resolved = True
                    detail = await receiver.fetch_message_detail(str(uid_num), folder=resolved_folder)
                    await _cache_remote_detail_with_assets(receiver, account, resolved_folder, detail)
                finally:
                    await _safe_disconnect(receiver)
                prefetched += 1
            except Exception as exc:
                logger.debug("prefetch message failed: account=%s uid=%s error=%s", account.email, uid_num, exc)
        if prefetched:
            await sync_service.refresh_clients(account.id, body.folder, user_uid=user_uid)

    create_background_task(_prefetch(), name="prefetch_messages")
    return {"success": True, "queued": len(message_ids[:50]), "prefetched": 0}


@router.get(
    "/api/messages/{message_id}/attachments/{part_number}",
    response_class=FileResponse,
    summary="下载附件",
)
async def download_attachment(
    request: Request,
    message_id: str,
    part_number: int,
    folder: str = Query(default="INBOX"),
    account_id: str = Query(default=""),
):
    user_uid, account = await _get_account(request, account_id)
    uid_num = _message_uid_int(message_id)

    cached_attachment = await get_cached_attachment(account.id, uid_num, folder, part_number)
    if cached_attachment:
        local_path = await resolve_cached_attachment_path(cached_attachment, touch=True)
        if local_path:
            filename = cached_attachment.get("filename") or local_path.name
            return FileResponse(str(local_path), filename=filename)

    if account.status == "offline":
        raise AppError(404, "本地未找到附件，且账号当前处于离线状态")

    try:
        async def _fetch_attachment():
            credentials = await ensure_account_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                remote_folder = await _resolve_remote_folder(receiver, folder)
                detail = await receiver.fetch_message_detail(str(uid_num), folder=remote_folder)
                attachment = next((att for att in detail.attachments if att.part_number == part_number), None)
                if not attachment:
                    raise AppError(404, "附件不存在")
                data = await receiver.fetch_attachment_data(str(uid_num), remote_folder, part_number)
                if data is None:
                    raise AppError(404, "附件内容不存在")
                return detail, attachment, data
            finally:
                await _safe_disconnect(receiver)

        detail, attachment, data = await _with_outlook_retry(account, _fetch_attachment)
        download_file = await _persist_attachment_locally(
            account=account,
            user_uid=user_uid,
            folder=folder,
            uid_num=uid_num,
            message_date=detail.date or "",
            attachment=attachment,
            data=data,
        )
        background = None
        if download_file.transient:
            background = BackgroundTask(remove_transient_download, Path(download_file.path))
        return FileResponse(
            download_file.path,
            filename=attachment.filename or Path(download_file.path).name,
            background=background,
        )
    except AppError:
        raise
    except Exception as exc:
        logger.error("download attachment failed: account=%s uid=%s part=%s error=%s", account.email, uid_num, part_number, exc)
        await _notify_if_permanent_token_error(exc, account, user_uid)
        if _is_outlook_connection_error(account, str(exc)):
            raise AppError(503, _OUTLOOK_RECONNECTING_MSG)
        raise AppError(500, "下载附件失败")


@router.post("/api/messages/upload-attachment", response_model=UploadAttachmentResponse, summary="上传附件")
async def upload_attachment(request: Request, file: UploadFile = File(...)):
    user_uid = await get_uid(request)
    if not file.filename:
        raise AppError(400, "附件文件名不能为空")
    safe_filename, save_path = build_upload_path(user_uid, file.filename)
    content = await file.read()
    if len(content) > MAX_SINGLE_FILE_SIZE:
        raise AppError(413, f"单个文件不能超过 {MAX_SINGLE_FILE_SIZE // (1024 * 1024)}MB")
    await asyncio.to_thread(save_path.write_bytes, content)
    return {
        "filename": safe_filename,
        "size": len(content),
        "path": str(save_path),
        "source": "local",
    }


@router.post(
    "/api/messages/register-nas-attachment",
    response_model=UploadAttachmentResponse,
    summary="引用 NAS 授权目录附件",
)
async def register_nas_attachment(request: Request, body: RegisterNasAttachmentRequest = Body(...)):
    user_uid = await get_uid(request)
    file_path = resolve_compose_attachment_path(user_uid, body.path)
    if is_temp_upload_path(user_uid, str(file_path)):
        raise AppError(400, "临时附件请使用上传接口")
    size = file_path.stat().st_size
    if size > MAX_SINGLE_FILE_SIZE:
        raise AppError(413, f"单个文件不能超过 {MAX_SINGLE_FILE_SIZE // (1024 * 1024)}MB")
    return {
        "filename": sanitize_attachment_filename(file_path.name),
        "size": size,
        "path": str(file_path),
        "source": "nas",
    }


@router.post(
    "/api/messages/{message_id}/attachments/{part_number}/save-to-nas",
    response_model=SaveAttachmentToNasResponse,
    summary="将邮件附件保存到 NAS 授权目录",
)
async def save_attachment_to_nas(
    request: Request,
    message_id: str,
    part_number: int,
    body: SaveAttachmentToNasRequest = Body(...),
):
    import shutil
    from utils.paths import is_path_authorized

    await get_uid(request)
    target_dir_raw = (body.target_dir or "").strip()
    if not target_dir_raw or not is_path_authorized(target_dir_raw):
        raise AppError(403, "目标目录不在授权范围内")
    target_dir = Path(target_dir_raw).resolve()
    if not target_dir.is_dir() or not os.access(target_dir, os.W_OK):
        raise AppError(400, "目标目录不存在或不可写")

    response = await download_attachment(
        request=request,
        message_id=message_id,
        part_number=part_number,
        folder=body.folder or "INBOX",
        account_id=body.account_id,
    )
    source_path = Path(response.path).resolve()
    raw_name = (body.filename or "").strip() or source_path.name
    destination = unique_target_file(target_dir, raw_name)
    if not is_path_authorized(str(destination)):
        raise AppError(403, "目标文件路径不在授权范围内")
    await asyncio.to_thread(shutil.copyfile, source_path, destination)
    return {
        "success": True,
        "path": str(destination),
        "filename": destination.name,
        "size": destination.stat().st_size,
    }


@router.delete("/api/messages/upload-attachment", response_model=StatusResponse, summary="删除已上传附件")
async def delete_attachment(request: Request, path: str = Query(..., description="附件路径")):
    user_uid = await get_uid(request)
    if not is_temp_upload_path(user_uid, path):
        from utils.paths import is_path_authorized
        if is_path_authorized(path):
            return {"success": True}
        raise AppError(403, "无权访问该附件")
    target = resolve_user_attachment_path(user_uid, path)
    if target.exists() and target.is_file():
        target.unlink()
    return {"success": True}


@router.delete("/api/messages/{message_id}", response_model=DeleteResponse, summary="删除邮件")
async def delete_message_api(
    request: Request,
    message_id: str,
    folder: str = Query(default="INBOX"),
    account_id: str = Query(default=""),
):
    user_uid, account = await _get_account(request, account_id)
    uid_num = _message_uid_int(message_id)
    uid_str = str(uid_num)

    if account.status != "offline":
        try:
            credentials = await ensure_account_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                source_folder = await _resolve_remote_folder(receiver, folder)
                trash_folder = await _resolve_remote_folder(receiver, "Trash")
                if trash_folder and source_folder != trash_folder and hasattr(receiver, "move_message"):
                    await receiver.move_message(uid_str, trash_folder, source_folder=source_folder)
                    action = "move"
                else:
                    await receiver.delete_message(uid_str, folder=source_folder)
                    action = "delete"
            finally:
                await _safe_disconnect(receiver)
        except Exception as exc:
            logger.error("delete message failed: account=%s uid=%s folder=%s error=%s", account.email, uid_num, folder, exc)
            await _notify_if_permanent_token_error(exc, account, user_uid)
            if _is_outlook_connection_error(account, str(exc)):
                raise AppError(503, _OUTLOOK_RECONNECTING_MSG)
            raise AppError(500, "删除邮件失败")
    else:
        action = "delete"

    await delete_cached_message_and_release(account.id, uid_num, folder)
    await sync_service.notify_message_state_changed(account.id, action, [uid_str], folder=folder, user_uid=user_uid)
    return {"success": True}


@router.post("/api/messages/batch-delete", response_model=BatchDeleteResponse, summary="批量删除邮件")
async def batch_delete_messages(
    request: Request,
    body: BatchDeleteRequest = Body(...),
):
    user_uid, account = await _get_account(request, body.account_id)
    uid_nums = [_message_uid_int(message_id) for message_id in body.message_ids]
    uid_strs = [str(uid_num) for uid_num in uid_nums]

    action = "delete"
    if account.status != "offline" and uid_strs:
        try:
            credentials = await ensure_account_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                source_folder = await _resolve_remote_folder(receiver, body.folder)
                trash_folder = await _resolve_remote_folder(receiver, "Trash")
                if trash_folder and source_folder != trash_folder and hasattr(receiver, "move_message_batch"):
                    moved = await receiver.move_message_batch(uid_strs, trash_folder, source_folder=source_folder)
                    action = "move"
                    deleted_count = moved
                elif hasattr(receiver, "delete_message_batch"):
                    deleted_count = await receiver.delete_message_batch(uid_strs, folder=source_folder)
                else:
                    deleted_count = 0
                    for uid_str in uid_strs:
                        await receiver.delete_message(uid_str, folder=source_folder)
                        deleted_count += 1
            finally:
                await _safe_disconnect(receiver)
        except Exception as exc:
            logger.error("batch delete failed: account=%s folder=%s error=%s", account.email, body.folder, exc)
            await _notify_if_permanent_token_error(exc, account, user_uid)
            if _is_outlook_connection_error(account, str(exc)):
                raise AppError(503, _OUTLOOK_RECONNECTING_MSG)
            raise AppError(500, "批量删除邮件失败")
    else:
        deleted_count = len(uid_strs)

    await batch_delete_cached_messages_and_release(account.id, uid_nums, body.folder)
    await sync_service.notify_message_state_changed(account.id, action, uid_strs, folder=body.folder, user_uid=user_uid)
    return {"success": True, "deleted": deleted_count}


@router.post("/api/mark-read", response_model=MessageResponse, summary="标记单封邮件为已读")
async def mark_message_as_read(
    request: Request,
    body: MarkReadRequest = Body(...),
):
    user_uid, account = await _get_account(request, body.account_id)
    uid_num = _message_uid_int(body.message_id)
    uid_str = str(uid_num)
    was_read = await get_cached_is_read(account.id, uid_num, body.folder)

    if account.status != "offline":
        try:
            await _mark_remote_message_read(account, uid_num, body.folder)
            await delete_pending_read_sync(account.id, uid_num, body.folder)
        except Exception as exc:
            logger.warning("mark read queued: account=%s uid=%s folder=%s error=%s", account.email, uid_num, body.folder, exc)
            await _notify_if_permanent_token_error(exc, account, user_uid)
            if _is_outlook_connection_error(account, str(exc)):
                raise AppError(503, _OUTLOOK_RECONNECTING_MSG)
            await enqueue_pending_read_sync(account.id, user_uid, uid_num, body.folder, True, str(exc))

    updated = await update_cached_message_read(account.id, uid_num, body.folder, True)
    if updated and was_read is False:
        await adjust_account_folder_unread(account.id, body.folder, -1)
        await _adjust_folder_unread_stats(account.id, body.folder, -1)
    await sync_service.notify_message_state_changed(account.id, "mark_read", [uid_str], folder=body.folder, user_uid=user_uid)
    return {"success": True, "message": "ok"}


@router.post("/api/messages/batch-mark-read", response_model=BatchMarkReadResponse, summary="批量标记已读")
async def batch_mark_read(
    request: Request,
    body: BatchMarkReadRequest = Body(...),
):
    user_uid, account = await _get_account(request, body.account_id)
    uid_nums = [_message_uid_int(message_id) for message_id in body.message_ids]
    uid_strs = [str(uid_num) for uid_num in uid_nums]
    unread_before = 0
    for uid_num in uid_nums:
        if await get_cached_is_read(account.id, uid_num, body.folder) is False:
            unread_before += 1

    marked = len(uid_nums)
    if account.status != "offline" and uid_strs:
        try:
            credentials = await ensure_account_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                remote_folder = await _resolve_remote_folder(receiver, body.folder)
                if hasattr(receiver, "mark_as_read_batch"):
                    marked = await receiver.mark_as_read_batch(uid_strs, folder=remote_folder)
                else:
                    marked = 0
                    for uid_str in uid_strs:
                        await receiver.mark_as_read(uid_str, folder=remote_folder)
                        marked += 1
                for uid_num in uid_nums:
                    await delete_pending_read_sync(account.id, uid_num, body.folder)
            finally:
                await _safe_disconnect(receiver)
        except Exception as exc:
            logger.warning("batch mark read queued: account=%s folder=%s error=%s", account.email, body.folder, exc)
            await _notify_if_permanent_token_error(exc, account, user_uid)
            if _is_outlook_connection_error(account, str(exc)):
                raise AppError(503, _OUTLOOK_RECONNECTING_MSG)
            for uid_num in uid_nums:
                await enqueue_pending_read_sync(account.id, user_uid, uid_num, body.folder, True, str(exc))

    updated = await batch_update_cached_messages_read(account.id, uid_nums, body.folder, True)
    delta = -min(updated, unread_before)
    if delta:
        await adjust_account_folder_unread(account.id, body.folder, delta)
        await _adjust_folder_unread_stats(account.id, body.folder, delta)
    await sync_service.notify_message_state_changed(account.id, "mark_read", uid_strs, folder=body.folder, user_uid=user_uid)
    return {"success": True, "marked": marked}


async def _mark_one_account_all_read(account: Account, folder: str, user_uid: str) -> dict:
    marked = 0
    uid_strs: list[str] = []
    try:
        if account.status != "offline":
            credentials = await ensure_account_token(account)
            receiver = ProviderFactory.get_receiver(account.provider)
            await receiver.connect(credentials)
            try:
                remote_folder = await _resolve_remote_folder(receiver, folder)
                uids = await receiver.fetch_unseen_uids(remote_folder)
                uid_strs = [str(uid) for uid in uids]
                marked = await receiver.mark_as_read_batch(uid_strs, remote_folder) if uid_strs else 0
            finally:
                await _safe_disconnect(receiver)
        updated = await mark_all_cached_messages_read(account.id, folder)
        stats = await get_folder_stats(account.id, folder)
        await upsert_folder_stats(account.id, folder, stats.get("total_count", 0), 0)
        await sync_service.notify_message_state_changed(
            account.id, "mark_read", uid_strs, folder=folder, user_uid=user_uid
        )
        return {"account_id": account.id, "email": account.email, "marked": max(marked, updated)}
    except Exception as exc:
        logger.error("mark all read failed: account=%s error_type=%s", account.email, type(exc).__name__)
        return {"account_id": account.id, "email": account.email, "marked": 0}


@router.post("/api/messages/mark-all-read", response_model=MarkAllReadResponse, summary="一键全部已读")
async def mark_all_read(request: Request, body: MarkAllReadRequest = Body(...)):
    user_uid = await get_uid(request)
    account_map = {account.id: account for account in await get_accounts(user_uid)}
    target_accounts = [account_map[account_id] for account_id in body.account_ids if account_id in account_map]
    if not target_accounts:
        raise AppError(404, "未找到指定账号")
    results = await asyncio.gather(
        *[_mark_one_account_all_read(account, body.folder, user_uid) for account in target_accounts]
    )
    return {
        "success": True,
        "results": results,
        "total_marked": sum(int(item.get("marked", 0) or 0) for item in results),
    }
