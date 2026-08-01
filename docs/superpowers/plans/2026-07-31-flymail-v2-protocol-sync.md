# FlyMail V2 协议核心与同步 Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在基础数据契约之上实现通用 IMAP/SMTP 核心、服务商插件、真实 MIME part 解析、消息摄取与会话构建、IDLE、自适应周期校正、分层正文缓存、离线操作冲突和可靠发送。

**Architecture:** Provider 核心只处理协议和标准能力，插件声明服务商差异；所有长任务由独立 Worker 从 MySQL 领取。IDLE 连接只产生轻量同步事件，普通工作连接串行执行命令；摘要、正文、附件和状态校正使用分级任务，所有用户操作先更新本地投影，再由 Worker 幂等提交远端。

**Tech Stack:** Python 3、aiomysql、imapclient、aioimaplib、SMTP、email 标准库、FastAPI 共享领域模型、MySQL 8.0、unittest。

## Global Constraints

- 必须先完成 `2026-07-31-flymail-v2-foundation-data.md` Gate 1。
- 继承总路线图全部约束。
- 不在 Provider 插件中访问数据库、对象存储、HTTP 路由或 WebSocket。
- 同一 IMAP 连接禁止多个协程并发发送命令。
- 普通附件同步阶段只保存元数据和真实 `imap_part`，不下载二进制。
- 正文和附件默认不得使用整封 `BODY.PEEK[]`；只有按需原始 `.eml` 或明确 MIME 解析回退任务可使用。
- IDLE 连接不下载正文、不解析 MIME、不执行数据库批量写入。
- 所有任务必须持久化；内存队列只能作为当前 Worker 的短期调度视图。
- 每账号最多 1 条 IDLE 连接、2 条普通 IMAP 工作连接、1 个状态修改任务。
- Gmail 标签通过关系表表示；普通 IMAP 文件夹使用相同 membership 接口。
- 用户操作和发送必须幂等；结果不确定时进入验证任务，不盲目重做。
- 本计划不实现最终 HTTP API 或前端。

## File Map

**Create:**

- `backend/flymail/providers/contracts.py`
- `backend/flymail/providers/errors.py`
- `backend/flymail/providers/registry.py`
- `backend/flymail/providers/core/imap_session.py`
- `backend/flymail/providers/core/imap_commands.py`
- `backend/flymail/providers/core/bodystructure.py`
- `backend/flymail/providers/core/mime_parts.py`
- `backend/flymail/providers/core/smtp_client.py`
- `backend/flymail/providers/core/rate_limit.py`
- `backend/flymail/providers/plugins/generic.py`
- `backend/flymail/providers/plugins/gmail.py`
- `backend/flymail/providers/plugins/outlook.py`
- `backend/flymail/providers/plugins/qq.py`
- `backend/flymail/providers/plugins/netease.py`
- `backend/flymail/providers/plugins/icloud.py`
- `backend/flymail/providers/plugins/sina.py`
- `backend/flymail/domain/mail.py`
- `backend/flymail/domain/threading.py`
- `backend/flymail/domain/operations.py`
- `backend/flymail/repositories/mailboxes.py`
- `backend/flymail/repositories/messages.py`
- `backend/flymail/repositories/threads.py`
- `backend/flymail/repositories/operations.py`
- `backend/flymail/workers/dispatcher.py`
- `backend/flymail/workers/scheduler.py`
- `backend/flymail/workers/idle.py`
- `backend/flymail/workers/reconciliation.py`
- `backend/flymail/workers/ingestion.py`
- `backend/flymail/workers/content_fetch.py`
- `backend/flymail/workers/operation_apply.py`
- `backend/flymail/workers/sender.py`
- `backend/flymail/notifications/contracts.py`
- `backend/flymail/notifications/channels.py`
- `backend/flymail/workers/notifications.py`
- `backend/tests/v2/test_provider_contracts.py`
- `backend/tests/v2/test_imap_session.py`
- `backend/tests/v2/test_bodystructure.py`
- `backend/tests/v2/test_message_ingestion.py`
- `backend/tests/v2/test_worker_scheduler.py`
- `backend/tests/v2/test_idle_reconciliation.py`
- `backend/tests/v2/test_content_fetch.py`
- `backend/tests/v2/test_operation_apply.py`
- `backend/tests/v2/test_reliable_sender.py`
- `backend/tests/v2/test_notification_dispatch.py`
- `backend/tests/v2/fixtures/imap/*.json`：人工构造、无真实邮件内容的协议响应。

**Modify:**

