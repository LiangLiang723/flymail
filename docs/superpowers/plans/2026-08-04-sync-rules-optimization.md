# FlyMail Sync Rules Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 FlyMail 实时监听、周期兜底、手动刷新和历史同步规则，降低重复连接与大邮箱查询开销，并保证任务可恢复、可停止、可观察。

**Architecture:** 新增账号级运行时协调器，区分交互、后台和独占同步；`MailSyncService` 负责 Provider 感知的实时监听与有限并发周期调度；历史同步在独占区间内执行并通过 WebSocket 推送阶段变化；数据库增加批量和分页查询以替代 N+1 与整文件夹 UID 加载。

**Tech Stack:** Python 3 asyncio、FastAPI、MySQL/aiomysql、Vue 3、TypeScript、Node test runner、Docker。

## Global Constraints

- 工作区只能是 `/home/chatgpt/flymail`，分支 `main`。
- 不删除或迁移 `/Docker/flymail/data`，测试只用独立临时目录。
- 不新增或升级生产依赖。
- 不修改认证方式和数据库表结构。
- `poll_interval_seconds` 保持 `5..3600`。
- 最多三个账号并发周期同步。
- 手动刷新超时固定为 45 秒。
- IDLE、iCloud Poll、非 IDLE 的完整兜底最低间隔分别为 180、120、60 秒。
- 版本升级为 `0.0.29`。

---

### Task 1: 账号同步协调器与引用计数暂停

**Files:**
- Create: `backend/services/sync_coordinator.py`
- Modify: `backend/services/sync.py`
- Create: `backend/tests/test_sync_coordination.py`

**Interfaces:**
- Produces: `sync_coordinator.interactive(account_id)`, `sync_coordinator.background(account_id)`, `sync_coordinator.exclusive(account_id)`, `sync_coordinator.is_exclusive(account_id)`, `sync_coordinator.should_yield_background(account_id)`。
- Produces: `MailSyncService.suspend_account(account_id)` 和 `resume_account(account_id)` 的引用计数语义。

- [ ] **Step 1: 写失败测试**

测试以下行为：独占执行时交互和后台均被拒绝；交互执行时新后台被拒绝；独占等待期间不再允许新操作；两次暂停必须两次恢复后才重启监听。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest tests.test_sync_coordination -v`
Expected: FAIL，因为协调器模块和引用计数尚不存在。

- [ ] **Step 3: 实现最小协调器和引用计数**

使用每账号 `asyncio.Condition` 保存 `interactive_count`、`background_active`、`exclusive_active`、`exclusive_waiters`。后台入口不排队，条件不满足时 yield `False`；独占等待所有活动操作结束。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m unittest tests.test_sync_coordination -v`
Expected: PASS。

### Task 2: Provider 感知周期调度和非 IDLE 批量扫描

**Files:**
- Modify: `backend/services/sync.py`
- Modify: `backend/tests/test_custom_folder_visibility.py`
- Modify: `backend/tests/test_sync_coordination.py`

**Interfaces:**
- Produces: `MailSyncService._periodic_sync_interval(account) -> int`。
- Produces: `MailSyncService._periodic_sync_account(account)`。
- Produces: 非 IDLE 监听通过一次 `fetch_folder_counts(folders)` 比较整轮变化。

- [ ] **Step 1: 写失败测试**

覆盖 IDLE=至少180秒、iCloud=至少120秒、非IDLE=至少60秒；不同账号周期任务允许并发但受 Semaphore(3) 限制；非 IDLE 一轮只调用一次批量计数，不逐文件夹调用 `idle_wait`。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest tests.test_custom_folder_visibility tests.test_sync_coordination -v`
Expected: FAIL，当前仍使用统一间隔和串行文件夹等待。

- [ ] **Step 3: 实现调度和批量扫描**

周期循环只负责发现到期账号并创建账号任务；账号任务在信号量内串行同步自身文件夹。非 IDLE 监听比较总数和未读数，增加、减少和未读变化分别触发缓存同步。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m unittest tests.test_custom_folder_visibility tests.test_sync_coordination -v`
Expected: PASS。

