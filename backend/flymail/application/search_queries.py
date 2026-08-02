"""Validated local-search compiler and tenant-scoped search orchestration."""

from __future__ import annotations

import json
import time
from typing import Any

from flymail.api.schemas.search import (
    SavedSearchResponse,
    SearchFilter,
    SearchHistoryItem,
    SearchResponse,
    SearchResultItem,
    SearchSuggestion,
)
from flymail.application.auth import AuthenticatedSession
from flymail.application.thread_queries import ThreadCursorCodec
from flymail.domain.errors import ApiContractError
from flymail.infrastructure.db.pool import DatabasePool
from flymail.repositories.base import TenantContext
from flymail.repositories.search import CompiledSearch, SearchRepository


class SearchCompiler:
    """Compile validated fields to fixed SQL fragments and bound values."""

    @staticmethod
    def _boolean_phrase(value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'+"{escaped}"'

    @staticmethod
    def _in_clause(values: tuple[str, ...]) -> str:
        return ",".join("%s" for _ in values)

    def compile(
        self,
        tenant: TenantContext,
        filters: SearchFilter,
        *,
        position: tuple[float, str] | None,
        limit: int,
    ) -> CompiledSearch:
        conditions = ["m.user_uid = %s", "m.thread_id IS NOT NULL"]
        where_params: list[object] = [tenant.user_uid]
        select_params: list[object] = []
        keyword = str(filters.keyword or "").strip()
        matched_field = "'metadata'"

        if keyword:
            normalized_keyword = keyword.casefold()
            if len(normalized_keyword) >= 4:
                matched_field = """
                    CASE
                        WHEN LOCATE(%s, LOWER(COALESCE(doc.body_text, ''))) > 0 THEN 'body'
                        WHEN LOCATE(%s, LOWER(COALESCE(doc.subject_text, ''))) > 0 THEN 'subject'
                        ELSE 'participants'
                    END
                """
                select_params.extend((normalized_keyword, normalized_keyword))
                conditions.append(
                    "MATCH(doc.subject_text, doc.participants_text, doc.body_text) "
                    "AGAINST (%s IN BOOLEAN MODE)"
                )
                where_params.append(self._boolean_phrase(keyword))
            else:
                pattern = f"%{normalized_keyword}%"
                matched_field = """
                    CASE
                        WHEN LOWER(COALESCE(m.subject, '')) LIKE %s THEN 'subject'
                        ELSE 'participants'
                    END
                """
                select_params.append(pattern)
                conditions.append(
                    "(LOWER(COALESCE(m.subject, '')) LIKE %s "
                    "OR LOWER(CAST(COALESCE(m.from_json, JSON_ARRAY()) AS CHAR)) LIKE %s "
                    "OR LOWER(CAST(COALESCE(m.to_json, JSON_ARRAY()) AS CHAR)) LIKE %s)"
                )
                where_params.extend((pattern, pattern, pattern))

        if filters.from_addresses:
            fragments = []
            for address in filters.from_addresses:
                fragments.append(
                    "JSON_CONTAINS(COALESCE(m.from_json, JSON_ARRAY()), JSON_QUOTE(%s))"
                )
                where_params.append(str(address).strip().casefold())
            conditions.append(f"({' OR '.join(fragments)})")
        if filters.to_addresses:
            fragments = []
            for address in filters.to_addresses:
                fragments.append(
                    "JSON_CONTAINS(COALESCE(m.to_json, JSON_ARRAY()), JSON_QUOTE(%s))"
                )
                where_params.append(str(address).strip().casefold())
            conditions.append(f"({' OR '.join(fragments)})")
        if filters.date_from is not None:
            conditions.append("m.received_at >= %s")
            where_params.append(float(filters.date_from))
        if filters.date_to is not None:
            conditions.append("m.received_at <= %s")
            where_params.append(float(filters.date_to))
        if filters.has_attachment is not None:
            conditions.append("m.has_attachments = %s")
            where_params.append(1 if filters.has_attachment else 0)
        if filters.min_size_bytes is not None:
            conditions.append("m.size_bytes >= %s")
            where_params.append(int(filters.min_size_bytes))
        if filters.max_size_bytes is not None:
            conditions.append("m.size_bytes <= %s")
            where_params.append(int(filters.max_size_bytes))

        if filters.account_ids:
            conditions.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM message_remote_instances account_instance
                    WHERE account_instance.user_uid = m.user_uid
                      AND account_instance.message_id = m.id
                      AND account_instance.remote_deleted = 0
                      AND account_instance.account_id IN ({self._in_clause(filters.account_ids)})
                )
                """
            )
            where_params.extend(filters.account_ids)
        if filters.mailbox_ids:
            conditions.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM message_remote_instances mailbox_instance
                    JOIN message_memberships mailbox_membership
                      ON mailbox_membership.remote_instance_id = mailbox_instance.id
                     AND mailbox_membership.user_uid = mailbox_instance.user_uid
                    JOIN mailboxes mailbox_filter
                      ON mailbox_filter.id = mailbox_membership.mailbox_id
                     AND mailbox_filter.user_uid = mailbox_membership.user_uid
                    WHERE mailbox_instance.user_uid = m.user_uid
                      AND mailbox_instance.message_id = m.id
                      AND mailbox_instance.remote_deleted = 0
                      AND mailbox_filter.mailbox_type = 'folder'
                      AND mailbox_filter.id IN ({self._in_clause(filters.mailbox_ids)})
                )
                """
            )
            where_params.extend(filters.mailbox_ids)
        if filters.label_ids:
            conditions.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM message_remote_instances label_instance
                    JOIN message_memberships label_membership
                      ON label_membership.remote_instance_id = label_instance.id
                     AND label_membership.user_uid = label_instance.user_uid
                    JOIN mailboxes label_filter
                      ON label_filter.id = label_membership.mailbox_id
                     AND label_filter.user_uid = label_membership.user_uid
                    WHERE label_instance.user_uid = m.user_uid
                      AND label_instance.message_id = m.id
                      AND label_instance.remote_deleted = 0
                      AND label_filter.mailbox_type = 'label'
                      AND label_filter.id IN ({self._in_clause(filters.label_ids)})
                )
                """
            )
            where_params.extend(filters.label_ids)
        if filters.is_read is not None:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM message_remote_instances read_instance
                    WHERE read_instance.user_uid = m.user_uid
                      AND read_instance.message_id = m.id
                      AND read_instance.remote_deleted = 0
                      AND read_instance.is_read = %s
                )
                """
            )
            where_params.append(1 if filters.is_read else 0)
        if filters.is_starred is not None:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM message_remote_instances starred_instance
                    WHERE starred_instance.user_uid = m.user_uid
                      AND starred_instance.message_id = m.id
                      AND starred_instance.remote_deleted = 0
                      AND starred_instance.is_starred = %s
                )
                """
            )
            where_params.append(1 if filters.is_starred else 0)

        outer_conditions = ["thread_rank = 1"]
        outer_params: list[object] = []
        if position is not None:
            outer_conditions.append(
                "(received_at < %s OR (received_at = %s AND matched_message_id < %s))"
            )
            outer_params.extend((position[0], position[0], position[1]))
        outer_params.append(int(limit) + 1)

        sql = f"""
            WITH matched AS (
                SELECT m.user_uid, m.thread_id,
                       m.id AS matched_message_id,
                       {matched_field} AS matched_field,
                       COALESCE(m.subject, '') AS subject,
                       COALESCE(m.snippet, '') AS snippet,
                       m.received_at, m.has_attachments,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.thread_id
                           ORDER BY m.received_at DESC, m.id DESC
                       ) AS thread_rank
                FROM messages m
                LEFT JOIN body_search_documents doc
                  ON doc.message_id = m.id AND doc.user_uid = m.user_uid
                WHERE {' AND '.join(conditions)}
            )
            SELECT matched.thread_id, matched.matched_message_id,
                   matched.matched_field, matched.subject, matched.snippet,
                   matched.received_at, matched.has_attachments,
                   COALESCE((
                       SELECT GROUP_CONCAT(
                           DISTINCT source_instance.account_id
                           ORDER BY source_instance.account_id SEPARATOR ','
                       )
                       FROM thread_messages source_thread
                       JOIN message_remote_instances source_instance
                         ON source_instance.message_id = source_thread.message_id
                        AND source_instance.user_uid = source_thread.user_uid
                        AND source_instance.remote_deleted = 0
                       WHERE source_thread.user_uid = matched.user_uid
                         AND source_thread.thread_id = matched.thread_id
                   ), '') AS account_ids,
                   EXISTS (
                       SELECT 1 FROM message_remote_instances unread_instance
                       WHERE unread_instance.user_uid = matched.user_uid
                         AND unread_instance.message_id = matched.matched_message_id
                         AND unread_instance.remote_deleted = 0
                         AND unread_instance.is_read = 0
                   ) AS unread,
                   EXISTS (
                       SELECT 1 FROM message_remote_instances result_starred
                       WHERE result_starred.user_uid = matched.user_uid
                         AND result_starred.message_id = matched.matched_message_id
                         AND result_starred.remote_deleted = 0
                         AND result_starred.is_starred = 1
                   ) AS starred
            FROM matched
            WHERE {' AND '.join(outer_conditions)}
            ORDER BY received_at DESC, matched_message_id DESC
            LIMIT %s
        """
        return CompiledSearch(
            sql=sql,
            params=tuple(select_params + where_params + outer_params),
        )


class SearchQueryService:
    def __init__(
        self,
        pool: DatabasePool,
        cursor_secret: str,
        *,
        now_fn=time.time,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool
        self.compiler = SearchCompiler()
        self.cursor_codec = ThreadCursorCodec(cursor_secret)
        self.now_fn = now_fn

    @staticmethod
    def _decode_json(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return dict(decoded) if isinstance(decoded, dict) else {}
        return {}

    @staticmethod
    def _saved(row: dict[str, Any]) -> SavedSearchResponse:
        return SavedSearchResponse(
            id=str(row["id"]),
            name=str(row["name"]),
            filters=SearchFilter.model_validate(SearchQueryService._decode_json(row["filters_json"])),
            is_pinned=bool(row["is_pinned"]),
            created_at=float(row["created_at"] or 0),
            updated_at=float(row["updated_at"] or 0),
        )

    async def search(
        self,
        session: AuthenticatedSession,
        filters: SearchFilter,
        *,
        limit: int,
        cursor: str | None,
    ) -> SearchResponse:
        if not filters.has_condition():
            raise ApiContractError(
                "empty_search",
                "搜索至少需要一个条件",
                status_code=422,
            )
        tenant = TenantContext(session.user.id)
        position = self.cursor_codec.decode(cursor)
        page_size = min(max(int(limit), 1), 100)
        compiled = self.compiler.compile(
            tenant,
            filters,
            position=position,
            limit=page_size,
        )
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = SearchRepository(connection)
                parser = await repository.fulltext_parser()
                rows = await repository.execute_search(compiled)
                await repository.append_history(
                    tenant,
                    filters.model_dump(mode="json"),
                    now=float(self.now_fn()),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        visible = rows[:page_size]
        items = tuple(
            SearchResultItem(
                thread_id=str(row["thread_id"]),
                matched_message_id=str(row["matched_message_id"]),
                matched_field=str(row["matched_field"] or "metadata"),
                subject=str(row["subject"] or ""),
                snippet=str(row["snippet"] or ""),
                received_at=float(row["received_at"] or 0),
                account_ids=tuple(
                    value for value in str(row["account_ids"] or "").split(",") if value
                ),
                unread=bool(row["unread"]),
                starred=bool(row["starred"]),
                has_attachment=bool(row["has_attachments"]),
            )
            for row in visible
        )
        next_cursor = None
        if len(rows) > page_size and items:
            last = items[-1]
            next_cursor = self.cursor_codec.encode(
                last.received_at,
                last.matched_message_id,
            )
        return SearchResponse(
            items=items,
            next_cursor=next_cursor,
            fulltext_parser=parser,
        )

    async def suggestions(
        self,
        session: AuthenticatedSession,
        query: str,
    ) -> tuple[SearchSuggestion, ...]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            rows = await SearchRepository(connection).suggestions(tenant, query)
        return tuple(SearchSuggestion(**row) for row in rows)

    async def history(
        self,
        session: AuthenticatedSession,
    ) -> tuple[SearchHistoryItem, ...]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            rows = await SearchRepository(connection).list_history(tenant)
        return tuple(
            SearchHistoryItem(
                sequence_id=int(row["sequence_id"]),
                filters=SearchFilter.model_validate(self._decode_json(row["filter_summary"])),
                created_at=float(row["created_at"] or 0),
            )
            for row in rows
        )

    async def clear_history(self, session: AuthenticatedSession) -> None:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await SearchRepository(connection).clear_history(tenant)
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    async def create_saved(
        self,
        session: AuthenticatedSession,
        *,
        name: str,
        filters: SearchFilter,
        is_pinned: bool,
    ) -> SavedSearchResponse:
        if not filters.has_condition():
            raise ApiContractError("empty_search", "保存搜索至少需要一个条件", status_code=422)
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                row = await SearchRepository(connection).create_saved_search(
                    tenant,
                    name=name,
                    filters=filters.model_dump(mode="json"),
                    is_pinned=is_pinned,
                    now=float(self.now_fn()),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return self._saved(row)

    async def list_saved(
        self,
        session: AuthenticatedSession,
    ) -> tuple[SavedSearchResponse, ...]:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            rows = await SearchRepository(connection).list_saved_searches(tenant)
        return tuple(self._saved(row) for row in rows)

    async def update_saved(
        self,
        session: AuthenticatedSession,
        saved_id: str,
        *,
        name: str | None,
        filters: SearchFilter | None,
        is_pinned: bool | None,
    ) -> SavedSearchResponse:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = SearchRepository(connection)
                current = await repository.get_saved_search(tenant, saved_id)
                current_filters = SearchFilter.model_validate(
                    self._decode_json(current["filters_json"])
                )
                selected_filters = filters or current_filters
                if not selected_filters.has_condition():
                    raise ApiContractError(
                        "empty_search",
                        "保存搜索至少需要一个条件",
                        status_code=422,
                    )
                row = await repository.update_saved_search(
                    tenant,
                    saved_id,
                    name=str(name if name is not None else current["name"]),
                    filters=selected_filters.model_dump(mode="json"),
                    is_pinned=(bool(is_pinned) if is_pinned is not None else bool(current["is_pinned"])),
                    now=float(self.now_fn()),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return self._saved(row)

    async def delete_saved(
        self,
        session: AuthenticatedSession,
        saved_id: str,
    ) -> None:
        tenant = TenantContext(session.user.id)
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await SearchRepository(connection).delete_saved_search(tenant, saved_id)
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
