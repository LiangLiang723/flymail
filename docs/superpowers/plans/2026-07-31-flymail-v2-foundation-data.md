# FlyMail V2 基础设施与数据层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 FlyMail V2 的最终 Python 包结构、配置系统、MySQL 连接池、迁移框架、核心数据表、事务边界、凭证加密、内容寻址对象存储、Repository 和可靠任务原语。

**Architecture:** 新代码进入 `backend/flymail/`，开发期间通过独立入口和独立测试数据库运行，不替换现有 `backend/main.py`。MySQL 是唯一可靠状态核心；Application Service 控制事务，Repository 不隐式提交；业务写入和 Outbox 同事务；大正文和附件通过本地 SHA-256 对象存储保存。

**Tech Stack:** Python 3、FastAPI、Pydantic 2、aiomysql、MySQL 8.0、cryptography、unittest、Docker。

## Global Constraints

- 继承 `2026-07-31-flymail-v2-roadmap.md` 的全部约束。
- 当前生产容器和 `/Docker/flymail/data` 不得被本计划修改。
- 测试数据库必须是独立空库，默认名称前缀为 `flymail_v2_test_`。
- 不使用 SQLite 代替 MySQL 特性测试。
- Repository 不调用 `commit()` 或 `rollback()`；事务只能由 Unit of Work 控制。
- 所有租户业务查询必须接收 `user_uid`。
- 凭证加密使用现有 `cryptography` 依赖，不新增加密库。
- 对象存储写入顺序为临时文件、流式哈希、`fsync`、原子重命名、数据库引用。
- 不保存手工引用计数；回收对象前查询真实引用。
- 本计划不实现 IMAP、SMTP、HTTP 业务路由或前端页面。

## File Map

**Create:**

- `backend/flymail/__init__.py`：V2 包标识。
- `backend/flymail/config.py`：API、Worker、数据库、对象存储和安全配置模型。
- `backend/flymail/domain/ids.py`：稳定 ID 类型和生成函数。
- `backend/flymail/domain/enums.py`：共享状态枚举。
- `backend/flymail/domain/errors.py`：领域和基础设施错误。
- `backend/flymail/infrastructure/db/pool.py`：API 与 Worker 独立连接池。
- `backend/flymail/infrastructure/db/uow.py`：显式事务和连接上下文。
- `backend/flymail/infrastructure/db/migrations/runner.py`：迁移发现、锁定和执行。
- `backend/flymail/infrastructure/db/migrations/v0001_identity.py`
- `backend/flymail/infrastructure/db/migrations/v0002_mail.py`
- `backend/flymail/infrastructure/db/migrations/v0003_jobs.py`
- `backend/flymail/infrastructure/db/migrations/v0004_content_search.py`
- `backend/flymail/infrastructure/security/passwords.py`
- `backend/flymail/infrastructure/security/credentials.py`
- `backend/flymail/infrastructure/security/sessions.py`
- `backend/flymail/infrastructure/object_store/models.py`
- `backend/flymail/infrastructure/object_store/store.py`
- `backend/flymail/infrastructure/object_store/quota.py`
- `backend/flymail/repositories/base.py`
- `backend/flymail/repositories/users.py`
- `backend/flymail/repositories/accounts.py`
- `backend/flymail/repositories/settings.py`
- `backend/flymail/repositories/objects.py`
- `backend/flymail/repositories/jobs.py`
- `backend/flymail/repositories/outbox.py`
- `backend/flymail/application/uow.py`
- `backend/flymail/workers/lease.py`
- `backend/v2_dev.py`：V2 API 开发入口，占位健康接口只用于集成测试。
- `backend/v2_worker.py`：V2 Worker 开发入口，只运行心跳和任务租约循环。
- `backend/tests/v2/mysql_test_case.py`：真实 MySQL 测试辅助。
- `backend/tests/v2/test_config.py`
- `backend/tests/v2/test_migrations.py`
- `backend/tests/v2/test_uow.py`
- `backend/tests/v2/test_security.py`
- `backend/tests/v2/test_object_store.py`
- `backend/tests/v2/test_repositories.py`
- `backend/tests/v2/test_jobs_outbox.py`

**Modify:**

- `.gitignore`：忽略 V2 本地临时数据库配置、对象目录和测试输出，不忽略源码或固定测试夹具。
- `backend/requirements.txt`：本计划预计不修改；只有测试证明现有依赖无法满足时才重新评审。
- `README.md`：本计划末尾仅增加“V2 开发中，不影响当前部署”的开发说明，不改生产部署步骤。

