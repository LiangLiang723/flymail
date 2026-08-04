# FlyMail Apple Mail 风格整站 UI 重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变后端接口和数据行为的前提下，将 FlyMail 全部一级页面统一为系统蓝、紧凑高效的 Apple Mail 风格，并把桌面邮件页改为真正同时可见的三栏工作台。

**Architecture:** 以现有语义设计系统为基础，先用静态契约测试锁定目标，再调整令牌、壳层和跨页面样式；只有邮件三栏和设置分类导航需要结构性模板改动。其余页面通过公共组件和 `page-system.css` 收口，避免重写业务逻辑或引入依赖。

**Tech Stack:** Vue 3、TypeScript、Pinia、Vite、Node.js `node:test`、CSS 自定义属性、FastAPI、Docker、MySQL 8.0。

## Global Constraints

- 主色：浅色主题 `#0A84FF`，深色主题使用高对比系统蓝。
- 桌面邮件结构：文件夹、邮件列表、邮件详情三栏同时可见。
- 960px 及以下保持单主视图移动流程；961–1180px 使用列表与详情双栏；1181px 以上使用三栏。
- 不修改后端接口、认证、权限、邮件同步、缓存、附件、通知和数据库行为。
- 不迁移或删除 `/Docker/flymail/data`，不改变容器内 `/data` 持久化结构。
- 不新增或升级生产依赖。
- 保持浅色、深色、减少动态、减少透明和高对比偏好。
- 所有主要交互控件保持不低于 44px 的命中区域。
- 仅提交本次任务文件，保留已有 `.benchmarks` 暂存内容不变。

---

### Task 1: 用失败测试锁定整站设计契约

**Files:**
- Create: `frontend/tests/apple-mail-full-ui.test.mjs`
- Read: `frontend/tests/design-system.test.mjs`
- Read: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: 现有源码文本和 Node.js `node:test`。
- Produces: 系统蓝、三栏邮件、设置分类导航、紧凑全局尺寸、移动断点和 README 描述的静态契约。

- [ ] **Step 1: 创建失败测试**

测试必须断言：

```js
assert.match(tokens, /--ui-accent:\s*#0a84ff/i);
assert.match(tokens, /--mail-folder-pane:\s*236px/);
assert.match(tokens, /--mail-list-pane:\s*390px/);
assert.match(mail, /class="mail-list"/);
assert.match(mail, /class="mail-detail mail-detail-empty"/);
assert.doesNotMatch(mail, /<div v-if="!selectedMessage" class="mail-list">/);
assert.doesNotMatch(mail, /<div v-else class="mail-detail"/);
assert.match(settings, /class="settings-layout"/);
assert.match(settings, /class="settings-nav"/);
assert.match(styles, /@media \(max-width:\s*1180px\)/);
assert.match(styles, /@media \(max-width:\s*960px\)/);
```

- [ ] **Step 2: 运行聚焦测试确认失败**

Run: `cd frontend && node --test tests/apple-mail-full-ui.test.mjs`

Expected: FAIL，至少因 `#0A84FF`、真正三栏结构和设置分类导航不存在而失败。

### Task 2: 重构语义令牌、基础控件和应用壳层

