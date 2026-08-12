# FlyMail 邮件列表标题附属图标布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 桌面邮件列表让会话数量、验证码复制和附件图标紧跟标题，日期独立固定最右；移动端继续保持日期右上、图标右下的 `0.0.54` 布局。

**Architecture:** 只修改现有 `frontend/src/views/MailList.vue` 的邮件行模板和 scoped CSS，不新增公共组件、第二套图标 DOM 或依赖。新增轻量 `mail-title-content` 包装标题与会话数量；`mail-meta-actions` 继续作为 `mail-item` 直接子项，以便桌面通过 flex 紧跟标题、移动端继续落入 `meta` grid area。先用静态前端契约让 `0.0.54` 明确 RED，再写最小实现；最后升级到 `0.0.55`，完整验证测试、Docker、浏览器和正式持久化。

**Tech Stack:** Vue 3、TypeScript、scoped CSS、Node.js `node:test`、Vite、Docker、MySQL 8.0。

## Global Constraints

- 桌面视觉顺序必须为：`未读信封 → 标题 → 会话数量 → 复制 → 附件 → 空白 → 日期`。
- 桌面 `mail-info` 不再 `flex: 1` 吞掉全部剩余宽度；使用 `flex: 0 1 auto` 且 `min-width: 0`。
- 新增 `mail-title-content`，只包含 `mail-subject` 和可选 `conversation-count`，使用 `display: inline-flex; align-items: center; gap: 6px; min-width: 0; flex: 0 1 auto`。
- 未读信封到标题继续保持 `.mail-main-row` 的 `8px`；标题到会话数量 `6px`；`mail-info` 到 `mail-meta-actions` `6px`；复制到附件 `6px`。
- 标题是连续内容簇中唯一可主动收缩和 ellipsis 的内容；会话数量、复制、附件、日期全部 `flex-shrink: 0`。
- `mail-meta-actions` 继续只在附件或验证码存在时渲染，不预留空槽，内部顺序固定 `verification-code-copy → att-badge`。
- 桌面日期是唯一最右固定锚点：`margin-left: auto`；总预留宽度 `70px`，其中左侧安全 inset `12px`，日期文本轨道保持 `58px`。
- 移动端继续 `"select sender date" / "select info meta"` 两行 grid；日期右上、`mail-meta-actions` 右下；不把图标塞进主题流。
- 移动端 `mail-title-content` 覆盖为 `flex: 1 1 auto; min-width: 0`。
- 复制按钮保持 28×28，内部 SVG 15×15；附件保持 15×15；附件存在时仍是附属图标中最右侧。
- 不改变验证码识别、附件检测/下载、会话逻辑、日期格式、未读状态、发件人 165px/24px、搜索位置、分页、同步或邮件打开行为。
- 不新增生产依赖，不修改后端、数据库 schema、认证、环境变量、Dockerfile 或入口脚本。
- 390×844、1440×900、1920×1080、720×450（200% 等价）不得出现页面级横向溢出。
- 正式 `/Docker/flymail/data` 不迁移、不删除、不清理；临时容器使用独立 `/Docker/flymail-test-0055-*`。
- `VERSION` 是版本事实来源；本次发布目标 `0.0.55`。
- 默认提交并推送 `origin/main`；默认不上传 Docker Hub。

---

### Task 1: 用失败契约锁定桌面标题伴随布局与移动端不变

**Files:**
- Modify: `frontend/tests/mail-verification-code.test.mjs`
- Modify: `frontend/tests/mail-list-visual-hierarchy.test.mjs`
- Read: `frontend/src/views/MailList.vue`

**Interfaces:**
- Consumes: 当前 `.mail-info`、`.mail-main-row`、`.mail-subject`、`.conversation-count`、`.mail-meta-actions`、`.mail-date`。
- Produces: 当前 `0.0.54` 必须失败、目标桌面/移动布局必须通过的 DOM/CSS 静态契约。

- [ ] **Step 1: 更新 DOM 顺序测试，要求新增 `mail-title-content`**