---

### Task 1: 建立 V2 包、配置和测试入口

**Files:**

- Create: `backend/flymail/__init__.py`
- Create: `backend/flymail/config.py`
- Create: `backend/flymail/domain/ids.py`
- Create: `backend/flymail/domain/enums.py`
- Create: `backend/flymail/domain/errors.py`
- Create: `backend/v2_dev.py`
- Create: `backend/v2_worker.py`
- Create: `backend/tests/v2/test_config.py`

**Interfaces:**

- Produces: `FlyMailSettings.from_env(role: Literal["api", "worker"]) -> FlyMailSettings`
- Produces: `new_id(prefix: str) -> str`
- Produces enums: `JobStatus`, `OperationStatus`, `BodyCacheState`, `ObjectKind`, `AccountRuntimeStatus`.
- Produces errors: `ConfigurationError`, `NotFoundError`, `ConflictError`, `AuthorizationError`, `RetryableError`, `PermanentError`.

- [x] **Step 1: Write failing configuration tests**

Create `backend/tests/v2/test_config.py`:

```python
import os
import unittest
from unittest.mock import patch

from flymail.config import FlyMailSettings
from flymail.domain.ids import new_id


class FlyMailSettingsTests(unittest.TestCase):
    def test_api_and_worker_use_distinct_pool_names(self):
        env = {
            "DATABASE_URL": "mysql://flymail:test@127.0.0.1:3306/flymail_v2_test",
            "FLYMAIL_SESSION_SECRET": "x" * 32,
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-data",
        }
        with patch.dict(os.environ, env, clear=True):
            api = FlyMailSettings.from_env("api")
            worker = FlyMailSettings.from_env("worker")
        self.assertEqual(api.db_pool_name, "flymail-api")
        self.assertEqual(worker.db_pool_name, "flymail-worker")
        self.assertNotEqual(api.db_max_connections, worker.db_max_connections)

    def test_short_session_secret_is_rejected(self):
        env = {
            "DATABASE_URL": "mysql://flymail:test@127.0.0.1:3306/flymail_v2_test",
            "FLYMAIL_SESSION_SECRET": "short",
            "FLYMAIL_DATA_DIR": "/tmp/flymail-v2-data",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "at least 16"):
                FlyMailSettings.from_env("api")

    def test_generated_ids_include_prefix_and_are_unique(self):
        first = new_id("usr")
        second = new_id("usr")
        self.assertTrue(first.startswith("usr_"))
        self.assertNotEqual(first, second)
```

- [x] **Step 2: Run the test and verify imports fail**

Run:

```bash
cd backend
python -m unittest tests.v2.test_config -v
```

Expected: FAIL because `flymail.config` and `flymail.domain.ids` do not exist.

- [x] **Step 3: Implement explicit settings**

Implement immutable `FlyMailSettings` with these required fields:

```python
@dataclass(frozen=True, slots=True)
class FlyMailSettings:
    role: Literal["api", "worker"]
    database_url: str
    data_dir: Path
    object_dir: Path
    object_tmp_dir: Path
    session_secret: str
    db_pool_name: str
    db_min_connections: int
    db_max_connections: int
    worker_heartbeat_seconds: int = 10
    job_lease_seconds: int = 60
    default_body_quota_bytes: int = 5 * 1024**3
```

Defaults:

- API pool: min `2`, max `12`.
- Worker pool: min `2`, max `8`.
- `object_dir = data_dir / "objects" / "sha256"`.
- `object_tmp_dir = data_dir / "objects" / ".tmp"`.
- reject missing database URL, missing data directory, and session secret shorter than 16 characters.
- never include the raw database URL or session secret in `repr`.

- [x] **Step 4: Add ID and enum contracts**

Use URL-safe random UUID-derived IDs:

```python
def new_id(prefix: str) -> str:
    normalized = prefix.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,15}", normalized):
        raise ValueError("invalid id prefix")
    return f"{normalized}_{uuid.uuid4().hex}"
```

Define string enums with exact values used throughout all child plans:

```text
JobStatus: pending, leased, running, succeeded, retry_wait, failed, cancelled
OperationStatus: pending, applying, synced, retry_wait, review_required, conflict, failed, cancelled
BodyCacheState: not_requested, queued, fetching, ready, evicted, failed, unavailable
ObjectKind: body_html, body_text, inline_image, attachment, raw_eml, draft_attachment, user_avatar, account_icon, contact_avatar, notification_asset
AccountRuntimeStatus: active, normal, quiet, degraded, auth_required, disabled
```

