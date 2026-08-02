# FlyMail V2 API 与完整业务功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在稳定的数据层和同步 Worker 之上实现 FlyMail V2 全部 HTTP 与实时接口，包括本地认证、管理员、邮箱账号与身份、Bootstrap、会话列表和详情、高级搜索、本地优先操作、写信草稿、可靠发送、设置配额、同步中心、审计及配置业务备份恢复。

**Architecture:** API 只执行短查询和短事务，远端 IMAP/SMTP 工作全部转为持久化任务。写命令通过 Application Service 原子更新本地投影、操作日志和 Outbox；读接口使用专用 Query Service 和游标投影。所有路由从服务端会话取得 `user_uid`，下载和实时事件在返回前再次验证租户归属。

**Tech Stack:** FastAPI、Pydantic 2、aiomysql、MySQL 8.0、cryptography、python-multipart、WebSocket、unittest/httpx。

## Global Constraints

- 必须先完成基础计划 Gate 1 和协议计划 Gate 2。
- 继承总路线图全部约束。
- API 不直接建立 IMAP 或 SMTP 连接。
- API 不在请求生命周期中等待正文、附件、历史同步或发送任务完成。
- 所有错误使用稳定错误码和安全用户提示；不返回原始 IMAP/SMTP 响应、SQL 或堆栈。
- 所有租户资源路由必须验证 `user_uid`，不能仅校验资源 ID 格式。
- 管理员默认无权读取其他用户的邮件正文、附件、草稿正文或完整搜索历史；系统诊断只能返回脱敏聚合数据。
- 正文和附件对象不能通过 SHA-256 直接公开访问。
- 浏览器会话使用 HttpOnly Cookie；前端不接收长期访问令牌。
- Bootstrap 仅包含首屏必要信息，不返回通知详情、正文或大统计。
- 搜索只查询本地索引，不等待或触发远端全账号同步。
- 备份恢复到临时数据库和对象目录验证完成后才允许切换；恢复的未完成发送和离线操作默认暂停。
- 本计划不切换当前 `backend/main.py`，仍通过 `backend/v2_dev.py` 暴露 V2 API。

## File Map

**Create:**

- `backend/flymail/api/app.py`
- `backend/flymail/api/dependencies.py`
- `backend/flymail/api/errors.py`
- `backend/flymail/api/middleware.py`
- `backend/flymail/api/schemas/common.py`
- `backend/flymail/api/schemas/auth.py`
- `backend/flymail/api/schemas/accounts.py`
- `backend/flymail/api/schemas/threads.py`
- `backend/flymail/api/schemas/search.py`
- `backend/flymail/api/schemas/compose.py`
- `backend/flymail/api/schemas/settings.py`
- `backend/flymail/api/schemas/personal.py`
- `backend/flymail/api/schemas/notifications.py`
- `backend/flymail/api/schemas/backup.py`
- `backend/flymail/api/routes/auth.py`
- `backend/flymail/api/routes/admin.py`
- `backend/flymail/api/routes/accounts.py`
- `backend/flymail/api/routes/bootstrap.py`
- `backend/flymail/api/routes/threads.py`
- `backend/flymail/api/routes/search.py`
- `backend/flymail/api/routes/operations.py`
- `backend/flymail/api/routes/content.py`
- `backend/flymail/api/routes/compose.py`
- `backend/flymail/api/routes/settings.py`
- `backend/flymail/api/routes/profiles.py`
- `backend/flymail/api/routes/contacts.py`
- `backend/flymail/api/routes/notifications.py`
- `backend/flymail/api/routes/storage.py`
- `backend/flymail/api/routes/sync.py`
- `backend/flymail/api/routes/realtime.py`
- `backend/flymail/api/routes/backup.py`
- `backend/flymail/application/auth.py`
- `backend/flymail/application/accounts.py`
- `backend/flymail/application/bootstrap.py`
- `backend/flymail/application/thread_queries.py`
- `backend/flymail/application/search_queries.py`
- `backend/flymail/application/operations.py`
- `backend/flymail/application/content.py`
- `backend/flymail/application/compose.py`
- `backend/flymail/application/settings.py`
- `backend/flymail/application/personal.py`
- `backend/flymail/application/notifications.py`
- `backend/flymail/application/storage_paths.py`
- `backend/flymail/application/sync_status.py`
- `backend/flymail/application/realtime.py`
- `backend/flymail/application/backup.py`
- `backend/flymail/repositories/sessions.py`
- `backend/flymail/repositories/drafts.py`
- `backend/flymail/repositories/search.py`
- `backend/flymail/repositories/realtime.py`
- `backend/flymail/repositories/contacts.py`
- `backend/flymail/repositories/notifications.py`
- `backend/flymail/repositories/audit.py`
- `backend/flymail/repositories/backup.py`
- `backend/tests/v2/test_api_app.py`
- `backend/tests/v2/test_api_auth_admin.py`
- `backend/tests/v2/test_api_accounts.py`
- `backend/tests/v2/test_api_bootstrap.py`
- `backend/tests/v2/test_api_threads.py`
- `backend/tests/v2/test_api_operations_content.py`
- `backend/tests/v2/test_api_search.py`
- `backend/tests/v2/test_api_compose.py`
- `backend/tests/v2/test_api_realtime.py`
- `backend/tests/v2/test_api_settings_sync.py`
- `backend/tests/v2/test_api_personal_notifications.py`
- `backend/tests/v2/test_api_backup.py`
- `backend/tests/v2/test_api_security.py`

**Modify:**

- `backend/v2_dev.py`：启动完整 V2 FastAPI app。
- `backend/flymail/infrastructure/db/migrations/`：只新增版本化迁移，不改已执行迁移。
- `README.md`：增加 V2 API 开发契约与错误码说明。

---

### Task 1: 建立 V2 FastAPI 应用、请求上下文和统一错误响应

**Files:**

