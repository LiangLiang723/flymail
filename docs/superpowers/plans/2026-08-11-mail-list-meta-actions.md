# FlyMail 邮件列表右侧元信息操作区 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把邮件列表中的验证码复制图标和附件图标统一到日期左侧的动态元信息区，按“复制 → 附件 → 日期”稳定排列，不为缺失图标预留空槽，并保持桌面、移动端和 200% 等价场景无横向溢出。

**Architecture:** 只修改现有 `frontend/src/views/MailList.vue` 的邮件行模板和 scoped CSS，不新增组件、公共样式或依赖。`mail-info` 继续独占可收缩主题区；新 `mail-meta-actions` 仅承载实际存在的可选图标，日期仍作为最右固定列。先用静态前端契约让当前 `0.0.53` 明确 RED，再做最小 DOM/CSS 调整；最后升级到 `0.0.54` 并按项目完整 Docker/浏览器/正式容器门槛验证。

**Tech Stack:** Vue 3、TypeScript、scoped CSS、Node.js `node:test`、Vite、Docker、MySQL 8.0。

## Global Constraints

- 日期永远是邮件行最右固定列，并在桌面各行保持对齐。
- 元信息图标区不预留空槽；没有附件和验证码时不渲染 `mail-meta-actions`。
- 两个图标同时存在时 DOM 顺序必须是 `verification-code-copy` → `att-badge` → `mail-date`。
- 只有附件时附件是日期左侧唯一图标；只有复制时复制是日期左侧唯一图标。
- 附件只要存在，就必须是图标区最靠近日期的图标。
- 附件 SVG 固定 `15px × 15px`；复制按钮保持 `28px × 28px`，内部 SVG 保持 `15px × 15px`。
- `mail-meta-actions` 内部固定 `gap: 6px`；主题到图标区 8px；图标区/主题到日期 8px。
- 复制按钮移除当前 `margin: 0 8px`；日期移除当前补偿型 `padding-left: 8px`。
- 附件图标仍是非交互状态指示，不增加按钮、hover 背景或附件下载入口。
- 不改变验证码识别、附件检测/下载、日期格式、未读蓝点/信封/字重、发件人 165px/24px、搜索位置、分页、同步或邮件打开行为。
- 移动端继续现有两行 grid：日期右上、动态元信息区右下；不新建第二套邮件行模板。
- 390×844、1440×900、1920×1080 和 720×450（200% 等价）不得出现页面级横向溢出。
- 不修改后端、数据库 schema、认证、环境变量、Dockerfile 或入口脚本。
- 不迁移、不删除、不清理正式 `/Docker/flymail/data`；临时验证必须使用独立临时目录。
- `VERSION` 是版本事实来源；本次实现发布目标为 `0.0.54`。
- 默认推送 `origin/main`，默认不上传 Docker Hub。

---

### Task 1: 用失败契约锁定动态图标区的 DOM 顺序

**Files:**
- Modify: `frontend/tests/mail-verification-code.test.mjs`
- Modify: `frontend/tests/mail-list-visual-hierarchy.test.mjs`
- Read: `frontend/src/views/MailList.vue`

**Interfaces:**
- Consumes: 当前邮件行 `.mail-main-row`、`.att-badge`、`.verification-code-copy`、`.mail-date` 模板结构。
- Produces: 当前 `0.0.53` 必须失败、目标布局必须通过的静态 DOM/CSS 契约。

- [ ] **Step 1: 锁定附件不再位于主题文本流**

在 `mail-verification-code.test.mjs` 增加一个邮件元信息布局测试：