- [x] **Step 5: Add development entrypoints**

`backend/v2_dev.py` must create a minimal FastAPI app with `/api/v2/health` returning role and version. It must not import legacy routes.

`backend/v2_worker.py` must load settings and exit with a clear configuration error until the database heartbeat service exists. It must not silently start an empty infinite loop.

- [x] **Step 6: Run narrow tests**

Run:

```bash
cd backend
python -m unittest tests.v2.test_config -v
```

Expected: all tests PASS.

- [x] **Step 7: Commit**

```bash
git add backend/flymail backend/v2_dev.py backend/v2_worker.py backend/tests/v2/test_config.py
git commit -m "🏗️ 建立 V2 后端包与配置契约"
```

---

### Task 2: 实现 API 与 Worker 独立 MySQL 连接池和 Unit of Work

**Files:**

- Create: `backend/flymail/infrastructure/db/pool.py`
- Create: `backend/flymail/infrastructure/db/uow.py`
- Create: `backend/flymail/application/uow.py`
- Create: `backend/tests/v2/mysql_test_case.py`
- Create: `backend/tests/v2/test_uow.py`

**Interfaces:**

- Produces: `DatabasePool.create(settings: FlyMailSettings) -> DatabasePool`
- Produces: `DatabasePool.acquire() -> AsyncContextManager[aiomysql.Connection]`
- Produces: `DatabasePool.close() -> Awaitable[None]`
- Produces: `SqlUnitOfWork(pool: DatabasePool)` with `__aenter__`, `commit`, `rollback`, `connection`.
- Produces: `ApplicationUnitOfWork` protocol consumed by application services.

- [x] **Step 1: Write transaction rollback and pool isolation tests**

Create tests that use a temporary table in a dedicated MySQL database:

```python
class SqlUnitOfWorkTests(MySqlIsolatedAsyncioTestCase):
    async def test_uncommitted_insert_rolls_back_on_exit(self):
        async with SqlUnitOfWork(self.pool) as uow:
            await execute(uow.connection, "INSERT INTO uow_probe(value) VALUES (%s)", ("discard",))
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM uow_probe"), 0)

    async def test_explicit_commit_persists(self):
        async with SqlUnitOfWork(self.pool) as uow:
            await execute(uow.connection, "INSERT INTO uow_probe(value) VALUES (%s)", ("keep",))
            await uow.commit()
        self.assertEqual(await self.scalar("SELECT COUNT(*) FROM uow_probe"), 1)

    async def test_api_and_worker_pools_have_distinct_names(self):
        self.assertNotEqual(self.api_pool.name, self.worker_pool.name)
```

- [x] **Step 2: Run tests and confirm missing contracts**

Run:

```bash
cd backend
FLYMAIL_TEST_DATABASE_URL='mysql://...' python -m unittest tests.v2.test_uow -v
```

Expected: FAIL because pool and UoW modules do not exist.

- [x] **Step 3: Implement URL parsing and safe logging**

Parse the MySQL URL with `urllib.parse`. Store password separately and expose only:

```python
def redacted_database_url(url: str) -> str:
    return "mysql://<user>:***@<host>:<port>/<database>"
```

Do not log the original URL, query string credentials, or decoded password.

- [x] **Step 4: Implement connection pool**

Use `aiomysql.create_pool` with:

- `autocommit=False`;
- `charset="utf8mb4"`;
- `pool_recycle=1800`;
- role-specific `minsize` and `maxsize`;
- `init_command="SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED"`.

Every acquired connection must be rolled back before returning to the pool if a transaction remains open.

- [x] **Step 5: Implement Unit of Work**

Required behavior:

```python
async with SqlUnitOfWork(pool) as uow:
    users = UserRepository(uow.connection)
    ...
    await uow.commit()
```

- `__aenter__` begins an explicit transaction.
- leaving without `commit()` rolls back.
- exceptions always roll back.
- `commit()` may be called once; subsequent calls raise `RuntimeError`.
- network operations are forbidden inside UoW by code review and application service structure.

- [x] **Step 6: Run tests**

Run the exact Task 2 test module and verify PASS.

- [x] **Step 7: Commit**

```bash
git add backend/flymail/infrastructure/db backend/flymail/application/uow.py backend/tests/v2/mysql_test_case.py backend/tests/v2/test_uow.py
git commit -m "🗄️ 建立 V2 数据库连接池与事务边界"
```

