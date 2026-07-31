# FlyMail V2 全面重构实施路线图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不影响当前 `flymail` 生产容器的前提下，完成 FlyMail V2 的后端、同步 Worker、数据访问层、协议核心、前端、备份恢复、容器运行和切换验证，并在最终确认后以全新数据目录整体上线。

**Architecture:** V2 在现有仓库中使用独立的最终命名空间逐步构建：后端代码进入 `backend/flymail/`，前端代码进入 `frontend/src/app`、`features`、`entities`、`layouts` 与 `shared`。开发阶段旧入口继续运行；全部子计划通过后，最终切换 `backend/main.py`、`backend/worker.py`、`frontend/src/main.ts` 与单容器入口脚本，不保留运行时兼容层。

**Tech Stack:** Python 3、FastAPI、Pydantic 2、aiomysql、MySQL 8.0、cryptography、IMAP、SMTP、Vue 3、TypeScript、Pinia、Vue Router、Tiptap、Docker、Node.js test runner、Python unittest。

## Global Constraints

- 工作区固定为 `/home/chatgpt/flymail`，分支固定为 `main`，远端固定为 `LiangLiang723/flymail`。
- 当前生产容器 `flymail` 和宿主机目录 `/Docker/flymail/data` 在最终切换前不得被开发、测试或容量测试修改。
- 开发测试只能使用独立临时目录、临时数据库和临时容器名。
- V2 不兼容旧数据库和旧缓存；正式切换时允许清空旧数据，但删除 `/Docker/flymail/data` 前必须再次展示统计并获得明确确认。
- 部署仍为单容器，内部运行 MySQL 8、FastAPI API 进程、独立同步 Worker 与前端静态资源。
- MySQL 只能监听容器内部 `127.0.0.1:3306`；FlyMail 监听 `0.0.0.0:8080`。
- 不引入 Redis、RabbitMQ、Elasticsearch、外部对象存储、OIDC 或 TOTP。
- 目标规模为最多约 50 个用户、300 个邮箱账号、单邮箱 50 万封、总计约 2,000 万封邮件摘要。
- API 与 Worker 使用独立 MySQL 连接池；所有可靠任务通过 MySQL 任务表与事务型 Outbox 协作。
- 普通附件只按需下载；正文、附件、内嵌图片和按需 `.eml` 使用 SHA-256 内容寻址对象存储。
- 正文缓存默认每用户 `5 GB`；用户可在设置中修改；`0` 表示不限制。
- 正文淘汰时对应正文全文索引一并删除；结构化元数据索引永久保留。
- 浏览器不保存邮件正文、附件或离线操作；离线能力仅指 FlyMail 服务器无法连接第三方邮箱时仍能操作本地状态。
- 用户登录继续使用本地用户名和密码；邮箱凭证使用实例密钥派生的独立密钥认证加密。
- 备份只包含配置和业务数据；邮箱凭证使用管理员独立备份密码重新加密。
- 所有 Repository 查询必须显式包含 `user_uid` 或受控管理员上下文。
- 所有大列表使用稳定游标分页，不使用深度 `OFFSET`。
- 不新增或升级生产依赖，除非现有标准库和已安装依赖无法满足已确认规格；任何依赖变化必须单独说明并验证镜像体积与许可证。
- 每个功能或缺陷修复必须先写失败测试，再写最小实现。
- 默认推送代码到 `origin/main`；默认不上传 Docker Hub。
- 最终发布版本不在实现早期提前修改；只有切换计划全部通过并获得生产数据清空确认后，才同步 `VERSION`、两个 `package.json`、`docker-compose.yml` 与 README 镜像版本。

## Child Plans and Required Order

1. `docs/superpowers/plans/2026-07-31-flymail-v2-foundation-data.md`
   - 最终包结构、配置、MySQL 连接池、迁移框架、核心表、加密、对象存储、Repository、Outbox 与任务租约。
2. `docs/superpowers/plans/2026-07-31-flymail-v2-protocol-sync.md`
   - Provider 合同、IMAP/SMTP 核心、MIME part、消息摄取、会话、IDLE、校正、离线操作与可靠发送。
3. `docs/superpowers/plans/2026-07-31-flymail-v2-api-features.md`
   - 认证、管理员、账号、Bootstrap、列表、详情、搜索、设置、附件、实时事件、备份恢复与安全接口。
4. `docs/superpowers/plans/2026-07-31-flymail-v2-frontend.md`
   - Vue 3 新应用壳、响应式布局、会话列表、详情、写信、搜索、设置、同步中心、管理员、备份与无障碍。
5. `docs/superpowers/plans/2026-07-31-flymail-v2-validation-cutover.md`
   - 双进程入口、健康检查、故障注入、容量基准、真实邮箱矩阵、文档、版本、临时容器和最终切换门槛。

后一个计划只能在前一个计划的验收门槛通过后开始。允许同一计划内部使用独立子任务并行，但不能跨越数据契约或接口依赖顺序。

## Persistent Development Strategy

