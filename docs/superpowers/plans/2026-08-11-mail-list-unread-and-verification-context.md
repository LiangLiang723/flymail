# FlyMail 邮件列表未读降噪与验证码上下文收紧 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除邮件列表右侧未读文字徽标、保留未读蓝点/信封/字重，增加桌面发件人与主题的 12px 视觉留白，并把验证码识别从“裸 verification”收紧到明确代码上下文，同时继续支持真实 Atlassian 验证身份场景。

**Architecture:** 前端只修改现有 `MailList.vue` 模板和 scoped CSS，不新增组件或公共样式；后端只收紧 `services/verification_code.py` 的上下文匹配，不修改 API、数据库或正文读取范围。两个行为分别采用 TDD：先让新的误识别测试和视觉契约在 `0.0.52` 上失败，再写最小实现；发布时升级补丁版本到 `0.0.53`，完整验证 Docker、浏览器和正式持久化。

**Tech Stack:** FastAPI/Python、Python `unittest`、Vue 3、TypeScript、scoped CSS、Node.js `node:test`、Vite、Docker、MySQL 8.0。

## Global Constraints

- 右侧“未读”/`未读 N` 徽标必须删除。
- 左侧未读蓝点、蓝色未读信封、未读发件人/主题字重必须保留。
- 桌面 `.mail-sender` 继续 `width: 165px`，`padding-right` 从 `12px` 精确改为 `24px`；769–1180px 固定 `width: 150px; padding-right: 12px`；768px 以下继续 `width: auto; padding-right: 0`。
- 不加竖线、边框或新的状态列；搜索继续保持在当前桌面工具栏右侧同一行。
- 验证码采用方案 B：收紧上下文，不新增 Jira、Atlassian、`IH831-` 或 `[A-Z]+-\d+` 黑名单/特例。
- 裸 `verification`、`verification request/status/completed` 不得单独触发验证码；`verification code`、`security code`、`authentication code`、`OTP`、`passcode`、中文“验证码/验证代码”等明确代码语义继续识别。
- 中文“验证身份”不能单独触发；只有同一局部上下文同时出现明确代码提示（如“请输入以下代码”）时继续支持 Atlassian 场景。
- 不改变验证码候选长度 4–8 位，不引入机器学习/OCR/第三方识别服务或新依赖。
- 不修改 API schema、数据库 schema、认证、环境变量、Dockerfile、入口脚本、搜索、分页、同步或邮件主题内容。
- 不迁移、不删除、不清理正式 `/Docker/flymail/data`；临时容器必须使用独立临时目录。
- `VERSION` 是版本事实来源；本次实现发布目标为 `0.0.53`。
- 默认推送 `origin/main`，默认不上传 Docker Hub。

---

### Task 1: 用失败测试锁定验证码误识别根因

**Files:**
- Modify: `backend/tests/test_verification_code.py`
- Read: `backend/services/verification_code.py`

**Interfaces:**
- Consumes: `services.verification_code.extract_verification_code(subject="", body_text="", body_html="") -> str`。
- Produces: 当前 `0.0.52` 必须失败、目标规则必须通过的误报与保真测试。

- [ ] **Step 1: 增加裸 verification 误报回归测试**

在 `VerificationCodeTests` 中加入脱敏 Jira 代表文本，不复制真实邮件正文，只保留触发结构：

```python
def test_ignores_jira_ticket_number_near_bare_verification(self):
    samples = (
        "Verification details for request IH831-4575 were updated.",
        "The verification workflow references IH831-4303 in this notification.",
        "Verification status changed for IH831-4590.",
    )
    for body_text in samples:
        with self.subTest(body_text=body_text):
            self.assertEqual(self.extract(body_text=body_text), "")
```

再加入两个纯英文泛化用例：

```python
def test_ignores_bare_verification_request_numbers(self):
    self.assertEqual(
        self.extract(body_text="Your verification request 123456 is being processed."),
        "",
    )
    self.assertEqual(
        self.extract(body_text="Verification completed for request 654321."),
        "",
    )
```