---

### Task 3: 建立版本化迁移框架和完整初始表结构

**Files:**

- Create: `backend/flymail/infrastructure/db/migrations/runner.py`
- Create: `backend/flymail/infrastructure/db/migrations/v0001_identity.py`
- Create: `backend/flymail/infrastructure/db/migrations/v0002_mail.py`
- Create: `backend/flymail/infrastructure/db/migrations/v0003_jobs.py`
- Create: `backend/flymail/infrastructure/db/migrations/v0004_content_search.py`
- Create: `backend/tests/v2/test_migrations.py`

**Interfaces:**

- Produces: `Migration(version: int, name: str, statements: tuple[str, ...])`
- Produces: `run_migrations(pool: DatabasePool) -> list[int]`
- Produces: `current_schema_version(connection) -> int`
- Produces schema version `5` from an empty database.

- [x] **Step 1: Write empty-database migration tests**

Tests must:

1. drop and recreate an isolated test database;
2. run all migrations;
3. assert schema version is `5`;
4. run migrations again and assert no changes;
5. verify all required tables exist;
6. create two concurrent migration runners and assert only one applies each version.

Required table set:

```python
EXPECTED_TABLES = {
    "schema_migrations",
    "users", "user_profiles", "user_sessions", "user_settings", "audit_events",
    "contacts", "authorized_storage_roots",
    "mail_accounts", "mail_identities", "provider_credentials",
    "oauth_authorization_states", "outbound_proxy_configs",
    "mailboxes", "messages", "message_headers", "message_remote_instances",
    "message_memberships", "threads", "thread_messages", "thread_projections",
    "message_bodies", "message_attachments",
    "content_objects", "content_references", "body_search_documents",
    "mail_operations", "outbox_events", "worker_jobs", "job_attempts",
    "sync_cursors", "account_runtime_state", "realtime_events",
    "notification_channels", "notification_rules", "notification_image_publishers",
    "notification_events", "notification_deliveries",
    "drafts", "draft_recipients", "draft_attachments", "send_attempts",
    "saved_searches", "search_history", "backup_jobs",
}
```

- [x] **Step 2: Verify failure before implementation**

Run `tests.v2.test_migrations`; expected import or missing-table failures.

- [x] **Step 3: Implement migration locking**

Use MySQL advisory lock:

```sql
SELECT GET_LOCK('flymail_v2_schema_migration', 30)
```

Release with `RELEASE_LOCK` in `finally`. Create `schema_migrations` before discovering current version. Each migration version is inserted in the same transaction as its statements.

- [x] **Step 4: Implement identity migration**

`v0001_identity.py` creates:

- users with unique username, password hash, role, enabled, password version and timestamps;
- user profiles with nickname and avatar object reference;
- sessions with token hash, user, expiry, revoked timestamp and last seen;
- user settings with body and attachment quotas;
- contacts and administrator-authorized `/data` storage roots;
- accounts, identities, signature fields and encrypted provider credentials;
- single-use OAuth authorization states with encrypted PKCE verifier, session binding and expiry;
- user/account outbound proxy configuration with encrypted credentials and explicit traffic scope;
- notification channel/rule/image-publisher configuration with encrypted secret fields;
- audit events.

All IDs use `VARCHAR(64)` ASCII collation. Email display strings use `utf8mb4`.

- [x] **Step 5: Implement mail migration**

`v0002_mail.py` creates mailboxes, messages, headers, remote instances, memberships, threads, thread messages and projections.

Critical unique keys:

```text
mail_accounts(user_uid, normalized_email)
mail_identities(account_id, normalized_from_address)
mailboxes(account_id, native_key)
message_remote_instances(account_id, mailbox_id, uidvalidity, remote_uid)
messages(user_uid, canonical_message_key)
thread_messages(thread_id, message_id)
message_memberships(remote_instance_id, mailbox_id)
```

Critical cursor index:

```text
thread_projections(user_uid, semantic_mailbox, latest_message_at DESC, thread_id DESC)
```

- [x] **Step 6: Implement jobs migration**

`v0003_jobs.py` creates operations, outbox, jobs, attempts, cursors, runtime state, realtime events, notification events/deliveries, drafts and sending tables. OAuth states, proxy configurations, notification channels/rules and image publishers remain identity/configuration tables created by `v0001_identity.py`; job payloads reference their IDs and never duplicate encrypted secrets.

Task claim index must begin with:

```text
worker_jobs(queue_name, status, available_at, priority, id)
```

