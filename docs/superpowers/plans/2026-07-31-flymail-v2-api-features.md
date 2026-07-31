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
- Modify: `backend/v2_dev.py`
- Create: `backend/tests/v2/test_api_app.py`

**Interfaces:**

- Produces: `create_app(settings: FlyMailSettings) -> FastAPI`
- Produces: `RequestContext(request_id: str, trace_id: str, actor: AuthenticatedUser | None)`
- Produces error envelope: `{"error":{"code":str,"message":str,"request_id":str,"details":dict|None}}`
- Produces `/api/v2/health` and `/api/v2/version`.

- [ ] **Step 1: Write app and error tests**

Tests assert:

- health returns API, database, Worker heartbeat and schema status without secrets;
- unknown route returns normal 404 envelope;
- `AuthorizationError`, `ConflictError`, `NotFoundError`, validation error and unexpected exception map to stable status/code;
- every response includes `X-Request-ID`;
- request-supplied safe request ID is accepted only if it matches allowed format;
- database URL and session secret never appear in error JSON or captured logs.

- [ ] **Step 2: Run tests and verify failure**

Run `tests.v2.test_api_app`; expected FAIL.

- [ ] **Step 3: Implement lifespan**

Lifespan sequence:

1. create API database pool;
2. run migrations;
3. verify object directory readable/writable;
4. initialize repositories/services;
5. register routes;
6. on shutdown stop accepting new requests, close realtime manager and pool.

API startup must not start Worker loops or schedulers.

- [ ] **Step 4: Implement safe middleware**

Middleware records total, database and serialization timing through request state. It never logs request bodies for auth, compose, credentials or backup endpoints.

- [ ] **Step 5: Implement health semantics**

Basic health is `ok` only when API and MySQL work and Worker heartbeat is within configured threshold. Third-party mailbox failures do not make container health fail. If Worker is stale, return `degraded` with HTTP 200 during a bounded startup grace period and HTTP 503 afterward.

- [ ] **Step 6: Run tests and commit**

```bash
cd backend
python -m unittest tests.v2.test_api_app -v
git add backend/flymail/api backend/v2_dev.py backend/tests/v2/test_api_app.py
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
- Create: `backend/tests/v2/test_api_auth_admin.py`

**Interfaces:**

- Produces routes: `/api/v2/auth/login`, `/logout`, `/me`, `/password`.
- Produces admin routes: `/api/v2/admin/users`, `/users/{id}/reset-password`, `/enable`, `/disable`, `/sessions/revoke`.
- Produces dependency: `require_user()` and `require_admin()`.

- [ ] **Step 1: Write auth and admin tests**

Tests cover:

- valid login sets HttpOnly session cookie;
- invalid login returns same message for unknown user and wrong password;
- disabled user cannot log in and existing session becomes invalid;
- password change increments password version and optionally revokes other sessions;
- admin reset forces target user re-login;
- normal user cannot call admin routes;
- login failures are rate-limited by username/source without globally locking all users;
- audit events contain action and actor but not passwords or raw session token.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement server-side sessions**

Store session ID, token hash, user ID, password version, expiry, revoked timestamp and last seen. Cookie contains signed session ID and raw random token; database stores only token hash. Validate cookie signature, token hash, expiry, user enabled state and password version.

- [ ] **Step 4: Implement CSRF protection**

For cookie-authenticated unsafe methods require same-origin checks and a per-session CSRF token delivered through Bootstrap or a dedicated endpoint. Reject missing or mismatched token before application service execution.

- [ ] **Step 5: Implement login rate limit**

Use process-local fast counters plus MySQL persisted failure windows. Store normalized username hash and masked source, not raw submitted password. Successful login clears only the relevant principal window.

- [ ] **Step 6: Implement audit writes**

Security actions write audit rows in the same transaction as the change when possible. Include request ID and safe result code.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_auth_admin -v
git add backend/flymail/application/auth.py backend/flymail/api/schemas/auth.py backend/flymail/api/routes/auth.py backend/flymail/api/routes/admin.py backend/flymail/repositories/sessions.py backend/flymail/repositories/audit.py backend/tests/v2/test_api_auth_admin.py
git commit -m "🔐 实现 V2 本地认证会话与用户管理"
```