```js
test('mail row optional metadata uses one dynamic action rail before the date', async () => {
  const source = await read('src/views/MailList.vue');
  const mainRowStart = source.indexOf('<div class="mail-main-row">');
  const mainRowEnd = source.indexOf('</div>', mainRowStart);
  const mainRow = source.slice(mainRowStart, mainRowEnd);

  assert.ok(mainRowStart >= 0 && mainRowEnd > mainRowStart);
  assert.doesNotMatch(mainRow, /class="att-badge"/);

  const metaStart = source.indexOf('class="mail-meta-actions"');
  const copyIndex = source.indexOf('class="verification-code-copy"', metaStart);
  const attachmentIndex = source.indexOf('class="att-badge"', metaStart);
  const metaEnd = source.indexOf('</div>', metaStart);
  const dateIndex = source.indexOf('<span class="mail-date">', metaEnd);

  assert.ok(metaStart >= 0);
  assert.ok(copyIndex > metaStart);
  assert.ok(attachmentIndex > copyIndex);
  assert.ok(metaEnd > attachmentIndex);
  assert.ok(dateIndex > metaEnd);
});
```

该测试要求 DOM 自身保证“复制 → 附件 → 日期”，不依赖 CSS `order`。

- [ ] **Step 2: 锁定动态图标容器条件**

在同一测试中加入：

```js
assert.match(
  source,
  /v-if="msg\.has_attachments \|\| \(msg\.verification_code && !selectMode\)"\s+class="mail-meta-actions"/,
);
assert.match(source, /<svg v-if="msg\.has_attachments" class="att-badge" width="15" height="15"/);
```

这样：

```text
普通模式 + 仅验证码 -> 容器存在，只显示复制
普通模式 + 仅附件 -> 容器存在，只显示附件
普通模式 + 两者 -> 容器存在，复制在前附件在后
多选模式 + 仅验证码 -> 容器不存在，不恢复复制动作
多选模式 + 附件 -> 容器仍存在，只显示附件状态
```

- [ ] **Step 3: 锁定桌面间距和日期列**

在 `mail-list-visual-hierarchy.test.mjs` 增加：

```js
assert.match(
  mail,
  /\.mail-meta-actions\s*\{[^}]*display:\s*inline-flex;[^}]*align-items:\s*center;[^}]*flex-shrink:\s*0;[^}]*gap:\s*6px;[^}]*margin-left:\s*8px;/s,
);
assert.match(mail, /\.att-badge\s*\{[^}]*width:\s*15px;[^}]*height:\s*15px;[^}]*margin:\s*0;/s);
assert.match(mail, /\.verification-code-copy\s*\{[^}]*margin:\s*0;/s);
assert.match(mail, /\.mail-date\s*\{[^}]*width:\s*58px;[^}]*margin-left:\s*8px;[^}]*padding-left:\s*0;/s);
assert.doesNotMatch(mail, /\.verification-code-copy\s*\{[^}]*margin:\s*0 8px;/s);
```

- [ ] **Step 4: 锁定移动端 meta grid area**

把当前 `code` grid 契约改成：

```js
assert.match(
  source,
  /@media \(max-width:\s*768px\)[\s\S]*grid-template-areas:[\s\S]*"select sender date"[\s\S]*"select info meta"/s,
);
assert.match(
  source,
  /@media \(max-width:\s*768px\)[\s\S]*\.mail-meta-actions\s*\{[^}]*grid-area:\s*meta;[^}]*justify-self:\s*end;[^}]*margin:\s*0;[^}]*gap:\s*6px;/s,
);
assert.doesNotMatch(
  source,
  /@media \(max-width:\s*768px\)[\s\S]*\.verification-code-copy\s*\{[^}]*grid-area:\s*code;/s,
);
```

继续保留已有 `.list-items { overflow-x: hidden; }`、搜索同一行、发件人 165px/24px 和未读信封契约。

- [ ] **Step 5: 运行聚焦前端测试确认 RED**

Run:

```bash
cd frontend
node --test \
  tests/mail-verification-code.test.mjs \
  tests/mail-list-visual-hierarchy.test.mjs \
  tests/mail-search-layout.test.mjs \
  tests/ui-layout.test.mjs
```

Expected: FAIL，至少因为当前没有 `.mail-meta-actions`、附件仍在 `.mail-main-row`、复制按钮仍有 `margin: 0 8px`、移动端仍使用 `code` grid area。

---