**Files:**
- Modify: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/styles/base.css`
- Modify: `frontend/src/styles/components.css`
- Modify: `frontend/src/styles/app-shell.css`
- Modify: `frontend/src/styles/layout-system.css`
- Modify: `frontend/src/components/app/AppSidebar.vue`（仅在语义或可访问标记需要时）
- Test: `frontend/tests/apple-mail-full-ui.test.mjs`
- Test: `frontend/tests/design-system.test.mjs`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: 现有 CSS 变量和公共组件类名。
- Produces: 系统蓝视觉令牌、紧凑页面尺寸、统一焦点/浮层/导航状态，以及邮件栏宽变量 `--mail-folder-pane`、`--mail-list-pane`。

- [ ] **Step 1: 更新测试期望中的旧设计令牌**

把旧紫色、24px 页面边距等断言更新为新规范，同时保留语义令牌集中管理和不在共享 CSS 写固定调色板的约束。

- [ ] **Step 2: 修改 `tokens.css`**

至少定义：

```css
--ui-accent: #0a84ff;
--ui-accent-hover: #0077e6;
--ui-accent-soft: rgba(10, 132, 255, 0.14);
--page-gutter: 18px;
--toolbar-height: 44px;
--list-row-height: 52px;
--mail-folder-pane: 236px;
--mail-list-pane: 390px;
```

深色主题使用高对比蓝色并同步 `--ui-focus-ring`、选中填充和兼容别名。

- [ ] **Step 3: 收紧公共组件和布局**

统一按钮、输入框、分段控件、卡片、工具栏、页头、Toast、确认框、菜单和抽屉的圆角、边框、阴影、字体和过渡。工作台页面减少外层留白，文档页面保留左对齐表单宽度。

- [ ] **Step 4: 运行设计系统与壳层测试**

Run: `cd frontend && node --test tests/apple-mail-full-ui.test.mjs tests/design-system.test.mjs tests/ui-layout.test.mjs`

Expected: 系统蓝和尺寸部分通过；邮件结构和设置导航断言仍失败。

### Task 3: 将邮件页改为真正响应式三栏

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/styles/page-system.css`
- Test: `frontend/tests/apple-mail-full-ui.test.mjs`
- Test: `frontend/tests/product-ui-redesign.test.mjs`
- Test: `frontend/tests/ui-layout.test.mjs`

**Interfaces:**
- Consumes: `selectedMessage`、`isMobile`、现有邮件列表和详情处理函数。
- Produces: 桌面同时渲染列表与详情，移动端仍保持单主视图；空详情类名 `mail-detail-empty`。

- [ ] **Step 1: 保持邮件列表在桌面持续渲染**

把列表容器条件改为：

```vue
<div v-show="!isMobile || !selectedMessage" class="mail-list">
```

列表内容和业务事件不变。

- [ ] **Step 2: 让详情区域在桌面始终存在**

把详情改为：

```vue
<div
  v-if="selectedMessage"
  class="mail-detail"
  ...
>
  <!-- 保留现有详情 -->
</div>
<div v-else-if="!isMobile" class="mail-detail mail-detail-empty">
  <UiEmptyState title="选择一封邮件" description="邮件内容会显示在这里。" />
</div>
```

移动端无选中邮件时不渲染空详情。

- [ ] **Step 3: 移除桌面详情中的返回按钮视觉占位**

保留移动端返回操作，在桌面通过 CSS 隐藏 `.btn-back`，不删除 `backToList()` 和滑动返回逻辑。

- [ ] **Step 4: 重写工作台网格规则**

在 `page-system.css` 中建立：

```css
.mail-view .mail-shell {
  grid-template-columns: var(--mail-folder-pane) minmax(320px, var(--mail-list-pane)) minmax(0, 1fr);
}

@media (max-width: 1180px) and (min-width: 961px) {
  .mail-view .mail-shell {
    grid-template-columns: minmax(320px, var(--mail-list-pane)) minmax(0, 1fr);
  }
  .mail-view .folder-sidebar { display: none; }
}

@media (max-width: 960px) {
  .mail-view .mail-shell { grid-template-columns: minmax(0, 1fr); }
}
```

同时统一三栏边界、独立滚动、列表宽度、未读点和详情空状态。

- [ ] **Step 5: 移除冗余已读/未读文字徽标**

从邮件行中移除 `mail-status-tag`，保留图标、蓝点、字重和筛选计数；不改变已读状态逻辑。

- [ ] **Step 6: 运行聚焦测试和生产构建**

Run: `cd frontend && node --test tests/apple-mail-full-ui.test.mjs tests/product-ui-redesign.test.mjs tests/ui-layout.test.mjs`

Expected: 邮件三栏断言通过；设置导航断言仍失败。

Run: `cd frontend && npm run build`

Expected: Vue 类型检查和 Vite 构建通过。

