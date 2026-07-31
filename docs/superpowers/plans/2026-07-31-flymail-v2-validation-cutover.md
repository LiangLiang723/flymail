# FlyMail V2 验证、单容器运行与切换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已完成功能的 V2 后端、Worker 和前端接入正式单容器入口，建立可观测性、健康检查、容量与故障验证、真实邮箱和浏览器验收，并在明确确认后安全替换生产数据和当前容器。

**Architecture:** 正式容器内由入口脚本监督 MySQL、FastAPI API 和独立 Worker。基础健康只依赖 API、MySQL、Worker 心跳和对象存储，不依赖第三方邮箱；性能、故障和容量测试全部在独立临时数据目录完成。正式切换以完整旧数据快照为回滚边界，V2 不读取旧数据库。

**Tech Stack:** Python 3、FastAPI、MySQL 8.0、Vue 3、Docker、Bash、curl、MySQL CLI、unittest、Node.js test runner、Linux process signals。

## Global Constraints

- 必须完成 Gate 1、Gate 2、Gate 3 和 Gate 4。
- 继承总路线图全部约束。
- 在 Task 10 Step 3 获得明确破坏性确认之前，禁止停止或替换生产 `flymail` 容器，禁止删除、移动或修改 `/Docker/flymail/data`。
- 所有临时容器使用唯一名称、唯一端口、唯一 `/tmp` 数据目录和唯一数据库密码。
- MySQL 必须只监听 `127.0.0.1:3306`，不得发布数据库端口。
- 入口脚本必须在关键进程退出时安全关闭其他进程并让容器退出，禁止无限静默重启。
- API 停止接收新写请求后，Worker 才停止领取新任务；SMTP 结果不确定必须持久化为 `verification_required`。
- 健康接口不得因单个第三方邮箱故障返回容器不健康。
- 性能目标必须通过测量，不得从代码审查推断。
- 容量和故障测试不得使用真实邮件、真实附件或生产数据库副本。
- 日志和镜像元数据不得包含密码、令牌、Cookie、会话密钥、完整数据库 URL 或真实邮件正文。
- 默认不上传 Docker Hub。
- 最终版本号和生产清空动作都必须在执行时单独确认；本计划不能替代确认。

## File Map

**Create:**

- `backend/flymail/observability/logging.py`
- `backend/flymail/observability/metrics.py`
- `backend/flymail/observability/timing.py`
- `backend/flymail/observability/health.py`
- `backend/flymail/workers/main.py`
- `backend/worker.py`
- `backend/tests/v2/test_observability.py`
- `backend/tests/v2/test_runtime_shutdown.py`
- `backend/tests/v2/test_fault_injection.py`
- `backend/tests/v2/test_capacity_queries.py`
- `scripts/test-v2-container.sh`
- `scripts/generate-v2-benchmark-data.py`
- `scripts/check-v2-secrets.sh`
- `docs/benchmarks/flymail-v2-methodology.md`
- `docs/operations/flymail-v2-backup-restore.md`
- `docs/operations/flymail-v2-cutover.md`
- `docs/operations/flymail-v2-provider-validation.md`

**Modify:**

- `backend/main.py`：最终切换为 V2 API 入口。
- `frontend/src/main.ts`：最终切换为 V2 前端入口。
- `scripts/docker-entrypoint.sh`：启动 MySQL、Worker 和 API，监督与优雅关闭。
- `Dockerfile`：复制正式 Worker、V2 前端构建产物和测试所需运行文件。
- `docker-compose.yml`：保持单容器与 `/data`，同步最终镜像版本。
- `.env.example`：增加或澄清 V2 必要配置，不添加真实秘密。
- `README.md`：全面替换为 V2 部署、能力、边界和恢复说明。
- `VERSION`、`package.json`、`frontend/package.json`：最终确认版本后由 `npm run sync-version` 同步。
- `.gitignore`：忽略基准输出和临时验证目录，但保留方法文档与小型基准摘要。

---

### Task 1: 实现结构化日志、分段计时、健康和管理员诊断

**Files:**

- Create: `backend/flymail/observability/logging.py`
- Create: `backend/flymail/observability/metrics.py`
- Create: `backend/flymail/observability/timing.py`
- Create: `backend/flymail/observability/health.py`
- Modify: `backend/flymail/api/middleware.py`
- Modify: `backend/flymail/api/app.py`
- Modify: `backend/flymail/workers/dispatcher.py`
- Create: `backend/tests/v2/test_observability.py`