Outbox unpublished index:

```text
outbox_events(published_at, created_at, id)
```

- [x] **Step 7: Implement content and search migration**

`v0004_content_search.py` creates content objects, references, bodies, attachment metadata, FULLTEXT search documents, saved searches, search history and backup jobs.

Create MySQL FULLTEXT index over normalized subject, participants and cached body text. Chinese search uses MySQL ngram parser only after a container capability test confirms it is installed; otherwise create the standard FULLTEXT index and record the limitation for the search plan. Do not silently issue unsupported SQL.

- [x] **Step 8: Verify indexes with information_schema**

Tests assert named indexes and exact column order for list, jobs, remote identity and content reference queries.

- [x] **Step 9: Run migration tests twice**

Run:

```bash
cd backend
FLYMAIL_TEST_DATABASE_URL='mysql://...' python -m unittest tests.v2.test_migrations -v
```

Expected: first run applies versions 1–5; second run applies none; all tests PASS.

- [x] **Step 10: Commit**

```bash
git add backend/flymail/infrastructure/db/migrations backend/tests/v2/test_migrations.py
git commit -m "🗃️ 建立 V2 空库迁移与核心表结构"
```

---

### Task 4: 实现密码、会话和邮箱凭证安全原语

**Files:**

- Create: `backend/flymail/infrastructure/security/passwords.py`
- Create: `backend/flymail/infrastructure/security/credentials.py`
- Create: `backend/flymail/infrastructure/security/sessions.py`
- Create: `backend/tests/v2/test_security.py`

**Interfaces:**

- Produces: `hash_password(password: str) -> str`
- Produces: `verify_password(password: str, encoded: str) -> bool`
- Produces: `CredentialCipher.from_master_secret(secret: str, key_version: int = 1)`
- Produces: `CredentialCipher.encrypt(account_id: str, plaintext: bytes) -> EncryptedValue`
- Produces: `CredentialCipher.decrypt(account_id: str, value: EncryptedValue) -> bytes`
- Produces: `new_session_token() -> tuple[raw_token: str, token_hash: str]`
- Produces: `sign_session_cookie(session_id: str, secret: bytes) -> str`
- Produces: `verify_session_cookie(cookie: str, secret: bytes) -> str`.

- [x] **Step 1: Write security tests**

Tests cover:

- two hashes for the same password differ and both verify;
- wrong password fails;
- credential ciphertext differs for the same plaintext because nonce is random;
- ciphertext is bound to `account_id` as authenticated additional data;
- wrong account or wrong master secret fails with authentication error;
- encrypted value stores algorithm and key version;
- session raw token never equals stored token hash;
- log-safe representations never include plaintext.

- [x] **Step 2: Run tests and verify missing modules**

Expected: FAIL.

- [x] **Step 3: Implement password hashing**

Use `cryptography.hazmat.primitives.kdf.scrypt.Scrypt` with per-password random salt. Store an encoded format:

```text
scrypt$v=1$n=32768$r=8$p=1$<salt-base64>$<digest-base64>
```

Use constant-time comparison. Reject passwords shorter than 10 characters at application validation; the hashing function itself accepts any non-empty string so imported administrative workflows remain explicit.

- [x] **Step 4: Implement credential encryption**

Use HKDF-SHA256 to derive a 32-byte credential key from `FLYMAIL_SESSION_SECRET` with info `b"flymail-v2/credentials/v1"`, then AES-256-GCM with random 12-byte nonce.

`EncryptedValue` exact fields:

```python
@dataclass(frozen=True, slots=True)
class EncryptedValue:
    algorithm: str
    key_version: int
    nonce_b64: str
    ciphertext_b64: str
```

AAD is `f"flymail:{account_id}:provider-credential:v1".encode()`.

- [x] **Step 5: Implement session token primitives**

Generate 32 random bytes. Store SHA-256 hash only. Cookie signing and verification use a separately HKDF-derived HMAC key with info `b"flymail-v2/session-signing/v1"`.

- [x] **Step 6: Run tests**

Run `tests.v2.test_security`; expected PASS.

- [x] **Step 7: Commit**

```bash
git add backend/flymail/infrastructure/security backend/tests/v2/test_security.py
git commit -m "🔒 建立 V2 密码会话与凭证加密原语"
```

---

### Task 5: 实现内容寻址对象存储和真实引用回收

**Files:**