- `backend/v2_worker.py`：注册并运行正式任务处理器。
- `backend/flymail/infrastructure/db/migrations/`：仅在已批准的数据契约确有遗漏时新增下一版本迁移，禁止修改已提交迁移语义。
- `README.md`：本计划末尾增加 V2 Provider 与同步开发说明，不切换当前生产行为。

---

### Task 1: 固定 Provider 能力、错误和插件合同

**Files:**

- Create: `backend/flymail/providers/contracts.py`
- Create: `backend/flymail/providers/errors.py`
- Create: `backend/flymail/providers/registry.py`
- Create: all files under `backend/flymail/providers/plugins/`
- Create: `backend/tests/v2/test_provider_contracts.py`

**Interfaces:**

- Produces: `ProviderCapabilities`
- Produces: `ProviderPlugin` protocol
- Produces: `ProviderRegistry.get(provider_key: str) -> ProviderPlugin`
- Produces: `ProviderErrorCode` and `ProviderError`
- Produces plugin keys: `generic`, `gmail`, `outlook`, `qq`, `netease`, `icloud`, `sina`.

- [x] **Step 1: Write provider contract tests**

Each plugin must pass the same suite:

```python
class ProviderContractMixin:
    provider_key: str

    def test_capabilities_are_explicit(self):
        plugin = ProviderRegistry.default().get(self.provider_key)
        caps = plugin.capabilities()
        self.assertIsInstance(caps.supports_idle, bool)
        self.assertGreaterEqual(caps.max_parallel_connections, 1)
        self.assertGreaterEqual(caps.recommended_poll_seconds, 60)

    def test_special_mailbox_mapping_preserves_native_key(self):
        plugin = ProviderRegistry.default().get(self.provider_key)
        mapped = plugin.map_mailbox(native_key="INBOX", attributes={"\\Inbox"})
        self.assertEqual(mapped.native_key, "INBOX")
        self.assertEqual(mapped.semantic_key, "inbox")
```

Also assert plugins do not import `flymail.repositories`, `flymail.infrastructure.db`, FastAPI or object-store modules.

- [x] **Step 2: Run tests and verify missing contracts**

Run `tests.v2.test_provider_contracts`; expected FAIL.

- [x] **Step 3: Define exact capability model**

```python
@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_idle: bool
    supports_move: bool
    supports_uidplus: bool
    supports_condstore: bool
    supports_qresync: bool
    supports_gmail_labels: bool
    supports_special_use: bool
    supports_smtp_utf8: bool
    supports_oauth: bool
    auto_saves_sent_copy: bool
    max_parallel_connections: int
    recommended_poll_seconds: int
    idle_refresh_seconds: int
    max_fetch_batch: int
    max_attachment_bytes: int
```

- [x] **Step 4: Define ProviderPlugin protocol**

Required methods:

```python
class ProviderPlugin(Protocol):
    key: str
    def capabilities(self) -> ProviderCapabilities: ...
    def default_endpoints(self) -> ProviderEndpoints: ...
    def map_mailbox(self, native_key: str, attributes: set[str]) -> MailboxMapping: ...
    def classify_error(self, operation: str, response: object) -> ProviderError: ...
    def sent_copy_strategy(self) -> SentCopyStrategy: ...
    def normalize_labels(self, raw_labels: Sequence[str]) -> tuple[str, ...]: ...
```

- [x] **Step 5: Implement plugins as data plus narrow overrides**

Generic plugin uses user-supplied endpoints. Gmail plugin declares label support and auto-saved sent copies. Outlook, QQ, NetEase, iCloud and Sina declare verified default endpoints and conservative connection limits from existing project code; do not invent provider-specific behavior not present in current code or official protocol responses.

- [x] **Step 6: Implement stable error classification**

Exact user-facing categories:

```text
authentication_failed, authorization_required, connection_failed,
rate_limited, mailbox_not_found, message_not_found, message_too_large,
unsupported_operation, server_rejected, temporary_server_error, protocol_error
```

`ProviderError` carries retryability and safe detail; raw server response stays in debug context with credential redaction.

- [x] **Step 7: Run contract tests and commit**

```bash
cd backend
python -m unittest tests.v2.test_provider_contracts -v
git add backend/flymail/providers backend/tests/v2/test_provider_contracts.py
git commit -m "🔌 建立 V2 邮箱服务商能力插件合同"
```

---

### Task 2: 实现串行 IMAP 会话、能力发现和安全生命周期

**Files:**

- Create: `backend/flymail/providers/core/imap_session.py`
- Create: `backend/flymail/providers/core/imap_commands.py`
- Create: `backend/flymail/providers/core/rate_limit.py`
- Create: `backend/tests/v2/test_imap_session.py`

**Interfaces:**