- [ ] **Step 2: 锁定必须继续识别的明确代码上下文**

保留现有测试，并新增/明确以下用例：

```python
def test_explicit_english_code_contexts_still_extract(self):
    self.assertEqual(self.extract(body_text="Your verification code is 654321."), "654321")
    self.assertEqual(self.extract(body_text="Security code: 778899"), "778899")
    self.assertEqual(self.extract(body_text="OTP 445566"), "445566")
    self.assertEqual(self.extract(body_text="Use passcode 334455 to sign in."), "334455")
```

增加“验证身份不能单独触发”和 Atlassian 组合上下文：

```python
def test_chinese_identity_verification_requires_code_prompt(self):
    self.assertEqual(
        self.extract(body_text="为了验证自己的身份，请检查请求 24681357 的状态。"),
        "",
    )
    self.assertEqual(
        self.extract(
            body_text=(
                "作为额外的安全防护层，您需要验证自己的身份。\n"
                "请输入以下代码：\n\n24681357"
            )
        ),
        "24681357",
    )
```

- [ ] **Step 3: 运行聚焦后端测试确认 RED**

Run:

```bash
cd backend
python -m unittest tests.test_verification_code.VerificationCodeTests -v
```

Expected: 新的 Jira/裸 `verification` 用例至少失败，当前实现会返回工单尾号或请求数字；已有明确验证码测试仍应保持通过。

---

### Task 2: 最小收紧验证码上下文并确认 GREEN

**Files:**
- Modify: `backend/services/verification_code.py`
- Test: `backend/tests/test_verification_code.py`
- Test existing integration contract: `backend/tests/test_message_folder_resolution.py`

**Interfaces:**
- Consumes: `_STRONG_POSITIVE_RE`、`_GENERIC_POSITIVE_RE`、`_NEIGHBORHOOD`、`_nearest_distance()`、`_extract_from_text()`。
- Produces: `extract_verification_code()` 公开签名和返回类型不变；裸 `verification` 不再作为强信号；中文身份验证仅在邻近明确代码提示时形成强信号。

- [ ] **Step 1: 收紧英文 verification 强上下文**

将 `_STRONG_POSITIVE_RE` 中：

```python
r"\bverification(?:\s+code)?\b|"
```

改为：

```python
r"\bverification\s+code\b|"
```

同时从 `_STRONG_POSITIVE_RE` 删除：

```python
r"验证(?:自己|您|你)?的?身份|"
```

其它明确验证码关键词保持原样。

- [ ] **Step 2: 添加中文身份 + 代码提示组合匹配**

在正则定义区新增两个私有规则：

```python
_IDENTITY_VERIFICATION_RE = re.compile(
    r"验证(?:自己|您|你)?的?身份",
    re.IGNORECASE,
)

_CODE_PROMPT_RE = re.compile(
    r"(?:请输入以下代码|输入以下代码|请输入代码|输入代码|代码如下)",
    re.IGNORECASE,
)
```

在 `_extract_from_text()` 计算 `strong_matches` 后，计算：

```python
identity_matches = list(_IDENTITY_VERIFICATION_RE.finditer(text))
code_prompt_matches = list(_CODE_PROMPT_RE.finditer(text))
for prompt in code_prompt_matches:
    if _nearest_distance(prompt.span(), identity_matches) is not None \
            and _nearest_distance(prompt.span(), identity_matches) <= _NEIGHBORHOOD:
        strong_matches.append(prompt)
```

实现时避免重复调用 `_nearest_distance()`，使用局部变量：

```python
for prompt in code_prompt_matches:
    identity_distance = _nearest_distance(prompt.span(), identity_matches)
    if identity_distance is not None and identity_distance <= _NEIGHBORHOOD:
        strong_matches.append(prompt)
```

不把 `_CODE_PROMPT_RE` 单独当成全局强信号，保持 spec 的组合约束。

- [ ] **Step 3: 运行聚焦测试确认 GREEN**

Run:

```bash
cd backend
python -m unittest tests.test_verification_code.VerificationCodeTests -v
```

