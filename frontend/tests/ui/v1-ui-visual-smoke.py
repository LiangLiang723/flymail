from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, Route, sync_playwright

BASE_URL = "http://127.0.0.1:4173"
ARTIFACTS = Path(__file__).resolve().parents[2] / "test-results" / "v1-ui"

BOOTSTRAP: dict[str, Any] = {
    "user": {
        "id": "user-1",
        "username": "admin",
        "role": "admin",
        "enabled": True,
        "display_name": "亮亮",
        "nickname": "亮亮",
        "avatar_url": None,
    },
    "permissions": ["admin"],
    "accounts": [
        {
            "id": "account-1",
            "provider_key": "imap",
            "email": "liangliang@example.com",
            "display_name": "工作邮箱",
            "remark": "工作邮箱",
            "group_name": "默认",
            "status": "active",
            "include_in_unified": True,
            "runtime_status": "idle",
            "idle_status": "connected",
            "icon_mode": "preset",
            "icon_value": "mail",
            "icon_object_sha256": None,
            "total_count": 42,
            "unread_count": 3,
        }
    ],
    "navigation": {
        "unified": {"account_ids": ["account-1"], "total_count": 42, "unread_count": 3},
        "accounts": [
            {
                "account_id": "account-1",
                "semantic_mailboxes": [
                    {
                        "id": "inbox",
                        "semantic_key": "inbox",
                        "native_key": "INBOX",
                        "native_name": "收件箱",
                        "total_count": 20,
                        "unread_count": 3,
                        "sync_status": "ready",
                    },
                    {
                        "id": "sent",
                        "semantic_key": "sent",
                        "native_key": "Sent",
                        "native_name": "已发送",
                        "total_count": 12,
                        "unread_count": 0,
                        "sync_status": "ready",
                    },
                ],
                "native_labels": [],
            }
        ],
    },
    "ui_preferences": {
        "theme": "light",
        "density": "comfortable",
        "expanded_account_ids": ["account-1"],
    },
    "sync_alert_summary": {
        "auth_required_accounts": 0,
        "degraded_accounts": 0,
        "pending_accounts": 0,
        "unread_notifications": 2,
    },
    "csrf_token": "visual-smoke-csrf",
    "realtime_cursor": 0,
    "version": "0.1.4",
}

THREADS = {
    "items": [
        {
            "id": "thread-1",
            "subject": "FlyMail V1 UI 恢复完成",
            "snippet": "保留 V2 功能，恢复熟悉的侧栏、邮件列表与卡片层级。",
            "participants": [{"name": "FlyMail", "address": "team@example.com"}],
            "latest_at": 1785744000,
            "unread_count": 1,
            "message_count": 2,
            "is_starred": True,
            "has_attachments": False,
        },
        {
            "id": "thread-2",
            "subject": "同步任务已完成",
            "snippet": "最近邮件与历史邮件均已同步。",
            "participants": [{"name": "系统通知", "address": "notify@example.com"}],
            "latest_at": 1785657600,
            "unread_count": 0,
            "message_count": 1,
            "is_starred": False,
            "has_attachments": True,
        },
    ],
    "next_cursor": None,
}

SETTINGS = {
    "ui_preferences": BOOTSTRAP["ui_preferences"],
    "body_cache_quota_bytes": 5 * 1024**3,
    "attachment_cache_quota_bytes": 2 * 1024**3,
    "body_cache_usage_bytes": 734003200,
    "attachment_cache_usage_bytes": 209715200,
    "cleanup_task_id": None,
    "remote_image_policy": {"default": "block"},
    "compose_preferences": {"autosave_seconds": 10},
}


def fulfill_json(route: Route, status: int, payload: dict[str, Any]) -> None:
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False),
    )


def install_api_mock(context: BrowserContext, *, authenticated: bool) -> None:
    def handler(route: Route) -> None:
        url = route.request.url
        if "/api/v2/bootstrap" in url:
            if authenticated:
                fulfill_json(route, 200, BOOTSTRAP)
            else:
                fulfill_json(
                    route,
                    401,
                    {"error": {"code": "authentication_required", "message": "请先登录"}},
                )
            return
        if "/api/v2/threads" in url:
            fulfill_json(route, 200, THREADS)
            return
        if "/api/v2/settings" in url:
            fulfill_json(route, 200, SETTINGS)
            return
        if "/api/v2/events" in url:
            fulfill_json(route, 200, {"items": [], "next_cursor": 0})
            return
        if "/api/v2/sync" in url:
            fulfill_json(route, 200, {"items": []})
            return
        fulfill_json(route, 200, {"items": []})

    context.route("**/api/v2/**", handler)