- Produces: `ImapSession.connect(credentials, endpoint, proxy) -> ImapSession`
- Produces: `ImapSession.execute(command: ImapCommand[T]) -> T`
- Produces: `ImapSession.select(mailbox_native_key: str) -> SelectedMailbox`
- Produces: `ImapSession.idle(events: AsyncIterator[IdleEvent])`
- Produces: `AccountConnectionLimiter`.

- [x] **Step 1: Write concurrency and shutdown tests**

Use a fake transport that records command start/end. Prove:

- two concurrent `execute` calls are serialized;
- cancellation releases the command lock;
- `BYE` marks session failed and rejects later commands;
- switching mailbox updates selected state only after success;
- disconnect is idempotent;
- raw password or OAuth token never enters repr or logs;
- per-account connection limit rejects a third normal connection while two are active.

- [x] **Step 2: Run tests and confirm failure**

Expected: FAIL.

- [x] **Step 3: Implement explicit session state machine**

States:

```text
disconnected, connecting, authenticated, selected, idling, closing, failed
```

Every command acquires one `asyncio.Lock`. IDLE temporarily owns the session and must exit before other commands run.

- [x] **Step 4: Implement capability discovery**

After authentication, parse standard capability tokens and return immutable set. Merge server capabilities with plugin policy; plugin may disable an unreliable advertised feature but cannot claim a server feature that is absent without a separate extension probe.

- [x] **Step 5: Implement timeout and cancellation boundaries**

Use operation-specific timeouts. Timeout closes the session because IMAP command stream state may be ambiguous. Callers receive classified retryable error.

- [x] **Step 6: Implement rate-limit feedback**

`AccountConnectionLimiter` tracks account and provider concurrency. On classified limit errors it reduces available permits and records cooldown; successful periods recover one permit at a time.

- [x] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_imap_session -v
git add backend/flymail/providers/core/imap_session.py backend/flymail/providers/core/imap_commands.py backend/flymail/providers/core/rate_limit.py backend/tests/v2/test_imap_session.py
git commit -m "📡 实现 V2 串行 IMAP 会话与连接限流"
```

---

### Task 3: 实现 BODYSTRUCTURE 和 MIME part 选择

**Files:**

- Create: `backend/flymail/providers/core/bodystructure.py`
- Create: `backend/flymail/providers/core/mime_parts.py`
- Create: `backend/flymail/domain/mail.py`
- Create: `backend/tests/v2/test_bodystructure.py`
- Create: protocol fixtures under `backend/tests/v2/fixtures/imap/`

**Interfaces:**

- Produces: `MimePart`
- Produces: `parse_bodystructure(raw: object) -> MimeTree`
- Produces: `select_message_parts(tree: MimeTree, html_body: str | None = None) -> PartSelection`
- Produces: `build_partial_fetch(imap_part: str, offset: int, count: int) -> str`

- [ ] **Step 1: Add MIME fixture tests**

Fixtures must cover:

- plain text only;
- multipart/alternative text and HTML;
- multipart/related HTML plus CID image;
- nested multipart/mixed with ordinary attachment;
- `message/rfc822` attachment;
- missing filename;
- malformed but recoverable BODYSTRUCTURE;
- invalid part number.

Assertions include exact `imap_part` values such as `1`, `1.1`, `1.2`, `2.1`.

- [ ] **Step 2: Verify failure**

Run `tests.v2.test_bodystructure`; expected FAIL.

- [ ] **Step 3: Define immutable part model**

```python
@dataclass(frozen=True, slots=True)
class MimePart:
    imap_part: str
    content_type: str
    charset: str | None
    transfer_encoding: str | None
    disposition: str | None
    filename: str | None
    content_id: str | None
    size: int
    children: tuple["MimePart", ...] = ()
```

- [ ] **Step 4: Implement recursive part numbering**

Part numbers come from BODYSTRUCTURE tree position, never from `email.walk()`. Validate against `^[1-9][0-9]*(\.[1-9][0-9]*)*$`.

- [ ] **Step 5: Implement body selection**

Rules:

1. choose HTML and text alternatives when both exist;
2. preserve both references for cache and fallback;
3. identify inline candidates by Content-ID and related context;
4. ordinary attachments remain metadata only;
5. fetch inline images only when sanitized HTML actually references their CID;
6. treat nested `message/rfc822` as attachment unless explicitly opened.

- [ ] **Step 6: Implement safe partial fetch syntax**

```python
def build_partial_fetch(imap_part: str, offset: int, count: int) -> str:
    validate_part(imap_part)
    if offset < 0 or count <= 0:
        raise ValueError("invalid partial range")
    return f"BODY.PEEK[{imap_part}]<{offset}.{count}>"
