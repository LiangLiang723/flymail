"""设置管理路由

处理应用设置的查询与更新（Gmail/Outlook OAuth2 凭据等），
以及 OAuth 配置诊断接口。
"""
import asyncio
import json
import os
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from deps import get_uid
from db import (
    get_accounts,
    get_cached_count,
    get_folder_stats,
    get_history_sync_job,
    list_account_folder_counts,
    list_history_sync_jobs,
    get_user_settings,
    set_user_settings,
    update_account_credentials,
)
from services.account_presenter import account_icon_fields
from services.attachment_cache import (
    DEFAULT_ATTACHMENT_CACHE_LIMIT_MB,
    enforce_user_attachment_cache_limit,
    get_shared_attachment_cache_usage,
    get_user_attachment_cache_usage,
    validate_attachment_cache_limit_mb,
)
from services.history_sync import (
    is_full_history_sync_active,
    pause_history_sync,
    pause_folder_history_sync,
    refresh_history_sync_job,
    resume_history_sync,
    resume_folder_history_sync,
    retry_history_sync,
    start_clear_cache,
    start_folder_clear_cache,
    start_folder_history_sync,
    start_history_sync,
)

from providers.proxy import create_proxy_socket
from services.settings import async_load_settings, async_save_settings
from services.sync import sync_service
from utils.tasks import create_background_task
from schemas import (
    OAuthDiagnosticResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
    ProxyTestRequest,
    ProxyTestResponse,
    UnifiedSettingsRequest,
    UnifiedSettingsResponse,
)

router = APIRouter(tags=["设置"])

# 日志目录（与 main.py 保持一致，用于 OAuth 诊断输出）
LOG_DIR = os.environ.get(
    "FLYMAIL_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"),
)


# ==================== 辅助函数 ====================


def sync_gmail_config(settings: dict):
    """将设置同步到 Gmail Provider 的运行时配置"""
    from providers.gmail import config as gmail_config
    gmail_config.GMAIL_CLIENT_ID = settings.get("gmail_client_id", "")
    gmail_config.GMAIL_CLIENT_SECRET = settings.get("gmail_client_secret", "")
    # 只有 settings 中有 redirect_uri 时才更新（避免用空值覆盖）
    redirect_uri = settings.get("gmail_redirect_uri", "")
    if redirect_uri:
        gmail_config.GMAIL_REDIRECT_URI = redirect_uri


def sync_outlook_config(settings: dict):
    """将设置同步到 Microsoft/Outlook Provider 的运行时配置"""
    from providers.outlook import config as outlook_config
    outlook_config.OUTLOOK_CLIENT_ID = settings.get("outlook_client_id", "")
    outlook_config.OUTLOOK_CLIENT_SECRET = settings.get("outlook_client_secret", "")
    # 只有 settings 中有 redirect_uri 时才更新（避免用空值覆盖）
    redirect_uri = settings.get("outlook_redirect_uri", "")
    if redirect_uri:
        outlook_config.OUTLOOK_REDIRECT_URI = redirect_uri