### Task 3: 统一重连计数和 iCloud 文件夹恢复

**Files:**
- Modify: `backend/services/sync.py`
- Modify: `backend/tests/test_sync_coordination.py`

**Interfaces:**
- Produces: 所有 Provider 进入异常路径时统一增加 `consecutive_failures`。
- Produces: Poll 模式 `_ensure_poll_connections()` 只重建缺失或断开的文件夹连接。

- [ ] **Step 1: 写失败测试**

模拟 IDLE 异常，断言下一次退避使用递增失败次数；模拟一个 Poll 文件夹断开，断言存活文件夹复用且断开文件夹重新创建。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest tests.test_sync_coordination -v`
Expected: FAIL。

- [ ] **Step 3: 实现统一失败累计和 Poll 补建**

异常处理入口先递增失败次数；连接全部建立成功后清零。Poll 分支按文件夹维护连接字典，清理断线项后调用补建函数。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m unittest tests.test_sync_coordination -v`
Expected: PASS。

### Task 4: 历史同步独占、有限重试和暂停批次

**Files:**
- Modify: `backend/services/history_sync.py`
- Modify: `backend/tests/test_history_sync_folders.py`

**Interfaces:**
- Consumes: `sync_coordinator.exclusive(account_id)`。
- Produces: `_fill_unchecked_message_bodies(..., should_stop=None, on_batch=None)`。
- Produces: 正文连续无进度三次后 `failed`，不再保留无 worker 的 `pending`。

- [ ] **Step 1: 写失败测试**

将旧的“剩余正文时设 pending”测试改为：正文阶段按 2、5、10 秒有限重试；第三次仍无进度时失败；每批检查暂停；独占区间结束后才恢复监听。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest tests.test_history_sync_folders -v`
Expected: FAIL。

- [ ] **Step 3: 实现独占和有限重试**

历史同步、账号缓存清空、文件夹缓存清空和账号删除使用独占协调器。正文批次回调检查暂停并推送进度；无进度重试耗尽后抛出明确异常，由现有失败路径保存断点。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m unittest tests.test_history_sync_folders -v`
Expected: PASS。

### Task 5: 手动刷新超时和后台摘要让行

**Files:**
- Modify: `backend/routes/messages.py`
- Modify: `backend/services/mail_cache.py`
- Modify: `backend/schemas.py`
- Modify: `backend/tests/test_message_folder_resolution.py`
- Modify: `backend/tests/test_recent_mail_sync.py`

**Interfaces:**
- Consumes: `sync_coordinator.interactive/background/is_exclusive/should_yield_background`。
- Produces: `/api/messages/refresh` 在 45 秒超时或独占同步时返回本地数据和 `error`。
- Produces: 摘要补齐在批次边界遇到交互操作时停止，不堆积。

- [ ] **Step 1: 写失败测试**

覆盖手动刷新超时、独占时不连接远端、摘要补齐在交互操作到来后停止、后台任务去重标记仍能清理。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest tests.test_message_folder_resolution tests.test_recent_mail_sync -v`
Expected: FAIL。

- [ ] **Step 3: 实现超时和协调器接入**

手动刷新用 `asyncio.wait_for(..., 45)`；远端读取包裹交互上下文；周期、实时和摘要使用后台上下文，未获准时安全跳过。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m unittest tests.test_message_folder_resolution tests.test_recent_mail_sync -v`
Expected: PASS。

### Task 6: 批量进度查询和 WebSocket 进度推送

**Files:**
- Modify: `backend/db/__init__.py`
- Modify: `backend/routes/settings.py`
- Modify: `backend/services/sync.py`
- Modify: `backend/services/history_sync.py`
- Modify: `backend/tests/test_history_sync_progress.py`
- Modify: `frontend/src/views/HistorySync.vue`
- Modify: `frontend/tests/page-templates.test.mjs`