### Task 2: 最小实现统一右侧元信息区并确认 GREEN

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Test: `frontend/tests/mail-verification-code.test.mjs`
- Test: `frontend/tests/mail-list-visual-hierarchy.test.mjs`

**Interfaces:**
- Consumes: `msg.has_attachments: boolean`、`msg.verification_code?: string`、`selectMode: boolean`、`copyVerificationCode(code: string)`、现有邮件行 flex/grid。
- Produces: 动态 `mail-meta-actions`，DOM 顺序固定为复制按钮 → 附件图标，日期始终在容器之后。

- [ ] **Step 1: 把附件移出 `.mail-main-row`**

将当前主题区：

```vue
<span class="mail-subject">{{ msg.subject || '(无主题)' }}</span>
<span v-if="listMode === 'conversations' && (msg.message_count || 1) > 1" class="conversation-count">{{ msg.message_count }}</span>
<svg v-if="msg.has_attachments" class="att-badge" width="12" height="12" ...>...</svg>
```

改为只保留主题和会话计数：

```vue
<span class="mail-subject">{{ msg.subject || '(无主题)' }}</span>
<span v-if="listMode === 'conversations' && (msg.message_count || 1) > 1" class="conversation-count">{{ msg.message_count }}</span>
```

不改变未读信封和主题文本。

- [ ] **Step 2: 新增动态 `mail-meta-actions`**

在 `mail-info` 之后、`mail-date` 之前加入：

```vue
<div
  v-if="msg.has_attachments || (msg.verification_code && !selectMode)"
  class="mail-meta-actions"
>
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
  <svg
    v-if="msg.has_attachments"
    class="att-badge"
    width="15"
    height="15"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
  >
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/>
  </svg>
</div>
<span class="mail-date">{{ formatDate(msg.date) }}</span>
```

不要使用 CSS `order`，不要为无图标状态渲染空容器。

- [ ] **Step 3: 统一桌面 CSS 间距**

在附件样式附近新增/调整：

```css
.mail-meta-actions {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  gap: 6px;
  margin-left: 8px;
}

.att-badge {
  flex-shrink: 0;
  width: 15px;
  height: 15px;
  color: var(--ui-text-3);
  margin: 0;
}
```

把复制按钮：

```css
margin: 0 8px;
```

改为：

```css
margin: 0;
```

在最终覆盖区把日期改为：

```css
.mail-date {
  width: 58px;
  margin-left: 8px;
  padding-left: 0;
}
```

日期仍保留原有 `flex-shrink: 0`、`text-align: right`、字体和颜色规则。

- [ ] **Step 4: 把移动端 `code` 区泛化为 `meta`**

将：

```css
grid-template-areas:
  "select sender date"
  "select info code";
```

改为：

```css
grid-template-areas:
  "select sender date"
  "select info meta";
```

增加：

```css
.mail-meta-actions {
  grid-area: meta;
  align-self: center;
  justify-self: end;
  margin: 0;
  gap: 6px;
}
```

把移动端 `.verification-code-copy` 中的 `grid-area: code` 删除，只保留 28×28 尺寸和 `margin: 0; padding: 0`。

移动端 `.mail-date` 继续：

```css
.mail-date {
  grid-area: date;
  width: auto;
  align-self: center;
  margin: 0;
  padding: 0;
  font-size: 10px;
}
```

- [ ] **Step 5: 运行聚焦测试确认 GREEN**

Run:

```bash
cd frontend
node --test \
  tests/mail-verification-code.test.mjs \
  tests/mail-list-visual-hierarchy.test.mjs \
  tests/mail-search-layout.test.mjs \
  tests/ui-layout.test.mjs
```

Expected: PASS。

- [ ] **Step 6: 运行前端完整测试和 production build**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: `node:test` 0 failures；`vue-tsc` 和 Vite build exit 0。允许项目已有 >500kB chunk warning，不允许编译错误。

---

### Task 3: 同步 README 和 0.0.54 版本

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: 当前版本 `0.0.53`、用户确认的邮件元信息布局。
- Produces: 所有版本源统一为 `0.0.54`，README 描述附件与验证码图标统一位于日期左侧。