- Create: `backend/flymail/api/app.py`
- Create: `backend/flymail/api/dependencies.py`
- Create: `backend/flymail/api/errors.py`
- Create: `backend/flymail/api/middleware.py`
- Create: `backend/flymail/api/schemas/common.py`
- Create: `backend/flymail/infrastructure/db/migrations/v0011_process_heartbeats.py`
- Create: `backend/flymail/repositories/runtime.py`
- Modify: `backend/flymail/infrastructure/db/migrations/runner.py`
- Modify: `backend/flymail/workers/lease.py`
- Modify: `backend/v2_dev.py`
- Create: `backend/tests/v2/test_api_app.py`
- Modify: `backend/tests/v2/test_config.py`
- Modify: `backend/tests/v2/test_foundation_integration.py`
- Modify: `backend/tests/v2/test_migrations.py`
- Modify: `README.md`

**Interfaces:**

- Produces: `create_app(settings: FlyMailSettings) -> FastAPI`
- Produces: `RequestContext(request_id: str, trace_id: str, actor: AuthenticatedUser | None)`
- Produces error envelope: `{"error":{"code":str,"message":str,"request_id":str,"details":dict|None}}`
- Produces `/api/v2/health` and `/api/v2/version`.

- [x] **Step 1: Write app and error tests**

Tests assert:

- health returns API, database, Worker heartbeat and schema status without secrets;
- unknown route returns normal 404 envelope;
- `AuthorizationError`, `ConflictError`, `NotFoundError`, validation error and unexpected exception map to stable status/code;
- every response includes `X-Request-ID`;
- request-supplied safe request ID is accepted only if it matches allowed format;
- database URL and session secret never appear in error JSON or captured logs.

- [x] **Step 2: Run tests and verify failure**

Run `tests.v2.test_api_app`; expected FAIL.

- [x] **Step 3: Implement lifespan**

Lifespan sequence:

1. create API database pool;
2. run migrations;
3. verify object directory readable/writable;
4. initialize repositories/services;
5. register routes;
6. on shutdown stop accepting new requests, close realtime manager and pool.

API startup must not start Worker loops or schedulers.

- [x] **Step 4: Implement safe middleware**

Middleware records total, database and serialization timing through request state. It never logs request bodies for auth, compose, credentials or backup endpoints.

- [x] **Step 5: Implement health semantics**

Basic health is `ok` only when API and MySQL work and Worker heartbeat is within configured threshold. Third-party mailbox failures do not make container health fail. If Worker is stale, return `degraded` with HTTP 200 during a bounded startup grace period and HTTP 503 afterward.

- [x] **Step 6: Run tests and commit**

```bash
cd backend
python -m unittest tests.v2.test_api_app -v
git add README.md backend/flymail/api backend/flymail/infrastructure/db/migrations/runner.py backend/flymail/infrastructure/db/migrations/v0011_process_heartbeats.py backend/flymail/repositories/runtime.py backend/flymail/workers/lease.py backend/v2_dev.py backend/tests/v2/test_api_app.py backend/tests/v2/test_config.py backend/tests/v2/test_foundation_integration.py backend/tests/v2/test_migrations.py docs/superpowers/plans/2026-07-31-flymail-v2-api-features.md
git commit -m "🌐 建立 V2 API 应用与统一错误边界"
```

---

### Task 2: 实现本地登录、会话、用户管理和安全审计

**Files:**

- Create: `backend/flymail/application/auth.py`
- Create: `backend/flymail/api/schemas/auth.py`
- Create: `backend/flymail/api/routes/auth.py`
- Create: `backend/flymail/api/routes/admin.py`
- Create: `backend/flymail/repositories/sessions.py`
- Create: `backend/flymail/repositories/audit.py`
- Create: `backend/flymail/repositories/rate_limits.py`
- Create: `backend/flymail/infrastructure/db/migrations/v0012_authentication_sessions.py`
- Create: `backend/tests/v2/test_api_auth_admin.py`
- Modify: `backend/flymail/api/app.py`, dependencies, errors, users Repository, migration runner, foundation/schema tests and README.

**Interfaces:**

- Produces routes: `/api/v2/auth/login`, `/logout`, `/me`, `/password`.
- Produces admin routes: `/api/v2/admin/users`, `/users/{id}/reset-password`, `/enable`, `/disable`, `/sessions/revoke`.
- Produces dependency: `require_user()` and `require_admin()`.

- [x] **Step 1: Write auth and admin tests**

Tests cover:

- valid login sets HttpOnly session cookie;
- invalid login returns same message for unknown user and wrong password;
- disabled user cannot log in and existing session becomes invalid;
- password change increments password version and optionally revokes other sessions;
- admin reset forces target user re-login;
- normal user cannot call admin routes;
- login failures are rate-limited by username/source without globally locking all users;
- audit events contain action and actor but not passwords or raw session token.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement server-side sessions**

Store session ID, token hash, user ID, password version, expiry, revoked timestamp and last seen. Cookie contains signed session ID and raw random token; database stores only token hash. Validate cookie signature, token hash, expiry, user enabled state and password version.

- [x] **Step 4: Implement CSRF protection**

For cookie-authenticated unsafe methods require same-origin checks and a per-session CSRF token delivered through Bootstrap or a dedicated endpoint. Reject missing or mismatched token before application service execution.

- [x] **Step 5: Implement login rate limit**

Use process-local fast counters plus MySQL persisted failure windows. Store normalized username hash and masked source, not raw submitted password. Successful login clears only the relevant principal window.

- [x] **Step 6: Implement audit writes**

Security actions write audit rows in the same transaction as the change when possible. Include request ID and safe result code.