**Interfaces:**

- Produces: `get_safe_logger(component: str)`.
- Produces: `RequestTiming` and `JobTiming` collectors.
- Produces: basic `/api/health` and admin `/api/v2/admin/diagnostics`.
- Produces `Server-Timing` fields: db, object, serialize.

- [ ] **Step 1: Write log-redaction and timing tests**

Tests inject values containing:

- database password;
- email authorization code;
- OAuth token;
- session secret;
- Cookie;
- full database URL;
- message body and attachment filename.

Assert none appear in formatted logs. Also assert request timing reports total and bounded components, and Worker metrics include queue wait, execution, retries and byte counts without message content.

- [ ] **Step 2: Verify failure**

Expected: FAIL.

- [ ] **Step 3: Implement centralized redaction**

Redact by field name and URL parser, not by replacing one known secret string. Allowed structured fields:

```text
timestamp, level, component, request_id, trace_id, job_id,
account_id_masked, provider, operation, error_class, duration_ms,
queue_wait_ms, bytes_in, bytes_out, result_count, cache_state
```

- [ ] **Step 4: Implement request and job timing**

Repository/UoW helper records database wait/query durations in request/job context. Object store records read/write/decompress. Do not use high-cardinality subject, email or Message-ID labels in aggregate metrics.

- [ ] **Step 5: Implement health semantics**

Basic response:

```json
{
  "status": "ok",
  "app": "flymail",
  "version": "0.0.25",
  "api": "ok",
  "database": "ok",
  "worker": "ok",
  "object_store": "ok"
}
```

Admin diagnostics additionally returns safe pool counts, queue counts, stale heartbeat count, free disk bytes and last maintenance result. The displayed `0.0.25` is the planning-time example; implementation must read the active value from `VERSION` and the release task later confirms the final value.

- [ ] **Step 6: Run tests and commit**

```bash
cd backend
python -m unittest tests.v2.test_observability -v
git add backend/flymail/observability backend/flymail/api/middleware.py backend/flymail/api/app.py backend/flymail/workers/dispatcher.py backend/tests/v2/test_observability.py
git commit -m "📊 建立 V2 安全日志性能计时与健康诊断"
```

---

### Task 2: 切换源码正式入口并实现 API/Worker 优雅关闭

**Files:**

- Create: `backend/flymail/workers/main.py`
- Create: `backend/worker.py`
- Modify: `backend/main.py`
- Modify: `frontend/src/main.ts`
- Remove after verification: `backend/v2_dev.py`, `backend/v2_worker.py`, `frontend/src/v2-main.ts`, `frontend/v2.html`
- Create: `backend/tests/v2/test_runtime_shutdown.py`

**Interfaces:**

- Produces formal commands:
  - API: `python -m uvicorn main:app --host 0.0.0.0 --port 8080`
  - Worker: `python worker.py`
- Produces process readiness and maintenance-mode hooks used by entrypoint.

- [ ] **Step 1: Write runtime entry and signal tests**

Tests prove:

- importing `main.app` creates V2 app and imports no legacy routers;
- `worker.py` invokes V2 Worker main;
- SIGTERM stops new job claims;
- current short job completes or lease releases within grace period;
- active IDLE sessions disconnect;
- uncertain SMTP attempt remains verification-required;
- database pools close;
- application does not swallow cancellation indefinitely.

- [ ] **Step 2: Verify failure before switching**

Expected: tests fail because formal entries still point to legacy behavior.

- [ ] **Step 3: Switch backend entries**

Replace `backend/main.py` with a thin V2 app creation entry. Create `backend/worker.py` as a thin async main wrapper. Do not keep compatibility imports or conditional environment flags selecting legacy behavior.

- [ ] **Step 4: Switch frontend entry**

Move V2 app startup from `v2-main.ts` into `main.ts`. Update default `index.html`/Vite input. Remove V2 development HTML only after normal `npm run build` produces V2 UI.

- [ ] **Step 5: Remove development entries and verify references**

Run:

```bash
rg -n 'v2_dev|v2_worker|v2-main|v2\.html|routes\.(messages|accounts|compose)' backend frontend README.md Dockerfile scripts
```

Expected: no runtime reference to development entries or legacy route modules. Test fixtures may mention removed names only when asserting absence.

- [ ] **Step 6: Run backend and frontend tests**