Expected: 全部通过；Jira 代表文本返回空，明确验证码上下文和 Atlassian 组合场景仍返回正确代码。

- [ ] **Step 4: 运行验证码相关集成测试**

Run:

```bash
cd backend
python -m unittest \
  tests.test_verification_code.VerificationCodeTests \
  tests.test_message_folder_resolution.MessageFolderResolutionTest.test_verification_code_decoration_uses_subject_then_scoped_cached_body \
  tests.test_message_folder_resolution.MessageFolderResolutionTest.test_remote_message_item_includes_subject_verification_code \
  -v
```

Expected: PASS；`routes/messages.py` 无需修改，仍先主题后限定正文。

---

### Task 3: 用失败前端契约锁定未读降噪和列间留白

**Files:**
- Modify: `frontend/tests/mail-list-visual-hierarchy.test.mjs`
- Modify: `frontend/tests/mail-verification-code.test.mjs`
- Read: `frontend/src/views/MailList.vue`

**Interfaces:**
- Consumes: `.mail-status-tag` 当前模板/CSS、未读信封 SVG、`.mail-item.unread`、`.mail-sender`、`.verification-code-copy`、`.mail-date`、`.toolbar-right`。
- Produces: 当前 `0.0.52` 必须失败、目标 UI 必须通过的静态契约。

- [ ] **Step 1: 把“未读徽标存在”契约反转为“未读徽标不存在”**

将当前 `read rows are quiet while unread rows keep explicit status text` 测试重命名为：

```js
test('unread rows keep icon and weight without a text status badge', async () => {
```

删除要求 `<UiBadge ... class="mail-status-tag">` 存在的断言，改为：

```js
assert.doesNotMatch(mail, /class="mail-status-tag"/);
assert.doesNotMatch(mail, /`未读 \$\{msg\.unread_count\}`/);
assert.match(mail, /v-if="!noReadStateFolder && !msg\.is_read" class="mail-status-icon unread-icon"/);
assert.match(mail, /\.mail-item\.unread::before\s*\{[^}]*background:\s*var\(--color-accent\);/s);
assert.match(mail, /\.mail-item\.unread \.mail-from\s*\{[^}]*font-weight:\s*var\(--font-semibold\);/s);
assert.match(mail, /\.mail-item\.unread \.mail-subject\s*\{[^}]*font-weight:\s*var\(--font-medium\);/s);
```

- [ ] **Step 2: 锁定桌面/窄桌面/移动端发件人留白**

把当前桌面 `padding-right: 12px` 断言改为：

```js
assert.match(
  mail,
  /\.mail-sender\s*\{[^}]*width:\s*165px;[^}]*gap:\s*9px;[^}]*padding-right:\s*24px;/s,
);
assert.match(
  mail,
  /@media \(max-width:\s*1180px\) and \(min-width:\s*769px\)[\s\S]*\.mail-sender\s*\{[^}]*width:\s*150px;[^}]*padding-right:\s*12px;/s,
);
assert.match(
  mail,
  /@media \(max-width:\s*768px\)[\s\S]*\.mail-sender\s*\{[^}]*width:\s*auto;[^}]*padding-right:\s*0;/s,
);
```

继续保留搜索同一行和 `.list-items { overflow-x: hidden; }` 的已有契约。

- [ ] **Step 3: 锁定复制图标与日期固定轨道**

在 `mail-verification-code.test.mjs` 保留现有 `flex-shrink: 0`、28×28px、移动 grid 契约，并增加：

```js
assert.match(source, /class="verification-code-copy"[\s\S]*<\/button>[\s\S]*<span class="mail-date">/s);
```

该断言确保删除未读徽标后模板顺序直接变成“复制按钮（若有）→ 日期”，不插入新的文字状态列。

- [ ] **Step 4: 运行前端聚焦测试确认 RED**

Run:

```bash
cd frontend
node --test \
  tests/mail-list-visual-hierarchy.test.mjs \
  tests/mail-verification-code.test.mjs \
  tests/mail-search-layout.test.mjs \
  tests/ui-layout.test.mjs
```

