# 同步卡片操作菜单与侧边栏版本移除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将同步卡片的次要与危险操作收进可访问的“更多”菜单，并移除侧边栏底部重复版本号。

**Architecture:** 保留 `HistorySync.vue` 现有同步状态判断和 API 方法，只在模板层重排操作入口，并用单个 `openActionMenuId` 管理当前打开菜单。侧边栏版本展示从 `AppSidebar.vue`、`App.vue` 和 `app-shell.css` 完整移除，不改变关于页或健康接口的版本来源。

**Tech Stack:** Vue 3 `<script setup lang="ts">`、现有 CSS 语义变量、Node.js `node:test` 静态回归测试、Docker 单容器部署。

## Global Constraints

- 只修改同步卡片操作区和侧边栏版本显示，不进行整站重构。
- 不修改后端接口、同步任务状态判断、确认弹窗、权限、数据库和 `/Docker/flymail/data`。
- 不新增或升级生产依赖。
- 保留现有刷新同步、清空、暂停、继续、重试的 API 调用和提示行为。
- 用户原有 `.benchmarks` 暂存文件不得修改或进入提交。

---

### Task 1: 添加同步卡片与侧边栏回归契约

**Files:**
- Create: `frontend/tests/sync-card-actions.test.mjs`
- Test: `frontend/tests/sync-card-actions.test.mjs`

**Interfaces:**
- Consumes: `frontend/src/views/HistorySync.vue`、`frontend/src/components/app/AppSidebar.vue`、`frontend/src/App.vue`、`frontend/src/styles/app-shell.css` 的源码文本。
- Produces: 对更多菜单结构、关闭行为和版本参数清理的静态回归契约。

- [ ] **Step 1: 写入失败测试**

```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (relativePath) => readFile(path.join(frontendRoot, relativePath), 'utf8');

test('sync cards keep one primary action and move secondary actions into an accessible menu', async () => {
  const source = await read('src/views/HistorySync.vue');
  assert.doesNotMatch(source, /disabled>暂停\/继续<\/button>/);
  assert.match(source, /aria-label="更多操作"/);
  assert.match(source, /role="menu"/);
  assert.match(source, /role="menuitem"[^>]*[\s\S]*刷新同步/);
  assert.match(source, /role="menuitem"[^>]*[\s\S]*清空同步数据/);
  assert.match(source, /openActionMenuId/);
  assert.match(source, /handleActionMenuPointerDown/);
  assert.match(source, /event\.key === 'Escape'/);
});

test('sidebar no longer receives or renders an application version', async () => {
  const app = await read('src/App.vue');
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const css = await read('src/styles/app-shell.css');
  assert.doesNotMatch(app, /app-version|const appVersion/);
  assert.doesNotMatch(sidebar, /sidebar-version|appVersion/);
  assert.doesNotMatch(css, /\.sidebar-version/);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && node --test tests/sync-card-actions.test.mjs`
Expected: FAIL，指出更多菜单尚不存在且侧边栏仍包含版本节点。

---

### Task 2: 实现同步卡片更多菜单

**Files:**
- Modify: `frontend/src/views/HistorySync.vue`
- Test: `frontend/tests/sync-card-actions.test.mjs`

**Interfaces:**
- Consumes: 现有 `pauseJob`、`resumeJob`、`retryJob`、`refreshSync`、`clearJob`、`isFullSyncActive`、`isClearActive`。
- Produces: `openActionMenuId: Ref<string | null>`、`toggleActionMenu(accountId)`、`runActionMenuCommand(command)`、`handleActionMenuPointerDown(event)`、`handleActionMenuKeydown(event)`。

- [ ] **Step 1: 重排卡片操作模板**

直接操作只保留状态主操作；删除禁用占位按钮。新增 `.job-more` 容器、带 `aria-haspopup="menu"`、动态 `aria-expanded` 和 `aria-label="更多操作"` 的 `···` 按钮，以及包含“刷新同步”和“清空同步数据”的 `role="menu"` 弹层。

- [ ] **Step 2: 增加菜单状态和事件处理**

```ts
const openActionMenuId = ref<string | null>(null);

function toggleActionMenu(accountId: string) {
  openActionMenuId.value = openActionMenuId.value === accountId ? null : accountId;
}

function runActionMenuCommand(command: () => void | Promise<void>) {
  openActionMenuId.value = null;
  void command();
}

function handleActionMenuPointerDown(event: PointerEvent) {
  if (!openActionMenuId.value) return;
  const target = event.target;
  if (!(target instanceof Element) || !target.closest('.job-more')) openActionMenuId.value = null;
}

function handleActionMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') openActionMenuId.value = null;
}
```