### Task 4: 将设置页改为分类导航 + 连续内容布局

**Files:**
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/styles/page-system.css`
- Test: `frontend/tests/apple-mail-full-ui.test.mjs`
- Test: `frontend/tests/unified-inbox-toggle.test.mjs`
- Test: `frontend/tests/product-ui-redesign.test.mjs`

**Interfaces:**
- Consumes: 现有设置卡片、保存函数和折叠状态。
- Produces: `settings-layout`、`settings-nav`、锚点 section `appearance`、`storage`、`network`、`providers`、`guides`。

- [ ] **Step 1: 增加设置布局和分类导航**

模板结构：

```vue
<div class="settings-layout">
  <nav class="settings-nav" aria-label="设置分类">
    <a href="#settings-appearance">外观与收件箱</a>
    <a href="#settings-storage">存储与清理</a>
    <a href="#settings-network">网络</a>
    <a href="#settings-providers">邮箱服务商</a>
    <a href="#settings-guides">配置教程</a>
  </nav>
  <div class="document-column settings-document settings-content">
    <!-- 现有内容按 section 包裹 -->
  </div>
</div>
```

- [ ] **Step 2: 按主题包裹现有设置内容**

使用语义 section 和 `scroll-margin-top`，不改 `v-model`、API、保存、开关、教程和图片预览逻辑。

- [ ] **Step 3: 清理固定颜色的 Microsoft 警告提示**

把内联 `rgba(...)`、`#FF9F0A` 改为语义类和 `--ui-warning` / `--ui-warning-soft`。

- [ ] **Step 4: 添加桌面与移动布局**

桌面为 184px 分类导航 + 内容列；移动端导航变为横向可滚动分段，内容单列，避免横向页面溢出。

- [ ] **Step 5: 运行设置相关测试**

Run: `cd frontend && node --test tests/apple-mail-full-ui.test.mjs tests/unified-inbox-toggle.test.mjs tests/product-ui-redesign.test.mjs`

Expected: 全部通过。

### Task 5: 收口所有一级页面和全局浮层

**Files:**
- Modify: `frontend/src/styles/page-system.css`
- Modify: `frontend/src/styles/components.css`
- Modify: `frontend/src/styles/app-shell.css`
- Modify when needed: `frontend/src/views/LoginView.vue`
- Modify when needed: `frontend/src/views/ComposeEmail.vue`
- Modify when needed: `frontend/src/views/ContactList.vue`
- Modify when needed: `frontend/src/views/UnifiedInbox.vue`
- Modify when needed: `frontend/src/views/AccountList.vue`
- Modify when needed: `frontend/src/views/HistorySync.vue`
- Modify when needed: `frontend/src/views/Backup.vue`
- Modify when needed: `frontend/src/views/Profile.vue`
- Modify when needed: `frontend/src/views/NotificationSettings.vue`
- Modify when needed: `frontend/src/views/UserManagement.vue`
- Modify when needed: `frontend/src/views/About.vue`
- Test: all `frontend/tests/*`

**Interfaces:**
- Consumes: 现有页面根类、公共组件和页面模板。
- Produces: 统一 Apple Mail 风格的列表、卡片、表单、编辑器、管理控制台、空状态、登录和浮层。

- [ ] **Step 1: 统一工作台页面**

邮件、写信、联系人、备份使用连续面板和细分隔，不使用多层阴影卡片。保持各自滚动所有权和业务事件。

- [ ] **Step 2: 统一管理页面**

聚合收件箱、账号、同步和用户管理使用紧凑工具栏、响应式网格/列表和稳定状态对齐；危险操作保持次级视觉。

- [ ] **Step 3: 统一文档页面和登录页**

个人资料、第三方通知、关于和登录页使用同一系统蓝、表面、输入框、焦点和排版层级。

- [ ] **Step 4: 检查页面 scoped CSS**

Run: `rg -n '#[0-9a-fA-F]{3,8}|rgba?\(' frontend/src/views frontend/src/components`

