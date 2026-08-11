# FlyMail 邮件列表紧凑操作精修 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有搜索位置和邮件业务行为的前提下，把验证码复制操作改为始终可见的纯图标，并收紧发件人列、强化当前文件夹标题、继续弱化列表表格感。

**Architecture:** 只修改现有 `MailList.vue` 模板与 scoped CSS，并更新已有静态契约测试，不新增组件、依赖、后端接口或数据结构。通过 TDD 锁定纯图标复制、165px 桌面发件人列、13px semibold 文件夹标题、36% 分隔线和现有搜索单行不变量；发布时升级补丁版本到 `0.0.52` 并同步 README、Compose 和 package 版本。

**Tech Stack:** Vue 3、TypeScript、scoped CSS、Node.js `node:test`、Vite、Docker、MySQL 8.0。

## Global Constraints

- 搜索继续保持在当前桌面工具栏右侧这一行。
- 验证码复制按钮始终显示，只保留复制图标，不显示“复制”或“复制验证码”可见文字。
- 复制图标保留动态 `aria-label`、`title="复制验证码"`、原生 button、`@click.stop` 和 focus-visible。
- 桌面最终 `.mail-sender` 固定为 `165px`；1180px 以下现有 `150px` 规则保持；移动端保持 `width: auto`。
- `.list-count` 固定为 `13px`、`var(--text-primary)`、`var(--font-semibold)`，不恢复未读/全部计数。
- 最终邮件行分隔线固定为 `color-mix(in srgb, var(--border-color) 36%, transparent)`；hover、selected、未读点行为保持。
- 不改变验证码识别、邮件主题内容、会话、搜索、筛选、同步、分页、附件、详情或 WebSocket 行为。
- 不修改后端接口、数据库、环境变量、Dockerfile 或入口脚本。
- 不删除、迁移或写入正式 `/Docker/flymail/data`；临时容器验证必须使用独立临时目录。
- 不新增或升级生产依赖。

---

### Task 1: 用失败测试锁定紧凑复制与视觉层级

**Files:**
- Modify: `frontend/tests/mail-verification-code.test.mjs`
- Modify: `frontend/tests/mail-list-visual-hierarchy.test.mjs`
- Read: `frontend/src/views/MailList.vue`

**Interfaces:**
- Consumes: `.verification-code-copy`、`.mail-sender`、`.list-count`、`.mail-item`、`.toolbar-right` 当前模板和 CSS。
- Produces: 会在当前 `0.0.51` 代码上失败、在目标实现上通过的静态 UI 契约。

- [ ] **Step 1: 更新验证码复制按钮契约**

在 `frontend/tests/mail-verification-code.test.mjs` 中把当前可见文字断言：

```js
assert.match(source, />\s*复制验证码\s*<\/button>/s);
```

替换为以下契约：

```js
assert.match(source, /class="verification-code-copy"[\s\S]*title="复制验证码"[\s\S]*@click\.stop="copyVerificationCode\(msg\.verification_code\)"/s);
assert.match(source, /class="verification-code-copy"[\s\S]*<svg[^>]*aria-hidden="true"/s);
assert.doesNotMatch(source, /class="verification-code-copy"[\s\S]{0,500}>\s*复制验证码\s*<\/button>/s);
assert.match(source, /\.verification-code-copy\s*\{[^}]*width:\s*28px;[^}]*min-width:\s*28px;[^}]*height:\s*28px;/s);
assert.match(source, /\.verification-code-copy\s*\{[^}]*color:\s*var\(--text-tertiary\);/s);
assert.match(source, /@media \(max-width:\s*768px\)[\s\S]*\.verification-code-copy\s*\{[^}]*width:\s*28px;[^}]*min-width:\s*28px;[^}]*height:\s*28px;/s);
assert.doesNotMatch(source, /\.verification-code-copy\s*\{[^}]*min-width:\s*78px;/s);
assert.doesNotMatch(source, /@media \(max-width:\s*768px\)[\s\S]*\.verification-code-copy\s*\{[^}]*min-width:\s*72px;/s);
```

- [ ] **Step 2: 更新视觉层级契约**

在 `frontend/tests/mail-list-visual-hierarchy.test.mjs` 增加：

```js
assert.match(mail, /\.list-count\s*\{[^}]*font-size:\s*13px;[^}]*color:\s*var\(--text-primary\);[^}]*font-weight:\s*var\(--font-semibold\);/s);
assert.match(mail, /\.mail-sender\s*\{[^}]*width:\s*165px;[^}]*gap:\s*9px;[^}]*padding-right:\s*12px;/s);
assert.match(mail, /@media \(max-width:\s*1180px\) and \(min-width:\s*769px\)[\s\S]*\.mail-sender\s*\{[^}]*width:\s*150px;/s);
assert.match(mail, /border-bottom-color:\s*color-mix\(in srgb, var\(--border-color\) 36%, transparent\);/);
assert.match(mail, /<div class="toolbar-right">[\s\S]*<MailSearchBar/s);
assert.match(mail, /\.toolbar-right\s*\{[^}]*flex-wrap:\s*nowrap;[^}]*justify-content:\s*flex-end;/s);
```

