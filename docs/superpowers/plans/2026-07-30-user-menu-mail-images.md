# 用户菜单与邮件图片体验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构主侧边栏和用户菜单，支持用户名、昵称、头像管理，并修复多内嵌图片显示及提供移动端友好的图片查看器。

**Architecture:** 用户资料扩展现有 `users` 表并将头像文件持久化到 `/data/flymail/files/avatars`，认证会话继续只绑定用户 ID。邮件解析保留 CID 引用，将每张内嵌图片独立缓存后改写为受保护的本地附件地址；前端仅展示普通附件，并通过独立图片查看器处理正文图片。

**Tech Stack:** FastAPI、Pydantic、MySQL、Pillow、Vue 3、TypeScript、DOMPurify、Pointer Events、Docker。

## Global Constraints

- 不改变现有认证方式与用户数据隔离边界。
- 不新增生产依赖；头像处理复用现有 Pillow。
- 所有持久化文件仅写入 `/data/flymail`。
- 普通附件继续按需缓存，正文内嵌图片继续自动缓存。
- 桌面端支持键盘和鼠标缩放，移动端支持双指缩放、拖动和左右滑动切换。
- 不读取、覆盖或删除 `/Docker/flymail/data`。

---

### Task 1: 修复内嵌图片解析与附件语义

**Files:**
- Modify: `backend/providers/base_imap.py`
- Modify: `backend/providers/base.py`
- Modify: `backend/services/history_sync.py`
- Modify: `backend/services/mail_cache.py`
- Modify: `backend/routes/messages.py`
- Test: `backend/tests/test_imap_inline_images.py`
- Test: `backend/tests/test_history_sync_folders.py`

- [ ] 写两张无文件名 CID 图片的失败测试，确认正文不再嵌入 base64 且两张图片均进入内嵌缓存流程。
- [ ] 运行定向测试并确认因当前解析行为失败。
- [ ] 保留 CID、记录所有内嵌图片、只用普通附件计算 `has_attachments`，并对 CID 与查询参数做健壮改写。
- [ ] 过滤详情响应中的内嵌附件，同时保留内部下载路由。
- [ ] 运行定向及全部后端测试。

### Task 2: 增加用户资料和头像持久化

**Files:**
- Modify: `backend/data_paths.py`
- Modify: `backend/models/__init__.py`
- Modify: `backend/db/__init__.py`
- Modify: `backend/routes/local_auth.py`
- Modify: `backend/routes/admin_users.py`
- Modify: `backend/services/users.py`
- Create: `backend/services/user_profiles.py`
- Test: `backend/tests/test_user_profiles.py`

- [ ] 写资料字段、用户名唯一性和头像缩放存储的失败测试。
- [ ] 运行定向测试并确认失败。
- [ ] 增加数据库兼容迁移、资料更新查询和头像目录。
- [ ] 增加当前用户与管理员资料/头像接口，删除用户时清理头像。
- [ ] 运行定向及全部后端测试。

### Task 3: 重构侧边栏与账号菜单

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/components/app/AppSidebar.vue`
- Modify: `frontend/src/components/app/UserMenu.vue`
- Modify: `frontend/src/styles/app-shell.css`
- Create: `frontend/src/views/Profile.vue`
- Modify: `frontend/src/views/UserManagement.vue`
- Test: `frontend/tests/ui-layout.test.mjs`
- Test: `frontend/tests/profile-and-image-viewer.test.mjs`

- [ ] 写主导航精简、账号菜单入口和资料编辑界面的失败测试。
- [ ] 运行前端测试并确认失败。
- [ ] 将管理型入口移到头像菜单，保持通知入口和移动端邮件导航可用。
- [ ] 实现当前用户资料页及管理员编辑弹窗，资料更新后立即刷新头像和显示名。
- [ ] 统一侧边栏表面、间距、选中态和深浅色变量。
- [ ] 运行前端测试和构建。

### Task 4: 增加邮件图片查看器

**Files:**
- Create: `frontend/src/components/mail/ImageViewer.vue`
- Create: `frontend/src/utils/image-viewer.ts`
- Modify: `frontend/src/views/MailList.vue`
- Test: `frontend/tests/image-viewer.test.ts`
- Test: `frontend/tests/profile-and-image-viewer.test.mjs`

- [ ] 写缩放边界、循环切换、滑动判定和组件接入的失败测试。
- [ ] 运行定向测试并确认失败。
- [ ] 实现正文图片事件委托、普通附件过滤和同邮件图片集合。
- [ ] 实现键盘、滚轮、双击、Pointer Events 双指缩放/拖动/滑动以及低动态适配。
- [ ] 运行前端测试和构建。

### Task 5: 文档、版本、Docker 与交付

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via `npm run sync-version`: `package.json`, `frontend/package.json`, `docker-compose.yml`, `README.md`

- [ ] 将版本更新到 `0.0.19` 并同步所有版本载体。
- [ ] 更新 README 的资料管理、头像路径、附件和图片查看器说明。
- [ ] 运行后端测试、前端测试与构建、Shell 语法、Compose 配置和 Git 检查。
- [ ] 构建 `benxianyu/flymail:0.0.19`，使用独立临时目录和容器验证健康、MySQL 8.0、持久化、重启、脱敏和安全关闭。
- [ ] 安全重建当前 `flymail` 容器并确认 `/Docker/flymail/data` 未被替换或删除。
- [ ] 检查差异，仅提交本次文件并推送 `origin/main`；不上传 Docker Hub。