```

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_bodystructure -v
git add backend/flymail/providers/core/bodystructure.py backend/flymail/providers/core/mime_parts.py backend/flymail/domain/mail.py backend/tests/v2/test_bodystructure.py backend/tests/v2/fixtures/imap
git commit -m "✉️ 实现 V2 MIME 结构与真实 Part 解析"
```

---

### Task 4: 实现摘要摄取、远端实例、标签关系和标准会话

**Files:**

- Create: `backend/flymail/domain/threading.py`
- Create: `backend/flymail/repositories/mailboxes.py`
- Create: `backend/flymail/repositories/messages.py`
- Create: `backend/flymail/repositories/threads.py`
- Create: `backend/flymail/workers/ingestion.py`
- Create: `backend/tests/v2/test_message_ingestion.py`

**Interfaces:**

- Produces: `RemoteSummary`
- Produces: `MessageIngestionService.ingest_batch(account, mailbox, summaries) -> IngestionResult`
- Produces: `ThreadResolver.resolve(user_uid, headers) -> str`
- Produces batch Repository methods for messages, remote instances, memberships and projections.

- [ ] **Step 1: Write ingestion tests**

Tests prove:

- a batch is written in one transaction;
- duplicate `(account, mailbox, uidvalidity, uid)` updates instead of duplicating;
- Gmail same stable message ID under two labels creates one message and two memberships;
- cross-account standard References chain creates one user-level thread;
- same subject without header relation does not merge unless conservative fallback participant and time rules match;
- user A Message-ID never merges into user B thread;
- UIDVALIDITY change creates new remote identity and marks old instance for reconciliation;
- projection counts and latest message are correct.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Define summary normalization**

Normalize headers without discarding originals needed for diagnostics. Canonical message key order:

1. provider stable message ID when available, scoped to account;
2. normalized Message-ID scoped to user;
3. fallback SHA-256 over account, mailbox, UIDVALIDITY, UID, date, size and normalized sender.

- [ ] **Step 4: Implement thread resolver**

Use `References` from oldest to newest, then `In-Reply-To`, then own Message-ID. The fallback subject rule requires normalized subject, overlapping participants and bounded time window; log only the reason code, not full subject.

- [ ] **Step 5: Implement batch Repository methods**

Required methods accept lists and use `executemany` or bounded multi-row SQL. No per-message commit. Return internal IDs using stable lookup keys after upsert.

- [ ] **Step 6: Update thread projection in transaction**

Projection fields include latest time, latest subject, participant summary, message count, unread count, star state, attachment flag, account count, pending operation count and latest snippet.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_message_ingestion -v
git add backend/flymail/domain/threading.py backend/flymail/repositories/mailboxes.py backend/flymail/repositories/messages.py backend/flymail/repositories/threads.py backend/flymail/workers/ingestion.py backend/tests/v2/test_message_ingestion.py
git commit -m "🧵 实现 V2 邮件摄取标签关系与标准会话"
```

---

### Task 5: 实现 Worker 调度器、队列优先级和账号公平性

**Files:**

- Create: `backend/flymail/workers/dispatcher.py`
- Create: `backend/flymail/workers/scheduler.py`
- Modify: `backend/v2_worker.py`
- Create: `backend/tests/v2/test_worker_scheduler.py`

**Interfaces:**

- Produces: `JobHandler` protocol
- Produces: `WorkerDispatcher.register(kind: str, handler: JobHandler)`
- Produces: `FairScheduler.next_claims(now: float) -> list[ClaimRequest]`
- Produces queues: `interactive`, `operations`, `realtime`, `reconcile`, `history`, `maintenance`.

- [ ] **Step 1: Write scheduler tests**

Prove:

- P0 interactive claims before history;
- continuous P0 load still gives history a bounded share;
- one account cannot consume all global slots;
- one provider cooldown does not block another provider;
- disabled or auth-required account jobs are not claimed;
- graceful shutdown stops new claims and allows current short jobs to release leases.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement weighted fair selection**

Default weights:

```text
interactive 8
operations 6
realtime 6
reconcile 3
history 1
maintenance 1
```

Apply per-account and per-provider caps before claiming. Persist priority and queue in MySQL; in-memory scheduler only decides current claim mix.

- [ ] **Step 4: Implement handler registry**

Unknown job kinds fail permanently with safe error. Handler signature:

```python
class JobHandler(Protocol):
    async def __call__(self, context: JobContext, payload: Mapping[str, object]) -> JobOutcome: ...
