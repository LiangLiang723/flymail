# ChatGPT 风格侧栏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除所有登录后页面的全局顶栏，把通知与用户入口迁入主侧栏，并让桌面侧栏折叠为图标栏、移动端使用统一抽屉导航。

**Architecture:** 保持现有单一 `App.vue` 应用壳，不引入新依赖。主导航改为扁平 `navItems`，桌面通过 CSS 网格在 220px 与 68px 之间切换；移动端继续复用当前抽屉和邮箱/文件夹导航，只增加独立浮动入口并把账号操作放到抽屉底部。

**Tech Stack:** Vue 3、TypeScript、Vite、Node.js 内置测试、Docker。

## Global Constraints

- 不修改后端接口、认证方式或数据库结构。
- 不触碰 `/Docker/flymail/data`，容器测试必须使用独立临时目录。
- 不新增或升级生产依赖。
- 桌面折叠状态继续保存在 `flymail_sidebar_collapsed`。
- 移动端断点保持 `960px`，邮件账号和文件夹仍在统一抽屉内。

---

### Task 1: 锁定新应用壳行为

**Files:**
- Modify: `frontend/tests/ui-layout.test.mjs`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `frontend/src/App.vue` 静态源码。
- Produces: 顶栏移除、扁平导航、侧栏账号操作、图标栏折叠和移动入口的回归断言。

- [ ] **Step 1: Write the failing test**

新增断言：源码不再包含 `class="topbar"` 和 `nav-group-label`；包含 `sidebar-header`、`sidebar-actions`、`sidebar-profile-trigger`、`mobile-sidebar-launcher`；折叠网格宽度为 `68px`，折叠时文字隐藏而侧栏仍可交互。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --test-name-pattern="application shell|responsive shell"`

Expected: FAIL，因为当前仍存在顶栏、分组标题，并且折叠宽度为 0。

### Task 2: 实现扁平可折叠侧栏

**Files:**
- Modify: `frontend/src/App.vue`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: 现有 `toggleSidebar()`、通知抽屉、用户菜单和 `flymail-mail-navigation` 事件。
- Produces: `navItems` 扁平数组；桌面 220px/68px 两态侧栏；侧栏底部通知与用户入口；移动抽屉与浮动菜单按钮。

- [ ] **Step 1: Write minimal implementation**

删除全局 `<header class="topbar">`；在侧栏头部放品牌与折叠按钮；将导航渲染改为单层按钮；在侧栏底部加入通知按钮和用户菜单；CSS 将折叠态改为 68px 图标栏并仅隐藏文字；移动端保留完整抽屉并显示浮动菜单按钮。

- [ ] **Step 2: Run test to verify it passes**

Run: `cd frontend && npm test`

Expected: PASS。

- [ ] **Step 3: Run typecheck and production build**

Run: `cd frontend && npm run build`

Expected: `vue-tsc` 与 Vite 构建成功。

### Task 3: 同步版本、文档与交付验证

**Files:**
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `scripts/sync-version.js`。
- Produces: 版本 `0.0.11` 的一致元数据和最新侧栏说明。

- [ ] **Step 1: Update version and documentation**

将 `VERSION` 更新为 `0.0.11`，运行 `npm run sync-version`，并把 README 的桌面/移动导航描述更新为扁平侧栏、图标折叠态和无全局顶栏。

- [ ] **Step 2: Run repository checks**

Run: `cd backend && python -m unittest discover -s tests -v`

Run: `bash -n scripts/docker-entrypoint.sh && docker compose config && git diff --check`

Expected: 全部成功。

- [ ] **Step 3: Build and run isolated Docker verification**

Run: `docker build -t benxianyu/flymail:0.0.11 .`

使用独立临时目录启动临时容器，验证健康接口版本、MySQL 8.0、`/data/mysql`、`/data/flymail`、数据库读写、重启持久化、日志脱敏、镜像元数据无密码以及停止时安全关闭。

- [ ] **Step 4: Commit and push**

仅暂存本任务文件，提交标题使用 `🎨 重构全局侧栏并移除顶栏`，推送到 `origin/main`。
