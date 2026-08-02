from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import aiomysql
import httpx

from flymail.config import FlyMailSettings
from flymail.domain.enums import ObjectKind
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.object_store.store import ObjectStore
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


async def _chunks(value: bytes):
    yield value


class ThreadApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-thread-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="thread-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("thread-user", "ThreadPassword!123")
        self.other = await self._create_user("thread-other", "OtherPassword!123")
        await self._seed_data()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "job_attempts",
            "worker_jobs",
            "content_references",
            "content_objects",
            "message_attachments",
            "message_bodies",
            "mail_operations",
            "thread_projections",
            "thread_messages",
            "message_memberships",
            "message_remote_instances",
            "message_headers",
            "messages",
            "threads",
            "mailboxes",
            "account_runtime_state",
            "mail_identities",
            "mail_accounts",
            "login_rate_limits",
            "user_sessions",
            "user_profiles",
            "user_settings",
            "users",
            "process_heartbeats",
        )
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                for table in tables:
                    await cursor.execute(f"DELETE FROM {table}")
            await connection.commit()

    async def _create_user(self, username: str, password: str):
        async with self.api_pool.acquire() as connection:
            await connection.begin()
            user = await UserRepository(connection).create_user_for_admin(
                AdminContext("usr_thread_test_admin"),
                username=username,
                password_hash=hash_password(password),
            )
            await connection.commit()
        return user

    async def _seed_data(self) -> None:
        store = ObjectStore(self.settings.object_dir, self.settings.object_tmp_dir)
        self.cached_html = b"<article><h1>Cached body</h1></article>"
        stored = await store.put_stream(ObjectKind.BODY_HTML, _chunks(self.cached_html))
        self.body_digest = stored.content_sha256

        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, status, poll_interval_seconds,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'active', 300, %s, %s)
                    """,
                    (
                        ("acc_thread_a", self.user.id, "gmail", "a@example.com", "a@example.com", "Account A", 1.0, 1.0),
                        ("acc_thread_b", self.user.id, "outlook", "b@example.com", "b@example.com", "Account B", 2.0, 2.0),
                        ("acc_thread_other", self.other.id, "gmail", "other@example.com", "other@example.com", "Other", 3.0, 3.0),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO account_runtime_state (
                        account_id, user_uid, status, idle_status,
                        last_activity_at, last_change_at, next_reconcile_at,
                        failure_count, backoff_until, updated_at
                    ) VALUES (%s, %s, 'normal', 'disconnected', 0, 0, 0, 0, 0, 0)
                    """,
                    (
                        ("acc_thread_a", self.user.id),
                        ("acc_thread_b", self.user.id),
                        ("acc_thread_other", self.other.id),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO mailboxes (
                        id, user_uid, account_id, native_key, native_name,
                        semantic_key, mailbox_type, total_count, unread_count,
                        sync_status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0, 'ready', 1, 1)
                    """,
                    (
                        ("mb_a_inbox", self.user.id, "acc_thread_a", "INBOX", "Inbox", "inbox", "folder"),
                        ("mb_a_label", self.user.id, "acc_thread_a", "Label/Project", "Project", "custom", "label"),
                        ("mb_b_inbox", self.user.id, "acc_thread_b", "INBOX", "Inbox", "inbox", "folder"),
                        ("mb_other", self.other.id, "acc_thread_other", "INBOX", "Inbox", "inbox", "folder"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO threads (
                        id, user_uid, canonical_thread_key,
                        normalized_subject, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, 1, %s)
                    """,
                    (
                        ("thr_thread_1", self.user.id, "thread-1", "first", 100.0),
                        ("thr_thread_2", self.user.id, "thread-2", "second", 100.0),
                        ("thr_thread_3", self.user.id, "thread-3", "third", 120.0),
                        ("thr_thread_other", self.other.id, "thread-other", "other", 999.0),
                    ),
                )
                messages = (
                    ("msg_thread_1a", self.user.id, "key-1a", "thr_thread_1", "First old", '["one@example.com"]', '["thread-user@example.com"]', 90.0, 90.0, 0, "old", "ready", "ready"),
                    ("msg_thread_1b", self.user.id, "key-1b", "thr_thread_1", "First latest", '["two@example.com"]', '["thread-user@example.com"]', 100.0, 100.0, 1, "latest", "ready", "ready"),
                    ("msg_thread_2", self.user.id, "key-2", "thr_thread_2", "Second", '["three@example.com"]', '["thread-user@example.com"]', 100.0, 100.0, 0, "second", "not_requested", "metadata"),
                    ("msg_thread_3", self.user.id, "key-3", "thr_thread_3", "Third", '["four@example.com"]', '["thread-user@example.com"]', 120.0, 120.0, 0, "third", "not_requested", "metadata"),
                    ("msg_thread_other", self.other.id, "key-other", "thr_thread_other", "Other secret", '["secret@example.com"]', '["other@example.com"]', 999.0, 999.0, 0, "other-secret", "ready", "ready"),
                )
                await cursor.executemany(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, thread_id,
                        subject, normalized_subject, from_json, to_json,
                        received_at, sent_at, has_attachments, snippet,
                        body_state, search_state, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, 1, 1)
                    """,
                    tuple(
                        (
                            message_id,
                            user_uid,
                            key,
                            thread_id,
                            subject,
                            subject.casefold(),
                            from_json,
                            to_json,
                            received_at,
                            sent_at,
                            attachments,
                            snippet,
                            body_state,
                            search_state,
                        )
                        for (
                            message_id,
                            user_uid,
                            key,
                            thread_id,
                            subject,
                            from_json,
                            to_json,
                            received_at,
                            sent_at,
                            attachments,
                            snippet,
                            body_state,
                            search_state,
                        ) in messages
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO thread_messages (
                        thread_id, message_id, user_uid, relation_source,
                        position_hint, created_at
                    ) VALUES (%s, %s, %s, 'headers', %s, 1)
                    """,
                    (
                        ("thr_thread_1", "msg_thread_1a", self.user.id, 1),
                        ("thr_thread_1", "msg_thread_1b", self.user.id, 2),
                        ("thr_thread_2", "msg_thread_2", self.user.id, 1),
                        ("thr_thread_3", "msg_thread_3", self.user.id, 1),
                        ("thr_thread_other", "msg_thread_other", self.other.id, 1),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO message_remote_instances (
                        id, user_uid, account_id, mailbox_id, message_id,
                        uidvalidity, remote_uid, flags_json, is_read, is_starred,
                        remote_deleted, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, %s, '{}', %s, %s, 0, 1, 1)
                    """,
                    (
                        ("ri_thread_1a", self.user.id, "acc_thread_a", "mb_a_inbox", "msg_thread_1a", 11, 1, 0),
                        ("ri_thread_1b_a", self.user.id, "acc_thread_a", "mb_a_inbox", "msg_thread_1b", 12, 0, 1),
                        ("ri_thread_1b_b", self.user.id, "acc_thread_b", "mb_b_inbox", "msg_thread_1b", 22, 1, 1),
                        ("ri_thread_2", self.user.id, "acc_thread_a", "mb_a_inbox", "msg_thread_2", 13, 0, 0),
                        ("ri_thread_3", self.user.id, "acc_thread_b", "mb_b_inbox", "msg_thread_3", 23, 1, 0),
                        ("ri_thread_other", self.other.id, "acc_thread_other", "mb_other", "msg_thread_other", 99, 0, 0),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO message_memberships (
                        remote_instance_id, mailbox_id, user_uid,
                        membership_kind, provider_label, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 1, 1)
                    """,
                    (
                        ("ri_thread_1a", "mb_a_inbox", self.user.id, "folder", ""),
                        ("ri_thread_1b_a", "mb_a_inbox", self.user.id, "folder", ""),
                        ("ri_thread_1b_a", "mb_a_label", self.user.id, "label", "Project"),
                        ("ri_thread_1b_b", "mb_b_inbox", self.user.id, "folder", ""),
                        ("ri_thread_2", "mb_a_inbox", self.user.id, "folder", ""),
                        ("ri_thread_3", "mb_b_inbox", self.user.id, "folder", ""),
                        ("ri_thread_other", "mb_other", self.other.id, "folder", ""),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO thread_projections (
                        user_uid, semantic_mailbox, thread_id,
                        latest_message_id, latest_message_at, subject,
                        participants_summary, latest_snippet, message_count,
                        unread_count, is_starred, has_attachments,
                        account_count, pending_operation_count,
                        projection_version, updated_at
                    ) VALUES (%s, 'inbox', %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, 0, 1, %s)
                    """,
                    (
                        (self.user.id, "thr_thread_1", "msg_thread_1b", 100.0, "First latest", "two@example.com", "latest", 2, 1, 1, 1, 2, 100.0),
                        (self.user.id, "thr_thread_2", "msg_thread_2", 100.0, "Second", "three@example.com", "second", 1, 1, 0, 0, 1, 100.0),
                        (self.user.id, "thr_thread_3", "msg_thread_3", 120.0, "Third", "four@example.com", "third", 1, 0, 0, 0, 1, 120.0),
                        (self.other.id, "thr_thread_other", "msg_thread_other", 999.0, "Other secret", "secret@example.com", "other-secret", 1, 1, 0, 0, 1, 999.0),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO content_objects (
                        content_sha256, object_kind, compression,
                        original_size_bytes, stored_size_bytes,
                        relative_path, verified_at, created_at
                    ) VALUES (%s, 'body_html', 'none', %s, %s, %s, 1, 1)
                    """,
                    (
                        self.body_digest,
                        len(self.cached_html),
                        len(self.cached_html),
                        f"{self.body_digest[:2]}/{self.body_digest}",
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO message_bodies (
                        message_id, user_uid, html_object_sha256, state,
                        body_size_bytes, index_version, parser_version,
                        checked_at, cached_at, last_accessed_at, updated_at
                    ) VALUES ('msg_thread_1b', %s, %s, 'ready', %s, 1, 1, 1, 1, 1, 1)
                    """,
                    (self.user.id, self.body_digest, len(self.cached_html)),
                )
                await cursor.execute(
                    """
                    INSERT INTO content_references (
                        id, user_uid, content_sha256, reference_kind,
                        reference_id, pinned, created_at, last_accessed_at
                    ) VALUES ('ref_thread_body', %s, %s, 'message_body_html',
                              'msg_thread_1b', 0, 1, 1)
                    """,
                    (self.user.id, self.body_digest),
                )
                await cursor.execute(
                    """
                    INSERT INTO message_attachments (
                        id, user_uid, message_id, remote_instance_id,
                        imap_part, filename, content_type, disposition,
                        remote_size_bytes, is_inline, cache_state,
                        created_at, updated_at
                    ) VALUES ('att_thread_1', %s, 'msg_thread_1b',
                              'ri_thread_1b_a', '2', 'report.pdf',
                              'application/pdf', 'attachment', 1234, 0,
                              'not_requested', 1, 1)
                    """,
                    (self.user.id,),
                )
                await cursor.execute(
                    """
                    INSERT INTO mail_operations (
                        id, user_uid, operation_type, target_type, target_id,
                        account_id, remote_instance_id, desired_state,
                        status, priority, available_at, idempotency_key,
                        created_at, updated_at
                    ) VALUES ('op_thread_pending', %s, 'star', 'message',
                              'msg_thread_1b', 'acc_thread_a', 'ri_thread_1b_a',
                              '{}', 'pending', 100, 0, 'thread-pending', 1, 1)
                    """,
                    (self.user.id,),
                )
            await connection.commit()

    @asynccontextmanager
    async def running_app(self):
        app = create_app(self.settings)
        async with app.router.lifespan_context(app):
            yield app

    def client(self, app, source: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app,
                raise_app_exceptions=False,
                client=(source, 443),
            ),
            base_url=ORIGIN,
        )

    async def login(self, client: httpx.AsyncClient, username: str, password: str) -> None:
        response = await client.post(
            "/api/v2/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200)

    async def test_thread_routes_require_authentication_and_reject_forged_cursor(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.70") as client:
                anonymous = await client.get("/api/v2/threads")
                await self.login(client, "thread-user", "ThreadPassword!123")
                forged = await client.get(
                    "/api/v2/threads",
                    params={"mailbox": "inbox", "cursor": "forged-position"},
                )
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(forged.status_code, 400)
        self.assertEqual(forged.json()["error"]["code"], "invalid_cursor")

    async def test_cursor_pagination_is_stable_bounded_and_filterable(self):
        recorded_queries: list[str] = []
        original_execute = aiomysql.cursors.Cursor.execute

        async def counted_execute(cursor, query, args=None):
            recorded_queries.append(" ".join(str(query).split()).casefold())
            return await original_execute(cursor, query, args)

        async with self.running_app() as app:
            async with self.client(app, "203.0.113.71") as client:
                await self.login(client, "thread-user", "ThreadPassword!123")
                with patch.object(aiomysql.cursors.Cursor, "execute", new=counted_execute):
                    first = await client.get(
                        "/api/v2/threads",
                        params={"mailbox": "inbox", "limit": 2},
                    )
                    second = await client.get(
                        "/api/v2/threads",
                        params={
                            "mailbox": "inbox",
                            "limit": 2,
                            "cursor": first.json()["next_cursor"],
                        },
                    )
                    account_filtered = await client.get(
                        "/api/v2/threads",
                        params={"mailbox": "inbox", "account_id": "acc_thread_b"},
                    )
                    label_filtered = await client.get(
                        "/api/v2/threads",
                        params={"mailbox": "inbox", "native_label": "mb_a_label"},
                    )
                    unread = await client.get(
                        "/api/v2/threads",
                        params={"mailbox": "inbox", "unread": "true"},
                    )
                    starred = await client.get(
                        "/api/v2/threads",
                        params={"mailbox": "inbox", "starred": "true"},
                    )
                    attachment = await client.get(
                        "/api/v2/threads",
                        params={"mailbox": "inbox", "has_attachment": "true"},
                    )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_ids = [item["id"] for item in first.json()["items"]]
        second_ids = [item["id"] for item in second.json()["items"]]
        self.assertEqual(first_ids, ["thr_thread_3", "thr_thread_2"])
        self.assertEqual(second_ids, ["thr_thread_1"])
        self.assertFalse(set(first_ids) & set(second_ids))
        self.assertEqual(
            [item["id"] for item in account_filtered.json()["items"]],
            ["thr_thread_3", "thr_thread_1"],
        )
        self.assertEqual(
            [item["id"] for item in label_filtered.json()["items"]],
            ["thr_thread_1"],
        )
        self.assertEqual(
            [item["id"] for item in unread.json()["items"]],
            ["thr_thread_2", "thr_thread_1"],
        )
        self.assertEqual(
            [item["id"] for item in starred.json()["items"]],
            ["thr_thread_1"],
        )
        self.assertEqual(
            [item["id"] for item in attachment.json()["items"]],
            ["thr_thread_1"],
        )
        rendered_queries = "\n".join(recorded_queries)
        self.assertNotIn(" offset ", rendered_queries)
        self.assertNotIn("message_bodies", rendered_queries)
        self.assertNotIn("body_search_documents", rendered_queries)
        self.assertTrue(
            any("force index (idx_thread_projection_cursor)" in query for query in recorded_queries),
            recorded_queries,
        )

    async def test_thread_detail_returns_timeline_sources_and_cache_states(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.72") as client:
                await self.login(client, "thread-user", "ThreadPassword!123")
                detail = await client.get("/api/v2/threads/thr_thread_1")
                missing = await client.get("/api/v2/threads/thr_thread_other")

        self.assertEqual(detail.status_code, 200)
        payload = detail.json()
        self.assertEqual(payload["id"], "thr_thread_1")
        self.assertEqual(
            [message["id"] for message in payload["messages"]],
            ["msg_thread_1a", "msg_thread_1b"],
        )
        latest = payload["messages"][1]
        self.assertEqual(latest["source_account_ids"], ["acc_thread_a", "acc_thread_b"])
        self.assertEqual(latest["body_state"], "ready")
        self.assertEqual(latest["attachments"][0]["filename"], "report.pdf")
        self.assertEqual(latest["operations"][0]["id"], "op_thread_pending")
        self.assertNotIn("body_html", json.dumps(payload))
        self.assertEqual(missing.status_code, 404)

    async def test_cached_body_streams_and_missing_body_reuses_one_priority_job(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.73") as client:
                await self.login(client, "thread-user", "ThreadPassword!123")
                cached = await client.get("/api/v2/messages/msg_thread_1b/body")
                first_missing = await client.get("/api/v2/messages/msg_thread_2/body")
                second_missing = await client.get("/api/v2/messages/msg_thread_2/body")
                other_missing = await client.get("/api/v2/messages/msg_thread_other/body")

        self.assertEqual(cached.status_code, 200)
        self.assertEqual(cached.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(cached.content, self.cached_html)
        self.assertEqual(first_missing.status_code, 202)
        self.assertEqual(first_missing.json()["state"], "queued")
        self.assertEqual(first_missing.json()["job_id"], second_missing.json()["job_id"])
        self.assertEqual(other_missing.status_code, 404)
        self.assertEqual(
            await self.scalar(
                """
                SELECT COUNT(*) FROM worker_jobs
                WHERE user_uid=%s AND job_kind='content.body'
                  AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.message_id'))='msg_thread_2'
                """,
                (self.user.id,),
            ),
            1,
        )
        priority = await self.scalar(
            """
            SELECT priority FROM worker_jobs
            WHERE user_uid=%s AND job_kind='content.body'
              AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.message_id'))='msg_thread_2'
            """,
            (self.user.id,),
        )
        self.assertEqual(int(priority), 10)

    async def test_missing_physical_body_object_is_requeued_without_stream_error(self):
        object_path = (
            self.settings.object_dir
            / self.body_digest[:2]
            / self.body_digest
        )
        object_path.unlink()

        async with self.running_app() as app:
            async with self.client(app, "203.0.113.74") as client:
                await self.login(client, "thread-user", "ThreadPassword!123")
                response = await client.get("/api/v2/messages/msg_thread_1b/body")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["state"], "queued")
        self.assertEqual(
            await self.scalar(
                "SELECT state FROM message_bodies WHERE message_id='msg_thread_1b' AND user_uid=%s",
                (self.user.id,),
            ),
            "queued",
        )
        self.assertEqual(
            await self.scalar(
                "SELECT COUNT(*) FROM worker_jobs WHERE user_uid=%s AND job_kind='content.body'",
                (self.user.id,),
            ),
            1,
        )

    async def test_core_thread_query_explain_uses_cursor_index_without_filesort(self):
        async with self.api_pool.acquire() as connection:
            async with connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    EXPLAIN
                    SELECT p.thread_id, p.latest_message_at
                    FROM thread_projections p FORCE INDEX (idx_thread_projection_cursor)
                    WHERE p.user_uid=%s AND p.semantic_mailbox=%s
                    ORDER BY p.latest_message_at DESC, p.thread_id DESC
                    LIMIT 20
                    """,
                    (self.user.id, "inbox"),
                )
                rows = [dict(row) for row in await cursor.fetchall()]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["key"], "idx_thread_projection_cursor")
        self.assertNotIn("filesort", str(rows[0].get("Extra") or "").casefold())