把现有 `mail row optional metadata uses one dynamic action rail before the date` 测试收紧为：

```js
test('desktop mail metadata follows the title while date remains the final row item', async () => {
  const source = await read('src/views/MailList.vue');
  const infoStart = source.indexOf('class="mail-info"');
  const titleContentStart = source.indexOf('class="mail-title-content"', infoStart);
  const subjectIndex = source.indexOf('class="mail-subject"', titleContentStart);
  const conversationIndex = source.indexOf('class="conversation-count"', subjectIndex);
  const titleContentEnd = source.indexOf('</div>', conversationIndex);
  const mainRowEnd = source.indexOf('</div>', titleContentEnd + 6);
  const infoEnd = source.indexOf('</div>', mainRowEnd + 6);
  const metaStart = source.indexOf('class="mail-meta-actions"', infoEnd);
  const copyIndex = source.indexOf('class="verification-code-copy"', metaStart);
  const attachmentIndex = source.indexOf('class="att-badge"', copyIndex);
  const metaEnd = source.indexOf('</div>', attachmentIndex);
  const dateIndex = source.indexOf('<span class="mail-date">', metaEnd);

  assert.ok(infoStart >= 0);
  assert.ok(titleContentStart > infoStart);
  assert.ok(subjectIndex > titleContentStart);
  assert.ok(conversationIndex > subjectIndex);
  assert.ok(titleContentEnd > conversationIndex);
  assert.ok(mainRowEnd > titleContentEnd);
  assert.ok(infoEnd > mainRowEnd);
  assert.ok(metaStart > infoEnd);
  assert.ok(copyIndex > metaStart);
  assert.ok(attachmentIndex > copyIndex);
  assert.ok(metaEnd > attachmentIndex);
  assert.ok(dateIndex > metaEnd);
});
```

该测试保证只有一套复制/附件 DOM，同时保证整体顺序为 `subject → conversation → copy → attachment → date`。

- [ ] **Step 2: 更新视觉契约，锁定桌面 flex 分配和精确间距**

在 `mail-list-visual-hierarchy.test.mjs` 将原来的右侧元信息轨道断言替换为：

```js
test('desktop metadata follows the title while the date owns the right edge', async () => {
  const mail = await read('src/views/MailList.vue');

  assert.match(mail, /\.mail-info\s*\{[^}]*flex:\s*0 1 auto;[^}]*min-width:\s*0;/s);
  assert.match(
    mail,
    /\.mail-title-content\s*\{[^}]*display:\s*inline-flex;[^}]*align-items:\s*center;[^}]*gap:\s*6px;[^}]*min-width:\s*0;[^}]*flex:\s*0 1 auto;/s,
  );
  assert.match(mail, /\.mail-main-row\s*\{[^}]*gap:\s*8px;/s);
  assert.match(mail, /\.mail-meta-actions\s*\{[^}]*gap:\s*6px;[^}]*margin-left:\s*6px;/s);
  assert.match(mail, /\.conversation-count\s*\{[^}]*flex-shrink:\s*0;/s);
  assert.match(mail, /\.att-badge\s*\{[^}]*flex-shrink:\s*0;/s);
  assert.match(mail, /\.verification-code-copy\s*\{[^}]*flex-shrink:\s*0;/s);
  assert.match(mail, /\.mail-date\s*\{[^}]*width:\s*70px;[^}]*margin-left:\s*auto;[^}]*padding-left:\s*12px;/s);
});
```

- [ ] **Step 3: 锁定移动端仍为右上日期、右下图标**

在 `mail-verification-code.test.mjs` 保留并补充：

```js
assert.match(
  source,
  /@media \(max-width:\s*768px\)[\s\S]*grid-template-areas:[\s\S]*"select sender date"[\s\S]*"select info meta"/s,
);
assert.match(
  source,
  /@media \(max-width:\s*768px\)[\s\S]*\.mail-title-content\s*\{[^}]*flex:\s*1 1 auto;[^}]*min-width:\s*0;/s,
);
assert.match(
  source,
  /@media \(max-width:\s*768px\)[\s\S]*\.mail-meta-actions\s*\{[^}]*grid-area:\s*meta;[^}]*justify-self:\s*end;[^}]*margin:\s*0;/s,
);
assert.match(
  source,
  /@media \(max-width:\s*768px\)[\s\S]*\.mail-date\s*\{[^}]*grid-area:\s*date;[^}]*width:\s*auto;[^}]*margin:\s*0;[^}]*padding:\s*0;/s,
);
```

