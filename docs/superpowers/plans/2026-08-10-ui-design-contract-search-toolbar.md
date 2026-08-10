# UI Design Contract And Stable Search Toolbar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 FlyMail 根目录 `DESIGN.md` 设计契约，并修复高级筛选条件导致邮件工具栏高度变化的问题。

**Architecture:** 用 `DESIGN.md` 统一记录跨页面长期 UI 不变量，`AGENTS.md` 只保留强制入口；用静态设计契约测试防止规则再次丢失。搜索组件删除正常文档流中的筛选 chips，仅保留固定尺寸的筛选数量，桌面工具栏禁止隐式换行；移动端继续使用现有显式响应式布局和浮层。

**Tech Stack:** Vue 3, TypeScript, scoped CSS, FlyMail semantic design tokens, Node test runner, Docker.

## Global Constraints

- 只在 `/home/chatgpt/flymail` 的 `main` 分支修改。
- 不修改、删除或迁移 `/Docker/flymail/data`。
- 不修改后端 API、认证、数据库结构、同步逻辑或环境变量。
- 不新增或升级生产依赖。
- `DESIGN.md` 是 FlyMail 用户界面设计与布局约束的事实来源；`AGENTS.md` 只指向该文件，不复制详细规则。
- 桌面工具栏不得由搜索词、筛选数量、邮箱地址、错误文本等动态内容改变高度。
- 多筛选状态只在固定控制中显示数量摘要，完整条件通过高级筛选浮层编辑。
- 桌面动态内容必须可收缩；移动端只在既有断点明确切换布局，不使用隐式换行修补桌面布局。
- 版本从 `0.0.43` 升级到 `0.0.44`。
- 只构建本地 `benxianyu/flymail:0.0.44`，默认不上传 Docker Hub。

---

### Task 1: 建立设计治理契约

**Files:**
- Create: `DESIGN.md`
- Modify: `AGENTS.md`
- Create: `frontend/tests/design-contract.test.mjs`
- Reference: `docs/superpowers/specs/2026-08-10-ui-design-governance-search-toolbar-design.md`

**Interfaces:**
- Consumes: 现有 `tokens.css`、PageFrame/PageToolbar/UiScrollRegion 设计系统和历史 UI 规范。
- Produces: 根目录长期 UI 契约，以及 AI 修改 UI 前必须读取它的入口规则。

- [ ] **Step 1: 先写失败的设计契约测试**

创建 `frontend/tests/design-contract.test.mjs`，从仓库根目录读取 `DESIGN.md` 和 `AGENTS.md`，要求：

```js
assert.match(design, /状态可以改变内容[^\n]*不能无意改变工作区骨架/);
assert.match(design, /min-width:\s*0/);
assert.match(design, /minmax\(0,\s*1fr\)/);
assert.match(design, /工具栏[\s\S]*筛选 N/);
assert.match(design, /浮层[\s\S]*不参与主布局/);
assert.match(design, /390[×x]844/);
assert.match(design, /200%/);
assert.match(design, /tokens\.css/);
assert.match(agents, /前端 UI、布局、组件或视觉修改前[^\n]*DESIGN\.md/);
assert.match(agents, /DESIGN\.md[^\n]*事实来源/);
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd frontend && node --test tests/design-contract.test.mjs`

Expected: FAIL，因为根目录 `DESIGN.md` 尚不存在，`AGENTS.md` 也尚未包含入口规则。

- [ ] **Step 3: 写最小完整 `DESIGN.md` 和 AGENTS 入口**

`DESIGN.md` 至少包含：设计权威、稳定骨架、动态内容/overflow 策略、toolbar、overlay、scroll ownership、tokens/样式职责、响应式、无障碍、极端内容验证、AI UI 修改流程、官方参考资料。`AGENTS.md` 只增加：

```markdown
- 进行任何前端 UI、布局、组件或视觉修改前，必须先读取根目录 `DESIGN.md`。
- `DESIGN.md` 是 FlyMail 用户界面设计与布局约束的事实来源。
```

- [ ] **Step 4: 运行设计契约测试确认 GREEN**

Run: `cd frontend && node --test tests/design-contract.test.mjs`

Expected: PASS。

---

### Task 2: 用测试锁定搜索工具栏稳定性

**Files:**
- Create: `frontend/tests/mail-search-layout.test.mjs`
- Modify: `frontend/src/components/mail/MailSearchBar.vue`
- Modify: `frontend/src/views/MailList.vue`

**Interfaces:**
- Keeps: `MailSearchBar` 的 `v-model`、`search`、`clear` 事件和所有搜索字段语义。
- Produces: 固定高度的桌面搜索控制行；筛选按钮继续显示条件数量并打开现有高级筛选浮层。

- [ ] **Step 1: 写失败的布局回归测试**

创建 `frontend/tests/mail-search-layout.test.mjs`，断言：

```js
assert.doesNotMatch(searchSource, /class="mail-search-summary"/);
assert.doesNotMatch(searchSource, /\.mail-search-summary\s*\{/);
assert.match(searchSource, /filter-count/);
assert.match(searchSource, /\.mail-search-bar\s*\{[^}]*min-width:\s*0;/s);
assert.match(searchSource, /\.mail-search-input-wrap\s*\{[^}]*min-width:\s*0;/s);
assert.match(searchSource, /\.mail-search-action,[\s\S]*flex-shrink:\s*0;/s);
assert.match(mailListSource, /\.toolbar-right\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*justify-content:\s*flex-end;/s);
assert.match(mailListSource, /@media \(max-width:\s*768px\)[\s\S]*\.list-toolbar\s*\{[^}]*flex-wrap:\s*wrap;/s);
```