Expected: FAIL，原因至少包括 `.mail-status-tag` 仍存在和桌面 `.mail-sender` 仍为 `padding-right: 12px`。

---

### Task 4: 最小实现未读降噪和列间留白并确认 GREEN

**Files:**
- Modify: `frontend/src/views/MailList.vue`
- Test: `frontend/tests/mail-list-visual-hierarchy.test.mjs`
- Test: `frontend/tests/mail-verification-code.test.mjs`

**Interfaces:**
- Consumes: `msg.is_read`、`msg.unread_count`、`noReadStateFolder`、`verification_code`、现有 mail row flex/grid。
- Produces: 不再有 `.mail-status-tag` 模板或样式；未读信封/蓝点/字重继续；桌面 165px/24px、窄桌面 150px/12px、移动 auto/0。

- [ ] **Step 1: 删除未读文字徽标模板**

从邮件行中完整删除：

```vue
<UiBadge
  v-if="!noReadStateFolder && (listMode === 'conversations' ? (msg.unread_count || 0) > 0 : !msg.is_read)"
  tone="accent"
  class="mail-status-tag"
>
  {{ listMode === 'conversations' ? `未读 ${msg.unread_count}` : '未读' }}
</UiBadge>
```

不要修改前面的未读信封：

```vue
<svg v-if="!noReadStateFolder && !msg.is_read" class="mail-status-icon unread-icon" ...>
```

- [ ] **Step 2: 删除不再使用的 `.mail-status-tag` CSS**

删除所有 `.mail-status-tag`、`.mail-status-tag.unread`、`.mail-status-tag.read` 以及媒体查询里的 `.mail-status-tag` 覆盖。移动端当前组合规则：

```css
.mail-status-icon,
.mail-status-tag {
  display: none;
}
```

整个删除，不保留 `.mail-status-icon { display: none; }`。这样 390px 移动端也继续显示用户明确要求保留的未读信封；信封仍位于现有 `.mail-main-row` 中，不改变两行 grid 的区域定义。

- [ ] **Step 3: 增加桌面列间留白**

把最终覆盖：

```css
.mail-sender {
  width: 165px;
  gap: 9px;
  padding-right: 12px;
}
```

改为：

```css
.mail-sender {
  width: 165px;
  gap: 9px;
  padding-right: 24px;
}
```

在 769–1180px 断点把：

```css
.mail-sender {
  width: 150px;
}
```

改为：

```css
.mail-sender {
  width: 150px;
  padding-right: 12px;
}
```

移动端 `.mail-sender` 已有 `padding-right: 0`，保持不变。

- [ ] **Step 4: 运行前端聚焦测试确认 GREEN**

Run:

```bash
cd frontend
node --test \
  tests/mail-list-visual-hierarchy.test.mjs \
  tests/mail-verification-code.test.mjs \
  tests/mail-search-layout.test.mjs \
  tests/ui-layout.test.mjs
```

Expected: PASS。

- [ ] **Step 5: 运行前端全量测试与生产构建**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: `node:test` 0 failures；`vue-tsc` 和 Vite build exit 0。允许项目现有 >500kB chunk warning，但不得有编译错误。

---

### Task 5: 同步 README 和 0.0.53 版本

**Files:**
- Modify: `README.md`
- Modify: `VERSION`
- Modify: `package.json`
- Modify: `frontend/package.json`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: 当前版本 `0.0.52` 和本轮用户可见行为。
- Produces: 所有发布版本源一致为 `0.0.53`；README 不再描述未读文字徽标或宽泛裸 verification 识别。

- [ ] **Step 1: 更新 README 验证码说明**

把当前“中文支持‘验证码 / 验证代码 / 验证身份’等上下文”收紧为明确代码语义，例如：