- [ ] **Step 3: 运行聚焦测试确认 RED**

Run:

```bash
cd frontend
node --test tests/mail-verification-code.test.mjs tests/mail-list-visual-hierarchy.test.mjs
```

Expected: FAIL，至少因为复制按钮仍显示文字且宽度为 78px、`.mail-sender` 仍为 190px、`.list-count` 仍使用弱文字层级、分隔线仍为 58%。

---

### Task 2: 最小实现纯图标复制和列表精修

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Test: `frontend/tests/mail-verification-code.test.mjs`
- Test: `frontend/tests/mail-list-visual-hierarchy.test.mjs`

**Interfaces:**
- Consumes: `msg.verification_code`、`copyVerificationCode(code: string)`、现有邮件行 flex/grid、现有 1180px/768px 响应式断点。
- Produces: 纯图标复制按钮和四项确认后的视觉结果；不改变任何 API 或状态流。

- [ ] **Step 1: 把复制按钮可见文字替换为 SVG**

将模板改为：

```vue
<button
  v-if="msg.verification_code && !selectMode"
  class="verification-code-copy"
  type="button"
  :aria-label="`复制验证码 ${msg.verification_code}`"
  title="复制验证码"
  @click.stop="copyVerificationCode(msg.verification_code)"
>
  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
</button>
```

不修改 `copyVerificationCode()`。

- [ ] **Step 2: 收紧复制按钮 CSS**

将主 `.verification-code-copy` 改为：

```css
.verification-code-copy {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 28px;
  min-width: 28px;
  height: 28px;
  margin: 0 8px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
}

.verification-code-copy:hover {
  border-color: var(--border-color);
  background: var(--bg-hover);
  color: var(--color-accent);
}
```

保留现有 focus-visible。

移动端覆盖改为固定 `28×28px`、`min-width: 28px`、`margin: 0`、`padding: 0`，继续使用 `grid-area: code`。

- [ ] **Step 3: 调整发件人、文件夹标题和分隔线**

在最终生效样式中精确调整：

```css
.list-count {
  font-size: 13px;
  color: var(--text-primary);
  font-weight: var(--font-semibold);
}

.mail-item,
.mail-item:not(.unread) {
  border-bottom-color: color-mix(in srgb, var(--border-color) 36%, transparent);
}

.mail-sender {
  width: 165px;
  gap: 9px;
  padding-right: 12px;
}
```

1180px 以下的 `150px` 和移动端 `width: auto` 保持不变。

- [ ] **Step 4: 运行聚焦测试确认 GREEN**

Run:

```bash
cd frontend
node --test tests/mail-verification-code.test.mjs tests/mail-list-visual-hierarchy.test.mjs tests/mail-search-layout.test.mjs tests/ui-layout.test.mjs
```

Expected: PASS。

- [ ] **Step 5: 运行前端全量测试和生产构建**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: `node:test` 0 failures；`vue-tsc` 和 Vite build exit 0。允许现有 >500kB chunk warning，但不得有编译错误。

---

### Task 3: 同步 README 和 0.0.52 版本

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via `npm run sync-version`: `package.json`
- Modify via `npm run sync-version`: `frontend/package.json`
- Modify via `npm run sync-version`: `docker-compose.yml`

**Interfaces:**
- Consumes: 当前版本 `0.0.51`。
- Produces: 所有发布版本源一致为 `0.0.52`；README 描述纯图标复制与紧凑列表。

- [ ] **Step 1: 更新 README 用户可见说明**

把“复制验证码按钮”的用户可见描述改为“主题右侧提供始终可见的复制图标”；在邮件列表计数口径段补充当前文件夹标题强化、桌面发件人列收紧和更淡分隔线，不改变搜索仍在右侧同一行的说明。

- [ ] **Step 2: 把 VERSION 从 0.0.51 改为 0.0.52**

`VERSION` 内容精确为：

```text
0.0.52
```

- [ ] **Step 3: 同步版本来源**

Run:

```bash
npm run sync-version
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
rg -n "benxianyu/flymail:0\.0\.52" docker-compose.yml README.md
```

Expected: 三个版本均为 `0.0.52`，Compose 和 README 镜像标签均为 `benxianyu/flymail:0.0.52`。

---

### Task 4: 发布级测试、Docker 和浏览器验证

**Files:**
- Verify only: backend/frontend/scripts/Docker deployment