- [x] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_auth_admin -v
git add README.md backend/flymail/application/auth.py backend/flymail/api/app.py backend/flymail/api/dependencies.py backend/flymail/api/errors.py backend/flymail/api/routes backend/flymail/api/schemas/auth.py backend/flymail/domain/errors.py backend/flymail/infrastructure/db/migrations/runner.py backend/flymail/infrastructure/db/migrations/v0012_authentication_sessions.py backend/flymail/repositories/audit.py backend/flymail/repositories/rate_limits.py backend/flymail/repositories/sessions.py backend/flymail/repositories/users.py backend/tests/v2/test_api_auth_admin.py backend/tests/v2/test_foundation_integration.py backend/tests/v2/test_migrations.py docs/superpowers/plans/2026-07-31-flymail-v2-api-features.md
git commit -m "🔐 实现 V2 本地认证会话与用户管理"
```

**Measured verification:** schema `12`; authentication/admin tests `11/11`; full backend tests `505/505`; frontend tests `93/93`; frontend production build passed. The temporary image `benxianyu/flymail:0.0.25-v2-auth-task2` passed real login, Secure/HttpOnly cookie, CSRF, admin authorization, user disable/session invalidation, audit redaction, Worker health, special-character MySQL password, restart persistence and graceful shutdown checks using an isolated `/tmp` data directory.

---

### Task 3: 实现邮箱账号、凭证和多发件身份 API

**Files:**

- Create: `backend/flymail/application/accounts.py`
- Create: `backend/flymail/api/schemas/accounts.py`
- Create: `backend/flymail/api/routes/accounts.py`
- Create: `backend/flymail/infrastructure/security/outbound.py`
- Create: `backend/flymail/workers/accounts.py`
- Create: `backend/tests/v2/test_api_accounts.py`
- Create: `backend/tests/v2/test_account_workers.py`
- Modify: `backend/flymail/repositories/accounts.py`
- Modify: `backend/flymail/repositories/jobs.py`
- Modify: `backend/flymail/api/app.py`
- Modify: `backend/flymail/api/dependencies.py`
- Modify: `backend/flymail/api/errors.py`
- Modify: `backend/flymail/domain/errors.py`
- Modify: `backend/tests/v2/test_protocol_worker_integration.py`
- Modify: `backend/v2_worker.py`
- Modify: `README.md`

**Interfaces:**

- Produces routes for list/create/update/delete account, password/authorization-code setup, OAuth start/callback/status, verify credentials, user-level proxy settings, list/create/update identities and reauthorize.
- Produces commands: `CreateAccountCommand`, `UpdateAccountCommand`, `UpsertIdentityCommand`, `UpdateIdentityCommand`.
- Account list responses never include encrypted credential fields.

- [x] **Step 1: Write account isolation and validation tests**

Tests cover:

- account creation encrypts credential;
- response excludes plaintext and ciphertext;
- duplicate email within user rejected;
- same email allowed for another user;
- user cannot view or mutate another user's account;
- identity From address must be provider-allowed or verified;
- deleting account first disables tasks and queues cleanup, not immediate object-file deletion in request;
- credential verification creates P0 Worker job and returns task ID without opening IMAP in API process;
- OAuth state is single-use, user/session-bound, expires, uses PKCE where supported, and callback cannot attach credentials to another user's account;
- OAuth authorization and token refresh use the account/user proxy when explicitly configured;
- proxy URL credentials are encrypted, excluded from responses/logs and validated without applying them to internal MySQL/API traffic;
- custom IMAP/SMTP endpoints reject loopback, private, link-local, multicast and cloud-metadata destinations unless an explicit administrator outbound-network policy allows them.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement create/update commands**

Application transaction creates account, encrypted credential, default identity, runtime state and Outbox event. Store provider key, endpoint overrides and encrypted proxy reference separately; do not place credentials inside job payload. Validate custom endpoint DNS/IP results against the outbound-network safety policy before saving and again before connection.

- [x] **Step 4: Implement OAuth and reauthorization flow**

Create short-lived signed OAuth state containing user, session, provider, account draft ID and PKCE verifier reference. Store verifier/token secrets encrypted server-side, never in browser storage. Callback validates state exactly once, exchanges the code through the selected proxy, encrypts tokens, records expiry and enqueues account verification. Refresh remains a Worker/provider responsibility.

- [x] **Step 5: Implement asynchronous verification**

`POST /accounts/{id}/verify` enqueues `account.verify` with account ID and credential version. Worker fetches/decrypts credential from Repository. API returns `202` and task status URL.

- [x] **Step 6: Implement safe account deletion intent**

Require account email confirmation. Mark disabled, cancel pending non-send sync jobs, preserve sent audit, and enqueue account cleanup. Active SMTP result-uncertain jobs block deletion until resolved or explicitly cancelled through a separate audited flow.

- [x] **Step 7: Run tests and commit**

```bash
cd backend/tests
PYTHONPATH=.. python -m unittest v2.test_api_accounts v2.test_account_workers -v
cd ../..
git add README.md docs/superpowers/plans/2026-07-31-flymail-v2-api-features.md backend/flymail/application/accounts.py backend/flymail/api/schemas/accounts.py backend/flymail/api/routes/accounts.py backend/flymail/infrastructure/security/outbound.py backend/flymail/workers/accounts.py backend/flymail/repositories/accounts.py backend/flymail/repositories/jobs.py backend/tests/v2/test_api_accounts.py backend/tests/v2/test_account_workers.py backend/tests/v2/test_protocol_worker_integration.py backend/flymail/api/app.py backend/flymail/api/dependencies.py backend/flymail/api/errors.py backend/flymail/domain/errors.py backend/v2_worker.py
git commit -m "📮 实现 V2 邮箱账号凭证与发件身份 API"
```

**Measured verification:** schema `12`; Task 3 account API and Worker tests `17/17`; affected API/auth/repository/scheduler regression tests `80/80`; full backend tests `522/522` with no skips; frontend tests `93/93`; frontend production build passed. The final local image `benxianyu/flymail:0.0.25-v2-accounts-task3` (`sha256:516a44444184b334f39a8cf69ff81bc810c5c5606bda0df28823bcdb5ccb33e1`) passed all V2 tests `366/366`, container health, schema `12`, encrypted OAuth credential and user proxy persistence, secret-free Job/Outbox/audit payload checks, restart persistence, image/log secret scans and graceful MySQL shutdown using isolated `/tmp` data. Real HTTP `/api/v2/health` and `/api/v2/version` were exercised in the final container; credential-bearing route behavior was exercised through the final image's FastAPI integration tests because the remote execution safety layer rejected ad-hoc commands containing test login credentials. Real Gmail/Outlook authorization and IMAP/SMTP verification remain blocked on production OAuth/client and provider gateway wiring.

---

### Task 4: 实现 Bootstrap、导航和轻量通知摘要

**Files:**

- Create: `backend/flymail/application/bootstrap.py`
- Create: `backend/flymail/api/routes/bootstrap.py`
- Create: `backend/tests/v2/test_api_bootstrap.py`

**Interfaces:**

- Produces: `GET /api/v2/bootstrap`.
- Produces response fields: user, permissions, accounts, navigation, ui_preferences, sync_alert_summary, csrf_token, realtime_cursor, version.

- [x] **Step 1: Write Bootstrap tests**

Assert:

- one authenticated request returns all first-screen metadata;
- no credential, mail body, notification detail or large sync history appears;
- navigation uses semantic mailboxes and native labels;
- disabled accounts are marked but excluded from active unified inbox by default;
- realtime cursor is user-scoped;
- query count stays below a fixed threshold measured by a test query recorder.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement one query service**

Use bounded aggregate queries. Do not call existing account, folder or notification route functions internally. Return immutable Pydantic response.

- [x] **Step 4: Add cache headers**

Bootstrap response uses `Cache-Control: no-store`. ETag is not used because CSRF token and realtime cursor are session-specific.

- [x] **Step 5: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_bootstrap -v
git add backend/flymail/application/bootstrap.py backend/flymail/api/routes/bootstrap.py backend/tests/v2/test_api_bootstrap.py
git commit -m "🚀 实现 V2 单请求启动与统一导航"
```

