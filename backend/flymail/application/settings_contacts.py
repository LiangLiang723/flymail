"""Tenant settings, contacts, and administrator history-sync orchestration."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import aiomysql
from pymysql.err import IntegrityError

from flymail.api.schemas.settings_contacts import (
    ComposePreferences,
    ContactResponse,
    HistorySyncItem,
    RemoteImagePolicy,
    SettingsResponse,
    SyncCursorResponse,
    UiPreferences,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.realtime import RealtimeService
from flymail.domain.errors import ConflictError, NotFoundError
from flymail.domain.ids import new_id
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.audit import AuditRepository
from flymail.repositories.base import TenantContext, normalize_email
from flymail.repositories.jobs import JobRepository, JobSpec
from flymail.repositories.objects import BODY_CACHE_REFERENCE_KINDS, ObjectRepository


@dataclass(frozen=True, slots=True)
class HistorySyncAction:
    job_id: str
    status: str


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _json_array(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item or "").strip())
    if value in (None, ""):
        return ()
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(item) for item in decoded if str(item or "").strip())


class SettingsContactsService:
    def __init__(
        self,
        pool: DatabasePool,
        realtime: RealtimeService,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(realtime, RealtimeService):
            raise TypeError("realtime must be RealtimeService")
        self.pool = pool
        self.realtime = realtime
        self.now_fn = now_fn

    @staticmethod
    def _settings(
        value: dict[str, Any],
        *,
        body_usage_bytes: int,
        attachment_usage_bytes: int,
        cleanup_task_id: str | None = None,
    ) -> SettingsResponse:
        ui = _json_object(value.get("ui_preferences"))
        compose = _json_object(value.get("compose_preferences"))
        remote = _json_object(value.get("remote_image_policy"))
        return SettingsResponse(
            body_cache_quota_bytes=max(
                int(value.get("body_cache_quota_bytes") or 0), 0
            ),
            attachment_cache_quota_bytes=max(
                int(value.get("attachment_cache_quota_bytes") or 0), 0
            ),
            ui_preferences=UiPreferences.model_validate(ui or {}),
            compose_preferences=ComposePreferences.model_validate(compose or {}),
            remote_image_policy=RemoteImagePolicy.model_validate(remote or {}),
            body_cache_usage_bytes=max(int(body_usage_bytes), 0),
            attachment_cache_usage_bytes=max(int(attachment_usage_bytes), 0),
            cleanup_task_id=cleanup_task_id,
            updated_at=float(value.get("updated_at") or 0),
        )

    async def _settings_row(
        self,
        connection: aiomysql.Connection,
        tenant: TenantContext,
        *,
        for_update: bool = False,
    ) -> dict[str, Any]:
        suffix = " FOR UPDATE" if for_update else ""
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                f"""
                SELECT body_cache_quota_bytes, attachment_cache_quota_bytes,
                       ui_preferences, compose_preferences,
                       remote_image_policy, updated_at
                FROM user_settings
                WHERE user_uid = %s
                {suffix}
                """,
                (tenant.user_uid,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("user settings were not found")
        return dict(row)

    async def get_settings(self, session: AuthenticatedSession) -> SettingsResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            value = await self._settings_row(connection, tenant)
            objects = ObjectRepository(connection)
            body_usage = await objects.get_user_usage_for_reference_kinds(
                tenant.user_uid,
                BODY_CACHE_REFERENCE_KINDS,
            )
            attachment_usage = await objects.get_user_usage_for_reference_kinds(
                tenant.user_uid,
                ("message_attachment",),
            )
        return self._settings(
            value,
            body_usage_bytes=body_usage,
            attachment_usage_bytes=attachment_usage,
        )

    async def update_settings(
        self,
        session: AuthenticatedSession,
        *,
        body_cache_quota_bytes: int | None,
        attachment_cache_quota_bytes: int | None,
        ui_preferences: UiPreferences | None,
        compose_preferences: ComposePreferences | None,
        remote_image_policy: RemoteImagePolicy | None,
        request_id: str,
    ) -> SettingsResponse:
        tenant = TenantContext(session.user.id)
        timestamp = float(self.now_fn())
        cleanup_task_id: str | None = None
        body_usage = 0
        attachment_usage = 0
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                current = await self._settings_row(
                    connection,
                    tenant,
                    for_update=True,
                )
                selected_body_quota = (
                    int(body_cache_quota_bytes)
                    if body_cache_quota_bytes is not None
                    else int(current["body_cache_quota_bytes"] or 0)
                )
                selected_attachment_quota = (
                    int(attachment_cache_quota_bytes)
                    if attachment_cache_quota_bytes is not None
                    else int(current["attachment_cache_quota_bytes"] or 0)
                )
                selected_ui = (
                    ui_preferences.model_dump(mode="json")
                    if ui_preferences is not None
                    else _json_object(current["ui_preferences"])
                )
                selected_compose = (
                    compose_preferences.model_dump(mode="json")
                    if compose_preferences is not None
                    else _json_object(current["compose_preferences"])
                )
                selected_remote = (
                    remote_image_policy.model_dump(mode="json")
                    if remote_image_policy is not None
                    else _json_object(current["remote_image_policy"])
                )
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE user_settings
                        SET body_cache_quota_bytes = %s,
                            attachment_cache_quota_bytes = %s,
                            ui_preferences = %s,
                            compose_preferences = %s,
                            remote_image_policy = %s,
                            updated_at = %s
                        WHERE user_uid = %s
                        """,
                        (
                            selected_body_quota,
                            selected_attachment_quota,
                            json.dumps(selected_ui, ensure_ascii=False, sort_keys=True),
                            json.dumps(selected_compose, ensure_ascii=False, sort_keys=True),
                            json.dumps(selected_remote, ensure_ascii=False, sort_keys=True),
                            timestamp,
                            tenant.user_uid,
                        ),
                    )
                current_body_quota = int(current["body_cache_quota_bytes"] or 0)
                current_attachment_quota = int(current["attachment_cache_quota_bytes"] or 0)
                quota_lowered = (
                    selected_body_quota < current_body_quota
                    or selected_attachment_quota < current_attachment_quota
                )
                if quota_lowered:
                    cleanup_task_id = await JobRepository(connection).enqueue(
                        JobSpec(
                            queue_name="maintenance",
                            job_kind="cache.cleanup",
                            user_uid=tenant.user_uid,
                            payload={
                                "user_uid": tenant.user_uid,
                                "body_cache_quota_bytes": selected_body_quota,
                                "attachment_cache_quota_bytes": selected_attachment_quota,
                            },
                            priority=200,
                            available_at=timestamp,
                            max_attempts=5,
                            dedupe_key=f"cache-cleanup:{tenant.user_uid}",
                        ),
                        now=timestamp,
                    )
                current = await self._settings_row(connection, tenant)
                objects = ObjectRepository(connection)
                body_usage = await objects.get_user_usage_for_reference_kinds(
                    tenant.user_uid,
                    BODY_CACHE_REFERENCE_KINDS,
                )
                attachment_usage = await objects.get_user_usage_for_reference_kinds(
                    tenant.user_uid,
                    ("message_attachment",),
                )
                await AuditRepository(connection).append(
                    event_type="settings.update",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="user_settings",
                    resource_id=tenant.user_uid,
                    safe_metadata={"scopes": ["preferences", "quotas"]},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self.realtime.publish(
            tenant,
            event_type="settings.updated",
            aggregate_type="settings",
            aggregate_id=tenant.user_uid,
            payload={"settings_scope": "user"},
        )
        return self._settings(
            current,
            body_usage_bytes=body_usage,
            attachment_usage_bytes=attachment_usage,
            cleanup_task_id=cleanup_task_id,
        )

    @staticmethod
    def _contact(row: dict[str, Any]) -> ContactResponse:
        return ContactResponse(
            id=str(row["id"]),
            display_name=str(row["display_name"] or ""),
            primary_email=str(row["primary_email"]),
            emails=_json_array(row["emails_json"]),
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
        )

    async def create_contact(
        self,
        session: AuthenticatedSession,
        *,
        display_name: str,
        primary_email: str,
        emails: tuple[str, ...],
        request_id: str,
    ) -> ContactResponse:
        tenant = TenantContext(session.user.id)
        display_email = str(primary_email or "").strip()
        normalized = normalize_email(display_email)
        normalized_emails = list(emails)
        if normalized not in {normalize_email(value) for value in normalized_emails}:
            normalized_emails.insert(0, display_email)
        contact_id = new_id("contact")
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO contacts (
                            id, user_uid, display_name, normalized_name,
                            primary_email, normalized_email, emails_json,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            contact_id,
                            tenant.user_uid,
                            str(display_name or "").strip(),
                            str(display_name or "").strip().casefold(),
                            display_email,
                            normalized,
                            json.dumps(normalized_emails, ensure_ascii=False),
                            timestamp,
                            timestamp,
                        ),
                    )
                await AuditRepository(connection).append(
                    event_type="contact.create",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="contact",
                    resource_id=contact_id,
                    safe_metadata={},
                    now=timestamp,
                )
                await connection.commit()
            except IntegrityError as exc:
                await connection.rollback()
                raise ConflictError("contact email already exists") from exc
            except Exception:
                await connection.rollback()
                raise
        return await self.get_contact(session, contact_id)

    async def quick_add_from_message(
        self,
        session: AuthenticatedSession,
        message_id: str,
        *,
        request_id: str,
    ) -> ContactResponse:
        tenant = TenantContext(session.user.id)
        normalized_message = str(message_id or "").strip()
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT from_json FROM messages WHERE id = %s AND user_uid = %s",
                    (normalized_message, tenant.user_uid),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("message was not found")
        raw = row["from_json"]
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = []
        else:
            decoded = raw
        sender = decoded[0] if isinstance(decoded, list) and decoded else None
        if not isinstance(sender, dict):
            raise NotFoundError("message sender was not available")
        address = str(sender.get("address") or "").strip()
        name = str(sender.get("name") or "").strip()
        normalized = normalize_email(address)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, display_name, primary_email, emails_json,
                           created_at, updated_at
                    FROM contacts
                    WHERE user_uid = %s AND normalized_email = %s
                    LIMIT 1
                    """,
                    (tenant.user_uid, normalized),
                )
                existing = await cursor.fetchone()
        if existing is not None:
            return self._contact(dict(existing))
        return await self.create_contact(
            session,
            display_name=name,
            primary_email=address,
            emails=(address,),
            request_id=request_id,
        )

    async def get_contact(
        self,
        session: AuthenticatedSession,
        contact_id: str,
    ) -> ContactResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, display_name, primary_email, emails_json,
                           created_at, updated_at
                    FROM contacts
                    WHERE id = %s AND user_uid = %s
                    """,
                    (str(contact_id or "").strip(), tenant.user_uid),
                )
                row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("contact was not found")
        return self._contact(dict(row))

    async def list_contacts(
        self,
        session: AuthenticatedSession,
        *,
        query: str,
        limit: int,
    ) -> tuple[ContactResponse, ...]:
        tenant = TenantContext(session.user.id)
        normalized = str(query or "").strip().casefold()
        pattern = f"%{normalized}%"
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT id, display_name, primary_email, emails_json,
                           created_at, updated_at
                    FROM contacts
                    WHERE user_uid = %s
                      AND (%s = '' OR normalized_name LIKE %s OR normalized_email LIKE %s)
                    ORDER BY normalized_name, normalized_email, id
                    LIMIT %s
                    """,
                    (
                        tenant.user_uid,
                        normalized,
                        pattern,
                        pattern,
                        min(max(int(limit), 1), 100),
                    ),
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(self._contact(row) for row in rows)

    async def update_contact(
        self,
        session: AuthenticatedSession,
        contact_id: str,
        *,
        display_name: str | None,
        primary_email: str | None,
        emails: tuple[str, ...] | None,
        request_id: str,
    ) -> ContactResponse:
        current = await self.get_contact(session, contact_id)
        tenant = TenantContext(session.user.id)
        selected_name = current.display_name if display_name is None else str(display_name).strip()
        selected_primary = current.primary_email if primary_email is None else str(primary_email).strip()
        normalized = normalize_email(selected_primary)
        selected_emails = list(current.emails if emails is None else emails)
        if normalized not in {normalize_email(value) for value in selected_emails}:
            selected_emails.insert(0, selected_primary)
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE contacts
                        SET display_name = %s, normalized_name = %s,
                            primary_email = %s, normalized_email = %s,
                            emails_json = %s, updated_at = %s
                        WHERE id = %s AND user_uid = %s
                        """,
                        (
                            selected_name,
                            selected_name.casefold(),
                            selected_primary,
                            normalized,
                            json.dumps(selected_emails, ensure_ascii=False),
                            timestamp,
                            current.id,
                            tenant.user_uid,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("contact was not found")
                await AuditRepository(connection).append(
                    event_type="contact.update",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="contact",
                    resource_id=current.id,
                    safe_metadata={},
                    now=timestamp,
                )
                await connection.commit()
            except IntegrityError as exc:
                await connection.rollback()
                raise ConflictError("contact email already exists") from exc
            except Exception:
                await connection.rollback()
                raise
        return await self.get_contact(session, current.id)

    async def delete_contact(
        self,
        session: AuthenticatedSession,
        contact_id: str,
        *,
        request_id: str,
    ) -> None:
        tenant = TenantContext(session.user.id)
        normalized_id = str(contact_id or "").strip()
        timestamp = float(self.now_fn())
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM contacts WHERE id = %s AND user_uid = %s",
                        (normalized_id, tenant.user_uid),
                    )
                    if cursor.rowcount != 1:
                        raise NotFoundError("contact was not found")
                await AuditRepository(connection).append(
                    event_type="contact.delete",
                    result_code="success",
                    request_id=request_id,
                    user_uid=tenant.user_uid,
                    actor_user_uid=tenant.user_uid,
                    resource_type="contact",
                    resource_id=normalized_id,
                    safe_metadata={},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise


class AdminHistorySyncService:
    def __init__(
        self,
        pool: DatabasePool,
        realtime: RealtimeService,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        self.realtime = realtime
        self.now_fn = now_fn

    @staticmethod
    def _public_status(status: str, error_class: str) -> str:
        if status == "cancelled" and error_class == "AdminPaused":
            return "paused"
        return status

    @staticmethod
    def _item(row: dict[str, Any]) -> HistorySyncItem:
        cursor = None
        if row.get("cursor_phase"):
            cursor = SyncCursorResponse(
                phase=str(row["cursor_phase"]),
                cursor_type=str(row["cursor_type"] or "json"),
                cursor=_json_object(row["cursor_json"]),
                last_uid=max(int(row["last_uid"] or 0), 0),
                highest_modseq=max(int(row["highest_modseq"] or 0), 0),
                updated_at=float(row["cursor_updated_at"] or 0),
            )
        return HistorySyncItem(
            job_id=str(row["id"]),
            user_uid=str(row["user_uid"]),
            account_id=str(row["account_id"]) if row["account_id"] else None,
            account_email=str(row["account_email"] or ""),
            provider_key=str(row["provider_key"]) if row["provider_key"] else None,
            status=AdminHistorySyncService._public_status(
                str(row["status"]), str(row["last_error_class"] or "")
            ),
            queue_name=str(row["queue_name"]),
            attempt_count=max(int(row["attempt_count"] or 0), 0),
            max_attempts=max(int(row["max_attempts"] or 1), 1),
            available_at=float(row["available_at"] or 0),
            last_error_class=str(row["last_error_class"] or ""),
            last_error_message=str(row["last_error_message"] or ""),
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
            cursor=cursor,
        )

    async def list_jobs(self) -> tuple[HistorySyncItem, ...]:
        async with self.pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT j.id, j.user_uid, j.account_id, j.provider_key,
                           j.status, j.queue_name, j.attempt_count, j.max_attempts,
                           j.available_at, j.last_error_class, j.last_error_message,
                           j.created_at, j.updated_at,
                           COALESCE(a.email, '') AS account_email,
                           c.phase AS cursor_phase, c.cursor_type, c.cursor_json,
                           c.last_uid, c.highest_modseq,
                           c.updated_at AS cursor_updated_at
                    FROM worker_jobs j
                    LEFT JOIN mail_accounts a
                      ON a.id = j.account_id AND a.user_uid = j.user_uid
                    LEFT JOIN sync_cursors c
                      ON c.user_uid = j.user_uid
                     AND c.account_id = j.account_id
                     AND c.mailbox_id = ''
                     AND c.phase = 'history'
                    WHERE j.job_kind = 'sync.initial'
                    ORDER BY j.updated_at DESC, j.id DESC
                    LIMIT 500
                    """
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        return tuple(self._item(row) for row in rows)

    async def _transition(
        self,
        session: AuthenticatedSession,
        job_id: str,
        *,
        action: str,
        request_id: str,
    ) -> HistorySyncAction:
        normalized_id = str(job_id or "").strip()
        timestamp = float(self.now_fn())
        target_status = "cancelled" if action == "pause" else "pending"
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(
                        """
                        SELECT id, user_uid, status, last_error_class
                        FROM worker_jobs
                        WHERE id = %s AND job_kind = 'sync.initial'
                        FOR UPDATE
                        """,
                        (normalized_id,),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise NotFoundError("history sync job was not found")
                    status = str(row["status"])
                    error_class = str(row["last_error_class"] or "")
                    if action == "pause":
                        if status not in {"pending", "retry_wait", "failed"}:
                            raise ConflictError("history sync job cannot be paused")
                        await cursor.execute(
                            """
                            UPDATE worker_jobs
                            SET status = 'cancelled',
                                last_error_class = 'AdminPaused',
                                last_error_message = '',
                                lease_owner = '', lease_token = NULL,
                                lease_expires_at = NULL, heartbeat_at = NULL,
                                finished_at = %s, updated_at = %s
                            WHERE id = %s
                            """,
                            (timestamp, timestamp, normalized_id),
                        )
                    elif action == "resume":
                        if not (status == "cancelled" and error_class == "AdminPaused"):
                            raise ConflictError("history sync job is not paused")
                        await cursor.execute(
                            """
                            UPDATE worker_jobs
                            SET status = 'pending', available_at = %s,
                                last_error_class = '', last_error_message = '',
                                lease_owner = '', lease_token = NULL,
                                lease_expires_at = NULL, heartbeat_at = NULL,
                                finished_at = NULL, updated_at = %s
                            WHERE id = %s
                            """,
                            (timestamp, timestamp, normalized_id),
                        )
                    elif action == "retry":
                        if status not in {"failed", "cancelled"}:
                            raise ConflictError("history sync job is not retryable")
                        await cursor.execute(
                            """
                            UPDATE worker_jobs
                            SET status = 'pending', available_at = %s,
                                last_error_class = '', last_error_message = '',
                                lease_owner = '', lease_token = NULL,
                                lease_expires_at = NULL, heartbeat_at = NULL,
                                finished_at = NULL, updated_at = %s
                            WHERE id = %s
                            """,
                            (timestamp, timestamp, normalized_id),
                        )
                    else:
                        raise ValueError("unsupported history sync action")
                await AuditRepository(connection).append(
                    event_type=f"admin.history_sync.{action}",
                    result_code="success",
                    request_id=request_id,
                    user_uid=str(row["user_uid"]),
                    actor_user_uid=session.user.id,
                    resource_type="worker_job",
                    resource_id=normalized_id,
                    safe_metadata={"action": action},
                    now=timestamp,
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        await self.realtime.publish(
            TenantContext(str(row["user_uid"])),
            event_type="sync.updated",
            aggregate_type="worker_job",
            aggregate_id=normalized_id,
            payload={"job_id": normalized_id, "status": self._public_status(target_status, "AdminPaused" if action == "pause" else "")},
        )
        return HistorySyncAction(
            job_id=normalized_id,
            status="paused" if action == "pause" else "pending",
        )

    async def pause(self, session: AuthenticatedSession, job_id: str, request_id: str) -> HistorySyncAction:
        return await self._transition(session, job_id, action="pause", request_id=request_id)

    async def resume(self, session: AuthenticatedSession, job_id: str, request_id: str) -> HistorySyncAction:
        return await self._transition(session, job_id, action="resume", request_id=request_id)

    async def retry(self, session: AuthenticatedSession, job_id: str, request_id: str) -> HistorySyncAction:
        return await self._transition(session, job_id, action="retry", request_id=request_id)
