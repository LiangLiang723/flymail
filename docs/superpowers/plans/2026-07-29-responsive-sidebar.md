# FlyMail 响应式侧边栏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为桌面端增加可记忆的主导航折叠，并将移动端改为包含主导航、账号和文件夹的左侧抽屉，同时修复邮件列表横向溢出。

**Architecture:** 继续使用现有 `App.vue` 页面壳和 `MailList.vue` 邮件逻辑。`App.vue` 负责桌面主导航折叠及移动抽屉，使用 `flymail-mail-navigation` 自定义事件把账号和文件夹选择交给 `MailList.vue`，避免复制邮件加载逻辑。

**Tech Stack:** Vue 3、TypeScript、Pinia、CSS、Node.js test runner、Vite、Docker。

## Global Constraints

- 桌面端只隐藏主导航栏，邮件账号与文件夹侧栏始终保留。
- 移动端不显示横向主导航胶囊或横向账号标签。
- 不修改后端接口、数据库、认证和 `/data` 路径。
- 不增加生产依赖。
- 点击邮件仍进入独立详情视图。

---

### Task 1: 响应式导航回归测试

**Files:**
- Modify: `frontend/tests/ui-layout.test.mjs`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `frontend/src/App.vue`、`frontend/src/views/MailList.vue`
- Produces: 折叠状态、移动抽屉和无横向账号条的源码结构约束

- [ ] **Step 1: 写失败测试**

增加断言：`App.vue` 包含 `sidebar-toggle`、`mobile-sidebar-backdrop`、`flymail_sidebar_collapsed` 和移动邮件导航；`MailList.vue` 不再包含 `mobile-account-tabs`，并监听 `flymail-mail-navigation`；移动端样式把 `.mail-item` 最小宽度设为 0。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test`
Expected: FAIL，提示缺少新结构或仍存在横向账号标签。

### Task 2: 桌面主导航折叠和移动抽屉

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/components/AppIcon.vue`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `navGroups`、`mailStore.accounts`、`mailStore.folders`
- Produces: `sidebarCollapsed`、`mobileSidebarOpen`、`toggleSidebar()`、`selectMobileMailNavigation()`

- [ ] **Step 1: 增加菜单与侧栏图标**

为 `AppIcon.vue` 增加 `menu`、`panel-left-open`、`panel-left-close` 和 `close` 图标。

- [ ] **Step 2: 增加响应式状态**

在 `App.vue` 中读取 `localStorage.getItem('flymail_sidebar_collapsed')`，监听窗口宽度，桌面点击按钮切换并持久化，移动端点击按钮打开抽屉。

- [ ] **Step 3: 重组模板**

顶部标题左侧加入切换按钮；移动端抽屉复用现有主导航，并在邮件管理页面增加账号和文件夹区域；加入遮罩和关闭按钮。

- [ ] **Step 4: 增加事件发送**

账号点击发送 `{ type: 'account', id }`，文件夹点击发送 `{ type: 'folder', path }` 的 `flymail-mail-navigation` 事件，并关闭移动抽屉。

- [ ] **Step 5: 调整样式**

桌面折叠时主导航列变为 0；960px 以下侧栏固定在左侧并使用 transform 打开/关闭，取消横向导航样式。

- [ ] **Step 6: 运行前端测试**

Run: `cd frontend && npm test`
Expected: 新增 App 相关断言通过，MailList 相关断言仍失败。

### Task 3: 移动邮件导航和列表布局

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `flymail-mail-navigation` 事件
- Produces: 复用 `switchAccount(id)` 与文件夹加载逻辑的 `handleMailNavigation(event)`

- [ ] **Step 1: 移除横向账号条**

删除移动端 `mobile-account-tabs` 模板，不改变桌面文件夹侧栏。

- [ ] **Step 2: 接收抽屉导航事件**

账号事件调用现有 `switchAccount()`；文件夹事件重置筛选、分页和缓存，调用 `mailStore.setFolder()` 后刷新列表。

- [ ] **Step 3: 简化移动工具栏**

当前文件夹按钮改为触发全局侧栏，不再打开底部文件夹选择器；移动端隐藏铺开的筛选标签，保留筛选菜单入口。

- [ ] **Step 4: 修复移动端邮件行**

覆盖 `.list-items`、`.mail-item`、`.mail-sender`、`.mail-main-row` 和状态列，取消固定最小宽度并形成单列可读布局。

- [ ] **Step 5: 运行测试与构建**

Run: `cd frontend && npm test && npm run build`
Expected: 测试、Vue 类型检查和 Vite 构建通过。

### Task 4: 文档、版本与完整验证

**Files:**
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `scripts/sync-version.js`
- Produces: 一致的 `0.0.9` 版本和部署文档

- [ ] **Step 1: 更新版本并同步**

将 `VERSION` 更新为 `0.0.9`，运行 `npm run sync-version`。

- [ ] **Step 2: 更新 README**

说明桌面主导航可折叠、移动端使用抽屉导航，环境变量不变。

- [ ] **Step 3: 运行完整验证**

Run: `cd backend && python -m unittest discover -s tests -v`
Run: `cd frontend && npm test && npm run build`
Run: `bash -n scripts/docker-entrypoint.sh && docker compose config -q && git diff --check`

- [ ] **Step 4: 构建和验证 Docker**

构建 `benxianyu/flymail:0.0.9`，使用独立临时目录验证 healthy、健康版本、MySQL 8.0、数据库读写、重启持久化、日志脱敏和安全停止。

- [ ] **Step 5: 重建当前容器**

保留 `/Docker/flymail/data`，使用新镜像替换 `flymail`，确认健康和现有数据库仍可访问。

- [ ] **Step 6: 提交并推送**

Commit: `📱 重构移动导航并支持桌面侧栏折叠`
Push: `origin/main`。默认不上传 Docker Hub。