**Measured verification:** Bootstrap contract tests `4/4`; affected V2 API/auth/account regression tests `36/36`; full backend tests `526/526` with no skips. The authenticated Bootstrap request executes at most six SQL statements including session authentication and uses four fixed aggregate queries for profile/preferences, accounts/runtime/counts, navigation, and cursor/notification summary. Final image and temporary-container verification is recorded in the task delivery commit.

---

### Task 5: 实现会话列表、游标分页和会话详情结构

**Files:**

- Create: `backend/flymail/application/thread_queries.py`
- Create: `backend/flymail/api/schemas/threads.py`
- Create: `backend/flymail/api/routes/threads.py`
- Create: `backend/tests/v2/test_api_threads.py`

**Interfaces:**

- Produces: `GET /api/v2/threads`
- Produces: `GET /api/v2/threads/{thread_id}`
- Produces: `GET /api/v2/messages/{message_id}/body`
- Cursor type: URL-safe encoded `(latest_message_at, thread_id)`.

- [x] **Step 1: Write list and detail tests**

Tests cover:

- stable ordering with equal timestamps;
- next page has no duplicate or missing thread;
- no deep OFFSET appears in SQL capture;
- filters: semantic mailbox, account, native label, unread, starred, attachment;
- cross-account thread displays each source account;
- detail returns timeline and body cache states without waiting for remote fetch;
- cached body streams from object store;
- uncached body enqueues or reuses one highest-priority interactive job and returns `202`;
- user isolation for thread and message IDs.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement cursor codec**

Sign or authenticate cursor payload to prevent arbitrary SQL-position manipulation. Invalid cursor returns `400 invalid_cursor`, not a server error.

- [x] **Step 4: Implement projection-only list query**

List query reads `thread_projections` plus bounded label/account display data. It never joins body objects or search documents.

- [x] **Step 5: Implement detail structure query**

Return thread metadata, ordered message headers, memberships, attachments metadata, operation states and body cache states. Old folded messages do not automatically stream bodies.

- [x] **Step 6: Implement body streaming**

Verify tenant and body reference, open object, set safe content type and stream decompression. Missing physical object atomically transitions the body to the queued repair state, enqueues fetch and returns `202`.

- [x] **Step 7: Run EXPLAIN integration assertions**

For representative data, assert core list plan uses the intended cursor index and does not report filesort. Store normalized EXPLAIN fixture in test output, not production logs.

- [x] **Step 8: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_threads -v
git add backend/flymail/application/thread_queries.py backend/flymail/api/schemas/threads.py backend/flymail/api/routes/threads.py backend/tests/v2/test_api_threads.py
git commit -m "📨 实现 V2 会话列表详情与游标查询"
```

**Measured verification:** Task 5 contract tests `6/6`; affected API, authentication, account and Bootstrap regression tests `42/42`. Cursor queries use `idx_thread_projection_cursor`, avoid OFFSET and body/search joins, and representative EXPLAIN output contains no filesort. Cached bodies stream from verified content objects; missing cache objects transition to the queued repair state and reuse one `content.body` interactive job.

---

### Task 6: 实现会话操作、撤销、附件和原始邮件 API

**Files:**

- Create: `backend/flymail/application/operations.py`
- Create: `backend/flymail/application/content.py`
- Create: `backend/flymail/api/routes/operations.py`
- Create: `backend/flymail/api/routes/content.py`
- Create: `backend/tests/v2/test_api_operations_content.py`

**Interfaces:**

- Produces operation endpoints for read, star, labels, move, archive, trash, permanent delete, query-scoped mark-all-read and undo.
- Produces attachment metadata/download and raw `.eml` request/status/download endpoints.

- [x] **Step 1: Write command and content route tests**

Tests prove:

- thread action expands to authorized message instances only;
- local projection changes immediately after API commit;
- response includes operation IDs and per-message initial status;
- undo cancels pending or creates compensation for synced reversible action;
- permanent delete requires trash membership and explicit confirmation token;
- attachment cache hit streams local object;
- cache miss returns task ID and never invokes IMAP in route;
- guessed attachment ID from another user returns 404-style denial;
- raw `.eml` fetch is explicit and quota-tagged;
- dangerous HTML/SVG attachments use download disposition and isolated content type;
- query-scoped mark-all-read validates the current mailbox/filter scope, creates one operation group and processes remote instances in bounded batches without loading the full result set into API memory.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement operation application service**

One UoW validates scope, updates projection, writes one operation per remote instance, writes one aggregate Outbox event and returns task IDs. Partial authorization is not allowed; unauthorized thread causes whole request rejection.

For query-scoped mark-all-read, persist a validated filter snapshot and enqueue a bounded batch job. Each batch uses tenant-scoped set queries, updates projections, creates remote operations and advances a cursor; it never enumerates millions of message IDs inside one HTTP request or one transaction.

- [x] **Step 4: Implement confirmation token for permanent deletion**

Token contains user, thread/message IDs, observed trash state and short expiry, signed with separate derived key. State change invalidates token.

- [x] **Step 5: Implement authenticated content routes**

Never accept object SHA in URL. Resolve through message and attachment IDs under tenant. Use RFC 5987-safe filename encoding and strip path separators/control characters.

- [x] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_operations_content -v
git add backend/flymail/application/operations.py backend/flymail/application/content.py backend/flymail/api/routes/operations.py backend/flymail/api/routes/content.py backend/tests/v2/test_api_operations_content.py
git commit -m "🗂️ 实现 V2 会话操作撤销与安全内容下载"
```