- [ ] **Step 4: 运行聚焦测试确认 RED**

Run:

```bash
cd frontend
node --test tests/mail-verification-code.test.mjs tests/mail-list-visual-hierarchy.test.mjs
```

Expected: FAIL。失败原因至少包含当前没有 `.mail-title-content`、`.mail-info` 仍为 `flex: 1`、`.mail-meta-actions` 仍为 `margin-left: 8px`、桌面日期仍为 `58px / margin-left: 8px`。

---

### Task 2: 最小实现桌面标题伴随图标，保持移动端布局

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Test: `frontend/tests/mail-verification-code.test.mjs`
- Test: `frontend/tests/mail-list-visual-hierarchy.test.mjs`

**Interfaces:**
- Consumes: Task 1 的 DOM/CSS 契约。
- Produces: 桌面标题伴随附属项 + 独立右侧日期；移动端继续右上日期、右下图标。

- [ ] **Step 1: 在模板中增加 `mail-title-content`，不复制图标 DOM**

把 `mail-main-row` 中标题与会话数量包装为：

```vue
<div class="mail-main-row">
  <svg
    v-if="!noReadStateFolder && !msg.is_read"
    class="mail-status-icon unread-icon"
    width="16"
    height="16"
    viewBox="0 0 24 24"
  >...</svg>
  <div class="mail-title-content">
    <span class="mail-subject">{{ msg.subject || '(无主题)' }}</span>
    <span
      v-if="listMode === 'conversations' && (msg.message_count || 1) > 1"
      class="conversation-count"
    >{{ msg.message_count }}</span>
  </div>
</div>
```

`mail-meta-actions` 与 `mail-date` 继续保持 `mail-item` 直接子项，复制按钮和附件 SVG 的条件、顺序和事件不变。

- [ ] **Step 2: 调整桌面 flex 与标题收缩规则**

在 scoped CSS 中写入：

```css
.mail-info {
  flex: 0 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
}

.mail-main-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 0 1 auto;
}

.mail-title-content {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 0 1 auto;
}

.mail-subject {
  flex: 0 1 auto;
  min-width: 0;
}

.mail-meta-actions {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  gap: 6px;
  margin-left: 6px;
}
```

保留 `.conversation-count`、`.verification-code-copy`、`.att-badge` 的 `flex-shrink: 0`。

- [ ] **Step 3: 让日期独立吸收剩余空间并保留 58px 文本轨道**

把最终桌面覆盖改为：

```css
.mail-date {
  width: 70px;
  margin-left: auto;
  padding-left: 12px;
}
```

现有 `.mail-date { text-align: right; flex-shrink: 0; }` 不变，因此 70px 总宽度 = 12px 安全 inset + 58px 日期文本轨道。

- [ ] **Step 4: 保持移动端 grid，并恢复标题内容层可占满 info 区**

在 `@media (max-width: 768px)` 中补：

```css
.mail-info {
  grid-area: info;
  width: 100%;
  min-width: 0;
  padding-left: 39px;
  flex: 1 1 auto;
}

.mail-main-row {
  width: 100%;
  min-width: 0;
  flex: 1 1 auto;
}

.mail-title-content {
  flex: 1 1 auto;
  min-width: 0;
}

.mail-meta-actions {
  grid-area: meta;
  align-self: center;
  justify-self: end;
  margin: 0;
  gap: 6px;
}

.mail-date {
  grid-area: date;
  width: auto;
  align-self: center;
  margin: 0;
  padding: 0;
  font-size: 10px;
}
```

移动 grid areas 继续保持：