def assert_no_horizontal_overflow(page: Page, label: str) -> None:
    overflow = page.evaluate(
        """() => ({
            document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            body: document.body.scrollWidth - document.body.clientWidth,
        })"""
    )
    assert overflow["document"] <= 1 and overflow["body"] <= 1, f"{label} horizontal overflow: {overflow}"


def collect_console_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    return errors


def run() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        login_context = browser.new_context(viewport={"width": 1440, "height": 900})
        install_api_mock(login_context, authenticated=False)
        login = login_context.new_page()
        login_errors = collect_console_errors(login)
        login.goto(BASE_URL, wait_until="networkidle")
        login.get_by_role("heading", name="欢迎回来").wait_for()
        assert login.locator(".brand-logo").is_visible()
        assert_no_horizontal_overflow(login, "login")
        login.screenshot(path=str(ARTIFACTS / "login-desktop.png"), full_page=True)
        unexpected_login_errors = [error for error in login_errors if "401 (Unauthorized)" not in error]
        assert not unexpected_login_errors, f"login console errors: {unexpected_login_errors}"
        login_context.close()

        desktop_context = browser.new_context(viewport={"width": 1440, "height": 900})
        install_api_mock(desktop_context, authenticated=True)
        desktop = desktop_context.new_page()
        desktop_errors = collect_console_errors(desktop)
        desktop.goto(f"{BASE_URL}/mail/semantic/inbox", wait_until="networkidle")
        desktop.locator(".app-sidebar").wait_for()
        desktop.locator(".v2-layout--desktop").wait_for()
        assert desktop.locator('[data-region="navigation"]').is_visible()
        assert desktop.locator('[data-region="thread-list"]').is_visible()
        assert desktop.locator('[data-region="thread-detail"]').is_visible()
        sidebar_width = desktop.locator(".app-sidebar").evaluate("element => element.getBoundingClientRect().width")
        assert 240 <= sidebar_width <= 256, f"unexpected desktop sidebar width: {sidebar_width}"
        assert_no_horizontal_overflow(desktop, "desktop mail")
        desktop.screenshot(path=str(ARTIFACTS / "mail-desktop.png"), full_page=True)

        desktop.goto(f"{BASE_URL}/settings", wait_until="networkidle")
        desktop.get_by_role("heading", name="设置", exact=True).wait_for()
        assert desktop.locator(".v2-layout").count() == 0, "settings must not render inside mail panes"
        assert desktop.locator(".v2-settings-links").is_visible()
        assert_no_horizontal_overflow(desktop, "desktop settings")
        desktop.screenshot(path=str(ARTIFACTS / "settings-desktop.png"), full_page=True)
        assert not desktop_errors, f"desktop console errors: {desktop_errors}"
        desktop_context.close()

        mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        install_api_mock(mobile_context, authenticated=True)
        mobile = mobile_context.new_page()
        mobile_errors = collect_console_errors(mobile)
        mobile.goto(f"{BASE_URL}/mail/semantic/inbox", wait_until="networkidle")
        mobile.locator(".mobile-sidebar-launcher").wait_for()
        assert_no_horizontal_overflow(mobile, "mobile mail")
        mobile.screenshot(path=str(ARTIFACTS / "mail-mobile.png"), full_page=True)
        mobile.locator(".mobile-sidebar-launcher").click()
        mobile.locator(".app-sidebar.is-mobile-open").wait_for()
        assert mobile.get_by_text("V2 邮件工作台").is_visible()
        assert_no_horizontal_overflow(mobile, "mobile drawer")
        mobile.screenshot(path=str(ARTIFACTS / "sidebar-mobile.png"), full_page=True)
        assert not mobile_errors, f"mobile console errors: {mobile_errors}"
        mobile_context.close()

        browser.close()

    print(f"visual smoke passed; screenshots: {ARTIFACTS}")


if __name__ == "__main__":
    run()