**Interfaces:**
- Produces: `list_cached_counts_by_account(account_id) -> dict[str, int]`。
- Produces: `MailSyncService.notify_history_sync_updated(account_id, user_uid)`。
- Produces: `_build_folder_progress(..., jobs)` 不再逐文件夹查询任务和缓存计数。

- [ ] **Step 1: 写失败测试**

后端断言进度构建只使用批量数据；前端断言处理 `history_sync_updated` 且兜底轮询为 15000ms。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest tests.test_history_sync_progress -v && cd ../frontend && npm test -- --test-name-pattern="history sync"`
Expected: FAIL。

- [ ] **Step 3: 实现批量查询和推送**

路由一次加载用户全部任务并按账号映射；文件夹统计和缓存计数批量加载。历史阶段和正文批次调用 WebSocket 通知；前端消息到达时防抖刷新，轮询仅作15秒兜底。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m unittest tests.test_history_sync_progress -v && cd ../frontend && npm test`
Expected: PASS。

### Task 7: 大邮箱 UID 和已读校正分页

**Files:**
- Modify: `backend/db/__init__.py`
- Modify: `backend/services/mail_cache.py`
- Modify: `backend/services/history_sync.py`
- Modify: `backend/tests/test_recent_mail_sync.py`
- Modify: `backend/tests/test_history_sync_folders.py`

**Interfaces:**
- Produces: `get_existing_cached_uids(account_id, folder, uids)`。
- Produces: `list_cached_read_states(account_id, folder, after_uid=0, limit=1000)`。
- Produces: 最近同步和历史最近阶段仅检查当前页 UID；已读状态每1000条分页校正。

- [ ] **Step 1: 写失败测试**

断言最近同步不调用 `get_cached_uids` 全量查询；历史最近阶段同样按页检查；已读校正连续读取多页直到结束。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && python -m unittest tests.test_recent_mail_sync tests.test_history_sync_folders -v`
Expected: FAIL。

- [ ] **Step 3: 实现分页查询**

每个远端页收集 UID，使用 SQL `IN` 查询已存在 UID。已读校正按 UID 升序分页，批量更新后使用最后 UID 获取下一页。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && python -m unittest tests.test_recent_mail_sync tests.test_history_sync_folders -v`
Expected: PASS。

### Task 8: 文案、版本和完整验证

**Files:**
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `README.md`
- Modify: `VERSION`
- Modify via sync script: `package.json`, `frontend/package.json`, `docker-compose.yml`
- Modify: relevant frontend contract test

**Interfaces:**
- Produces: 用户可见的差异化间隔、45秒超时、历史重试和WebSocket说明。
- Produces: 版本 `0.0.29`。

- [ ] **Step 1: 更新文案测试并确认失败**

Run: `cd frontend && npm test`
Expected: FAIL，旧文案仍描述所有在线账号按同一间隔兜底。

- [ ] **Step 2: 更新前端和 README，设置 VERSION=0.0.29 并同步版本**

Run: `npm run sync-version`
Expected: 三个版本和镜像标签一致为 `0.0.29`。

- [ ] **Step 3: 运行完整代码验证**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
```

Expected: 全部通过；仅保留已知 Vite chunk size warning。

- [ ] **Step 4: 构建和验证独立临时容器**

Run: `docker build -t benxianyu/flymail:0.0.29 .`

验证健康接口、MySQL 8.0、`/data/mysql/`、`/data/flymail`、数据库读写、重启持久化、日志脱敏、镜像元数据无密码、SIGTERM 安全关闭。使用独立临时目录和容器名，结束后清理。

- [ ] **Step 5: 安全重建正式容器**

确认 `/Docker/flymail/data` 挂载不变，使用新本地镜像重建 `flymail`，验证 `/api/health` 返回 `0.0.29`、MySQL 数据目录和现有账号数据仍在。

- [ ] **Step 6: 提交和推送**

仅暂存本任务文件，提交标题：`⚡ 优化邮件同步调度与刷新规则`。推送 `origin/main`，不上传 Docker Hub。
