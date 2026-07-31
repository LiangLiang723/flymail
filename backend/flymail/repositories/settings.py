"""Tenant-scoped FlyMail V2 user settings repository."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import aiomysql

from flymail.repositories.base import TenantContext, fetch_one


MEBIBYTE = 1024**2
DEFAULT_BODY_CACHE_QUOTA_BYTES = 5 * 1024**3
DEFAULT_ATTACHMENT_CACHE_QUOTA_BYTES = 2048 * MEBIBYTE
MIN_ATTACHMENT_CACHE_QUOTA_BYTES = 100 * MEBIBYTE
DEFAULT_THEME = "system"
DEFAULT_DENSITY = "comfortable"
_ALLOWED_THEMES = {"system", "light", "dark"}
_ALLOWED_DENSITIES = {"comfortable", "compact"}


@dataclass(frozen=True, slots=True)
class UserSettings:
    user_uid: str
    body_cache_quota_bytes: int
    attachment_cache_quota_bytes: int
    theme: str
    density: str
    compose_preferences: dict
    remote_image_policy: dict
    created_at: float
    updated_at: float


def _decode_json(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _map_settings(row) -> UserSettings:
    ui_preferences = _decode_json(row["ui_preferences"])
    return UserSettings(
        user_uid=str(row["user_uid"]),
        body_cache_quota_bytes=int(row["body_cache_quota_bytes"] or 0),
        attachment_cache_quota_bytes=int(row["attachment_cache_quota_bytes"] or 0),
        theme=str(ui_preferences.get("theme") or DEFAULT_THEME),
        density=str(ui_preferences.get("density") or DEFAULT_DENSITY),
        compose_preferences=_decode_json(row["compose_preferences"]),
        remote_image_policy=_decode_json(row["remote_image_policy"]),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
    )


def validate_attachment_quota(value: int) -> int:
    normalized = int(value)
    if normalized < 0:
        raise ValueError("attachment quota must be non-negative")
    if normalized != 0 and normalized < MIN_ATTACHMENT_CACHE_QUOTA_BYTES:
        raise ValueError("attachment quota must be 0 or at least 100 MB")
    return normalized


class SettingsRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def create_defaults(self, tenant: TenantContext, *, now: float | None = None) -> UserSettings:
        timestamp = float(now if now is not None else time.time())
        ui_preferences = json.dumps(
            {"theme": DEFAULT_THEME, "density": DEFAULT_DENSITY},
            ensure_ascii=False,
            sort_keys=True,
        )
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO user_settings (
                    user_uid, body_cache_quota_bytes, attachment_cache_quota_bytes,
                    ui_preferences, compose_preferences, remote_image_policy,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant.user_uid,
                    DEFAULT_BODY_CACHE_QUOTA_BYTES,
                    DEFAULT_ATTACHMENT_CACHE_QUOTA_BYTES,
                    ui_preferences,
                    json.dumps({}, sort_keys=True),
                    json.dumps({}, sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )
        return await self.get_settings(tenant)

    async def get_settings(self, tenant: TenantContext) -> UserSettings:
        row = await fetch_one(
            self.connection,
            """
            SELECT user_uid, body_cache_quota_bytes, attachment_cache_quota_bytes,
                   ui_preferences, compose_preferences, remote_image_policy,
                   created_at, updated_at
            FROM user_settings
            WHERE user_uid = %s
            """,
            (tenant.user_uid,),
        )
        if row:
            return _map_settings(row)
        return UserSettings(
            user_uid=tenant.user_uid,
            body_cache_quota_bytes=DEFAULT_BODY_CACHE_QUOTA_BYTES,
            attachment_cache_quota_bytes=DEFAULT_ATTACHMENT_CACHE_QUOTA_BYTES,
            theme=DEFAULT_THEME,
            density=DEFAULT_DENSITY,
            compose_preferences={},
            remote_image_policy={},
            created_at=0,
            updated_at=0,
        )

    async def update_settings(
        self,
        tenant: TenantContext,
        *,
        body_cache_quota_bytes: int | None = None,
        attachment_cache_quota_bytes: int | None = None,
        theme: str | None = None,
        density: str | None = None,
    ) -> bool:
        current = await fetch_one(
            self.connection,
            """
            SELECT user_uid, body_cache_quota_bytes, attachment_cache_quota_bytes,
                   ui_preferences, created_at
            FROM user_settings
            WHERE user_uid = %s
            FOR UPDATE
            """,
            (tenant.user_uid,),
        )
        if current is None:
            return False

        body_quota = int(
            current["body_cache_quota_bytes"]
            if body_cache_quota_bytes is None
            else body_cache_quota_bytes
        )
        if body_quota < 0:
            raise ValueError("body quota must be non-negative")
        attachment_quota = validate_attachment_quota(
            int(
                current["attachment_cache_quota_bytes"]
                if attachment_cache_quota_bytes is None
                else attachment_cache_quota_bytes
            )
        )
        ui_preferences = _decode_json(current["ui_preferences"])
        next_theme = str(theme or ui_preferences.get("theme") or DEFAULT_THEME)
        next_density = str(density or ui_preferences.get("density") or DEFAULT_DENSITY)
        if next_theme not in _ALLOWED_THEMES:
            raise ValueError("unsupported theme")
        if next_density not in _ALLOWED_DENSITIES:
            raise ValueError("unsupported density")
        ui_preferences.update(theme=next_theme, density=next_density)

        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE user_settings
                SET body_cache_quota_bytes = %s,
                    attachment_cache_quota_bytes = %s,
                    ui_preferences = %s,
                    updated_at = %s
                WHERE user_uid = %s
                """,
                (
                    body_quota,
                    attachment_quota,
                    json.dumps(ui_preferences, ensure_ascii=False, sort_keys=True),
                    time.time(),
                    tenant.user_uid,
                ),
            )
        return True