V2 使用最终目录结构，不创建需要在上线前整体搬迁的临时源码树：

```text
backend/flymail/
├── api/
├── application/
├── domain/
├── infrastructure/
│   ├── db/
│   ├── object_store/
│   └── security/
├── observability/
├── providers/
├── search/
└── workers/

frontend/src/
├── app/
├── entities/
├── features/
├── layouts/
└── shared/
```

开发阶段使用独立入口：

- `backend/v2_dev.py`：仅供 V2 API 集成测试与本地临时容器运行。
- `backend/v2_worker.py`：仅供 V2 Worker 集成测试。
- `frontend/src/v2-main.ts`：仅供 Vite V2 测试入口或临时构建。

最终切换时：

- `backend/main.py` 改为创建 V2 API 应用。
- `backend/worker.py` 成为正式 Worker 入口。
- `frontend/src/main.ts` 启动 V2 前端。
- 删除仅用于开发入口的 `v2_dev.py`、`v2_worker.py` 和 `v2-main.ts`。
- 旧业务模块只在全部回归通过后删除；删除前使用 `rg` 检查路由、动态 import、测试、文档和入口引用。

## Milestone Gates

### Gate 1: Foundation Ready

必须满足：

- 全新临时 MySQL 数据库可从空库执行所有 V2 migration。
- API 与 Worker 连接池完全分离。
- Repository 不隐式提交事务。
- 业务数据和 Outbox 可在同一事务原子提交或回滚。
- Worker 多消费者领取任务时不重复领取。
- 凭证密文无法从数据库直接识别，错误密钥解密失败。
- 对象存储原子写入、去重、引用回收和配额测试通过。
- 跨用户 Repository 读取测试全部拒绝。

### Gate 2: Protocol and Worker Ready

必须满足：

- Generic、Gmail、Outlook、QQ、网易、iCloud、新浪插件通过统一合同测试。
- IMAP 单连接命令串行，不允许同连接并发。
- BODYSTRUCTURE 解析保存真实 part specifier。
- 打开正文和下载普通附件不使用整封 `BODY.PEEK[]`。
- IDLE 事件只创建增量任务，不执行重型同步。
- 慢账号不阻塞其他账号。
- 离线操作可重试、幂等并可在 Worker 重启后恢复。
- SMTP 结果不确定时进入验证状态，测试中重复发送数为 0。

### Gate 3: API Feature Complete

必须满足：

- 当前全部用户功能拥有 V2 API。
- 会话列表、详情、搜索和本地操作满足用户隔离。
- 本地缓存存在时 API 不连接远端邮箱。
- Bootstrap 只返回首屏必要数据。
- WebSocket 断线可以按事件序号续传或触发精确失效。
- 备份错误密码、损坏文件或不兼容版本不会写入正式数据。
- API OpenAPI、错误码和前端类型契约固定。

### Gate 4: Frontend Feature Complete

必须满足：

- 桌面、平板和移动端覆盖全部功能。
- 会话列表默认按会话聚合，跨账号回复身份符合规则。
- 打开邮件、已读、星标、移动和删除不触发整页重载。
- 写信、草稿、即时发送、定时发送和冲突处理完整。
- 深色和浅色邮件正文可读。
- 键盘、屏幕阅读器、触控和减少动画测试通过。
- 初始 JS gzip 不超过 `180 KB`。

### Gate 5: Release Candidate Ready

必须满足：

- 后端、前端、MySQL 集成、Provider 合同、故障注入、容量和安全测试全部通过。
- 使用独立临时 `/data` 启动单容器，API、Worker、MySQL 与对象存储均健康。
- 临时容器重启后数据、任务、草稿和对象引用保持。
- 日志和镜像元数据不包含密码、令牌、密钥或完整连接地址。
- 使用包含引号、反斜杠、`@`、`:`、`/` 和 `%` 的数据库密码完成一次容器验证。
- 多个真实测试邮箱完成收取、发送、移动、标签、附件、IDLE 降级与重授权验收。
- README、`.env.example`、部署、备份和恢复文档完成。

### Task 1: Establish Execution Tracking

**Files:**

- Modify during execution: this roadmap and the active child plan only for checkbox state.
- Do not modify production code in this task.

**Interfaces:**

- Produces: a single active child plan at any time.
- Produces: one Git commit per independently reviewable task.

- [ ] **Step 1: Confirm clean baseline before each child plan**

Run:

```bash
git status --short --branch
git branch --show-current
git remote -v
git log -5 --oneline
```

Expected:

- branch is `main`;
- remote points to `LiangLiang723/flymail`;
- any unrelated user changes are listed and excluded from staging.

- [ ] **Step 2: Create an isolated worktree when implementation starts**

Use the `superpowers:using-git-worktrees` skill before changing code. The worktree must be based on current `main`; the production checkout remains available for inspection and the running container continues to use its existing image.

- [ ] **Step 3: Execute one child plan task at a time**

For each task:

