"""Bounded single-request Bootstrap query for FlyMail V2."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

import aiomysql
from pydantic import BaseModel, ConfigDict, Field

from flymail.application.auth import AuthenticatedSession
from flymail.domain.errors import NotFoundError
from flymail.infrastructure.db.pool import DatabasePool
from version import VERSION


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BootstrapUser(_ImmutableModel):
    id: str
    username: str
    role: Literal["admin", "user"]
    enabled: bool
    nickname: str
    avatar_object_sha256: str | None


class BootstrapAccount(_ImmutableModel):
    id: str
    provider_key: str
    email: str
    display_name: str
    remark: str
    group_name: str
    status: str
    include_in_unified: bool
    runtime_status: str
    idle_status: str
    icon_mode: str
    icon_value: str
    icon_object_sha256: str | None
    total_count: int = Field(ge=0)
    unread_count: int = Field(ge=0)


class NavigationMailbox(_ImmutableModel):
    id: str
    semantic_key: str
    native_key: str
    native_name: str
    total_count: int = Field(ge=0)
    unread_count: int = Field(ge=0)
    sync_status: str


class AccountNavigation(_ImmutableModel):
    account_id: str
    semantic_mailboxes: tuple[NavigationMailbox, ...]
    native_labels: tuple[NavigationMailbox, ...]


class UnifiedNavigation(_ImmutableModel):
    account_ids: tuple[str, ...]
    total_count: int = Field(ge=0)
    unread_count: int = Field(ge=0)


class BootstrapNavigation(_ImmutableModel):
    unified: UnifiedNavigation
    accounts: tuple[AccountNavigation, ...]


class BootstrapUiPreferences(_ImmutableModel):
    theme: Literal["system", "light", "dark"] = "system"
    density: Literal["comfortable", "compact"] = "comfortable"


class SyncAlertSummary(_ImmutableModel):
    auth_required_accounts: int = Field(ge=0)
    degraded_accounts: int = Field(ge=0)
    pending_accounts: int = Field(ge=0)
    unread_notifications: int = Field(ge=0)


class BootstrapResponse(_ImmutableModel):
    user: BootstrapUser
    permissions: tuple[str, ...]
    accounts: tuple[BootstrapAccount, ...]
    navigation: BootstrapNavigation
    ui_preferences: BootstrapUiPreferences
    sync_alert_summary: SyncAlertSummary
    csrf_token: str
    realtime_cursor: int = Field(ge=0)
    version: str


_SEMANTIC_ORDER = {
    "inbox": 0,
    "sent": 1,
    "drafts": 2,
    "archive": 3,
    "junk": 4,
    "trash": 5,
    "all_mail": 6,
    "important": 7,
    "custom": 8,
}

_BASE_PERMISSIONS = (
    "accounts.manage",
    "mail.read",
    "mail.send",
    "settings.manage",
)


def _decode_json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _permissions(role: str) -> tuple[str, ...]:
    if role == "admin":
        return (*_BASE_PERMISSIONS, "users.manage")
    return _BASE_PERMISSIONS


class BootstrapService:
    """Load first-screen state using a fixed four-query budget."""

    def __init__(self, pool: DatabasePool) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool

    async def load(self, session: AuthenticatedSession) -> BootstrapResponse:
        user_uid = session.user.id
        async with self.pool.acquire() as connection:
            profile_row = await self._profile_and_preferences(connection, user_uid)
            account_rows = await self._accounts(connection, user_uid)
            mailbox_rows = await self._mailboxes(connection, user_uid)
            summary_row = await self._cursor_and_notifications(connection, user_uid)

        ui_preferences = _decode_json_object(profile_row["ui_preferences"])
        accounts = tuple(self._map_account(row) for row in account_rows)
        account_ids = {account.id for account in accounts}
        mailbox_groups: dict[str, list[NavigationMailbox]] = defaultdict(list)
        label_groups: dict[str, list[NavigationMailbox]] = defaultdict(list)
        unified_total = 0
        unified_unread = 0
        active_ids = tuple(account.id for account in accounts if account.include_in_unified)
        active_id_set = set(active_ids)

        for row in mailbox_rows:
            account_id = str(row["account_id"])
            if account_id not in account_ids:
                continue
            item = NavigationMailbox(
                id=str(row["id"]),
                semantic_key=str(row["semantic_key"] or "custom"),
                native_key=str(row["native_key"]),
                native_name=str(row["native_name"]),
                total_count=max(int(row["total_count"] or 0), 0),
                unread_count=max(int(row["unread_count"] or 0), 0),
                sync_status=str(row["sync_status"] or "pending"),
            )
            if str(row["mailbox_type"]) == "label":
                label_groups[account_id].append(item)
            else:
                mailbox_groups[account_id].append(item)
                if account_id in active_id_set and item.semantic_key == "inbox":
                    unified_total += item.total_count
                    unified_unread += item.unread_count

        navigation_accounts = tuple(
            AccountNavigation(
                account_id=account.id,
                semantic_mailboxes=tuple(
                    sorted(
                        mailbox_groups.get(account.id, ()),
                        key=lambda item: (
                            _SEMANTIC_ORDER.get(item.semantic_key, 99),
                            item.native_name.casefold(),
                            item.id,
                        ),
                    )
                ),
                native_labels=tuple(
                    sorted(
                        label_groups.get(account.id, ()),
                        key=lambda item: (item.native_name.casefold(), item.id),
                    )
                ),
            )
            for account in accounts
        )

        auth_required = sum(
            account.status == "auth_required" or account.runtime_status == "auth_required"
            for account in accounts
        )
        degraded = sum(account.runtime_status == "degraded" for account in accounts)
        pending = sum(account.status == "pending" for account in accounts)

        theme = str(ui_preferences.get("theme") or "system")
        density = str(ui_preferences.get("density") or "comfortable")
        if theme not in {"system", "light", "dark"}:
            theme = "system"
        if density not in {"comfortable", "compact"}:
            density = "comfortable"

        return BootstrapResponse(
            user=BootstrapUser(
                id=session.user.id,
                username=session.user.username,
                role=session.user.role,
                enabled=session.user.enabled,
                nickname=str(profile_row["nickname"] or ""),
                avatar_object_sha256=(
                    str(profile_row["avatar_object_sha256"])
                    if profile_row["avatar_object_sha256"]
                    else None
                ),
            ),
            permissions=_permissions(session.user.role),
            accounts=accounts,
            navigation=BootstrapNavigation(
                unified=UnifiedNavigation(
                    account_ids=active_ids,
                    total_count=unified_total,
                    unread_count=unified_unread,
                ),
                accounts=navigation_accounts,
            ),
            ui_preferences=BootstrapUiPreferences(theme=theme, density=density),
            sync_alert_summary=SyncAlertSummary(
                auth_required_accounts=auth_required,
                degraded_accounts=degraded,
                pending_accounts=pending,
                unread_notifications=max(int(summary_row["unread_notifications"] or 0), 0),
            ),
            csrf_token=session.csrf_token,
            realtime_cursor=max(int(summary_row["realtime_cursor"] or 0), 0),
            version=VERSION,
        )

    @staticmethod
    def _map_account(row: dict) -> BootstrapAccount:
        return BootstrapAccount(
            id=str(row["id"]),
            provider_key=str(row["provider_key"]),
            email=str(row["email"]),
            display_name=str(row["display_name"] or ""),
            remark=str(row["remark"] or ""),
            group_name=str(row["group_name"] or ""),
            status=str(row["status"]),
            include_in_unified=(
                str(row["status"]) == "active"
                and str(row["runtime_status"] or "normal")
                not in {"disabled", "auth_required"}
            ),
            runtime_status=str(row["runtime_status"] or "normal"),
            idle_status=str(row["idle_status"] or "disconnected"),
            icon_mode=str(row["icon_mode"] or "provider"),
            icon_value=str(row["icon_value"] or ""),
            icon_object_sha256=(
                str(row["icon_object_sha256"])
                if row["icon_object_sha256"]
                else None
            ),
            total_count=max(int(row["total_count"] or 0), 0),
            unread_count=max(int(row["unread_count"] or 0), 0),
        )

    @staticmethod
    async def _profile_and_preferences(
        connection: aiomysql.Connection,
        user_uid: str,
    ) -> dict:
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT p.nickname, p.avatar_object_sha256, s.ui_preferences
                FROM users u
                LEFT JOIN user_profiles p ON p.user_uid = u.id
                LEFT JOIN user_settings s ON s.user_uid = u.id
                WHERE u.id = %s AND u.enabled = 1
                """,
                (user_uid,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("user profile not found")
        return dict(row)

    @staticmethod
    async def _accounts(
        connection: aiomysql.Connection,
        user_uid: str,
    ) -> list[dict]:
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT a.id, a.provider_key, a.email, a.display_name,
                       a.remark, a.group_name, a.status,
                       a.icon_mode, a.icon_value, a.icon_object_sha256,
                       COALESCE(r.status, 'normal') AS runtime_status,
                       COALESCE(r.idle_status, 'disconnected') AS idle_status,
                       COALESCE(t.total_count, 0) AS total_count,
                       COALESCE(t.unread_count, 0) AS unread_count
                FROM mail_accounts a
                LEFT JOIN account_runtime_state r
                  ON r.account_id = a.id AND r.user_uid = a.user_uid
                LEFT JOIN (
                    SELECT user_uid, account_id,
                           COALESCE(SUM(total_count), 0) AS total_count,
                           COALESCE(SUM(unread_count), 0) AS unread_count
                    FROM mailboxes
                    WHERE user_uid = %s AND mailbox_type = 'folder'
                    GROUP BY user_uid, account_id
                ) t ON t.account_id = a.id AND t.user_uid = a.user_uid
                WHERE a.user_uid = %s
                ORDER BY a.created_at ASC, a.id ASC
                """,
                (user_uid, user_uid),
            )
            return [dict(row) for row in await cursor.fetchall()]

    @staticmethod
    async def _mailboxes(
        connection: aiomysql.Connection,
        user_uid: str,
    ) -> list[dict]:
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, account_id, native_key, native_name,
                       semantic_key, mailbox_type, total_count,
                       unread_count, sync_status
                FROM mailboxes
                WHERE user_uid = %s
                ORDER BY account_id ASC, mailbox_type ASC,
                         semantic_key ASC, native_name ASC, id ASC
                """,
                (user_uid,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    @staticmethod
    async def _cursor_and_notifications(
        connection: aiomysql.Connection,
        user_uid: str,
    ) -> dict:
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT
                    COALESCE((
                        SELECT MAX(sequence_id)
                        FROM realtime_events
                        WHERE user_uid = %s
                    ), 0) AS realtime_cursor,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM notification_events
                        WHERE user_uid = %s
                          AND read_at IS NULL
                          AND dismissed_at IS NULL
                    ), 0) AS unread_notifications
                """,
                (user_uid, user_uid),
            )
            row = await cursor.fetchone()
        return dict(row or {"realtime_cursor": 0, "unread_notifications": 0})