Run all V2 and full current suites. Expected: V2 is now the normal build and tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/main.py backend/worker.py backend/flymail/workers/main.py frontend/src/main.ts frontend/vite.config.ts backend/tests/v2/test_runtime_shutdown.py
git rm backend/v2_dev.py backend/v2_worker.py frontend/src/v2-main.ts frontend/v2.html
git commit -m "🚦 切换 FlyMail V2 正式 API Worker 与前端入口"
```

---

### Task 3: 重写单容器入口脚本和 Docker 构建

**Files:**

- Modify: `scripts/docker-entrypoint.sh`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Create: `scripts/test-v2-container.sh`
- Modify or create tests: `backend/tests/test_docker_entrypoint.py`, `backend/tests/v2/test_runtime_shutdown.py`

**Interfaces:**

- Entrypoint supervises `mysqld`, `flymail-worker` and `flymail-api`.
- API readiness requires migrations complete, Worker heartbeat current and object store writable.

- [ ] **Step 1: Write entrypoint contract tests**

Tests inspect and execute controlled stubs to prove:

- required environment variables validated without echoing values;
- MySQL starts on `127.0.0.1` only;
- migration failure prevents API/Worker startup;
- Worker starts before API readiness;
- Worker exit causes API and MySQL safe shutdown and container nonzero exit;
- API exit causes Worker and MySQL safe shutdown;
- SIGTERM orders API drain, Worker drain, MySQL shutdown;
- no real password is in Dockerfile ENV;
- health command checks V2 `/api/health`.

- [ ] **Step 2: Run tests and verify expected legacy mismatch**

Expected: FAIL until entrypoint supports Worker.

- [ ] **Step 3: Implement process supervision**

Entrypoint sequence:

1. validate env and create `/data/mysql`, `/data/mysql-files`, `/data/flymail` trees;
2. initialize/start MySQL;
3. create internal DB/user with safe SQL escaping;
4. generate internal `DATABASE_URL` in process environment only;
5. run migration command once;
6. start Worker and wait for heartbeat row;
7. start API;
8. monitor all PIDs with `wait -n` compatible loop;
9. on any critical exit, execute ordered shutdown;
10. return failing process exit code.

- [ ] **Step 4: Preserve password safety**

Use MySQL client mechanisms that avoid password command-line exposure. Application logs use redacted URL. Docker image history and config must not contain actual test values.

- [ ] **Step 5: Update Dockerfile build**

Build V2 frontend through normal `npm run build`. Copy only runtime backend source, static distribution, VERSION and entrypoint. Do not include tests, temporary benchmark data, `.env`, node_modules or Git metadata in final image.

- [ ] **Step 6: Implement reusable smoke script**

`scripts/test-v2-container.sh` must:

- require an image tag argument;
- create unique temp data directory and container name;
- generate random test secrets including a database password with special characters;
- select an available host port;
- start container;
- wait for healthy with timeout;
- run API/MySQL/object/persistence checks;
- restart container and recheck;
- stop container and inspect safe MySQL shutdown log;
- clean only its own temp resources in `trap`;
- reject `/Docker/flymail/data` as an input path.

- [ ] **Step 7: Validate syntax and compose**

```bash
bash -n scripts/docker-entrypoint.sh
bash -n scripts/test-v2-container.sh
docker compose --env-file .env.example config
```

- [ ] **Step 8: Commit**

```bash
git add scripts/docker-entrypoint.sh scripts/test-v2-container.sh Dockerfile docker-compose.yml backend/tests/test_docker_entrypoint.py backend/tests/v2/test_runtime_shutdown.py
git commit -m "🐳 重构 V2 单容器双进程启动与安全关闭"
```

---

### Task 4: 建立真实临时容器、持久化和秘密扫描验证

**Files:**

- Create: `scripts/check-v2-secrets.sh`
- Modify: `scripts/test-v2-container.sh`
- Create: `docs/benchmarks/flymail-v2-methodology.md`

**Interfaces:**

- Produces a deterministic smoke report file under a unique temporary directory.
- Produces secret scan exit code `0` only when no forbidden values appear.

- [ ] **Step 1: Build local release-candidate image**

Use a temporary tag based on Git SHA:

```bash
IMAGE="benxianyu/flymail:v2-rc-$(git rev-parse --short HEAD)"
docker build -t "$IMAGE" .
```

- [ ] **Step 2: Run smoke test with hostile password**

The script must use a generated password containing at least quote, backslash, `@`, `:`, `/` and `%`. Validate:

- container reaches healthy;
- `/api/health` returns actual VERSION;
- Worker heartbeat is current;
- MySQL reports 8.0 and `/data/mysql/`;
- bind address is `127.0.0.1`;
- `/data/flymail` object/config/log directories exist;
- create user/draft/test object through V2 API or controlled integration command;
- restart container;
- data and object remain;
- job lease recovery works;
- MySQL shuts down cleanly.

- [ ] **Step 3: Implement secret scan**

Scan:

- `docker inspect` Config.Env and labels;
- `docker history --no-trunc`;
- container logs;
- generated application logs;
- Git diff/staged files.

Forbidden exact test secrets must not occur. Also match patterns for unredacted MySQL URLs, Authorization headers and session secrets. Avoid false positives on `.env.example` placeholder names.

- [ ] **Step 4: Verify no MySQL host publication**

Inspect container ports and compose config. Only port 8080 may be exposed/published.

- [ ] **Step 5: Record methodology, not secrets**

Document commands, resource limits, data scale and result schema. Do not commit generated passwords, raw logs or temp paths.

- [ ] **Step 6: Commit**

```bash
git add scripts/check-v2-secrets.sh scripts/test-v2-container.sh docs/benchmarks/flymail-v2-methodology.md
git commit -m "🔍 建立 V2 容器持久化与秘密扫描验证"
```

---

### Task 5: 建立故障注入和恢复测试

**Files:**

- Create: `backend/tests/v2/test_fault_injection.py`
- Modify: relevant Worker/Object Store tests only to expose injectable boundaries.
- Create: `docs/benchmarks/flymail-v2-fault-results.md` after measured run.

**Interfaces:**

- Produces deterministic failure injectors for database, object store, IMAP transport, SMTP transport and process signals.

- [ ] **Step 1: Add failure injection hooks restricted to tests**

Use dependency injection/fake transports, not production environment backdoors. No `FLYMAIL_CHAOS_MODE` production variable.

- [ ] **Step 2: Implement required failure cases**

Automated tests cover:

1. API terminated before transaction commit;
2. API terminated immediately after commit;
3. Worker terminated after lease and before remote call;
4. Worker terminated after remote operation and before local completion;
5. MySQL unavailable during claim and during completion;
6. object temp write fails or disk full;
7. object file missing after database reference;
8. IMAP body/attachment stream disconnect;
9. SMTP disconnect after DATA;
10. Outbox publication crash after event persistence;
11. IDLE half-open timeout;
12. service provider rate limit storm;
13. container SIGTERM during history batch;
14. system clock adjustment around scheduled send.

- [ ] **Step 3: Assert safety invariants**

Every scenario verifies:

- no lost committed task;
- no duplicate SMTP delivery;
- no wrong-user access;
- no deletion of referenced object;
- explicit recoverable/permanent/uncertain state;
- restart recovery within specified bound;
- safe user-visible error code.

- [ ] **Step 4: Run tests repeatedly**

Run fault suite at least 20 iterations for concurrency cases. Record test count and zero invariant failures.

- [ ] **Step 5: Commit result summary**

Commit only concise measured results, environment and command hashes, not noisy raw logs.

```bash
git add backend/tests/v2/test_fault_injection.py docs/benchmarks/flymail-v2-fault-results.md
git commit -m "🧪 验证 V2 故障恢复与重复发送防护"
```

---

### Task 6: 建立 2,000 万摘要容量数据和性能门槛

**Files:**

- Create: `scripts/generate-v2-benchmark-data.py`
- Create: `backend/tests/v2/test_capacity_queries.py`
- Create: `docs/benchmarks/flymail-v2-capacity-results.md`
- Modify: `.gitignore`

**Interfaces:**

- Produces deterministic synthetic dataset seed and scale arguments.
- Produces machine-readable benchmark JSON plus concise committed summary.

- [ ] **Step 1: Write generator tests at small scale**

Verify deterministic counts, user isolation, cross-account threads, Gmail labels, hot/cold accounts, body-cache ratios and search terms. Generated content must be synthetic and not resemble real email addresses beyond reserved example domains.

- [ ] **Step 2: Implement streaming/batched generator**

Arguments:

```text
--users 50
--accounts 300
--messages 20000000
--seed 20260731
--batch-size 5000
--body-cache-ratio 0.05
```

Use bounded memory and bulk SQL. Refuse production database names or host data paths.

- [ ] **Step 3: Define resource profile**

Run benchmarks in a documented container CPU/memory/disk profile. Record MySQL buffer pool and connection limits. A result without resource profile is invalid.

- [ ] **Step 4: Measure API query targets**

Measure at least 30 warmed samples and report P50/P95/P99:

- Bootstrap <= 300 ms P95;
- thread first/next page <= 150 ms P95;
- cached detail structure <= 150 ms P95;
- cached body first byte <= 200 ms P95;
- structured search <= 300 ms P95;
- cached-body FULLTEXT <= 500 ms P95;
- local operation commit <= 200 ms P95;
- API DB connection wait <= 50 ms P95.

- [ ] **Step 5: Measure Worker targets**

Using deterministic fake providers:

- P0 queue wait <= 500 ms P95;
- IDLE event to visible summary <= 5 seconds;
- recovered offline operation begins <= 5 seconds;
- Worker restart recovery <= 30 seconds;
- slow account does not block another account;
- normal attachment prefetch bytes = 0.

- [ ] **Step 6: Require EXPLAIN ANALYZE evidence**

Store normalized plans for thread list, search, job claim, remote instance lookup and quota LRU. Reject filesort/full scan where the design promises an index path. Change indexes only through a new migration and re-run all tests.

- [ ] **Step 7: Commit concise results**

Raw 2,000 万 data and full logs remain ignored. Commit generator, tests, methodology and summary with exact Git SHA/image/resource profile.

```bash
git add scripts/generate-v2-benchmark-data.py backend/tests/v2/test_capacity_queries.py docs/benchmarks/flymail-v2-capacity-results.md .gitignore
git commit -m "🚀 验证 V2 两千万邮件容量与性能门槛"
```

---

### Task 7: 完成真实邮箱、代理和桌面移动浏览器验收

**Files:**

- Create: `docs/operations/flymail-v2-provider-validation.md`
- Create: `docs/benchmarks/flymail-v2-real-provider-results.md` with no credentials or real message content.

**Interfaces:**

- Produces a provider/browser matrix with pass, fail, blocked and limitation states.

- [ ] **Step 1: Prepare isolated test accounts**

Use non-production test mailboxes for Gmail, Outlook/Microsoft, QQ, NetEase, iCloud, Sina and Generic IMAP/SMTP when credentials are available. Do not place credentials in shell history, Git, logs or plan files.

- [ ] **Step 2: Validate each provider flow**

For each available provider verify:

- password/OAuth/authorization setup through the account UI;
- user-level HTTP CONNECT proxy for OAuth, token refresh, IMAP, SMTP and IDLE where supported;
- special mailbox mapping;
- initial summary sync;
- IDLE or documented polling fallback;
- read/star/move/archive/trash/permanent delete;
- labels where supported;
- exact body and inline-image fetch;
- ordinary attachment on-demand;
- send, sent-copy behavior and Message-ID verification;
- authorization expiry and reauthorization;
- provider rate-limit user message;
- Bark, Telegram, enterprise WeChat, DingTalk, Feishu and generic Webhook test deliveries when safe test endpoints are available, including opt-in proxy reuse and secret-redacted failures;
- optional notification-image publishing through the maintained `flymail-imgbed` template or a safe test publisher, including upload, text fallback, cleanup/expiry and secret redaction.

- [ ] **Step 3: Validate proxy cases**

Use available HTTP CONNECT proxy test for supported providers, verify no proxy credential leaks and no proxy environment globally changes unrelated internal MySQL/API traffic.

- [ ] **Step 4: Validate browsers and viewports**

Test current desktop Chrome/Edge/Safari/Firefox where available and representative iOS/Android browsers. Verify login, profile/avatar, account password/OAuth/proxy setup, account icon, contacts/autocomplete, responsive navigation, thread reading, image viewer, PDF export, mark-all-read, compose keyboard, browser upload, authorized NAS-path import, search, notification center/settings/image publishing, conflict, settings, About/version, PWA install/server-unreachable shell and backup inspect. Record exact browser versions at execution time.

- [ ] **Step 5: Validate accessibility manually**

Use keyboard-only flow, at least one desktop screen reader, focus restoration, reduced motion, zoom and high-contrast checks. Record unresolved browser-specific limitations.

- [ ] **Step 6: Commit sanitized matrix**

The result contains provider/browser names, date, build SHA and behavior status only. No account addresses, subjects, message IDs, server responses containing identifiers or credentials.

```bash
git add docs/operations/flymail-v2-provider-validation.md docs/benchmarks/flymail-v2-real-provider-results.md
git commit -m "✅ 完成 V2 真实邮箱与多端浏览器验收"
```

---

### Task 8: 完成 README、环境变量、备份恢复和切换文档

**Files:**

- Modify: `README.md`
- Modify: `.env.example`
- Create: `docs/operations/flymail-v2-backup-restore.md`
- Create: `docs/operations/flymail-v2-cutover.md`
- Modify: all user-visible deployment examples.
- Modify when behavior changes: `flymail-imgbed/README.md` and its example configuration, without adding deployment credentials.

**Interfaces:**

- Produces complete operator documentation before production deletion confirmation.

- [ ] **Step 1: Document exact environment contract**

For each variable specify purpose, required/default, secret status and restart effect. Include `FLYMAIL_SESSION_SECRET` minimum 16 and recommended `openssl rand -hex 32`. Do not add real values.

- [ ] **Step 2: Rewrite README for V2**

Document:

- single-container API/Worker/MySQL architecture;
- server-side offline behavior;
- supported provider/plugin boundaries;
- layered body and attachment cache;
- contacts, signatures, profile/avatar, account icons, image viewer, PDF export and static-only PWA;
- in-app and Bark/Telegram/enterprise WeChat/DingTalk/Feishu/Webhook notifications, including optional `flymail-imgbed` image publishing and text fallback;
- authorized `/data` attachment roots and path-security boundary;
- per-user quotas;
- local search limitations after body eviction;
- reliable send states;
- backup scope and independent password;
- health meaning;
- data paths;
- About/version page behavior and public information boundary;
- build/run/stop/update commands;
- no Docker Hub upload by default.

- [ ] **Step 3: Document backup and restore drill**

Include create, inspect, password failure, checksum failure, temporary restore validation, maintenance switch, paused restored operations and rollback. Verify commands against a temporary container.

- [ ] **Step 4: Document cutover and rollback**

State exact current production directory `/Docker/flymail/data`, required snapshot, checksum, stop/start commands, no old/new database compatibility and rollback restore steps. Do not include a command that deletes production data without a preceding explicit confirmation checkpoint.

- [ ] **Step 5: Check documentation consistency**

Run `rg` for old version numbers, legacy feature claims, old routes, Redis, old attachment paths and old single-process descriptions. Correct conflicts.

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example docs/operations
git commit -m "📝 完成 FlyMail V2 部署备份与切换文档"
```