```text
邮件列表会在本地识别主题或已缓存正文中的常见 4–8 位数字验证码，并在主题右侧提供始终可见的复制图标；中文支持“验证码 / 验证代码”等明确代码上下文，英文要求 verification code、security code、OTP、passcode 等明确代码语义，普通 verification 不再单独触发；“验证身份”只有同时出现“请输入以下代码”等代码提示时才识别。
```

在“邮件列表与计数口径”中把“未读邮件通过左侧状态点、字重和‘未读’徽标突出”改为：

```text
未读邮件通过左侧状态点、未读信封和字重突出，不再显示右侧文字未读徽标。
```

并说明桌面发件人列保持 165px、增加 12px 列间留白，搜索仍在同一行。

- [ ] **Step 2: 把版本更新到 0.0.53**

使用编辑工具把：

```text
VERSION: 0.0.52 -> 0.0.53
package.json version: 0.0.52 -> 0.0.53
frontend/package.json version: 0.0.52 -> 0.0.53
docker-compose.yml image: benxianyu/flymail:0.0.52 -> benxianyu/flymail:0.0.53
README 所有 benxianyu/flymail:0.0.52 -> benxianyu/flymail:0.0.53
```

由于 DevSpace `bash` 明确禁止命令修改项目文件，本轮不通过 `npm run sync-version` 写文件；但必须用只读命令验证与 `scripts/sync-version.js` 预期一致。

- [ ] **Step 3: 验证版本事实来源一致**

Run:

```bash
printf 'VERSION=' && cat VERSION
node -e "console.log('root=' + require('./package.json').version)"
node -e "console.log('frontend=' + require('./frontend/package.json').version)"
rg -n "benxianyu/flymail:0\.0\.53" docker-compose.yml README.md
rg -n "benxianyu/flymail:0\.0\.52" docker-compose.yml README.md || true
```

Expected: `VERSION/root/frontend = 0.0.53`，Compose 与 README 只引用 `0.0.53`。

- [ ] **Step 4: 重新检查 `.env.example` 是否需要同步**

本轮无环境变量变化，因此只读确认 `.env.example` 不需要修改；不得创建 `.env`。

---

### Task 6: 发布级自动化、Docker 与浏览器验证

**Files:**
- Verify only: backend/frontend/scripts/Docker runtime

**Interfaces:**
- Consumes: 完整 `0.0.53` 工作树和本地镜像 `benxianyu/flymail:0.0.53`。
- Produces: 后端、前端、Compose、Docker、MySQL、持久化、安全日志和四档浏览器视口的交付证据。

- [ ] **Step 1: 完整后端测试**

Run:

```bash
cd backend
python -m unittest discover -s tests -v
```

Expected: 0 failures。

- [ ] **Step 2: 完整前端测试和生产构建（版本更新后重跑）**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: 0 failures，build exit 0。

- [ ] **Step 3: Shell、Compose 和 diff 检查**

Run:

```bash
bash -n scripts/docker-entrypoint.sh
docker compose config --no-interpolate --no-path-resolution --quiet
git diff --check
git status --short
git diff
```

说明：仓库没有 `.env`，Compose 用 `config` 子命令的 `--no-interpolate --no-path-resolution` 做结构校验，不创建 `.env`。

- [ ] **Step 4: 构建本地镜像**

Run:

```bash
docker build -t benxianyu/flymail:0.0.53 .
```

Expected: build exit 0；镜像元数据不得包含运行时管理员密码、数据库密码或 session secret。

- [ ] **Step 5: 独立临时容器验证**

使用 `/Docker/flymail-test-0053-*` 独立临时目录（不是 `/Docker/flymail/data`），根目录 `0755`。动态生成管理员密码、至少 16 字符 session secret，以及包含 `'`、反斜杠、`@`、`:`、`/`、`%` 中至少一种特殊字符的 MySQL 密码。

验证：

```text
容器 healthy
/api/health version=0.0.53
mysql --version 为 8.0.x
SELECT @@datadir 为 /data/mysql/
/data/flymail 已创建
数据库写入测试成功
restart 后测试数据仍存在
日志不包含测试数据库密码/管理员密码/session secret
image Config.Env 不包含测试 secret
SIGTERM/stop 后 MySQL 安全关闭
临时容器和目录清理
```