- Create: `backend/flymail/infrastructure/object_store/models.py`
- Create: `backend/flymail/infrastructure/object_store/store.py`
- Create: `backend/flymail/infrastructure/object_store/quota.py`
- Create: `backend/flymail/repositories/objects.py`
- Create: `backend/tests/v2/test_object_store.py`

**Interfaces:**

- Produces: `ObjectStore.put_stream(kind: ObjectKind, chunks: AsyncIterable[bytes], expected_size: int | None = None) -> StoredObject`
- Produces: `ObjectStore.open(content_sha256: str) -> AsyncContextManager[BinaryIO]`
- Produces: `ObjectStore.verify(content_sha256: str) -> ObjectVerification`
- Produces: `ObjectStore.remove_unreferenced(content_sha256: str, repository: ObjectRepository) -> bool`
- Produces: `ObjectRepository.attach_reference(...)`, `detach_reference(...)`, `count_references(...)`.
- Produces: `QuotaService.get_user_usage(user_uid: str, kinds: set[ObjectKind]) -> int`
- Produces: `QuotaService.evict_body_cache(user_uid: str, limit_bytes: int) -> EvictionResult`.

- [x] **Step 1: Write object store tests**

Tests cover:

- same content produces one physical file and same SHA-256;
- invalid digest path is rejected;
- interrupted write leaves no visible final object;
- expected-size mismatch deletes temporary file;
- missing physical file returns a corrupt/missing verification state;
- last reference removes object, but any remaining user or business reference preserves it;
- body quota counts unique SHA-256 per user;
- `0` quota means unlimited;
- drafts, pending-send, user avatar, account icon, contact avatar and notification asset object kinds are never selected for body/attachment quota eviction.

- [x] **Step 2: Verify failure**

Run `tests.v2.test_object_store`; expected FAIL.

- [x] **Step 3: Implement safe path building**

```python
def object_path(root: Path, digest: str) -> Path:
    normalized = digest.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("invalid SHA-256 digest")
    return root / normalized[:2] / normalized
```

No user filename is used in physical paths.

- [x] **Step 4: Implement atomic streaming writes**

Required sequence:

1. create random temp file under `.tmp` with `O_EXCL`;
2. write chunks while updating SHA-256 and byte count;
3. flush and `os.fsync`;
4. validate expected size if supplied;
5. create target bucket;
6. if target exists, verify its size and discard temp;
7. otherwise `os.replace` temp to target;
8. return digest and sizes;
9. always clean temp file on cancellation or exception.

- [x] **Step 5: Implement object Repository transactions**

The database reference may only be attached after the final file exists. Detaching a reference occurs in a transaction; physical deletion happens after commit and rechecks global references. A file deletion failure is logged for maintenance retry and does not restore a removed business reference.

- [x] **Step 6: Implement body quota eviction contract**

Eviction ordering:

1. exclude pinned, draft, pending-send, currently leased and active-read references;
2. order by user-specific `last_accessed_at`, oldest first;
3. detach all current-user body references for one digest as a unit;
4. delete body search document for affected messages in the same transaction;
5. remove physical object only if no global references remain;
6. return logical bytes released, physical bytes released, message count and object count.

- [x] **Step 7: Run tests**

Run object store tests with a temporary directory and real MySQL test database. Expected PASS.

- [x] **Step 8: Commit**

```bash
git add backend/flymail/infrastructure/object_store backend/flymail/repositories/objects.py backend/tests/v2/test_object_store.py
git commit -m "📦 建立 V2 内容寻址对象存储与配额回收"
```

---

### Task 6: 实现租户隔离 Repository 基础和用户账号数据访问

**Files:**

- Create: `backend/flymail/repositories/base.py`
- Create: `backend/flymail/repositories/users.py`
- Create: `backend/flymail/repositories/accounts.py`
- Create: `backend/flymail/repositories/settings.py`
- Create: `backend/tests/v2/test_repositories.py`

**Interfaces:**

- Produces: `TenantContext(user_uid: str)` and `AdminContext(actor_user_uid: str)`.
- Produces: `UserRepository`, `AccountRepository`, `IdentityRepository`, `CredentialRepository`, `SettingsRepository`.
- Every tenant method receives `TenantContext`; only explicitly named admin methods receive `AdminContext`.

- [x] **Step 1: Write cross-user isolation tests**

Create two users and accounts. Assert user A cannot:

- read user B account by guessed ID;
- update user B settings;
- list user B identities;
- read encrypted credentials;
- infer existence from different error messages.

Also test disabled accounts are excluded from active Worker queries but remain visible in account management.