async def apply_user_gmail_proxy(user_uid: str, enabled: bool, proxy_url: str) -> None:
    """将当前用户的代理设置写入其 Gmail 凭据并重载监听。"""
    normalized_url = (proxy_url or "").strip() if enabled else ""
    accounts = await get_accounts(user_uid)
    for account in accounts:
        if account.provider != "gmail":
            continue
        try:
            payload = json.loads(account.credentials_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        extra = dict(payload.get("extra") or {})
        extra["gmail_proxy_enabled"] = bool(enabled)
        extra["gmail_proxy_url"] = normalized_url
        payload["extra"] = extra
        new_json = json.dumps(payload, ensure_ascii=False)
        await update_account_credentials(account.id, new_json)
        account.credentials_json = new_json
        create_background_task(sync_service.add_account(account.id), name="reload_gmail_proxy")


def _proxy_failure_message(error: Exception) -> str:
    text = str(error or "").lower()
    if isinstance(error, TimeoutError) or "timeout" in text or "timed out" in text:
        return "连接超时，请检查代理地址、端口与网络"
    if isinstance(error, ConnectionRefusedError) or "refused" in text:
        return "无法连接代理服务，请确认代理已启动"
    if isinstance(error, OSError) and getattr(error, "errno", None) == -2:
        return "无法解析代理主机名"
    if isinstance(error, ConnectionError):
        return "代理无法建立到目标服务的 CONNECT 隧道"
    return "代理连接失败，请检查配置与网络"


def _test_proxy_to_google_sync(proxy_url: str) -> dict:
    """经用户填写的 HTTP 代理探测 Gmail IMAP 与 Google HTTPS。"""
    raw_url = (proxy_url or "").strip()
    parsed = urlparse(raw_url)
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        return {
            "success": False,
            "message": "代理地址格式无效，请使用 http://host:port",
            "latency_ms": 0,
            "target": "",
        }

    started = time.perf_counter()
    last_error: Exception | None = None
    for host, port in (("imap.gmail.com", 993), ("www.google.com", 443)):
        sock = None
        try:
            sock = create_proxy_socket(raw_url, host, port, timeout=12)
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {
                "success": True,
                "message": f"代理连通正常（经 {host}:{port}，{latency_ms}ms）",
                "latency_ms": latency_ms,
                "target": f"{host}:{port}",
            }
        except Exception as exc:
            last_error = exc
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "success": False,
        "message": f"代理无法连通 Google：{_proxy_failure_message(last_error or ConnectionError())}",
        "latency_ms": latency_ms,
        "target": "",
    }


# ==================== 设置接口 ==================


FOLDER_PROGRESS_ITEMS = [
    ("INBOX", "收件箱"),
    ("Sent", "已发送"),
    ("Drafts", "草稿箱"),
    ("Junk", "垃圾邮件"),
    ("Trash", "已删除"),
]

ACCOUNT_DISABLED_MESSAGE = "账户已禁用，请先在邮箱管理启用账户"


def _disabled_account_response() -> dict:
    return {"success": False, "message": ACCOUNT_DISABLED_MESSAGE, "code": "account_disabled"}


async def _build_folder_progress(account_id: str) -> list[dict]:
    items = []
    count_rows = await list_account_folder_counts(account_id)
    count_by_key = {item.get("folder_key"): item for item in count_rows}
    progress_folders = [
        (folder_key, label, count_by_key.get(folder_key.lower()))
        for folder_key, label in FOLDER_PROGRESS_ITEMS
    ]
    core_keys = {folder_key.lower() for folder_key, _label in FOLDER_PROGRESS_ITEMS}
    progress_folders.extend(
        (
            item.get("folder_path") or item.get("folder_key") or "",
            item.get("display_name") or item.get("folder_path") or item.get("folder_key") or "",
            item,
        )
        for item in count_rows
        if item.get("folder_key") not in core_keys
        and (item.get("folder_path") or item.get("folder_key"))
    )

    for folder_key, label, summary in progress_folders:
        folder_stats = await get_folder_stats(account_id, folder_key)
        cached_count = await get_cached_count(account_id, folder_key)
        synced_count = int((summary or {}).get("cached_count", 0) or 0)
        synced_count = max(synced_count, cached_count)
        sync_job = await get_history_sync_job(account_id, job_type=f"folder_sync:{folder_key}")
        clear_job = await get_history_sync_job(account_id, job_type=f"folder_clear:{folder_key}")
        total_count = max(
            int(folder_stats.get("total_count", 0) or 0),
            int((summary or {}).get("total_count", 0) or 0),
            synced_count,
            cached_count,
        )
        unread_count = max(
            int(folder_stats.get("unread_count", 0) or 0),
            int((summary or {}).get("unread_count", 0) or 0),
        )
        if folder_key == "Sent":
            unread_count = 0
        items.append(
            {
                "folder": folder_key,
                "label": label,
                "cached_count": synced_count,
                "summary_count": cached_count,
                "total_count": total_count,
                "unread_count": unread_count,
                "is_synced": total_count > 0 and synced_count >= total_count,
                "sync_job": sync_job,
                "clear_job": clear_job,
            }
        )
    return items

