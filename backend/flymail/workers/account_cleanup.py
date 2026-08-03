"""Bounded local account cleanup preserving shared mail and send audit."""

from __future__ import annotations

import time

from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.observability.logging import get_safe_logger
from flymail.repositories.base import TenantContext
from flymail.repositories.objects import ObjectRepository
from flymail.repositories.threads import ThreadRepository


logger = get_safe_logger("account.cleanup")


_TEMP_TABLES = (
    "tmp_account_cleanup_remote",
    "tmp_account_cleanup_messages",
    "tmp_account_cleanup_threads",
    "tmp_account_cleanup_attachments",
    "tmp_account_cleanup_drafts",
    "tmp_account_cleanup_draft_attachments",
    "tmp_account_cleanup_digests",
)


class AccountDataCleanupGateway:
    """Delete one account's local data without deleting shared content or send audit."""

    def __init__(self, pool: DatabasePool, store: ObjectStore) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(store, ObjectStore):
            raise TypeError("store must be ObjectStore")
        self.pool = pool
        self.store = store

    async def cleanup(self, *, user_uid: str, account_id: str) -> None:
        tenant = TenantContext(user_uid)
        normalized_account = str(account_id or "").strip()
        if not normalized_account:
            raise ValueError("account_id is required")
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await self._prepare_temp_tables(connection)
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT status
                        FROM mail_accounts
                        WHERE id=%s AND user_uid=%s
                        FOR UPDATE
                        """,
                        (normalized_account, tenant.user_uid),
                    )
                    row = await cursor.fetchone()
                if row is None:
                    await connection.rollback()
                    return
                if str(row[0]) != "deleting":
                    raise ValueError("mail account is not deleting")

                await self._capture_scope(connection, tenant, normalized_account)
                await self._delete_scope(connection, tenant, normalized_account)
                await self._refresh_threads(connection, tenant)
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

            await self._remove_unreferenced_objects(connection)

    async def _prepare_temp_tables(self, connection) -> None:
        definitions = {
            "tmp_account_cleanup_remote": "id VARCHAR(64) PRIMARY KEY, message_id VARCHAR(64) NOT NULL",
            "tmp_account_cleanup_messages": "id VARCHAR(64) PRIMARY KEY",
            "tmp_account_cleanup_threads": "id VARCHAR(64) PRIMARY KEY",
            "tmp_account_cleanup_attachments": "id VARCHAR(64) PRIMARY KEY",
            "tmp_account_cleanup_drafts": "id VARCHAR(64) PRIMARY KEY",
            "tmp_account_cleanup_draft_attachments": "id VARCHAR(64) PRIMARY KEY",
            "tmp_account_cleanup_digests": "digest CHAR(64) PRIMARY KEY",
        }
        async with connection.cursor() as cursor:
            for table in _TEMP_TABLES:
                await cursor.execute(
                    f"CREATE TEMPORARY TABLE IF NOT EXISTS {table} ({definitions[table]}) ENGINE=InnoDB"
                )
                await cursor.execute(f"TRUNCATE TABLE {table}")

    async def _capture_scope(
        self,
        connection,
        tenant: TenantContext,
        account_id: str,
    ) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT IGNORE INTO tmp_account_cleanup_remote (id, message_id)
                SELECT id, message_id
                FROM message_remote_instances
                WHERE user_uid=%s AND account_id=%s
                """,
                (tenant.user_uid, account_id),
            )
            await cursor.execute(
                """
                INSERT IGNORE INTO tmp_account_cleanup_threads (id)
                SELECT DISTINCT messages.thread_id
                FROM messages
                JOIN tmp_account_cleanup_remote remote
                  ON remote.message_id=messages.id
                WHERE messages.user_uid=%s
                """,
                (tenant.user_uid,),
            )
            await cursor.execute(
                """
                INSERT IGNORE INTO tmp_account_cleanup_messages (id)
                SELECT DISTINCT remote.message_id
                FROM tmp_account_cleanup_remote remote
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM message_remote_instances other
                    WHERE other.user_uid=%s
                      AND other.message_id=remote.message_id
                      AND other.account_id<>%s
                      AND other.remote_deleted=0
                )
                """,
                (tenant.user_uid, account_id),
            )
            await cursor.execute(
                """
                INSERT IGNORE INTO tmp_account_cleanup_attachments (id)
                SELECT attachment.id
                FROM message_attachments attachment
                JOIN tmp_account_cleanup_remote remote
                  ON remote.id=attachment.remote_instance_id
                WHERE attachment.user_uid=%s
                """,
                (tenant.user_uid,),
            )
            await cursor.execute(
                """
                INSERT IGNORE INTO tmp_account_cleanup_drafts (id)
                SELECT id
                FROM drafts
                WHERE user_uid=%s AND account_id=%s
                """,
                (tenant.user_uid, account_id),
            )
            await cursor.execute(
                """
                INSERT IGNORE INTO tmp_account_cleanup_draft_attachments (id)
                SELECT attachment.id
                FROM draft_attachments attachment
                JOIN tmp_account_cleanup_drafts draft
                  ON draft.id=attachment.draft_id
                WHERE attachment.user_uid=%s
                """,
                (tenant.user_uid,),
            )
            await cursor.execute(
                """
                INSERT IGNORE INTO tmp_account_cleanup_digests (digest)
                SELECT reference.content_sha256
                FROM content_references reference
                WHERE reference.user_uid=%s
                  AND (
                    (reference.reference_kind='account_icon' AND reference.reference_id=%s)
                    OR (
                        reference.reference_kind IN (
                            'message_body_html','message_body_text','raw_eml'
                        )
                        AND EXISTS (
                            SELECT 1 FROM tmp_account_cleanup_messages message
                            WHERE message.id=reference.reference_id
                        )
                    )
                    OR (
                        reference.reference_kind IN (
                            'message_inline_image','message_attachment'
                        )
                        AND EXISTS (
                            SELECT 1 FROM tmp_account_cleanup_attachments attachment
                            WHERE attachment.id=reference.reference_id
                        )
                    )
                    OR (
                        reference.reference_kind IN (
                            'draft_body_html','draft_body_text','raw_eml'
                        )
                        AND EXISTS (
                            SELECT 1 FROM tmp_account_cleanup_drafts draft
                            WHERE draft.id=reference.reference_id
                        )
                    )
                    OR (
                        reference.reference_kind='draft_attachment'
                        AND EXISTS (
                            SELECT 1
                            FROM tmp_account_cleanup_draft_attachments attachment
                            WHERE attachment.id=reference.reference_id
                        )
                    )
                  )
                """,
                (tenant.user_uid, account_id),
            )

    async def _delete_scope(
        self,
        connection,
        tenant: TenantContext,
        account_id: str,
    ) -> None:
        now = time.time()
        statements: tuple[tuple[str, tuple], ...] = (
            (
                """
                DELETE reference FROM content_references reference
                WHERE reference.user_uid=%s
                  AND EXISTS (
                    SELECT 1 FROM tmp_account_cleanup_digests digest
                    WHERE digest.digest=reference.content_sha256
                  )
                  AND (
                    (reference.reference_kind='account_icon' AND reference.reference_id=%s)
                    OR EXISTS (
                        SELECT 1 FROM tmp_account_cleanup_messages message
                        WHERE message.id=reference.reference_id
                    )
                    OR EXISTS (
                        SELECT 1 FROM tmp_account_cleanup_attachments attachment
                        WHERE attachment.id=reference.reference_id
                    )
                    OR EXISTS (
                        SELECT 1 FROM tmp_account_cleanup_drafts draft
                        WHERE draft.id=reference.reference_id
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM tmp_account_cleanup_draft_attachments attachment
                        WHERE attachment.id=reference.reference_id
                    )
                  )
                """,
                (tenant.user_uid, account_id),
            ),
            (
                """
                DELETE attachment FROM message_attachments attachment
                JOIN tmp_account_cleanup_attachments target ON target.id=attachment.id
                WHERE attachment.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE part FROM message_body_parts part
                JOIN tmp_account_cleanup_remote remote ON remote.id=part.remote_instance_id
                WHERE part.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE membership FROM message_memberships membership
                JOIN tmp_account_cleanup_remote remote
                  ON remote.id=membership.remote_instance_id
                WHERE membership.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE remote FROM message_remote_instances remote
                JOIN tmp_account_cleanup_remote target ON target.id=remote.id
                WHERE remote.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE header FROM message_headers header
                JOIN tmp_account_cleanup_messages message ON message.id=header.message_id
                WHERE header.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE body FROM message_bodies body
                JOIN tmp_account_cleanup_messages message ON message.id=body.message_id
                WHERE body.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE search FROM body_search_documents search
                JOIN tmp_account_cleanup_messages message ON message.id=search.message_id
                WHERE search.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE link FROM thread_messages link
                JOIN tmp_account_cleanup_messages message ON message.id=link.message_id
                WHERE link.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE message FROM messages message
                JOIN tmp_account_cleanup_messages target ON target.id=message.id
                WHERE message.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE attachment FROM draft_attachments attachment
                JOIN tmp_account_cleanup_draft_attachments target
                  ON target.id=attachment.id
                WHERE attachment.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE recipient FROM draft_recipients recipient
                JOIN tmp_account_cleanup_drafts draft ON draft.id=recipient.draft_id
                WHERE recipient.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE version FROM draft_versions version
                JOIN tmp_account_cleanup_drafts draft ON draft.id=version.draft_id
                WHERE version.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE draft FROM drafts draft
                JOIN tmp_account_cleanup_drafts target ON target.id=draft.id
                WHERE draft.user_uid=%s
                """,
                (tenant.user_uid,),
            ),
            (
                """
                DELETE operation FROM mail_operations operation
                LEFT JOIN send_attempts attempt ON attempt.operation_id=operation.id
                WHERE operation.user_uid=%s AND operation.account_id=%s
                  AND attempt.id IS NULL
                """,
                (tenant.user_uid, account_id),
            ),
            (
                "DELETE FROM worker_jobs WHERE user_uid=%s AND account_id=%s",
                (tenant.user_uid, account_id),
            ),
            (
                "DELETE FROM sync_cursors WHERE user_uid=%s AND account_id=%s",
                (tenant.user_uid, account_id),
            ),
            (
                "DELETE FROM provider_credentials WHERE user_uid=%s AND account_id=%s",
                (tenant.user_uid, account_id),
            ),
            (
                "DELETE FROM mail_identities WHERE user_uid=%s AND account_id=%s",
                (tenant.user_uid, account_id),
            ),
            (
                "DELETE FROM outbound_proxy_configs WHERE user_uid=%s AND account_id=%s",
                (tenant.user_uid, account_id),
            ),
            (
                "UPDATE notification_events SET account_id=NULL WHERE user_uid=%s AND account_id=%s",
                (tenant.user_uid, account_id),
            ),
            (
                "DELETE FROM account_runtime_state WHERE user_uid=%s AND account_id=%s",
                (tenant.user_uid, account_id),
            ),
            (
                "DELETE FROM mailboxes WHERE user_uid=%s AND account_id=%s",
                (tenant.user_uid, account_id),
            ),
            (
                "DELETE FROM mail_accounts WHERE user_uid=%s AND id=%s AND status='deleting'",
                (tenant.user_uid, account_id),
            ),
        )
        async with connection.cursor() as cursor:
            for sql, params in statements:
                await cursor.execute(sql, params)
            await cursor.execute(
                """
                UPDATE users SET updated_at=GREATEST(updated_at, %s)
                WHERE id=%s
                """,
                (now, tenant.user_uid),
            )

    async def _refresh_threads(self, connection, tenant: TenantContext) -> None:
        repository = ThreadRepository(connection)
        last_id = ""
        while True:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT id
                    FROM tmp_account_cleanup_threads
                    WHERE id>%s
                    ORDER BY id
                    LIMIT 500
                    """,
                    (last_id,),
                )
                rows = await cursor.fetchall()
            if not rows:
                return
            thread_ids = tuple(str(row[0]) for row in rows)
            await repository.refresh_projections(
                tenant,
                thread_ids,
                now=time.time(),
            )
            await repository.remove_empty_threads(tenant, thread_ids)
            last_id = thread_ids[-1]

    async def _remove_unreferenced_objects(self, connection) -> None:
        repository = ObjectRepository(connection)
        last_digest = ""
        while True:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT digest
                    FROM tmp_account_cleanup_digests
                    WHERE digest>%s
                    ORDER BY digest
                    LIMIT 100
                    """,
                    (last_digest,),
                )
                rows = await cursor.fetchall()
            if not rows:
                return
            digests = tuple(str(row[0]) for row in rows)
            for digest in digests:
                try:
                    await self.store.remove_unreferenced(digest, repository)
                except Exception as exc:
                    logger.warning(
                        "object cleanup deferred",
                        operation="remove_unreferenced_object",
                        error_class=type(exc).__name__,
                        result_count=1,
                    )
            last_digest = digests[-1]


__all__ = ["AccountDataCleanupGateway"]
