"""Tenant-isolated mailbox and native-label persistence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import aiomysql

from flymail.domain.ids import new_id
from flymail.repositories.base import TenantContext, fetch_one


_MAILBOX_TYPES = {"folder", "label"}
_SEMANTIC_KEYS = {
    "inbox",
    "sent",
    "drafts",
    "trash",
    "junk",
    "archive",
    "all_mail",
    "important",
    "custom",
}


@dataclass(frozen=True, slots=True)
class Mailbox:
    id: str
    user_uid: str
    account_id: str
    native_key: str
    native_name: str
    semantic_key: str
    mailbox_type: str
    delimiter_value: str
    attributes: tuple[str, ...]
    uidvalidity: int
    highest_modseq: int
    total_count: int
    unread_count: int
    sync_status: str
    created_at: float
    updated_at: float


def _decode_attributes(value) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item or ""))


def _map_mailbox(row) -> Mailbox:
    return Mailbox(
        id=str(row["id"]),
        user_uid=str(row["user_uid"]),
        account_id=str(row["account_id"]),
        native_key=str(row["native_key"]),
        native_name=str(row["native_name"]),
        semantic_key=str(row["semantic_key"]),
        mailbox_type=str(row["mailbox_type"]),
        delimiter_value=str(row["delimiter_value"] or ""),
        attributes=_decode_attributes(row["attributes_json"]),
        uidvalidity=int(row["uidvalidity"] or 0),
        highest_modseq=int(row["highest_modseq"] or 0),
        total_count=int(row["total_count"] or 0),
        unread_count=int(row["unread_count"] or 0),
        sync_status=str(row["sync_status"] or "pending"),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
    )


_MAILBOX_COLUMNS = """
    id, user_uid, account_id, native_key, native_name, semantic_key,
    mailbox_type, delimiter_value, attributes_json, uidvalidity,
    highest_modseq, total_count, unread_count, sync_status,
    created_at, updated_at