---

### Task 3: 实现邮箱账号、凭证和多发件身份 API

**Files:**

- Create: `backend/flymail/application/accounts.py`
- Create: `backend/flymail/api/schemas/accounts.py`
- Create: `backend/flymail/api/routes/accounts.py`
- Create: `backend/tests/v2/test_api_accounts.py`

**Interfaces:**

- Produces routes for list/create/update/delete account, password/authorization-code setup, OAuth start/callback/status, verify credentials, user-level proxy settings, list/create/update identities and reauthorize.
- Produces commands: `CreateAccountCommand`, `UpdateAccountCommand`, `UpsertIdentityCommand`.
- Account list responses never include encrypted credential fields.

- [ ] **Step 1: Write account isolation and validation tests**

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

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement create/update commands**

Application transaction creates account, encrypted credential, default identity, runtime state and Outbox event. Store provider key, endpoint overrides and encrypted proxy reference separately; do not place credentials inside job payload. Validate custom endpoint DNS/IP results against the outbound-network safety policy before saving and again before connection.

- [ ] **Step 4: Implement OAuth and reauthorization flow**

Create short-lived signed OAuth state containing user, session, provider, account draft ID and PKCE verifier reference. Store verifier/token secrets encrypted server-side, never in browser storage. Callback validates state exactly once, exchanges the code through the selected proxy, encrypts tokens, records expiry and enqueues account verification. Refresh remains a Worker/provider responsibility.

- [ ] **Step 5: Implement asynchronous verification**

`POST /accounts/{id}/verify` enqueues `account.verify` with account ID and credential version. Worker fetches/decrypts credential from Repository. API returns `202` and task status URL.

- [ ] **Step 6: Implement safe account deletion intent**

Require account email confirmation. Mark disabled, cancel pending non-send sync jobs, preserve sent audit, and enqueue account cleanup. Active SMTP result-uncertain jobs block deletion until resolved or explicitly cancelled through a separate audited flow.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_accounts -v
git add backend/flymail/application/accounts.py backend/flymail/api/schemas/accounts.py backend/flymail/api/routes/accounts.py backend/tests/v2/test_api_accounts.py
git commit -m "📮 实现 V2 邮箱账号凭证与发件身份 API"
```

---

### Task 4: 实现 Bootstrap、导航和轻量通知摘要

**Files:**

- Create: `backend/flymail/application/bootstrap.py`
- Create: `backend/flymail/api/routes/bootstrap.py`
- Create: `backend/tests/v2/test_api_bootstrap.py`

**Interfaces:**

- Produces: `GET /api/v2/bootstrap`.
- Produces response fields: user, permissions, accounts, navigation, ui_preferences, sync_alert_summary, csrf_token, realtime_cursor, version.

- [ ] **Step 1: Write Bootstrap tests**

Assert:

- one authenticated request returns all first-screen metadata;
- no credential, mail body, notification detail or large sync history appears;
- navigation uses semantic mailboxes and native labels;
- disabled accounts are marked but excluded from active unified inbox by default;
- realtime cursor is user-scoped;
- query count stays below a fixed threshold measured by a test query recorder.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement one query service**

Use bounded aggregate queries. Do not call existing account, folder or notification route functions internally. Return immutable Pydantic response.

- [ ] **Step 4: Add cache headers**

Bootstrap response uses `Cache-Control: no-store`. ETag is not used because CSRF token and realtime cursor are session-specific.

- [ ] **Step 5: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_bootstrap -v
git add backend/flymail/application/bootstrap.py backend/flymail/api/routes/bootstrap.py backend/tests/v2/test_api_bootstrap.py
git commit -m "🚀 实现 V2 单请求启动与统一导航"
```

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

- [ ] **Step 1: Write list and detail tests**

Tests cover:

- stable ordering with equal timestamps;
- next page has no duplicate or missing thread;
- no deep OFFSET appears in SQL capture;
- filters: semantic mailbox, account, native label, unread, starred, attachment;
- cross-account thread displays each source account;
- detail returns timeline and body cache states without waiting for remote fetch;
- cached body streams from object store;
- uncached body enqueues or reuses one P0 job and returns `202`;
- user isolation for thread and message IDs.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement cursor codec**

Sign or authenticate cursor payload to prevent arbitrary SQL-position manipulation. Invalid cursor returns `400 invalid_cursor`, not a server error.

- [ ] **Step 4: Implement projection-only list query**

List query reads `thread_projections` plus bounded label/account display data. It never joins body objects or search documents.

- [ ] **Step 5: Implement detail structure query**

Return thread metadata, ordered message headers, memberships, attachments metadata, operation states and body cache states. Old folded messages do not automatically stream bodies.

- [ ] **Step 6: Implement body streaming**

Verify tenant and body reference, open object, set safe content type and stream decompression. Missing physical object atomically marks body `evicted`, enqueues repair/fetch and returns `202`.

- [ ] **Step 7: Run EXPLAIN integration assertions**

For representative data, assert core list plan uses the intended cursor index and does not report filesort. Store normalized EXPLAIN fixture in test output, not production logs.

- [ ] **Step 8: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_threads -v
git add backend/flymail/application/thread_queries.py backend/flymail/api/schemas/threads.py backend/flymail/api/routes/threads.py backend/tests/v2/test_api_threads.py
git commit -m "📨 实现 V2 会话列表详情与游标查询"
```

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

- [ ] **Step 1: Write command and content route tests**

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

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement operation application service**

One UoW validates scope, updates projection, writes one operation per remote instance, writes one aggregate Outbox event and returns task IDs. Partial authorization is not allowed; unauthorized thread causes whole request rejection.

For query-scoped mark-all-read, persist a validated filter snapshot and enqueue a bounded batch job. Each batch uses tenant-scoped set queries, updates projections, creates remote operations and advances a cursor; it never enumerates millions of message IDs inside one HTTP request or one transaction.

- [ ] **Step 4: Implement confirmation token for permanent deletion**

Token contains user, thread/message IDs, observed trash state and short expiry, signed with separate derived key. State change invalidates token.

- [ ] **Step 5: Implement authenticated content routes**

Never accept object SHA in URL. Resolve through message and attachment IDs under tenant. Use RFC 5987-safe filename encoding and strip path separators/control characters.

- [ ] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_operations_content -v
git add backend/flymail/application/operations.py backend/flymail/application/content.py backend/flymail/api/routes/operations.py backend/flymail/api/routes/content.py backend/tests/v2/test_api_operations_content.py
git commit -m "🗂️ 实现 V2 会话操作撤销与安全内容下载"
```

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

- [ ] **Step 1: Write search tests**

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

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement validated compiler**

Map allowed filter fields to fixed SQL fragments. Empty condition sets are allowed only for normal mailbox browsing limits; search endpoint requires at least one condition.

- [ ] **Step 4: Implement FULLTEXT and fallback policy**

Use MySQL FULLTEXT for cached body and normalized metadata. If ngram parser is unavailable, expose capability in response and use standard FULLTEXT; do not fall back to unbounded `%LIKE%` over body HTML. Short unsupported keyword may search bounded subject/address columns only.

- [ ] **Step 5: Implement suggestions and history limits**

Suggestions use user contacts, frequent participants, account identities, labels and recent searches. Cap history and allow user clear. Do not expose other users' participants.