---

### Task 9: 形成发布候选证据并同步最终版本

**Files:**

- Modify after explicit version choice: `VERSION`, `package.json`, `frontend/package.json`, `docker-compose.yml`, README image examples.
- Create: `docs/benchmarks/flymail-v2-release-evidence.md`.

**Interfaces:**

- Produces release candidate image and immutable evidence report.
- Does not yet modify production data.

- [ ] **Step 1: Present version choice before editing**

Show current `VERSION`, incompatibility level and proposed release number. Obtain user confirmation for the final version number. Do not assume architecture name “V2” automatically means a specific SemVer value.

- [ ] **Step 2: Synchronize confirmed version**

Write confirmed value to `VERSION`, then run:

```bash
npm run sync-version
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
```

All three must match. Check compose and README image tag.

- [ ] **Step 3: Run complete verification from clean state**

```bash
cd backend
python -m unittest discover -s tests -v

cd ../frontend
npm test
npm run test:v2
npm run build

cd ..
bash -n scripts/docker-entrypoint.sh
bash -n scripts/test-v2-container.sh
bash -n scripts/check-v2-secrets.sh
docker compose --env-file .env.example config
git diff --check
git status --short
git diff
```

- [ ] **Step 4: Build and smoke final local image**

```bash
IMAGE="benxianyu/flymail:$(cat VERSION)"
docker build -t "$IMAGE" .
scripts/test-v2-container.sh "$IMAGE"
scripts/check-v2-secrets.sh "$IMAGE"
```