**Measured verification:** Task 6 API contracts `7/7`; operation, content-fetch, migration and Worker-registry regression tests `67/67`. Permanent-delete confirmations are independently signed, expire after five minutes and are invalidated by trash/remote-version changes. Query-scoped mark-all-read persists a tenant-scoped filter snapshot in schema 13 and advances through deterministic bounded Worker batches. Content routes stream only verified local objects, sanitize filenames, enforce download disposition for dangerous types and enqueue quota-tagged jobs on cache misses.

---

### Task 7: 实现高级组合搜索、搜索历史和保存搜索

**Files:**

- Create: `backend/flymail/repositories/search.py`
- Create: `backend/flymail/application/search_queries.py`
- Create: `backend/flymail/api/schemas/search.py`
- Create: `backend/flymail/api/routes/search.py`
- Create: `backend/tests/v2/test_api_search.py`

**Interfaces:**

- Produces: `POST /api/v2/search`
- Produces: search suggestion, recent history, saved-search CRUD.
- Produces: `SearchFilter` with keyword, from, to, dates, accounts, mailboxes, labels, read, starred, attachment and size fields.

- [x] **Step 1: Write search tests**

Tests cover:

- structural filters work without cached body;
- body keyword matches only messages with current search document;
- body eviction removes match;
- results aggregate by thread and identify matching message/field;
- all SQL is parameterized;
- frontend field names cannot become raw SQL identifiers;
- query always includes user scope;
- cursor pagination stable;
- search does not enqueue remote sync;
- full raw search keyword is absent from normal performance logs;
- saved search stores validated structured JSON only.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement validated compiler**

Map allowed filter fields to fixed SQL fragments. Empty condition sets are allowed only for normal mailbox browsing limits; search endpoint requires at least one condition.

- [x] **Step 4: Implement FULLTEXT and fallback policy**

Use MySQL FULLTEXT for cached body and normalized metadata. If ngram parser is unavailable, expose capability in response and use standard FULLTEXT; do not fall back to unbounded `%LIKE%` over body HTML. Short unsupported keyword may search bounded subject/address columns only.

- [x] **Step 5: Implement suggestions and history limits**

Suggestions use user contacts, frequent participants, account identities, labels and recent searches. Cap history and allow user clear. Do not expose other users' participants.

- [x] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_search -v
git add backend/flymail/repositories/search.py backend/flymail/application/search_queries.py backend/flymail/api/schemas/search.py backend/flymail/api/routes/search.py backend/tests/v2/test_api_search.py
git commit -m "🔎 实现 V2 高级组合搜索与搜索历史"
```

**Measured verification:** Task 7 search contracts `5/5`. Structural filters remain available without body cache; FULLTEXT results disappear immediately when the current search document is evicted. Values are bound parameters, SQL fragments come only from validated fields, every search is tenant-scoped, result pages aggregate by thread with authenticated cursors, and searches create no remote jobs. Ngram false positives were eliminated with parameterized exact Boolean phrases; history is capped at 50 and saved searches persist only validated structured JSON.

---

### Task 8: 实现草稿、附件上传、回复转发和可靠发送 API

**Files:**

- Create: `backend/flymail/repositories/drafts.py`
- Create: `backend/flymail/application/compose.py`
- Create: `backend/flymail/api/schemas/compose.py`
- Create: `backend/flymail/api/routes/compose.py`
- Create: `backend/tests/v2/test_api_compose.py`

**Interfaces:**

- Produces draft CRUD, autosave with version, attachment upload/remove, reply/forward template and send/schedule/cancel routes.
- Produces: `DraftVersionConflict` response containing both version metadata, not hidden overwrite.

- [x] **Step 1: Write compose tests**

Tests cover:

- creating draft selects explicit account identity;
- cross-account reply defaults to receiving account/identity;
- optimistic version prevents silent overwrite;
- conflicting local/remote draft produces two versions;
- upload streams to temporary object and attaches after complete hash;
- deleting draft releases only unreferenced draft objects;
- immediate and scheduled send use same persisted command;
- send endpoint returns queue ID without SMTP call;
- queued send can cancel before Worker lease;
- Bcc is accepted but never returned in public sent header representation;
- user cannot attach another user's object ID;
- user may attach a file from an administrator-authorized `/data` root, but path traversal, symlink escape and an unapproved root are rejected;
- selecting a NAS/server file streams it into `draft_attachment` object storage and does not keep a live reference to the external path.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement versioned drafts**

Every save supplies `expected_version`. Mismatch returns `409 draft_version_conflict` with server version ID and safe timestamps. Do not return full alternate body unless caller explicitly requests conflict detail under same user.

- [x] **Step 4: Implement streaming upload**

Read upload in bounded chunks, enforce per-file and total draft limits, use object store kind `draft_attachment`, and attach reference only after complete. Request cancellation cleans temporary file.

- [x] **Step 5: Implement authorized server-path attachment import**

Expose only administrator-defined roots and tenant-visible labels. Resolve with `Path.resolve()`, verify the result remains under the approved root, reject symlinks that escape, require a regular readable file, enforce size limits, then stream the bytes into the content-addressed `draft_attachment` object. The send command never reopens the original NAS path.

- [x] **Step 6: Implement send command transaction**

Validate identity, recipients, attachment ownership and schedule. Persist immutable send snapshot, Message-ID, body object refs, send task and Outbox in one UoW. Mark draft as queued but preserve until send succeeds.

- [x] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_compose -v
git add backend/flymail/repositories/drafts.py backend/flymail/application/compose.py backend/flymail/api/schemas/compose.py backend/flymail/api/routes/compose.py backend/tests/v2/test_api_compose.py
git commit -m "✍️ 实现 V2 草稿写信附件与可靠发送 API"
```

