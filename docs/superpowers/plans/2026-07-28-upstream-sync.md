# FlyMail Upstream Sync Implementation Plan

**Goal:** 将 `DinDing1/FlyMail` 在重置基线 `636af10` 之后至 `79ab879` 的功能和修复同步到当前 Docker 多用户版，同时保留本项目的本地登录、多用户隔离、MySQL、历史同步和 `/data` 持久化能力。

**Architecture:** 以上游重置根提交 `636af10` 作为人工共同基线，先导入三方合并可自动融合的独立模块，再按当前项目的数据模型和路由约定适配核心冲突。所有新增数据继续按 `user_uid` 隔离；所有文件访问受当前数据目录或明确授权目录约束；现有 Docker 和版本未提交改动不被覆盖。

**Tech Stack:** FastAPI、Pydantic 2、MySQL/aiomysql、Vue 3、TypeScript、Pinia、Docker。

## Global Constraints

- 只在 `/home/chatgpt/flymail` 工作。
- 不覆盖当前未提交的 Docker、版本、README、调度器和测试改动。
- 不删除或迁移 `/Docker/flymail/data`。
- 不移除本地认证、管理员、用户隔离、历史同步或 MySQL 支持。
- 自定义 IMAP/SMTP 仅支持 SSL/TLS 与 STARTTLS；禁止明文认证。
- 自定义服务器禁止回环、私有、链路本地、组播、保留和未指定地址。
- API 和日志不得泄露邮箱密码、授权码、OAuth token 或完整数据库连接串。
- 不上传 Docker Hub。

---

### Task 1: 建立上游差异基线与回归测试

**Files:**
- Create: `backend/tests/test_upstream_sync_security.py`
- Create: `backend/tests/test_contacts_isolation.py`
- Create: `backend/tests/test_unified_inbox_isolation.py`

**Interfaces:**
- Produces: 自定义服务器校验、联系人隔离、聚合收件箱隔离的可执行验收条件。

- [ ] 编写自定义邮件服务器受限地址与加密模式失败测试。
- [ ] 编写联系人只能访问当前 `user_uid` 数据的失败测试。
- [ ] 编写聚合收件箱只能查询当前用户账号的失败测试。
- [ ] 运行新增测试，确认因功能缺失失败。

### Task 2: 导入无冲突上游模块

**Files:**
- Add upstream clean modules under `backend/routes/`, `backend/services/notify/`, `frontend/src/components/`, `frontend/src/composables/`, `frontend/src/views/`, `frontend/public/`, and `flymail-imgbed/`.
- Preserve: `backend/services/scheduler.py` current worktree changes.

**Interfaces:**
- Produces: 联系人、备份、通知、PWA、PDF、NAS 选择器等独立实现文件。

- [ ] 从三方合并结果树导入无冲突文本与二进制文件。
- [ ] 排除当前已修改文件和调度器。
- [ ] 检查没有冲突标记或意外删除。

### Task 3: 适配后端数据模型与 API

**Files:**
- Modify: `backend/models/__init__.py`
- Modify: `backend/db/__init__.py`
- Modify: `backend/schemas.py`
- Modify: `backend/main.py`
- Modify: `backend/routes/accounts.py`
- Modify: `backend/routes/messages.py`
- Modify: `backend/routes/settings.py`
- Modify: `backend/routes/compose.py`

**Interfaces:**
- Produces: contacts、unified inbox、backup、notification settings、custom/sina accounts、mark-all-read、NAS attachment APIs。

- [ ] 增加联系人、通知详情、归档和账号排序所需字段与表。
- [ ] 所有 SQL 查询加入 `user_uid` 归属条件。
- [ ] 注册新增路由。
- [ ] 接入自定义与新浪邮箱添加流程。
- [ ] 接入聚合收件箱、一键全部已读和通知详情字段。
- [ ] 接入备份与 NAS 附件校验。
- [ ] 运行后端测试并修复回归。

### Task 4: 适配 Provider 与协议修复

**Files:**
- Create/Modify: `backend/providers/custom/*`
- Create/Modify: `backend/providers/sina/*`
- Modify: `backend/providers/factory.py`
- Modify: `backend/providers/base_imap.py`
- Modify: existing provider receiver/sender files
- Modify: `backend/services/idle_manager.py`
- Modify: `backend/services/sync.py`
- Modify: `backend/services/token.py`

**Interfaces:**
- Produces: `custom` 与 `sina` Provider；统一 Message-ID；安全 TLS；轮询噪声修复。

- [ ] 先让自定义服务器安全测试失败。
- [ ] 实现主机格式、DNS 解析和受限地址校验。
- [ ] 实现 SSL/TLS 与 STARTTLS 连接，禁止 `none` 和跳过证书校验。
- [ ] 注册 custom/sina provider。
- [ ] 移植 SMTP Message-ID、IMAP INTERNALDATE、连接超时和日志修复。
- [ ] 运行 provider 与同步测试。

### Task 5: 适配前端功能与导航

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/views/AccountList.vue`
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/stores/mail.ts`
- Modify: `frontend/src/types/mail.ts`
- Modify: `frontend/src/utils/provider.ts`
- Modify: `frontend/src/utils/mail-helpers.ts`
- Modify: `frontend/src/utils/sanitize.ts`
- Modify: `frontend/src/composables/useWebSocket.ts`
- Modify: `frontend/index.html`
- Modify: `frontend/vite.config.ts`

**Interfaces:**
- Produces: 联系人、聚合收件箱、备份、第三方通知、自定义/新浪邮箱、PDF 导出、移动端和 PWA 的用户入口。

- [ ] 保留登录、用户管理、历史同步导航。
- [ ] 新增聚合收件箱、联系人和备份视图。
- [ ] 新增 custom/sina 账号表单和重新连接逻辑。
- [ ] 接入联系人自动补全、回复收件人修复、PDF 导出、NAS 附件。
- [ ] 接入通知跳转、通知设置和移动端布局修复。
- [ ] 接入 PWA manifest 与静态资源路径处理。
- [ ] 运行 TypeScript 构建并修复全部错误。

### Task 6: 文档、完整验证、容器和交付

**Files:**
- Modify: `README.md` without overwriting existing Docker/MySQL edits.
- Review: `.env.example`, `Dockerfile`, `docker-compose.yml`, `scripts/docker-entrypoint.sh`, `VERSION`, package versions.

**Interfaces:**
- Produces: 可部署镜像和完整验证记录。

- [ ] 运行全量后端 unittest。
- [ ] 运行前端 `npm install && npm run build`。
- [ ] 运行 `bash -n`、`docker compose config`、`git diff --check`。
- [ ] 构建 `benxianyu/flymail:$(cat VERSION)`。
- [ ] 使用独立临时数据目录启动临时容器，检查健康、MySQL 8.0、数据库读写、重启持久化、日志脱敏和安全停止。
- [ ] 重新检查 README 和环境变量文档。
- [ ] 仅暂存本次同步文件，提交并推送 `origin/main`。