```

- [ ] **Step 5: Wire Worker lifecycle**

`v2_worker.py` starts dispatcher, heartbeat, lease reaper and scheduler in one `asyncio.TaskGroup`. SIGTERM stops claiming, waits bounded grace period, releases leases and closes sessions/pools.

- [ ] **Step 6: Run tests repeatedly and commit**

```bash
for i in $(seq 1 10); do python -m unittest tests.v2.test_worker_scheduler -q || exit 1; done
git add backend/flymail/workers/dispatcher.py backend/flymail/workers/scheduler.py backend/v2_worker.py backend/tests/v2/test_worker_scheduler.py
git commit -m "⚖️ 实现 V2 Worker 公平调度与优先队列"
```

---

### Task 6: 实现 IMAP IDLE 和自适应周期校正

**Files:**

- Create: `backend/flymail/workers/idle.py`
- Create: `backend/flymail/workers/reconciliation.py`
- Create: `backend/tests/v2/test_idle_reconciliation.py`

**Interfaces:**

- Produces: `IdleSupervisor.run_account(account_id: str) -> None`
- Produces: `ReconciliationPlanner.plan(account_state, now) -> ReconciliationPlan`
- Produces job kinds: `sync.incremental`, `sync.reconcile`, `sync.initial`, `sync.mailbox_refresh`.

- [ ] **Step 1: Write IDLE and cadence tests**

Tests cover:

- EXISTS, EXPUNGE and FLAGS events enqueue one deduplicated incremental job;
- no MIME parsing or message database write occurs on the IDLE session;
- IDLE refreshes before plugin timeout;
- unsupported IDLE falls back to polling;
- network recovery enqueues immediate reconcile;
- active interval is 5 minutes, normal 15, quiet 30;
- failures exponentially back off with jitter;
- account A failure does not delay account B plan.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement IDLE supervisor**

One supervisor per enabled account. It obtains one IDLE permit, listens, converts events to deduped Outbox/job entries, exits on shutdown, credential change or account disable.

- [ ] **Step 4: Implement adaptive state transitions**

State calculation inputs:

- last user view;
- recent change count;
- pending operation count;
- consecutive failures;
- provider minimum interval;
- current cooldown.

Output is one of active, normal, quiet, degraded or auth_required with exact next reconcile time.

- [ ] **Step 5: Implement reconciliation phases**

Each mailbox task performs bounded work:

1. capability/cursor check;
2. new and changed summary fetch;
3. remote deletion/membership comparison;
4. flag and label reconciliation;
5. cursor update;
6. enqueue body work separately.

A history task processes one bounded batch and re-enqueues itself with updated cursor.

- [ ] **Step 6: Run tests and commit**

```bash
python -m unittest tests.v2.test_idle_reconciliation -v
git add backend/flymail/workers/idle.py backend/flymail/workers/reconciliation.py backend/tests/v2/test_idle_reconciliation.py
git commit -m "🔄 实现 V2 IDLE 与自适应周期校正"
```

---

### Task 7: 实现分层正文、内嵌图片、普通附件和原始源码获取

**Files:**

- Create: `backend/flymail/workers/content_fetch.py`
- Modify: `backend/flymail/repositories/messages.py`
- Modify: `backend/flymail/repositories/objects.py`
- Create: `backend/tests/v2/test_content_fetch.py`

**Interfaces:**

- Produces job kinds: `content.body`, `content.inline`, `content.attachment`, `content.raw_eml`, `content.evict`.
- Produces: `ContentFetchService.fetch_body(...)`, `fetch_attachment(...)`, `fetch_raw_eml(...)`.
- Consumes: `ObjectStore`, `MimeTree`, message and object repositories.

- [ ] **Step 1: Write content fetch tests**

Prove:

- body fetch issues exact `BODY.PEEK[part]` requests;
- ordinary attachment metadata exists before bytes are downloaded;
- opening body does not fetch ordinary attachment bytes;
- only HTML-referenced CID images are queued;
- attachment fetch uses exact part and streams to object store;
- duplicate attachment content reuses object;
- raw `.eml` is only fetched for explicit job;
- body state transitions follow allowed state machine;
- concurrent same-content request deduplicates by job key;
- eviction deletes body search document and reference, not message metadata.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement body state transition guard**

Allowed transitions:

```text
not_requested -> queued
queued -> fetching | failed
fetching -> ready | failed | unavailable
evicted -> queued
failed -> queued
ready -> evicted
```

Reject illegal transitions with `ConflictError`.

- [ ] **Step 4: Implement precise body fetch**

Fetch selected text/HTML parts, decode transfer encoding and charset with bounded memory, sanitize before storage, write compressed object when compression saves space, attach references and update body state in one database transaction after object durability.

- [ ] **Step 5: Implement CID fetch**

Parse sanitized HTML references, normalize Content-ID and enqueue only matched allowed image parts below size limit. Replace CID with authenticated API reference token identifier, not a public object hash.

- [ ] **Step 6: Implement attachment and raw source fetch**

Use partial ranges when provider/core supports reliable chunking. Enforce declared and actual size limits. Cancellation deletes temp files. A raw source task may use full RFC 822 fetch and is tagged for body quota/LRU.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_content_fetch -v
git add backend/flymail/workers/content_fetch.py backend/flymail/repositories/messages.py backend/flymail/repositories/objects.py backend/tests/v2/test_content_fetch.py
git commit -m "⚡ 实现 V2 分层正文与附件按需获取"
```