- [ ] **Step 6: 浏览器视觉验收**

使用 `0.0.53` production build 临时容器；Playwright 拦截 `/api/*` 注入脱敏数据，包括：

```text
普通已读邮件
普通未读 Jira 邮件（verification 正文但 API verification_code=""）
真正未读验证码邮件（verification_code="654321"）
超长发件人
超长主题
```

验证：

```text
1440×900：无文字“未读”徽标；未读蓝点/信封/字重存在；桌面发件人列约 165px，右 padding 24px；Jira 行无复制图标；验证码行有纯复制图标且在日期左侧；搜索同一工具栏行；无页面横向溢出。
1920×1080：同上，主题空间和列对齐稳定。
390×844：无文字未读徽标；未读信封可见；现有移动 grid 不重叠；复制图标与日期可访问；scrollWidth <= viewport width。
720×450（1440×900 的 200% 等价视口）：工具栏不重叠；发件人/主题仍可区分；无页面级横向溢出。
```

---

### Task 7: 提交、推送并安全更新正式 flymail 容器

**Files:**
- Commit only task files
- Runtime: current `flymail` container, preserving `/Docker/flymail/data:/data`

**Interfaces:**
- Consumes: 所有验证通过的 `0.0.53` 工作树和本地镜像。
- Produces: `origin/main` 与本地 HEAD 一致；正式 `flymail` 使用 `0.0.53`，业务数据和重启持久化保持。

- [ ] **Step 1: 提交前审查并只暂存本次文件**

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
  frontend/tests/mail-list-visual-hierarchy.test.mjs \
  frontend/tests/mail-verification-code.test.mjs \
  backend/services/verification_code.py \
  backend/tests/test_verification_code.py \
  docs/superpowers/plans/2026-08-11-mail-list-unread-and-verification-context.md
git diff --staged
```

Spec `docs/superpowers/specs/2026-08-11-mail-list-unread-and-verification-context-design.md` 已在前一提交 `923430d` 中单独提交，不重复暂存。

- [ ] **Step 2: 提交实现**

Commit:

```bash
git commit -m "🐛 收紧验证码识别并精简未读状态"
```

- [ ] **Step 3: 推送 `origin/main`**

Run:

```bash
git push origin main
```

若 SSH 22 端口失败，先检查状态再使用：

```bash
GIT_SSH_COMMAND='ssh -p 443 -o HostName=ssh.github.com' git push origin main
```

禁止 force push。

- [ ] **Step 4: 读取正式容器现状但不输出 secret 值**

确认：

```text
当前 image/version/health
host port -> 8080
restart policy
/Docker/flymail/data:/data mount
MySQL version/datadir
users/accounts/cached_messages 等代表性计数
仅输出环境变量键名，不输出值
```

- [ ] **Step 5: 安全替换正式容器**

因为仓库没有 `.env`，从当前容器安全继承已有运行时环境值到权限 `0600` 的临时 env 文件，禁止输出值。停止旧容器并改名为 rollback 副本；用：

```text
image benxianyu/flymail:0.0.53
相同 host port
restart=always（或继承当前策略）
mount /Docker/flymail/data:/data
继承现有环境
```

创建新的 `flymail`。

只有以下全部成立后删除 rollback 容器：

```text
new flymail healthy
/api/health=0.0.53
MySQL 8.0
@@datadir=/data/mysql/
users/accounts/cached_messages 与切换前一致
/data/flymail 存在
日志无 inherited secret
```

然后删除临时 env 文件。

- [ ] **Step 6: 正式重启持久化终检**

Run:

```bash
docker restart flymail
```

等待 healthy 后重新检查：

```text
/api/health=0.0.53
MySQL version/datadir 不变
cached_messages 等代表性计数与重启前一致
mount 仍为 /Docker/flymail/data:/data
```

再运行：

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
```

Expected: 工作区干净；`HEAD == origin/main`；正式容器 running/healthy `0.0.53`。Docker Hub 不上传。
