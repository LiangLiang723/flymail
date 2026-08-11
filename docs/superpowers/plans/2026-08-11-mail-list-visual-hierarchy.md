# FlyMail 邮件列表视觉层级精修 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留搜索当前桌面行、邮件业务行为和移动端结构的前提下，去掉已读视觉噪音，强化未读/文件夹层级并弱化邮箱账号上下文。

**Architecture:** 只修改现有 `MailList.vue`、`AppSidebar.vue` 和 `app-shell.css` 的模板语义与局部样式，不引入新组件或依赖。通过静态前端契约测试锁定“已读不显示徽标、账号仅为上下文、文件夹强选中、搜索仍在右侧单行”等不变量；发布时仅升级补丁版本并同步 README。

**Tech Stack:** Vue 3、TypeScript、scoped CSS、Node.js `node:test`、Docker/MySQL 8.0。

## Global Constraints

- 搜索继续留在当前桌面工具栏右侧这一行，不额外拆出第三行。
- `邮件 / 会话` 是一级显示模式；`全部 / 未读 / 已读 / 附件 / 星标` 是二级快速筛选。
- 已读邮件/已读会话不渲染“已读”徽标；未读状态仍必须有文字提示，不能只靠颜色。
- 当前邮箱账号只使用轻量 `is-context`；当前文件夹继续使用强 `active`。
- 不改变后端接口、数据库、环境变量、搜索逻辑、同步、分页、会话、验证码复制和移动端统一侧栏。
- 不写入或迁移 `/Docker/flymail/data`；Docker 验证必须使用独立临时目录。
- 不新增或升级生产依赖。

---

### Task 1: 锁定邮件列表和侧栏视觉语义

**Files:**
- Create: `frontend/tests/mail-list-visual-hierarchy.test.mjs`
- Read: `frontend/src/views/MailList.vue`
- Read: `frontend/src/components/app/AppSidebar.vue`
- Read: `frontend/src/styles/app-shell.css`

**Interfaces:**
- Consumes: `MailList.vue` 的 `.list-count`、`.toolbar-right`、`.mail-status-tag`、`.mail-item.unread` 和 `.mail-date`；`AppSidebar.vue` 的账号/文件夹 class binding。
- Produces: 静态契约测试，后续模板和 CSS 必须满足这些规则。

- [ ] **Step 1: 新增失败测试**

创建 `frontend/tests/mail-list-visual-hierarchy.test.mjs`，使用 `readFile` 读取源码并至少包含以下断言：

```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file) => readFile(path.join(frontendRoot, file), 'utf8');

test('mail list keeps search on the desktop toolbar and removes duplicate folder counts', async () => {
  const mail = await read('src/views/MailList.vue');
  assert.match(mail, /<span v-else class="list-count">\s*\{\{ mailStore\.currentFolderName \}\}\s*<\/span>/s);
  assert.match(mail, /<div class="toolbar-right">[\s\S]*<MailSearchBar/s);
  assert.match(mail, /\.toolbar-right\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*justify-content:\s*flex-end;/s);
});

test('read rows are quiet while unread rows keep explicit status text', async () => {
  const mail = await read('src/views/MailList.vue');
  assert.match(mail, /<UiBadge[^>]*v-if="[^\"]*unread[^\"]*"[^>]*class="mail-status-tag"/s);
  assert.doesNotMatch(mail, /msg\.is_read \? '已读' : '未读'/);
  assert.doesNotMatch(mail, /\? `未读 \$\{msg\.unread_count\}` : '已读'/);
  assert.match(mail, /\.mail-item\.unread::before\s*\{[^}]*background:\s*var\(--color-accent\);/s);
  assert.match(mail, /\.mail-item\.unread \.mail-from\s*\{[^}]*font-weight:\s*var\(--font-semibold\);/s);
  assert.match(mail, /\.mail-date\s*\{[^}]*width:\s*58px;/s);
});

test('account is context while folder remains the strong selected navigation item', async () => {
  const sidebar = await read('src/components/app/AppSidebar.vue');
  const css = await read('src/styles/app-shell.css');
  assert.doesNotMatch(sidebar, /active:\s*currentView === 'mail' && mailStore\.currentAccountId === account\.id/);
  assert.match(sidebar, /'is-context': mailStore\.currentAccountId === account\.id/);
  assert.match(sidebar, /active:\s*currentView === 'mail' && mailStore\.currentFolder === folder\.path/);
  assert.match(css, /\.sidebar-account-item\.is-context\s*\{[^}]*background:\s*var\(--ui-fill-muted\);/s);
  assert.doesNotMatch(css, /\.sidebar-account-item\.active \.sidebar-row-icon::before/);
});
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
cd frontend
node --test tests/mail-list-visual-hierarchy.test.mjs
```