- [ ] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_search -v
git add backend/flymail/repositories/search.py backend/flymail/application/search_queries.py backend/flymail/api/schemas/search.py backend/flymail/api/routes/search.py backend/tests/v2/test_api_search.py
git commit -m "🔎 实现 V2 高级组合搜索与搜索历史"
```

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

- [ ] **Step 1: Write compose tests**

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

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement versioned drafts**

Every save supplies `expected_version`. Mismatch returns `409 draft_version_conflict` with server version ID and safe timestamps. Do not return full alternate body unless caller explicitly requests conflict detail under same user.

- [ ] **Step 4: Implement streaming upload**

Read upload in bounded chunks, enforce per-file and total draft limits, use object store kind `draft_attachment`, and attach reference only after complete. Request cancellation cleans temporary file.

- [ ] **Step 5: Implement authorized server-path attachment import**

Expose only administrator-defined roots and tenant-visible labels. Resolve with `Path.resolve()`, verify the result remains under the approved root, reject symlinks that escape, require a regular readable file, enforce size limits, then stream the bytes into the content-addressed `draft_attachment` object. The send command never reopens the original NAS path.

- [ ] **Step 6: Implement send command transaction**

Validate identity, recipients, attachment ownership and schedule. Persist immutable send snapshot, Message-ID, body object refs, send task and Outbox in one UoW. Mark draft as queued but preserve until send succeeds.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_compose -v
git add backend/flymail/repositories/drafts.py backend/flymail/application/compose.py backend/flymail/api/schemas/compose.py backend/flymail/api/routes/compose.py backend/tests/v2/test_api_compose.py
git commit -m "✍️ 实现 V2 草稿写信附件与可靠发送 API"
```

---

### Task 9: 实现用户级实时事件、断线续传和查询失效

**Files:**

- Create: `backend/flymail/repositories/realtime.py`
- Create: `backend/flymail/application/realtime.py`
- Create: `backend/flymail/api/routes/realtime.py`
- Create: `backend/tests/v2/test_api_realtime.py`

**Interfaces:**

- Produces: `GET /api/v2/events?after=<sequence>`
- Produces: `WS /api/v2/realtime`
- Event fields: sequence, event_type, aggregate_id, occurred_at, minimal payload.

- [ ] **Step 1: Write realtime tests**

Tests prove:

- events are monotonically increasing per user;
- user A never receives user B event;
- reconnect after sequence returns missing events;
- expired event window returns explicit `resync_required` scopes;
- event payload does not contain body, attachment bytes, credentials or complete recipient lists;
- revoked session closes WebSocket;
- slow client is disconnected or coalesced without blocking event publisher.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement persisted event cursor**

Worker/Application writes `realtime_events` from Outbox publication. API fetches by user and sequence. Keep retention by time/count; cleanup is a maintenance task.

- [ ] **Step 4: Implement WebSocket handshake**

Authenticate session cookie, verify Origin, accept last sequence, send backlog then live notifications. Database remains source of truth; process-local condition variable only wakes connected clients.

- [ ] **Step 5: Define exact event types**

```text
thread.created, thread.updated, thread.removed,
message.body_state, operation.updated, send.updated,
account.status_changed, sync.updated, conflict.created,
settings.updated, session.revoked, version.changed
```

- [ ] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_realtime -v
git add backend/flymail/repositories/realtime.py backend/flymail/application/realtime.py backend/flymail/api/routes/realtime.py backend/tests/v2/test_api_realtime.py
git commit -m "📣 实现 V2 用户实时事件与断线续传"
```

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

- [ ] **Step 1: Write settings and sync tests**

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

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement settings transaction**

Update setting, audit change and enqueue cleanup/reconfigure event in one UoW. Return logical usage and task state, not guessed physical free space.

- [ ] **Step 4: Implement sync status projection**

Read runtime state, jobs, cursors and operation counts through bounded query services. A refresh endpoint reads local status only; manual sync endpoint explicitly enqueues work.

- [ ] **Step 5: Implement conflict actions**

Support draft version choice, uncertain-send resolution, missing-mailbox target selection and operation retry/cancel. Each resolution is audited.

- [ ] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_settings_sync -v
git add backend/flymail/application/settings.py backend/flymail/application/sync_status.py backend/flymail/api/schemas/settings.py backend/flymail/api/routes/settings.py backend/flymail/api/routes/sync.py backend/tests/v2/test_api_settings_sync.py
git commit -m "⚙️ 实现 V2 设置配额与同步冲突中心 API"
```

