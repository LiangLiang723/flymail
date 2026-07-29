# FlyMail 邮件界面重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有邮件交互和详情页切换方式的前提下，完成主导航、顶部用户区、文件夹栏和邮件列表的视觉重构。

**Architecture:** 继续使用现有 Vue 3 单页结构、Pinia 状态和 `currentView` 页面切换。只在 `App.vue` 与 `MailList.vue` 中调整模板和样式，并新增一个无状态 SVG 图标组件；不改变 API、Store 或后端行为。

**Tech Stack:** Vue 3、TypeScript、Pinia、CSS、Node.js test runner、Vite、Docker。

## Global Constraints

- 不增加右侧常驻邮件预览栏。
- 邮件列表工具栏继续位于邮件列表卡片内部。
- 不修改后端接口、数据库、认证和持久化路径。
- 不新增或升级生产依赖。
- 移动端现有文件夹弹层、详情返回和分页行为必须保留。

---

### Task 1: 建立 UI 结构回归测试

**Files:**
- Create: `frontend/tests/ui-layout.test.mjs`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `frontend/src/App.vue`、`frontend/src/views/MailList.vue`
- Produces: 应用导航分组、用户菜单、文件夹标题、列表内工具栏和无右侧预览栏的源码结构约束

- [ ] **Step 1: 写失败测试**

使用 Node.js 读取 Vue 文件，断言 `App.vue` 包含 `nav-groups`、`user-menu-trigger`，`MailList.vue` 包含 `folder-sidebar-header`，并断言 `list-toolbar` 位于 `mail-list` 内且不存在常驻 `mail-preview-pane`。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- tests/ui-layout.test.mjs`
Expected: FAIL，缺少新的结构类名。

- [ ] **Step 3: 保留测试等待实现**

测试只验证用户已经确认的布局边界，不断言像素值或易变化文案。

### Task 2: 重构应用导航和用户菜单

**Files:**
- Create: `frontend/src/components/AppIcon.vue`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/styles/macos.css`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `currentView`、`isAdmin`、`changePassword()`、`logout()`、通知 Store
- Produces: `navGroups` 分组导航、`AppIcon` 图标组件、`showUserMenu` 用户菜单状态

- [ ] **Step 1: 新增无状态图标组件**

创建 `AppIcon.vue`，接收 `name: string` 和可选 `size: number`，为导航、通知、用户菜单和文件夹提供统一的 20px 线性 SVG 图标。

- [ ] **Step 2: 将导航数据改为分组结构**

在 `App.vue` 定义 `navGroups`，分为“邮件”“管理”“系统”，管理员用户在系统组中显示“用户管理”；`navItems` 由分组扁平化，继续供标题和合法性检查使用。

- [ ] **Step 3: 收拢顶部用户操作**

将通知按钮改为图标按钮；新增头像、用户名和下拉菜单，菜单中调用现有 `changePassword()` 与 `logout()`；打开一个浮层时关闭另一个浮层。

- [ ] **Step 4: 调整主框架样式**

主侧栏宽度改为约 220px，增加分组标题、图标间距、低饱和激活态和底部版本信息；顶部标题栏保持独立，不承载邮件工具栏。

- [ ] **Step 5: 运行前端测试**

Run: `cd frontend && npm test`
Expected: PASS。

### Task 3: 重构文件夹栏和邮件列表视觉

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `mailStore.accounts`、`mailStore.folders`、`switchAccount()`、现有搜索筛选刷新函数
- Produces: 文件夹标题、账号切换区、文件夹图标、降噪邮件行和保持卡片内部的工具栏

- [ ] **Step 1: 重组文件夹栏模板**

将多账号切换区域移动到桌面文件夹栏内部，新增“文件夹”标题和当前账号区域；单账号授权提示保留。

- [ ] **Step 2: 增加文件夹图标和更清晰的计数**

使用 `AppIcon` 根据核心文件夹名称显示 inbox、send、draft、trash 等图标，自定义文件夹使用 folder 图标；不改变 `setFolder()` 和计数函数。

- [ ] **Step 3: 保持列表工具栏结构并优化视觉**

保留现有 `list-toolbar` 在 `mail-list` 内部，只调整高度、搜索框、筛选标签、图标按钮和连接状态；不创建页面级顶部工具条。

- [ ] **Step 4: 降低邮件行噪声**

取消已读邮件的大面积灰色底色，未读邮件使用左侧状态点、字重和弱化标签；调整发件人、主题、附件和日期列宽及悬停状态。

- [ ] **Step 5: 检查移动端覆盖规则**

确保 768px 以下继续隐藏桌面文件夹栏，并保留横向列表、文件夹弹层和详情页结构。

- [ ] **Step 6: 运行前端测试与构建**

Run: `cd frontend && npm test && npm run build`
Expected: PASS，Vue 类型检查和 Vite 构建成功。

### Task 4: 版本和文档同步

**Files:**
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `scripts/sync-version.js`
- Produces: 一致的 `0.0.6` 版本和 UI 重构说明

- [ ] **Step 1: 将 VERSION 更新为 0.0.6**

- [ ] **Step 2: 执行版本同步**

Run: `npm run sync-version`
Expected: 根包、前端包、Compose 和 README 的镜像标签均为 `0.0.6`。

- [ ] **Step 3: 更新 README 当前能力**

补充桌面端分组导航、紧凑邮件列表和保持点击进入详情的界面说明；环境变量章节不变。

### Task 5: 完整验证、容器替换和交付

**Files:**
- Verify only: all changed files

**Interfaces:**
- Consumes: 完成后的源码和 `benxianyu/flymail:0.0.6`
- Produces: 可运行、可持久化、已推送的 `main` 分支

- [ ] **Step 1: 运行后端测试**

Run: `cd backend && python -m unittest discover -s tests -v`
Expected: 全部通过。

- [ ] **Step 2: 运行静态检查**

Run: `bash -n scripts/docker-entrypoint.sh && docker compose config && git diff --check`
Expected: 全部成功。

- [ ] **Step 3: 构建 Docker 镜像**

Run: `docker build -t benxianyu/flymail:0.0.6 .`
Expected: 构建成功，镜像元数据无密码和密钥。

- [ ] **Step 4: 使用独立临时目录启动临时容器**

验证容器 healthy、`/api/health` 返回 `0.0.6`、MySQL 8.0、数据目录 `/data/mysql/`、`/data/flymail` 创建、数据库读写和重启持久化、日志密码脱敏、安全停止。

- [ ] **Step 5: 重建当前 flymail 容器**

保留 `/Docker/flymail/data`，使用新镜像重建当前容器并验证健康接口。不得删除或初始化现有数据目录。

- [ ] **Step 6: 检查并提交本次文件**

Run: `git status --short && git diff --check && git diff`
Commit: `🎨 重构邮件界面提升导航与列表层级`

- [ ] **Step 7: 推送 origin/main**

Run: `git push origin main`；22 端口失败时使用 GitHub SSH 443。默认不上传 Docker Hub。