1. read the exact task and interfaces;
2. write the failing test;
3. run the narrow test and confirm the expected failure;
4. implement the smallest passing change;
5. run the narrow test;
6. run the plan-level regression set;
7. inspect `git diff --check`, `git diff`, and `git status --short`;
8. commit only task files with a precise Chinese title;
9. push only after the plan gate passes or the task explicitly requires a checkpoint push.

- [ ] **Step 4: Record measured results, not estimates**

When a performance or capacity task runs, store reproducible commands and measured output in `docs/benchmarks/` or the corresponding test fixture. Do not mark a performance checkbox from code inspection alone.

### Task 2: Preserve Current Production Until Cutover

**Files:**

- No production file modifications in this task.
- Test artifacts: temporary directories outside `/Docker/flymail/data`.

**Interfaces:**

- Consumes: current container `flymail`.
- Produces: verified non-interference throughout development.

- [ ] **Step 1: Capture read-only production baseline**

Run without printing secrets:

```bash
docker inspect flymail --format 'image={{.Config.Image}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}}'
curl -fsS http://127.0.0.1:36080/api/health
docker exec flymail mysql --version
docker exec flymail mysql -Nse "SELECT @@datadir, @@bind_address"
```

Expected: current container remains healthy, MySQL data directory is `/data/mysql/`, and bind address is `127.0.0.1`.

- [ ] **Step 2: Use explicit temporary resources**

Every integration or container command must define:

```text
RUN_ID=$(date +%s)-$$
GIT_SHA=$(git rev-parse --short HEAD)
container name: flymail-v2-test-${RUN_ID}
host data dir: /tmp/flymail-v2-test-${RUN_ID}
image tag: benxianyu/flymail:v2-test-${GIT_SHA}
MySQL database: flymail_v2_test_${RUN_ID//-/_}
```

- [ ] **Step 3: Verify no production path appears in test scripts**

Run:

```bash
rg -n '/Docker/flymail/data|container_name:\s*flymail\b' backend frontend scripts docs/benchmarks tests || true
```

Expected: only documentation warnings or the production compose file contain those values; V2 test scripts use temporary values.

### Task 3: Complete Each Child Plan Gate

**Files:**

- Read and execute the five child plans in the listed order.

**Interfaces:**

- Consumes: previous gate artifacts and interfaces.
- Produces: the next gate's stable dependency surface.

- [ ] **Step 1: Execute foundation and data plan**

Run all tasks in `2026-07-31-flymail-v2-foundation-data.md`. Do not begin protocol work until Gate 1 passes.

- [ ] **Step 2: Execute protocol and sync plan**

Run all tasks in `2026-07-31-flymail-v2-protocol-sync.md`. Do not expose unstable provider or task contracts to API or frontend code.

- [ ] **Step 3: Execute API features plan**

Run all tasks in `2026-07-31-flymail-v2-api-features.md`. Freeze OpenAPI and event schemas before frontend feature implementation.

- [ ] **Step 4: Execute frontend plan**

Run all tasks in `2026-07-31-flymail-v2-frontend.md`. Keep legacy `frontend/src/main.ts` active until the final frontend gate.

- [ ] **Step 5: Execute validation and cutover plan**

Run all non-destructive tasks in `2026-07-31-flymail-v2-validation-cutover.md`. Stop before the explicit production data deletion gate and request confirmation with current statistics.

### Task 4: Final Review Before Destructive Cutover

**Files:**

- Review: all changed source, tests, docs, Docker files and version files.
- Do not delete production data in this planning task.

**Interfaces:**

- Consumes: release candidate image and full verification evidence.
- Produces: an explicit go/no-go report.

- [ ] **Step 1: Build the release evidence table**

The report must include:

- backend test count and failures;
- frontend test count and build chunk sizes;
- MySQL integration tests;
- provider contract matrix;
- fault injection results;
- capacity measurements against all stated P95 targets;
- temporary container health, MySQL version and data directory;
- persistence and restart evidence;
- log and image secret scan;
- real mailbox and browser coverage;
- README and environment documentation status;
- current production version, image, container status and data size;
- release candidate version and image tag;
- exact directories that would be removed or replaced;
- rollback snapshot path and checksum.

- [ ] **Step 2: Request explicit destructive confirmation**

The confirmation request must state that `/Docker/flymail/data` contains the current instance and will be replaced. Do not treat approval of the architecture or this implementation plan as deletion authorization.

- [ ] **Step 3: Execute cutover only after confirmation**

Follow the final child plan exactly. If any verification differs from the approved release evidence, stop and report the difference before modifying production data.

## Plan-Level Verification

Before claiming this roadmap is complete during implementation, run:

```bash
cd backend
python -m unittest discover -s tests -v

cd ../frontend
npm test
npm run build

cd ..
bash -n scripts/docker-entrypoint.sh
docker compose --env-file .env.example config
git diff --check
git status --short
git diff
```

Application, Dockerfile, dependency, version or entrypoint changes additionally require a real temporary container test from the validation plan.