```css
grid-template-areas:
  "select sender date"
  "select info meta";
```

- [ ] **Step 5: 运行聚焦测试确认 GREEN**

Run:

```bash
cd frontend
node --test tests/mail-verification-code.test.mjs tests/mail-list-visual-hierarchy.test.mjs tests/ui-regression.test.mjs
```

Expected: PASS，所有新增布局契约和既有 UI 回归契约通过。

- [ ] **Step 6: 运行前端全量测试与 production build**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: 全部测试 PASS；`vue-tsc` 和 Vite build 成功。允许项目现有 >500kB chunk 提示，不允许类型或构建错误。

---

### Task 3: 同步 README 和 0.0.55 版本

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify via version sync: `package.json`
- Modify via version sync: `frontend/package.json`
- Modify via version sync: `docker-compose.yml`

**Interfaces:**
- Consumes: Task 2 已验证的用户可见行为。
- Produces: 用户文档与所有版本源一致为 `0.0.55`。

- [ ] **Step 1: 更新 README 邮件列表描述**

将“验证码复制和附件状态统一放入日期左侧动态元信息区”的描述改为：桌面端会话数量、验证码复制和附件紧跟标题，日期独立固定最右；移动端仍保持日期右上、图标右下。验证码识别规则、未读状态、发件人 165px/24px 和搜索位置说明保持不变。

- [ ] **Step 2: 把 `VERSION` 从 0.0.54 改为 0.0.55**

文件内容精确为：

```text
0.0.55
```

- [ ] **Step 3: 执行项目版本同步脚本**

Run:

```bash
npm run sync-version
```

Expected: `package.json`、`frontend/package.json`、`docker-compose.yml`、README 镜像标签全部同步到 `0.0.55`。

- [ ] **Step 4: 验证版本一致性与环境变量不变**

Run:

```bash
cat VERSION
node -e "console.log(require('./package.json').version)"
node -e "console.log(require('./frontend/package.json').version)"
rg -n 'benxianyu/flymail:0\.0\.55' README.md docker-compose.yml
rg -n 'benxianyu/flymail:0\.0\.54' README.md docker-compose.yml || true
```

Expected: 三个版本均 `0.0.55`，README/Compose 不再引用 `0.0.54` 镜像。`.env.example` 无需修改，因为没有新增或修改环境变量。

---

### Task 4: 发布级自动化、Docker 与浏览器验证

**Files:**
- Verify only: full workspace

**Interfaces:**
- Consumes: 完整 `0.0.55` 工作树。
- Produces: 可提交、可部署的验证证据。

- [ ] **Step 1: 运行后端全量测试**

Run:

```bash
cd backend
python -m unittest discover -s tests -v
```

Expected: 现有后端全量测试全部 PASS；本轮不改后端。

- [ ] **Step 2: 运行前端全量测试与 production build**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: 全量测试 PASS，production build 成功。

- [ ] **Step 3: 运行静态与 Compose 检查**

Run:

```bash
bash -n scripts/docker-entrypoint.sh
docker compose config --no-interpolate --no-path-resolution --quiet
git diff --check
git status --short
git diff
```

Expected: Shell、Compose、diff check 全通过；改动只包含本任务文件和已提交 spec。

- [ ] **Step 4: 构建本地镜像**

Run:

```bash
docker build -t benxianyu/flymail:0.0.55 .
```

Expected: build 成功，记录镜像 SHA。默认不执行 `docker login` 或 `docker push`。

- [ ] **Step 5: 使用独立临时数据验证容器**

使用独立 `/Docker/flymail-test-0055-*`、临时容器名和随机管理员/session secret/MySQL 密码；不得使用 `/Docker/flymail/data`。至少验证：

```text
container healthy
/api/health version=0.0.55
mysql version=8.0
@@datadir=/data/mysql/
/data/flymail exists
database read/write succeeds
restart preserves sentinel data
logs do not expose DB/session passwords
image Config.Env contains no runtime password/session secret
SIGTERM stops MySQL cleanly
```

