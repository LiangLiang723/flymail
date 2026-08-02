"""SQL-only tenant-scoped local search, history, suggestions, and saved searches."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Sequence

import aiomysql
from pymysql.err import IntegrityError

from flymail.domain.errors import ConflictError, NotFoundError
from flymail.domain.ids import new_id
from flymail.repositories.base import TenantContext


@dataclass(frozen=True, slots=True)
class CompiledSearch:
    sql: str
    params: tuple[object, ...]


class SearchRepository:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection

    async def fulltext_parser(self) -> str:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                "SELECT metadata_json FROM schema_migrations WHERE version = 4"
            )
            row = await cursor.fetchone()
        if not row:
            return "standard"
        raw = row[0]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return "standard"
        return str(raw.get("fulltext_parser") or "standard") if isinstance(raw, dict) else "standard"

    async def execute_search(self, compiled: CompiledSearch) -> list[dict[str, Any]]:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(compiled.sql, compiled.params)
            return [dict(row) for row in await cursor.fetchall()]

    async def append_history(
        self,
        tenant: TenantContext,
        filters: dict[str, Any],
        *,
        now: float | None = None,
        maximum: int = 50,
    ) -> None:
        timestamp = float(time.time() if now is None else now)
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO search_history (user_uid, filter_summary, created_at)
                VALUES (%s, %s, %s)
                """,
                (
                    tenant.user_uid,
                    json.dumps(filters, ensure_ascii=False, sort_keys=True),
                    timestamp,
                ),
            )
            await cursor.execute(
                """
                DELETE FROM search_history
                WHERE user_uid = %s
                  AND sequence_id NOT IN (
                      SELECT sequence_id
                      FROM (
                          SELECT sequence_id
                          FROM search_history
                          WHERE user_uid = %s
                          ORDER BY created_at DESC, sequence_id DESC
                          LIMIT %s
                      ) retained
                  )
                """,
                (tenant.user_uid, tenant.user_uid, int(maximum)),
            )

    async def list_history(
        self,
        tenant: TenantContext,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT sequence_id, filter_summary, created_at
                FROM search_history
                WHERE user_uid = %s
                ORDER BY created_at DESC, sequence_id DESC
                LIMIT %s
                """,
                (tenant.user_uid, int(limit)),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def clear_history(self, tenant: TenantContext) -> int:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM search_history WHERE user_uid = %s",
                (tenant.user_uid,),
            )
            return int(cursor.rowcount)

    async def suggestions(
        self,
        tenant: TenantContext,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        normalized = str(query or "").strip().casefold()
        prefix = f"{normalized}%"
        contains = f"%{normalized}%"
        suggestions: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        async def collect(sql: str, params: Sequence[object], kind: str) -> None:
            async with self.connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, tuple(params))
                rows = await cursor.fetchall()
            for row in rows:
                value = str(row["value"] or "").strip()
                if not value:
                    continue
                key = (kind, value.casefold())
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append(
                    {
                        "kind": kind,
                        "value": value,
                        "label": str(row["label"] or value),
                    }
                )
                if len(suggestions) >= limit:
                    return

        await collect(
            """
            SELECT primary_email AS value,
                   CASE WHEN display_name = '' THEN primary_email
                        ELSE CONCAT(display_name, ' <', primary_email, '>') END AS label
            FROM contacts
            WHERE user_uid = %s
              AND (normalized_name LIKE %s OR normalized_email LIKE %s)
            ORDER BY normalized_name, id
            LIMIT 8
            """,
            (tenant.user_uid, prefix, prefix),
            "contact",
        )
        if len(suggestions) < limit:
            await collect(
                """
                SELECT from_address AS value,
                       CASE WHEN display_name = '' THEN from_address
                            ELSE CONCAT(display_name, ' <', from_address, '>') END AS label
                FROM mail_identities
                WHERE user_uid = %s AND is_verified = 1
                  AND (normalized_from_address LIKE %s OR LOWER(display_name) LIKE %s)
                ORDER BY is_default DESC, id
                LIMIT 8
                """,
                (tenant.user_uid, prefix, prefix),
                "identity",
            )
        if len(suggestions) < limit:
            await collect(
                """
                SELECT id AS value, native_name AS label
                FROM mailboxes
                WHERE user_uid = %s AND mailbox_type = 'label'
                  AND LOWER(native_name) LIKE %s
                ORDER BY native_name, id
                LIMIT 8
                """,
                (tenant.user_uid, prefix),
                "label",
            )
        if len(suggestions) < limit:
            await collect(
                """
                SELECT JSON_UNQUOTE(JSON_EXTRACT(filter_summary, '$.keyword')) AS value,
                       JSON_UNQUOTE(JSON_EXTRACT(filter_summary, '$.keyword')) AS label
                FROM search_history
                WHERE user_uid = %s
                  AND JSON_UNQUOTE(JSON_EXTRACT(filter_summary, '$.keyword')) LIKE %s
                ORDER BY created_at DESC, sequence_id DESC
                LIMIT 8
                """,
                (tenant.user_uid, contains),
                "recent",
            )
        return suggestions[:limit]

    async def create_saved_search(
        self,
        tenant: TenantContext,
        *,
        name: str,
        filters: dict[str, Any],
        is_pinned: bool,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = float(time.time() if now is None else now)
        saved_id = new_id("search")
        try:
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO saved_searches (
                        id, user_uid, name, filters_json, is_pinned,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        saved_id,
                        tenant.user_uid,
                        str(name or "").strip(),
                        json.dumps(filters, ensure_ascii=False, sort_keys=True),
                        1 if is_pinned else 0,
                        timestamp,
                        timestamp,
                    ),
                )
        except IntegrityError as exc:
            raise ConflictError("saved search name already exists") from exc
        return await self.get_saved_search(tenant, saved_id)

    async def get_saved_search(
        self,
        tenant: TenantContext,
        saved_id: str,
    ) -> dict[str, Any]:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, name, filters_json, is_pinned, created_at, updated_at
                FROM saved_searches
                WHERE id = %s AND user_uid = %s
                """,
                (str(saved_id or "").strip(), tenant.user_uid),
            )
            row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("saved search was not found")
        return dict(row)

    async def list_saved_searches(self, tenant: TenantContext) -> list[dict[str, Any]]:
        async with self.connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(
                """
                SELECT id, name, filters_json, is_pinned, created_at, updated_at
                FROM saved_searches
                WHERE user_uid = %s
                ORDER BY is_pinned DESC, updated_at DESC, id DESC
                """,
                (tenant.user_uid,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def update_saved_search(
        self,
        tenant: TenantContext,
        saved_id: str,
        *,
        name: str,
        filters: dict[str, Any],
        is_pinned: bool,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = float(time.time() if now is None else now)
        try:
            async with self.connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE saved_searches
                    SET name = %s, filters_json = %s, is_pinned = %s,
                        updated_at = %s
                    WHERE id = %s AND user_uid = %s
                    """,
                    (
                        str(name or "").strip(),
                        json.dumps(filters, ensure_ascii=False, sort_keys=True),
                        1 if is_pinned else 0,
                        timestamp,
                        str(saved_id or "").strip(),
                        tenant.user_uid,
                    ),
                )
                changed = cursor.rowcount == 1
        except IntegrityError as exc:
            raise ConflictError("saved search name already exists") from exc
        if not changed:
            raise NotFoundError("saved search was not found")
        return await self.get_saved_search(tenant, saved_id)

    async def delete_saved_search(self, tenant: TenantContext, saved_id: str) -> None:
        async with self.connection.cursor() as cursor:
            await cursor.execute(
                "DELETE FROM saved_searches WHERE id = %s AND user_uid = %s",
                (str(saved_id or "").strip(), tenant.user_uid),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("saved search was not found")