- [x] **Step 2: Run tests and confirm missing repositories**

Expected: FAIL.

- [x] **Step 3: Implement typed row mapping**

Repository methods return dataclasses, not raw tuple positions. SQL must name columns explicitly and must not use `SELECT *`.

Example method:

```python
async def get_account(self, tenant: TenantContext, account_id: str) -> MailAccount | None:
    row = await fetch_one(
        self.connection,
        """
        SELECT id, user_uid, provider_key, email, normalized_email, enabled, created_at, updated_at
        FROM mail_accounts
        WHERE id = %s AND user_uid = %s
        """,
        (account_id, tenant.user_uid),
    )
    return map_account(row) if row else None
```

- [x] **Step 4: Implement account and identity uniqueness**

Normalize email addresses with trimmed Unicode casefold for lookup while preserving display form. Reject duplicate account email per user and duplicate From identity per account.

- [x] **Step 5: Implement encrypted credential Repository**

Repository stores `EncryptedValue` fields. Decryption occurs in a dedicated application/security service, not in list queries. No method returns decrypted credentials to API query services.

- [x] **Step 6: Implement settings defaults**

New users get:

- body quota `5 * 1024**3` bytes;
- attachment quota `2048 MB`, read from one shared constant; `0` means unlimited and nonzero values below `100 MB` are rejected;
- theme `system`;
- density `comfortable`.

- [x] **Step 7: Run tests**

Run `tests.v2.test_repositories`; expected PASS.

- [x] **Step 8: Commit**

```bash
git add backend/flymail/repositories backend/tests/v2/test_repositories.py
git commit -m "🛡️ 建立 V2 租户隔离 Repository 契约"
```

---

### Task 7: 实现事务型 Outbox、任务租约和 Worker 心跳

**Files:**

- Create: `backend/flymail/repositories/jobs.py`
- Create: `backend/flymail/repositories/outbox.py`
- Create: `backend/flymail/workers/lease.py`
- Modify: `backend/v2_worker.py`
- Create: `backend/tests/v2/test_jobs_outbox.py`

**Interfaces:**

- Produces: `OutboxRepository.append(event_type: str, aggregate_id: str, payload: dict) -> str`
- Produces: `JobRepository.enqueue(JobSpec) -> str`
- Produces: `JobRepository.claim(queue_name: str, worker_id: str, limit: int, lease_seconds: int) -> list[LeasedJob]`
- Produces: `JobRepository.heartbeat(job_id: str, lease_token: str, extend_seconds: int) -> bool`
- Produces: `JobRepository.complete(...)`, `retry(...)`, `fail(...)`, `release_expired_leases(...)`.
- Produces: `WorkerHeartbeatService.touch(worker_id: str, role: str) -> None`.

- [x] **Step 1: Write atomicity and concurrency tests**

Tests must prove:

- rolling back a business transaction also removes its Outbox event;
- committing persists both;
- two Worker connections using `FOR UPDATE SKIP LOCKED` never claim the same job;
- expired lease can be reclaimed with a new lease token;
- stale lease token cannot complete a reclaimed job;
- deduplication key prevents duplicate pending/running job creation;
- heartbeat updates only active jobs;
- retry uses bounded exponential backoff and deterministic jitter input for testing.

- [x] **Step 2: Run tests and verify failure**

Expected: FAIL.

- [x] **Step 3: Implement Outbox payload validation**

Store JSON with a schema version and creation timestamp:

```json
{
  "schema_version": 1,
  "user_uid": "usr_...",
  "event": {},
  "trace_id": "trc_..."
}
```

Reject payloads containing keys named `password`, `token`, `secret`, `authorization`, `body_html` or raw attachment bytes.

- [x] **Step 4: Implement job claiming SQL**

Within one transaction:

```sql
SELECT id
FROM worker_jobs
WHERE queue_name = %s
  AND status IN ('pending', 'retry_wait')
  AND available_at <= %s
ORDER BY priority ASC, available_at ASC, id ASC
LIMIT %s
FOR UPDATE SKIP LOCKED
```

Then update selected rows with `status='leased'`, worker ID, random lease token and lease expiry. Return only rows whose update succeeded.

- [x] **Step 5: Implement retry policy**

Use:

```python
delay = min(max_seconds, base_seconds * (2 ** max(attempt - 1, 0)))
delay += deterministic_jitter_seconds
```

The error classifier decides retryable versus permanent; Job Repository only applies the requested outcome.