---

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

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd backend
python -m unittest tests.v2.test_api_personal_notifications -v
```

Expected: FAIL because the personal, contact, notification and storage APIs do not exist.

- [ ] **Step 3: Implement profile and image normalization**

Use the existing Pillow dependency. Decode from a bounded stream, apply EXIF orientation, convert to RGBA/RGB, perform explicit square crop, resize to `256 × 256`, encode WebP, write through `ObjectStore`, and replace the user/account object reference transactionally. Releasing an old image follows true-reference cleanup.

- [ ] **Step 4: Implement contacts and signatures**

Contact methods always require `TenantContext`. Autocomplete searches normalized display name/email with a bounded result limit and stable ordering. Signature HTML uses the same safe-content policy as compose input and remains linked to `mail_identity.id`.

- [ ] **Step 5: Implement notification configuration**

Separate non-secret channel and image-publisher fields from encrypted secret values. Rules map event types to channel IDs and optionally to one publisher ID. Validate publisher endpoints through the same outbound-network policy as Webhooks. Channel test endpoint writes a `notification.deliver` job with a synthetic safe event; API never sends the HTTP or image-upload request itself.

- [ ] **Step 6: Implement authorized storage roots**

Only administrators create root records, and every physical root must resolve under `/data`. Users can browse roots exposed to them, with pagination and hidden-file policy. API returns logical root IDs and relative paths, never unrestricted host paths.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_personal_notifications -v
git add backend/flymail/repositories/contacts.py backend/flymail/repositories/notifications.py backend/flymail/application/personal.py backend/flymail/application/notifications.py backend/flymail/application/storage_paths.py backend/flymail/api/schemas/personal.py backend/flymail/api/schemas/notifications.py backend/flymail/api/routes/profiles.py backend/flymail/api/routes/contacts.py backend/flymail/api/routes/notifications.py backend/flymail/api/routes/storage.py backend/tests/v2/test_api_personal_notifications.py
git commit -m "👤 实现 V2 资料联系人图标签名与通知配置"
```

---

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

- [ ] **Step 1: Write backup tests**

Tests cover:

- backup includes users, profiles, contacts, accounts, identities/signatures, avatar/account-icon objects, notification rules/channels, encrypted credentials, settings, metadata, memberships, threads, drafts, draft attachments, pending send data, operations, authorized storage-root configuration and cursors;
- backup excludes remote body cache, inline image cache, ordinary attachment cache, raw `.eml`, regenerated body search docs and logs;
- wrong password fails before database changes;
- corrupted checksum fails before database changes;
- backup credentials decrypt with backup password and re-encrypt under new instance key;
- restore uses temporary database and object directory;
- restored pending sends and remote operations are assigned exact `review_required` state and cannot execute before stable `operation_id`, `Message-ID` and current remote state are revalidated;
- failed restore leaves original data intact;
- backup password never appears in logs or job payload JSON.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement password-derived backup encryption**

Use Scrypt with random salt to derive AES-GCM backup key. Manifest records algorithm versions and parameters, never password or instance secret.

- [ ] **Step 4: Implement consistent export**

Use a consistent MySQL transaction/snapshot for business tables. Export local-only draft/send objects by referenced hash with checksums. Write archive to temporary path and atomically rename after final checksum.

- [ ] **Step 5: Implement inspect and validation**

Inspection parses manifest, verifies format version, validates all checksums and backup password, then returns counts and compatibility without writing target database.

- [ ] **Step 6: Implement staged restore**

Restore into temporary database/schema and temporary business-object root. Run migrations/compatibility checks, tenant constraints, object checks and credential re-encryption. Convert every unfinished send and remote operation from the snapshot to `review_required`, preserve its original stable identifiers, and enqueue revalidation only after the restored instance starts. `restore_apply` requires admin confirmation token and maintenance mode; the final container-level atomic switch is completed in validation plan.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_api_backup -v
git add backend/flymail/repositories/backup.py backend/flymail/application/backup.py backend/flymail/api/schemas/backup.py backend/flymail/api/routes/backup.py backend/tests/v2/test_api_backup.py
git commit -m "💾 实现 V2 配置业务备份与安全恢复"
```

---

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