Expected: FAIL，至少因为当前 `.list-count` 仍包含未读/全部计数、账号仍绑定 `active`、已读仍渲染徽标。

---

### Task 2: 最小实现邮件列表与侧栏层级调整

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Modify: `frontend/src/components/app/AppSidebar.vue`
- Modify: `frontend/src/styles/app-shell.css`
- Test: `frontend/tests/mail-list-visual-hierarchy.test.mjs`

**Interfaces:**
- Consumes: `mailStore.currentFolderName`、`listMode`、`msg.is_read`、`msg.unread_count`、`currentView`、`mailStore.currentAccountId/currentFolder`。
- Produces: 已读默认降噪、未读明确提示、账号轻上下文、文件夹强选中，同时保持搜索/刷新右侧单行。

- [ ] **Step 1: 精简文件夹上下文摘要**

将桌面 `.list-count` 改成只渲染：

```vue
<span v-else class="list-count">{{ mailStore.currentFolderName }}</span>
```

不改移动端 `.folder-picker`。

- [ ] **Step 2: 只为未读状态渲染徽标**

将行末状态徽标改为：

```vue
<UiBadge
  v-if="!noReadStateFolder && (listMode === 'conversations' ? (msg.unread_count || 0) > 0 : !msg.is_read)"
  tone="accent"
  class="mail-status-tag"
>
  {{ listMode === 'conversations' ? `未读 ${msg.unread_count}` : '未读' }}
</UiBadge>
```

已读邮件和已读会话不创建徽标节点；未读继续有文字状态。

- [ ] **Step 3: 弱化邮件行表格感**

在最终 `2026 邮件工作区视觉重构` 样式覆盖区完成以下最小调整：

```css
.mail-item,
.mail-item:not(.unread) {
  border-bottom-color: color-mix(in srgb, var(--border-color) 58%, transparent);
}

.mail-item:not(.unread) .mail-from {
  color: var(--text-secondary);
  font-weight: var(--font-normal);
}

.mail-item:not(.unread) .mail-subject {
  color: var(--text-tertiary);
}

.mail-status-tag {
  width: auto;
  min-width: 40px;
  padding-inline: 6px;
}
```

保留 `.mail-item.unread::before`、未读字重、hover/selected、验证码、附件和日期固定列。

- [ ] **Step 4: 将邮箱账号从强 active 改为轻 context**

在 `AppSidebar.vue` 中删除账号的 `active:` binding，只保留：

```vue
:class="{
  'is-context': mailStore.currentAccountId === account.id,
}"
```

文件夹 `active` binding 保持不变。

在 `app-shell.css` 中：

```css
.sidebar-mail-entry.active,
.sidebar-folder-item.active {
  background: var(--ui-fill-selected);
  color: var(--ui-accent);
}

.sidebar-mail-entry.active .sidebar-row-icon::before,
.sidebar-folder-item.active .sidebar-row-icon::before {
  /* existing accent rail */
}

.sidebar-account-item.is-context {
  background: var(--ui-fill-muted);
  color: var(--ui-text-1);
}
```

账号 context 不显示左侧强调条。

- [ ] **Step 5: 运行聚焦测试确认 GREEN**

Run:

```bash
cd frontend
node --test tests/mail-list-visual-hierarchy.test.mjs tests/mail-sidebar-refactor.test.mjs tests/mail-search-layout.test.mjs tests/mail-verification-code.test.mjs tests/ui-layout.test.mjs
```

Expected: PASS。

- [ ] **Step 6: 运行完整前端测试与构建**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: 全部 PASS，`vue-tsc` 与 Vite production build 成功。