**Measured verification:** Task 8 API contracts `6/6`; compose, reliable-sender and migration regression tests `45/45`. Schema 14 persists immutable draft snapshots and conflict bodies. Upload and server-import paths stream into content-addressed objects, reject path traversal and symlinks, enforce size/quota limits and preserve shared objects until the last reference is removed. Immediate and scheduled sends reuse the same reliable command, make no SMTP call in the API process, keep Bcc envelope-only and allow pre-lease cancellation.

---

### Task 9: 实现用户级实时事件、断线续传和查询失效

**Files:**

- Create: `backend/flymail/repositories/realtime.py`
- Create: `backend/flymail/application/realtime.py`
- Create: `backend/flymail/api/routes/realtime.py`
- Create: `backend/tests/v2/test_api_realtime.py`

**Interfaces:**

- Produces: `GET /api/v2/events?after=<sequence>`
- Produces: `WS /api/v2/realtime`（兼容别名 `/api/v2/ws`）
- Event fields: sequence, event_type, aggregate_id, occurred_at, minimal payload.

- [x] **Step 1: Write realtime tests**

Tests prove:

- events are monotonically increasing per user;
- user A never receives user B event;
- reconnect after sequence returns missing events;
- expired event window returns explicit `resync_required` scopes;
- event payload does not contain body, attachment bytes, credentials or complete recipient lists;
- revoked session closes WebSocket;
- slow client is disconnected or coalesced without blocking event publisher.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement persisted event cursor**

Worker/Application writes `realtime_events` from Outbox publication. API fetches by user and sequence. Keep retention by time/count; cleanup is a maintenance task.

- [x] **Step 4: Implement WebSocket handshake**

Authenticate session cookie, verify Origin, accept last sequence, send backlog then live notifications. Database remains source of truth; process-local condition variable only wakes connected clients.

- [x] **Step 5: Define exact event types**

```text
thread.created, thread.updated, thread.removed,
message.body_state, operation.updated, send.updated,
account.status_changed, sync.updated, conflict.created,
settings.updated, session.revoked, version.changed,
notification.created
```

- [x] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_realtime -v
git add backend/flymail/repositories/realtime.py backend/flymail/application/realtime.py backend/flymail/api/routes/realtime.py backend/tests/v2/test_api_realtime.py
git commit -m "📣 实现 V2 用户实时事件与断线续传"
```

**Measured verification:** Task 9 realtime contracts `5/5`; realtime and notification regression tests `26/26`. HTTP and WebSocket consumers resume from persisted per-user cursors, expired windows return explicit resync scopes, revoked sessions close with 4401 and slow clients with 1013. Database rows remain the source of truth; local conditions only wake waiters, and payload validation rejects sensitive or oversized data.

---

### Task 10: 实现设置、缓存配额、同步中心和审计查询

**Files:**

- Create: `backend/flymail/application/settings.py`
- Create: `backend/flymail/application/sync_status.py`
- Create: `backend/flymail/api/schemas/settings.py`
- Create: `backend/flymail/api/routes/settings.py`
- Create: `backend/flymail/api/routes/sync.py`
- Create: `backend/tests/v2/test_api_settings_sync.py`

**Interfaces:**

- Produces settings routes for profile, UI preferences, body quota, attachment quota, remote image policy and compose preferences.
- Produces sync center routes for account runtime, phase progress, pending operations, retries, conflicts and maintenance state.

- [x] **Step 1: Write settings and sync tests**

Tests cover:

- default body quota is 5 GB;
- `0` means unlimited;
- nonzero quota below documented minimum rejected;
- lowering quota enqueues immediate cleanup and returns task ID;
- usage counts unique object hashes per user;
- inline images excluded from ordinary attachment quota;
- sync progress separates summary, body, index and state phases;
- heartbeat prevents long message processing from stale failure;
- retry route deduplicates active task;
- user sees only own account operations/conflicts;
- admin health view is aggregate and body-free.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement settings transaction**

Update setting, audit change and enqueue cleanup/reconfigure event in one UoW. Return logical usage and task state, not guessed physical free space.

- [x] **Step 4: Implement sync status projection**

Read runtime state, jobs, cursors and operation counts through bounded query services. A refresh endpoint reads local status only; manual sync endpoint explicitly enqueues work.

- [x] **Step 5: Implement conflict actions**

Support draft version choice, uncertain-send resolution, missing-mailbox target selection and operation retry/cancel. Each resolution is audited.

- [x] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_settings_sync -v
git add backend/flymail/application/settings.py backend/flymail/application/sync_status.py backend/flymail/api/schemas/settings.py backend/flymail/api/routes/settings.py backend/flymail/api/routes/sync.py backend/tests/v2/test_api_settings_sync.py
git commit -m "⚙️ 实现 V2 设置配额与同步冲突中心 API"
```

---

**Measured verification:** Task 10 API and cache-cleanup contracts `13/13`. Settings return distinct per-user logical usage, enforce `0` as unlimited and a 100 MiB nonzero minimum, atomically audit and deduplicate `cache.cleanup` jobs when quotas shrink. The Worker evicts body and ordinary-attachment caches without removing message metadata, inline images, cross-tenant references or shared physical objects. Sync center queries local runtime/cursor/operation projections only, manual refresh deduplicates `sync.reconcile`, conflict actions are tenant-scoped and audited, and administrator diagnostics return aggregate body-free counts.

### Task 11: 实现个人资料、联系人、签名、账号图标和通知配置

**Files:**

- Create: `backend/flymail/repositories/contacts.py`
- Create: `backend/flymail/repositories/notifications.py`
- Create: `backend/flymail/application/personal.py`
- Create: `backend/flymail/application/notifications.py`
- Create: `backend/flymail/application/storage_paths.py`
- Create: `backend/flymail/api/schemas/personal.py`
- Create: `backend/flymail/api/schemas/notifications.py`
- Create: `backend/flymail/api/routes/profiles.py`
- Create: `backend/flymail/api/routes/contacts.py`
- Create: `backend/flymail/api/routes/notifications.py`
- Create: `backend/flymail/api/routes/storage.py`
- Create: `backend/tests/v2/test_api_personal_notifications.py`