- [ ] **Step 5: Write release evidence**

Include actual counts/results for backend, frontend, build sizes, MySQL integration, provider contracts, security, fault, capacity, real providers/browsers, container, persistence, shutdown, secret scan, docs and known limitations.

- [ ] **Step 6: Commit and push release candidate**

```bash
git add VERSION package.json frontend/package.json docker-compose.yml README.md docs/benchmarks/flymail-v2-release-evidence.md
git commit -m "📦 发布 FlyMail V2 候选版本"
git push origin main
```

Do not upload Docker Hub.

---

### Task 10: 生产数据清空确认、正式切换和回滚验证

**Files:**

- Runtime only: current container `flymail`, `/Docker/flymail/data`, approved snapshot directory.
- Update docs only if actual commands differ from tested plan.

**Interfaces:**

- Consumes release evidence and explicit destructive approval.
- Produces final production V2 container or restores the exact old snapshot.

- [ ] **Step 1: Capture current production read-only statistics**

Without exposing secrets, record:

- current container image/status/health;
- current `/api/health` version;
- `/Docker/flymail/data` total size and major directory sizes;
- MySQL version/data directory/bind address;
- current Git HEAD and release candidate image digest;
- current active users/accounts/message counts where a safe admin query exists.

- [ ] **Step 2: Create and verify rollback snapshot**