---

### Task 3: 同步 README 与补丁版本

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via `npm run sync-version`: `package.json`
- Modify via `npm run sync-version`: `frontend/package.json`
- Modify via `npm run sync-version`: `docker-compose.yml`

**Interfaces:**
- Consumes: 当前版本 `0.0.50`。
- Produces: 新版本 `0.0.51`，所有版本来源一致。

- [ ] **Step 1: 更新 README 用户可见说明**

在设计系统/永久侧栏相关能力附近补充一句：邮件列表已读邮件默认不显示重复状态徽标，未读通过状态点、字重和未读徽标突出；当前邮箱账号使用轻上下文状态，当前文件夹承担强选中状态。搜索保持桌面工具栏当前行。

- [ ] **Step 2: 将 VERSION 改为 0.0.51**

`VERSION` 内容必须精确为：

```text
0.0.51
```

- [ ] **Step 3: 同步版本**

Run:

```bash
npm run sync-version
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
```

Expected: 三处均为 `0.0.51`，`docker-compose.yml` 与 README 镜像标签为 `benxianyu/flymail:0.0.51`。

---

### Task 4: 完整回归、Docker 与临时容器验证

**Files:**
- Verify only: backend/frontend/scripts/Docker deployment

**Interfaces:**
- Consumes: 完整工作区与镜像 `benxianyu/flymail:0.0.51`。
- Produces: 可交付的构建、真实容器与持久化验证结果。

- [ ] **Step 1: 后端、Shell、Compose 和 diff 检查**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose config
git diff --check
git status --short
git diff
```

Expected: 后端全绿、Shell/Compose 无错误、diff check 无错误。

- [ ] **Step 2: 构建本地 Docker 镜像**

Run:

```bash
docker build -t benxianyu/flymail:0.0.51 .
```

Expected: build success。

- [ ] **Step 3: 使用独立临时数据验证真实容器**

创建宿主机临时目录（位于工作区外且不是 `/Docker/flymail/data`），以临时容器名运行 `benxianyu/flymail:0.0.51`，环境变量使用临时随机管理员密码、至少 16 位 session secret 和包含特殊字符的 MySQL 密码。

至少验证：

```text
容器达到 healthy
GET /api/health 返回 version=0.0.51
mysql --version 为 8.0.x
SELECT @@datadir 返回 /data/mysql/
/data/flymail 已创建
创建测试表/行后重启容器仍可读
日志不出现明文数据库密码或 session secret
镜像 Config.Env 不包含测试密码/session secret
SIGTERM 停止后 MySQL 日志显示正常 shutdown
```

验证结束删除临时容器与临时目录，不接触 `/Docker/flymail/data`。

- [ ] **Step 4: 浏览器视觉验证**

使用可用的本地浏览器/Playwright 检查 1440×900、1920×1080 和 390×844；桌面确认搜索仍在右侧工具栏同一行、账号弱上下文/文件夹强选中、已读不显示徽标、未读仍有文字状态；移动端确认无横向页面溢出。再用浏览器缩放或等价字号增加检查 200%。

- [ ] **Step 5: 提交本次实现文件**

提交前执行：

```bash
git status --short
git diff --check
git diff
git add VERSION README.md package.json frontend/package.json docker-compose.yml frontend/src/views/MailList.vue frontend/src/components/app/AppSidebar.vue frontend/src/styles/app-shell.css frontend/tests/mail-list-visual-hierarchy.test.mjs docs/superpowers/plans/2026-08-11-mail-list-visual-hierarchy.md
git diff --staged
```

Commit:

```bash
git commit -m "🎨 精简邮件列表状态并强化导航层级"
```

- [ ] **Step 6: 推送 origin/main 并重建当前 flymail 容器**

先推送：

```bash
git push origin main
```

若 22 端口不可用：

```bash
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git push origin main
```

推送成功后，保留当前 `/Docker/flymail/data:/data` 挂载和现有环境配置，用 `benxianyu/flymail:0.0.51` 重建当前 `flymail` 容器；重建前检查现有容器配置，重建后验证 running/healthy、`/api/health` 版本和原数据可访问。默认不执行 `docker login` / `docker push`。