仅允许品牌 SVG、邮件签名模板、编辑器颜色选择等内容语义所需固定颜色；新增主题样式必须使用 token。

- [ ] **Step 5: 运行前端全量测试与构建**

Run: `cd frontend && npm test`

Expected: 全部测试通过。

Run: `cd frontend && npm run build`

Expected: Vue 类型检查和 Vite 构建通过；已有 chunk 大小警告可记录但不能有构建错误。

### Task 6: 同步版本和文档

**Files:**
- Modify: `VERSION`
- Modify via script: `package.json`
- Modify via script: `frontend/package.json`
- Modify via script: `docker-compose.yml`
- Modify: `README.md`
- Test: version consistency commands

**Interfaces:**
- Consumes: `VERSION` 和 `scripts/sync-version.js`。
- Produces: 新补丁版本、同步镜像标签和整站 UI 行为说明。

- [ ] **Step 1: 将版本提升到下一个补丁版本**

把 `VERSION` 从 `0.0.26` 改为 `0.0.27`。

- [ ] **Step 2: 同步版本**

Run: `npm run sync-version`

Expected: 根包、前端包、Compose 和 README 镜像标签全部为 `0.0.27`。

- [ ] **Step 3: 更新 README**

明确说明系统蓝、紧凑密度、桌面真正三栏、961–1180px 双栏和移动端单主视图；不声明未经浏览器验证的像素级结论。

- [ ] **Step 4: 验证版本一致性**

Run:

```bash
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
```

Expected: 三处均为 `0.0.27`。

### Task 7: 完整验证、镜像、临时容器和正式部署

**Files:**
- Verify only: repository and Docker runtime

**Interfaces:**
- Consumes: 完成后的源码和现有容器配置。
- Produces: 可验证镜像 `benxianyu/flymail:0.0.27`、健康临时容器和安全替换后的 `flymail`。

- [ ] **Step 1: 运行完整代码检查**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
printf 'services:\n  flymail-app:\n    env_file: !reset []\n' | docker compose -f docker-compose.yml -f - config --quiet
git diff --check
git status --short
git diff
```

Expected: 全部命令通过；只存在本次改动和用户原有 `.benchmarks` 暂存文件。

- [ ] **Step 2: 构建镜像**

Run: `docker build -t benxianyu/flymail:$(cat VERSION) .`

Expected: 成功构建本地镜像，不上传 Docker Hub。

- [ ] **Step 3: 启动独立临时容器**

使用独立临时目录、临时容器名、随机宿主机端口和测试密钥，不挂载 `/Docker/flymail/data`。

验证：

- 容器达到 `healthy`。
- `/api/health` 返回 `0.0.27`。
- MySQL 为 8.0，datadir 为 `/data/mysql/`。
- `/data/flymail` 创建成功。
- 数据库可读写，重启后测试数据仍存在。
- 日志无密码、密钥或完整数据库连接地址。
- 镜像元数据无真实密码和密钥。
- 停止容器时 MySQL 安全关闭。

- [ ] **Step 4: 检查前端产物契约**

确认服务静态资源包含 `#0a84ff`、`--mail-folder-pane`、`settings-layout`、`mail-detail-empty` 和 1180/960 响应式规则。

- [ ] **Step 5: 安全替换正式容器**

读取现有 `flymail` 的镜像、环境、端口、网络、重启策略和挂载，不打印敏感值。保留旧容器作为回滚，启动新容器并等待健康后再删除旧容器。

验证：

- `/Docker/flymail/data:/data` 挂载不变。
- 健康接口返回 `0.0.27`。
- 用户数据仍可读取。
- 重启后健康和数据仍正常。

- [ ] **Step 6: 最终 Git 检查、提交和推送**

Run:

```bash
git status --short
git diff --check
git diff
git add <仅本次任务文件>
git diff --staged
git commit -m "🎨 重构整站 Apple Mail 风格三栏界面"
git push origin main
```

Expected: 推送到 `origin/main` 成功；`.benchmarks` 不进入提交；不上传 Docker Hub。