**Interfaces:**
- Consumes: 完整 `0.0.52` 工作树和镜像 `benxianyu/flymail:0.0.52`。
- Produces: 后端、前端、Compose、Docker、MySQL、持久化、安全日志和四种浏览器视口的可交付证据。

- [ ] **Step 1: 后端、Shell、Compose 和 diff 检查**

Run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose --no-interpolate --no-path-resolution config --quiet
git diff --check
git status --short
git diff
```

说明：仓库当前没有提交 `.env`，因此使用 `--no-interpolate --no-path-resolution` 做结构校验，不创建或提交本地 `.env`。

- [ ] **Step 2: 构建本地镜像**

Run:

```bash
docker build -t benxianyu/flymail:0.0.52 .
```

Expected: build exit 0，Dockerfile 仍未写入任何真实密码或密钥。

- [ ] **Step 3: 独立临时容器验证**

使用位于 `/Docker` 下、但不是 `/Docker/flymail/data` 的临时目录，并设置根目录模式为 `0755` 让容器内 MySQL 用户可以遍历挂载根。临时环境使用随机管理员密码、至少 16 字符的 session secret，以及包含引号/反斜杠/`@`/`:`/`/`/`%` 中至少一种特殊字符的 MySQL 密码。

验证：

```text
容器达到 healthy
GET /api/health 返回 version=0.0.52
mysql --version 为 8.0.x
SELECT @@datadir 返回 /data/mysql/
/data/flymail 已创建
数据库写入测试行成功
docker restart 后测试行仍存在
日志不包含测试 MySQL 密码、管理员密码或 session secret
image Config.Env 不包含测试密码或 session secret
SIGTERM 停止后 MySQL 正常关闭
临时容器和临时目录全部清理
```

- [ ] **Step 4: 浏览器视觉验证**

使用本地 `0.0.52` 临时容器提供真实 production build，并通过 Playwright 拦截 API 注入包含普通长主题、长发件人和验证码邮件的测试数据。验证：

```text
1440×900：纯复制图标始终可见；无“复制验证码”可见文字；日期仍在最右；搜索仍同一工具栏行；文件夹标题比快速筛选更突出。
1920×1080：同上；发件人列宽约 165px，主题得到更多空间。
390×844：纯复制图标可见，页面 scrollWidth <= viewport width。
200% 等价场景：工具栏不重叠，复制图标和日期可访问，无页面级横向溢出。
```

---

### Task 5: 提交、推送并更新正式 flymail 容器

**Files:**
- Commit only task files
- Runtime: current `flymail` container, preserving `/Docker/flymail/data:/data`

**Interfaces:**
- Consumes: 所有验证通过的 `0.0.52` 工作树和本地镜像。
- Produces: `origin/main` 与本地 HEAD 一致；正式 `flymail` 使用 `0.0.52` 且原数据和重启持久化不变。

- [ ] **Step 1: 提交前审查**

Run:

```bash
git status --short
git diff --check
git diff
git add README.md VERSION docker-compose.yml package.json frontend/package.json frontend/src/views/MailList.vue frontend/tests/mail-verification-code.test.mjs frontend/tests/mail-list-visual-hierarchy.test.mjs docs/superpowers/plans/2026-08-11-mail-list-compact-actions.md
git diff --staged
```

不得暂存无关文件或任何 secret。

- [ ] **Step 2: 提交实现**

Commit:

```bash
git commit -m "🎨 精简验证码复制操作并收紧邮件列表"
```

- [ ] **Step 3: 推送 origin/main**

Run:

```bash
git push origin main
```

若 SSH 22 端口失败，检查状态后使用：

```bash
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git push origin main
```

不得 force push。

- [ ] **Step 4: 安全重建正式容器**

先检查当前 `flymail` 的镜像、端口、restart policy、环境变量键、`/Docker/flymail/data:/data` 挂载和数据库计数。因为仓库没有 `.env`，从当前容器安全继承运行时环境值，不打印值；停止旧容器并改名保留为回滚副本，使用 `benxianyu/flymail:0.0.52`、相同端口和同一 `/Docker/flymail/data:/data` 创建新 `flymail`。

只有新容器达到 `healthy`、`/api/health` 返回 `0.0.52`、MySQL 仍为 8.0、`@@datadir=/data/mysql/`、关键业务计数与切换前一致且日志无 secret 后，才删除旧回滚容器。

- [ ] **Step 5: 正式重启持久化和 Git 终检**

执行一次：

```bash
docker restart flymail
```

等待 healthy 后确认缓存邮件等业务计数与重启前一致；再检查：

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

Expected: 正式容器 running/healthy/version `0.0.52`；`HEAD == origin/main`；工作区干净。Docker Hub 不上传。