- [x] **Step 6: Implement V2 Worker heartbeat loop**

`backend/v2_worker.py` must:

1. run migrations;
2. create Worker pool;
3. generate a worker ID;
4. touch process heartbeat every 10 seconds;
5. release expired leases at startup;
6. handle SIGTERM and SIGINT;
7. stop claiming new jobs before closing the pool.

It must not process protocol jobs until the next plan registers handlers.

- [x] **Step 7: Run concurrency tests repeatedly**

Run the job test module at least 10 times in a loop to expose duplicate-claim races. Expected: zero duplicate claims.

- [x] **Step 8: Commit**

```bash
git add backend/flymail/repositories/jobs.py backend/flymail/repositories/outbox.py backend/flymail/workers/lease.py backend/v2_worker.py backend/tests/v2/test_jobs_outbox.py
git commit -m "⚙️ 建立 V2 Outbox 与可恢复任务租约"
```

---

### Task 8: 完成基础层集成验收和开发文档

**Files:**

- Modify: `backend/v2_dev.py`
- Modify: `README.md`
- Create: `backend/tests/v2/test_foundation_integration.py`
- Optional create: `scripts/test-v2-foundation.sh` only if repeated commands cannot be represented by existing test runner; script must not contain secrets or production paths.

**Interfaces:**

- Produces: Gate 1 evidence consumed by protocol plan.
- Produces: stable schema version and public Python interfaces.

- [ ] **Step 1: Add foundation integration test**

The test must start from an empty temporary database and temporary object directory, then:

1. run migrations;
2. create user and account in one UoW;
3. encrypt and save a credential;
4. write a body object;
5. attach a reference;
6. enqueue a job and Outbox event in the same transaction;
7. claim the job from Worker pool;
8. verify API pool remains able to execute a query;
9. restart pools and verify all persistent state remains;
10. detach the last object reference and verify physical cleanup.

- [ ] **Step 2: Add V2 development health details**

`/api/v2/health` may report only:

```json
{
  "status": "ok",
  "role": "api",
  "schema_version": 5,
  "database": "ok",
  "object_store": "ok"
}
```

It must not expose database URL, filesystem host path, secrets or account information. The value `5` is the Gate 1 expectation; later migration plans must return the dynamically read current schema version rather than hard-code it.

- [ ] **Step 3: Run full foundation verification**

Run:

```bash
cd backend
FLYMAIL_TEST_DATABASE_URL='mysql://...' python -m unittest discover -s tests/v2 -p 'test_*.py' -v

cd ..
git diff --check
git status --short
git diff
```

Expected: all V2 foundation tests PASS and legacy source behavior remains unchanged.

- [ ] **Step 4: Run legacy backend regression**

Run:

```bash
cd backend
python -m unittest discover -s tests -v
```

Expected: existing backend suite PASS. If host dependencies are unavailable, run the suite in the current FlyMail image with the workspace mounted read-only, as established by the project workflow.

- [ ] **Step 5: Update README development note**

Document:

- V2 is under development and not the active production entry;
- V2 test database and object directory must be temporary;
- current deployment remains on legacy entry until final cutover;
- no instruction may point tests at `/Docker/flymail/data`.

- [ ] **Step 6: Inspect interfaces for downstream consistency**

Use `rg` to confirm names in this plan exactly match code:

```bash
rg -n 'FlyMailSettings|SqlUnitOfWork|CredentialCipher|ObjectStore|JobRepository|OutboxRepository' backend/flymail backend/tests/v2
```

- [ ] **Step 7: Commit and push Gate 1**

```bash
git add backend/v2_dev.py backend/tests/v2/test_foundation_integration.py README.md
git commit -m "✅ 验证 V2 基础设施与数据层"
git push origin main
```

## Gate 1 Completion Checklist

- [ ] Empty MySQL database migrates to schema version 5.
- [ ] Re-running migrations is idempotent.
- [ ] API and Worker pools are distinct and bounded.
- [ ] UoW rollback and commit behavior is proven.
- [ ] Credential encryption uses independent derived key and authenticated account binding.
- [ ] Object store is atomic, deduplicated and reference-safe.
- [ ] Body quota and search-index removal contract is tested.
- [ ] Tenant Repository isolation tests pass.
- [ ] Outbox and business state are atomic.
- [ ] Concurrent task claiming has zero duplicate claims.
- [ ] Worker heartbeat and lease recovery survive restart.
- [ ] Legacy backend tests remain green.
- [ ] Current production container and data path were not modified.