Stop writes as documented, create a full filesystem snapshot/copy of `/Docker/flymail/data`, calculate checksum or snapshot identifier, and verify a temporary old-version container can start from a copy of that snapshot. Do not delete original data yet.

- [ ] **Step 3: Request explicit destructive confirmation**

The request must include:

- exact directory to be replaced: `/Docker/flymail/data`;
- current version/image;
- target version/image digest;
- snapshot path and verification result;
- statement that users, accounts, cached mail and settings will be reset;
- statement that rollback requires restoring the old snapshot;
- statement that V2 changes cannot be read by old version.

Wait for an unambiguous confirmation specific to deletion/replacement.

- [ ] **Step 4: Stop current container safely**

Use normal Docker stop and verify MySQL safe shutdown. If shutdown logs indicate corruption or timeout, stop and investigate before deleting or moving data.

- [ ] **Step 5: Preserve old directory and initialize new data**

Prefer atomic rename of old data directory to a timestamped preserved path on the same filesystem, then create a fresh `/Docker/flymail/data` with correct ownership/permissions. Do not use `rm -rf` as the first cutover action.

- [ ] **Step 6: Start V2 production container**

Start `flymail` with approved image and fresh data. Verify healthy, version, Worker heartbeat, MySQL 8/data directory/bind, object store and logs.