---

### Task 8: 实现本地优先操作、冲突规则和两阶段删除

**Files:**

- Create: `backend/flymail/domain/operations.py`
- Create: `backend/flymail/repositories/operations.py`
- Create: `backend/flymail/workers/operation_apply.py`
- Create: `backend/tests/v2/test_operation_apply.py`

**Interfaces:**

- Produces operation kinds: `set_read`, `set_starred`, `add_label`, `remove_label`, `move`, `archive`, `trash`, `delete_permanent`.
- Produces: `OperationService.record_local_intent(...) -> str`
- Produces: `OperationApplyHandler`.
- Produces conflict outcomes: merged, superseded, conflict, terminal_missing, retry.

- [ ] **Step 1: Write operation tests**

Prove:

- local projection and operation row commit atomically;
- read and starred fields merge independently;
- later move supersedes an older pending move for the same remote instance;
- Gmail archive removes Inbox label;
- generic archive uses mapped archive mailbox;
- first delete creates trash operation;
- permanent delete requires message currently in trash and explicit confirmation flag;
- remote missing is terminal success;
- retries use same idempotency key;
- partial thread operation records per-message outcomes;
- stale remote version produces conflict or recomputed target, never blind replay.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement operation intent model**

Required fields include operation ID, user, account, remote instance, kind, target, observed remote version, idempotency key, status, retry count and safe error code.

- [ ] **Step 4: Implement local projection update**

Application service updates thread/message projection, writes operation and Outbox in one UoW. It does not call Provider.

- [ ] **Step 5: Implement apply handler**

Handler loads fresh remote state, invokes plugin/core operation, records exact outcome and emits realtime projection event. Unsupported direct MOVE uses safe copy/delete fallback only when plugin contract permits.

- [ ] **Step 6: Implement undo boundary**