**Interfaces:**

- Produces profile nickname/avatar and personal-preference endpoints.
- Produces contact CRUD, quick-add-from-message and recipient autocomplete.
- Produces identity signature update and account-icon preset/upload endpoints.
- Produces notification center, read/dismiss, channel/rule CRUD, optional image-publisher configuration and test-delivery endpoints.
- Produces administrator-authorized storage-root list and safe path browser endpoints.

- [ ] **Step 1: Write personal, contact, icon and notification tests**

Tests cover:

- user and admin can update only allowed profile fields; role changes remain admin-only;
- avatar and account icon uploads are decoded with Pillow, reject malformed/oversized images, crop/normalize to `256 × 256 WebP`, store as non-evictable object kinds and preserve tenant isolation;
- account icon preset, uploaded icon and provider default produce one stable response model used everywhere;
- contacts are user-scoped, normalize email, support quick-add and bounded autocomplete without exposing other users;
- identity signature is tied to one verified sending identity and sanitized before later compose insertion;
- notification center lists new-mail, send and backup events with cursor pagination;
- Bark, Telegram, enterprise WeChat, DingTalk, Feishu and generic Webhook settings encrypt tokens/secrets and never return ciphertext;
- optional `flymail-imgbed`/generic HTTPS image-publisher settings encrypt upload/delete tokens, validate public endpoints and return only configured-state metadata;
- test-delivery enqueues a notification job and returns task ID without outbound HTTP in the API process;
- Gmail/proxy reuse is opt-in per notification channel;
- authorized storage browsing cannot escape configured `/data` roots through `..`, symlink or encoded separators;
- backup/export includes profiles, contacts, signatures, icons and notification configuration but not delivery secrets in plaintext.

- [x] **Step 2: Run tests and verify failure**

Run:

```bash
cd backend
python -m unittest tests.v2.test_api_personal_notifications -v
```

Expected: FAIL because the personal, contact, notification and storage APIs do not exist.

- [x] **Step 3: Implement profile and image normalization**

Use the existing Pillow dependency. Decode from a bounded stream, apply EXIF orientation, convert to RGBA/RGB, perform explicit square crop, resize to `256 × 256`, encode WebP, write through `ObjectStore`, and replace the user/account object reference transactionally. Releasing an old image follows true-reference cleanup.

- [x] **Step 4: Implement contacts and signatures**

Contact methods always require `TenantContext`. Autocomplete searches normalized display name/email with a bounded result limit and stable ordering. Signature HTML uses the same safe-content policy as compose input and remains linked to `mail_identity.id`.

- [x] **Step 5: Implement notification configuration**

Separate non-secret channel and image-publisher fields from encrypted secret values. Rules map event types to channel IDs and optionally to one publisher ID. Validate publisher endpoints through the same outbound-network policy as Webhooks. Channel test endpoint writes a `notification.deliver` job with a synthetic safe event; API never sends the HTTP or image-upload request itself.

- [x] **Step 6: Implement authorized storage roots**

Only administrators create root records, and every physical root must resolve under `/data`. Users can browse roots exposed to them, with pagination and hidden-file policy. API returns logical root IDs and relative paths, never unrestricted host paths.

- [x] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_personal_notifications -v
git add backend/flymail/repositories/contacts.py backend/flymail/repositories/notifications.py backend/flymail/application/personal.py backend/flymail/application/notifications.py backend/flymail/application/storage_paths.py backend/flymail/api/schemas/personal.py backend/flymail/api/schemas/notifications.py backend/flymail/api/routes/profiles.py backend/flymail/api/routes/contacts.py backend/flymail/api/routes/notifications.py backend/flymail/api/routes/storage.py backend/tests/v2/test_api_personal_notifications.py
git commit -m "👤 实现 V2 资料联系人图标签名与通知配置"
```

---

**Measured verification:** Task 11 personal and notification contracts `5/5`; related account, notification-center, notification Worker, object-store, backup and application regressions `72/72` after correcting one SQL wildcard escape in the backup test. Avatar and account-icon uploads are bounded, EXIF-normalized and stored as pinned `256 × 256 WebP` content objects. Contacts support tenant-scoped quick-add and autocomplete. Identity signatures use an allowlist sanitizer. Notification channel/rule/image-publisher CRUD encrypts secrets with the existing credential cipher, rejects private endpoints and queues test delivery without outbound API-process HTTP. Storage roots are administrator-authorized under the configured data directory and browsing returns only safe relative entries. Backup inclusion and plaintext-secret exclusion are verified in Task 12.

### Task 12: 实现配置业务备份、独立密码加密和安全恢复

**Files:**

- Create: `backend/flymail/repositories/backup.py`
- Create: `backend/flymail/application/backup.py`
- Create: `backend/flymail/api/schemas/backup.py`
- Create: `backend/flymail/api/routes/backup.py`
- Create: `backend/tests/v2/test_api_backup.py`

**Interfaces:**

- Produces routes to create/list/download/inspect/restore backup jobs.
- Produces job kinds: `backup.create`, `backup.inspect`, `backup.restore_validate`, `backup.restore_apply`.
- Backup format includes manifest, database export, business objects, checksums and encrypted credentials.

- [x] **Step 1: Write backup tests**

Tests cover:

- backup includes users, profiles, contacts, accounts, identities/signatures, avatar/account-icon objects, outbound proxy configurations, notification rules/channels/image-publisher configuration, encrypted mailbox/proxy/notification credentials, settings, metadata, memberships, threads, drafts, draft attachments, pending send data, operations, authorized storage-root configuration and cursors;
- backup excludes transient OAuth authorization states, notification delivery attempts, temporary notification assets, remote body cache, inline image cache, ordinary attachment cache, raw `.eml`, regenerated body search docs and logs;
- wrong password fails before database changes;
- corrupted checksum fails before database changes;
- backup credentials decrypt with backup password and re-encrypt under new instance key;
- restore uses temporary database and object directory;
- restored pending sends and remote operations are assigned exact `review_required` state and cannot execute before stable `operation_id`, `Message-ID` and current remote state are revalidated;
- failed restore leaves original data intact;
- backup password never appears in logs or job payload JSON.

- [x] **Step 2: Verify failure**

Expected: FAIL.

- [x] **Step 3: Implement password-derived backup encryption**

Use Scrypt with random salt to derive AES-GCM backup key. Manifest records algorithm versions and parameters, never password or instance secret.

- [x] **Step 4: Implement consistent export**

Use a consistent MySQL transaction/snapshot for business tables. Export local-only draft/send objects by referenced hash with checksums. Write archive to temporary path and atomically rename after final checksum.

- [x] **Step 5: Implement inspect and validation**

Inspection parses manifest, verifies format version, validates all checksums and backup password, then returns counts and compatibility without writing target database.

- [x] **Step 6: Implement staged restore**

Restore into temporary database/schema and temporary business-object root. Run migrations/compatibility checks, tenant constraints, object checks and credential re-encryption. Convert every unfinished send and remote operation from the snapshot to `review_required`, preserve its original stable identifiers, and enqueue revalidation only after the restored instance starts. `restore_apply` requires admin confirmation token and maintenance mode; the final container-level atomic switch is completed in validation plan.

- [x] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_backup -v
git add backend/flymail/repositories/backup.py backend/flymail/application/backup.py backend/flymail/api/schemas/backup.py backend/flymail/api/routes/backup.py backend/tests/v2/test_api_backup.py
git commit -m "💾 实现 V2 配置业务备份与安全恢复"
```