def _visible_history_job(job: dict | None, folder_progress: list[dict]) -> dict | None:
    if not job:
        return None
    visible = dict(job)
    if visible.get("status") == "completed" and not any(item["cached_count"] for item in folder_progress):
        visible.update(
            {
                "current_folder": "",
                "current_page": 1,
                "current_uid": 0,
                "total_folders": 0,
                "completed_folders": 0,
                "fetched_messages": 0,
                "downloaded_attachments": 0,
                "downloaded_inline_images": 0,
            }
        )
    return visible


def _latest_job_by_account(jobs: list[dict], job_type: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for job in jobs:
        if job.get("job_type") != job_type:
            continue
        if job["account_id"] not in result:
            result[job["account_id"]] = job
    return result


def _valid_cleanup_time(value: str) -> bool:
    try:
        hour_text, minute_text = str(value or "").split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except (TypeError, ValueError):
        return False


async def _find_user_account(request: Request, account_id: str):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    return next((item for item in accounts if item.id == account_id), None)


@router.get("/api/settings", response_model=SettingsResponse, summary="获取应用设置")
async def get_settings(request: Request):
    """获取当前保存的 Gmail/Outlook OAuth2 凭据等设置。

    client_secret 会脱敏处理，只显示首尾各4位，中间用星号替代。
    """
    settings = await async_load_settings()
    uid = await get_uid(request)
    user_settings = await get_user_settings(
        uid,
        ["gmail_proxy_enabled", "gmail_proxy_url", "attachment_cache_limit_mb"],
    )
    try:
        attachment_cache_limit_mb = validate_attachment_cache_limit_mb(
            int(user_settings.get("attachment_cache_limit_mb", DEFAULT_ATTACHMENT_CACHE_LIMIT_MB))
        )
    except (TypeError, ValueError):
        attachment_cache_limit_mb = DEFAULT_ATTACHMENT_CACHE_LIMIT_MB
    attachment_cache_usage_bytes, attachment_cache_shared_physical_bytes = await asyncio.gather(
        get_user_attachment_cache_usage(uid),
        get_shared_attachment_cache_usage(),
    )
    secret = settings.get("gmail_client_secret", "")
    if secret and len(secret) > 8:
        masked_secret = secret[:4] + "*" * (len(secret) - 8) + secret[-4:]
    else:
        masked_secret = secret
    outlook_secret = settings.get("outlook_client_secret", "")
    if outlook_secret and len(outlook_secret) > 8:
        masked_outlook_secret = outlook_secret[:4] + "*" * (len(outlook_secret) - 8) + outlook_secret[-4:]
    else:
        masked_outlook_secret = outlook_secret
    return {
        "gmail_client_id": settings.get("gmail_client_id", ""),
        "gmail_client_secret": masked_secret if secret else "",
        "gmail_redirect_uri": settings.get("gmail_redirect_uri", ""),
        "has_credentials": bool(settings.get("gmail_client_id")) and bool(settings.get("gmail_client_secret")),
        "gmail_proxy_enabled": bool(user_settings.get("gmail_proxy_enabled", False)),
        "gmail_proxy_url": str(user_settings.get("gmail_proxy_url", "") or ""),
        "outlook_client_id": settings.get("outlook_client_id", ""),
        "outlook_client_secret": masked_outlook_secret if outlook_secret else "",
        "outlook_redirect_uri": settings.get("outlook_redirect_uri", ""),
        "has_outlook_credentials": bool(settings.get("outlook_client_id")) and bool(settings.get("outlook_client_secret")),
        "uploads_cleanup_weekday": int(settings.get("uploads_cleanup_weekday", 0) or 0),
        "uploads_cleanup_time": settings.get("uploads_cleanup_time", "02:00"),
        "attachment_cache_limit_mb": attachment_cache_limit_mb,
        "attachment_cache_usage_bytes": attachment_cache_usage_bytes,
        "attachment_cache_shared_physical_bytes": attachment_cache_shared_physical_bytes,
    }


@router.put("/api/settings", response_model=SettingsUpdateResponse, summary="更新应用设置")
async def update_settings(request: Request, body: SettingsUpdateRequest):
    """更新 Gmail/Outlook OAuth2 凭据等设置。

    - client_secret 为空或包含星号（脱敏值）时不会覆盖已有密钥
    - 保存后自动同步到 Gmail/Outlook Provider 的运行时配置
    """
    # 转为 dict，过滤掉 None 字段（未传入的字段不覆盖）
    update_data = body.model_dump(exclude_none=True)

    uid = await get_uid(request)
    proxy_enabled_update = update_data.pop("gmail_proxy_enabled", None)
    proxy_url_update = update_data.pop("gmail_proxy_url", None)
    attachment_cache_limit_update = update_data.pop("attachment_cache_limit_mb", None)
    attachment_cache_cleanup = None

    # client_secret 为空或包含星号（脱敏值）时不会覆盖已有密钥
    secret_in_body = update_data.get("gmail_client_secret", "")
    if not secret_in_body or "*" in str(secret_in_body):
        update_data.pop("gmail_client_secret", None)

    outlook_secret_in_body = update_data.get("outlook_client_secret", "")
    if not outlook_secret_in_body or "*" in str(outlook_secret_in_body):
        update_data.pop("outlook_client_secret", None)

    if "uploads_cleanup_time" in update_data and not _valid_cleanup_time(update_data["uploads_cleanup_time"]):
        update_data["uploads_cleanup_time"] = "02:00"

    if proxy_enabled_update is not None or proxy_url_update is not None:
        existing_proxy = await get_user_settings(uid, ["gmail_proxy_enabled", "gmail_proxy_url"])
        proxy_enabled = bool(
            proxy_enabled_update
            if proxy_enabled_update is not None
            else existing_proxy.get("gmail_proxy_enabled", False)
        )
        proxy_url = str(
            proxy_url_update
            if proxy_url_update is not None
            else existing_proxy.get("gmail_proxy_url", "")
        ).strip()
        if proxy_enabled:
            parsed_proxy = urlparse(proxy_url)
            if parsed_proxy.scheme.lower() != "http" or not parsed_proxy.hostname:
                from errors import AppError
                raise AppError(400, "Gmail 代理地址必须使用 http://host:port 格式")
        await set_user_settings(uid, {
            "gmail_proxy_enabled": proxy_enabled,
            "gmail_proxy_url": proxy_url if proxy_enabled else "",
        })
        await apply_user_gmail_proxy(uid, proxy_enabled, proxy_url)

    if attachment_cache_limit_update is not None:
        attachment_cache_limit = validate_attachment_cache_limit_mb(attachment_cache_limit_update)
        await set_user_settings(uid, {
            "attachment_cache_limit_mb": attachment_cache_limit,
        })
        attachment_cache_cleanup = await enforce_user_attachment_cache_limit(
            uid,
            attachment_cache_limit,
        )

    saved = await async_save_settings(update_data)
    sync_gmail_config(saved)
    sync_outlook_config(saved)
    if "uploads_cleanup_weekday" in update_data or "uploads_cleanup_time" in update_data:
        from services.upload_cleanup import restart_upload_cleanup
        await restart_upload_cleanup()

    response = {"success": True, "message": "设置已保存"}
    if attachment_cache_cleanup is not None:
        response["attachment_cache_cleanup"] = attachment_cache_cleanup.as_dict()
    return response


@router.post(
    "/api/settings/proxy/test",
    response_model=ProxyTestResponse,
    summary="测试 Gmail HTTP 代理连通性",
)
async def test_gmail_proxy(request: Request, body: ProxyTestRequest):
    await get_uid(request)
    return await asyncio.to_thread(_test_proxy_to_google_sync, body.proxy_url)


@router.get("/api/settings/unified", response_model=UnifiedSettingsResponse, summary="获取聚合收件箱设置")
async def get_unified_settings(request: Request):
    from db import get_user_settings

    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    settings = await get_user_settings(uid, ["unified_account_ids"])
    selected = settings.get("unified_account_ids", [])
    if not isinstance(selected, list):
        selected = []
    valid_ids = {account.id for account in accounts}
    selected = [account_id for account_id in selected if account_id in valid_ids]
    return {
        "account_ids": selected,
        "accounts": [
            {
                "id": account.id,
                "email": account.email,
                "provider": account.provider,
                "selected": account.id in selected,
                **account_icon_fields(account),
            }
            for account in accounts
        ],
    }


@router.put("/api/settings/unified", summary="保存聚合收件箱设置")
async def save_unified_settings(request: Request, body: UnifiedSettingsRequest):
    from db import set_user_settings

    uid = await get_uid(request)
    valid_ids = {account.id for account in await get_accounts(uid)}
    invalid_ids = [account_id for account_id in body.account_ids if account_id not in valid_ids]
    if invalid_ids:
        from errors import AppError
        raise AppError(400, "聚合账号列表包含无权访问的账号")
    deduplicated = list(dict.fromkeys(body.account_ids))
    await set_user_settings(uid, {"unified_account_ids": deduplicated})
    return {"success": True}


@router.get("/api/settings/oauth-diagnostic", response_model=OAuthDiagnosticResponse, summary="OAuth 诊断")
async def oauth_diagnostic():
    """诊断 Gmail OAuth2 配置状态，帮助排查授权问题。

    返回运行时的 OAuth 配置（脱敏），以及持久化存储的配置状态对比。
    不暴露完整密钥。
    """
    settings = await async_load_settings()
    from providers.gmail import config as gmail_config

    runtime_client_id = gmail_config.GMAIL_CLIENT_ID
    runtime_client_secret = gmail_config.GMAIL_CLIENT_SECRET
    runtime_redirect_uri = gmail_config.GMAIL_REDIRECT_URI

    stored_client_id = settings.get("gmail_client_id", "")
    stored_client_secret = settings.get("gmail_client_secret", "")
    stored_redirect_uri = settings.get("gmail_redirect_uri", "")

    issues = []
    if not runtime_client_id:
        issues.append("运行时 client_id 为空 - 请在设置页面配置客户端 ID")
    if not runtime_client_secret:
        issues.append("运行时 client_secret 为空 - 请在设置页面配置客户端密钥并保存")
    if not stored_client_id:
        issues.append("settings.json 中 client_id 为空")
    if not stored_client_secret:
        issues.append("settings.json 中 client_secret 为空 - 密钥可能未保存成功")
    if not runtime_redirect_uri:
        issues.append("运行时 redirect_uri 为空 - 请先在设置页面保存设置（系统会自动生成回调地址）")
    if runtime_redirect_uri and runtime_redirect_uri.startswith("http://localhost"):
        issues.append(f"运行时 redirect_uri 为 localhost 默认值({runtime_redirect_uri})，在飞牛环境中不正确")

    return {
        "status": "有问题" if issues else "正常",
        "issues": issues,
        "runtime": {
            "client_id": (runtime_client_id[:10] + "..." + runtime_client_id[-6:]) if runtime_client_id and len(runtime_client_id) > 16 else (runtime_client_id or "空"),
            "client_secret": ("已配置(" + str(len(runtime_client_secret)) + "字符)") if runtime_client_secret else "空",
            "redirect_uri": runtime_redirect_uri or "空",
        },
        "stored": {
            "client_id": (stored_client_id[:10] + "..." + stored_client_id[-6:]) if stored_client_id and len(stored_client_id) > 16 else (stored_client_id or "空"),
            "client_secret": ("已配置(" + str(len(stored_client_secret)) + "字符)") if stored_client_secret else "空",
            "redirect_uri": stored_redirect_uri or "空",
        },
        "log_dir": LOG_DIR,
        "tip": "如果 client_secret 显示为空，请在设置页面重新输入密钥并点击保存",
    }


@router.get("/api/history-sync/jobs", summary="获取历史邮件同步任务")
async def get_history_sync_jobs(request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    jobs = await list_history_sync_jobs(uid)
    history_by_account = _latest_job_by_account(jobs, "history_sync")
    clear_by_account = _latest_job_by_account(jobs, "clear_cache")

    items = []
    for account in accounts:
        folder_progress = await _build_folder_progress(account.id)
        history_job = _visible_history_job(history_by_account.get(account.id), folder_progress)
        clear_job = clear_by_account.get(account.id)
        items.append(
            {
                "account_id": account.id,
                "email": account.email,
                "remark": account.remark,
                "provider": account.provider,
                "account_status": account.status,
                "status": history_job.get("status", "idle") if history_job else "idle",
                "job": history_job,
                "clear_job": clear_job,
                "folder_progress": folder_progress,
            }
        )
    return {"jobs": items}


@router.get("/api/history-sync/jobs/{account_id}", summary="查询单个历史邮件同步任务")
async def get_history_sync_job_detail(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"job": None, "clear_job": None}
    folder_progress = await _build_folder_progress(account.id)
    job = _visible_history_job(await get_history_sync_job(account_id, job_type="history_sync"), folder_progress)
    clear_job = await get_history_sync_job(account_id, job_type="clear_cache")
    return {
        "job": job,
        "clear_job": clear_job,
        "account": {
            "id": account.id,
            "email": account.email,
            "remark": account.remark,
            "provider": account.provider,
        },
        "folder_progress": folder_progress,
    }


@router.post("/api/history-sync/jobs/{account_id}/refresh", summary="refresh_history_sync_status")
async def refresh_history_sync_job_status(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    folder_progress = await _build_folder_progress(account.id)
    job = _visible_history_job(await refresh_history_sync_job(account_id), folder_progress)
    clear_job = await get_history_sync_job(account_id, job_type="clear_cache")
    return {
        "success": True,
        "job": job,
        "clear_job": clear_job,
        "status": job.get("status", "idle") if job else "idle",
        "folder_progress": folder_progress,
    }


@router.post("/api/history-sync/jobs/{account_id}/start", summary="重置历史邮件同步")
async def start_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    started = await start_history_sync(account_id, reset=True)
    return {"success": started}


@router.post("/api/history-sync/jobs/{account_id}/pause", summary="暂停历史邮件同步")
async def pause_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    if not any(item.id == account_id for item in accounts):
        return {"success": False, "message": "account_not_found"}
    paused = await pause_history_sync(account_id)
    return {"success": paused}


@router.post("/api/history-sync/jobs/{account_id}/resume", summary="继续历史邮件同步")
async def resume_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    resumed = await resume_history_sync(account_id)
    return {"success": resumed}


@router.post("/api/history-sync/jobs/{account_id}/retry", summary="重试历史邮件同步")
async def retry_history_sync_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    account = next((item for item in accounts if item.id == account_id), None)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    retried = await retry_history_sync(account_id)
    return {"success": retried}


@router.post("/api/history-sync/jobs/{account_id}/clear", summary="清空历史邮件本地缓存")
async def clear_history_sync_cache_job(account_id: str, request: Request):
    uid = await get_uid(request)
    accounts = await get_accounts(uid)
    if not any(item.id == account_id for item in accounts):
        return {"success": False, "message": "account_not_found"}
    started = await start_clear_cache(account_id)
    return {"success": started}


@router.post("/api/history-sync/jobs/{account_id}/folders/{folder}/start", summary="启动单文件夹历史同步")
async def start_folder_history_sync_job(account_id: str, folder: str, request: Request):
    account = await _find_user_account(request, account_id)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    started, message = await start_folder_history_sync(account_id, folder, reset=True)
    return {"success": started, "message": message}


@router.post("/api/history-sync/jobs/{account_id}/folders/{folder}/pause", summary="暂停单文件夹历史同步")
async def pause_folder_history_sync_job(account_id: str, folder: str, request: Request):
    account = await _find_user_account(request, account_id)
    if not account:
        return {"success": False, "message": "account_not_found"}
    paused, message = await pause_folder_history_sync(account_id, folder)
    return {"success": paused, "message": message}


@router.post("/api/history-sync/jobs/{account_id}/folders/{folder}/resume", summary="继续单文件夹历史同步")
async def resume_folder_history_sync_job(account_id: str, folder: str, request: Request):
    account = await _find_user_account(request, account_id)
    if not account:
        return {"success": False, "message": "account_not_found"}
    if account.status == "offline":
        return _disabled_account_response()
    resumed, message = await resume_folder_history_sync(account_id, folder)
    return {"success": resumed, "message": message}


@router.post("/api/history-sync/jobs/{account_id}/folders/{folder}/clear", summary="清空单文件夹本地缓存")
async def clear_folder_history_sync_cache_job(account_id: str, folder: str, request: Request):
    account = await _find_user_account(request, account_id)
    if not account:
        return {"success": False, "message": "account_not_found"}
    started, message = await start_folder_clear_cache(account_id, folder)
    return {"success": started, "message": message}