Pending operations can be cancelled. Already-synced reversible operations create a compensating operation using captured previous state. Permanent delete is not presented as reversible after remote confirmation.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_operation_apply -v
git add backend/flymail/domain/operations.py backend/flymail/repositories/operations.py backend/flymail/workers/operation_apply.py backend/tests/v2/test_operation_apply.py
git commit -m "📬 实现 V2 本地优先操作与冲突合并"
```

---

### Task 9: 实现可靠 SMTP 发送和结果不确定校验

**Files:**

- Create: `backend/flymail/providers/core/smtp_client.py`
- Create: `backend/flymail/workers/sender.py`
- Modify: `backend/flymail/repositories/messages.py`
- Create: `backend/tests/v2/test_reliable_sender.py`

**Interfaces:**

- Produces: `MimeComposer.compose(send_command) -> ComposedMessage`
- Produces: `ReliableSender.handle(job_context, payload) -> JobOutcome`
- Produces send states: `queued`, `sending`, `sent`, `failed`, `verification_required`, `review_required`, `cancelled`.
- Produces job kinds: `send.deliver`, `send.verify`, `send.append_sent_copy`.

- [ ] **Step 1: Write reliable send tests**

Tests cover:

- stable Message-ID across retries;
- Bcc appears only in SMTP envelope, not public headers;
- reply carries In-Reply-To and References;
- account identity and Reply-To are validated;
- SMTP accepted result marks sent once;
- disconnect after DATA enters `verification_required`;
- verification finds Message-ID in sent mailbox and marks sent without resend;
- verification not found after bounded window permits one controlled retry;
- auto-save provider does not APPEND duplicate copy;
- provider requiring APPEND creates a separate idempotent job;
- scheduled send respects `available_at`;
- queued message can be cancelled before delivery begins.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement MIME composition**

Build deterministic headers from persisted command. Attachments stream from object store. Generate one Message-ID when send command is created and persist it. SMTPUTF8 is used only when plugin and server support it.

- [ ] **Step 4: Persist attempt before network call**

Create `send_attempts` row with operation ID, Message-ID, attempt number and state before SMTP connection. Do not store body or credential in attempts.

- [ ] **Step 5: Implement uncertain result policy**

When server acceptance cannot be determined, update state to `verification_required` and enqueue `send.verify`. The verification handler searches a bounded sent-mail window using Message-ID and provider stable identifiers.

- [ ] **Step 6: Implement sent-copy strategy**

Use plugin declaration. `auto_saves_sent_copy=True` skips APPEND. Otherwise enqueue exact composed source object for APPEND; UIDPLUS result is recorded when available.

- [ ] **Step 7: Run tests and commit**

```bash
python -m unittest tests.v2.test_reliable_sender -v
git add backend/flymail/providers/core/smtp_client.py backend/flymail/workers/sender.py backend/flymail/repositories/messages.py backend/tests/v2/test_reliable_sender.py
git commit -m "📤 实现 V2 可靠发送与重复投递防护"
```

---

### Task 10: 实现站内与第三方通知可靠分发

**Files:**

- Create: `backend/flymail/notifications/contracts.py`
- Create: `backend/flymail/notifications/channels.py`
- Create: `backend/flymail/notifications/image_publishers.py`
- Create: `backend/flymail/workers/notifications.py`
- Create: `backend/tests/v2/test_notification_dispatch.py`

**Interfaces:**

- Produces channel keys: `in_app`, `bark`, `telegram`, `wecom`, `dingtalk`, `feishu`, `generic_webhook`.
- Produces job kind: `notification.deliver`.
- Produces: `NotificationChannel.send(message: NotificationMessage, config: NotificationConfig, proxy: ProxyConfig | None) -> DeliveryResult`.
- Produces: `NotificationImagePublisher.publish(asset: StoredObject, config: ImagePublisherConfig, proxy: ProxyConfig | None) -> PublishedImage`.
- Produces event kinds for new mail, scheduled-send result, backup result, account authorization and system warning.

- [ ] **Step 1: Write notification contract and delivery tests**

Tests prove:

- new-mail event creates one in-app notification and deduplicated delivery jobs per enabled rule;
- Bark, Telegram, enterprise WeChat, DingTalk, Feishu and generic Webhook adapters map the same safe message model to channel-specific payloads;
- notification payload truncation never splits invalid Unicode and never includes full message body, credentials or unrestricted recipient lists;
- channel token, webhook secret and proxy password are loaded from encrypted configuration and absent from job payload/logs;
- retryable HTTP failures back off, permanent 4xx configuration errors stop retrying;
- one failed channel does not block other channels or in-app notification;
- Telegram and generic Webhook may reuse the user's configured outbound proxy only when the rule explicitly enables it;
- duplicate Outbox publication does not send the same channel delivery twice;
- disabled user/account/rule prevents delivery;
- notification image generation, when enabled, stores only a temporary `notification_asset` object and releases it after all deliveries finish;
- optional image publishing supports the maintained `flymail-imgbed` Cloudflare Worker contract and a generic reviewed HTTPS publisher, validates public endpoints, uses encrypted tokens, and deletes or lets expire the published image according to provider capability;
- a publisher failure degrades to text notification when the channel supports text and does not block in-app delivery.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd backend
python -m unittest tests.v2.test_notification_dispatch -v
```

Expected: FAIL because notification channel and Worker contracts do not exist.

- [ ] **Step 3: Define a channel-neutral message model**

```python
@dataclass(frozen=True, slots=True)
class NotificationMessage:
    event_id: str
    event_type: str
    title: str
    summary: str
    action_path: str | None
    occurred_at: float
    account_id: str | None = None
```

The model excludes raw HTML, attachment bytes, OAuth tokens and provider credentials. It may carry an internal `notification_asset_id` reference, never a public object hash or local filesystem path.

- [ ] **Step 4: Implement channel adapters**

Use existing `httpx` dependency. Each adapter owns only payload formatting and HTTP result classification. It cannot query MySQL, decrypt secrets or choose user rules. Generic Webhook validates public HTTPS/HTTP destinations under the project's outbound-network safety policy and rejects loopback, private, link-local and metadata endpoints unless an explicitly authorized internal endpoint policy is later approved.

- [ ] **Step 5: Implement optional image publisher adapters**

Define a narrow publisher contract. The `flymail-imgbed` adapter follows the repository template's authenticated upload/delete API; generic HTTPS publisher support is limited to an explicitly documented request/response contract. Both reuse outbound-network validation, keep token/password encrypted, return a bounded public URL, and never expose the local SHA-256 object path. Image publishing is optional per channel/rule, and text fallback is mandatory where supported.