- [ ] **Step 1: 更新 README 邮件列表说明**

把当前：

```text
识别到验证码时，日期左侧始终显示紧凑的纯复制图标。
```

扩展为：

```text
邮件行右侧把验证码复制和附件状态统一放入日期左侧的动态元信息区；同时存在时固定为“复制 → 附件 → 日期”，只有一个图标时该图标直接靠近日期，不为缺失图标预留空槽位。
```

保留发件人 165px/24px、未读信封、搜索同一行等现有说明。

- [ ] **Step 2: 把 `VERSION` 更新到 0.0.54**

使用编辑工具修改：

```text
VERSION: 0.0.53 -> 0.0.54
```

由于 DevSpace `bash` 禁止命令修改项目文件，不能直接运行会写文件的 `npm run sync-version`；使用受控编辑工具同步其等价结果：

```text
package.json -> 0.0.54
frontend/package.json -> 0.0.54
docker-compose.yml image -> benxianyu/flymail:0.0.54
README 所有 benxianyu/flymail:0.0.53 -> benxianyu/flymail:0.0.54
```

- [ ] **Step 3: 只读验证版本一致**

Run:

```bash
printf 'VERSION=' && cat VERSION
node -e "console.log('root=' + require('./package.json').version)"
node -e "console.log('frontend=' + require('./frontend/package.json').version)"
rg -n "benxianyu/flymail:0\.0\.54" README.md docker-compose.yml
rg -n "benxianyu/flymail:0\.0\.53" README.md docker-compose.yml || true
```

Expected: VERSION/root/frontend 全部 `0.0.54`；README/Compose 不再引用 `0.0.53` 镜像标签。

- [ ] **Step 4: 重新确认 `.env.example` 无需修改**

本轮没有环境变量、端口、路径、权限或认证变化，因此 `.env.example` 保持原样且不得创建 `.env`。

---

### Task 4: 发布级测试、Docker 和浏览器验收

**Files:**
- Verify only: backend/frontend/scripts/Docker runtime

**Interfaces:**
- Consumes: 完整 `0.0.54` 工作树。
- Produces: 自动化测试、production build、Docker/MySQL/持久化/安全和四档浏览器视口的交付证据。

- [ ] **Step 1: 完整后端测试**

虽然本轮不改后端，项目交付门槛仍要求：

```bash
cd backend
python -m unittest discover -s tests -v
```

Expected: 0 failures。

- [ ] **Step 2: 版本更新后重新跑完整前端测试与构建**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: 0 failures，build exit 0。

- [ ] **Step 3: Shell、Compose、diff 检查**

Run:

```bash
bash -n scripts/docker-entrypoint.sh
docker compose config --no-interpolate --no-path-resolution --quiet
git diff --check
git status --short
git diff
```

仓库没有 `.env`；Compose 只做结构校验，不创建或写入 `.env`。

- [ ] **Step 4: 构建本地镜像**

Run:

```bash
docker build -t benxianyu/flymail:0.0.54 .
```

Expected: build exit 0；镜像元数据不包含管理员密码、数据库密码或 session secret。

- [ ] **Step 5: 独立临时容器验证**

使用 `/Docker/flymail-test-0054-*` 独立临时目录，绝不使用 `/Docker/flymail/data`。动态生成测试管理员密码、至少 16 字符 session secret，以及包含引号/反斜杠/`@`/`:`/`/`/`%` 等特殊字符的 MySQL 密码。

验证：

```text
容器 healthy
/api/health version=0.0.54
MySQL 8.0.x
@@datadir=/data/mysql/
/data/flymail 存在
数据库读写成功
restart 后测试数据仍存在
日志不包含测试密码或 secret
image Config.Env 不包含运行时 secret
stop/SIGTERM 后 MySQL 安全关闭
临时容器和临时目录清理
```

- [ ] **Step 6: 浏览器四状态 × 四视口验收**

使用真实 `0.0.54` production build + Playwright 安全拦截 `/api/*`，构造四行脱敏邮件：

