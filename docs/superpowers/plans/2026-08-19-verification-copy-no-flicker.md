# Verification Copy No-Flicker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 复制未读邮件验证码后立即标记已读，但不再因抢跑刷新和 WebSocket 二次整页加载造成列表闪烁或已读状态回弹。

**Architecture:** 保留现有 `/api/mark-read`、本地乐观更新和 WebSocket 跨标签页同步。删除 `markMessageRead()` 在接口完成前发起的列表刷新；`message_state_changed` 对普通未筛选邮件列表只原地更新状态并刷新文件夹计数，会话或筛选结果可能改变时才调用 `loadMessages(true)` 做保留当前内容的静默校正。

**Tech Stack:** Vue 3 + TypeScript、Node test runner、FastAPI 现有 `/api/mark-read`、WebSocket。

**Spec:** `docs/superpowers/specs/2026-08-12-verification-copy-mark-read-design.md`

## Global Constraints

- 不新增后端接口、数据库字段、迁移或生产依赖。
- 不修改 `/Docker/flymail/data` 数据结构，不删除正式邮件、附件或数据库数据。
- 复制成功仍立即标记已读；复制失败仍保持未读。
- `/api/mark-read` 仍负责远端 IMAP 已读同步和 `pending_read_sync` 兜底。
- 普通未筛选邮件列表的已读状态变化不得清空 `messages` 或显示加载骨架。
- 会话/筛选需要重新查询时必须使用 `loadMessages(true)`，保留当前可见列表直到新结果返回。
- `VERSION` 是版本事实来源，本次发布从 `0.0.56` 升至 `0.0.57`。

---

### Task 1: 用回归测试锁定无闪烁已读更新

**Files:**
- Modify: `frontend/tests/mail-verification-code.test.mjs`
- Modify: `frontend/src/views/MailList.vue`

**Interfaces:**
- Consumes: `markMessageRead(msg: Message)`, `handleWsMessage(data)`, `loadMessages(preserveVisible?: boolean)`, `mailStore.loadFolderCounts()`。
- Produces: 复制后的乐观已读路径不主动重新加载列表；WebSocket 已读状态在普通列表中原地同步。

- [ ] **Step 1: Write the failing test**

在 `frontend/tests/mail-verification-code.test.mjs` 增加契约：

```js
const markStart = source.indexOf('function markMessageRead(msg: Message)');
const copyStart = source.indexOf('async function copyVerificationCode(msg: Message)', markStart);
const markBlock = source.slice(markStart, copyStart);
assert.doesNotMatch(markBlock, /refreshCurrentListCounts\(\)/);
assert.doesNotMatch(markBlock, /loadMessages\(/);

const wsStart = source.indexOf("} else if (data.type === 'message_state_changed') {");
const markStateStart = source.indexOf("if (data.action === 'mark_read' || data.action === 'mark_unread') {", wsStart);
const deleteStateStart = source.indexOf("if (data.action === 'delete' || data.action === 'move') {", markStateStart);
const wsMarkBlock = source.slice(markStateStart, deleteStateStart);
assert.match(wsMarkBlock, /loadMessages\(true\)/);
assert.doesNotMatch(wsMarkBlock, /refreshCurrentListCounts\(\)/);
assert.doesNotMatch(wsMarkBlock, /refreshCurrentListState\(\)/);
```

- [ ] **Step 2: Run focused test and verify RED**

Run: `cd frontend && node --test tests/mail-verification-code.test.mjs`

Expected: FAIL because current `markMessageRead()` calls `refreshCurrentListCounts()` and the WebSocket path calls destructive refresh helpers.

- [ ] **Step 3: Implement the minimal fix**

`markMessageRead()` keeps local `is_read`、筛选计数、侧栏未读数和异步 `/mark-read`，但删除：

```ts
refreshCurrentListCounts();
```

`message_state_changed` 调整为：

```ts
if (data.action === 'mark_read' || data.action === 'mark_unread') {
  const isRead = data.action === 'mark_read';
  for (const uid of data.uids) {
    const msg = messages.value.find(m => String(m.uid) === String(uid));
    if (msg) {
      updateFilterCountsForReadChange(!!msg.is_read, isRead);
      msg.is_read = isRead;
    }
  }
  pageCache.clear();
  mailStore.loadFolderCounts();
  if (listMode.value === 'conversations' || hasActiveFilter.value) {
    loadMessages(true);
  }
  return;
}
```

删除/移动继续走原来的完整列表刷新逻辑。

- [ ] **Step 4: Run focused test and verify GREEN**

Run: `cd frontend && node --test tests/mail-verification-code.test.mjs`

Expected: PASS，且复制按钮、剪贴板 fallback、已读 API 契约继续通过。

---

### Task 2: 版本、全量验证和本地部署

**Files:**
- Modify: `VERSION`
- Modify via version sync: `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md`
- Verify: `README.md`, `.env.example`, Docker/runtime files

**Interfaces:**
- Consumes: `scripts/sync-version.js`、现有 Docker 单容器部署。
- Produces: 本地镜像 `benxianyu/flymail:0.0.57` 和健康运行的 `flymail` 容器。

- [ ] **Step 1: Bump and synchronize version**

把 `VERSION` 改为：

```text
0.0.57
```

运行：

```bash
npm run sync-version
```

Expected: 根 package、frontend package、compose 和 README 镜像标签均为 `0.0.57`；无环境变量变化。

- [ ] **Step 2: Run full source verification**

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

Expected: 全部项目检查通过；若工作区没有 `.env`，Compose 使用不泄露真实凭证的等价配置验证结构。

- [ ] **Step 3: Build and verify isolated temporary container**

```bash
docker build -t benxianyu/flymail:0.0.57 .
```

用独立临时目录与容器名验证：healthy、`/api/health` 返回 `0.0.57`、MySQL 8.0、`/data/mysql/`、`/data/flymail`、数据库读写、重启持久化、日志密码脱敏、镜像元数据无密码/密钥、安全关机。临时数据库密码至少包含引号、反斜杠、`@`、`:`、`/` 或 `%`。

Expected: 临时验证不触碰 `/Docker/flymail/data`，结束后清理临时容器和数据。

- [ ] **Step 4: Rebuild current local container**

保留当前正式容器环境变量、端口和 `/Docker/flymail/data:/data` 挂载，用 `benxianyu/flymail:0.0.57` 重建 `flymail`；确认重启后仍 healthy 且 `/api/health` 返回 `0.0.57`。

- [ ] **Step 5: Commit and push only this task**

```bash
git status --short
git diff --check
git diff
git add README.md VERSION docker-compose.yml package.json frontend/package.json frontend/src/views/MailList.vue frontend/tests/mail-verification-code.test.mjs docs/superpowers/plans/2026-08-19-verification-copy-no-flicker.md
git diff --staged
git commit -m "🐛 消除复制验证码标记已读时列表闪烁"
git push origin main
```

Expected: `origin/main` 与本地提交一致；不执行 `docker push`。
