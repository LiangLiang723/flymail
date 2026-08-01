"""Batch message, header, remote-instance, and membership persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

import aiomysql

from flymail.domain.errors import ConflictError, NotFoundError
from flymail.domain.ids import new_id
from flymail.domain.threading import normalize_message_id
from flymail.repositories.base import TenantContext, fetch_all, fetch_one


@dataclass(frozen=True, slots=True)
class MessageUpsert:
    canonical_message_key: str
    message_id_header: str
    thread_id: str
    subject: str
    normalized_subject: str
    from_addresses: tuple[str, ...]
    to_addresses: tuple[str, ...]
    cc_addresses: tuple[str, ...]
    sent_at: float
    received_at: float
    size_bytes: int
    has_attachments: bool
    snippet: str


@dataclass(frozen=True, slots=True)
class HeaderUpsert:
    canonical_message_key: str
    in_reply_to: str
    references: tuple[str, ...]
    parsed_at: float


@dataclass(frozen=True, slots=True)
class RemoteInstanceUpsert:
    canonical_message_key: str
    account_id: str
    mailbox_id: str
    uidvalidity: int
    remote_uid: int
    provider_message_id: str
    provider_thread_id: str
    flags: tuple[str, ...]
    is_read: bool
    is_starred: bool
    remote_version: str
    seen_at: float


@dataclass(frozen=True, slots=True)
class MembershipUpsert:
    remote_instance_id: str
    mailbox_id: str
    membership_kind: str
    provider_label: str
    updated_at: float


def _json_array(values: Iterable[str]) -> str:
    return json.dumps(tuple(str(value) for value in values), ensure_ascii=False)


def _unique_by_key(records: Iterable, key_name: str) -> list:
    ordered: dict[object, object] = {}
    for record in records:
        ordered[getattr(record, key_name)] = record
    return list(ordered.values())


class MessageRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def upsert_messages(
        self,
        tenant: TenantContext,
        records: Iterable[MessageUpsert],
        *,
        now: float,
    ) -> dict[str, str]:
        unique = _unique_by_key(records, "canonical_message_key")
        if not unique:
            return {}
        keys = [record.canonical_message_key for record in unique]
        existing = await self._message_ids_by_keys(tenant, keys)

        updates = [record for record in unique if record.canonical_message_key in existing]
        inserts = [record for record in unique if record.canonical_message_key not in existing]
        async with self.connection.cursor() as cursor:
            if updates:
                await cursor.executemany(
                    """
                    UPDATE messages
                    SET message_id_header = %s, thread_id = %s, subject = %s,
                        normalized_subject = %s, from_json = %s, to_json = %s,
                        cc_json = %s, sent_at = %s, received_at = %s,
                        size_bytes = %s, has_attachments = %s, snippet = %s,
                        updated_at = %s
                    WHERE user_uid = %s AND canonical_message_key = %s
                    """,
                    [
                        (
                            record.message_id_header,
                            record.thread_id,
                            record.subject,
                            record.normalized_subject,
                            _json_array(record.from_addresses),
                            _json_array(record.to_addresses),
                            _json_array(record.cc_addresses),
                            record.sent_at,
                            record.received_at,
                            record.size_bytes,
                            1 if record.has_attachments else 0,
                            record.snippet,
                            now,
                            tenant.user_uid,
                            record.canonical_message_key,
                        )
                        for record in updates
                    ],
                )
            if inserts:
                insert_rows = []
                for record in inserts:
                    message_id = new_id("msg")
                    existing[record.canonical_message_key] = message_id
                    insert_rows.append(
                        (
                            message_id,
                            tenant.user_uid,
                            record.canonical_message_key,
                            record.message_id_header,
                            record.thread_id,
                            record.subject,
                            record.normalized_subject,
                            _json_array(record.from_addresses),
                            _json_array(record.to_addresses),
                            _json_array(record.cc_addresses),
                            record.sent_at,
                            record.received_at,
                            record.size_bytes,
                            1 if record.has_attachments else 0,
                            record.snippet,
                            now,
                            now,
                        )
                    )
                await cursor.executemany(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, message_id_header,
                        thread_id, subject, normalized_subject, from_json,
                        to_json, cc_json, bcc_json, reply_to_json, sent_at,
                        received_at, size_bytes, has_attachments, snippet,
                        body_state, search_state, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              NULL, NULL, %s, %s, %s, %s, %s,
                              'not_requested', 'metadata', %s, %s)
                    """,
                    insert_rows,
                )
        return existing

    async def upsert_headers(
        self,
        tenant: TenantContext,
        records: Iterable[HeaderUpsert],
        message_ids: dict[str, str],
    ) -> int:
        unique = _unique_by_key(records, "canonical_message_key")
        if not unique:
            return 0
        rows = [
            (
                message_ids[record.canonical_message_key],
                tenant.user_uid,
                record.in_reply_to,
                _json_array(record.references),
                record.parsed_at,
            )
            for record in unique
        ]
        message_id_values = [row[0] for row in rows]
        placeholders = ",".join("%s" for _ in message_id_values)
        existing_rows = await fetch_all(
            self.connection,
            f"""
            SELECT message_id
            FROM message_headers
            WHERE user_uid = %s AND message_id IN ({placeholders})
            """,
            (tenant.user_uid, *message_id_values),
        )
        existing_ids = {str(row["message_id"]) for row in existing_rows}
        updates = [row for row in rows if row[0] in existing_ids]
        inserts = [row for row in rows if row[0] not in existing_ids]
        async with self.connection.cursor() as cursor:
            if updates:
                await cursor.executemany(
                    """
                    UPDATE message_headers
                    SET in_reply_to = %s, references_json = %s,
                        parser_version = 1, parsed_at = %s
                    WHERE message_id = %s AND user_uid = %s
                    """,
                    [
                        (row[2], row[3], row[4], row[0], row[1])
                        for row in updates
                    ],
                )
            if inserts:
                await cursor.executemany(
                    """
                    INSERT INTO message_headers (
                        message_id, user_uid, in_reply_to, references_json,
                        list_id, raw_header_object_sha256, parser_version, parsed_at
                    ) VALUES (%s, %s, %s, %s, '', NULL, 1, %s)
                    """,
                    inserts,
                )
        return len(rows)

    async def upsert_remote_instances(
        self,
        tenant: TenantContext,
        records: Iterable[RemoteInstanceUpsert],
        message_ids: dict[str, str],
        *,
        now: float,
    ) -> dict[tuple[str, str, int, int], str]:
        deduplicated: dict[tuple[str, str, int, int], RemoteInstanceUpsert] = {}
        for record in records:
            identity = (
                record.account_id,
                record.mailbox_id,
                int(record.uidvalidity),
                int(record.remote_uid),
            )
            deduplicated[identity] = record
        unique = list(deduplicated.values())
        if not unique:
            return {}

        scopes = {(record.account_id, record.mailbox_id) for record in unique}
        if len(scopes) != 1:
            raise ValueError("remote instance batch must use a single account and mailbox")
        account_id, mailbox_id = next(iter(scopes))
        uidvalidities = sorted({int(record.uidvalidity) for record in unique})
        remote_uids = sorted({int(record.remote_uid) for record in unique})
        uidvalidity_placeholders = ",".join("%s" for _ in uidvalidities)
        uid_placeholders = ",".join("%s" for _ in remote_uids)
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT id, account_id, mailbox_id, uidvalidity, remote_uid
            FROM message_remote_instances
            WHERE user_uid = %s AND account_id = %s AND mailbox_id = %s
              AND uidvalidity IN ({uidvalidity_placeholders})
              AND remote_uid IN ({uid_placeholders})
            """,
            (
                tenant.user_uid,
                account_id,
                mailbox_id,
                *uidvalidities,
                *remote_uids,
            ),
        )
        existing = {
            (
                str(row["account_id"]),
                str(row["mailbox_id"]),
                int(row["uidvalidity"]),
                int(row["remote_uid"]),
            ): str(row["id"])
            for row in rows
        }
        updates = []
        inserts = []
        for record in unique:
            identity = (
                record.account_id,
                record.mailbox_id,
                int(record.uidvalidity),
                int(record.remote_uid),
            )
            common = (
                message_ids[record.canonical_message_key],
                record.provider_message_id,
                record.provider_thread_id,
                _json_array(record.flags),
                1 if record.is_read else 0,
                1 if record.is_starred else 0,
                record.remote_version,
                record.seen_at,
                now,
            )
            if identity in existing:
                updates.append((*common, existing[identity], tenant.user_uid))
            else:
                remote_id = new_id("rmi")
                existing[identity] = remote_id
                inserts.append(
                    (
                        remote_id,
                        tenant.user_uid,
                        record.account_id,
                        record.mailbox_id,
                        message_ids[record.canonical_message_key],
                        record.uidvalidity,
                        record.remote_uid,
                        record.provider_message_id,
                        record.provider_thread_id,
                        _json_array(record.flags),
                        1 if record.is_read else 0,
                        1 if record.is_starred else 0,
                        record.remote_version,
                        record.seen_at,
                        now,
                        now,
                    )
                )
        async with self.connection.cursor() as cursor:
            if updates:
                await cursor.executemany(
                    """
                    UPDATE message_remote_instances
                    SET message_id = %s, provider_message_id = %s,
                        provider_thread_id = %s, flags_json = %s,
                        is_read = %s, is_starred = %s, remote_version = %s,
                        remote_deleted = 0, last_seen_at = %s, updated_at = %s
                    WHERE id = %s AND user_uid = %s
                    """,
                    updates,
                )
            if inserts:
                await cursor.executemany(
                    """
                    INSERT INTO message_remote_instances (
                        id, user_uid, account_id, mailbox_id, message_id,
                        uidvalidity, remote_uid, provider_message_id,
                        provider_thread_id, flags_json, is_read, is_starred,
                        remote_version, remote_deleted, last_seen_at,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, 0, %s, %s, %s)
                    """,
                    inserts,
                )
        return existing

    async def upsert_memberships(
        self,
        tenant: TenantContext,
        records: Iterable[MembershipUpsert],
    ) -> int:
        unique = {
            (record.remote_instance_id, record.mailbox_id): record
            for record in records
        }
        if not unique:
            return 0
        values = list(unique.values())
        remote_ids = sorted({record.remote_instance_id for record in values})
        mailbox_ids = sorted({record.mailbox_id for record in values})
        remote_placeholders = ",".join("%s" for _ in remote_ids)
        mailbox_placeholders = ",".join("%s" for _ in mailbox_ids)
        existing_rows = await fetch_all(
            self.connection,
            f"""
            SELECT remote_instance_id, mailbox_id
            FROM message_memberships
            WHERE user_uid = %s
              AND remote_instance_id IN ({remote_placeholders})
              AND mailbox_id IN ({mailbox_placeholders})
            """,
            (tenant.user_uid, *remote_ids, *mailbox_ids),
        )
        existing_pairs = {
            (str(row["remote_instance_id"]), str(row["mailbox_id"]))
            for row in existing_rows
        }
        updates = [
            record
            for record in values
            if (record.remote_instance_id, record.mailbox_id) in existing_pairs
        ]
        inserts = [
            record
            for record in values
            if (record.remote_instance_id, record.mailbox_id) not in existing_pairs
        ]
        async with self.connection.cursor() as cursor:
            if updates:
                await cursor.executemany(
                    """
                    UPDATE message_memberships
                    SET membership_kind = %s, provider_label = %s, updated_at = %s
                    WHERE remote_instance_id = %s AND mailbox_id = %s AND user_uid = %s
                    """,
                    [
                        (
                            record.membership_kind,
                            record.provider_label,
                            record.updated_at,
                            record.remote_instance_id,
                            record.mailbox_id,
                            tenant.user_uid,
                        )
                        for record in updates
                    ],
                )
            if inserts:
                await cursor.executemany(
                    """
                    INSERT INTO message_memberships (
                        remote_instance_id, mailbox_id, user_uid,
                        membership_kind, provider_label, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            record.remote_instance_id,
                            record.mailbox_id,
                            tenant.user_uid,
                            record.membership_kind,
                            record.provider_label,
                            record.updated_at,
                            record.updated_at,
                        )
                        for record in inserts
                    ],
                )
        return len(values)

    async def message_threads_by_keys(
        self,
        tenant: TenantContext,
        canonical_message_keys: Iterable[str],
    ) -> dict[str, str]:
        keys = sorted(
            {
                str(key or "").strip()
                for key in canonical_message_keys
                if str(key or "").strip()
            }
        )
        if not keys:
            return {}
        placeholders = ",".join("%s" for _ in keys)
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT canonical_message_key, thread_id
            FROM messages
            WHERE user_uid = %s
              AND canonical_message_key IN ({placeholders})
              AND thread_id IS NOT NULL
            """,
            (tenant.user_uid, *keys),
        )
        return {
            str(row["canonical_message_key"]): str(row["thread_id"])
            for row in rows
        }

    async def message_ids_by_header(
        self,
        tenant: TenantContext,
        message_id_headers: Iterable[str],
    ) -> dict[str, tuple[str, str | None]]:
        normalized = sorted(
            {
                value
                for raw in message_id_headers
                if (value := normalize_message_id(raw))
            }
        )
        if not normalized:
            return {}
        placeholders = ",".join("%s" for _ in normalized)
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT id, message_id_header, thread_id
            FROM messages
            WHERE user_uid = %s AND message_id_header IN ({placeholders})
            """,
            (tenant.user_uid, *normalized),
        )
        return {
            normalize_message_id(str(row["message_id_header"])): (
                str(row["id"]),
                str(row["thread_id"]) if row["thread_id"] else None,
            )
            for row in rows
        }

    async def transition_body_state(
        self,
        tenant: TenantContext,
        message_id: str,
        target_state: str,
        *,
        now: float,
    ) -> bool:
        allowed = {
            "not_requested": {"queued"},
            "queued": {"fetching", "failed"},
            "fetching": {"ready", "failed", "unavailable"},
            "ready": {"evicted"},
            "evicted": {"queued"},
            "failed": {"queued"},
            "unavailable": set(),
        }
        normalized_message = str(message_id or "").strip()
        normalized_target = str(target_state or "").strip().casefold()
        if not normalized_message:
            raise ValueError("message_id is required")
        if normalized_target not in allowed:
            raise ValueError("unsupported body state")
        row = await fetch_one(
            self.connection,
            """
            SELECT state
            FROM message_bodies
            WHERE user_uid = %s AND message_id = %s
            FOR UPDATE
            """,
            (tenant.user_uid, normalized_message),
        )
        if row is None:
            raise NotFoundError("message body state was not found")
        current = str(row["state"])
        if current == normalized_target:
            return False
        if normalized_target not in allowed[current]:
            raise ConflictError(
                f"body state cannot transition from {current} to {normalized_target}"
            )
        search_state = {
            "queued": "queued",
            "fetching": "queued",
            "ready": "ready",
            "evicted": "evicted",
            "failed": "failed",
            "unavailable": "failed",
        }.get(normalized_target, "metadata")
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                UPDATE message_bodies
                SET state = %s, updated_at = %s,
                    last_error_class = CASE WHEN %s IN ('queued','fetching','ready') THEN '' ELSE last_error_class END,
                    last_error_message = CASE WHEN %s IN ('queued','fetching','ready') THEN '' ELSE last_error_message END
                WHERE user_uid = %s AND message_id = %s AND state = %s
                """,
                (
                    normalized_target,
                    float(now),
                    normalized_target,
                    normalized_target,
                    tenant.user_uid,
                    normalized_message,
                    current,
                ),
            )
            if cursor.rowcount != 1:
                raise ConflictError("body state changed concurrently")
            await cursor.execute(
                """
                UPDATE messages
                SET body_state = %s, search_state = %s, updated_at = %s
                WHERE user_uid = %s AND id = %s
                """,
                (
                    normalized_target,
                    search_state,
                    float(now),
                    tenant.user_uid,
                    normalized_message,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("message was not found")
        return True

    async def _message_ids_by_keys(
        self,
        tenant: TenantContext,
        keys: list[str],
    ) -> dict[str, str]:
        if not keys:
            return {}
        placeholders = ",".join("%s" for _ in keys)
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT id, canonical_message_key
            FROM messages
            WHERE user_uid = %s AND canonical_message_key IN ({placeholders})
            """,
            (tenant.user_uid, *keys),
        )
        return {
            str(row["canonical_message_key"]): str(row["id"])
            for row in rows
        }