```text
A: has_attachments=false, verification_code=""
B: has_attachments=true,  verification_code=""
C: has_attachments=false, verification_code="654321"
D: has_attachments=true,  verification_code="654321"
```

1440×900 与 1920×1080 验证：

```text
四行日期右边界一致
A 没有 mail-meta-actions
B 只有附件，附件紧邻日期左侧
C 只有复制，复制紧邻日期左侧
D DOM/视觉顺序为复制 → 附件 → 日期
D 中附件比复制更靠近日期
附件不再位于主题文本流
复制按钮 28×28、附件 15×15
图标内部 gap=6px，主题/日期两侧间距符合 8px 规则
长主题不会挤掉图标和日期
搜索仍在桌面工具栏同一行
page scrollWidth <= viewport width
```

390×844 验证：

```text
日期右上、meta 右下
D 中复制在左、附件在右
A 不为缺失图标保留第二行固定占位
图标与日期不重叠
page scrollWidth <= 390
```

720×450（200% 等价）验证相同关键约束，尤其是无页面级横向溢出。

---

### Task 5: 提交、推送并安全更新正式 flymail 容器

**Files:**
- Commit only task files
- Runtime: current `flymail`, preserving `/Docker/flymail/data:/data`

**Interfaces:**
- Consumes: 所有验证通过的 `0.0.54` 工作树和本地镜像。
- Produces: `origin/main` 与本地 HEAD 一致；正式 `flymail` 使用 `0.0.54` 且业务数据与重启持久化保持。

- [ ] **Step 1: 提交前终检并只暂存本次文件**

Run:

```bash
git status --short
git diff --check
git diff
git add \
  README.md \
  VERSION \
  package.json \
  docker-compose.yml \
  frontend/package.json \
  frontend/src/views/MailList.vue \
  frontend/tests/mail-verification-code.test.mjs \
  frontend/tests/mail-list-visual-hierarchy.test.mjs \
  docs/superpowers/plans/2026-08-11-mail-list-meta-actions.md
git diff --staged
```

Spec `docs/superpowers/specs/2026-08-11-mail-list-meta-actions-design.md` 已在提交 `eca5ab7` 中，不重复暂存。

- [ ] **Step 2: 提交实现**

Commit:

```bash
git commit -m "🎨 统一邮件列表图标与日期排布"
```

- [ ] **Step 3: 推送 `origin/main`**

Run:

```bash
git push origin main
```

若 SSH 22 端口失败，先检查分支/远端/状态，再使用官方 443 SSH；禁止 force push。

- [ ] **Step 4: 读取正式容器基线，不输出 secret 值**

确认：

```text
当前 image/version/health
host port -> 8080
restart policy
/Docker/flymail/data:/data mount
MySQL version/datadir
users/accounts/cached_messages 代表性计数
仅输出运行时环境变量键名，不输出值
```

- [ ] **Step 5: 回滚保护方式替换正式容器**

仓库没有 `.env`，因此从当前容器安全继承现有运行时环境到权限 `0600` 临时 env 文件，禁止输出其值。旧容器先停止并改名为 rollback；创建新 `flymail` 时保持：

```text
image=benxianyu/flymail:0.0.54
相同 host port
相同 restart policy
/Docker/flymail/data:/data
相同运行时环境
```

只有以下全部通过后才删除 rollback：

```text
new flymail healthy
/api/health=0.0.54
MySQL 8.0
@@datadir=/data/mysql/
/data/flymail 存在
users/accounts/cached_messages 与切换前一致
日志无 inherited secret
```

然后删除临时 env 文件。

- [ ] **Step 6: 正式重启持久化终检**

Run:

```bash
docker restart flymail
```

等待 healthy 后再次确认：

```text
/api/health=0.0.54
MySQL version/datadir 不变
cached_messages 等代表性计数重启前后一致
mount 仍为 /Docker/flymail/data:/data
```

最后：

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

Expected: 工作区干净，`HEAD == origin/main`，正式容器 running/healthy `0.0.54`。Docker Hub 不上传。