- [ ] **Step 7: Perform production acceptance**

Create admin, update profile/avatar, add designated test mailbox through password or OAuth, verify proxy where configured, account icon, contacts/autocomplete, sync, read, image viewer, PDF export, search, mark-all-read, send, browser/NAS attachment, notification test delivery, operation, PWA/mobile browser and backup creation. Do not declare success from health endpoint alone.

- [ ] **Step 8: Validate restart persistence**

Create safe test state, restart `flymail`, verify user/account/task/object state and MySQL clean start. Verify shutdown remains safe.

- [ ] **Step 9: Decide go or rollback**

If any release gate fails, stop V2, preserve its new data for diagnosis, restore old data directory snapshot and old image, and verify old health/data. Do not attempt to make old version read V2 database.

- [ ] **Step 10: Finalize only after observation window**

Keep old snapshot until the user explicitly approves cleanup after stable observation. Docker Hub remains untouched unless separately requested.

## Gate 5 Completion Checklist

- [ ] Safe logs, metrics, Server-Timing and health semantics pass.
- [ ] Normal backend and frontend entries run V2 only.
- [ ] Single container supervises MySQL, API and Worker correctly.
- [ ] Temporary container is healthy, persistent and cleanly stoppable.
- [ ] MySQL is 8.0, data directory `/data/mysql/`, bind `127.0.0.1`.
- [ ] Database password special-character validation passes.
- [ ] Logs and image metadata contain no secrets.
- [ ] Required fault injection invariants pass.
- [ ] 2,000 万摘要 capacity and P95 targets pass on recorded resource profile.
- [ ] Real provider and desktop/mobile browser matrix is complete or blocked items are clearly documented.
- [ ] README, `.env.example`, backup/restore and cutover docs match tested behavior.
- [ ] Final version is explicitly confirmed and synchronized.
- [ ] Release candidate commit is pushed to `origin/main`.
- [ ] Docker Hub is not uploaded.
- [ ] Production data is not replaced before separate explicit confirmation.
- [ ] Rollback snapshot is verified before cutover.
- [ ] Final production container passes feature and restart acceptance.