"""


class MailboxRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def upsert_mailbox(
        self,
        tenant: TenantContext,
        *,
        account_id: str,
        native_key: str,
        native_name: str,
        semantic_key: str = "custom",
        mailbox_type: str = "folder",
        delimiter_value: str = "",
        attributes: tuple[str, ...] | list[str] = (),
        uidvalidity: int = 0,
        highest_modseq: int = 0,
        now: float | None = None,
    ) -> Mailbox:
        account = str(account_id or "").strip()
        native = str(native_key or "")
        display_name = str(native_name or "").strip() or native
        semantic = str(semantic_key or "custom").strip().casefold()
        mailbox_kind = str(mailbox_type or "folder").strip().casefold()
        if not account or not native.strip():
            raise ValueError("account_id and native_key are required")
        if semantic not in _SEMANTIC_KEYS:
            raise ValueError("unsupported semantic mailbox key")
        if mailbox_kind not in _MAILBOX_TYPES:
            raise ValueError("unsupported mailbox type")
        if isinstance(uidvalidity, bool) or int(uidvalidity) < 0:
            raise ValueError("uidvalidity must be non-negative")
        if isinstance(highest_modseq, bool) or int(highest_modseq) < 0:
            raise ValueError("highest_modseq must be non-negative")
        normalized_attributes = tuple(
            sorted({str(attribute).strip() for attribute in attributes if str(attribute).strip()})
        )
        timestamp = float(time.time() if now is None else now)

        account_row = await fetch_one(
            self.connection,
            "SELECT id FROM mail_accounts WHERE id = %s AND user_uid = %s",
            (account, tenant.user_uid),
        )
        if not account_row:
            raise ValueError("mail account does not belong to tenant")

        existing = await fetch_one(
            self.connection,
            f"""
            SELECT {_MAILBOX_COLUMNS}
            FROM mailboxes
            WHERE account_id = %s AND native_key = %s AND user_uid = %s
            FOR UPDATE
            """,
            (account, native, tenant.user_uid),
        )
        new_uidvalidity = int(uidvalidity)
        new_modseq = int(highest_modseq)
        attributes_json = json.dumps(normalized_attributes, ensure_ascii=False)

        if existing:
            mailbox_id = str(existing["id"])
            old_uidvalidity = int(existing["uidvalidity"] or 0)
            effective_uidvalidity = new_uidvalidity or old_uidvalidity
            old_modseq = int(existing["highest_modseq"] or 0)
            effective_modseq = new_modseq or old_modseq
            uidvalidity_changed = (
                old_uidvalidity > 0
                and effective_uidvalidity > 0
                and old_uidvalidity != effective_uidvalidity
            )
            sync_status = "reconciling" if uidvalidity_changed else str(existing["sync_status"] or "pending")
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE mailboxes
                    SET native_name = %s, semantic_key = %s, mailbox_type = %s,
                        delimiter_value = %s, attributes_json = %s,
                        uidvalidity = %s, highest_modseq = %s,
                        sync_status = %s, updated_at = %s
                    WHERE id = %s AND user_uid = %s
                    """,
                    (
                        display_name,
                        semantic,
                        mailbox_kind,
                        str(delimiter_value or ""),
                        attributes_json,
                        effective_uidvalidity,
                        effective_modseq,
                        sync_status,
                        timestamp,
                        mailbox_id,
                        tenant.user_uid,
                    ),
                )
                if uidvalidity_changed:
                    marker = f"reconcile:uidvalidity:{old_uidvalidity}->{effective_uidvalidity}"
                    await cursor.execute(
                        """
                        UPDATE message_remote_instances
                        SET remote_deleted = 1, remote_version = %s, updated_at = %s
                        WHERE user_uid = %s AND account_id = %s AND mailbox_id = %s
                          AND uidvalidity = %s AND remote_deleted = 0
                        """,
                        (
                            marker[:191],
                            timestamp,
                            tenant.user_uid,
                            account,
                            mailbox_id,
                            old_uidvalidity,
                        ),
                    )
        else:
            mailbox_id = new_id("mbx")
            sync_status = "pending"
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO mailboxes (
                        id, user_uid, account_id, native_key, native_name,
                        semantic_key, mailbox_type, delimiter_value,
                        attributes_json, uidvalidity, highest_modseq,
                        total_count, unread_count, sync_status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              0, 0, %s, %s, %s)
                    """,
                    (
                        mailbox_id,
                        tenant.user_uid,
                        account,
                        native,
                        display_name,
                        semantic,
                        mailbox_kind,
                        str(delimiter_value or ""),
                        attributes_json,
                        new_uidvalidity,
                        new_modseq,
                        sync_status,
                        timestamp,
                        timestamp,
                    ),
                )

        row = await fetch_one(
            self.connection,
            f"SELECT {_MAILBOX_COLUMNS} FROM mailboxes WHERE id = %s AND user_uid = %s",
            (mailbox_id, tenant.user_uid),
        )
        if not row:
            raise RuntimeError("mailbox upsert did not persist a row")
        return _map_mailbox(row)

    async def get_mailbox(self, tenant: TenantContext, mailbox_id: str) -> Mailbox | None:
        row = await fetch_one(
            self.connection,
            f"SELECT {_MAILBOX_COLUMNS} FROM mailboxes WHERE id = %s AND user_uid = %s",
            (str(mailbox_id or "").strip(), tenant.user_uid),
        )
        return _map_mailbox(row) if row else None

    async def update_counts(
        self,
        tenant: TenantContext,
        mailbox_id: str,
        *,
        now: float | None = None,
    ) -> tuple[int, int]:
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT mb.id, COUNT(ri.id),
                       COALESCE(SUM(CASE WHEN ri.id IS NOT NULL AND ri.is_read = 0 THEN 1 ELSE 0 END), 0)
                FROM mailboxes mb
                LEFT JOIN message_remote_instances ri
                  ON ri.mailbox_id = mb.id
                 AND ri.user_uid = mb.user_uid
                 AND ri.remote_deleted = 0
                WHERE mb.id = %s AND mb.user_uid = %s
                GROUP BY mb.id
                """,
                (str(mailbox_id or "").strip(), tenant.user_uid),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError("mailbox does not belong to tenant")
            total = int(row[1] or 0)
            unread = int(row[2] or 0)
            await cursor.execute(
                """
                UPDATE mailboxes
                SET total_count = %s, unread_count = %s, updated_at = %s
                WHERE id = %s AND user_uid = %s
                """,
                (total, unread, timestamp, mailbox_id, tenant.user_uid),
            )
        return total, unread