本轮不涉及密码处理，但仍复用现有容器发布门槛；测试结束删除临时容器与临时数据。

- [ ] **Step 6: 用真实 production build 做四档浏览器布局验证**

使用安全模拟 API 数据覆盖：无图标、仅附件、仅验证码、两者都有、会话数+两图标、超长主题。

Desktop `1440×900` 与 `1920×1080`：

```text
标题后紧跟会话数/复制/附件
同时存在顺序 = 标题 → 数量 → 复制 → 附件
只有一个图标时不预留空槽
长主题只截断主题
图标与日期之间存在明显弹性空白
所有日期右边界严格对齐
页面无横向溢出
```

Mobile `390×844`：

```text
日期仍在右上
mail-meta-actions 仍在右下
复制在附件左侧
桌面标题伴随规则不泄漏到移动端
页面无横向溢出
```

`720×450`（200% 等价）：标题正确收缩、图标和日期可见、无页面级横向溢出。

---

### Task 5: 最终复验、提交、推送和正式容器升级

**Files:**
- Stage/commit only files from Tasks 1–3 plus this implementation plan
- Runtime: `flymail`, preserving `/Docker/flymail/data:/data`

**Interfaces:**
- Consumes: 所有验证通过的 `0.0.55` 工作树和本地镜像。
- Produces: `origin/main` 与本地 HEAD 一致；正式 `flymail` 使用 `0.0.55` 且数据与重启持久化保持。

- [ ] **Step 1: 在提交前重新运行完整终检**

Fresh run:

```bash
cd backend && python -m unittest discover -s tests -v
cd ../frontend && npm test && npm run build
cd ..
bash -n scripts/docker-entrypoint.sh
docker compose config --no-interpolate --no-path-resolution --quiet
git diff --check
```

只有 fresh 完整结果全绿才进入提交。

- [ ] **Step 2: 检查并只暂存本次文件**

Run:

```bash
git status --short
git diff --check
git diff
git add README.md VERSION package.json frontend/package.json docker-compose.yml frontend/src/views/MailList.vue frontend/tests/mail-verification-code.test.mjs frontend/tests/mail-list-visual-hierarchy.test.mjs docs/superpowers/plans/2026-08-12-mail-list-title-meta-layout.md
git diff --staged
```

确认 staged 中没有 secret、真实邮件、日志、附件、数据库数据或无关修改。

- [ ] **Step 3: 提交实现**

提交标题：

```text
🎨 调整邮件标题附属图标排布
```

- [ ] **Step 4: 推送 `origin/main`**

Run:

```bash
git push origin main
```

若 22 端口失败，使用项目允许的 GitHub 官方 SSH 443：

```bash
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git push origin main
```

不得 force push。

- [ ] **Step 5: 读取正式容器升级前基线**

只读记录：

```text
container=image/status/health/restart policy/port
mount=/Docker/flymail/data:/data
/api/health version
MySQL version and @@datadir
users/accounts/cached_messages counts
```

不输出密码、连接串或真实邮件正文。

- [ ] **Step 6: 回滚保护替换正式容器为 0.0.55**

保持旧容器作为临时 rollback，使用与现有正式容器相同的非敏感运行参数和原环境值创建新 `flymail`，唯一数据挂载仍为 `/Docker/flymail/data:/data`。只有新容器满足以下条件后才删除 rollback：

```text
running + healthy
/api/health=0.0.55
MySQL 8.0 and @@datadir=/data/mysql/
mount=/Docker/flymail/data:/data
users/accounts/cached_messages counts unchanged
logs do not expose secrets
```

- [ ] **Step 7: 正式容器真实重启持久化复验**

Run `docker restart flymail`，等待 healthy，再核对：

```text
/api/health=0.0.55
MySQL 8.0 /data/mysql/
mount still /Docker/flymail/data:/data
users/accounts/cached_messages counts preserved
```

- [ ] **Step 8: 最终 Git 与运行状态确认**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

Expected: 工作区干净，`HEAD == origin/main`，正式容器 running/healthy `0.0.55`。Docker Hub 未上传。
