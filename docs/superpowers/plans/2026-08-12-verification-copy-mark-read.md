# Verification Copy Mark Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 成功复制邮件验证码后，把对应未读邮件立即标记为已读，并复用现有远端已读同步机制。

**Architecture:** 保持后端 `/api/mark-read` 不变。前端把 `selectMessage()` 中已有的单封邮件乐观已读逻辑抽成 `markMessageRead(msg)`，打开邮件和验证码复制成功后共同调用；剪贴板失败不调用该函数。

**Tech Stack:** Vue 3 + TypeScript、Node test runner、FastAPI 现有 `/api/mark-read`。

## Global Constraints

- 不新增数据库字段、迁移或生产依赖。
- 不修改 `/Docker/flymail/data` 数据结构。
- 不记录验证码值。
- 复制按钮继续 `stopPropagation`，不能打开邮件详情。
- 标记远端已读失败继续交给现有 `pending_read_sync` 机制。
- `VERSION` 是版本事实来源；发布版本同步到 package、compose 与 README。

---

### Task 1: 锁定复制成功后标记已读行为

**Files:**
- Modify: `frontend/tests/mail-verification-code.test.mjs`
- Modify: `frontend/src/views/MailList.vue`

**Interfaces:**
- Consumes: `Message`、`api.post('/mark-read', ...)`、现有 `updateFilterCountsForReadChange()`、`mailStore.decrementUnreadCount()`、`refreshCurrentListCounts()`。
- Produces: `markMessageRead(msg: Message): void` 与 `copyVerificationCode(msg: Message): Promise<void>`。

- [ ] **Step 1: Write the failing test**

把复制按钮契约改为传递整封邮件，并断言复制成功路径调用 `markMessageRead(msg)`：

```js
assert.match(source, /@click\.stop="copyVerificationCode\(msg\)"/);
assert.match(source, /async function copyVerificationCode\(msg: Message\)/);
assert.match(source, /uiStore\.success\('验证码已复制'\);\s*markMessageRead\(msg\);/s);
assert.match(source, /function markMessageRead\(msg: Message\)/);
assert.match(source, /api\.post\('\/mark-read', \{[\s\S]*message_id: msg\.id/s);
```

同时锁定失败路径不会在 catch 之前先标记已读。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && node --test tests/mail-verification-code.test.mjs`
Expected: FAIL，因为当前按钮传 `msg.verification_code`，且没有 `markMessageRead()`。

- [ ] **Step 3: Write minimal implementation**

把按钮改为：

```vue
@click.stop="copyVerificationCode(msg)"
```

把已有未读更新块抽成：

```ts
function markMessageRead(msg: Message) {
  if (noReadStateFolder.value || msg.is_read) return;
  // 更新 messages / conversationMessages / 筛选计数
  // 异步 POST /mark-read
  // 侧栏未读数减一并刷新列表计数
}
```

`copyVerificationCode(msg)` 使用 `msg.verification_code` 写剪贴板；主路径或 legacy fallback 成功后显示成功提示并调用 `markMessageRead(msg)`，失败时只显示复制失败。

`selectMessage()` 在详情读取成功后改为调用同一个 `markMessageRead(msg)`。

- [ ] **Step 4: Run focused test to verify it passes**

Run: `cd frontend && node --test tests/mail-verification-code.test.mjs`
Expected: PASS。

---

### Task 2: 文档与版本同步

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md`

**Interfaces:**
- Consumes: `scripts/sync-version.js`。
- Produces: 全部发布位置统一到 `0.0.56`。

- [ ] **Step 1: Update README behavior**

在验证码列表能力说明中补充：成功复制验证码后，该未读邮件会立即标记已读并同步到邮箱服务器；远端暂时失败时使用现有延迟同步机制。

- [ ] **Step 2: Bump version**

把 `VERSION` 从 `0.0.55` 改为 `0.0.56`，运行：

```bash
npm run sync-version
```

Expected: 根 package、frontend package、compose 镜像标签和 README 镜像标签全部为 `0.0.56`。

---

### Task 3: 全量验证、容器验证与交付

**Files:**
- Verify only; no new files expected.

**Interfaces:**
- Produces: 可提交的 `0.0.56` 本地 Docker 镜像与运行中的 `flymail` 容器。

- [ ] **Step 1: Run backend and frontend checks**

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm test && npm run build
```

Expected: 全部通过。

- [ ] **Step 2: Run repository checks**

```bash
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
```

Expected: 全部检查通过，只有本次任务文件变化。

- [ ] **Step 3: Build and run isolated temporary container**

构建 `benxianyu/flymail:0.0.56`，使用独立临时数据目录和临时容器名验证：healthy、`/api/health` 版本、MySQL 8.0、`/data/mysql`、`/data/flymail`、数据库读写、重启持久化、日志密码脱敏、镜像元数据无密码密钥、停止时 MySQL 安全关闭。

Expected: 全部通过且不触碰 `/Docker/flymail/data`。

- [ ] **Step 4: Rebuild current local container**

在完整验证后用当前 `.env` 和 `/Docker/flymail/data` 重建 `flymail`，确认健康接口返回 `0.0.56`。

Expected: 现有数据保留，容器 healthy。

- [ ] **Step 5: Commit and push**

仅暂存本次文件，检查 staged diff 后提交：

```bash
git commit -m "🐛 修复复制验证码后邮件保持未读"
git push origin main
```

Expected: 推送到 `origin/main` 成功；不上传 Docker Hub。
