from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import aiomysql
import httpx

from flymail.api.schemas.search import SearchFilter
from flymail.application.search_queries import SearchCompiler
from flymail.config import FlyMailSettings
from flymail.infrastructure.db.migrations.runner import run_migrations
from flymail.infrastructure.security.passwords import hash_password
from flymail.repositories.base import AdminContext, TenantContext
from flymail.repositories.users import UserRepository
from tests.v2.mysql_test_case import MySqlIsolatedAsyncioTestCase
from flymail.api.app import create_app


ORIGIN = "https://testserver"


class SearchApiTests(MySqlIsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await run_migrations(self.api_pool)
        await self._clear_tables()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="flymail-v2-search-api-")
        root = Path(self.temp_dir.name)
        self.settings = FlyMailSettings(
            role="api",
            database_url=self.database_url(),
            data_dir=root,
            object_dir=root / "objects" / "sha256",
            object_tmp_dir=root / "objects" / ".tmp",
            session_secret="search-api-session-secret-0123456789abcdef",
            db_pool_name="flymail-api",
            db_min_connections=2,
            db_max_connections=12,
        )
        self.user = await self._create_user("search-user", "SearchPassword!123")
        self.other = await self._create_user("search-other", "OtherPassword!123")
        await self._seed_data()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        await super().asyncTearDown()

    async def _clear_tables(self) -> None:
        tables = (
            "search_history", "saved_searches", "body_search_documents",
            "worker_jobs", "job_attempts", "thread_projections", "thread_messages",
            "message_memberships", "message_remote_instances", "messages", "threads",
            "mailboxes", "contacts", "account_runtime_state", "mail_identities",
            "mail_accounts", "login_rate_limits", "user_sessions", "user_profiles",
            "user_settings", "users", "process_heartbeats",
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
                AdminContext("usr_search_test_admin"),
                username=username,
                password_hash=hash_password(password),
            )
            await connection.commit()
        return user

    async def _seed_data(self) -> None:
        async with self.api_pool.acquire() as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO mail_accounts (
                        id, user_uid, provider_key, email, normalized_email,
                        display_name, status, poll_interval_seconds, created_at, updated_at
                    ) VALUES (%s, %s, 'gmail', %s, %s, %s, 'active', 300, 1, 1)
                    """,
                    (
                        ("acc_search_a", self.user.id, "alpha@example.com", "alpha@example.com", "Alpha"),
                        ("acc_search_b", self.user.id, "beta@example.com", "beta@example.com", "Beta"),
                        ("acc_search_other", self.other.id, "other@example.com", "other@example.com", "Other"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO mail_identities (
                        id, user_uid, account_id, from_address,
                        normalized_from_address, display_name, is_default,
                        is_verified, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 1, 1, 1, 1)
                    """,
                    (
                        ("ident_search_a", self.user.id, "acc_search_a", "alpha@example.com", "alpha@example.com", "Alpha Identity"),
                        ("ident_search_other", self.other.id, "acc_search_other", "other@example.com", "other@example.com", "Other Identity"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO contacts (
                        id, user_uid, display_name, normalized_name,
                        primary_email, normalized_email, emails_json,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 1, 1)
                    """,
                    (
                        ("contact_search_alice", self.user.id, "Alice Contact", "alice contact", "alice@example.com", "alice@example.com", '["alice@example.com"]'),
                        ("contact_search_other", self.other.id, "Alice Secret", "alice secret", "secret@example.com", "secret@example.com", '["secret@example.com"]'),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO mailboxes (
                        id, user_uid, account_id, native_key, native_name,
                        semantic_key, mailbox_type, sync_status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'ready', 1, 1)
                    """,
                    (
                        ("mb_search_a", self.user.id, "acc_search_a", "INBOX", "Inbox", "inbox", "folder"),
                        ("mb_search_label", self.user.id, "acc_search_a", "Label/Project", "Project", "custom", "label"),
                        ("mb_search_b", self.user.id, "acc_search_b", "INBOX", "Inbox", "inbox", "folder"),
                        ("mb_search_other", self.other.id, "acc_search_other", "INBOX", "Inbox", "inbox", "folder"),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO threads (
                        id, user_uid, canonical_thread_key, normalized_subject,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, 1, %s)
                    """,
                    (
                        ("thr_search_1", self.user.id, "search-1", "quarterly report", 100.0),
                        ("thr_search_2", self.user.id, "search-2", "project update", 100.0),
                        ("thr_search_3", self.user.id, "search-3", "small note", 120.0),
                        ("thr_search_other", self.other.id, "search-other", "other secret", 999.0),
                    ),
                )
                messages = (
                    ("msg_search_1a", self.user.id, "key-1a", "thr_search_1", "Quarterly Report", '["alice@example.com"]', '["search-user@example.com"]', 90.0, 1, 2048, "old report"),
                    ("msg_search_1b", self.user.id, "key-1b", "thr_search_1", "Quarterly Report follow-up", '["bob@example.com"]', '["search-user@example.com"]', 100.0, 1, 4096, "latest report"),
                    ("msg_search_2", self.user.id, "key-2", "thr_search_2", "Project Update", '["carol@example.com"]', '["search-user@example.com"]', 100.0, 0, 512, "project"),
                    ("msg_search_3", self.user.id, "key-3", "thr_search_3", "Small Note", '["alice@example.com"]', '["search-user@example.com"]', 120.0, 0, 128, "note"),
                    ("msg_search_other", self.other.id, "key-other", "thr_search_other", "Other Secret", '["secret@example.com"]', '["other@example.com"]', 999.0, 0, 999, "secret"),
                )
                await cursor.executemany(
                    """
                    INSERT INTO messages (
                        id, user_uid, canonical_message_key, thread_id, subject,
                        normalized_subject, from_json, to_json, received_at,
                        sent_at, size_bytes, has_attachments, snippet,
                        body_state, search_state, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, LOWER(%s), %s, %s, %s,
                              %s, %s, %s, %s, 'ready', 'ready', 1, 1)
                    """,
                    tuple(
                        (mid, uid, key, tid, subject, subject, from_json, to_json,
                         received, received, size, attachment, snippet)
                        for mid, uid, key, tid, subject, from_json, to_json,
                            received, attachment, size, snippet in messages
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
                        ("thr_search_1", "msg_search_1a", self.user.id, 1),
                        ("thr_search_1", "msg_search_1b", self.user.id, 2),
                        ("thr_search_2", "msg_search_2", self.user.id, 1),
                        ("thr_search_3", "msg_search_3", self.user.id, 1),
                        ("thr_search_other", "msg_search_other", self.other.id, 1),
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
                        ("ri_search_1a", self.user.id, "acc_search_a", "mb_search_a", "msg_search_1a", 11, 0, 0),
                        ("ri_search_1b", self.user.id, "acc_search_a", "mb_search_a", "msg_search_1b", 12, 1, 1),
                        ("ri_search_2", self.user.id, "acc_search_a", "mb_search_a", "msg_search_2", 13, 0, 0),
                        ("ri_search_3", self.user.id, "acc_search_b", "mb_search_b", "msg_search_3", 21, 1, 0),
                        ("ri_search_other", self.other.id, "acc_search_other", "mb_search_other", "msg_search_other", 99, 0, 0),
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
                        ("ri_search_1a", "mb_search_a", self.user.id, "folder", ""),
                        ("ri_search_1b", "mb_search_a", self.user.id, "folder", ""),
                        ("ri_search_1b", "mb_search_label", self.user.id, "label", "Project"),
                        ("ri_search_2", "mb_search_a", self.user.id, "folder", ""),
                        ("ri_search_3", "mb_search_b", self.user.id, "folder", ""),
                        ("ri_search_other", "mb_search_other", self.other.id, "folder", ""),
                    ),
                )
                await cursor.executemany(
                    """
                    INSERT INTO body_search_documents (
                        message_id, user_uid, thread_id, subject_text,
                        participants_text, body_text, language,
                        index_version, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'en', 1, 1)
                    """,
                    (
                        ("msg_search_1b", self.user.id, "thr_search_1", "Quarterly Report follow-up", "bob@example.com", "needleword appears in the cached body"),
                        ("msg_search_2", self.user.id, "thr_search_2", "Project Update", "carol@example.com", "ordinary indexed text"),
                        ("msg_search_other", self.other.id, "thr_search_other", "Other Secret", "secret@example.com", "needleword belongs to another tenant"),
                    ),
                )
            await connection.commit()

    @asynccontextmanager
    async def running_app(self):
        app = create_app(self.settings)
        async with app.router.lifespan_context(app):
            yield app

    def client(self, app, source: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False, client=(source, 443)),
            base_url=ORIGIN,
        )

    async def login(self, client: httpx.AsyncClient, username: str, password: str) -> str:
        response = await client.post("/api/v2/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200)
        return str(response.json()["csrf_token"])

    @staticmethod
    def csrf_headers(token: str) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": token}

    async def test_ascii_keyword_uses_standard_fulltext_index(self):
        compiled = SearchCompiler().compile(
            TenantContext(self.user.id),
            SearchFilter(keyword="capacitybucket0000"),
            position=None,
            limit=20,
        )
        self.assertIn(
            "JOIN body_search_documents doc FORCE INDEX (ft_body_search_standard)",
            " ".join(compiled.sql.split()),
        )

    async def test_cjk_keyword_uses_ngram_fulltext_index(self):
        compiled = SearchCompiler().compile(
            TenantContext(self.user.id),
            SearchFilter(keyword="中文测试"),
            position=None,
            limit=20,
        )
        self.assertIn(
            "JOIN body_search_documents doc FORCE INDEX (ft_body_search)",
            " ".join(compiled.sql.split()),
        )

    async def test_structural_search_is_parameterized_tenant_scoped_and_local_only(self):
        captured: list[tuple[str, object]] = []
        original = aiomysql.cursors.Cursor.execute

        async def recording(cursor, query, args=None):
            captured.append((" ".join(str(query).split()), args))
            return await original(cursor, query, args)

        async with self.running_app() as app:
            async with self.client(app, "203.0.113.90") as client:
                await self.login(client, "search-user", "SearchPassword!123")
                with patch.object(aiomysql.cursors.Cursor, "execute", new=recording):
                    response = await client.post(
                        "/api/v2/search",
                        json={
                            "filters": {
                                "account_ids": ["acc_search_a"],
                                "has_attachment": True,
                                "min_size_bytes": 1000,
                            },
                            "limit": 20,
                        },
                    )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["thread_id"] for item in response.json()["items"]], ["thr_search_1"])
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 0)
        search_sql = next(query for query, _args in captured if "WITH matched AS" in query)
        self.assertIn("m.user_uid = %s", search_sql)
        self.assertNotIn("acc_search_a", search_sql)
        self.assertTrue(any("acc_search_a" in tuple(map(str, args or ())) for _query, args in captured))

    async def test_fulltext_body_match_disappears_after_search_document_eviction(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.91") as client:
                await self.login(client, "search-user", "SearchPassword!123")
                first = await client.post(
                    "/api/v2/search",
                    json={"filters": {"keyword": "needleword"}, "limit": 20},
                )
                async with self.api_pool.acquire() as connection:
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            "DELETE FROM body_search_documents WHERE message_id='msg_search_1b' AND user_uid=%s",
                            (self.user.id,),
                        )
                    await connection.commit()
                second = await client.post(
                    "/api/v2/search",
                    json={"filters": {"keyword": "needleword"}, "limit": 20},
                )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()["items"]), 1)
        self.assertEqual(first.json()["items"][0]["thread_id"], "thr_search_1")
        self.assertEqual(first.json()["items"][0]["matched_message_id"], "msg_search_1b")
        self.assertEqual(first.json()["items"][0]["matched_field"], "body")
        self.assertIn(first.json()["fulltext_parser"], {"standard", "ngram", "hybrid"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["items"], [])
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM worker_jobs"), 0)

    async def test_cursor_pagination_aggregates_by_thread_without_duplicates(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.92") as client:
                await self.login(client, "search-user", "SearchPassword!123")
                first = await client.post(
                    "/api/v2/search",
                    json={"filters": {"from_addresses": ["alice@example.com"]}, "limit": 1},
                )
                second = await client.post(
                    "/api/v2/search",
                    json={
                        "filters": {"from_addresses": ["alice@example.com"]},
                        "limit": 1,
                        "cursor": first.json()["next_cursor"],
                    },
                )
                forged = await client.post(
                    "/api/v2/search",
                    json={"filters": {"from_addresses": ["alice@example.com"]}, "cursor": "forged"},
                )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual([item["thread_id"] for item in first.json()["items"]], ["thr_search_3"])
        self.assertEqual([item["thread_id"] for item in second.json()["items"]], ["thr_search_1"])
        self.assertEqual(forged.status_code, 400)
        self.assertEqual(forged.json()["error"]["code"], "invalid_cursor")

    async def test_suggestions_and_history_are_bounded_and_tenant_scoped(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.93") as client:
                csrf = await self.login(client, "search-user", "SearchPassword!123")
                for index in range(55):
                    response = await client.post(
                        "/api/v2/search",
                        json={"filters": {"keyword": f"historyword{index}"}},
                    )
                    self.assertEqual(response.status_code, 200)
                suggestions = await client.get("/api/v2/search/suggestions", params={"q": "ali"})
                history = await client.get("/api/v2/search/history")
                cleared = await client.delete(
                    "/api/v2/search/history",
                    headers=self.csrf_headers(csrf),
                )
                empty = await client.get("/api/v2/search/history")
        self.assertEqual(suggestions.status_code, 200)
        rendered = json.dumps(suggestions.json(), ensure_ascii=False)
        self.assertIn("alice@example.com", rendered)
        self.assertNotIn("secret@example.com", rendered)
        self.assertEqual(history.status_code, 200)
        self.assertLessEqual(len(history.json()["items"]), 20)
        self.assertLessEqual(
            int(await self.scalar("SELECT COUNT(*) FROM search_history WHERE user_uid=%s", (self.user.id,))),
            50,
        )
        self.assertEqual(cleared.status_code, 204)
        self.assertEqual(empty.json()["items"], [])

    async def test_saved_search_crud_stores_only_validated_structured_json(self):
        async with self.running_app() as app:
            async with self.client(app, "203.0.113.94") as client:
                csrf = await self.login(client, "search-user", "SearchPassword!123")
                created = await client.post(
                    "/api/v2/saved-searches",
                    headers=self.csrf_headers(csrf),
                    json={
                        "name": "Unread Project",
                        "filters": {
                            "keyword": "project",
                            "is_read": False,
                            "label_ids": ["mb_search_label"],
                        },
                        "is_pinned": True,
                    },
                )
                invalid = await client.post(
                    "/api/v2/saved-searches",
                    headers=self.csrf_headers(csrf),
                    json={
                        "name": "Unsafe",
                        "filters": {"raw_sql": "user_uid = other"},
                    },
                )
                listing = await client.get("/api/v2/saved-searches")
                saved_id = created.json()["id"]
                stored_filters = await self.scalar(
                    "SELECT filters_json FROM saved_searches WHERE id=%s AND user_uid=%s",
                    (saved_id, self.user.id),
                )
                updated = await client.patch(
                    f"/api/v2/saved-searches/{saved_id}",
                    headers=self.csrf_headers(csrf),
                    json={"name": "Unread Project Updated", "is_pinned": False},
                )
                deleted = await client.delete(
                    f"/api/v2/saved-searches/{saved_id}",
                    headers=self.csrf_headers(csrf),
                )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual([item["id"] for item in listing.json()["items"]], [saved_id])
        decoded_filters = (
            json.loads(stored_filters) if isinstance(stored_filters, str) else stored_filters
        )
        self.assertEqual(decoded_filters["keyword"], "project")
        self.assertEqual(decoded_filters["label_ids"], ["mb_search_label"])
        self.assertNotIn("raw_sql", decoded_filters)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["name"], "Unread Project Updated")
        self.assertEqual(deleted.status_code, 204)
        raw = await self.scalar("SELECT filters_json FROM saved_searches WHERE id=%s", (saved_id,))
        self.assertIsNone(raw)