- [ ] **Step 6: Implement notification Worker handler**

The handler loads the user-scoped channel/rule and decrypts its secret, renders a bounded message, selects optional proxy, optionally publishes a generated image, calls one channel adapter, records `notification_deliveries`, emits realtime status, requests published-image cleanup when supported, and releases temporary notification assets after the final reference disappears. Idempotency key is `(notification_event_id, channel_id)`.

- [ ] **Step 7: Register notification sources**

Outbox consumers create notification events for:

```text
mail.new
send.sent
send.failed
backup.completed
backup.failed
account.authorization_required
system.storage_warning
```

Rules decide channel delivery. In-app events remain available even when all external channels are disabled.

- [ ] **Step 8: Run tests and commit**

```bash
python -m unittest tests.v2.test_notification_dispatch -v
git add backend/flymail/notifications backend/flymail/workers/notifications.py backend/tests/v2/test_notification_dispatch.py
git commit -m "🔔 实现 V2 站内与第三方通知可靠分发"
```

---

### Task 11: 完成协议与 Worker 集成验收

**Files:**

- Modify: `backend/v2_worker.py`
- Create: `backend/tests/v2/test_protocol_worker_integration.py`
- Modify: `README.md`

**Interfaces:**

- Produces: Gate 2 evidence and stable job/provider contracts for API plan.

- [ ] **Step 1: Add integrated fake-provider scenario**

Using a deterministic fake IMAP/SMTP server, test:

1. account discovery;
2. initial summary synchronization;
3. Gmail-like multi-label membership;
4. thread construction;
5. IDLE new-message event;
6. exact body part fetch;
7. inline CID fetch;
8. ordinary attachment on-demand fetch;
9. local read and move operations;
10. reliable send with uncertain-result verification;
11. Worker restart with pending jobs;
12. account A slow/failing while account B completes;
13. new-mail, send-result and backup-result notifications deliver independently across enabled channels.

- [ ] **Step 2: Register all handlers in V2 Worker**

Handler registry must contain explicit job kind mapping. Startup fails if a persisted runnable job kind has no registered handler in the current schema version.

- [ ] **Step 3: Run all V2 protocol tests**

```bash
cd backend
FLYMAIL_TEST_DATABASE_URL='mysql://...' python -m unittest \
  tests.v2.test_provider_contracts \
  tests.v2.test_imap_session \
  tests.v2.test_bodystructure \
  tests.v2.test_message_ingestion \
  tests.v2.test_worker_scheduler \
  tests.v2.test_idle_reconciliation \
  tests.v2.test_content_fetch \
  tests.v2.test_operation_apply \
  tests.v2.test_reliable_sender \
  tests.v2.test_notification_dispatch \
  tests.v2.test_protocol_worker_integration -v
```

Expected: all PASS.

- [ ] **Step 4: Run foundation and legacy regressions**

Run all `tests/v2` and legacy `backend/tests`. Expected: PASS.

- [ ] **Step 5: Search for forbidden full-message fetches**

```bash
rg -n 'BODY\.PEEK\[\]|RFC822\b' backend/flymail
```

Expected: matches only the explicit raw `.eml` handler or documented MIME fallback path. Body and attachment handlers must not match.

- [ ] **Step 6: Update README development architecture**

Document Provider plugin boundaries, IDLE plus reconciliation, exact part fetching, layered cache and Worker recovery. State clearly that V2 is not yet the active production entry.

- [ ] **Step 7: Commit and push Gate 2**

```bash
git add backend/v2_worker.py backend/tests/v2/test_protocol_worker_integration.py README.md
git commit -m "✅ 验证 V2 协议核心与同步 Worker"
git push origin main
```

## Gate 2 Completion Checklist

- [ ] Seven provider plugins pass one contract suite.
- [ ] IMAP commands are serialized per connection.
- [ ] BODYSTRUCTURE yields real part specifiers.
- [ ] Summary synchronization never downloads ordinary attachments.
- [ ] Body and attachment fetches avoid full-message MIME.
- [ ] Gmail labels use memberships without duplicate body storage.
- [ ] IDLE only enqueues lightweight work.
- [ ] Adaptive reconciliation follows 5/15/30-minute policy and backoff.
- [ ] Fair scheduler prevents account starvation.
- [ ] Local operations are atomic, recoverable and conflict-aware.
- [ ] Two-stage deletion is enforced.
- [ ] Reliable sender produces zero duplicate deliveries in tests.
- [ ] In-app and external notification delivery is encrypted, idempotent and channel-isolated.
- [ ] Worker restart restores leases, operations, notifications and send verification.
- [ ] Production container and data remain untouched.
