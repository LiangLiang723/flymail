"""Tenant-isolated thread persistence and transactional projection rebuilds."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

import aiomysql

from flymail.domain.ids import new_id
from flymail.domain.threading import normalize_address
from flymail.repositories.base import TenantContext, fetch_all


@dataclass(frozen=True, slots=True)
class ThreadSeed:
    canonical_thread_key: str
    normalized_subject: str
    updated_at: float


@dataclass(frozen=True, slots=True)
class ThreadRecord:
    id: str
    canonical_thread_key: str
    normalized_subject: str


@dataclass(frozen=True, slots=True)
class ThreadLink:
    thread_id: str
    message_id: str
    parent_message_id: str | None
    relation_source: str
    position_hint: int
    created_at: float


def _decode_addresses(value) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        normalized
        for raw in value
        if (normalized := normalize_address(str(raw)))
    )


class ThreadRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def upsert_threads(
        self,
        tenant: TenantContext,
        seeds: Iterable[ThreadSeed],
    ) -> dict[str, ThreadRecord]:
        unique: dict[str, ThreadSeed] = {}
        for seed in seeds:
            key = str(seed.canonical_thread_key or "").strip()
            if not key:
                raise ValueError("canonical_thread_key is required")
            unique[key] = seed
        if not unique:
            return {}

        keys = list(unique)
        placeholders = ",".join("%s" for _ in keys)
        existing_rows = await fetch_all(
            self.connection,
            f"""
            SELECT id, canonical_thread_key, normalized_subject
            FROM threads
            WHERE user_uid = %s AND canonical_thread_key IN ({placeholders})
            """,
            (tenant.user_uid, *keys),
        )
        existing = {
            str(row["canonical_thread_key"]): ThreadRecord(
                id=str(row["id"]),
                canonical_thread_key=str(row["canonical_thread_key"]),
                normalized_subject=str(row["normalized_subject"] or ""),
            )
            for row in existing_rows
        }

        updates = [unique[key] for key in keys if key in existing]
        inserts = [unique[key] for key in keys if key not in existing]
        async with self.connection.cursor() as cursor:
            if updates:
                await cursor.executemany(
                    """
                    UPDATE threads
                    SET normalized_subject = CASE
                            WHEN %s <> '' THEN %s ELSE normalized_subject END,
                        updated_at = %s
                    WHERE user_uid = %s AND canonical_thread_key = %s
                    """,
                    [
                        (
                            seed.normalized_subject,
                            seed.normalized_subject,
                            seed.updated_at,
                            tenant.user_uid,
                            seed.canonical_thread_key,
                        )
                        for seed in updates
                    ],
                )
            if inserts:
                insert_rows = []
                for seed in inserts:
                    thread_id = new_id("thr")
                    existing[seed.canonical_thread_key] = ThreadRecord(
                        id=thread_id,
                        canonical_thread_key=seed.canonical_thread_key,
                        normalized_subject=seed.normalized_subject,
                    )
                    insert_rows.append(
                        (
                            thread_id,
                            tenant.user_uid,
                            seed.canonical_thread_key,
                            seed.normalized_subject,
                            seed.updated_at,
                            seed.updated_at,
                        )
                    )
                await cursor.executemany(
                    """
                    INSERT INTO threads (
                        id, user_uid, canonical_thread_key, normalized_subject,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    insert_rows,
                )
        return existing

    async def find_fallback_thread(
        self,
        tenant: TenantContext,
        *,
        normalized_subject: str,
        participants: frozenset[str],
        received_at: float,
        window_seconds: int = 14 * 24 * 3600,
    ) -> ThreadRecord | None:
        subject = str(normalized_subject or "").strip()
        if not subject or not participants:
            return None
        window = int(window_seconds)
        if window < 1:
            raise ValueError("thread fallback window must be positive")
        rows = await fetch_all(
            self.connection,
            """
            SELECT t.id AS thread_id, t.canonical_thread_key,
                   t.normalized_subject, m.from_json, m.to_json, m.cc_json,
                   m.received_at
            FROM messages m FORCE INDEX (idx_messages_subject_fallback)
            JOIN threads t ON t.id = m.thread_id AND t.user_uid = m.user_uid
            WHERE m.user_uid = %s AND m.normalized_subject = %s
              AND m.thread_id IS NOT NULL
              AND m.received_at BETWEEN %s AND %s
            ORDER BY ABS(m.received_at - %s) ASC, m.received_at DESC, m.id DESC
            LIMIT 100
            """,
            (
                tenant.user_uid,
                subject,
                float(received_at) - window,
                float(received_at) + window,
                float(received_at),
            ),
        )
        for row in rows:
            candidate_participants = frozenset(
                (
                    *_decode_addresses(row["from_json"]),
                    *_decode_addresses(row["to_json"]),
                    *_decode_addresses(row["cc_json"]),
                )
            )
            if participants & candidate_participants:
                return ThreadRecord(
                    id=str(row["thread_id"]),
                    canonical_thread_key=str(row["canonical_thread_key"]),
                    normalized_subject=str(row["normalized_subject"] or ""),
                )
        return None

    async def link_messages(
        self,
        tenant: TenantContext,
        links: Iterable[ThreadLink],
    ) -> int:
        unique = {(link.thread_id, link.message_id): link for link in links}
        if not unique:
            return 0
        values = list(unique.values())
        target_threads: dict[str, str] = {}
        for link in values:
            if link.relation_source not in {"headers", "fallback"}:
                raise ValueError("unsupported thread relation source")
            existing_target = target_threads.get(link.message_id)
            if existing_target is not None and existing_target != link.thread_id:
                raise ValueError("one message cannot be linked to multiple target threads")
            target_threads[link.message_id] = link.thread_id
        thread_ids = sorted({link.thread_id for link in values})
        message_ids = sorted({link.message_id for link in values})
        thread_placeholders = ",".join("%s" for _ in thread_ids)
        message_placeholders = ",".join("%s" for _ in message_ids)
        async with self.connection.cursor() as cursor:
            await cursor.executemany(
                """
                DELETE FROM thread_messages
                WHERE user_uid = %s AND message_id = %s AND thread_id <> %s
                """,
                [
                    (tenant.user_uid, message_id, thread_id)
                    for message_id, thread_id in target_threads.items()
                ],
            )
        existing_rows = await fetch_all(
            self.connection,
            f"""
            SELECT thread_id, message_id
            FROM thread_messages
            WHERE user_uid = %s
              AND thread_id IN ({thread_placeholders})
              AND message_id IN ({message_placeholders})
            """,
            (tenant.user_uid, *thread_ids, *message_ids),
        )
        existing_pairs = {
            (str(row["thread_id"]), str(row["message_id"]))
            for row in existing_rows
        }
        updates = [
            link
            for link in values
            if (link.thread_id, link.message_id) in existing_pairs
        ]
        inserts = [
            link
            for link in values
            if (link.thread_id, link.message_id) not in existing_pairs
        ]
        async with self.connection.cursor() as cursor:
            if updates:
                await cursor.executemany(
                    """
                    UPDATE thread_messages
                    SET parent_message_id = %s, relation_source = %s,
                        position_hint = %s
                    WHERE thread_id = %s AND message_id = %s AND user_uid = %s
                    """,
                    [
                        (
                            link.parent_message_id,
                            link.relation_source,
                            link.position_hint,
                            link.thread_id,
                            link.message_id,
                            tenant.user_uid,
                        )
                        for link in updates
                    ],
                )
            if inserts:
                await cursor.executemany(
                    """
                    INSERT INTO thread_messages (
                        thread_id, message_id, user_uid, parent_message_id,
                        relation_source, position_hint, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            link.thread_id,
                            link.message_id,
                            tenant.user_uid,
                            link.parent_message_id,
                            link.relation_source,
                            link.position_hint,
                            link.created_at,
                        )
                        for link in inserts
                    ],
                )
        return len(values)

    async def refresh_projections(
        self,
        tenant: TenantContext,
        thread_ids: Iterable[str],
        *,
        now: float,
    ) -> int:
        ids = sorted({str(thread_id or "").strip() for thread_id in thread_ids if str(thread_id or "").strip()})
        if not ids:
            return 0
        placeholders = ",".join("%s" for _ in ids)
        rows = await fetch_all(
            self.connection,
            f"""
            SELECT tm.thread_id, mb.semantic_key, m.id AS message_id,
                   m.received_at, m.subject, m.snippet, m.from_json,
                   m.to_json, m.cc_json, m.has_attachments,
                   ri.account_id, ri.is_read, ri.is_starred
            FROM thread_messages tm
            JOIN messages m
              ON m.id = tm.message_id AND m.user_uid = tm.user_uid
            JOIN message_remote_instances ri
              ON ri.message_id = m.id AND ri.user_uid = m.user_uid
             AND ri.remote_deleted = 0
            JOIN message_memberships mm
              ON mm.remote_instance_id = ri.id AND mm.user_uid = m.user_uid
            JOIN mailboxes mb
              ON mb.id = mm.mailbox_id AND mb.user_uid = m.user_uid
            WHERE tm.user_uid = %s AND tm.thread_id IN ({placeholders})
            ORDER BY tm.thread_id, mb.semantic_key, m.received_at, m.id
            """,
            (tenant.user_uid, *ids),
        )
        pending_rows = await fetch_all(
            self.connection,
            f"""
            SELECT target_id, COUNT(*) AS pending_count
            FROM mail_operations
            WHERE user_uid = %s AND target_type = 'thread'
              AND target_id IN ({placeholders})
              AND status IN ('pending', 'applying', 'retry_wait', 'review_required', 'conflict')
            GROUP BY target_id
            """,
            (tenant.user_uid, *ids),
        )
        pending = {
            str(row["target_id"]): int(row["pending_count"] or 0)
            for row in pending_rows
        }
        previous_rows = await fetch_all(
            self.connection,
            f"""
            SELECT semantic_mailbox, thread_id, projection_version
            FROM thread_projections
            WHERE user_uid = %s AND thread_id IN ({placeholders})
            """,
            (tenant.user_uid, *ids),
        )
        previous_versions = {
            (str(row["thread_id"]), str(row["semantic_mailbox"])): int(row["projection_version"] or 0)
            for row in previous_rows
        }

        grouped: dict[tuple[str, str], dict[str, dict]] = {}
        for row in rows:
            group_key = (str(row["thread_id"]), str(row["semantic_key"] or "custom"))
            messages = grouped.setdefault(group_key, {})
            message_id = str(row["message_id"])
            entry = messages.setdefault(
                message_id,
                {
                    "received_at": float(row["received_at"] or 0),
                    "subject": str(row["subject"] or ""),
                    "snippet": str(row["snippet"] or ""),
                    "from": _decode_addresses(row["from_json"]),
                    "to": _decode_addresses(row["to_json"]),
                    "cc": _decode_addresses(row["cc_json"]),
                    "has_attachments": bool(row["has_attachments"]),
                    "all_read": True,
                    "is_starred": False,
                    "accounts": set(),
                },
            )
            entry["all_read"] = bool(entry["all_read"] and bool(row["is_read"]))
            entry["is_starred"] = bool(entry["is_starred"] or bool(row["is_starred"]))
            entry["accounts"].add(str(row["account_id"]))

        projection_rows = []
        for (thread_id, semantic), messages in grouped.items():
            message_values = list(messages.items())
            latest_id, latest = max(
                message_values,
                key=lambda item: (float(item[1]["received_at"]), item[0]),
            )
            participants: list[str] = []
            seen: set[str] = set()
            for value in (*latest["from"], *latest["to"], *latest["cc"]):
                if value not in seen:
                    seen.add(value)
                    participants.append(value)
            accounts = {
                account
                for _message_id, message in message_values
                for account in message["accounts"]
            }
            projection_rows.append(
                (
                    tenant.user_uid,
                    semantic,
                    thread_id,
                    latest_id,
                    latest["received_at"],
                    latest["subject"],
                    ", ".join(participants[:20]),
                    latest["snippet"],
                    len(message_values),
                    sum(1 for _message_id, message in message_values if not message["all_read"]),
                    1 if any(message["is_starred"] for _message_id, message in message_values) else 0,
                    1 if any(message["has_attachments"] for _message_id, message in message_values) else 0,
                    len(accounts),
                    pending.get(thread_id, 0),
                    previous_versions.get((thread_id, semantic), 0) + 1,
                    now,
                )
            )

        async with self.connection.cursor() as cursor:
            await cursor.execute(
                f"DELETE FROM thread_projections WHERE user_uid = %s AND thread_id IN ({placeholders})",
                (tenant.user_uid, *ids),
            )
            if projection_rows:
                await cursor.executemany(
                    """
                    INSERT INTO thread_projections (
                        user_uid, semantic_mailbox, thread_id, latest_message_id,
                        latest_message_at, subject, participants_summary,
                        latest_snippet, message_count, unread_count, is_starred,
                        has_attachments, account_count, pending_operation_count,
                        projection_version, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    projection_rows,
                )
        return len(projection_rows)

    async def remove_empty_threads(
        self,
        tenant: TenantContext,
        thread_ids: Iterable[str],
    ) -> int:
        ids = sorted(
            {
                str(thread_id or "").strip()
                for thread_id in thread_ids
                if str(thread_id or "").strip()
            }
        )
        if not ids:
            return 0
        placeholders = ",".join("%s" for _ in ids)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                f"""
                DELETE FROM threads
                WHERE user_uid = %s AND id IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1
                      FROM thread_messages tm
                      WHERE tm.user_uid = threads.user_uid
                        AND tm.thread_id = threads.id
                  )
                """,
                (tenant.user_uid, *ids),
            )
            return int(cursor.rowcount or 0)
