# FlyMail 邮件阅读、签名与联系人完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 修复邮件正文溢出与写信布局问题，并提供按邮箱生效的多签名管理及从本地往来邮件批量导入联系人的能力。

**Architecture:** 邮件正文由唯一的详情滚动容器负责双轴滚动，渲染前归零会把内容推到左侧边界之外的负水平边距。签名沿用现有 `signatures` 表并增加回复默认字段，以“用户 + 邮箱范围”维持新邮件和回复/转发两套默认；写信页使用固定层级的管理弹窗和可替换签名块。联系人候选只从当前用户指定邮箱的 `cached_messages` 聚合，过滤本人及现有联系人后由用户勾选导入。

**Tech Stack:** FastAPI、Pydantic、MySQL 8.0、Vue 3、TypeScript、Tiptap、Node test、Python unittest、Docker。

## Global Constraints

- 只在 `/home/chatgpt/flymail` 工作，保护 `/Docker/flymail/data`。
- 保持多用户、邮箱及联系人数据隔离，不记录密码、令牌或完整数据库连接地址。
- 不新增生产依赖，不上传 Docker Hub。
- `VERSION` 是版本唯一事实来源，行为变更发布为 `0.0.34`。

---

### Task 1: 邮件正文滚动与左边界

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/utils/mail-body-theme.ts`
- Test: `frontend/tests/mail-reading-layout.test.mjs`
- Test: `frontend/tests/mail-body-layout.test.ts`

**Interfaces:**
- Produces: `isNegativeCssLength(value: string): boolean`
- Produces: `clampNegativeHorizontalMargins(element: HTMLElement): void`

- [x] 运行现有回归测试，确认双层横向滚动、负边距和工具栏层级测试失败。
- [x] 在主题适配遍历中归零负的 `margin-left` 与 `margin-inline-start`。
- [x] 让 `.detail-body` 独占双轴滚动，正文包装层不再创建第二个横向滚动上下文，并允许固定宽度表格自然触发横向滚动。
- [x] 运行邮件正文相关测试确认通过。

### Task 2: 写信表单对齐与签名浮层

**Files:**
- Modify: `frontend/src/views/ComposeEmail.vue`
- Modify: `frontend/src/components/TiptapEditor.vue`
- Test: `frontend/tests/mail-reading-layout.test.mjs`
- Create: `frontend/tests/compose-signature-contract.test.mjs`

**Interfaces:**
- Produces: 写信表单统一的 `.compose-field-label` 与 `.compose-field-control` 网格。
- Produces: Tiptap 暴露 `setManagedSignature(id: number | null, html?: string)`。

- [x] 新增签名切换、无签名、按邮箱范围和两类默认设置的失败契约测试。
- [x] 将发件人、收件人、抄送、密送和主题改为同一两列网格结构。
- [x] 给工具栏建立明确的相对定位、正层级和可见溢出，管理弹窗使用固定定位脱离收件人表单。
- [x] 在编辑器增加可识别签名块，切换签名时替换现有签名而不是重复追加。
- [x] 将原始 HTML 文本框替换为富文本签名编辑器，并提供模板起点、邮箱范围、新邮件默认、回复/转发默认和删除操作。
- [x] 运行前端契约测试和构建。

### Task 3: 签名后端默认规则

**Files:**
- Modify: `backend/db/__init__.py`
- Modify: `backend/models/__init__.py`
- Modify: `backend/schemas.py`
- Modify: `backend/routes/signatures.py`
- Create: `backend/tests/test_signature_defaults.py`

**Interfaces:**
- Produces: `Signature.is_reply_default: int`
- Produces: `/api/signatures` 请求与响应字段 `is_reply_default`。

- [x] 编写失败测试，覆盖同一用户同一邮箱范围内新邮件默认和回复默认分别唯一、不同用户互不影响、账号归属校验。
- [x] 为 `signatures` 表新增可重复执行的 `is_reply_default` 列迁移。
- [x] 更新创建和修改事务，只清除相同用户、相同 `account_id` 范围内对应类型的默认项。
- [x] 路由校验非空 `account_id` 确属当前用户，并返回完整字段。
- [x] 运行签名测试及完整后端测试。

### Task 4: 从指定邮箱导入联系人

**Files:**
- Modify: `backend/db/__init__.py`
- Modify: `backend/schemas.py`
- Modify: `backend/routes/contacts.py`
- Modify: `frontend/src/composables/useContacts.ts`
- Modify: `frontend/src/views/ContactList.vue`
- Create: `backend/tests/test_contact_candidates.py`
- Create: `frontend/tests/contact-import-contract.test.mjs`

**Interfaces:**
- Produces: `get_contact_candidates(user_uid: str, account_id: str, search: str = "", limit: int = 500) -> list[dict]`
- Produces: `GET /api/contacts/candidates?account_id=...&search=...`
- Produces: `POST /api/contacts/import` with `{account_id, contacts:[{name,email}]}`。

- [x] 编写失败测试，覆盖邮箱归属、地址解析、本人过滤、已有联系人过滤、往来次数聚合和批量导入去重。
- [x] 从当前用户指定账号的 `cached_messages` 解析 From/To/Cc，按邮箱聚合名称、收发次数和最近日期。
- [x] 批量导入只处理候选邮箱，已存在项计入 skipped，不覆盖已有联系人资料。
- [x] 联系人页新增“从邮件导入”入口、邮箱选择、搜索、全选、候选列表、空状态、导入进度和结果反馈。
- [x] 运行联系人后端测试、前端契约测试和构建。

### Task 5: 文档、版本与完整交付验证

**Files:**
- Verify: `VERSION`
- Verify: `package.json`
- Verify: `frontend/package.json`
- Verify: `docker-compose.yml`
- Modify: `README.md`

- [x] 确认 `VERSION`、根目录 `package.json`、`frontend/package.json` 与镜像版本均为 `0.0.34`。
- [x] README 记录邮件横向滚动、按邮箱签名默认和联系人候选导入的行为边界。
- [x] 执行后端完整测试、前端完整测试与构建、`bash -n scripts/docker-entrypoint.sh`、Docker Compose 结构检查、`git diff --check`。
- [x] 构建 `benxianyu/flymail:0.0.34`，用独立临时数据目录启动容器并验证健康、MySQL 8.0、`/data/mysql/`、数据库读写、重启持久化、日志脱敏、镜像元数据和安全关闭。
- [x] 检查当前 `flymail` 容器挂载为 `/Docker/flymail/data:/data` 后重建，验证健康接口版本与数据计数。
- [x] 仅提交本任务文件，提交标题使用 `✨ 完善邮件阅读签名与联系人导入`，推送 `origin/main`。