同时要求筛选按钮具有基于 `activeLabels.length` 的可访问名称，并继续保留 `aria-expanded`。

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd frontend && node --test tests/mail-search-layout.test.mjs`

Expected: FAIL，因为当前存在正常流 `.mail-search-summary`、搜索根有内容型最小宽度、输入包裹层最小 220px，桌面 `.toolbar-right` 允许 wrap。

- [ ] **Step 3: 做最小布局修复**

`MailSearchBar.vue`：

- 删除 `.mail-search-summary` markup 和对应 `.search-chip` / summary CSS。
- 保留 `activeLabels` 只用于数量、active 状态和辅助名称。
- 筛选按钮添加 `:aria-label="activeLabels.length ? \`筛选邮件，已应用 ${activeLabels.length} 个条件\` : '筛选邮件'"`。
- `.mail-search-bar` 改为可收缩的稳定单行容器，例如 `width: min(560px, 42vw); max-width: 100%; min-width: 0;`。
- `.mail-search-input-wrap` 改为 `min-width: 0`。
- 搜索/筛选/清除按钮设 `flex-shrink: 0`。
- 高级筛选面板继续 `position: absolute`，手机继续 `position: fixed`。

`MailList.vue`：

- 桌面 `.toolbar-right` 改为 `flex-wrap: nowrap`。
- 保留现有 `@media (max-width: 768px)` 显式 `.list-toolbar { flex-wrap: wrap; }` 和 `.toolbar-right { width: 100%; flex-wrap: nowrap; }`。

- [ ] **Step 4: 运行聚焦测试确认 GREEN**

Run:

```bash
cd frontend
node --test tests/mail-search-layout.test.mjs tests/mail-search.test.ts tests/ui-layout.test.mjs tests/page-templates.test.mjs
```

Expected: all PASS。

---

### Task 3: 文档、版本和完整前端/后端验证

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: release `0.0.44` and README-visible design governance/search behavior.

- [ ] **Step 1: 同步用户文档与版本**

README 的“邮件搜索与会话”说明高级筛选使用固定 `筛选 N` 摘要、详细条件位于浮层，不再把工具栏撑高；“文档”章节增加根目录 `DESIGN.md`。版本文件统一为 `0.0.44`。

- [ ] **Step 2: 检查版本一致**

Run:

```bash
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
grep -n 'benxianyu/flymail:0.0.44' docker-compose.yml README.md
```

Expected: 全部为 `0.0.44`。

- [ ] **Step 3: 完整验证**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm install && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
# 使用 .env.example 的非敏感临时副本进行 compose config
git diff --check
git status --short
git diff
```

Expected: 后端、前端、类型检查/构建、Shell、Compose、diff checks 全部通过。

---

### Task 4: 浏览器、Docker、生产替换和 Git 交付

**Files:**
- Update completion checkboxes in this plan only if needed.

**Interfaces:**
- Produces: local image `benxianyu/flymail:0.0.44` and healthy production container `flymail` using existing `/Docker/flymail/data:/data`.

- [ ] **Step 1: 浏览器布局回归**

在可用浏览器自动化环境中检查生产构建：

- 桌面 1440×900：记录工具栏无筛选时高度，应用至少 5 个高级条件后高度差必须为 0（允许浏览器小数像素取整差不超过 1px）。
- 390×844：`document.documentElement.scrollWidth <= clientWidth`，搜索控制和筛选面板可用。
- 200% 页面缩放或等价增大字体检查：筛选入口仍可操作，完整条件仍在浮层可读。

若真实浏览器发现新问题，必须先补失败测试，再做最小修复。

- [ ] **Step 2: 构建并验证独立临时容器**

Build: `docker build -t benxianyu/flymail:0.0.44 .`

使用独立临时容器名和临时 `/data`，不得挂载 `/Docker/flymail/data`。至少验证：healthy、`/api/health` 返回 `0.0.44`、MySQL 8.0 和 `/data/mysql/`、`/data/flymail`、数据库读写、重启持久化、日志没有测试密码/密钥、镜像元数据没有密码/密钥、停止时 MySQL 安全关闭。测试数据库密码包含引号、反斜杠、`@`、`:`、`/` 或 `%`。

- [ ] **Step 3: 无损替换当前容器**

读取当前 `flymail` 的镜像、端口、restart policy 和挂载，保留旧容器作临时回滚；用 `benxianyu/flymail:0.0.44` 复用原环境和 `/Docker/flymail/data:/data`。只有新容器 running/healthy、健康接口版本正确、MySQL 8.0/datadir 正确、邮件缓存数据未丢失后才删除回滚容器。

- [ ] **Step 4: 最终复验和提交推送**

Fresh run 后端全量、前端全量/构建、Shell、Compose、`git diff --check`、Docker build、生产健康/版本检查。只暂存本任务文件，检查 `.env`、日志、数据目录和明显密钥没有进入 staged diff。

Commit title:

```text
🎨 固化界面设计规范并稳定搜索筛选工具栏
```

Push `origin main` without force. Verify local branch clean and equal to `origin/main`. Do not upload Docker Hub.