---

**Measured verification:** Task 12 secure and compatibility backup contracts `7/7`; schema 15/16 migration plus backup contracts `26/26`; related account, notification, object-store and application regression produced no additional failures. Backups use independent-password Scrypt plus streaming AES-256-GCM, a repeatable-read business snapshot allowlist, and content-hash verification for local-only business objects. Instance-encrypted mailbox/proxy/notification credentials are re-encrypted under the backup key and then under the current instance key during rehearsal. OAuth/session/job/delivery/cache/search/log state is excluded. Wrong passwords and corrupted ciphertext fail before temporary database creation. Unfinished sends and remote operations become `review_required`, stable IDs are preserved, no runnable jobs are restored, and all temporary databases/files are removed. Production `/data` is never replaced; destructive apply remains a separate Gate 5 confirmation.

### Task 13: 完成 API 安全、OpenAPI 和全功能集成验收

**Files:**

- Create: `backend/tests/v2/test_api_security.py`
- Create: `backend/tests/v2/test_api_integration.py`
- Modify: `backend/v2_dev.py`
- Modify: `README.md`

**Interfaces:**

- Produces: frozen V2 OpenAPI and event schema consumed by frontend plan.
- Produces: Gate 3 evidence.

- [ ] **Step 1: Add cross-resource security matrix**

For two users, attempt guessed IDs against profiles, contacts, accounts, identities, account icons, notification channels/events, storage roots, threads, messages, attachments, drafts, jobs, operations, search history, saved search, backup and WebSocket cursor. Every unauthorized lookup returns a non-enumerating denial and creates no side effect. Repeat content, attachment, draft and full-search-history requests under an administrator session and assert they are denied unless the resource belongs to that administrator; administrator diagnostics may return only aggregate counts and masked identifiers.

- [ ] **Step 2: Add malicious input tests**

Cover:

- path traversal filenames;
- HTML/SVG active content;
- SQL-like filter values;
- forged cursor and permanent-delete token;
- CSRF and Origin mismatch;
- oversized JSON, upload and recipient lists;
- session replay after password reset;
- backup zip path traversal;
- object hash guessed directly.

- [ ] **Step 3: Add full API integration scenario**

Test login, profile/avatar, contacts/autocomplete, account creation/icon/signature, notification channel test, authorized storage attachment, async verify, Bootstrap, initial sync projection, list, detail body task, search, read/star/move/mark-all-read, draft autosave, upload, schedule send, realtime updates, quota change, sync retry, backup create/inspect/restore validate and logout.

- [ ] **Step 4: Freeze schemas**

Generate OpenAPI JSON in test memory and assert operation IDs, route paths, enum values and error envelope. Store a reviewed snapshot at `backend/tests/v2/fixtures/openapi-v2.json`; changes require explicit review.

- [ ] **Step 5: Run all V2 backend tests**

```bash
cd backend
FLYMAIL_TEST_DATABASE_URL='mysql://...' python -m unittest discover -s tests/v2 -p 'test_*.py' -v
```

Expected: PASS.

- [ ] **Step 6: Run legacy backend regression**

```bash
python -m unittest discover -s tests -v
```

Expected: PASS.

- [ ] **Step 7: Update README**

Document V2 API prefix, local session model, async task semantics, local-only search boundary, backup scope and that current production entry remains unchanged.

- [ ] **Step 8: Commit and push Gate 3**

```bash
git add backend/v2_dev.py backend/tests/v2/test_api_security.py backend/tests/v2/test_api_integration.py backend/tests/v2/fixtures/openapi-v2.json README.md
git commit -m "✅ 验证 V2 全功能 API 与安全隔离"
git push origin main
```

## Gate 3 Completion Checklist

- [ ] Local login, sessions and admin operations work and are audited.
- [ ] Account credentials remain encrypted and absent from API responses.
- [ ] Bootstrap is single-request and bounded.
- [ ] Thread list/detail use cursor projection and do not access remote mailbox.
- [ ] Body and attachment misses return task state instead of blocking.
- [ ] Local operations, undo and permanent delete rules are enforced.
- [ ] Advanced search is local, structured and tenant-scoped.
- [ ] Drafts, uploads, immediate send and scheduled send use reliable queue.
- [ ] Realtime events are user-scoped and resumable.
- [ ] Settings, quotas, sync status and conflicts are complete.
- [ ] Profiles, contacts, signatures, account icons, notification configuration and authorized storage paths are complete.
- [ ] Backup and restore validation are safe and portable.
- [ ] Cross-user and malicious input matrix passes.
- [ ] OpenAPI and realtime event schema are frozen for frontend.
- [ ] Legacy backend tests remain green.
- [ ] Production container and data remain untouched.