在 `onMounted` 注册 `pointerdown` 和 `keydown`，在 `onBeforeUnmount` 对称移除。

- [ ] **Step 3: 增加菜单样式**

`.job-actions` 保持右对齐但不再为多个按钮换行；`.job-more` 使用相对定位；菜单绝对定位在右下方，最小宽度 168px、最高层级、边框和浮层阴影；菜单项使用 40px 最小高度，危险项使用 `var(--ui-danger)`；移动端菜单使用 `max-width: calc(100vw - 32px)`。

- [ ] **Step 4: 运行聚焦测试**

Run: `cd frontend && node --test tests/sync-card-actions.test.mjs`
Expected: 同步卡片测试通过，侧边栏版本测试仍失败。

---

### Task 3: 移除侧边栏版本参数和样式

**Files:**
- Modify: `frontend/src/components/app/AppSidebar.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles/app-shell.css`
- Test: `frontend/tests/sync-card-actions.test.mjs`

**Interfaces:**
- Consumes: `AppSidebar` 现有用户和导航属性。
- Produces: 不再包含 `appVersion` 属性的 `AppSidebar` 公共接口。

- [ ] **Step 1: 删除侧边栏版本节点与属性**

从 `AppSidebar.vue` 删除 `.sidebar-version` 节点和 `appVersion: string` 属性。

- [ ] **Step 2: 删除应用层版本传递**

从 `App.vue` 删除 `:app-version="appVersion"` 和仅服务于该节点的 `const appVersion`。

- [ ] **Step 3: 删除孤立样式**

从 `app-shell.css` 删除桌面 `.sidebar-version` 规则及移动媒体查询中的 `.sidebar-version` 规则。

- [ ] **Step 4: 运行聚焦测试**

Run: `cd frontend && node --test tests/sync-card-actions.test.mjs`
Expected: 2 个测试全部通过。

---

### Task 4: 版本、文档与完整交付验证

**Files:**
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `VERSION` 唯一版本事实来源和 `npm run sync-version`。
- Produces: 一致的 `0.0.27` 发布版本和本地 Docker 镜像。

- [ ] **Step 1: 将 VERSION 更新为 0.0.27 并同步**

Run: `npm run sync-version`
Expected: 根包、前端包、Compose 和 README 镜像标签均为 `0.0.27`。

- [ ] **Step 2: 运行前端和后端检查**

Run: `cd frontend && npm test && npm run build`
Expected: 全部测试通过，Vue 类型检查和 Vite 构建成功。

Run: `cd backend && python -m unittest discover -s tests -v`
Expected: 全部后端测试通过。

- [ ] **Step 3: 运行部署静态检查**

Run: `bash -n scripts/docker-entrypoint.sh`
Expected: 退出码 0。

Run: `printf 'services:\n  flymail-app:\n    env_file: !reset []\n' | docker compose -f docker-compose.yml -f - config --quiet`
Expected: 退出码 0，且不创建 `.env`。

Run: `git diff --check`
Expected: 无输出。

- [ ] **Step 4: 构建和验证临时容器**

Run: `docker build -t benxianyu/flymail:$(cat VERSION) .`
Expected: 构建成功。

使用独立 `/tmp` 数据目录启动临时容器，验证 `/api/health` 返回 `0.0.27`、MySQL 8.0、`/data/mysql/`、数据库读写与重启持久化、日志脱敏、镜像元数据、静态产物包含更多菜单且不包含侧边栏版本节点，以及 SIGTERM 安全关闭。

- [ ] **Step 5: 安全替换正式容器**

复用当前 `flymail` 的端口、环境、网络、重启策略和 `/Docker/flymail/data:/data`，保留旧容器作为回滚副本；新容器健康、用户数一致且重启通过后删除回滚副本。

- [ ] **Step 6: 提交并推送**

只暂存本次任务文件，执行 `git diff --staged` 审查后提交：

```bash
git commit -m "🎨 优化同步卡片操作菜单并移除侧栏版本号"
git push origin main
```

Expected: 本地与 `origin/main` SHA 一致，`.benchmarks` 仍保持用户原有暂存状态，Docker Hub 未上传。
